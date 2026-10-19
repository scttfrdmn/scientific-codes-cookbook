---
tool: imagecodecs
tool_version: "2026.8.16"
env: pathology
image: quay.io/aarchsci/pathology@sha256:9c7f7713f34ef818acc7aa07ee9084e8df293666277497bccb297d966797fc5a
env_x86_64: pathology-c817c8dd8540
spawn_version: 0.127.0
last_verified: 2026-10-12
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# imagecodecs — "lossless" checked as byte identity, 25 round-trips

Encodes and decodes a tissue-like image on Graviton4 through every lossless codec this build provides, plus tiled TIFFs, asserting `array_equal` rather than a tolerance. For anyone storing whole-slide imagery on ARM.

## Run it

```bash
make stage RECIPE=imagecodecs     # once: the checks only — the image is synthesised
spawn task run --spec "$(make -s spec RECIPE=imagecodecs)" --wait
make ls RECIPE=imagecodecs

np.array_equal(imagecodecs.png_decode(imagecodecs.png_encode(a)), a)        # True
np.frombuffer(imagecodecs.zstd_decode(imagecodecs.zstd_encode(a.tobytes())),
              dtype=a.dtype).reshape(a.shape)                               # byte codec
tifffile.imwrite("t.tif", a, tile=(256, 256), compression="jpegxl")         # 1.61x, exact
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `frombuffer(...).reshape(...)` for byte codecs | comparing the decode directly | **deflate/lzma/zstd/lz4/bz2/brotli/snappy decode to a FLAT BUFFER.** Compare one to a 2-D array and you get `shape ()` and conclude the codec is broken. It isn't — the caller owns shape and dtype. |
| the one-flag controls | the identity checks alone | **keep them.** `webp(lossless=False)`, `jpeg2k(reversible=False)` and `jpegxl(lossless=False)` each break identity on the same codec and data — that is what makes 25 passing round-trips mean something. |
| `compression="jpegxl"` in tifffile | `"jpeg"` | jpegxl is lossless at **1.61×**; `jpeg` is **not** (`max|diff| 27`). For slide archives that difference is the whole question. |
| the synthetic image | your own tile | staging checks it is a *fair* test — 168 grey levels, entropy 6.58 bits/px, neighbour difference 6.9/255. A flat image round-trips through anything; pure noise cannot compress at all. |
| `lz4`, `snappy`, `packbits` | deflate/zstd | they are **~1.00×** on this data and packbits is **0.99×** — it expands. Speed-first codecs, not size-first; the recipe records that rather than asserting compression. |

**Leave the fixture.** Nothing is fetched: byte identity needs no reference value, so the image is synthesised from a pinned seed. **Scale it** to a real slide tile — the identities hold at any size, and [pathology](../pathology/README.md) stages CAMELYON16 if you want genuine tissue.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All 25 round-trips plus 9 TIFF writes are **5 s** of a 1m04s window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: 25 byte-identical round-trips, four controls including three codecs that flip on one argument, and two honest non-results</summary>

### The checks

| observable | assertion | observed | x86_64 (m8i.large) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | identical |
| imagecodecs / tifffile / numpy | read from the running install | **2026.8.16** / 2026.9.20 / 2.5.4 | identical |
| test image | ≥ 50 grey levels | 168 | identical |
| **byte codecs** | **buffer identical, 7 of 7** | deflate, lzma, zstd, lz4, bz2, brotli, snappy | identical |
| **image codecs** | **array identical, 10 of 10** | png (grey+RGB), webp, jpeg2k, jpegxl, lerc, tiff, jpegls, jpegxr, zfp | identical |
| **tifffile, tiled** | **identical, 8 of 8** | none, deflate, lzma, zstd, jpeg2000, jpegxl, lzw, packbits | identical |
| **control: `webp(lossless=False)`** | **must differ, and compress more** | 2.97× → **30.51×**, `max|diff| 25` | identical |
| **control: `jpeg2k(reversible=False)`** | **must differ, and compress more** | 1.61× → **1044.40×**, `max|diff| 96` | identical |
| **control: `jpegxl(lossless=False)`** | **must differ, and compress more** | 3.71× → **44.46×**, `max|diff| 27` | identical |
| **control: jpeg8** | no lossless mode; `0 < max|diff| < 64` | 7.18×, **10** | identical |
| **control: tifffile `jpeg`** | must differ | 33,560 bytes, `max|diff| 27` | identical |
| compression effective | > 1.05× for the 5 general compressors | 1.22×–1.50× | identical |
| lz4 / snappy / packbits | *recorded, not asserted* | 1.00× / 1.00× / **0.99×** | identical |
| determinism | repeat encode byte-identical | deflate, png, zstd, jpeg8 | identical |

### Identity, not similarity

A pathology archive stores terabytes under a codec advertised as lossless, and that
advertisement is a correctness claim — so the check is `np.array_equal`, with no tolerance
anywhere:

```text
byte codecs      7 of 7 identical   (buffer, reshaped by the caller)
image codecs    10 of 10 identical   (array, shape and dtype restored)
tiled TIFF       8 of 8 identical   (through tifffile's writer)
```

Twenty-five round-trips. The compression ratios are recorded alongside each because a codec
that returned its input unchanged would also be "lossless" — deflate at 1.26× and jpegxl at
3.71× show work was done.

### Four controls, and three of them are one argument apart

Twenty-five passing identities could all be a comparison that is not really comparing. The
sharpest refutation available is a codec that flips behaviour on a single flag — same library,
same code path, same data:

```text
webp     lossless=True   2.97x   identical        lossless=False    30.51x   max|diff| 25
jpeg2k   reversible=True 1.61x   identical        reversible=False 1044.40x  max|diff| 96
jpegxl   lossless=True   3.71x   identical        lossless=False    44.46x   max|diff| 27
```

Both directions are asserted: the lossy mode must differ from the input **and** must compress
better than the lossless one. A flag that changed nothing, or that lost fidelity without buying
size, fails. jpeg2k at **1044×** with `max|diff| 96` on 8-bit data is the extreme — most of the
image is gone, which is exactly what makes it a good control.

Two further controls come from codecs with no lossless mode at all: `jpeg8` directly
(`max|diff| 10`), and tifffile's `compression="jpeg"` (`max|diff| 27`) — the second routed
through the same writer that produced the eight exact TIFFs, so the control and the claim share
a code path.

### The result that looks like a failure and is not

`deflate`, `lzma`, `zstd`, `lz4`, `bz2`, `brotli` and `snappy` are **byte** codecs. Their decode
returns a flat buffer, so comparing it to a 2-D array reports `shape ()` and reads as a broken
codec:

```python
imagecodecs.zstd_decode(blob)                                  # flat bytes
np.frombuffer(..., dtype=a.dtype).reshape(a.shape)             # the caller restores this
```

An earlier probe of mine reported exactly that `shape ()` for all five it tried, and the
conclusion "five codecs failed" would have been wrong — the quantity compared was not the
quantity meant. Image codecs (`png`, `webp`, `jpeg*`, `lerc`, `tiff`, `zfp`) do restore shape and
dtype, and the recipe asserts both.

### Two honest non-results

**`lz4` and `snappy` do not compress this image** — 1.00× each. They are speed-first codecs and
this is the expected outcome, so the recipe records it rather than asserting compression and
quietly excluding them.

**`packbits` expands it**, to 0.99× of raw. A run-length scheme on textured data can do that. It
still round-trips exactly, which is the claim being tested; the ratio is reported so nobody reads
"8 of 8 lossless" as "8 of 8 useful".

Staging is what makes those ratios interpretable: it measures the image's first-order entropy at
**6.579 bits/px** (an ideal coder would reach ~1.22×) and the mean horizontal neighbour difference
at **6.856/255**. Pure noise sits at 8 bits/px and cannot be compressed at all, which would make
the `>1.05×` assertions unsatisfiable rather than strict; a flat image would round-trip through a
broken codec. Both failure modes are checked before the run.

### Pins

| | |
|---|---|
| data | **none.** A structured 512×512 image from `default_rng(20261012)`; the reference is byte identity, which needs no value |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/pathology@sha256:9c7f7713f34ef818ac…` — imagecodecs 2026.8.16, tifffile 2026.9.20, openslide 4.0.1, scikit-image 0.26.0, zarr, dask 2026.8.0, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

This gap was named by the catalog itself — [pathology](../pathology/README.md) records that
"`tifffile`, `imagecodecs`, `zarr` and `dask` are in this env and unexercised". Two of the four
are exercised here; [zarr-dask](../zarr-dask/README.md) now covers the other two.

One API correction from probing: `lerc_encode` takes no `maxzerror` argument in this build (it is
lossless by default), so a guessed keyword raises `TypeError`.

**x86_64.** The same spec with `ARCH=x86_64` runs on `m8i.large` through the `pathology-c817c8dd8540`
twin (`envs-x86_64/`: the pinned image's versions as linux-64 builds, installed offline in 7 s).
imagecodecs, tifffile and numpy are the same versions; 5 of 208 versions differ, and one is in the
computation: **charls 2.4.5** (arm64 2.4.4), the JPEG-LS codec. `score.tsv` is **byte-identical**
across chips anyway — every lossless round trip, every encoded size (JPEG-LS 162,464 bytes on
both), and the lossy controls' `max|diff|`. The others are fonttools, libcurl, libglib,
libsqlite.

### Run + verify

```sh
make stage RECIPE=imagecodecs
spawn task run --spec "$(make -s spec RECIPE=imagecodecs)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/imagecodecs/r1/score.tsv" -
```

Fails on a pin mismatch, a missing codec function, any lossless round-trip that is not
byte-identical, an image codec that changes shape or dtype, a lossy mode that returns the input
unchanged or fails to compress better than its lossless sibling, a jpeg8 difference outside
`(0, 64)`, a general-purpose compressor below 1.05×, non-deterministic encoding, a tiled TIFF that
does not read back identically, or a JPEG-compressed TIFF that does — but check the bucket
regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real slide data.** The image is synthetic; [pathology](../pathology/README.md) stages CAMELYON16
TIFFs and reads them with OpenSlide, but no recipe yet re-encodes genuine tissue through these
codecs at slide scale, where the ratios would be very different.

**Chunked, lazy access to a pyramidal store** — the half of whole-slide I/O this does not
touch — is covered separately by [zarr-dask](../zarr-dask/README.md).
**Pyramidal/multi-resolution TIFF**: one level,
one tile size, no `subifds`. **Throughput**: every codec here is correctness-only, with no
encode/decode timing, so nothing says which is affordable at terabyte scale. **16-bit and
multichannel** beyond RGB — fluorescence imagery is commonly 16-bit, where `lerc` and `zfp` matter
most. **Lossy fidelity metrics**: the controls assert only that lossy modes differ, not that their
error is acceptable for diagnosis, which is a clinical question rather than a numerical one.

</details>
