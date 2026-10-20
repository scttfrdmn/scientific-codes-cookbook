#!/usr/bin/env python3
"""Compare the three chips' psi4.jsonl, geometry by geometry and column by column.

    python3 measurements/psi4-real/collect.py <dir holding arm64/ x86_64/ amd/ psi4.jsonl>

Prints max |difference| (kcal/mol) and how many geometries are bit-identical, Intel and AMD each against
Graviton4, and Intel against AMD (the same x86 image on both)."""
import json
import os
import sys

COLS = ["sapt_es", "sapt_ex", "sapt_exs2", "sapt_ind", "sapt_exind", "sapt_disp", "sapt_exdisp_os", "sapt_exdisp_ss",
        "sapt_delta_HF", "sapt_all", "tz_HF", "tz_MP2_os", "tz_MP2_ss", "tz_MP2_all"]


def load(path):
    out = {}
    for line in open(path):
        r = json.loads(line)
        key = (r["geom_id"], "rot" if r.get("rotated") else "ctl" if r.get("control") else "base")
        if key in out:
            raise SystemExit("duplicate %s in %s" % (key, path))
        out[key] = r
    return out


def main(d):
    runs = {a: load(os.path.join(d, a, "psi4.jsonl")) for a in ("arm64", "x86_64", "amd")}
    for a, b in (("x86_64", "arm64"), ("amd", "arm64"), ("x86_64", "amd")):
        keys = sorted(set(runs[a]) & set(runs[b]))
        base = [k for k in keys if k[1] == "base"]
        print("== %s vs %s: %d results in common (%d base geometries)" % (a, b, len(keys), len(base)))
        for c in COLS:
            dd = [(abs(runs[a][k][c] - runs[b][k][c]), runs[a][k]["row"]) for k in keys if c in runs[a][k] and c in runs[b][k]]
            same = sum(1 for v, _ in dd if v == 0.0)
            worst = max(dd)
            print("  %-15s max |d| %.2e kcal/mol (row %d), median %.2e, bit-identical %d of %d"
                  % (c, worst[0], worst[1], sorted(dd)[len(dd) // 2][0], same, len(dd)))


if __name__ == "__main__":
    main(sys.argv[1])
