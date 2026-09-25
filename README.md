# VisionPath
Wearable assistive navigation system for visually impaired users. Real time obstacle
and hazard detection, collision risk prediction, fall detection, indoor/outdoor
navigation, and voice/haptic feedback, with a caregiver companion app.

## Repo layout

See each top level folder for that module's code. `docs/architecture.md` holds
the system interface contract, `docs/team-plan.md` the full weekly breakdown.

## Hardware note

All hardware is available except the OAK D camera. Anything depending on it uses
`scripts/mock_camera_feed.py` (webcam or recorded footage) as a stand in until
the camera arrives.