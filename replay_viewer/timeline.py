"""
Turn a black box snapshot (or any contract .jsonl recording) into timeline events.

No Rerun here, so CI can test it. view.py sends the events to the Rerun viewer.

Every event has:
    t      seconds since the first message in the file
    kind   "boxes" | "clear" | "scalar" | "point" | "text"
    path   where it shows in the viewer, e.g. "distance/ultrasonic_us-front"
    data   what to draw (a dict, see each kind below)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from contracts import ContractError, from_json

DEFAULT_FRAME = (640, 480)          # used until a camera_frame gives the real size
ALERT_LEVEL = {"critical": "ERROR", "high": "WARN"}   # others: INFO
TRIGGERS = ("near_miss", "fault", "fall", "manual")


@dataclass
class Event:
    t: float
    kind: str
    path: str
    data: dict = field(default_factory=dict)


@dataclass
class Timeline:
    events: list[Event]
    messages: int = 0
    skipped: int = 0                # unknown type, newer version or invalid: counted, not drawn
    duration_s: float = 0.0
    counts: dict = field(default_factory=dict)


def top_view_xy(distance_m: float, bearing_deg: float) -> tuple[float, float]:
    """Seen from above, user at (0, 0): x to the right, y down the screen, so ahead is up."""
    rad = math.radians(bearing_deg)
    return distance_m * math.sin(rad), -distance_m * math.cos(rad)


def trigger_of(path) -> str | None:
    """'s3_20261031T160632Z_near_miss.jsonl' -> 'near_miss'."""
    stem = Path(path).stem
    for name in TRIGGERS:
        if stem.endswith("_" + name):
            return name
    return None


REASON_WORDS = {
    "ttc_below_threshold": "about to collide",
    "close_range": "very close",
    "corridor_blocked": "path blocked",
    "drop_ahead": "drop ahead",
    "overhead_hazard": "obstacle at head height",
    "low_confidence": "low confidence (rain or fog)",
    "system_fault": "system fault",
}


def build(lines, trigger: str | None = None, latency_target_ms: float = 300.0,
          near_miss_ttc_s: float = 1.0) -> Timeline:
    """Events in time order. Lines that are not valid contract messages are skipped and counted."""
    msgs, skipped = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            msgs.append(from_json(line))
        except (ContractError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            skipped += 1
    msgs.sort(key=lambda m: m.t_mono_ns)
    if not msgs:
        return Timeline(events=[], skipped=skipped)

    t0 = msgs[0].t_mono_ns
    end = (msgs[-1].t_mono_ns - t0) / 1e9
    width, height = DEFAULT_FRAME
    frame_label = ["camera frame (no image is saved)"]
    events = [
        Event(0.0, "boxes", "camera/frame", {"mins": [[0, 0]], "sizes": [[width, height]], "labels": frame_label}),
        Event(0.0, "point", "top_view/user", {"xy": [0.0, 0.0], "label": "user", "radius": 0.15}),
    ]
    for t in (0.0, end):                     # two points = a flat line across each chart
        events.append(Event(t, "scalar", "latency_ms/target", {"value": latency_target_ms}))
        events.append(Event(t, "scalar", "time_to_collision/near_miss", {"value": near_miss_ttc_s}))
    counts: dict[str, int] = {}

    for m in msgs:
        t = (m.t_mono_ns - t0) / 1e9
        kind = m.MSG_TYPE
        counts[kind] = counts.get(kind, 0) + 1

        latency_ms = (m.t_mono_ns - m.t_origin_ns) / 1e6
        if latency_ms > 0:
            events.append(Event(t, "scalar", f"latency_ms/{kind}", {"value": latency_ms}))

        if kind == "camera_frame":
            if (m.width, m.height) != (width, height):
                width, height = m.width, m.height
                events.append(Event(t, "boxes", "camera/frame", {
                    "mins": [[0, 0]], "sizes": [[width, height]], "labels": frame_label}))

        elif kind == "detection_set" and not m.objects:
            events.append(Event(t, "clear", "camera/detections"))      # nothing seen: remove old boxes

        elif kind == "detection_set":
            events.append(Event(t, "boxes", "camera/detections", {
                "mins": [[o.bbox.x1 * width, o.bbox.y1 * height] for o in m.objects],
                "sizes": [[(o.bbox.x2 - o.bbox.x1) * width, (o.bbox.y2 - o.bbox.y1) * height] for o in m.objects],
                "labels": [f"{o.hazard.value} {o.score:.2f}" for o in m.objects],
            }))

        elif kind == "ultrasonic_reading":
            if m.distance_m is not None:
                events.append(Event(t, "scalar", f"distance/ultrasonic_{m.sensor_id}", {"value": m.distance_m}))

        elif kind == "track":
            events.append(Event(t, "scalar", f"distance/track_{m.track_id}", {"value": m.distance_m}))
            if m.ttc_s is not None:
                events.append(Event(t, "scalar", f"time_to_collision/track_{m.track_id}", {"value": m.ttc_s}))
            events.append(Event(t, "point", f"top_view/track_{m.track_id}", {
                "xy": list(top_view_xy(m.distance_m, m.bearing_deg)),
                "label": f"{m.hazard.value} {m.distance_m:.1f} m",
                "radius": 0.12,
            }))

        elif kind == "risk_alert":
            r = m.reason
            facts = [f"{r.distance_m:.1f} m away" if r.distance_m is not None else "",
                     f"{r.ttc_s:.1f} s to collision" if r.ttc_s is not None else ""]
            facts = ", ".join(f for f in facts if f)
            events.append(Event(t, "text", "alerts", {
                "text": f"Warning ({m.priority.value}): {m.hazard.value}, "
                        f"{REASON_WORDS.get(r.code.value, r.code.value)}" + (f", {facts}" if facts else ""),
                "level": ALERT_LEVEL.get(m.priority.value, "INFO"),
            }))

        elif kind == "output_command":
            if m.channel.value == "haptic":
                text = f"Vibration sent ({m.haptic_pattern or 'pattern'})"
            else:
                text = f"Voice said: {m.voice_line or ''}"
            events.append(Event(t, "text", "alerts", {"text": text, "level": "INFO"}))

    if trigger:
        events.append(Event(end, "text", "alerts", {
            "text": f"Black box saved ({trigger.replace('_', ' ')}): these are the last {end:.0f} seconds",
            "level": "ERROR"}))
    events.sort(key=lambda e: e.t)
    return Timeline(events=events, messages=len(msgs), skipped=skipped, duration_s=end, counts=counts)


def load(path, latency_target_ms: float = 300.0, near_miss_ttc_s: float = 1.0) -> Timeline:
    with open(path, encoding="utf-8") as f:
        return build(f, trigger=trigger_of(path), latency_target_ms=latency_target_ms,
                     near_miss_ttc_s=near_miss_ttc_s)
