"""Black box viewer (Week 3). Run with: python -m unittest discover -s tests -v

The Rerun SDK is not needed: a small stand-in records what would be sent to the viewer,
once with the newer Rerun API and once with the older one.
"""
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from datalogger.blackbox import BlackBox
from datalogger.db import open_db, start_session
from datalogger.stub_stream import stub_stream
from replay_viewer import timeline as TL
from replay_viewer import view

START_NS = 1_000_000_000_000


class FakeRerun:
    """Records calls. new_api=True: set_time + Scalars (Rerun 0.23+); False: set_time_seconds + Scalar."""

    def __init__(self, new_api=True):
        self.calls = []
        self.times = []
        self.init_args = None
        self.saved_to = None
        if new_api:
            self.set_time = lambda name, duration: self.times.append((name, duration))
            self.Scalars = lambda values: ("scalars", values)
            self.TextLogLevel = types.SimpleNamespace(ERROR="E", WARN="W", INFO="I")
        else:
            self.set_time_seconds = lambda name, seconds: self.times.append((name, seconds))
            self.Scalar = lambda value: ("scalar", value)

    def init(self, app_id, spawn=False):
        self.init_args = (app_id, spawn)

    def save(self, path):
        self.saved_to = path

    def send_blueprint(self, blueprint):
        self.blueprint = blueprint

    def log(self, path, item):
        self.calls.append((path, item))

    @staticmethod
    def Boxes2D(mins, sizes, labels):
        return ("boxes", mins, sizes, labels)

    @staticmethod
    def Points2D(positions, labels, radii):
        return ("points", positions, labels)

    @staticmethod
    def TextLog(text, level):
        return ("text", text, level)

    @staticmethod
    def Clear(recursive):
        return ("clear", recursive)


def fake_blueprint_module():
    """Stand-in for rerun.blueprint (Rerun 0.18+ style: panels take state=)."""
    m = types.ModuleType("rerun.blueprint")

    class Box:
        def __init__(self, *children, **kw):
            self.children, self.kw = children, kw

    class Panel:
        def __init__(self, state):
            self.state = state

    for name in ("Blueprint", "Vertical", "Horizontal", "Spatial2DView", "TextLogView", "TimeSeriesView"):
        setattr(m, name, type(name, (Box,), {}))
    for name in ("BlueprintPanel", "SelectionPanel", "TimePanel"):
        setattr(m, name, type(name, (Panel,), {}))
    return m


def near_miss_snapshot(folder: Path) -> Path:
    """A real 30 s snapshot written by the black box from the 60 s stub scenario."""
    conn = open_db(folder / "vp.db")
    try:
        box = BlackBox(folder / "blackbox", conn=conn, session_id=start_session(conn, user_id=1,
                                                                                 monotonic_start_ns=START_NS))
        fired = [p for p in (box.add(line) for line in stub_stream(duration_s=60, start_ns=START_NS)) if p]
    finally:
        conn.close()
    return fired[0]


class Geometry(unittest.TestCase):
    def test_top_view_ahead_is_up_and_right_is_right(self):
        x, y = TL.top_view_xy(2.0, 0.0)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, -2.0)
        x, y = TL.top_view_xy(3.0, 90.0)
        self.assertAlmostEqual(x, 3.0)
        self.assertAlmostEqual(y, 0.0, places=9)
        self.assertLess(TL.top_view_xy(1.0, -30.0)[0], 0)          # negative bearing = left

    def test_trigger_from_file_name(self):
        self.assertEqual(TL.trigger_of("s3_20261031T160632637248Z_near_miss.jsonl"), "near_miss")
        self.assertEqual(TL.trigger_of(Path("x/s1_20261031T1Z_fault.jsonl")), "fault")
        self.assertIsNone(TL.trigger_of("recording.jsonl"))


class Timeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.snapshot = near_miss_snapshot(Path(self.tmp.name))
        self.tl = TL.load(self.snapshot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_whole_snapshot_on_one_timeline(self):
        self.assertEqual(self.tl.skipped, 0)
        self.assertGreater(self.tl.messages, 1000)
        self.assertGreaterEqual(self.tl.duration_s, 29.0)
        self.assertLessEqual(self.tl.duration_s, 30.0)
        times = [e.t for e in self.tl.events]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[0], 0.0)

    def test_every_view_gets_data(self):
        roots = {e.path.split("/")[0] for e in self.tl.events}
        self.assertEqual(roots, {"camera", "top_view", "distance", "time_to_collision", "latency_ms", "alerts"})

    def test_target_lines_on_the_charts(self):
        target = [e for e in self.tl.events if e.path == "latency_ms/target"]
        near = [e for e in self.tl.events if e.path == "time_to_collision/near_miss"]
        self.assertEqual([e.data["value"] for e in target], [300.0, 300.0])
        self.assertEqual([e.data["value"] for e in near], [1.0, 1.0])
        self.assertEqual((target[0].t, target[-1].t), (0.0, self.tl.duration_s))

    def test_camera_and_ultrasonic_share_the_distance_chart(self):
        names = {e.path for e in self.tl.events if e.path.startswith("distance/")}
        self.assertTrue(any(n.startswith("distance/track_") for n in names))
        self.assertIn("distance/ultrasonic_us-front", names)

    def test_alert_output_and_trigger_in_the_log(self):
        texts = [e.data for e in self.tl.events if e.kind == "text"]
        self.assertTrue(texts[0]["text"].startswith("Warning (high): pedestrian, about to collide"))
        self.assertEqual(texts[0]["level"], "WARN")
        self.assertTrue(texts[1]["text"].startswith("Vibration sent"))
        self.assertTrue(texts[-1]["text"].startswith("Black box saved (near miss)"))
        self.assertEqual(texts[-1]["level"], "ERROR")

    def test_a_frame_with_nothing_detected_clears_the_old_boxes(self):
        kinds = {e.kind for e in self.tl.events if e.path == "camera/detections"}
        self.assertEqual(kinds, {"boxes", "clear"})

    def test_boxes_are_in_pixels_of_the_frame(self):
        boxes = [e for e in self.tl.events if e.path == "camera/detections" and e.kind == "boxes"]
        last = boxes[-1].data
        x, y = last["mins"][0]
        w, h = last["sizes"][0]
        self.assertTrue(0 <= x and x + w <= 640 and 0 <= y and y + h <= 480)

    def test_closing_obstacle_reaches_the_near_miss(self):
        ttc = [e.data["value"] for e in self.tl.events if e.path.startswith("time_to_collision/track_")]
        self.assertLess(min(ttc), 1.0)


