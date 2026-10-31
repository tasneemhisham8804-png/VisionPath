"""
Latency report for one session: p50 / p95 / max per pipeline stage, checked
against [targets] latency_p95_ms in config/visionpath.toml.

    python -m datalogger.report                  # last session in the database
    python -m datalogger.report --session 3
    python -m datalogger.report --list           # all sessions

Latency of a message = t_mono_ns - t_origin_ns (data contract): the time from
the original sensor reading to that message. Each stage includes the stages
before it, so the slowest stage is the end-to-end latency the user feels.
PASS when the p95 of every stage is within the target.

Exit code: 0 PASS, 1 FAIL, 2 no data or no target.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PIPELINE_ORDER = ("camera_frame", "ultrasonic_reading", "detection_set", "fused_obstacle",
                  "track", "risk_alert", "output_command")
FEW_SAMPLES = 20                    # below this, p95 is close to the maximum: read it with care
PRIORITY_NAMES = ("critical", "high", "medium", "low", "info")   # contract Priority.rank 0..4


def percentile(values, p: float) -> float:
    """Nearest-rank percentile (p in 0..100) of a non-empty list."""
    ordered = sorted(values)
    k = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[k - 1]


def latency_stats(conn, session_id: int) -> list[dict]:
    """One dict per stage: stage, module, n, p50, p95, max (ms), in pipeline order."""
    by_stage: dict[str, list[float]] = {}
    modules: dict[str, str] = {}
    for stage, module, value in conn.execute(
            "SELECT stage, module, value FROM performance_metrics "
            "WHERE session_id = ? AND metric = 'latency_ms'", (session_id,)):
        by_stage.setdefault(stage, []).append(value)
        modules[stage] = module
    order = {s: i for i, s in enumerate(PIPELINE_ORDER)}
    stats = []
    for stage in sorted(by_stage, key=lambda s: (order.get(s, len(order)), s)):
        v = by_stage[stage]
        stats.append({"stage": stage, "module": modules[stage], "n": len(v),
                      "p50": percentile(v, 50), "p95": percentile(v, 95), "max": max(v)})
    return stats


def verdict(stats: list[dict], target_ms: float | None) -> tuple[str, dict | None]:
    """('PASS' | 'FAIL' | 'NO DATA' | 'NO TARGET', slowest stage)."""
    if not stats:
        return "NO DATA", None
    slowest = max(stats, key=lambda s: s["p95"])
    if target_ms is None:
        return "NO TARGET", slowest
    return ("PASS" if slowest["p95"] <= target_ms else "FAIL"), slowest


def session_report(conn, session_id: int, target_ms: float | None) -> tuple[str, str]:
    """(report text, verdict) for one session."""
    s = conn.execute("SELECT started_at, ended_at FROM sessions WHERE session_id = ?",
                     (session_id,)).fetchone()
    if s is None:
        return f"Session {session_id} does not exist.", "NO DATA"
    kinds = [r[0] for r in conn.execute(
        "SELECT DISTINCT source FROM performance_metrics WHERE session_id = ?", (session_id,))]
    lines = [f"Session {session_id}  {s[0]} -> {s[1] or 'still running'}"
             + (f"  ({', '.join(k for k in kinds if k)})" if kinds else "")]

    stats = latency_stats(conn, session_id)
    if stats:
        lines.append("")
        lines.append(f"  {'stage':<20}{'module':<12}{'n':>7}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}")
        for st in stats:
            note = "  (few samples)" if st["n"] < FEW_SAMPLES else ""
            lines.append(f"  {st['stage']:<20}{st['module'] or '':<12}{st['n']:>7}"
                         f"{st['p50']:>10.1f}{st['p95']:>10.1f}{st['max']:>10.1f}{note}")
    result, slowest = verdict(stats, target_ms)
    lines.append("")
    if result == "NO DATA":
        lines.append("  No latency data in this session.")
    elif result == "NO TARGET":
        lines.append(f"  Slowest stage p95: {slowest['stage']} {slowest['p95']:.1f} ms "
                     "(no [targets] latency_p95_ms in the config)")
    else:
        lines.append(f"  Slowest stage p95: {slowest['stage']} {slowest['p95']:.1f} ms "
                     f"(target <= {target_ms:g} ms) -> {result}")

    pri = Counter(r[0] for r in conn.execute(
        "SELECT priority FROM alerts WHERE session_id = ?", (session_id,)))
    trig = Counter(r[0] for r in conn.execute(
        "SELECT trigger_type FROM blackbox_snapshots WHERE session_id = ?", (session_id,)))
    obstacles = conn.execute("SELECT COUNT(*) FROM obstacles WHERE session_id = ?",
                             (session_id,)).fetchone()[0]
    alert_text = ", ".join(f"{PRIORITY_NAMES[p] if p is not None and 0 <= p < 5 else p} {n}"
                           for p, n in sorted(pri.items(), key=lambda kv: (kv[0] is None, kv[0])))
    snap_text = ", ".join(f"{k} {n}" for k, n in sorted(trig.items()))
    lines.append(f"  alerts: {sum(pri.values())}{f' ({alert_text})' if alert_text else ''} | "
                 f"obstacles: {obstacles} | black box snapshots: {snap_text or 0}")
    return "\n".join(lines), result


def main(argv=None) -> int:
    from datalogger.config import ConfigError, datalogger_settings, latency_target_ms, load_config

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config file (default: VISIONPATH_CONFIG or config/visionpath.toml)")
    ap.add_argument("--session", type=int, help="session_id (default: the last one)")
    ap.add_argument("--list", action="store_true", help="list the sessions in the database")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    db_path = Path(datalogger_settings(cfg)["db_path"])
    if not db_path.is_file():
        print(f"No database at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    try:
        if args.list:
            for sid, started, ended in conn.execute(
                    "SELECT session_id, started_at, ended_at FROM sessions ORDER BY session_id"):
                print(f"  session {sid}: {started} -> {ended or 'still running'}")
            return 0
        session_id = args.session
        if session_id is None:
            row = conn.execute("SELECT MAX(session_id) FROM sessions").fetchone()
            session_id = row[0]
            if session_id is None:
                print("No sessions in the database yet.")
                return 2
        text, result = session_report(conn, session_id, latency_target_ms(cfg))
        print(text)
        return {"PASS": 0, "FAIL": 1}.get(result, 2)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
