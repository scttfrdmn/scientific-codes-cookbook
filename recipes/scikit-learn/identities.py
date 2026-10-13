#!/usr/bin/env python3
"""scikit-learn on Graviton4: closed forms, a cross-code MLE, and planted truth.

The task first runs scikit-learn's OWN test suite (the verdict). These checks are the part a
reader can lift into their own work: each asserts a claim that holds for mathematical
reasons, so none is a band on an observed number.

 1. LOGISTIC REGRESSION IS AN MLE, so it is a FIXED POINT -- two unrelated codes must land on
    it, and the score equation X'(y - p) = 0 must hold there. sklearn's unpenalised fit is
    checked against statsmodels' Newton-Raphson Logit. The control is the default L2
    penalty, which must SHRINK the estimate: for a ridge penalty ||beta_lambda|| is
    non-increasing in lambda, so the penalised norm cannot exceed the MLE's.

 2. PCA IS AN EIGENDECOMPOSITION of the sample covariance. sklearn computes it by SVD of the
    centred data; numpy's eigvalsh takes the symmetric-eigensolver route -- two LAPACK paths
    to one answer. And the truncated reconstruction error is not a tolerance either: the
    residual sum of squares / (n - 1) EQUALS the sum of the discarded eigenvalues.

 3. KMEANS ON PLANTED CLUSTERS recovers them exactly -- adjusted Rand index 1.0, not
    "high" -- and the control is the same centres with heavy overlap, where it cannot.

A pinned digest does not pin the numerics (OpenBLAS picks its kernel from the host CPU), so
every assertion here is on a fixed point or an exact identity, never on a path.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


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


import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score
import statsmodels.api as sm

ver("sklearn", lambda: sklearn.__version__)
ver("statsmodels", lambda: sm.__version__ if hasattr(sm, "__version__")
    else __import__("statsmodels").__version__)
ver("numpy", lambda: np.__version__)
try:
    from threadpoolctl import threadpool_info
    rec("blas", ", ".join("%s %s %s" % (i.get("internal_api"), i.get("version"),
                                         i.get("architecture")) for i in threadpool_info()
                          if i.get("user_api") == "blas"))
except Exception as exc:
    rec("blas", "unknown (%s)" % type(exc).__name__)

SEED = 20261013

# ============== 1. logistic regression: the MLE is a fixed point two codes must share
rng = np.random.default_rng(SEED)
n, p = 5000, 5
X = rng.normal(size=(n, p))
beta_true = np.array([0.8, -1.2, 0.5, 0.0, 2.0])
intercept_true = -0.3
logit = intercept_true + X @ beta_true
y = (rng.random(n) < 1.0 / (1.0 + np.exp(-logit))).astype("float64")
rec("logistic_data", "n=%d p=%d, %d positives (planted beta %s, intercept %.1f)"
    % (n, p, int(y.sum()), np.array2string(beta_true), intercept_true))

# C=np.inf, not penalty=None: penalty= is deprecated since 1.8 and removed in 1.10 (this pin
# warns), and an unpenalised fit IS the C -> infinity limit of the L2 objective.
sk = LogisticRegression(C=np.inf, solver="newton-cholesky", tol=1e-12, max_iter=1000)
sk.fit(X, y)
b_sk = np.concatenate([sk.intercept_, sk.coef_.ravel()])
sm_fit = sm.Logit(y, sm.add_constant(X)).fit(method="newton", tol=1e-12, maxiter=1000,
                                              disp=False)
b_sm = np.asarray(sm_fit.params, dtype="float64")
finite("the two MLEs", b_sk, b_sm)
d_mle = float(np.max(np.abs(b_sk - b_sm)))
rec("mle_sklearn", np.array2string(b_sk, precision=10))
rec("mle_statsmodels", np.array2string(b_sm, precision=10))
rec("mle_max_abs_diff", "%.3e" % d_mle)

# The score equation, which defines the MLE: the gradient of the log-likelihood vanishes.
Xa = np.column_stack([np.ones(n), X])
pr = 1.0 / (1.0 + np.exp(-(Xa @ b_sk)))
grad = Xa.T @ (y - pr)
gnorm = float(np.max(np.abs(grad)))
rec("score_equation", "max |X'(y - p)| = %.3e at sklearn's solution (n = %d)" % (gnorm, n))
# Both bounds are set by the solvers' tolerance (1e-12) and the problem's conditioning, and
# are loose against it: Newton converges quadratically, so a correct fit is orders inside.
if gnorm > 1e-6:
    die("the log-likelihood gradient at sklearn's solution is %.3e; an unpenalised logistic "
        "fit is the point where it vanishes" % gnorm)
if d_mle > 1e-6:
    die("sklearn and statsmodels reach MLEs %.3e apart; the MLE is a fixed point, so two "
        "correct Newton solvers must agree" % d_mle)

# THE CONTROL: the default L2 penalty must move the estimate, and only toward zero.
pen = LogisticRegression(C=1e-2, solver="lbfgs", tol=1e-10, max_iter=10000).fit(X, y)
b_pen = pen.coef_.ravel()
finite("the penalised fit", b_pen)
n_mle, n_pen = float(np.linalg.norm(b_sk[1:])), float(np.linalg.norm(b_pen))
rec("control_l2_shrinkage", "||beta|| MLE %.6f vs C=0.01 penalised %.6f (moved %.3e)"
    % (n_mle, n_pen, float(np.max(np.abs(b_pen - b_sk[1:])))))
if not n_pen < n_mle:
    die("the L2-penalised coefficient norm (%.6f) is not below the MLE's (%.6f); a ridge "
        "penalty can only shrink it" % (n_pen, n_mle))
if float(np.max(np.abs(b_pen - b_sk[1:]))) < 1e-3:
    die("the penalised fit moved the coefficients by less than 1e-3, so this control does "
        "not show the cross-check above could detect a different estimator")

# ====================== 2. PCA: two LAPACK routes, and an exact reconstruction identity
rng = np.random.default_rng(SEED + 1)
m, k = 600, 20
A = rng.normal(size=(m, k)) @ rng.normal(size=(k, k))     # correlated, full rank
pca = PCA(svd_solver="full").fit(A)
ev_sk = np.asarray(pca.explained_variance_, dtype="float64")
ev_eig = np.sort(np.linalg.eigvalsh(np.cov(A, rowvar=False)))[::-1]
finite("explained variance", ev_sk, ev_eig)
d_ev = float(np.max(np.abs(ev_sk - ev_eig) / ev_eig))
rec("pca_eigenvalues", "sklearn (SVD) vs numpy eigvalsh: max rel diff %.3e over %d components"
    % (d_ev, k))
if d_ev > 1e-10:
    die("PCA's explained variance is %.3e from the covariance eigenvalues; both are the same "
        "decomposition by different LAPACK routes" % d_ev)

W = np.asarray(pca.components_, dtype="float64")
orth = float(np.max(np.abs(W @ W.T - np.eye(k))))
rec("pca_orthonormal", "max |W W' - I| = %.3e" % orth)
if orth > 1e-12:
    die("PCA components are not orthonormal (%.3e)" % orth)

full = float(np.max(np.abs(pca.inverse_transform(pca.transform(A)) - A)))
rec("pca_full_reconstruction", "max |X - X_hat| = %.3e with all %d components" % (full, k))
if full > 1e-9:
    die("reconstructing from every component lost %.3e; it is a rotation" % full)

# The identity that makes truncation checkable: RSS / (n - 1) == sum of dropped eigenvalues.
for keep in (5, 10, 15):
    pk = PCA(n_components=keep, svd_solver="full").fit(A)
    resid = A - pk.inverse_transform(pk.transform(A))
    lhs = float((resid ** 2).sum() / (m - 1))
    rhs = float(ev_eig[keep:].sum())
    rel = abs(lhs - rhs) / rhs
    rec("pca_truncation_%d" % keep, "RSS/(n-1) %.10f  dropped eigenvalues %.10f  rel %.3e"
        % (lhs, rhs, rel))
    if rel > 1e-9:
        die("keeping %d components, RSS/(n-1) = %.10f but the dropped eigenvalues sum to "
            "%.10f; the identity is exact" % (keep, lhs, rhs))

# ============================== 3. KMeans recovers planted clusters exactly -- or not
rng = np.random.default_rng(SEED + 2)
centres = np.array([[0, 0], [10, 0], [0, 10], [10, 10]], dtype="float64")
truth = np.repeat(np.arange(4), 250)


def blobs(std):
    return centres[truth] + rng.normal(scale=std, size=(len(truth), 2))


for std, label in ((0.5, "separated"), (6.0, "overlapping")):
    pts = blobs(std)
    km = KMeans(n_clusters=4, n_init=10, random_state=SEED).fit(pts)
    ari = float(adjusted_rand_score(truth, km.labels_))
    rec("kmeans_%s" % label, "std %.1f between centres 10 apart: ARI %.6f" % (std, ari))
    if label == "separated" and ari != 1.0:
        die("KMeans recovered well-separated planted clusters with ARI %.6f, not 1.0; at "
            "std 0.5 between centres 10 apart no point is closer to another centre" % ari)
    if label == "overlapping" and not ari < 0.9:
        die("KMeans scored ARI %.6f on heavily overlapping clusters; then ARI 1.0 above does "
            "not show this check can tell recovery from failure" % ari)

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k_, v in ROWS:
        fh.write("%s\t%s\n" % (k_, v))

print("SKLEARN IDENTITIES OK")
