#!/usr/bin/env python3
"""Cross-chip comparison of measurements/neuroimaging-real: Intel and AMD each against Graviton4.

Reads /tmp/<arch>/{dipy-dipy-maps.tar, dipy-results.tsv, fsl-dtifit.tar} for arm64, x86_64, amd.
Every quantity here is a FIXED POINT -- the median-Otsu mask is a filter plus a histogram threshold,
and OLS and DIPY's WLS are closed-form linear solves (WLS is one reweighting, not an iteration) -- so
the chips must agree up to rounding, and that is asserted. Exit 1 if any assertion fails.
"""
import gzip, io, sys, tarfile
import numpy as np
import nibabel as nib

A = ("arm64", "x86_64", "amd")
bad = []


def check(name, ok, obs):
    print("  %-56s %s  %s" % (name, "PASS" if bool(ok) else "FAIL", obs))
    if not bool(ok):
        bad.append(name)


def members(path):
    with tarfile.open(path) as t:
        for m in t:
            if m.isfile():
                yield m.name.lstrip("./"), t.extractfile(m).read()


def dipy_maps(arch):
    return {n[:-4]: dict(np.load(io.BytesIO(b))) for n, b in members("/tmp/%s/dipy-dipy-maps.tar" % arch)}


def fsl_maps(arch):
    out = {}
    for n, b in members("/tmp/%s/fsl-dtifit.tar" % arch):
        sid, f = n.split("/")
        out.setdefault(sid, {})[f] = b
    return out


def img(b):
    return np.asarray(nib.Nifti1Image.from_bytes(gzip.decompress(b)).dataobj, dtype=np.float64)


ref_d, ref_f = dipy_maps("arm64"), fsl_maps("arm64")
print("subjects: arm64 DIPY %d, dtifit %d" % (len(ref_d), len(ref_f)))
for arch in A[1:]:
    print("\n== %s vs arm64 ==" % arch)
    d, fm = dipy_maps(arch), fsl_maps(arch)
    check("same subjects (DIPY)", sorted(d) == sorted(ref_d), "%d / %d" % (len(d), len(ref_d)))
    check("same subjects (dtifit)", sorted(fm) == sorted(ref_f), "%d / %d" % (len(fm), len(ref_f)))
    common = sorted(set(d) & set(ref_d))
    mask_eq = sum(np.array_equal(d[s]["mask"], ref_d[s]["mask"]) for s in common)
    check("median-Otsu brain mask identical, every subject", mask_eq == len(common), "%d / %d" % (mask_eq, len(common)))
    same = [s for s in common if np.array_equal(d[s]["mask"], ref_d[s]["mask"])]
    # The cross-chip difference is ABSOLUTE in diffusivity (a few ulps of log S through a fit of
    # condition ~3e3), so it is asserted in mm^2/s for MD, and for FA -- a ratio, which amplifies it
    # where diffusivity is near zero -- in voxels with MD >= 1e-4 mm^2/s (all tissue and CSF). Both
    # are also reported over every voxel, with the MD of the voxel where FA moved most.
    for fit in ("ols", "wls"):
        fa, md = "fa_" + fit, "md_" + fit
        ident = sum(np.array_equal(d[s][fa], ref_d[s][fa]) for s in same)
        dmd = max(float(np.max(np.abs(d[s][md] - ref_d[s][md]))) for s in same)
        tis, alls, at = 0.0, 0.0, 0.0
        for s in same:
            e = np.abs(d[s][fa] - ref_d[s][fa])
            t = ref_d[s][md] >= 1e-4
            tis = max(tis, float(e[t].max()))
            i = int(np.argmax(e))
            if e[i] > alls:
                alls, at = float(e[i]), float(ref_d[s][md][i])
        check("DIPY %s MD max |diff| < 1e-12 mm^2/s" % fit.upper(), dmd < 1e-12, "%.3e" % dmd)
        check("DIPY %s FA where MD >= 1e-4, max |diff| < 1e-9" % fit.upper(), tis < 1e-9,
              "%.3e  (every voxel: %.3e, at MD %.2e; %d / %d maps byte-identical)" % (tis, alls, at, ident, len(same)))
    # dtifit writes float32, so its rounding floor is float32's, not float64's
    fc = sorted(set(fm) & set(ref_f))
    for k, bound in (("pos.nii.gz", 0), ("dti_FA.nii.gz", 1e-6), ("dti_MD.nii.gz", 1e-6),
                     ("dti_L1.nii.gz", 1e-6), ("dti_L2.nii.gz", 1e-6), ("dti_L3.nii.gz", 1e-6)):
        ident = sum(fm[s][k] == ref_f[s][k] for s in fc)
        worst = 0.0
        for s in fc:
            if fm[s][k] != ref_f[s][k]:
                a, b = img(fm[s][k]), img(ref_f[s][k])
                scale = 1.0 if k in ("pos.nii.gz", "dti_FA.nii.gz") else max(float(np.abs(b).max()), 1e-30)
                worst = max(worst, float(np.max(np.abs(a - b))) / scale)
        check("dtifit %s max %s <= %g" % (k[:-7], "|diff|" if k in ("pos.nii.gz", "dti_FA.nii.gz") else "|diff|/max", bound),
              worst <= bound, "%.3e  (%d / %d files byte-identical)" % (worst, ident, len(fc)))
print("\n== amd vs x86_64 (the same x86 build on two vendors' cores) ==")
dx, da = dipy_maps("x86_64"), dipy_maps("amd")
fx, fa_ = fsl_maps("x86_64"), fsl_maps("amd")
nd = sum(all(np.array_equal(dx[s][k], da[s][k]) for k in dx[s]) for s in dx if s in da)
nf = sum(fx[s] == fa_.get(s) for s in fx)
print("  DIPY maps byte-identical: %d / %d subjects;  dtifit outputs byte-identical: %d / %d" % (nd, len(dx), nf, len(fx)))
for b in bad:
    print("FAIL: " + b)
sys.exit(1 if bad else 0)
