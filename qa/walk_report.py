"""
Walk test report (Week 6): the numbers of a walk test, from the paper sheets and the device.

    python -m qa.walk_report --new D:\\GRADUATION\\walk_test_1        # a folder with the 2 empty sheets
    (fill walk_log.csv and labels.csv in Excel, from the paper sheets)
    python -m qa.walk_report D:\\GRADUATION\\walk_test_1 --round wt1   # the report
    python -m qa.walk_report --demo                                   # try it on made-up data

Inputs, one CSV file per paper sheet (field testing sheets, pages 7 and 8):
  walk_log.csv  one line per walk:
                walk, route, walker, spotter, session, minutes_walked, collisions, safety_stops, notes
  labels.csv    one line per hazard or false alert, from the video:
                session, type (hazard or false_alert), seen_at, hazard, side (L/A/R), high_risk (Y/N),
                reached_2m_at, alert_at, result (Hit, Late or Miss), notes
  The device database (data/visionpath.db, copied from the Pi, or --db): the alerts it logged,
  the latency, and the minutes of a walk when minutes_walked is blank.

Outputs: report.md in the same folder, the same numbers on the screen, and one row per route
plus an "All" row in dashboard/walk_results.csv, so the dashboard shows them.

Rules (evaluation methodology):
  Hit = alert before the walker reached 2 m, Late = alert after it, Miss = no alert.
  A blank result is worked out from reached_2m_at and alert_at (mm:ss on the video).
  R2 recall = Hits / (Hits + Late + Misses), high-risk hazards only.
  R4 = false alerts / minutes walked x 10.
  R1 = p95 latency of the slowest stage, over all the sessions of the walks.

Exit code: 0 every measured target met, 1 a target not met, 2 a problem with the input.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dashboard.metrics import ROUNDS, read_walk_results, targets, walk_metrics, write_walk_results
from datalogger.config import REPO_ROOT, datalogger_settings, load_config
from datalogger.db import parse_utc_text
from datalogger.report import percentile

ROUND_KEYS = {"r1": ROUNDS[0], "r2": ROUNDS[1], "r3": ROUNDS[2], "wt1": ROUNDS[3], "wt2": ROUNDS[4]}
LOG_COLUMNS = ["walk", "route", "walker", "spotter", "session", "minutes_walked", "collisions", "safety_stops",
               "notes"]
LABEL_COLUMNS = ["session", "type", "seen_at", "hazard", "side", "high_risk", "reached_2m_at", "alert_at",
                 "result", "notes"]
RESULTS = ("Hit", "Late", "Miss")
RESULTS_KEY = "Hazards = high-risk hazards (recall counts only these), False = false alerts, Stops = safety stops."
DASHBOARD_CSV = REPO_ROOT / "dashboard" / "walk_results.csv"


class InputError(Exception):
    """A problem that stops the report: a missing file or column, or a value that cannot be read."""


# ---------- reading the sheets ----------

def read_sheet(path: Path, columns: list[str]) -> list[dict]:
    """Rows of a CSV saved by Excel: comma or semicolon, with or without the BOM. Blank rows dropped."""
    if not path.is_file():
        raise InputError(f"{path.name} is missing in {path.parent}")
    text = path.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in columns if c not in header]
    if missing:
        raise InputError(f"{path.name}: missing column(s) {', '.join(missing)}")
    rows = []
    for raw in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.get(c) for c in columns):
            rows.append(row)
    return rows


def seconds(text: str) -> float | None:
    """'5:30' -> 330. Blank -> None. Excel sometimes turns 5:30 into 05:30:00, which is read as 5:30 too."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise InputError(f"{text!r} is not a time like 5:30") from None
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        if nums[2] == 0 and nums[0] > 0:            # Excel: 5:30 became 05:30:00
            return nums[0] * 60 + nums[1]
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    raise InputError(f"{text!r} is not a time like 5:30")


def whole(text: str, what: str) -> int | None:
    if not (text or "").strip():
        return None
    try:
        return int(float(text))
    except ValueError:
        raise InputError(f"{what}: {text!r} is not a number") from None


