"""Acceptance test for the Data Engineer stage (P2.5).

Runs architect then engineer over examples/retail/ and asserts the medallion contract:
silver is cleaned+deduped, gold builds the modeled fact (agg_orders_daily) and the
conformed dimension (dim_sellers, PII dropped), lineage bronze->silver->gold is captured,
and — the signature receipt — every summed gold measure reconciles to silver.

Stdlib only: `python3 -m unittest tests.test_engineer`.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "run" / "scripts"))

import architect  # noqa: E402
import engineer  # noqa: E402

EXAMPLES = ROOT / "examples" / "retail"


class TestEngineer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        architect.run(self.tmp, "test", [str(EXAMPLES)], copy_sources=False)
        self.res = engineer.run(self.tmp, "test")
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "test"

    def test_status_ok_and_reconciled(self):
        self.assertEqual(self.res["status"], "ok", "reconciliation must pass")

    def test_silver_built_and_deduped(self):
        con = sqlite3.connect(self.rundir / "silver" / "silver.db")
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"orders", "sellers"})
        self.assertEqual(con.execute("SELECT count(*) FROM orders").fetchone()[0], 8)
        con.close()

    def test_gold_fact_and_dim(self):
        con = sqlite3.connect(self.rundir / "gold" / "gold.db")
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("agg_orders_daily", names, "orders (date+measures) -> a daily fact")
        self.assertIn("dim_sellers", names, "sellers (no date) -> a conformed dimension")
        # the fact's gmv total equals the raw silver total (the matches-raw-fact receipt)
        gold_gmv = con.execute("SELECT round(sum(gmv),2) FROM agg_orders_daily").fetchone()[0]
        self.assertAlmostEqual(gold_gmv, 34143.49, places=2)
        # orders_count sums to the raw row count
        cnt = con.execute("SELECT sum(orders_count) FROM agg_orders_daily").fetchone()[0]
        self.assertEqual(cnt, 8)
        # dim_sellers dropped the PII 'name' column
        cols = {r[1] for r in con.execute("PRAGMA table_info(dim_sellers)")}
        self.assertNotIn("name", cols, "seller name is PII -> dropped from gold dimension")
        self.assertIn("seller_id", cols)
        con.close()

    def test_reconciliation_receipt_present(self):
        m = json.loads((self.rundir / "manifest.json").read_text())
        eng = next(s for s in m["stages"] if s["stage"] == "engineer")
        recs = " ".join(eng["receipts"])
        self.assertIn("matches raw fact", recs)
        # every reconciliation quality check passed
        recon = [q for q in eng["governance"]["quality"] if "reconcile" in q["check"]]
        self.assertTrue(recon)
        self.assertTrue(all(q["result"] == "pass" for q in recon))

    def test_lineage_chain(self):
        edges = json.loads((self.rundir / "lineage" / "engineer.json").read_text())["edges"]
        outs = {e["output"] for e in edges}
        # full chain present: file -> bronze -> silver -> gold
        self.assertIn("bronze.orders", outs)          # inherited from architect
        self.assertIn("silver.orders", outs)
        self.assertIn("gold.agg_orders_daily", outs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
