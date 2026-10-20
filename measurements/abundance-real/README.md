# Bracken on two whole published mock-community runs: three chips

> **Bracken turns kraken2's 3.0% *Listeria monocytogenes* into 87.0% against a published 89.1%, and
> conserves every read while doing it, byte-identically on all three chips. It also inflates one
> absent genus, *Xanthomonas*, 17× to 1.66% of the even mock, failing the closed-list check.**
> Bracken itself runs for under a second. Every cent of the bill is the box.

Workload: Bracken at species and genus rank on the two **whole-run** kraken2 reports that
[kraken2-real](../kraken2-real/README.md) produced from the ZymoBIOMICS standards of Nicholls et al.
2019 ([PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)). The report bytes were
identical on all three chips there. Each chip reads the same pinned copy here:

| run | standard | read pairs | read length | `-r` | report |
|---|---|---|---|---|---|
| `ERR2984773` | D6300 **even**: 8 bacteria at 12%, 2 yeasts at 2% | 8,785,731 | 2×151 | 150 | sha256 `e3217986…` |
| `ERR2935805` | D6310 **log**: 89.1% down to 0.000089% | 47,832,553 | 2×101 | 100 | sha256 `f9073b2b…` |

This is Bracken's real job. Its input is a kraken2 report of a whole sample, a few hundred KB however
deep the run, plus the `kmer_distrib` that ships with the index, here the recipe's
`k2_standard_08gb_20250402`. A bigger input does not exist. A cohort is just more reports.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/kraken2-real/stage-inputs.sh "$B"     # and run kraken2-real once: the reports
spawn task run --wait --spec "$(python3 scripts/spec_arch.py abundance arm64 "$B" "$(date +%s)" \
  measurements/abundance-real/00-extract.task.json)"      # once: kmer_distrib out of the index tarball
bash measurements/abundance-real/stage-inputs.sh "$B"
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py abundance $a "$B" "$(date +%s)" \
    measurements/abundance-real/01-bracken.task.json)"
done
bash measurements/abundance-real/collect.sh "$B"         # results/<chip>/, results/compare.txt
```

`01-bracken` checks every staged object against `pins.sha256`. Then `run.py` runs the recipe's
invocation four times, once per sample at each rank, and `score.py` checks each result:

```sh
bracken -d /tmp -i ERR2935805.report.txt -o S.bracken -w S.breport -r 100 -l S -t 10
```

`00-extract` runs once, on Graviton4 (`r8g.large`, 59 s to gunzip the 5.96 GB tarball). It writes
all seven `database<L>mers.kmer_distrib` (4.4–5.7 MB each) to the bucket and never writes the 8 GB
hash. Bioconda's x86 Bracken image has no `tar`, and the files are data, so one extraction serves
all three chips.

## Results

`c8*.large` (2 vCPU, 4 GiB), one task per chip runs all four Bracken calls. Bracken 3.1p1
(`est_abundance.py` sha256 `575c327f…` in both images). aarch.bio's build on Graviton4, bioconda's on
the other two:

| chip | instance | cores × threads | Bracken wall (even / log) | billed | peak RSS | $/hr | compute $/result (even / log) | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.large` | 2 × 1 | 0.35 / 0.38 s | 69 s | 31.8 MiB | 0.0798 | 7.8e-6 / 8.4e-6 | 0.00076 |
| Xeon 6975P-C | `c8i.large` | 1 × 2 | 0.32 / 0.39 s | 77 s | 32.2 MiB | 0.0937 | 8.3e-6 / 1.0e-5 | 0.00100 |
| EPYC 9R45 | `c8a.large` | 2 × 1 | **0.24 / 0.25 s** | **48 s** | 32.3 MiB | 0.1078 | 7.2e-6 / 7.5e-6 | **0.00072** |

- **Bracken costs nothing measurable.** Four calls take 0.5–0.7 s and 32 MiB, against 48–77 s
  billed. The rest is the box: 4–6 s stage-in, 26–41 s Docker install, 13–32 s image pull. The
  bill is about 3% of kraken2's on the same samples ($0.055–0.064 for both,
  [kraken2-real](../kraken2-real/README.md)). So run it where the reports already are. Bracken
  needs only the 5.5 MB `kmer_distrib` for your read length, not the index.
- **AMD is fastest, and cheapest billed per sample** ($0.00072, against $0.00076 for Graviton4 and
  $0.00100 for Intel), because its whole box finished first. At sub-second compute these are
  boot-time differences, not Bracken's. **These timings are not compute cost.**
- **Wall and RSS do not grow with depth.** The 47.8M-pair log run takes the same time as the 8.8M
  even run. Bracken reads a report and a 5.5 MB distribution, and neither grows with the reads.
- **Billed** is the completion record's window, from spawn's wrapper start to its end, at the hourly
  price spawn printed. Result = one sample at both ranks. n = 1 per cell. Bracken is
  deterministic, and its outputs match byte for byte across chips (below).

## What held at scale, on all three chips

All exact. Bracken's printed summary, its table and its `-w` report are each checked against
`score.py`'s own parse of the kraken2 report:

