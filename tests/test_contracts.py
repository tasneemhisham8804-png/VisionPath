"""Contract tests. Run with: python -m unittest discover -s tests -v"""
import json
import pathlib
import unittest

from contracts import schema as c

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "contracts" / "samples"


def load(name):
    return json.loads((SAMPLES / name).read_text())


class GoldenSamples(unittest.TestCase):
    def test_every_sample_parses_and_round_trips(self):
        files = sorted(SAMPLES.glob("*.json"))
        self.assertTrue(files)
        for f in files:
            with self.subTest(sample=f.name):
                data = json.loads(f.read_text())
                self.assertEqual(c.from_dict(data).to_dict(), data)

    def test_every_message_type_has_a_sample(self):
        covered = {json.loads(f.read_text())["msg_type"] for f in SAMPLES.glob("*.json")}
        self.assertEqual(covered, set(c.message_types()))

    def test_chain_keeps_origin_and_latency_grows(self):
        lines = (SAMPLES / "chain_frame42.jsonl").read_text().splitlines()
        msgs = [c.from_json(line) for line in lines]
        frame = msgs[0]
        derived = [m for m in msgs if m.source not in ("camera", "ultrasonic")]
        for m in derived:
            self.assertEqual(m.t_origin_ns, frame.t_mono_ns)
        latencies = [m.latency_ms() for m in derived]
        self.assertEqual(latencies, sorted(latencies))


class EnvelopeRules(unittest.TestCase):
    def setUp(self):
        self.track = load("track.json")

    def bad(self, data):
        with self.assertRaises(c.ContractError):
            c.from_dict(data)

    def test_spatial_message_needs_bearing(self):
        self.track["bearing"] = None
        self.bad(self.track)

    def test_non_spatial_message_may_omit_bearing(self):
        c.from_dict(load("camera_frame.json"))

    def test_unknown_field_rejected(self):
        self.track["ttc"] = 1.0  # typo of ttc_s
        self.bad(self.track)

    def test_missing_field_rejected(self):
        del self.track["confidence"]
        self.bad(self.track)

    def test_confidence_range(self):
        self.track["confidence"] = 1.2
        self.bad(self.track)

    def test_origin_after_publish_rejected(self):
        self.track["t_origin_ns"] = self.track["t_mono_ns"] + 1
        self.bad(self.track)

    def test_bad_enum_rejected(self):
        self.track["bearing"] = "behind"
        self.bad(self.track)

    def test_wrong_types_rejected(self):
        self.track["track_id"] = "7"
        self.bad(self.track)

    def test_unknown_msg_type_rejected(self):
        self.track["msg_type"] = "trak"
        self.bad(self.track)


class Versioning(unittest.TestCase):
    def with_version(self, v):
        d = load("track.json")
        d["schema_version"] = v
        return d

    def test_other_major_rejected(self):
        major = int(c.SCHEMA_VERSION.split(".")[0])
        with self.assertRaises(c.ContractError):
            c.from_dict(self.with_version(f"{major + 1}.0.0"))

    def test_newer_minor_rejected(self):
        major, minor, _ = c.SCHEMA_VERSION.split(".")
        with self.assertRaises(c.ContractError):
            c.from_dict(self.with_version(f"{major}.{int(minor) + 1}.0"))

    def test_patch_difference_accepted(self):
        major, minor, _ = c.SCHEMA_VERSION.split(".")
        c.from_dict(self.with_version(f"{major}.{minor}.99"))


class StageRules(unittest.TestCase):
    def test_ttc_null_when_not_approaching(self):
        d = load("track.json")
        d["closing_speed_mps"] = -0.5
        with self.assertRaises(c.ContractError):
            c.from_dict(d)
        d["ttc_s"] = None
        c.from_dict(d)

    def test_haptic_command_needs_pattern(self):
        d = load("output_command_haptic.json")
        d["haptic_pattern"] = None
        with self.assertRaises(c.ContractError):
            c.from_dict(d)

    def test_voice_command_needs_language(self):
        d = load("output_command_voice.json")
        d["language"] = None
        with self.assertRaises(c.ContractError):
            c.from_dict(d)

    def test_bbox_must_be_normalised(self):
        d = load("detection_set.json")
        d["objects"][0]["bbox"]["x2"] = 640
        with self.assertRaises(c.ContractError):
            c.from_dict(d)

    def test_fusion_needs_a_source(self):
        d = load("fused_obstacle.json")
        d["sources"] = []
        with self.assertRaises(c.ContractError):
            c.from_dict(d)

    def test_bearing_rule(self):
        w = c.AHEAD_HALF_WIDTH_DEG
        self.assertEqual(c.bearing_from_deg(-w - 0.1), c.Bearing.LEFT)
        self.assertEqual(c.bearing_from_deg(-w), c.Bearing.AHEAD)
        self.assertEqual(c.bearing_from_deg(0), c.Bearing.AHEAD)
        self.assertEqual(c.bearing_from_deg(w), c.Bearing.AHEAD)
        self.assertEqual(c.bearing_from_deg(w + 0.1), c.Bearing.RIGHT)

    def test_priority_rank_order(self):
        ranked = sorted(c.Priority, key=lambda p: p.rank)
        self.assertEqual(ranked[0], c.Priority.CRITICAL)
        self.assertEqual(ranked[-1], c.Priority.INFO)


if __name__ == "__main__":
    unittest.main()
