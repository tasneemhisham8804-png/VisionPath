"""Landmark memory (Week 4). Run with: python -m unittest discover -s tests -v

ChromaDB is not needed: a small stand-in with the same calls checks the chroma backend.
"""
import math
import tempfile
import types
import unittest

from contracts import Track
from landmarks import bench, demo
from landmarks.fake import FakeFingerprints, fake_walk
from landmarks.memory import LandmarkMemory, Selector, landmark_place
from landmarks.store import ChromaStore, SimpleStore, cosine

START_NS = 1_000_000_000_000


def track(t_s=0.0, track_id=1, hazard="pole", age=20, closing=1.2, ego=False, distance=5.0, bearing=0.0):
    return Track(seq=0, t_mono_ns=START_NS + int(t_s * 1e9), t_origin_ns=START_NS + int(t_s * 1e9), source="tracker",
                 confidence=0.9, bearing="ahead", track_id=track_id, hazard=hazard, distance_m=distance,
                 bearing_deg=bearing, closing_speed_mps=closing, ttc_s=None, ego_compensated=ego, age_frames=age)


def fake_chromadb():
    """Same calls as chromadb: PersistentClient, get_or_create_collection, add, count, query."""

    class Collection:
        def __init__(self):
            self.rows = []

        def add(self, ids, embeddings, metadatas):
            self.rows += list(zip(ids, embeddings, metadatas))

        def count(self):
            return len(self.rows)

        def query(self, query_embeddings, n_results):
            q = query_embeddings[0]
            ranked = sorted(self.rows, key=lambda r: 1 - cosine(q, r[1]))[:n_results]
            return {"ids": [[r[0] for r in ranked]], "distances": [[1 - cosine(q, r[1]) for r in ranked]],
                    "metadatas": [[r[2] for r in ranked]]}

    class Client:
        def __init__(self, path):
            self.path, self.col = path, Collection()

        def get_or_create_collection(self, name, metadata=None):
            self.metadata = metadata
            return self.col

    return types.SimpleNamespace(PersistentClient=Client)


class StoreTests:
    """Run on both backends."""

    def make(self, folder):
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = self.make(self.tmp.name)
        self.fp = FakeFingerprints(dim=32)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_add_count_and_nearest(self):
        a = self.fp.of("pole", (1, 15))
        self.store.add(a, {"hazard": "pole", "place_x": 1.0, "place_y": 15.0, "session": 1})
        self.store.add(self.fp.of("step", (0, 30)), {"hazard": "step", "place_x": 0.0, "place_y": 30.0, "session": 1})
        self.assertEqual(self.store.count(), 2)
        best = self.store.nearest(self.fp.of("pole", (1, 15), sighting=7), k=1)[0]
        self.assertEqual(best[2]["hazard"], "pole")
        self.assertGreater(best[1], 0.9)

    def test_place_filter(self):
        self.store.add(self.fp.of("pole", (1, 15)), {"hazard": "pole", "place_x": 1.0, "place_y": 15.0})
        q = self.fp.of("pole", (1, 15))
        self.assertEqual(len(self.store.nearest(q, place=(1.0, 16.0), radius_m=3)), 1)
        self.assertEqual(self.store.nearest(q, place=(1.0, 40.0), radius_m=3), [])

    def test_empty_store(self):
        self.assertEqual(self.store.nearest(self.fp.of("pole", (0, 0))), [])


class SimpleStoreTest(StoreTests, unittest.TestCase):
    def make(self, folder):
        return SimpleStore(folder)

    def test_kept_on_disk(self):
        self.store.add(self.fp.of("curb", (1, 55)), {"hazard": "curb", "place_x": 1.2, "place_y": 55.0})
        self.store.close()
        self.store = SimpleStore(self.tmp.name)
        self.assertEqual(self.store.count(), 1)


class ChromaStoreTest(StoreTests, unittest.TestCase):
    def make(self, folder):
        return ChromaStore(folder, chromadb_module=fake_chromadb())

    def test_cosine_space(self):
        self.assertEqual(self.store.client.metadata, {"hnsw:space": "cosine"})