| claim | why it must hold | even S / G | log S / G |
|---|---|---|---|
| total == ENA `read_count`; unclassified == kraken2's | same input | 8,785,731; 652,227 | 47,832,553; 7,564,918 |
| kept + discarded + distributed + not distributed + unclassified == total | Bracken's books partition the report | yes / yes | yes / yes |
| kept, discarded == clade reads at the rank, split at `-t 10` | a second reading of the report | 2,683,734 / 6,742,829 | 2,323,122 / 39,042,215 |
| distributed + not distributed == taxon reads above the rank | the reads Bracken may move | 5,445,614 / 1,389,121 | 37,937,459 / 1,222,898 |
| table rows == taxa at the rank ≥ threshold; `kraken_assigned_reads` == their clade | the input passes through | 226 / 149 rows, 0 off | 231 / 205, 0 off |
| `added_reads` == new − assigned, and ≥ 0 on every row | redistribution only adds | 0 off | 0 off |
| **kept + distributed − sum(new) ∈ [0, rows)** | **conservation:** each row is `int()` of a sum of shares | **109 / 59** | **111 / 82** |
| `fraction_total_reads` == new / sum(new), to 5 decimals | a normalisation, not an estimate | ≤ 5.6e-6 | ≤ 5.9e-6 |
| `-w` report rows == the table; its root == sum(new) within truncation | two writers, one estimate | yes / yes | yes / yes |
| **top 8 == the 8 Zymo bacteria** | D6300's list | **yes / yes** | — |
| **top 3 in order == 89.1% > 8.9% > 0.89%** | D6310's first three decades | — | ***L. monocytogenes* > *P. aeruginosa* > *B. spizizenii*** / genus too |
| **no genus outside the mock above 1%** | the closed list | **FAIL: *Xanthomonas* 1.6616%** | yes: *Xanthomonas* 0.3855% |

The truth is scored as in kraken2-real: bacteria only (standard-8 holds no fungi), with *Bacillus
spizizenii* standing for Zymo's *B. subtilis* strain. The 1% closed-list line is the one
[kraken2-real](../kraken2-real/README.md) asserts on the same reports. It was set before Bracken ran
and has not been moved. **Each chip's task therefore exits 1.** Every other check passes.

**Why *Xanthomonas* fails.** kraken2 puts 7,883 reads (0.097%) on *Xanthomonas*, all on one strain
taxid, *X. euvesicatoria* pv. alfalfae CFBP 3836 (1365647). Bracken divides those 7,869 direct reads
by the share of that genome's own reads that its `kmer_distrib` puts on that taxid, which is
**0.000128**. The prior comes out at **61.3 million reads, 7× the whole run**. That prior takes 59% of
the 150,028 reads kraken2 left at *Bacteria*, 82% of the 28,217 at *Pseudomonadota*, and 63% of the
19,415 at *Gammaproteobacteria*. Together that is 127k reads moved onto a genus that is not in the
tube. (These figures come from a per-node trace of `est_abundance.py` on the same bytes. The 135,108
it adds up to is the shipped result.) Bracken does not cap a genome's estimate at the sample size. A
few reads that the distribution says the genome almost never produces at that taxid are enough to
set it off. In the log run the same strain reaches 0.3855%, inside the line.

Observed, not asserted (`score.tsv`):
- **Recovery, even run (12% each by DNA):** kraken2's species-rank shares of 0.44–10.2% become
  8.6–17.0%. *L. monocytogenes* goes from 0.44% to 11.17%. *E. coli* goes from 0.73% to 8.64%,
  mostly from the 964,561 reads stuck at Enterobacteriaceae (602k of them). *Salmonella* is
  over-called at 17.0%: it takes the rest of that family node.
- **Recovery, log run:** 79% of all pairs (37.9M) sit above species in kraken2's report. Bracken
  moves them down: *L. monocytogenes* 3.00% → **87.02%** (89.1%), *P. aeruginosa* 1.61% → **10.57%**
  (8.9%), *B. spizizenii* 0.53% → **0.78%** (0.89%). Past the third decade the background sets the
  floor (*Listeria innocua* 0.38%, *Xanthomonas* 0.39%), as it did for kraken2.
- **Against [MetaPhlAn](../metaphlan-real/README.md) on the same reads:** *Bacillus* is 11.4% here
  and 0.055% there. The two estimate different quantities (read share, marker coverage), so this is
  an observation.

## Across chips: what is a fixed point and what is a path

**All eight outputs are byte-identical on all three chips** (`S.bracken`, `S.breport`, `G.bracken`,
`G.breport` per sample), and so are `checks.txt` and `score.tsv` (`results/compare.txt`). Intel and
AMD each match Graviton4. Bracken is a single pass of IEEE adds, multiplies and divides, done in
Python's dict order with no BLAS and no iteration, so the same script on the same bytes is expected
to give the same floats on any chip, and it did. This is neither a fixed point nor a path: the
arithmetic is identical. The
images differ only in Python patch release (3.14.6 on aarch.bio, 3.14.5 on bioconda).

## Caveats

- Two samples, one index. The *Xanthomonas* inflation is a property of this index's `kmer_distrib`
  and these reads. Another index or sample will throw up its own outliers, or none.
- Bracken's summary log carries timestamps, so it is the one output not compared byte for byte.
- Prices are on-demand us-west-2, as spawn printed them.
