"""Latency report (p95 against the target). Run with: python -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path

from datalogger.db import open_db, start_session
from datalogger.logger import Logger
from datalogger.report import latency_stats, percentile, session_report, verdict
from datalogger.stub_stream import stub_stream

START_NS = 1_000_000_000_000


class Percentile(unittest.TestCase):
    def test_nearest_rank(self):
        values = list(range(1, 101))                  # 1..100
        self.assertEqual(percentile(values, 50), 50)
        self.assertEqual(percentile(values, 95), 95)
        self.assertEqual(percentile(values, 100), 100)
        self.assertEqual(percentile([7], 95), 7)
        self.assertEqual(percentile([3, 1, 2], 95), 3)   # order of input does not matter


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.tmp.name) / "test.db")
        self.session = start_session(self.conn, user_id=1, monotonic_start_ns=START_NS)
        log = Logger(self.conn, self.session)
        for line in stub_stream(duration_s=60, fps=30, start_ns=START_NS):
            log.handle(line)
        log.flush()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_stats_per_stage_in_pipeline_order(self):
        stats = latency_stats(self.conn, self.session)
        self.assertEqual([s["stage"] for s in stats],
                         ["detection_set", "fused_obstacle", "track", "risk_alert", "output_command"])
        p95 = {s["stage"]: s["p95"] for s in stats}
        self.assertEqual(p95["detection_set"], 38.0)
        self.assertEqual(p95["output_command"], 75.0)

    def test_pass_and_fail_against_the_target(self):
        stats = latency_stats(self.conn, self.session)
        self.assertEqual(verdict(stats, 300)[0], "PASS")
        result, slowest = verdict(stats, 50)
        self.assertEqual((result, slowest["stage"]), ("FAIL", "output_command"))
        self.assertEqual(verdict(stats, None)[0], "NO TARGET")
        self.assertEqual(verdict([], 300)[0], "NO DATA")

    def test_report_text(self):
        text, result = session_report(self.conn, self.session, 300)
        self.assertEqual(result, "PASS")
        self.assertIn("output_command", text)
        self.assertIn("alerts: 2 (critical 1, high 1)", text)
        self.assertIn("(few samples)", text)               # 2 alerts: p95 = max, read with care

    def test_unknown_session(self):
        self.assertEqual(session_report(self.conn, 999, 300)[1], "NO DATA")


if __name__ == "__main__":
    unittest.main()
