"""Acceptance test for the BI Engineer stage (P6.4).

Builds gold+semantic then runs bi; asserts panels materialize from gold, the dashboard
renders, RBAC scopes panels per role (a finance metric is hidden from `viewer`), and the
refresh block is present.

Stdlib only: `python3 -m unittest tests.test_bi`.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "run" / "scripts"))

import architect  # noqa: E402
import bi  # noqa: E402
import designer  # noqa: E402
import engineer  # noqa: E402

EXAMPLES = ROOT / "examples" / "retail"
METRICS = ROOT / "examples" / "retail_metrics"


class TestBI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        architect.run(self.tmp, "test", [str(EXAMPLES)], copy_sources=False)
        engineer.run(self.tmp, "test")
        designer.run(self.tmp, "test", metrics_dir=str(METRICS))
        self.res = bi.run(self.tmp, "test")
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "test"
        self.dash = json.loads((self.rundir / "bi" / "dashboard.json").read_text())
        self.access = json.loads((self.rundir / "bi" / "access_model.json").read_text())

    def test_panels_and_artifacts(self):
        self.assertGreaterEqual(self.res["panels"], 4)
        self.assertTrue((self.rundir / "bi" / "dashboard.html").exists())
        html = (self.rundir / "bi" / "dashboard.html").read_text()
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("<svg", html)  # breakdown charts embedded

    def test_kpi_value_matches_gold(self):
        gmv_kpi = next(p for p in self.dash["panels"] if p["metric"] == "gmv" and p["kind"] == "kpi")
        self.assertAlmostEqual(float(gmv_kpi["value"]), 34143.49, places=2)

    def test_rbac_hides_finance_from_viewer(self):
        # aov is finance-cdm (restricted) -> its KPI must NOT be visible to `viewer`
        aov_kpi = next(p for p in self.dash["panels"] if p["metric"] == "aov" and p["kind"] == "kpi")
        self.assertNotIn("viewer", aov_kpi["visible_to"])
        self.assertIn("leadership", aov_kpi["visible_to"])
        # a breakdown panel is analyst/admin only (leadership/viewer get KPIs only)
        brk = next(p for p in self.dash["panels"] if p["kind"] == "breakdown")
        self.assertNotIn("leadership", brk["visible_to"])
        self.assertIn("analyst", brk["visible_to"])

    def test_refresh_block(self):
        r = self.dash["refresh"]
        self.assertEqual(r["cadence"], "daily")
        self.assertTrue(r["materialized"])
        self.assertTrue(r["last_refreshed"].endswith("Z"))

    def test_access_model_roles(self):
        self.assertEqual(set(self.access["roles"]), {"admin", "analyst", "leadership", "viewer"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
