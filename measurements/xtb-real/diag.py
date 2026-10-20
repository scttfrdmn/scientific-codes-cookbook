"""Which xtb build computes the gradient of its own energy?  Full central-difference gradient (all 3N
components, 5-point stencil, h = 1e-3 Bohr, acc 1e-4) against the analytic one, per structure.
    python3 -u diag.py <GMTKN55 dir> <label> <out.tsv> <workers> [xtb flags...]
Uses whatever `xtb` is first on PATH, so the same script runs against any build."""
import os
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xtb_run as X  # noqa: E402

STRUCT = ["ACONF/B_G", "ACONF/B_T", "ACONF/H_g+t+g-", "ADIM6/AD2", "AL2X6/al2me5", "S22/02", "S22/02a",
          "Amino20x4/ASN_xae", "WATER27/H2O"]
H = 1.0e-3
ENGINE = os.environ.get("DIAG_ENGINE", "xtb")   # "tblite": tblite's own CLI for energy and gradient


def energy(args):
    tag, sysdir, xyz, el, chrg, uhf = args
    if ENGINE == "tblite":
        return X.tblite(sysdir, None, chrg, uhf, xyz, el, tag)["E"]
    r = X.xtb(tag, sysdir, None, chrg, uhf, "0.0001", False, xyz, el)
    return r["E"]


def main():
    root, label, out, nw = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    X.SETTINGS += sys.argv[5:]
    os.makedirs(X.WORK, exist_ok=True)
    rows = []
    with Pool(nw) as pool:
        for s in STRUCT:
            if not os.path.exists(os.path.join(root, s, "coord")):
                continue
            src = os.path.join(root, s, "coord")
            xyz, el = X.read_coord(src)
            chrg = X.read_int(os.path.join(root, s, ".CHRG"))
            uhf = X.read_int(os.path.join(root, s, ".UHF"))
            t = X.tblite(s, src, chrg, uhf, tag="an") if ENGINE == "tblite" else X.xtb("an", s, src, chrg, uhf, "0.0001", True)
            g = t.get("g")
            if g is None:
                print("%s\t%s\tno gradient (rc %s)" % (label, s, t.get("rc")), flush=True)
                rows.append("%s\t%s\tno gradient (rc %s)" % (label, s, t.get("rc")))
                continue
            jobs, idx = [], []
            for i in range(xyz.shape[0]):
                for c in range(3):
                    for k in (-2, -1, 1, 2):
                        x = xyz.copy()
                        x[i, c] += k * H
                        jobs.append(("fd%d_%d_%+d" % (i, c, k), s, x, el, chrg, uhf))
                        idx.append((i, c, k))
            es = pool.map(energy, jobs)
            E = {k: e for k, e in zip(idx, es)}
            if any(e is None for e in es):
                rows.append("%s\t%s\tfinite-difference energies failed (%d of %d)" % (label, s, sum(e is None for e in es), len(es)))
                print(rows[-1], flush=True)
                continue
            fd = np.zeros_like(xyz)
            for i in range(xyz.shape[0]):
                for c in range(3):
                    fd[i, c] = (8 * (E[i, c, 1] - E[i, c, -1]) - (E[i, c, 2] - E[i, c, -2])) / (12 * H)
            err = np.abs(g - fd)
            cen = xyz - xyz.mean(axis=0)
            rows.append("%s\t%s\t%d\t%.12f\t%.6e\t%.6e\t%.6e\t%.3e\t%.3e\t%.3e\t%s" % (
                label, s, len(el), t["E"], np.linalg.norm(g), np.linalg.norm(fd), err.max(),
                np.linalg.norm(g - fd) / np.linalg.norm(fd), np.abs(np.cross(cen, g).sum(0)).max(),
                np.abs(np.cross(cen, fd).sum(0)).max(),
                "worst atom %d %s" % (int(err.max(axis=1).argmax()) + 1, el[int(err.max(axis=1).argmax())])))
            print(rows[-1], flush=True)
    with open(out, "a") as fh:
        fh.write("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
