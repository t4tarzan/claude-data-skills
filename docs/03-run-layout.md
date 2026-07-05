# Run Layout, Manifest & Governance Envelope (P0.4)

A run is a single directory. All stages read and write inside it, at the fixed paths that
back the contract keys ([P0.2](01-artifact-contract.md)). Nothing a stage does escapes the
run directory.

## Canonical layout

```
./data-team-out/<slug>/                 # one run (slug from --name or the source folder)
├── manifest.json                       # the run record — plan + one entry per stage
├── sources/            (key: sources)  # raw inputs, as-supplied (copied or linked)
├── bronze/             (key: bronze)   # typed, as-landed tables  ── Data Architect
│   └── catalog.json    (key: catalog)  # per-source/column profile + types + keys
├── silver/             (key: silver)   # cleaned, deduped, conformed ── Data Engineer
├── gold/               (key: gold)     # modeled, aggregated, columnar-optimized
├── lineage/            (key: lineage)  # per-stage lineage graphs
├── semantic/           (key: semantic) # metric/dimension/definition DSL ── Data Designer
│   └── conformance.json (key: conformance)
├── reports/            (key: reports)  # tabular + narrative answers ── Data Analyst
├── visuals/            (key: visuals)  # charts
├── bi/                 (key: dashboard, access_model) ── BI Engineer
├── models/             (key: model, eval) ── Data Scientist
├── service/            (key: service)  # deployable model service ── ML Engineer
├── deploy/             (key: deployment) # k8s manifests/Helm + observability ── Data SRE
└── governance/         # aggregated governance report (P9), if requested
```

A stage creates only the directories for the keys it produces. A spine-only run never
creates `models/`, `service/`, `deploy/`.

## `manifest.json`

The single source of truth for what happened. Two top-level parts:

```json
{
  "run":  { "slug": "flipkart-jul", "created_at": "...", "skill_version": "0.1.0" },
  "plan": {
    "selection": ["analyst"],
    "supplied":  {},
    "policy":    "synthesize",
    "resolved":  ["architect", "engineer", "analyst"],
    "reason":    "analyst needs gold → added engineer, architect"
  },
  "stages": [ /* one stage manifest per executed stage — schema in 01 */ ]
}
```

`plan` is written by the resolver ([P0.3](02-selection-resolver.md)); `stages[]` grows as
each stage completes. The file is valid and useful after every stage, so a stopped run is
still a deliverable.

## The governance envelope (the cross-cutting plane)

Governance is not a directory a user opts into — it is a **required block on every stage
manifest** that **propagates forward**. Envelope shape (also in the stage-manifest schema):

```json
"governance": {
  "lineage":  [ { "output": "<artifact.table>", "from": ["<upstream>"], "logic": "<how>" } ],
  "pii":      [ { "column": "<table.col>", "class": "email|phone|name|id|...", "action": "masked|flagged|dropped" } ],
  "contract": { "name": "<dataset@ver>", "honored": true, "violations": [] },
  "quality":  [ { "check": "<expr>", "result": "pass|warn|fail", "detail": "<n rows>" } ]
}
```

**Propagation rule:** a stage inherits the union of its upstream stages' `lineage` and
`pii`, extends it with what it did, and writes the extended envelope. So by the Analyst
stage, a PII column flagged at ingest is still tracked in the report that uses it — no
governance fact is ever dropped mid-pipeline.

The optional **governance report** (P9) walks `stages[].governance` and renders one
consolidated view (full lineage graph, every PII column and its final disposition,
contract adherence, quality-SLA scoreboard) into `governance/report.{json,html}`.

## Why one directory + one manifest

- **Portable deliverable:** zip `./data-team-out/<slug>/` and the whole run — data,
  definitions, reports, and its complete audit trail — travels together.
- **Resumable:** re-running reads `manifest.json`, skips stages whose inputs+outputs are
  unchanged (content hashes recorded in `produced`).
- **Auditable by construction:** governance is in the same file as the work, stage by
  stage — you cannot produce a gold table without also recording where it came from.
