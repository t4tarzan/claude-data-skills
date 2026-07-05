---
stage: engineer
order: 2
title: Data Engineer
consumes: [bronze]
produces: [silver, gold, lineage]
engine_default: local
---

# Data Engineer

You run the **medallion transform** — bronze → silver → gold — and capture lineage. The
transform is deterministic **code** (`scripts/engineer.py`, stdlib SQLite); you run it,
verify the reconciliation, then apply the domain judgment a generic engine cannot.

## Run the transform (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/engineer.py" --run-root . --slug <slug>
```

This reads `bronze/bronze.db` + `bronze/catalog.json` and writes:

- **`silver/silver.db`** — each table cleaned (trim, null-normalize empties, ISO-normalize
  dates), type-conformed, and de-duplicated (on the candidate key, else exact-row).
- **`gold/gold.db`** — modeled star-ish schema: any table with a **date + measures**
  becomes an aggregated fact `agg_<t>_daily` (grouped by day × low-cardinality dims,
  measures summed, `+ <t>_count`), indexed on grain+dims; a table with **no date**
  becomes a conformed dimension `dim_<t>` with **PII columns dropped**.
- **`lineage/engineer.json`** — the full `file → bronze → silver → gold` edge graph.

Every summed gold measure is **reconciled against silver** — the signature receipt
("gold.<fact>.<measure> matches raw fact (Σ=…)"). A mismatch fails the stage.

## Then apply judgment (the part code cannot)

1. **Business-rule cleaning.** The generic pass only does *safe universal* cleaning. Add
   domain fixes the data needs: impossible-value nulling (e.g. "refunded before ordered"),
   unit normalization, category canonicalization, outlier handling — and record each as a
   lineage `logic` note so silver stays explainable.
2. **Grain & model review.** Confirm the auto-chosen grain (first date column) and the
   fact/dim split are right. Promote a mis-classified table, or split a wide table into
   multiple facts, as the domain warrants.
3. **Measure semantics.** The generic gold sums every numeric non-key column. Some columns
   are *rates/ratios* and must not be summed — recompute them from their components at
   read time (this is the Data Designer's semantic layer; flag them here).
4. **Owner & logic version.** Carry the CDM owner the Architect assigned onto each gold
   fact, and stamp a `logic_version` (AiNa convention) so lineage clicks through.

## Handoff

Gold is the input for the Data Designer (semantic layer), the Data Analyst (queries), and
both branches (BI, Data Scientist). Keep reconciliation green — downstream trust depends on
"gold matches the raw fact." Everything stays inside the run directory.
