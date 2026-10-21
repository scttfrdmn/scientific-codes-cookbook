#!/usr/bin/env python3
"""Every tile of every annotated CAMELYON16 slide through imagecodecs: the JPEG archive losslessly
transcoded to JPEG XL and rebuilt byte for byte, the LZW masks decoded by two independent
decoders, and the lossless pixel codecs measured on genuine tissue.

    python3 -u real.py <workers> slides.tsv <outdir>          # SLIDES=a,b,c to run a subset

One process per slide, largest first. Nothing is written to disk: the slide streams from RODA in
32 MiB range GETs (md5 over the whole object, checked against the publisher's checksums.md5) and
each JPEG tile is processed as soon as its bytes have arrived. The mask (LZW, ~25 MB) is read whole.

Per slide, asserted -- a failure is recorded and fails the run at the end:

  md5          image and mask match the publisher's md5 (slides.tsv)
  layout       every WSI page is JPEG, YCbCr, 8-bit RGB with JPEGTables (tiled, bar the Radboud
               slides' stripped overview image, whose strips are treated as tiles); every mask page
               tiled LZW uint8; tile count == ceil(H/th) * ceil(W/tw) on every page; no empty WSI tile
               (the masks are sparse: an empty tile is all background, read as zeros);
               tile byte ranges disjoint and inside the file
  pruned       the tile spliced with ONLY the tables it references and does not define itself
               (libjxl refuses a stream with an unreferenced quantisation table, or with one defined
               twice: probe, probe2) decodes to exactly the pixels tifffile's
               own decode gives the TIFF tile -- so the pruning is a header edit, not an image edit
  jxl_exact    jpegxl_decode_jpeg(jpegxl_encode_jpeg(stream)) == stream, byte for byte, every tile;
               and the TIFF tile itself is recovered from it by dropping the spliced tables
  mask_two     every non-empty mask tile decoded by imagecodecs (tifffile) == the same compressed bytes
               decoded by libtiff through Pillow, in a one-tile TIFF built around them
  bakeoff_rt   on every 1024th level-0 tile (idx % 1024 == 0), each lossless pixel codec
               round-trips the decoded RGB tile exactly (array_equal)

Reported, not asserted: JPEG and JPEG XL bytes per level (the archive saving), the histogram of
|libjxl's own pixel decode - libjpeg-turbo's| on every tile from level 2 up (two correct decoders of
the same coefficients, which the JPEG standard does not require to agree), per-codec size and
encode/decode time in the bake-off, and sha256 of the decoded pixels, of the JPEG XL streams and of
the decoded masks -- compared across chips by compare.py.
"""
import hashlib
import io
import json
import multiprocessing as mp
import os
import resource
import struct
import sys
import time
import traceback
import urllib.request

import numpy as np

RODA = "https://camelyon-dataset.s3.us-west-2.amazonaws.com/CAMELYON16/"
CHUNK = 32 << 20
BAKE_STRIDE = 1024


def init():
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"


def get(url, a=None, b=None):
    """GET a whole object, or bytes a..b inclusive, with retries."""
    for attempt in range(5):
        try:
            hdr = {} if a is None else {"Range": "bytes=%d-%d" % (a, b)}
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=300) as r:
                d = r.read()
            if a is not None and len(d) != b - a + 1:
                raise IOError("short range read %d of %d" % (len(d), b - a + 1))
            return d
        except Exception:                                          # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))


def head_size(url):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
                return int(r.headers["Content-Length"])
        except Exception:                                          # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))


class Ranged:
    """A seekable read-only file over HTTP range requests, only for tifffile's IFD parse."""
    BLK = 1 << 20

    def __init__(self, url, size):
        self.url, self.size, self.pos, self.cache = url, size, 0, {}

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        out, p, end = bytearray(), self.pos, min(self.size, self.pos + n)
        while p < end:
            k = p // self.BLK
            if k not in self.cache:
                self.cache[k] = get(self.url, k * self.BLK, min(self.size, (k + 1) * self.BLK) - 1)
            c = self.cache[k]
            take = c[p - k * self.BLK: min(len(c), end - k * self.BLK)]
            out += take
            p += len(take)
        self.pos = end
        return bytes(out)

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)

    def seekable(self):
        return True

    def readable(self):
        return True

    def close(self):
        pass


