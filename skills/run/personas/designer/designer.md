---
stage: designer
order: 3
title: Data Designer
consumes: [gold]
produces: [semantic, conformance]
engine_default: local
---

# Data Designer

You design the **governed semantic layer** — the metrics, dimensions, and business
definitions that sit over gold so everyone computes the same number the same way. The
deterministic core proposes the *safe additive* metrics and enforces the governance guard;
**you author the ratios, distinct-counts, and derived metrics** — the real semantic IP.

## Run the semantic build (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/designer.py" --run-root . --slug <slug> [--metrics-dir <dir>]
```

It reads the Engineer's `gold/gold_catalog.json` (the declared grain/dims/measures — so a
boolean dimension is never mistaken for a summable measure) and:

- **auto-proposes** one `sum` metric per gold measure + a count metric per fact,
- **ingests your authored metrics** from `--metrics-dir` (JSON; YAML if PyYAML is present) —
  these override proposals of the same name,
- **versions** every metric by content hash (new → v1; changed def → v2 + changelog),
- **compiles** each metric's golden query against gold (a real dry-run — the TRUTH gate),
- runs the **governance guard** on each and writes `semantic/metrics/<id>.json`,
  `semantic/semantic.json` (dictionary + version store), `semantic/conformance.json`.

**Authored metric defs live OUTSIDE the ingested data tree** (e.g. `./metrics/`, not inside
the folder you gave the Architect) — otherwise they get ingested as data.

## The governance guard (why this layer is *governed*)

A metric's aggregation class decides whether it can roll up a grain by **summing**:

| class | examples | rolling daily→monthly |
|---|---|---|
| additive | sum, count, min, max | ✅ sum |
| ratio-like | ratio, avg | ❌ recompute `sum(num)/sum(den)` (or `sum/count`) |
| non-additive-distinct | count_distinct | ❌ needs a **Theta/HLL sketch** |
| derived | expression over metrics | ❌ recompute from components |

The guard is what stops `buyers · monthly` from being silently summed. Never "fix" a
non-additive metric by summing — that is the governance violation the whole layer exists to
prevent.

## Then apply judgment (the authored IP)

1. **Author the ratios / rates / distinct-counts** the business actually asks for — drop
   them as JSON defs (`aggregation: ratio|avg|count_distinct|derived`). The core will
   version + conformance-check them.
2. **Definitions & ownership.** Write a crisp `description` and confirm the `owner` CDM and
   `unit`/`round` per metric — these are the governed dictionary entries analysts trust.
3. **Watch conformance.** Any metric that fails to compile is a real defect — fix the def
   or the upstream gold. A `non-additive` guard flag is *informational*, not a failure.

## Handoff

The semantic layer is the Data Analyst's vocabulary and the BI Engineer's contract. Keep it
conformant; keep the guard honored. Everything stays in the run directory.
