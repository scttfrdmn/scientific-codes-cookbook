#!/usr/bin/env python3
"""DIPY against a constructed truth, and AFNI against nibabel on identical bytes.

Nothing is staged but this file: every signal is synthesised from a tensor whose FA has a
closed form, so the answer is known before either tool runs. Run with `python3 -u`.
"""
import math
import os
import subprocess
import sys

import numpy as np
import nibabel as nib

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def fail(msg):
    sys.exit("FAIL: %s" % msg)


import dipy                                                  # noqa: E402
from dipy.core.gradients import gradient_table                # noqa: E402
from dipy.core.sphere import HemiSphere, disperse_charges      # noqa: E402
from dipy.reconst.dti import TensorModel                       # noqa: E402
from dipy.sims.voxel import single_tensor                      # noqa: E402

rec("dipy_version", getattr(dipy, "__version__", "unknown"))
rec("nibabel_version", getattr(nib, "__version__", "unknown"))
rec("numpy_version", getattr(np, "__version__", "unknown"))

AFNI_V = subprocess.run(["afni", "-ver"], capture_output=True, text=True)
ver = (AFNI_V.stdout or AFNI_V.stderr).strip()
rec("afni_version_line", ver[:90])
if "AFNI_25.0.00" not in ver:
    fail("AFNI reports %r, this recipe's numbers were taken on AFNI_25.0.00" % ver[:60])

# =============================================================================================
# A fixed 64-direction scheme plus a b=0. Deterministic: the seeds are fixed and
# disperse_charges is a deterministic relaxation, so the gradient table is the same every run.
# =============================================================================================
N = 64
theta = np.pi * np.random.default_rng(1).random(N)
phi = 2 * np.pi * np.random.default_rng(2).random(N)
hsph, _ = disperse_charges(HemiSphere(theta=theta, phi=phi), 100)
bvecs = np.vstack([[0, 0, 0], hsph.vertices])
bvals = np.hstack([0, np.full(N, 1000.0)])
gtab = gradient_table(bvals, bvecs=bvecs)
S0 = 100.0
rec("gradient_directions", N)


def analytic_fa(evals):
    """FA has a closed form in the eigenvalues, so the truth needs no reference dataset."""
    l = np.asarray(evals, dtype="float64")
    md = l.mean()
    return math.sqrt(1.5 * ((l - md) ** 2).sum() / (l ** 2).sum())


def fit_fa_md(evals):
    sig = single_tensor(gtab, S0=S0, evals=np.asarray(evals), evecs=np.eye(3), snr=None)
    vol = np.tile(sig, (2, 2, 2, 1)).astype("float64")
    f = TensorModel(gtab).fit(vol)
    return np.asarray(f.fa), np.asarray(f.md), sig

# =============================================================================================
# 1. CONSTRUCTED TRUTH. Build the signal from a tensor whose FA and MD are known in closed
#    form, then fit it back. Noise-free, so this is not a statistical claim -- a correct
#    estimator must return the generating parameters to solver precision.
# =============================================================================================
EVALS = (1.5e-3, 0.4e-3, 0.4e-3)
fa_t = analytic_fa(EVALS)
md_t = float(np.mean(EVALS))
rec("tensor_evals", ", ".join("%.4e" % v for v in EVALS))
rec("analytic_fa", "%.12f" % fa_t)
rec("analytic_md", "%.12e" % md_t)

fa, md, _ = fit_fa_md(EVALS)
e_fa = float(np.abs(fa - fa_t).max())
e_md = float(np.abs(md - md_t).max())
rec("dipy_fa_max_error", "%.3e" % e_fa)
rec("dipy_md_max_error", "%.3e" % e_md)
# Measured at 2.1e-15 and 1.8e-18 -- this is a linear least-squares fit of a noise-free signal,
# so the bound is double-precision conditioning, not estimator variance.
if e_fa > 1e-10:
    fail("DIPY's FA is %.3e from the analytic value %.12f" % (e_fa, fa_t))
if e_md > 1e-12:
    fail("DIPY's MD is %.3e from the analytic value %.12e" % (e_md, md_t))
rec("identity_constructed_truth", "DIPY recovers the generating tensor to %.1e" % e_fa)

# =============================================================================================
# 2. AN EXACT IDENTITY. An isotropic tensor has FA exactly zero, by the definition of FA --
#    the numerator is the variance of the eigenvalues. No reference value, no tolerance beyond
#    arithmetic, and an implementation that normalised wrongly could not satisfy it.
# =============================================================================================
fa_i, md_i, _ = fit_fa_md((0.7e-3, 0.7e-3, 0.7e-3))
e_iso = float(np.abs(fa_i).max())
rec("isotropic_fa_max", "%.3e" % e_iso)
if e_iso > 1e-9:
    fail("an isotropic tensor returned FA %.3e, which should be exactly 0" % e_iso)
rec("identity_isotropic", "FA of an isotropic tensor is 0 to %.1e" % e_iso)

