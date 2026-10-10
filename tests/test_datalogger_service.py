"""Config, ZeroMQ bus and the datalogger service. Run with: python -m unittest discover -s tests -v

The ZeroMQ test needs pyzmq (pip install pyzmq); without it, it is skipped.
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

START_NS = 1_000_000_000_000


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, bus: str) -> Path:
        path = self.dir / "test.toml"
        db, bb = (self.dir / "vp.db").as_posix(), (self.dir / "blackbox").as_posix()
        path.write_text(f'{bus}\n[datalogger]\ndb_path = "{db}"\nblackbox_dir = "{bb}"\n'
                        f'flush_interval_s = 0.1\n', encoding="utf-8")
        return path


class Config(TempDir):
    def test_repo_config_file_has_a_bus_endpoint(self):
        endpoints = config.bus_endpoints(config.load_config(config.DEFAULT_CONFIG))
        self.assertTrue(endpoints and all(e.startswith("tcp://") for e in endpoints))

    def test_endpoint_or_endpoints(self):
        one = config.load_config(self.write_config('[bus]\nendpoint = "tcp://127.0.0.1:7000"'))
        self.assertEqual(config.bus_endpoints(one), ["tcp://127.0.0.1:7000"])
        two = config.load_config(self.write_config('[bus]\nendpoints = ["tcp://a:1", "tcp://b:2"]'))
        self.assertEqual(config.bus_endpoints(two), ["tcp://a:1", "tcp://b:2"])

    def test_missing_endpoint_or_file_is_an_error_not_a_default(self):
        with self.assertRaises(config.ConfigError):
            config.bus_endpoints(config.load_config(self.write_config("[bus]")))
        with self.assertRaises(config.ConfigError):
            config.load_config(self.dir / "nope.toml")

    def test_relative_paths_are_from_the_repo_root(self):
        s = config.datalogger_settings({"datalogger": {"db_path": "data/x.db"}})
        self.assertEqual(s["db_path"], (config.REPO_ROOT / "data" / "x.db").resolve())

    def test_no_endpoint_hardcoded_in_the_code(self):
        """Tasneem: the port lives in the config file, not in the code."""
        for py in (config.REPO_ROOT / "datalogger").glob("*.py"):
            self.assertIsNone(re.search(r"tcp://[\w.]+:\d+", py.read_text(encoding="utf-8")), py.name)


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

    def test_service_from_file_writes_db_rows_and_snapshot(self):
        recording = self.dir / "rec.jsonl"
        recording.write_text("\n".join(stub_stream(duration_s=60, start_ns=START_NS)) + "\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = run.main(["--config", str(self.write_config('[bus]\nendpoint = "tcp://127.0.0.1:7000"')),
                           "--from-file", str(recording)])
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.dir / "vp.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM obstacles").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT DISTINCT source FROM performance_metrics").fetchall(), [("replay",)])
        snap = conn.execute("SELECT trigger_type, obstacle_id, duration_s FROM blackbox_snapshots").fetchall()
        self.assertEqual(snap, [("near_miss", 1, 30)])
        self.assertIsNotNone(conn.execute("SELECT ended_at FROM sessions").fetchone()[0])
        conn.close()


@unittest.skipUnless(HAVE_ZMQ, "pyzmq not installed")
class ZeroMQ(TempDir):
    def test_service_receives_every_message_and_fires_the_near_miss(self):
        from datalogger.bus import Publisher, Subscriber
        from datalogger.stub_publisher import publish

        pub = Publisher("tcp://127.0.0.1:*")                 # any free port, for the test only
        settings = config.datalogger_settings(config.load_config(
            self.write_config(f'[bus]\nendpoint = "{pub.endpoint}"')))
        service = run.Service(settings, monotonic_start_ns=START_NS)
        sub = Subscriber([pub.endpoint], recv_timeout_ms=100)
        time.sleep(0.5)                                      # slow joiner: let the SUB connect

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

        self.assertEqual(service.messages, len(lines))       # nothing lost on the bus
        self.assertEqual(len(triggers), 1)
        self.assertTrue(triggers[0].name.endswith("_near_miss.jsonl"))
        lines_out = triggers[0].read_text(encoding="utf-8").splitlines()
        times = [m.t_mono_ns for m in (from_json(l) for l in lines_out)]
        self.assertEqual(times, sorted(times))
        self.assertGreaterEqual((times[-1] - times[0]) / 1e9, 29.0)


if __name__ == "__main__":
    unittest.main()
