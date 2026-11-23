"""
End-to-end check (Week 8): everything Yasmin built, working together, from the first message to the last.

    python -m qa.e2e                                  # on the laptop: two made-up walks through the whole chain
    python -m qa.e2e --db data/visionpath.db          # on the Pi: check the last real session
    python -m qa.e2e --db data/visionpath.db --session 12

On the laptop, two walks of the same route (the stub sensor stream with a pedestrian and a
near miss, plus four fixed landmarks) go through:
  1 datalogger    every message logged, nothing skipped
  2 latency       the latency report: p95 of the slowest stage within the target (R1)
  3 black box     the near miss saved a snapshot file and its database row
  4 Rerun         the snapshot opens as a timeline in the black box viewer
  5 dashboard     both walks appear in the dashboard's speed tab, with alerts and snapshots
  6 landmarks     walk 2 recognises the 4 landmarks; the pedestrian and the new pothole are not
  7 familiar      walk 2 speaks fewer alerts for the landmarks; no urgent or moving alert is quieter
  8 wellbeing     the 11 cases pass, and their checks are saved in walk 2's session
  9 walk report   the walk test report reads both sessions from the database and fills the dashboard

On the Pi (--db), a real session recorded with the real sensors is checked: the session ended,
every pipeline stage sent messages, the latency is within the target, every alert reached the
output, and the black box files exist and hold real camera and ultrasonic messages.

Exit code: 0 every step passed, 1 a step failed, 2 a problem with the input.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

from dashboard.metrics import read_walk_results, session_rows
from datalogger.blackbox import load_snapshot
from datalogger.config import datalogger_settings, latency_target_ms, load_config
from datalogger.db import mono_to_utc, session_clock
from datalogger.report import latency_stats, verdict
from datalogger.stub_stream import stub_stream
from landmarks.demo import NEW_ON_WALK_2
from landmarks.fake import LANDMARKS, FakeFingerprints, fake_walk
from landmarks.familiar import FamiliarAlerts
from landmarks.familiar_demo import FakeRisk, commands, urgent
from landmarks.memory import STATIC_CLASSES, LandmarkMemory, Selector, landmark_place
from landmarks.store import open_store

START = 1_000_000_000_000
WALK_SPEED = 1.2                         # the fake walk goes north at 1.2 m/s (stand-in for Mariam's localization)
LANDMARK_SEQ = 100_000                   # the landmark tracks get their own seq range in the merged stream
PIPELINE = ("detection_set", "fused_obstacle", "track", "risk_alert", "output_command")


# ---------- the two made-up walks ----------

def walk_lines(walk: int) -> list[str]:
    """The stub sensor stream (pedestrian, near miss) merged with the fake walk's landmark tracks and alerts."""
    timed = [(json.loads(line)["t_mono_ns"], i, line) for i, line in enumerate(stub_stream(duration_s=60, fps=10))]
    risk = FakeRisk()
    risk.alert_id = 1000                 # alert ids of their own, after the stub's
    landmarks = LANDMARKS + ([NEW_ON_WALK_2] if walk == 2 else [])
    n = len(timed)
    for track, _ in fake_walk(landmarks, pedestrian=False, start_ns=START):
        track.seq += LANDMARK_SEQ
        out = [track] + [m for a in risk.alerts(track) for m in [a] + commands(a)]
        for m in out:
            n += 1
            timed.append((m.t_mono_ns, n, m.to_json()))
    return [line for _, _, line in sorted(timed)]


def user_place(t_ns: int) -> tuple[float, float]:
    return 0.0, round((t_ns - START) / 1e9 * WALK_SPEED, 3)


class Steps:
    def __init__(self):
        self.rows: list[tuple[int, str, bool, str]] = []
        self.info = ""                   # a line for information, not a check

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.rows.append((len(self.rows) + 1, name, bool(ok), detail))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.rows) and all(ok for _, _, ok, _ in self.rows)


