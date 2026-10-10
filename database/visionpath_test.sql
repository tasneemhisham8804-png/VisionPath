/* =====================================================================
   VisionPath — TEST script for SQL Server   v2
   Run this AFTER visionpath_sqlserver.sql (v2) has run successfully.
   Safe to run more than once (Part 0 removes the old test data first).

   Results tab  -> the SELECT tables (Parts 2 and 4)
   Messages tab -> OK / FAIL lines (Parts 1 and 3)
   ===================================================================== */
USE VisionPath;
GO
SET NOCOUNT ON;

/* ---------------------------------------------------------------------
   PART 0: remove test data from any previous run
   --------------------------------------------------------------------- */
DECLARE @old INT = (SELECT TOP 1 user_id FROM core.users WHERE phone = '01000000001');
IF @old IS NOT NULL EXEC core.usp_delete_user_data @user_id = @old;
DELETE FROM core.caregivers WHERE email = 'caregiver.test@example.com';
DELETE FROM core.devices    WHERE serial_number = 'VP-TEST-0001';

/* ---------------------------------------------------------------------
   PART 1: insert one full example in all 21 tables
   --------------------------------------------------------------------- */
DECLARE @ar  INT = (SELECT language_id FROM core.languages WHERE code = 'ar-EG');
DECLARE @now DATETIMEOFFSET(3) = SYSDATETIMEOFFSET();
DECLARE @start DATETIMEOFFSET(3) = DATEADD(MINUTE, -20, @now);

-- core: user (with first run setup and wellbeing settings)
INSERT INTO core.users (full_name, phone, date_of_birth, gender, language_id, vision_level,
                        height_cm, walking_pace, speech_rate, haptic_strength,
                        wellbeing_interval_min, quiet_hours_start, quiet_hours_end)
VALUES (N'مستخدم تجريبي', '01000000001', '1990-05-10', 'male', @ar, 'total',
        172, 'normal', 'normal', 3,
        15, '23:00', '07:00');
DECLARE @user INT = SCOPE_IDENTITY();

-- core: caregiver + phone + link
INSERT INTO core.caregivers (full_name, phone, email, language_id)
VALUES (N'مسؤول تجريبي', '01000000002', 'caregiver.test@example.com', @ar);
DECLARE @cg INT = SCOPE_IDENTITY();

INSERT INTO core.caregiver_devices (caregiver_id, platform) VALUES (@cg, 'android');

INSERT INTO core.user_caregivers (user_id, caregiver_id, relationship, is_primary)
VALUES (@user, @cg, N'أخت', 1);

-- core: emergency contacts + device
INSERT INTO core.emergency_contacts (user_id, name, phone, relationship, priority_order)
VALUES (@user, N'جهة اتصال أولى', '01000000003', N'أب', 1);
INSERT INTO core.emergency_contacts (user_id, name, phone, priority_order, is_ambulance)
VALUES (@user, N'الإسعاف', '123', 2, 1);

INSERT INTO core.devices (user_id, serial_number, hw_version, fw_version)
VALUES (@user, 'VP-TEST-0001', 'v1', '0.1.0');

-- ai: walk session (started 20 minutes ago)
INSERT INTO ai.sessions (user_id, started_at, monotonic_start_s, git_commit, start_battery_pct)
VALUES (@user, @start, 1234.567, 'abc1234', 95);
DECLARE @session BIGINT = SCOPE_IDENTITY();

-- ai: an obstacle (near miss: ttc < 1 s)
INSERT INTO ai.obstacles (session_id, track_id, class, confidence, source, distance_m, direction,
                          height_level, closing_speed_mps, ttc_s, hazard_score, first_seen, last_seen, lat, lon)
VALUES (@session, 7, 'pole', 0.87, 'fused', 0.9, 'ahead',
        'body', 1.2, 0.8, 0.92, DATEADD(SECOND, -3, @now), @now, 30.0444, 31.2357);
