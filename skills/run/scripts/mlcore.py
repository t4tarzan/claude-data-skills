"""mlcore.py — a from-scratch, stdlib-only ML core shared by the Data Scientist (train/eval)
and the ML Engineer (serve/predict).

No numpy/sklearn (keeps the skill venv-free). Implements: a linear-system solver (Gaussian
elimination w/ partial pivoting), ridge-regularized ordinary least squares, feature building
(numeric + drop-first one-hot for categoricals), single-row prediction, and regression
metrics. Honest ML: the model is a real fitted linear model, portable as pure JSON.
"""

from __future__ import annotations

import math
from typing import Any


# --- linear algebra ----------------------------------------------------------

def solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Solve A x = b for a square matrix A (Gaussian elimination, partial pivoting)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            M[piv][col] += 1e-9  # nudge a singular pivot (ridge should prevent this)
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(n):
            if r == col:
                continue
            f = M[r][col] / pv
            if f:
                for c in range(col, n + 1):
                    M[r][c] -= f * M[col][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def _matT_mat(X: list[list[float]]) -> list[list[float]]:
    m = len(X[0])
    out = [[0.0] * m for _ in range(m)]
    for row in X:
        for i in range(m):
            xi = row[i]
            if xi:
                for j in range(m):
                    out[i][j] += xi * row[j]
    return out


def _matT_vec(X: list[list[float]], y: list[float]) -> list[float]:
    m = len(X[0])
    out = [0.0] * m
    for row, yi in zip(X, y):
        for i in range(m):
            out[i] += row[i] * yi
    return out


def fit_ridge(X: list[list[float]], y: list[float], lam: float = 1.0) -> list[float]:
    """Ridge OLS: w = (XᵀX + λI) ⁻¹ Xᵀy. λ regularizes (no penalty on the bias term, col 0)."""
    XtX = _matT_mat(X)
    for i in range(1, len(XtX)):  # skip bias (index 0)
        XtX[i][i] += lam
    return solve(XtX, _matT_vec(X, y))


# --- feature building --------------------------------------------------------

def build_schema(rows: list[dict], numeric: list[str], categorical: list[str], target: str) -> dict:
    """Freeze a feature schema: numeric feature order + drop-first one-hot categories."""
    cats: dict[str, list[str]] = {}
    for c in categorical:
        vals = sorted({str(r.get(c)) for r in rows if r.get(c) is not None})
        cats[c] = vals[1:] if len(vals) > 1 else vals  # drop-first (baseline absorbed by bias)
    names = ["_bias"] + list(numeric) + [f"{c}={v}" for c in categorical for v in cats[c]]
    return {"target": target, "numeric": list(numeric), "categorical": cats, "feature_names": names}


def featurize_row(schema: dict, row: dict) -> list[float]:
    vec = [1.0]  # bias
    for n in schema["numeric"]:
        vec.append(_num(row.get(n)))
    for c, vals in schema["categorical"].items():
        rv = str(row.get(c))
        for v in vals:
            vec.append(1.0 if rv == v else 0.0)
    return vec


def build_matrix(schema: dict, rows: list[dict]) -> tuple[list[list[float]], list[float]]:
    X = [featurize_row(schema, r) for r in rows]
    y = [_num(r.get(schema["target"])) for r in rows]
    return X, y


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --- prediction + metrics ----------------------------------------------------

def predict_one(model: dict, row: dict) -> float:
    vec = featurize_row(model["schema"], row)
    w = model["weights"]
    return sum(a * b for a, b in zip(vec, w))


def metrics(y_true: list[float], y_pred: list[float]) -> dict:
    n = len(y_true)
    if n == 0:
        return {"n": 0, "r2": None, "rmse": None, "mae": None}
    mean = sum(y_true) / n
    ss_tot = sum((v - mean) ** 2 for v in y_true)
    ss_res = sum((t - p) ** 2 for t, p in zip(y_true, y_pred))
    rmse = math.sqrt(ss_res / n)
    mae = sum(abs(t - p) for t, p in zip(y_true, y_pred)) / n
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else (1.0 if ss_res < 1e-9 else 0.0)
    return {"n": n, "r2": round(r2, 4), "rmse": round(rmse, 4), "mae": round(mae, 4)}
