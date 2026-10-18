"""Minimal reproduction: HistGradientBoostingClassifier's TRAINING log-loss rises at l2_regularization=0.

Covertype (sklearn 1.9.1's own fetch_covtype pin), the first 20,000 rows of a seeded permutation,
the 10 numeric features only, 7 classes. Every fit has early_stopping=False and random_state=0.
python3 -u hgb_repro.py covtype.data.gz
"""
import functools, sys, time
import numpy as np, pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss
import sklearn.ensemble._hist_gradient_boosting.gradient_boosting as _gb
from sklearn.ensemble._hist_gradient_boosting.utils import get_equivalent_estimator

def ver(m):
    return str(getattr(m, "__version__", "unknown"))

print("sklearn", ver(sklearn), "numpy", ver(np), flush=True)
raw = pd.read_csv(sys.argv[1], header=None).to_numpy()
assert raw.shape == (581012, 55), raw.shape
idx = np.random.default_rng(0).permutation(len(raw))[:20000]
X, y = raw[idx, :10].astype(np.float64), raw[idx, 54]
print("rows", len(y), "features", X.shape[1], "class counts", np.bincount(y)[1:].tolist(), flush=True)
EV = (1, 25, 50, 75, 100, 150, 200)
BASE = dict(max_iter=200, early_stopping=False, random_state=0)   # everything else default


def trace_sk(m):
    tr = {k + 1: log_loss(y, p, labels=m.classes_) for k, p in enumerate(m.staged_predict_proba(X))}
    leaf = max(np.abs(p.nodes["value"][p.nodes["is_leaf"].astype(bool)]).max()
               for it in m._predictors for p in it)
    return tr, leaf


def show(name, tr, leaf=None):
    ks = sorted(tr)
    best = min(ks, key=lambda k: tr[k])
    rises = sum(tr[k] > tr[k - 1] + 1e-12 for k in ks[1:])
    print("%-34s %s | min %.4f@%d, final/min %.2f, iters-with-rise %d%s" % (
        name, " ".join("%d:%.4f" % (k, tr[k]) for k in EV if k in tr), tr[best], best,
        tr[ks[-1]] / tr[best], rises, "" if leaf is None else ", max|leaf| %.3g" % leaf), flush=True)
    return tr


def fit(name, mh=None, **kw):
    orig = _gb.TreeGrower
    if mh is not None:   # not an estimator parameter: TreeGrower's default is 1e-3
        _gb.TreeGrower = functools.partial(orig, min_hessian_to_split=mh)
    try:
        t0 = time.time()
        m = HistGradientBoostingClassifier(**dict(BASE, **kw)).fit(X, y)
    finally:
        _gb.TreeGrower = orig
    tr, leaf = trace_sk(m)
    show(name + " (%.1fs)" % (time.time() - t0), tr, leaf)
    return m, tr


print("\n== sklearn HistGradientBoostingClassifier, defaults except max_iter=200, early_stopping=False ==")
m0, tr0 = fit("defaults (l2=0, 31 leaves, lr 0.1)")
fit("again, fresh fit (determinism)")
print("\n== one change at a time ==")
for name, kw in [("l2_regularization=0.01", dict(l2_regularization=0.01)),
                 ("l2_regularization=0.1", dict(l2_regularization=0.1)),
                 ("l2_regularization=1", dict(l2_regularization=1.0)),
                 ("max_leaf_nodes=15", dict(max_leaf_nodes=15)),
                 ("max_leaf_nodes=7", dict(max_leaf_nodes=7)),
                 ("max_leaf_nodes=255", dict(max_leaf_nodes=255)),
                 ("min_samples_leaf=100", dict(min_samples_leaf=100)),
                 ("learning_rate=0.05", dict(learning_rate=0.05)),
                 ("learning_rate=0.2", dict(learning_rate=0.2))]:
    fit(name, **kw)
for mh in (2e-3, 1e-2, 1e-1, 1.0):
    fit("min_hessian_to_split=%g (patched)" % mh, mh=mh)


class ZeroInit(HistGradientBoostingClassifier):
    """Start from raw score 0 (uniform 1/7) instead of the log class priors."""
    def _get_loss(self, sample_weight):
        loss = super()._get_loss(sample_weight)
        f = loss.fit_intercept_only
        loss.fit_intercept_only = lambda *a, **k: np.zeros_like(f(*a, **k))
        return loss


try:
    t0 = time.time()
    mz = ZeroInit(**BASE).fit(X, y)
    tr, leaf = trace_sk(mz)
    show("baseline raw score 0, not log-prior (%.1fs)" % (time.time() - t0), tr, leaf)
except Exception as exc:
    print("zero-init variant failed: %s: %s" % (type(exc).__name__, exc))

print("\n== what a user on all-default settings sees (early_stopping='auto' is on above 10,000 rows) ==")
md = HistGradientBoostingClassifier(random_state=0).fit(X, y)
trd, leafd = trace_sk(md)
print("HistGradientBoostingClassifier(random_state=0): stopped at n_iter_=%d; train loss there %.4f; max|leaf| %.3g"
      % (md.n_iter_, trd[md.n_iter_], leafd))

