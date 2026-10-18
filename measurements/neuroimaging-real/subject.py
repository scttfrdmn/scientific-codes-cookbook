#!/usr/bin/env python3
"""One NKI-RS diffusion scan through DIPY's tensor fits, checked four ways. Called by dipy.sh.

    subject.py <id>          -> /tmp/res/<id>.tsv (one row) and /tmp/maps/<id>.npz
    subject.py --summary     -> cohort assertions over /tmp/res; exit 1 if any fails

Per subject, with no reference image needed:
  * FSL's dtifit, same bytes, same method (OLS on log signal): FA, MD and the eigenvalues agree
  * a tensor planted at every brain voxel, at this subject's real gradient table, is recovered
    to its closed-form FA (the recipe's check, at real geometry and scale)
  * rotating the gradient table leaves FA and MD unchanged (a rotation invariant)
  * fslmaths' all-volumes-positive mask equals numpy's, and AFNI's 3dTstat mean equals numpy's
"""
import glob, hashlib, os, subprocess, sys, time
import numpy as np

COLS = ["id", "status", "nvol", "slope", "n_brain", "n_pd", "n_cmp", "n_floor", "pos_eq", "afni_rel",
        "fsl_dfa_max", "fsl_dfa_p99", "fsl_dmd_rel_max", "fsl_dl_rel_max", "fsl_dfa_naive", "fsl_dfa_floor_only", "fsl_dfa_norm_only",
        "fsl_fa_gt1", "plant_fa_ols", "plant_fa_wls", "plant_md_rel", "rot_dfa", "rot_dmd_rel", "rot_dfa_clipped",
        "fa_med_ols", "fa_med_wls", "md_med", "wls_ols_dfa_med", "fa_sha16", "mask_sha16",
        "t_load", "t_afni", "t_mask", "t_ols", "t_wls", "t_checks", "t_total"]


def fa_closed(ev):
    """FA = sqrt(1.5 * sum (l - mean)^2 / sum l^2) -- written out, not dipy's own function."""
    m = ev.mean(axis=-1, keepdims=True)
    return np.sqrt(1.5 * ((ev - m) ** 2).sum(-1) / (ev ** 2).sum(-1))


