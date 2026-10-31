"""
The numbers behind the QA dashboard. No Streamlit here, so CI can test them.

Two sources:
  * the on-device database (data/visionpath.db): latency per session (R1),
    alerts and black box snapshots, written by the datalogger;
  * dashboard/walk_results.csv: the walk test results copied from the paper
    results summary (page 9 of the field testing sheets).

Targets: R1 comes from [targets] latency_p95_ms in config/visionpath.toml.
R2 and R4 are proposals until Dr Khaled confirms them (evaluation methodology),
so they live here with their defaults; [targets] hazard_recall_min and
false_alerts_per_10min_max in the config file override them if added.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from datalogger.report import latency_stats, verdict

WALK_COLUMNS = ["route", "round", "walks", "minutes_walked", "hits", "late", "misses",
                "false_alerts", "collisions", "p95_ms", "notes"]
NUMBER_COLUMNS = ["walks", "minutes_walked", "hits", "late", "misses", "false_alerts", "collisions", "p95_ms"]
ROUTES = ["A", "B", "C", "D", "E", "All"]
ROUNDS = ["Round 1 (W5)", "Round 2 (W8)", "Round 3 (W10)", "Walk test 1 (W6)", "Walk test 2 (W8)"]

DEFAULT_RECALL_MIN = 0.90          # R2, proposed
DEFAULT_FALSE_PER_10MIN_MAX = 2.0  # R4, proposed
DEFAULT_LATENCY_P95_MS = 300.0     # R1, used only if the config file has no target


def targets(cfg: dict | None) -> dict:
    """The three targets the dashboard checks, from the config file when present."""
    t = (cfg or {}).get("targets") or {}
    return {
        "latency_p95_ms": float(t.get("latency_p95_ms", DEFAULT_LATENCY_P95_MS)),
        "recall_min": float(t.get("hazard_recall_min", DEFAULT_RECALL_MIN)),
        "false_per_10min_max": float(t.get("false_alerts_per_10min_max", DEFAULT_FALSE_PER_10MIN_MAX)),
    }


# ---------- database: one row per session ----------

def session_rows(conn, latency_target_ms: float) -> list[dict]:
    """Oldest first: session, started_at, kind, slowest stage, its p95, PASS/FAIL, alerts, snapshots."""
    rows = []
    for sid, started, ended in conn.execute(
            "SELECT session_id, started_at, ended_at FROM sessions ORDER BY session_id"):
        stats = latency_stats(conn, sid)
        result, slowest = verdict(stats, latency_target_ms)
        kinds = sorted(k for (k,) in conn.execute(
            "SELECT DISTINCT source FROM performance_metrics WHERE session_id = ?", (sid,)) if k)
        alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE session_id = ?", (sid,)).fetchone()[0]
        snaps = conn.execute("SELECT COUNT(*) FROM blackbox_snapshots WHERE session_id = ?", (sid,)).fetchone()[0]
        rows.append({
            "session": sid,
            "started_at": started,
            "ended_at": ended,
            "kind": ", ".join(kinds),
            "slowest_stage": slowest["stage"] if slowest else None,
            "p95_ms": slowest["p95"] if slowest else None,
            "result": result,
            "alerts": alerts,
            "snapshots": snaps,
        })
    return rows


# ---------- walk test results (CSV) ----------

def number(value) -> float | None:
    """A cell as a number: blank, text or NaN -> None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def walk_metrics(row: dict, t: dict) -> dict:
    """
    Computed columns for one results row:
      hazards = hits + late + misses   (a blank late or misses counts as 0 once hits is filled)
      recall  = hits / hazards                                  (R2)
      false_per_10min = false_alerts / minutes_walked * 10      (R4)
    Each check is True, False, or None when the numbers are missing.
    """
    hits = number(row.get("hits"))
    late = number(row.get("late"))
    misses = number(row.get("misses"))
    false_alerts = number(row.get("false_alerts"))
    minutes = number(row.get("minutes_walked"))
    p95 = number(row.get("p95_ms"))

    hazards = None if hits is None else hits + (late or 0) + (misses or 0)
    recall = hits / hazards if hazards else None
    false_per_10min = false_alerts / minutes * 10 if false_alerts is not None and minutes else None
    return {
        "hazards": hazards,
        "recall": recall,
        "false_per_10min": false_per_10min,
        "R2_met": None if recall is None else recall >= t["recall_min"],
        "R4_met": None if false_per_10min is None else false_per_10min <= t["false_per_10min_max"],
        "R1_met": None if p95 is None else p95 <= t["latency_p95_ms"],
    }


def read_walk_results(path) -> list[dict]:
    """Rows of the CSV as dicts (missing file -> no rows). Accepts the BOM Excel adds."""
    p = Path(path)
    if not p.is_file():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return [{k: (r.get(k) or "") for k in WALK_COLUMNS} for r in csv.DictReader(f)]


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value).strip()


def write_walk_results(path, rows) -> int:
    """Write the rows that have a route; returns how many were written."""
    kept = [r for r in rows if _cell(r.get("route"))]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=WALK_COLUMNS)
        w.writeheader()
        for r in kept:
            w.writerow({k: _cell(r.get(k)) for k in WALK_COLUMNS})
    return len(kept)
