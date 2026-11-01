"""
Regression gate: a change that makes the numbers worse cannot be merged.

    python -m qa.gate             # replay the benchmarks, compare with qa/baseline.json (exit 1 if worse)
    python -m qa.gate --update    # accept today's numbers as the new baseline (say why in the commit)

Each benchmark is a recorded session, replayed through the datalogger exactly as
on the device. Today the benchmarks are the stub scenarios (fake sensors); real
walk recordings go in qa/benchmarks/*.jsonl and are picked up automatically.

A benchmark fails when, compared with the baseline:
    p95 latency      rises by more than 10% (+1 ms), or goes above the target in config/visionpath.toml
    logged messages  fewer than before (a change lost messages)
    skipped messages more than before (a change broke the message format)
    alerts, obstacles, black box snapshots   fewer than before
    recall           drops by more than 0.02          (once walk recordings with ground truth exist)
    false alerts     rise by more than 0.2 per 10 min (same)
Numbers that are not in the baseline yet are shown as "new" and do not fail.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from datalogger.config import ConfigError, datalogger_settings, latency_target_ms, load_config
from datalogger.report import latency_stats, verdict
from datalogger.run import Service, git_commit
from datalogger.stub_stream import stub_stream

QA_DIR = Path(__file__).resolve().parent
BASELINE = QA_DIR / "baseline.json"
RECORDINGS = QA_DIR / "benchmarks"
START_NS = 1_000_000_000_000

# metric: (better, allowed change as a fraction of the baseline, allowed change as an amount)
RULES = {
    "p95_ms": ("lower", 0.10, 1.0),
    "logged": ("higher", 0.0, 0.0),
    "skipped": ("lower", 0.0, 0.0),
    "alerts": ("higher", 0.0, 0.0),
    "obstacles": ("higher", 0.0, 0.0),
    "snapshots": ("higher", 0.0, 0.0),
    "recall": ("higher", 0.0, 0.02),
    "false_per_10min": ("lower", 0.0, 0.2),
}
NAMES = {
    "p95_ms": "p95 latency (ms)", "logged": "logged messages", "skipped": "skipped messages",
    "alerts": "alerts", "obstacles": "obstacles", "snapshots": "black box snapshots",
    "recall": "recall", "false_per_10min": "false alerts per 10 min",
}


def benchmarks() -> dict:
    """Name -> function returning the lines. Stub scenarios first, then recorded walks."""
    found = {
        "stub_10fps": lambda: stub_stream(duration_s=60, fps=10, start_ns=START_NS),
        "stub_30fps": lambda: stub_stream(duration_s=60, fps=30, start_ns=START_NS),
        "stub_fault": lambda: stub_stream(duration_s=60, fps=10, start_ns=START_NS, fault_at_s=50),
    }
    for path in sorted(RECORDINGS.glob("*.jsonl")):
        found[path.stem] = (lambda p=path: p.read_text(encoding="utf-8").splitlines())
    return found


def measure(lines, settings: dict) -> dict:
    """Replay one benchmark through the datalogger in a temporary folder and return its numbers."""
    lines = list(lines)
    with tempfile.TemporaryDirectory() as tmp:
        s = dict(settings)
        s["db_path"] = Path(tmp) / "gate.db"
        s["blackbox_dir"] = Path(tmp) / "blackbox"
        first = json.loads(lines[0]).get("t_mono_ns") if lines else None
        service = Service(s, source_kind="replay", monotonic_start_ns=first)
        for line in lines:
            service.handle(line)
        logged = sum(service.logger.counts.values())
        skipped = sum(service.logger.skipped.values())
        snapshots = len(service.box.snapshots)
        session = service.session_id
        service.close()
        conn = sqlite3.connect(s["db_path"])
        try:
            stats = latency_stats(conn, session)
            alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            obstacles = conn.execute("SELECT COUNT(*) FROM obstacles").fetchone()[0]
        finally:
            conn.close()
    _, slowest = verdict(stats, None)
    numbers = {"logged": logged, "skipped": skipped, "alerts": alerts, "obstacles": obstacles,
               "snapshots": snapshots}
    if slowest:
        numbers["p95_ms"] = slowest["p95"]
    return numbers


def check(name: str, now: dict, base: dict | None, target_ms: float) -> list[dict]:
    """One row per metric: name, baseline, now, ok (True/False), why."""
    rows = []
    for metric in RULES:
        if metric not in now:
            continue
        value = now[metric]
        before = None if base is None else base.get(metric)
        ok, why = True, ""
        if metric == "p95_ms" and value > target_ms:
            ok, why = False, f"above the target ({target_ms:g} ms)"
        if before is not None and ok:
            better, frac, amount = RULES[metric]
            room = abs(before) * frac + amount
            if better == "lower" and value > before + room:
                ok, why = False, f"worse than the baseline (allowed up to {before + room:g})"
            if better == "higher" and value < before - room:
                ok, why = False, f"worse than the baseline (allowed down to {before - room:g})"
        if before is None:
            why = why or "new"
        rows.append({"benchmark": name, "metric": metric, "baseline": before, "now": value, "ok": ok, "why": why})
    return rows


def run_all(target_ms: float, settings: dict, baseline: dict) -> tuple[dict, list[dict]]:
    measured, rows = {}, []
    for name, lines in benchmarks().items():
        measured[name] = measure(lines(), settings)
        rows += check(name, measured[name], baseline.get(name), target_ms)
    return measured, rows


def fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:g}" if isinstance(v, float) else str(v)


def text_report(rows: list[dict], target_ms: float) -> str:
    out = [f"Regression gate (target p95 <= {target_ms:g} ms)", ""]
    out.append(f"{'benchmark':<14}{'number':<26}{'baseline':>10}{'now':>10}  result")
    for r in rows:
        mark = "ok" if r["ok"] else "WORSE"
        note = f"  {r['why']}" if r["why"] else ""
        out.append(f"{r['benchmark']:<14}{NAMES[r['metric']]:<26}{fmt(r['baseline']):>10}{fmt(r['now']):>10}  {mark}{note}")
    failed = [r for r in rows if not r["ok"]]
    out += ["", f"RESULT: {'FAIL' if failed else 'PASS'}"
            + (f" ({len(failed)} number{'s' if len(failed) != 1 else ''} got worse)" if failed else "")]
    return "\n".join(out)


def markdown_report(rows: list[dict], target_ms: float) -> str:
    failed = [r for r in rows if not r["ok"]]
    out = [f"## Regression gate: {'FAIL' if failed else 'PASS'}", "",
           f"Target: p95 latency ≤ {target_ms:g} ms. Baseline: `qa/baseline.json`.", "",
           "| Benchmark | Number | Baseline | Now | Result |", "|---|---|---|---|---|"]
    for r in rows:
        result = "✅" if r["ok"] else f"❌ {r['why']}"
        if r["ok"] and r["why"]:
            result += f" {r['why']}"
        out.append(f"| {r['benchmark']} | {NAMES[r['metric']]} | {fmt(r['baseline'])} | {fmt(r['now'])} | {result} |")
    return "\n".join(out) + "\n"


def load_baseline(path: Path | None = None) -> dict:
    path = path or BASELINE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("benchmarks", {})


def save_baseline(measured: dict, path: Path | None = None) -> None:
    path = path or BASELINE
    data = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": git_commit(), "benchmarks": measured}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fail when a change makes the numbers worse.")
    ap.add_argument("--update", action="store_true", help="accept today's numbers as the new baseline")
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
        settings = datalogger_settings(cfg)
        target_ms = latency_target_ms(cfg) or 300.0
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    baseline = load_baseline()
    measured, rows = run_all(target_ms, settings, {} if args.update else baseline)
    print(text_report(rows, target_ms))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")      # shown on the GitHub checks page
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(markdown_report(rows, target_ms))

    if args.update:
        if any(not r["ok"] for r in rows):
            print("\nNot saved: a number is above its target, so it cannot become the baseline.")
            return 1
        save_baseline(measured)
        print(f"\nBaseline saved to {BASELINE.name} in the qa folder. Commit it and say why in the message.")
        return 0
    if not baseline:
        print("\nNo baseline yet. Create it once with: python -m qa.gate --update")
        return 2
    return 1 if any(not r["ok"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
