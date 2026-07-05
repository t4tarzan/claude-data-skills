"""_run.py — the run directory + manifest plumbing shared by every stage.

A "run" is one directory under ./data-team-out/<slug>/. Stages read/write ONLY inside it,
at the fixed paths that back the contract keys (docs/03-run-layout.md). Every stage appends
one stage-manifest object to run/manifest.json -> stages[] (schema: schema/stage-manifest.schema.json).

Stdlib only — no third-party deps, works in any Claude Code session with plain python3.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
from typing import Any

# contract key -> run-relative path (docs/03-run-layout.md). One place; stages never hardcode paths.
KEY_PATHS: dict[str, str] = {
    "sources": "sources",
    "bronze": "bronze",
    "catalog": "bronze/catalog.json",
    "silver": "silver",
    "gold": "gold",
    "lineage": "lineage",
    "semantic": "semantic",
    "conformance": "semantic/conformance.json",
    "reports": "reports",
    "visuals": "visuals",
    "dashboard": "bi",
    "access_model": "bi/access_model.json",
    "model": "models",
    "eval": "models/eval.json",
    "service": "service",
    "deployment": "deploy",
}


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_hash(path: pathlib.Path) -> str:
    """sha256 of a file, short — used to skip unchanged stages on re-run."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()[:16]


def empty_governance() -> dict[str, Any]:
    """A valid, empty governance envelope (the cross-cutting plane; required on every stage)."""
    return {"lineage": [], "pii": [], "contract": {"name": "", "honored": True, "violations": []}, "quality": []}


class RunContext:
    """Handle to one run directory + its manifest. Passed to each stage."""

    def __init__(self, root: str | pathlib.Path, slug: str, skill_version: str = "0.1.0"):
        self.root = pathlib.Path(root).resolve()
        self.dir = self.root / "data-team-out" / slug
        self.slug = slug
        self.skill_version = skill_version
        self.manifest_path = self.dir / "manifest.json"

    # --- paths -------------------------------------------------------------
    def path(self, key: str) -> pathlib.Path:
        """Absolute path backing a contract key (e.g. path('bronze'))."""
        if key not in KEY_PATHS:
            raise KeyError(f"unknown contract key: {key!r}")
        return self.dir / KEY_PATHS[key]

    def ensure_dir(self, key: str) -> pathlib.Path:
        """Ensure the directory for a key exists (creates parent dir for file-keys)."""
        p = self.path(key)
        (p if p.suffix == "" else p.parent).mkdir(parents=True, exist_ok=True)
        return p

    def rel(self, key: str) -> str:
        """Run-relative path string for a key, as recorded in the manifest."""
        return KEY_PATHS[key]

    # --- manifest ----------------------------------------------------------
    def load(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text())
        return {"run": {"slug": self.slug, "created_at": _utcnow(), "skill_version": self.skill_version},
                "plan": {}, "stages": []}

    def save(self, manifest: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    def set_plan(self, plan: dict[str, Any]) -> None:
        m = self.load()
        m["plan"] = plan
        self.save(m)

    def append_stage(self, stage: dict[str, Any]) -> None:
        """Append (or replace, on re-run) one stage manifest, keyed by stage name."""
        m = self.load()
        m["stages"] = [s for s in m.get("stages", []) if s.get("stage") != stage.get("stage")]
        m["stages"].append(stage)
        self.save(m)

    def stage_output(self, stage_name: str, key: str) -> Any:
        """Look up what an earlier stage recorded producing under `key` (for consume-resolution)."""
        for s in self.load().get("stages", []):
            if s.get("stage") == stage_name:
                return s.get("produced", {}).get(key)
        return None

    def inherited_governance(self) -> dict[str, list]:
        """Union of upstream lineage + pii, so this stage extends (never restarts) the envelope."""
        lineage: list = []
        pii: list = []
        for s in self.load().get("stages", []):
            g = s.get("governance", {})
            lineage.extend(g.get("lineage", []))
            pii.extend(g.get("pii", []))
        return {"lineage": lineage, "pii": pii}


def new_stage(stage: str, engine: dict | None = None) -> dict[str, Any]:
    """Start a stage-manifest object (fill produced/consumed/governance/status as the stage runs)."""
    return {
        "stage": stage,
        "status": "ok",
        "started_at": _utcnow(),
        "ended_at": None,
        "engine": engine or {"role": stage, "model": "local", "provider": "claude"},
        "consumed": {},
        "produced": {},
        "governance": empty_governance(),
        "receipts": [],
        "notes": "",
    }


def finish_stage(stage_obj: dict[str, Any], status: str | None = None) -> dict[str, Any]:
    stage_obj["ended_at"] = _utcnow()
    if status:
        stage_obj["status"] = status
    return stage_obj
