"""Acceptance test for the Data Analyst stage (P4.5).

Runs the full spine architect->engineer->designer->analyst over examples/retail/ and asserts:
NL questions plan to the right metric, compile to guard-honoring SQL over gold (a ratio is
recomputed, not summed), answers carry receipts, a breakdown yields a table + an SVG chart,
and the report artifacts land per contract.

Stdlib only: `python3 -m unittest tests.test_analyst`.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "run" / "scripts"))

import analyst  # noqa: E402
import architect  # noqa: E402
import designer  # noqa: E402
import engineer  # noqa: E402

EXAMPLES = ROOT / "examples" / "retail"
METRICS = ROOT / "examples" / "retail_metrics"


class TestAnalyst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        architect.run(self.tmp, "test", [str(EXAMPLES)], copy_sources=False)
        engineer.run(self.tmp, "test")
        designer.run(self.tmp, "test", metrics_dir=str(METRICS))
        self.res = analyst.run(self.tmp, "test",
                               ["What is total gmv?", "gmv by platform", "average order value by tier"])
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "test"
        self.rep = json.loads((self.rundir / "reports" / "report.json").read_text())

    def test_status_and_artifacts(self):
        self.assertEqual(self.res["answered"], 3)
        self.assertTrue((self.rundir / "reports" / "report.md").exists())
        self.assertTrue((self.rundir / "reports" / "report.json").exists())

    def test_total_gmv_answer(self):
        a = next(a for a in self.rep["answers"] if a["question"] == "What is total gmv?")
        self.assertEqual(a["plan"]["metric"], "gmv")
        self.assertIsNone(a["plan"]["breakdown"])
        # single total equals the reconciled fact total
        self.assertAlmostEqual(float(a["rows"][0][0]), 34143.49, places=2)

    def test_breakdown_has_chart_and_table(self):
        a = next(a for a in self.rep["answers"] if a["question"] == "gmv by platform")
        self.assertEqual(a["plan"]["breakdown"], "platform")
        self.assertTrue(len(a["rows"]) >= 2, "several platforms")
        self.assertIn("chart", a)
        self.assertTrue((self.rundir / a["chart"]).exists(), "SVG chart file written")
        self.assertTrue((self.rundir / a["chart"]).read_text().startswith("<svg"))

    def test_ratio_recomputed_not_summed(self):
        a = next(a for a in self.rep["answers"] if a["question"] == "average order value by tier")
        self.assertEqual(a["plan"]["metric"], "aov")
        # the compiled SQL must recompute the ratio from components, never sum a precomputed ratio
        self.assertIn("nullif(sum", a["receipt"]["sql"])
        self.assertEqual(a["receipt"]["note"], "ratio recomputed from components")

    def test_every_answer_has_receipt(self):
        for a in self.rep["answers"]:
            self.assertIn("receipt", a)
            self.assertIn("sql", a["receipt"])
            self.assertIn("lineage", a["receipt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
