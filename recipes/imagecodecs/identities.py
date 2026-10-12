#!/usr/bin/env python3
"""imagecodecs + tifffile on Graviton4: "lossless" checked as byte identity.

Whole-slide pathology pipelines store terabytes under codecs advertised as lossless, and
that advertisement is a correctness claim. So the check is `np.array_equal` -- not a
tolerance, not a PSNR -- across every lossless codec this build provides, in both the
imagecodecs API and through tifffile's tiled writer.

What makes identity mean something is the control, and this env offers the sharpest kind:
THREE CODECS THAT FLIP ON ONE ARGUMENT. `webp(lossless=True)` is identical and
`webp(lossless=False)` is not; same for jpeg2k's `reversible` and jpegxl's `lossless`.
Same codec, same data, one flag -- so a passing identity cannot be an artifact of the
comparison being blind.

One distinction worth knowing before reading any result, because it looks like a failure:
BYTE codecs (deflate, lzma, zstd, lz4, bz2, brotli, snappy) round-trip a BUFFER and the
caller owns shape and dtype -- decoding returns flat bytes, so comparing the output to a
2-D array reports `shape ()` and looks broken. IMAGE codecs (png, webp, jpeg*, lerc, tiff,
zfp) round-trip the array including its shape. Both are exact; they just answer different
questions.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import os
import sys

import numpy as np
import imagecodecs

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


ver("imagecodecs", lambda: imagecodecs.__version__)
ver("numpy", lambda: np.__version__)
try:
    import tifffile
    HAVE_TIFF = True
except Exception as exc:
    tifffile = None
    HAVE_TIFF = False
ver("tifffile", lambda: tifffile.__version__ if HAVE_TIFF else "not importable")

# ------------------------------------------------------------------- the test image
# Structured texture, not noise and not flat: a flat image compresses trivially (so a
# broken codec could still "work") and pure noise does not compress at all (so a ratio
# assertion would be meaningless). Staging checks both properties.
SEED = 20261012
H = W = 512
rng = np.random.default_rng(SEED)
yy, xx = np.mgrid[:H, :W].astype("float64")
base = (140
        + 40 * np.sin(2 * np.pi * xx / 140.0) * np.cos(2 * np.pi * yy / 190.0)
        + 25 * ((xx - 200) ** 2 + (yy - 300) ** 2 < 90 ** 2)
        - 30 * ((xx - 380) ** 2 + (yy - 150) ** 2 < 60 ** 2)
        + rng.normal(0, 6.0, (H, W)))
GRAY = np.clip(base, 0, 255).astype("uint8")
RGB = np.dstack([GRAY,
                 np.clip(base * 0.92 + 8, 0, 255).astype("uint8"),
                 np.clip(base * 1.05 - 6, 0, 255).astype("uint8")])
F32 = base.astype("float32")
rec("image", "%dx%d uint8 grey, %s uint8 RGB, float32" % (H, W, RGB.shape))
rec("grey_levels", "%d" % len(np.unique(GRAY)))
if len(np.unique(GRAY)) < 50:
    die("the test image has only %d distinct levels; it would compress trivially and a "
        "broken codec could still round-trip it" % len(np.unique(GRAY)))

# ------------------- 1. BYTE codecs: the buffer round-trips, the caller owns the shape
BYTE_CODECS = ["deflate", "lzma", "zstd", "lz4", "bz2", "brotli", "snappy"]
byte_ok, byte_ratios = [], {}
for name in BYTE_CODECS:
    enc = getattr(imagecodecs, "%s_encode" % name, None)
    dec = getattr(imagecodecs, "%s_decode" % name, None)
    if enc is None or dec is None:
        die("imagecodecs in this build has no %s_encode/_decode; the recipe asserts it "
            "is present" % name)
    blob = enc(GRAY.tobytes())
    out = np.frombuffer(dec(blob), dtype=GRAY.dtype).reshape(GRAY.shape)
    ratio = GRAY.nbytes / len(blob)
    byte_ratios[name] = ratio
    if not np.array_equal(out, GRAY):
        die("%s is a lossless byte codec and did not round-trip the buffer exactly" % name)
    byte_ok.append(name)
    rec("byte_%s" % name, "%d bytes, %.2fx, identical" % (len(blob), ratio))
rec("byte_codecs", "%d of %d round-trip exactly" % (len(byte_ok), len(BYTE_CODECS)))

# ------------------------------ 2. IMAGE codecs: the array round-trips including shape
IMAGE_CASES = [("png", GRAY), ("png", RGB), ("webp", RGB), ("jpeg2k", GRAY),
               ("jpegxl", RGB), ("lerc", GRAY), ("tiff", GRAY), ("jpegls", GRAY),
               ("jpegxr", GRAY), ("zfp", F32)]
img_ok, img_ratios = 0, {}
for name, arr in IMAGE_CASES:
    enc = getattr(imagecodecs, "%s_encode" % name, None)
    dec = getattr(imagecodecs, "%s_decode" % name, None)
    if enc is None or dec is None:
        die("imagecodecs has no %s_encode/_decode in this build" % name)
    blob = enc(arr)
    out = np.asarray(dec(blob))
    tag = "%s_%s" % (name, "rgb" if arr.ndim == 3 else str(arr.dtype))
    ratio = arr.nbytes / len(blob)
    img_ratios[tag] = ratio
    if out.shape != arr.shape:
        die("%s decoded to shape %s, not %s -- an image codec must restore the shape"
            % (name, out.shape, arr.shape))
    if out.dtype != arr.dtype:
        die("%s decoded to dtype %s, not %s" % (name, out.dtype, arr.dtype))
    if not np.array_equal(out, arr):
        d = float(np.abs(out.astype("f8") - arr.astype("f8")).max())
        die("%s is lossless by default in this build but changed the data (max |diff| "
            "%g)" % (name, d))
    img_ok += 1
    rec("image_%s" % tag, "%d bytes, %.2fx, identical" % (len(blob), ratio))
rec("image_codecs", "%d of %d round-trip exactly" % (img_ok, len(IMAGE_CASES)))

# ------- 3. THE CONTROL: one flag flips identity, on three codecs, same data each time
FLAG_CASES = [("webp", RGB, {"lossless": True}, {"lossless": False, "level": 75}),
              ("jpeg2k", GRAY, {"reversible": True}, {"reversible": False, "level": 20}),
              ("jpegxl", RGB, {"lossless": True}, {"lossless": False, "level": 80})]
for name, arr, kw_ok, kw_lossy in FLAG_CASES:
    enc = getattr(imagecodecs, "%s_encode" % name)
    dec = getattr(imagecodecs, "%s_decode" % name)
    b_ok = enc(arr, **kw_ok)
    o_ok = np.asarray(dec(b_ok))
    if not (o_ok.shape == arr.shape and np.array_equal(o_ok, arr)):
        die("%s with %s was not lossless" % (name, kw_ok))
    b_lo = enc(arr, **kw_lossy)
    o_lo = np.asarray(dec(b_lo))
    if o_lo.shape != arr.shape:
        die("%s lossy decode changed the shape to %s" % (name, o_lo.shape))
    d = float(np.abs(o_lo.astype("f8") - arr.astype("f8")).max())
    r_ok, r_lo = arr.nbytes / len(b_ok), arr.nbytes / len(b_lo)
    rec("flag_%s" % name, "lossless %.2fx identical | lossy %.2fx max|diff| %g"
        % (r_ok, r_lo, d))
    if np.array_equal(o_lo, arr):
        die("%s with %s returned the data unchanged, so the lossless result above does "
            "not demonstrate anything -- the comparison cannot tell the modes apart"
            % (name, kw_lossy))
    if d <= 0:
        die("%s lossy mode reported max |diff| %g" % (name, d))
    # The flag buys compression at the cost of fidelity; both directions must hold.
    if not r_lo > r_ok:
        die("%s lossy mode (%.2fx) did not compress better than lossless (%.2fx), so the "
            "flag is not doing what it claims" % (name, r_lo, r_ok))
rec("flag_control", "webp, jpeg2k and jpegxl each flip identity on one argument")

# jpeg8 has no lossless mode at all -- a second, independent form of the same control.
b = imagecodecs.jpeg8_encode(RGB)
o = np.asarray(imagecodecs.jpeg8_decode(b))
d8 = float(np.abs(o.astype("f8") - RGB.astype("f8")).max())
rec("jpeg8_lossy", "%.2fx, max|diff| %g" % (RGB.nbytes / len(b), d8))
if np.array_equal(o, RGB):
    die("jpeg8 round-tripped exactly; it is a lossy codec and this would mean the "
        "identity checks above cannot distinguish lossless from lossy")
if not 0 < d8 < 64:
    die("jpeg8 max |diff| is %g; outside (0, 64) it is either lossless or broken" % d8)

# -------------------------- 4. compression actually happened where it is claimed
COMPRESSORS = ["deflate", "lzma", "zstd", "bz2", "brotli"]
for name in COMPRESSORS:
    if byte_ratios[name] <= 1.05:
        die("%s compressed only %.3fx; it is a general-purpose compressor and this image "
            "has structure to exploit" % (name, byte_ratios[name]))
rec("compression_effective", "deflate/lzma/zstd/bz2/brotli all above 1.05x")
# RECORDED, not asserted: some codecs do not compress this data at all, and one expands it.
rec("no_compression_note", "lz4 %.2fx, snappy %.2fx -- speed-first codecs, near 1.00x"
    % (byte_ratios["lz4"], byte_ratios["snappy"]))

# ------------------------------------------------------- 5. determinism of encoding
for name in ("deflate", "png", "zstd", "jpeg8"):
    enc = getattr(imagecodecs, "%s_encode" % name)
    arr = RGB if name == "jpeg8" else GRAY
    if enc(arr) != enc(arr):
        die("%s produced different bytes for the same input; the recipe quotes exact "
            "encoded sizes, which would then be unstable" % name)
rec("determinism", "deflate, png, zstd and jpeg8 encode byte-identically on repeat")

# -------------- 6. tifffile: the realistic path -- tiled, compressed, read back
if HAVE_TIFF:
    LOSSLESS_TIFF = [None, "deflate", "lzma", "zstd", "jpeg2000", "jpegxl", "lzw",
                     "packbits"]
    tif_ok = 0
    for comp in LOSSLESS_TIFF:
        fn = "t_%s.tif" % (comp or "none")
        tifffile.imwrite(fn, GRAY, tile=(256, 256), compression=comp)
        back = tifffile.imread(fn)
        sz = os.path.getsize(fn)
        if back.shape != GRAY.shape or not np.array_equal(back, GRAY):
            die("a tiled TIFF written with compression=%r did not read back identically"
                % comp)
        tif_ok += 1
        rec("tiff_%s" % (comp or "none"), "%d bytes, %.2fx, identical" % (sz, GRAY.nbytes / sz))
    rec("tifffile_lossless", "%d of %d compressions round-trip exactly"
        % (tif_ok, len(LOSSLESS_TIFF)))

    # the control, through the same writer: a lossy TIFF compression must differ
    tifffile.imwrite("t_lossy.tif", GRAY, tile=(256, 256), compression="jpeg",
                     compressionargs={"level": 75})
    back = tifffile.imread("t_lossy.tif")
    dl = float(np.abs(back.astype("f8") - GRAY.astype("f8")).max())
    rec("tiff_jpeg_lossy", "%d bytes, max|diff| %g" % (os.path.getsize("t_lossy.tif"), dl))
    if np.array_equal(back, GRAY):
        die("a JPEG-compressed TIFF round-tripped exactly, so the tifffile identity "
            "checks above do not distinguish lossless from lossy")
else:
    rec("tifffile_lossless", "SKIPPED: tifffile not importable")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("IMAGECODECS OK")
