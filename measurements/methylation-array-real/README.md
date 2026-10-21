# minfi + SeSAMe on a real cohort — 689 arrays, Graviton4

> **The whole GSE42861 cohort goes through minfi and SeSAMe for $0.56 billed, under $0.001 per
> array.** Sex from X/Y intensity, SeSAMe's independent sex call, and the depositors' annotation
> agree on all 689 samples. Each SeSAMe sample's best rank-match among minfi's 689 is itself. minfi
> is serial and needs 52 GiB, so memory picks the box, not cores. Intel and AMD are a recorded gap:
> bioconda's x86 preprocessCore cannot start a thread.

Workload: **GSE42861** (Liu et al. 2013, *Nat Biotechnol* 31:142), the complete deposit: 689
peripheral-blood Illumina 450k arrays, 354 rheumatoid arthritis and 335 controls, 492 F / 197 M.
This is an EWAS-sized cohort, the scale these two packages are used at, and it is processed the way
an EWAS processes it: read every IDAT pair, sex check, functional normalisation across all 689
arrays (the step that needs the whole cohort at once), then a case/control DMP scan. The recipe's
6-sample fixture tests the same claims. This run tests them with 688 rivals for every sample.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/methylation-array-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # GEO -> bucket, on AWS, once
for s in $(python3 scripts/spec_arch.py methylation-array arm64 "$COOKBOOK_BUCKET" "$(date +%s)" \
             measurements/methylation-array-real/01-minfi.task.json measurements/methylation-array-real/02-sesame.task.json); do
  spawn task run --spec "$s" --wait
