"""Resolver + end-to-end smoke test for the pipeline runner (P5.1 + P5.4).

Asserts the DAG resolver's behavior (docs/02) and that the full spine runs to a report
through the runner. Stdlib only: `python3 -m unittest tests.test_pipeline`.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "run" / "scripts"))

import run_pipeline as rp  # noqa: E402

EXAMPLES = ROOT / "examples" / "retail"
METRICS = ROOT / "examples" / "retail_metrics"


class TestResolver(unittest.TestCase):
    def test_analyst_synthesizes_upstream(self):
        order, reasons = rp.resolve(["analyst"], supplied=set(), policy="synthesize")
        self.assertEqual(order, ["architect", "engineer", "analyst"])  # designer optional -> skipped
        self.assertTrue(any("added engineer" in r for r in reasons))

    def test_supplied_gold_cuts_the_chain(self):
        order, _ = rp.resolve(["analyst"], supplied={"gold"}, policy="synthesize")
        self.assertEqual(order, ["analyst"])

    def test_strict_refuses_missing(self):
        with self.assertRaises(rp.ResolveError):
            rp.resolve(["analyst"], supplied=set(), policy="strict")

    def test_full_spine_order(self):
        order, reasons = rp.resolve(["architect", "engineer", "designer", "analyst"],
                                    supplied=set(), policy="synthesize")
        self.assertEqual(order, ["architect", "engineer", "designer", "analyst"])
        self.assertEqual(reasons, [])

    def test_unimplemented_stage_errors(self):
        # sre is not implemented until P8; selecting it should error clearly
        with self.assertRaises(rp.ResolveError):
            rp.resolve(["sre"], supplied={"service"}, policy="synthesize")


class TestPipelineSmoke(unittest.TestCase):
    def test_full_spine_end_to_end(self):
        tmp = tempfile.mkdtemp()
        res = rp.run(tmp, "smoke", ["architect", "engineer", "designer", "analyst"],
                     {"run_root": tmp, "slug": "smoke", "sources": [str(EXAMPLES)],
                      "metrics_dir": str(METRICS), "ask": ["gmv by platform"], "policy": "synthesize",
                      "supplied": {}})
        self.assertEqual(res["plan"], ["architect", "engineer", "designer", "analyst"])
        rundir = pathlib.Path(tmp) / "data-team-out" / "smoke"
        # every stage recorded in the manifest, plan captured
        m = json.loads((rundir / "manifest.json").read_text())
        stages = {s["stage"] for s in m["stages"]}
        self.assertEqual(stages, {"architect", "engineer", "designer", "analyst"})
        self.assertEqual(m["plan"]["resolved"], ["architect", "engineer", "designer", "analyst"])
        # the deliverable exists
        self.assertTrue((rundir / "reports" / "report.md").exists())
        self.assertTrue((rundir / "gold" / "gold.db").exists())

    def test_supplied_gold_runs_analyst_only(self):
        # build a gold once, then feed it as supplied to an analyst-only run
        tmp = tempfile.mkdtemp()
        rp.run(tmp, "base", ["architect", "engineer", "designer"],
               {"run_root": tmp, "slug": "base", "sources": [str(EXAMPLES)],
                "metrics_dir": str(METRICS), "policy": "synthesize", "supplied": {}})
        base_gold = pathlib.Path(tmp) / "data-team-out" / "base" / "gold"
        base_sem = pathlib.Path(tmp) / "data-team-out" / "base" / "semantic"
        tmp2 = tempfile.mkdtemp()
        # supply gold + semantic so analyst runs alone
        res = rp.run(tmp2, "byo", ["analyst"],
                     {"run_root": tmp2, "slug": "byo", "ask": ["gmv by platform"], "policy": "synthesize",
                      "supplied": {"gold": str(base_gold), "semantic": str(base_sem)}})
        self.assertEqual(res["plan"], ["analyst"])
        self.assertTrue((pathlib.Path(tmp2) / "data-team-out" / "byo" / "reports" / "report.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
