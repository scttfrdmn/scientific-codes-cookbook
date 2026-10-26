# MarkDuplicates across four Graviton generations: the FP hypothesis does not hold

> **A JVM tool doing integer and IO work gains 1.93× from Graviton2 to Graviton5 — more than SIESTA's
> 1.86×.** Every generation step pays for itself here, and the answer is byte-identical on all four.

Picard MarkDuplicates 3.5.0 on bwa's whole-genome sorted BAM (48,817,006 records, 4.5 GB), `-Xmx24g`,
8 vCPU and 64 GiB on every rung, same image digest, same input object.

| generation | instance | MarkDuplicates | billed | $/hr | **$/run** | compute-only $ |
|---|---|---|---|---|---|---|
| Graviton2 | `r6g.2xlarge` | 928 s | 1048 s | 0.4032 | **0.1174** | 0.1039 |
| Graviton3 | `r7g.2xlarge` | 747 s | 907 s | 0.4284 | **0.1079** | 0.0889 |
| Graviton4 | `r8g.2xlarge` | 591 s | 702 s | 0.4713 | **0.0919** | 0.0774 |
| **Graviton5** | `r9g.2xlarge` | **482 s** | 570 s | 0.5137 | **0.0813** | **0.0688** |

*Billed* is spawn's task window, wrapper start to stage-out done, in both tables (completion
records, [`results/billed-windows.txt`](results/billed-windows.txt)).

Per-step speedup is remarkably flat: **1.24×, 1.26×, 1.23×**. Cost falls monotonically on both the
billed window and the compute-only basis, so the conclusion does not depend on how overhead is attributed.

## What this corrects

The catalog's generation evidence was, until this run, five FP-heavy codes: GROMACS 2.43×, GPAW 2.33×,
LAMMPS 2.24×, SIESTA 1.86×, plus RAxML-NG. The tidy reading — newer Graviton adds vector throughput, so
FP-heavy codes gain most and integer/IO codes gain least — predicts MarkDuplicates near the bottom.

It lands at **1.93×, above SIESTA.** So the discriminator is not FP-versus-integer. SIESTA's small
localised-basis problem is dense linear algebra on modest matrices, which gains least of anything
measured here; MarkDuplicates is a sort-and-hash over tens of millions of records, which gains steadily.
**Working-set behaviour separates these codes better than instruction mix does** — but that is a
hypothesis this measurement motivates, not one it establishes; attributing it needs a bandwidth and
cache measurement, not another wall clock.

Also worth separating from the DFT result: GPAW and SIESTA both show Graviton3→Graviton4 as
cost-neutral ([dft-crosscheck](../dft-crosscheck/README.md)). MarkDuplicates does not — every rung is
cheaper than the last. So "Gv3→Gv4 is not worth paying for" is a statement about those codes, not
about the step.

## The identity across chips

`READ_PAIR_DUPLICATES` 206,518 and `PERCENT_DUPLICATION` 0.008684 on all four generations, and the
tmpfs high-water mark within 2 MB (8,832–8,834 MB). MarkDuplicates is deterministic, so this is an
exact assertion rather than a tolerance — the same move [GPAW](../../recipes/gpaw/README.md) makes
across rank counts. It also makes each generation run a check on the other three for free, and it
shows the memory footprint is set by the data, not the box.

## Same generation, three chips

The Graviton4 rung against same-generation Intel and AMD: the same command (`chip.sh`, with
`chip-r8g.task.json` resolved by `spec_arch.py`), the same `-Xmx24g`, 8 vCPU and 64 GiB. The x86
legs run bioconda's x86 build of the same Picard 3.5.0 on their own chip's bwa-samtools BAM. All
three BAMs are 4,568,357,482 bytes. Intel's has arm64's S3 ETag; AMD's was uploaded in different
parts, so its ETag cannot show byte identity.

| chip | instance | MarkDuplicates | billed | $/hr | **$/run** | compute-only $ |
|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 591 s | 702 s | 0.4713 | **0.0919** | **0.0774** |
| Xeon 6975P-C | `r8i.2xlarge` | 533 s | 622 s | 0.5557 | 0.0960 | 0.0823 |
| **EPYC 9R45** | `r8a.2xlarge` | **459 s** | 530 s | 0.6390 | 0.0941 | 0.0815 |

- **AMD is 1.29× faster than Graviton4 and Intel 1.11×.** The recipe's own verifying runs of the
  same command, a week apart, gave 536 s (Intel) and 457 s (AMD), so these are repeatable to <1%.
- **Graviton4 is cheapest on both bases.** Billed per run it is 2% under AMD and 4% under Intel; on
  MarkDuplicates' own time, 5% under AMD. The gap between the two bases is Docker install, staging
  and image pull: 111 s on Graviton4, 89 s on Intel and 71 s on AMD (boot comes before the window).
- **The 8a vCPUs are 8 whole cores; the 8i vCPUs are 4 cores with SMT** (`/proc/cpuinfo`: `siblings`
  8, `cpu cores` 8 on `r8a` and 4 on `r8i`). So the Intel row is fewer physical cores, not a slower
  core.
- **The answer is a fixed point on all three chips.** `dup_metrics.txt` is identical line for line
  on Graviton4, Intel and AMD, apart from the `# Started on:` timestamp. All five in-task assertions
  pass on each chip: partition total 48,817,006, examined == flagstat, supplementary == flagstat,
  the `PERCENT_DUPLICATION` recomputation, and the BAM magic. The tmpfs high-water mark was 8,834 MB
  on Intel and 8,815 MB on AMD. AMD's recipe run read 8,831 MB, so the 19 MB is the 2-second
  sampler, not the chip.
- MarkDuplicates is a JVM and links no OpenBLAS, so there is no BLAS kernel to record. n = 1 per
  chip, made defensible by the identical result and the repeat above. Spend for the two new legs:
  $0.19 billed ($0.21 EC2 launch to terminate).

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make run RECIPE=picard                      # the Graviton4 rung, as shipped
# the other three: same spec with resources.families swapped
spawn task run --spec measurements/picard-real/gen-r6g.task.json --wait
spawn task run --spec measurements/picard-real/gen-r7g.task.json --wait
spawn task run --spec measurements/picard-real/gen-r9g.task.json --wait
# Intel and AMD at the Graviton4 rung: the task body is staged, its sha256 in the spec
bash measurements/picard-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for a in x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py picard $a "$COOKBOOK_BUCKET" "$(date +%s)" \
    measurements/picard-real/chip-r8g.task.json)"
done
```

`make run` substitutes `${COOKBOOK_BUCKET}`; `spawn task run` does **not** — pass a spec with the
bucket already resolved, or stage-in fails with `Invalid bucket name "${COOKBOOK_BUCKET}"`.

## Caveats

n = 1 per generation. That is defensible only because the *result* is identical across all four and
the per-step speedups are within 0.03× of each other — a run that had landed off the trend would need
repeating before it could be reported.

One instance size (2xlarge, 8 vCPU) and one input. MarkDuplicates' thread usage is modest and not
configurable the way an aligner's is, so there is no core knee to find here; the generation axis is
the whole question. A deeper BAM would raise both the duplicate rate and the tmpfs peak, and could
move the ratios.
