# Kraken2 on two whole published mock-community runs: three chips

> **Every chip classifies every one of 56.6M read pairs identically: the per-read output is the same
> bytes on all three.** AMD EPYC is 1.18× faster than Graviton4 and 8% dearer per run; Intel is 6%
> slower and 18% dearer. About half of every bill is staging and unpacking the index, not kraken2.

Workload: the two **whole** Illumina shotgun runs of the ZymoBIOMICS standards from Nicholls et al.
2019 (GigaScience, [PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)), the same
staged bytes [metaphlan-real](../metaphlan-real/README.md) profiles, classified one after the other
on one box against the recipe's `k2_standard_08gb_20250402` index:

| run | standard | read pairs | bases | what it tests |
|---|---|---|---|---|
| `ERR2984773` | D6300 **even**: 8 bacteria at 12%, 2 yeasts at 2% | 8,785,731 (MiSeq 2×151) | 2.65 Gbp | the closed list |
| `ERR2935805` | D6310 **log**: 89.1% down to 0.000089%, one decade per step | 47,832,553 (HiSeq 2×101) | 9.66 Gbp | depth and dynamic range |

Researchers classify shotgun samples of 10–100M reads, and standard-8 is the index most of them run,
so this is the tool's normal job. A published mixture is the truth the recipe's human WGS run lacks.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
make stage RECIPE=kraken2                                    # the standard-8 index, once
bash measurements/metaphlan-real/stage-inputs.sh "$B"        # the two Zymo runs (RODA -> FASTQ.gz)
bash measurements/kraken2-real/stage-inputs.sh "$B"          # scripts + pins
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py kraken2 $a "$B" "$(date +%s)" \
    measurements/kraken2-real/01-classify.task.json)"
done
bash measurements/kraken2-real/collect.sh "$B"               # results/<chip>/, results/compare.txt
```

`01-classify` checks every staged object against `pins.sha256`, then runs `classify.py`, which runs
the recipe's invocation once per sample, with the per-read output kept:

```sh
kraken2 --db db --paired --threads 8 --report report.txt --output /var/tmp/ERR2935805.kraken \
        ERR2935805_1.fastq.gz ERR2935805_2.fastq.gz
