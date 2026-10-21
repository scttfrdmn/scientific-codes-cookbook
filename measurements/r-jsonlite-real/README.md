# jsonlite on a whole OpenAlex entity — Graviton4, Intel and AMD

> **All 143,455 OpenAlex institution records (2.28 GB of JSON Lines) parse, survive a lossless round trip,
> and agree with Arrow's independent C++ parser on every checked field, on all three chips.** The
> recipe's finding holds at scale: only `digits = I(17)` *with* `always_decimal = TRUE` round-trips every
> record. `digits = NA` loses 119,904 of them. AMD does the R work 1.44× faster than Graviton4 and is
> the cheapest per result. Intel is the dearest.

Workload: the **complete** `institutions` entity of the OpenAlex snapshot released 2026-10-14: 83 gzipped
JSON Lines parts, 143,455 records, 200 MB compressed and 2,277,561,725 bytes of JSON. These are deeply nested
records (topics, per-year counts, roles, repositories, alternative names in many scripts). That is the
shape `openalexR` and every hand-rolled API client feeds to jsonlite. A whole entity is the unit people
load. The next step up, `works` (675 GB), is no longer an in-memory R session.

Data tier: **RODA** (`s3://openalex`, the AWS Open Data registry). [`stage-inputs.sh`](stage-inputs.sh) copies
the parts S3 to S3. [`hash.task.json`](hash.task.json) then pins every part by sha256 on AWS, as
[`institutions.sha256`](institutions.sha256), and checks it against OpenAlex's own manifest (per-part
`record_count` and `content_length`). OpenAlex replaces its snapshot every quarter, so a later release
fails the pin check instead of silently changing the dataset.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
bash measurements/r-jsonlite-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # copy; then hash.task.json once; re-run
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" measurements/r-jsonlite-real/real.task.json > /tmp/j.json
spawn task run --spec /tmp/j.json --wait                                                   # r8g.2xlarge
for a in x86_64 amd; do                                                                    # r8i / r8a
  spawn task run --spec "$(python3 scripts/spec_arch.py r-jsonlite $a "$COOKBOOK_BUCKET" $a \
    measurements/r-jsonlite-real/real.task.json)" --wait; done
