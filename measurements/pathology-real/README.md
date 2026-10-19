# OpenSlide on every annotated CAMELYON16 slide — Graviton4, Intel and AMD

> **Graviton4 is the cheapest place to read whole-slide images: AMD is 1.10× faster and 21% dearer
> per result, Intel 1.50× slower and 73% dearer.** All 160 slides were read at full resolution over
> their tissue, 725,548 level-0 blocks in all. Every block is byte-identical through openslide and
> through tifffile, and the publisher's tumour masks match the annotation polygons wherever a pixel
> is more than 4 px from an outline. All three chips decoded the same pixels, down to the sha256.

Workload: **every CAMELYON16 slide with tumour annotations**: 111 training and 49 test slides,
321 GB of pyramidal TIFF from two hospitals (95 Radboud, 65 Utrecht). For each slide the task reads
every 1024×1024 level-0 block that has tissue under it, using the publisher's own tissue mask
(`background_tissue/`), plus every block an annotation touches. Each block is read through openslide
and again through tifffile, and its mask block is read too. The annotation polygons are rasterised
at level 0, and a 256×256 patch grid is labelled from the mask. That is the pass a
patch-extraction pipeline makes over a training cohort, at the resolution it makes it. The
[recipe](../../recipes/pathology/README.md) runs the same checks on one slide at level 4.

**Data tier: RODA.** `s3://camelyon-dataset/CAMELYON16/`, read in place over public HTTPS in
us-west-2, so there is no second copy. [`slides.tsv`](slides.tsv) pins every object. Images, masks
and XMLs carry the publisher's md5 from `checksums.md5`. The tissue masks are not in that file, so
they are pinned by S3's single-part ETag. [`make_slides.py`](make_slides.py) rebuilds `slides.tsv`
from the two manifests the recipe already pins by sha256. Every md5 is checked while the object streams in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/pathology-real/stage-inputs.sh "$B"      # scripts + slides.tsv, pinned by sha256
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py pathology $a "$B" r3 \
    measurements/pathology-real/real.task.json)"
done
bash measurements/pathology-real/collect.sh "$B" r3         # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) runs 32 single-threaded worker processes, one slide each, largest first. Each
worker fetches its slide into `/tmp` under a 100 GB budget, analyses it and deletes it:

```python
a = np.asarray(wsi.read_region((x0, y0), 0, (1024, 1024)))[..., :3]     # openslide
b = zarr.open(tifffile.TiffFile(p).aszarr(level=0))[y0:y0+1024, x0:x0+1024]   # tifffile
assert np.array_equal(a, b)
```

## Results

