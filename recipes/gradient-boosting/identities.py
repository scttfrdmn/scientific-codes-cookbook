#!/usr/bin/env python3
"""xgboost and lightgbm on Graviton, checked by structural identities rather than agreement.

THE OBVIOUS CHECK IS THE WRONG ONE. xgboost grows trees depth-wise, LightGBM leaf-wise, with
different binning -- so their predictions do NOT agree on real data, and that disagreement is
correct. Asserting numerical agreement would be asserting away a method difference, which is the
trap this catalog already recorded for kallisto/salmon TPM.

So nothing below compares their numbers. Each library is held to guarantees it documents, and the
only cross-library claim is on RANK, where the truth is known by construction:

  a constant target     every tree predicts the base value, so predictions EQUAL the constant
  separable data        the misclassification count is an integer, and 0 is 0
  a fixed seed          two runs must be bit-identical -- no tolerance to argue about
  a monotone constraint a documented structural guarantee: no negative step along the feature
  importance rank       x0 >> x1 >> nothing, by construction, so the top-2 order is determined

The importance SCALES are not comparable at all -- xgboost reports normalised gain (~0.977),
LightGBM reports split counts (305) -- which is the second reason rank is the only shared ground.
And the noise features' order differs between the two: reported, not asserted, because with no
signal their ranking is arbitrary in both.

Run with `python3 -u`.
"""
import sys

import numpy as np

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def dump():
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    sys.exit("FAIL: %s" % msg)


import lightgbm as lgb
import xgboost as xgb

rec("xgboost", xgb.__version__)
rec("lightgbm", lgb.__version__)
rec("numpy", np.__version__)

SEED = 20261011
CONST = 7.25


def xgb_reg(**kw):
    return xgb.XGBRegressor(random_state=SEED, n_jobs=1, tree_method="exact", **kw)


def lgb_reg(**kw):
    return lgb.LGBMRegressor(random_state=SEED, n_jobs=1, verbose=-1,
                             min_child_samples=5, **kw)


# ---- 1. a constant target is predicted exactly ------------------------------------------
rng = np.random.default_rng(SEED)
Xc = rng.normal(size=(400, 3))
yc = np.full(400, CONST)
for nm, m in (("xgboost", xgb_reg(n_estimators=20, max_depth=3).fit(Xc, yc)),
              ("lightgbm", lgb_reg(n_estimators=20, max_depth=3).fit(Xc, yc))):
    p = np.asarray(m.predict(Xc), dtype=float)
    err = float(np.abs(p - CONST).max())
    nuniq = int(len(np.unique(p)))
    rec("constant_%s_max_abs_error" % nm, "%.3e" % err)
    rec("constant_%s_unique_predictions" % nm, nuniq)
    if err != 0.0:
        die("%s predicts a constant target with error %.3e, not exactly" % (nm, err))
    if nuniq != 1:
        die("%s returned %d distinct predictions for a constant target" % (nm, nuniq))
rec("identity_constant_target",
    "both libraries predict a constant target exactly (0.0) and with a single distinct value")

# ---- 2. separable data is fit with exactly zero training error ---------------------------
Xs = rng.normal(size=(500, 4))
ys = (Xs[:, 0] > 0.5).astype(int)
for nm, m in (("xgboost", xgb.XGBClassifier(n_estimators=50, max_depth=3, random_state=SEED,
                                            n_jobs=1, tree_method="exact").fit(Xs, ys)),
              ("lightgbm", lgb.LGBMClassifier(n_estimators=50, max_depth=3, random_state=SEED,
                                              n_jobs=1, verbose=-1,
                                              min_child_samples=5).fit(Xs, ys))):
    wrong = int((np.asarray(m.predict(Xs)) != ys).sum())
    rec("separable_%s_misclassified" % nm, "%d of %d" % (wrong, len(ys)))
    if wrong != 0:
        die("%s misclassified %d of %d on data a single threshold separates"
            % (nm, wrong, len(ys)))
rec("identity_separable",
    "both libraries reach exactly 0 training errors on data one threshold separates")

