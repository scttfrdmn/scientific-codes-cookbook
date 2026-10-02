---
tool: fastqc-multiqc
tool_version: "FastQC 0.12.1 / MultiQC 1.35"
images:
  - quay.io/aarchbio/fastqc@sha256:a4a8edb4754d731a0fd513cb4594a14766d662d5156c4e93299bdd57ef4b2fc3
  - quay.io/aarchbio/multiqc@sha256:69df62f478fd50e624ab0c9af81fc23f36afd8c049fd7b3221f8d444cb0c9ffc
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# QC and aggregation — a cohort report checked against metrics we chose

FastQC measures four samples on Graviton4 and MultiQC rolls them into one cohort report, both checked against read counts, lengths, GC and duplication that were fixed before the FASTQs existed. For anyone QC-ing more than one sample.

> **What this covers.** Four synthetic FASTQs (1000/1000/800/1000 reads), exact GC by construction, one with planted duplicates. FastQC's per-sample metrics and MultiQC's aggregation of them. Not adapter/overrepresentation modules, trimming, or real quality-score distributions.

## Run it

```bash
for s in $(make -s spec RECIPE=qc-aggregate); do spawn task run --spec "$s" --wait; done
fastqc -t 2 --extract -o . sampleA.fastq sampleB.fastq sampleC.fastq sampleD.fastq
multiqc -f -o mqc zips/          # reads the *_fastqc.zip files directly
```

Two tasks: FastQC builds the fixture and measures it, MultiQC aggregates the four reports and the table is checked against FastQC's own numbers.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| four synthetic FASTQs | your reads | FastQC takes any number of files; nothing here changes. |
| FastQC in one task | one task per sample | MultiQC is the **aggregator that pairs with a [fan-out](../../patterns/job-arrays.md)** — N samples QC'd in parallel, one report at the end. It doesn't care whether they ran on one box or N. |
| `multiqc zips/` | `multiqc .` over a whole analysis dir | MultiQC scans for *any* tool's output it recognises — bwa, samtools, salmon, picard — so pointing it at a project root is the usual real use. |
| implicit sample count | an explicit expected N | **assert the row count yourself.** MultiQC cannot know a sample is missing; see below. |

**Leave the fixture:** metrics fixed by construction make every FastQC number exactly assertable rather than merely plausible. **Scale it** to real reads whenever — the aggregation checks are what carry over.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m and 12m, caps $0.05 each. FastQC is a JVM tool and MultiQC a Python one; both finish in seconds here and the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact metrics by construction, the silently-short cohort report, the 1000-vs-1000.0 trap, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture GC | counted from the FASTQ == planted, independent of the generator | **50 / 25 / 50 / 50%** |
| total sequences | exact per sample | **1000 / 1000 / 800 / 1000** |
| read length | exact | **100 / 100 / 50 / 100** |
| %GC | exact | **exact** |
| deduplicated % | exact — `100 × (N − dup + 1)/N` | **50.1** on sampleD |
| rows in the cohort report | == the number of FastQC reports | **4 of 4** |
| every aggregated value | round-trips **numerically exact** | **yes** |
| cohort read total | FastQC sum == MultiQC sum | **3800 == 3800** |
| a withheld sample | MultiQC still exits **0** with one row fewer | **exit 0, 3 rows, 0 warnings** |

### Metrics fixed by construction, not observed then blessed

Each read is built with a **fixed count** of G/C bases — 50 of 100 for 50% GC, 25 of 100 for 25% — so the sample's GC is an exact integer before FastQC runs, and the fixture verifies it by counting bases independently of the generator that wrote them. Read counts and lengths are exact for the same reason.

Duplication is the one that looks like it needs a band and doesn't. sampleD is **500 copies of one read plus 500 unique reads**, so the distinct count is `500 + 1 = 501` and FastQC's Total Deduplicated Percentage must be `100 × 501/1000 = 50.1`. It reports exactly `50.1`. A planted duplication level is arithmetic, not an estimate.

### An aggregator reports what it finds — so the row count is your job

This is the finding worth the recipe. Withhold one sample's report and run MultiQC again:

```text
multiqc exit code with a sample missing: 0
rows in the cohort report:               3
log lines naming the absent sample:      0
```

**Exit 0, a shorter table, no warning.** MultiQC has no idea how many samples you meant to have — it scans a directory and reports what parsed. In the shape this tool exists for, that is exactly the dangerous case: fan out N samples, one task fails or its output never lands, and the cohort report comes back looking complete because nothing in it says otherwise. Every downstream number — a mean, a "worst sample", a flag count — is then computed over a cohort quietly missing a member.

So the recipe asserts the row count against the expected N, and deliberately *demonstrates* the silent case rather than describing it. The assertion has to live outside MultiQC because MultiQC is structurally unable to make it. That is the aggregator half of the [exit codes prove nothing](../../practices/container-path.md) rule: here the command genuinely succeeded, and the output is still wrong.

### `1000` and `1000.0` are the same number and not the same string

MultiQC re-serialises what it parses: FastQC writes `1000`, `100`, `50`; MultiQC's table carries `1000.0`, `100.0`, `50.0`. Comparing the two as text disagrees on **every row**:

| comparison of identical values | rows matching |
|---|---|
| numeric | **4 of 4** |
| string | **0 of 4** |

So the round-trip assertion is numeric, and the string result is recorded beside it as the contrast. Anyone writing "check MultiQC's table matches the tool's own output" with `diff` gets a total mismatch from a perfectly faithful aggregation.

One trap inside the trap, caught by the local run before this page claimed anything: **awk compares two numeric-looking strings numerically**, so a naive `$2 == $7` reported `4 of 4` and the observation had to be forced with `($2 "") == ($7 "")` to measure what it says it measures. A check written to demonstrate a notation difference was itself defeated by an implicit conversion.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| FastQC | `quay.io/aarchbio/fastqc@sha256:a4a8edb4…` (0.12.1) |
| MultiQC | `quay.io/aarchbio/multiqc@sha256:69df62f4…` (1.35) |
| input | none — four FASTQs with exact GC, lengths and duplication are generated in-task by awk from `srand(53)` |

MultiQC parses `*_fastqc.zip` directly, so the four zips travel between tasks as flat files and no FastQC output directory has to be staged — which matters, because a [directory output cannot be staged on the container path](../../practices/container-path.md). MultiQC's own `multiqc_data/` is a directory for the same reason, so the two tables the checks need are copied to flat paths before stage-out.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in both tasks.

### Run + verify

```sh
make run RECIPE=qc-aggregate
make ls  RECIPE=qc-aggregate
```

Assertions are `test` calls and awk exits inside both tasks. Expect `smoke-check.txt` with `rows_in_report 4 of 4`, `values_round_trip yes`, `cohort_reads 3800 == 3800`, `missing_sample_rows 3`, and `string_equal_rows 0 of 4`.

</details>
