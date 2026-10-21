#!/usr/bin/env python3
"""Probe, before sizing or writing any check: what imagecodecs costs on real CAMELYON16 tiles.

    python3 -u probe.py <slide> [<slide> ...]

Fetches each slide from RODA, then on a spread of level-0 tiles times and compares: the JPEG decode
several ways (tifffile's page.decode, imagecodecs.jpeg8_decode on the spliced stream, Pillow, cv2),
the JPEG -> JPEG XL lossless transcode and its byte-exact reconstruction, libjxl's own pixel decode,
and every lossless pixel codec. Asserts nothing; it reports what the design is built from.
"""
import hashlib
import io
import os
import resource
import sys
import time
import urllib.request

import numpy as np
import imagecodecs
import tifffile

RODA = "https://camelyon-dataset.s3.us-west-2.amazonaws.com/CAMELYON16/images/"


def rec(k, v):
    print("%s\t%s" % (str(k), str(v)), flush=True)


def ver(k, fn):
    try:
        rec(k, fn())
    except Exception as e:                                     # noqa: BLE001
        rec(k, "unknown (%s: %s)" % (type(e).__name__, e))


ver("imagecodecs", lambda: imagecodecs.__version__)
ver("tifffile", lambda: tifffile.__version__)
ver("numpy", lambda: np.__version__)
ver("imagecodecs_libs", lambda: imagecodecs.version())
ver("jpegxl_encode_jpeg", lambda: hasattr(imagecodecs, "jpegxl_encode_jpeg"))
ver("jpegxl_decode_jpeg", lambda: hasattr(imagecodecs, "jpegxl_decode_jpeg"))
ver("codecs_available", lambda: sorted(n[:-7] for n in dir(imagecodecs) if n.endswith("_encode")))
try:
    import PIL
    import PIL.features
    ver("pillow", lambda: PIL.__version__)
    ver("pillow_jpeg", lambda: PIL.features.version("jpg"))
    from PIL import Image
except Exception as e:                                         # noqa: BLE001
    Image = None
    rec("pillow", "not importable (%s)" % e)
try:
    import cv2
    ver("cv2", lambda: cv2.__version__)
except Exception as e:                                         # noqa: BLE001
    cv2 = None
    rec("cv2", "not importable (%s)" % type(e).__name__)


def segments(s, stop_at_sos):
    """(marker, payload) for each marker segment after SOI, up to SOS (inclusive) or EOI."""
    out, i = [], 2
    while i < len(s) - 1:
        assert s[i] == 0xFF, "marker expected at %d" % i
        m = s[i + 1]
        if m == 0xD9:
            break
        L = int.from_bytes(s[i + 2:i + 4], "big")
        out.append((m, s[i + 4:i + 2 + L]))
        i += 2 + L
        if m == 0xDA and stop_at_sos:
            break
    return out, i


def split_tables(tables):
    """JPEGTables -> {('q', id): segment bytes, ('h', class, id): segment bytes}, one table each."""
    out = {}
    for m, p in segments(tables, False)[0]:
        j = 0
        if m == 0xDB:
            while j < len(p):
                n = 65 if p[j] >> 4 == 0 else 129
                out[("q", p[j] & 15)] = b"\xff\xdb" + (n + 2).to_bytes(2, "big") + p[j:j + n]
                j += n
        elif m == 0xC4:
            while j < len(p):
                n = 17 + sum(p[j + 1:j + 17])
                out[("h", p[j] >> 4, p[j] & 15)] = b"\xff\xc4" + (n + 2).to_bytes(2, "big") + p[j:j + n]
                j += n
        else:
            raise ValueError("unexpected marker %02X in JPEGTables" % m)
    return out


def splice(tables, tile, prune=False):
    """An abbreviated TIFF tile stream plus the page's JPEGTables -> one complete JPEG. With prune,
    only the tables the tile's SOF and SOS reference are carried; the entropy data is untouched."""
    assert tables[:2] == b"\xff\xd8" and tables[-2:] == b"\xff\xd9" and tile[:2] == b"\xff\xd8"
    if not prune:
        return tile[:2] + tables[2:-2] + tile[2:]
    segs, _ = segments(tile, True)
    used = set()
    for m, p in segs:
        if 0xC0 <= m <= 0xC3:
            used |= {("q", p[6 + 3 * k + 2]) for k in range(p[5])}
        if m == 0xDA:
            for k in range(p[0]):
                used |= {("h", 0, p[2 + 2 * k] >> 4), ("h", 1, p[2 + 2 * k] & 15)}
    t = split_tables(tables)
    keep = [t[k] for k in sorted(t, key=str) if k in used]
    assert all(k in t for k in used), "tile references a table JPEGTables does not define"
    return tile[:2] + b"".join(keep) + tile[2:]


def timed(fn, *a, **k):
    t = time.perf_counter()
    r = fn(*a, **k)
    return r, time.perf_counter() - t