# ---- 3. a fixed seed and one thread give bit-identical runs -------------------------------
Xr = rng.normal(size=(300, 5))
yr = Xr[:, 0] * 3 - Xr[:, 1] * 2 + rng.normal(scale=0.1, size=300)
for nm, mk in (("xgboost", lambda: xgb_reg(n_estimators=30, max_depth=4)),
               ("lightgbm", lambda: lgb_reg(n_estimators=30, max_depth=4))):
    a = np.asarray(mk().fit(Xr, yr).predict(Xr), dtype=float)
    b = np.asarray(mk().fit(Xr, yr).predict(Xr), dtype=float)
    d = float(np.abs(a - b).max())
    rec("reproducible_%s_max_abs_diff" % nm, "%.3e" % d)
    if not bool((a == b).all()):
        die("%s is not reproducible at a fixed seed: max|diff| = %.3e" % (nm, d))
rec("identity_reproducible",
    "two runs at seed %d on one thread are bit-identical in both libraries -- which is what "
    "licenses every exact claim above" % SEED)

# ---- 4. the monotonic constraint, with its control ---------------------------------------
# y DEcreases in x0 while the model is told x0 must be non-decreasing. A correct implementation
# honours the constraint and therefore fits the data badly -- that is the guarantee, and the
# unconstrained fit is run alongside to show the constraint is doing work rather than the data
# being flat.
Xm = rng.uniform(0, 1, size=(600, 2))
ym = -4.0 * Xm[:, 0] + rng.normal(scale=0.05, size=600)
grid = np.column_stack([np.linspace(0, 1, 200), np.full(200, 0.5)])
for nm, con, unc in (
    ("xgboost",
     xgb_reg(n_estimators=60, max_depth=3, monotone_constraints=(1, 0)).fit(Xm, ym),
     xgb_reg(n_estimators=60, max_depth=3).fit(Xm, ym)),
    ("lightgbm",
     lgb_reg(n_estimators=60, max_depth=3, monotone_constraints=[1, 0]).fit(Xm, ym),
     lgb_reg(n_estimators=60, max_depth=3).fit(Xm, ym))):
    worst = float(np.diff(np.asarray(con.predict(grid), dtype=float)).min())
    free = float(np.diff(np.asarray(unc.predict(grid), dtype=float)).min())
    rec("monotone_%s_worst_step" % nm, "%.3e" % worst)
    rec("monotone_%s_unconstrained_worst_step" % nm, "%.3e" % free)
    if worst < 0.0:
        die("%s violated its monotone constraint by %.3e" % (nm, worst))
    if free >= -1e-3:
        die("%s unconstrained fit is flat (%.3e) -- the constraint is not being tested" % (nm, free))
rec("identity_monotone_constraint",
    "neither library takes a negative step along a feature constrained non-decreasing (0.0), "
    "while the unconstrained fits of the same data clearly decrease -- so the guarantee is "
    "doing work")

# ---- 5. the only cross-library claim: rank, where the truth is constructed ---------------
n = 1200
Xi = rng.normal(size=(n, 6))
yi = 5.0 * Xi[:, 0] + 0.5 * Xi[:, 1] + rng.normal(scale=0.1, size=n)
ranks = {}
for nm, m in (("xgboost", xgb_reg(n_estimators=80, max_depth=3).fit(Xi, yi)),
              ("lightgbm", lgb_reg(n_estimators=80, max_depth=3).fit(Xi, yi))):
    imp = np.asarray(m.feature_importances_, dtype=float)
    order = [int(i) for i in np.argsort(-imp)]
    ranks[nm] = order
    rec("importance_%s_values" % nm, np.array2string(imp, precision=4))
    rec("importance_%s_rank" % nm, order)
    if order[0] != 0 or order[1] != 1:
        die("%s ranks features %s; the data was built with x0 >> x1 >> nothing" % (nm, order))
rec("identity_importance_rank",
    "both libraries rank x0 first and x1 second, which is how the data was constructed -- "
    "asserted on RANK because the scales are not comparable")
rec("observation_noise_rank_differs",
    "the four irrelevant features order differently between the libraries (%s vs %s) -- "
    "reported, not asserted: with no signal their ranking is arbitrary in both"
    % (ranks["xgboost"][2:], ranks["lightgbm"][2:]))
rec("observation_importance_scales",
    "xgboost reports normalised gain and lightgbm raw split counts, so only the ORDER is "
    "shared ground -- the values cannot be compared at all")

dump()
print("GRADIENT-BOOSTING OK")