def judge(row: dict, where: str, warnings: list) -> str | None:
    """Hit, Late or Miss for one hazard row, or None (and a warning) when it cannot be told."""
    given = row.get("result", "").strip().capitalize()
    if given and given not in RESULTS:
        warnings.append(f"{where}: result {row['result']!r} is not Hit, Late or Miss, so the row is left out")
        return None
    reached, alert = seconds(row.get("reached_2m_at")), seconds(row.get("alert_at"))
    if alert is None and reached is not None:
        worked = "Miss"
    elif alert is not None and reached is not None:
        worked = "Hit" if alert <= reached else "Late"
    else:
        worked = None
    if given:
        if worked and worked != given:
            warnings.append(f"{where}: the result says {given} but the times say {worked}. {given} is used")
        return given
    if worked is None:
        warnings.append(f"{where}: no result, and the times are not enough to tell, so the row is left out")
    return worked


# ---------- the device database ----------

def device_numbers(db: Path | None, sessions: set, warnings: list) -> dict:
    """session -> {minutes, alerts, latency: {stage: [ms]}} for the sessions found in the database."""
    out = {}
    if db is None or not Path(db).is_file():
        warnings.append(f"no device database at {db}: minutes come from the walk log only, and there is no p95")
        return out
    conn = sqlite3.connect(db)
    try:
        for s in sorted(sessions):
            row = conn.execute("SELECT started_at, ended_at FROM sessions WHERE session_id = ?", (s,)).fetchone()
            if row is None:
                warnings.append(f"session {s} is not in the device database")
                continue
            minutes = None
            if row[1]:
                minutes = round((parse_utc_text(row[1]) - parse_utc_text(row[0])).total_seconds() / 60, 2)
            alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE session_id = ? AND alert_type = 'obstacle'",
                                  (s,)).fetchone()[0]
            latency = defaultdict(list)
            for stage, value in conn.execute("SELECT stage, value FROM performance_metrics "
                                             "WHERE session_id = ? AND metric = 'latency_ms'", (s,)):
                latency[stage].append(value)
            out[s] = {"minutes": minutes, "alerts": alerts, "latency": latency}
    finally:
        conn.close()
    return out


def p95_of(latencies: list) -> float | None:
    """p95 of the slowest stage, all the given sessions together."""
    merged = defaultdict(list)
    for lat in latencies:
        for stage, values in lat.items():
            merged[stage] += values
    if not merged:
        return None
    return round(max(percentile(v, 95) for v in merged.values()), 1)


# ---------- putting it together ----------

@dataclass
class Walk:
    walk: str
    route: str
    walker: str
    spotter: str
    session: int | None
    minutes: float | None
    collisions: int
    safety_stops: int
    notes: str = ""
    alerts_logged: int | None = None
    latency: dict = field(default_factory=dict)
    hits: int = 0
    late: int = 0
    misses: int = 0
    low_risk: int = 0
    false_alerts: int = 0
    findings: list = field(default_factory=list)      # misses, late alerts and false alerts, for the report


def build(folder: Path, db: Path | None) -> tuple[list[Walk], list[str]]:
    warnings: list[str] = []
    log = read_sheet(folder / "walk_log.csv", LOG_COLUMNS)
    labels = read_sheet(folder / "labels.csv", LABEL_COLUMNS)
    if not log:
        raise InputError("walk_log.csv has no walks")
    walks, by_session = [], {}
    for i, r in enumerate(log, start=2):
        w = Walk(walk=r["walk"] or str(i - 1), route=(r["route"] or "?").upper(), walker=r["walker"],
                 spotter=r["spotter"], session=whole(r["session"], f"walk_log.csv line {i} session"),
                 minutes=None, collisions=whole(r["collisions"], f"walk_log.csv line {i} collisions") or 0,
                 safety_stops=whole(r["safety_stops"], f"walk_log.csv line {i} safety_stops") or 0, notes=r["notes"])
        if r["minutes_walked"]:
            try:
                w.minutes = float(r["minutes_walked"])
            except ValueError:
                raise InputError(f"walk_log.csv line {i}: minutes_walked {r['minutes_walked']!r} "
                                 "is not a number") from None
        if w.session is None:
            warnings.append(f"walk_log.csv line {i}: no session number, so its labels cannot be matched")
        elif w.session in by_session:
            raise InputError(f"walk_log.csv line {i}: session {w.session} is used by two walks")
        else:
            by_session[w.session] = w
        walks.append(w)

    device = device_numbers(db, set(by_session), warnings)
    for s, w in by_session.items():
        d = device.get(s)
        if d:
            w.alerts_logged, w.latency = d["alerts"], d["latency"]
            if w.minutes is None:
                w.minutes = d["minutes"]
        if w.minutes is None:
            warnings.append(f"walk {w.walk}: no minutes walked (blank in the walk log and not in the database)")

    for i, r in enumerate(labels, start=2):
        where = f"labels.csv line {i}"
        s = whole(r["session"], f"{where} session")
        w = by_session.get(s)
        if w is None:
            warnings.append(f"{where}: session {r['session'] or '(blank)'} is not in the walk log, "
                            "so the row is left out")
            continue
        kind = r["type"].lower().replace(" ", "_") or "hazard"
        if kind == "false_alert":
            w.false_alerts += 1
            w.findings.append(("False alert", r["alert_at"] or r["seen_at"], r["side"], r["notes"] or "-"))
            continue
        if kind != "hazard":
            warnings.append(f"{where}: type {r['type']!r} is not hazard or false_alert, so the row is left out")
            continue
        result = judge(r, where, warnings)
        if result is None:
            continue
        high = r["high_risk"].upper()[:1]
        if high not in ("Y", "N"):
            warnings.append(f"{where}: high_risk is blank or not Y/N, so it is counted as high-risk")
            high = "Y"
        if high == "N":
            w.low_risk += 1
            continue
        w.hits += result == "Hit"
        w.late += result == "Late"
        w.misses += result == "Miss"
        if result != "Hit":
            w.findings.append((result, r["seen_at"], r["side"], r["hazard"] or "?"))
    return walks, warnings


