"""
Tests for the on-device SQLite database.
Run from the repo root:   pytest datalogger
"""

import sqlite3

import pytest

from datalogger.db import open_db, utc_now

EXPECTED_TABLES = {
    "sessions", "obstacles", "faults", "alerts", "location_logs",
    "emergency_events", "wellbeing_checks", "blackbox_snapshots",
    "performance_metrics", "routes", "place_memory", "landmark_matches",
}


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "test.db")
    yield c
    c.close()


def new_session(conn) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (user_id, started_at) VALUES (1, ?)", (utc_now(),))
    return cur.lastrowid


def test_all_tables_created(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert EXPECTED_TABLES <= names


def test_open_twice_is_safe(tmp_path):
    open_db(tmp_path / "x.db").close()
    open_db(tmp_path / "x.db").close()      # CREATE ... IF NOT EXISTS: no error


def test_wal_mode_on(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO obstacles (session_id, track_id) VALUES (999, 1)")


def test_direction_must_match_contract(conn):
    sid = new_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO obstacles (session_id, track_id, direction) VALUES (?, 1, 'center')", (sid,))


def test_tags_must_be_json(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO performance_metrics (ts, value, tags) VALUES (?, 1, 'not json')", (utc_now(),))


def test_obstacle_upsert_keeps_one_row_per_track(conn):
    sid = new_session(conn)
    upsert = """
        INSERT INTO obstacles (session_id, track_id, class, distance_m, first_seen, last_seen)
        VALUES (?, 7, 'pole', ?, ?, ?)
        ON CONFLICT (session_id, track_id) DO UPDATE SET
            last_seen  = excluded.last_seen,
            distance_m = MIN(distance_m, excluded.distance_m)
    """
    conn.execute(upsert, (sid, 2.4, "2026-10-10T13:00:00.000Z", "2026-10-10T13:00:00.000Z"))
    conn.execute(upsert, (sid, 1.1, "2026-10-10T13:00:00.000Z", "2026-10-10T13:00:01.000Z"))

    rows = conn.execute("SELECT * FROM obstacles WHERE session_id = ?", (sid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["distance_m"] == pytest.approx(1.1)
    assert rows[0]["last_seen"] == "2026-10-10T13:00:01.000Z"


def test_deleting_session_cascades(conn):
    sid = new_session(conn)
    conn.execute("INSERT INTO location_logs (session_id, ts, lat, lon) VALUES (?, ?, 30.04, 31.23)",
                 (sid, utc_now()))
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
    assert conn.execute("SELECT COUNT(*) FROM location_logs").fetchone()[0] == 0


def test_utc_now_format():
    ts = utc_now()
    assert ts.endswith("Z") and "T" in ts and len(ts) == 24   # 2026-10-10T13:30:00.123Z
