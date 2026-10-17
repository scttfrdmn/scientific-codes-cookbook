#!/usr/bin/env python3
"""scikit-learn on a real workload: all 581,012 rows of UCI Covertype, three model families.

What a researcher actually runs: load a real tabular dataset, hold out a stratified test set,
fit a linear model, a gradient-boosted ensemble and a random forest, compare test accuracy.
Every claim asserted here holds for a reason other than "it was observed once":

 1. THE DATA IS THE PUBLISHED DATA. Same sha256 as scikit-learn 1.9.1's own fetch_covtype pin
    (checked in the task before this runs); 581,012 x 55; the seven class counts equal UCI's
    covtype.info exactly; every row has exactly one wilderness area and one soil type set
    (the one-hot encoding the dataset documents).

 2. THE LINEAR MODEL IS A FIXED POINT. L2-penalised multinomial logistic regression is strictly
    convex, so its optimum is unique and defined by the stationarity condition
        X'(P - Y) + W / C = 0,        sum_i (P - Y) = 0   (unpenalised intercept)
    The second line is a conservation identity: at the optimum the predicted probabilities of
    each class sum to that class's training count. Asserted for newton-cholesky, and a second
    solver (lbfgs) must reach the same objective value.

 3. FIXED SEED + PINNED THREADS => BIT-IDENTICAL FITS on one chip. HistGradientBoosting and
    RandomForest are each fitted twice; the predicted probabilities (and the forest's node
    arrays) must hash identically.

 4. TREES ARE INVARIANT TO A STRICTLY INCREASING AFFINE MAP OF THE FEATURES. A split depends
    only on the order of feature values, so refitting on 4*X + 1024 must give bit-identical
    predictions. Covertype is integer-valued and 4, 1024 are powers of two, so the map and
    every split midpoint are exact in float64 -- this is an identity, not a tolerance.

Accuracy is a PATH (it depends on every floating-point sum along the way) and is reported,
with floors only at what each model class is known to clear on this data.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import hashlib
import json
import os
import resource
import sys
import time

import numpy as np

ROWS = []
T_START = time.time()


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    dump()
    sys.exit(1)


def dump():
    with open("real-results.tsv", "w") as fh:
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite" % name)


def h(*arrays):
    m = hashlib.sha256()
    for a in arrays:
        a = np.ascontiguousarray(a)
        m.update(str(a.dtype).encode() + str(a.shape).encode())
        m.update(a.tobytes())
    return m.hexdigest()[:16]


def rss_gib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 ** 2  # Linux: KiB


def timed(label, fn):
    t0 = time.time()
    out = fn()
    dt = time.time() - t0
    rec("wall_%s_s" % label, "%.2f" % dt)
    return out


import sklearn
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score

THREADS = int(os.environ.get("SKR_THREADS", "8"))
ver("sklearn", lambda: sklearn.__version__)
ver("numpy", lambda: np.__version__)
ver("pandas", lambda: pd.__version__)
ver("arch", lambda: os.uname().machine)
ver("threads", lambda: THREADS)
try:
    from threadpoolctl import threadpool_info
    rec("threadpools", "; ".join("%s %s %s n=%s" % (i.get("internal_api"), i.get("version"),
                                                 i.get("architecture"), i.get("num_threads"))
                                 for i in threadpool_info()))
except Exception as exc:
    rec("threadpools", "unknown (%s)" % type(exc).__name__)

# ---------------------------------------------------------------- 1. the published data
PUBLISHED = [211840, 283301, 35754, 2747, 9493, 17367, 20510]   # covtype.info, section 10
raw = timed("load", lambda: pd.read_csv("covtype.data.gz", header=None).to_numpy())
rec("data_shape", "%d x %d" % raw.shape)
if raw.shape != (581012, 55):
    die("covtype is %r, the published table is 581012 x 55" % (raw.shape,))
X, y = raw[:, :54].astype(np.float64), raw[:, 54].astype(np.int64)
counts = [int((y == k).sum()) for k in range(1, 8)]
rec("class_counts", ",".join(map(str, counts)))
if counts != PUBLISHED:
    die("class counts %s != UCI's published %s" % (counts, PUBLISHED))
wild, soil = X[:, 10:14].sum(axis=1), X[:, 14:54].sum(axis=1)
rec("onehot_rows_ok", "%d wilderness, %d soil of 581012" % ((wild == 1).sum(), (soil == 1).sum()))
if not ((wild == 1).all() and (soil == 1).all()):
    die("a row does not have exactly one wilderness area and one soil type")

Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, stratify=y, random_state=0)
rec("split", "%d train / %d test, stratified, random_state=0" % (len(ytr), len(yte)))
n = len(ytr)
majority = max(np.bincount(yte)) / len(yte)
rec("acc_majority_baseline", "%.6f" % majority)

# ---------------------------------------------------------------- 2. the linear fixed point
C = 1.0
sc = StandardScaler().fit(Xtr)
Ztr, Zte = sc.transform(Xtr), sc.transform(Xte)
classes = np.unique(ytr)
Y = (ytr[:, None] == classes[None, :]).astype(np.float64)


def objective(m):
    """sklearn's objective / n: mean cross-entropy + ||W||^2 / (2 C n)."""
    P = m.predict_proba(Ztr)
    return -np.mean(np.log(P[Y.astype(bool)])) + (m.coef_ ** 2).sum() / (2 * C * n)