done
```

Task 1 (minfi image) runs [`minfi-real.R`](minfi-real.R). Task 2 (SeSAMe image) runs
[`sesame-real.R`](sesame-real.R) on the same IDATs and on the raw betas and sex calls task 1 wrote.
[`run.sh`](run.sh) wraps both: it checks all 1,378 staged IDATs against their sha256, records the
host, and samples memory every 5 s. Each script is staged as a pinned file and checked before it runs.

## Results

Graviton4, same staged bytes for both tasks. minfi runs single-threaded. SeSAMe's `openSesame` runs
16 workers (`MulticoreParam`). Its `inferSex` runs serially.

| chip | task | instance | tool wall | billed | peak RSS | $/hr | compute $/cohort | billed $/cohort |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | minfi | `r8g.4xlarge` | 1,216 s | 1,427 s | 51.9 GiB | 0.9426 | 0.318 | 0.374 |
| Graviton4 | SeSAMe | `m8g.4xlarge` | 773 s | 948 s | 30.9 GiB | 0.7181 | 0.154 | 0.189 |
| **Graviton4** | **both** | | **1,989 s** | **2,375 s** | | | **0.473** | **0.563** |
| Intel (`r8i`/`m8i`) | — | — | gap | | | | | |
| AMD (`r8a`/`m8a`) | — | — | gap | | | | | |

Inside minfi's 1,216 s: `read.metharray` 398 s, `preprocessFunnorm` 726 s, `dmpFinder` 27 s.
Inside SeSAMe's 773 s: `openSesame` 288 s, `inferSex` 367 s, the cross-checks 30 s.

- **Memory picks minfi's box.** Peak anonymous memory is 51.9 GiB, about 77 MB per array, and the
  peak is reached inside `preprocessFunnorm`. CPU use averages 1.01 cores: `read.metharray` and
  funnorm are serial. A first run on a 64 GiB `r8g.2xlarge` was killed (exit 137) at 54 GiB as
  funnorm started, while it still held the raw intensities. With those freed the peak is 51.9 GiB,
  and staging adds 8 GiB of tmpfs, which would leave under 2 GiB on the `2xlarge`. So the smallest
  `r8g` that fits is the `4xlarge`, with 15 of its 16 vCPUs idle. For a bigger cohort, scale the
  RAM, not the cores.
- **SeSAMe is the parallel half.** `openSesame` kept about 10 of 16 cores busy and read all 689
  arrays in 288 s. The serial `inferSex` then takes longer than all 689
  `openSesame` calls together. Running `inferSex` under `MulticoreParam` lost a worker's result, so
  it stays serial here.
- **15–20% of the bill is overhead**: about 15 s of boot, 30 s of Docker install, 25–70 s of image
  pull and 30 s of stage-in (6.2 GB of IDATs from S3, in 21–30 s). That is a smaller share than the
  fixture recipe, where it is 74–86%.
- **n = 1 timing per task.** Every asserted value is deterministic. A repeat SeSAMe run reproduced
  the beta matrix (sum 144114611.5627523661 over 282,910,849 values), all 689 sex calls and every
  matching margin to five decimals. minfi's 2.68 GB raw-beta export was byte-identical across two
  runs (same S3 ETag).

## What held at scale, on Graviton4

Every row is asserted inside the task, so a failure fails the task.

| check | kind | observed |
|---|---|---|
| arrays / probes / identity probes | platform, exact | 689 / **485,512** / **65** |
| raw and funnorm betas in [0, 1] | algebra | [0, 1] and [0, 0.99751] |
| **minfi `getSex` == SeSAMe `inferSex`** | cross-code, categorical | **689 / 689** |
| annotation mismatches that SeSAMe confirms | constructed truth | **0 mismatches**: minfi, SeSAMe and the annotation agree on all 689 |
| **each SeSAMe sample's best Spearman match among minfi's 689 is itself** (top 5,000 probes by minfi variance) | cross-code matching | **689 / 689**; 689 correct of 474,721 ordered pairs |
| `dmpFinder` F == pooled two-sample t², over every finite probe | closed form | max diff **3.0e-12** over 484,657 probes |
| `dmpFinder` p == `pf(F, 1, 687)` | closed form | exact (0) |
| Spearman by rank cross-product == `cor(method = "spearman")` | identity (the check's own tool) | 6.7e-16 |

**The sex call is the strongest check at this scale.** The two codes read different signals: minfi
uses total X and Y intensity, SeSAMe uses methylation of X-linked probes. They agree on every
sample, and both agree with every one of GEO's 689 annotations, so no array's X/Y data contradicts
the sex it was deposited with. funnorm's own internal sex prediction also matched `getSex` on all 689.

**The identity matching holds, but its margin shrinks with more rivals.** The fixture's narrowest
margin was 0.116. Here the narrowest of 689 per-sample margins is 0.012:

| probes (top by minfi variance) | matched | min diagonal | per-sample margin, min / median |
|---|---|---|---|
| 50,000 | 689/689 | 0.97087 | 0.01922 / 0.03986 |
| 20,000 | 689/689 | 0.95196 | 0.03187 / 0.06667 |
| **5,000 (asserted)** | **689/689** | 0.87835 | 0.01234 / 0.13113 |
| 1,000 | **688/689** | 0.71223 | −0.00100 / 0.19210 |

301,762 probes are retained by SeSAMe in all 689 samples. A whole-blood cohort is homogeneous, so a
sample's closest rival is much closer than in the fixture's three donors. At 1,000 probes the
matching fails for one sample, by 0.001. At 5,000 to 50,000 it holds for all 689. The fixture's
`margin > 0.05` assertion does not carry to this scale and is not asserted. The result is stable,
not flaky: it is a deterministic function of the IDATs, and the repeat run matched to five decimals.
To use this on your own cohort, take 20,000 probes, which gave the widest minimum margin here.

**Reported, not asserted:**

- 5,603 raw betas are 0/0, where a bead type read zero in both channels.
- 855 probes have a funnorm beta of exactly 0, so their M-value is −∞; they are left out of the DMP scan.
- The 65 SNP probes find no duplicate donors. The closest pair of samples is 2.045 apart, against a
  within-donor median of 0.595 in the fixture, so every array is a distinct person, as the design says.
- The DMP scan finds 303,507 probes at q < 0.05 and 76,431 at Bonferroni 0.05 (top: cg26244575,
  p = 4.2e-65). This is unadjusted for blood cell composition, which differs between RA cases and
  controls. Liu et al. adjust for it. The count shows the scan ran at full scale. It is not a
  biological result.

## Intel and AMD: a recorded gap, cause measured 2026-10-21

**The cause is a thread stack smaller than OpenBLAS's TLS, and no environment variable changes
it.** preprocessCore 1.72.0 creates its quantile-normalisation threads with a fixed stack of
`PTHREAD_STACK_MIN + sysconf(_SC_PAGE_SIZE)` (`src/qnorm.c:530`, the same in every threaded
routine). The `__pthread_get_minstack` route that would add TLS is commented out (`qnorm.c:121`).
The only environment variable the source reads is `R_THREADS`, a thread count. glibc refuses with
`EINVAL` (22) any stack that cannot hold the process's static TLS. [tls-probe.R](tls-probe.R) reads
each mapped library's `PT_TLS` segment from its ELF headers, on both images:

| | `PTHREAD_STACK_MIN` | thread stack | OpenBLAS static TLS | `normalize.quantiles` |
|---|---|---|---|---|
| x86_64, `c8i.large`, biocontainers image | 16,384 | 20,480 | 143,360 (`libopenblasp-r0.3.30`) | `pthread_create() is 22` |
| arm64, `c8g.large`, aarchbio image | 131,072 | ≥ 135,168 | 61,440 (`libopenblasp-r0.3.34`) | ok |

On x86 every variant still fails: `R_THREADS=1`, `R_THREADS=4`, `OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1`, and `GLIBC_TUNABLES=glibc.rtld.nns=1:glibc.rtld.optional_static_tls=0`, which
shrinks glibc's TLS surplus by about 1 KB against a 123 KB shortfall. All other libraries together
carry 328 bytes of TLS. The fix is a build: bioconda's `build.sh` runs `R CMD INSTALL --build .`
with no `--configure-args="--disable-threading"`, which is the workaround in
[bioconda/bioconda-recipes#31420](https://github.com/bioconda/bioconda-recipes/issues/31420).
Probe specs: [tls-probe-x86_64.task.json](tls-probe-x86_64.task.json),
[tls-probe-arm64.task.json](tls-probe-arm64.task.json); output in
[results/x86_64/tls-probe.txt](results/x86_64/tls-probe.txt) and
[results/arm64/tls-probe.txt](results/arm64/tls-probe.txt).

The recipe's `gap_x86_64` still holds. bioconda's only x86 build of
`bioconductor-preprocesscore` 1.72.0 (`r45h01b2380_0`, in `quay.io/biocontainers/bioconductor-sesame:1.28.1--r45hdfd78af_0`,
digest `sha256:6cd24f0a…`, unchanged since 2026-03-03) fails a one-call `normalize.quantiles` on a
c8i (Xeon 6975P-C) with `ERROR; return code from pthread_create() is 22`
([gap-probe.task.json](gap-probe.task.json), [results/x86_64/gap-probe.txt](results/x86_64/gap-probe.txt)).
SeSAMe's normalisation calls preprocessCore, so task 2 cannot run on x86. That rules out the
cross-code checks and the identity matching. The minfi half did pass on x86 in the recipe's 2026-10-13
check. But the recipe's frontmatter allows either an x86 route or a gap, not both, so neither task
has an x86 pin. Rather than substitute another image, Intel and AMD stay a gap until bioconda
publishes a working x86 preprocessCore.

## Pins

| | |
|---|---|
| data | GEO **GSE42861**, `GSE42861_RAW.tar` (2015-11-04), 6,166,384,640 bytes (GEO `filelist.txt`), sha256 `d6e34e09…62bab0ed`; per-IDAT sha256 for all 1,378 in `idat.sha256` (sha256 `67af70d7…`), checked in every task. Durable-id tier. [fetch.sh](fetch.sh) downloads on AWS, not locally |
| annotation | the series-matrix header (GEO regenerates that file, so its bytes are not a pin). The pin is the derived `pheno.tsv`, sha256 `3a4aa6e3…` |
| SeSAMe cache | the recipe's four pinned ExperimentHub blobs plus `probeIDSignature` (EH8553), which `inferSex` reads to infer the platform. [sesame-cache.sh](sesame-cache.sh) builds it on AWS, and task 2 checks all five blob sha256s. Fetching the new resource also refreshed `hub_index.rds`, so that blob's pin differs from the recipe's |
| images | the recipe's `quay.io/aarchbio/bioconductor-minfidata@sha256:afec63a7…` and `quay.io/aarchbio/bioconductor-sesame@sha256:624254ed…` (arm64) |
| all pins | [pins.sha256](pins.sha256) |
| raw reports | [results/arm64/](results/arm64/): each task's `checks.txt`, step `times.txt`, `host.txt`, `footprint.txt`, sex calls, top-1000 DMPs, and the first SeSAMe run's checks (`checks-r4.txt`) |
