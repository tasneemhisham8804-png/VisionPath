"""
VisionPath on-device database (SQLite).

Usage:
    from datalogger.db import open_db, utc_now
    conn = open_db("data/visionpath.db")   # creates the file and tables if missing
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema_sqlite.sql")


def utc_now() -> str:
    """Current time as the text format used in every table: UTC ISO 8601 with 'Z'."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def open_db(path: str | Path) -> sqlite3.Connection:
    """
    Open (or create) the database and make sure every table exists.

    - foreign_keys = ON : SQLite ignores foreign keys unless this is set on every connection.
    - journal_mode = WAL: the logger can keep writing while another process reads,
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


if __name__ == "__main__":
    # Quick manual check:  python -m datalogger.db
    db_path = Path("data") / "visionpath.db"
    with open_db(db_path) as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    print(f"Database ready at {db_path.resolve()}")
    print(f"{len(tables)} tables: {', '.join(tables)}")
