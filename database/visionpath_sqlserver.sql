/* =====================================================================
   VisionPath — Full database script for SQL Server (T-SQL)   v2
   Run in SSMS: open this file, then press Execute (F5).

   Schema 1: core -> people, accounts and settings (from the app)   7 tables
   Schema 2: ai   -> data produced by the wearable and the AI      14 tables

   Upgrading from v1? Delete the old VisionPath database first
   (Object Explorer > right-click VisionPath > Delete > tick
   "Close existing connections"), or uncomment the RESET block below.
   ===================================================================== */

-- =====================================================================
-- 0) Database
-- =====================================================================
IF DB_ID(N'VisionPath') IS NULL
    CREATE DATABASE VisionPath;
GO

USE VisionPath;
GO

-- =====================================================================
-- (Optional) RESET — uncomment ONLY to wipe everything and rebuild.
-- WARNING: this deletes all tables and all data (v1 or v2).
-- =====================================================================
/*
DROP PROCEDURE IF EXISTS core.usp_delete_user_data;
DROP PROCEDURE IF EXISTS ai.usp_build_daily_summary;
DROP TABLE IF EXISTS ai.daily_summary, ai.landmark_matches, ai.place_memory, ai.routes,
                     ai.performance_metrics, ai.blackbox_snapshots, ai.call_attempts,
                     ai.wellbeing_checks, ai.alerts, ai.emergency_events, ai.faults,
                     ai.location_logs, ai.obstacles, ai.sessions;
DROP TABLE IF EXISTS core.caregiver_devices, core.user_caregivers,
                     core.emergency_contacts, core.devices,
                     core.caregivers, core.users, core.languages;
GO
*/

-- =====================================================================
-- 1) Schemas
-- =====================================================================
IF SCHEMA_ID(N'core') IS NULL EXEC (N'CREATE SCHEMA core');
GO
IF SCHEMA_ID(N'ai') IS NULL EXEC (N'CREATE SCHEMA ai');
GO

/* =====================================================================
   SCHEMA 1: core
   ===================================================================== */

CREATE TABLE core.languages (
    language_id  INT IDENTITY(1,1) NOT NULL,
    code         VARCHAR(5)        NOT NULL,   -- 'ar-EG' / 'en'
    name         NVARCHAR(50)      NOT NULL,
    CONSTRAINT PK_languages PRIMARY KEY (language_id),
    CONSTRAINT UQ_languages_code UNIQUE (code)
);
GO

CREATE TABLE core.users (
    user_id                  INT IDENTITY(1,1) NOT NULL,
    full_name                NVARCHAR(100)     NOT NULL,
    phone                    VARCHAR(20)       NULL,
    date_of_birth            DATE              NULL,
    gender                   VARCHAR(10)       NULL,
    language_id              INT               NULL,
    vision_level             VARCHAR(20)       NULL,   -- total / partial
    location_sharing_paused  BIT               NOT NULL CONSTRAINT DF_users_loc_paused DEFAULT (0),
    -- first run setup
    height_cm                SMALLINT          NULL,
    walking_pace             VARCHAR(10)       NULL,   -- slow / normal / fast
    speech_rate              VARCHAR(10)       NULL,   -- slow / normal / fast
    haptic_strength          SMALLINT          NULL,   -- 1..5
    -- passive wellbeing check settings (set by the caregiver)
    wellbeing_interval_min   SMALLINT          NOT NULL CONSTRAINT DF_users_wb_interval DEFAULT (15),
    quiet_hours_start        TIME(0)           NULL,
    quiet_hours_end          TIME(0)           NULL,
    created_at               DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_users_created DEFAULT (SYSDATETIMEOFFSET()),
    CONSTRAINT PK_users PRIMARY KEY (user_id),
    CONSTRAINT FK_users_language FOREIGN KEY (language_id)
        REFERENCES core.languages (language_id),
    CONSTRAINT CK_users_vision      CHECK (vision_level IN ('total', 'partial')),
    CONSTRAINT CK_users_height      CHECK (height_cm BETWEEN 100 AND 230),
    CONSTRAINT CK_users_pace        CHECK (walking_pace IN ('slow', 'normal', 'fast')),
    CONSTRAINT CK_users_speech      CHECK (speech_rate  IN ('slow', 'normal', 'fast')),
    CONSTRAINT CK_users_haptic      CHECK (haptic_strength BETWEEN 1 AND 5),
    CONSTRAINT CK_users_wb_interval CHECK (wellbeing_interval_min BETWEEN 10 AND 60),
    CONSTRAINT CK_users_quiet_hours CHECK ((quiet_hours_start IS NULL AND quiet_hours_end IS NULL)
                                        OR (quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL))
);
GO

