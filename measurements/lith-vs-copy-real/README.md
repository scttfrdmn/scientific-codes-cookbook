# Copy, tune the copy, or read through lith — four real workloads where staging set the bill

> **Tune the copy.** The AWS CLI's CRT transfer client, one config line, copies the same
> bytes 5–15× faster than spawn's stage-in does today (1.55–2.68 GB/s against 179–285 MB/s), and that
> alone cuts the billed $/result by 29% (tomography), 65% (gfs), 30% (manta), 14% (delly) and **89%
> (gsw)**. **lith wins one of the five: delly**, at $0.50 a genome on a 64 GiB `c8g` against $0.73 tuned.
> On gfs it ties, on a cheaper box. On tomography it ties before request charges and loses after them.
> Manta is 7.6× slower through lith. gsw did not finish under a $1.33 cap, where the tuned copy
> finished the whole job for $0.16. Every assertion held in every arm that finished.

The four workloads are the Phase 2 measurements where staging, not compute, picked the box and most
of the bill: [tomography-real](../tomography-real/README.md),
[gfs-thickness-real](../gfs-thickness-real/README.md),
[structural-variants-real](../structural-variants-real/README.md) (manta and delly, separately) and
[gsw-real](../gsw-real/README.md). Graviton4 only, because the variable is the data path. Each gets
three rows:

- **today**: spawn's stage-in, `aws s3 cp` at the CLI default (10 requests, 8 MiB parts), into the
  half-RAM `/tmp` tmpfs. These are the measurements' own runs, recomputed from their completion
  records on the same definition as below. Not re-run.
- **tuned copy**: the same box, the same `/tmp` paths, copied by the same CLI with
  `preferred_transfer_client = crt`, `target_bandwidth` = the box's peak NIC and 16 MiB parts.
