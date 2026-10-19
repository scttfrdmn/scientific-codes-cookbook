"""xgboost + LightGBM on all of UCI HIGGS, with LightGBM's own published benchmark settings.

Train = the first 10,500,000 rows, test = the last 500,000 (the split UCI documents and
LightGBM's docs/Experiments.rst uses). Each library is held to guarantees it documents, never to
the other's predictions:

  determinism  a fresh 100-round fit (dataset rebuilt) == the main model's first 100 trees, bytes
  invariance   a fit on 4*X (a power of two: exact in float32) predicts 4*X_test bit-identically
  monotone     constraining m_bb non-decreasing gives no negative step on a grid, exactly;
               the unconstrained main model must step down on the same grid (the control)
  conservation iteration-0 hessian: sum of tree-0 leaf covers == n * p0 * (1 - p0);
               LightGBM: every tree's leaf counts sum to n_train (no bagging)
  SHAP         sum of TreeSHAP contributions + bias == the raw margin (local accuracy)

AUC is compared to the published Experiments.rst values and reported, not asserted tightly.
"""
import hashlib, json, os, re, resource, sys, time

import numpy as np
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
import sklearn
from sklearn.metrics import roc_auc_score

T = int(os.environ.get("GB_THREADS", "8"))
ROUNDS = int(os.environ.get("GB_ROUNDS", "500"))
SUB = int(os.environ.get("GB_SUB", "100"))
EXPECT_ROWS = int(os.environ.get("GB_EXPECT_ROWS", "11000000"))
N_TEST = int(os.environ.get("GB_TEST", "500000"))
DATA = os.environ.get("GB_DATA", "/tmp/HIGGS.csv.gz")
OUT = os.environ.get("GB_OUT", "/tmp")
SEED = 20261011
SCALE = np.float32(4.0)
MBB = 25            # m_bb: 0-based feature index (column 26 of the CSV, after the label)
GRID, GRID_ROWS, SHAP_ROWS = 200, 500, 1000

FAILED = []
res = open(os.path.join(OUT, "real-results.tsv"), "w")


def rec(key, value, ok=None):
    """Record one line. Every value goes through str() so no formatting can raise."""
    status = "" if ok is None else ("ok" if bool(ok) else "FAIL")
    line = "%s\t%s\t%s" % (str(key), str(value), status)
    print(line, flush=True)
    res.write(line + "\n"); res.flush()
    if ok is not None and not bool(ok):
        FAILED.append(str(key))


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def peak_gib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # Linux: KiB


class clock:
    def __init__(self, name): self.name = name
    def __enter__(self): self.t = time.time(); return self
    def __exit__(self, *a):
        self.s = time.time() - self.t
        rec("time_" + self.name, "%.1f s" % self.s)
        rec("peak_rss_after_" + self.name, "%.2f GiB" % peak_gib())


rec("versions", "xgboost %s lightgbm %s numpy %s pandas %s sklearn %s"
    % (ver(xgb), ver(lgb), ver(np), ver(pd), ver(sklearn)))
rec("threads", T)

# ---- data -------------------------------------------------------------------------------
with clock("load"):
    df = pd.read_csv(DATA, header=None, engine="pyarrow", dtype=np.float32)
    arr = df.to_numpy(dtype=np.float32); del df
rec("rows", arr.shape[0], arr.shape[0] == EXPECT_ROWS)
rec("columns", arr.shape[1], arr.shape[1] == 29)
y = arr[:, 0].copy()
X = np.ascontiguousarray(arr[:, 1:]); del arr
rec("labels_binary", sorted(np.unique(y).tolist()), set(np.unique(y).tolist()) == {0.0, 1.0})
rec("finite", bool(np.isfinite(X).all()), np.isfinite(X).all())
rec("signal_fraction", "%.6f" % y.mean())
rec("X_sha256_16", sha(X))
ntr = X.shape[0] - N_TEST
Xtr, ytr, Xte, yte = X[:ntr], y[:ntr], X[ntr:], y[ntr:]
rec("n_train", ntr); rec("n_test", N_TEST)
Xtr4 = Xtr * SCALE; Xte4 = Xte * SCALE
rec("scaling_exact", "4*X/4 == X", bool(((Xtr4 / SCALE) == Xtr).all()))
lo, hi = np.quantile(Xte[:, MBB], [0.005, 0.995])
grid = np.linspace(lo, hi, GRID, dtype=np.float32)
G = np.repeat(Xte[:GRID_ROWS], GRID, axis=0)
G[:, MBB] = np.tile(grid, GRID_ROWS)