DECLARE @obstacle BIGINT = SCOPE_IDENTITY();

-- ai: a camera fault that recovered after 40 s
INSERT INTO ai.faults (session_id, module, fault_type, detected_by, detected_at, degraded_mode,
                       user_notified, message, recovered_at)
VALUES (@session, 'camera', 'heartbeat_lost', 'watchdog', DATEADD(MINUTE, -10, @now), 'ultrasonic_only',
        'voice', N'لا توجد رسائل من الكاميرا لمدة 3 ثواني', DATEADD(SECOND, 40, DATEADD(MINUTE, -10, @now)));
DECLARE @fault BIGINT = SCOPE_IDENTITY();

-- ai: two alerts (one from the obstacle, one from the fault)
INSERT INTO ai.alerts (session_id, obstacle_id, ts, alert_type, reason_code, channel, direction,
                       priority, message, e2e_latency_ms)
VALUES (@session, @obstacle, @now, 'obstacle', 'POLE_AHEAD_1S', 'voice', 'ahead',
        1, N'عمود قدامك', 182.5);
INSERT INTO ai.alerts (session_id, fault_id, ts, alert_type, reason_code, channel, priority, message)
VALUES (@session, @fault, DATEADD(MINUTE, -10, @now), 'system_fault', 'CAMERA_DOWN', 'voice',
        2, N'الكاميرا وقفت، بكمل بحساسات المسافة');

-- ai: navigation logs (the last one is the last known location)
INSERT INTO ai.location_logs (session_id, ts, lat, lon, accuracy_m) VALUES
    (@session, DATEADD(MINUTE, -15, @now), 30.0440, 31.2350, 5),
    (@session, DATEADD(MINUTE, -5,  @now), 30.0442, 31.2354, 4),
    (@session, @now,                       30.0444, 31.2357, 4);

-- ai: emergency 1 = a fall the user cancelled by voice
INSERT INTO ai.emergency_events (user_id, session_id, ts, trigger_type, severity, lat, lon,
                                 battery_pct, status, cancel_method)
VALUES (@user, @session, DATEADD(MINUTE, -8, @now), 'fall', 'low', 30.0442, 31.2354,
        90, 'cancelled', 'voice');
DECLARE @fall_event BIGINT = SCOPE_IDENTITY();

-- ai: emergency 2 = a wellbeing check with no answer, acknowledged on the 2nd push
INSERT INTO ai.emergency_events (user_id, session_id, ts, trigger_type, lat, lon, battery_pct,
                                 status, caregiver_notified_at, acknowledged_at, acknowledged_by)
VALUES (@user, @session, DATEADD(MINUTE, -2, @now), 'wellbeing', 30.0444, 31.2357, 88,
        'notified', DATEADD(SECOND, -115, @now), DATEADD(SECOND, 200, @now), @cg);
DECLARE @wb_event BIGINT = SCOPE_IDENTITY();

INSERT INTO ai.call_attempts (event_id, caregiver_id, attempt_order, status, failure_reason, attempted_at, completed_at)
VALUES (@wb_event, @cg, 1, 'failed', N'مفيش تأكيد خلال 5 دقايق', DATEADD(SECOND, -115, @now), DATEADD(SECOND, 185, @now)),
       (@wb_event, @cg, 2, 'acknowledged', NULL, DATEADD(SECOND, 185, @now), DATEADD(SECOND, 200, @now));

-- ai: two wellbeing checks (one answered by voice, one escalated)
INSERT INTO ai.wellbeing_checks (user_id, session_id, stationary_since, interval_min, started_at, lat, lon,
                                 attempts, outcome, responded_at, notification_status, ended_at)
VALUES (@user, @session, DATEADD(MINUTE, -35, @now), 15, DATEADD(MINUTE, -19, @now), 30.0440, 31.2350,
        1, 'okay_voice', DATEADD(SECOND, 10, DATEADD(MINUTE, -19, @now)), 'not_needed',
        DATEADD(SECOND, 10, DATEADD(MINUTE, -19, @now)));
