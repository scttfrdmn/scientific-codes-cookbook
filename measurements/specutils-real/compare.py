#!/usr/bin/env python3
"""specutils-real: compare the three chips' per-fit outputs (pix.tar from each run), on AWS.

    python3 compare.py <dir>     <dir>/{arm64,x86_64,amd}/pix.tar  ->  <dir>/../out/crosschip.txt
"""
import io
import json
import math
import os
import sys
import tarfile

import numpy as np

D = sys.argv[1]
OUT = "/tmp/out"
CHIPS = ["arm64", "x86_64", "amd"]
KEYS = {"ref": ["FLUX", "SIGMA", "CONT", "EW", "FLUX_IVAR", "CHI2", "NDOF"],
        "su": ["FLUX", "SIGMA", "FLUX_IVAR", "EW", "CHI2", "BOX3", "CENTROID"],
        "su_analytic": ["FLUX", "SIGMA", "CHI2"]}
out = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out.append(s)


def load(chip):
    rows = {}
    chk = {}
    with tarfile.open(os.path.join(D, chip, "pix.tar")) as t:
        for m in t.getmembers():
            if not m.name.endswith(".json"):
                continue
            d = json.load(io.TextIOWrapper(t.extractfile(m)))
            for r in d["rows"]:
                rows[(r["pix"], r["j"], r["line"])] = r
            chk[os.path.basename(m.name)] = d["chk"]
    return rows, chk


data = {c: load(c) for c in CHIPS}
base = data["arm64"][0]
say("slots", {c: len(data[c][0]) for c in CHIPS})
for c in CHIPS[1:]:
    say("same slot keys %s vs arm64: %s" % (c, set(data[c][0]) == set(base)))


def diff(a, b, src, k):
    x = np.array([a[s][src].get(k, np.nan) if src in a[s] else np.nan for s in keys], float)
    y = np.array([b[s][src].get(k, np.nan) if src in b[s] else np.nan for s in keys], float)
    both = np.isfinite(x) & np.isfinite(y)
    nanmis = int((np.isfinite(x) != np.isfinite(y)).sum())
    eq = int((x[both] == y[both]).sum())
    rel = np.abs(x[both] - y[both]) / np.maximum(np.abs(x[both]), 1e-300)
    return both.sum(), eq, nanmis, (float(rel.max()) if rel.size else 0.0), x, y


keys = sorted(base)
for pair in (("arm64", "x86_64"), ("arm64", "amd"), ("x86_64", "amd")):
    a, b = data[pair[0]][0], data[pair[1]][0]
    say("== %s vs %s ==" % pair)
    for src, ks in KEYS.items():
        for k in ks:
            n, eq, nm, rmax, x, y = diff(a, b, src, k)
            extra = ""
            if src in ("ref", "su") and k == "FLUX":
                iv = np.array([a[s]["cat"]["FLUX_IVAR"] for s in keys], float)
                ok = np.isfinite(x) & np.isfinite(y) & (iv > 0)
                ds = np.abs(x[ok] - y[ok]) * np.sqrt(iv[ok])
                extra = "  |dF|/sigma_F max %.3e, > 1e-3 in %d" % (ds.max() if ds.size else 0, int((ds > 1e-3).sum()))
            say("%-12s %-10s %7d finite pairs, %7d bit-identical, %d NaN-pattern mismatches, max rel %.3e%s" % (
                src, k, n, eq, nm, rmax, extra))


# The fits are optimiser paths to a fixed point: compare them on check.py's well-posed set (from
# Graviton4's rows), in units of each parameter's own error, and by chi2.
def wellposed(r):
    c, f = r["cat"], r["ref"]
    if not (np.isfinite(c["FLUX"]) and c["FLUX_IVAR"] > 0 and "su" in r and np.isfinite(r["su"].get("VAR_ABS", np.nan))):
        return False
    if not (0.34 <= f["SIGMA"] < 9.99) or (r["line"] == "OII" and not (0.101 < f["SHARE"] < 0.99)):
        return False
    return c["FLUX"] * math.sqrt(c["FLUX_IVAR"]) >= 3.0


wp = [s for s in keys if wellposed(base[s])]
say("== well-posed fits (Graviton4's definition): %d ==" % len(wp))
for pair in (("arm64", "x86_64"), ("arm64", "amd")):
    a, b = data[pair[0]][0], data[pair[1]][0]
    both = [s for s in wp if "su" in b[s] and b[s]["ref"]["NDOF"] >= 0]
    sab = np.array([math.sqrt(a[s]["su"]["VAR_ABS"]) for s in both])
    for src in ("ref", "su"):
        F = np.array([[a[s][src]["FLUX"], b[s][src]["FLUX"]] for s in both])
        X = np.array([[a[s]["su"]["CHI2_AT_REF" if src == "ref" else "CHI2"], b[s]["su"]["CHI2_AT_REF" if src == "ref" else "CHI2"]] for s in both])
        dF = np.abs(F[:, 0] - F[:, 1]) / sab
        dX = np.abs(X[:, 0] - X[:, 1]) / X[:, 0]
        tol = 2 * np.sqrt(10 * 1e-8 * X[:, 0])
        diffmin = dX > 1e-7
        say("%s vs %s %-3s: %d fits, F bit-identical %d, |dF|/sigma median %.3e p99 %.3e max(same chi2 min) %.3e; chi2 bit-identical %d; "
            "different chi2 minimum (|dchi2|/chi2 > 1e-7) %d; |dF| > 2 sqrt(10 ftol chi2) at the same minimum %d" % (
                pair[0], pair[1], src, len(both), int((F[:, 0] == F[:, 1]).sum()), np.median(dF), np.percentile(dF, 99),
                dF[~diffmin].max(), int((X[:, 0] == X[:, 1]).sum()), int(diffmin.sum()), int((dF[~diffmin] > tol[~diffmin]).sum())))
        for s in [s for s, d in zip(both, diffmin) if d][:10]:
            say("    different minimum %s: %s sigma %.3f / %.3f  F %.4g / %.4g  chi2 %.6f / %.6f" % (
                src, s, a[s][src]["SIGMA"], b[s][src]["SIGMA"], a[s][src]["FLUX"], b[s][src]["FLUX"],
                a[s]["su"]["CHI2_AT_REF" if src == "ref" else "CHI2"], b[s]["su"]["CHI2_AT_REF" if src == "ref" else "CHI2"]))
    for s in keys:
        if a[s]["ref"]["NDOF"] != b[s]["ref"]["NDOF"]:
            say("    NDOF differs %s: %s vs %s, sigma %s / %s, F %s / %s, S/N %s" % (
                s, a[s]["ref"]["NDOF"], b[s]["ref"]["NDOF"], a[s]["ref"]["SIGMA"], b[s]["ref"]["SIGMA"], a[s]["ref"]["FLUX"],
                b[s]["ref"]["FLUX"], b[s]["ref"]["FLUX"] * math.sqrt(b[s]["ref"]["FLUX_IVAR"]) if b[s]["ref"]["FLUX_IVAR"] > 0 else "nan"))
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "crosschip.txt"), "w").write("\n".join(out) + "\n")
say("COMPARE DONE")
