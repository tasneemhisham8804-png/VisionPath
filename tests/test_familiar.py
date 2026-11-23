"""Familiar route (Week 6): fewer repeated alerts, and the landmark_match message for Mariam.
Run with: python -m unittest discover -s tests -v
"""
import json
import pathlib
import tempfile
import unittest

from contracts import ContractError, OutputCommand, Reason, RiskAlert, Track
from datalogger.config import load_config
from landmarks import familiar_demo
from landmarks.fake import FakeFingerprints
from landmarks.familiar import FamiliarAlerts, settings
from landmarks.memory import LandmarkMemory, landmark_place
from landmarks.messages import LandmarkMatch, landmark_match, parse, where_is_the_user
from landmarks.store import SimpleStore

START_NS = 1_000_000_000_000
SAMPLE = pathlib.Path(__file__).resolve().parents[1] / "landmarks" / "samples" / "landmark_match.json"


def alert(alert_id=1, track_id=1, hazard="pole", priority="medium", distance=5.0, ttc=4.0):
    return RiskAlert(seq=alert_id, t_mono_ns=START_NS, t_origin_ns=START_NS, source="risk", confidence=0.9,
                     bearing="ahead", alert_id=alert_id, hazard=hazard, priority=priority, track_id=track_id,
                     reason=Reason(code="corridor_blocked", ttc_s=ttc, distance_m=distance))


def voice(alert_id):
    return OutputCommand(seq=0, t_mono_ns=START_NS, t_origin_ns=START_NS, source="output", confidence=0.9,
                         bearing=None, command_id=0, channel="voice", priority="medium", interrupt=False,
                         alert_id=alert_id, voice_line="pole_ahead", language="ar")


def haptic(alert_id):
    return OutputCommand(seq=0, t_mono_ns=START_NS, t_origin_ns=START_NS, source="output", confidence=0.9,
                         bearing=None, command_id=0, channel="haptic", priority="medium", interrupt=False,
                         alert_id=alert_id, haptic_pattern="obstacle_ahead", intensity=0.8)


def track(track_id=1, hazard="pole", distance=5.0, bearing=0.0, ttc=4.0):
    return Track(seq=0, t_mono_ns=START_NS, t_origin_ns=START_NS, source="tracker", confidence=0.9,
                 bearing="ahead", track_id=track_id, hazard=hazard, distance_m=distance, bearing_deg=bearing,
                 closing_speed_mps=1.2, ttc_s=ttc, ego_compensated=False, age_frames=20)


def seen(track_id=1, hazard="pole", place=(1.0, 15.0)):
    return {"seen_before": True, "track_id": track_id, "hazard": hazard, "matched_place": place,
            "similarity": 0.95, "matched_session": 1, "place": place}


class Rule(unittest.TestCase):
    def setUp(self):
        self.f = FamiliarAlerts()

    def familiar_pole(self, track_id=1):
        self.f.note(seen(track_id))
        self.assertEqual(self.f.decide(alert(1, track_id)).why, "first this walk")

    def test_first_alert_in_full_then_vibration_only(self):
        self.familiar_pole()
        d = self.f.decide(alert(2))
        self.assertFalse(d.full)
        self.assertEqual(d.why, "familiar")

    def test_never_quieter_when_urgent(self):
        self.familiar_pole()
        self.assertEqual(self.f.decide(alert(2, priority="critical")).why, "critical")
        self.assertEqual(self.f.decide(alert(3, distance=1.2)).why, "close")
        self.assertEqual(self.f.decide(alert(4, ttc=1.5)).why, "about to hit")
        self.assertTrue(all(d.full for d in self.f.decisions))

    def test_latest_track_counts_when_closer_than_the_alert_says(self):
        self.familiar_pole()
        self.f.see(track(distance=1.0, ttc=0.8))
        self.assertEqual(self.f.decide(alert(2, distance=5.0, ttc=4.0)).why, "close")

    def test_never_quieter_for_moving_new_or_no_track(self):
        self.f.note(seen(7, hazard="pedestrian"))
        for i in range(2):
            self.assertEqual(self.f.decide(alert(10 + i, 7, hazard="pedestrian")).why, "moves")
        for i in range(2):
            self.assertEqual(self.f.decide(alert(20 + i, 8)).why, "new")
        self.assertEqual(self.f.decide(alert(30, None, hazard="unknown_obstacle")).why, "no track")
        self.assertTrue(all(d.full for d in self.f.decisions))

    def test_same_landmark_under_a_new_track_id_is_still_familiar(self):
        self.familiar_pole(track_id=1)
        self.f.note(seen(track_id=5, place=(1.4, 15.8)))        # the tracker lost it and found it again
        self.assertEqual(self.f.decide(alert(2, 5)).why, "familiar")

    def test_another_landmark_of_the_same_class_gets_its_own_full_alert(self):
        self.familiar_pole(track_id=1)
        self.f.note(seen(track_id=2, place=(1.0, 40.0)))
        self.assertEqual(self.f.decide(alert(2, 2)).why, "first this walk")

    def test_every_walk_starts_again(self):
        self.familiar_pole()
        self.f.new_walk()
        self.familiar_pole()

    def test_only_the_voice_is_dropped(self):
        self.familiar_pole()
        self.f.decide(alert(2))
        self.assertTrue(self.f.keep(voice(1)))
        self.assertFalse(self.f.keep(voice(2)))
        self.assertTrue(self.f.keep(haptic(2)))

    def test_bus_lines(self):
        match = landmark_match(seen(), track(), seq=0, t_mono_ns=START_NS + 1)
        for line in (match.to_json(), track().to_json(), "{broken"):
            self.assertIsNone(self.f.feed(line))
        self.assertEqual(self.f.feed(alert(1).to_json()).why, "first this walk")
        self.assertFalse(self.f.feed(alert(2).to_json()).full)