class SelectorTest(unittest.TestCase):
    def test_fixed_objects_only(self):
        s = Selector()
        self.assertTrue(s.wants(track(hazard="pole")))
        self.assertFalse(s.wants(track(hazard="pedestrian", track_id=2)))
        self.assertFalse(s.wants(track(hazard="unknown_obstacle", track_id=3)))

    def test_steady_tracks_only(self):
        self.assertFalse(Selector(min_age_frames=15).wants(track(age=5)))

    def test_about_once_a_second(self):
        s = Selector(interval_s=1.0)
        picks = [s.wants(track(t_s=i / 10)) for i in range(25)]          # 2.5 s at 10 fps
        self.assertEqual(sum(picks), 3)

    def test_moving_object_once_ego_motion_is_removed(self):
        s = Selector()
        self.assertTrue(s.wants(track(closing=1.2, ego=False)))           # no ego motion yet: cannot tell
        self.assertFalse(s.wants(track(track_id=2, closing=1.2, ego=True)))
        self.assertTrue(s.wants(track(track_id=3, closing=0.1, ego=True)))


class Geometry(unittest.TestCase):
    def test_landmark_place(self):
        self.assertEqual(landmark_place((0, 10), 0, 5, 0), (0.0, 15.0))         # straight ahead, facing north
        x, y = landmark_place((0, 0), 0, 2, 90)                                 # to the right
        self.assertAlmostEqual(x, 2.0)
        self.assertAlmostEqual(y, 0.0)
        x, y = landmark_place((0, 0), 90, 3, 0)                                 # facing east
        self.assertAlmostEqual(x, 3.0)


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SimpleStore(self.tmp.name)
        self.memory = LandmarkMemory(self.store)
        self.fp = FakeFingerprints()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_recognised_on_a_later_walk_not_the_same_walk(self):
        first = self.memory.remember(self.fp.of("pole", (1, 15), 1), "pole", (1.0, 15.0), session=1)
        again = self.memory.remember(self.fp.of("pole", (1, 15), 2), "pole", (1.0, 15.1), session=1)
        later = self.memory.remember(self.fp.of("pole", (1, 15), 3), "pole", (1.1, 15.0), session=2)
        self.assertFalse(first["seen_before"])
        self.assertFalse(again["seen_before"])                    # same walk: not a memory
        self.assertTrue(later["seen_before"])
        self.assertGreaterEqual(later["similarity"], 0.9)
        self.assertEqual(self.store.count(), 3)

    def test_other_object_or_other_place_is_new(self):
        self.memory.remember(self.fp.of("pole", (1, 15)), "pole", (1.0, 15.0), session=1)
        self.assertFalse(self.memory.remember(self.fp.of("step", (1, 15)), "step", (1.0, 15.0), session=2)["seen_before"])
        self.assertFalse(self.memory.remember(self.fp.of("pole", (1, 40)), "pole", (1.0, 40.0), session=2)["seen_before"])


class Demo(unittest.TestCase):
    def test_second_walk_recognises_every_landmark_but_not_the_new_one(self):
        r = demo.run("simple")
        self.assertEqual(set(r["walk1"]), {"pole", "step", "overhead", "curb"})
        self.assertFalse(any(seen for _, seen, _ in r["walk1"].values()))
        for hazard in ("pole", "step", "overhead", "curb"):
            self.assertTrue(r["walk2"][hazard][1], hazard)
        self.assertFalse(r["walk2"]["pothole"][1])
        self.assertIn("pedestrian", r["ignored"])

    def test_about_one_fingerprint_per_second_per_landmark(self):
        r = demo.run("simple")
        for count, _, _ in r["walk1"].values():
            self.assertTrue(5 <= count <= 9, count)                 # about 8 s in view at 1.2 m/s, first 1.5 s skipped

    def test_the_fake_walk_follows_the_contract(self):
        tracks = [t for t, _ in fake_walk()]
        self.assertGreater(len(tracks), 100)
        self.assertTrue(all(t.distance_m <= 10 for t in tracks))


class Bench(unittest.TestCase):
    def test_small_bench(self):
        r = bench.bench("simple", n=50, dim=16, queries=5)
        self.assertEqual(r["n"], 50)
        self.assertGreater(r["size_mb"], 0)
        self.assertTrue(math.isfinite(r["query_p95_ms"]))


if __name__ == "__main__":
    unittest.main()
