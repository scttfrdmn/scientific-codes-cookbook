#!/usr/bin/env python3
"""Every annotated CAMELYON16 slide, read at full resolution over its tissue, checked against the
publisher's own artifacts.

    python3 -u real.py <workers> <budget_gb> slides.tsv <outdir>

One process per slide, largest first. Each slide's four objects are fetched from RODA over HTTPS
(public, same region) into /tmp/work under a byte budget, hashed while they stream, analysed, and
deleted -- files this process created, never staged inputs.

The level-0 pass visits every 1024x1024 block that has tissue under it (the publisher's own tissue
mask, background_tissue/) or that a polygon touches. Per slide, asserted -- a failure is recorded
and fails the run at the end, after every slide's evidence is written:

  md5           image, mask and XML match the publisher's checksums.md5; the tissue mask its pin
                (S3's single-part ETag, in slides.tsv)
  aligned       WSI and mask have the same level-0 size, and every level the mask has is the
                slide's same level (the mask's pyramid may stop one level short)
  pyramid       level_downsamples exactly 2**k, each level's size follows from level 0
  two_readers   every visited block is BYTE-IDENTICAL through openslide's read_region and through
                tifffile's zarr view of the same TIFF page -- two independent tile readers
  labels        the level-0 mask carries only {0, 1, 2}
  mask_pyramid  mask levels 1-4 are exactly level 0 subsampled, level_k == level_0[::2**k, ::2**k]
                (probe: top-left subsampling matched with 0 mismatches where max, min and both
                means did not)
  polygons      the annotations painted largest first (Tumor as tumour, Exclusion as not, so the
                innermost polygon decides a pixel), each rasterised at LEVEL 0 by nonzero winding
                at pixel centres, agree with mask==2 everywhere except within BOUNDARY_PX of an
                outline edge (exact point-to-segment distance), in both directions -- bar the one
                publisher omission in KNOWN_OMITTED, which must itself be observed
  exclusion     no pixel inside an Exclusion polygon and farther than BOUNDARY_PX from its edge is
                labelled tumour (the negative control, where Exclusion lies inside Tumor)
  coverage      no tumour pixel in the level-4 mask falls outside the visited blocks, so the
                level-0 pass saw all of it

Reported, not asserted: Dice of polygons vs mask; the distance histogram of every disagreement;
how many pixels the fill rule decides (inside by nonzero winding, outside by even-odd) and how
the mask labels them; the tissue-patch and tumour-patch counts of a 256x256 level-0 patch grid
(what a patch-extraction pipeline emits) with a sha256 of that manifest; and a sha256 and RGB sum
over every decoded block -- compared across chips by compare.py.
"""
import hashlib
import json
import multiprocessing as mp
import os
import resource
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

RODA = "https://camelyon-dataset.s3.us-west-2.amazonaws.com/CAMELYON16/"
WORK = "/tmp/work"
B = 1024             # level-0 block; a multiple of every slide's 512x512 TIFF tile (probe)
P = 256              # level-0 patch, the unit a patch-extraction pipeline writes
# A disagreement between polygon and mask must lie within this many level-0 px of an outline
# edge: ~0.9 um, a fraction of one nucleus, and 8x tighter than the recipe's level-4 check (2 px
# there = 32 px here). Canary, 6 slides: 99.9% of disagreements within 1 px, max 2.94.
BOUNDARY_PX = 4.0
DIST_EDGES = np.array([0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 10.0])   # disagreement distance histogram
COV_LEVEL = 4        # coverage check level: a block is exactly 64x64 there
PYR_LEVELS = 4       # mask levels 1..4 checked against level-0 subsampling
TUMOUR_GROUPS = {"Tumor", "_0", "_1"}      # ASAP's names; README: Tumor, cut by Exclusion
EXCL_GROUPS = {"Exclusion", "_2"}
# The one place the publisher's mask and its own annotations disagree on the innermost rule:
# tumor_088's "Annotation 10" is an 8-vertex Tumor polygon (6,207 px^2, ~370 um^2) lying wholly
# inside Exclusion "Annotation 9", which lies inside Tumor "Annotation 2" -- the same nesting as
# test_026's ten islands, which the mask keeps -- yet the mask leaves it unlabelled (runs 1-2).
# Pixels far from an outline inside a polygon named here are tallied as known_omitted_px rather
# than failed; the run fails if a listed omission is NOT observed, so this cannot go stale.
KNOWN_OMITTED = {"tumor_088": {"Annotation 10"}}

