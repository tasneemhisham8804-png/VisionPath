"""
Metrics logging pipeline (Week 2): turns bus messages into database rows, live.

Each contract message (one JSON line) goes through Logger.handle(line):

  every message     -> performance_metrics  latency_ms (t_mono_ns - t_origin_ns)
                                             per module (source) and stage (msg_type)
  detection_set     -> performance_metrics  objects_detected (count, hazards in tags)
  fused_obstacle    -> remembered for a moment, to know which sensors saw a track
  track             -> obstacles            one row per track_id (upsert):
                                             closest distance, lowest ttc, last direction
  risk_alert        -> alerts               one row per alert, linked to its obstacle
  output_command    -> alerts               channel + end-to-end latency of that alert

Anything else (a msg_type that is not in this contract version, a newer
schema_version, an invalid message) is counted in `skipped` and ignored: the
logger never stops because of one bad message. The black box records every
message anyway, so nothing is lost for replay.

Rows are committed by flush() (the service calls it every flush_interval_s),
so the SD card sees one write per second instead of one per message.

Standard library only.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, OrderedDict

from contracts import ContractError, ReasonCode, from_dict, message_types
from datalogger.db import mono_to_utc, session_clock, utc_now

KNOWN_TYPES = frozenset(message_types())
SOURCE_KINDS = ("live", "replay", "walk_test")
_FUSED_MEMORY = 256           # fused_obstacle sources kept to match the next tracks

_UPSERT_OBSTACLE = """
INSERT INTO obstacles (session_id, track_id, class, confidence, source, distance_m, direction,
                       closing_speed_mps, ttc_s, first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (session_id, track_id) DO UPDATE SET
    class             = excluded.class,
    confidence        = MAX(COALESCE(confidence, 0), excluded.confidence),
    source            = COALESCE(excluded.source, source),
    distance_m        = MIN(COALESCE(distance_m, excluded.distance_m), excluded.distance_m),
    direction         = excluded.direction,
    closing_speed_mps = excluded.closing_speed_mps,
    ttc_s             = CASE WHEN ttc_s IS NULL THEN excluded.ttc_s
                             WHEN excluded.ttc_s IS NULL THEN ttc_s
                             ELSE MIN(ttc_s, excluded.ttc_s) END,
    last_seen         = excluded.last_seen
"""


class Logger:
    def __init__(self, conn: sqlite3.Connection, session_id: int, source_kind: str = "live"):
        """
        conn        : open database (datalogger.db.open_db)
        session_id  : current session (datalogger.db.start_session)
        source_kind : 'live' on the device, 'replay' when feeding a recording,
                      'walk_test' for field tests (performance_metrics.source)
        """
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"source_kind must be one of {SOURCE_KINDS}")
        self.conn = conn
        self.session_id = session_id
        self.source_kind = source_kind
        self._started_at, self._mono_start_s = session_clock(conn, session_id)

        self.counts: Counter = Counter()        # logged messages per msg_type
        self.skipped: Counter = Counter()       # ignored messages per reason
        self.last_error: str | None = None
        self.rows_written = 0
        self._pending = 0

        self._obstacle_ids: dict[int, int] = {}         # track_id -> obstacles.obstacle_id
        self._alert_ids: dict[int, int] = {}            # contract alert_id -> alerts.alert_id
        self._fused_sources: OrderedDict = OrderedDict()  # (t_origin_ns, hazard) -> sources

    # ------------------------------------------------------------------ input
    def handle(self, line: str) -> bool:
        """Log one JSON line. Returns True if it was logged, False if it was skipped."""
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            return self._skip("bad_json", "not valid JSON")
        if not isinstance(data, dict):
            return self._skip("bad_json", "not a JSON object")
        msg_type = data.get("msg_type")
        if msg_type not in KNOWN_TYPES:
            return self._skip("unknown_type", f"msg_type {msg_type!r} is not in this contract version")
        try:
            msg = from_dict(data)
        except ContractError as e:
            return self._skip("invalid", str(e))
        except (TypeError, ValueError, KeyError) as e:
            return self._skip("invalid", f"{type(e).__name__}: {e}")

        try:
            self._log_latency(msg)
            handler = getattr(self, f"_on_{msg.MSG_TYPE}", None)
            if handler is not None:
                handler(msg)
        except sqlite3.Error as e:
            return self._skip("db_error", f"{type(e).__name__}: {e}")

        self.counts[msg.MSG_TYPE] += 1
        self._pending += 1
        return True

    def flush(self) -> None:
        """Commit the rows written since the last flush."""
        if self._pending:
            self.conn.commit()
            self._pending = 0

    def close(self) -> None:
        self.flush()

    def summary(self) -> str:
        logged = ", ".join(f"{k} {v}" for k, v in sorted(self.counts.items())) or "nothing"
        text = f"logged: {logged} | rows written: {self.rows_written}"
        if self.skipped:
            text += " | skipped: " + ", ".join(f"{k} {v}" for k, v in sorted(self.skipped.items()))
        return text

    # ---------------------------------------------------------------- helpers
    def _skip(self, reason: str, detail: str) -> bool:
        self.skipped[reason] += 1
        self.last_error = detail
        return False

    def _ts(self, t_mono_ns: int) -> str:
        if self._mono_start_s is None:
            return utc_now()
        return mono_to_utc(self._started_at, self._mono_start_s, t_mono_ns)

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.rows_written += max(cur.rowcount, 0)
        return cur

    def _metric(self, msg, metric: str, value: float, unit: str, tags: dict | None = None) -> None:
        self._exec(
            """INSERT INTO performance_metrics
                   (session_id, ts, frame_id, module, metric, stage, value, unit, source, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.session_id, self._ts(msg.t_mono_ns), getattr(msg, "frame_id", None), msg.source,
             metric, msg.MSG_TYPE, value, unit, self.source_kind,
             json.dumps(tags, sort_keys=True) if tags else None))

    def _obstacle_id(self, track_id) -> int | None:
        if track_id is None:
            return None
        if track_id not in self._obstacle_ids:
            row = self.conn.execute(
                "SELECT obstacle_id FROM obstacles WHERE session_id = ? AND track_id = ?",
                (self.session_id, track_id)).fetchone()
            if row is None:
                return None
            self._obstacle_ids[track_id] = row[0]
        return self._obstacle_ids[track_id]

    @staticmethod
    def _value(x):
        """Enum -> its string value; None stays None."""
        return getattr(x, "value", x)

    # ------------------------------------------------------- per message type
    def _log_latency(self, msg) -> None:
        # A raw sensor reading published at the moment it was taken has latency 0
        # by definition: no row, it would only fill the table.
        if msg.t_mono_ns == msg.t_origin_ns:
            return
        self._metric(msg, "latency_ms", round(msg.latency_ms(), 3), "ms")

    def _on_detection_set(self, msg) -> None:
        tags = {"model_id": msg.model_id}
        hazards = sorted({self._value(o.hazard) for o in msg.objects})
        if hazards:
            tags["hazards"] = hazards
        self._metric(msg, "objects_detected", len(msg.objects), "count", tags)

    def _on_fused_obstacle(self, msg) -> None:
        key = (msg.t_origin_ns, self._value(msg.hazard))
        self._fused_sources[key] = [self._value(s) for s in msg.sources]
        while len(self._fused_sources) > _FUSED_MEMORY:
            self._fused_sources.popitem(last=False)

    def _on_track(self, msg) -> None:
        hazard = self._value(msg.hazard)
        sources = self._fused_sources.get((msg.t_origin_ns, hazard))
        source = None if not sources else ("fused" if len(set(sources)) > 1 else sources[0])
        ts = self._ts(msg.t_mono_ns)
        self._exec(_UPSERT_OBSTACLE, (
            self.session_id, msg.track_id, hazard, msg.confidence, source, msg.distance_m,
            self._value(msg.bearing), msg.closing_speed_mps, msg.ttc_s, ts, ts))
        self._obstacle_id(msg.track_id)

    def _on_risk_alert(self, msg) -> None:
        code = self._value(msg.reason.code)
        if code == ReasonCode.SYSTEM_FAULT.value:
            alert_type = "system_fault"
        elif code == ReasonCode.LOW_CONFIDENCE.value:     # contract: rain or fog lowered confidence
            alert_type = "weather_degraded"
        else:
            alert_type = "obstacle"
        cur = self._exec(
            """INSERT INTO alerts (session_id, obstacle_id, ts, alert_type, reason_code,
                                   direction, priority, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.session_id, self._obstacle_id(msg.track_id), self._ts(msg.t_mono_ns), alert_type,
             code, self._value(msg.bearing), msg.priority.rank, msg.reason.detail))
        self._alert_ids[msg.alert_id] = cur.lastrowid

    def _on_output_command(self, msg) -> None:
        db_alert_id = self._alert_ids.get(msg.alert_id) if msg.alert_id is not None else None
        if db_alert_id is None:
            return
        # The first command played for an alert gives its channel and its
        # end-to-end latency (sensor reading -> output command).
        self._exec(
            """UPDATE alerts SET channel = COALESCE(channel, ?),
                                 e2e_latency_ms = COALESCE(e2e_latency_ms, ?)
               WHERE alert_id = ?""",
            (self._value(msg.channel), round(msg.latency_ms(), 3), db_alert_id))