def flat(dec):
    return lambda b, a: np.frombuffer(dec(b), np.uint8).reshape(a.shape)


IC = imagecodecs
PIX = [
    ("zlib", lambda a: IC.zlib_encode(a.tobytes(), level=6), flat(IC.zlib_decode)),
    ("deflate", lambda a: IC.deflate_encode(a.tobytes(), level=6), flat(IC.deflate_decode)),
    ("zstd", lambda a: IC.zstd_encode(a.tobytes(), level=3), flat(IC.zstd_decode)),
    ("lz4", lambda a: IC.lz4_encode(a.tobytes()), flat(IC.lz4_decode)),
    ("brotli5", lambda a: IC.brotli_encode(a.tobytes(), level=5), flat(IC.brotli_decode)),
    ("lzma", lambda a: IC.lzma_encode(a.tobytes()), flat(IC.lzma_decode)),
    ("bz2", lambda a: IC.bz2_encode(a.tobytes()), flat(IC.bz2_decode)),
    ("png", lambda a: IC.png_encode(a), lambda b, a: IC.png_decode(b)),
    ("webp", lambda a: IC.webp_encode(a, lossless=True), lambda b, a: IC.webp_decode(b)),
    ("jpegxl_e1", lambda a: IC.jpegxl_encode(a, lossless=True, effort=1, numthreads=1),
     lambda b, a: IC.jpegxl_decode(b, numthreads=1)),
    ("jpegxl_e3", lambda a: IC.jpegxl_encode(a, lossless=True, effort=3, numthreads=1),
     lambda b, a: IC.jpegxl_decode(b, numthreads=1)),
    ("jpegxl_e7", lambda a: IC.jpegxl_encode(a, lossless=True, numthreads=1),
     lambda b, a: IC.jpegxl_decode(b, numthreads=1)),
    ("jpeg2k", lambda a: IC.jpeg2k_encode(a, reversible=True, numthreads=1),
     lambda b, a: IC.jpeg2k_decode(b, numthreads=1)),
    ("jpegls", lambda a: IC.jpegls_encode(a), lambda b, a: IC.jpegls_decode(b)),
    ("lerc", lambda a: IC.lerc_encode(a), lambda b, a: IC.lerc_decode(b)),
    ("qoi", lambda a: IC.qoi_encode(a), lambda b, a: IC.qoi_decode(b)),
]

