"""
Week 2 "done when" check: a trigger writes a replayable 30 second snapshot.

    python -m datalogger.blackbox_demo                 # stub stream, automatic near-miss trigger
    python -m datalogger.blackbox_demo --manual        # also fire a manual trigger at the end
    python -m datalogger.blackbox_demo --replay FILE   # feed a recorded .jsonl instead of the stub

What it does
  1. opens data/visionpath.db and starts a session,
  2. feeds 60 s of stub sensor messages (or a recording) into the black box,
  3. the near miss in the stub (time to collision < 1 s) fires a snapshot,
  4. reads every snapshot back with contracts.from_json (what the replay
     harness does) and prints the checks.
"""

import argparse
import json
from pathlib import Path

from datalogger.blackbox import BlackBox, load_snapshot
from datalogger.db import end_session, open_db, start_session
from datalogger.stub_stream import stub_stream


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/visionpath.db")
    ap.add_argument("--out", default="data/blackbox")
    ap.add_argument("--replay", help="feed this .jsonl recording instead of the stub stream")
    ap.add_argument("--manual", action="store_true", help="fire a manual trigger after the stream")
    args = ap.parse_args()

    if args.replay:
        lines = Path(args.replay).read_text(encoding="utf-8").splitlines()
    else:
        lines = list(stub_stream(duration_s=60, fault_at_s=None))
    first_ns = json.loads(lines[0])["t_mono_ns"]

    conn = open_db(args.db)
    session_id = start_session(conn, user_id=1, git_commit="demo", monotonic_start_ns=first_ns)
    box = BlackBox(args.out, conn=conn, session_id=session_id)

    print(f"Session {session_id}: feeding {len(lines)} messages ...")
    for line in lines:
        path = box.add(line)
        if path:
            print(f"  TRIGGER -> {path.name}")
    if args.manual:
        path = box.trigger("manual")
        print(f"  MANUAL TRIGGER -> {path.name if path else 'skipped'}")
    end_session(conn, session_id)

    if not box.snapshots:
        print("No snapshot was written.")
        return 1

    ok = True
    for path in box.snapshots:
        msgs = load_snapshot(path)                        # raises if any line breaks the contract
        times = [m.t_mono_ns for m in msgs]
        span_s = (times[-1] - times[0]) / 1e9
        in_order = times == sorted(times)
        full_window = 29.0 <= span_s <= 30.0
        ok &= in_order and (full_window or bool(args.replay))   # a short recording cannot fill 30 s
        print(f"\n{path}")
        print(f"  messages            : {len(msgs)}")
        print(f"  all parse (contract): yes")
        print(f"  time order          : {'yes' if in_order else 'NO'}")
        print(f"  span                : {span_s:.1f} s {'(full 30 s window)' if full_window else '(NOT 30 s)'}")
        print(f"  size                : {path.stat().st_size / 1024:.0f} KB")

    rows = conn.execute(
        "SELECT snapshot_id, trigger_type, duration_s, file_size_kb, ts FROM blackbox_snapshots "
        "WHERE session_id = ? ORDER BY snapshot_id", (session_id,)).fetchall()
    print("\nblackbox_snapshots rows:")
    for r in rows:
        print(f"  #{r['snapshot_id']} {r['trigger_type']:<9} {r['duration_s']} s  {r['file_size_kb']} KB  at {r['ts']}")
    conn.close()

    print("\nRESULT:", "PASS - replayable 30 s snapshot written" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