CREATE TABLE core.caregivers (
    caregiver_id   INT IDENTITY(1,1) NOT NULL,
    full_name      NVARCHAR(100)     NOT NULL,
    phone          VARCHAR(20)       NULL,
    email          VARCHAR(100)      NOT NULL,
    password_hash  VARCHAR(255)      NULL,     -- bcrypt; stays NULL if Firebase Auth is used
    language_id    INT               NULL,
    created_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_caregivers_created DEFAULT (SYSDATETIMEOFFSET()),
    CONSTRAINT PK_caregivers PRIMARY KEY (caregiver_id),
    CONSTRAINT UQ_caregivers_email UNIQUE (email),
    CONSTRAINT FK_caregivers_language FOREIGN KEY (language_id)
        REFERENCES core.languages (language_id)
);
GO

CREATE TABLE core.caregiver_devices (
    id            INT IDENTITY(1,1) NOT NULL,
    caregiver_id  INT               NOT NULL,
    platform      VARCHAR(10)       NULL,      -- android / ios
    CONSTRAINT PK_caregiver_devices PRIMARY KEY (id),
    CONSTRAINT FK_cgdev_caregiver FOREIGN KEY (caregiver_id)
        REFERENCES core.caregivers (caregiver_id) ON DELETE CASCADE,
    CONSTRAINT CK_cgdev_platform CHECK (platform IN ('android', 'ios'))
);
GO

CREATE TABLE core.user_caregivers (
    user_id                  INT          NOT NULL,
    caregiver_id             INT          NOT NULL,
    relationship             NVARCHAR(30) NULL,
    is_primary               BIT NOT NULL CONSTRAINT DF_uc_primary  DEFAULT (0),
    can_see_location         BIT NOT NULL CONSTRAINT DF_uc_location DEFAULT (1),
    can_see_battery          BIT NOT NULL CONSTRAINT DF_uc_battery  DEFAULT (1),
    can_see_daily_summary    BIT NOT NULL CONSTRAINT DF_uc_summary  DEFAULT (1),
    can_receive_emergencies  BIT NOT NULL CONSTRAINT DF_uc_emerg    DEFAULT (1),
    CONSTRAINT PK_user_caregivers PRIMARY KEY (user_id, caregiver_id),
    CONSTRAINT FK_uc_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id) ON DELETE CASCADE,
    CONSTRAINT FK_uc_caregiver FOREIGN KEY (caregiver_id)
        REFERENCES core.caregivers (caregiver_id) ON DELETE CASCADE
);
GO

CREATE TABLE core.emergency_contacts (
    contact_id      INT IDENTITY(1,1) NOT NULL,
    user_id         INT               NOT NULL,
    name            NVARCHAR(100)     NOT NULL,
    phone           VARCHAR(20)       NOT NULL,
    relationship    NVARCHAR(30)      NULL,
    priority_order  SMALLINT          NOT NULL,  -- 1 = first in the menu
    is_ambulance    BIT               NOT NULL CONSTRAINT DF_ec_ambulance DEFAULT (0),
    CONSTRAINT PK_emergency_contacts PRIMARY KEY (contact_id),
    CONSTRAINT FK_ec_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id) ON DELETE CASCADE,
    CONSTRAINT CK_ec_priority CHECK (priority_order >= 1)
);
GO

CREATE TABLE core.devices (
    device_id      INT IDENTITY(1,1) NOT NULL,
    user_id        INT               NULL,
    serial_number  VARCHAR(50)       NOT NULL,
    hw_version     VARCHAR(20)       NULL,
    fw_version     VARCHAR(20)       NULL,
    registered_at  DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_devices_registered DEFAULT (SYSDATETIMEOFFSET()),
    CONSTRAINT PK_devices PRIMARY KEY (device_id),
    CONSTRAINT UQ_devices_serial UNIQUE (serial_number),
    CONSTRAINT FK_devices_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id) ON DELETE SET NULL  -- device can be reassigned
);
GO

/* =====================================================================
   SCHEMA 2: ai
   Rule used for foreign keys: only a parent -> child tree cascades
   (session -> its rows, route -> its places, event -> its attempts).
   Every other link is NO ACTION, because SQL Server rejects
   "multiple cascade paths". To delete a user and ALL their data,
   use core.usp_delete_user_data (bottom of this file).
   ===================================================================== */

