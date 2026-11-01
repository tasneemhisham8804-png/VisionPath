"""
Landmark memory demo: walk the same corridor twice, the second walk recognises the landmarks.

    python -m landmarks.demo                   # simple backend
    python -m landmarks.demo --backend chroma  # ChromaDB, after pip install chromadb

Fingerprints are fake (landmarks/fake.py) until the Hailo model exists. The second walk
also has a new pothole, which must not be recognised.
"""

from __future__ import annotations

import argparse
import tempfile

from landmarks.fake import LANDMARKS, FakeFingerprints, fake_walk
from landmarks.memory import LandmarkMemory, Selector, landmark_place
from landmarks.store import open_store

NEW_ON_WALK_2 = ("pothole", (0.5, 48.0))


def walk(memory: LandmarkMemory, session: int, landmarks, fingerprints: FakeFingerprints) -> tuple[list, set]:
    """One walk. Returns the memory's answers and the tracks the selector left out."""
    selector = Selector()
    answers, ignored = [], set()
    for n, (track, user) in enumerate(fake_walk(landmarks)):
        if not selector.wants(track):
            if track.hazard.value not in selector.classes:
                ignored.add(track.hazard.value)
            continue
        place = landmark_place(user, 0.0, track.distance_m, track.bearing_deg)
        fp = fingerprints.of(track.hazard.value, place, sighting=session * 100_000 + n)
        answers.append(memory.remember(fp, track.hazard.value, place, session=session,
                                       t=round(track.t_mono_ns / 1e9, 2), track_id=track.track_id))
    return answers, ignored


def per_landmark(answers: list) -> dict:
    """hazard -> (fingerprints stored, recognised, best similarity)."""
    out: dict[str, list] = {}
    for a in answers:
        row = out.setdefault(a["hazard"], [0, False, None])
        row[0] += 1
        row[1] = row[1] or a["seen_before"]
        if a["similarity"] is not None:
            row[2] = max(row[2] or 0.0, a["similarity"])
    return {k: tuple(v) for k, v in out.items()}


def run(backend: str = "simple", folder=None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = open_store(folder or tmp, backend)
        try:
            memory, fingerprints = LandmarkMemory(store), FakeFingerprints()
            first, _ = walk(memory, 1, LANDMARKS, fingerprints)
            second, ignored = walk(memory, 2, LANDMARKS + [NEW_ON_WALK_2], fingerprints)
            return {"walk1": per_landmark(first), "walk2": per_landmark(second), "ignored": ignored,
                    "stored": store.count(), "backend": store.backend}
        finally:
            store.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Walk the same corridor twice with the landmark memory.")
    ap.add_argument("--backend", default="simple", choices=["simple", "chroma"])
    args = ap.parse_args(argv)
    r = run(args.backend)

    print("Landmark memory demo (fake fingerprints until the Hailo model)\n")
    stored1 = sum(v[0] for v in r["walk1"].values())
    print(f"Walk 1: {stored1} fingerprints stored for {len(r['walk1'])} landmarks. "
          "The memory was empty, so nothing is recognised.\n")
    print("Walk 2:")
    print(f"  {'landmark':<10}{'fingerprints':>13}  {'recognised':<12}{'similarity':>10}")
    places = dict(LANDMARKS + [NEW_ON_WALK_2])
    for hazard, (count, seen, sim) in r["walk2"].items():
        label = "yes" if seen else ("no (new)" if hazard == NEW_ON_WALK_2[0] else "NO")
        print(f"  {hazard:<10}{count:>13}  {label:<12}{('-' if sim is None else f'{sim:.2f}'):>10}"
              f"   at {places[hazard]}")
    for hazard in sorted(r["ignored"]):
        print(f"  {hazard:<10}  ignored: it moves, so it is not a landmark")
    print(f"\nThe memory now holds {r['stored']} fingerprints ({r['backend']} backend).")
    ok = all(v[1] for k, v in r["walk2"].items() if k != NEW_ON_WALK_2[0]) \
        and not r["walk2"].get(NEW_ON_WALK_2[0], (0, False))[1]
    print("RESULT: PASS" if ok else "RESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