INSERT INTO ai.wellbeing_checks (user_id, session_id, stationary_since, interval_min, started_at, lat, lon,
                                 attempts, outcome, emergency_event_id, notification_status, ended_at)
VALUES (@user, @session, DATEADD(MINUTE, -19, @now), 15, DATEADD(MINUTE, -4, @now), 30.0444, 31.2357,
        2, 'escalated', @wb_event, 'acknowledged', DATEADD(MINUTE, -2, @now));

-- ai: black box snapshots (one for the fall, one for the fault)
INSERT INTO ai.blackbox_snapshots (session_id, ts, trigger_type, file_path, file_size_kb, emergency_event_id)
VALUES (@session, DATEADD(MINUTE, -8, @now), 'fall', N'/data/blackbox/test_fall.bin', 2048, @fall_event);
INSERT INTO ai.blackbox_snapshots (session_id, ts, trigger_type, file_path, file_size_kb, fault_id)
VALUES (@session, DATEADD(MINUTE, -10, @now), 'fault', N'/data/blackbox/test_fault.bin', 1900, @fault);

-- ai: performance numbers
INSERT INTO ai.performance_metrics (session_id, ts, frame_id, module, metric, stage, value, unit, source, tags)
VALUES (@session, @now, 1001, 'detector', 'latency_ms',  'inference', 41.3,  'ms', 'live', '{"model":"INT8"}'),
       (@session, @now, 1001, 'alert',    'latency_ms',  'e2e',       182.5, 'ms', 'live', NULL),
       (@session, @now, NULL, 'system',   'chip_temp_c', NULL,        61.0,  'C',  'live', NULL);

-- ai: a known route, two places (outdoor GPS + indoor VIO) and one match
INSERT INTO ai.routes (user_id, name, times_walked, is_known, last_walked_at)
VALUES (@user, N'البيت ← الكلية', 3, 1, @now);
DECLARE @route INT = SCOPE_IDENTITY();

INSERT INTO ai.place_memory (route_id, chroma_id, place_type, order_in_route, position_source, lat, lon, times_matched, first_seen)
VALUES (@route, 'chroma-test-001', 'door', 1, 'gps', 30.0450, 31.2360, 3, DATEADD(DAY, -2, @now));
DECLARE @place1 INT = SCOPE_IDENTITY();
INSERT INTO ai.place_memory (route_id, chroma_id, place_type, order_in_route, position_source, rel_x_m, rel_y_m, times_matched, first_seen)
VALUES (@route, 'chroma-test-002', 'stairs', 2, 'vio', 12.5, -3.0, 3, DATEADD(DAY, -2, @now));

INSERT INTO ai.landmark_matches (session_id, place_id, ts, distance, accepted, sent_to_loop_closure, is_correct)
VALUES (@session, @place1, DATEADD(MINUTE, -12, @now), 0.18, 1, 1, 1);

-- ai: end the session
UPDATE ai.sessions
SET ended_at = @now, end_battery_pct = 88, distance_walked_m = 950
WHERE session_id = @session;

-- ai: daily summary for the session's Cairo day
DECLARE @day DATE = CAST(@start AT TIME ZONE 'Egypt Standard Time' AS DATE);
EXEC ai.usp_build_daily_summary @day = @day;

PRINT N'PART 1 OK: sample data inserted in all 21 tables.';

/* ---------------------------------------------------------------------
   PART 2: read the data back through the relations
   --------------------------------------------------------------------- */

-- 2a) alert -> obstacle / fault -> session -> user
SELECT u.full_name, a.alert_type, a.reason_code, o.class, o.direction, o.ttc_s,
       f.module AS fault_module, f.degraded_mode, a.message
