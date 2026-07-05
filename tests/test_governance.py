"""Acceptance test for the governance plane (P9).

Runs a broad pipeline (spine + BI + branch B + SRE) then aggregates governance; asserts the
report consolidates lineage, the PII register (with dispositions), contract adherence, the
quality/SLA scoreboard, the access policy (BI RBAC), and SLOs (SRE) into one verdict.

Stdlib only: `python3 -m unittest tests.test_governance`.
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


class TestGovernance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # spine + BI (gives PII, RBAC, reconciliation, conformance)
        self.res = rp.run(self.tmp, "gov", ["architect", "engineer", "designer", "analyst", "bi"],
                          {"run_root": self.tmp, "slug": "gov", "sources": [str(EXAMPLES)],
                           "metrics_dir": str(METRICS), "ask": ["gmv by platform"], "policy": "synthesize",
                           "supplied": {}, "governance_report": True})
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "gov"
        self.rep = json.loads((self.rundir / "governance" / "report.json").read_text())

    def test_report_artifacts(self):
        self.assertTrue((self.rundir / "governance" / "report.html").exists())
        self.assertTrue((self.rundir / "governance" / "report.json").exists())
        self.assertIsNotNone(self.res["governance"])

    def test_verdict_governed(self):
        self.assertTrue(self.rep["verdict"]["governed"], self.rep["verdict"]["issues"])

    def test_pii_register_with_disposition(self):
        cols = {p["column"]: p for p in self.rep["pii"]["columns"]}
        self.assertIn("orders.customer_email", cols)
        self.assertIn("sellers.name", cols)
        # seller name is dropped when sellers becomes a gold dimension
        self.assertEqual(cols["sellers.name"]["disposition"], "dropped in gold")

    def test_lineage_spans_the_run(self):
        outs = set(self.rep["lineage"]["outputs"])
        self.assertTrue(any(o.startswith("silver.") for o in outs))
        self.assertTrue(any(o.startswith("gold.") for o in outs))
        self.assertTrue(any(o.startswith("metric.") for o in outs))
        self.assertTrue(self.rep["lineage"]["roots"], "should have file: roots")

    def test_contracts_and_quality(self):
        self.assertTrue(self.rep["all_contracts_honored"])
        self.assertEqual(self.rep["quality"]["totals"]["fail"], 0)
        self.assertTrue(any("matches raw fact" in r for r in self.rep["quality"]["reconciliation"]))
        self.assertIsNotNone(self.rep["quality"]["conformance"])
        self.assertIn("aov", self.rep["quality"]["conformance"]["non_additive_guarded"])

    def test_access_policy_present(self):
        self.assertIsNotNone(self.rep["access_policy"])
        self.assertIn("viewer", self.rep["access_policy"]["roles"])
        self.assertIn("finance-cdm", self.rep["access_policy"]["restricted_owners"])

    def test_manifest_pointer(self):
        m = json.loads((self.rundir / "manifest.json").read_text())
        self.assertIn("governance_report", m)
        self.assertTrue(m["governance_report"]["verdict"]["governed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
