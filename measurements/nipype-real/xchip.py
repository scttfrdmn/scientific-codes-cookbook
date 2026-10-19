#!/usr/bin/env python3
"""measurements/nipype-real: how far apart the chips' float32 FA and MD maps are.

Each chip's cohort run asserts that its nipype maps equal that chip's neuroimaging-real WLS maps
rounded to float32, bit for bit (ref_fa_within_1ulp: 0 ulp, 123 of 123). So rounding the three
reference map sets to float32 reproduces the three chips' nipype outputs exactly, and comparing those
is the cross-chip comparison of nipype's outputs -- without re-running the cohort to keep its maps.
Reads /tmp/<arch>.tar for arm64, x86_64, amd.
"""
import glob, os, sys, tarfile
import numpy as np

ARCH = ("arm64", "x86_64", "amd")
for a in ARCH:
    os.makedirs("/tmp/" + a, exist_ok=True)
    with tarfile.open("/tmp/%s.tar" % a) as t:
        t.extractall("/tmp/" + a, filter="data")
ids = sorted(os.path.basename(p)[:-4] for p in glob.glob("/tmp/arm64/*.npz"))
print("subjects\t%d" % len(ids))
fails = 0
for other in ("x86_64", "amd"):
    for key in ("fa_wls", "md_wls"):
        ulp_all = ulp_tissue = 0.0
        ident = nvox = ndiff = 0
        for sid in ids:
            g = np.load("/tmp/arm64/%s.npz" % sid)
            o = np.load("/tmp/%s/%s.npz" % (other, sid))
            if not np.array_equal(g["mask"], o["mask"]):
                print("FAIL\t%s mask differs for %s" % (other, sid)); fails += 1
            x, y = g[key].astype(np.float32), o[key].astype(np.float32)
            md = g["md_wls"]
            u = np.abs(x.astype(np.float64) - y) / np.spacing(np.maximum(np.abs(x), np.abs(y)))
            ulp_all = max(ulp_all, float(u.max()))
            t = md >= 1e-4                       # tissue-scale diffusivity, as neuroimaging-real bounds it
            ulp_tissue = max(ulp_tissue, float(u[t].max()) if t.any() else 0.0)
            ident += int(np.array_equal(x, y))
            ndiff += int((x != y).sum())
            nvox += x.size
        print("%s_vs_arm64_%s\tfloat32 maps identical in %d of %d subjects; %d of %d voxels differ (%.4f%%); "
              "max %.1f ulp over all voxels, %.1f ulp where MD >= 1e-4 mm^2/s"
              % (other, key, ident, len(ids), ndiff, nvox, 100.0 * ndiff / nvox, ulp_all, ulp_tissue))
a = {s: np.load("/tmp/x86_64/%s.npz" % s) for s in ids}
b = {s: np.load("/tmp/amd/%s.npz" % s) for s in ids}
same = sum(all(np.array_equal(a[s][k], b[s][k]) for k in ("mask", "fa_wls", "md_wls")) for s in ids)
print("x86_64_vs_amd\tmask and WLS FA/MD (float64) identical in %d of %d subjects" % (same, len(ids)))
sys.exit(1 if fails else 0)
