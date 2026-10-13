#!/usr/bin/env python3
"""Nilearn on Graviton4: four of its main APIs held to closed forms and exact identities.

Nilearn's own test suite is not runnable in this env yet (26 of its 135 test modules import
`polars`/`numpydoc`, which the env lacks -- requested as playgroundlogic/aarchsci#43), so
these checks go straight at the APIs a user calls, each against a statement that holds for
mathematical reasons:

 1. NiftiMasker is a gather/scatter: transform then inverse_transform reproduces every
    in-mask voxel EXACTLY and writes zeros elsewhere.
 2. A first-level GLM under OLS is least squares on its own design matrix, so its effect
    sizes must equal numpy.linalg.lstsq on the same matrix -- a fixed point reached by two
    routes -- and on noise-free data it must recover the planted betas exactly. This needs
    signal_scaling=False: the default rescales each voxel to percent signal change, so the
    effect sizes are not in the data's units.
 3. ConnectivityMeasure(kind="correlation") is Pearson correlation ONLY with an empirical
    covariance estimator. Its default estimator is Ledoit-Wolf shrinkage, so the default
    "correlation" is a shrunk correlation -- which makes the default the control.
 4. signal.clean(detrend=True) projects out the constant and the linear trend: the residual
    is orthogonal to both, and a planted pure trend cleans to zero.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys
import warnings

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


warnings.simplefilter("ignore", category=FutureWarning)
import nibabel as nib
import nilearn
from nilearn.maskers import NiftiMasker
from nilearn.glm.first_level import FirstLevelModel, make_first_level_design_matrix
from nilearn.connectome import ConnectivityMeasure
from nilearn import signal as nsignal
from sklearn.covariance import EmpiricalCovariance

ver("nilearn", lambda: nilearn.__version__)
ver("nibabel", lambda: nib.__version__)
ver("numpy", lambda: np.__version__)
ver("sklearn", lambda: __import__("sklearn").__version__)

SEED = 20261013
rng = np.random.default_rng(SEED)
AFFINE = np.diag([2.0, 2.0, 2.0, 1.0])

# ======================================= 1. NiftiMasker: an exact gather and scatter
shape, T = (8, 9, 10), 50
data = rng.normal(size=shape + (T,))
mask_arr = np.zeros(shape, dtype="int8")
mask_arr[1:7, 2:8, 3:9] = 1
mask_arr[3, 4, 5] = 0                                   # a hole, so the mask is not a box
img = nib.Nifti1Image(data, AFFINE)
mask = nib.Nifti1Image(mask_arr, AFFINE)
masker = NiftiMasker(mask_img=mask, standardize=False, detrend=False)
ts = masker.fit_transform(img)
nvox = int(mask_arr.sum())
rec("masker_shape", "%s for %d in-mask voxels x %d volumes" % (ts.shape, nvox, T))
if ts.shape != (T, nvox):
    die("NiftiMasker returned %s, not (%d, %d)" % (ts.shape, T, nvox))
back = np.asarray(masker.inverse_transform(ts).get_fdata(), dtype="float64")
finite("the reconstructed image", back)
inside = float(np.max(np.abs(back[mask_arr == 1] - data[mask_arr == 1])))
outside = float(np.max(np.abs(back[mask_arr == 0])))
rec("masker_roundtrip", "in-mask max |diff| %.3e, out-of-mask max |value| %.3e" % (inside, outside))
# A gather then a scatter moves no arithmetic, and float64 is kept end to end (measured: the
# round trip is exact), so the claim is equality, not a tolerance.
if inside != 0.0:
    die("NiftiMasker's round trip moved in-mask voxels by %.3e; a gather and scatter is exact" % inside)
if outside != 0.0:
    die("inverse_transform wrote %.3e outside the mask; it must leave zeros" % outside)

# ============ 2. first-level GLM under OLS == least squares on its own design matrix
t_r, n_scans = 2.0, 120
frame_times = np.arange(n_scans) * t_r
import pandas as pd
events = pd.DataFrame({"onset": [10, 40, 70, 100, 130, 160, 190, 220],
                       "duration": [8] * 8,
                       "trial_type": ["faces", "houses"] * 4})
dm = make_first_level_design_matrix(frame_times, events, hrf_model="glover",
                                    drift_model="cosine", high_pass=0.01)
X = dm.to_numpy(dtype="float64")
cols = list(dm.columns)
rec("design_matrix", "%d scans x %d regressors: %s" % (X.shape[0], X.shape[1], ", ".join(cols)))
gshape = (4, 4, 4)
nv = int(np.prod(gshape))
beta_true = rng.normal(size=(X.shape[1], nv))
glm_mask = nib.Nifti1Image(np.ones(gshape, dtype="int8"), AFFINE)


def fit_effects(Y, noise_model="ols"):
    bold = nib.Nifti1Image(Y.T.reshape(gshape + (n_scans,)), AFFINE)
    # no t_r: with a design matrix supplied nilearn ignores it (and says so)
    flm = FirstLevelModel(mask_img=glm_mask, noise_model=noise_model,
                          signal_scaling=False, smoothing_fwhm=None, standardize=False,
                          minimize_memory=False)
    flm.fit(bold, design_matrices=[dm])
    eff = np.empty((X.shape[1], nv))
    for j in range(X.shape[1]):
        c = np.zeros(X.shape[1]); c[j] = 1.0
        eff[j] = np.asarray(flm.compute_contrast(c, output_type="effect_size").get_fdata(),
                            dtype="float64").reshape(nv)
    return eff


# noise-free: the planted betas must come back exactly
eff0 = fit_effects(X @ beta_true)
finite("noise-free effect sizes", eff0)
d0 = float(np.max(np.abs(eff0 - beta_true)))
rec("glm_noise_free", "max |effect - planted beta| = %.3e over %d voxels x %d regressors"
    % (d0, nv, X.shape[1]))
# One least-squares problem: the bound is the design's conditioning times machine epsilon, and
# 1e-10 leaves orders of headroom over the measured 7e-15 while failing any real miss.
if d0 > 1e-10:
    die("on noise-free data the OLS GLM missed the planted betas by %.3e; with a full-rank "
        "design they are the exact least-squares solution" % d0)

# noisy: OLS effect sizes must equal numpy's least squares on the SAME matrix
Yn = X @ beta_true + rng.normal(scale=0.5, size=(n_scans, nv))
eff_ols = fit_effects(Yn)
lst = np.linalg.lstsq(X, Yn, rcond=None)[0]
finite("noisy effect sizes", eff_ols, lst)
d_ols = float(np.max(np.abs(eff_ols - lst)))
rec("glm_ols_vs_lstsq", "max |FirstLevelModel(ols) - numpy lstsq| = %.3e" % d_ols)
# Same matrix, same objective, two codes (nilearn's pinv-based OLS vs LAPACK gelsd): a fixed
# point, so the gap is round-off -- bounded as above.
if d_ols > 1e-10:
    die("the OLS GLM and numpy's least squares disagree by %.3e on one design matrix" % d_ols)

# THE CONTROL: an AR(1) noise model is generalised least squares, a different estimator.
eff_ar = fit_effects(Yn, noise_model="ar1")
d_ar = float(np.max(np.abs(eff_ar - lst)))
rec("control_ar1", "AR(1) effects differ from OLS by %.3e" % d_ar)
if d_ar < 1e-3:
    die("the AR(1) fit is within %.3e of OLS, so the OLS comparison does not show it could "
        "tell estimators apart" % d_ar)

# ==== 3. ConnectivityMeasure: Pearson correlation only with an empirical covariance
n_t, n_reg = 200, 12
mix = rng.normal(size=(n_reg, n_reg))
series = rng.normal(size=(n_t, n_reg)) @ mix
pearson = np.corrcoef(series, rowvar=False)
emp = ConnectivityMeasure(kind="correlation", cov_estimator=EmpiricalCovariance(),
                          standardize=False).fit_transform([series])[0]
finite("the empirical correlation", emp)
d_emp = float(np.max(np.abs(emp - pearson)))
rec("connectivity_empirical", "kind='correlation' + EmpiricalCovariance vs np.corrcoef: %.3e"
    % d_emp)
if d_emp > 1e-12:
    die("correlation with an empirical covariance estimator is %.3e from Pearson" % d_emp)

# THE CONTROL is the DEFAULT: Ledoit-Wolf shrinkage, so not Pearson.
dflt = ConnectivityMeasure(kind="correlation", standardize=False).fit_transform([series])[0]
d_dflt = float(np.max(np.abs(dflt - pearson)))
off = ~np.eye(n_reg, dtype=bool)
shrink = float(np.mean(np.abs(dflt[off])) / np.mean(np.abs(pearson[off])))
rec("control_default_estimator", "default (Ledoit-Wolf) differs from Pearson by up to %.3e; "
    "off-diagonals shrunk to %.3f of Pearson on average" % (d_dflt, shrink))
if d_dflt < 1e-4:
    die("the default estimator reproduced Pearson to %.3e, so this check cannot tell a "
        "shrunk correlation from a plain one" % d_dflt)
if not shrink < 1.0:
    die("the default 'correlation' is not smaller in magnitude than Pearson (%.3f); "
        "Ledoit-Wolf shrinks toward the identity" % shrink)

# ============ 4. signal.clean(detrend=True) projects out the constant and the trend
tt = np.arange(300, dtype="float64")
basis = np.column_stack([np.ones_like(tt), tt])
sig = rng.normal(size=(300, 5)) + 3.0 + 0.02 * tt[:, None]
cl = nsignal.clean(sig, detrend=True, standardize=False)
finite("the cleaned signal", cl)
orth = float(np.max(np.abs(basis.T @ cl)) / np.max(np.abs(basis.T @ sig)))
rec("clean_orthogonal", "max |[1,t]' residual| relative to the input's: %.3e" % orth)
if orth > 1e-10:
    die("detrended residuals are not orthogonal to the constant and the trend (%.3e)" % orth)
pure = 7.0 - 0.3 * tt[:, None] * np.ones((1, 3))
zero = float(np.max(np.abs(nsignal.clean(pure, detrend=True, standardize=False))))
rec("clean_pure_trend", "a planted pure linear trend cleans to max |value| %.3e" % zero)
if zero > 1e-9:
    die("a pure linear trend left %.3e after detrending; it should vanish" % zero)

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k_, v in ROWS:
        fh.write("%s\t%s\n" % (k_, v))

print("NILEARN IDENTITIES OK")
