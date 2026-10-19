#!/usr/bin/env python3
"""Compare the three chips' healpy-real outputs.

    python3 collect.py <dir>    # <dir>/{arm64,x86_64,amd}/{results.json,spectra.npz}

Auto-spectra (TT EE BB) are compared relatively; cross-spectra (TE EB TB) cross zero, so their
difference is scaled by sqrt(auto x auto) at the same l instead.
"""
import json
import os
import sys

import numpy as np

D = sys.argv[1]
NAMES = ("TT", "EE", "BB", "TE", "EB", "TB")
AUTO = {"TE": (0, 1), "EB": (1, 2), "TB": (0, 2)}


def load(chip):
    return json.load(open(os.path.join(D, chip, "results.json"))), np.load(os.path.join(D, chip, "spectra.npz"))


def spec_diff(a, b):
    out = []
    for i, n in enumerate(NAMES[:len(a)]):
        d = np.abs(b[i, 2:] - a[i, 2:])
        if n in AUTO:
            j, k = AUTO[n]
            d = d / np.sqrt(a[j, 2:] * a[k, 2:])
        else:
            d = d / a[i, 2:]
        out.append("%s %.1e" % (n, d.max()))
    return " ".join(out)


def compare(c0, c1):
    r0, s0 = load(c0)
    r1, s1 = load(c1)
    print("== %s vs %s" % (c1, c0))
    print("  %s: failed checks %s" % (c1, r1["failed"] or "none"))
    for k in ("hash_ring_I", "hash_ud_64"):
        print("  %-14s %s" % (k, "identical" if r0[k] == r1[k] else "DIFFERS"))
    print("  %-14s %s" % ("synalm a_lm", "identical" if r0["synfast"]["hash_alm"] == r1["synfast"]["hash_alm"] else "DIFFERS"))
    for k in ("cl_full", "cl_mask", "synalm_cl", "synfast_cl"):
        same = np.array_equal(s0[k], s1[k])
        print("  %-11s %s" % (k, "bit-identical" if same else "max diff, l>=2: " + spec_diff(s0[k], s1[k])))
    for k in ("first_peak", "random_ang2pix_disagree", "centre_sep_max_rad", "rotation", "roundtrip_rate",
              "synthesis_rel_err"):
        print("  %-24s %s %s | %s %s" % (k, c0, r0.get(k), c1, r1.get(k)))
    print("  %-24s %s %s | %s %s" % ("synfast z", c0, r0["synfast"]["z"], c1, r1["synfast"]["z"]))
    print("  %-24s %s %s | %s %s" % ("inpaint", c0, "same" if r0["inpaint"] == r1["inpaint"] else "DIFFERS", c1, ""))
    nd = [k for k in r0 if isinstance(r0[k], dict) and "detail" in r0[k] and r0[k]["detail"] != r1[k]["detail"]]
    print("  check details identical except: %s" % (", ".join(nd) or "none"))


compare("arm64", "x86_64")
compare("arm64", "amd")
compare("x86_64", "amd")