_budget = None
_cond = None


def init(budget, cond):
    global _budget, _cond
    _budget, _cond = budget, cond
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"


def reserve(n):
    with _cond:
        while _budget.value < n:
            _cond.wait()
        _budget.value -= n


def release(n):
    with _cond:
        _budget.value += n
        _cond.notify_all()


def fetch(key, dest):
    for attempt in range(3):
        try:
            h = hashlib.md5()
            with urllib.request.urlopen(RODA + key, timeout=300) as r, open(dest, "wb") as fh:
                while True:
                    b = r.read(8 << 20)
                    if not b:
                        break
                    h.update(b)
                    fh.write(b)
            return h.hexdigest()
        except Exception:                                          # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(5)


def shoelace(co):
    return abs(0.5 * float(np.sum(co[:, 0] * np.roll(co[:, 1], -1) - np.roll(co[:, 0], -1) * co[:, 1])))


def polygons(xml):
    """Tumor and Exclusion polygons, and all of them largest-area first (for paint())."""
    tum, exc, order, groups = [], [], [], {}
    for ann in ET.parse(xml).getroot().findall(".//Annotations/Annotation"):
        g = ann.get("PartOfGroup")
        groups[g] = groups.get(g, 0) + 1
        co = np.array([(float(c.get("X")), float(c.get("Y"))) for c in ann.findall(".//Coordinate")])
        if len(co) < 3:
            continue
        item = (co, co[:, 0].min(), co[:, 0].max(), co[:, 1].min(), co[:, 1].max())
        if g in TUMOUR_GROUPS:
            tum.append(item)
        elif g in EXCL_GROUPS:
            exc.append(item)
        else:
            raise ValueError("annotation group %r is neither tumour nor exclusion" % g)
        order.append((item, g in TUMOUR_GROUPS, ann.get("Name"), shoelace(co)))
    order.sort(key=lambda o: -o[3])          # stable: equal areas keep document order
    return tum, exc, order, groups


def paint(order, x0, y0, w, h, only=None):
    """The mask the annotations define: every polygon painted largest first, Tumor as tumour and
    Exclusion as not, so the INNERMOST polygon containing a pixel decides it. That is "Tumor minus
    Exclusion" everywhere except a Tumor polygon nested inside an Exclusion polygon -- an island of
    tumour in a cut-out, which subtraction would erase and the mask keeps (run 1: 3 slides failed
    subtraction; document order failed 5 others in run 2). Also returns where any polygon's
    nonzero and even-odd fills disagree, which pixels an Exclusion decided, and, for the names in
    `only`, the pixels those polygons cover."""
    lab = np.zeros((h, w), bool)
    excl_last = np.zeros((h, w), bool)
    rule = np.zeros((h, w), bool)
    named = np.zeros((h, w), bool)
    for item, is_tumour, name, _ in order:
        if item[1] > x0 + w or item[2] < x0 or item[4] < y0 or item[3] > y0 + h:
            continue
        nz, eo = winding_raster([item], x0, y0, w, h)
        if not nz.any():
            continue
        lab[nz] = is_tumour
        excl_last[nz] = not is_tumour
        rule |= nz & ~eo
        if only and name in only:
            named |= nz
    return lab, excl_last, rule, named


