"""Fault injection (Week 5): the degraded modes table, on every push. Run with: python -m unittest discover -s tests -v

Rows whose system behaviour belongs to modules that are not in the repository yet are
skipped, and the skip message says who they wait for.
"""
import contextlib
import io
import json
import unittest

from qa import faults, health


class Injectors(unittest.TestCase):
    def setUp(self):
        self.lines = faults.stub()

    def count(self, lines, **match):
        return sum(1 for line in lines if all(json.loads(line).get(k) == v for k, v in match.items()))

    def test_camera_down_removes_frames_and_detections_only_in_the_window(self):
        out = faults.camera_down(self.lines, 20, 25)
        self.assertEqual(self.count(out, msg_type="camera_frame"), 600 - 50)
        self.assertEqual(self.count(out, msg_type="detection_set"), 600 - 50)
        self.assertEqual(self.count(out, msg_type="ultrasonic_reading"), 600)

    def test_frozen_ultrasonic_repeats_one_reading(self):
        out = faults.ultrasonic_frozen(self.lines, 30, 40)
        seqs = [json.loads(l)["seq"] for l in out if json.loads(l)["msg_type"] == "ultrasonic_reading"]
        self.assertEqual(len(seqs), 600)
        self.assertEqual(len(set(seqs)), 600 - 100)       # the 100 readings in the window repeat the last one before it

    def test_other_injectors(self):
        self.assertEqual(self.count(faults.drop_frames(self.lines, 3), msg_type="camera_frame"), 400)
        self.assertEqual(self.count(faults.kill_module(self.lines, "detector", 10), source="detector"), 100)
        self.assertEqual(sum(l.startswith("{broken") for l in faults.corrupt(self.lines, 50)), len(self.lines) // 50)
        self.assertGreater(len(faults.duplicate_and_swap(self.lines)), len(self.lines))
        slow = faults.delay_stage(self.lines, "output_command", 300)
        d = [json.loads(l) for l in slow if json.loads(l)["msg_type"] == "output_command"][0]
        self.assertEqual(d["t_mono_ns"] - d["t_origin_ns"], 375_000_000)


class HealthWatch(unittest.TestCase):
    def test_no_false_alarm_on_a_clean_session(self):
        self.assertEqual(health.watch(faults.stub(fps=10)), [])
        self.assertEqual(health.watch(faults.stub(fps=30)), [])

    def test_tick_notices_silence_when_nothing_arrives(self):
        h = health.Health()
        for line in faults.stub()[:50]:
            h.feed(line)
        last = json.loads(faults.stub()[49])["t_mono_ns"]
        events = h.tick(last + 2_000_000_000)
        self.assertEqual({e.name for e in events}, {"camera", "detector", "ultrasonic"})

    def test_broken_lines_are_ignored(self):
        self.assertEqual(health.Health().feed("{broken"), [])


class DegradedModes(unittest.TestCase):
    """One test per row of the degraded modes table (TDR section 7)."""

    def run_check(self, check):
        ok, detail = check()
        if ok is None:
            self.skipTest(detail)
        self.assertTrue(ok, detail)

    def test_camera_down(self):
        self.run_check(faults.check_camera_down)

    def test_camera_down_fault_alert_saves_a_black_box_file(self):
        self.run_check(faults.check_fault_alert_snapshot)

    def test_ultrasonic_silent(self):
        self.run_check(faults.check_ultrasonic_silent)

    def test_ultrasonic_frozen(self):
        self.run_check(faults.check_ultrasonic_frozen)

    def test_rows_that_wait_for_other_modules(self):
        for failure, _, _, _, checks, waits_for in faults.DEGRADED_MODES:
            with self.subTest(failure=failure):
                if not checks:
                    self.skipTest(f"{failure}: waits for {waits_for}")


class Robustness(unittest.TestCase):
    def run_check(self, check):
        ok, detail = check()
        if ok is None:
            self.skipTest(detail)
        self.assertTrue(ok, detail)

    def test_detector_crash(self):
        self.run_check(faults.check_detector_killed)

    def test_dropped_frames(self):
        self.run_check(faults.check_dropped_frames)

    def test_broken_messages(self):
        self.run_check(faults.check_broken_messages)

    def test_repeated_and_out_of_order_messages(self):
        self.run_check(faults.check_duplicates_and_order)

    def test_slower_stage_is_caught_by_the_regression_gate(self):
        self.run_check(faults.check_slow_stage_caught)

    def test_bus_restart(self):
        self.run_check(faults.check_bus_restart)


class Table(unittest.TestCase):
    def test_the_table_command_passes(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = faults.main([])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("RESULT: PASS", out.getvalue())
        self.assertEqual(len(faults.DEGRADED_MODES), 9)


if __name__ == "__main__":
    unittest.main()
