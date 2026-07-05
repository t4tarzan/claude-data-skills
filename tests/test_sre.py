"""Acceptance test for the Data SRE stage (P8.5).

Builds a model service (via the ML branch) then runs sre; asserts the deployment bundle is
generated per contract — Deployment/Service/HPA + observability (ServiceMonitor +
PrometheusRule) + SLO — with the expected kinds, autoscaling bounds, health probes, and
alert rules.

Stdlib only: `python3 -m unittest tests.test_sre`.
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
import engineer  # noqa: E402
import ml as mlstage  # noqa: E402
import scientist  # noqa: E402
import sre  # noqa: E402

ML_DATA = ROOT / "examples" / "ml_sales"


class TestSRE(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        architect.run(self.tmp, "test", [str(ML_DATA)], copy_sources=False)
        engineer.run(self.tmp, "test")
        scientist.run(self.tmp, "test", target="gmv")
        mlstage.run(self.tmp, "test")
        self.res = sre.run(self.tmp, "test", min_replicas=2, max_replicas=8)
        self.dd = pathlib.Path(self.tmp) / "data-team-out" / "test" / "deploy" / "model-api"

    def test_bundle_files(self):
        for f in ("Dockerfile", "deployment.yaml", "service.yaml", "hpa.yaml",
                  "servicemonitor.yaml", "prometheusrule.yaml", "slo.json", "values.yaml"):
            self.assertTrue((self.dd / f).exists(), f"{f} missing")

    def test_deployment_manifest(self):
        dep = (self.dd / "deployment.yaml").read_text()
        self.assertIn("kind: Deployment", dep)
        self.assertIn("readinessProbe", dep)
        self.assertIn("/health", dep)
        self.assertIn('prometheus.io/scrape: "true"', dep)  # observability annotation
        self.assertIn("limits:", dep)  # resource limits

    def test_hpa_autoscaling(self):
        hpa = (self.dd / "hpa.yaml").read_text()
        self.assertIn("kind: HorizontalPodAutoscaler", hpa)
        self.assertIn("minReplicas: 2", hpa)
        self.assertIn("maxReplicas: 8", hpa)
        self.assertIn("averageUtilization: 70", hpa)

    def test_observability_and_alerts(self):
        sm = (self.dd / "servicemonitor.yaml").read_text()
        self.assertIn("kind: ServiceMonitor", sm)
        self.assertIn("/metrics", sm)
        pr = (self.dd / "prometheusrule.yaml").read_text()
        self.assertIn("kind: PrometheusRule", pr)
        self.assertIn("Down", pr)          # availability alert
        self.assertIn("HighErrorRate", pr)  # error-rate alert

    def test_slo_and_machine_artifact(self):
        slo = json.loads((self.dd / "slo.json").read_text())
        self.assertEqual(slo["availability"], "99.9%")
        dj = json.loads((pathlib.Path(self.tmp) / "data-team-out" / "test" / "deploy" / "deployment.json").read_text())
        self.assertEqual(dj["deployables"][0]["autoscale"], {"min": 2, "max": 8, "cpuTarget": 70})
        self.assertTrue(dj["deployables"][0]["observability"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