def log_walks(settings: dict) -> list[dict]:
    """Walk 1 and walk 2 through the datalogger, one session each (60 s of replay = 1 minute walked)."""
    from datalogger.run import Service
    walks = []
    for walk in (1, 2):
        lines = walk_lines(walk)
        service = Service(settings, source_kind="replay", monotonic_start_ns=START)
        for line in lines:
            service.handle(line)
        info = {"walk": walk, "lines": lines, "session": service.session_id,
                "logged": sum(service.logger.counts.values()), "skipped": sum(service.logger.skipped.values()),
                "snapshots": list(service.box.snapshots)}
        service.close()
        conn = sqlite3.connect(settings["db_path"])
        started, mono_s = conn.execute("SELECT started_at, monotonic_start_s FROM sessions WHERE session_id = ?",
                                       (info["session"],)).fetchone()
        conn.execute("UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                     (mono_to_utc(started, mono_s, START + 60_000_000_000), info["session"]))
        conn.commit()
        conn.close()
        walks.append(info)
    return walks


def landmarks_and_familiar(walks: list[dict], folder: Path) -> dict:
    """Steps 6 and 7: the landmark memory and the familiar-route rule, on the logged tracks and alerts."""
    store = open_store(folder, "simple")
    out = {}
    try:
        memory, fp = LandmarkMemory(store), FakeFingerprints()
        for w in walks:
            selector, familiar = Selector(), FamiliarAlerts()
            seen, fingerprinted, spoken, wrongly_quiet = set(), set(), Counter(), []
            quiet_alerts, moving = {}, Counter()
            for n, line in enumerate(w["lines"]):
                d = json.loads(line)
                kind = d["msg_type"]
                if kind == "track":
                    from contracts import from_dict
                    track = from_dict(d)
                    familiar.see(track)
                    if not selector.wants(track):
                        continue
                    fingerprinted.add(track.hazard.value)
                    place = landmark_place(user_place(track.t_mono_ns), 0.0, track.distance_m, track.bearing_deg)
                    answer = memory.remember(fp.of(track.hazard.value, place, sighting=w["walk"] * 100_000 + n),
                                             track.hazard.value, place, session=w["session"],
                                             t=round(track.t_mono_ns / 1e9, 2), track_id=track.track_id)
                    familiar.note(answer)
                    if answer["seen_before"]:
                        seen.add(track.hazard.value)
                elif kind == "risk_alert":
                    decision = familiar.feed(line)
                    quiet_alerts[d["alert_id"]] = (decision, d)
                    if d["hazard"] not in STATIC_CLASSES:          # the stub's pedestrian: always in full
                        moving["alerts"] += 1
                        moving["full"] += decision.full
                elif kind == "output_command" and d.get("channel") == "voice" and d.get("alert_id") in quiet_alerts:
                    decision, alert = quiet_alerts[d["alert_id"]]
                    if decision.full:
                        spoken[alert["hazard"]] += 1
                    else:
                        from contracts import from_dict
                        a = from_dict(alert)
                        if urgent(a, familiar) or a.hazard.value not in STATIC_CLASSES:
                            wrongly_quiet.append(alert)
            out[w["walk"]] = {"seen": seen, "fingerprinted": fingerprinted, "spoken": spoken,
                              "wrongly_quiet": wrongly_quiet, "moving": moving}
    finally:
        store.close()
    return out


def wellbeing_into(db: Path, session: int) -> tuple[int, int, int]:
    """Step 8: the 11 cases, their checks saved in the walk's session. Returns (passed, saved rows, cases)."""
    from wellbeing import cases as wc
    from wellbeing.check import save
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        started, mono_s = session_clock(conn, session)
        passed = saved = 0
        all_cases = wc.cases()
        for case in all_cases:
            got, w = wc.replay(case)
            passed += all(got.get(k) == v for k, v in case.expect.items())
            for check in w.checks:
                save(conn, check, session, 1, lambda t: mono_to_utc(started, mono_s, t))
        saved = conn.execute("SELECT COUNT(*) FROM wellbeing_checks WHERE session_id = ?", (session,)).fetchone()[0]
    finally:
        conn.close()
    return passed, saved, len(all_cases)


def walk_report_step(folder: Path, db: Path, walks: list[dict], cfg: dict) -> dict:
    """Step 9: the walk test report on the two logged sessions, into a copy of the dashboard's CSV."""
    from qa import walk_report as wr
    sheets = folder / "walk_test"
    sheets.mkdir()
    s1, s2 = walks[0]["session"], walks[1]["session"]
    (sheets / "walk_log.csv").write_text(
        ",".join(wr.LOG_COLUMNS) + f"\n1,A,Talal,Yasmin,{s1},,0,0,\n2,A,Mariam,Yasmin,{s2},,0,0,\n", encoding="utf-8")
    (sheets / "labels.csv").write_text(
        ",".join(wr.LABEL_COLUMNS) + "\n"
        + "".join(f"{s},hazard,0:{t:02d},{h},A,Y,0:{t + 6:02d},0:{t + 2:02d},,\n"
                  for s in (s1, s2) for t, h in ((12, "pole"), (25, "step"), (35, "overhead"), (46, "curb")))
        + f"{s1},hazard,0:40,pedestrian,A,Y,0:44,0:42,,\n{s2},hazard,0:40,pedestrian,A,Y,0:44,0:42,,\n",
        encoding="utf-8")
    r = wr.report(sheets, wr.ROUND_KEYS["wt2"], db, cfg)
    dash = folder / "walk_results.csv"
    rows = wr.update_dashboard(r, dash)
    return {"report": r, "dashboard_rows": len(read_walk_results(dash)), "written": rows}


def run_laptop(cfg: dict) -> Steps:
    steps = Steps()
    target = latency_target_ms(cfg) or 300.0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = datalogger_settings(cfg)
        settings.update(db_path=tmp / "visionpath.db", blackbox_dir=tmp / "blackbox")
        walks = log_walks(settings)
        db = settings["db_path"]

        steps.add("Datalogger", all(w["logged"] == len(w["lines"]) and w["skipped"] == 0 for w in walks),
                  "; ".join(f"walk {w['walk']}: {w['logged']} of {len(w['lines'])} messages logged, "
                            f"{w['skipped']} skipped" for w in walks))

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            results = [verdict(latency_stats(conn, w["session"]), target) for w in walks]
            steps.add("Latency (R1)", all(r == "PASS" for r, _ in results),
                      "; ".join(f"walk {w['walk']}: {s['stage']} p95 {s['p95']:.0f} ms {r} (target {target:g} ms)"
                                for w, (r, s) in zip(walks, results)))

            snaps = walks[0]["snapshots"]
            rows = conn.execute("SELECT trigger_type, file_path FROM blackbox_snapshots WHERE session_id = ?",
                                (walks[0]["session"],)).fetchall()
            parsed = all(load_snapshot(p) for p in snaps)
            near = [r for r in rows if r["trigger_type"] == "near_miss"]
            steps.add("Black box", bool(near) and len(rows) == len(snaps) and all(Path(p).is_file() for p in snaps)
                      and parsed, f"walk 1: {len(snaps)} snapshot files ({len(near)} near miss), "
                                  f"{len(rows)} database rows, every line a valid message")

            from replay_viewer.timeline import load as load_timeline
            tl = load_timeline(snaps[0], latency_target_ms=target) if snaps else None
            saved = bool(tl) and any(e.kind == "text" and "Black box saved" in e.data.get("text", "")
                                     for e in tl.events)
            steps.add("Rerun viewer", bool(tl) and tl.messages > 0 and saved,
                      f"the first snapshot opens as a timeline: {tl.messages if tl else 0} messages, "
                      f"{len(tl.events) if tl else 0} things to draw" if tl else "no snapshot to open")

            dash = {r["session"]: r for r in session_rows(conn, target)}
            mine = [dash.get(w["session"]) for w in walks]
            steps.add("Dashboard", all(m and m["result"] == "PASS" and m["alerts"] > 0 for m in mine)
                      and mine[0]["snapshots"] >= 1,
                      "; ".join(f"session {m['session']}: {m['result']}, {m['alerts']} alerts, "
                                f"{m['snapshots']} snapshots" for m in mine if m))
        finally:
            conn.close()

        lm = landmarks_and_familiar(walks, tmp / "landmarks")
        known = {h for h, _ in LANDMARKS}
        w1, w2 = lm[1], lm[2]
        steps.add("Landmark memory", w2["seen"] == known and not w1["seen"] and "pedestrian" not in w1["fingerprinted"]
                  and NEW_ON_WALK_2[0] not in w2["seen"],
                  f"walk 2 recognised {', '.join(sorted(w2['seen'])) or 'nothing'}; the new pothole is new; "
                  "the pedestrian is never stored")

        s1 = sum(w1["spoken"][h] for h in known)
        s2 = sum(w2["spoken"][h] for h in known)
        ped = w2["moving"]
        steps.add("Familiar route", s2 < s1 and not w1["wrongly_quiet"] and not w2["wrongly_quiet"]
                  and ped["alerts"] > 0 and ped["full"] == ped["alerts"],
                  f"spoken landmark alerts {s1} -> {s2}; pedestrian alerts in full {ped['full']} of {ped['alerts']}; "
                  f"urgent or moving alerts made quieter: {len(w1['wrongly_quiet']) + len(w2['wrongly_quiet'])}")

        passed, saved, total = wellbeing_into(db, walks[1]["session"])
        steps.add("Wellbeing check", passed == total == 11 and saved > 0,
                  f"{passed} of {total} cases pass; {saved} checks saved in walk 2's session")

        wr = walk_report_step(tmp, db, walks, cfg)
        total_row = wr["report"]["rows"][-1]
        steps.add("Walk test report", total_row["minutes_walked"] == 2.0 and total_row["p95_ms"] is not None
                  and wr["dashboard_rows"] == 2,
                  f"read both sessions from the database: {total_row['minutes_walked']:g} minutes, "
                  f"p95 {total_row['p95_ms']:g} ms, recall {total_row['recall']:.2f}; "
                  f"{wr['dashboard_rows']} rows in the dashboard")
    return steps


# ---------- a real session, on the Pi ----------

def find_snapshot(path: Path, db: Path, cfg: dict) -> Path | None:
    """The snapshot file as recorded, or with the same name next to the database or in the black box folder
    (when the database was copied from the Pi to the laptop)."""
    for p in (path, db.parent / path.name, db.parent / "blackbox" / path.name,
              Path(datalogger_settings(cfg)["blackbox_dir"]) / path.name):
        if p.is_file():
            return p
    return None


def run_db(db: Path, session: int | None, cfg: dict) -> Steps:
    steps = Steps()
    target = latency_target_ms(cfg) or 300.0
    if not db.is_file():
        raise FileNotFoundError(f"no database at {db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        if session is None:
            row = conn.execute("SELECT MAX(session_id) FROM sessions").fetchone()
            session = row[0]
        s = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session,)).fetchone() if session else None
        if s is None:
            raise FileNotFoundError(f"no session {session} in {db}")
        steps.add("Session", s["ended_at"] is not None,
                  f"session {session}: {s['started_at']} -> {s['ended_at'] or 'still running (stop the datalogger first)'}")

        stats = latency_stats(conn, session)
        have = {st["stage"]: st["n"] for st in stats}
        missing = [p for p in PIPELINE if not have.get(p)]
        steps.add("Every stage sent messages", not missing,
                  ", ".join(f"{p} {have.get(p, 0)}" for p in PIPELINE)
                  + (f". Missing: {', '.join(missing)}: is that module running?" if missing else ""))

        result, slowest = verdict(stats, target)
        steps.add("Latency (R1)", result == "PASS",
                  f"{slowest['stage']} p95 {slowest['p95']:.0f} ms (target {target:g} ms): {result}" if slowest
                  else "no latency data")

        alerts = conn.execute("SELECT COUNT(*), SUM(channel IS NOT NULL) FROM alerts WHERE session_id = ?",
                              (session,)).fetchone()
        n_alerts, played = alerts[0], alerts[1] or 0
        steps.add("Alerts reach the output", n_alerts > 0 and played == n_alerts,
                  f"{n_alerts} alerts, {played} with a vibration or voice command"
                  + ("" if n_alerts else ". Walk towards an obstacle so at least one alert fires"))

        snaps = conn.execute("SELECT trigger_type, file_path FROM blackbox_snapshots WHERE session_id = ?",
                             (session,)).fetchall()
        found, real, kinds = [], False, Counter()
        for r in snaps:
            p = find_snapshot(Path(r["file_path"]), db, cfg)
            if p is not None:
                found.append(p)
                msgs = load_snapshot(p)
                kinds.update(m.MSG_TYPE for m in msgs)
                cams = [m for m in msgs if m.MSG_TYPE == "camera_frame"]
                real = real or any(m.capture.value != "replay" for m in cams)
        sensors = kinds["camera_frame"] > 0 and kinds["ultrasonic_reading"] > 0
        steps.add("Black box on the real sensors", bool(snaps) and len(found) == len(snaps) and sensors and real,
                  (f"{len(snaps)} snapshots, {len(found)} files found; camera frames {kinds['camera_frame']}, "
                   f"ultrasonic readings {kinds['ultrasonic_reading']}; "
                   + ("real camera" if real else "camera capture is 'replay': these are not the real sensors"))
                  if snaps else "no black box snapshot in this session. Get close to an obstacle (a near miss)")

        wb = conn.execute("SELECT COUNT(*) FROM wellbeing_checks WHERE session_id = ?", (session,)).fetchone()[0]
        faults = conn.execute("SELECT COUNT(*) FROM faults WHERE session_id = ?", (session,)).fetchone()[0]
        steps.info = f"For information: {wb} wellbeing checks and {faults} faults in this session."
    finally:
        conn.close()
    return steps