print("\n== same rows, sklearn's own get_equivalent_estimator ==")
est = HistGradientBoostingClassifier(**BASE)
try:
    lg = get_equivalent_estimator(est, lib="lightgbm", n_classes=7)
    lg.set_params(n_jobs=8, random_state=0, deterministic=True, force_row_wise=True)
    lg.fit(X, y)
    import lightgbm
    print("lightgbm", ver(lightgbm), {k: lg.get_params()[k] for k in ("learning_rate", "num_leaves", "min_sum_hessian_in_leaf", "reg_lambda", "min_data_in_leaf")})
    tr = {k: log_loss(y, lg.predict_proba(X, num_iteration=k), labels=lg.classes_) for k in range(1, 201)}
    show("lightgbm equivalent", tr)
    for kw in (dict(min_sum_hessian_in_leaf=1e-6), dict(boost_from_average=False)):
        lg2 = get_equivalent_estimator(est, lib="lightgbm", n_classes=7)
        lg2.set_params(n_jobs=8, random_state=0, deterministic=True, force_row_wise=True, **kw).fit(X, y)
        tr = {k: log_loss(y, lg2.predict_proba(X, num_iteration=k), labels=lg2.classes_) for k in range(1, 201)}
        show("lightgbm equivalent, %s" % kw, tr)
except Exception as exc:
    print("lightgbm unavailable: %s: %s" % (type(exc).__name__, exc))
try:
    import xgboost
    for f in (1, 2):
        xg = get_equivalent_estimator(est, lib="xgboost", n_classes=7)
        p = xg.get_params(); p.pop("silent", None)
        p.update(objective="multi:softprob", n_jobs=8, random_state=0, learning_rate=p["learning_rate"] * f)
        xg = xgboost.XGBClassifier(**p).fit(X, y - 1)
        tr = {k: log_loss(y - 1, xg.predict_proba(X, iteration_range=(0, k)), labels=range(7)) for k in range(1, 201)}
        show("xgboost equivalent, lr x%d" % f, tr)
    print("xgboost", ver(xgboost), "(multi:softprob uses hessian 2p(1-p), so lr x2 is the sklearn-equivalent step)")
except Exception as exc:
    print("xgboost unavailable: %s: %s" % (type(exc).__name__, exc))

print("\n== forensics on the default fit: the first iteration whose training loss rises ==")
try:
    def softmax(R):
        E = np.exp(R - R.max(1, keepdims=True)); return E / E.sum(1, keepdims=True)
    stages = list(m0.staged_decision_function(X))
    k = next(k for k in range(2, 201) if tr0[k] > tr0[k - 1])
    before, after = stages[k - 2], stages[k - 1]          # raw scores after k-1 and k iterations
    P = softmax(before)
    Y = (y[:, None] == m0.classes_[None, :]).astype(float)
    best = None
    for c, pred in enumerate(m0._predictors[k - 1]):
        lv = pred.nodes[pred.nodes["is_leaf"].astype(bool)]
        j = int(np.abs(lv["value"]).argmax())
        if best is None or abs(lv["value"][j]) > abs(best[1]):
            best = (c, float(lv["value"][j]), int(lv["count"][j]))
    c, val, cnt = best
    inleaf = np.abs((after - before)[:, c] - val) <= abs(val) * 1e-9
    g, h = P[inleaf, c] - Y[inleaf, c], P[inleaf, c] * (1 - P[inleaf, c])
    own_before = -np.log(np.clip(P[inleaf][Y[inleaf].astype(bool)], 1e-300, None))
    own_after = -np.log(np.clip(softmax(after)[inleaf][Y[inleaf].astype(bool)], 1e-300, None))
    print("iteration %d: train loss %.4f -> %.4f" % (k, tr0[k - 1], tr0[k]))
    print("largest leaf of that iteration: tree for class %d, value %.4g, count %d (rows matched %d)"
          % (m0.classes_[c], val, cnt, inleaf.sum()))
    print("  rows of that class in the leaf %d; sum g %.4g, sum h %.4g, -lr*G/H %.4g; min h %.3g, max h %.3g, median p_class %.3g"
          % (int(Y[inleaf, c].sum()), g.sum(), h.sum(), -0.1 * g.sum() / h.sum(), h.min(), h.max(), np.median(P[inleaf, c])))
    print("  those rows' summed loss %.4g -> %.4g; whole-set summed loss change %.4g"
          % (own_before.sum(), own_after.sum(), len(y) * (tr0[k] - tr0[k - 1])))
except Exception as exc:
    print("forensics failed: %s: %s" % (type(exc).__name__, exc))
print("\n== the original observation, measurements/scikit-learn-real/real.py's diagnostic, all 581,012 rows ==")
from sklearn.model_selection import train_test_split
XA, yA = raw[:, :54].astype(np.float64), raw[:, 54].astype(np.int64)
Xtr, Xte, ytr, yte = train_test_split(XA, yA, test_size=0.2, stratify=yA, random_state=0)
t0 = time.time()
dd = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=255, learning_rate=0.1, l2_regularization=0.0,
                                    early_stopping=False, random_state=0).fit(Xtr, ytr)
sub = np.random.default_rng(0).choice(len(ytr), 50000, replace=False)
trf = {k + 1: log_loss(ytr[sub], p, labels=dd.classes_) for k, p in enumerate(dd.staged_predict_proba(Xtr[sub]))}
leaff = max(np.abs(p.nodes["value"][p.nodes["is_leaf"].astype(bool)]).max() for it in dd._predictors for p in it)
EV = (20, 40, 60, 80, 100)
show("full data, 255 leaves, 100 it (%.0fs)" % (time.time() - t0), trf, leaff)
print("HGB REPRO OK", flush=True)
