# laspy on a whole 3DEP delivery: 128 source LAZ tiles, 4.64 G points, three chips

> **laspy streams, splits and rewrites a whole USGS 3DEP project in 6.5–11 minutes on 16 vCPU, for
> $0.09–0.15 of compute.** AMD is 1.20× faster than Graviton4 and Intel 1.39× slower; Graviton4 is
> cheapest per result, billed and on compute. Every record, header, VLR and EVLR survives the round
> trip, and every output is identical on all three chips, down to the bytes of the 256 LAZ files
> laspy wrote. Two of laspy's defaults lose metadata on these files, measured below.

Workload: **every source tile USGS delivered for `CA_LassenNP_2020`**, 128 LAZ files, 26.2 GB,
4,642,900,288 points, from RODA's requester-pays `s3://usgs-lidar` (us-west-2). These are the files a
researcher downloads from 3DEP: LAS 1.4, point format 6, written by QSI LiDAR Suite, each with its
CRS as a WKT EVLR. Tiles hold 96 k to 78 M points (median 39 M). The job is the one laspy is used for
at this size: open each tile with `laspy.open`, read it with `chunk_iterator`, split it, and write the
parts back as LAZ with the header and the metadata kept. Here each tile becomes **ground (class 2)**
and **everything else**, so all 4.64 G points are decoded and re-encoded into 256 files, 27.7 GB
([`real.py`](real.py), 16 tiles at a time).

The project is the one [pointcloud-real](../pointcloud-real/README.md) ran PDAL over, on purpose. That
run read USGS's Entwine Point Tiles (the same points, reprojected to EPSG:3857 and re-encoded by
Entwine) and wrote LAZ with PDAL. This one reads the delivered tiles the EPT was built from (the EPT's
own `ept-sources/manifest.json` lists these 128 paths), and writes them with laspy. So each run checks
the other on the same points.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/laspy-real/stage-inputs.sh "$B"     # the 128 tiles, S3 to S3, plus scripts and pins
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py laspy $a "$B" r1 measurements/laspy-real/real.task.json)"
done
bash measurements/laspy-real/collect.sh "$B" r1         # results/<chip>/, results/compare.txt
```

The core of each tile job, verbatim from `split_write` in `real.py` (the accounting calls cut; `raw`
is the file's VLR payloads, read from its bytes):

```python
    with laspy.open(src, laz_backend=laspy.LazBackend.Lazrs) as r:
        h = r.header
        ...
            h = copy.deepcopy(h)
            for i, v in enumerate(h.vlrs):
                if (v.user_id, v.record_id) == ("LASF_Spec", 0) and ("LASF_Spec", 0) in raw:
                    desc, data = raw[("LASF_Spec", 0)]
                    h.vlrs[i] = laspy.VLR("LASF_Spec", 0, desc, data)
        wg = laspy.open(paths["ground"], mode="w", header=h, do_compress=True, laz_backend=laspy.LazBackend.Lazrs)
        wo = laspy.open(paths["rest"], mode="w", header=h, do_compress=True, laz_backend=laspy.LazBackend.Lazrs)
        for ch in r.chunk_iterator(CHUNK):
            ...
            m = ch.array["classification"] == GROUND
            pg, po = ch[m], ch[~m]
            ...
            wg.write_points(pg)
            wo.write_points(po)
        for w in (wg, wo):
            if keep_evlrs and h.evlrs:
                w.write_evlrs(h.evlrs)       # the streaming writer does not write them on close
            w.close()
