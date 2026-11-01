"""
Fault injection (Week 5): break a recorded session on purpose and check the system copes,
row by row of the degraded modes table (TDR section 7).

    python -m qa.faults        # run every check and print the degraded modes table (exit 1 if one fails)

The injectors below change a recording the way a real fault would (the camera stops, the
ultrasonic sensor freezes, messages arrive broken...). The checks then replay it and look at
what exists today: the health watch (qa/health.py), the datalogger, the black box and the
regression gate. The "system behaviour" of each row belongs to modules that are not in the
repository yet; those rows say who they wait for.

The same checks run in CI through tests/test_fault_injection.py, on every push.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

from datalogger.config import datalogger_settings, load_config
from datalogger.run import Service
from datalogger.stub_stream import stub_stream
from qa import gate, health

START_NS = 1_000_000_000_000


# ---------------------------------------------------------------- injectors

def _load(lines):
    return [json.loads(line) for line in lines]


def _dump(d) -> str:
    return json.dumps(d, separators=(",", ":"), sort_keys=True)          # the contract's own format


def _seconds(ds):
    t0 = min(d["t_mono_ns"] for d in ds)
    return lambda d: (d["t_mono_ns"] - t0) / 1e9


def _inside(s, from_s, to_s):
    return s >= from_s and (to_s is None or s < to_s)


def camera_down(lines, from_s, to_s=None):
    """The camera stops: no frames and so no detections between from_s and to_s."""
    ds = _load(lines)
    sec = _seconds(ds)
    return [_dump(d) for d in ds if not (d.get("source") in ("camera", "detector") and _inside(sec(d), from_s, to_s))]


def ultrasonic_silent(lines, from_s, to_s=None):
    """The ESP32 goes silent: no ultrasonic readings."""
    ds = _load(lines)
    sec = _seconds(ds)
    return [_dump(d) for d in ds if not (d.get("source") == "ultrasonic" and _inside(sec(d), from_s, to_s))]


def ultrasonic_frozen(lines, from_s, to_s=None):
    """The ESP32 freezes: it keeps sending its last reading (same seq, same t_origin_ns)."""
    ds = _load(lines)
    sec = _seconds(ds)
    out, last = [], None
    for d in ds:
        if d.get("source") == "ultrasonic":
            if _inside(sec(d), from_s, to_s) and last is not None:
                d = dict(last, t_mono_ns=d["t_mono_ns"])
            else:
                last = d
        out.append(_dump(d))
    return out


def kill_module(lines, source, from_s):
    """One module crashes at from_s and never comes back."""
    ds = _load(lines)
    sec = _seconds(ds)
    return [_dump(d) for d in ds if not (d.get("source") == source and sec(d) >= from_s)]


def drop_frames(lines, every=3):
    """The camera drops one frame in every `every` (a slow USB, a busy CPU)."""
    out, n = [], 0
    for line in lines:
        if json.loads(line).get("msg_type") == "camera_frame":
            n += 1
            if n % every == 0:
                continue
        out.append(line)
    return out


def delay_stage(lines, msg_type, ms):
    """One stage gets slower by `ms` milliseconds."""
    out = []
    for d in _load(lines):
        if d.get("msg_type") == msg_type:
            d["t_mono_ns"] += int(ms * 1e6)
        out.append(_dump(d))
    return out


def corrupt(lines, every=50):
    """Every `every`th message arrives broken."""
    return [("{broken" + line[7:]) if i % every == every - 1 else line for i, line in enumerate(lines)]


def duplicate_and_swap(lines, every=20):
    """Messages arrive twice, and neighbours arrive out of order."""
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        if i % every == 0:
            out.append(line)
    for i in range(0, len(out) - 1, 7):
        out[i], out[i + 1] = out[i + 1], out[i]
    return out


# ---------------------------------------------------------------- what the checks look at

def stub(fps=10, fault_at_s=None):
    return list(stub_stream(duration_s=60, fps=fps, start_ns=START_NS, fault_at_s=fault_at_s))


def replay(lines) -> dict:
    """Feed lines to the datalogger in a temporary folder. Never raises: a crash is a result."""
    with tempfile.TemporaryDirectory() as tmp:
        s = dict(datalogger_settings(load_config()))
        s["db_path"], s["blackbox_dir"] = Path(tmp) / "f.db", Path(tmp) / "bb"
        try:
            first = json.loads(lines[0])["t_mono_ns"]
        except (ValueError, KeyError, IndexError):
            first = None
        try:
            service = Service(s, source_kind="replay", monotonic_start_ns=first)
            for line in lines:
                service.handle(line)
            snaps = [p.read_text(encoding="utf-8").splitlines() for p in service.box.snapshots]
            result = {"crashed": None, "logged": dict(service.logger.counts),
                      "skipped": sum(service.logger.skipped.values()),
                      "snapshots": [p.name for p in service.box.snapshots], "snapshot_lines": snaps}
            service.close()
            return result
        except Exception as e:                      # noqa: BLE001  (any crash fails the check)
            return {"crashed": f"{type(e).__name__}: {e}"}


def first(events, name, what):
    return next((e for e in events if e.name == name and e.what == what), None)


# ---------------------------------------------------------------- checks (each returns ok, detail)

def check_camera_down():
    lines = camera_down(stub(), 20, 25)
    ev = health.watch(lines)
    down, back = first(ev, "camera", "down"), first(ev, "camera", "back")
    r = replay(lines)
    ok = (down is not None and 20.0 < down.t_s <= 20.0 + health.CAMERA_TIMEOUT_S + 0.1 and back is not None
          and r["crashed"] is None and r["logged"].get("ultrasonic_reading", 0) == 600)
    detail = (f"detected at {down.t_s:g} s, back at {back.t_s:g} s; ultrasonic kept logging"
              if down and back else f"events: {ev}; {r.get('crashed') or ''}")
    return ok, detail


def check_ultrasonic_silent():
    lines = ultrasonic_silent(stub(), 30, 40)
    ev = health.watch(lines)
    down = first(ev, "ultrasonic", "down")
    r = replay(lines)
    ok = (down is not None and down.reason == "silent" and 30.0 < down.t_s <= 30.0 + health.ULTRASONIC_TIMEOUT_S + 0.1
          and first(ev, "ultrasonic", "back") is not None and r["crashed"] is None
          and r["logged"].get("detection_set", 0) == 600)
    return ok, (f"detected at {down.t_s:g} s (silent); camera kept logging" if down else f"events: {ev}")


def check_ultrasonic_frozen():
    lines = ultrasonic_frozen(stub(), 30, 40)
    ev = health.watch(lines)
    down = first(ev, "ultrasonic", "down")
    ok = (down is not None and down.reason == "frozen" and down.t_s <= 30.0 + health.ULTRASONIC_TIMEOUT_S + 0.1
          and first(ev, "ultrasonic", "back") is not None and replay(lines)["crashed"] is None)
    return ok, (f"detected at {down.t_s:g} s (frozen: the same reading repeated)" if down else f"events: {ev}")


def check_detector_killed():
    lines = kill_module(stub(), "detector", 10)
    ev = health.watch(lines)
    down = first(ev, "detector", "down")
    ok = (down is not None and 10.0 < down.t_s <= 10.0 + health.MODULE_TIMEOUT_S + 0.1
          and first(ev, "detector", "back") is None and replay(lines)["crashed"] is None)
    return ok, (f"detected at {down.t_s:g} s, stays down" if down else f"events: {ev}")


def check_dropped_frames():
    lines = drop_frames(stub(), every=3)
    ev = health.watch(lines)
    r = replay(lines)
    ok = not ev and r["crashed"] is None and r["logged"].get("camera_frame") == 400
    return ok, "no false alarm for short gaps; 400 of 600 frames logged" if ok else f"events: {ev}; {r}"


def check_broken_messages():
    lines = corrupt(stub(), every=50)
    broken = sum(1 for line in lines if line.startswith("{broken"))
    r = replay(lines)
    ok = r["crashed"] is None and r["skipped"] == broken and len(r["snapshots"]) == 1
    return ok, f"{broken} broken messages skipped and counted; the near miss was still saved" if ok else str(r)


def check_duplicates_and_order():
    r = replay(duplicate_and_swap(stub()))
    times = [json.loads(line)["t_mono_ns"] for line in (r.get("snapshot_lines") or [[]])[0]]
    ok = r["crashed"] is None and len(r["snapshots"]) == 1 and times == sorted(times)
    return ok, "no crash; the black box file is still in time order" if ok else str(r.get("crashed"))


def check_fault_alert_snapshot():
    r = replay(stub(fault_at_s=50))
    ok = r["crashed"] is None and any(name.endswith("_fault.jsonl") for name in r["snapshots"])
    return ok, "a system_fault alert saves a fault black box file" if ok else str(r)


def check_slow_stage_caught():
    settings = datalogger_settings(load_config())
    now = gate.measure(delay_stage(stub(), "output_command", 300), settings)
    base = gate.load_baseline().get("stub_10fps")
    rows = gate.check("stub_10fps", now, base, 300.0)
    caught = any(r["metric"] == "p95_ms" and not r["ok"] for r in rows)
    return caught, f"output 300 ms slower: p95 {now.get('p95_ms'):g} ms, the regression gate fails it" if caught \
        else "the gate did not notice"


def check_bus_restart():
    """The bus process restarts mid-session: the logger reconnects by itself. Needs pyzmq."""
    try:
        import zmq  # noqa: F401
    except ImportError:
        return None, "skipped: needs pyzmq"
    from datalogger.bus import BusProxy, Publisher, Subscriber
    lines = stub()
    bus = BusProxy("tcp://127.0.0.1:*", "tcp://127.0.0.1:*")
    pub_ep, sub_ep = bus.pub_endpoint, bus.sub_endpoint
    state = {"bus": bus, "run": True}
    lock = threading.Lock()                                 # never close the bus while it is working

    def pump():
        while state["run"]:
            with lock:
                if state["bus"] is not None:
                    state["bus"].step(20)
            time.sleep(0.001)

    worker = threading.Thread(target=pump)
    worker.start()
    sub = Subscriber([sub_ep], recv_timeout_ms=50)
    pub = Publisher(pub_ep)
    got = {"before": 0, "probe": 0, "after": 0}
    phase = {"now": "before"}
    reconnected = None

    def drain(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if sub.recv() is not None:
                got[phase["now"]] += 1

    try:
        time.sleep(0.5)
        for line in lines[:300]:
            pub.send(line)
        drain(0.8)
        with lock:                                           # the bus process dies
            state["bus"].close()
            state["bus"] = None
        time.sleep(0.5)
        for attempt in range(10):                            # and starts again on the same ports
            try:
                new_bus = BusProxy(pub_ep, sub_ep)
                with lock:
                    state["bus"] = new_bus
                break
            except Exception:                                # noqa: BLE001  port not free yet
                time.sleep(0.3)
        if state["bus"] is None:
            return None, "skipped: the port could not be reused on this computer"
        # ZeroMQ reconnects in the background. Messages sent while it does are lost (normal for
        # PUB/SUB), so send a probe until one arrives: that is the moment the logger is back.
        phase["now"] = "probe"
        start = time.monotonic()
        while time.monotonic() - start < 5.0:
            pub.send(lines[300])
            if sub.recv() is not None:
                reconnected = time.monotonic() - start
                break
        drain(0.3)
        phase["now"] = "after"
        for line in lines[301:600]:
            pub.send(line)
        drain(1.5)
    finally:
        state["run"] = False
        worker.join()
        sub.close()
        pub.close()
        with lock:
            if state["bus"] is not None:
                state["bus"].close()
    sent_after = 600 - 301
    ok = got["before"] >= 250 and reconnected is not None and got["after"] >= sent_after * 0.95
    if reconnected is None:
        return False, "the logger did not reconnect within 5 s"
    return ok, (f"the logger reconnected by itself in {reconnected:.1f} s, then got "
                f"{got['after']} of {sent_after} messages")


# ---------------------------------------------------------------- the degraded modes table

# failure, detected by, system behaviour, user perceives (TDR section 7) + our checks + who the rest waits for
DEGRADED_MODES = [
    ("Camera down", "No frames for 0.5 s", "Ultrasonic only", "Fault pattern and voice notice",
     [check_camera_down, check_fault_alert_snapshot], "fusion and risk (Tasneem), output (Baraa, Mostafa)"),
    ("Camera IMU lost", "No IMU samples", "No fall detection; caregiver notified", "Voice notice",
     [], "an IMU message in the data contract (Tasneem), fall detection (Malak Ibrahim)"),
    ("Low depth confidence", "Confidence map", "Depth weight reduced", "Nothing unless sustained",
     [], "fusion (Tasneem)"),
    ("Ultrasonic down", "ESP32 silent or frozen", "Camera only, earlier alerts", "Voice notice",
     [check_ultrasonic_silent, check_ultrasonic_frozen], "risk (Tasneem), output (Baraa, Mostafa)"),
    ("Pi frozen or off", "ESP32 heartbeat timeout", "Fail-safe buzzer only", "Distinct system down pattern",
     [], "ESP32 firmware bench test, R5 (Malak Emad)"),
    ("No internet", "Backend unreachable", "Outbox; retried until reconnected", "Nothing unless emergency",
     [], "backend outbox (Abdelrahman)"),
    ("Phone disconnected", "Relay heartbeat lost", "Outbox retains events", "Voice notice",
     [], "app relay (Baraa), outbox (Abdelrahman)"),
    ("Battery critical", "INA226", "Send location, clean shutdown", "Voice notice",
     [], "power monitor (Malak Emad)"),
    ("Overheating", "Pi throttle flag or Hailo temperature", "Lower FPS", "Voice notice",
     [], "a throttle or temperature message (Malak Emad, Tasneem)"),
]

ROBUSTNESS = [
    ("A detection module crashes", check_detector_killed),
    ("Dropped frames (1 in 3)", check_dropped_frames),
    ("Broken messages (1 in 50)", check_broken_messages),
    ("Repeated and out-of-order messages", check_duplicates_and_order),
    ("A stage gets 300 ms slower", check_slow_stage_caught),
    ("The bus process restarts", check_bus_restart),
]


def run_all():
    rows, failed, waiting = [], 0, 0
    for failure, detected_by, behaviour, perceives, checks, waits_for in DEGRADED_MODES:
        results = [c() for c in checks]
        bad = [d for ok, d in results if ok is False]
        failed += len(bad)
        if not checks:
            waiting += 1
        rows.append(("mode", failure, detected_by, behaviour, results, waits_for))
    for name, check in ROBUSTNESS:
        ok, detail = check()
        failed += ok is False
        rows.append(("robust", name, ok, detail))
    return rows, failed, waiting


def main(argv=None) -> int:
    rows, failed, waiting = run_all()
    print("Fault injection: the degraded modes table (TDR section 7)\n")
    for row in rows:
        if row[0] != "mode":
            continue
        _, failure, detected_by, behaviour, results, waits_for = row
        print(f"{failure}  ({detected_by} -> {behaviour})")
        if results:
            for ok, detail in results:
                print(f"    {'ok   ' if ok else 'FAIL '} {detail}")
            print(f"    wait  the system's behaviour: {waits_for}")
        else:
            print(f"    wait  {waits_for}")
    print("\nRobustness of what exists today\n")
    for row in rows:
        if row[0] == "robust":
            _, name, ok, detail = row
            mark = "skip " if ok is None else ("ok   " if ok else "FAIL ")
            print(f"    {mark} {name}: {detail}")
    print(f"\nRESULT: {'FAIL' if failed else 'PASS'}"
          f" ({failed} failed; {waiting} of {len(DEGRADED_MODES)} rows wait for other modules)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