lr = timed("logreg_newton", lambda: LogisticRegression(
    C=C, solver="newton-cholesky", tol=1e-10, max_iter=200).fit(Ztr, ytr))
P = lr.predict_proba(Ztr)
G = Ztr.T @ (P - Y) + lr.coef_.T / C          # stationarity in W
balance = (P - Y).sum(axis=0)                 # stationarity in the intercept
finite("logistic gradient", G, balance)
g_w = np.abs(G).max() / n
g_b = np.abs(balance).max() / n
rec("logreg_iters", lr.n_iter_)
rec("logreg_stationarity_W_max_over_n", "%.3e" % g_w)
rec("logreg_balance_max_over_n", "%.3e" % g_b)
rec("logreg_balance_counts", "pred " + ",".join("%.6f" % s for s in P.sum(axis=0))
    + " vs train " + ",".join(str(int(s)) for s in Y.sum(axis=0)))
# Newton converges quadratically: at tol=1e-10 a correct fit sits orders inside 1e-6.
if g_w > 1e-6 or g_b > 1e-6:
    die("logistic optimum fails its stationarity condition (W %.2e, intercept %.2e)" % (g_w, g_b))
f_newton = objective(lr)
lr2 = timed("logreg_lbfgs", lambda: LogisticRegression(
    C=C, solver="lbfgs", tol=1e-7, max_iter=5000).fit(Ztr, ytr))
f_lbfgs = objective(lr2)
rec("logreg_lbfgs_iters", lr2.n_iter_)
rec("logreg_objective_newton", "%.15f" % f_newton)
rec("logreg_objective_lbfgs", "%.15f" % f_lbfgs)
rec("logreg_objective_reldiff", "%.3e" % (abs(f_lbfgs - f_newton) / f_newton))
# lbfgs can only be ABOVE the unique minimum, and it is a first-order method stopped at a
# loose tolerance, so the bound is on the objective (which is quadratic in the error), not
# on the coefficients (the one-hot columns are collinear, so the problem is ill-conditioned).
if not (f_lbfgs >= f_newton - 1e-12 and (f_lbfgs - f_newton) / f_newton < 1e-6):
    die("lbfgs and newton-cholesky disagree on the minimum (%.15f vs %.15f)" % (f_lbfgs, f_newton))
acc_lr = accuracy_score(yte, lr.predict(Zte))
rec("acc_logreg", "%.6f" % acc_lr)
np.save("logreg_coef.npy", np.hstack([lr.coef_, lr.intercept_[:, None]]))
rec("rss_after_logreg_gib", "%.2f" % rss_gib())

# ---------------------------------------------------------------- 3+4. trees
# l2_regularization=1: without it a near-pure leaf's Newton step -G/H has a tiny H, and on
# this data the multiclass fit diverges (see the diagnostic at the end).
HGB = dict(max_iter=300, max_leaf_nodes=255, learning_rate=0.1, l2_regularization=1.0,
           early_stopping=False, random_state=0)


def hgb_fit(Xa):
    return HistGradientBoostingClassifier(**HGB).fit(Xa, ytr)


hgb = timed("hgb_fit", lambda: hgb_fit(Xtr))
p_hgb = timed("hgb_predict", lambda: hgb.predict_proba(Xte))
hgb_b = timed("hgb_fit_repeat", lambda: hgb_fit(Xtr))
X4tr, X4te = 4.0 * Xtr + 1024.0, 4.0 * Xte + 1024.0
hgb_c = timed("hgb_fit_affine", lambda: hgb_fit(X4tr))
hashes = {"a": h(p_hgb), "repeat": h(hgb_b.predict_proba(Xte)),
          "affine": h(hgb_c.predict_proba(X4te))}
rec("hgb_proba_sha", "%s repeat %s affine %s" % (hashes["a"], hashes["repeat"], hashes["affine"]))
acc_hgb = accuracy_score(yte, hgb.classes_[p_hgb.argmax(axis=1)])
rec("acc_hgb", "%.6f" % acc_hgb)
rec("rss_after_hgb_gib", "%.2f" % rss_gib())
if hashes["repeat"] != hashes["a"]:
    die("HistGradientBoosting is not bit-reproducible with a fixed seed and %d threads" % THREADS)
if hashes["affine"] != hashes["a"]:
    die("HistGradientBoosting predictions changed under an exact increasing affine map of X")
del hgb_b, hgb_c


def rf_fit(Xa):
    return RandomForestClassifier(n_estimators=100, n_jobs=THREADS, random_state=0).fit(Xa, ytr)


