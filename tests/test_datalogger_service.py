"""Config, ZeroMQ bus and the datalogger service. Run with: python -m unittest discover -s tests -v

Tests that read the config file need Python 3.11+ (tomllib); the ZeroMQ
test needs pyzmq (pip install pyzmq). Without them, those tests are skipped.
"""
import contextlib
import io
import json
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from contracts import from_json
from datalogger import config, run
from datalogger.bus import parse_frames, topic_of
from datalogger.stub_publisher import rebase
from datalogger.stub_stream import stub_stream

try:
    import zmq  # noqa: F401
    HAVE_ZMQ = True
except ImportError:
    HAVE_ZMQ = False
HAVE_TOML = config.tomllib is not None

START_NS = 1_000_000_000_000


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self) -> dict:
        return config.datalogger_settings({"datalogger": {
            "db_path": str(self.dir / "vp.db"), "blackbox_dir": str(self.dir / "blackbox"),
            "flush_interval_s": 0.1}})

    def write_config(self, bus: str) -> Path:
        path = self.dir / "test.toml"
        db, bb = (self.dir / "vp.db").as_posix(), (self.dir / "blackbox").as_posix()
        path.write_text(f"{bus}\n[targets]\nlatency_p95_ms = 300\n"
                        f"[datalogger]\ndb_path = \"{db}\"\nblackbox_dir = \"{bb}\"\n"
                        f"flush_interval_s = 0.1\n", encoding="utf-8")
        return path


class ConfigValues(unittest.TestCase):
    """No file reading: runs everywhere, CI included."""

    def test_sub_and_pub_endpoints(self):
        cfg = {"bus": {"sub_endpoint": "tcp://a:1", "pub_endpoint": "tcp://a:2"}}
        self.assertEqual(config.sub_endpoints(cfg), ["tcp://a:1"])
        self.assertEqual(config.pub_endpoint(cfg), "tcp://a:2")
        self.assertEqual(config.sub_endpoints({"bus": {"sub_endpoint": ["tcp://a:1", "tcp://b:1"]}}),
                         ["tcp://a:1", "tcp://b:1"])

    def test_missing_endpoint_is_an_error_not_a_default(self):
        for cfg in ({}, {"bus": {}}, {"bus": {"sub_endpoint": ""}}):
            with self.assertRaises(config.ConfigError):
                config.sub_endpoints(cfg)
            with self.assertRaises(config.ConfigError):
                config.pub_endpoint(cfg)

    def test_targets_and_rates(self):
        cfg = {"targets": {"latency_p95_ms": 300}, "rates": {"camera_hz": 30}}
        self.assertEqual((config.latency_target_ms(cfg), config.camera_hz(cfg)), (300.0, 30.0))
        self.assertEqual((config.latency_target_ms({}), config.camera_hz({})), (None, None))

    def test_relative_paths_are_from_the_repo_root(self):
        s = config.datalogger_settings({"datalogger": {"db_path": "data/x.db"}})
        self.assertEqual(s["db_path"], (config.REPO_ROOT / "data" / "x.db").resolve())

    def test_no_endpoint_hardcoded_in_the_code(self):
        """Tasneem: the port lives in the config file, not in the code."""
        for py in (config.REPO_ROOT / "datalogger").glob("*.py"):
            self.assertIsNone(re.search(r"tcp://[\w.]+:\d+", py.read_text(encoding="utf-8")), py.name)

    def test_missing_file_is_an_error(self):
        with self.assertRaises(config.ConfigError):
            config.load_config(config.REPO_ROOT / "no_such_config.toml")


