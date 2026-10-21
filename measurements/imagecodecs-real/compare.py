#!/usr/bin/env python3
"""Compare every per-slide observable except timings across the three chips.

    python3 -I compare.py results/        # reads results/{arm64,x86_64,amd}/slides.jsonl

ASSERTED fields are integer decodes, counts and hashes of decoded pixels: they must be identical
on every slide, and a difference exits 1. REPORTED fields are encoder outputs (libjxl, the bake-off
codecs' sizes) and libjxl's float pixel decode, which a chip may legitimately change.
"""
import json
import os
import sys

ASSERTED = ["wsi_tiles", "wsi_tile_bytes", "mask_tiles", "mask_empty_tiles", "pruned_equal",
            "jxl_exact", "tiff_tile_recovered", "mask_two_decoders_equal", "mask_sha256",
            "pixels_sha256", "mask_labels_l0_tiles", "levels.jpeg_bytes",
            "levels.pruned_bytes", "bake.tiles", "bake.px"]
REPORTED = ["jxl_sha256", "levels.jxl_bytes", "jxl_vs_turbo_absdiff_hist_l2plus"]


def get(r, k):
    for p in k.split("."):
        r = (r or {}).get(p)
    return r


def load(d, chip):
    p = os.path.join(d, chip, "slides.jsonl")
    if not os.path.exists(p):
        return None
    return {r["slide"]: r for r in map(json.loads, open(p))}


def main():
    d = sys.argv[1]
    base = load(d, "arm64")
    bad = 0
    for chip in ("x86_64", "amd"):
        other = load(d, chip)
        if other is None:
            print("%s\tno results" % chip)
            continue
        common = sorted(set(base) & set(other))
        print("== %s vs arm64: %d slides in common (arm64 %d, %s %d)" % (chip, len(common), len(base), chip, len(other)))
        if len(common) != len(base) or len(other) != len(base):
            bad += 1
        for kind, fields in (("asserted", ASSERTED), ("reported", REPORTED)):
            for k in fields:
                same = sum(1 for s in common if get(base[s], k) == get(other[s], k))
                print("%s\t%-36s\t%d / %d identical" % (kind, k, same, len(common)))
                if kind == "asserted" and same != len(common):
                    bad += 1
        codecs = sorted((get(base[common[0]], "bake.codecs") or {}).keys()) if common else []
        for c in codecs:
            same = sum(1 for s in common if get(base[s], "bake.codecs")[c]["bytes"] == get(other[s], "bake.codecs")[c]["bytes"])
            tb = sum(get(base[s], "bake.codecs")[c]["bytes"] for s in common)
            to = sum(get(other[s], "bake.codecs")[c]["bytes"] for s in common)
            print("reported\tbake %-12s bytes\t%d / %d slides identical, total %d vs %d (%+.6f%%)" % (
                c, same, len(common), tb, to, 100.0 * (to - tb) / tb if tb else 0))
        jb = sum(sum(get(base[s], "levels.jxl_bytes")) for s in common)
        jo = sum(sum(get(other[s], "levels.jxl_bytes")) for s in common)
        print("reported\tjxl bytes total\t%d vs %d (%+.6f%%)" % (jb, jo, 100.0 * (jo - jb) / jb if jb else 0))
    print("COMPARE %s" % ("OK" if not bad else "FAIL (%d)" % bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