# ------------------------------------------------------------------------------- JPEG splicing
def segments(s):
    """(marker, payload) for each marker segment after SOI, up to and including SOS."""
    out, i = [], 2
    while i < len(s) - 1:
        if s[i] != 0xFF:
            raise ValueError("marker expected at %d" % i)
        m = s[i + 1]
        if m == 0xD9:
            break
        n = int.from_bytes(s[i + 2:i + 4], "big")
        out.append((m, s[i + 4:i + 2 + n]))
        i += 2 + n
        if m == 0xDA:
            break
    return out


def split_tables(tables):
    """JPEGTables -> {("q", id) | ("h", class, id): one-table segment bytes}."""
    out = {}
    for m, p in segments(tables):
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


def used_tables(tile):
    used = set()
    for m, p in segments(tile):
        if 0xC0 <= m <= 0xC3:
            used |= {("q", p[6 + 3 * k + 2]) for k in range(p[5])}
        if m == 0xDA:
            for k in range(p[0]):
                used |= {("h", 0, p[2 + 2 * k] >> 4), ("h", 1, p[2 + 2 * k] & 15)}
    return frozenset(used - defined_tables(tile))


def defined_tables(tile):
    """Tables the tile defines itself (the Radboud overview strip carries its own DQT): splicing a
    second copy from JPEGTables would define a table twice, which libjxl refuses (probe2)."""
    out = set()
    for m, p in segments(tile):
        j = 0
        if m == 0xDB:
            while j < len(p):
                out.add(("q", p[j] & 15))
                j += 65 if p[j] >> 4 == 0 else 129
        elif m == 0xC4:
            while j < len(p):
                out.add(("h", p[j] >> 4, p[j] & 15))
                j += 17 + sum(p[j + 1:j + 17])
    return out


# --------------------------------------------------------------------- a one-tile TIFF for libtiff
def one_tile_tiff(raw, tl, tw, compression, predictor):
    ents = [(256, 3, tw), (257, 3, tl), (258, 3, 8), (259, 3, compression), (262, 3, 1),
            (277, 3, 1), (284, 3, 1), (317, 3, predictor), (322, 3, tw), (323, 3, tl),
            (324, 4, None), (325, 4, len(raw))]
    off = 8 + 2 + 12 * len(ents) + 4
    ifd = struct.pack("<H", len(ents))
    for tag, typ, val in ents:
        val = off if val is None else val
        ifd += struct.pack("<HHI", tag, typ, 1) + (struct.pack("<HH", val, 0) if typ == 3 else struct.pack("<I", val))
    return b"II*\x00" + struct.pack("<I", 8) + ifd + b"\x00\x00\x00\x00" + raw


def bake_codecs(ic):
    def flat(dec):
        return lambda b, a: np.frombuffer(dec(b), np.uint8).reshape(a.shape)
    return [
        ("zlib", lambda a: ic.zlib_encode(a.tobytes(), level=6), flat(ic.zlib_decode)),
        ("deflate", lambda a: ic.deflate_encode(a.tobytes(), level=6), flat(ic.deflate_decode)),
        ("zstd", lambda a: ic.zstd_encode(a.tobytes(), level=3), flat(ic.zstd_decode)),
        ("lz4", lambda a: ic.lz4_encode(a.tobytes()), flat(ic.lz4_decode)),
        ("brotli5", lambda a: ic.brotli_encode(a.tobytes(), level=5), flat(ic.brotli_decode)),
        ("lzma", lambda a: ic.lzma_encode(a.tobytes()), flat(ic.lzma_decode)),
        ("bz2", lambda a: ic.bz2_encode(a.tobytes()), flat(ic.bz2_decode)),
        ("png", lambda a: ic.png_encode(a), lambda b, a: ic.png_decode(b)),
        ("webp", lambda a: ic.webp_encode(a, lossless=True), lambda b, a: ic.webp_decode(b)),
        ("jpegxl_e1", lambda a: ic.jpegxl_encode(a, lossless=True, effort=1, numthreads=1),
         lambda b, a: ic.jpegxl_decode(b, numthreads=1)),
        ("jpegxl_e3", lambda a: ic.jpegxl_encode(a, lossless=True, effort=3, numthreads=1),
         lambda b, a: ic.jpegxl_decode(b, numthreads=1)),
        ("jpegxl_e7", lambda a: ic.jpegxl_encode(a, lossless=True, numthreads=1),
         lambda b, a: ic.jpegxl_decode(b, numthreads=1)),
        ("jpeg2k", lambda a: ic.jpeg2k_encode(a, reversible=True, numthreads=1),
         lambda b, a: ic.jpeg2k_decode(b, numthreads=1)),
        ("jpegls", lambda a: ic.jpegls_encode(a), lambda b, a: ic.jpegls_decode(b)),
        ("lerc", lambda a: ic.lerc_encode(a), lambda b, a: ic.lerc_decode(b)),
        ("qoi", lambda a: ic.qoi_encode(a), lambda b, a: ic.qoi_decode(b)),
    ]