def totals(walks: list[Walk], name: str) -> dict:
    minutes = [w.minutes for w in walks if w.minutes is not None]
    return {
        "route": name, "walks": len(walks), "minutes_walked": round(sum(minutes), 2) if minutes else None,
        "hits": sum(w.hits for w in walks), "late": sum(w.late for w in walks),
        "misses": sum(w.misses for w in walks), "false_alerts": sum(w.false_alerts for w in walks),
        "collisions": sum(w.collisions for w in walks), "safety_stops": sum(w.safety_stops for w in walks),
        "p95_ms": p95_of([w.latency for w in walks]), "low_risk": sum(w.low_risk for w in walks),
        "alerts_logged": sum(w.alerts_logged or 0 for w in walks),
    }


def report(folder: Path, round_name: str, db: Path | None, cfg: dict) -> dict:
    walks, warnings = build(folder, db)
    t = targets(cfg)
    routes = sorted({w.route for w in walks})
    rows = [totals([w for w in walks if w.route == r], r) for r in routes] + [totals(walks, "All")]
    for row in rows:
        row.update(walk_metrics(row, t))
    return {"round": round_name, "folder": folder, "db": db, "walks": walks, "rows": rows, "targets": t,
            "warnings": warnings}


# ---------- writing it out ----------

def _n(value, digits=2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"
    return str(value)


def _yes(value) -> str:
    return "-" if value is None else ("Yes" if value else "NO")


def tables(r: dict) -> dict:
    """The report's tables as (header, rows) pairs, shared by the screen and report.md."""
    t, total = r["targets"], r["rows"][-1]
    results = (["Route", "Walks", "Minutes", "Hazards", "Hits", "Late", "Misses", "Recall",
                "False", "Per 10 min", "Collisions", "Stops", "p95 ms"],
               [[x["route"], x["walks"], _n(x["minutes_walked"], 1), _n(x["hazards"], 0), x["hits"], x["late"],
                 x["misses"], _n(x["recall"]), x["false_alerts"], _n(x["false_per_10min"]), x["collisions"],
                 x["safety_stops"], _n(x["p95_ms"], 0)] for x in r["rows"]])
    checks = (["Code", "Target", "Result", "Met?"],
              [["R2", f"Recall >= {t['recall_min']:g} (high-risk hazards)", _n(total["recall"]),
                _yes(total["R2_met"])],
               ["R4", f"False alerts <= {t['false_per_10min_max']:g} per 10 min", _n(total["false_per_10min"]),
                _yes(total["R4_met"])],
               ["R1", f"p95 latency <= {t['latency_p95_ms']:g} ms", _n(total["p95_ms"], 0), _yes(total["R1_met"])],
               ["R13", "Collisions (compared with the cane alone in W10)", total["collisions"], "-"]])
    per_walk = (["Walk", "Route", "Walker", "Spotter", "Session", "Minutes", "Alerts logged", "Hits", "Late",
                 "Misses", "False alerts", "Collisions", "Safety stops"],
                [[w.walk, w.route, w.walker or "-", w.spotter or "-", _n(w.session), _n(w.minutes, 1),
                  _n(w.alerts_logged), w.hits, w.late, w.misses, w.false_alerts, w.collisions, w.safety_stops]
                 for w in r["walks"]])
    findings = (["Walk", "Session", "What", "Time on video", "Side", "Hazard or cause"],
                [[w.walk, _n(w.session), *f] for w in r["walks"] for f in w.findings])
    return {"results": results, "checks": checks, "per_walk": per_walk, "findings": findings}


def text_table(header, rows) -> str:
    cells = [[str(c) for c in header]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(header))]
    lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(row)) for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join("  " + line for line in lines)


