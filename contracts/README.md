# VisionPath data flow contract v1.0.0

Status: **draft, pending sign-off by all leads**. The code in `schema.py` is the source of truth; this page explains it. If they ever disagree, `schema.py` wins and this page gets fixed.

```
camera_frame ─┐
              ├─> detection_set ─> fused_obstacle ─> track ─> risk_alert ─> output_command
ultrasonic ───┘        (Talal)       (Tasneem)     (Tasneem)   (Tasneem)    (Baraa / Mostafa)
(Malak Emad)
```

## How to use it

```python
from contracts import Track, from_json, now_ns

msg = Track(seq=0, t_mono_ns=now_ns(), t_origin_ns=frame.t_mono_ns, source="tracker",
            confidence=0.9, bearing="ahead", track_id=7, hazard="pedestrian",
            distance_m=2.4, bearing_deg=2.5, closing_speed_mps=1.6, ttc_s=1.5,
            ego_compensated=False, age_frames=12)
line = msg.to_json()        # publish or write to the replay log
back = from_json(line)      # every consumer parses with this, which validates
```

Messages are transport independent: the same JSON line goes over a queue, a socket, or into a `.jsonl` replay log. Images are never inside a message; `camera_frame.image_ref` points to them.

## Envelope (every message)

| Field | Type | Meaning |
|---|---|---|
| `msg_type` | string | Which message this is (see below) |
| `schema_version` | string | Contract version that produced it, e.g. `1.0.0` |
| `seq` | int ≥ 0 | Per source counter, +1 per message; gaps reveal drops |
| `t_mono_ns` | int | `time.monotonic_ns()` on the Pi when published |
| `t_origin_ns` | int | `t_mono_ns` of the sensor reading this derives from, copied unchanged downstream |
| `source` | string | Producing module: `camera`, `ultrasonic`, `detector`, `fusion`, `tracker`, `risk`, `feedback` |
| `confidence` | float 0–1 | How much the producer trusts this message |
| `bearing` | `left` / `ahead` / `right` / null | Direction; null only on non-spatial types |

`t_mono_ns − t_origin_ns` is the latency up to that stage, so the latency budget is measured from the logs with no extra instrumentation.

## Message types

| Type | Producer → consumer | Key fields | Bearing |
|---|---|---|---|
| `camera_frame` | camera → detector | `frame_id`, `capture` (webcam / oakd / replay), `camera_id`, `width`, `height`, `image_ref` | null |
| `ultrasonic_reading` | ultrasonic → fusion | `sensor_id`, `mount_deg`, `distance_m` (null = no echo) | required |
| `detection_set` | detector → fusion | `frame_id`, `model_id`, `objects[]` (one per frame, even if empty) | null (each object has its own) |
| `fused_obstacle` | fusion → tracker | `obstacle_id`, `frame_id` (null if camera down), `hazard`, `distance_m`, `bearing_deg`, `sources[]` | required |
| `track` | tracker → risk | `track_id` (persistent), `hazard`, `distance_m`, `bearing_deg`, `closing_speed_mps`, `ttc_s`, `ego_compensated`, `age_frames` | required |
| `risk_alert` | risk → feedback, logging | `alert_id`, `hazard`, `priority`, `reason`, `track_id` | required |
| `output_command` | feedback → haptic / voice | `command_id`, `channel`, `priority`, `interrupt`, `haptic_pattern` + `intensity` or `voice_line` + `language` | optional |

Each detected object: `hazard`, `score` (0–1), `bbox` (normalised 0–1, top left origin), `bearing`, `bearing_deg`, `depth_m` (null until OAK D depth is used).

Each alert `reason`: `code`, `ttc_s`, `distance_m`, `detail`. This is what the "why?" voice command speaks and what makes every alert explainable from the log alone.

## Conventions

- **Units:** metres, seconds, metres per second, degrees. Never centimetres or milliseconds in a message.
- **Angles:** 0° = straight ahead, negative = left, positive = right.
- **Bearing rule:** `|bearing_deg| ≤ 15°` is ahead. Always use `bearing_from_deg()`; never re-implement it.
- **Speed sign:** `closing_speed_mps > 0` means getting closer. `ttc_s` is null when not approaching.
- **Unknown values:** null, never `-1` or `0`.
- **Priority order:** critical > high > medium > low > info. Sort with `Priority.rank`, not the string.
- **Hazard classes:** curb, step, pole, pedestrian, overhead, manhole, pothole, motorbike, tuktuk, hanging_cable, vehicle, drop, unknown_obstacle. `unknown_obstacle` covers the class agnostic corridor check and ultrasonic only detections.
- **Pattern and voice IDs** (`haptic_pattern`, `voice_line`) are owned by the haptic and voice modules. The contract carries the ID string only, so new patterns don't need a contract change.

## Versioning

| Change | Bump | Old messages still parse? |
|---|---|---|
| Wording or docs only | patch | yes |
| New optional field, enum member or message type | minor | yes |
| Remove, rename or change the meaning of anything | major | no |

Consumers reject a different major version, and a newer minor than they know (the fix is to pull `contracts/`). Older recordings always replay on newer code within the same major, which keeps the replay harness working all semester. Every change goes in `CHANGELOG.md` with an updated sample in `samples/`.

## Tests and CI

`python -m unittest discover -s tests -v` checks every golden sample parses and round-trips, every message type has a sample, the full chain in `samples/chain_frame42.jsonl` keeps one `t_origin_ns`, and the envelope, version and stage rules hold. GitHub Actions runs it on every push and pull request (`.github/workflows/contracts.yml`).

## Draft latency budget (haptic path, target 300 ms)

Proposed numbers to measure in Week 3. The first haptic cue must land within 300 ms of the sensor reading; voice follows and adds Bluetooth latency (measured by Mostafa in Week 3).

| Stage | Budget (ms) |
|---|---|
| Capture and transfer | 35 |
| Detection on Hailo | 40 |
| Fusion | 15 |
| Tracking and time to collision | 10 |
| Risk scoring | 5 |
| Output dispatch | 10 |
| Haptic actuation (I2C + LRA) | 25 |
| **Subtotal** | **140** |
| Margin (p95 jitter, second model, depth) | 160 |

## Sign-off

| Lead | Stage | Signed |
|---|---|---|
| Tasneem | Architecture, fusion, tracking, risk | ☐ |
| Talal | Detection | ☐ |
| Malak Emad | Ultrasonic, hardware | ☐ |
| Baraa | Haptic patterns, app | ☐ |
| Mostafa | Voice | ☐ |
| Mariam | Localization, ego motion | ☐ |
| Abdelrahman | Backend, heartbeat | ☐ |
| Yasmin | Logging, replay data | ☐ |
| Malak Ibrahim | Fall detection | ☐ |
