---
tool: plink2
tool_version: "2.0.0-a.7.8"
images:
  - quay.io/aarchbio/plink@sha256:9d0a839edcdba396b6a6d374f60c3ea28fdc10954dbcbc5edef5795a1ae4f4f7
  - quay.io/aarchbio/plink2@sha256:8a025c5284f1fce7c14e50309e596d10860431abde8d23b94cd6ec5e061f511d
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# PLINK 2 — exact agreement with PLINK 1.9 on 1000 variants

Runs PLINK 2 and PLINK 1.9 over the identical binary fileset on Graviton4 and checks that their allele counts and missingness match to the integer. For anyone moving a PLINK 1.9 pipeline to PLINK 2 on ARM.

## Run it

```bash
make stage RECIPE=plink2        # PLINK 1.9 generates the fileset and its own summaries
spawn task run --spec "$(make -s spec RECIPE=plink2 | grep crosscheck)" --wait
make ls RECIPE=plink2

plink  --dummy 300 1000 0.02 0 acgt --seed 42 --make-bed --out data
plink  --bfile data --freq counts --out p19     # 1.9: C1/C2, A1 = MINOR allele
plink2 --bfile data --freq counts --out p2      # 2.0: ALT_CTS/OBS_CT, ALT = ALT allele
```

Two tasks: PLINK 1.9 generates and stages the fileset, then PLINK 2 reads **those bytes** and compares.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `--dummy` fileset | your `.bed/.bim/.fam` | PLINK 2 reads PLINK 1 binary filesets directly via `--bfile`; no conversion needed. |
| `--freq counts` | `--freq` | **counts are integers, frequencies are floats.** Comparing counts gives exact equality; comparing frequencies needs a tolerance for nothing. |
| comparing `A1` to `ALT` | `min(count, total−count)` | **this is the part worth copying** — 1.9's `A1` is the *minor* allele, 2.0's `ALT` is the *alt* allele. Compare the minor count and the convention stops mattering. |
| `--assoc` | `--glm` | PLINK 2 **removed** `--assoc`. `--glm` is a regression, not an allelic chi-square — a different test, so old and new association output are not comparable. |
| `--hardy` | — | 1.9 emits `ALL`/`AFF`/`UNAFF` rows per variant when a phenotype exists; 2.0 emits one. Filter to `ALL` or you are comparing different samples. |

**Leave the fixture.** 300 samples × 1000 variants solves in seconds and is large enough that an off-by-one in either tool shows up somewhere. The point is the *agreement*, not the data. **Scale it** to a real cohort once you trust the pair — the comparison logic is size-independent.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 20m each, caps $0.05 each. Both tools finish in under a second; the recorded windows are almost entirely boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: exact integer equality, the two conventions that would have faked a disagreement, and four failed attempts</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fileset bytes | PLINK 2 verifies PLINK 1.9's sha256 before running | `data.bed/bim/fam: OK` |
| variants compared | ~1000 | **1000** |
| **total allele observations** | **identical, exactly** | **0 mismatches** |
| **minor-allele counts** | **identical, exactly** | **0 mismatches** |
| **per-variant missing counts** | **identical, exactly** | **0 mismatches** |
| HWE p-values | agree to printed precision (1e-4) | **5.000e-05** |

**Three of these are exact integer equality with no tolerance anywhere**, which is the strongest
form a cross-code check can take. Allele counts and missing-genotype counts are integers; two
correct implementations reading the same bytes have no licence to differ by one.

**The comparison runs on verified-identical bytes, not on a regenerated fixture.** PLINK 1.9
`--dummy --seed 42` is deterministic, so regenerating in the second image would *probably* produce
the same fileset — but "probably" is not a cross-check. Task 1 stages `data.{bed,bim,fam}` with
their sha256; task 2 verifies them before PLINK 2 runs.

### Two conventions that would each have faked a disagreement

