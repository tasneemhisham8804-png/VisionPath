"""
The 11 wellbeing test cases (Week 7), replayed as bus messages.

    python -m wellbeing.cases                 # run the 11 cases
    python -m wellbeing.cases --save FOLDER   # also write each case as a .jsonl recording

Each case is a short recording: motion messages once a second (Mariam's signal), and the
user's answers, falls and alerts at set times. It is fed to the wellbeing check like a
replay, and the result is compared with what must happen. Every check is also written to
the wellbeing_checks table of a temporary on-device database, to prove the table takes it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from contracts import Reason, RiskAlert
from datalogger.db import mono_to_utc, open_db, session_clock, start_session
from wellbeing.check import Settings, Wellbeing, clock_from, save

T0 = 1_000_000_000_000
SECOND = 1_000_000_000
PLACE = {"lat": 30.0444, "lon": 31.2357, "source": "gps"}


class Script:
    """Builds a recording, one message at a time, moving the clock forward."""

    def __init__(self):
        self.t = T0
        self.lines: list[str] = []
        self.seq = 0

    def _add(self, msg_type: str, **fields) -> None:
        self.lines.append(json.dumps({"msg_type": msg_type, "t_mono_ns": self.t, **fields}))

    def _motion(self, seconds: float, moving: bool) -> "Script":
        for _ in range(int(seconds)):
            self._add("motion_state", moving=moving, place=PLACE, source="localization")
            self.t += SECOND
        return self

    def still(self, minutes: float = 0, seconds: float = 0) -> "Script":
        return self._motion(minutes * 60 + seconds, False)

    def walk(self, seconds: float) -> "Script":
        return self._motion(seconds, True)

    def say(self, intent: str = "im_okay") -> "Script":
        self._add("voice_intent", intent=intent, source="voice")
        return self

    def press(self, press: str = "short") -> "Script":
        self._add("button_press", press=press, source="buttons")
        return self

    def fall(self) -> "Script":
        self._add("fall_detected", source="fall")
        return self

    def alert(self, priority: str = "high") -> "Script":
        self.seq += 1
        self.lines.append(RiskAlert(seq=self.seq, t_mono_ns=self.t, t_origin_ns=self.t - 60_000_000, source="risk",
                                    confidence=0.9, bearing="ahead", alert_id=self.seq, hazard="motorbike",
                                    priority=priority, track_id=3,
                                    reason=Reason(code="ttc_below_threshold", ttc_s=1.6, distance_m=4.0)).to_json())
        return self


@dataclass
class Case:
    number: int
    title: str
    script: Script
    expect: dict                     # outcome, questions, caregiver (and checks, asked_at_min when they matter)
    settings: dict = field(default_factory=dict)
    clock: str = "14:00"             # local time at the start of the recording


def cases() -> list[Case]:
    return [
        Case(1, "Still 15 min, says \"I'm okay\"",
             Script().still(15, 1).still(seconds=5).say().still(10),
             {"outcome": "okay_voice", "attempts": 1, "questions": 1, "caregiver": 0, "checks": 1,
              "asked_at_min": 15.0}),
        Case(2, "Still 15 min, answers with a short press",
             Script().still(15, 1).still(seconds=8).press().still(1),
             {"outcome": "okay_button", "attempts": 1, "questions": 1, "caregiver": 0}),
        Case(3, "No answer to both questions",
             Script().still(15, 1).still(seconds=70),
             {"outcome": "escalated", "attempts": 2, "questions": 2, "caregiver": 1}),
        Case(4, "No answer to the first question, okay to the second",
             Script().still(15, 1).still(seconds=36).say().still(1),
             {"outcome": "okay_voice", "attempts": 2, "questions": 2, "caregiver": 0}),
        Case(5, "Falls while the question is playing",
             Script().still(15, 1).still(seconds=3).fall().still(seconds=90),
             {"outcome": "overridden_by_fall", "attempts": 1, "questions": 1, "caregiver": 0, "checks": 1}),
        Case(6, "Walks away instead of answering",
             Script().still(15, 1).walk(70),
             {"outcome": "escalated", "attempts": 2, "questions": 2, "caregiver": 1}),
        Case(7, "Still 14 min, then walks: no question",
             Script().still(14).walk(120).still(5),
             {"checks": 0, "caregiver": 0}),
        Case(8, "Still 20 min in quiet hours (23:00-07:00): no question",
             Script().still(20),
             {"checks": 0, "caregiver": 0}, settings={"quiet_hours": "23:00-07:00"}, clock="23:30"),
        Case(9, "A danger alert during the question: asked again, then okay",
             Script().still(15, 1).still(seconds=20).alert("high").still(seconds=20).say().still(1),
             {"outcome": "okay_voice", "attempts": 1, "questions": 2, "caregiver": 0}),
        Case(10, "Says something else (\"repeat\"), not \"I'm okay\"",
             Script().still(15, 1).still(seconds=5).say("repeat").still(seconds=70),
             {"outcome": "escalated", "attempts": 2, "questions": 2, "caregiver": 1}),
        Case(11, "The caregiver set 30 min: no question at 15, asked at 30",
             Script().still(30, 1).still(seconds=5).press().still(1),
             {"outcome": "okay_button", "attempts": 1, "questions": 1, "caregiver": 0, "checks": 1,
              "asked_at_min": 30.0}, settings={"interval_min": 30}),
    ]


def replay(case: Case, cfg: dict | None = None) -> tuple[dict, Wellbeing]:
    """Feed the recording to the wellbeing check, return what happened."""
    w = Wellbeing(Settings.from_config(cfg, **case.settings), clock=clock_from(case.clock, T0))
    for line in case.script.lines:
        w.feed(line)
    first = w.checks[0] if w.checks else None
    asks = [a for a in w.actions if a.kind == "ask"]
    got = {"checks": len(w.checks), "outcome": first.outcome if first else None,
           "attempts": first.attempts if first else None, "questions": first.questions if first else 0,
           "caregiver": sum(a.kind == "notify_caregiver" for a in w.actions),
           "asked_at_min": round((asks[0].t_ns - T0) / 60e9, 1) if asks else None}
    return got, w


LABELS = {"okay_voice": "okay by voice", "okay_button": "okay by button", "escalated": "caregiver told",
          "overridden_by_fall": "fall flow takes over"}


def describe(r: dict) -> str:
    """What happened, in a few words: 'okay by voice, asked 1x'."""
    if not r.get("questions"):
        return "no question"
    return f"{LABELS.get(r.get('outcome'), r.get('outcome'))}, asked {r['questions']}x"


def run_all(cfg: dict | None = None, save_dir: Path | None = None) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = open_db(Path(tmp) / "wellbeing.db")
        session = start_session(conn, user_id=1, monotonic_start_ns=T0)
        started, mono_s = session_clock(conn, session)
        to_utc = lambda t: mono_to_utc(started, mono_s, t)
        for case in cases():
            got, w = replay(case, cfg)
            ok = all(got.get(k) == v for k, v in case.expect.items())
            for check in w.checks:
                save(conn, check, session, 1, to_utc)
            if save_dir is not None:
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / f"wellbeing_case_{case.number:02d}.jsonl").write_text(
                    "\n".join(case.script.lines) + "\n", encoding="utf-8")
            results.append({"case": case, "got": got, "ok": ok})
        saved = conn.execute("SELECT COUNT(*) FROM wellbeing_checks").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM emergency_events WHERE trigger_type = 'wellbeing'").fetchone()[0]
        conn.close()
    results.append({"saved": saved, "events": events})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the 11 wellbeing test cases as replays.")
    ap.add_argument("--save", metavar="FOLDER", help="also write each case as a .jsonl recording")
    args = ap.parse_args(argv)
    try:
        from datalogger.config import load_config
        cfg = load_config()
    except Exception:                                   # the cases also run with the defaults
        cfg = None
    results = run_all(cfg, Path(args.save) if args.save else None)
    db = results.pop()
    s = Settings.from_config(cfg)
    print("Wellbeing check: the 11 test cases, replayed as bus messages\n")
    print(f"Settings: ask after {s.interval_min:g} min still, wait {s.answer_wait_s:g} s for an answer, "
          f"{s.attempts} tries, then tell the caregiver.\n")
    width = max(len(r["case"].title) for r in results)
    print(f"  {'#':<4}{'Case':<{width + 2}}{'Must happen':<32}Result")
    for r in results:
        c = r["case"]
        line = f"  {c.number:<4}{c.title:<{width + 2}}{describe(c.expect):<32}{'PASS' if r['ok'] else 'FAIL'}"
        print(line)
        if not r["ok"]:
            print(f"      got: {describe(r['got'])}  {r['got']}")
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed} of {len(results)} cases pass.")
    print(f"Saved {db['saved']} checks to wellbeing_checks and {db['events']} caregiver alerts to "
          "emergency_events (temporary database): the tables take them.")
    if args.save:
        print(f"Recordings written to {args.save}")
    ok = passed == len(results) == 11
    print("RESULT: PASS" if ok else "RESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
