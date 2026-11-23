"""The wellbeing check live (Week 8): the runner, without a bus. Run with: python -m unittest discover -s tests -v"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from datalogger.db import open_db, start_session
from wellbeing import live
from wellbeing.check import Settings, Wellbeing

SECOND = 1_000_000_000


class Typed(unittest.TestCase):
    def test_commands(self):
        line, _ = live.typed("ok", 5)
        self.assertEqual(json.loads(line), {"t_mono_ns": 5, "msg_type": "voice_intent", "intent": "im_okay",
                                            "source": "try"})
        self.assertEqual(json.loads(live.typed("press", 5)[0])["press"], "short")
        self.assertEqual(json.loads(live.typed("long", 5)[0])["press"], "long")
        self.assertEqual(json.loads(live.typed("say repeat", 5)[0])["intent"], "repeat")
        self.assertEqual(json.loads(live.typed("fall", 5)[0])["msg_type"], "fall_detected")
        self.assertEqual(json.loads(live.typed("alert", 5)[0])["priority"], "high")
        self.assertEqual(live.typed("walk", 5), (None, True))
        self.assertEqual(live.typed("still", 5), (None, False))
        self.assertEqual(live.typed("hello", 5), (None, None))


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.tmp.name) / "t.db")
        self.t = {"now": 10 * SECOND}
        self.session = start_session(self.conn, user_id=1, monotonic_start_ns=self.t["now"])
        self.printed = []
        w = Wellbeing(Settings(interval_min=0.5, answer_wait_s=5))
        self.r = live.Runner(w, self.conn, self.session, try_mode=True, out=self.printed.append,
                             clock=lambda: self.t["now"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_for(self, seconds, line=None):
        if line:
            self.r.step(line)
        for _ in range(int(seconds * 5)):
            self.t["now"] += SECOND // 5
            self.r.step(None)

    def rows(self):
        return [tuple(r) for r in self.conn.execute("SELECT outcome, attempts, notification_status FROM "
                                                    "wellbeing_checks WHERE session_id = ?", (self.session,))]

    def test_asks_then_okay_is_saved(self):
        self.run_for(31)
        self.assertTrue(any("ASK (try 1)" in p for p in self.printed))
        self.run_for(1, live.typed("ok", self.t["now"])[0])
        self.assertTrue(any("THANKS" in p for p in self.printed))
        self.assertEqual(self.rows(), [("okay_voice", 1, "not_needed")])

    def test_no_answer_tells_the_caregiver_and_is_saved(self):
        self.run_for(42)
        self.assertTrue(any("TELL THE CAREGIVER" in p for p in self.printed))
        self.assertEqual(self.rows(), [("escalated", 2, "queued")])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM emergency_events").fetchone()[0], 1)

    def test_walking_means_no_question(self):
        self.r.moving = True
        self.run_for(40)
        self.assertFalse(any("ASK" in p for p in self.printed))


class Session(unittest.TestCase):
    def test_joins_the_datalogger_session_or_starts_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v.db"
            conn = open_db(db)
            running = start_session(conn, user_id=1)
            conn.close()
            conn, session, own = live.open_session(db, 1)
            self.assertEqual((session, own), (running, False))
            conn.execute("UPDATE sessions SET ended_at = '2099-01-01T00:00:00.000Z'")
            conn.commit()
            conn.close()
            conn, session, own = live.open_session(db, 1)
            self.assertTrue(own)
            self.assertNotEqual(session, running)
            conn.close()


class TryCommand(unittest.TestCase):
    def test_try_mode_runs_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(live, "TRY_DB", Path(tmp) / "try.db"), \
                mock.patch("sys.stdin", io.StringIO("")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = live.main(["--try", "--interval-min", "0.01", "--duration", "1.5"])
            conn = open_db(Path(tmp) / "try.db")
            n = conn.execute("SELECT COUNT(*) FROM wellbeing_checks").fetchone()[0]
            conn.close()
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("ASK (try 1)", out.getvalue())
        self.assertEqual(n, 1)                         # the open question is saved when it stops


if __name__ == "__main__":
    unittest.main()