CREATE TABLE ai.sessions (
    session_id         BIGINT IDENTITY(1,1) NOT NULL,
    user_id            INT                  NOT NULL,
    started_at         DATETIMEOFFSET(3)    NOT NULL,  -- wall clock at start
    monotonic_start_s  FLOAT                NULL,      -- shared monotonic clock at the same moment
    ended_at           DATETIMEOFFSET(3)    NULL,      -- NULL while running
    git_commit         VARCHAR(40)          NULL,
    start_battery_pct  SMALLINT             NULL,
    end_battery_pct    SMALLINT             NULL,
    distance_walked_m  REAL                 NULL,
    CONSTRAINT PK_sessions PRIMARY KEY (session_id),
    CONSTRAINT FK_sessions_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id),
    CONSTRAINT CK_sessions_times  CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT CK_sessions_batt_s CHECK (start_battery_pct BETWEEN 0 AND 100),
    CONSTRAINT CK_sessions_batt_e CHECK (end_battery_pct   BETWEEN 0 AND 100)
);
GO

CREATE TABLE ai.obstacles (
    obstacle_id        BIGINT IDENTITY(1,1) NOT NULL,
    session_id         BIGINT               NOT NULL,
    track_id           INT                  NULL,   -- ByteTrack ID within session
    class              VARCHAR(30)          NULL,   -- pole / step / curb / pedestrian / overhead / manhole / pothole / motorbike / tuktuk / cable / unknown
    confidence         REAL                 NULL,
    source             VARCHAR(15)          NULL,   -- camera / ultrasonic / fused
    distance_m         REAL                 NULL,
    direction          VARCHAR(10)          NULL,   -- left / ahead / right (data contract)
    height_level       VARCHAR(10)          NULL,   -- ground / body / head
    drop_height_band   VARCHAR(10)          NULL,   -- low / medium / high (curbs, steps, holes)
    closing_speed_mps  REAL                 NULL,
    ttc_s              REAL                 NULL,   -- time to collision
    hazard_score       REAL                 NULL,
    first_seen         DATETIMEOFFSET(3)    NULL,
    last_seen          DATETIMEOFFSET(3)    NULL,
    lat                FLOAT                NULL,
    lon                FLOAT                NULL,
    CONSTRAINT PK_obstacles PRIMARY KEY (obstacle_id),
    CONSTRAINT FK_obstacles_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT CK_obstacles_conf   CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT CK_obstacles_source CHECK (source IN ('camera', 'ultrasonic', 'fused')),
    CONSTRAINT CK_obstacles_dir    CHECK (direction IN ('left', 'ahead', 'right')),
    CONSTRAINT CK_obstacles_height CHECK (height_level IN ('ground', 'body', 'head')),
    CONSTRAINT CK_obstacles_drop   CHECK (drop_height_band IN ('low', 'medium', 'high'))
);
GO

CREATE TABLE ai.faults (
    fault_id       BIGINT IDENTITY(1,1) NOT NULL,
    session_id     BIGINT               NOT NULL,
    module         VARCHAR(20)          NULL,   -- camera / ultrasonic / imu / gps / network / app / detector / battery
    fault_type     VARCHAR(25)          NULL,   -- heartbeat_lost / self_test_failed / no_internet / app_disconnected / throttling / undervoltage
    detected_by    VARCHAR(15)          NULL,   -- watchdog / self_test / module
    detected_at    DATETIMEOFFSET(3)    NOT NULL,
    degraded_mode  VARCHAR(30)          NULL,   -- e.g. ultrasonic_only
    user_notified  VARCHAR(10)          NULL,   -- voice / haptic / none
    message        NVARCHAR(200)        NULL,
    recovered_at   DATETIMEOFFSET(3)    NULL,
    CONSTRAINT PK_faults PRIMARY KEY (fault_id),
    CONSTRAINT FK_faults_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT CK_faults_by       CHECK (detected_by IN ('watchdog', 'self_test', 'module')),
    CONSTRAINT CK_faults_notified CHECK (user_notified IN ('voice', 'haptic', 'none')),
    CONSTRAINT CK_faults_times    CHECK (recovered_at IS NULL OR recovered_at >= detected_at)
);
GO