- **lith**: [lith](https://github.com/scttfrdmn/lith) v1.18.1 mounts the inputs read-only, in place in
  the us-west-2 bucket. Each input path is a symlink into the mount, and the run is on the
  **smallest box with the same vCPU count that holds the measured memory**, now that tmpfs need not
  hold the inputs.

Same image digests, same scripts, same in-task checks. Each original script starts by
`sha256sum`-ing its inputs against the measurement's pins. In the lith arm that check reads every byte
through the mount, which is the verify-on-read. It passed in every lith arm, gsw's included.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
P=s3://$COOKBOOK_BUCKET/inputs/lith-vs-copy-real
aws s3 cp measurements/lith-vs-copy-real/arm.sh $P/arm.sh
for o in tomography-real/real.task.json gfs-thickness-real/gfs-cycle.task.json gsw-real/argo.task.json \
         structural-variants-real/01-manta.task.json structural-variants-real/02-delly.task.json; do
  aws s3 cp measurements/$o $P/orig/${o//\//--}
done
python3 measurements/lith-vs-copy-real/mkspec.py arm delly lith c8g.8xlarge b1 /tmp/s.json   # workload mode box nonce
spawn task run --region us-west-2 --wait --spec /tmp/s.json
python3 measurements/lith-vs-copy-real/collect.py "$COOKBOOK_BUCKET" delly-lith-b1
```

The workloads' own inputs must be staged first, by each measurement's `stage-inputs.sh`.
[`mkspec.py`](mkspec.py) turns an original spec into a spawn **host-path** task (no `container`).
spawn stages the original's small inputs (scripts, pins) where the original put them, and runs
[`arm.sh`](arm.sh) on the host, which:

1. checks the instance's region equals the bucket's (us-west-2 for every arm) and times a no-tool
   `aws s3 cp` of one 3.19 GB object at the CLI default and tuned. That baseline is subtracted from
   the billed window;
2. **copy**: `AWS_CONFIG_FILE=<tuned> aws s3 cp [--recursive] <src> <the original's destination>`;
   **lith**: fetches the sha256-pinned binary, runs `lith index build` on the data prefix, then `lith mount --allow-other
   --nic-gbps <baseline> --mem-cache 8GiB` (4 GiB on tomography's 64 GiB box) and `ln -s` each input into place;
3. runs the original spec's container and command, read from the original spec file and sha256-checked
   against it. The command is unchanged, run as spawn runs it:
   `docker run --rm --user <instance uid> -v /tmp:/tmp [-v /mnt/lith:/mnt/lith:ro]`.

The FUSE mount has to live on the host, and a TaskSpec has no host-side step before its container.
That is why the arms are host-path tasks that start their own `docker run` rather than container
tasks. [`canary.sh`](canary.sh) proved that wiring first, along with the transfer settings (below).

## Results

**Billed** is spawn's window (wrapper start to command exit, as `cost_estimate` prices it), minus the
two baseline copies. **$/result** is that at spawn's rate. **Requests** are GET/HEAD and LIST costed
at us-west-2 list prices **as if you own the bucket**. Ours does: `Payer: BucketOwner` on our own
bucket, and every read is from our us-west-2 copy. lith's counts are its own `lith_s3_requests_total`;
the copies' are computed from the objects' sizes and the part size. **Peak memory** is host
`MemTotal − MemAvailable`, sampled each second, which counts the tmpfs. **tmpfs** is `/tmp`'s
high-water mark (about 3 GiB in lith arms is the baseline object, briefly). The **today** rows use the
same accounting, so they differ from the source pages' billed figures (EC2 launch to terminate for gsw
and gfs, spawn's task window for manta and delly): gsw $1.395 (page
$1.43), gfs $0.361 ($0.434), manta $0.361 and delly $0.842 (unchanged). Tomography is $0.130 against
the page's $0.121, because this page counts the in-task pin check and that page did not.

### tomography — the whole BBii scan, 42.4 GB of zips; $/result excludes the astra/compare checks, as tomography-real does

| arm | box | $/hr | staging | read 1,731 TIFFs | TomoPy wall | billed | **$/result** | requests | peak mem / tmpfs | whole task, checks included |
|---|---|---|---|---|---|---|---|---|---|---|
| today | `r8g.4xlarge` | 0.9426 | 149 s (285 MB/s) | 11.4 s | 279 s | 495 s | 0.130 | 5,055 · $0.002 | — / 39.5 GiB | 5,710 s · $1.495 |
| tuned copy | `r8g.4xlarge` | 0.9426 | **27 s (1.55 GB/s)** | 11.5 s | 279 s | 352 s | **0.092** | 2,530 · $0.001 | 74.6 / 39.6 GiB | 5,579 s · $1.461 |
| lith | `m8g.4xlarge` | 0.7181 | 25 s (no copy) | 98.9 s | 365 s | 477 s | 0.095 | **183,830 · $0.074** | 35.1 GiB / none | 6,292 s · $1.255 |

lith fetched **422.5 GB to serve 42.4 GB**, 10.0×. The zips are read by 16 threads seeking member by
member, and the slice comparison pulls `rec.zip` members by index. The check phases that read
`rec.zip` slowed most: compare 268 → 726 s, calibration 38 → 150 s. On the whole task, CPU-bound astra
for 82 minutes, the cheaper hour makes lith cheapest: $1.255 + $0.074 against $1.461. On the
TomoPy result itself, the tuned copy wins.

### gfs — one whole 0.25° GFS cycle, 418 objects, 113.1 GB

| arm | box | $/hr | staging | pin (md5 + sha256, all bytes) | gfs.py wall | billed | **$/result** | requests | peak mem / tmpfs |
|---|---|---|---|---|---|---|---|---|---|
| today | `r8g.8xlarge` | 1.8851 | 477 s (237 MB/s) | 9.1 s | 127.6 s | 689 s | 0.361 | 13,804 · $0.006 | — / 107 GiB |
| tuned copy | `r8g.8xlarge` | 1.8851 | **63 s (1.79 GB/s)** | 9.1 s | 124.6 s | 244 s | 0.128 | 7,060 · $0.003 | 153.9 / 107.0 GiB |
| lith | `m8g.8xlarge` | 1.4362 | 25 s (no copy) | 63.9 s (1.77 GB/s, through lith) | 149.0 s | 294 s | **0.117** | 32,702 · $0.013 | 75.3 GiB / none |

lith read 226.3 GB, 2.0×: once for the pin, once for the decode. Requests included, it is $0.130
against $0.131: a tie, on a box with half the RAM.

### structural variants — HG002 30×, a 63.9 GB BAM + 3.2 GB reference, two callers

| arm | box | $/hr | staging | sha256 of inputs | caller wall | billed | **$/result** | requests | peak mem / tmpfs |
|---|---|---|---|---|---|---|---|---|---|
| manta, today | `r8g.8xlarge` | 1.8851 | 229 s (293 MB/s) | 191 s | 230 s | 690 s | 0.361 | 8,029 · $0.003 | — / 62.5 GiB |
| manta, tuned copy | `r8g.8xlarge` | 1.8851 | **40 s (1.68 GB/s)** | 192 s | 230 s | 482 s | **0.252** | 4,027 · $0.002 | 68.5 / 62.5 GiB |
| manta, lith | `c8g.8xlarge` | 1.2762 | 27 s (no copy) | 197 s | **1,747 s** | 1,992 s | 0.706 | **398,579 · $0.159** | 22.5 GiB / none |
| delly, today | `r8g.8xlarge` | 1.8851 | 234 s | 192 s | 1,140 s | 1,607 s | 0.842 | 8,029 · $0.003 | — / 62.5 GiB |
| delly, tuned copy | `r8g.8xlarge` | 1.8851 | 44 s (1.53 GB/s) | 192 s | 1,144 s | 1,387 s | 0.726 | 4,027 · $0.002 | 67.2 / 62.5 GiB |
| delly, lith | `c8g.8xlarge` | 1.2762 | 26 s (no copy) | 200 s | 1,165 s | 1,408 s | **0.499** | 29,688 · $0.012 | 22.2 GiB / none |

The callers are opposite access shapes. **delly** streams the BAM: lith served it as whole-object
fills (245.2 GB = the hash plus ~2.7 passes, zero prefetched chunks evicted unread), at the speed of
local tmpfs (1,165 vs 1,144 s). **manta** runs 32 region readers against one 64 GB object. lith
fetched 308.2 GB (4.6×), evicted 46,172 prefetched chunks unread and served 371,104 demand misses
uncovered, and manta ran 7.6× slower. Per genome, both callers: today $1.20, tuned $0.98, and
manta-tuned plus delly-lith $0.77 with requests.

### gsw — the whole Argo GDAC archive, 30,230 files, 215.1 GB

| arm | box | $/hr | staging | sha256 of inputs (64 parallel) | argo.py | billed | **$/result** | requests | peak mem / tmpfs |
|---|---|---|---|---|---|---|---|---|---|
| today | `r8g.16xlarge` | 3.7702 | 1,202 s (179 MB/s) | 8 s | 51 s | 1,332 s | 1.395 | 44,412 + 31 LIST · $0.018 | — / 200 GiB |
| tuned copy | `r8g.16xlarge` | 3.7702 | **80 s (2.68 GB/s)** | 9 s | 50 s | 149 s | **0.156** | 33,391 + 31 · $0.014 | 216.7 / 200.5 GiB |
| lith | `c8g.16xlarge` | 2.5523 | 25 s (no copy) | 553 s (389 MB/s, through lith) | **not finished** | ≥ 1,877 s | **≥ 1.33, no result** | ≥ 742,216 · ≥ $0.30 | — |

The lith arm ran twice and was stopped twice by its own `cost_limit`. The first, at $2.01, left no
container log, so `arm.sh` now streams one; the second, at $1.33, left
[`results/gsw-lith-b2/command.log`](results/gsw-lith-b2/command.log). In that log the pin check passes
through lith, and then `argo.py` pulls a steady ~500 MB/s for 20 minutes. That is 592 GB more,
2.75× the whole archive, and it still had not finished. 64 workers each open one 7 MB netCDF-4
file and read about twenty variables from it (P, T and S, each raw, adjusted and both QC flags,
plus eight per-profile fields). The likely cause is that lith's 8 GiB tier, shared by 64 readers,
evicts a file before its next variable read, so the reads go back to S3. That was not diagnosed. A copy of this archive costs $0.16, so no lith
configuration was worth a third attempt.

## What held, per arm

| check (the measurement's own, in-task) | today | tuned copy | lith |
|---|---|---|---|
| tomography: inputs vs `pins.sha256`; every `ok_` assertion (slice map, three centres, 2,160/2,160 FRC, invariance, astra FBP/CGLS) | pass | pass, `ok_` lines and `score.tsv` identical to today | pass, identical (only `gridrec_s_per_slice` 0.068 vs 0.070) |
| gfs: 418 files vs source MD5; `gfs-cycle.sha256` == committed; 155,240 messages vs `.idx`; thickness ≤ 2% above ground; `GFS THICKNESS REAL OK` | pass | pass; `summary.json` identical on all 24 non-timing keys | pass; identical (worker peak RSS 2.66 vs 2.68 GiB) |
| manta: inputs vs pins; END/SVLEN convention on every record; sentinel | pass, records `711477cd…` | pass, `711477cd…` | pass, `711477cd…`; VCF byte-identical to the tuned arm |
| delly: inputs vs pins; MAD 90, 776,849 discordant pairs; BGZF BCF | pass | pass | pass; BCF identical to the tuned arm byte for byte, and to today's except `##fileDate` |
| gsw: 30,230 inputs vs pins; TEOS-10 check values; every involution; `GSW ARGO OK` | pass | pass; `gsw-results.tsv` identical except three memory lines | **pins pass through lith; argo.py did not complete, so its identities were not reached** |

## What the numbers mean

- **Most of today's staging cost is the CLI default, not the copy.** The same `aws s3 cp` with the
  CRT client moved 1.53–2.68 GB/s on every box against 179–293 MB/s, and the no-tool baseline agreed
  on every box (339–449 MB/s default, 818–1,519 MB/s tuned, on one 3.19 GB object). That is
  [spore-host/spawn#1256](https://github.com/spore-host/spawn/issues/1256)'s fix, measured. It needs no
  new box and no new tool.
- **lith pays where the tool streams each object once or twice and the copy forced a bigger box:**
  delly (31% under tuned) and gfs (a tie on half the RAM). It loses where many readers each take a
  slice of one object (manta's regions, tomography's zip members, gsw's netCDF variables), because an
  8 GiB tier on a small box cannot hold what the next reader wants. The byte amplification says which
  shape you are in: 2.0× and 3.65× where lith paid, 4.6×, 10.0× and ≥ 3.7× where it did not.
- **Request charges decide two of the four.** On data you own, lith's 33k–399k GETs per result add
  $0.013–0.159, against $0.001–0.014 for a copy's 16 MiB parts
  ([the design point](../lith-kraken2-roda/README.md)). On tomography that addition alone is what
  turns a tie into a loss.
- **Smallest box:** with lith, peak host memory was 22–35 GiB for SV and tomography and 75 GiB for gfs,
  against 67–217 GiB for the copies. Each of those boxes fit with headroom. A smaller vCPU count
  was not run, because thread count was held fixed to keep the tool's run unchanged.
- n = 1 per cell. Every tool here is deterministic, and every completed arm reproduced today's
  asserted values, so a repeat would only re-measure transfer noise.

## The canary

[`canary.sh`](canary.sh) on a `c8g.8xlarge` ([`results/canary/`](results/canary/)). It checked the
instance and bucket regions (both us-west-2) and then lith: index (0.25 s), mount, a 3.19 GB
sequential read at 952 MB/s, and 365 Argo files hashed through the mount at 506 MB/s, identical to
the copied files. Then a container running as the instance uid read a lith file through a `/tmp`
symlink. It also picked arm A's transfer client. On one 3.19 GB object, 64-way classic gave 709 MB/s,
128-way 723 and CRT 981. On 365 files, 646, 687 and 1,559 MB/s. Its "default" rows failed (exit 255):
an empty `s3 =` section in an AWS CLI config file fails every `aws s3` command. `arm.sh` omits the
section, and its baselines supply the default figure.

## Pins

| | |
|---|---|
| data | each measurement's own staged inputs and pins, unchanged. The gfs cycle and the tomography zips were re-staged by their own `stage-inputs.sh` (they had been deleted after Phase 2), and both matched the committed pins again |
| lith | v1.18.1 `lith_linux_arm64`, sha256 `8b4f6218b1d549fc10b5fb6d4cdd18121121efd3f922cac323fb251529551f63`, fetched from the GitHub release on the box and checked before use |
| scripts | `arm.sh` sha256 `7330406e…` for the first eight arms (manta, delly, gfs, gsw-copy, gsw-lith-b1) and `04e75b2f…` (adds the streamed container log and progress lines) for gsw-lith-b2 and both tomography arms; `canary.sh` `fe2cb449…`. Each spec checks its script's hash, and `arm.sh` checks the original spec's. The specs that ran are in [`specs/`](specs/) |
| images | the original specs' digests, read from those specs at run time |
| AWS CLI | 2.36.47 (AL2023), CRT client |

## Caveats

- **The two arms' boxes differ by family as well as by data path.** That is the comparison asked for:
  lith on the smallest box it allows, the copy on the box the tmpfs forces. Graviton4 throughout,
  `r8g` for the copies and `c8g`/`m8g` for lith.
- In both new arms Docker installs during the copy or the mount. Today's stage-in ran them one after
  the other, which is worth ~25 s of the gap between today and the tuned copy.
- The copy arms' request counts are computed (GETs = Σ ⌈size / part⌉, a HEAD per single-object copy,
  a LIST per 1,000 keys), not counted. lith's are its own counters, plus the index build's LIST pages.
- One lith configuration per workload (`--mem-cache 8GiB`, 4 GiB on tomography). A larger tier on a
  larger box might rescue manta and gsw, but then it is no longer the small box.
- **Spend: $9.06** at spawn's estimates, about $10.5 with boot time and the lith request charges. That
  is $0.35 for the tomography re-fetch, $0.03 for the canary, $5.34 for the nine completed arms (the
  two tomography arms are $2.72 of it, nearly all astra) and **$3.34 for the two gsw lith attempts**.
  The re-staged gfs cycle (113 GB) and tomography zips (42.4 GB) are deleted from the bucket.

Raw per-arm evidence is in [`results/`](results/): `arm/arm.tsv`, the container's log and lith's log
and counters, the workload's own outputs, spawn's completion record and `row.json`.
[`results/controls.txt`](results/controls.txt) holds today's phase timestamps.
[data-movement](../../patterns/data-movement.md) is when to reach for a mount at all.
