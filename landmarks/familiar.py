"""
Fewer repeated alerts on a familiar route (Week 6).

On a route the user walks every day, the same pole should not be announced in full every
time. When the landmark memory says a fixed object was seen at the same place on an
earlier walk, its first alert in this walk is played in full (voice and vibration). Later
alerts for that landmark in this walk are vibration only.

Never quieter, always voice and vibration:
  * priority critical
  * closer than close_m (1.5 m)
  * time to collision below min_ttc_s (2 s)
  * anything that moves (pedestrian, motorbike, tuktuk, vehicle) or an unknown obstacle
  * anything new: no match from an earlier walk
  * alerts with no track (system faults, ultrasonic only)

    familiar = FamiliarAlerts.from_config(cfg)
    familiar.see(track)                        # every track message (latest distance and time to collision)
    familiar.note(memory.remember(...))        # every landmark memory answer
    decision = familiar.decide(risk_alert)     # Decision: full=True (voice + vibration) or False (vibration only)
    if familiar.keep(output_command): play it  # False only for the voice of a quieter alert

The settings are in config/visionpath.toml, section [familiar].
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from contracts import from_dict
from landmarks.memory import STATIC_CLASSES

CLOSE_M = 1.5               # closer than this: always in full
MIN_TTC_S = 2.0             # less time to collision than this: always in full
SAME_LANDMARK_M = 3.0       # two matches of the same class closer than this are the same landmark


@dataclass
class Decision:
    alert_id: int
    hazard: str
    priority: str
    full: bool                  # True = voice and vibration, False = vibration only
    why: str                    # one of WHY below

    @property
    def safety_rule(self) -> bool:
        """True when the alert was kept in full because it is urgent."""
        return self.why in ("critical", "close", "about to hit")


WHY = {
    "critical": "critical alert: always in full",
    "close": "closer than the close distance: always in full",
    "about to hit": "time to collision is short: always in full",
    "no track": "no object behind it (fault or ultrasonic only): always in full",
    "moves": "it moves, so it is never familiar",
    "new": "not seen on an earlier walk",
    "first this walk": "familiar, but the first alert for it on this walk",
    "familiar": "familiar and already announced on this walk: vibration only",
}


def settings(cfg: dict | None) -> dict:
    """The [familiar] section of config/visionpath.toml, with the defaults above."""
    s = (cfg or {}).get("familiar", {})
    return {"close_m": float(s.get("close_m", CLOSE_M)),
            "min_ttc_s": float(s.get("min_ttc_s", MIN_TTC_S)),
            "same_landmark_m": float(s.get("same_landmark_m", SAME_LANDMARK_M))}


class FamiliarAlerts:
    """Decides, alert by alert, whether to play it in full or as vibration only."""

    def __init__(self, close_m: float = CLOSE_M, min_ttc_s: float = MIN_TTC_S,
                 same_landmark_m: float = SAME_LANDMARK_M):
        self.close_m = close_m
        self.min_ttc_s = min_ttc_s
        self.same_landmark_m = same_landmark_m
        self.landmark_of: dict[int, tuple] = {}     # track_id -> (hazard, stored place) once recognised
        self.announced: list[tuple] = []            # landmarks already played in full on this walk
        self.last_track: dict[int, tuple] = {}      # track_id -> (distance_m, ttc_s)
        self.quiet_alerts: set[int] = set()         # alert ids played as vibration only
        self.decisions: list[Decision] = []

    @classmethod
    def from_config(cls, cfg: dict | None) -> "FamiliarAlerts":
        return cls(**settings(cfg))

    # -- inputs ---------------------------------------------------------------------------
    def see(self, track) -> None:
        self.last_track[track.track_id] = (track.distance_m, track.ttc_s)

    def note(self, answer: dict) -> None:
        """An answer of LandmarkMemory.remember(). Once a track is recognised it stays recognised."""
        if answer.get("seen_before") and answer.get("track_id") is not None:
            place = answer.get("matched_place") or answer.get("place")
            self.landmark_of[answer["track_id"]] = (answer["hazard"], tuple(place))

    def feed(self, line: str):
        """Replay or bus use: one JSON line. Returns a Decision for a risk_alert, else None."""
        try:
            d = json.loads(line)
        except ValueError:
            return None
        kind = d.get("msg_type")
        if kind == "landmark_match":
            self.note({"seen_before": True, "track_id": d.get("track_id"), "hazard": d.get("hazard"),
                       "matched_place": (d.get("landmark_x_m"), d.get("landmark_y_m"))})
            return None
        if kind in ("track", "risk_alert"):
            msg = from_dict(d)
            if kind == "track":
                self.see(msg)
                return None
            return self.decide(msg)
        return None

    # -- the rule -------------------------------------------------------------------------
    def _why(self, alert) -> str:
        hazard = alert.hazard.value
        if alert.track_id is None:
            return "no track"
        if alert.priority.value == "critical":
            return "critical"
        distance, ttc = self._distance_and_ttc(alert)
        if distance is not None and distance < self.close_m:
            return "close"
        if ttc is not None and ttc < self.min_ttc_s:
            return "about to hit"
        if hazard not in STATIC_CLASSES:
            return "moves"
        landmark = self.landmark_of.get(alert.track_id)
        if landmark is None:
            return "new"
        if any(self._same(landmark, done) for done in self.announced):
            return "familiar"
        self.announced.append(landmark)
        return "first this walk"

    def _distance_and_ttc(self, alert):
        """The smaller of what the alert says and what the latest track says."""
        tr_d, tr_ttc = self.last_track.get(alert.track_id, (None, None))
        values = lambda *xs: min([x for x in xs if x is not None], default=None)
        return values(alert.reason.distance_m, tr_d), values(alert.reason.ttc_s, tr_ttc)

    def _same(self, a, b) -> bool:
        return a[0] == b[0] and math.dist(a[1], b[1]) <= self.same_landmark_m

    def decide(self, alert) -> Decision:
        why = self._why(alert)
        full = why != "familiar"
        if not full:
            self.quiet_alerts.add(alert.alert_id)
        d = Decision(alert.alert_id, alert.hazard.value, alert.priority.value, full, why)
        self.decisions.append(d)
        return d

    def keep(self, command) -> bool:
        """False only for the voice part of an alert that is vibration only. Vibration is always kept."""
        return not (command.channel.value == "voice" and command.alert_id in self.quiet_alerts)

    def new_walk(self) -> None:
        """Call at the start of every walk: each landmark gets one full alert per walk."""
        self.announced.clear()
        self.landmark_of.clear()
        self.last_track.clear()
        self.quiet_alerts.clear()
