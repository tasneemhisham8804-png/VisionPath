# datalogger

Owner: Yasmin Sherif Mohamed (database, logging, replay data)

The service that runs on the Pi. It listens to every message on the ZeroMQ bus and does two jobs:

1. **Metrics logging.** It writes detection and navigation events to the on-device SQLite database (`data/visionpath.db`) as they happen.
2. **Black box.** It keeps the last 30 s of bus messages in memory. On a near miss, a fault, a fall or a manual trigger it saves them as a replayable `.jsonl` snapshot.

## Run

You need Python 3.11 or newer. Live mode also needs pyzmq: `pip install -r datalogger/requirements.txt`.

```
python -m datalogger.run                      # live: subscribe to the bus (Ctrl+C to stop)
python -m datalogger.stub_publisher           # in a second window: 60 s stub scenario, near miss at about 44 s
python -m datalogger.run --from-file X.jsonl  # no bus: log a recording (performance_metrics.source = replay)
python -m datalogger.blackbox_demo            # Week 2 check without the bus: prints RESULT: PASS
python -m unittest discover -s tests -v       # all tests (the ZeroMQ test is skipped without pyzmq)
```

## Config

All settings are in `config/visionpath.toml`. To use another file, set `VISIONPATH_CONFIG`.

- `[bus] endpoint` (or `endpoints = [...]`) is the only place the port is written. The code has no default endpoint.
- `[datalogger]` holds the database path, the black box folder, the 30 s window, the near miss TTC (1.0 s), the 10 s cooldown and the commit interval.

## Bus format

- ZeroMQ PUB/SUB, with no broker.
- Every message is sent as two frames: `[topic, json]`.
  - The topic is the contract `msg_type`.
  - The JSON is exactly `msg.to_json()`.
- The subscriber also accepts a single frame, either `"topic json"` or just the JSON.

## What goes where

| Message | Table | What is stored |
|---|---|---|
| every message (latency > 0) | `performance_metrics` | `latency_ms` = t_mono_ns − t_origin_ns, by module (source) and stage (msg_type) |
| `detection_set` | `performance_metrics` | `objects_detected` (count); hazards and model_id in `tags` |
| `track` | `obstacles` | One row per track_id (upsert): class, direction, closest distance, lowest TTC, first and last seen. Source is camera, depth, ultrasonic, or fused when the matching `fused_obstacle` lists more than one sensor |
| `risk_alert` | `alerts` | One row per alert, linked to its obstacle. Priority is `Priority.rank` (0 = critical … 4 = info). `system_fault` → alert_type system_fault, `low_confidence` → weather_degraded |
| `output_command` | `alerts` | Channel and `e2e_latency_ms` of the alert it plays |
| any message, any type | black box | Last 30 s kept. Snapshot in `data/blackbox/`, one row in `blackbox_snapshots` |

Some messages are not logged to the database:

- an unknown `msg_type` (for example a future heartbeat);
- a newer `schema_version`;
- an invalid message.

These are counted and skipped, so the service never stops on a bad message. The black box still records any message that has a `t_mono_ns`.

Rows are committed once per `flush_interval_s` (default 1 s) to limit SD card writes.

## Black box snapshot

A snapshot is JSON Lines with one message per line, exactly as it was on the bus, sorted by `t_mono_ns`. This is the replay harness format.

- **Triggers:**
  - `near_miss`: a `track` with `ttc_s` < 1.0.
  - `fault`: a `risk_alert` whose `reason.code` is `system_fault`.
  - `fall` and `manual`: called through `BlackBox.trigger()`.
- **Cooldown:** at most one snapshot of each kind every 10 s.
- **Safe write:** the file is written to a temporary name, then fsynced and renamed. A power cut never leaves half a file.
- **No video:** there are no images in the snapshot. `camera_frame.image_ref` points to an image that was not saved. A replay of a snapshot therefore starts from `detection_set`, which is enough for fusion, tracking and risk.

## Files

| File | What it is |
|---|---|
| `run.py` | The service (bus → logger + black box) |
| `logger.py` | Messages → database rows |
| `blackbox.py` | 30 s ring buffer and snapshots |
| `bus.py` | ZeroMQ publisher and subscriber (pyzmq is imported only when used) |
| `config.py` | Reads `config/visionpath.toml` |
| `db.py` + `schema_sqlite.sql` | The 12 on-device tables and the session clock (t_mono_ns → UTC) |
| `stub_stream.py` / `stub_publisher.py` | Fake sensor stream until the real sensors arrive (Week 8) |
| `blackbox_demo.py` | Week 2 "done when" check |