os.makedirs("/tmp/work", exist_ok=True)
for slide in sys.argv[1:]:
    rec("== slide", slide)
    p = "/tmp/work/%s.tif" % slide
    t = time.time()
    h = hashlib.md5()
    with urllib.request.urlopen(RODA + slide + ".tif", timeout=300) as r, open(p, "wb") as fh:
        while True:
            b = r.read(8 << 20)
            if not b:
                break
            h.update(b)
            fh.write(b)
    rec("fetch", "%d B in %.1f s md5 %s" % (os.path.getsize(p), time.time() - t, h.hexdigest()))
    with tifffile.TiffFile(p) as tf, open(p, "rb") as fh:
        tot_tiles = tot_bytes = 0
        for i, pg in enumerate(tf.pages):
            n = len(pg.dataoffsets)
            nb = int(sum(pg.databytecounts))
            tot_tiles += n
            tot_bytes += nb
            rec("page%d" % i, "shape %s comp %s phot %s tile %s subs %s ntiles %d bytes %d zero_tiles %d tables %s" % (
                pg.shape, pg.compression.name, pg.photometric.name, (pg.tilelength, pg.tilewidth),
                getattr(pg, "subsampling", None), n, nb, sum(1 for c in pg.databytecounts if c == 0),
                None if pg.jpegtables is None else len(pg.jpegtables)))
        rec("tiles_total", "%d tiles %d bytes of %d file (%.4f)" % (
            tot_tiles, tot_bytes, os.path.getsize(p), tot_bytes / os.path.getsize(p)))
        pg = tf.pages[0]
        bc = np.array(pg.databytecounts)
        rec("tile_bytes_pct", "p0 %d p10 %d p50 %d p90 %d p100 %d" % tuple(np.percentile(bc, [0, 10, 50, 90, 100])))
        order = np.argsort(bc)[::-1]
        top = order[: max(1, len(order) * 3 // 10)]
        pick = list(top[np.linspace(0, len(top) - 1, 120).astype(int)])
        bot = order[len(order) // 2:]
        pick += list(bot[np.linspace(0, len(bot) - 1, 20).astype(int)])
        tm, sz, eq, diffs = {}, {}, {}, {}

        def add(k, dt, n=None):
            tm[k] = tm.get(k, 0.0) + dt
            if n is not None:
                sz[k] = sz.get(k, 0) + n

        def cnt(k, ok):
            eq[k] = eq.get(k, 0) + int(bool(ok))

        npx = 0
        for idx in pick:
            fh.seek(pg.dataoffsets[idx])
            raw = fh.read(pg.databytecounts[idx])
            if idx == pick[0]:
                # which pruning libjxl needs: drop unused quant tables, unused Huffman, or both
                t = split_tables(pg.jpegtables)
                f0 = splice(pg.jpegtables, raw, prune=True)
                rec("pruned_header", "%d -> %d B" % (len(splice(pg.jpegtables, raw)), len(f0)))
                for lab, drop in (("no_q1", [("q", 1)]), ("no_unused_h", [("h", 0, 1), ("h", 1, 1)]),
                                  ("both", [("q", 1), ("h", 0, 1), ("h", 1, 1)])):
                    s = raw[:2] + b"".join(v for k, v in sorted(t.items(), key=lambda kv: str(kv[0])) if k not in drop) + raw[2:]
                    try:
                        jx = imagecodecs.jpegxl_encode_jpeg(s, numthreads=1)
                        rec("variant_" + lab, "OK exact %s" % (bytes(imagecodecs.jpegxl_decode_jpeg(jx)) == s))
                    except Exception as e:                     # noqa: BLE001
                        rec("variant_" + lab, "FAIL %s" % e)
            full = splice(pg.jpegtables, raw, prune=True)
            a_tf, dt = timed(lambda: pg.decode(raw, idx, jpegtables=pg.jpegtables)[0])
            add("dec_tifffile", dt)
            a_tf = np.asarray(a_tf).reshape(pg.tilelength, pg.tilewidth, 3)
            a_ic, dt = timed(imagecodecs.jpeg8_decode, full)
            add("dec_jpeg8_full", dt)
            a_ic = np.asarray(a_ic)
            cnt("tifffile==jpeg8_full", np.array_equal(a_tf, a_ic))
            if Image is not None:
                a_pil, dt = timed(lambda: np.asarray(Image.open(io.BytesIO(full)).convert("RGB")))
                add("dec_pillow", dt)
                cnt("pillow==jpeg8", np.array_equal(a_pil, a_ic))
            if cv2 is not None:
                a_cv, dt = timed(lambda: cv2.imdecode(np.frombuffer(full, np.uint8), cv2.IMREAD_COLOR)[..., ::-1])
                add("dec_cv2", dt)
                cnt("cv2==jpeg8", np.array_equal(a_cv, a_ic))
            npx += a_ic.shape[0] * a_ic.shape[1]
            add("jpeg_bytes", 0.0, len(full))
            try:
                jx, dt = timed(imagecodecs.jpegxl_encode_jpeg, full, numthreads=1)
            except Exception as e:                             # noqa: BLE001
                eq["err_jxl_transcode"] = repr(e)[:160]
                continue
            add("jxl_transcode", dt, len(jx))
            back, dt = timed(imagecodecs.jpegxl_decode_jpeg, jx)
            add("jxl_reconstruct", dt)
            cnt("jxl_reconstruct_bytes", bytes(back) == full)
            cnt("jxl_transcode_deterministic", imagecodecs.jpegxl_encode_jpeg(full) == jx) if idx == pick[0] else None
            px, dt = timed(imagecodecs.jpegxl_decode, jx)
            add("jxl_pixel_decode", dt)
            px = np.asarray(px)
            cnt("jxl_pixels_shape_ok", px.shape == a_ic.shape)
            if px.shape == a_ic.shape:
                d = np.abs(px.astype(np.int16) - a_ic.astype(np.int16))
                for v, c in zip(*np.unique(d, return_counts=True)):
                    diffs[int(v)] = diffs.get(int(v), 0) + int(c)
            for name, enc, dec in PIX:
                try:
                    b, dt = timed(enc, a_ic)
                    add("enc_" + name, dt, len(b))
                    o, dt = timed(dec, b, a_ic)
                    add("dec_" + name, dt)
                    cnt("rt_" + name, np.array_equal(np.asarray(o), a_ic))
                except Exception as e:                         # noqa: BLE001
                    eq["err_" + name] = repr(e)[:160]
        mp = npx / 1e6
        rec("tiles_probed", "%d tiles, %.1f MP, jpeg %d B" % (len(pick), mp, sz["jpeg_bytes"]))
        for k in sorted(tm):
            if k == "jpeg_bytes":
                continue
            s = "%-22s %8.3f s  %8.1f MP/s" % (k, tm[k], mp / tm[k] if tm[k] else 0)
            if k in sz:
                s += "  %d B  ratio_vs_raw %.3f  ratio_vs_jpeg %.4f" % (sz[k], npx * 3 / sz[k], sz["jpeg_bytes"] / sz[k])
            rec("time", s)
        rec("equal", eq)
        rec("jxl_pixel_vs_turbo_absdiff_hist", dict(sorted(diffs.items())))
    os.remove(p)
    rec("peak_rss_mib", "%.1f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
rec("PROBE", "done")
