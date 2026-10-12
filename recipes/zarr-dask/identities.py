#!/usr/bin/env python3
"""zarr + dask on Graviton4: what chunking must not change, and the two things it does.

Chunked, lazy array I/O is the half of whole-slide and climate workflows the catalog
reaches through imagecodecs and tifffile but never exercises. The claims worth checking
are invariances -- the chunk shape is a STORAGE decision and must not be a scientific one:

 1. STORAGE IS CHUNK-INVARIANT. One array written at four chunk shapes, including a
    non-divisor (17, 13) that leaves 320 ragged chunks, must read back byte-identical.
    Same for zarr format 2 vs 3, for sharded vs unsharded, for every lossless codec, and
    for slices that cross chunk boundaries. All exact, no tolerance.

 2. COMPUTE IS NOT, AND THAT IS NOT A BUG. A blocked reduction adds the numbers in a
    different ORDER, and floating-point addition is not associative, so dask's float sum
    differs from numpy's in the last bits. Integer addition IS associative, so integer
    reductions are bit-exact at every chunking. This recipe measures both.

THE TRAP, AND THE REASON THE OBVIOUS CHECK IS WRONG: "dask == numpy" looks like the
natural assertion for the float sum, and it FAILS -- which reads as a dask bug. It is not.
`math.fsum` is correctly rounded (Shewchuk's exact algorithm), so it is not a third opinion
but the true value, and measured against it NUMPY is 6 ULP out while the multi-chunk dask
results sit at 2-4 ULP -- i.e. closer. Blocked summation is pairwise summation, which is
more accurate than a longer sequential pass, so the disagreement is evidence about the
METRIC and not about either library. This is CLAUDE.md's fixed-point-versus-path line
appearing inside one library: a sum is a path.

SECOND TRAP: `argmax` is not chunk-invariant either, but for a completely different reason
-- ties. Over 65,536 draws from 0..999 the maximum occurs many times, and numpy returns the
first while dask combines per-block winners. The claim worth asserting is therefore the one
about a UNIQUE maximum, not the convenient proxy. Same discipline as "nothing beats itself"
rather than "the best hit is itself".

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import math
import os
import sys

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
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


def ulps(x, y):
    """Distance in units in the last place of the larger operand."""
    if x == y:
        return 0.0
    return abs(x - y) / math.ulp(max(abs(x), abs(y)))


def dirsize(path):
    return sum(os.path.getsize(os.path.join(dp, fn))
               for dp, _, fs in os.walk(path) for fn in fs)


def nfiles(path):
    return sum(len(fs) for _, _, fs in os.walk(path))


import zarr
import dask
import dask.array as da

ver("zarr", lambda: zarr.__version__)
ver("dask", lambda: dask.__version__)
ver("numpy", lambda: np.__version__)
ver("numcodecs", lambda: __import__("numcodecs").__version__)

# ------------------------------------------------------------------- the test arrays
# A tissue-like uint16 texture for the STORAGE checks: structured, not flat (a flat array
# round-trips through anything) and not noise (noise cannot compress, which would make the
# ratio observations meaningless). Staging measures its entropy.
SEED = 20261012
H = W = 512


def make_arrays(seed=SEED, h=H, w=W):
    """The three test arrays. Staging lifts THIS function out of this file by AST and runs
    it, so the data validated before the run and the data used in it cannot drift apart.

    IMG is a tissue-like uint16 texture for the STORAGE checks: structured, so a broken
    codec cannot round-trip it by accident, but not noise, which would compress at 1.00x
    and make every ratio meaningless. FLT is deliberately near-cancelling for the
    REDUCTION checks. INT carries ties at its maximum, which is what makes argmax
    ambiguous.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:h, :w].astype("float64")
    base = (28000
            + 9000 * np.sin(2 * np.pi * xx / 140.0) * np.cos(2 * np.pi * yy / 190.0)
            + 5200 * ((xx - 200) ** 2 + (yy - 300) ** 2 < 90 ** 2)
            - 6100 * ((xx - 380) ** 2 + (yy - 150) ** 2 < 60 ** 2)
            + rng.normal(0, 1400.0, (h, w)))
    img = np.clip(base, 0, 65535).astype("uint16")
    flt = rng.normal(0.0, 1.0, (h, w)).astype("float64")
    integ = rng.integers(0, 1000, (h, w)).astype("int64")
    # PLANT is a constructed case, not a sample: one huge value followed by 262,143 ones.
    # ulp(1e16) is 2.0, so 1e16 + 1.0 == 1e16 exactly -- a left-to-right pass absorbs every
    # one of the ones and loses them all, while any scheme that adds the small terms to each
    # other FIRST recovers them. It is the textbook demonstration that summation order can
    # cost everything, and it belongs here so the recipe can show the effect's full range
    # rather than quoting a magnitude measured somewhere else.
    plant = np.ones((h, w), dtype="float64")
    plant[0, 0] = 1e16
    return img, flt, integ, plant


