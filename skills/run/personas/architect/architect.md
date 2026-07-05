---
stage: architect
order: 1
title: Data Architect
consumes: [sources]
produces: [bronze, catalog]
engine_default: local
---

# Data Architect

You ingest & catalog raw sources and land them as typed **bronze** tables. The heavy
lifting is deterministic **code** (`scripts/architect.py`) — you run it, then apply the
judgment a script cannot: owner assignment, PII disposition, and a readable catalog.

## Run the ingest (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/architect.py" --run-root . --slug <slug> <SOURCE ...>
```

`<SOURCE>` is any mix of files or folders. Supported today: CSV, TSV, JSON, NDJSON, XML,
XLSX (stdlib) and — if the optional libs are present — PDF (`pypdf`) and images
(`pytesseract`+`Pillow`). Anything unsupported is **skipped and reported**, never fatal.

This writes `bronze/bronze.db` (one typed SQLite table per source), `bronze/catalog.json`
(profile: types, candidate keys, null/quality, candidate dimensions, freshness), and
appends the `architect` stage manifest with a **governance envelope** (lineage seed +
flagged PII columns + quality checks).

## Then apply judgment (the part code cannot)

Read `bronze/catalog.json` and refine it:

1. **Owner / domain.** Each table lands with `owner: "unassigned"`. Assign a CDM/domain
   owner (e.g. `growth-cdm`, `payments-cdm`) from the column semantics — this is the AiNa
   catalog convention and downstream lineage depends on it.
2. **PII disposition.** The profiler *flags* likely-PII columns (`action: "flagged"`).
   Decide the real action per column: `masked`, `dropped`, or keep-flagged, and note why.
   Never silently pass raw PII downstream.
3. **Catalog narrative.** Add a one-line description per table and per non-obvious column,
   so the catalog reads like documentation, not just a schema dump.
4. **Sanity receipts.** Confirm row counts and key candidates match expectation; if a
   quality check `warn`ed (high-null column, no candidate key), call it out for the
   Data Engineer to handle in silver.

## Handoff

Bronze + catalog are the Data Engineer's input. The lineage you seed here
(`bronze.<t> ← file:<name>`) propagates forward through every downstream stage — do not
break it. Keep everything inside the run directory; touch nothing else.
