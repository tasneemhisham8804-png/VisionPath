"""
Publishes the stub sensor stream on the ZeroMQ bus, in real time, so the
datalogger service can be tested with no camera and no other module running.
("stubbed now, real sensors in Week 8")

    python -m datalogger.stub_publisher                 # 60 s scenario, near miss at about 44 s
    python -m datalogger.stub_publisher --fault-at 50   # also one system_fault alert at 50 s
    python -m datalogger.stub_publisher --speed 10      # 10 times faster
    python -m datalogger.stub_publisher --file X.jsonl  # publish a recording instead

Start `python -m datalogger.run` first, in another window: a ZeroMQ
subscriber only receives what is published after it has connected.
The publisher binds the endpoint from config/visionpath.toml.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from datalogger.config import ConfigError, bus_endpoints, load_config
from datalogger.stub_stream import stub_stream


def rebase(lines, new_start_ns: int):
    """Shift t_mono_ns / t_origin_ns of a recording so it starts at new_start_ns (this machine's clock)."""
    lines = [l for l in lines if l.strip()]
    if not lines:
        return []
    shift = new_start_ns - json.loads(lines[0])["t_mono_ns"]
    out = []
    for line in lines:
        d = json.loads(line)
        for key in ("t_mono_ns", "t_origin_ns"):
            if isinstance(d.get(key), int):
                d[key] += shift
        out.append(json.dumps(d, separators=(",", ":"), sort_keys=True))   # same format as msg.to_json()
    return out


def publish(pub, lines, speed: float = 1.0, progress=None) -> Counter:
    """Send lines at the pace of their t_mono_ns (divided by speed). Returns messages sent per topic."""
    from datalogger.bus import topic_of
    sent = Counter()
    t0_msg = t0_real = None
    for line in lines:
        t = json.loads(line)["t_mono_ns"]
        if t0_msg is None:
            t0_msg, t0_real = t, time.monotonic_ns()
        due = t0_real + (t - t0_msg) / speed
        delay = (due - time.monotonic_ns()) / 1e9
        if delay > 0:
            time.sleep(delay)
        pub.send(line)
        sent[topic_of(line)] += 1
        if progress:
            progress((t - t0_msg) / 1e9, sum(sent.values()))
    return sent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config file (default: VISIONPATH_CONFIG or config/visionpath.toml)")
    ap.add_argument("--duration", type=float, default=60.0, help="stub scenario length in seconds")
    ap.add_argument("--fault-at", type=float, help="send one system_fault alert at this second")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 10 = ten times faster")
    ap.add_argument("--wait", type=float, default=1.5, help="seconds to wait for subscribers to connect")
    ap.add_argument("--file", help="publish this .jsonl recording instead of the stub scenario")
    args = ap.parse_args(argv)

    try:
        endpoint = bus_endpoints(load_config(args.config))[0]
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    from datalogger.bus import Publisher
    try:
        pub = Publisher(endpoint)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"Publishing on {pub.endpoint}. Waiting {args.wait:.1f} s for subscribers ...")
    time.sleep(args.wait)

    start_ns = time.monotonic_ns()
    if args.file:
        lines = rebase(Path(args.file).read_text(encoding="utf-8").splitlines(), start_ns)
    else:
        lines = list(stub_stream(duration_s=args.duration, start_ns=start_ns, fault_at_s=args.fault_at))
    print(f"Sending {len(lines)} messages ({args.speed:g}x real time). Ctrl+C to stop.")

    last = {"s": -10.0}

    def progress(stream_s, count):
        if stream_s - last["s"] >= 10:
            last["s"] = stream_s
            print(f"  {stream_s:4.0f} s: {count} messages sent")

    try:
        sent = publish(pub, lines, speed=args.speed, progress=progress)
    except KeyboardInterrupt:
        print("stopped")
        pub.close()
        return 1
    time.sleep(0.5)                      # let the last messages leave the socket
    pub.close()
    print("Done. Sent: " + ", ".join(f"{k} {v}" for k, v in sorted(sent.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