def show(steps: Steps, title: str) -> None:
    print(title + "\n")
    width = max(len(name) for _, name, _, _ in steps.rows)
    for number, name, ok, detail in steps.rows:
        print(f"  {number:<3}{name:<{width + 2}}{'ok' if ok else 'FAIL'}")
        for part in detail.split("; "):
            print(f"       {part}")
    done = sum(ok for _, _, ok, _ in steps.rows)
    if steps.info:
        print(f"\n{steps.info}")
    print(f"\nRESULT: {'PASS' if steps.passed else 'FAIL'} ({done} of {len(steps.rows)} steps)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="End-to-end check of the logging, dashboard, black box, "
                                             "landmark memory and wellbeing check.")
    ap.add_argument("--db", help="check a real session in this database (on the Pi: data/visionpath.db)")
    ap.add_argument("--session", type=int, help="with --db: the session to check (default: the last one)")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.db:
        try:
            steps = run_db(Path(args.db), args.session, cfg)
        except FileNotFoundError as e:
            print(f"Problem: {e}")
            return 2
        show(steps, "End-to-end check of a real session")
    else:
        steps = run_laptop(cfg)
        show(steps, "End-to-end check: two walks of the same route through the whole chain (made-up data)")
    return 0 if steps.passed else 1


if __name__ == "__main__":
    sys.exit(main())
