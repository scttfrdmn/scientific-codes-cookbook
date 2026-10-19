#!/usr/bin/env python3
"""Probe, before sizing or writing any check: what a level-0 tiled pass over CAMELYON16 costs.

Run with `python3 -u probe.py <slide> ...`. Asserts nothing. For each slide it measures the
HTTPS download rate from RODA and the md5 rate, the TIFF layout (compression, photometric, tile
size) as tifffile sees it, the level geometry as openslide sees it, the publisher's tissue mask,
whether openslide and tifffile return the same level-0 bytes, how the mask's pyramid levels relate
to level 0, and the per-block decode rate. The real run's checks and box are built from this.
"""
import hashlib
import os
import resource
import sys
import time
import urllib.request

import numpy as np
import openslide
import tifffile
import zarr

RODA = "https://camelyon-dataset.s3.us-west-2.amazonaws.com/CAMELYON16/"
WORK = "/tmp/work"
os.makedirs(WORK, exist_ok=True)


def rec(k, v):
    print("%s\t%s" % (k, str(v)), flush=True)


def fetch(key, dest):
    t = time.time()
    h = hashlib.md5()
    n = 0
    with urllib.request.urlopen(RODA + key, timeout=120) as r, open(dest, "wb") as fh:
        while True:
            b = r.read(8 << 20)
            if not b:
                break
            h.update(b)
            fh.write(b)
            n += len(b)
    return n, time.time() - t, h.hexdigest()


rec("versions", "openslide %s / %s, tifffile %s, zarr %s, numpy %s" % (
    openslide.__library_version__, getattr(openslide, "__version__", "?"),
    tifffile.__version__, zarr.__version__, np.__version__))
try:
    zarr.config.set({"threading.max_workers": 1})
except Exception as e:                                             # noqa: BLE001
    rec("zarr_config", "could not set: %s" % e)

