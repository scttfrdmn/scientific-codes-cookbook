# MarkDuplicates across four Graviton generations: the FP hypothesis does not hold

> **A JVM tool doing integer and IO work gains 1.93× from Graviton2 to Graviton5 — more than SIESTA's
> 1.86×.** Every generation step pays for itself here, and the answer is byte-identical on all four.

Picard MarkDuplicates 3.5.0 on bwa's whole-genome sorted BAM (48,817,006 records, 4.5 GB), `-Xmx24g`,
8 vCPU and 64 GiB on every rung, same image digest, same input object.

| generation | instance | MarkDuplicates | billed | $/hr | **$/run** | compute-only $ |
|---|---|---|---|---|---|---|
| Graviton2 | `r6g.2xlarge` | 928 s | 1150 s | 0.4032 | **0.1288** | 0.1039 |
| Graviton3 | `r7g.2xlarge` | 747 s | 961 s | 0.4284 | **0.1144** | 0.0889 |
| Graviton4 | `r8g.2xlarge` | 591 s | 826 s | 0.4713 | **0.1081** | 0.0774 |
| **Graviton5** | `r9g.2xlarge` | **482 s** | 647 s | 0.5137 | **0.0923** | **0.0688** |

Per-step speedup is remarkably flat: **1.24×, 1.26×, 1.23×**. Cost falls monotonically on both the
billed window and the compute-only basis, so the conclusion does not depend on how boot is attributed.

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

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make run RECIPE=picard                      # the Graviton4 rung, as shipped
# the other three: same spec with resources.families swapped
spawn task run --spec measurements/picard-real/gen-r6g.task.json --wait
spawn task run --spec measurements/picard-real/gen-r7g.task.json --wait
spawn task run --spec measurements/picard-real/gen-r9g.task.json --wait
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