def min_step(margin):
    return float(np.diff(margin.reshape(GRID_ROWS, GRID).astype(np.float64), axis=1).min())


def leaves(node):
    if "leaf_index" in node or "leaf" in node and "children" not in node:
        yield node
        return
    for k in ("left_child", "right_child", "children"):
        v = node.get(k)
        if isinstance(v, dict):
            yield from leaves(v)
        elif isinstance(v, list):
            for c in v:
                yield from leaves(c)


# ---- xgboost ------------------------------------------------------------------------------
XP = dict(objective="binary:logistic", tree_method="hist", eta=0.1, max_depth=0, max_leaves=255,
          grow_policy="lossguide", min_child_weight=100, nthread=T, seed=SEED, verbosity=1)

with clock("xgb_main_fit"):
    dtr = xgb.QuantileDMatrix(Xtr, ytr, nthread=T)
    bx = xgb.train(XP, dtr, ROUNDS)
mx = bx.inplace_predict(Xte, predict_type="margin")
np.save(os.path.join(OUT, "xgb_margin.npy"), mx)
auc_x = roc_auc_score(yte, mx)
rec("xgb_auc", "%.6f (published xgboost_hist 0.845314)" % auc_x, auc_x > 0.80)
rec("xgb_margin_sha256_16", sha(mx))
gain = bx.get_score(importance_type="gain")
rec("xgb_top5_gain", [k for k, _ in sorted(gain.items(), key=lambda kv: -kv[1])[:5]])

cfg = json.loads(bx.save_config())
bs = float(re.findall(r"[-+0-9.eE]+", str(cfg["learner"]["learner_model_param"]["base_score"]))[0])
t0 = json.loads(bx.get_dump(with_stats=True, dump_format="json")[0])
cov = sum(float(l["cover"]) for l in leaves(t0))
want = ntr * bs * (1 - bs)
rec("xgb_tree0_hessian", "sum leaf cover %.6e vs n*p0*(1-p0) %.6e rel %.2e"
    % (cov, want, abs(cov - want) / want), abs(cov - want) / want < 1e-5)

with clock("xgb_shap"):
    cx = bx.predict(xgb.DMatrix(Xte[:SHAP_ROWS]), pred_contribs=True)
d = np.abs(cx.astype(np.float64).sum(axis=1) - mx[:SHAP_ROWS].astype(np.float64))
rec("xgb_shap_local_accuracy", "max|sum-margin| %.3e" % d.max(), d.max() < 1e-3)

with clock("xgb_repeat_fit"):
    del dtr
    dtr = xgb.QuantileDMatrix(Xtr, ytr, nthread=T)
    bxr = xgb.train(XP, dtr, SUB)
a = bx.inplace_predict(Xte, predict_type="margin", iteration_range=(0, SUB))
b = bxr.inplace_predict(Xte, predict_type="margin")
rec("xgb_determinism", "first %d trees: margins %s, dump %s" % (SUB, "identical" if a.tobytes() == b.tobytes() else "DIFFER",
    "identical" if bx.get_dump()[:SUB] == bxr.get_dump() else "DIFFER"),
    a.tobytes() == b.tobytes() and bx.get_dump()[:SUB] == bxr.get_dump())
del bxr, dtr

with clock("xgb_scaled_fit"):
    d4 = xgb.QuantileDMatrix(Xtr4, ytr, nthread=T)
    bx4 = xgb.train(XP, d4, SUB)
c = bx4.inplace_predict(Xte4, predict_type="margin")
rec("xgb_scale_invariance", "max|diff| %.3e, bytes %s" % (float(np.abs(c - a).max()), "identical" if c.tobytes() == a.tobytes() else "DIFFER"),
    c.tobytes() == a.tobytes())
del bx4, d4

with clock("xgb_monotone_fit"):
    mc = tuple(1 if i == MBB else 0 for i in range(X.shape[1]))
    dtr = xgb.QuantileDMatrix(Xtr, ytr, nthread=T)
    bxm = xgb.train(dict(XP, monotone_constraints=str(mc).replace(" ", "")), dtr, SUB)