32 workers on `r8g.8xlarge`, `r8i.8xlarge` and `r8a.8xlarge` (32 vCPU, 256 GiB). Graviton4 runs the
recipe's image. The two x86 chips run the `pathology` env's linux-64 twin (openslide 4.0.1,
tifffile 2026.9.20, numpy 2.5.3 on all three):

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.8xlarge` | 1,473 s | 1,544 s | 1.42 GiB | 1.8851 | **0.771** | **0.809** |
| Xeon 6975P-C | `r8i.8xlarge` | 2,211 s | 2,262 s | 1.42 GiB | 2.2227 | 1.365 | 1.397 |
| EPYC 9R45 | `r8a.8xlarge` | **1,339 s** | **1,379 s** | 1.42 GiB | 2.5562 | 0.951 | 0.979 |

A result is the whole cohort: 160 slides and every check below, about half a cent per slide on
Graviton4. "Billed" is spawn's completion window, and $/hr is spawn's `cost_estimate` over that
window. Peak RSS is the largest single worker (`ru_maxrss`). The box's cgroup peak was 104–106 GiB,
almost all of it slides held in the `/tmp` tmpfs.

- **The box is sized by the slides, not by the workers.** 32 workers use 1.4 GiB each. Slides in
  flight take up to 100 GB of tmpfs, which is half the RAM, so the memory family `r8g` is the right
  fit, not `c8g`. A `c8g.8xlarge` would hold 32 GB of slides, eight of the largest at once.
- **Fetching is not the bottleneck.** A single HTTPS stream from RODA ran at 40–45 MB/s. 32 at once
  pulled 325 GB in about 25 minutes, and fetching was 17% of worker time (7,376 of 43,725 s on
  Graviton4).
- **openslide's `read_region` is the cost:** 35% of analysis time on Graviton4 (12,898 s). The
  RGBA conversion and stitching make it 3.3× slower than tifffile reading the same tiles (3,941 s).
  AMD reads at about Graviton4's rate (11,699 s), and Intel's SMT threads take 1.9× as long (24,891 s).
- **Intel's 32 vCPU are 16 cores × 2 threads.** Graviton4's and AMD's are 32 full cores. The
  workload is a CPU decode over independent slides, so it pays for cores, and Intel's half-cores
  cost it 1.50× the wall at a dearer hour.
- n = 1 per chip. Every observable is an integer count or a hash, and all are identical across the
  three chips (below), so a timing difference is a speed difference, not a different amount of work.

## What held at scale, on all three chips

[`real.py`](real.py) asserts these per slide inside the task, so a failure fails the run:

| claim | why it must hold | all three chips |
|---|---|---|
| image, mask, XML match the publisher's md5; tissue mask matches its ETag | provenance | 640 / 640 objects |
| WSI and mask level-0 size equal, and every mask level equals the slide's | pixel alignment | 160 / 160 (114 masks stop 1–2 levels short) |
| `level_downsamples` exactly 2**k | pyramid arithmetic | 160 / 160 |
| **openslide == tifffile, byte for byte, on every visited block** | two independent tile readers, one TIFF | **725,548 blocks, 0 px differ** |
| mask labels ⊆ {0, 1, 2} at level 0 | the documented label set | 160 / 160 (no label 1 on exactly the 20 slides the publisher lists as not exhaustively annotated) |
| **mask level k == level 0 [::2**k, ::2**k], k = 1–4** | the mask pyramid is a subsample (probe: max, min and mean pooling all fail) | **0 px differ** |
| **polygons, painted innermost-wins with nonzero fill, == mask==2 beyond 4 px of an outline** | the mask is a rasterisation of the polygons | **35.44 G polygon px, 0 far disagreements** |
| Exclusion interiors not labelled tumour | negative control, 31 slides, 462 M px | 0 px |
| no level-4 tumour pixel outside the visited blocks | the pass saw all the tumour | 0 px |

**The disagreements are all on the outline.** 12.67 M pixels disagree between the polygons and the
mask, 0.036% of the polygon area. 99.91% of them lie within 1 px of an edge. On 159 slides, none is more than 2.94 px from one (`tumor_075`).
`tumor_088` has 564 more pixels between 2 and 4 px. The run does not separate these from the rim
of its omitted island (below). The 4 px bound (≈0.9
µm) is 8× tighter than the recipe's level-4 check, which allows 2 px at level 4, or 32 px at level 0.
Dice ranges from 0.9915 to 0.99996 (median 0.99915). It is reported, not asserted.

**How the publisher's masks were drawn, measured three ways.** These are ASAP's rules, not
openslide's, and getting them wrong failed between 3 and 5 slides:

- *Fill rule.* 58 slides have self-overlapping outlines, 36,703 pixels with winding number ±2. The
  mask labels 36,544 of them (99.6%) tumour, so it fills by **nonzero winding**, not even-odd.
- *Nesting.* "Tumor minus Exclusion", which is what the README describes, fails 3 slides
  (`test_026`, `test_105`, `tumor_110`). They have Tumor polygons drawn inside Exclusion polygons,
  and the mask keeps those islands: 959,213 of 968,801 pixels. Painting in document order instead
  fails 5 other slides, where a later Tumor would cover an earlier Exclusion. Painting **largest
  polygon first**, so the innermost polygon decides a pixel, fits 159 of 160.
- *The one exception.* `tumor_088`'s "Annotation 10" is an 8-vertex Tumor polygon of 6,207 px²
  (about 370 µm²), wholly inside Exclusion "Annotation 9", which is inside Tumor "Annotation 2".
  That is the same nesting as `test_026`'s ten islands, which the mask keeps, but this one is left
  unlabelled. It is listed in `KNOWN_OMITTED`, its 5,047 far pixels are tallied apart, and the run
  fails if the omission is not observed, so the entry cannot go stale.

**Patch manifest** (reported): 10,270,461 tissue patches of 256×256 at level 0. 598,639 contain
tumour, and 539,983 are at least half tumour. Each slide's manifest is pinned by a sha256.

## Across chips

[`compare.py`](compare.py) compares every per-slide observable except timings. The output is in
[`results/compare.txt`](results/compare.txt).

| observable | kind | Intel vs Graviton4 | AMD vs Graviton4 |
|---|---|---|---|
| **decoded RGB, sha256 + sum over every visited block** | integer decode, **asserted** | **160 / 160 identical** | **160 / 160 identical** |
| patch manifest sha256 | integer, asserted | 160 / 160 | 160 / 160 |
| every check count, distance histogram, max distance, Dice | integer / elementwise FP, asserted | 160 / 160 | 160 / 160 |

The JPEG decode is integer arithmetic in libjpeg-turbo. It runs through NEON on Graviton4 and AVX2
on x86, and gives the same 0.76 Tpx on all three. Nothing in this workload is a path, and nothing
goes through BLAS (`openblas_get_corename()`: `neoversev2` on Graviton4, `Cooperlake` on both x86
chips). There was nothing to differ, and nothing did.

## Caveats

- **Not a model.** Nothing here trains or scores a detector, so it says nothing about FROC.
  It covers the data path a detector's training loop runs on.
- **The 4 px bound was set after measuring.** The canary's six slides reached 2.94 px. The bound is
  justified by scale (sub-cellular, 8× the recipe's) and has 1.36× headroom over the 159-slide
  maximum. It is not a published tolerance.
- **Normal slides are not included.** The 159 `normal_*` slides have no annotations to check against.
- [`probe.py`](probe.py) ([`00-probe.task.json`](00-probe.task.json)) measured the layout, read
  rates and mask-pyramid rule this design is built from. It asserts nothing.
