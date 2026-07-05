"""Acceptance test for branch B — Data Scientist + ML Engineer (P7.5).

Runs architect->engineer->scientist->ml over examples/ml_sales/ (a synthetic dataset with a
known linear relationship gmv = 5*visits + 1500*promo + region_offset) and asserts: the model
recovers the relationship (held-out R² high), the eval report + portable model land, the ML
Engineer packages a self-contained service, and a train->predict round-trip on the PACKAGED
model returns the known answer.

Stdlib only: `python3 -m unittest tests.test_ml`.
"""

from __future__ import annotations

import json
import pathlib
import py_compile
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "run" / "scripts"))

import architect  # noqa: E402
import engineer  # noqa: E402
import ml as mlstage  # noqa: E402
import mlcore  # noqa: E402
import scientist  # noqa: E402

ML_DATA = ROOT / "examples" / "ml_sales"


class TestBranchB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        architect.run(self.tmp, "test", [str(ML_DATA)], copy_sources=False)
        engineer.run(self.tmp, "test")
        self.sci = scientist.run(self.tmp, "test", target="gmv")
        self.mle = mlstage.run(self.tmp, "test")
        self.rundir = pathlib.Path(self.tmp) / "data-team-out" / "test"

    def test_model_recovers_relationship(self):
        self.assertEqual(self.sci["target"], "gmv")
        self.assertGreater(self.sci["test"]["r2"], 0.9, "held-out R² should be high on clean linear data")
        # the visits coefficient should be ~5 (the true slope)
        model = json.loads((self.rundir / "models" / "model.json").read_text())
        wv = dict(zip(model["schema"]["feature_names"], model["weights"]))
        self.assertAlmostEqual(wv["visits"], 5.0, delta=0.6)

    def test_artifacts_present(self):
        self.assertTrue((self.rundir / "models" / "model.json").exists())
        self.assertTrue((self.rundir / "models" / "eval.json").exists())
        for f in ("model.json", "mlcore.py", "serve.py", "manifest.json", "README.md"):
            self.assertTrue((self.rundir / "service" / f).exists(), f"service/{f} missing")

    def test_service_serve_compiles(self):
        # the packaged server is valid Python (deployable as-is)
        py_compile.compile(str(self.rundir / "service" / "serve.py"), doraise=True)

    def test_train_predict_roundtrip(self):
        # load the PACKAGED model and predict a known input: region=W, visits=200, promo=1
        model = json.loads((self.rundir / "service" / "model.json").read_text())
        pred = mlcore.predict_one(model, {"region": "W", "visits": 200, "promo": 1})
        true = 5 * 200 + 1500 * 1 + 1500  # = 4000
        self.assertLess(abs(pred - true), 400, f"prediction {pred:.0f} should be near {true}")

    def test_manifest_input_spec(self):
        man = json.loads((self.rundir / "service" / "manifest.json").read_text())
        self.assertEqual(man["target"], "gmv")
        self.assertIn("visits", man["input_spec"]["numeric"])
        self.assertIn("region", man["input_spec"]["categorical"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
