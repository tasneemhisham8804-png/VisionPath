"""
Health watch: notices when a sensor or module stops, the "Detected by" column of the
degraded modes table (TDR section 7).

    watch = Health()
    for line in bus_lines:
        for event in watch.feed(line):       # Event(t_s, name, what), e.g. (20.5, "camera", "down")
            ...
    watch.tick(now_ns)                       # live use: notice silence even when nothing else arrives

Watched sources (the ones that send all the time):
    camera      camera_frame       down after CAMERA_TIMEOUT_S without a frame (0.5 s in the table)
    detector    detection_set      down after MODULE_TIMEOUT_S without a result
    ultrasonic  ultrasonic_reading down when silent, or frozen: the same reading (same seq and
                                   t_origin_ns) sent again and again, for ULTRASONIC_TIMEOUT_S
Other modules (fusion, tracker, risk, output) only send when there is something to say,
so their silence means nothing until they send heartbeats (not in contract 1.0.0 yet).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

CAMERA_TIMEOUT_S = 0.5          # TDR degraded modes table: "No frames for 0.5 s"
ULTRASONIC_TIMEOUT_S = 0.5      # proposed, same as the camera
MODULE_TIMEOUT_S = 1.0          # proposed

WATCHED = {"camera_frame": "camera", "detection_set": "detector", "ultrasonic_reading": "ultrasonic"}


@dataclass
class Event:
    t_s: float                  # seconds since the first message seen
    name: str                   # camera | detector | ultrasonic
    what: str                   # down | back
    reason: str = ""            # silent | frozen


class Health:
    def __init__(self, camera_timeout_s: float = CAMERA_TIMEOUT_S,
                 ultrasonic_timeout_s: float = ULTRASONIC_TIMEOUT_S, module_timeout_s: float = MODULE_TIMEOUT_S):
        self.timeout_ns = {"camera": camera_timeout_s * 1e9, "detector": module_timeout_s * 1e9,
                           "ultrasonic": ultrasonic_timeout_s * 1e9}
        self.t0 = None
        self.last_ns: dict[str, int] = {}          # last real message per watched source
        self.down: dict[str, str] = {}             # name -> reason, while down
        self.frozen_since: dict[str, int] = {}     # ultrasonic sensor -> time the repeats started
        self.last_reading: dict[str, tuple] = {}   # ultrasonic sensor -> (seq, t_origin_ns)
        self.events: list[Event] = []

    def _event(self, t_ns: int, name: str, what: str, reason: str = "") -> Event:
        e = Event(round((t_ns - self.t0) / 1e9, 3), name, what, reason)
        self.events.append(e)
        return e

    def tick(self, now_ns: int) -> list[Event]:
        """Notice silence: a watched source with no new message for longer than its timeout."""
        out = []
        for name, last in self.last_ns.items():
            if name not in self.down and now_ns - last > self.timeout_ns[name]:
                # repeats of an old ultrasonic reading are arriving: the sensor is frozen, not silent
                reason = "frozen" if name == "ultrasonic" and self.frozen_since else "silent"
                self.down[name] = reason
                out.append(self._event(int(last + self.timeout_ns[name]), name, "down", reason))
        return out

    def feed(self, line: str) -> list[Event]:
        try:
            d = json.loads(line)
            t = int(d["t_mono_ns"])
        except (ValueError, KeyError, TypeError):
            return []                               # not a message: the logger counts those
        if self.t0 is None:
            self.t0 = t
        out = self.tick(t)
        name = WATCHED.get(d.get("msg_type"))
        if name is None:
            return out

        if name == "ultrasonic":
            sensor = d.get("sensor_id", "?")
            reading = (d.get("seq"), d.get("t_origin_ns"))
            if reading == self.last_reading.get(sensor):          # the same reading again: frozen?
                since = self.frozen_since.setdefault(sensor, t)
                if name not in self.down and t - since >= self.timeout_ns[name]:
                    self.down[name] = "frozen"
                    out.append(self._event(t, name, "down", "frozen"))
                return out                                        # a repeat is not a sign of life
            self.last_reading[sensor] = reading
            self.frozen_since.pop(sensor, None)

        self.last_ns[name] = t
        if name in self.down:
            self.down.pop(name)
            out.append(self._event(t, name, "back"))
        return out


def watch(lines) -> list[Event]:
    """All health events of a recording, in order."""
    h = Health()
    for line in lines:
        h.feed(line)
    return h.events