FROM ai.alerts         AS a
JOIN ai.sessions       AS s ON s.session_id  = a.session_id
JOIN core.users        AS u ON u.user_id     = s.user_id
LEFT JOIN ai.obstacles AS o ON o.obstacle_id = a.obstacle_id
LEFT JOIN ai.faults    AS f ON f.fault_id    = a.fault_id
WHERE u.user_id = @user
ORDER BY a.ts;

-- 2b) emergencies -> push attempts -> caregiver who acknowledged
SELECT e.trigger_type, e.status, e.cancel_method, c.attempt_order,
       c.status AS push_status, c.failure_reason, cg.full_name AS caregiver,
       ack.full_name AS acknowledged_by
FROM ai.emergency_events   AS e
LEFT JOIN ai.call_attempts AS c   ON c.event_id      = e.event_id
LEFT JOIN core.caregivers  AS cg  ON cg.caregiver_id = c.caregiver_id
LEFT JOIN core.caregivers  AS ack ON ack.caregiver_id = e.acknowledged_by
WHERE e.user_id = @user
ORDER BY e.ts, c.attempt_order;

-- 2c) wellbeing checks -> escalation event
SELECT w.started_at, w.attempts, w.outcome, w.notification_status,
       e.trigger_type AS escalation_trigger, e.status AS escalation_status
FROM ai.wellbeing_checks       AS w
LEFT JOIN ai.emergency_events  AS e ON e.event_id = w.emergency_event_id
WHERE w.user_id = @user
ORDER BY w.started_at;

-- 2d) black box -> what caused it
SELECT b.trigger_type, b.file_path, e.trigger_type AS from_event, f.module AS from_fault
FROM ai.blackbox_snapshots    AS b
LEFT JOIN ai.emergency_events AS e ON e.event_id = b.emergency_event_id
LEFT JOIN ai.faults           AS f ON f.fault_id = b.fault_id
WHERE b.session_id = @session;

-- 2e) route -> places -> matches
SELECT r.name AS route, p.order_in_route, p.place_type, p.position_source,
       p.lat, p.rel_x_m, COUNT(m.match_id) AS matches
FROM ai.routes                AS r
JOIN ai.place_memory          AS p ON p.route_id = r.route_id
LEFT JOIN ai.landmark_matches AS m ON m.place_id = p.place_id
WHERE r.user_id = @user
GROUP BY r.name, p.order_in_route, p.place_type, p.position_source, p.lat, p.rel_x_m
ORDER BY p.order_in_route;

-- 2f) daily summary — expected: sessions 1, obstacles 1, alerts 2, near_misses 1,
--     emergencies 2, cancelled 1, wellbeing 2, escalations 1, faults 1, min battery 88,
--     last location 30.0444 / 31.2357
SELECT * FROM ai.daily_summary WHERE user_id = @user;

/* ---------------------------------------------------------------------
   PART 3: bad data MUST be rejected (every line should say OK)
   --------------------------------------------------------------------- */
BEGIN TRY
    INSERT INTO ai.obstacles (session_id, direction) VALUES (@session, 'center');
    PRINT N'FAIL: direction ''center'' was accepted';
END TRY
BEGIN CATCH PRINT N'OK: direction must be left / ahead / right'; END CATCH;

BEGIN TRY
    INSERT INTO ai.sessions (user_id, started_at, start_battery_pct) VALUES (@user, @now, 150);
    PRINT N'FAIL: battery 150% was accepted';
END TRY
BEGIN CATCH PRINT N'OK: battery 150% rejected'; END CATCH;

BEGIN TRY
    INSERT INTO ai.sessions (user_id, started_at) VALUES (999999, @now);
    PRINT N'FAIL: session for a user that does not exist was accepted';
END TRY
BEGIN CATCH PRINT N'OK: session for missing user rejected (foreign key works)'; END CATCH;

BEGIN TRY
    INSERT INTO core.caregivers (full_name, email) VALUES (N'مكرر', 'caregiver.test@example.com');
    PRINT N'FAIL: duplicate email was accepted';
