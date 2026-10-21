# imagecodecs on every tile of every annotated CAMELYON16 slide — Graviton4, Intel and AMD

> **The whole 321 GB cohort transcodes losslessly to JPEG XL at 1.35×, and every one of its 14.35 M
> JPEG tiles is rebuilt from the JPEG XL byte for byte, on all three chips.** AMD is 1.25× faster than
> Graviton4 and 7% dearer per result; Intel is 1.31× slower and 53% dearer. Decoded pixels are
> identical across chips down to the sha256. The JPEG XL bitstreams are not: Graviton4's differ
> from x86's on every slide, by 2 ppm in size, and each still rebuilds its JPEG exactly.

Workload: **every annotated CAMELYON16 slide**, the 160 that [pathology-real](../pathology-real/README.md)
reads (111 training, 49 test; 95 Radboud, 65 Utrecht), and **every tile of every page** in them:
14,352,374 JPEG tiles across 1,653 pyramid levels, plus the 14,352,163 tiles of their LZW tumour
masks. Each JPEG tile is decoded, transcoded to JPEG XL, rebuilt and compared. That is the pass
an archive migration makes over a slide collection. The
[recipe](../../recipes/imagecodecs/README.md) runs the same identities on a synthetic 512×512 image.

**Data tier: RODA.** `s3://camelyon-dataset/CAMELYON16/`, streamed in place over public HTTPS in
us-west-2, so nothing is copied. Nothing touches a disk either: each slide arrives in 32 MiB range
GETs and every tile is processed as soon as its bytes are in. The cohort manifest is
pathology-real's [`slides.tsv`](../pathology-real/slides.tsv), read from that measurement's staged
copy and pinned here by sha256 in `pins.sha256`. The md5 of every image and mask (the publisher's
`checksums.md5`) is checked over the streamed bytes.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/imagecodecs-real/stage-inputs.sh "$B"      # scripts + pins; slides.tsv shared
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py imagecodecs $a "$B" r1 \
    measurements/imagecodecs-real/real.task.json)"
done
bash measurements/imagecodecs-real/collect.sh "$B" r1          # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) runs 64 single-threaded worker processes, one slide each, largest first. The
core of it, per tile:

```python
stream = raw[:2] + tables_it_uses + raw[2:]           # the TIFF tile made a complete JPEG
assert np.array_equal(ic.jpeg8_decode(stream), page.decode(raw, idx, jpegtables=...)[0])
jx = ic.jpegxl_encode_jpeg(stream, numthreads=1)
assert bytes(ic.jpegxl_decode_jpeg(jx)) == stream    # and so the TIFF tile itself
```

## Results