@unittest.skipUnless(HAVE_TOML, "needs Python 3.11+ (tomllib)")
class ConfigFile(TempDir):
    def test_repo_config_file_is_complete(self):
        cfg = config.load_config(config.DEFAULT_CONFIG)
        self.assertTrue(config.sub_endpoints(cfg)[0].startswith("tcp://"))
        self.assertTrue(config.pub_endpoint(cfg).startswith("tcp://"))
        self.assertNotEqual(config.sub_endpoints(cfg)[0], config.pub_endpoint(cfg))   # one port per socket
        self.assertEqual(config.latency_target_ms(cfg), 300.0)
        self.assertIsNotNone(config.camera_hz(cfg))

    def test_bad_toml_is_a_config_error(self):
        path = self.dir / "bad.toml"
        path.write_text("[bus\nsub_endpoint = \n", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load_config(path)


class Frames(unittest.TestCase):
    LINE = '{"msg_type":"track","t_mono_ns":1}'

    def test_two_frames_topic_and_json(self):
        self.assertEqual(parse_frames([b"track", self.LINE.encode()]), ("track", self.LINE))

    def test_one_frame_topic_space_json(self):
        self.assertEqual(parse_frames([b"track " + self.LINE.encode()]), ("track", self.LINE))

    def test_one_frame_json_only(self):
        self.assertEqual(parse_frames([self.LINE.encode()]), (None, self.LINE))

    def test_topic_is_msg_type(self):
        self.assertEqual(topic_of(self.LINE), "track")
        self.assertEqual(topic_of("junk"), "")


class Replay(TempDir):
    def test_rebase_keeps_the_contract_format(self):
        lines = list(stub_stream(duration_s=2, start_ns=START_NS))
        moved = rebase(lines, 5_000_000_000_000)
        self.assertEqual(json.loads(moved[0])["t_mono_ns"], 5_000_000_000_000)
        for line in moved:
            self.assertEqual(from_json(line).to_json(), line)      # still exactly msg.to_json()

    def test_service_writes_db_rows_and_snapshot(self):
        service = run.Service(self.settings(), source_kind="replay", monotonic_start_ns=START_NS)
        for line in stub_stream(duration_s=60, start_ns=START_NS):
            service.handle(line)
        service.close()
        conn = sqlite3.connect(self.dir / "vp.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM obstacles").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT DISTINCT source FROM performance_metrics").fetchall(), [("replay",)])
        snap = conn.execute("SELECT trigger_type, obstacle_id, duration_s FROM blackbox_snapshots").fetchall()
        self.assertEqual(snap, [("near_miss", 1, 30)])
        self.assertIsNotNone(conn.execute("SELECT ended_at FROM sessions").fetchone()[0])
        conn.close()

    @unittest.skipUnless(HAVE_TOML, "needs Python 3.11+ (tomllib)")
    def test_run_from_file_prints_the_latency_report(self):
        recording = self.dir / "rec.jsonl"
        recording.write_text("\n".join(stub_stream(duration_s=60, start_ns=START_NS)) + "\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run.main(["--config", str(self.write_config('[bus]\nsub_endpoint = "tcp://127.0.0.1:7000"')),
                           "--from-file", str(recording)])
        self.assertEqual(rc, 0)
        self.assertIn("-> PASS", out.getvalue())
        self.assertIn("near_miss 1", out.getvalue())


@unittest.skipUnless(HAVE_ZMQ, "pyzmq not installed")
class ZeroMQ(TempDir):
    def test_through_the_bus_process_every_message_arrives_and_the_near_miss_fires(self):
        from datalogger.bus import BusProxy, Publisher, Subscriber
        from datalogger.stub_publisher import publish

        bus = BusProxy("tcp://127.0.0.1:*", "tcp://127.0.0.1:*")     # any free ports, for the test only
        running = threading.Event()
        running.set()

        def bus_loop():
            while running.is_set():
                bus.step(50)

        bus_thread = threading.Thread(target=bus_loop)
        bus_thread.start()
        try:
            service = run.Service(self.settings(), monotonic_start_ns=START_NS)
            sub = Subscriber([bus.sub_endpoint], recv_timeout_ms=100)   # connects, like every module
            pub = Publisher(bus.pub_endpoint)                            # connects, like every module
            time.sleep(0.7)                                              # slow joiner: let subscriptions reach pub

            lines = list(stub_stream(duration_s=60, start_ns=START_NS))
            sender = threading.Thread(target=publish, args=(pub, lines), kwargs={"speed": 200})
            sender.start()
            triggers, deadline = [], time.monotonic() + 30
            while service.messages < len(lines) and time.monotonic() < deadline:
                line = sub.recv()
                if line is not None:
                    path = service.handle(line)
                    if path:
                        triggers.append(path)
            sender.join()
            sub.close()
            pub.close()
            service.close()
        finally:
            running.clear()
            bus_thread.join()
            bus.close()

        self.assertEqual(service.messages, len(lines))       # nothing lost on the bus
        self.assertEqual(bus.forwarded, len(lines))
        self.assertEqual(len(triggers), 1)
        self.assertTrue(triggers[0].name.endswith("_near_miss.jsonl"))
        lines_out = triggers[0].read_text(encoding="utf-8").splitlines()
        times = [m.t_mono_ns for m in (from_json(l) for l in lines_out)]
        self.assertEqual(times, sorted(times))
        self.assertGreaterEqual((times[-1] - times[0]) / 1e9, 29.0)


if __name__ == "__main__":
    unittest.main()