CREATE TABLE ai.alerts (
    alert_id        BIGINT IDENTITY(1,1) NOT NULL,
    session_id      BIGINT               NOT NULL,
    obstacle_id     BIGINT               NULL,   -- set when caused by an obstacle
    fault_id        BIGINT               NULL,   -- set when caused by a fault
    ts              DATETIMEOFFSET(3)    NOT NULL,
    alert_type      VARCHAR(20)          NULL,   -- obstacle / battery_low / system_fault / weather_degraded / fall
    reason_code     VARCHAR(40)          NULL,   -- e.g. POLE_AHEAD_2S
    channel         VARCHAR(10)          NULL,   -- voice / haptic / buzzer
    direction       VARCHAR(10)          NULL,
    priority        SMALLINT             NULL,
    message         NVARCHAR(MAX)        NULL,
    e2e_latency_ms  REAL                 NULL,   -- target < 300 ms
    CONSTRAINT PK_alerts PRIMARY KEY (alert_id),
    CONSTRAINT FK_alerts_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT FK_alerts_obstacle FOREIGN KEY (obstacle_id)
        REFERENCES ai.obstacles (obstacle_id),
    CONSTRAINT FK_alerts_fault FOREIGN KEY (fault_id)
        REFERENCES ai.faults (fault_id),
    CONSTRAINT CK_alerts_type    CHECK (alert_type IN ('obstacle', 'battery_low', 'system_fault', 'weather_degraded', 'fall')),
    CONSTRAINT CK_alerts_channel CHECK (channel IN ('voice', 'haptic', 'buzzer'))
);
GO

CREATE TABLE ai.location_logs (
    log_id      BIGINT IDENTITY(1,1) NOT NULL,
    session_id  BIGINT               NOT NULL,
    ts          DATETIMEOFFSET(3)    NOT NULL,
    lat         FLOAT                NULL,
    lon         FLOAT                NULL,
    accuracy_m  REAL                 NULL,
    CONSTRAINT PK_location_logs PRIMARY KEY (log_id),
    CONSTRAINT FK_loc_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE
);
GO

CREATE TABLE ai.emergency_events (
    event_id               BIGINT IDENTITY(1,1) NOT NULL,
    user_id                INT                  NOT NULL,
    session_id             BIGINT               NULL,
    ts                     DATETIMEOFFSET(3)    NOT NULL,
    trigger_type           VARCHAR(20)          NULL,   -- ambulance_button / contact_button / fall / wellbeing / critical_battery
    severity               VARCHAR(10)          NULL,
    lat                    FLOAT                NULL,
    lon                    FLOAT                NULL,
    battery_pct            SMALLINT             NULL,
    status                 VARCHAR(20)          NOT NULL CONSTRAINT DF_ee_status DEFAULT ('pending'),
    cancel_method          VARCHAR(10)          NULL,   -- voice / button (NULL if not cancelled)
    caregiver_notified_at  DATETIMEOFFSET(3)    NULL,
    acknowledged_at        DATETIMEOFFSET(3)    NULL,   -- caregiver tapped "I've seen it"
    acknowledged_by        INT                  NULL,
    resolved_at            DATETIMEOFFSET(3)    NULL,
    CONSTRAINT PK_emergency_events PRIMARY KEY (event_id),
    CONSTRAINT FK_ee_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id),
    CONSTRAINT FK_ee_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id),
    CONSTRAINT FK_ee_ack_by FOREIGN KEY (acknowledged_by)
        REFERENCES core.caregivers (caregiver_id),
    CONSTRAINT CK_ee_trigger CHECK (trigger_type IN ('ambulance_button', 'contact_button', 'fall', 'wellbeing', 'critical_battery')),
    CONSTRAINT CK_ee_status  CHECK (status IN ('pending', 'notified', 'cancelled', 'resolved')),
    CONSTRAINT CK_ee_cancel  CHECK (cancel_method IN ('voice', 'button')),
    CONSTRAINT CK_ee_battery CHECK (battery_pct BETWEEN 0 AND 100)
);
GO

CREATE TABLE ai.call_attempts (
    attempt_id      BIGINT IDENTITY(1,1) NOT NULL,
    event_id        BIGINT               NOT NULL,
    caregiver_id    INT                  NULL,   -- push alarm sent to this caregiver
    attempt_order   SMALLINT             NULL,   -- 1 = first push, 2 = resend after 5 min, ...
    status          VARCHAR(15)          NOT NULL CONSTRAINT DF_ca_status DEFAULT ('pending'),
    failure_reason  NVARCHAR(100)        NULL,
    attempted_at    DATETIMEOFFSET(3)    NULL,
    completed_at    DATETIMEOFFSET(3)    NULL,
    CONSTRAINT PK_call_attempts PRIMARY KEY (attempt_id),
    CONSTRAINT FK_ca_event FOREIGN KEY (event_id)
        REFERENCES ai.emergency_events (event_id) ON DELETE CASCADE,
    CONSTRAINT FK_ca_caregiver FOREIGN KEY (caregiver_id)
        REFERENCES core.caregivers (caregiver_id),
    CONSTRAINT CK_ca_status CHECK (status IN ('pending', 'sent', 'acknowledged', 'failed'))
);
GO

