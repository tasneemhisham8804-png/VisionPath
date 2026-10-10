"""Black box (Week 2). Run with: python -m unittest discover -s tests -v"""
import json
import tempfile
import unittest
from pathlib import Path

from contracts import from_json
from datalogger.blackbox import BlackBox, load_snapshot
from datalogger.db import open_db, start_session
from datalogger.stub_stream import stub_stream

START_NS = 1_000_000_000_000


class BlackBoxTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "test.db")
        self.session = start_session(self.conn, user_id=1, monotonic_start_ns=START_NS)
        self.box = BlackBox(self.dir / "blackbox", conn=self.conn, session_id=self.session)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def feed(self, lines):
        fired = []
        for line in lines:
            path = self.box.add(line)
            if path:
                fired.append(path)
        return fired


class StubStream(unittest.TestCase):
    def test_every_stub_line_follows_the_contract(self):
        lines = list(stub_stream(duration_s=60, start_ns=START_NS, fault_at_s=50))
        msgs = [from_json(line) for line in lines]          # raises if any line is invalid
        times = [m.t_mono_ns for m in msgs]
        self.assertEqual(times, sorted(times))
        self.assertIn("track", {m.MSG_TYPE for m in msgs})


class RingBuffer(BlackBoxTestCase):
    def test_keeps_only_the_last_30_seconds(self):
        lines = list(stub_stream(duration_s=60, start_ns=START_NS, obstacle_from_s=999))  # no triggers
        self.feed(lines)
        self.assertLessEqual(self.box.buffered_seconds(), 30.0)
        self.assertGreater(self.box.buffered_seconds(), 29.0)
        self.assertLess(len(self.box), len(lines))

    def test_bad_lines_are_ignored_not_fatal(self):
        self.box.add("not json")
        self.box.add('{"msg_type": "track"}')               # no t_mono_ns
        self.assertEqual(self.box.ignored_lines, 2)
        self.assertEqual(len(self.box), 0)

    def test_trigger_on_empty_buffer_writes_nothing(self):
        self.assertIsNone(self.box.trigger("manual"))

    def test_any_message_type_is_recorded(self):
        """Types that are not in contract 1.0.0 yet (heartbeat, ego_motion) still go in the snapshot."""
        lines = list(stub_stream(duration_s=5, start_ns=START_NS, obstacle_from_s=999))
        heartbeat = json.dumps({"msg_type": "heartbeat", "schema_version": "1.1.0", "seq": 0,
                                "t_mono_ns": START_NS + 2_500_000_000, "t_origin_ns": START_NS + 2_500_000_000,
                                "source": "camera", "confidence": 1.0, "bearing": None},
                               separators=(",", ":"), sort_keys=True)
        self.feed(lines[:30] + [heartbeat] + lines[30:])
        path = self.box.trigger("manual")
        written = path.read_text(encoding="utf-8").splitlines()
        self.assertIn(heartbeat, written)                    # kept exactly as it was on the bus
        self.assertEqual(len(written), len(lines) + 1)

    def test_snapshot_sorted_by_t_mono_ns_even_if_bus_order_differs(self):
        lines = list(stub_stream(duration_s=5, start_ns=START_NS, obstacle_from_s=999))
        shuffled = lines[:]
        for i in range(0, len(shuffled) - 1, 2):             # swap neighbours: out of order arrival
            shuffled[i], shuffled[i + 1] = shuffled[i + 1], shuffled[i]
        self.feed(shuffled)
        written = self.box.trigger("manual").read_text(encoding="utf-8").splitlines()
        times = [json.loads(l)["t_mono_ns"] for l in written]
        self.assertEqual(times, sorted(times))
        self.assertEqual(sorted(written), sorted(lines))

    def test_late_message_older_than_window_is_left_out(self):
        self.feed(stub_stream(duration_s=40, start_ns=START_NS, obstacle_from_s=999))
        stale = next(stub_stream(duration_s=1, start_ns=START_NS))  # t = 0 s, now 40 s old
        self.box.add(stale)
        written = self.box.trigger("manual").read_text(encoding="utf-8").splitlines()
        self.assertNotIn(stale, written)
        self.assertLessEqual(self.box.buffered_seconds(), 30.0)


class Triggers(BlackBoxTestCase):
    def test_near_miss_writes_replayable_30_second_snapshot(self):
        """Week 2 done-when: a trigger writes a replayable 30 second snapshot."""
        fired = self.feed(stub_stream(duration_s=60, start_ns=START_NS))
        self.assertEqual(len(fired), 1)                      # cooldown: one snapshot, not one per frame
        self.assertTrue(fired[0].name.endswith("_near_miss.jsonl"))

        msgs = load_snapshot(fired[0])                       # every line parses with the contract
        times = [m.t_mono_ns for m in msgs]
        self.assertEqual(times, sorted(times))
        span_s = (times[-1] - times[0]) / 1e9
        self.assertGreaterEqual(span_s, 29.0)
        self.assertLessEqual(span_s, 30.0)
        last_track = [m for m in msgs if m.MSG_TYPE == "track"][-1]
        self.assertLess(last_track.ttc_s, 1.0)               # the snapshot ends at the near miss

    def test_snapshot_row_recorded(self):
        fired = self.feed(stub_stream(duration_s=60, start_ns=START_NS))
        row = self.conn.execute("SELECT * FROM blackbox_snapshots").fetchone()
        self.assertEqual(row["session_id"], self.session)
        self.assertEqual(row["trigger_type"], "near_miss")
        self.assertEqual(row["file_path"], str(fired[0]))
        self.assertEqual(row["duration_s"], 30)
        self.assertGreater(row["file_size_kb"], 0)
        self.assertTrue(row["ts"].endswith("Z"))

    def test_system_fault_alert_triggers_fault_snapshot(self):
        fired = self.feed(stub_stream(duration_s=40, start_ns=START_NS, obstacle_from_s=999, fault_at_s=35))
        self.assertEqual([p.name.rsplit("_", 1)[-1] for p in fired], ["fault.jsonl"])

    def test_manual_and_fall_triggers(self):
        self.feed(stub_stream(duration_s=5, start_ns=START_NS, obstacle_from_s=999))
        self.assertIsNotNone(self.box.trigger("manual"))
        self.assertIsNotNone(self.box.trigger("fall"))
        with self.assertRaises(ValueError):
            self.box.trigger("explosion")

    def test_cooldown_blocks_repeated_trigger(self):
        self.feed(stub_stream(duration_s=5, start_ns=START_NS, obstacle_from_s=999))
        self.assertIsNotNone(self.box.trigger("manual"))
        self.assertIsNone(self.box.trigger("manual"))      # same kind within 10 s

    def test_no_temporary_file_left_behind(self):
        self.feed(stub_stream(duration_s=60, start_ns=START_NS))
        self.assertEqual(list((self.dir / "blackbox").glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