def subject(sid):
    import nibabel as nib
    from dipy.core.gradients import gradient_table
    from dipy.io.gradients import read_bvals_bvecs
    from dipy.reconst import dti
    from dipy.segment.mask import median_otsu

    r = {"id": sid}
    T0 = time.time()
    d = "/tmp/dwi/sub-%s_ses-BAS1_dwi" % sid
    f = "/tmp/fslout/%s" % sid
    step = "load"
    try:
        img = nib.load(d + ".nii.gz")
        slope, inter = img.dataobj.slope, img.dataobj.inter
        raw = np.asarray(img.dataobj)
        bvals, bvecs = read_bvals_bvecs(d + ".bval", d + ".bvec")
        r["nvol"] = raw.shape[-1]
        r["slope"] = "%g/%g" % (slope, inter)
        if not (raw.ndim == 4 and len(bvals) == raw.shape[-1] == bvecs.shape[0]):
            raise ValueError("shape %s vs %d bvals" % (raw.shape, len(bvals)))
        gtab = gradient_table(bvals, bvecs=bvecs, b0_threshold=50)
        r["t_load"] = time.time() - T0

        step = "afni"
        t = time.time()
        w = "/tmp/work/%s" % sid
        os.makedirs(w, exist_ok=True)
        subprocess.run(["3dTstat", "-mean", "-prefix", w + "/mean.nii.gz", d + ".nii.gz"],
                       check=True, capture_output=True)
        a = np.asarray(nib.load(w + "/mean.nii.gz").dataobj, dtype=np.float64).squeeze()
        n = raw.astype(np.float64).mean(axis=-1)
        big = n > 1
        r["afni_rel"] = float(np.max(np.abs(a[big] - n[big]) / n[big]))
        r["t_afni"] = time.time() - t

        step = "mask"
        t = time.time()
        b0 = raw[..., gtab.b0s_mask].astype(np.float64).mean(axis=-1)
        _, brain = median_otsu(b0, median_radius=4, numpass=4)
        pos = raw.min(axis=-1) > 0
        pos_fsl = np.asarray(nib.load(f + "/pos.nii.gz").dataobj) > 0
        r["pos_eq"] = int(np.array_equal(pos, pos_fsl))
        M = brain & pos
        r["n_brain"] = int(M.sum())
        Y = raw[M].astype(np.float64)
        r["t_mask"] = time.time() - t

        step = "ols"
        t = time.time()
        ols = dti.TensorModel(gtab, fit_method="OLS").fit(Y)
        fa_o, md_o, ev_o = ols.fa, ols.md, ols.evals
        r["t_ols"] = time.time() - t
        # A fixed rotation of the gradient table, for the invariance check below.
        a1, a2, a3 = np.radians([30.0, 45.0, 60.0])
        Rz = lambda x: np.array([[np.cos(x), -np.sin(x), 0], [np.sin(x), np.cos(x), 0], [0, 0, 1]])
        Rx = lambda x: np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
        R = Rz(a1) @ Rx(a2) @ Rz(a3)
        gtr = gradient_table(bvals, bvecs=bvecs @ R.T, b0_threshold=50)
        # DIPY clips eigenvalues at 1e-6 / -min(design matrix) (~6.7e-10 mm^2/s here), not at 0,
        # and that level depends on the gradient table's orientation. Read the unclipped
        # eigenvalues from the fitted lower triangle: PD marks the voxels whose smallest one is
        # above the clip level of either table, i.e. a positive-definite tensor neither fit touched.
        X = dti.design_matrix(gtab)
        lt = dti.ols_fit_tensor(X, Y, return_lower_triangular=True)[0]
        clip = 1e-6 / -max(X.min(), dti.design_matrix(gtr).min())   # the higher of the two levels
        PD = np.linalg.eigvalsh(dti.from_lower_triangular(lt))[:, 0] > clip
        r["n_pd"] = int(PD.sum())
        step = "wls"
        t = time.time()
        wls = dti.TensorModel(gtab).fit(Y)          # DIPY's default fit: what users run
        fa_w, md_w = wls.fa, wls.md
        r["t_wls"] = time.time() - t

        step = "checks"
        t = time.time()
        # FSL dtifit on the same voxels, same method -- which takes matching two things dtifit does
        # and DIPY does not (fdt dtifit.cc). (1) It renormalises every bvec to unit length, with the
        # norm held in single precision; this file's bvecs are unit only to ~1e-6. (2) Its "OLS" is
        # OLS on a floored signal (tensorfit): a first fit estimates S0 (replaced by max S if below
        # mean S), every S <= 1% of S0 is raised to 1% of S0, and the fit is redone. Both are applied
        # here before DIPY's OLS, so the two codes solve the same problem. Compared where both
        # tensors are positive definite: DIPY clips a negative eigenvalue and dtifit does not
        # (dtifit then reports FA > 1), so outside that set they report conventions, not fits.
        L = np.stack([np.asarray(nib.load("%s/dti_L%d.nii.gz" % (f, k)).dataobj, dtype=np.float64)[M]
                      for k in (1, 2, 3)], axis=-1)
        Ffa = np.asarray(nib.load(f + "/dti_FA.nii.gz").dataobj, dtype=np.float64)[M]
        Fmd = np.asarray(nib.load(f + "/dti_MD.nii.gz").dataobj, dtype=np.float64)[M]
        nrm = np.sqrt((bvecs ** 2).sum(axis=1)).astype(np.float32).astype(np.float64)
        gfsl = gradient_table(bvals, bvecs=bvecs / np.where(nrm > 0, nrm, 1.0)[:, None], b0_threshold=50)

        def floor(g):
            s0 = np.exp(-dti.ols_fit_tensor(dti.design_matrix(g), Y, return_lower_triangular=True)[0][:, 6])
            s0 = np.where(s0 < Y.mean(axis=1), Y.max(axis=1), s0)
            fl = Y / s0[:, None] <= 0.01
            return np.where(fl, 0.01 * s0[:, None], Y), fl

        Yf, floored = floor(gfsl)
        r["n_floor"] = int(floored.any(axis=1).sum())
        olf = dti.TensorModel(gfsl, fit_method="OLS").fit(Yf)
        C = PD & (L[:, 2] > 0)
        r["n_cmp"] = int(C.sum())
        r["fsl_fa_gt1"] = int((Ffa > 1).sum())
        dfa = np.abs(olf.fa[C] - Ffa[C])
        r["fsl_dfa_max"] = float(dfa.max())
        r["fsl_dfa_p99"] = float(np.quantile(dfa, 0.99))
        r["fsl_dmd_rel_max"] = float(np.max(np.abs(olf.md[C] - Fmd[C]) / Fmd[C]))
        r["fsl_dl_rel_max"] = float(np.max(np.abs(olf.evals[C] - L[C]) / Fmd[C, None]))
        # what each unmatched difference alone does to the comparison, and to DIPY's own answer
        r["fsl_dfa_naive"] = float(np.max(np.abs(fa_o[C] - Ffa[C])))
        r["fsl_dfa_floor_only"] = float(np.max(np.abs(dti.TensorModel(gtab, fit_method="OLS").fit(floor(gtab)[0]).fa[C] - Ffa[C])))
        r["fsl_dfa_norm_only"] = float(np.max(np.abs(dti.TensorModel(gfsl, fit_method="OLS").fit(Y).fa[C] - Ffa[C])))

        # Plant: this voxel's own fitted tensor (where positive definite), noise-free, at the real
        # gradient table; S0 is the voxel's mean b=0. Truth is the closed form in the eigenvalues.
        P = PD
        lam, V = ev_o[P], ols.evecs[P]
        D = np.einsum("nij,nj,nkj->nik", V, lam, V)
        q = np.einsum("bi,nij,bj->nb", gtab.bvecs, D, gtab.bvecs)
        S = b0[M][P, None] * np.exp(-gtab.bvals[None, :] * q)
        truth = fa_closed(lam)
        po = dti.TensorModel(gtab, fit_method="OLS").fit(S)
        pw = dti.TensorModel(gtab).fit(S)
        r["plant_fa_ols"] = float(np.max(np.abs(po.fa - truth)))
        r["plant_fa_wls"] = float(np.max(np.abs(pw.fa - truth)))
        r["plant_md_rel"] = float(np.max(np.abs(po.md - lam.mean(-1)) / lam.mean(-1)))

        # Rotate the gradient table by a fixed rotation: the fitted tensor rotates with it, so its
        # invariants must not move. Exercises the B-matrix built from the bvecs.
        rot = dti.TensorModel(gtr, fit_method="OLS").fit(Y)
        r["rot_dfa"] = float(np.max(np.abs(rot.fa - fa_o)[PD]))
        r["rot_dmd_rel"] = float(np.max((np.abs(rot.md - md_o) / np.abs(md_o))[PD]))
        # not invariant where an eigenvalue was clipped, because the clip level moves with the
        # table's orientation. Reported, not asserted.
        r["rot_dfa_clipped"] = float(np.max(np.abs(rot.fa - fa_o)[~PD])) if (~PD).any() else 0.0
        r["t_checks"] = time.time() - t

        r["fa_med_ols"] = float(np.median(fa_o))
        r["fa_med_wls"] = float(np.median(fa_w))
        r["md_med"] = float(np.median(md_o))
        r["wls_ols_dfa_med"] = float(np.median(np.abs(fa_w - fa_o)))
        r["fa_sha16"] = hashlib.sha256(np.ascontiguousarray(fa_o).tobytes()).hexdigest()[:16]
        r["mask_sha16"] = hashlib.sha256(np.packbits(M).tobytes()).hexdigest()[:16]
        os.makedirs("/tmp/maps", exist_ok=True)
        np.savez_compressed("/tmp/maps/%s.npz" % sid, mask=np.packbits(M), shape=np.array(M.shape),
                            fa_ols=fa_o, fa_wls=fa_w, md_ols=md_o, md_wls=md_w)
        subprocess.run(["rm", "-rf", w])            # the task's own work dir, never a staged input
        r["status"] = "OK"
    except Exception as e:  # one row per subject, naming the step that failed
        r["status"] = "FAIL"
        r["slope"] = "%s: %s" % (step, str(e).replace("\t", " ").replace("\n", " ")[:200])
        print("FAIL %s at %s: %s" % (sid, step, e), file=sys.stderr)
    r["t_total"] = time.time() - T0
    os.makedirs("/tmp/res", exist_ok=True)
    with open("/tmp/res/%s.tsv" % sid, "w") as fh:
        fh.write("\t".join(fmt(r.get(c, "")) for c in COLS) + "\n")


