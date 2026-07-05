"""Acceptance test for the Data Architect stage (P1.5).

Runs architect over the bundled examples/retail/ dataset in a temp run dir and asserts the
contract: bronze SQLite tables land with correct types + row counts, the catalog is emitted,
candidate keys + PII are detected, and the stage manifest + governance envelope are valid
against the shape in schema/stage-manifest.schema.json.

Stdlib only: `python3 -m unittest` (or `python3 tests/test_architect.py`).
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

EXAMPLES = ROOT / "examples" / "retail"


class TestArchitect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = architect.run(self.tmp, "test", [str(EXAMPLES)], copy_sources=True)
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "test"

    def test_landed_two_tables(self):
        self.assertEqual(self.res["tables"], 2, "orders.csv + sellers.json should land as 2 tables")
        self.assertEqual(self.res["status"], "ok")

    def test_bronze_db_has_typed_rows(self):
        con = sqlite3.connect(self.rundir / "bronze" / "bronze.db")
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"orders", "sellers"})
        self.assertEqual(con.execute("SELECT count(*) FROM orders").fetchone()[0], 8)
        self.assertEqual(con.execute("SELECT count(*) FROM sellers").fetchone()[0], 4)
        # gmv typed real -> arithmetic works; sum of the 8 gmv values
        total = con.execute("SELECT round(sum(gmv), 2) FROM orders").fetchone()[0]
        self.assertAlmostEqual(total, 34143.49, places=2)
        # empty customer_email cell landed as NULL, not ''
        nulls = con.execute("SELECT count(*) FROM orders WHERE customer_email IS NULL").fetchone()[0]
        self.assertEqual(nulls, 1)
        con.close()

    def test_catalog_types_and_keys(self):
        cat = json.loads((self.rundir / "bronze" / "catalog.json").read_text())
        self.assertEqual(cat["table_count"], 2)
        orders = next(t for t in cat["tables"] if t["table"] == "bronze.orders")
        types = {c["name"]: c["type"] for c in orders["columns"]}
        self.assertEqual(types["order_id"], "integer")
        self.assertEqual(types["gmv"], "real")
        self.assertEqual(types["order_date"], "date")
        self.assertEqual(types["is_plus"], "boolean")
        self.assertIn("order_id", orders["candidate_keys"])

    def test_pii_flagged(self):
        cat = json.loads((self.rundir / "bronze" / "catalog.json").read_text())
        m = json.loads((self.rundir / "manifest.json").read_text())
        pii_cols = {p["column"] for p in m["stages"][0]["governance"]["pii"]}
        self.assertIn("orders.customer_email", pii_cols, "email column should be flagged PII")

    def test_manifest_envelope_valid(self):
        m = json.loads((self.rundir / "manifest.json").read_text())
        self.assertEqual(len(m["stages"]), 1)
        st = m["stages"][0]
        self.assertEqual(st["stage"], "architect")
        self.assertEqual(st["status"], "ok")
        for k in ("lineage", "pii", "contract", "quality"):
            self.assertIn(k, st["governance"])
        # lineage seeds bronze tables from their source files
        outs = {l["output"] for l in st["governance"]["lineage"]}
        self.assertEqual(outs, {"bronze.orders", "bronze.sellers"})
        self.assertTrue(st["produced"]["bronze_db_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
