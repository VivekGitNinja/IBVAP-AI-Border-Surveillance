#!/usr/bin/env python3
"""
SAJJATA deck — measured prototype benchmark on SYNTHETIC fleet data.
Everything this script prints/writes is measured in-process; nothing is asserted
from literature. Output: /Users/vivek/Downloads/ibvap/deck/metrics.json

Data model (synthetic, clearly labelled):
- 24 aircraft units ("A-001".."A-024"), 300 operational cycles each, run-to-failure.
- Each unit has a random degradation onset and rate; 14 sensor channels respond to
  underlying health with per-sensor loadings + noise. This mimics the *shape* of
  C-MAPSS-style run-to-failure data WITHOUT copying it and without claiming any
  real aircraft or MoD data.
"""
import json, os
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (roc_auc_score, precision_score, recall_score,
                             f1_score, mean_absolute_error)

rng = np.random.default_rng(42)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics.json")

# ---------------- synthetic generation ----------------
N_UNITS, N_CYCLES, N_SENSORS = 24, 300, 14
rows = []
for u in range(N_UNITS):
    L_u = int(rng.integers(230, 361))      # variable unit life -> cycle count is NOT a RUL proxy
    health = np.ones(L_u)
    onset = rng.integers(50, 170)          # degradation start
    rate = rng.uniform(0.0015, 0.007)      # degradation speed
    for t in range(1, L_u):
        health[t] = health[t-1] - rate * rng.uniform(0.4, 1.6)   # noisy onset & rate
        if t >= onset:
            health[t] -= abs(rng.normal(0, 0.012))                # stochastic wear events
        health[t] = max(0.05, health[t])
    loadings = np.clip(rng.normal(0.45, 0.30, N_SENSORS), 0.04, 1.1)  # some sensors barely respond
    noise = rng.normal(0, 0.05, (L_u, N_SENSORS))
    sensors = health[:, None] * loadings[None, :] + noise
    fail_arr = np.where(health < 0.15)[0]
    fail_cycle = int(fail_arr[0]) if len(fail_arr) else L_u
    for t in range(L_u):
        rows.append(dict(unit=u, cycle=t, health=health[t], rul=max(0, fail_cycle - t),
                         **{f"s{i}": sensors[t, i] for i in range(N_SENSORS)}))

import pandas as pd
df = pd.DataFrame(rows)

# rolling features per unit (trend + spread + anomaly-frequency proxies)
feat_cols = [f"s{i}" for i in range(N_SENSORS)]
for w in (10, 30):
    for c in feat_cols[:6]:
        df[f"{c}_mean{w}"] = df.groupby("unit")[c].transform(lambda s: s.rolling(w, min_periods=1).mean())
        df[f"{c}_std{w}"]  = df.groupby("unit")[c].transform(lambda s: s.rolling(w, min_periods=1).std()).fillna(0)
        df[f"{c}_slope{w}"] = df.groupby("unit")[c].transform(lambda s: s.diff(w).fillna(0) / w)
df["cycles_since_start"] = df["cycle"]

feature_cols = [c for c in df.columns if c not in ("unit", "cycle", "health", "rul")]

# ---------------- Model A: anomaly detection ----------------
# Per-unit baselining (as real AHM does): z-score each feature against that
# unit's own healthy baseline (first 25% of its life), then Isolation Forest.
base_mask = df.groupby("unit")["cycle"].transform(lambda c: c <= c.max() * 0.25)
base_stats = df[base_mask].groupby("unit")[feature_cols].agg(["mean", "std"])
Z = pd.DataFrame(index=df.index)
for c in feature_cols:
    mu = df["unit"].map(base_stats[(c, "mean")])
    sd = df["unit"].map(base_stats[(c, "std")]).replace(0, 1e-6)
    Z[c] = (df[c] - mu) / sd
Z = Z.fillna(0.0)
train_mask = base_mask
iso = IsolationForest(n_estimators=200, contamination=0.02, random_state=42)
iso.fit(Z.loc[train_mask, feature_cols])
score = -iso.score_samples(Z[feature_cols])           # higher = more anomalous
y_true_anom = (df["health"] < 0.85).astype(int)        # degradation underway
anom_auc = float(roc_auc_score(y_true_anom, score))

# ---------------- Model B: failure prediction (≤30 cycles) ----------------
df["fail30"] = (df["rul"] <= 30).astype(int)
units = df["unit"].unique()
train_units = units[:16]                                # time/unit-based split (no leakage)
test_units = units[16:]
tr, te = df[df.unit.isin(train_units)], df[df.unit.isin(test_units)]
clf = GradientBoostingClassifier(n_estimators=300, max_depth=3, learning_rate=0.08, random_state=42)
clf.fit(tr[feature_cols], tr["fail30"])
pred = clf.predict(te[feature_cols])
p30, r30 = precision_score(te["fail30"], pred, zero_division=0), recall_score(te["fail30"], pred)
f30 = f1_score(te["fail30"], pred, zero_division=0)

# ---------------- Model C: RUL regression ----------------
rfr = RandomForestRegressor(n_estimators=300, max_depth=14, min_samples_leaf=4, random_state=42, n_jobs=-1)
rfr.fit(tr[feature_cols], tr["rul"])
rul_pred = rfr.predict(te[feature_cols])
mae = float(mean_absolute_error(te["rul"], rul_pred))

# feature importances (top 6, for XAI narrative)
imp = [[k, v] for k, v in sorted(zip(feature_cols, rfr.feature_importances_), key=lambda kv: -kv[1]) if v > 0][:6]
if not imp:
    imp = [[k, round(float(v), 4)] for k, v in sorted(zip(feature_cols, rfr.feature_importances_), key=lambda kv: -kv[1])[:1]]

metrics = {
    "synthetic_data": {"units": N_UNITS, "cycles": N_CYCLES, "sensors": N_SENSORS, "rows": len(df)},
    "anomaly_detection": {"model": "Isolation Forest on per-unit z-scored features (baseline = first 25% of life)", "roc_auc": round(anom_auc, 3)},
    "failure_prediction": {"model": "Gradient Boosted Trees", "task": "failure within 30 cycles",
                            "precision": round(float(p30), 3), "recall": round(float(r30), 3), "f1": round(float(f30), 3)},
    "rul": {"model": "Random Forest Regressor", "mae_cycles": round(mae, 1)},
    "split": {"train_units": 16, "test_units": 8, "note": "unit-disjoint split to avoid leakage"},
    "top_features": [[k, round(float(v), 4)] for k, v in imp],
}
with open(OUT, "w") as f:
    json.dump(metrics, f, indent=2)
print(json.dumps(metrics, indent=2))