def fmt(v):
    if isinstance(v, float):
        return "%.6e" % v
    return str(v)


def summary(ids_file, full):
    ids = [l.strip() for l in open(ids_file) if l.strip()]
    rows, bad = [], []
    for sid in ids:
        p = "/tmp/res/%s.tsv" % sid
        if not os.path.exists(p):
            bad.append("%s: no result" % sid)
            continue
        rows.append(dict(zip(COLS, open(p).read().rstrip("\n").split("\t"))))
    with open("/tmp/results.tsv", "w") as fh:
        fh.write("\t".join(COLS) + "\n")
        for r in rows:
            fh.write("\t".join(r.get(c, "") for c in COLS) + "\n")
    ok = [r for r in rows if r["status"] == "OK"]
    for r in rows:
        if r["status"] != "OK":
            bad.append("%s did not complete (%s)" % (r["id"], r["slope"]))
    col = lambda k: np.array([float(r[k]) for r in ok])

    def check(name, ok_, observed):
        print("%-58s %s  %s" % (name, "PASS" if bool(ok_) else "FAIL", observed))
        if not bool(ok_):
            bad.append(name)

    print("subjects  %d listed, %d OK" % (len(ids), len(ok)))
    if full:
        check("all subjects complete", len(ok) == len(ids), "%d / %d" % (len(ok), len(ids)))
    if ok:
        # bounds: see measurements/neuroimaging-real/README.md for why each is the number it is
        check("137 volumes per scan", np.all(col("nvol") == 137), "%d" % col("nvol").min())
        check("fslmaths positive mask == numpy's, every subject", np.all(col("pos_eq") == 1), "%d / %d" % (col("pos_eq").sum(), len(ok)))
        check("AFNI 3dTstat mean vs numpy, max relative < 1e-6", col("afni_rel").max() < 1e-6, "%.3e" % col("afni_rel").max())
        # With both modes matched what is left is dtifit's single precision: it computes FA in
        # floats (calc_FA) and writes float32 maps, so 1e-6 is ~16 float32 ulps -- of FA, and
        # relative to the voxel's own MD for MD and the eigenvalues.
        check("DIPY OLS vs dtifit FA (modes matched), max |diff| < 1e-6", col("fsl_dfa_max").max() < 1e-6, "%.3e" % col("fsl_dfa_max").max())
        check("DIPY OLS vs dtifit MD, max relative < 1e-6", col("fsl_dmd_rel_max").max() < 1e-6, "%.3e" % col("fsl_dmd_rel_max").max())
        check("DIPY OLS vs dtifit eigenvalues, max |diff| / MD < 1e-6", col("fsl_dl_rel_max").max() < 1e-6, "%.3e" % col("fsl_dl_rel_max").max())
        check("planted tensor FA recovered (OLS), max error < 1e-10", col("plant_fa_ols").max() < 1e-10, "%.3e" % col("plant_fa_ols").max())
        check("planted tensor FA recovered (WLS), max error < 1e-10", col("plant_fa_wls").max() < 1e-10, "%.3e" % col("plant_fa_wls").max())
        check("planted tensor MD recovered, max relative < 1e-10", col("plant_md_rel").max() < 1e-10, "%.3e" % col("plant_md_rel").max())
        check("rotated gradient table: FA unchanged, max |diff| < 1e-10", col("rot_dfa").max() < 1e-10, "%.3e" % col("rot_dfa").max())
        check("rotated gradient table: MD unchanged, max relative < 1e-10", col("rot_dmd_rel").max() < 1e-10, "%.3e" % col("rot_dmd_rel").max())
        q = lambda k: "min %.4g  median %.4g  max %.4g" % (col(k).min(), np.median(col(k)), col(k).max())
        for k in ("n_brain", "n_pd", "n_cmp", "n_floor", "fsl_fa_gt1", "fsl_dfa_naive", "fsl_dfa_floor_only", "fsl_dfa_norm_only", "rot_dfa_clipped", "fa_med_ols", "fa_med_wls", "md_med", "wls_ols_dfa_med", "fsl_dfa_p99",
                  "t_load", "t_afni", "t_mask", "t_ols", "t_wls", "t_checks", "t_total"):
            print("  %-16s %s" % (k, q(k)))
        tot = col("n_brain").sum()
        print("  positive definite and unclipped %.6f of brain voxels; compared with dtifit %.6f" % (col("n_pd").sum() / tot, col("n_cmp").sum() / tot))
        print("  dtifit's 1%%-of-S0 floor binds in %d voxels (%.2e of brain); dtifit FA > 1 in %d" % (col("n_floor").sum(), col("n_floor").sum() / tot, col("fsl_fa_gt1").sum()))
        print("  DIPY vs dtifit FA max |diff|: neither mode matched %.3e, floor only %.3e, bvec norm only %.3e, both %.3e" % (
            col("fsl_dfa_naive").max(), col("fsl_dfa_floor_only").max(), col("fsl_dfa_norm_only").max(), col("fsl_dfa_max").max()))
        print("  slope/inter      %s" % ",".join(sorted({r["slope"] for r in ok})))
        for k in ("t_load", "t_afni", "t_mask", "t_ols", "t_wls", "t_checks", "t_total"):
            print("  sum_%-12s %.1f" % (k, col(k).sum()))
    for b in bad:
        print("FAIL: " + b)
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1] == "--summary":
        sys.exit(summary(sys.argv[2], sys.argv[3] == "1"))
    subject(sys.argv[1])
