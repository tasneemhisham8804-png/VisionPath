"""End-to-end check (Week 8). Run with: python -m unittest discover -s tests -v"""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from datalogger.config import datalogger_settings, load_config
from datalogger.run import Service
from datalogger.stub_stream import stub_stream
from qa import e2e


def recorded_session(folder: Path, capture: str) -> Path:
    """A session like the Pi records: the stub stream, with the camera marked as `capture`."""
    settings = datalogger_settings(load_config())
    settings.update(db_path=folder / "visionpath.db", blackbox_dir=folder / "blackbox")
    service = Service(settings, source_kind="live", monotonic_start_ns=1_000_000_000_000)
    for line in stub_stream(duration_s=60, fps=10):
        service.handle(line.replace('"capture":"replay"', f'"capture":"{capture}"'))
    service.close()
    return settings["db_path"]


class Laptop(unittest.TestCase):
    def test_every_step_passes(self):
        steps = e2e.run_laptop(load_config())
        self.assertEqual(len(steps.rows), 9)
        for number, name, ok, detail in steps.rows:
            with self.subTest(step=f"{number} {name}"):
                self.assertTrue(ok, detail)

    def test_the_command(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = e2e.main([])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("RESULT: PASS (9 of 9 steps)", out.getvalue())


class RealSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_camera_passes(self):
        db = recorded_session(self.dir, "oakd")
        steps = e2e.run_db(db, None, load_config())
        self.assertTrue(steps.passed, steps.rows)

    def test_stub_data_is_not_the_real_sensors(self):
        db = recorded_session(self.dir, "replay")
        steps = e2e.run_db(db, None, load_config())
        failed = [name for _, name, ok, _ in steps.rows if not ok]
        self.assertEqual(failed, ["Black box on the real sensors"])

    def test_database_copied_to_the_laptop_finds_the_snapshots_by_name(self):
        db = recorded_session(self.dir / "pi", "oakd")
        laptop = self.dir / "laptop"
        laptop.mkdir()
        shutil.copy(db, laptop / "visionpath.db")
        shutil.copytree(self.dir / "pi" / "blackbox", laptop / "blackbox")
        shutil.rmtree(self.dir / "pi")
        steps = e2e.run_db(laptop / "visionpath.db", None, load_config())
        self.assertTrue(steps.passed, steps.rows)

    def test_problems(self):
        db = recorded_session(self.dir, "oakd")
        with self.assertRaises(FileNotFoundError):
            e2e.run_db(db, 99, load_config())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(e2e.main(["--db", str(self.dir / "none.db")]), 2)
        self.assertIn("no database", out.getvalue())


if __name__ == "__main__":
    unittest.main()