class Settings(unittest.TestCase):
    def test_config_file_has_the_section(self):
        self.assertEqual(settings(load_config()), {"close_m": 1.5, "min_ttc_s": 2.0, "same_landmark_m": 3.0})

    def test_change_it_in_the_config(self):
        f = FamiliarAlerts.from_config({"familiar": {"close_m": 6}})
        f.note(seen())
        f.decide(alert(1))
        self.assertEqual(f.decide(alert(2, distance=5.0)).why, "close")


class Message(unittest.TestCase):
    def test_built_from_a_memory_answer(self):
        m = landmark_match(seen(), track(distance=8.2, bearing=7.0), seq=3, t_mono_ns=START_NS + 1)
        self.assertEqual((m.landmark_x_m, m.landmark_y_m, m.hazard.value), (1.0, 15.0, "pole"))
        self.assertEqual(m.t_origin_ns, START_NS)                 # latency is measured like every message
        self.assertEqual(parse(m.to_json()), m)

    def test_only_for_a_landmark_seen_before(self):
        with self.assertRaises(ContractError):
            landmark_match({"seen_before": False}, track(), seq=0, t_mono_ns=START_NS)

    def test_follows_the_envelope_rules(self):
        d = json.loads(landmark_match(seen(), track(), 0, START_NS).to_json())
        for key, bad in (("bearing", None), ("similarity", 1.5), ("distance_m", -1), ("schema_version", "2.0.0")):
            with self.subTest(key=key), self.assertRaises(ContractError):
                parse({**d, key: bad})

    def test_sample_file(self):
        m = parse(SAMPLE.read_text())
        self.assertIsInstance(m, LandmarkMatch)

    def test_loop_closure_gives_the_user_back(self):
        user, heading = (2.0, 10.0), 30.0
        for distance, bearing in ((5.0, 0.0), (3.0, -40.0), (7.5, 25.0)):
            landmark = landmark_place(user, heading, distance, bearing)
            m = landmark_match(seen(place=landmark), track(distance=distance, bearing=bearing), 0, START_NS)
            x, y = where_is_the_user(m, heading)
            self.assertAlmostEqual(x, user[0], delta=0.02)
            self.assertAlmostEqual(y, user[1], delta=0.02)


class MemoryAnswer(unittest.TestCase):
    def test_says_where_the_earlier_walk_stored_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SimpleStore(tmp)
            memory, fp = LandmarkMemory(store), FakeFingerprints()
            first = memory.remember(fp.of("pole", (1, 15), 1), "pole", (1.0, 15.0), session=1)
            later = memory.remember(fp.of("pole", (1, 15), 2), "pole", (1.6, 15.9), session=2)
            store.close()
        self.assertIsNone(first["matched_place"])
        self.assertEqual(later["matched_place"], (1.0, 15.0))
        self.assertEqual(later["matched_session"], 1)


class Demo(unittest.TestCase):
    def test_familiar_route(self):
        r = familiar_demo.run()
        self.assertTrue(familiar_demo.passed(r))
        self.assertLess(r["spoken2"], r["spoken1"])
        self.assertEqual(r["walk1"]["wrongly_quiet"] + r["walk2"]["wrongly_quiet"], [])
        self.assertGreater(len(r["walk2"]["matches"]), 0)
        self.assertEqual(r["walk1"]["matches"], [])               # nothing to match on a new route
        self.assertLess(r["drift_after_m"], 0.3)


if __name__ == "__main__":
    unittest.main()
