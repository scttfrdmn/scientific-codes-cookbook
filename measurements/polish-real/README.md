# racon and medaka on a whole bacterial genome: three chips

> **medaka takes a Flye draft of *E. coli* CFT073 from 27 errors to 10, and its consensus is
> byte-identical on Graviton4, Intel and AMD.** One round of racon makes the same draft worse (411
> errors), on every chip, so the recipe's racon claim fails at scale. AMD EPYC is the fastest and
> cheapest polish; Graviton4 is the slowest medaka and Intel the dearest racon.

Workload: the **whole** R10.4.1 run of *E. coli* CFT073 from Sanderson et al. 2024 (Microbial
Genomics, [PRJEB51164](https://www.ebi.ac.uk/ena/browser/view/PRJEB51164)), `ERR12813217`,
397,033 reads / 2.54 Gbp (486× of the 5.24 Mb genome), basecalled with Dorado 0.5.3
`sup@v4.3.0`. Flye assembles it once on Graviton4. Every chip then polishes that same draft from
the same reads.

The answer key is the paper's **Trycycler reference** for this strain. It was built from the
strain's R9.4.1 and Illumina reads, not from these, and published with the paper's assemblies
(figshare [10.6084/m9.figshare.25472167.v1](https://doi.org/10.6084/m9.figshare.25472167.v1)). It is
one circular chromosome of 5,236,926 bp. A finished bacterial genome is the job a polisher is run on.
A whole GridION run of one isolate is what such a lab gets back, so nothing here is subsampled.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/polish-real/stage-inputs.sh "$B"   # RODA .sra -> bucket (server-side), scripts, pins
for s in 00-prep 00-fetch 01-draft 02-overlap; do    # once, on Graviton4
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py polish arm64 "$B" r1 measurements/polish-real/$s.task.json)"
done
for a in arm64 x86_64 amd; do                        # the workload, per chip
  for s in 03-racon 04-medaka; do
    spawn task run --wait --spec "$(python3 scripts/spec_arch.py polish $a "$B" r1 measurements/polish-real/$s.task.json)"
  done
done
bash measurements/polish-real/collect.sh "$B" r1     # results/<chip>/, results/summary.txt
```

The commands the tasks run, at the recipe's own settings with 8 threads:

```sh
flye --nano-hq ERR12813217.fastq.gz -g 5.2m --asm-coverage 50 -t 8 -o flye   # ONT's wf-bacterial-genomes default
minimap2 -x map-ont -t 8 draft.fa ERR12813217.fastq.gz > ov.paf
racon -t 8 ERR12813217.fastq.gz ov.paf draft.fa > racon.fa
medaka_consensus -i ERR12813217.fastq.gz -d draft.fa -o med -t 8 -m r1041_e82_400bps_sup_v4.3.0_model_pt.tar.gz
```

The model is the one that matches the basecaller. The image bundles only `hac_v6.0.0` and
`sup_v5.2.0`, so `00-fetch` stages `sup_v4.3.0` from medaka's own repository at `v2.2.2`, pinned by
sha256, and medaka is given the file. Nothing is fetched at run time.

## Results

`m8*.2xlarge` (8 vCPU, 32 GiB) on every chip, `-t 8`, the same draft, reads and overlaps:

| chip | instance | racon wall | medaka wall | billed (racon + medaka) | peak RSS (racon / medaka) | $/hr | compute $ (racon / medaka) | billed $ (both) |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 582 s | 317 s | 652 + 419 s | 7.47 / 7.77 GiB | 0.3590 | 0.0580 / 0.0316 | 0.1068 |
| Xeon 6975P-C | `m8i.2xlarge` | 523 s | 253 s | 590 + 399 s | 7.45 / 7.84 GiB | 0.4234 | 0.0615 / 0.0298 | 0.1163 |
| **EPYC 9R45** | `m8a.2xlarge` | **402 s** | **191 s** | **447 + 280 s** | 7.46 / 8.26 GiB | 0.4869 | **0.0544 / 0.0258** | **0.0984** |

Done once, on Graviton4 (`m8g.2xlarge`): Flye 1,343 s (peak RSS 4.75 GiB, $0.134 compute,
$0.141 billed) and minimap2 44 s ($0.010 billed).

| chip | cores × threads | medaka busy CPUs | torch BLAS / CPU capability |
|---|---|---|---|
| Graviton4 | 8 × 1 | 4.11 | OpenBLAS (`neoversev2`) / `DEFAULT` |
| Xeon 6975P-C | 4 × 2 | 5.13 | MKL / `AVX512` |
| EPYC 9R45 | 8 × 1 | 4.08 | MKL / `AVX512` |

- **AMD is the cheapest polish: 1.66× Graviton4's speed on medaka and 1.45× on racon, 8% less
  billed for both.** Intel runs medaka 1.25× faster than Graviton4 for 6% less compute, but racon
  costs it 6% more, so Intel is 9% dearer billed overall.
- **medaka leaves half the box idle.** With `-t 8` it gives PyTorch 2 intra-op threads ("Setting
  pytorch intra/inter-op threads to 2/1" on every chip) and keeps about 4 CPUs busy. A smaller box
  was not measured.
- **Memory is set by the data, not the chip:** about 7.5 GiB for racon (VmHWM) and 7.8–8.3 GiB for
  medaka (`ru_maxrss`, largest child) on all three. The 32 GiB box is for the 2.4 GB of staged reads
  in the tmpfs plus that.
- **The draft is most of the money.** Flye's $0.141 is more than either chip's two polishes together.
  Boot and staging are 45–146 s of each polish task's bill.
- n = 1 per cell. Every output is byte-identical across the three chips, so the timings compare the same computation.

## What held at scale, on every chip

| claim | why it must hold | observed |
|---|---|---|
| reads == ENA's `read_count` / `base_count` | the dump of RODA's `.sra` is the whole run | 397,033 / 2,544,407,444 |
| Flye completed | `INFO: Final assembly:` in its log | 1 circular contig, 5,236,920 bp, 504× |
| medaka completed | `Polished assembly written to` in its log | 1 contig |
| **scorer: 1,000 substitutions + 100 deletions planted in the truth score exactly 1,100** | the metric is exact edit distance, so a planted count is recovered exactly | 1,100 (1,000 / 0 / 100) |
| **scorer: the truth rotated by 1,234,567 bp and reverse-complemented scores 0** | a circular genome has no fixed start or strand | 0 |
| every assembly has a contig for the chromosome | minimap2 `asm5` places it | 4/4 |
| **medaka: fewer errors than the draft** | the polisher's job | **10 vs 27** |
| **racon: fewer errors than the draft** | the recipe's claim | **FAILS: 411 vs 27** |

Scores against the Trycycler truth, by exact Levenshtein distance (edlib, end to end, after
placing the contig's first 2 kb on the truth), identical on all three chips:

| assembly | bp | errors | substitutions | extra bases | missing bases | Q |
|---|---|---|---|---|---|---|
| Flye draft | 5,236,920 | 27 | 5 | 8 | 14 | 52.9 |
| racon, one round | 5,236,686 | 411 | 15 | 78 | 318 | 41.1 |
| **medaka** | **5,236,925** | **10** | **1** | **4** | **5** | **57.2** |
| the paper's own Flye + Medaka, 100× | 5,236,785 | 599 | 446 | 6 | 147 | 39.4 |

- **racon makes a Flye draft worse, and that is the recipe's claim failing, reported, not
  loosened.** On the plasmid fixture racon scored 0 edits, but its output was 11 bp short and the
  infix metric did not penalise that. Here it emits 234 bp less than the draft and adds 384 errors.
  Flye already polishes its own assembly. One racon round with default settings, on reads this
  accurate, undoes some of that. Run medaka on a Flye assembly, not racon.
- **medaka's remaining 10 errors are mostly indels** (4 extra bases, 5 missing, 1 substitution).
  The truth comes from other reads and has its own error rate, so some of the 10 may be the truth's.
- **The paper's own assembly scores 599**, mostly substitutions, against its own reference. The
  paper ran medaka 1.6.0 with "default settings". This was not traced further, and it is observed, not asserted.

## Across chips: fixed point or path

| output | Intel vs Graviton4 | AMD vs Graviton4 |
|---|---|---|
| `racon.fa` | byte-identical | byte-identical |
| `medaka.fa` | **byte-identical** | **byte-identical** |

medaka is a neural network, and its torch runs different CPU kernels on each side: OpenBLAS
(`neoversev2`) on Graviton4, MKL with `AVX512` dispatch on both x86 chips. Its per-base
probabilities were not compared here. The consensus is the most probable base at each position, and
it came out the same base for base on a whole genome. So the accuracy claim is asserted against the truth, and
the byte identity is reported, not asserted. racon's POA is integer arithmetic, so its identity is expected.

## Caveats

- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- spawn records each `04-medaka` task as **failed**: `score.py` exits 1 on the racon claim, after medaka's
  own checks have passed. The failure is the finding, so the spec keeps the assertion.
- The reads were dumped from the AWS Open Data `.sra` (`sra.sha256` in `results/graviton4-once/reads/`),
  not ENA's FASTQ. ENA's FTP served 1.1 GB in 38 minutes and then dropped the connection. The read and base
  counts match ENA's.
- Intel's 8 vCPUs are 4 cores with SMT; AMD's and Graviton4's are 8 cores.

## Files

`stage-inputs.sh` (the RODA copy, scripts and `pins.sha256`), `mkspec.py` (writes the six specs),
`prep.sh` (`.sra` → FASTQ, checked against ENA's counts), `fetch.sh` (truth, paper assembly,
model), `draft.sh`, `overlap.sh`, `racon.sh`, `medaka.sh`, `score.py`, `common.sh`, `run.py`,
`collect.sh`. `pins.sha256` carries every script, every fetched object, the draft and the
overlaps. Per-run evidence is in `results/<chip>/` and `results/graviton4-once/`; the table
above is `results/summary.txt`.