# Monotonicity is free here and catches a sign or normalisation error the two cases above
# would not: FA must increase as the tensor becomes more anisotropic.
prev, mono = -1.0, True
for l1 in (0.8e-3, 1.0e-3, 1.4e-3, 2.0e-3):
    f, _, _ = fit_fa_md((l1, 0.4e-3, 0.4e-3))
    v = float(f.mean())
    if v <= prev:
        mono = False
    prev = v
rec("fa_monotone_in_anisotropy", "yes" if mono else "NO")
if not mono:
    fail("FA did not increase monotonically with anisotropy")

# =============================================================================================
# 3. AFNI AND nibabel MUST AGREE ON WHAT THE VOXELS ARE, on identical bytes. This is the
#    cross-implementation check, and it is not the DTI comparison this recipe was first designed
#    around: `3dDWItoDT` is absent from this build (see the page), so AFNI cannot fit tensors
#    here. What it can do is read the same NIfTI, which is where pipelines silently diverge.
# =============================================================================================
os.makedirs("/tmp/nii", exist_ok=True)
rng = np.random.default_rng(7)


def afni_tstat_mean(path, shape):
    r = subprocess.run(["3dTstat", "-mean", "-prefix", "/tmp/nii/m.nii.gz", "-overwrite", path],
                       capture_output=True, text=True)
    if not os.path.exists("/tmp/nii/m.nii.gz"):
        fail("3dTstat produced nothing: %s" % (r.stderr or r.stdout)[:200])
    a = np.asarray(nib.load("/tmp/nii/m.nii.gz").dataobj).astype("float64")
    os.remove("/tmp/nii/m.nii.gz")
    return a.reshape(shape)


vol = (rng.random((4, 5, 6, 9)) * 1000).astype("float32")
nib.save(nib.Nifti1Image(vol, np.eye(4)), "/tmp/nii/plain.nii.gz")
ours = vol.astype("float64").mean(axis=3)
theirs = afni_tstat_mean("/tmp/nii/plain.nii.gz", ours.shape)
rel = float(np.abs(ours - theirs).max() / ours.max())
rec("afni_vs_numpy_mean_relative", "%.3e" % rel)
# AFNI writes float32, so float32 epsilon (1.19e-07) is the floor. Measured 1.03e-07.
if rel > 1e-6:
    fail("3dTstat and numpy disagree by %.3e relative -- more than float32 rounding" % rel)
rec("identity_afni_reads_the_same_values", "agree to %.1e relative, float32 precision" % rel)

# ---- the discriminator: scaled integer data, which is how scanner data actually arrives ----
raw = rng.integers(0, 4000, size=(4, 5, 6, 9)).astype("int16")
img = nib.Nifti1Image(raw, np.eye(4))
img.header.set_slope_inter(0.25, 10.0)
nib.save(img, "/tmp/nii/scaled.nii.gz")
back = nib.load("/tmp/nii/scaled.nii.gz")
ours_s = back.get_fdata().mean(axis=3)
theirs_s = afni_tstat_mean("/tmp/nii/scaled.nii.gz", ours_s.shape)
agree = float(np.abs(ours_s - theirs_s).max())
raw_mean = raw.astype("float64").mean(axis=3)
ignored = float(np.abs(raw_mean - theirs_s).max())
rec("scaled_agreement_abs", "%.3e" % agree)
rec("scaled_if_slope_ignored_abs", "%.3e" % ignored)
rec("scaled_discrimination_ratio", "%.3e" % (ignored / max(agree, 1e-300)))
# THE VACUITY GUARD. Agreement means nothing unless disagreement was possible: if both tools
# ignored the scaling they would also agree. So the alternative is measured too, and it must be
# orders away. Measured: 3.4e-05 agreement against 2.2e+03 for the ignore-it reading.
if agree > 1e-3:
    fail("AFNI and nibabel differ by %.3e on scaled data" % agree)
if ignored < 1e3 * agree:
    fail("the ignore-the-slope reading is only %.1fx away -- the agreement is not discriminating"
         % (ignored / max(agree, 1e-300)))
rec("identity_scaling_applied",
    "both apply scl_slope/scl_inter; ignoring it would be %.0fx further off" % (ignored / agree))

# ---- and 3dcalc's arithmetic is exact, not merely close ----
subprocess.run(["3dcalc", "-a", "/tmp/nii/plain.nii.gz", "-expr", "a*2+1",
                "-prefix", "/tmp/nii/c.nii.gz", "-overwrite"], capture_output=True, text=True)
c = np.asarray(nib.load("/tmp/nii/c.nii.gz").dataobj).astype("float64")
d = float(np.abs(c - (vol.astype("float64") * 2 + 1)).max())
rec("afni_3dcalc_vs_numpy_abs", "%.3e" % d)
if d != 0.0:
    fail("3dcalc a*2+1 differs from numpy by %.3e; it was exactly 0 when measured" % d)
rec("identity_3dcalc_exact", "3dcalc a*2+1 matches numpy with zero difference")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in out.items():
        fh.write("%s\t%s\n" % (k, v))
print("NEUROIMAGING OK")