CREATE TABLE ai.wellbeing_checks (
    check_id             BIGINT IDENTITY(1,1) NOT NULL,
    user_id              INT                  NOT NULL,
    session_id           BIGINT               NULL,
    stationary_since     DATETIMEOFFSET(3)    NULL,
    interval_min         SMALLINT             NULL,   -- interval in force at the time
    started_at           DATETIMEOFFSET(3)    NOT NULL,  -- first prompt
    lat                  FLOAT                NULL,
    lon                  FLOAT                NULL,
    attempts             SMALLINT             NULL,   -- 1 or 2
    outcome              VARCHAR(20)          NULL,   -- okay_voice / okay_button / escalated / overridden_by_fall
    responded_at         DATETIMEOFFSET(3)    NULL,
    emergency_event_id   BIGINT               NULL,   -- set when escalated
    notification_status  VARCHAR(15)          NULL,   -- not_needed / queued / sent / acknowledged / failed
    ended_at             DATETIMEOFFSET(3)    NULL,
    CONSTRAINT PK_wellbeing_checks PRIMARY KEY (check_id),
    CONSTRAINT FK_wb_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id),
    CONSTRAINT FK_wb_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT FK_wb_event FOREIGN KEY (emergency_event_id)
        REFERENCES ai.emergency_events (event_id),
    CONSTRAINT CK_wb_attempts CHECK (attempts BETWEEN 1 AND 2),
    CONSTRAINT CK_wb_outcome  CHECK (outcome IN ('okay_voice', 'okay_button', 'escalated', 'overridden_by_fall')),
    CONSTRAINT CK_wb_notif    CHECK (notification_status IN ('not_needed', 'queued', 'sent', 'acknowledged', 'failed'))
);
GO

CREATE TABLE ai.blackbox_snapshots (
    snapshot_id         BIGINT IDENTITY(1,1) NOT NULL,
    session_id          BIGINT               NOT NULL,
    ts                  DATETIMEOFFSET(3)    NOT NULL,
    trigger_type        VARCHAR(15)          NULL,   -- fall / near_miss / fault / manual
    duration_s          SMALLINT             NOT NULL CONSTRAINT DF_bb_duration DEFAULT (30),
    file_path           NVARCHAR(400)        NULL,   -- file stays on the Pi
    file_size_kb        INT                  NULL,
    emergency_event_id  BIGINT               NULL,   -- when trigger = fall
    obstacle_id         BIGINT               NULL,   -- when trigger = near_miss
    fault_id            BIGINT               NULL,   -- when trigger = fault
    CONSTRAINT PK_blackbox_snapshots PRIMARY KEY (snapshot_id),
    CONSTRAINT FK_bb_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT FK_bb_event FOREIGN KEY (emergency_event_id)
        REFERENCES ai.emergency_events (event_id),
    CONSTRAINT FK_bb_obstacle FOREIGN KEY (obstacle_id)
        REFERENCES ai.obstacles (obstacle_id),
    CONSTRAINT FK_bb_fault FOREIGN KEY (fault_id)
        REFERENCES ai.faults (fault_id),
    CONSTRAINT CK_bb_trigger  CHECK (trigger_type IN ('fall', 'near_miss', 'fault', 'manual')),
    CONSTRAINT CK_bb_duration CHECK (duration_s > 0)
);
GO

CREATE TABLE ai.performance_metrics (
    metric_id   BIGINT IDENTITY(1,1) NOT NULL,
    session_id  BIGINT               NULL,
    ts          DATETIMEOFFSET(3)    NOT NULL,
    frame_id    BIGINT               NULL,
    module      VARCHAR(20)          NULL,   -- detector / fusion / tracker / hazard / alert / system
    metric      VARCHAR(30)          NULL,   -- latency_ms / fps / chip_temp_c / battery_pct / precision / recall
    stage       VARCHAR(20)          NULL,   -- capture / inference / e2e
    value       FLOAT                NOT NULL,
    unit        VARCHAR(10)          NULL,
    source      VARCHAR(10)          NULL,   -- live / replay / walk_test
    tags        NVARCHAR(MAX)        NULL,   -- JSON text
    CONSTRAINT PK_performance_metrics PRIMARY KEY (metric_id),
    CONSTRAINT FK_pm_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT CK_pm_source CHECK (source IN ('live', 'replay', 'walk_test')),
    CONSTRAINT CK_pm_tags   CHECK (tags IS NULL OR ISJSON(tags) = 1)
);
GO

