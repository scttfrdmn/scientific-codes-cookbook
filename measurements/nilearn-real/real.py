"""nilearn-real: ABIDE I Preprocessed, all 871 quality-checked subjects, through the analysis
nilearn's ABIDE examples and Abraham et al. 2017 (NeuroImage 147:736) run -- CC200 region signals,
connectomes, the tangent embedding, and a cross-validated autism-vs-control classifier -- with the
recipe's identities asserted at that scale.

    python3 real.py <data dir> <out dir> <workers>

Every check is asserted here; a failure exits 1 before the sentinel is written.
"""
import csv
import hashlib
import os
import sys
import time
import warnings

import numpy as np

T_START = time.time()
DATA, OUT, W = sys.argv[1], sys.argv[2], int(sys.argv[3])
STRIDE = int(sys.argv[4]) if len(sys.argv) > 4 else 1   # > 1 for sizing only: every k-th subject
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "real.log"), "w")
SCORE = []
FAILED = []


def say(*a):
    s = " ".join(x if isinstance(x, str) else str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n"); LOG.flush()


def rec(name, value, ok=None, bound=""):
    """One claim. ok=None is reported, not asserted. A NaN fails any bound (nan > tol is False)."""
    v = value if isinstance(value, str) else str(value)
    if ok is None:
        status = "report"
    else:
        status = "ok" if bool(ok) else "FAIL"
        if not bool(ok):
            FAILED.append(name)
    SCORE.append((name, v, str(bound), status))
    say(f"  [{status}] {name}: {v}" + (f"  (bound {bound})" if bound else ""))


def below(x, tol):
    x = float(x)
    return np.isfinite(x) and x < tol


PHASE = {}


def phase(name, t0):
    PHASE[name] = time.time() - t0
    say(f"== phase {name}: {PHASE[name]:.1f} s")


def ver(m):
    try:
        mod = __import__(m)
        return str(getattr(mod, "__version__", "unknown"))
    except Exception as exc:  # describing the run must never fail it
        return "unavailable (%s)" % type(exc).__name__


for m in ("nilearn", "nibabel", "numpy", "scipy", "sklearn", "joblib"):
    rec("version " + m, ver(m))

# ---------------------------------------------------------------- the cohort
t0 = time.time()
rows = list(csv.DictReader(open(os.path.join(DATA, "Phenotypic_V1_0b_preprocessed1.csv"), newline="")))
# nilearn.datasets.fetch_abide_pcp(quality_checked=True), the filter verbatim
qc = [r for r in rows if r["FILE_ID"] != "no_filename"
      and r["qc_rater_1"] == "OK" and r["qc_anat_rater_2"] in ("OK", "maybe")
      and r["qc_func_rater_2"] in ("OK", "maybe") and r["qc_anat_rater_3"] == "OK"
      and r["qc_func_rater_3"] == "OK"]
qc.sort(key=lambda r: r["FILE_ID"])
ids = [r["FILE_ID"] for r in qc]
rec("cohort: quality-checked subjects", len(ids), len(ids) == 871, "== 871 (fetch_abide_pcp)")
rec("cohort: FILE_IDs unique", len(set(ids)) == len(ids), len(set(ids)) == len(ids))
rec("cohort: SUB_IDs unique", len({r["SUB_ID"] for r in qc}) == len(ids), len({r["SUB_ID"] for r in qc}) == len(ids))
dx = np.array([int(r["DX_GROUP"]) for r in qc])           # 1 = autism, 2 = control
site = np.array([r["SITE_ID"] for r in qc])
rec("cohort: autism / control", f"{(dx == 1).sum()} / {(dx == 2).sum()}", set(dx) == {1, 2})
rec("cohort: sites", len(set(site)), len(set(site)) == 20, "== 20")
if STRIDE > 1:
    qc = qc[::STRIDE]
    ids = [r["FILE_ID"] for r in qc]
    dx = np.array([int(r["DX_GROUP"]) for r in qc])
    site = np.array([r["SITE_ID"] for r in qc])
    rec("SIZING SUBSET, not a result: every k-th subject", f"k = {STRIDE}, {len(ids)} subjects")
missing = [i for i in ids if not all(os.path.exists(p) for p in (
    f"{DATA}/func_preproc/{i}_func_preproc.nii.gz", f"{DATA}/func_mask/{i}_func_mask.nii.gz",
    f"{DATA}/rois_cc200/{i}_rois_cc200.1D"))]
rec("cohort: subjects with all three files staged", len(ids) - len(missing), not missing, "every one")
if missing:
    say("missing:", missing[:10]); sys.exit(1)
phase("cohort", t0)

# which placement of the atlas is the published one: CC200_ROI_labels.csv states each region's
# centre of mass (mm); compare it with the centre computed through the atlas header, and through
# the image header (the placement an index-aligned extraction implies). Reported, not asserted.
import nibabel as _nib  # noqa: E402
_at = _nib.load(os.path.join(DATA, "cc200_roi_atlas.nii.gz"))
_A = np.asarray(_at.dataobj).astype(np.int64)
_fimg = _nib.load(f"{DATA}/func_preproc/{ids[0]}_func_preproc.nii.gz")
_com = {}
for _r in csv.reader(open(os.path.join(DATA, "CC200_ROI_labels.csv"), newline="")):
    if _r and _r[0].strip().isdigit():
        _c = _r[2].strip().strip("()").split(";")
        _com[int(_r[0])] = np.array([float(x) for x in _c])
_dh, _di = [], []
for _k, _c in _com.items():
    _v = np.argwhere(_A == _k).mean(axis=0)
    _dh.append(np.linalg.norm((_at.affine @ np.r_[_v, 1])[:3] - _c))
    _di.append(np.linalg.norm((_fimg.affine @ np.r_[_v, 1])[:3] - _c))
rec("CC200 centres of mass vs the published table: via atlas header, max mm", "%.2f" % max(_dh))
rec("CC200 centres of mass vs the published table: via image header (index-aligned), max mm", "%.2f" % max(_di))

# ---------------------------------------------------------------- per subject, in parallel
from joblib import Parallel, delayed  # noqa: E402

ATLAS = os.path.join(DATA, "cc200_roi_atlas.nii.gz")


def subject(i):
    """Region signals for one subject, and the per-subject identities. Returns plain numbers."""
    import warnings as w
    w.simplefilter("ignore", FutureWarning)
    import nibabel as nib
    from nilearn.maskers import NiftiLabelsMasker, NiftiMasker
    from nilearn.connectome import ConnectivityMeasure
    from nilearn import signal
    from sklearn.covariance import EmpiricalCovariance

    t = time.time()
    img = nib.load(f"{DATA}/func_preproc/{i}_func_preproc.nii.gz")
    X = np.asarray(img.dataobj)
    mk = nib.load(f"{DATA}/func_mask/{i}_func_mask.nii.gz")
    M = np.asarray(mk.dataobj) > 0
    atlas = nib.load(ATLAS)
    A = np.asarray(atlas.dataobj).astype(np.int64)
    out = {"id": i, "T": X.shape[3], "dtype": str(X.dtype), "load_s": time.time() - t}

    # 1. CC200 region means. The atlas is 63x75x61 and its origin sits one voxel off the 61x73x61
    #    image's in x and y, so nilearn resamples it (nearest) onto the image. The hand reference
    #    reads the voxel-to-voxel map from the two affines, requires it to be an integer shift, and
    #    averages each label's voxels directly.
    t = time.time()
    lm = NiftiLabelsMasker(labels_img=atlas, strategy="mean", standardize=False, detrend=False)
    S = lm.fit_transform(img)
    out["masker_s"] = time.time() - t
    v2v = np.linalg.inv(atlas.affine) @ img.affine
    sh = np.rint(v2v[:3, 3]).astype(int)
    out["affine_integer_shift"] = int(np.allclose(v2v[:3, :3], np.eye(3)) and np.allclose(v2v[:3, 3], sh))
    out["shift"] = ",".join(str(x) for x in sh)
    nx, ny, nz, T = X.shape
    Xf = X.reshape(-1, T)
    labs = np.unique(A)[1:]

    def means(Al):
        """per-label mean, per-label n * eps * mean|x| (the float64 summation bound), voxel counts"""
        flat = Al.reshape(-1)
        mu, bd, nv = [], [], []
        for k in labs:
            ix = np.flatnonzero(flat == k)
            v = Xf[ix].astype(np.float64)
            mu.append(v.mean(axis=0) if len(ix) else np.zeros(T))
            bd.append(len(ix) * np.finfo(np.float64).eps * np.abs(v).mean(axis=0) if len(ix) else np.zeros(T))
            nv.append(len(ix))
        return np.stack(mu, 1), np.stack(bd, 1), np.array(nv)

    A_hdr = A[sh[0]:sh[0] + nx, sh[1]:sh[1] + ny, sh[2]:sh[2] + nz]   # where the headers put it
    A_idx = A[:nx, :ny, :nz]                                          # index-aligned, ignoring them
    ref, bd, nv = means(A_hdr)
    out["n_regions"] = S.shape[1]
    out["regions_match"] = int(S.shape == ref.shape)
    out["empty_regions"] = int((nv == 0).sum())
    if S.shape == ref.shape:
        d = np.abs(S.astype(np.float64) - ref)
        out["masker_vs_hand_abs"] = float(d.max())
        out["masker_vs_hand_over_bound"] = float(np.max(d - bd))      # <= 0 means inside the bound
    else:
        out["masker_vs_hand_abs"] = out["masker_vs_hand_over_bound"] = float("nan")
    out["sha_signals"] = hashlib.sha256(np.ascontiguousarray(S, dtype=np.float64).tobytes()).hexdigest()

    # 2. C-PAC's own CC200 series for the same image (C-PAC 2016, a different code). It reproduces a
    #    mean over the INDEX-aligned atlas, not the header-aligned one, so it is compared against that,
    #    to the precision of a float32 mean: the float32 summation bound n * eps32 * mean|x|, one
    #    float32 rounding, and the 6 printed decimals. The 3 mm placement difference against nilearn
    #    is reported as a correlation.
    R = np.loadtxt(f"{DATA}/rois_cc200/{i}_rois_cc200.1D", comments="#", ndmin=2)
    out["cpac_shape_ok"] = int(R.shape == S.shape)
    if R.shape == S.shape:
        Hc, bd64, _ = means(A_idx)
        dc = np.abs(Hc - R)
        bd32 = bd64 * (np.finfo(np.float32).eps / np.finfo(np.float64).eps)
        tol = bd32 + np.spacing(np.abs(R).astype(np.float32)).astype(np.float64) + 5e-7
        out["cpac_vs_idx_abs"] = float(dc.max())
        out["cpac_vs_idx_over_bound"] = float(np.max(dc - tol))
        out["cpac_vs_nilearn_abs"] = float(np.abs(R - S).max())
        live = (R.std(0) > 0) & (S.std(0) > 0)
        rc = [np.corrcoef(R[:, j], S[:, j])[0, 1] for j in np.flatnonzero(live)]
        out["cpac_vs_nilearn_r_median"] = float(np.median(rc))
        out["cpac_vs_nilearn_r_min"] = float(np.min(rc))
    else:
        for k in ("cpac_vs_idx_abs", "cpac_vs_idx_over_bound", "cpac_vs_nilearn_abs",
                  "cpac_vs_nilearn_r_median", "cpac_vs_nilearn_r_min"):
            out[k] = float("nan")

    # 3. NiftiMasker round trip on the subject's own brain mask
    t = time.time()
    nm = NiftiMasker(mask_img=mk, standardize=False, detrend=False)
    Y = nm.fit_transform(img)
    back = np.asarray(nm.inverse_transform(Y).dataobj)
    out["roundtrip_in"] = float(np.abs(back[M] - X[M]).max())
    out["roundtrip_out"] = float(np.abs(back[~M]).max()) if (~M).any() else 0.0
    out["mask_voxels"] = int(M.sum())
    out["roundtrip_s"] = time.time() - t
    del back, Y

    # 4. region variance: a zero-variance region has no correlation and breaks the SPD tangent space
    S64 = S.astype(np.float64)
    sd = S64.std(axis=0)
    out["zero_var_regions"] = ",".join(str(int(k)) for k, s in zip(labs, sd) if not s > 0)

    # 5. detrending is a projection: the cleaned series is orthogonal to [1, t]
    live = sd > 0
    C = signal.clean(S64[:, live], detrend=True, standardize=False, standardize_confounds=False)
    tt = np.arange(S.shape[0], dtype=np.float64)
    B = np.column_stack([np.ones_like(tt), tt - tt.mean()])
    B /= np.linalg.norm(B, axis=0)
    out["detrend_rel"] = float(np.abs(B.T @ C).max() / max(np.abs(C).max() * np.sqrt(len(tt)), 1e-300))

    # 6. correlation with an empirical estimator is Pearson's
    cm = ConnectivityMeasure(kind="correlation", cov_estimator=EmpiricalCovariance())
    P = cm.fit_transform([S64[:, live]])[0]
    out["pearson_abs"] = float(np.abs(P - np.corrcoef(S64[:, live].T)).max())
    np.save(f"/tmp/sig/{i}.npy", S64)
    return out


os.makedirs("/tmp/sig", exist_ok=True)
t0 = time.time()
res = Parallel(n_jobs=W, verbose=0)(delayed(subject)(i) for i in ids)
phase("per-subject (masker, round trip, C-PAC, detrend, Pearson)", t0)

with open(os.path.join(OUT, "subjects.tsv"), "w") as f:
    keys = list(res[0].keys())
    f.write("\t".join(keys) + "\n")
    for r in res:
        f.write("\t".join(str(r[k]) for k in keys) + "\n")

say("== per-subject identities, worst over %d ==" % len(res))
T = np.array([r["T"] for r in res])
rec("volumes per subject (min / median / max)", f"{T.min()} / {int(np.median(T))} / {T.max()}")
rec("total volumes", int(T.sum()))
rec("image dtypes", sorted({r["dtype"] for r in res}))
rec("CC200 regions extracted, every subject", sorted({r["n_regions"] for r in res}),
    all(r["regions_match"] for r in res), "== labels in the atlas")
rec("atlas-to-image voxel map is an integer shift, every subject", sorted({r["shift"] for r in res}),
    all(r["affine_integer_shift"] for r in res))
rec("CC200 regions with no voxel in the image, max", max(r["empty_regions"] for r in res))
ob = max(r["masker_vs_hand_over_bound"] for r in res)
rec("NiftiLabelsMasker vs hand mean, max abs", "%.3e" % max(r["masker_vs_hand_abs"] for r in res))
rec("NiftiLabelsMasker vs hand mean, worst (|diff| - n eps mean|x|)", "%.3e" % ob, np.isfinite(ob) and ob <= 0,
    "<= 0 (float64 summation bound, every element)")
rec("NiftiMasker round trip in-mask, max abs", max(r["roundtrip_in"] for r in res),
    max(r["roundtrip_in"] for r in res) == 0.0, "== 0")
rec("NiftiMasker round trip out-of-mask, max abs", max(r["roundtrip_out"] for r in res),
    max(r["roundtrip_out"] for r in res) == 0.0, "== 0")
dt = max(r["detrend_rel"] for r in res)
rec("signal.clean(detrend) . [1,t], max rel", "%.3e" % dt, below(dt, 1e-10), "< 1e-10")
pa = max(r["pearson_abs"] for r in res)
rec("correlation(EmpiricalCovariance) vs np.corrcoef, max abs", "%.3e" % pa, below(pa, 1e-12), "< 1e-12")
rec("C-PAC rois_cc200 same shape, every subject", sum(r["cpac_shape_ok"] for r in res),
    all(r["cpac_shape_ok"] for r in res), "871")
oc = max(r["cpac_vs_idx_over_bound"] for r in res)
rec("C-PAC vs mean over the index-aligned atlas, max abs", "%.3e" % max(r["cpac_vs_idx_abs"] for r in res))
rec("C-PAC vs mean over the index-aligned atlas, worst (|diff| - (n eps32 mean|x| + ulp32 + 5e-7))", "%.3e" % oc,
    np.isfinite(oc) and oc <= 0, "<= 0 (a float32 mean, 6 printed decimals)")
rec("control: C-PAC vs nilearn (header-aligned), max abs", "%.3e" % max(r["cpac_vs_nilearn_abs"] for r in res),
    max(r["cpac_vs_nilearn_abs"] for r in res) > 1.0, "> 1 (the placements differ)")
rec("C-PAC vs nilearn per-region r: median of medians / min",
    "%.4f / %.4f" % (np.median([r["cpac_vs_nilearn_r_median"] for r in res]), min(r["cpac_vs_nilearn_r_min"] for r in res)))
zv = {}
for r in res:
    for k in filter(None, r["zero_var_regions"].split(",")):
        zv[k] = zv.get(k, 0) + 1
rec("zero-variance regions (region:subjects)", ";".join(f"{k}:{v}" for k, v in sorted(zv.items(), key=lambda kv: int(kv[0]))) or "none")
h = hashlib.sha256("".join(r["sha_signals"] for r in res).encode()).hexdigest()
rec("sha256 over all region-signal arrays", h)

def write_score():
    with open(os.path.join(OUT, "phases.tsv"), "w") as f:
        for k, v in PHASE.items():
            f.write(f"{k}\t{v:.1f}\n")
    with open(os.path.join(OUT, "score.tsv"), "w") as f:
        f.write("claim\tvalue\tbound\tstatus\n")
        for row in SCORE:
            f.write("\t".join(row) + "\n")


if FAILED and STRIDE == 1:   # a sizing run carries on, so its timings cover every phase
    write_score()
    say("FAILED:", FAILED)
    sys.exit(1)

# ---------------------------------------------------------------- the group: tangent space
from nilearn.connectome import ConnectivityMeasure  # noqa: E402

bad = sorted(int(k) for k in zv)
import nibabel as nib  # noqa: E402
LAB = np.unique(np.asarray(nib.load(ATLAS).dataobj).astype(np.int64))[1:]
keep = np.array([int(k) not in bad for k in LAB])
rec("regions kept for the group (nonzero variance in every subject)", int(keep.sum()))
SIG = [np.load(f"/tmp/sig/{i}.npy")[:, keep] for i in ids]

t0 = time.time()
with warnings.catch_warnings(record=True) as wl:
    warnings.simplefilter("always")
    tan = ConnectivityMeasure(kind="tangent")           # the default estimator: Ledoit-Wolf
    Tg = tan.fit_transform(SIG)
nonconv = [str(x.message) for x in wl if "Maximum number of iterations" in str(x.message)]
phase("tangent fit, 871 subjects", t0)
n = Tg.shape[1]
G = tan.mean_
Wh = tan.whitening_
grad = np.linalg.norm(Tg.mean(axis=0)) / G.size
rec("tangent: geometric mean converged (no max_iter warning)", len(nonconv) == 0, len(nonconv) == 0)
rec("tangent: |mean of tangent vectors|_F / n^2 at the returned mean", "%.3e" % grad, below(grad, 1e-7),
    "< 1e-7 (nilearn's own tol)")
# control: the same gradient at the arithmetic mean, the iteration's starting point
from scipy import linalg  # noqa: E402
covs = ConnectivityMeasure(kind="covariance").fit_transform(SIG)
Am = covs.mean(axis=0)
va, ve = linalg.eigh(Am)
Ais = (ve / np.sqrt(va)) @ ve.T
logs = []
for Ck in covs:
    v2, e2 = linalg.eigh(Ais @ Ck @ Ais)
    logs.append((e2 * np.log(v2)) @ e2.T)
g0 = np.linalg.norm(np.mean(logs, axis=0)) / G.size
rec("control: the same gradient at the arithmetic mean", "%.3e" % g0, g0 > 100 * grad, "> 100x the converged one")
vals = linalg.eigvalsh(G)
kG = vals.max() / vals.min()
I_err = np.abs(Wh @ G @ Wh - np.eye(n)).max()
rec("tangent: whitening . mean . whitening == I, max abs", "%.3e" % I_err, below(I_err, 1e3 * kG * np.finfo(float).eps),
    "< 1e3 kappa eps = %.1e (kappa %.1f)" % (1e3 * kG * np.finfo(float).eps, kG))
back = tan.inverse_transform(Tg)
rt = max(np.abs(b - c).max() / np.abs(c).max() for b, c in zip(back, covs))
kc = max(np.linalg.cond(c) for c in covs)
rec("tangent: inverse_transform recovers every covariance, max rel", "%.3e" % rt,
    below(rt, 1e3 * kc * np.finfo(float).eps), "< 1e3 kappa_max eps = %.1e (kappa_max %.1f)" % (1e3 * kc * np.finfo(float).eps, kc))
np.save(os.path.join(OUT, "tangent-mean.npy"), G)
rec("sha256 tangent mean", hashlib.sha256(np.ascontiguousarray(G).tobytes()).hexdigest())

# ---------------------------------------------------------------- the classifier (reported)
from sklearn.model_selection import StratifiedKFold, LeaveOneGroupOut  # noqa: E402
from sklearn.svm import LinearSVC  # noqa: E402

y = (dx == 1).astype(int)


def fold(tr, te, yy):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cm = ConnectivityMeasure(kind="tangent", vectorize=True, discard_diagonal=True)
        Xtr = cm.fit_transform([SIG[k] for k in tr])
        Xte = cm.transform([SIG[k] for k in te])
        clf = LinearSVC(C=1.0, max_iter=100000, random_state=0).fit(Xtr, yy[tr])
        return te, clf.predict(Xte)


def cv(splits, yy):
    out = Parallel(n_jobs=min(W, len(splits)))(delayed(fold)(tr, te, yy) for tr, te in splits)
    pred = np.full(len(yy), -1)
    accs = []
    for te, p in out:
        pred[te] = p
        accs.append(float((p == yy[te]).mean()))
    return pred, accs


strata = np.array([f"{s}_{d}" for s, d in zip(site, dx)])
t0 = time.time()
kf = list(StratifiedKFold(n_splits=10, shuffle=True, random_state=0).split(np.zeros(len(y)), strata))
p10, a10 = cv(kf, y)
phase("10-fold CV (tangent + LinearSVC)", t0)
rec("10-fold CV accuracy, mean (folds stratified by site x diagnosis)", "%.4f" % np.mean(a10))
rec("10-fold CV accuracy, per fold", ",".join("%.3f" % a for a in a10))
t0 = time.time()
lo = list(LeaveOneGroupOut().split(np.zeros(len(y)), y, site))
pls, als = cv(lo, y)
phase("leave-one-site-out CV", t0)
rec("leave-one-site-out accuracy, pooled", "%.4f" % float((pls == y).mean()))
t0 = time.time()
yp = np.random.default_rng(20261018).permutation(y)
pp, ap = cv(kf, yp)
phase("permuted-label control", t0)
rec("control: permuted diagnosis, 10-fold accuracy", "%.4f" % np.mean(ap))
with open(os.path.join(OUT, "predictions.tsv"), "w") as f:
    f.write("FILE_ID\tsite\tdx\tpred_10fold\tpred_loso\n")
    for k, i in enumerate(ids):
        f.write(f"{i}\t{site[k]}\t{dx[k]}\t{p10[k]}\t{pls[k]}\n")

write_score()
say("wall %.1f s" % (time.time() - T_START))
if FAILED:
    say("FAILED:", FAILED)
    sys.exit(1)
say("NILEARN REAL OK")