```

## Results

8 vCPU (`r8*.2xlarge`, 64 GiB: the RAM is for the tmpfs holding the index twice, as in the
[recipe](../../recipes/kraken2/README.md)), `--threads 8`, the same staged bytes. Kraken2 2.17.1
(aarch.bio's build on Graviton4, bioconda's on the other two). One task classifies both samples:

| chip | instance | even wall | log wall | tool wall | billed | peak RSS | $/hr | compute $/result (even / log) | billed $ (both) |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 42.1 s | 151.5 s | 193.6 s | 417 s | 7.93 GiB | 0.4713 | 0.0055 / 0.0198 | **0.0546** |
| Xeon 6975P-C | `r8i.2xlarge` | 46.6 s | 162.0 s | 208.6 s | 416 s | 7.97 GiB | 0.5557 | 0.0072 / 0.0250 | 0.0642 |
| EPYC 9R45 | `r8a.2xlarge` | **36.3 s** | **128.8 s** | **165.1 s** | **333 s** | 7.93 GiB | 0.6390 | 0.0064 / 0.0229 | 0.0591 |

| chip | cores × threads | busy cores (even / log) | untar the index | log run: pairs/s | log run: compute $ per Gbp |
|---|---|---|---|---|---|
| Graviton4 | 8 × 1 | 2.25 / 2.10 | 58 s | 316,000 | 0.0021 |
| Xeon 6975P-C | 4 × 2 | 2.08 / 2.00 | 62 s | 295,000 | 0.0026 |
| EPYC 9R45 | 8 × 1 | 2.14 / 2.07 | 49 s | 371,000 | 0.0024 |

- **AMD is 1.18× Graviton4** on the log run and 1.16× on the even one. At 36% more per hour that
  makes it 15% dearer per sample in compute, and 8% dearer billed, because its whole box was faster
  (stage-in, untar, image pull). **Intel is 6–10% slower than Graviton4**, 26–31% dearer per sample in compute and 18% dearer billed.
- **kraken2 kept about 2 of its 8 threads busy on every chip.** CPU time over wall time is
  2.0–2.25 on 8 full cores and on 4 SMT cores alike, so most of the box is idle while it classifies,
  and Intel's halved core count costs it only 6%. The likely limit is kraken2's input path (one
  thread reads and decompresses the gzipped FASTQ for all workers); that was not tested. The box is
  sized by the tmpfs (22.6 of 31.6 GB after unpacking), not by compute.
- **Peak RSS is 7.9 GiB everywhere** (`ru_maxrss` of the classify process): the 8.0 GB `hash.k2d`.
  It does not grow with depth.
- **Overhead is 168–223 s per task, half the bill:** 42–50 s to stage 15 GB, 30–40 s for docker and the image
  pull, 49–62 s to unpack the index, and 9–44 s to verify the pins (aarch.bio's `sha256sum` on
  Graviton4 took 44 s against 9–11 s on the x86 images; that is the hashing tool, not kraken2). It is
  paid once per task however many samples follow, so classify a cohort in one task, as here.
- **Billed** is the completion record's window (spawn's wrapper start to end), at the hourly price
  spawn printed. These timings are not a benchmark of compute cost.
- n = 1 per cell. Kraken2 is deterministic (identical per-read output on all three chips, below), so
  a repeat would change the timings and nothing else.

## What held at scale, on all three chips

| claim | why it must hold | even | log |
|---|---|---|---|
| pairs processed == ENA `read_count` | nothing dropped or doubled | 8,785,731 | 47,832,553 |
| Mbp kraken2 prints == ENA `base_count` | to its printed 2 decimals | 2653.29 | 9662.18 |
| classified + unclassified == processed | kraken2's books | 8,133,504 + 652,227 | 40,267,635 + 7,564,918 |
| report root / unclassified rows == the log's two counts | two summaries, one count | yes | yes |
| every clade == its taxon reads + its children's clades | the report is a tree | 5,309 clades, 0 off | 7,194, 0 off |
| per-read output, tallied by taxid == report's taxon reads | two writers, one classification | 3,438 taxids, 0 off | 4,700, 0 off |
| **unexpected genera above 1% of classified** | the closed list | **0** (largest *Mycobacterium* 0.18%) | **0** (0.025%) |
| **top 8 genera == the 8 Zymo bacteria** | D6300's list | **yes** | — |
| **top 3 genera, in order == 89.1% > 8.9% > 0.89%** | D6310's first three decades | — | ***Listeria* > *Pseudomonas* > *Bacillus*** |

`score.py` carries every check, `zymo.tsv` the two compositions. The truth is scored at **genus**
rank and only for the bacteria: standard-8 holds no fungi, so both yeasts are unclassifiable by
design. The 1% line is the closed-list rule from the [metaphlan recipe](../../recipes/metaphlan/README.md),
5.6× above the largest unexpected genus observed, not a band fitted to it.

Observed, not asserted:
- **Genus shares in the even run are 9.8–15.0% of classified reads for seven of the eight**,
  against 12% by DNA. *Escherichia* is the exception at 0.91%: *E. coli* shares k-mers with
  *Salmonella* and *Shigella*, so most of its reads stop at Enterobacteriaceae.
- **Species rank needs today's taxonomy.** Zymo's *B. subtilis* strain is filed as
  *B. spizizenii* (taxid 96241) in this index, which is where 636,468 of the even run's reads go;
  only 2,258 land on *B. subtilis*.
- **The log run's detection floor is set by background, near 0.02%.** *Salmonella* (0.089% of DNA)
  is at 0.077% and *Limosilactobacillus* (0.0089%) at 0.0074%. Below that, real members sit among
  spurious genera of the same size (*Mycobacterium* 0.025%, *Xanthomonas* 0.016%, both also in the
  recipe's human run), and *Enterococcus* (0.00089%) and *Staphylococcus* (0.000089%) read 15–20×
  their DNA share. Only the first three decades are asserted.
- **Against [MetaPhlAn](../metaphlan-real/README.md) on the same bytes:** both find all 8 bacteria in
  the even run. MetaPhlAn under-calls *Bacillus* about 200× (0.055%); kraken2 reads it at 11.8%. The
  two estimate different quantities (marker coverage, read share), so this is an observation.

## Across chips: what is a fixed point and what is a path

**Every classification is identical.** The per-read output (941 MB even, 3.98 GB log, one line per
pair) has the same sha256 on all three chips, and so does each report. Kraken2's classification is
integer k-mer lookup and a hit-count walk up the taxonomy, with no floating point to diverge, so this
is the expected result, and it is checked rather than assumed. `checks.txt` differs between images
only in the order one Python dict of counts is printed in; the counts are equal.

## Caveats

- Two samples are a small cohort. Per-read cost is what should transfer. Overhead is per task.
- The per-read output goes to the container disk (`/var/tmp`, `disk_gib: 60`), not the tmpfs, which
  already holds the index twice. Writing it is inside the timed wall.
- Prices are on-demand us-west-2, as spawn printed them on these instances.