for slide in sys.argv[1:]:
    rec("== slide", slide)
    paths = {}
    for kind, key in (("wsi", "images/%s.tif" % slide), ("mask", "masks/%s_mask.tif" % slide),
                      ("xml", "annotations/%s.xml" % slide),
                      ("tissue", "background_tissue/%s_tissue.tif" % slide)):
        p = os.path.join(WORK, os.path.basename(key))
        try:
            n, dt, md5 = fetch(key, p)
            rec("fetch_" + kind, "%d B in %.2f s = %.1f MB/s md5 %s" % (n, dt, n / dt / 1e6, md5))
            paths[kind] = p
        except Exception as e:                                     # noqa: BLE001
            rec("fetch_" + kind, "FAILED %s" % e)

    for kind in ("wsi", "mask", "tissue"):
        if kind not in paths:
            continue
        with tifffile.TiffFile(paths[kind]) as tf:
            for i, pg in enumerate(tf.pages[:3]):
                rec("tiff_%s_page%d" % (kind, i), "shape %s dtype %s compression %s photometric %s "
                    "tile %s planar %s subsampling %s res %s" % (
                        pg.shape, pg.dtype, pg.compression.name, pg.photometric.name,
                        (pg.tilelength, pg.tilewidth), pg.planarconfig.name,
                        getattr(pg, "subsampling", None),
                        (pg.tags.get("XResolution").value if pg.tags.get("XResolution") else None,
                         pg.tags.get("ResolutionUnit").value if pg.tags.get("ResolutionUnit") else None)))
            rec("tiff_%s_npages" % kind, len(tf.pages))
            rec("tiff_%s_series_levels" % kind, [s.shape for s in tf.series[0].levels])
        try:
            o = openslide.OpenSlide(paths[kind])
            rec("os_%s" % kind, "levels %d dims %s ds %s mpp %s vendor %s" % (
                o.level_count, o.level_dimensions, [round(d, 4) for d in o.level_downsamples],
                o.properties.get("openslide.mpp-x"), o.properties.get("openslide.vendor")))
            o.close()
        except Exception as e:                                     # noqa: BLE001
            rec("os_%s" % kind, "openslide FAILED %s" % e)

    if "wsi" not in paths or "mask" not in paths:
        continue
    wsi = openslide.OpenSlide(paths["wsi"])
    msk = openslide.OpenSlide(paths["mask"])
    W, H = wsi.dimensions

    # mask label set + mask pyramid relation (level 1 vs level 0 subsample rules) on a tumour box
    L4 = min(4, msk.level_count - 1)
    m4 = np.asarray(msk.read_region((0, 0), L4, msk.level_dimensions[L4]))[..., 0]
    vals, cnt = np.unique(m4, return_counts=True)
    rec("mask_l%d_hist" % L4, dict(zip(vals.tolist(), cnt.tolist())))
    ys, xs = np.nonzero(m4 == 2)
    if len(ys):
        ds = msk.level_downsamples[L4]
        cy, cx = int(np.median(ys) * ds), int(np.median(xs) * ds)
        y0, x0 = (cy // 2048) * 2048, (cx // 2048) * 2048
        a0 = np.asarray(msk.read_region((x0, y0), 0, (2048, 2048)))[..., 0]
        a1 = np.asarray(msk.read_region((x0, y0), 1, (1024, 1024)))[..., 0]
        rec("mask_l0_l1_box", "x0 %d y0 %d l0 hist %s l1 hist %s" % (
            x0, y0, dict(zip(*[v.tolist() for v in np.unique(a0, return_counts=True)])),
            dict(zip(*[v.tolist() for v in np.unique(a1, return_counts=True)]))))
        q = a0.reshape(1024, 2, 1024, 2)
        rules = {"tl": a0[::2, ::2], "br": a0[1::2, 1::2], "max": q.max(axis=(1, 3)),
                 "min": q.min(axis=(1, 3)),
                 "mean_floor": (q.astype(np.int32).sum(axis=(1, 3)) // 4),
                 "mean_round": ((q.astype(np.int32).sum(axis=(1, 3)) + 2) // 4)}
        rec("mask_l1_vs_l0_rules", {k: int((v != a1).sum()) for k, v in rules.items()})
        # the same through tifffile, which reads the TIFF page directly
        with tifffile.TiffFile(paths["mask"]) as tf:
            z0 = zarr.open(tf.aszarr(level=0), mode="r")
            t0 = np.asarray(z0[y0:y0 + 2048, x0:x0 + 2048])
            t0 = t0[..., 0] if t0.ndim == 3 else t0
            rec("mask_l0_openslide_vs_tifffile_mismatch", int((t0 != a0).sum()))

    # tissue mask geometry vs wsi
    tissue = None
    if "tissue" in paths:
        with tifffile.TiffFile(paths["tissue"]) as tf:
            tissue = tf.pages[0].asarray()
        rec("tissue_shape", "%s dtype %s values %s; wsi/tissue ratio x %.4f y %.4f" % (
            tissue.shape, tissue.dtype, np.unique(tissue)[:10].tolist(),
            W / tissue.shape[1], H / tissue.shape[0]))

    # level-0 block decode: openslide vs tifffile, same bytes? and the rate
    with tifffile.TiffFile(paths["wsi"]) as tf:
        z = zarr.open(tf.aszarr(level=0), mode="r")
        rec("zarr_l0", "shape %s chunks %s dtype %s" % (z.shape, z.chunks, z.dtype))
        B = 1024
        cand = []
        if tissue is not None:
            ty, tx = np.nonzero(tissue[:, :] > 0)
            sy, sx = H / tissue.shape[0], W / tissue.shape[1]
            rng = np.random.default_rng(0)
            for i in rng.choice(len(ty), size=min(40, len(ty)), replace=False):
                cand.append((int(tx[i] * sx) // B * B, int(ty[i] * sy) // B * B))
        else:
            cand = [(4096 + i * B, 4096) for i in range(20)]
        cand = [(x, y) for x, y in cand if x + B <= W and y + B <= H]
        t_os = t_tf = 0.0
        mism = 0
        maxd = 0
        alpha_not_255 = 0
        for x, y in cand:
            t = time.time()
            r = np.asarray(wsi.read_region((x, y), 0, (B, B)))
            t_os += time.time() - t
            alpha_not_255 += int((r[..., 3] != 255).sum())
            a = r[..., :3]
            t = time.time()
            b = np.asarray(z[y:y + B, x:x + B])
            t_tf += time.time() - t
            if b.shape != a.shape:
                rec("shape_mismatch", "%s vs %s" % (a.shape, b.shape))
                break
            d = np.abs(a.astype(np.int16) - b.astype(np.int16))
            mism += int((d != 0).any(axis=2).sum())
            maxd = max(maxd, int(d.max()))
        n = max(1, len(cand))
        rec("l0_blocks", "%d blocks of %d^2: openslide %.3f s/block (%.1f MP/s), tifffile %.3f s/block "
            "(%.1f MP/s); mismatched px %d, max |diff| %d, alpha!=255 px %d" % (
                len(cand), B, t_os / n, B * B / (t_os / n) / 1e6, t_tf / n, B * B / (t_tf / n) / 1e6,
                mism, maxd, alpha_not_255))
        if tissue is not None:
            frac = float((tissue > 0).mean())
            rec("tissue_fraction", "%.4f -> ~%.0f level-0 blocks of %d^2" % (frac, frac * W * H / B / B, B))
    wsi.close()
    msk.close()
    for p in paths.values():
        os.unlink(p)
    rec("peak_rss_mib", "%.1f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0))

rec("nproc", os.cpu_count())
print("PROBE DONE")