def forest_hash(rf):
    m = hashlib.sha256()
    for est in rf.estimators_:
        t = est.tree_
        for a in (t.feature, t.threshold, t.children_left, t.children_right, t.value):
            m.update(np.ascontiguousarray(a).tobytes())
    return m.hexdigest()[:16]


def rf_proba(rf, Xa):
    # n_jobs=1 for prediction: the parallel predictor accumulates per-tree probabilities under
    # a lock in whatever order threads finish, so its float sum is order-dependent by design.
    rf.n_jobs = 1
    return rf.predict_proba(Xa)


rf = timed("rf_fit", lambda: rf_fit(Xtr))
p_rf = timed("rf_predict", lambda: rf_proba(rf, Xte))
nodes = sum(e.tree_.node_count for e in rf.estimators_)
rec("rf_nodes_total", nodes)
fh_a = forest_hash(rf)
rec("rss_after_rf_gib", "%.2f" % rss_gib())
rf_b = timed("rf_fit_repeat", lambda: rf_fit(Xtr))
fh_b, ph_b = forest_hash(rf_b), h(rf_proba(rf_b, Xte))
del rf_b
rf_c = timed("rf_fit_affine", lambda: rf_fit(X4tr))
ph_c = h(rf_proba(rf_c, X4te))
# The affine forest has the same structure but its thresholds are 4t+1024, so compare
# everything except the thresholds, plus the thresholds mapped back.
same_structure = all(
    np.array_equal(a.tree_.feature, b.tree_.feature)
    and np.array_equal(a.tree_.children_left, b.tree_.children_left)
    and np.array_equal(a.tree_.value, b.tree_.value)
    and np.array_equal(4.0 * a.tree_.threshold[a.tree_.feature >= 0] + 1024.0,
                       b.tree_.threshold[b.tree_.feature >= 0])
    for a, b in zip(rf.estimators_, rf_c.estimators_))
del rf_c
rec("rf_forest_sha", "%s repeat %s" % (fh_a, fh_b))
rec("rf_proba_sha", "%s repeat %s affine %s" % (h(p_rf), ph_b, ph_c))
rec("rf_affine_same_trees", same_structure)
acc_rf = accuracy_score(yte, rf.classes_[p_rf.argmax(axis=1)])
rec("acc_rf", "%.6f" % acc_rf)
rec("rss_peak_gib", "%.2f" % rss_gib())
if fh_b != fh_a or ph_b != h(p_rf):
    die("RandomForest is not bit-reproducible with a fixed seed and n_jobs=%d" % THREADS)
if ph_c != h(p_rf) or not same_structure:
    die("RandomForest changed under an exact increasing affine map of X")

# ---------------------------------------------------------------- accuracy: a path, floored
# Floors are what each class is known to clear on Covertype, not the observed values: any
# model must beat the majority class; trees capture the elevation x soil interactions a linear
# model cannot, which is why the published 2-layer NN reached 70% where LDA reached 58%.
finite("accuracy", acc_lr, acc_hgb, acc_rf)
if not (majority < acc_lr < min(acc_hgb, acc_rf)):
    die("accuracy order broken: majority %.4f, linear %.4f, hgb %.4f, rf %.4f"
        % (majority, acc_lr, acc_hgb, acc_rf))
if min(acc_hgb, acc_rf) < 0.90:
    die("a tree ensemble below 0.90 on Covertype (hgb %.4f, rf %.4f)" % (acc_hgb, acc_rf))

# ---------------------------------------------------------------- diagnostic, reported only
# The same fit at sklearn's default l2_regularization=0. Its TRAINING loss rises after ~40
# iterations as leaf values reach ~1e5: a property of unregularised Newton boosting, recorded
# on both chips so an architecture dependence would show. It must never fail the run.
try:
    from sklearn.metrics import log_loss
    t0 = time.time()
    d = HistGradientBoostingClassifier(**dict(HGB, l2_regularization=0.0, max_iter=100)).fit(Xtr, ytr)
    rec("wall_diag_l2zero_fit_s", "%.2f" % (time.time() - t0))
    sub = np.random.default_rng(0).choice(n, 50000, replace=False)
    trace = []
    for k, pp in enumerate(d.staged_predict_proba(Xtr[sub])):
        if k + 1 in (20, 40, 60, 80, 100):
            trace.append("%d:%.4f" % (k + 1, log_loss(ytr[sub], pp, labels=d.classes_)))
    rec("diag_l2zero_train_logloss", " ".join(trace))
    leaf = np.concatenate([p.nodes["value"][p.nodes["is_leaf"].astype(bool)]
                           for it in d._predictors for p in it])
    rec("diag_l2zero_max_abs_leaf", "%.4g" % np.abs(leaf).max())
    rec("diag_l2zero_acc", "%.6f" % accuracy_score(yte, d.predict(Xte)))
except Exception as exc:
    rec("diag_l2zero", "unavailable (%s)" % type(exc).__name__)

rec("wall_total_s", "%.1f" % (time.time() - T_START))
dump()
print("SKLEARN REAL OK", flush=True)