s_con = min_step(bxm.inplace_predict(G, predict_type="margin"))
s_unc = min_step(bx.inplace_predict(G, predict_type="margin"))
rec("xgb_monotone", "constrained min step %.3e" % s_con, s_con >= 0.0)
rec("xgb_monotone_control", "unconstrained min step %.3e" % s_unc, s_unc < -1e-3)
del bxm, dtr

# ---- LightGBM -----------------------------------------------------------------------------
LP = dict(objective="binary", learning_rate=0.1, num_leaves=255, min_data_in_leaf=0,
          min_sum_hessian_in_leaf=100, num_threads=T, seed=SEED, deterministic=True,
          force_col_wise=True, verbose=0)


def lds(Xa):
    return lgb.Dataset(Xa, ytr, params={"verbose": -1, "num_threads": T}, free_raw_data=True)


with clock("lgb_main_fit"):
    bl = lgb.train(LP, lds(Xtr), ROUNDS)
ml = bl.predict(Xte, raw_score=True)
np.save(os.path.join(OUT, "lgb_margin.npy"), ml)
auc_l = roc_auc_score(yte, ml)
rec("lgb_auc", "%.6f (published LightGBM 0.845724)" % auc_l, auc_l > 0.80)
rec("lgb_margin_sha256_16", sha(ml))
imp = bl.feature_importance(importance_type="gain")
rec("lgb_top5_gain", ["f%d" % i for i in np.argsort(-imp)[:5]])

dm = bl.dump_model()
bad = 0
for t in dm["tree_info"]:
    n = sum(int(l["leaf_count"]) for l in leaves(t["tree_structure"]))
    bad += n != ntr
rec("lgb_leaf_count_conservation", "%d of %d trees sum to n_train" % (len(dm["tree_info"]) - bad, len(dm["tree_info"])),
    bad == 0 and len(dm["tree_info"]) == ROUNDS)
p0 = float(ytr.astype(np.float64).mean())
hw = sum(float(l["leaf_weight"]) for l in leaves(dm["tree_info"][0]["tree_structure"]))
want = ntr * p0 * (1 - p0)
rec("lgb_tree0_hessian", "sum leaf weight %.6e vs n*p0*(1-p0) %.6e rel %.2e"
    % (hw, want, abs(hw - want) / want), abs(hw - want) / want < 1e-5)
del dm

with clock("lgb_shap"):
    cl = bl.predict(Xte[:SHAP_ROWS], pred_contrib=True)
d = np.abs(cl.sum(axis=1) - ml[:SHAP_ROWS])
rec("lgb_shap_local_accuracy", "max|sum-margin| %.3e" % d.max(), d.max() < 1e-9)

with clock("lgb_repeat_fit"):
    blr = lgb.train(LP, lds(Xtr), SUB)
a = bl.predict(Xte, raw_score=True, num_iteration=SUB)
b = blr.predict(Xte, raw_score=True)
rec("lgb_determinism", "first %d trees: margins %s" % (SUB, "identical" if a.tobytes() == b.tobytes() else "DIFFER"),
    a.tobytes() == b.tobytes())
del blr

with clock("lgb_scaled_fit"):
    bl4 = lgb.train(LP, lds(Xtr4), SUB)
c = bl4.predict(Xte4, raw_score=True)
rec("lgb_scale_invariance", "max|diff| %.3e, bytes %s" % (float(np.abs(c - a).max()), "identical" if c.tobytes() == a.tobytes() else "DIFFER"),
    c.tobytes() == a.tobytes())
del bl4

with clock("lgb_monotone_fit"):
    blm = lgb.train(dict(LP, monotone_constraints=list(mc)), lds(Xtr), SUB)
s_con = min_step(blm.predict(G, raw_score=True))
s_unc = min_step(bl.predict(G, raw_score=True))
rec("lgb_monotone", "constrained min step %.3e" % s_con, s_con >= 0.0)
rec("lgb_monotone_control", "unconstrained min step %.3e" % s_unc, s_unc < -1e-3)

rec("peak_rss", "%.2f GiB" % peak_gib())
for p in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
    try:
        rec("cgroup_peak", "%.2f GiB (%s)" % (int(open(p).read()) / 2**30, p))
        break
    except Exception:
        pass
if FAILED:
    rec("FAILED", FAILED)
    sys.exit(1)
print("GB REAL OK", flush=True)
