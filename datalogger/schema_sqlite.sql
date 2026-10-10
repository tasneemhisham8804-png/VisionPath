-- =====================================================================
-- VisionPath — on-device database (SQLite) for the Raspberry Pi
-- Same "ai" tables as the server (SQL Server v2), adapted for SQLite.
--
-- Differences from the server version:
--   * Only the tables the Pi writes. Not here: core.* (lives on the
--     server), call_attempts (the backend sends the pushes) and
--     daily_summary (computed on the server every night).
--   * user_id / acknowledged_by are plain integers: there is no
--     core.users table on the Pi, the device knows its user from config.
--   * Timestamps are TEXT in ISO 8601, always in UTC with a 'Z',
--     e.g. '2026-10-10T13:30:00.123Z'. One format everywhere keeps
--     text comparisons and ORDER BY correct; the server converts to
--     DATETIMEOFFSET and Cairo time when it needs to.
--   * BIT -> INTEGER 0/1, NVARCHAR -> TEXT, FLOAT/REAL -> REAL.
--   * obstacles has UNIQUE (session_id, track_id) so the logger can
--     upsert one row per tracked obstacle.
--
-- Foreign keys are only enforced when each connection runs
-- PRAGMA foreign_keys = ON  (datalogger/db.py does this).
-- =====================================================================

