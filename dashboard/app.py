"""
VisionPath QA dashboard (Week 3).

    streamlit run dashboard/app.py

Tab 1, Speed (R1): reads the on-device database written by the datalogger.
Tab 2, Walk tests (R2, R4): the results from the paper sheets, typed into a
table here and saved to dashboard/walk_results.csv.
"""

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:          # streamlit runs this file as a script
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd                          # noqa: E402  (installed with streamlit)
import streamlit as st                       # noqa: E402

from dashboard import metrics as M           # noqa: E402
from datalogger.config import ConfigError, datalogger_settings, load_config   # noqa: E402
from datalogger.report import latency_stats  # noqa: E402

WALK_CSV = Path(__file__).resolve().parent / "walk_results.csv"


def mark(ok):
    return "" if ok is None else ("Yes" if ok else "No")


st.set_page_config(page_title="VisionPath QA", layout="wide")
st.title("VisionPath QA dashboard")

try:
    cfg = load_config()
    db_path = Path(datalogger_settings(cfg)["db_path"])
except ConfigError as e:
    st.error(f"Config error: {e}")
    st.stop()
T = M.targets(cfg)
st.caption(f"Targets: R1 p95 ≤ {T['latency_p95_ms']:g} ms · "
           f"R2 recall ≥ {T['recall_min']:g} · R4 ≤ {T['false_per_10min_max']:g} "
           f"false alerts per 10 minutes")

tab_speed, tab_walk = st.tabs(["Speed (R1)", "Walk tests (R2, R4)"])

# ---------------- Tab 1: speed from the database ----------------
with tab_speed:
    if not db_path.is_file():
        st.info(f"No database yet at {db_path}. Run the datalogger first: python -m datalogger.run")
    else:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = M.session_rows(conn, T["latency_p95_ms"])
            measured = [r for r in rows if r["p95_ms"] is not None]
            if not measured:
                st.info("The database has no latency yet. Run the datalogger with the stub publisher.")
            else:
                last = measured[-1]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Sessions", len(rows))
                c2.metric("Last session p95", f"{last['p95_ms']:.0f} ms")
                c3.metric("Target", f"≤ {T['latency_p95_ms']:g} ms")
                c4.metric("Last session", last["result"])

                st.subheader("p95 of the slowest stage, per session")
                chart = pd.DataFrame({
                    "session": [r["session"] for r in measured],
                    "p95 (ms)": [r["p95_ms"] for r in measured],
                    "target (ms)": [T["latency_p95_ms"]] * len(measured),
                }).set_index("session")
                st.line_chart(chart)

                st.subheader("Sessions, newest first")
                table = pd.DataFrame([{
                    "Session": r["session"],
                    "Started": r["started_at"],
                    "Kind": r["kind"],
                    "Slowest stage": r["slowest_stage"],
                    "p95 (ms)": r["p95_ms"],
                    "Result": r["result"],
                    "Alerts": r["alerts"],
                    "Black box": r["snapshots"],
                } for r in reversed(rows)])
                st.dataframe(table, hide_index=True)

                st.subheader("One session, stage by stage")
                pick = st.selectbox("Session", [r["session"] for r in reversed(measured)])
                stages = pd.DataFrame(latency_stats(conn, pick)).rename(columns={
                    "stage": "Stage", "module": "Module", "n": "Messages",
                    "p50": "p50 (ms)", "p95": "p95 (ms)", "max": "Max (ms)"})
                st.dataframe(stages, hide_index=True)
                st.caption("Each stage includes the stages before it, so the slowest stage is "
                           "what the user feels. Stages with fewer than 20 messages: read p95 with care.")
        finally:
            conn.close()

# ---------------- Tab 2: walk test results ----------------
with tab_walk:
    st.write("Type the numbers from the paper results summary (page 9), one line per route and round, "
             "then press **Save**. Recall, false alerts per 10 minutes and the checks are computed below.")

    data = pd.DataFrame(M.read_walk_results(WALK_CSV), columns=M.WALK_COLUMNS)
    for col in M.NUMBER_COLUMNS:
        data[col] = pd.to_numeric(data[col], errors="coerce").astype("float64")
    for col in ("route", "round", "notes"):
        data[col] = data[col].astype("object").where(data[col].notna() & (data[col] != ""), None)

    edited = st.data_editor(
        data,
        num_rows="dynamic",
        hide_index=True,
        key="walk_results",
        column_config={
            "route": st.column_config.SelectboxColumn("Route", options=M.ROUTES),
            "round": st.column_config.SelectboxColumn("Round", options=M.ROUNDS),
            "walks": st.column_config.NumberColumn("Walks", min_value=0, step=1),
            "minutes_walked": st.column_config.NumberColumn("Minutes walked", min_value=0),
            "hits": st.column_config.NumberColumn("Hits", min_value=0, step=1),
            "late": st.column_config.NumberColumn("Late", min_value=0, step=1),
            "misses": st.column_config.NumberColumn("Misses", min_value=0, step=1),
            "false_alerts": st.column_config.NumberColumn("False alerts", min_value=0, step=1),
            "collisions": st.column_config.NumberColumn("Collisions", min_value=0, step=1),
            "p95_ms": st.column_config.NumberColumn("p95 (ms)", min_value=0),
            "notes": st.column_config.TextColumn("Notes"),
        },
    )
    saved = st.session_state.pop("walk_saved", None)
    if saved is not None:
        st.success(f"Saved {saved} {'line' if saved == 1 else 'lines'} to dashboard/walk_results.csv")
    if st.button("Save"):
        st.session_state["walk_saved"] = M.write_walk_results(WALK_CSV, edited.to_dict("records"))
        st.session_state.pop("walk_results", None)   # the file now holds the edits: start the table fresh
        st.rerun()

    results = []
    for r in edited.to_dict("records"):
        if not r.get("route"):
            continue
        m = M.walk_metrics(r, T)
        results.append({
            "Route": r["route"],
            "Round": r.get("round") or "",
            "Hazards": m["hazards"],
            "Recall": None if m["recall"] is None else round(m["recall"], 2),
            "R2 met": mark(m["R2_met"]),
            "False alerts per 10 min": None if m["false_per_10min"] is None else round(m["false_per_10min"], 1),
            "R4 met": mark(m["R4_met"]),
            "p95 (ms)": M.number(r.get("p95_ms")),
            "R1 met": mark(m["R1_met"]),
            "Collisions": M.number(r.get("collisions")),
        })
    st.subheader("Results")
    if results:
        st.dataframe(pd.DataFrame(results), hide_index=True)
    else:
        st.info("No walk test results yet. They start with round 1 of the benchmark routes.")