def winding_raster(polys, x0, y0, w, h):
    """Per-pixel winding number of each polygon over a w x h window at (x0, y0), at pixel centres
    (x + 0.5, y + 0.5): a scanline crossing count, exact for any polygon. Returns two masks over
    the union of polygons: inside by nonzero winding, and inside by even-odd parity."""
    nz = np.zeros((h, w), bool)
    eo = np.zeros((h, w), bool)
    yc = y0 + 0.5 + np.arange(h)
    for co, xa, xb, ya, yb in polys:
        if xa > x0 + w or yb < y0 or ya > y0 + h:
            continue
        x1, y1 = co[:, 0], co[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        keep = (np.maximum(y1, y2) >= y0) & (np.minimum(y1, y2) <= y0 + h) & (np.minimum(x1, x2) < x0 + w)
        if not keep.any():
            continue
        x1, y1, x2, y2 = x1[keep], y1[keep], x2[keep], y2[keep]
        Y = yc[:, None]
        up = (y1 <= Y) & (y2 > Y)
        dn = (y2 <= Y) & (y1 > Y)
        cross = up | dn
        if not cross.any():
            continue
        xi = x1 + (Y - y1) * (x2 - x1) / np.where(y2 != y1, y2 - y1, 1.0)
        rows, ei = np.nonzero(cross)
        xs = xi[rows, ei]
        dirn = np.where(up[rows, ei], 1, -1)
        # a crossing at x counts for every pixel centre to its right: column ceil(x - x0 - 0.5)
        col = np.ceil(xs - x0 - 0.5).astype(np.int64)
        ok = col < w
        col = np.clip(col[ok], 0, w)
        diff = np.zeros((h, w + 1), np.int64)
        np.add.at(diff, (rows[ok], col), dirn[ok])
        wn = np.cumsum(diff[:, :w], axis=1)
        nz |= wn != 0
        eo |= (wn % 2) != 0
    return nz, eo


def segments(polys):
    """Every outline edge of every polygon, closed, as rows x1 y1 x2 y2."""
    if not polys:
        return np.zeros((0, 4))
    return np.concatenate([np.hstack([co, np.roll(co, -1, axis=0)]) for co, *_ in polys])


def edge_distance(px, py, segs):
    """Exact Euclidean distance from each point to the nearest outline edge (level-0 px)."""
    out = np.full(len(px), np.inf)
    if len(segs) == 0:
        return out
    lo_x, hi_x = px.min() - 64, px.max() + 64
    lo_y, hi_y = py.min() - 64, py.max() + 64
    s = segs[(np.maximum(segs[:, 0], segs[:, 2]) >= lo_x) & (np.minimum(segs[:, 0], segs[:, 2]) <= hi_x)
             & (np.maximum(segs[:, 1], segs[:, 3]) >= lo_y) & (np.minimum(segs[:, 1], segs[:, 3]) <= hi_y)]
    if len(s) == 0:
        s = segs
    x1, y1, x2, y2 = s[:, 0], s[:, 1], s[:, 2], s[:, 3]
    dx, dy = x2 - x1, y2 - y1
    ll = np.where(dx * dx + dy * dy > 0, dx * dx + dy * dy, 1.0)
    for i in range(0, len(px), 2048):
        qx, qy = px[i:i + 2048, None], py[i:i + 2048, None]
        t = np.clip(((qx - x1) * dx + (qy - y1) * dy) / ll, 0.0, 1.0)
        out[i:i + 2048] = np.sqrt(((x1 + t * dx - qx) ** 2 + (y1 + t * dy - qy) ** 2).min(axis=1))
    return out


def touches(polys, x0, y0, w, h):
    return any(not (xb < x0 or xa > x0 + w or yb < y0 or ya > y0 + h) for _, xa, xb, ya, yb in polys)


def analyse(row):
    import openslide
    import tifffile
    import zarr
    zarr.config.set({"threading.max_workers": 1})

    slide = row["slide"]
    r = {"slide": slide, "center": row["center"], "class": row["class"], "fail": []}
    t_start = time.time()
    need = int(row["image_bytes"]) + int(row["mask_bytes"]) + 2 * (1 << 20)
    reserve(need)
    d = os.path.join(WORK, slide)
    os.makedirs(d, exist_ok=True)
    paths = {}
    try:
        t = time.time()
        for kind, key, want in (
                ("wsi", "images/%s.tif" % slide, row["image_md5"]),
                ("mask", "masks/%s_mask.tif" % slide, row["mask_md5"]),
                ("xml", "annotations/%s.xml" % slide, row["xml_md5"]),
                ("tissue", "background_tissue/%s_tissue.tif" % slide, row["tissue_md5"])):
            p = os.path.join(d, os.path.basename(key))
            md5 = fetch(key, p)
            paths[kind] = p
            if md5 != want:
                r["fail"].append("md5 %s %s != %s" % (kind, md5, want))
        r["fetch_s"] = round(time.time() - t, 2)
        r["bytes"] = sum(os.path.getsize(p) for p in paths.values())
        if r["fail"]:
            return r

        t = time.time()
        wsi = openslide.OpenSlide(paths["wsi"])
        msk = openslide.OpenSlide(paths["mask"])
        W, H = wsi.dimensions
        r["dims"] = "%dx%d" % (W, H)
        r["levels"] = wsi.level_count
        r["mpp"] = wsi.properties.get("openslide.mpp-x")
        r["mask_levels"] = msk.level_count
        if (wsi.dimensions != msk.dimensions or msk.level_count > wsi.level_count
                or tuple(wsi.level_dimensions[:msk.level_count]) != tuple(msk.level_dimensions)):
            r["fail"].append("aligned: wsi %s mask %s" % (wsi.level_dimensions, msk.level_dimensions))
        for k, (ds, (w, h)) in enumerate(zip(wsi.level_downsamples, wsi.level_dimensions)):
            if abs(ds - 2 ** k) > 1e-9 or abs(w - W / 2 ** k) > 1 or abs(h - H / 2 ** k) > 1:
                r["fail"].append("pyramid: level %d ds %.6f dims %s" % (k, ds, (w, h)))
                break
        if r["fail"]:
            return r

        tum, exc, order, groups = polygons(paths["xml"])
        known = KNOWN_OMITTED.get(slide, set())
        r["groups"] = groups
        with tifffile.TiffFile(paths["tissue"]) as tf:
            tissue = tf.pages[0].asarray() > 0
        sy, sx = H / tissue.shape[0], W / tissue.shape[1]
        r["tissue_scale"] = [round(sx, 6), round(sy, 6)]

        # the block set: every level-0 block with tissue under it, plus every block a polygon touches
        nbx, nby = (W + B - 1) // B, (H + B - 1) // B
        visit = np.zeros((nby, nbx), bool)
        for by in range(nby):
            ta, tb = int(by * B / sy), int(min(H, (by + 1) * B) / sy) + 1
            row_t = tissue[ta:tb]
            if not row_t.any():
                continue
            for bx in range(nbx):
                xa, xb = int(bx * B / sx), int(min(W, (bx + 1) * B) / sx) + 1
                if row_t[:, xa:xb].any():
                    visit[by, bx] = True
        r["blocks_tissue"] = int(visit.sum())
        for co, xa, xb, ya, yb in tum + exc:
            visit[max(0, int(ya) // B):min(nby, int(yb) // B + 1),
                  max(0, int(xa) // B):min(nbx, int(xb) // B + 1)] = True
        r["blocks_total"] = nbx * nby
        r["blocks_visited"] = int(visit.sum())

        # coverage: all tumour in the level-4 mask lies in visited blocks
        cl = COV_LEVEL
        m4 = np.asarray(msk.read_region((0, 0), cl, msk.level_dimensions[cl]))[..., 0]
        cov = np.repeat(np.repeat(visit, B >> cl, axis=0), B >> cl, axis=1)[:m4.shape[0], :m4.shape[1]]
        outside = int(((m4 == 2) & ~cov).sum())
        r["tumour_l4_outside_visited"] = outside
        if outside:
            r["fail"].append("coverage: %d level-%d tumour px outside visited blocks" % (outside, cl))
        del m4, cov

        segs = segments(tum + exc)
        tfw = tifffile.TiffFile(paths["wsi"])
        tfm = tifffile.TiffFile(paths["mask"])
        zw = zarr.open(tfw.aszarr(level=0), mode="r")
        zm = zarr.open(tfm.aszarr(level=0), mode="r")
        zmk = [None] + [zarr.open(tfm.aszarr(level=k), mode="r") for k in range(1, PYR_LEVELS + 1)]
        digest = hashlib.sha256()
        manifest = hashlib.sha256()
        rgb_sum = np.zeros(3, np.uint64)
        lab = np.zeros(256, np.int64)
        dhist = np.zeros(len(DIST_EDGES) + 1, np.int64)
        c = dict.fromkeys(("mism_px", "alpha_px", "pyr_mism", "tumour_px", "truth_px", "inter",
                           "dis", "far_in", "far_out", "excl_px", "excl_far", "rule_px",
                           "rule_px_tumour", "island_px", "island_px_tumour", "far_known", "p_tissue", "p_any", "p_tumour"), 0)
        dmax = 0.0
        where = []
        tm = dict.fromkeys(("openslide", "tifffile", "mask", "pyramid", "patches", "polygons"), 0.0)
        for by, bx in zip(*np.nonzero(visit)):
            x0, y0 = int(bx) * B, int(by) * B
            w, h = min(B, W - x0), min(B, H - y0)
            tq = time.time()
            a = np.asarray(wsi.read_region((x0, y0), 0, (w, h)))
            tm["openslide"] += time.time() - tq
            c["alpha_px"] += int((a[..., 3] != 255).sum())
            a = np.ascontiguousarray(a[..., :3])
            tq = time.time()
            b = np.asarray(zw[y0:y0 + h, x0:x0 + w])
            tm["tifffile"] += time.time() - tq
            if b.shape != a.shape:
                c["mism_px"] += w * h
            elif not np.array_equal(a, b):
                c["mism_px"] += int((a != b).any(axis=2).sum())
            digest.update(a.data)
            rgb_sum += a.sum(axis=(0, 1), dtype=np.uint32).astype(np.uint64)

            tq = time.time()
            m = np.asarray(zm[y0:y0 + h, x0:x0 + w])
            if m.ndim == 3:
                m = m[..., 0]
            lab += np.bincount(m.ravel(), minlength=256)
            is_t = (m == 2)
            nt = int(is_t.sum())
            c["tumour_px"] += nt
            tm["mask"] += time.time() - tq

            tq = time.time()
            for k in range(1, PYR_LEVELS + 1):
                ref_ = m[::1 << k, ::1 << k]
                got = np.asarray(zmk[k][(y0 >> k):(y0 >> k) + ref_.shape[0],
                                        (x0 >> k):(x0 >> k) + ref_.shape[1]])
                if got.ndim == 3:
                    got = got[..., 0]
                if got.shape != ref_.shape:
                    ref_ = ref_[:got.shape[0], :got.shape[1]]
                c["pyr_mism"] += int((got != ref_).sum())
            tm["pyramid"] += time.time() - tq

            # the patch grid a pipeline would write: tissue = publisher tissue mask under the patch
            tq = time.time()
            tp_grid = is_t[:h - h % P, :w - w % P].reshape(h // P, P, w // P, P).sum(axis=(1, 3))
            for iy in range(h // P):
                py = iy * P
                ta, tb = int((y0 + py) / sy), int((y0 + py + P) / sy) + 1
                for ix in range(w // P):
                    px = ix * P
                    xa, xb = int((x0 + px) / sx), int((x0 + px + P) / sx) + 1
                    tis = bool(tissue[ta:tb, xa:xb].any())
                    tp = int(tp_grid[iy, ix])
                    c["p_tissue"] += tis
                    c["p_any"] += tp > 0
                    c["p_tumour"] += 2 * tp >= P * P
                    manifest.update(b"%d,%d,%d,%d\n" % (x0 + px, y0 + py, tis, tp))
            tm["patches"] += time.time() - tq

            tq = time.time()
            if touches(tum + exc, x0 - 8, y0 - 8, w + 16, h + 16):
                truth, xe, rule, kn = paint(order, x0, y0, w, h, known)
                ntr = int(truth.sum())
                c["truth_px"] += ntr
                c["inter"] += int((truth & is_t).sum())
                c["excl_px"] += int(xe.sum())
                if exc:
                    # where painting in order differs from "Tumor minus Exclusion": nested islands
                    tnz, _ = winding_raster(tum, x0, y0, w, h)
                    enz, _ = winding_raster(exc, x0, y0, w, h)
                    isl = truth & ~(tnz & ~enz)
                    if isl.any():
                        c["island_px"] += int(isl.sum())
                        c["island_px_tumour"] += int((isl & is_t).sum())
                if rule.any():
                    c["rule_px"] += int(rule.sum())
                    c["rule_px_tumour"] += int((rule & is_t).sum())
                dis = truth ^ is_t
                if dis.any():
                    yy, xx = np.nonzero(dis)
                    dd = edge_distance(xx + x0 + 0.5, yy + y0 + 0.5, segs)
                    c["dis"] += len(dd)
                    dhist += np.bincount(np.searchsorted(DIST_EDGES, dd, side="left"),
                                         minlength=len(DIST_EDGES) + 1)
                    dmax = max(dmax, float(dd.max()))
                    far = dd > BOUNDARY_PX
                    if known:
                        # a publisher annotation the mask omits (KNOWN_OMITTED): tallied apart
                        kf = far & kn[yy, xx] & truth[yy, xx]
                        c["far_known"] += int(kf.sum())
                        far &= ~kf
                    if far.any():
                        tv = truth[yy, xx]
                        c["far_in"] += int((far & tv).sum())
                        c["far_out"] += int((far & ~tv).sum())
                        c["excl_far"] += int((far & xe[yy, xx]).sum())
                        for i in np.nonzero(far)[0][:max(0, 40 - len(where))]:
                            where.append("%s d=%.2f @%d,%d" % (
                                "inside_not_tumour" if tv[i] else "outside_tumour", dd[i],
                                x0 + int(xx[i]), y0 + int(yy[i])))
            elif nt:
                # no polygon within reach of this block: any tumour here is far from every outline
                c["dis"] += nt
                c["far_out"] += nt
                dhist[-1] += nt
                dmax = float("inf")
                yy, xx = np.nonzero(is_t)
                for i in range(min(len(yy), max(0, 40 - len(where)))):
                    where.append("outside_tumour d=inf @%d,%d" % (x0 + int(xx[i]), y0 + int(yy[i])))
            tm["polygons"] += time.time() - tq
        tfw.close()
        tfm.close()
        wsi.close()
        msk.close()

        labels = [int(v) for v in np.nonzero(lab)[0]]
        r.update({
            "two_readers_mismatched_px": c["mism_px"], "alpha_not_255_px": c["alpha_px"],
            "labels": labels, "label_counts_l0": {str(v): int(lab[v]) for v in labels},
            "mask_pyramid_mismatch_px": c["pyr_mism"],
            "tumour_px_l0": c["tumour_px"], "polygon_px_l0": c["truth_px"],
            "dice": round(2.0 * c["inter"] / max(1, c["truth_px"] + c["tumour_px"]), 7),
            "disagree_px": c["dis"], "disagree_dist_hist": [int(v) for v in dhist],
            "disagree_dist_max": round(dmax, 4),
            "far_inside_not_tumour": c["far_in"], "far_outside_tumour": c["far_out"],
            "exclusion_px": c["excl_px"], "exclusion_far_tumour": c["excl_far"],
            "fill_rule_px": c["rule_px"], "fill_rule_px_tumour": c["rule_px_tumour"],
            "island_px": c["island_px"], "island_px_tumour": c["island_px_tumour"],
            "known_omitted": sorted(known), "known_omitted_px": c["far_known"],
            "patches_tissue": c["p_tissue"], "patches_any_tumour": c["p_any"],
            "patches_tumour50": c["p_tumour"],
            "rgb_sum": [int(v) for v in rgb_sum], "pixels_sha256": digest.hexdigest(),
            "manifest_sha256": manifest.hexdigest(), "where": where,
            "phase_s": {k: round(v, 2) for k, v in tm.items()},
        })
        if c["mism_px"]:
            r["fail"].append("two_readers: %d px differ" % c["mism_px"])
        if c["pyr_mism"]:
            r["fail"].append("mask pyramid: %d px differ from level-0 subsampling" % c["pyr_mism"])
        if not set(labels) <= {0, 1, 2}:
            r["fail"].append("labels: %s" % labels)
        if c["truth_px"] == 0:
            r["fail"].append("polygons: no polygon pixels -- vacuous")
        if c["far_in"] or c["far_out"]:
            r["fail"].append("polygons: %d px unlabelled inside, %d labelled outside, beyond %s px "
                             "of an outline" % (c["far_in"], c["far_out"], BOUNDARY_PX))
        if c["excl_far"]:
            r["fail"].append("exclusion: %d px labelled tumour" % c["excl_far"])
        if known and c["far_known"] == 0:
            r["fail"].append("known omission %s not observed -- KNOWN_OMITTED is stale" % sorted(known))
        r["analyse_s"] = round(time.time() - t, 2)
    except Exception:                                              # noqa: BLE001
        r["fail"].append("exception: " + traceback.format_exc(limit=3).replace("\n", " | "))
    finally:
        for p in paths.values():
            try:
                os.unlink(p)
            except OSError:
                pass
        release(need)
        r["wall_s"] = round(time.time() - t_start, 2)
        r["rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    return r


def main():
    workers, budget_gb, slides_tsv, out = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4]
    os.makedirs(out, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    rows = []
    with open(slides_tsv) as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            rows.append(dict(zip(hdr, line.rstrip("\n").split("\t"))))
    only = os.environ.get("SLIDES")
    if only:
        rows = [x for x in rows if x["slide"] in only.split(",")]
    rows.sort(key=lambda x: -int(x["image_bytes"]))
    print("slides\t%d\t%.1f GB" % (len(rows), sum(int(x["image_bytes"]) for x in rows) / 1e9), flush=True)

    budget = mp.Value("q", int(budget_gb * 1e9), lock=False)
    cond = mp.Condition()
    t0 = time.time()
    results = []
    with mp.Pool(workers, initializer=init, initargs=(budget, cond), maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(analyse, rows):
            results.append(r)
            print("%s\t%s\t%s\tfetch %s analyse %s wall %s\t%s" % (
                r["slide"], r.get("dims"), r.get("blocks_visited"), r.get("fetch_s"),
                r.get("analyse_s"), r.get("wall_s"), "FAIL " + "; ".join(r["fail"]) if r["fail"] else "ok"),
                flush=True)
    wall = time.time() - t0
    results.sort(key=lambda x: x["slide"])
    with open(os.path.join(out, "slides.jsonl"), "w") as fh:
        for r in results:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    fails = [r for r in results if r["fail"]]

    def tot(k):
        return sum(int(r.get(k, 0) or 0) for r in results)

    hist = np.zeros(len(DIST_EDGES) + 1, np.int64)
    for r in results:
        if r.get("disagree_dist_hist"):
            hist += np.array(r["disagree_dist_hist"])
    summary = {
        "slides": len(results), "failed": len(fails), "wall_s": round(wall, 1), "workers": workers,
        "boundary_px": BOUNDARY_PX, "dist_edges": DIST_EDGES.tolist(),
        "bytes_fetched": tot("bytes"), "blocks_visited": tot("blocks_visited"),
        "decoded_px": sum(int(r.get("blocks_visited", 0) or 0) for r in results) * B * B,
        "two_readers_mismatched_px": tot("two_readers_mismatched_px"),
        "mask_pyramid_mismatch_px": tot("mask_pyramid_mismatch_px"),
        "tumour_px_l0": tot("tumour_px_l0"), "polygon_px_l0": tot("polygon_px_l0"),
        "disagree_px": tot("disagree_px"), "disagree_dist_hist": hist.tolist(),
        "disagree_dist_max": max((r.get("disagree_dist_max", 0) or 0 for r in results), default=0),
        "far_inside_not_tumour": tot("far_inside_not_tumour"), "far_outside_tumour": tot("far_outside_tumour"),
        "exclusion_slides": sum(1 for r in results if r.get("exclusion_px")),
        "exclusion_px": tot("exclusion_px"), "exclusion_far_tumour": tot("exclusion_far_tumour"),
        "fill_rule_slides": sum(1 for r in results if r.get("fill_rule_px")),
        "fill_rule_px": tot("fill_rule_px"), "fill_rule_px_tumour": tot("fill_rule_px_tumour"),
        "island_slides": sum(1 for r in results if r.get("island_px")),
        "island_px": tot("island_px"), "island_px_tumour": tot("island_px_tumour"),
        "known_omitted_px": tot("known_omitted_px"),
        "tumour_l4_outside_visited": tot("tumour_l4_outside_visited"),
        "patches_tissue": tot("patches_tissue"), "patches_any_tumour": tot("patches_any_tumour"),
        "patches_tumour50": tot("patches_tumour50"),
        "max_worker_rss_mib": max((r.get("rss_mib", 0) for r in results), default=0),
        "fetch_s_sum": round(sum(r.get("fetch_s", 0) or 0 for r in results), 1),
        "analyse_s_sum": round(sum(r.get("analyse_s", 0) or 0 for r in results), 1),
    }
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    print(json.dumps(summary, sort_keys=True), flush=True)
    for r in fails:
        print("FAIL\t%s\t%s" % (r["slide"], "; ".join(r["fail"])), flush=True)
    if fails or len(results) != len(rows):
        sys.exit("FAIL: %d of %d slides" % (len(fails), len(rows)))
    print("PATHOLOGY-REAL OK", flush=True)


if __name__ == "__main__":
    main()
