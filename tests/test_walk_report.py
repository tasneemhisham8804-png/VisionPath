"""Walk test report (Week 6). Run with: python -m unittest discover -s tests -v"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from dashboard.metrics import read_walk_results
from datalogger.config import load_config
from qa import walk_report as wr

LOG_HEADER = ",".join(wr.LOG_COLUMNS) + "\n"
LABEL_HEADER = ",".join(wr.LABEL_COLUMNS) + "\n"


def run(argv):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = wr.main(argv)
    return code, out.getvalue()


class Times(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(wr.seconds("5:30"), 330)
        self.assertEqual(wr.seconds("0:07"), 7)
        self.assertEqual(wr.seconds("95"), 95)
        self.assertIsNone(wr.seconds(""))

    def test_excel_turns_5_30_into_05_30_00(self):
        self.assertEqual(wr.seconds("05:30:00"), 330)
        self.assertEqual(wr.seconds("0:05:30"), 330)

    def test_not_a_time(self):
        with self.assertRaises(wr.InputError):
            wr.seconds("five")


class Judge(unittest.TestCase):
    def j(self, **row):
        warnings = []
        return wr.judge({"result": "", "reached_2m_at": "", "alert_at": "", **row}, "line 2", warnings), warnings

    def test_worked_out_from_the_times(self):
        self.assertEqual(self.j(reached_2m_at="1:10", alert_at="1:05")[0], "Hit")
        self.assertEqual(self.j(reached_2m_at="1:10", alert_at="1:10")[0], "Hit")
        self.assertEqual(self.j(reached_2m_at="1:10", alert_at="1:12")[0], "Late")
        self.assertEqual(self.j(reached_2m_at="1:10")[0], "Miss")

    def test_written_result_wins_but_a_mismatch_is_reported(self):
        result, warnings = self.j(result="hit", reached_2m_at="1:10", alert_at="1:12")
        self.assertEqual(result, "Hit")
        self.assertIn("the times say Late", warnings[0])

    def test_cannot_tell(self):
        result, warnings = self.j(alert_at="1:05")
        self.assertIsNone(result)
        self.assertEqual(len(warnings), 1)
        self.assertIsNone(self.j(result="maybe")[0])


class Demo(unittest.TestCase):
    def test_demo_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = wr.demo(Path(tmp), load_config())
        total = r["rows"][-1]
        self.assertEqual((total["hits"], total["late"], total["misses"]), (11, 1, 0))
        self.assertEqual(total["low_risk"], 1)                         # the mat: labelled, not in recall
        self.assertAlmostEqual(total["recall"], 11 / 12)
        self.assertEqual(total["minutes_walked"], 11.5)                # walk 2 minutes came from the database
        self.assertAlmostEqual(total["false_per_10min"], 2 / 11.5 * 10)
        self.assertIsNotNone(total["p95_ms"])
        self.assertTrue(total["R1_met"] and total["R2_met"] and total["R4_met"])
        self.assertEqual(r["warnings"], [])
        self.assertEqual(wr.verdict(r), "PASS")
        self.assertEqual([x["route"] for x in r["rows"]], ["A", "B", "All"])

    def test_demo_command(self):
        code, out = run(["--demo"])
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: PASS", out)


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def sheets(self, log, labels, folder=None):
        f = folder or self.dir / "wt"
        f.mkdir(exist_ok=True)
        (f / "walk_log.csv").write_text(log, encoding="utf-8-sig")
        (f / "labels.csv").write_text(labels, encoding="utf-8-sig")
        return f

    def test_new_makes_the_two_empty_sheets_and_never_overwrites(self):
        f = self.dir / "walk_test_1"
        code, _ = run(["--new", str(f)])
        self.assertEqual(code, 0)
        self.assertEqual((f / "walk_log.csv").read_text(encoding="utf-8-sig"), LOG_HEADER)
        self.assertEqual((f / "labels.csv").read_text(encoding="utf-8-sig"), LABEL_HEADER)
        code, out = run(["--new", str(f)])
        self.assertEqual(code, 2)
        self.assertIn("already exists", out)

    def test_report_file_and_dashboard_rows(self):
        f = self.sheets(LOG_HEADER + "1,A,Talal,Yasmin,4,10,1,0,\n",
                        LABEL_HEADER + "4,hazard,0:10,pole,R,Y,0:20,0:15,,\n4,hazard,0:40,step,A,Y,0:50,,,\n"
                                       "4,false_alert,,,L,,,0:30,,poster\n")
        r = wr.report(f, wr.ROUND_KEYS["wt1"], self.dir / "no.db", load_config())
        total = r["rows"][-1]
        self.assertEqual((total["hits"], total["misses"], total["collisions"]), (1, 1, 1))
        self.assertAlmostEqual(total["false_per_10min"], 1.0)
        self.assertIsNone(total["p95_ms"])                              # no database: no p95
        self.assertTrue(any("no device database" in w for w in r["warnings"]))
        self.assertEqual(wr.verdict(r), "FAIL")                         # recall 0.5
        md = wr.to_markdown(r)
        self.assertIn("| Miss | 0:40 | A | step |", md)
        self.assertIn("| False alert | 0:30 | L | poster |", md)

        csv_path = self.dir / "walk_results.csv"
        csv_path.write_text("route,round,walks,minutes_walked,hits,late,misses,false_alerts,collisions,p95_ms,notes\n"
                            "C,Round 1 (W5),2,12,9,0,1,1,0,180,typed by hand\n", encoding="utf-8")
        self.assertEqual(wr.update_dashboard(r, csv_path), 2)
        self.assertEqual(wr.update_dashboard(r, csv_path), 2)          # run again: replaced, not doubled
        rows = read_walk_results(csv_path)
        self.assertEqual([(x["route"], x["round"]) for x in rows],
                         [("C", "Round 1 (W5)"), ("A", "Walk test 1 (W6)"), ("All", "Walk test 1 (W6)")])

    def test_semicolons_from_excel_and_odd_lines(self):
        f = self.sheets(LOG_HEADER.replace(",", ";") + "1;A;;;7;5;0;0;\n",
                        LABEL_HEADER.replace(",", ";") + "7;hazard;0:10;pole;R;;0:20;0:15;;\n"
                                                         "9;hazard;0:10;pole;R;Y;0:20;0:15;;\n"
                                                         "7;remark;;;;;;;;\n")
        r = wr.report(f, wr.ROUND_KEYS["wt1"], None, load_config())
        self.assertEqual(r["rows"][-1]["hits"], 1)                      # blank high_risk counts as high-risk
        text = " ".join(r["warnings"])
        for expected in ("counted as high-risk", "session 9 is not in the walk log", "'remark' is not hazard"):
            self.assertIn(expected, text)

    def test_input_problems_exit_2(self):
        f = self.sheets("walk,route\n1,A\n", LABEL_HEADER)
        code, out = run([str(f), "--round", "wt1", "--no-dashboard"])
        self.assertEqual(code, 2)
        self.assertIn("missing column", out)
        code, out = run([str(self.dir / "nowhere"), "--round", "wt1", "--no-dashboard"])
        self.assertEqual(code, 2)
        self.assertIn("walk_log.csv is missing", out)

    def test_the_command_writes_report_md(self):
        f = self.sheets(LOG_HEADER + "1,B,,,3,10,0,0,\n", LABEL_HEADER + "3,hazard,0:10,pole,R,Y,0:20,0:15,,\n")
        code, out = run([str(f), "--round", "wt1", "--no-dashboard", "--db", str(self.dir / "no.db")])
        self.assertEqual(code, 0, out)                                  # recall and R4 met, p95 not measured
        self.assertIn("RESULT: PASS (some targets not measured)", out)
        self.assertTrue((f / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()