64 workers on `c8g.16xlarge`, `c8i.16xlarge` and `c8a.16xlarge` (64 vCPU, 128 GiB). Graviton4 runs
the recipe's image. The two x86 chips run the `pathology` env's linux-64 twin, with the same
imagecodecs 2026.8.16, libjxl 0.12.0, libjpeg-turbo 3.2.0, libtiff 4.7.2 and Pillow 12.3.0:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.16xlarge` | 2,429 s | 2,492 s | 529 MiB | 2.5523 | **1.722** | **1.767** |
| Xeon 6975P-C | `c8i.16xlarge` | 3,187 s | 3,237 s | 535 MiB | 2.9987 | 2.655 | 2.696 |
| EPYC 9R45 | `c8a.16xlarge` | **1,940 s** | **1,979 s** | 534 MiB | 3.4487 | 1.858 | 1.896 |

A result is the whole cohort: 160 slides, 321 GB, about 1.1 cents per slide on Graviton4. "Billed"
is spawn's completion window, and $/hr is spawn's `cost_estimate` over that window. Peak RSS is
the largest single worker (`ru_maxrss`); the box's cgroup peak was 13.2–15.0 GB.

- **The JPEG XL transcode is the cost.** It is 42% of worker CPU time on Graviton4 (52,668 of
  126,592 s), about 6.1 MB/s of JPEG per core. Rebuilding the JPEG is 21% (12 MB/s per core).
  Decoding, hashing, the masks and the codec bake-off share the rest.
- **It is compute-bound, so `c8g`.** Streaming means a worker holds one 32 MiB chunk and one tile,
  never a slide. The fetch is 5% of worker time (6,776 s) at 64 concurrent streams.
- **Intel's 64 vCPU are 32 cores × 2 threads.** Graviton4's and AMD's are 64 full cores. The tiles
  are independent, so the work pays for cores, and Intel's half-cores cost it 1.31× the wall at a
  dearer hour. AMD's Zen 5 cores beat Graviton4's by 1.25× and lose it back in the price.
- n = 1 per chip. Every asserted observable is identical across the three chips (below), so a
  timing difference is a speed difference, not a different amount of work.

## What held at scale, on all three chips

[`real.py`](real.py) asserts these per slide inside the task, so a failure fails the run:

| claim | why it must hold | all three chips |
|---|---|---|
| image and mask match the publisher's md5 | provenance | 320 / 320 objects |
| page format, tile grid = ⌈H/t⌉·⌈W/t⌉, tile ranges disjoint and inside the file | the TIFF is what it says | 1,653 WSI and 1,443 mask pages |
| **the spliced stream decodes to tifffile's own pixels** | pruning the tables is a header edit, not an image edit | **14,352,374 / 14,352,374 tiles** |
| **`jpegxl_decode_jpeg(jpegxl_encode_jpeg(s)) == s`** | JPEG XL's lossless JPEG recompression | **14,352,374 / 14,352,374, 0 refused** |
| the TIFF tile is recovered byte for byte from the rebuilt stream | the archive can be restored | 14,352,374 / 14,352,374 |
| **mask tiles: imagecodecs' LZW == libtiff's (through Pillow)** | two independent LZW decoders | **3,256,103 tiles, 0 differ** (11,096,060 sparse) |
| 16 lossless pixel codecs round-trip every 1,024th level-0 tile, `array_equal` | lossless means identical | 10,574 tiles × 16, 0 differ |

**The archive saving.** 320.67 GB of JPEG tile data becomes 237.69 GB of JPEG XL: **1.349×, 25.9%
smaller, with every byte recoverable.** Radboud's slides gain 1.397× and Utrecht's 1.272×. Per slide
the range is 1.212× to 1.739× (median 1.340). Level 0 gains 1.360×, level 1 1.336×, and levels
2–6 1.25–1.30×. Making each tile a standalone JPEG adds 4.09 GB of tables, 1.3%, which JPEG XL
carries inside the 237.69 GB.

**libjxl refuses the obvious splice, measured two ways** ([`probe2.py`](probe2.py)). A TIFF tile is
an abbreviated JPEG; its Huffman and quantisation tables live once per page in `JPEGTables`.
Splicing all of them in gives a valid JPEG that libjpeg-turbo decodes. `jpegxl_encode_jpeg`
rejects it with `JXL_ENC_ERR_GENERIC` on every tile, for two reasons:

- **An unreferenced quantisation table.** CAMELYON16's tables define quantisation tables 0 and 1,
  but every pyramid tile uses table 0 for all three components. Dropping table 1 is enough;
  unused Huffman tables are accepted.
- **A table defined twice.** The 95 Radboud slides carry a stripped 3629×1577 overview image that
  defines its own quantisation tables. Splicing `JPEGTables` in front defines them again.

So `real.py` splices only the tables a tile references and does not define itself. The first
assertion above is what licenses that edit. `jpegtran -copy none` also produces streams libjxl
accepts, but it re-codes the entropy data, so the rebuilt stream would not be the TIFF's.

### Lossless pixel codecs on genuine tissue

Every 1,024th level-0 tile of every slide (`idx % 1024 == 0`): 10,574 tiles, 2.77 Gpx, 8.32 GB of
RGB that arrived as 221.8 MB of JPEG. Single-thread throughput in Mpx/s:

| codec | ratio vs RGB | size vs the source JPEG | encode, Graviton4 | decode, Graviton4 | encode, Intel | encode, AMD |
|---|---|---|---|---|---|---|
| webp lossless | **7.75×** | 4.8× | 3 | 99 | 3 | 4 |
| jpegxl effort 7 | 7.32× | 5.1× | 3 | 10 | 2 | 3 |
| jpeg2k reversible | 7.07× | 5.3× | 13 | 14 | 9 | 13 |
| **jpegxl effort 1** | **6.82×** | 5.5× | **184** | 49 | 121 | 214 |
| jpegxl effort 3 | 6.45× | 5.8× | 9 | 14 | 6 | 10 |
| bz2 | 5.52× | 6.8× | 7 | 9 | 6 | 8 |
| lzma | 5.07× | 7.4× | 2 | 35 | 1 | 2 |
| png | 5.06× | 7.4× | 8 | 82 | 6 | 8 |
| jpegls | 4.93× | 7.6× | 41 | 62 | 29 | 45 |
| brotli 5 | 4.16× | 9.0× | 25 | 139 | 19 | 30 |
| deflate 6 (libdeflate) | 4.11× | 9.1× | 40 | 124 | 31 | 45 |
| zlib 6 | 4.08× | 9.2× | 13 | 123 | 10 | 13 |
| qoi | 4.04× | 9.3× | 207 | 300 | 146 | 213 |
| zstd 3 | 3.94× | 9.5× | 120 | 603 | 84 | 148 |
| lerc | 3.81× | 9.8× | 37 | 111 | 27 | 42 |
| lz4 | 2.72× | 13.8× | 294 | 523 | 225 | 345 |

- **Never re-encode a JPEG slide's pixels losslessly to save space.** The best pixel codec is still
  4.8× the JPEG it came from. Transcode the JPEG instead (1.35× smaller than the source).
- **For decoded tiles, such as an analysis cache, jpegxl effort 1 is the outlier.** It compresses
  better than every general-purpose codec, at 184 Mpx/s, while effort 3 is 20× slower and
  compresses worse. webp and effort 7 gain another 7–14% at 3 Mpx/s.
- The ratios are over a systematic 1-in-1,024 sample, which is 73% background tiles as the slides
  are. They are reported, not asserted.

## Across chips

[`compare.py`](compare.py) compares every per-slide observable except timings. The output is in
[`results/compare.txt`](results/compare.txt).

| observable | kind | Intel vs Graviton4 | AMD vs Graviton4 |
|---|---|---|---|
| **decoded RGB, sha256 over every tile of every page** | integer decode, **asserted** | **160 / 160 identical** | **160 / 160 identical** |
| decoded mask sha256, label counts, every check count, JPEG and spliced bytes | integer, asserted | 160 / 160 | 160 / 160 |
| **JPEG XL bitstream sha256** | encoder output, reported | **0 / 160** | **0 / 160** |
| JPEG XL bytes | encoder output, reported | +5,061 B of 237.69 GB (+2 ppm); per slide −1,185 to +2,031 B | the same |
| libjxl's pixel decode vs libjpeg-turbo, \|diff\| histogram | float decode, reported | differs on 160 / 160 | differs on 160 / 160 |
| bake-off encoded bytes, 13 integer codecs | encoder output, reported | 160 / 160 | 160 / 160 |
| bake-off bytes, jpegxl effort 3 / effort 7 | encoder output, reported | 120 / 91 of 160 (−11 / −38 B in total) | the same |

**Intel and AMD agree with each other bit for bit on every field**, including the JPEG XL streams
and the histogram. The split is ARM against x86, with the same libjxl version on both. The likely
source is libjxl's encoder heuristics running through different SIMD code (Highway's NEON/SVE
against AVX2/AVX-512 targets); this run does not isolate it. Either way the same coefficients get
a slightly different, equally valid bitstream. That is a path, and it is why the assertion is the
rebuild, not the bytes: every one of the 14.35 M rebuilds is exact on every chip.

**Two correct decoders, which the JPEG standard does not require to agree.** On every tile from
level 2 up (713.8 G samples), libjxl's own pixel decode of the JPEG XL matches libjpeg-turbo on
62.78% of samples, 95.31% within 1, 98.95% within 2, and at most 32 apart (mean 0.433). The two
use different IDCTs and chroma upsampling. The histogram differs across chips in the last digits
(within 2: 98.953% vs 98.952%), which is the float path again. Reported, not asserted.

libjpeg-turbo's decode is integer arithmetic. It runs through NEON on Graviton4 and AVX2 on x86 and
gives the same 3.76 Tpx (every tile, every level) on all three. Nothing here goes through BLAS (`openblas_get_corename()`:
`neoversev2` on Graviton4, `Cooperlake` on both x86 chips).

## Caveats

- **Pillow's TIFF path is libtiff** (`im.tile` decoder `libtiff` on every slide). That is what makes
  the mask comparison two independent LZW decoders. The masks use no predictor.
- **The bake-off is a sample, by design.** At the measured rates, all 16 codecs on every level-0
  tile would cost about 1,024× the bake-off's 7,096 core-seconds. The sample is systematic, fixed
  before the run, and covers every slide.
- **Normal slides are not included.** The manifest is pathology-real's 160 annotated slides; the
  159 `normal_*` slides were not run.
- [`probe.py`](probe.py) ([`00-probe.task.json`](00-probe.task.json)) measured each codec's rate and
  ratio on 280 tiles of two slides, which sized this run. [`probe2.py`](probe2.py)
  ([`00-probe2.task.json`](00-probe2.task.json)) found what libjxl refuses; its spec pins the
  earlier `real.py` it imported, before the splice learned to skip a tile's own tables, so it
  reproduces only with that file from git. Neither asserts anything.
  [`01-canary.task.json`](01-canary.task.json) ran `real.py` on four slides first.