CREATE TABLE ai.routes (
    route_id        INT IDENTITY(1,1) NOT NULL,
    user_id         INT               NOT NULL,
    name            NVARCHAR(100)     NULL,   -- named later by user or caregiver
    times_walked    INT               NOT NULL CONSTRAINT DF_routes_walked DEFAULT (1),
    is_known        BIT               NOT NULL CONSTRAINT DF_routes_known  DEFAULT (0),
    created_at      DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_routes_created DEFAULT (SYSDATETIMEOFFSET()),
    last_walked_at  DATETIMEOFFSET(3) NULL,
    CONSTRAINT PK_routes PRIMARY KEY (route_id),
    CONSTRAINT FK_routes_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id)
);
GO

CREATE TABLE ai.place_memory (
    place_id         INT IDENTITY(1,1) NOT NULL,
    route_id         INT               NULL,
    chroma_id        VARCHAR(64)       NULL,   -- embedding stored in ChromaDB
    place_type       VARCHAR(20)       NULL,   -- door / stairs / corner / crossing
    order_in_route   SMALLINT          NULL,
    position_source  VARCHAR(5)        NULL,   -- gps (outdoors) / vio (indoors)
    lat              FLOAT             NULL,   -- when position_source = gps
    lon              FLOAT             NULL,
    rel_x_m          REAL              NULL,   -- when position_source = vio: metres from the route anchor
    rel_y_m          REAL              NULL,
    times_matched    INT               NOT NULL CONSTRAINT DF_place_matched DEFAULT (0),
    first_seen       DATETIMEOFFSET(3) NULL,
    last_matched_at  DATETIMEOFFSET(3) NULL,
    CONSTRAINT PK_place_memory PRIMARY KEY (place_id),
    CONSTRAINT FK_place_route FOREIGN KEY (route_id)
        REFERENCES ai.routes (route_id) ON DELETE CASCADE,
    CONSTRAINT CK_place_type   CHECK (place_type IN ('door', 'stairs', 'corner', 'crossing')),
    CONSTRAINT CK_place_source CHECK (position_source IN ('gps', 'vio'))
);
GO

CREATE TABLE ai.landmark_matches (
    match_id              BIGINT IDENTITY(1,1) NOT NULL,
    session_id            BIGINT               NOT NULL,
    place_id              INT                  NOT NULL,
    ts                    DATETIMEOFFSET(3)    NOT NULL,
    distance              REAL                 NULL,   -- ChromaDB distance (smaller = more similar)
    accepted              BIT                  NULL,   -- below threshold and used
    sent_to_loop_closure  BIT                  NULL,
    is_correct            BIT                  NULL,   -- filled during evaluation (walk tests)
    CONSTRAINT PK_landmark_matches PRIMARY KEY (match_id),
    CONSTRAINT FK_lm_session FOREIGN KEY (session_id)
        REFERENCES ai.sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT FK_lm_place FOREIGN KEY (place_id)
        REFERENCES ai.place_memory (place_id) ON DELETE CASCADE
);
GO

CREATE TABLE ai.daily_summary (
    summary_id             BIGINT IDENTITY(1,1) NOT NULL,
    user_id                INT                  NOT NULL,
    summary_date           DATE                 NOT NULL,   -- Cairo day
    sessions_count         SMALLINT             NULL,
    active_minutes         INT                  NULL,
    distance_walked_m      REAL                 NULL,
    obstacles_detected     INT                  NULL,
    alerts_count           INT                  NULL,
    near_misses            INT                  NULL,       -- ttc_s < 1 s
    emergencies_count      SMALLINT             NULL,
    emergencies_cancelled  SMALLINT             NULL,
    wellbeing_checks       SMALLINT             NULL,
    wellbeing_escalations  SMALLINT             NULL,
    faults_count           SMALLINT             NULL,
    min_battery_pct        SMALLINT             NULL,
    last_lat               FLOAT                NULL,
    last_lon               FLOAT                NULL,
    generated_at           DATETIMEOFFSET(3)    NOT NULL CONSTRAINT DF_ds_generated DEFAULT (SYSDATETIMEOFFSET()),
    CONSTRAINT PK_daily_summary PRIMARY KEY (summary_id),
    CONSTRAINT UQ_daily_summary_user_day UNIQUE (user_id, summary_date),
    CONSTRAINT FK_ds_user FOREIGN KEY (user_id)
        REFERENCES core.users (user_id) ON DELETE CASCADE
);
GO