class BadInput(unittest.TestCase):
    def test_unknown_and_invalid_lines_are_skipped_and_counted(self):
        lines = list(stub_stream(duration_s=2, start_ns=START_NS, obstacle_from_s=999))
        heartbeat = json.dumps({"msg_type": "heartbeat", "t_mono_ns": START_NS, "t_origin_ns": START_NS})
        tl = TL.build(lines + [heartbeat, "not json", ""])
        self.assertEqual(tl.skipped, 2)
        self.assertEqual(tl.messages, len(lines))

    def test_nothing_valid(self):
        tl = TL.build(["junk"])
        self.assertEqual((tl.events, tl.messages, tl.skipped), ([], 0, 1))


class SendToRerun(unittest.TestCase):
    def setUp(self):
        self.tl = TL.build(stub_stream(duration_s=60, start_ns=START_NS), trigger="manual")

    def test_newer_rerun_api(self):
        rr = FakeRerun(new_api=True)
        sent = view.log_events(rr, self.tl)
        self.assertEqual(sent, len(self.tl.events))
        self.assertEqual(len(rr.calls), sent)
        self.assertEqual(len(rr.times), sent)
        self.assertEqual(rr.times[0][0], "time")
        self.assertIn(("text", self.tl.events[-1].data["text"], "E"), [c[1] for c in rr.calls])
        self.assertIn("scalars", {c[1][0] for c in rr.calls})

    def test_older_rerun_api(self):
        rr = FakeRerun(new_api=False)
        view.log_events(rr, self.tl)
        self.assertIn("scalar", {c[1][0] for c in rr.calls})
        self.assertIn("ERROR", {c[1][2] for c in rr.calls if c[1][0] == "text"})


class Layout(unittest.TestCase):
    def setUp(self):
        self.saved = {k: sys.modules.get(k) for k in ("rerun", "rerun.blueprint")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_six_named_boxes_and_folded_panels(self):
        sys.modules["rerun"] = FakeRerun()
        sys.modules["rerun.blueprint"] = fake_blueprint_module()
        bp = view.layout(sys.modules["rerun"])
        top, bottom = bp.children[0].children
        names = [v.kw["name"] for v in top.children + bottom.children]
        self.assertEqual(names, ["Camera: what it saw", "From above (ahead is up)", "What happened",
                                 "Distance (m)", "Seconds to collision", "Speed of each stage (ms)"])
        origins = [v.kw["origin"] for v in top.children + bottom.children]
        self.assertEqual(origins, ["/camera", "/top_view", "/alerts", "/distance", "/time_to_collision",
                                   "/latency_ms"])
        self.assertEqual({p.state for p in bp.children[1:]}, {"collapsed"})

    def test_no_blueprint_api_means_rerun_s_own_layout(self):
        sys.modules["rerun"] = FakeRerun()
        sys.modules["rerun.blueprint"] = None          # import fails
        self.assertIsNone(view.layout(sys.modules["rerun"]))


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.fake = FakeRerun()
        self.saved_module = sys.modules.get("rerun")
        sys.modules["rerun"] = self.fake
        self.quiet = contextlib.ExitStack()
        self.quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def tearDown(self):
        self.quiet.close()
        if self.saved_module is None:
            sys.modules.pop("rerun", None)
        else:
            sys.modules["rerun"] = self.saved_module
        self.tmp.cleanup()

    def test_save_a_snapshot_to_a_rerun_file(self):
        snapshot = near_miss_snapshot(self.dir)
        out = str(self.dir / "out.rrd")
        self.assertEqual(view.main([str(snapshot), "--save", out]), 0)
        self.assertEqual(self.fake.saved_to, out)
        self.assertEqual(self.fake.init_args, (view.APP_ID, False))
        self.assertGreater(len(self.fake.calls), 1000)

    def test_opens_the_viewer_by_default(self):
        snapshot = near_miss_snapshot(self.dir)
        self.assertEqual(view.main([str(snapshot)]), 0)
        self.assertEqual(self.fake.init_args, (view.APP_ID, True))

    def test_sends_the_layout_when_rerun_has_one(self):
        saved = sys.modules.get("rerun.blueprint")
        sys.modules["rerun.blueprint"] = fake_blueprint_module()
        try:
            self.assertEqual(view.main([str(near_miss_snapshot(self.dir))]), 0)
        finally:
            if saved is None:
                sys.modules.pop("rerun.blueprint", None)
            else:
                sys.modules["rerun.blueprint"] = saved
        self.assertEqual(type(self.fake.blueprint).__name__, "Blueprint")

    def test_newest_snapshot_and_missing_file(self):
        self.assertIsNone(view.newest_snapshot(self.dir))
        snapshot = near_miss_snapshot(self.dir)
        self.assertEqual(view.newest_snapshot(snapshot.parent), snapshot)
        self.assertEqual(view.main([str(self.dir / "nope.jsonl")]), 2)


if __name__ == "__main__":
    unittest.main()