```

## Results

`m8g/m8i/m8a.4xlarge` (16 vCPU, 64 GiB), 16 tile jobs at a time, same versions, same bytes:

| chip | instance | cores × threads | laspy wall | task window | peak RSS (worker / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 16 × 1 | 467 s | 800 s | 418 MiB / 28.8 GiB | 0.7181 | **0.093** | **0.160** |
| Xeon 6975P-C | `m8i.4xlarge` | 8 × 2 | 651 s | 1034 s | 436 MiB / 30.9 GiB | 0.8467 | 0.153 | 0.243 |
| **EPYC 9R45** | `m8a.4xlarge` | 16 × 1 | **389 s** | **660 s** | 437 MiB / 30.9 GiB | 0.9738 | 0.105 | 0.179 |

A *result* is the whole project split, written and checked. *laspy wall* is the 128 tile jobs, launch
of the first to exit of the last: each job decodes its tile, writes two LAZ files, decodes both again,
and decodes the tile a second time at a different chunk size. Compute $/result is that at the hourly
price. *Task window* is spawn's, wrapper start to command exit; billed $/result is spawn's
`cost_estimate` for it, which also holds staging the 26.2 GB into tmpfs, hashing it (3–4 s), the PDAL
pass over the outputs (131–218 s), Docker and the image or env install. *Peak RSS* is the largest
`ru_maxrss` of any one laspy worker; *box* is the container cgroup's `memory.peak`, which is mostly the
page cache of the 27.7 GB written.

- **laspy is compute-bound here.** The workers used 15.85–15.89 of the 16 vCPU for the whole phase on
  every chip: 9.9 M points/s on Graviton4, each point decoded three times and encoded once by lazrs.
- **AMD is fastest, Graviton4 cheapest.** AMD's 1.20× speed does not cover its 1.36× price, so its
  compute cost is 13% above Graviton4's. Intel's 16 vCPU are 8 cores, and it is 1.39× slower than
  Graviton4 at 1.18× the price. The same order as PDAL on the same project.
- **Memory is small; the input sets the box.** Chunked reading holds a worker to ~0.4 GiB however big
  the tile. The 64 GiB `m8g` is for the tmpfs (½ RAM): the staged 24.4 GiB of tiles fill 80% of it.
  The written LAZ goes to the root disk (`disk_gib` 100) and is deleted after the PDAL pass.
- **n = 1 per chip.** laspy is deterministic, and every asserted value and every output byte is the
  same on all three chips. These timings are not compute cost in general
  ([layout](../../patterns/layout-and-effective-cost.md)).

## What held at scale, on all three chips

Checked inside each run by `real.py`:

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| every tile's size and MD5 == RODA's listing (62 single-part ETags, 66 multipart at 5 MiB parts); sha256 == `pins.tsv` | provenance | 128 / 128 | same | same |
| header point count (64-bit) == decoded; header bounds == decoded extents; header points-by-return (15) == decoded | the source header describes its points | 128 / 128 | same | same |
| decoded count and `proj:bbox` == USGS's VPC; count and Z bounds == the EPT's sources manifest | two other catalogues of the same tiles | 128 / 128 | same | same |
| record bytes identical at chunk sizes 2,000,000 and 1,234,567 | chunked reading is exact | 128 / 128 | same | same |
| ground + rest == source, per tile; ground == class-2 count; class histograms partition | the split loses and duplicates nothing | 128 / 128 | same | same |
| **every raw field of PDRF 6, summed over ground + rest == the source's** (mod 2⁶⁴) | every attribute is conserved, not only XYZ | 11 fields × 128 | same | same |
| **written LAZ decodes to the exact records written (sha256 of the 30-byte records)** | the LAZ round trip is lossless | 256 / 256 | same | same |
| written header: count, points-by-return == decoded; bounds == extents exactly | laspy updates the header it writes | 256 / 256 | same | same |
| written header: version 1.4, PDRF 6, scale, offset, global encoding, system id == source | the header is kept | 256 / 256 | same | same |
| **written VLRs and EVLRs byte-identical to the source's** (laszip VLR aside); `parse_crs()` equal | the metadata is kept | 256 / 256 | same | same |
| **PDAL (laz-perf) reads every laspy-written file**: count, min/max XYZ, class and return histograms == laspy | a second decoder on laspy's output | 256 / 256 | same | same |
| PDAL mean Z == laspy's exact integer mean, within accumulation + print error | decode, every point | worst 0.90 of bound | same | same |
| **project: points, class histogram, Σ Z (cm, exact), Z range == the EPT's** | Entwine kept every point, its class and its Z | 4,642,900,288; Σ Z 953,651,753,831,395 cm | same | same |
| **project return histogram mod 8 == the EPT's** | see below | 8 values | same | same |

The mean-Z bound has two clauses, both from the inputs and set before the run: summation error
(n−1)·2⁻⁵³·max|z| (PDAL's running mean damps an error made at point j by j/n, so it does no worse), and
half a unit in the last digit PDAL prints, since its metadata gives the average to six decimals. The
print clause is the larger here: the worst file is `4.86e-07` off, inside `3.76e-08 + 5.0e-07`.

## What is new against pointcloud-real

- **The EPT's "ReturnNumber 0" points are return 8.** pointcloud-real found 57,303 points with return
  number 0, which LAS forbids, on both decoders. The delivered tiles have **none**: they have 57,303
  points at return **8**, 4,536 at 9 and 17 at 10. The EPT's nodes are LAS 1.2 point format 1, whose
  return number is 3 bits, so 8, 9, 10 are stored as 0, 1, 2. The run asserts the whole mod-8 histogram
  equals the EPT's, exactly. Number of returns is truncated the same way; Z is not: the project's exact
  sum of Z in centimetres is the same through Entwine's reprojection.
- **Writing, at scale, with every attribute checked.** pointcloud-real wrote with PDAL and read back
  with laspy. Here laspy writes all 4.64 G points and PDAL reads them back, and the check covers all 11
  fields of the record (intensity, flags, scan angle, GPS time and the rest), not just geometry.
- **VLRs, EVLRs and the CRS**, which no run had compared. All of them survive, but only with the two
  steps below.
- **The written LAZ is byte-identical across chips.** laspy reads a tile's points in file order, so
  lazrs encodes the same sequence everywhere: all 256 files have the same sha256 on Graviton4, Intel and
  AMD. pointcloud-real's PDAL tiles differ run to run, because `readers.ept` emits points in a varying
  order.

## What laspy's defaults lose

Both are run as a control on the smallest tile, so the preservation checks above could fail:

| default call | what is lost | the fix used here |
|---|---|---|
| `laspy.open(out, mode="w", header=h)` + `close()` | **the EVLRs, so the WKT CRS.** The header keeps the WKT bit set, so the file claims a WKT CRS and has none; `parse_crs()` returns `None` | call `writer.write_evlrs(h.evlrs)` before `close()` (`LasData.write()` does this; the streaming writer does not) |
| any read and write of these tiles | **the Classification Lookup VLR is rewritten.** laspy parses it into names and keeps only `[A-Za-z0-9 ]`, so 5 of QSI's 256 entries change, e.g. `LEX_PotenialGND` → `LEXPotenialGND` | put the VLR back as raw bytes (`laspy.VLR("LASF_Spec", 0, desc, data)`) read from the file |

The control asserts the lookup comes out exactly as that rule predicts, and that every other VLR
survives the default call. Both are in laspy 2.7.0 (`laswriter.py`, `vlrs/known.py`).

## Pins

| | |
|---|---|
| point cloud | RODA `s3://usgs-lidar/Projects/CA_LassenNP_LowerBearCreekHUC10_2020_D20/CA_LassenNP_2020/LAZ/`, 128 objects, 26,173,428,355 B, copied S3 to S3 into your bucket by [`stage-inputs.sh`](stage-inputs.sh) (a container cannot sign requester-pays reads). RODA's listing (key, size, ETag, date) is [`src.tsv`](src.tsv); every tile's sha256 and MD5, computed on AWS, is [`pins.tsv`](pins.tsv) |
| catalogues | the EPT's `ept-sources/manifest.json` (sha256 `7e822813…`, as pinned by pointcloud-real) and USGS's `CA_LassenNP_2020.vpc` (`25ef2688…`) |
| scripts | `real.py`, `real.sh`, `src.tsv`, `pins.tsv` and the catalogues in [`pins.sha256`](pins.sha256), checked in-task |
| image | the recipe's `pointcloud@sha256:7817e029…` (arm64: laspy 2.7.0, lazrs-python 0.8.2, PDAL 2.10.2) and its `pointcloud-93bbd77d2902` x86 twin (the same three versions) |

Raw per-chip outputs are in `results/`, and `results/compare.txt` is the cross-chip diff: 20 of 20
values and 128 of 128 tile rows identical, for Intel and for AMD. The staged copy is 26.2 GB of your
bucket (about $0.60 a month); delete `inputs/laspy-real/laz/` when done.

## Spend

Six launches, **$0.77**. The three verifying runs cost $0.58. Three probes on 16 tiles cost $0.19:
the first two failed on the checks, not the tool (the first on the lookup VLR above and a PDAL option
name, the second on a mean-Z bound that left out PDAL's six printed decimals), and the third passed.
