"""Acceptance test for the Data Designer stage (P3.5).

Runs architect -> engineer -> designer over examples/retail/ (with the authored aov ratio
metric) and asserts the governed semantic layer: additive metrics auto-proposed from gold,
the authored ratio versioned + compiled, and — the signature governance guard — the ratio
is flagged non-additive (recompute, not sum) while a distinct-count would demand a sketch.

Stdlib only: `python3 -m unittest tests.test_designer`.
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
import designer  # noqa: E402
import dsl  # noqa: E402
import engineer  # noqa: E402

EXAMPLES = ROOT / "examples" / "retail"
METRICS = ROOT / "examples" / "retail_metrics"  # authored metric defs live OUTSIDE the ingested data tree


class TestDesigner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        architect.run(self.tmp, "test", [str(EXAMPLES)], copy_sources=False)
        engineer.run(self.tmp, "test")
        self.res = designer.run(self.tmp, "test", metrics_dir=str(METRICS))
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "test"
        self.sem = json.loads((self.rundir / "semantic" / "semantic.json").read_text())
        self.conf = json.loads((self.rundir / "semantic" / "conformance.json").read_text())["metrics"]

    def test_status_ok(self):
        self.assertEqual(self.res["status"], "ok")

    def test_additive_metrics_proposed(self):
        ids = {m["metric"] for m in self.sem["metrics"]}
        # gmv + items summed, and an 'orders' count metric proposed from orders_count
        self.assertIn("gmv", ids)
        self.assertIn("items", ids)
        self.assertIn("orders", ids)
        gmv = next(m for m in self.sem["metrics"] if m["metric"] == "gmv")
        self.assertEqual(gmv["aggregation"], "sum")
        self.assertTrue(gmv["_proposed"])

    def test_authored_ratio_versioned_and_compiled(self):
        aov = next((m for m in self.sem["metrics"] if m["metric"] == "aov"), None)
        self.assertIsNotNone(aov, "authored aov ratio should be in the semantic layer")
        self.assertFalse(aov["_proposed"])
        self.assertEqual(aov["version"], "v1")
        conf = next(c for c in self.conf if c["metric"] == "aov")
        self.assertTrue(conf["compiles"], "aov golden query must compile against gold")
        self.assertEqual(conf["golden_query"], 'sum("gmv")*1.0/nullif(sum("orders_count"),0)')

    def test_governance_guard_flags_ratio(self):
        conf = next(c for c in self.conf if c["metric"] == "aov")
        guard = conf["rollup_guard"]
        self.assertFalse(guard["ok"], "a ratio must NOT be summable up a grain")
        self.assertEqual(guard["strategy"], "recompute")
        self.assertIn("aov", self.res["non_additive"])
        # additive gmv is fine to roll up
        gconf = next(c for c in self.conf if c["metric"] == "gmv")
        self.assertTrue(gconf["rollup_guard"]["ok"])

    def test_guard_distinct_needs_sketch(self):
        # the signature AiNa behaviour: buyers (count_distinct) monthly -> Theta/HLL sketch
        v = dsl.reaggregation_verdict("count_distinct", "daily", "monthly")
        self.assertFalse(v["ok"])
        self.assertEqual(v["strategy"], "sketch")
        self.assertIn("Theta", v["reason"])

    def test_dimension_dictionary(self):
        self.assertIn("platform", self.sem["dimensions"])
        self.assertIn("tier", self.sem["dimensions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
