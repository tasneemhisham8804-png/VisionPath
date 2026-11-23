"""
The wellbeing check live (Week 8).

    python -m wellbeing.live --try                    # on the laptop: try it by typing, no bus needed
    python -m wellbeing.live --try --interval-min 0.5 # ask after 30 s instead of 15 min
    python -m wellbeing.live                          # on the Pi: listen to the bus (with the other modules)

--try: the user is "still" from the start (made-up motion, once a second) and you answer by
typing: ok ("I'm okay"), press (short press), long (emergency button), fall, walk, still,
alert (a danger alert), say <word> (another voice command), quit. The checks are saved in
data/wellbeing_try.db, so your real database is not touched.

On the Pi it listens to motion_state, voice_intent, button_press, fall_detected and
risk_alert on the bus, asks once a second whether time is up, prints every action, and saves
every check to the on-device database, in the datalogger's running session (or its own).
Until the output module and the backend take the actions, they are printed only.
"""

from __future__ import annotations

import argparse
import json
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

from datalogger.config import REPO_ROOT, ConfigError, datalogger_settings, load_config, sub_endpoints
from datalogger.db import end_session, mono_to_utc, open_db, session_clock, start_session
from wellbeing.check import Settings, Wellbeing, save

TOPICS = ("motion_state", "voice_intent", "button_press", "fall_detected", "risk_alert")
TRY_DB = REPO_ROOT / "data" / "wellbeing_try.db"
TRY_PLACE = {"lat": 30.0444, "lon": 31.2357, "source": "try"}
COMMANDS = "ok, press, long, fall, walk, still, alert, say <word>, quit"


def describe(action, try_mode: bool = False) -> str:
    d = action.detail
    if action.kind == "ask":
        hint = f"   (type: {COMMANDS})" if try_mode else ""
        return f"ASK (try {d['attempt']}): are you okay?  [{d['why']}]{hint}"
    if action.kind == "thanks":
        return "THANKS: okay, thanks"
    if action.kind == "notify_caregiver":
        return (f"TELL THE CAREGIVER: no answer after {d['attempts']} tries, still for {d['still_min']} min, "
                f"place {d.get('place')}")
    if action.kind == "tell_caregiver_okay":
        return f"TELL THE CAREGIVER: the user is okay now ({d.get('how')})"
    if action.kind == "stop":
        return f"STOP: {d.get('why')}"
    if action.kind == "signal_lost":
        return "MOTION SIGNAL LOST: no new question until it is back"
    return f"{action.kind.upper()}: {d}"


def typed(text: str, t_ns: int) -> tuple[str | None, bool | None]:
    """A typed command -> (bus line, moving). moving is True/False for walk/still, else None."""
    words = text.strip().lower().split()
    if not words:
        return None, None
    cmd = words[0]
    msg = {"t_mono_ns": t_ns}
    if cmd == "ok":
        msg.update(msg_type="voice_intent", intent="im_okay", source="try")
    elif cmd == "say":
        msg.update(msg_type="voice_intent", intent=" ".join(words[1:]) or "other", source="try")
    elif cmd in ("press", "long"):
        msg.update(msg_type="button_press", press="short" if cmd == "press" else "long", source="try")
    elif cmd == "fall":
        msg.update(msg_type="fall_detected", source="try")
    elif cmd == "alert":
        msg.update(msg_type="risk_alert", priority="high", source="try")
    elif cmd in ("walk", "still"):
        return None, cmd == "walk"
    else:
        return None, None
    return json.dumps(msg), None


