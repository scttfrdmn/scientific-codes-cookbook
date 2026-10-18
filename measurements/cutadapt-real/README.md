# cutadapt on two whole published runs: three chips

> **Every chip writes the same trimmed reads, record for record, and every identity holds on all
> 113 million read pairs.** AMD EPYC trims 1.6–1.75× faster than Graviton4 and is 18–23% cheaper per
> run; Intel matches Graviton4's speed at 6–22% more. At `-j 16` cutadapt keeps only about 5 cores busy.

Workload: the two **whole** Illumina shotgun runs of the ZymoBIOMICS standards from Nicholls et al.
2019 (GigaScience 8:giz043, [PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)). The
libraries are Kapa HyperPlus with **TruSeq Y-adapters**, the recipe's adapters, so the recipe's
command runs unchanged:

| run | instrument | pairs | bases | R1 reads with adapter |
|---|---|---|---|---|
| `ERR2984773` (D6300 even) | MiSeq 2×151 | 8,785,731 | 2.65 Gbp | 1,407,649 (16.0%) |
| `ERR2935805` (D6310 log) | HiSeq 1500 2×101 | 47,832,553 | 9.66 Gbp | 1,801,335 (3.8%) |

A sample of 10–50M pairs is what one trimming job sees, and the two runs bracket it. The 151 bp run
has many inserts shorter than its reads, so a sixth of its reads carry adapter. The 101 bp run is
the deep one. Both are a published benchmark, and [metaphlan-real](../metaphlan-real/README.md)
profiles the same bytes.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/metaphlan-real/stage-inputs.sh "$B"     # the reads: RODA .sra -> FASTQ.gz, pinned
bash measurements/cutadapt-real/stage-inputs.sh "$B"      # trim.sh, check.py, pins.sha256
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py cutadapt $a "$B" "$(date +%s)" \
    measurements/cutadapt-real/01-trim.task.json)"
done
bash measurements/cutadapt-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

`01-trim` checks every staged object against `pins.sha256` and runs `trim.sh`. That runs the
recipe's command on each sample, then `check.py` walks every read:

```sh
cutadapt -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC -A AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT \
         -e 0.1 -O 3 -m 10 --max-n 0 -j 16 --json=main.json \
         -o main_R1.fq.gz -p main_R2.fq.gz ERR2935805_1.fastq.gz ERR2935805_2.fastq.gz
```

## Results

