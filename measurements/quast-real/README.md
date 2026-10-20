# QUAST on fourteen whole E. coli genomes against the closed reference: three chips

> **All three chips write the same 54 report files, byte for byte. Every length statistic matches
> an independent recomputation, and the planted errors come back exactly as planted.** QUAST
> takes 10–14 s for fourteen 4.6 Mb genomes. The bill is boot and image pull: 90–144 s billed, about a
> cent per run. AMD is the fastest (9.9 s) and Graviton4 the cheapest to bill ($0.0091).

Workload: one reference-based QUAST run, `-r` U00096.3 with `-g` its NCBI annotation (9,523
features). It scores fourteen whole-genome *E. coli* K-12 MG1655 assemblies:

- **nine published closed genomes** from NCBI GenBank: four PacBio, two ONT, one Illumina+ONT
  hybrid and two Illumina, 2014 to 2026 (`assemblies.tsv`);
- **spades-real's assembly** of the whole DRR171213 run, contigs and scaffolds;
- **three planted-truth controls** built from the reference by `mkcontrols.py`: the reference
  itself; the reference with 50 substitutions and 20 indels of 1–5 bp; and the reference cut into
  8 pieces, two reverse-complemented and two non-adjacent pieces joined (one relocation).

This is the job QUAST is for: a handful of bacterial assemblies against a closed reference. It is
not a slice. The tool is light at this scale, so the run measures what it costs to answer
"how good are these assemblies?" on AWS. QUAST's compute is not the answer to that question.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/quast-real/stage-inputs.sh "$B"     # 00-prep: NCBI -> bucket, md5- and length-checked
for a in arm64 x86_64 amd; do bash measurements/quast-real/launch.sh "$B" $a; done
bash measurements/quast-real/collect.sh "$B"          # results/<chip>/ + results/compare.txt
```

`01-evaluate` runs `run.sh`, pinned by sha256 with every input in `pins.sha256`. That calls:

```sh
quast.py self.fasta planted.fasta chimera.fasta spades_contigs.fasta spades_scaffolds.fasta \
  pacbio_2014.fna.gz ... illumina_2017.fna.gz -r GCF_000005845.2.fna.gz -g ref.gff -t 8 -o q
```

## Results

8 vCPU (`c8*.2xlarge`), `-t 8`, the same pinned bytes. QUAST 5.3.0: aarch.bio's build on
Graviton4, bioconda's on the other two.

| chip | instance | QUAST wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 12.6 s | 103 s | 248 MiB | 0.3190 | 0.00112 | **0.0091** |
| Xeon 6975P-C | `c8i.2xlarge` | 14.3 s | 144 s | 252 MiB | 0.3749 | 0.00149 | 0.0150 |
| EPYC 9R45 | `c8a.2xlarge` | **9.9 s** | **90 s** | 250 MiB | 0.4311 | 0.00119 | 0.0108 |

A result is one QUAST report covering all fourteen assemblies.

- **AMD is 1.27× faster than Graviton4 and Intel 0.88×.** Graviton4 is the cheapest on compute,
  with AMD 6% dearer and Intel 33% dearer. These are 10–14 s runs, so the order is real but the
  money is not.
- **Boot, image pull and staging are 88–90% of every bill.** Billed $/result follows the billed
  window, not QUAST's speed, and Intel's 144 s window makes it the dearest.
- **QUAST keeps about 3 of 8 CPUs busy** (2.84–3.11 on average), so a `c8g.xlarge`
  would fit too.
- **Peak RSS is 248–252 MiB** (`ru_maxrss` of the largest child). The cgroup's `memory.peak`
  reads 0.92 GiB because it counts page cache. `/tmp` holds 90 MiB, so any box fits.
- n = 1 per chip. QUAST is deterministic here: three chips and two minimap2 builds give
  identical bytes. Only the timings could move on a second run.

## What held at scale, on all three chips

59 checks per chip, all passing (`results/<chip>/checks.txt`):

| observable | assertion | observed (all three) |
|---|---|---|
| length statistics, 14 assemblies | the 29 QUAST length rows (counts and totals at each threshold, largest, GC, N's, N50/N90/NG50/NG90, L50–LG90, auN, auNG) == an independent Python recomputation from the same FASTA bytes | 406 of 406 equal |
| `self` | GF 100.000, duplication 1.000, 0 misassemblies, mismatches, indels, unaligned; NGA50 == 4,641,652 | exact |
| `planted` | 50 mismatches, 20 indels (all ≤ 5 bp), indel length 60, 0 misassemblies | exact |
| `chimera` | 7 contigs, 1 misassembly (a contig relocation, 0 inversions or translocations), misassembled length 1,200,000; N50 600,000, NA50 600,000, largest alignment 650,000 | exact |
| annotation | `# genomic features` == the GFF's own line count (9,523, the chromosome-long `region` included) whole in `self` and `planted`; in `chimera`, exactly the 13 features that cross a cut are partial | exact |
| `spades_contigs` | all 54 rows == [spades-real](../spades-real/README.md)'s QUAST report on the same bytes | equal (GF 98.599, NGA50 176,019) |
| published genomes | genome fraction ≥ 95% of U00096.3 (the same strain) | 97.710–100.000 |
| QUAST completion | exit 0 **and** `Thank you for using QUAST!` in `quast.log` | ok |