def check_layout(r, pages, kind, size):
    """Assert the page formats and tile grids; return the tile list (offset, count, page, index)."""
    import tifffile
    tiles = []
    for i, pg in enumerate(pages):
        H, W = pg.shape[:2]
        if pg.is_tiled:
            want = -(-H // pg.tilelength) * -(-W // pg.tilewidth)
        else:                                  # Radboud slides carry one stripped overview image
            want = -(-H // min(pg.rowsperstrip, H))
        n = len(pg.dataoffsets)
        if kind == "wsi":
            ok = (pg.compression == tifffile.COMPRESSION.JPEG
                  and pg.photometric == tifffile.PHOTOMETRIC.YCBCR and pg.dtype == np.uint8
                  and pg.samplesperpixel == 3 and pg.jpegtables is not None
                  and pg.planarconfig == tifffile.PLANARCONFIG.CONTIG)
        else:
            ok = (pg.is_tiled and pg.compression == tifffile.COMPRESSION.LZW and pg.dtype == np.uint8
                  and pg.samplesperpixel == 1)
        if not ok:
            r["fail"].append("%s page %d is not the expected format (%s %s %s)" % (
                kind, i, pg.compression.name, pg.photometric.name, pg.dtype))
        if n != want:
            r["fail"].append("%s page %d has %d tiles, grid wants %d" % (kind, i, n, want))
        for j, (o, c) in enumerate(zip(pg.dataoffsets, pg.databytecounts)):
            tiles.append((int(o), int(c), i, j))
    empty = sum(1 for t in tiles if t[1] == 0)
    r[kind + "_empty_tiles"] = empty
    if empty and kind == "wsi":
        r["fail"].append("%s has %d empty tiles" % (kind, empty))
    tiles.sort()
    bad = sum(1 for a, b in zip(tiles, tiles[1:]) if a[0] + a[1] > b[0])
    bad += sum(1 for t in tiles if t[0] < 0 or t[0] + t[1] > size)
    if bad:
        r["fail"].append("%s has %d overlapping or out-of-file tile ranges" % (kind, bad))
    r[kind + "_tiles"] = len(tiles)
    r[kind + "_tile_bytes"] = sum(t[1] for t in tiles)
    r[kind + "_file_bytes"] = size
    r[kind + "_pages"] = ["%dx%d %s" % (pg.shape[1], pg.shape[0], "tiled" if pg.is_tiled else "strips")
                          for pg in pages]
    return tiles


def analyse(row):
    import imagecodecs as ic
    import tifffile
    from PIL import Image

    slide = row["slide"]
    r = {"slide": slide, "center": row["center"], "fail": []}
    t_start = time.time()
    tm = dict.fromkeys(("fetch", "decode_tifffile", "decode_pruned", "transcode", "reconstruct",
                        "jxl_pixels", "hash", "mask", "bakeoff"), 0.0)
    try:
        # ------------------------------------------------------------------------------ mask
        t = time.time()
        mdata = get(RODA + "masks/%s_mask.tif" % slide)
        tm["fetch"] += time.time() - t
        md5 = hashlib.md5(mdata).hexdigest()
        if md5 != row["mask_md5"]:
            r["fail"].append("md5 mask %s != %s" % (md5, row["mask_md5"]))
            return r
        t = time.time()
        hm = hashlib.sha256()
        mask_eq = mask_ne = 0
        labels = np.zeros(256, np.int64)
        with tifffile.TiffFile(io.BytesIO(mdata)) as mf:
            mpages = list(mf.pages)
            mtiles = check_layout(r, mpages, "mask", len(mdata))
            r["mask_predictor"] = sorted({int(p.predictor) for p in mpages})
            for off, cnt, pi, idx in mtiles:
                pg = mpages[pi]
                if cnt == 0:                     # sparse: background, never decoded by anyone
                    a = np.zeros((pg.tilelength, pg.tilewidth), np.uint8)
                    hm.update(a.tobytes())
                    if pi == 0:
                        labels[0] += a.size
                    continue
                raw = mdata[off:off + cnt]
                a = np.asarray(pg.decode(raw, idx)[0]).reshape(pg.tilelength, pg.tilewidth)
                im = Image.open(io.BytesIO(one_tile_tiff(raw, pg.tilelength, pg.tilewidth, 5, int(pg.predictor))))
                if "pil_decoder" not in r:
                    r["pil_decoder"] = str(im.tile[0][0]) if im.tile else "?"
                b = np.asarray(im)
                if b.shape == a.shape and np.array_equal(a, b):
                    mask_eq += 1
                else:
                    mask_ne += 1
                hm.update(a.tobytes())
                if pi == 0:
                    labels += np.bincount(a.ravel(), minlength=256)
        r["mask_two_decoders_equal"] = mask_eq
        r["mask_two_decoders_differ"] = mask_ne
        r["mask_sha256"] = hm.hexdigest()
        r["mask_labels_l0_tiles"] = {int(k): int(v) for k, v in enumerate(labels) if v}
        if mask_eq + r["mask_empty_tiles"] != len(mtiles):
            r["fail"].append("mask: %d equal + %d empty != %d tiles" % (mask_eq, r["mask_empty_tiles"], len(mtiles)))
        if mask_ne:
            r["fail"].append("mask: %d tiles differ between imagecodecs and libtiff" % mask_ne)
        del mdata
        tm["mask"] += time.time() - t

        # ------------------------------------------------------------------------------- WSI
        url = RODA + "images/%s.tif" % slide
        size = head_size(url)
        if size != int(row["image_bytes"]):
            r["fail"].append("size %d != %s" % (size, row["image_bytes"]))
            return r
        tf = tifffile.TiffFile(Ranged(url, size))
        pages = list(tf.pages)
        tiles = check_layout(r, pages, "wsi", size)
        if r["fail"]:
            return r
        tabs = [split_tables(pg.jpegtables) for pg in pages]
        hdr_cache = {}
        nl = len(pages)
        lv = {k: [0] * nl for k in ("tiles", "jpeg_bytes", "pruned_bytes", "jxl_bytes")}
        cnt = dict.fromkeys(("pruned_equal", "pruned_differ", "jxl_exact", "jxl_inexact", "jxl_refused",
                             "tiff_tile_recovered"), 0)
        diff_hist = np.zeros(256, np.int64)
        jxl_shape_bad = 0
        hp, hj = hashlib.sha256(), hashlib.sha256()
        codecs = bake_codecs(ic)
        bake = {name: {"bytes": 0, "enc_s": 0.0, "dec_s": 0.0, "equal": 0, "differ": 0, "error": ""}
                for name, _, _ in codecs}
        bake_tiles = bake_px = bake_jpeg = 0
        md5 = hashlib.md5()
        buf, bstart, pos, ti = bytearray(), 0, 0, 0
        while pos < size:
            t = time.time()
            end = min(size, pos + CHUNK)
            data = get(url, pos, end - 1)
            md5.update(data)
            tm["fetch"] += time.time() - t
            buf += data
            pos = end
            bend = bstart + len(buf)
            while ti < len(tiles) and tiles[ti][0] + tiles[ti][1] <= bend:
                off, n, pi, idx = tiles[ti]
                ti += 1
                raw = bytes(buf[off - bstart: off - bstart + n])
                pg = pages[pi]
                # the stream with only the tables this tile references
                t = time.time()
                u = hdr_cache.get(pi)
                used = used_tables(raw)
                if u is None or u[0] != used:
                    hdr = b"".join(tabs[pi][k] for k in sorted(tabs[pi], key=str) if k in used)
                    if not used <= set(tabs[pi]):
                        r["fail"].append("page %d tile %d references a table JPEGTables lacks" % (pi, idx))
                    hdr_cache[pi] = u = (used, hdr)
                stream = raw[:2] + u[1] + raw[2:]
                b = np.asarray(ic.jpeg8_decode(stream))
                tm["decode_pruned"] += time.time() - t
                t = time.time()
                a = np.asarray(pg.decode(raw, idx, jpegtables=pg.jpegtables)[0])
                tm["decode_tifffile"] += time.time() - t
                if a.size == b.size and np.array_equal(a.reshape(b.shape), b):
                    cnt["pruned_equal"] += 1
                else:
                    cnt["pruned_differ"] += 1
                t = time.time()
                hp.update(b)
                tm["hash"] += time.time() - t
                lv["tiles"][pi] += 1
                lv["jpeg_bytes"][pi] += n
                lv["pruned_bytes"][pi] += len(stream)
                t = time.time()
                try:
                    jx = ic.jpegxl_encode_jpeg(stream, numthreads=1)
                except Exception as e:                             # noqa: BLE001
                    cnt["jxl_refused"] += 1
                    r.setdefault("jxl_refused_example", "page %d tile %d: %s" % (pi, idx, e))
                    tm["transcode"] += time.time() - t
                    continue
                tm["transcode"] += time.time() - t
                hj.update(jx)
                lv["jxl_bytes"][pi] += len(jx)
                t = time.time()
                back = bytes(ic.jpegxl_decode_jpeg(jx, numthreads=1))
                tm["reconstruct"] += time.time() - t
                if back == stream:
                    cnt["jxl_exact"] += 1
                else:
                    cnt["jxl_inexact"] += 1
                if back[:2] + back[2 + len(u[1]):] == raw:
                    cnt["tiff_tile_recovered"] += 1
                if pi >= 2:
                    t = time.time()
                    px = np.asarray(ic.jpegxl_decode(jx, numthreads=1))
                    if px.shape == b.shape:
                        d = np.abs(px.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)
                        diff_hist += np.bincount(d.ravel(), minlength=256)
                    else:
                        jxl_shape_bad += 1
                    tm["jxl_pixels"] += time.time() - t
                if pi == 0 and idx % BAKE_STRIDE == 0:
                    t = time.time()
                    bake_tiles += 1
                    bake_px += b.shape[0] * b.shape[1]
                    bake_jpeg += n
                    for name, enc, dec in codecs:
                        s = bake[name]
                        try:
                            t1 = time.perf_counter()
                            e = enc(b)
                            t2 = time.perf_counter()
                            o = np.asarray(dec(e, b))
                            t3 = time.perf_counter()
                        except Exception as ex:                    # noqa: BLE001
                            s["differ"] += 1
                            s["error"] = repr(ex)[:160]
                            continue
                        s["bytes"] += len(e)
                        s["enc_s"] += t2 - t1
                        s["dec_s"] += t3 - t2
                        if o.shape == b.shape and np.array_equal(o, b):
                            s["equal"] += 1
                        else:
                            s["differ"] += 1
                    tm["bakeoff"] += time.time() - t
            nxt = tiles[ti][0] if ti < len(tiles) else bend
            nxt = max(bstart, min(nxt, bend))
            del buf[:nxt - bstart]
            bstart = nxt
        tf.close()
        if md5.hexdigest() != row["image_md5"]:
            r["fail"].append("md5 wsi %s != %s" % (md5.hexdigest(), row["image_md5"]))
        if ti != len(tiles):
            r["fail"].append("only %d of %d tiles processed" % (ti, len(tiles)))
        r.update(cnt)
        r["levels"] = lv
        r["jxl_vs_turbo_absdiff_hist_l2plus"] = [int(x) for x in np.trim_zeros(diff_hist, "b")]
        r["jxl_pixels_shape_bad"] = jxl_shape_bad
        r["pixels_sha256"] = hp.hexdigest()
        r["jxl_sha256"] = hj.hexdigest()
        r["bake"] = {"tiles": bake_tiles, "px": bake_px, "jpeg_bytes": bake_jpeg, "codecs": bake}
        T = len(tiles)
        if cnt["pruned_differ"]:
            r["fail"].append("%d tiles: the pruned stream decodes differently from the TIFF tile" % cnt["pruned_differ"])
        if cnt["jxl_refused"] or cnt["jxl_inexact"] or cnt["jxl_exact"] != T:
            r["fail"].append("JPEG XL: %d exact of %d, %d refused, %d inexact" % (
                cnt["jxl_exact"], T, cnt["jxl_refused"], cnt["jxl_inexact"]))
        if cnt["tiff_tile_recovered"] != T:
            r["fail"].append("TIFF tile recovered for %d of %d" % (cnt["tiff_tile_recovered"], T))
        if jxl_shape_bad:
            r["fail"].append("libjxl pixel decode changed the shape on %d tiles" % jxl_shape_bad)
        for name, s in bake.items():
            if s["differ"] or s["equal"] != bake_tiles:
                r["fail"].append("bake-off %s: %d of %d exact %s" % (name, s["equal"], bake_tiles, s["error"]))
    except Exception:                                              # noqa: BLE001
        r["fail"].append("exception: " + traceback.format_exc()[-1500:])
    finally:
        r["phase_s"] = {k: round(v, 2) for k, v in tm.items()}
        r["wall_s"] = round(time.time() - t_start, 2)
        r["cpu_s"] = round(sum(resource.getrusage(resource.RUSAGE_SELF)[:2]), 2)
        r["rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    return r


def main():
    workers, slides_tsv, out = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
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
    t0 = time.time()
    results = []
    with mp.Pool(workers, initializer=init, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(analyse, rows):
            results.append(r)
            print("%s\ttiles %s\tjxl_exact %s\tmask_eq %s\twall %s\t%s" % (
                r["slide"], r.get("wsi_tiles"), r.get("jxl_exact"), r.get("mask_two_decoders_equal"),
                r.get("wall_s"), "FAIL " + "; ".join(r["fail"]) if r["fail"] else "ok"), flush=True)
    wall = time.time() - t0
    results.sort(key=lambda x: x["slide"])
    with open(os.path.join(out, "slides.jsonl"), "w") as fh:
        for r in results:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    fails = [r for r in results if r["fail"]]

    def tot(k):
        return sum(int(r.get(k, 0) or 0) for r in results)

    def lvtot(k):
        return sum(sum(r.get("levels", {}).get(k, [])) for r in results)

    hist = np.zeros(256, np.int64)
    for r in results:
        h = r.get("jxl_vs_turbo_absdiff_hist_l2plus") or []
        hist[:len(h)] += np.array(h, np.int64)
    bake = {}
    for r in results:
        for name, s in (r.get("bake") or {}).get("codecs", {}).items():
            b = bake.setdefault(name, {"bytes": 0, "enc_s": 0.0, "dec_s": 0.0, "equal": 0, "differ": 0})
            for k in b:
                b[k] += s[k]
    summary = {
        "slides": len(results), "failed": len(fails), "wall_s": round(wall, 1), "workers": workers,
        "wsi_bytes": tot("wsi_file_bytes"), "wsi_tiles": tot("wsi_tiles"), "wsi_tile_bytes": tot("wsi_tile_bytes"),
        "mask_bytes": tot("mask_file_bytes"), "mask_tiles": tot("mask_tiles"),
        "jpeg_bytes": lvtot("jpeg_bytes"), "pruned_bytes": lvtot("pruned_bytes"), "jxl_bytes": lvtot("jxl_bytes"),
        "pruned_equal": tot("pruned_equal"), "pruned_differ": tot("pruned_differ"),
        "jxl_exact": tot("jxl_exact"), "jxl_inexact": tot("jxl_inexact"), "jxl_refused": tot("jxl_refused"),
        "tiff_tile_recovered": tot("tiff_tile_recovered"),
        "mask_two_decoders_equal": tot("mask_two_decoders_equal"),
        "mask_two_decoders_differ": tot("mask_two_decoders_differ"),
        "jxl_vs_turbo_absdiff_hist_l2plus": [int(x) for x in np.trim_zeros(hist, "b")],
        "bake_tiles": sum((r.get("bake") or {}).get("tiles", 0) for r in results),
        "bake_px": sum((r.get("bake") or {}).get("px", 0) for r in results),
        "bake_jpeg_bytes": sum((r.get("bake") or {}).get("jpeg_bytes", 0) for r in results),
        "bake": bake,
        "max_worker_rss_mib": max((r.get("rss_mib", 0) for r in results), default=0),
        "cpu_s_sum": round(sum(r.get("cpu_s", 0) for r in results), 1),
        "phase_s_sum": {k: round(sum(r.get("phase_s", {}).get(k, 0) for r in results), 1)
                        for k in (results[0].get("phase_s", {}) if results else {})},
    }
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    print(json.dumps(summary, sort_keys=True), flush=True)
    for r in fails:
        print("FAIL\t%s\t%s" % (r["slide"], "; ".join(r["fail"])), flush=True)
    if fails or len(results) != len(rows):
        sys.exit(1)
    print("IMAGECODECS-REAL OK\t%d slides, %d tiles transcoded and rebuilt exactly, %d mask tiles agree"
          % (len(results), summary["jxl_exact"], summary["mask_two_decoders_equal"]), flush=True)


if __name__ == "__main__":
    main()