**1. `A1` is not `ALT`.** PLINK 1.9 reports counts for `A1`, defined as the **minor** allele. PLINK 2
reports `ALT_CTS`, the **alt** allele. On any variant where the alt allele is the major one, those
two numbers differ — roughly half of them. A naive `A1 count vs ALT_CTS` comparison would have
disagreed on ~500 of 1000 variants and looked like a serious bug in one tool.

`min(count, total − count)` is the minor-allele count under **either** convention. It needs no
allele matching, no `--ref-allele` flag, and it stays an integer:

```text
PLINK 1.9   C1=268  C2=316            -> minor 268
PLINK 2     ALT_CTS=316  OBS_CT=584   -> min(316, 268) = 268     agree
```

**2. `--hardy` emits different numbers of rows.** PLINK 1.9 stratifies by phenotype — `ALL`, `AFF`,
`UNAFF` — whenever a case/control column exists, and `--dummy` creates one. PLINK 2 emits a single
all-samples row. Measured: **3000 rows for 1000 variants** on the 1.9 side.

The first version keyed on variant ID, so `AFF` then `UNAFF` silently overwrote `ALL`, and the
comparison became *PLINK 1.9's unaffected subset against PLINK 2's whole sample* — **9.212e-01
apart**. Filtering to `TEST == "ALL"` brings it to **5.000e-05**, which is the ~4 significant figures
PLINK prints, not a numerical disagreement.

**The magnitude is what identified it as a metric error rather than a numerics error.** Two
implementations of the same exact test disagree at ~1e-12, never at ~1. A discrepancy of order 1
means the two sides are different quantities. Worth keeping as triage: size the residual before
hunting in the implementation.

### Deliberately not compared: association

PLINK 2 **removed** `--assoc`. Its replacement, `--glm`, is a regression rather than an allelic
chi-square, so comparing 1.9's `--assoc` statistics to 2.0's `--glm` statistics would measure the
method change, not the two implementations. The task records that as a note instead of producing a
number ([cross-checks](../../practices/cross-checks.md)).

### Pins

| | |
|---|---|
| PLINK 1.9 | `quay.io/aarchbio/plink@sha256:9d0a839e…` — binary self-reports `v1.9.0-b.8 (22 Oct 2024)` |
| PLINK 2 | `quay.io/aarchbio/plink2@sha256:8a025c52…` — binary self-reports `v2.0.0-a.7.8 (19 Sep 2026)` |
| fileset | generated by task 1, staged with sha256 `4508a3d0…` (bed) |

Both cosign-verified; signatures cover the **manifest-list** digest, so verify the tag and pin the
arm64 digest. The PLINK 2 image exists because of
[aarchbio#67](https://github.com/playgroundlogic/aarchbio/issues/67) — bioconda published its first
`linux-aarch64` build of plink2 on 2026-10-04, which is what unblocked this recipe at all.

### Two implementation notes for anyone extending this

**A bioconda single-tool image may contain no interpreter.** This one wraps a C binary and ships
no `python3`, so the comparison is written in awk. That is separate from the PATH question —
aarchbio images need `export PATH=/opt/conda/bin:$PATH`, aarchsci env images must not have it.

**Read column indices from the header, never by position.** PLINK 2's `.acount` is
`#CHROM ID REF ALT PROVISIONAL_REF? ALT_CTS OBS_CT` — the `PROVISIONAL_REF?` column is easy to not
anticipate, and fixed positions would compare the wrong field confidently.

The awk comparison was validated both ways before running: a case where PLINK 2's `ALT` is
deliberately the major allele, and a case with one count altered by 1 to confirm the check fails.
A check never observed failing is not yet a check.

### Run + verify

```sh
make stage RECIPE=plink2
spawn task run --spec "$(make -s spec RECIPE=plink2 | grep crosscheck)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/plink2/r1/score.tsv" -
```

The comparison runs inside task 2 and fails it on any integer mismatch, a fileset whose hashes do
not match, or HWE p-values beyond printed precision — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Association testing (above), PLINK 2's native `.pgen` format and its compression advantages, sample
QC beyond missingness, relatedness (`--king-cutoff`), PCA, and real cohort scale. `--glm` on a
phenotype with covariates is the obvious next recipe and is a different claim.

</details>