-- =====================================================================
-- 2) Indexes (SQL Server does NOT index foreign keys automatically)
-- =====================================================================
CREATE UNIQUE INDEX UX_place_memory_chroma ON ai.place_memory (chroma_id) WHERE chroma_id IS NOT NULL;

CREATE INDEX IX_users_language      ON core.users (language_id);
CREATE INDEX IX_caregivers_language ON core.caregivers (language_id);
CREATE INDEX IX_cgdev_caregiver     ON core.caregiver_devices (caregiver_id);
CREATE INDEX IX_uc_caregiver        ON core.user_caregivers (caregiver_id);
CREATE INDEX IX_ec_user             ON core.emergency_contacts (user_id, priority_order);
CREATE INDEX IX_devices_user        ON core.devices (user_id);

CREATE INDEX IX_sessions_user       ON ai.sessions (user_id, started_at);
CREATE INDEX IX_obstacles_session   ON ai.obstacles (session_id);
CREATE INDEX IX_faults_session      ON ai.faults (session_id, detected_at);
CREATE INDEX IX_alerts_session      ON ai.alerts (session_id, ts);
CREATE INDEX IX_alerts_obstacle     ON ai.alerts (obstacle_id);
CREATE INDEX IX_alerts_fault        ON ai.alerts (fault_id);
CREATE INDEX IX_loc_session         ON ai.location_logs (session_id, ts);
CREATE INDEX IX_ee_user             ON ai.emergency_events (user_id, ts);
CREATE INDEX IX_ee_session          ON ai.emergency_events (session_id);
CREATE INDEX IX_ee_ack_by           ON ai.emergency_events (acknowledged_by);
CREATE INDEX IX_ca_event            ON ai.call_attempts (event_id);
CREATE INDEX IX_ca_caregiver        ON ai.call_attempts (caregiver_id);
CREATE INDEX IX_wb_user             ON ai.wellbeing_checks (user_id, started_at);
CREATE INDEX IX_wb_session          ON ai.wellbeing_checks (session_id);
CREATE INDEX IX_wb_event            ON ai.wellbeing_checks (emergency_event_id);
CREATE INDEX IX_bb_session          ON ai.blackbox_snapshots (session_id);
CREATE INDEX IX_bb_event            ON ai.blackbox_snapshots (emergency_event_id);
CREATE INDEX IX_bb_obstacle         ON ai.blackbox_snapshots (obstacle_id);
CREATE INDEX IX_bb_fault            ON ai.blackbox_snapshots (fault_id);
CREATE INDEX IX_pm_metric           ON ai.performance_metrics (metric, module, ts);
CREATE INDEX IX_pm_session          ON ai.performance_metrics (session_id);
CREATE INDEX IX_routes_user         ON ai.routes (user_id);
CREATE INDEX IX_place_route         ON ai.place_memory (route_id);
CREATE INDEX IX_lm_session          ON ai.landmark_matches (session_id);
CREATE INDEX IX_lm_place            ON ai.landmark_matches (place_id);
GO

-- =====================================================================
-- 3) Seed data
-- =====================================================================
INSERT INTO core.languages (code, name) VALUES
    ('ar-EG', N'عربي مصري'),
    ('en',    N'English');
GO

