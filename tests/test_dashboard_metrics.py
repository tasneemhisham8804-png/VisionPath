"""QA dashboard numbers (Week 3). Run with: python -m unittest discover -s tests -v

Only dashboard.metrics is tested here: it does not need Streamlit, so CI runs it.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import metrics as M
from datalogger import config, run
from datalogger.stub_stream import stub_stream

START_NS = 1_000_000_000_000
T = M.targets({"targets": {"latency_p95_ms": 300}})


class Targets(unittest.TestCase):
    def test_defaults_and_config_override(self):
        self.assertEqual(M.targets({}), {"latency_p95_ms": 300.0, "recall_min": 0.90, "false_per_10min_max": 2.0})
        t = M.targets({"targets": {"latency_p95_ms": 250, "hazard_recall_min": 0.85}})
        self.assertEqual((t["latency_p95_ms"], t["recall_min"]), (250.0, 0.85))


class WalkMetrics(unittest.TestCase):
    def test_example_from_the_sheets(self):
        """18 hits, 1 late, 1 miss -> recall 0.90; 3 false alerts in 20 minutes -> 1.5."""
        m = M.walk_metrics({"hits": "18", "late": "1", "misses": "1", "false_alerts": "3",
                            "minutes_walked": "20", "p95_ms": "75"}, T)
        self.assertEqual(m["hazards"], 20)
        self.assertAlmostEqual(m["recall"], 0.90)
        self.assertAlmostEqual(m["false_per_10min"], 1.5)
        self.assertEqual((m["R2_met"], m["R4_met"], m["R1_met"]), (True, True, True))

    def test_targets_missed(self):
        m = M.walk_metrics({"hits": 8, "late": 1, "misses": 1, "false_alerts": 5,
                            "minutes_walked": 10, "p95_ms": 320}, T)
        self.assertEqual((m["R2_met"], m["R4_met"], m["R1_met"]), (False, False, False))

    def test_blank_late_and_misses_count_as_zero_once_hits_is_filled(self):
        self.assertEqual(M.walk_metrics({"hits": "10", "late": "", "misses": None}, T)["recall"], 1.0)

    def test_missing_numbers_give_no_verdict(self):
        m = M.walk_metrics({"route": "A"}, T)
        self.assertEqual((m["recall"], m["false_per_10min"], m["R2_met"], m["R4_met"], m["R1_met"]),
                         (None, None, None, None, None))
        self.assertIsNone(M.walk_metrics({"false_alerts": 2, "minutes_walked": 0}, T)["false_per_10min"])

    def test_number_reads_cells(self):
        self.assertEqual([M.number(v) for v in ("3", " 2.5 ", "", "abc", None, float("nan"), 4)],
                         [3.0, 2.5, None, None, None, None, 4.0])


class WalkCsv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "walk_results.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_keeps_only_rows_with_a_route(self):
        rows = [{"route": "A", "round": "Round 1 (W5)", "walks": 3.0, "minutes_walked": 6.5, "hits": 9.0,
                 "late": float("nan"), "notes": "dry"},
                {"route": None, "hits": 4}]
        self.assertEqual(M.write_walk_results(self.path, rows), 1)
        back = M.read_walk_results(self.path)
        self.assertEqual(len(back), 1)
        self.assertEqual((back[0]["walks"], back[0]["minutes_walked"], back[0]["late"], back[0]["notes"]),
                         ("3", "6.5", "", "dry"))

    def test_missing_file_and_excel_bom(self):
        self.assertEqual(M.read_walk_results(self.path), [])
        self.path.write_text("﻿route,round,hits\nB,Round 2 (W8),7\n", encoding="utf-8")
        row = M.read_walk_results(self.path)[0]
        self.assertEqual((row["route"], row["hits"], row["late"]), ("B", "7", ""))

    def test_the_repo_csv_has_the_right_header(self):
        header = (config.REPO_ROOT / "dashboard" / "walk_results.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(header.split(","), M.WALK_COLUMNS)


class Sessions(unittest.TestCase):
    def test_a_logged_stub_session(self):
        with tempfile.TemporaryDirectory() as d:
            settings = config.datalogger_settings({"datalogger": {
                "db_path": str(Path(d) / "vp.db"), "blackbox_dir": str(Path(d) / "bb")}})
            service = run.Service(settings, source_kind="replay", monotonic_start_ns=START_NS)
            for line in stub_stream(duration_s=60, start_ns=START_NS):
                service.handle(line)
            service.close()
            conn = sqlite3.connect(Path(d) / "vp.db")
            try:
                rows = M.session_rows(conn, 300.0)
            finally:
                conn.close()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["session"], r["kind"], r["result"], r["alerts"], r["snapshots"]),
                         (1, "replay", "PASS", 2, 1))
        self.assertEqual(r["slowest_stage"], "output_command")
        self.assertLessEqual(r["p95_ms"], 300.0)

    def test_empty_database(self):
        with tempfile.TemporaryDirectory() as d:
            from datalogger.db import open_db
            conn = open_db(Path(d) / "empty.db")
            try:
                self.assertEqual(M.session_rows(conn, 300.0), [])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
