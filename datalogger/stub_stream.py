"""
Stub sensor stream ("stubbed now, real sensors in Week 8").

Generates contract-valid JSON lines, exactly like the real pipeline will
publish them, so the logger and the black box can be built and tested on a
laptop with no camera, no ultrasonic sensor and no message bus.

Scenario (default 60 s at 10 frames per second):
  * 0 - 30 s : the user walks, the path is clear (camera frames, empty
               detections, ultrasonic "no echo").
  * 30 s on  : a pedestrian appears 12 m ahead and closes at 0.8 m/s.
               Time to collision drops below 2 s (high alert) and then
               below 1 s (critical alert, near miss) at about 44 s.
  * fault_at_s (optional): one system_fault risk alert, for testing faults.

Every message is built with the contract dataclasses, so anything this
module yields is guaranteed to pass contracts.from_json().
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterator

from contracts import (BBox, CameraFrame, DetectedObject, DetectionSet, FusedObstacle,
                       OutputCommand, Reason, RiskAlert, Track, UltrasonicReading,
                       bearing_from_deg)

# Stage delays (ns) after the camera frame, taken from the contract's sample chain.
DETECT_NS, FUSE_NS, TRACK_NS, RISK_NS, OUTPUT_NS = 38_000_000, 55_000_000, 63_000_000, 68_000_000, 75_000_000
ULTRASONIC_RANGE_M = 4.0


def stub_stream(duration_s: float = 60.0, fps: int = 10, start_ns: int = 1_000_000_000_000,
                obstacle_from_s: float = 30.0, start_distance_m: float = 12.0,
                closing_speed_mps: float = 0.8, fault_at_s: float | None = None) -> Iterator[str]:
    """Yield contract JSON lines in publish order (sorted by t_mono_ns)."""
    seq = defaultdict(int)                       # per source counter, as the contract requires
    alert_id = command_id = 0
    alerted_high = alerted_critical = False
    fault_sent = False
    frame_ns = 1_000_000_000 // fps
    track_id, bearing_deg = 7, 2.5
    bearing = bearing_from_deg(bearing_deg)

    def nxt(source):
        value = seq[source]
        seq[source] += 1
        return value

    for frame_id in range(int(duration_s * fps)):
        t = start_ns + frame_id * frame_ns       # t_origin_ns of everything derived from this frame
        t_s = frame_id / fps
        out = []

        present = t_s >= obstacle_from_s
        distance = start_distance_m - closing_speed_mps * (t_s - obstacle_from_s) if present else None
        if distance is not None and distance <= 0.3:
            present, distance = False, None      # the user stopped / walked around it

        out.append(CameraFrame(
            seq=nxt("camera"), t_mono_ns=t, t_origin_ns=t, source="camera", confidence=1.0,
            bearing=None, frame_id=frame_id, capture="replay", camera_id="stub-cam",
            width=640, height=480, image_ref=f"stub://frames/{frame_id}"))

        echo = distance if distance is not None and distance <= ULTRASONIC_RANGE_M else None
        out.append(UltrasonicReading(
            seq=nxt("ultrasonic"), t_mono_ns=t + 2_000_000, t_origin_ns=t + 2_000_000,
            source="ultrasonic", confidence=0.9, bearing="ahead", sensor_id="us-front",
            mount_deg=0.0, distance_m=echo))

        objects = []
        if present:
            objects.append(DetectedObject(
                hazard="pedestrian", score=0.88, bbox=BBox(x1=0.45, y1=0.2, x2=0.6, y2=0.9),
                bearing=bearing, bearing_deg=bearing_deg, depth_m=None))
        out.append(DetectionSet(
            seq=nxt("detector"), t_mono_ns=t + DETECT_NS, t_origin_ns=t, source="detector",
            confidence=0.95, bearing=None, frame_id=frame_id, model_id="stub-detector", objects=objects))

        if present:
            sources = ["camera", "ultrasonic"] if echo is not None else ["camera"]
            out.append(FusedObstacle(
                seq=nxt("fusion"), t_mono_ns=t + FUSE_NS, t_origin_ns=t, source="fusion",
                confidence=0.91, bearing=bearing, obstacle_id=0, frame_id=frame_id,
                hazard="pedestrian", distance_m=round(distance, 3), bearing_deg=bearing_deg,
                sources=sources))

            ttc = round(distance / closing_speed_mps, 3)
            out.append(Track(
                seq=nxt("tracker"), t_mono_ns=t + TRACK_NS, t_origin_ns=t, source="tracker",
                confidence=0.9, bearing=bearing, track_id=track_id, hazard="pedestrian",
                distance_m=round(distance, 3), bearing_deg=bearing_deg,
                closing_speed_mps=closing_speed_mps, ttc_s=ttc, ego_compensated=False,
                age_frames=frame_id - int(obstacle_from_s * fps)))

            level = None
            if ttc < 1.0 and not alerted_critical:
                level, alerted_critical = "critical", True
            elif ttc < 2.0 and not alerted_high:
                level, alerted_high = "high", True
            if level:
                alert_id += 1
                out.append(RiskAlert(
                    seq=nxt("risk"), t_mono_ns=t + RISK_NS, t_origin_ns=t, source="risk",
                    confidence=0.9, bearing=bearing, alert_id=alert_id, hazard="pedestrian",
                    priority=level, track_id=track_id,
                    reason=Reason(code="ttc_below_threshold", ttc_s=ttc, distance_m=round(distance, 3),
                                  detail=f"ttc {ttc:.1f} s < {2.0 if level == 'high' else 1.0} s")))
                command_id += 1
                out.append(OutputCommand(
                    seq=nxt("feedback"), t_mono_ns=t + OUTPUT_NS, t_origin_ns=t, source="feedback",
                    confidence=0.9, bearing=bearing, command_id=command_id, channel="haptic",
                    priority=level, interrupt=True, alert_id=alert_id,
                    haptic_pattern="approach_ahead", intensity=1.0 if level == "critical" else 0.8))

        if fault_at_s is not None and not fault_sent and t_s >= fault_at_s:
            fault_sent = True
            alert_id += 1
            out.append(RiskAlert(
                seq=nxt("risk"), t_mono_ns=t + RISK_NS, t_origin_ns=t, source="risk",
                confidence=0.5, bearing="ahead", alert_id=alert_id, hazard="unknown_obstacle",
                priority="high", track_id=None,
                reason=Reason(code="system_fault", detail="stub: camera heartbeat lost")))

        out.sort(key=lambda m: m.t_mono_ns)
        for msg in out:
            yield msg.to_json()


if __name__ == "__main__":
    # Write the stream to a file:  python -m datalogger.stub_stream > data/stub.jsonl
    for line in stub_stream():
        print(line)
