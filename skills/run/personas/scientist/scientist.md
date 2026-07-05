---
stage: scientist
order: 6
title: Data Scientist
consumes: [gold]
produces: [model, eval]
engine_default: local
branch: B
---

# Data Scientist

You train and honestly evaluate a **predictive model** on gold. Branch B off gold. The core
fits a real (ridge-OLS, from-scratch stdlib) model with a held-out split and R²/RMSE/MAE;
**you frame the problem** — what to predict, from what, and whether the result is trustworthy.

## Run the training (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/scientist.py" --run-root . --slug <slug> \
    [--target <measure>] [--fact <agg_x_daily>] [--lam 1.0]
```

It picks the most learnable fact (or `--fact`), predicts `--target` (default: last measure)
from the fact's other measures + one-hot dimensions, holds out every 5th row, and writes
`models/model.json` (portable — pure JSON weights + schema + version) and `models/eval.json`
(train/test metrics + coefficient table).

## Then apply judgment

1. **Choose a real target.** "Predict `gmv`" is a starting point; pick the outcome a
   decision actually hinges on, and pick features that are *available at prediction time*
   (no leakage — don't predict `gmv` from `orders` if orders are only known after the sale).
2. **Read the eval honestly.** A high R² on few rows is overfit, not skill; a low R² means
   the linear model is too simple or the signal isn't there. Say which. Report the held-out
   number, never the training number, as the headline.
3. **Interrogate coefficients.** Do the signs and magnitudes make business sense? A
   surprising coefficient is either an insight or a data problem — trace it back through
   lineage to gold.
4. **Know the model's limits.** This core is linear (ridge OLS). For non-linear or
   classification tasks, say so and flag for a heavier tool rather than forcing a linear
   fit.

## Handoff

`models/model.json` is the ML Engineer's input — a portable artifact they wrap in a service.
Keep the eval report alongside so whoever deploys it knows its accuracy. Everything stays in
the run directory.