```

[`real.R`](real.R) splits the entity into 72 chunks of 2,000 records. Eight forked workers (`mclapply`) each
`parse_json` their chunk and round-trip it four ways. Arrow's `read_json_arrow` then reads the same bytes.
Last comes the plain user path: `stream_in` the whole entity into one data.frame, `stream_out` it, and
`stream_in` it again.

## Results

8 vCPU / 64 GiB on all three, the recipe's image (arm64) and its locked linux-64 twin (x86), same input
bytes, 8 workers:

| chip | instance | R wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 1,944 s | 2,088 s | 19.0 GiB | 0.4713 | 0.2545 | 0.2734 |
| Intel Xeon 6975P-C | `r8i.2xlarge` | 2,204 s | 2,326 s | 19.1 GiB | 0.5557 | 0.3402 | 0.3590 |
| **AMD EPYC 9R45** | `r8a.2xlarge` | **1,350 s** | **1,484 s** | 19.2 GiB | 0.6390 | **0.2396** | **0.2634** |

| phase (s) | parallel per-record work | `stream_in` (MB/s) | `stream_out` | `stream_in` again | Arrow, 83 parts |
|---|---|---|---|---|---|
| Graviton4 | 1,062 | 281 (8.1) | 261 | 279 | 15.8 |
| Intel | 1,411 | 257 (8.9) | 221 | 259 | 12.2 |
| AMD | **746** | **192 (11.9)** | **170** | **200** | **10.1** |

- **AMD is 1.44× faster than Graviton4 and 4% cheaper per result billed.** It wins every phase, serial and
  parallel, which is enough to beat a 36% higher hourly rate.
- **Intel is quicker single-threaded and slow in parallel.** It beats Graviton4 on `stream_in` (9%),
  `stream_out` and Arrow. It loses the 8-worker phase by 33%, because its 8 vCPUs are 4 cores × 2 SMT
  threads (`lscpu`), where Graviton4 and AMD have 8 full cores. Net: 13% slower and 31% dearer per result.
- **jsonlite's own reader is 8–12 MB/s.** `stream_in` of 2.28 GB takes 3–5 minutes on any chip and is
  single-threaded. The per-record work runs in parallel only because `real.R` forks workers, so more
  cores can only shorten that phase.
- **Peak RSS is 19.0–19.2 GiB in the parent** (`VmHWM`): the nested 28-column data.frame of 143,455 rows.
  The cgroup peak (32–35 GiB) adds the workers and page cache. That is why this is `r8g` and not `c8g`.
- **Boot and staging are small here: 5–9% of the bill** (144 s Graviton4, 122 s Intel, 134 s AMD). The R work is
  most of it, unlike [salmon](../salmon-real/README.md). These timings are still not a benchmark.
- n = 1 per cell. Every asserted output is deterministic. The lossless serialization has the same sha256
  on all three chips and on an earlier Graviton4 run, so a second run adds a timing sample and nothing about
  correctness.

## What held at scale, on all three chips

Asserted inside the task by [`real.R`](real.R), on every record:

| identity | Graviton4 | Intel | AMD |
|---|---|---|---|
| lines per part == OpenAlex manifest `record_count`, 83 of 83 | 143,455 | 143,455 | 143,455 |
| `parse_json` records == `stream_in` rows == Arrow rows == source | 143,455 | 143,455 | 143,455 |
| ids in source order, from all three readers | yes | yes | yes |
| **round trip with `digits = I(17), always_decimal = TRUE`: not `identical()`** | **0** | **0** | **0** |
| control: default round trip changes records | 143,455 | 143,455 | 143,455 |
| **Arrow == jsonlite, bit for bit**: id, name, works, cites, lat, lon, alternative names, per-year works | 0 differ | 0 differ | 0 differ |

Arrow's reader shares no code with jsonlite's yajl parser. Two parsers that agree on every UTF-8 name
(15,489 are non-ASCII) and every decimal coordinate parsed to the same double show that neither is
misparsing. A conservation total backs it up: `works_count` sums to 302,220,203 and `cited_by_count` to
18,155,478,720 on every chip.

## The recipe's findings, counted on real data (recorded, not asserted)

| serialization | records not `identical()` after the round trip |
|---|---|
| default (`digits = 4`) | **143,455** of 143,455 |
| `digits = NA` | 142,550 |
| `digits = NA, always_decimal = TRUE` | **119,904** |
| `digits = I(17)` | 136,342 |
| `digits = I(17), always_decimal = TRUE` | **0** |

The rows separate the two failures on the recipe page:

- **15 digits is not enough for 84% of real records.** `digits = NA` with decimals kept still loses
  119,904 records, each holding a double that needs 16–17 significant digits. "Use `digits = NA`" is
  wrong advice for this data, not just in theory.
- **Whole-valued doubles come back as integers in 95% of records.** `I(17)` alone loses 136,342, because a
  field like `"2yr_mean_citedness": 0.0` is written `0` and read back as `integer`. `always_decimal = TRUE`
  closes that gap and nothing else does.

Two observations, not asserted:

- **jsonlite rarely re-emits the source's exact bytes.** Re-serializing with `digits = NA` and
  `always_decimal` reproduces OpenAlex's line byte for byte in 5,871 records. The 119,904 that need more
  than 15 digits cannot match. Why the other ~17,700 differ was not measured.
- **The data.frame path is not lossless.** After `stream_out` (`I(17)`, `always_decimal`) and `stream_in`,
  2 of 28 columns are not `identical()`. `associated_institutions` differs in 859 rows (first:
  `I4402554131`). Every sub-column of `geo` compares identical, so its difference is in the nested
  data.frame's structure. Simplifying to data frames is where a round trip stops being exact; the
  unsimplified list path above is exact.

## What differed across chips

Nothing asserted, and nothing recorded either. **Every line of `score.tsv` except `arch` and the timings is
identical on all three** ([`results/`](results/)). This includes the four lossy counts, the 5,871
byte-identical records, the 859 data.frame rows and the conservation sums. The whole lossless
serialization has one sha256 everywhere: `66727d60…3906265`. JSON parsing and `%.17g` printing are
correctly rounded fixed points, and no BLAS is on this path, so there was no path difference to report.
The x86 route installs jsonlite 2.0.0 / arrow 25.0.0 / R 4.5.3 from the env's locked twin, not the arm64
image, so this is also agreement across two builds.

## Spend

About **$2.00** in total: the hash task (~$0.01), a first attempt that died at its 90 m TTL with no
progress output (~$0.72), a one-part probe that sized the rest (~$0.09), a first full Graviton4 pass
(~$0.27), and the three verifying runs above ($0.27 + $0.36 + $0.26).
