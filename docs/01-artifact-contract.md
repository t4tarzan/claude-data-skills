# Artifact Contract (P0.2)

Every stage is a pure function over the **run directory**: it reads the artifacts named
in its `consumes` and writes the artifacts named in its `produces`, then appends a
**stage manifest** recording exactly what it did. The manifest is the seam that lets any
stage run after any other (or standalone) — a downstream stage reads the *manifest*, not
the previous stage's internals.

## Stage contracts (what each reads / writes)

Artifact keys are stable logical names; their on-disk location is fixed by the run layout
([P0.4](03-run-layout.md)).

| stage | consumes (keys) | produces (keys) |
|-------|-----------------|-----------------|
| `architect` | `sources` (raw input paths) | `bronze`, `catalog` |
| `engineer`  | `bronze` | `silver`, `gold`, `lineage` |
| `designer`  | `gold` | `semantic` (metric/dimension defs), `conformance` |
| `analyst`   | `gold`, `semantic?` | `reports`, `visuals` |
| `bi`        | `gold`, `semantic`, `reports?` | `dashboard`, `access_model` |
| `scientist` | `gold` | `model`, `eval` |
| `ml`        | `model` | `service` (deployable) |
| `sre`       | `service` \| `dashboard` \| `pipeline` (any deployable) | `deployment` |

`?` = optional consume (the stage degrades gracefully if absent). A stage may consume a
key produced by an earlier stage **or** one the user supplied directly (see the resolver,
[P0.3](02-selection-resolver.md)).

## The stage manifest

Each stage appends one object to `run/manifest.json → stages[]`. Schema:
`schema/stage-manifest.schema.json`. Shape:

```json
{
  "stage": "engineer",
  "status": "ok",
  "started_at": "2026-07-05T10:00:00Z",
  "ended_at":   "2026-07-05T10:02:11Z",
  "engine":     { "role": "engineer", "model": "local", "provider": "claude" },
  "consumed":   { "bronze": "bronze/" },
  "produced":   { "silver": "silver/", "gold": "gold/", "lineage": "lineage/engineer.json" },
  "governance": {
    "lineage":  [ { "output": "gold.orders_daily", "from": ["bronze.orders"], "logic": "silver→gold agg v1" } ],
    "pii":      [ { "column": "gold.customers.email", "class": "email", "action": "masked" } ],
    "contract": { "name": "orders@1", "honored": true },
    "quality":  [ { "check": "no_nulls(order_id)", "result": "pass" } ]
  },
  "receipts":   [ "gold.orders_daily matches raw fact (Σ=25,004,113)" ],
  "notes":      "columnar gold; roaring-bitmap index on tier"
}
```

### Field rules

- **`status`** ∈ `ok | partial | failed | skipped`. `partial` = produced some outputs but
  a quality check or optional consume degraded; the run continues, the resolver may warn.
- **`engine`** records the model actually used for this stage (see [P0.5](04-engine-and-sovereignty.md))
  so a run is reproducible and auditable.
- **`consumed` / `produced`** map contract keys → run-relative paths. A stage MUST NOT
  read or write outside these declared paths (keeps stages composable and sandboxed).
- **`governance`** is REQUIRED on every stage (may be empty arrays) — this is the
  cross-cutting plane. `lineage` and `pii` propagate forward: a downstream stage inherits
  and extends the envelope rather than restarting it.
- **`receipts`** are the human-readable "show your work" lines AiNa already uses (e.g.
  "gold matches the raw 25M fact") — surfaced in reports and the run summary.

## Why manifest-mediated (not stage-to-stage calls)

- **Composability:** any stage can start from artifacts on disk, whoever produced them.
- **Resumability:** a run can stop after any stage and be valuable; re-running skips
  stages whose outputs + inputs are unchanged (content hash in `produced`).
- **Auditability:** the manifest *is* the receipt trail — which model, which contract,
  what lineage — end to end, in one file.

The manifest is append-only within a run; the run directory ([P0.4](03-run-layout.md)) is
the unit of work.
