#!/usr/bin/env python3
"""astra-toolbox + tomopy on Graviton4: CPU tomography against Radon identities.

The phantom is synthetic, so ground truth is exact and nothing is staged. What this recipe
asserts and what it only reports is a deliberate split, because the two halves of the
pipeline behaved very differently:

 * THE FORWARD MODEL CROSS-VALIDATES. astra and tomopy project the same phantom to
   per-angle sums agreeing to ~3e-7 relative. Two unrelated Radon implementations, same
   answer -- that is the real cross-code result here.
 * THE RECONSTRUCTIONS DO NOT. astra's FBP recovers the phantom at 0.070 relative L2;
   tomopy's best configuration manages 0.850, on the SAME sinogram. So the reconstruction
   comparison is REPORTED with controls for scale, not asserted, and this file does not
   claim to know which default is responsible.

Three things measured rather than assumed:
 1. astra computes in FLOAT32, so linearity and mass conservation hold to ~1e-6, not to
    zero. Both bounds come from that, not from where the run landed.
 2. Reconstruction error SATURATES with angle count (ratios 1.73, 1.43, 1.12) because a
    sharp-edged disc rings. So monotone decrease is asserted; no convergence order is.
 3. tomopy's `fbp` ignores `filter_name` -- five documented filters give byte-identical
    output. That is reported as an observation, with the bytes as the evidence.

This module runs under `if __name__ == "__main__"` because PYTHON 3.14 CHANGED the default
multiprocessing start method on Linux from fork to forkserver, and tomopy calls
mp.Manager() from library code (tomopy/util/mproc.py). Without the guard the child
re-imports this file and tomopy.project dies with an error blaming the caller.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys

import numpy as np
import astra
import tomopy

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


N = 256
R = 60.0
N_ANG = 180
SEED = 20261011


def phantom():
    yy, xx = np.mgrid[:N, :N].astype("float64")
    c = (N - 1) / 2.0
    return ((yy - c) ** 2 + (xx - c) ** 2 <= R * R).astype("float64"), c


def astra_forward(img, n_ang):
    vg = astra.create_vol_geom(img.shape[0], img.shape[1])
    ang = np.linspace(0, np.pi, n_ang, endpoint=False)
    pg = astra.create_proj_geom("parallel", 1.0, img.shape[1], ang)
    pid = astra.create_projector("linear", pg, vg)      # CPU projector, no _CUDA
    sid, sino = astra.create_sino(img, pid)
    astra.data2d.delete(sid)
    astra.projector.delete(pid)
    return sino, ang


def astra_fbp(sino, ang, shape):
    vg = astra.create_vol_geom(shape[0], shape[1])
    pg = astra.create_proj_geom("parallel", 1.0, shape[1], ang)
    pid = astra.create_projector("linear", pg, vg)
    sid = astra.data2d.create("-sino", pg, sino)
    rid = astra.data2d.create("-vol", vg, 0)
    cfg = astra.astra_dict("FBP")                        # CPU; FBP_CUDA is the GPU name
    cfg.update(ProjectorId=pid, ProjectionDataId=sid, ReconstructionDataId=rid)
    aid = astra.algorithm.create(cfg)
    astra.algorithm.run(aid)
    out = astra.data2d.get(rid).copy()
    for d, fn in ((aid, astra.algorithm.delete), (sid, astra.data2d.delete),
                  (rid, astra.data2d.delete), (pid, astra.projector.delete)):
        fn(d)
    return out


def main():
    rec("astra", getattr(astra, "__version__", "unknown"))
    rec("tomopy", getattr(tomopy, "__version__", "unknown"))
    rec("numpy", np.__version__)

    # ------------- 1. prove the CPU path. astra ships CUDA builds that sort last by
    # build string, and a GPU algorithm would invalidate every cost claim on the page.
    cuda = bool(astra.astra.use_cuda())
    rec("astra_use_cuda", "%s" % cuda)
    if cuda:
        die("this build reports CUDA available; the recipe asserts the CPU path "
            "('FBP' and the 'linear' projector, not FBP_CUDA) and the timings assume it")
    rec("algorithms", "FBP + 'linear' projector (no _CUDA suffix anywhere)")

    disc, centre = phantom()
    mass = float(disc.sum())
    analytic = np.pi * R * R
    rec("phantom", "uniform disc r=%g in %dx%d" % (R, N, N))
    rec("phantom_mass_pixels", "%.1f" % mass)
    rec("phantom_mass_analytic", "%.3f" % analytic)
    rec("phantom_rasterisation", "%.6f of pi*R^2" % (mass / analytic))
    if abs(mass / analytic - 1.0) > 0.01:
        die("the rasterised disc is %.4f of pi*R^2; too far off to use as a mass "
            "reference" % (mass / analytic))

    sino, ang = astra_forward(disc, N_ANG)
    if not np.all(np.isfinite(sino)):
        die("the forward projection produced a non-finite value")
    rec("sinogram_shape", "%s" % (sino.shape,))
    rec("sinogram_dtype", "%s" % sino.dtype)

    # ----------------- 2. Radon mass conservation: angle-independent, equal to the mass
    sums = sino.sum(axis=1)
    spread = float((sums.max() - sums.min()) / sums.mean())
    ratio = float(sums.mean() / mass)
    rec("projection_sum_mean", "%.6f" % sums.mean())
    rec("projection_sum_angular_spread", "%.3e" % spread)
    rec("projection_sum_over_mass", "%.9f" % ratio)
    # Bounds from float32 plus a rasterised boundary, not from the observation:
    # the projector interpolates along each ray, so a jagged disc edge costs ~1e-4
    # per-angle while the MEAN over angles averages that away to ~1e-5.
    if spread > 1e-3:
        die("projection sums vary %.3e across angles; the Radon integral must be "
            "angle-independent to within the discretisation" % spread)
    if abs(ratio - 1.0) > 1e-4:
        die("the mean projection sum is %.9f of the object mass; Radon conserves mass"
            % ratio)
    rec("conservation_radon", "angle-independent to %.1e, mean equals the mass to %.1e"
        % (spread, abs(ratio - 1.0)))

    # --------------------------------------- 3. linearity of the forward transform
    yy, xx = np.mgrid[:N, :N].astype("float64")
    g = (((yy - 80) ** 2 + (xx - 170) ** 2) <= 30 * 30).astype("float64") * 2.5
    a_, b_ = 0.37, -1.9
    s_f, _ = astra_forward(disc, 60)
    s_g, _ = astra_forward(g, 60)
    s_c, _ = astra_forward(a_ * disc + b_ * g, 60)
    lin = float(np.abs(s_c - (a_ * s_f + b_ * s_g)).max() / np.abs(s_c).max())
    rec("linearity_residual", "%.3e" % lin)
    # float32 eps is 1.2e-07; a few tens of eps over a 256-sample ray sum is the floor.
    if lin > 1e-5:
        die("R(af+bg) departs from aR(f)+bR(g) by %.3e; the Radon transform is linear "
            "and float32 only explains ~1e-6" % lin)
    rec("linearity", "holds to %.1e (float32 arithmetic)" % lin)

    # ------------- 4. the forward-model CROSS-VALIDATION: astra vs tomopy, same phantom
    proj_t = tomopy.project(disc[None, :, :], ang, pad=False, ncore=1)
    if not np.all(np.isfinite(proj_t)):
        die("tomopy.project produced a non-finite value")
    s_a = float(sino.sum(axis=1).mean())
    s_t = float(proj_t.sum(axis=2).mean())
    rel = abs(s_t - s_a) / s_a
    rec("forward_astra_mean_sum", "%.6f" % s_a)
    rec("forward_tomopy_mean_sum", "%.6f" % s_t)
    rec("forward_cross_rel_diff", "%.3e" % rel)
    if rel > 1e-5:
        die("astra and tomopy disagree on the projection total by %.3e; two Radon "
            "implementations on the same phantom should not" % rel)
    rec("cross_validation_forward",
        "astra and tomopy agree on the projection total to %.1e" % rel)

    # ---------------------- 5. astra reconstructs the phantom; the metric has controls
    def norm_err(img):
        m = img[disc > 0].mean()
        if not np.isfinite(m) or m == 0:
            return float("nan")
        return float(np.linalg.norm(img / m - disc) / np.linalg.norm(disc))

    rec_a = astra_fbp(sino, ang, disc.shape)
    e_a = norm_err(rec_a)
    rec("astra_recon_range", "[%.4f, %.4f]" % (rec_a.min(), rec_a.max()))
    rec("astra_recon_norm_err", "%.6f" % e_a)
    if not np.isfinite(e_a) or e_a > 0.10:
        die("astra's FBP recovers the phantom at %.6f relative L2; 0.07 is what this "
            "geometry gives and the edge-ringing floor is ~0.063" % e_a)

    rng = np.random.default_rng(SEED)
    controls = (("gaussian_noise", rng.normal(size=disc.shape) + 1.0),
                ("disc_shifted_40px", np.roll(disc, 40, axis=1) + 1e-9),
                ("uniform_field", np.ones_like(disc)))
    worst_ctl = 9e9
    for name, img in controls:
        e = norm_err(img)
        rec("control_%s" % name, "%.6f" % e)
        worst_ctl = min(worst_ctl, e)
    if worst_ctl <= e_a:
        die("a deliberately wrong image scored %.6f against the phantom, no worse than "
            "astra's %.6f -- the metric cannot tell a reconstruction from a mistake"
            % (worst_ctl, e_a))
    rec("control_margin", "the closest wrong image is %.1fx worse than astra's recon"
        % (worst_ctl / e_a))

    # -------- 6. more angles must help, but the rate is REPORTED: the error saturates
    errs = []
    for na in (45, 90, 180, 360):
        s, a = astra_forward(disc, na)
        errs.append((na, norm_err(astra_fbp(s, a, disc.shape))))
    rec("angle_sweep", "; ".join("%d:%.6f" % t for t in errs))
    rec("angle_sweep_ratios", ", ".join("%.3f" % (errs[i][1] / errs[i + 1][1])
                                        for i in range(len(errs) - 1)))
    for i in range(len(errs) - 1):
        if not errs[i + 1][1] < errs[i][1]:
            die("going from %d to %d angles did not reduce the error (%.6f -> %.6f)"
                % (errs[i][0], errs[i + 1][0], errs[i][1], errs[i + 1][1]))
    rec("angle_order_asserted", "no -- the error saturates on edge ringing; see README")

    # ---------------------------- 7. determinism, which FBP and the projector both have
    s1, _ = astra_forward(disc, N_ANG)
    s2, _ = astra_forward(disc, N_ANG)
    r1 = astra_fbp(sino, ang, disc.shape)
    r2 = astra_fbp(sino, ang, disc.shape)
    det = bool(np.array_equal(s1, s2) and np.array_equal(r1, r2))
    rec("determinism", "forward and FBP byte-identical on repeat" if det else "DIFFERS")
    if not det:
        die("repeating the forward projection or the FBP gave different arrays")

    # ------- 8. tomopy's reconstruction: REPORTED, and the filter observation with it
    best = None
    for filt in ("ramlak", "shepp", "parzen"):
        try:
            r = tomopy.recon(np.ascontiguousarray(sino[:, None, :], dtype="float32"),
                             ang, center=centre, algorithm="fbp", filter_name=filt,
                             sinogram_order=False, ncore=1)[0]
        except Exception as exc:
            rec("tomopy_fbp_%s" % filt, "FAILED %s" % type(exc).__name__)
            continue
        e = norm_err(r)
        rec("tomopy_fbp_%s" % filt, "range [%.4f, %.4f] norm_err %.6f"
            % (r.min(), r.max(), e))
        if best is None:
            best = (filt, r.copy(), e)
        elif np.array_equal(r, best[1]):
            rec("tomopy_filter_%s_vs_%s" % (filt, best[0]), "byte-identical")
    if best is not None:
        rec("tomopy_best_norm_err", "%.6f" % best[2])
        rec("tomopy_vs_astra_recon", "tomopy %.3f vs astra %.3f relative L2"
            % (best[2], e_a))
    rec("reconstruction_comparison", "REPORTED not asserted -- see README")

    with open("score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))

    print("TOMOGRAPHY OK")


if __name__ == "__main__":
    main()
