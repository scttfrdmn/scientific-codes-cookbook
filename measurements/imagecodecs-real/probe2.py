#!/usr/bin/env python3
"""Probe 2: why JxlEncoderAddJPEGFrame refuses a CAMELYON16 tile, and which stream it accepts.

    python3 -u probe2.py <slide>

Reads a handful of level-0 tiles by HTTP range (no full download), dumps the JPEG marker sequence
of the page's JPEGTables and of each tile, and tries the lossless JPEG -> JPEG XL transcode on
variants of the stream. Asserts nothing.
"""
import os
import shutil
import struct
import subprocess
import sys
import urllib.request

import numpy as np
import imagecodecs
import tifffile

URL = "https://camelyon-dataset.s3.us-west-2.amazonaws.com/CAMELYON16/images/%s.tif"


def rec(k, v):
    print("%s\t%s" % (str(k), str(v)), flush=True)


class Ranged:
    """A minimal seekable read-only file over HTTP range requests, for tifffile's IFD parse."""

    def __init__(self, url):
        self.url, self.pos = url, 0
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60)
        self.size = int(r.headers["Content-Length"])
        self.cache = {}

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        if n == 0:
            return b""
        blk = 1 << 20
        out = bytearray()
        p, end = self.pos, min(self.size, self.pos + n)
        while p < end:
            b0 = p // blk
            if b0 not in self.cache:
                rq = urllib.request.Request(self.url, headers={"Range": "bytes=%d-%d" % (b0 * blk, min(self.size, (b0 + 1) * blk) - 1)})
                self.cache[b0] = urllib.request.urlopen(rq, timeout=60).read()
            c = self.cache[b0]
            take = c[p - b0 * blk: min(len(c), end - b0 * blk)]
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


def markers(s):
    out, i = [], 0
    while i < len(s) - 1:
        if s[i] != 0xFF:
            i += 1
            continue
        m = s[i + 1]
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            out.append("%02X" % m)
            i += 2
            continue
        if m in (0x00, 0xFF):
            i += 1
            continue
        L = struct.unpack(">H", s[i + 2:i + 4])[0]
        seg = s[i + 4:i + 2 + L]
        desc = "%02X(%d)" % (m, L)
        if m == 0xC4:
            j, ids = 0, []
            while j < len(seg):
                tc = seg[j]
                n = sum(seg[j + 1:j + 17])
                ids.append("%d/%d:%d" % (tc >> 4, tc & 15, n))
                j += 17 + n
            desc += "[" + ",".join(ids) + "]"
        if m == 0xDB:
            desc += "[%s]" % ",".join(str(seg[k] & 15) for k in range(0, len(seg), 65))
        if 0xC0 <= m <= 0xC3:
            P, Y, X, nc = struct.unpack(">BHHB", seg[:6])
            desc += "[P%d %dx%d nc%d %s]" % (P, X, Y, nc, " ".join("id%d h%dv%d q%d" % (seg[6 + 3 * k], seg[7 + 3 * k] >> 4, seg[7 + 3 * k] & 15, seg[8 + 3 * k]) for k in range(nc)))
        if m == 0xDA:
            nc = seg[0]
            desc += "[%s Ss%d Se%d AhAl%02X]" % (" ".join("id%d td%d ta%d" % (seg[1 + 2 * k], seg[2 + 2 * k] >> 4, seg[2 + 2 * k] & 15) for k in range(nc)), seg[1 + 2 * nc], seg[2 + 2 * nc], seg[3 + 2 * nc])
            out.append(desc)
            # skip entropy data to the next non-RST marker
            i = i + 2 + L
            while i < len(s) - 1 and not (s[i] == 0xFF and s[i + 1] not in (0x00,) and not (0xD0 <= s[i + 1] <= 0xD7)):
                i += 1
            continue
        if m == 0xDD:
            desc += "[ri %d]" % struct.unpack(">H", seg[:2])
        out.append(desc)
        i += 2 + L
    return " ".join(out)


def splice(tables, tile):
    return tile[:2] + tables[2:-2] + tile[2:]


JFIF = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"


def try_jxl(label, s, ref):
    try:
        jx = imagecodecs.jpegxl_encode_jpeg(s, numthreads=1)
        back = bytes(imagecodecs.jpegxl_decode_jpeg(jx))
        px = np.asarray(imagecodecs.jpeg8_decode(s))
        rec("jxl_" + label, "OK %d -> %d B (%.4f) reconstruct_exact %s pixels==orig %s" % (
            len(s), len(jx), len(s) / len(jx), back == s, np.array_equal(px, ref)))
    except Exception as e:                                     # noqa: BLE001
        rec("jxl_" + label, "FAIL %s" % e)


for tool in ("cjxl", "djxl", "jpegtran", "djpeg", "cjpeg", "jxlinfo"):
    rec("which_" + tool, shutil.which(tool))

slide = sys.argv[1]
PAGE = int(sys.argv[2]) if len(sys.argv) > 2 else 0
sys.path.insert(0, "/tmp")
import real  # noqa: E402  (staged beside this probe: its splice is the one under test)
f = Ranged(URL % slide)
with tifffile.TiffFile(f) as tf:
    pg = tf.pages[PAGE]
    rec("page", "%d shape %s tiled %s rowsperstrip %s segments %d" % (PAGE, pg.shape, pg.is_tiled, pg.rowsperstrip, len(pg.dataoffsets)))
    tables = pg.jpegtables
    rec("tables_markers", markers(tables))
    bc = np.array(pg.databytecounts)
    order = np.argsort(bc)[::-1]
    for idx in sorted({int(order[0]), int(order[len(order) // 10]), int(order[len(order) // 2]), int(order[-1])}):
        f.seek(pg.dataoffsets[idx])
        raw = f.read(pg.databytecounts[idx])
        rec("== tile", "%d bytes %d head %s tail %s" % (idx, len(raw), raw[:4].hex(), raw[-4:].hex()))
        rec("tile_markers", markers(raw))
        full = splice(tables, raw)
        ref = np.asarray(pg.decode(raw, idx, jpegtables=tables)[0])
        pr = raw[:2] + b"".join(v for k, v in sorted(real.split_tables(tables).items(), key=lambda kv: str(kv[0])) if k in real.used_tables(raw)) + raw[2:]
        rec("pruned_markers", markers(pr))
        ref = ref.reshape(np.asarray(imagecodecs.jpeg8_decode(pr)).shape)
        try_jxl("spliced", full, ref)
        try_jxl("pruned", pr, ref)
        try_jxl("jfif", full[:2] + JFIF + full[2:], ref)
        open("/tmp/t.jpg", "wb").write(full)
        if shutil.which("jpegtran"):
            open("/tmp/t.jpg", "wb").write(pr)
            for args in (["-copy", "none"], ["-copy", "none", "-optimize"], ["-copy", "none", "-restart", "0"]):
                p = subprocess.run(["jpegtran"] + args + ["/tmp/t.jpg"], capture_output=True)
                if p.returncode == 0:
                    rec("jpegtran_markers_" + "_".join(args), markers(p.stdout))
                    try_jxl("jpegtran" + "".join(args), p.stdout, ref)
                else:
                    rec("jpegtran_err", p.stderr[:300])
        if shutil.which("cjxl"):
            p = subprocess.run(["cjxl", "--lossless_jpeg=1", "-v", "/tmp/t.jpg", "/tmp/t.jxl"], capture_output=True, text=True)
            rec("cjxl_rc", "%d %s" % (p.returncode, (p.stderr + p.stdout)[-600:].replace("\n", " | ")))
rec("PROBE2", "done")
