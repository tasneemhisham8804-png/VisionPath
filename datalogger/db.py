"""
VisionPath on-device database (SQLite). Standard library only.

Usage:
    from datalogger.db import open_db, start_session
    conn = open_db("data/visionpath.db")          # creates the file and tables if missing
    session_id = start_session(conn, user_id=1)    # one row per run of the device
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema_sqlite.sql")


# --------------------------------------------------------------------------
# Time helpers
# Every timestamp in the database is UTC text: 2026-10-10T13:30:00.123Z
# Contract messages carry t_mono_ns (time.monotonic_ns() on the Pi). A session
# stores the wall clock and the monotonic clock taken at the same moment, so
# any t_mono_ns of that session converts to UTC.
# --------------------------------------------------------------------------
def to_utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now() -> str:
    """Current time in the text format used in every table."""
    return to_utc_text(datetime.now(timezone.utc))


def parse_utc_text(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def mono_to_utc(started_at: str, monotonic_start_s: float, t_mono_ns: int) -> str:
    """Convert a contract t_mono_ns to UTC text, using the session's clock anchor."""
    offset_s = t_mono_ns / 1e9 - monotonic_start_s
    return to_utc_text(parse_utc_text(started_at) + timedelta(seconds=offset_s))


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
def open_db(path) -> sqlite3.Connection:
    """
    Open (or create) the database and make sure every table exists.

    - foreign_keys = ON : SQLite ignores foreign keys unless this is set on every connection.
    - journal_mode = WAL: the logger keeps writing while another process reads,
                          and a power cut does not corrupt the file.
    - synchronous = NORMAL: safe with WAL and much faster on an SD card than FULL.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row          # rows behave like dicts: row["session_id"]
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()
    return conn


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def start_session(conn, user_id: int, git_commit=None, start_battery_pct=None,
                  started_at=None, monotonic_start_ns=None) -> int:
    """
    Insert a new session row and return its session_id.
    started_at / monotonic_start_ns default to "now"; a replay passes the
    first message's t_mono_ns so its timestamps convert consistently.
    """
    if monotonic_start_ns is None:
        monotonic_start_ns = time.monotonic_ns()
    cur = conn.execute(
        """INSERT INTO sessions (user_id, started_at, monotonic_start_s, git_commit, start_battery_pct)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, started_at or utc_now(), monotonic_start_ns / 1e9, git_commit, start_battery_pct))
    conn.commit()
    return cur.lastrowid


def end_session(conn, session_id: int, end_battery_pct=None, distance_walked_m=None,
                ended_at=None) -> None:
    conn.execute(
        """UPDATE sessions SET ended_at = ?, end_battery_pct = ?, distance_walked_m = ?
           WHERE session_id = ?""",
        (ended_at or utc_now(), end_battery_pct, distance_walked_m, session_id))
    conn.commit()


def session_clock(conn, session_id: int):
    """(started_at, monotonic_start_s) of a session, for mono_to_utc()."""
    row = conn.execute(
        "SELECT started_at, monotonic_start_s FROM sessions WHERE session_id = ?",
        (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"session {session_id} does not exist")
    return row["started_at"], row["monotonic_start_s"]


if __name__ == "__main__":
    # Quick manual check:  python -m datalogger.db
    db_path = Path("data") / "visionpath.db"
    conn = open_db(db_path)
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    conn.close()
    print(f"Database ready at {db_path.resolve()}")
    print(f"{len(tables)} tables: {', '.join(tables)}")