-- =====================================================================
-- 4) Daily summary — builds one row per user for one Cairo day.
--    Run every night at 00:05 for "yesterday":
--      DECLARE @yesterday DATE = CAST(DATEADD(DAY, -1, SYSDATETIMEOFFSET() AT TIME ZONE 'Egypt Standard Time') AS DATE);
--      EXEC ai.usp_build_daily_summary @day = @yesterday;
--    Safe to run twice for the same day (it rebuilds that day).
-- =====================================================================
CREATE OR ALTER PROCEDURE ai.usp_build_daily_summary
    @day DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @from DATETIMEOFFSET(3) = CAST(@day AS DATETIME2) AT TIME ZONE 'Egypt Standard Time';
    DECLARE @to   DATETIMEOFFSET(3) = CAST(DATEADD(DAY, 1, @day) AS DATETIME2) AT TIME ZONE 'Egypt Standard Time';

    BEGIN TRANSACTION;

    DELETE FROM ai.daily_summary WHERE summary_date = @day;

    WITH ds AS (
        SELECT * FROM ai.sessions
        WHERE started_at >= @from AND started_at < @to
    )
    INSERT INTO ai.daily_summary
        (user_id, summary_date, sessions_count, active_minutes, distance_walked_m,
         obstacles_detected, alerts_count, near_misses, emergencies_count,
         emergencies_cancelled, wellbeing_checks, wellbeing_escalations,
         faults_count, min_battery_pct, last_lat, last_lon)
    SELECT
        u.user_id,
        @day,
        (SELECT COUNT(*) FROM ds WHERE ds.user_id = u.user_id),
        (SELECT SUM(DATEDIFF(MINUTE, ds.started_at, ds.ended_at)) FROM ds WHERE ds.user_id = u.user_id),
        (SELECT SUM(ds.distance_walked_m) FROM ds WHERE ds.user_id = u.user_id),
        (SELECT COUNT(*) FROM ai.obstacles o JOIN ds ON ds.session_id = o.session_id
            WHERE ds.user_id = u.user_id),
        (SELECT COUNT(*) FROM ai.alerts a JOIN ds ON ds.session_id = a.session_id
            WHERE ds.user_id = u.user_id),
        (SELECT COUNT(*) FROM ai.obstacles o JOIN ds ON ds.session_id = o.session_id
            WHERE ds.user_id = u.user_id AND o.ttc_s < 1.0),
        (SELECT COUNT(*) FROM ai.emergency_events e
            WHERE e.user_id = u.user_id AND e.ts >= @from AND e.ts < @to),
        (SELECT COUNT(*) FROM ai.emergency_events e
            WHERE e.user_id = u.user_id AND e.ts >= @from AND e.ts < @to AND e.status = 'cancelled'),
        (SELECT COUNT(*) FROM ai.wellbeing_checks w
            WHERE w.user_id = u.user_id AND w.started_at >= @from AND w.started_at < @to),
        (SELECT COUNT(*) FROM ai.wellbeing_checks w
            WHERE w.user_id = u.user_id AND w.started_at >= @from AND w.started_at < @to
              AND w.outcome = 'escalated'),
        (SELECT COUNT(*) FROM ai.faults f JOIN ds ON ds.session_id = f.session_id
            WHERE ds.user_id = u.user_id),
        (SELECT MIN(ds.end_battery_pct) FROM ds WHERE ds.user_id = u.user_id),
        last_loc.lat,
        last_loc.lon
    FROM core.users AS u
    OUTER APPLY (
        SELECT TOP 1 l.lat, l.lon
        FROM ai.location_logs l JOIN ds ON ds.session_id = l.session_id
        WHERE ds.user_id = u.user_id
        ORDER BY l.ts DESC
    ) AS last_loc
    WHERE EXISTS (SELECT 1 FROM ds WHERE ds.user_id = u.user_id);

    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- 5) Delete a user and ALL their data (right to withdraw)
--    Usage:  EXEC core.usp_delete_user_data @user_id = 5;
--    Note: delete the user's ChromaDB embeddings first (chroma_id in
--    ai.place_memory), because those rows are removed here.
-- =====================================================================
CREATE OR ALTER PROCEDURE core.usp_delete_user_data
    @user_id INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRY
        BEGIN TRANSACTION;

        -- rows that point at obstacles / faults / events with NO ACTION go first
        DELETE b FROM ai.blackbox_snapshots AS b
        JOIN ai.sessions AS s ON s.session_id = b.session_id
        WHERE s.user_id = @user_id;

        DELETE a FROM ai.alerts AS a
        JOIN ai.sessions AS s ON s.session_id = a.session_id
        WHERE s.user_id = @user_id;

        DELETE FROM ai.wellbeing_checks WHERE user_id = @user_id;

        -- emergencies (call_attempts cascade)
        DELETE FROM ai.emergency_events WHERE user_id = @user_id;

        -- sessions (obstacles, faults, location_logs, performance_metrics, landmark_matches cascade)
        DELETE FROM ai.sessions WHERE user_id = @user_id;

        -- routes (place_memory cascades)
        DELETE FROM ai.routes WHERE user_id = @user_id;

        -- the user (emergency_contacts, user_caregivers, daily_summary cascade; devices -> NULL)
        DELETE FROM core.users WHERE user_id = @user_id;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END;
GO

PRINT 'VisionPath v2 database created successfully (7 core + 14 ai tables).';
GO
