"""Wellbeing check (Week 7): the 11 test cases and the rules around them.
Run with: python -m unittest discover -s tests -v
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from datalogger.config import load_config
from datalogger.db import mono_to_utc, open_db, session_clock, start_session
from wellbeing import cases as wc
from wellbeing.check import Settings, Wellbeing, clock_from, in_window, quiet_window, save


class TheElevenCases(unittest.TestCase):
    def test_each_case(self):
        all_cases = wc.cases()
        self.assertEqual(len(all_cases), 11)
        for case in all_cases:
            with self.subTest(case=f"{case.number} {case.title}"):
                got, _ = wc.replay(case)
                for key, value in case.expect.items():
                    self.assertEqual(got[key], value, f"{key}: {got}")

    def test_the_command(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            code = wc.main(["--save", tmp])
            files = sorted(p.name for p in Path(tmp).iterdir())
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("11 of 11 cases pass", out.getvalue())
        self.assertEqual(len(files), 11)

    def test_with_the_config_file(self):
        self.assertEqual(Settings.from_config(load_config()), Settings())
        for case in wc.cases():
            with self.subTest(case=case.number):
                got, _ = wc.replay(case, load_config())
                self.assertTrue(all(got[k] == v for k, v in case.expect.items()), got)


class MoreRules(unittest.TestCase):
    def run_script(self, script, **settings):
        w = Wellbeing(Settings(**settings), clock=clock_from("14:00", wc.T0))
        for line in script.lines:
            w.feed(line)
        return w

    def test_long_press_is_the_emergency_button(self):
        w = self.run_script(wc.Script().still(15, 1).still(seconds=4).press("long").still(seconds=90))
        self.assertEqual(w.checks[0].outcome, "escalated")
        self.assertEqual(w.checks[0].ended_by, "emergency button")
        self.assertEqual(w.checks[0].notification_status, "not_needed")       # the emergency flow tells them
        self.assertFalse(any(a.kind == "notify_caregiver" for a in w.actions))
        self.assertEqual(len(w.checks), 1)

    def test_late_okay_after_the_caregiver_was_told(self):
        w = self.run_script(wc.Script().still(15, 1).still(seconds=70).say().still(seconds=5))
        kinds = [a.kind for a in w.actions]
        self.assertEqual(kinds, ["ask", "ask", "notify_caregiver", "tell_caregiver_okay", "thanks"])
        self.assertIsNotNone(w.checks[0].responded_ns)

    def test_no_new_question_after_escalation_until_the_user_moves(self):
        w = self.run_script(wc.Script().still(15, 1).still(20))
        self.assertEqual(len(w.checks), 1)
        w = self.run_script(wc.Script().still(15, 1).still(seconds=70).walk(5).still(15, 1))
        self.assertEqual(len(w.checks), 2)

    def test_motion_signal_lost(self):
        s = wc.Script().still(5)
        s.t += 20 * wc.SECOND                                        # 20 s with no motion message
        s.say("repeat")
        w = self.run_script(s)
        self.assertEqual([a.kind for a in w.actions], ["signal_lost"])
        w = self.run_script(wc.Script().still(5).still(15), motion_timeout_s=10)
        self.assertEqual(len(w.checks), 1)                           # a steady signal: asked as usual

    def test_one_try_only(self):
        w = self.run_script(wc.Script().still(15, 1).still(seconds=40), attempts=1)
        self.assertEqual((w.checks[0].outcome, w.checks[0].attempts), ("escalated", 1))

    def test_settings_are_checked(self):
        for bad in ({"attempts": 3}, {"attempts": 0}, {"interval_min": 0}, {"quiet_hours": "late"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Settings(**bad)

    def test_quiet_hours_cross_midnight(self):
        w = quiet_window("23:00-07:00")
        for hhmm, inside in (("23:30", True), ("03:00", True), ("07:00", False), ("14:00", False)):
            self.assertEqual(in_window(clock_from(hhmm, 0)(0), w), inside, hhmm)
        self.assertFalse(in_window(clock_from("12:00", 0)(0), None))


class Database(unittest.TestCase):
    def test_checks_go_in_the_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "t.db")
            sid = start_session(conn, user_id=1, monotonic_start_ns=wc.T0)
            started, mono_s = session_clock(conn, sid)
            to_utc = lambda t: mono_to_utc(started, mono_s, t)
            for number in (1, 3):
                _, w = wc.replay(wc.cases()[number - 1])
                save(conn, w.checks[0], sid, 1, to_utc)
            rows = conn.execute("SELECT outcome, attempts, interval_min, notification_status, emergency_event_id, "
                                "lat, stationary_since, started_at FROM wellbeing_checks ORDER BY check_id").fetchall()
            event = conn.execute("SELECT trigger_type, status, lat FROM emergency_events").fetchall()
            conn.close()
        self.assertEqual([tuple(r)[:5] for r in rows],
                         [("okay_voice", 1, 15, "not_needed", None), ("escalated", 2, 15, "queued", 1)])
        self.assertEqual([tuple(e) for e in event], [("wellbeing", "pending", wc.PLACE["lat"])])
        self.assertLess(rows[0]["stationary_since"], rows[0]["started_at"])


if __name__ == "__main__":
    unittest.main()
