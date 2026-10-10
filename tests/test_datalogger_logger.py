"""Metrics logging pipeline (Week 2). Run with: python -m unittest discover -s tests -v"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import from_json
from datalogger.db import open_db, start_session
from datalogger.logger import Logger
from datalogger.stub_stream import stub_stream

START_NS = 1_000_000_000_000


class LoggerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.conn = open_db(self.path)
        self.session = start_session(self.conn, user_id=1, started_at="2026-10-10T13:00:00.000Z",
                                     monotonic_start_ns=START_NS)
        self.log = Logger(self.conn, self.session)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def feed_stub(self, **kw):
        kw.setdefault("duration_s", 60)
        for line in stub_stream(start_ns=START_NS, **kw):
            self.assertTrue(self.log.handle(line))
        self.log.flush()

    def rows(self, sql, *params):
        return [dict(r) for r in self.conn.execute(sql, params)]


class Obstacles(LoggerTestCase):
    def test_one_row_per_track_with_closest_approach(self):
        self.feed_stub()
        rows = self.rows("SELECT * FROM obstacles")
        self.assertEqual(len(rows), 1)
        ob = rows[0]
        self.assertEqual((ob["track_id"], ob["class"], ob["direction"]), (7, "pedestrian", "ahead"))
        self.assertEqual(ob["source"], "fused")                 # camera + ultrasonic once it is in range
        self.assertLess(ob["distance_m"], 0.5)                  # closest distance, not the first one
        self.assertLess(ob["ttc_s"], 1.0)                       # lowest time to collision
        self.assertEqual(ob["first_seen"], "2026-10-10T13:00:30.063Z")   # t_mono_ns -> UTC with the session anchor
        self.assertGreater(ob["last_seen"], ob["first_seen"])


class Alerts(LoggerTestCase):
    def test_risk_alerts_with_output_channel_and_latency(self):
        self.feed_stub(fault_at_s=50)
        alerts = self.rows("SELECT * FROM alerts ORDER BY alert_id")
        self.assertEqual([a["priority"] for a in alerts], [1, 0, 1])     # high, critical, then the fault
        obstacle_id = self.rows("SELECT obstacle_id FROM obstacles")[0]["obstacle_id"]
        for a in alerts[:2]:
            self.assertEqual(a["alert_type"], "obstacle")
            self.assertEqual(a["reason_code"], "ttc_below_threshold")
            self.assertEqual(a["obstacle_id"], obstacle_id)
            self.assertEqual(a["channel"], "haptic")
            self.assertAlmostEqual(a["e2e_latency_ms"], 75.0)
        fault = alerts[2]
        self.assertEqual((fault["alert_type"], fault["reason_code"]), ("system_fault", "system_fault"))
        self.assertIsNone(fault["obstacle_id"])
        self.assertIsNone(fault["channel"])                     # no output command in the stub for the fault

    def test_low_confidence_is_weather_degraded(self):
        alert = from_json(next(l for l in stub_stream(duration_s=60, start_ns=START_NS)
                               if '"risk_alert"' in l)).to_dict()
        alert["reason"] = {"code": "low_confidence", "detail": "fog"}
        alert["track_id"] = None
        self.assertTrue(self.log.handle(json.dumps(alert)))
        self.assertEqual(self.rows("SELECT alert_type FROM alerts")[0]["alert_type"], "weather_degraded")


class Metrics(LoggerTestCase):
    def test_latency_per_stage(self):
        self.feed_stub()
        avg = {r["stage"]: r["v"] for r in self.rows(
            "SELECT stage, AVG(value) AS v FROM performance_metrics WHERE metric = 'latency_ms' GROUP BY stage")}
        self.assertEqual(avg, {"detection_set": 38.0, "fused_obstacle": 55.0, "track": 63.0,
                               "risk_alert": 68.0, "output_command": 75.0})
        sources = {r["source"] for r in self.rows("SELECT DISTINCT source FROM performance_metrics")}
        self.assertEqual(sources, {"live"})

    def test_objects_detected_with_json_tags(self):
        self.feed_stub()
        row = self.rows("SELECT * FROM performance_metrics WHERE metric = 'objects_detected' AND value > 0")[0]
        self.assertEqual(row["unit"], "count")
        self.assertEqual(json.loads(row["tags"])["hazards"], ["pedestrian"])
        self.assertIsNotNone(row["frame_id"])


class BadInput(LoggerTestCase):
    def test_unknown_newer_and_invalid_messages_are_skipped_not_fatal(self):
        good = next(stub_stream(duration_s=1, start_ns=START_NS))           # a camera_frame
        newer = json.loads(good) | {"schema_version": "1.1.0"}
        invalid = json.loads(good) | {"confidence": 7}
        self.assertFalse(self.log.handle("not json"))
        self.assertFalse(self.log.handle('{"msg_type": "heartbeat", "t_mono_ns": 5}'))
        self.assertFalse(self.log.handle(json.dumps(newer)))
        self.assertFalse(self.log.handle(json.dumps(invalid)))
        self.assertEqual(dict(self.log.skipped), {"bad_json": 1, "unknown_type": 1, "invalid": 2})
        self.assertTrue(self.log.handle(good))                              # still working
        self.assertEqual(self.log.counts["camera_frame"], 1)

    def test_bad_source_kind(self):
        with self.assertRaises(ValueError):
            Logger(self.conn, self.session, source_kind="test")


class Flush(LoggerTestCase):
    def test_rows_reach_the_file_only_on_flush(self):
        for line in stub_stream(duration_s=40, start_ns=START_NS):
            self.log.handle(line)
        other = sqlite3.connect(self.path)                    # what another process would see
        count = lambda: other.execute("SELECT COUNT(*) FROM obstacles").fetchone()[0]
        self.assertEqual(count(), 0)
        self.log.flush()
        self.assertEqual(count(), 1)
        other.close()


if __name__ == "__main__":
    unittest.main()
