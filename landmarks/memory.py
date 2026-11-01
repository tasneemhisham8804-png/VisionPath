"""
Landmark memory: which tracks get a fingerprint, and "have we seen this here before?".

    selector = Selector()                       # once per second, steady, fixed objects only
    memory = LandmarkMemory(open_store(folder))
    if selector.wants(track):                   # track = contract Track message
        place = landmark_place(user_xy, heading_deg, track.distance_m, track.bearing_deg)
        result = memory.remember(fingerprint, track.hazard.value, place, session=4, t=31.2)
        result["seen_before"]                   # True if a fingerprint from an earlier walk matches here

The fingerprint comes from a small model on the Hailo (to be chosen with Talal).
Until then landmarks/fake.py makes stand-in fingerprints, so the rest can be built and tested.
"""

from __future__ import annotations

import math

# Fixed parts of the street: worth remembering. Moving things (pedestrian, motorbike,
# tuktuk, vehicle) and unknown obstacles are not landmarks.
STATIC_CLASSES = frozenset({"curb", "step", "pole", "overhead", "manhole", "pothole", "hanging_cable", "drop"})


class Selector:
    """
    Decides which tracks get a fingerprint:
      * a fixed object (STATIC_CLASSES),
      * followed for at least min_age_frames (a steady track, not a flicker),
      * not moving by itself, once the user's own motion is removed (ego_compensated),
      * at most once every interval_s per track (about once a second, not every frame).
    """

    def __init__(self, min_age_frames: int = 15, interval_s: float = 1.0, static_speed_mps: float = 0.3,
                 classes=STATIC_CLASSES):
        self.min_age_frames = min_age_frames
        self.interval_s = interval_s
        self.static_speed_mps = static_speed_mps
        self.classes = frozenset(classes)
        self._last: dict[int, float] = {}

    def wants(self, track) -> bool:
        if track.hazard.value not in self.classes or track.age_frames < self.min_age_frames:
            return False
        # Without ego motion (not in contract 1.0.0 yet), a fixed pole also "closes in" at walking
        # speed, so the speed check only applies once the track is ego compensated.
        if track.ego_compensated and abs(track.closing_speed_mps) > self.static_speed_mps:
            return False
        t = track.t_mono_ns / 1e9
        last = self._last.get(track.track_id)
        if last is not None and t - last < self.interval_s:
            return False
        self._last[track.track_id] = t
        return True


def landmark_place(user_xy, heading_deg: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    """Where the object is, in metres: the user's place plus distance along heading + bearing (0 = north = +y)."""
    rad = math.radians(heading_deg + bearing_deg)
    return (round(user_xy[0] + distance_m * math.sin(rad), 2), round(user_xy[1] + distance_m * math.cos(rad), 2))


class LandmarkMemory:
    """Stores every fingerprint and says whether an earlier walk saw the same object near the same place."""

    def __init__(self, store, match: float = 0.90, radius_m: float = 3.0):
        self.store = store
        self.match = match             # similarity needed to call it the same object
        self.radius_m = radius_m       # how far from the stored place it may be

    def remember(self, fingerprint, hazard: str, place, session=None, t=None, track_id=None) -> dict:
        best = None
        for _, similarity, info in self.store.nearest(fingerprint, k=5, place=place, radius_m=self.radius_m):
            if session is not None and info.get("session") == session:
                continue               # the same walk: that is the same sighting again, not a memory
            if info.get("hazard") != hazard:
                continue
            best = (similarity, info)
            break
        self.store.add(fingerprint, {"hazard": hazard, "place_x": place[0], "place_y": place[1],
                                     "session": session, "t": t, "track_id": track_id})
        return {
            "hazard": hazard,
            "place": place,
            "track_id": track_id,
            "similarity": None if best is None else round(best[0], 3),
            "seen_before": best is not None and best[0] >= self.match,
        }