END TRY
BEGIN CATCH PRINT N'OK: duplicate email rejected'; END CATCH;

BEGIN TRY
    INSERT INTO ai.performance_metrics (ts, value, tags) VALUES (@now, 1, 'not json');
    PRINT N'FAIL: invalid JSON in tags was accepted';
END TRY
BEGIN CATCH PRINT N'OK: invalid JSON rejected'; END CATCH;

BEGIN TRY
    INSERT INTO ai.obstacles (session_id, confidence) VALUES (@session, 1.7);
    PRINT N'FAIL: confidence 1.7 was accepted';
END TRY
BEGIN CATCH PRINT N'OK: confidence > 1 rejected'; END CATCH;

BEGIN TRY
    UPDATE core.users SET wellbeing_interval_min = 5 WHERE user_id = @user;
    PRINT N'FAIL: wellbeing interval 5 min was accepted';
END TRY
BEGIN CATCH PRINT N'OK: wellbeing interval must be 10 - 60 min'; END CATCH;

BEGIN TRY
    UPDATE ai.emergency_events SET cancel_method = 'shout' WHERE event_id = @fall_event;
    PRINT N'FAIL: cancel_method ''shout'' was accepted';
END TRY
BEGIN CATCH PRINT N'OK: cancel_method must be voice / button'; END CATCH;

BEGIN TRY
    INSERT INTO ai.daily_summary (user_id, summary_date) VALUES (@user, @day);
    PRINT N'FAIL: a second summary for the same day was accepted';
END TRY
BEGIN CATCH PRINT N'OK: one summary per user per day'; END CATCH;

BEGIN TRY
    INSERT INTO ai.place_memory (route_id, chroma_id) VALUES (@route, 'chroma-test-001');
    PRINT N'FAIL: duplicate chroma_id was accepted';
END TRY
BEGIN CATCH PRINT N'OK: duplicate chroma_id rejected'; END CATCH;

PRINT N'PART 3 done.';
GO

/* ---------------------------------------------------------------------
   PART 4 (optional): test deleting a user with ALL their data.
   Change 0 to 1 below, then run the file again.
   --------------------------------------------------------------------- */
DECLARE @run_delete_test BIT = 0;

IF @run_delete_test = 1
BEGIN
    DECLARE @u INT = (SELECT TOP 1 user_id FROM core.users WHERE phone = '01000000001');
    EXEC core.usp_delete_user_data @user_id = @u;

    -- every number below should be 0
    SELECT
        (SELECT COUNT(*) FROM core.users              WHERE user_id = @u) AS users_left,
        (SELECT COUNT(*) FROM core.emergency_contacts WHERE user_id = @u) AS contacts_left,
        (SELECT COUNT(*) FROM core.user_caregivers    WHERE user_id = @u) AS links_left,
        (SELECT COUNT(*) FROM ai.sessions             WHERE user_id = @u) AS sessions_left,
        (SELECT COUNT(*) FROM ai.emergency_events     WHERE user_id = @u) AS emergencies_left,
        (SELECT COUNT(*) FROM ai.wellbeing_checks     WHERE user_id = @u) AS wellbeing_left,
        (SELECT COUNT(*) FROM ai.daily_summary        WHERE user_id = @u) AS summaries_left,
        (SELECT COUNT(*) FROM ai.routes               WHERE user_id = @u) AS routes_left,
        (SELECT COUNT(*) FROM ai.place_memory WHERE chroma_id LIKE 'chroma-test-%')             AS places_left,
        (SELECT COUNT(*) FROM ai.blackbox_snapshots WHERE file_path LIKE N'/data/blackbox/test_%') AS blackbox_left,
        (SELECT COUNT(*) FROM core.devices WHERE serial_number = 'VP-TEST-0001' AND user_id IS NOT NULL) AS device_still_linked;

    PRINT N'PART 4 done: check that every number in the result is 0.';
END
GO