16 vCPU, 32 GiB (`c8*.4xlarge`; half the RAM is the tmpfs that holds 8.5 GB of staged reads),
`-j 16`, gzip in and out, the same staged bytes. cutadapt 5.2 (aarch.bio's build on Graviton4,
bioconda's on the other two). One task trims both samples:

| chip | instance | cores × threads | even wall | log wall | busy cores | peak RSS | $/hr | compute $/result (even / log) | task billed | task $ |
|---|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 16 × 1 | 21 s | 74 s | 5.0 / 5.3 | 0.28 GiB | 0.6381 | 0.0037 / 0.0131 | 1,126 s | 0.1996 |
| Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 19 s | 77 s | 5.0 / 4.7 | 0.25 GiB | 0.7497 | 0.0040 / 0.0160 | 1,006 s | 0.2095 |
| **EPYC 9R45** | `c8a.4xlarge` | 16 × 1 | **12 s** | **45 s** | 5.4 / 5.7 | 0.38 GiB | 0.8622 | **0.0029 / 0.0108** | 941 s | 0.2254 |

- **AMD trims 1.64× (log) to 1.75× (even) faster than Graviton4**, and at 35% more per hour that makes it
  **18–23% cheaper per run**. On the deep run that is 1.06M pairs/s against 646k. **Intel matches
  Graviton4** (77 s vs 74 s, 19 s vs 21 s) and costs 6–22% more per run.
- **`-j 16` buys about five cores.** Busy cores averaged 4.7–5.7 on every chip and both runs. `-j 1`
  took 105 s against 21 s on Graviton4 (92 vs 19 Intel, 69 vs 12 AMD), so 16 workers give about 5×.
  Decompression and recompression by one parent process are the likely limit; that was not tested. A
  box with half the vCPUs is the obvious next rung. It was not measured, and it has half the tmpfs, so
  it needs one sample per task.
- **Peak RSS is under 0.4 GiB everywhere.** Staging picks the box, not the tool: the 8.5 GB of reads
  need the 16 GiB tmpfs of a 32 GiB instance.
- **The task bill is mostly checks, not trimming.** cutadapt's recipe command ran 57–96 s of a 941–1,126 s
  task. The rest is the extra runs and `check.py`, which reads ~390M records in Python, plus 62–81 s of
  boot, Docker and image pull. **These timings are not compute cost.** A task that only trims would
  bill about boot plus the walls above, so trim a cohort in one task.
- Walls come from whole-second clocks, so the 12–21 s even runs are ±1 s. n = 1 per cell. cutadapt is
  deterministic here (identical output on all three chips, below), so a repeat would change only the timings.

## What held at scale, on all three chips

`check.py` reads the input, an unfiltered run (`raw`, the same command without `-m 10 --max-n 0`) and
the recipe's run (`main`) together. cutadapt keeps input order at any `-j`.

| claim | why it must hold | even | log |
|---|---|---|---|
| pairs, bases == ENA `read_count`, `base_count` | the dump is the archived run | 8,785,731 / 2,653,290,762 | 47,832,553 / 9,662,175,706 |
| every trimmed read is a prefix of its input, sequence **and** quality | `-a`/`-A` remove a 3' suffix only | 0 exceptions | 0 |
| per mate, the histogram of removed lengths from the files == the report's `trimmed_lengths` | the report describes the files beside it | 149 lengths, 0 differ | 99, 0 differ |
| reads with something removed == `read{1,2}_with_adapter`; bases in and out == the files | same | 1,407,649 / 1,378,137 | 1,801,335 / 1,771,027 |
| **no untouched read contains the adapter exactly, or ends in an exact adapter prefix ≥ 3 bp** | a 0-error match within `-O 3` always passes `-e 0.1`; found by an independent string search | **0 of 14.8M** | **0 of 92.1M** |
| main output == the raw pairs with both mates ≥ 10 bp and no `N`, `too_short` first, in order | the filters are applied after trimming, per pair | 8,778,584 kept, 1,110 + 6,037 filtered | 47,734,403, 2,378 + 95,772 |
| `-j 1` writes the same records and counts as `-j 16` | parallelism does not change the result | identical | (not run) |
| `-q 15,20` on each mate == the BWA rule, written from cutadapt's docs | the documented algorithm | 17.6M reads, 3.06M trimmed, 0 differ | (not run) |

Bases balance as `in = out + adapter-trimmed + left in filtered pairs`: 2,653,290,762 = 2,572,518,444 +
78,855,000 + 1,917,318 (even), and 9,662,175,706 = 9,593,898,054 + 48,980,685 + 19,296,967 (log).
The guarantee check finds the matches that need zero errors. One that needs errors or indels within
`-e` cannot be checked this cheaply, and cutadapt's own alignment is what finds those.

Observed, not asserted:
- **Both mates agree on the insert in 91–92% of read-through pairs.** Of pairs trimmed on both mates,
  trimmed R1 == reverse complement of trimmed R2, base for base, in 1,136,596 of 1,242,808 (even) and
  797,317 of 870,472 (log). The lengths agree in 99.8% and 99.3%, so the rest is mostly sequencing
  error in the overlap. Trimming each mate on its own recovers the same molecule from both ends.
- **96.6–97.8% of R1 cuts are an exact adapter prefix** (93–96% for R2, where quality is lower). The rest
  are matches with errors, which is what `-e 0.1` is for.
- **The wrong kit's adapter fails silently, at scale.** Nextera's `CTGTCTCTTATACACATCT` on the even run
  "trims" 188,329 R1 reads and exits 0. All but 241 of those are 3–9 bp chance matches at the 3' end.
  TruSeq removes ≥10 bp from 1,004,362. A trimming report with many reads trimmed, nearly all by a few
  bases, means the adapter is wrong.
- Pairs trimmed on one mate only, 300,170 (even) and 1,831,418 (log), are mostly the same 3–5 bp
  chance matches, which `-O 3` accepts by design.

## Across chips: what is a fixed point and what is a path

**Everything is identical.** The SHA-256 of every output record stream (main, `-j 1`, both `-q` runs),
both removed-length histograms and every report count match on all three chips. So do all six JSON
reports once version strings are set aside (`results/compare.txt`). Trimming is integer alignment
scoring and string slicing, so there is no floating-point path. Each chip ran a different build
(aarch.bio's arm64, bioconda's x86), and the result does not depend on it. cutadapt does not link a
BLAS, so the [OpenBLAS kernel question](../../patterns/cross-architecture.md) does not arise.

## Caveats

- Two samples from one study and one library kit. Per-pair cost should transfer; the share of reads
  with adapter depends on the insert size, and the 16% here is not a property of cutadapt.
- Outputs that are only checked (`raw`, `-q`) were written uncompressed to the container disk, which is
  why `raw` takes 2.4–4.6× longer than `main` on the deep run: it writes over 20 GB to EBS. Only `main` and `-j 1`
  match a real invocation, and only `main` is quoted as the workload.
- Prices are on-demand us-west-2, as spawn printed them for these instances.