def md_table(header, rows) -> str:
    out = ["| " + " | ".join(str(c) for c in header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def verdict(r: dict) -> str:
    met = [r["rows"][-1][k] for k in ("R2_met", "R4_met", "R1_met")]
    if any(m is False for m in met):
        return "FAIL"
    return "PASS" if all(m is True for m in met) else "PASS (some targets not measured)"


def to_markdown(r: dict) -> str:
    tb, total = tables(r), r["rows"][-1]
    parts = [f"# {r['round']}: walk test report", "",
             f"Made by `python -m qa.walk_report` on {date.today().isoformat()} from the sheets in "
             f"`{r['folder']}` and the device database `{r['db']}`.", "",
             f"**Result: {verdict(r)}**", "", "## Targets", "", md_table(*tb["checks"]), "",
             "The R2 and R4 targets are proposals until Dr Khaled approves the evaluation methodology.", "",
             "## Results per route", "", md_table(*tb["results"]), "",
             f"{RESULTS_KEY} Low-risk hazards labelled too: {total['low_risk']}. "
             f"Obstacle alerts the device logged: {total['alerts_logged']}.", "",
             "## Each walk", "", md_table(*tb["per_walk"]), ""]
    parts += ["## Misses, late alerts and false alerts", "",
              "Look at these on the video and in the black box (replay_viewer).", "",
              md_table(*tb["findings"]) if tb["findings"][1] else "None.", ""]
    if r["warnings"]:
        parts += ["## Check these lines in the sheets", ""] + [f"- {w}" for w in r["warnings"]] + [""]
    return "\n".join(parts)


def to_screen(r: dict) -> str:
    tb = tables(r)
    out = [f"{r['round']}: walk test report", "", "Targets", text_table(*tb["checks"]), "",
           "Results per route", text_table(*tb["results"]), f"  {RESULTS_KEY}", ""]
    if r["warnings"]:
        out += ["Check these lines in the sheets:"] + [f"  - {w}" for w in r["warnings"]] + [""]
    out.append(f"RESULT: {verdict(r)}")
    return "\n".join(out)


def update_dashboard(r: dict, path: Path = DASHBOARD_CSV) -> int:
    """Replace this round's rows (same route and round) in the dashboard's CSV. Returns how many were written."""
    keep = [row for row in read_walk_results(path) if row["round"] != r["round"]
            or row["route"] not in {x["route"] for x in r["rows"]}]
    new = [{"route": x["route"], "round": r["round"], "walks": x["walks"], "minutes_walked": x["minutes_walked"],
            "hits": x["hits"], "late": x["late"], "misses": x["misses"], "false_alerts": x["false_alerts"],
            "collisions": x["collisions"], "p95_ms": x["p95_ms"], "notes": "from qa/walk_report.py"}
           for x in r["rows"]]
    write_walk_results(path, keep + new)
    return len(new)


def new_folder(folder: Path) -> list[Path]:
    """The two empty sheets, ready for Excel. Never overwrites."""
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for name, columns in (("walk_log.csv", LOG_COLUMNS), ("labels.csv", LABEL_COLUMNS)):
        p = folder / name
        if p.exists():
            raise InputError(f"{p} already exists, so nothing was changed")
        made.append(p)
    for p, columns in zip(made, (LOG_COLUMNS, LABEL_COLUMNS)):
        p.write_text(",".join(columns) + "\n", encoding="utf-8-sig")
    return made


# ---------- demo: made-up walk test, nothing of yours is changed ----------

DEMO_LOG = """walk,route,walker,spotter,session,minutes_walked,collisions,safety_stops,notes
1,A,Talal,Yasmin,1,6,0,0,indoor corridor
2,B,Mariam,Yasmin,2,,0,1,minutes from the device
"""
DEMO_LABELS = """session,type,seen_at,hazard,side,high_risk,reached_2m_at,alert_at,result,notes
1,hazard,0:20,chair,A,Y,0:31,0:27,,
1,hazard,1:05,door,R,Y,1:12,1:09,,
1,hazard,1:50,overhead sign,A,Y,2:01,1:58,Hit,
1,hazard,2:40,person,L,Y,2:46,2:48,,late: the person stepped out of a door
1,hazard,3:30,bin,R,Y,3:41,3:36,,
1,hazard,4:15,step,A,Y,4:30,4:22,Hit,
1,hazard,5:00,mat,A,N,5:08,,,
1,false_alert,,,L,,,2:10,,a wall poster
2,hazard,0:15,pole,R,Y,0:24,0:19,,
2,hazard,1:00,curb,A,Y,1:10,1:04,Hit,
2,hazard,1:45,parked car,L,Y,1:58,1:50,,
2,hazard,2:30,pothole,A,Y,2:41,2:35,,
2,hazard,3:20,tuktuk,L,Y,3:27,3:23,Hit,
2,hazard,4:05,pole,R,Y,4:14,4:09,,
2,false_alert,,,R,,,3:50,,shadow on the sidewalk
"""


def demo(tmp: Path, cfg: dict) -> dict:
    from datalogger.run import Service
    from datalogger.stub_stream import stub_stream

    folder = tmp / "walk_test_demo"
    folder.mkdir()
    (folder / "walk_log.csv").write_text(DEMO_LOG, encoding="utf-8")
    (folder / "labels.csv").write_text(DEMO_LABELS, encoding="utf-8")
    settings = datalogger_settings(cfg)
    settings.update(db_path=tmp / "visionpath.db", blackbox_dir=tmp / "blackbox")
    for minutes in (6.0, 5.5):                    # two sessions, as if the Pi had logged both walks
        lines = list(stub_stream(duration_s=60, fps=10))
        service = Service(settings, source_kind="replay", monotonic_start_ns=1_000_000_000_000)
        for line in lines:
            service.handle(line)
        sid = service.session_id
        service.close()
        conn = sqlite3.connect(settings["db_path"])
        start = conn.execute("SELECT started_at FROM sessions WHERE session_id = ?", (sid,)).fetchone()[0]
        conn.execute("UPDATE sessions SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', ?, ?) WHERE session_id = ?",
                     (start.rstrip("Z"), f"+{minutes * 60:g} seconds", sid))
        conn.commit()
        conn.close()
    return report(folder, ROUNDS[3], settings["db_path"], cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Walk test report from the paper sheets and the device database.")
    ap.add_argument("folder", nargs="?", help="folder with walk_log.csv and labels.csv")
    ap.add_argument("--round", choices=sorted(ROUND_KEYS), help="wt1, wt2 (walk tests) or r1, r2, r3 (route rounds)")
    ap.add_argument("--db", help="device database copied from the Pi (default: the one in config/visionpath.toml)")
    ap.add_argument("--new", metavar="FOLDER", help="make FOLDER with the two empty sheets")
    ap.add_argument("--no-dashboard", action="store_true", help="do not add the rows to dashboard/walk_results.csv")
    ap.add_argument("--demo", action="store_true", help="made-up walk test: try it without changing anything")
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
        if args.new:
            for p in new_folder(Path(args.new)):
                print(f"made {p}")
            print("Open them in Excel, one line per walk and one line per hazard or false alert, "
                  "then save as CSV.")
            return 0
        if args.demo:
            with tempfile.TemporaryDirectory() as tmp:
                r = demo(Path(tmp), cfg)
                print(to_screen(r))
                print("\nDemo only: made-up data, dashboard/walk_results.csv was not changed.")
            return 1 if verdict(r) == "FAIL" else 0
        if not args.folder or not args.round:
            ap.error("give the folder and --round (or --new FOLDER, or --demo)")
        db = Path(args.db) if args.db else datalogger_settings(cfg)["db_path"]
        r = report(Path(args.folder), ROUND_KEYS[args.round], db, cfg)
    except InputError as e:
        print(f"Problem: {e}")
        return 2
    out = Path(args.folder) / "report.md"
    out.write_text(to_markdown(r), encoding="utf-8")
    print(to_screen(r))
    print(f"\nReport written: {out}")
    if not args.no_dashboard:
        n = update_dashboard(r)
        print(f"Dashboard: {n} rows for {r['round']} in dashboard/walk_results.csv")
    return 1 if verdict(r) == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
