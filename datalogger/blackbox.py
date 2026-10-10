"""
On-device black box: keeps the last 30 seconds of pipeline messages in memory
and saves them to a replayable .jsonl file on a fall, a near miss or a fault.

Week 2 task: "a ring buffer of the last 30 seconds of sensor data, saved on a
fall, near miss or fault ... A trigger writes a replayable 30 second snapshot."

How it works
  * add(line)  : every JSON line on the bus goes in, whatever its msg_type
                 (heartbeat, ego_motion and any later type are kept too: the only
                 field needed is the envelope's t_mono_ns). The buffer is trimmed
                 by the messages' own clock (t_mono_ns), so a replayed recording
                 behaves exactly like the live device.
  * Automatic triggers, read from the stream itself:
        near_miss : a `track` with ttc_s below NEAR_MISS_TTC_S
        fault     : a `risk_alert` whose reason.code is system_fault
  * trigger(kind): manual trigger. "fall" comes from Malak Ibrahim's fall
                   detector, which is not in contract 1.0.0 yet, so it calls
                   this directly; "manual" is for tests.
  * A snapshot is JSON Lines, one message per line exactly as it was on the
    bus (msg.to_json()), sorted by t_mono_ns: the replay harness format
    (contracts/samples/chain_frame42.jsonl). Messages from different modules
    can arrive slightly out of order on the bus; sorting happens on write.
  * camera_frame.image_ref points to an image that is not saved, so a replay
    of a snapshot starts from detection_set (fusion, tracking and risk).
  * The file is written to a temporary name and renamed at the end, so a power
    cut never leaves half a snapshot behind.
  * Raw video never goes in: camera_frame only carries image_ref (a pointer).

Standard library only.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from datalogger.db import mono_to_utc, session_clock, utc_now

WINDOW_S = 30.0
NEAR_MISS_TTC_S = 1.0
COOLDOWN_S = 10.0                 # at most one snapshot of each kind per 10 s
TRIGGER_KINDS = ("fall", "near_miss", "fault", "manual")


class BlackBox:
    def __init__(self, out_dir, conn=None, session_id=None, window_s: float = WINDOW_S,
                 near_miss_ttc_s: float = NEAR_MISS_TTC_S, cooldown_s: float = COOLDOWN_S):
        """
        out_dir    : folder for the .jsonl snapshots (on the Pi: the writable data partition)
        conn       : open SQLite connection (datalogger.db.open_db) or None
        session_id : current session; needed to record rows in blackbox_snapshots
        """
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.conn = conn
        self.session_id = session_id
        self.window_ns = int(window_s * 1e9)
        self.near_miss_ttc_s = near_miss_ttc_s
        self.cooldown_ns = int(cooldown_s * 1e9)

        self._buffer: deque[tuple[int, str]] = deque()   # (t_mono_ns, json line), arrival order
        self._newest_ns: int | None = None
        self._last_trigger_ns: dict[str, int] = {}
        self.ignored_lines = 0                             # lines that were not valid JSON messages
        self.record_errors = 0                             # snapshots saved but their database row failed
        self.snapshots: list[Path] = []

    # ------------------------------------------------------------------ input
    def add(self, line: str):
        """
        Feed one JSON line (any msg_type). Returns the snapshot Path if this
        message fired an automatic trigger, otherwise None. Never raises on bad
        input: the black box must keep running whatever the other modules send.
        Only lines that are not a JSON object with an integer t_mono_ns are
        ignored (counted in ignored_lines).
        """
        line = line.strip()
        try:
            msg = json.loads(line)
            t = msg["t_mono_ns"]
            if isinstance(t, bool) or not isinstance(t, int):
                raise TypeError("t_mono_ns must be an integer")
        except (ValueError, KeyError, TypeError):
            self.ignored_lines += 1
            return None

        self._buffer.append((t, line))
        if self._newest_ns is None or t > self._newest_ns:
            self._newest_ns = t
        self._trim()
        try:
            return self._auto_trigger(msg, t)
        except (TypeError, AttributeError):        # e.g. ttc_s sent as text: keep the line, no trigger
            return None

    def _trim(self) -> None:
        oldest_allowed = self._newest_ns - self.window_ns
        while self._buffer and self._buffer[0][0] < oldest_allowed:
            self._buffer.popleft()

    def _window(self) -> list[tuple[int, str]]:
        """Buffered messages of the last window_s, sorted by t_mono_ns (stable for equal times)."""
        oldest_allowed = self._newest_ns - self.window_ns
        return sorted((item for item in self._buffer if item[0] >= oldest_allowed), key=lambda item: item[0])

    def _auto_trigger(self, msg: dict, t: int):
        kind = msg.get("msg_type")
        if kind == "track" and msg.get("ttc_s") is not None and msg["ttc_s"] < self.near_miss_ttc_s:
            return self.trigger("near_miss", at_ns=t, track_id=msg.get("track_id"))
        if kind == "risk_alert" and (msg.get("reason") or {}).get("code") == "system_fault":
            return self.trigger("fault", at_ns=t)
        return None

    # ---------------------------------------------------------------- trigger
    def trigger(self, kind: str, at_ns: int | None = None, track_id: int | None = None,
                emergency_event_id: int | None = None, fault_id: int | None = None):
        """
        Save the buffer to a snapshot file. Returns its Path, or None when the
        buffer is empty or the same kind fired less than cooldown_s ago.
        """
        if kind not in TRIGGER_KINDS:
            raise ValueError(f"trigger kind must be one of {TRIGGER_KINDS}")
        if not self._buffer:
            return None
        at_ns = at_ns if at_ns is not None else self._newest_ns
        last = self._last_trigger_ns.get(kind)
        if last is not None and at_ns - last < self.cooldown_ns:
            return None
        self._last_trigger_ns[kind] = at_ns

        window = self._window()
        path = self._write_snapshot(kind, [line for _, line in window])
        self.snapshots.append(path)
        if self.conn is not None and self.session_id is not None:
            span_ns = window[-1][0] - window[0][0]
            try:
                self._record(kind, at_ns, path, span_ns, track_id, emergency_event_id, fault_id)
            except sqlite3.Error:                  # the file is what matters; never crash on the row
                self.record_errors += 1
        return path

    def _write_snapshot(self, kind: str, lines: list[str]) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        session = f"s{self.session_id}" if self.session_id is not None else "nosession"
        path = self.out_dir / f"{session}_{stamp}_{kind}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)               # atomic: the snapshot appears complete or not at all
        return path

    def _record(self, kind, at_ns, path, span_ns, track_id, emergency_event_id, fault_id) -> None:
        """One row in blackbox_snapshots per snapshot file."""
        try:
            started_at, mono_start_s = session_clock(self.conn, self.session_id)
            ts = mono_to_utc(started_at, mono_start_s, at_ns) if mono_start_s is not None else utc_now()
        except ValueError:
            ts = utc_now()

        obstacle_id = None
        if track_id is not None:            # link the near miss to the obstacle row, if the logger wrote it
            row = self.conn.execute(
                "SELECT obstacle_id FROM obstacles WHERE session_id = ? AND track_id = ?",
                (self.session_id, track_id)).fetchone()
            obstacle_id = row["obstacle_id"] if row else None

        duration_s = max(1, round(span_ns / 1e9))
        self.conn.execute(
            """INSERT INTO blackbox_snapshots
                   (session_id, ts, trigger_type, duration_s, file_path, file_size_kb,
                    emergency_event_id, obstacle_id, fault_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.session_id, ts, kind, duration_s, str(path),
             max(1, round(path.stat().st_size / 1024)), emergency_event_id, obstacle_id, fault_id))
        self.conn.commit()

    # ------------------------------------------------------------------ info
    def buffered_seconds(self) -> float:
        if len(self._buffer) < 2:
            return 0.0
        window = self._window()
        return (window[-1][0] - window[0][0]) / 1e9

    def __len__(self) -> int:
        return len(self._buffer)


def load_snapshot(path):
    """Read a snapshot back as contract messages (raises ContractError if any line is invalid)."""
    from contracts import from_json
    with open(path, encoding="utf-8") as f:
        return [from_json(line) for line in f if line.strip()]
