"""Regression gate (Week 3). Run with: python -m unittest discover -s tests -v"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from datalogger import config
from datalogger.stub_stream import stub_stream
from qa import gate

START_NS = 1_000_000_000_000
TARGET = 300.0


def settings():
    return config.datalogger_settings(config.load_config())


def short_stub():
    return list(stub_stream(duration_s=60, fps=10, start_ns=START_NS))


def slowed(lines, msg_type="output_command", extra_ms=300):
    """The same scenario, with one stage arriving extra_ms later: a slower change."""
    out = []
    for line in lines:
        d = json.loads(line)
        if d["msg_type"] == msg_type:
            d["t_mono_ns"] += extra_ms * 1_000_000
        out.append(json.dumps(d, separators=(",", ":"), sort_keys=True))
    return out


def result(rows, metric):
    return next(r for r in rows if r["metric"] == metric)


class Rules(unittest.TestCase):
    BASE = {"p95_ms": 75.0, "logged": 2098, "skipped": 0, "alerts": 2, "obstacles": 1, "snapshots": 1}

    def test_same_numbers_pass(self):
        rows = gate.check("b", dict(self.BASE), self.BASE, TARGET)
        self.assertTrue(all(r["ok"] for r in rows))

    def test_p95_may_rise_a_little_but_not_10_percent(self):
        self.assertTrue(result(gate.check("b", {"p95_ms": 82.0}, self.BASE, TARGET), "p95_ms")["ok"])
        self.assertFalse(result(gate.check("b", {"p95_ms": 90.0}, self.BASE, TARGET), "p95_ms")["ok"])

    def test_p95_above_the_target_fails_even_without_a_baseline(self):
        r = result(gate.check("b", {"p95_ms": 310.0}, None, TARGET), "p95_ms")
        self.assertFalse(r["ok"])
        self.assertIn("target", r["why"])

    def test_lost_or_broken_messages_fail(self):
        self.assertFalse(result(gate.check("b", {"logged": 2000}, self.BASE, TARGET), "logged")["ok"])
        self.assertFalse(result(gate.check("b", {"skipped": 5}, self.BASE, TARGET), "skipped")["ok"])
        self.assertFalse(result(gate.check("b", {"alerts": 1}, self.BASE, TARGET), "alerts")["ok"])
        self.assertFalse(result(gate.check("b", {"snapshots": 0}, self.BASE, TARGET), "snapshots")["ok"])

    def test_better_numbers_pass(self):
        rows = gate.check("b", {"p95_ms": 50.0, "logged": 2100, "skipped": 0, "alerts": 3}, self.BASE, TARGET)
        self.assertTrue(all(r["ok"] for r in rows))

    def test_recall_and_false_alerts_for_walk_recordings(self):
        base = {"recall": 0.92, "false_per_10min": 1.5}
        self.assertTrue(result(gate.check("w", {"recall": 0.91}, base, TARGET), "recall")["ok"])
        self.assertFalse(result(gate.check("w", {"recall": 0.89}, base, TARGET), "recall")["ok"])
        self.assertTrue(result(gate.check("w", {"false_per_10min": 1.6}, base, TARGET), "false_per_10min")["ok"])
        self.assertFalse(result(gate.check("w", {"false_per_10min": 1.8}, base, TARGET), "false_per_10min")["ok"])

    def test_a_new_number_is_shown_but_does_not_fail(self):
        r = result(gate.check("b", {"recall": 0.5}, self.BASE, TARGET), "recall")
        self.assertEqual((r["ok"], r["why"], r["baseline"]), (True, "new", None))


class Measure(unittest.TestCase):
    def test_stub_numbers(self):
        n = gate.measure(short_stub(), settings())
        self.assertEqual(n, {"logged": 2098, "skipped": 0, "alerts": 2, "obstacles": 1, "snapshots": 1,
                             "p95_ms": 75.0})

    def test_a_slower_output_stage_is_measured(self):
        n = gate.measure(slowed(short_stub()), settings())
        self.assertGreater(n["p95_ms"], 300)

    def test_the_repo_baseline_covers_every_benchmark(self):
        self.assertEqual(set(gate.load_baseline()), set(gate.benchmarks()))


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.baseline = Path(self.tmp.name) / "baseline.json"
        self.out = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self, lines, args=()):
        with mock.patch.object(gate, "BASELINE", self.baseline), \
                mock.patch.object(gate, "benchmarks", lambda: {"one": lambda: lines}), \
                contextlib.redirect_stdout(self.out):
            return gate.main(list(args))

    def test_update_then_pass_then_a_slower_change_fails(self):
        self.assertEqual(self.run_gate(short_stub()), 2)                  # no baseline yet
        self.assertEqual(self.run_gate(short_stub(), ["--update"]), 0)
        self.assertTrue(self.baseline.is_file())
        self.assertEqual(self.run_gate(short_stub()), 0)
        self.assertEqual(self.run_gate(slowed(short_stub(), extra_ms=40)), 1)   # 75 -> 115 ms: worse than baseline
        self.assertIn("RESULT: FAIL", self.out.getvalue())

    def test_numbers_above_the_target_never_become_the_baseline(self):
        self.assertEqual(self.run_gate(slowed(short_stub()), ["--update"]), 1)
        self.assertFalse(self.baseline.is_file())

    def test_summary_for_the_github_checks_page(self):
        summary = Path(self.tmp.name) / "summary.md"
        self.run_gate(short_stub(), ["--update"])
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.run_gate(short_stub())
        text = summary.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## Regression gate: PASS"))
        self.assertIn("| one | p95 latency (ms) | 75 | 75 |", text)


if __name__ == "__main__":
    unittest.main()
