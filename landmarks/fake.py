"""
Stand-ins until the real parts exist: fake fingerprints and a fake walk.

FakeFingerprints: the same object (same class, same 2 m cell) always gives almost the
same fingerprint, plus a little noise per sighting, like a real model would. Different
objects give unrelated fingerprints. It is replaced by the Hailo model's output.

fake_walk: a 60 m corridor walked at 1.2 m/s, 10 frames per second, with fixed
landmarks and one passing pedestrian. Yields (Track message, user place) per sighting.
"""

from __future__ import annotations

import math
import random

from contracts import Track, bearing_from_deg

LANDMARKS = [
    ("pole", (1.0, 15.0)),
    ("step", (0.0, 30.0)),
    ("overhead", (-0.8, 42.0)),
    ("curb", (1.2, 55.0)),
]


class FakeFingerprints:
    def __init__(self, dim: int = 64, noise: float = 0.25, cell_m: float = 2.0):
        self.dim, self.noise, self.cell_m = dim, noise, cell_m

    def of(self, hazard: str, place, sighting: int = 0) -> list[float]:
        key = f"{hazard}:{round(place[0] / self.cell_m)}:{round(place[1] / self.cell_m)}"
        base = random.Random(key)
        jitter = random.Random(f"{key}:{sighting}")
        v = [base.gauss(0, 1) + self.noise * jitter.gauss(0, 1) for _ in range(self.dim)]
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]


def fake_walk(landmarks=LANDMARKS, length_m: float = 60.0, speed_mps: float = 1.2, fps: int = 10,
              view_m: float = 10.0, pedestrian: bool = True, start_ns: int = 1_000_000_000_000,
              place_noise_m: float = 0.0, seed: int = 0):
    """Walk north along x = 0. Every frame, each object 0.5 to view_m ahead becomes a Track message."""
    rng = random.Random(seed)
    ages: dict[int, int] = {}
    seq = 0
    objects = [(i + 1, hazard, xy) for i, (hazard, xy) in enumerate(landmarks)]
    frames = int(length_m / speed_mps * fps)
    for f in range(frames):
        t_ns = start_ns + int(f * 1e9 / fps)
        user = (rng.gauss(0, place_noise_m) if place_noise_m else 0.0, f * speed_mps / fps)
        current = list(objects)
        if pedestrian:                                        # someone walking towards the user from 20 m
            current.append((99, "pedestrian", (0.5, 20.0 - f * 0.08)))
        for track_id, hazard, (x, y) in current:
            dx, dy = x - user[0], y - user[1]
            distance = math.hypot(dx, dy)
            if not (dy > 0.5 and distance <= view_m):
                continue
            ages[track_id] = ages.get(track_id, 0) + 1
            bearing = math.degrees(math.atan2(dx, dy))
            track = Track(seq=seq, t_mono_ns=t_ns, t_origin_ns=t_ns - 60_000_000, source="tracker",
                          confidence=0.9, bearing=bearing_from_deg(bearing), track_id=track_id, hazard=hazard,
                          distance_m=round(distance, 3), bearing_deg=round(bearing, 2),
                          closing_speed_mps=speed_mps, ttc_s=round(distance / speed_mps, 2),
                          ego_compensated=False, age_frames=ages[track_id])
            seq += 1
            yield track, user
