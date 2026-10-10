"""On-device SQLite database. Run with: python -m unittest discover -s tests -v"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from datalogger.db import mono_to_utc, open_db, session_clock, start_session, utc_now

EXPECTED_TABLES = {
    "sessions", "obstacles", "faults", "alerts", "location_logs",
    "emergency_events", "wellbeing_checks", "blackbox_snapshots",
    "performance_metrics", "routes", "place_memory", "landmark_matches",
}


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.conn = open_db(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def new_session(self) -> int:
        return start_session(self.conn, user_id=1)


class Schema(DbTestCase):
    def test_all_tables_created(self):
        names = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue(EXPECTED_TABLES <= names)

    def test_open_twice_is_safe(self):
        open_db(self.path).close()           # CREATE ... IF NOT EXISTS: no error

    def test_wal_mode_on(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")


class Constraints(DbTestCase):
    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO obstacles (session_id, track_id) VALUES (999, 1)")

    def test_direction_must_match_contract(self):
        sid = self.new_session()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO obstacles (session_id, track_id, direction) VALUES (?, 1, 'center')", (sid,))

    def test_depth_source_accepted(self):      # contract SensorSource includes depth (OAK-D)
        sid = self.new_session()
        self.conn.execute(
            "INSERT INTO obstacles (session_id, track_id, source) VALUES (?, 1, 'depth')", (sid,))

    def test_priority_is_contract_rank(self):
        sid = self.new_session()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO alerts (session_id, ts, priority) VALUES (?, ?, 5)", (sid, utc_now()))

    def test_tags_must_be_json(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO performance_metrics (ts, value, tags) VALUES (?, 1, 'not json')", (utc_now(),))


class Behaviour(DbTestCase):
    def test_obstacle_upsert_keeps_one_row_per_track(self):
        sid = self.new_session()
        upsert = """
            INSERT INTO obstacles (session_id, track_id, hazard_score, distance_m, first_seen, last_seen)
            VALUES (?, 7, NULL, ?, ?, ?)
            ON CONFLICT (session_id, track_id) DO UPDATE SET
                last_seen  = excluded.last_seen,
                distance_m = MIN(distance_m, excluded.distance_m)
        """
        self.conn.execute(upsert, (sid, 2.4, "2026-10-10T13:00:00.000Z", "2026-10-10T13:00:00.000Z"))
        self.conn.execute(upsert, (sid, 1.1, "2026-10-10T13:00:00.000Z", "2026-10-10T13:00:01.000Z"))
        rows = self.conn.execute("SELECT * FROM obstacles WHERE session_id = ?", (sid,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["distance_m"], 1.1)
        self.assertEqual(rows[0]["last_seen"], "2026-10-10T13:00:01.000Z")

    def test_deleting_session_cascades(self):
        sid = self.new_session()
        self.conn.execute("INSERT INTO location_logs (session_id, ts, lat, lon) VALUES (?, ?, 30.04, 31.23)",
                          (sid, utc_now()))
        self.conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM location_logs").fetchone()[0], 0)


class Clock(DbTestCase):
    def test_utc_now_format(self):
        ts = utc_now()
        self.assertTrue(ts.endswith("Z") and "T" in ts)
        self.assertEqual(len(ts), 24)             # 2026-10-10T13:30:00.123Z

    def test_mono_to_utc_uses_session_anchor(self):
        sid = start_session(self.conn, user_id=1, started_at="2026-10-10T13:00:00.000Z",
                            monotonic_start_ns=1_000_000_000_000)
        started_at, mono_s = session_clock(self.conn, sid)
        # 2.5 s after the session's monotonic start
        self.assertEqual(mono_to_utc(started_at, mono_s, 1_002_500_000_000), "2026-10-10T13:00:02.500Z")


if __name__ == "__main__":
    unittest.main()