CREATE TABLE IF NOT EXISTS sessions (
    session_id         INTEGER PRIMARY KEY,
    user_id            INTEGER NOT NULL,
    started_at         TEXT    NOT NULL,          -- wall clock at start
    monotonic_start_s  REAL,                      -- shared monotonic clock at the same moment
    ended_at           TEXT,                      -- NULL while running
    git_commit         TEXT,
    start_battery_pct  INTEGER CHECK (start_battery_pct BETWEEN 0 AND 100),
    end_battery_pct    INTEGER CHECK (end_battery_pct   BETWEEN 0 AND 100),
    distance_walked_m  REAL,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE IF NOT EXISTS obstacles (
    obstacle_id        INTEGER PRIMARY KEY,
    session_id         INTEGER NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
    track_id           INTEGER,
    class              TEXT,       -- contract HazardClass: curb / step / pole / pedestrian / overhead / manhole / pothole / motorbike / tuktuk / hanging_cable / vehicle / drop / unknown_obstacle
    confidence         REAL    CHECK (confidence BETWEEN 0 AND 1),
    source             TEXT    CHECK (source IN ('camera', 'depth', 'ultrasonic', 'fused')),  -- one contract SensorSource, or 'fused' when several
    distance_m         REAL,
    direction          TEXT    CHECK (direction IN ('left', 'ahead', 'right')),
    height_level       TEXT    CHECK (height_level IN ('ground', 'body', 'head')),
    drop_height_band   TEXT    CHECK (drop_height_band IN ('low', 'medium', 'high')),
    closing_speed_mps  REAL,
    ttc_s              REAL,
    hazard_score       REAL,
    first_seen         TEXT,
    last_seen          TEXT,
    lat                REAL,
    lon                REAL,
    UNIQUE (session_id, track_id)
);

CREATE TABLE IF NOT EXISTS faults (
    fault_id       INTEGER PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
    module         TEXT,       -- camera / ultrasonic / imu / gps / network / app / detector / battery
    fault_type     TEXT,       -- heartbeat_lost / self_test_failed / no_internet / app_disconnected / throttling / undervoltage
    detected_by    TEXT    CHECK (detected_by IN ('watchdog', 'self_test', 'module')),
    detected_at    TEXT    NOT NULL,
    degraded_mode  TEXT,
    user_notified  TEXT    CHECK (user_notified IN ('voice', 'haptic', 'none')),
    message        TEXT,
    recovered_at   TEXT,
    CHECK (recovered_at IS NULL OR recovered_at >= detected_at)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
    obstacle_id     INTEGER REFERENCES obstacles (obstacle_id),
    fault_id        INTEGER REFERENCES faults (fault_id),
    ts              TEXT    NOT NULL,
    alert_type      TEXT    CHECK (alert_type IN ('obstacle', 'battery_low', 'system_fault', 'weather_degraded', 'fall')),
    reason_code     TEXT,      -- contract ReasonCode, e.g. ttc_below_threshold
    channel         TEXT    CHECK (channel IN ('voice', 'haptic', 'buzzer')),
    direction       TEXT,
    priority        INTEGER CHECK (priority BETWEEN 0 AND 4),   -- contract Priority.rank: 0 critical .. 4 info
    message         TEXT,
    e2e_latency_ms  REAL
);

CREATE TABLE IF NOT EXISTS location_logs (
    log_id      INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
    ts          TEXT    NOT NULL,
    lat         REAL,
    lon         REAL,
    accuracy_m  REAL
);

CREATE TABLE IF NOT EXISTS emergency_events (
    event_id               INTEGER PRIMARY KEY,
    user_id                INTEGER NOT NULL,
    session_id             INTEGER REFERENCES sessions (session_id),
    ts                     TEXT    NOT NULL,
    trigger_type           TEXT    CHECK (trigger_type IN ('ambulance_button', 'contact_button', 'fall', 'wellbeing', 'critical_battery')),
    severity               TEXT,
    lat                    REAL,
    lon                    REAL,
    battery_pct            INTEGER CHECK (battery_pct BETWEEN 0 AND 100),
    status                 TEXT    NOT NULL DEFAULT 'pending'
                                   CHECK (status IN ('pending', 'notified', 'cancelled', 'resolved')),
    cancel_method          TEXT    CHECK (cancel_method IN ('voice', 'button')),
    caregiver_notified_at  TEXT,
    acknowledged_at        TEXT,
    acknowledged_by        INTEGER,
    resolved_at            TEXT
);

CREATE TABLE IF NOT EXISTS wellbeing_checks (
    check_id             INTEGER PRIMARY KEY,
    user_id              INTEGER NOT NULL,
    session_id           INTEGER REFERENCES sessions (session_id) ON DELETE CASCADE,
    stationary_since     TEXT,
    interval_min         INTEGER,
    started_at           TEXT    NOT NULL,
    lat                  REAL,
    lon                  REAL,
    attempts             INTEGER CHECK (attempts BETWEEN 1 AND 2),
    outcome              TEXT    CHECK (outcome IN ('okay_voice', 'okay_button', 'escalated', 'overridden_by_fall')),
    responded_at         TEXT,
    emergency_event_id   INTEGER REFERENCES emergency_events (event_id),
    notification_status  TEXT    CHECK (notification_status IN ('not_needed', 'queued', 'sent', 'acknowledged', 'failed')),
    ended_at             TEXT
);

CREATE TABLE IF NOT EXISTS blackbox_snapshots (
    snapshot_id         INTEGER PRIMARY KEY,
    session_id          INTEGER NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
    ts                  TEXT    NOT NULL,
    trigger_type        TEXT    CHECK (trigger_type IN ('fall', 'near_miss', 'fault', 'manual')),
    duration_s          INTEGER NOT NULL DEFAULT 30 CHECK (duration_s > 0),
    file_path           TEXT,
    file_size_kb        INTEGER,
    emergency_event_id  INTEGER REFERENCES emergency_events (event_id),
    obstacle_id         INTEGER REFERENCES obstacles (obstacle_id),
    fault_id            INTEGER REFERENCES faults (fault_id)
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    metric_id   INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions (session_id) ON DELETE CASCADE,
    ts          TEXT    NOT NULL,
    frame_id    INTEGER,
    module      TEXT,
    metric      TEXT,
    stage       TEXT,
    value       REAL    NOT NULL,
    unit        TEXT,
    source      TEXT    CHECK (source IN ('live', 'replay', 'walk_test')),
    tags        TEXT    CHECK (tags IS NULL OR json_valid(tags))
);

CREATE TABLE IF NOT EXISTS routes (
    route_id        INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    name            TEXT,
    times_walked    INTEGER NOT NULL DEFAULT 1,
    is_known        INTEGER NOT NULL DEFAULT 0 CHECK (is_known IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_walked_at  TEXT
);

CREATE TABLE IF NOT EXISTS place_memory (
    place_id         INTEGER PRIMARY KEY,
    route_id         INTEGER REFERENCES routes (route_id) ON DELETE CASCADE,
    chroma_id        TEXT    UNIQUE,
    place_type       TEXT    CHECK (place_type IN ('door', 'stairs', 'corner', 'crossing')),
    order_in_route   INTEGER,
    position_source  TEXT    CHECK (position_source IN ('gps', 'vio')),
    lat              REAL,
    lon              REAL,
    rel_x_m          REAL,
    rel_y_m          REAL,
    times_matched    INTEGER NOT NULL DEFAULT 0,
    first_seen       TEXT,
    last_matched_at  TEXT
);

CREATE TABLE IF NOT EXISTS landmark_matches (
    match_id              INTEGER PRIMARY KEY,
    session_id            INTEGER NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
    place_id              INTEGER NOT NULL REFERENCES place_memory (place_id) ON DELETE CASCADE,
    ts                    TEXT    NOT NULL,
    distance              REAL,
    accepted              INTEGER CHECK (accepted IN (0, 1)),
    sent_to_loop_closure  INTEGER CHECK (sent_to_loop_closure IN (0, 1)),
    is_correct            INTEGER CHECK (is_correct IN (0, 1))
);

-- Indexes on foreign keys and common lookups
CREATE INDEX IF NOT EXISTS ix_obstacles_session ON obstacles (session_id);
CREATE INDEX IF NOT EXISTS ix_faults_session    ON faults (session_id, detected_at);
CREATE INDEX IF NOT EXISTS ix_alerts_session    ON alerts (session_id, ts);
CREATE INDEX IF NOT EXISTS ix_loc_session       ON location_logs (session_id, ts);
CREATE INDEX IF NOT EXISTS ix_ee_session        ON emergency_events (session_id);
CREATE INDEX IF NOT EXISTS ix_wb_session        ON wellbeing_checks (session_id);
CREATE INDEX IF NOT EXISTS ix_bb_session        ON blackbox_snapshots (session_id);
CREATE INDEX IF NOT EXISTS ix_pm_metric         ON performance_metrics (metric, module, ts);
CREATE INDEX IF NOT EXISTS ix_pm_session        ON performance_metrics (session_id);
CREATE INDEX IF NOT EXISTS ix_place_route       ON place_memory (route_id);
CREATE INDEX IF NOT EXISTS ix_lm_session        ON landmark_matches (session_id);
CREATE INDEX IF NOT EXISTS ix_lm_place          ON landmark_matches (place_id);