Observed for the nine published genomes, not asserted (`results/arm64/report.tsv`):

| assembly | GF % | misassemblies | mismatches /100 kbp | indels /100 kbp | NGA50 |
|---|---|---|---|---|---|
| pacbio_2014 | 99.878 | 3 | 0.04 | 0.88 | 4,536,239 |
| pacbio_2016 | 100.000 | 4 | 0.09 | 0.92 | 3,976,500 |
| pacbio_2018 | 99.936 | 3 | 0.06 | 0.28 | 4,547,067 |
| pacbio_2026 | 99.992 | 4 | 0.04 | 0.09 | 3,976,106 |
| hybrid_2022 | 99.590 | 10 | 5.03 | 0.52 | 1,261,419 |
| ont_2023 | 99.941 | 1 | 2.46 | **58.11** | 4,636,433 |
| ont_2024 | 97.710 | 31 | 7.46 | 0.35 | 384,227 |
| illumina_2015 | 100.000 | 0 | 0.02 | 0.15 | 4,641,665 |
| illumina_2017 | 99.764 | 9 | 15.31 | 2.27 | 1,555,637 |

ont_2023's 58 indels per 100 kbp is what an unpolished nanopore assembly looks like. The closed
PacBio genomes' 3–4 misassemblies may be structural differences between lab stocks or where
each circular genome was opened; QUAST reports them, and this run does not settle which.

**Nothing differed across chips.** All 54 `.tsv`/`.txt` files QUAST wrote are byte-identical
on all three (`results/compare.txt`). That covers the alignments, the misassembly and unaligned
reports and the per-genome feature tables. It holds although the images ship **different
minimap2 builds**: 2.31 in aarch.bio's arm64 image and 2.28 (the version QUAST 5.3.0 bundles)
in bioconda's x86 one. QUAST warns about this on arm64. On these inputs the alignments QUAST
keeps do not depend on that version step. Every metric here is a count or a sum over
alignments, a fixed point with no floating-point path to drift.

## Caveats

- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- The ≥ 95% genome-fraction floor is loose on purpose. These are other labs' stocks of the
  strain, not the reference isolate, and ont_2024 is 44 kb shorter than U00096.3.
- The chimera's cut points were not chosen to avoid repeats. The counts were exact anyway. A
  cut inside an IS element or rRNA operon could move an alignment edge, so treat the control
  as specific to these positions.

## Files

`stage-inputs.sh` (uploads scripts, runs `00-prep`), `prep.sh`, `assemblies.tsv`, `run.sh`,
`mkcontrols.py`, `check.py`, `mkspec.py` (writes both specs), `mkpins.sh`, `launch.sh`,
`collect.sh`, `pins.sha256`, `spades-real.report.tsv` (spades-real's report, the reproduction
target). Per-chip evidence is in `results/<chip>/`. The NCBI md5 files and staging checks are
in `results/prep/`.
