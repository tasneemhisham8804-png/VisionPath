# Contract changelog

Versioning: patch = wording only; minor = new optional field, new enum member or new message type; major = anything removed, renamed or changed in meaning. Every change needs a sample update and sign-off from the leads whose stages produce or consume the message.

## 1.0.0 (Week 1, pending sign-off)
- Envelope: seq, t_mono_ns, t_origin_ns, source, confidence, bearing, schema_version.
- Messages: camera_frame, ultrasonic_reading, detection_set, fused_obstacle, track, risk_alert, output_command.

## Planned
- 1.1.0 (Week 5): heartbeat message, agreed with Abdelrahman.
- 1.2.0 (Week 6): ego_motion message from Mariam for time to collision.
