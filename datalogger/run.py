"""
Datalogger service: listens to every message on the ZeroMQ bus and
  * logs metrics and navigation events to the database (datalogger.logger),
  * keeps the last 30 s in the black box and saves a snapshot on a
    near miss, a fault, a fall or a manual trigger (datalogger.blackbox).

    python -m datalogger.run                       # live: subscribe to the bus in the config file
    python -m datalogger.run --duration 70         # stop by itself after 70 s
    python -m datalogger.run --from-file X.jsonl   # no bus: feed a recording (session source = replay)

Ctrl+C stops it cleanly: the last rows are committed and the session is closed.
Bus endpoint, database path and black box settings come from
config/visionpath.toml (or the file in VISIONPATH_CONFIG).
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from datalogger.blackbox import BlackBox
from datalogger.config import REPO_ROOT, ConfigError, bus_endpoints, datalogger_settings, load_config
from datalogger.db import end_session, open_db, start_session
from datalogger.logger import Logger

STATUS_EVERY_S = 5.0


def git_commit() -> str | None:
    """Short commit hash of the running code, stored in sessions.git_commit."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class Service:
    """Logger + black box on one database session. Feed it lines with handle()."""

    def __init__(self, settings: dict, source_kind: str = "live", monotonic_start_ns: int | None = None,
                 commit: str | None = None):
        self.settings = settings
        self.flush_interval_s = float(settings["flush_interval_s"])
        self.conn = open_db(settings["db_path"])
        self.session_id = start_session(self.conn, user_id=int(settings["user_id"]), git_commit=commit,
                                        monotonic_start_ns=monotonic_start_ns)
        self.logger = Logger(self.conn, self.session_id, source_kind=source_kind)
        self.box = BlackBox(settings["blackbox_dir"], conn=self.conn, session_id=self.session_id,
                            window_s=float(settings["window_s"]),
                            near_miss_ttc_s=float(settings["near_miss_ttc_s"]),
                            cooldown_s=float(settings["cooldown_s"]))
        self.messages = 0
        self._last_flush = time.monotonic()

    def handle(self, line: str):
        """One bus message. Returns the snapshot Path if it fired a trigger."""
        self.messages += 1
        self.logger.handle(line)        # first: the obstacle row exists before a near-miss snapshot links to it
        return self.box.add(line)

    def tick(self) -> None:
        """Commit pending rows once per flush_interval_s."""
        if time.monotonic() - self._last_flush >= self.flush_interval_s:
            self.logger.flush()
            self._last_flush = time.monotonic()

    def close(self) -> None:
        self.logger.close()
        end_session(self.conn, self.session_id)
        self.conn.close()


def _print_summary(service: Service) -> None:
    print(f"\nSession {service.session_id} closed. {service.messages} messages received.")
    print("  " + service.logger.summary())
    if service.logger.last_error:
        print(f"  last skipped message: {service.logger.last_error}")
    if service.box.ignored_lines:
        print(f"  black box ignored {service.box.ignored_lines} lines without t_mono_ns")
    print(f"  snapshots: {len(service.box.snapshots)}")
    for p in service.box.snapshots:
        print(f"    {p}")
    print(f"  database : {service.settings['db_path']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config file (default: VISIONPATH_CONFIG or config/visionpath.toml)")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--from-file", help="feed this .jsonl recording instead of the bus")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
        settings = datalogger_settings(cfg)
        endpoints = None if args.from_file else bus_endpoints(cfg)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.from_file:
        lines = Path(args.from_file).read_text(encoding="utf-8").splitlines()
        first = next((json.loads(l)["t_mono_ns"] for l in lines if l.strip()), None)
        service = Service(settings, source_kind="replay", monotonic_start_ns=first, commit=git_commit())
        print(f"Session {service.session_id}: replaying {len(lines)} lines from {args.from_file}")
        for line in lines:
            if line.strip():
                path = service.handle(line)
                if path:
                    print(f"  TRIGGER -> {path.name}")
        service.close()
        _print_summary(service)
        return 0

    from datalogger.bus import Subscriber      # needs pyzmq only in live mode
    try:
        sub = Subscriber(endpoints)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2

    service = Service(settings, source_kind="live", commit=git_commit())
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))   # systemd stop on the Pi

    print(f"Session {service.session_id}: listening on {', '.join(endpoints)} (all topics). Ctrl+C to stop.")
    started = last_status = time.monotonic()
    try:
        while not stop["now"]:
            line = sub.recv()
            if line is not None:
                path = service.handle(line)
                if path:
                    print(f"  TRIGGER -> {path.name}")
            service.tick()
            now = time.monotonic()
            if now - last_status >= STATUS_EVERY_S:
                last_status = now
                print(f"  {now - started:5.0f} s: {service.messages} messages, "
                      f"{len(service.box.snapshots)} snapshots")
            if args.duration is not None and now - started >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        sub.close()
        service.close()
    _print_summary(service)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
