"""LightGBM estimates per-bin counts from hessians (count ~ hess * n / H) and applies min_data_in_leaf
to the estimate. Does LightGBM's stability survive removing that, i.e. min_data_in_leaf=1?"""
import sys
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss
from sklearn.ensemble._hist_gradient_boosting.utils import get_equivalent_estimator

raw = pd.read_csv(sys.argv[1], header=None).to_numpy()
idx = np.random.default_rng(0).permutation(len(raw))[:20000]
X, y = raw[idx, :10].astype(np.float64), raw[idx, 54]
BASE = dict(max_iter=200, early_stopping=False, random_state=0)
EV = (1, 25, 50, 100, 150, 200)


def show(name, tr):
    best = min(tr, key=tr.get)
    print("%-52s %s | min %.4f@%d final/min %.2f" % (name, " ".join("%d:%.4f" % (k, tr[k]) for k in EV), tr[best], best, tr[200] / tr[best]), flush=True)


for msl in (20, 5, 1):
    lg = get_equivalent_estimator(HistGradientBoostingClassifier(**dict(BASE, min_samples_leaf=msl)), lib="lightgbm", n_classes=7)
    lg.set_params(n_jobs=8, random_state=0, deterministic=True, force_row_wise=True, min_sum_hessian_in_leaf=1e-3 * 7 / 6)
    lg.fit(X, y)
    show("lightgbm equivalent, min_data_in_leaf=%d" % msl, {k: log_loss(y, lg.predict_proba(X, num_iteration=k), labels=lg.classes_) for k in range(1, 201)})
    m = HistGradientBoostingClassifier(**dict(BASE, min_samples_leaf=msl)).fit(X, y)
    show("sklearn, min_samples_leaf=%d" % msl, {k + 1: log_loss(y, p, labels=m.classes_) for k, p in enumerate(m.staged_predict_proba(X))})
print("LGB COUNT OK", flush=True)
