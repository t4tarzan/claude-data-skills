---
stage: bi
order: 5
title: BI Engineer
consumes: [gold, semantic]
produces: [dashboard, access_model]
engine_default: local
branch: A
---

# BI Engineer

You stand up the **refreshing, role-scoped BI layer** — the dashboard leaders actually open.
Branch A off gold: it needs the semantic layer, so it never invents a number, only surfaces
governed ones. The deterministic core materializes panels + RBAC; **you curate the
dashboard and set the access policy.**

## Run the BI build (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/bi.py" --run-root . --slug <slug> [--refresh-cadence daily]
```

It reads gold + `semantic.json` and writes `bi/dashboard.json` (a KPI panel per metric + a
breakdown per metric×primary-dim, **materialized from gold** with guard-honoring SQL),
`bi/access_model.json` (RBAC), and a self-contained `bi/dashboard.html` (inline SVG, no CDN).
**Re-running IS the refresh** — it re-materializes from current gold and stamps
`refresh.last_refreshed`.

## RBAC — role scoping at the edge

Follows AiNa's `X-Principal`-at-the-edge model. Default roles:

| role | sees |
|---|---|
| admin / analyst | all panels (KPI + breakdown), all metrics |
| leadership | KPI totals only |
| viewer | KPI totals for **non-restricted** metrics (finance/payments hidden) |

Every panel carries `visible_to: [roles]`. The backend stays role-blind; the gateway
enforces the access model. A restricted-owner metric (e.g. `finance-cdm`) is withheld from
the public `viewer` automatically.

## Then apply judgment

1. **Curate.** The auto-panels are a starting grid. Order them by what a leader checks
   first; drop noise; group by domain.
2. **Set the real access policy.** Adjust `_RESTRICTED_OWNERS` / role definitions to your
   org — who sees revenue, who sees only their domain. Never widen access to a
   PII-derived or financial metric without a reason.
3. **Refresh cadence.** Pick a cadence that matches the data's freshness (the Architect's
   catalog records it). Wire the re-run to your scheduler (cron/PM2) outside the skill.

## Handoff

The dashboard is the served product; the access model is the contract the gateway enforces.
Everything stays in the run directory.