IMG, FLT, INT, PLANT = make_arrays()
rec("image", "%dx%d uint16, %d distinct levels, %d B"
    % (H, W, len(np.unique(IMG)), IMG.nbytes))
if len(np.unique(IMG)) < 1000:
    die("the test image has only %d distinct levels; it would compress trivially"
        % len(np.unique(IMG)))

rec("float_arrays", "%dx%d float64 sampled, plus a planted [1e16, 1.0 x %d] case"
    % (H, W, H * W - 1))

CHUNKINGS = [(512, 512), (128, 128), (64, 64), (17, 13)]

# ===================================== 1. storage is invariant to the chunk shape
stored = {}
for ch in CHUNKINGS:
    p = "/tmp/zd_chunk_%d_%d.zarr" % ch
    z = zarr.create_array(store=p, shape=IMG.shape, chunks=ch, dtype=IMG.dtype,
                          overwrite=True)
    z[:] = IMG
    back = z[...]
    stored[ch] = back
    if back.shape != IMG.shape or back.dtype != IMG.dtype:
        die("chunks %s changed the array's shape or dtype: %s %s"
            % (ch, back.shape, back.dtype))
    if not np.array_equal(back, IMG):
        die("chunks %s did not round-trip the array exactly; the chunk shape is a storage "
            "decision and must not change the data" % (ch,))
    rec("chunks_%dx%d" % ch, "%d chunks, %d B on disk, identical"
        % (int(np.prod([-(-s // c) for s, c in zip(IMG.shape, ch)])), dirsize(p)))
ref_ch = CHUNKINGS[0]
for ch in CHUNKINGS[1:]:
    if not np.array_equal(stored[ref_ch], stored[ch]):
        die("stores written at %s and %s disagree" % (ref_ch, ch))
rec("chunk_invariance", "4 chunk shapes incl. the non-divisor (17, 13) agree byte for byte")

# the non-divisor case must actually be ragged, or it is not testing the hard path
nx = -(-W // 13)
if W % 13 == 0 or H % 17 == 0:
    die("(17, 13) divides the 512x512 shape, so no partial edge chunk exists and the "
        "ragged path is untested")
rec("ragged_chunks", "(17, 13) over 512x512 leaves partial edge chunks in both axes "
    "(%d x %d grid)" % (-(-H // 17), nx))

# ------------------------------------------------- 2. format 2 and format 3 agree
fmt = {}
for v in (2, 3):
    p = "/tmp/zd_v%d.zarr" % v
    z = zarr.create_array(store=p, shape=IMG.shape, chunks=(64, 64), dtype=IMG.dtype,
                          overwrite=True, zarr_format=v)
    z[:] = IMG
    fmt[v] = z[...]
    if not np.array_equal(fmt[v], IMG):
        die("zarr format %d did not round-trip the array exactly" % v)
    rec("zarr_format_%d" % v, "%s, identical" % type(z.metadata).__name__)
if not np.array_equal(fmt[2], fmt[3]):
    die("the v2 and v3 stores of one array disagree")
rec("format_invariance", "zarr_format 2 and 3 round-trip the same bytes")

# ------------------------------- 3. lossless codecs, and lossy ones as the controls
from zarr.codecs import BloscCodec, GzipCodec, ZstdCodec
ratios = {}
for name, codec in (("blosc", BloscCodec()), ("gzip", GzipCodec()), ("zstd", ZstdCodec())):
    p = "/tmp/zd_c_%s.zarr" % name
    z = zarr.create_array(store=p, shape=IMG.shape, chunks=(64, 64), dtype=IMG.dtype,
                          overwrite=True, compressors=[codec])
    z[:] = IMG
    if not np.array_equal(z[...], IMG):
        die("the %s codec is lossless and did not round-trip the array exactly" % name)
    ratios[name] = IMG.nbytes / dirsize(p)
    rec("codec_%s" % name, "%d B, %.2fx, identical" % (dirsize(p), ratios[name]))
rec("lossless_codecs", "blosc, gzip and zstd all byte-identical")

# THE CONTROL: lossy filters on the same path must break identity, and by graded amounts.
from zarr.codecs import BitRound, Quantize, FixedScaleOffset
LOSSY = [("BitRound(keepbits=6)", BitRound(keepbits=6)),
         ("Quantize(digits=2)", Quantize(digits=2, dtype="float64")),
         ("FixedScaleOffset(scale=10)",
          FixedScaleOffset(offset=0, scale=10, dtype="float64", astype="int16"))]
lossy_err = {}
for name, codec in LOSSY:
    p = "/tmp/zd_lossy_%s.zarr" % name.split("(")[0]
    z = zarr.create_array(store=p, shape=FLT.shape, chunks=(64, 64), dtype="float64",
                          overwrite=True, filters=[codec])
    z[:] = FLT
    back = z[...]
    if back.shape != FLT.shape:
        die("%s changed the array's shape to %s" % (name, back.shape))
    d = float(np.abs(back - FLT).max())
    lossy_err[name] = d
    rec("control_%s" % name.split("(")[0], "max|diff| %.6e" % d)
    if np.array_equal(back, FLT):
        die("%s returned the data unchanged, so the lossless results above do not show "
            "that this comparison can detect a difference at all" % name)
    if not np.isfinite(d) or d <= 0:
        die("%s reported max|diff| %r" % (name, d))
rec("lossy_controls", "three lossy filters on the same write path break identity: %s"
    % ", ".join("%s %.1e" % (n.split("(")[0], e) for n, e in lossy_err.items()))

# ------------------------------------------------ 4. sharding collapses files, not data
p_sh, p_no = "/tmp/zd_shard.zarr", "/tmp/zd_noshard.zarr"
z = zarr.create_array(store=p_sh, shape=IMG.shape, chunks=(64, 64), shards=(256, 256),
                      dtype=IMG.dtype, overwrite=True)
z[:] = IMG
z2 = zarr.create_array(store=p_no, shape=IMG.shape, chunks=(64, 64), dtype=IMG.dtype,
                       overwrite=True)
z2[:] = IMG
n_sh, n_no = nfiles(p_sh), nfiles(p_no)
rec("sharding", "%d files sharded vs %d unsharded, same (64, 64) chunks" % (n_sh, n_no))
if not np.array_equal(z[...], IMG):
    die("a sharded store did not round-trip the array exactly")
if not np.array_equal(z[...], z2[...]):
    die("the sharded and unsharded stores of one array disagree")
if n_sh >= n_no:
    die("sharding produced %d files against %d unsharded; it exists to reduce the object "
        "count and this run would not demonstrate it" % (n_sh, n_no))
rec("sharding_invariance", "identical to the unsharded store while collapsing %d objects "
    "to %d" % (n_no, n_sh))

# ------------------------- 5. partial reads and writes across chunk boundaries
p = "/tmp/zd_region.zarr"
z = zarr.create_array(store=p, shape=IMG.shape, chunks=(64, 64), dtype=IMG.dtype,
                      overwrite=True, fill_value=0)
z[:] = 0
z[30:150, 70:200] = IMG[30:150, 70:200]
want = np.zeros_like(IMG)
want[30:150, 70:200] = IMG[30:150, 70:200]
if not np.array_equal(z[...], want):
    die("a region write spanning chunk boundaries did not land exactly")
rec("region_write", "[30:150, 70:200] across (64, 64) chunks is exact, and the untouched "
    "area stayed at the fill value")

p = "/tmp/zd_slice.zarr"
z = zarr.create_array(store=p, shape=IMG.shape, chunks=(17, 13), dtype=IMG.dtype,
                      overwrite=True)
z[:] = IMG
SLICES = [("single element", np.s_[0:1, 0:1]),
          ("crossing both chunk edges", np.s_[16:19, 12:15]),
          ("whole column", np.s_[:, 100]),
          ("the ragged corner", np.s_[505:, 505:]),
          ("strided", np.s_[3:500:7, 5:510:11])]
for label, sl in SLICES:
    got = z[sl]
    if not np.array_equal(got, IMG[sl]):
        die("the slice %r (%s) of a (17, 13)-chunked store differs from numpy's"
            % (sl, label))
rec("slices", "%d slices on a ragged (17, 13) store match numpy exactly, including a "
    "strided read and the partial corner" % len(SLICES))

# ============ 6. dask: elementwise is exact, and so is INTEGER reduction, at any chunking
dimg = {ch: da.from_array(IMG, chunks=ch) for ch in CHUNKINGS}
for ch in CHUNKINGS:
    d = dimg[ch]
    for label, fd, fn in (("x*2+1", lambda z: z * 2 + 1, lambda z: z * 2 + 1),
                          ("transpose", lambda z: z.T, lambda z: z.T),
                          ("where", lambda z: da.where(z > 30000, z, 0),
                           lambda z: np.where(z > 30000, z, 0))):
        got = np.asarray(fd(d).compute())
        if not np.array_equal(got, fn(IMG)):
            die("dask's %s at chunks %s does not match numpy elementwise" % (label, ch))
rec("elementwise", "x*2+1, transpose and where are array-identical to numpy at all %d "
    "chunkings" % len(CHUNKINGS))

for ch in CHUNKINGS:
    d = da.from_array(INT, chunks=ch)
    s, m = int(d.sum().compute()), int(d.max().compute())
    if s != int(INT.sum()):
        die("dask's integer sum at chunks %s is %d, numpy's is %d; integer addition is "
            "associative so the chunking cannot change it" % (ch, s, int(INT.sum())))
    if m != int(INT.max()):
        die("dask's integer max at chunks %s is %d, numpy's is %d" % (ch, m, int(INT.max())))
rec("integer_reduction", "sum and max are bit-exact against numpy at every chunking "
    "(sum %d) -- integer addition is associative" % int(INT.sum()))
for ch in CHUNKINGS:
    d = da.from_array(FLT, chunks=ch)
    if float(d.max().compute()) != float(FLT.max()) or float(d.min().compute()) != float(FLT.min()):
        die("dask's float min/max at chunks %s differ from numpy's; min and max are "
            "order-independent even in floating point" % (ch,))
rec("float_minmax", "float min and max are exact at every chunking too -- they are "
    "selections, not accumulations")

# ===== 7. the float SUM, against a correctly rounded reference and across conditioning
# `math.fsum` is exactly rounded (Shewchuk), so it is not a third opinion but the answer;
# staging proves that against exact rational arithmetic. Two arrays, because the effect is
# a property of the SUM's conditioning and not of dask: kappa = sum|x| / |sum x| says how
# much cancellation the accumulation has to survive.
SUM_CHUNKS = [(512, 512), (128, 128), (64, 64), (37, 51), (8, 8)]
# The bound is DERIVED, not chosen. Summation's backward error -- the error relative to the
# absolute magnitude accumulated -- is O(n*eps) for a left-to-right pass and O(log2(n)*eps)
# for any pairwise or blocked scheme. numpy sums pairwise and dask sums blocks of pairwise
# sums, so both must meet the log2 bound; a naive pass must not, and the planted case below
# shows it missing by four orders of magnitude. So this bound discriminates rather than
# merely passing.
EPS = float(np.finfo("float64").eps)
BOUND = math.log2(H * W) * EPS
summary = []
for label, arr in (("sampled", FLT), ("planted", PLANT)):
    true = math.fsum(arr.ravel().tolist())
    at = float(np.abs(arr).sum())
    kappa = at / abs(true)
    nps = float(arr.sum())
    if not math.isfinite(true) or not math.isfinite(nps):
        die("the %s sum is not finite" % label)
    rec("%s_conditioning" % label, "kappa = %.4g (sum|x| %.6g accumulating to %.6g)"
        % (kappa, at, abs(true)))
    rec("%s_fsum" % label, "%.17g  (correctly rounded, so the true value)" % true)
    rec("%s_numpy" % label, "%.17g  %.2f ULP from truth" % (nps, ulps(nps, true)))

    sums, ulp = {}, {}
    for ch in SUM_CHUNKS:
        s = float(da.from_array(arr, chunks=ch).sum().compute())
        if not math.isfinite(s):
            die("the dask sum of %s at chunks %s is not finite" % (label, ch))
        sums[ch], ulp[ch] = s, ulps(s, true)
        rec("%s_dask_%dx%d" % ((label,) + ch), "%.17g  %.2f ULP  == numpy: %s"
            % (s, ulp[ch], s == nps))

    # The scale-free statement: a summation's error belongs against the magnitude it
    # accumulated, not against its own (possibly cancelled) result.
    worst = max(abs(s - true) / at for s in [nps] + list(sums.values()))
    if worst > BOUND:
        die("a %s sum is %.3e of the accumulated magnitude from the correctly rounded "
            "value, past the log2(%d)*eps = %.3e that a pairwise or blocked summation of "
            "this length must achieve" % (label, worst, H * W, BOUND))
    rec("%s_backward_error" % label, "every sum within %.3e of truth relative to the "
        "%.6g accumulated (bound log2(%d)*eps = %.3e)" % (worst, at, H * W, BOUND))

    # THE CONTROL: a single chunk reproduces numpy EXACTLY, and at least one multi-chunk
    # layout does not -- so the difference is attributable to the blocking and nothing else.
    if sums[(512, 512)] != nps:
        die("even as one chunk, dask's %s sum (%.17g) differs from numpy's (%.17g); then "
            "no multi-chunk difference can be attributed to the blocking"
            % (label, sums[(512, 512)], nps))
    multi = [ch for ch in SUM_CHUNKS if ch != (512, 512)]
    differ = [ch for ch in multi if sums[ch] != nps]
    if not differ:
        die("every one of the %d multi-chunk layouts reproduced numpy's %s sum bit for "
            "bit, so this run does not demonstrate that a blocked reduction reorders the "
            "additions at all" % (len(multi), label))
    rec("%s_blocking_attributed" % label, "one chunk == numpy exactly; %d of %d "
        "multi-chunk layouts differ -- the reordering is the blocking, nothing else"
        % (len(differ), len(multi)))
    closer = [ch for ch in multi if ulp[ch] < ulps(nps, true)]
    summary.append((label, kappa, ulps(nps, true),
                    [ulp[ch] for ch in multi], len(closer), len(multi)))

# AND THE DIRECTION, which is the part the obvious reading of "dask != numpy" gets wrong.
# A naive left-to-right pass is the control: on the planted array it loses every one of the
# 262,143 ones, so reordering is not a rounding curiosity -- it is the whole answer. Which
# of two *good* schemes lands nearer the truth is data-dependent and is reported, never
# asserted.
naive = 0.0
for v in PLANT.ravel().tolist():
    naive += v
true_p = math.fsum(PLANT.ravel().tolist())
ones = float(H * W - 1)
lost = abs(true_p - naive)
rec("naive_sequential_sum", "%.17g vs the true %.17g -- a left-to-right pass absorbs all "
    "%d ones and returns the big value unchanged. The gap reads %.0f rather than %d "
    "because ulp(1e16) = %.1f, so at this magnitude the true sum is representable only to "
    "even integers (%.0f ULP from truth)"
    % (naive, true_p, int(ones), lost, int(ones), math.ulp(1e16), ulps(naive, true_p)))
if lost < ones * 0.5:
    die("a naive left-to-right sum lost only %.6g of %.6g on an array built so that "
        "1e16 + 1.0 == 1e16; the planted case is not doing what it was planted for"
        % (lost, ones))
# THE BOUND MUST REJECT THE NAIVE SUM, or meeting it says nothing.
naive_be = lost / float(np.abs(PLANT).sum())
rec("bound_discriminates", "the naive pass's backward error is %.3e against the "
    "log2(%d)*eps = %.3e bound -- %.0fx outside, so the bound separates a blocked "
    "summation from an order-naive one rather than just passing everything"
    % (naive_be, H * W, BOUND, naive_be / BOUND))
if naive_be <= BOUND:
    die("the naive sum's backward error %.3e is inside the %.3e bound, so the bound does "
        "not discriminate and the sums meeting it have proved nothing"
        % (naive_be, BOUND))
np_p = float(PLANT.sum())
rec("ordering_matters", "on the same bytes: naive %.2f ULP from truth, numpy %.2f, dask "
    "%.2f-%.2f. Reordering is worth %.0f ULP here, so 'dask != numpy' in the last bit is "
    "not evidence either is wrong -- both are vastly better than the order-naive answer."
    % (ulps(naive, true_p), ulps(np_p, true_p),
       min(u for l, k, n, mu, c, m in summary if l == "planted" for u in mu),
       max(u for l, k, n, mu, c, m in summary if l == "planted" for u in mu),
       ulps(naive, true_p)))
for label, kappa, npu, mu, nclose, nmulti in summary:
    rec("%s_accuracy_note" % label, "kappa %.4g: numpy %.2f ULP from truth, the "
        "multi-chunk dask sums %s; %d of %d are CLOSER than numpy. Blocked summation IS "
        "pairwise summation, so neither is systematically better -- reported, not asserted."
        % (kappa, npu, "/".join("%.2f" % u for u in mu), nclose, nmulti))

# ======== 8. argmax is not chunk-invariant under TIES -- so assert the claim, not the proxy
ties = int(np.count_nonzero(INT == INT.max()))
rec("argmax_ties", "the integer array's maximum %d occurs %d times" % (int(INT.max()), ties))
if ties < 2:
    die("the integer array has a unique maximum, so the tie-breaking demonstration below "
        "cannot happen and the recipe would overstate argmax's invariance")
disagree = []
for ch in CHUNKINGS:
    g = int(da.from_array(INT, chunks=ch).argmax().compute())
    if g != int(INT.argmax()):
        disagree.append((ch, g))
rec("argmax_tied_note", "with %d tied maxima, dask's argmax differs from numpy's at %d of "
    "%d chunkings (numpy returns the first occurrence, %d; dask combines per-block "
    "winners). RECORDED, not asserted: an exact argmax check on tied data is flaky by "
    "construction." % (ties, len(disagree), len(CHUNKINGS), int(INT.argmax())))
if not disagree:
    die("argmax agreed at every chunking despite %d tied maxima; then the warning this "
        "recipe records is not reproducible here and should not be published" % ties)

# the claim actually worth asserting: a UNIQUE maximum is found identically everywhere
UNI = INT.copy()
UNI[np.unravel_index(0, UNI.shape)] = 0
UNI[123, 456] = 10 ** 6
if int(np.count_nonzero(UNI == UNI.max())) != 1:
    die("the planted maximum is not unique, so the assertion below is the flaky one again")
flat_want = int(np.ravel_multi_index((123, 456), UNI.shape))
for ch in CHUNKINGS:
    g = int(da.from_array(UNI, chunks=ch).argmax().compute())
    if g != flat_want or g != int(UNI.argmax()):
        die("with a unique maximum, dask's argmax at chunks %s is %d, not %d; a unique "
            "extremum has no tie to break and must be chunk-invariant" % (ch, g, flat_want))
rec("argmax_unique", "with a unique maximum, argmax is identical to numpy's at every "
    "chunking (flat index %d) -- the assertion the tied case cannot support" % flat_want)

# ====================================== 9. rechunk, and the zarr<->dask round trip
d = da.from_array(FLT, chunks=(64, 64))
if not np.array_equal(np.asarray(d.rechunk((17, 13)).compute()), FLT):
    die("rechunking (64, 64) -> (17, 13) changed the data")
rec("rechunk", "(64, 64) -> (17, 13) preserves every element exactly")
p = "/tmp/zd_fromzarr.zarr"
da.to_zarr(da.from_array(IMG, chunks=(64, 64)), p, overwrite=True)
rt = np.asarray(da.from_zarr(p).compute())
if not np.array_equal(rt, IMG):
    die("a da.to_zarr / da.from_zarr round-trip changed the data")
rec("dask_zarr_roundtrip", "da.to_zarr -> da.from_zarr is exact, chunks read back as %s"
    % (da.from_zarr(p).chunksize,))

# ============== 10. a pyramid, which is the pathology use -- and it conserves exactly
g = zarr.open_group("/tmp/zd_pyramid.zarr", mode="w")
l0 = g.create_array("0", shape=IMG.shape, chunks=(64, 64), dtype=IMG.dtype, overwrite=True)
l0[:] = IMG
levels = [IMG.astype("uint32")]
for lvl in (1, 2):
    src = levels[-1]
    dsrc = da.from_array(src, chunks=(64, 64))
    got = np.asarray(da.coarsen(np.sum, dsrc, {0: 2, 1: 2}).compute())
    want = src.reshape(src.shape[0] // 2, 2, src.shape[1] // 2, 2).sum(axis=(1, 3))
    if not np.array_equal(got, want):
        die("dask's 2x2 coarsen at level %d does not match numpy's blockwise sum" % lvl)
    arr = g.create_array(str(lvl), shape=got.shape, chunks=(64, 64), dtype="uint32",
                         overwrite=True)
    arr[:] = got
    levels.append(got)
    rec("pyramid_level_%d" % lvl, "%s, dask coarsen == numpy exactly" % (got.shape,))
tot = [int(l.astype("uint64").sum()) for l in levels]
rec("pyramid_conservation", "total intensity %s across %d levels"
    % (" == ".join(str(t) for t in tot), len(tot)))
if len(set(tot)) != 1:
    die("the pyramid does not conserve total intensity: %s; a 2x2 SUM downsample in "
        "integer arithmetic is exactly conservative" % tot)
if sorted(g.array_keys()) != ["0", "1", "2"]:
    die("the pyramid group holds %s, not three levels" % sorted(g.array_keys()))

# ---------------------------------------------------------------------- determinism
p = "/tmp/zd_det.zarr"
z = zarr.create_array(store=p, shape=IMG.shape, chunks=(64, 64), dtype=IMG.dtype,
                      overwrite=True, compressors=[BloscCodec()])
z[:] = IMG
first = dirsize(p)
z2 = zarr.create_array(store="/tmp/zd_det2.zarr", shape=IMG.shape, chunks=(64, 64),
                       dtype=IMG.dtype, overwrite=True, compressors=[BloscCodec()])
z2[:] = IMG
if dirsize("/tmp/zd_det2.zarr") != first:
    die("two identical blosc-compressed stores differ in size (%d vs %d); the recipe "
        "quotes encoded sizes" % (first, dirsize("/tmp/zd_det2.zarr")))
s1 = float(da.from_array(FLT, chunks=(64, 64)).sum().compute())
s2 = float(da.from_array(FLT, chunks=(64, 64)).sum().compute())
if s1 != s2:
    die("two identical dask sums gave %.17g and %.17g" % (s1, s2))
rec("determinism", "repeat blosc stores are the same size and repeat dask sums are "
    "bit-identical")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("ZARR_DASK OK")