class Runner:
    """Feeds the wellbeing check, ticks it once a second, prints and saves. Works with any line source."""

    def __init__(self, w: Wellbeing, conn, session: int, try_mode: bool = False, out=print,
                 clock=time.monotonic_ns):
        self.w, self.conn, self.session, self.try_mode, self.out, self.clock = w, conn, session, try_mode, out, clock
        started, mono_s = session_clock(conn, session)
        self.to_utc = lambda t: mono_to_utc(started, mono_s, t)
        self.saved: set[int] = set()
        self.moving = False
        self.last_tick = self.last_motion = 0

    def report(self, actions) -> None:
        for a in actions:
            self.out(f"  {describe(a, self.try_mode)}")

    def save_closed(self, also_open: bool = False) -> None:
        for check in self.w.checks:
            if id(check) not in self.saved and (check.outcome is not None or also_open):
                save(self.conn, check, self.session, 1, self.to_utc)
                self.saved.add(id(check))

    def step(self, line: str | None) -> None:
        now = self.clock()
        if self.try_mode and now - self.last_motion >= 1e9:          # made-up motion, once a second
            self.last_motion = now
            self.report(self.w.feed(json.dumps({"msg_type": "motion_state", "t_mono_ns": now,
                                                "moving": self.moving, "place": TRY_PLACE})))
        if line:
            self.report(self.w.feed(line))
        if now - self.last_tick >= 1e9:
            self.last_tick = now
            self.report(self.w.tick(now))
        self.save_closed()


def open_session(db: Path, user_id: int) -> tuple[sqlite3.Connection, int, bool]:
    """The datalogger's running session if there is one, else a new one. Returns (conn, session, own)."""
    conn = open_db(db)
    row = conn.execute("SELECT session_id FROM sessions WHERE ended_at IS NULL ORDER BY session_id DESC").fetchone()
    if row is not None:
        return conn, row[0], False
    return conn, start_session(conn, user_id=user_id), True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The wellbeing check, live.")
    ap.add_argument("--try", dest="try_mode", action="store_true", help="laptop: answer by typing, no bus")
    ap.add_argument("--interval-min", type=float, help="ask after this many minutes still (default: the config)")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
        dl = datalogger_settings(cfg)
    except ConfigError as e:
        print(f"Config error: {e}")
        return 2
    override = {"interval_min": args.interval_min} if args.interval_min else {}
    if args.try_mode and not override:
        override = {"interval_min": 1.0}                              # a quick try: ask after 1 minute
    w = Wellbeing(Settings.from_config(cfg, **override))

    source = None
    if not args.try_mode:
        from datalogger.bus import Subscriber                         # needs pyzmq only here
        try:
            source = Subscriber(sub_endpoints(cfg), topics=TOPICS)
        except (RuntimeError, ConfigError) as e:
            print(e)
            return 2
    db = TRY_DB if args.try_mode else Path(dl["db_path"])
    db.parent.mkdir(parents=True, exist_ok=True)
    conn, session, own = open_session(db, int(dl["user_id"]))
    runner = Runner(w, conn, session, try_mode=args.try_mode)

    typed_lines: queue.Queue = queue.Queue()
    if args.try_mode:
        threading.Thread(target=lambda: [typed_lines.put(l) for l in iter(sys.stdin.readline, "")],
                         daemon=True).start()
        print(f"Wellbeing check, try mode: you are still from now. The question comes after "
              f"{w.s.interval_min:g} min.\nType: {COMMANDS}\n")
    else:
        print(f"Wellbeing check live: listening to the bus, session {session}. Ctrl+C to stop.\n")

    started = time.monotonic()
    try:
        while True:
            line = None
            if source is not None:
                line = source.recv()
            else:
                try:
                    text = typed_lines.get(timeout=0.2)
                except queue.Empty:
                    text = None
                if text is not None:
                    if text.strip().lower() == "quit":
                        break
                    line, moving = typed(text, time.monotonic_ns())
                    if moving is not None:
                        runner.moving = moving
                        print(f"  (you are {'walking' if moving else 'still'} now)")
            runner.step(line)
            if args.duration is not None and time.monotonic() - started >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        runner.save_closed(also_open=True)
        if source is not None:
            source.close()
        if own:
            end_session(conn, session)
        saved = conn.execute("SELECT COUNT(*) FROM wellbeing_checks WHERE session_id = ?", (session,)).fetchone()[0]
        conn.close()
    print(f"\n{len(w.checks)} checks this time, saved in {db} (session {session}, {saved} rows in wellbeing_checks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
