# kallisto on a whole RNA-seq run — Graviton4, Intel and AMD

> **The same sample on all three chips: 14,510,404 of 15,800,127 pairs pseudoaligned on every one, the
> estimated counts adding back to that number to within 1.4 reads, and the rank agreement with salmon
> (0.908) and RSEM (genes 0.988) holding on each.** AMD is 1.36× faster than Graviton4 for the same
> compute cost; Intel is slowest and 24% dearer. With the fragment-length distribution given, the
> output is byte-identical on all three chips. Left to estimate it, the output follows thread order.

Workload: the **complete** Geuvadis run `ERR188026` (15,800,127 read pairs) against **all 465,769**
Ensembl 116 cDNA transcripts, with `kallisto quant` at its defaults and 16 threads. One sample against
the whole transcriptome is the unit kallisto runs on, once per sample before any differential
expression, so this is the real per-sample job at its real size. The reads, the transcriptome and the
index are the bytes [salmon-real](../salmon-real/README.md), [rsem-real](../rsem-real/README.md) and
[recipes/kallisto](../../recipes/kallisto/README.md) already use, so the cross-checks compare three
quantifiers on identical input.

**Data tier: durable public ids, plus one built intermediate.** The reads are pinned by ENA's
`fastq_md5` ([`reads.md5`](reads.md5)) and checked on every box. The cDNA is Ensembl release 116,
pinned by Ensembl's `CHECKSUMS` when salmon-real staged it. The index is the one `recipes/kallisto`
builds from that cDNA (931,285,390 bytes, sha256 `424b0343…`), hashed on every box; the check asserts
all three chips read the same file. The salmon and RSEM outputs the check compares against are pinned
in [`siblings.sha256`](siblings.sha256).

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/kallisto-real/stage-inputs.sh "$B"     # scripts only; reads, cDNA and index are already staged
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py kallisto $a "$B" r1 measurements/kallisto-real/02-quant.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py kallisto arm64 "$B" c2 measurements/kallisto-real/03-check.task.json)"
bash measurements/kallisto-real/collect.sh "$B" r1 r1    # results/<chip>/ and the table below
```

```sh
kallisto quant -i kallisto.idx -o A -t 16 ERR188026_1.fastq.gz ERR188026_2.fastq.gz                  # the job
kallisto quant -i kallisto.idx -o B -t 16 -l 200 -s 30 ERR188026_1.fastq.gz ERR188026_2.fastq.gz     # the control
```

[`quant.sh`](quant.sh) runs both and checks bookkeeping and conservation in the task. [`check.R`](check.R)
runs on Graviton4 in the RSEM image, for its R (the kallisto image has no python3 and no jq), and
does everything that needs a join. [`mkspec.py`](mkspec.py) writes both specs and pins the staged
files by sha256.

## Results

Same image digests on each chip: aarch.bio's kallisto 0.52.0 on Graviton4 and bioconda's x86 build of
the same version on both x86 chips. Same staged bytes. 16 threads on a `4xlarge` (16 vCPU, 32 GiB).

| chip | instance | quant wall | billed window | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 221 s | 669 s | 2.70 GiB | 0.6381 | **0.0392** | 0.0496 |
| Xeon 6975P-C | `c8i.4xlarge` | 233 s | 685 s | 2.71 GiB | 0.7497 | 0.0485 | 0.0610 |
| EPYC 9R45 | `c8a.4xlarge` | **162 s** | **476 s** | 2.71 GiB | 0.8622 | **0.0388** | **0.0493** |

A *result* is one sample quantified at the defaults (run A). *Quant wall* is `kallisto quant` end to
end, index load included. *Billed window* runs from launch to terminate and also holds the B control
(389, 392 and 270 s), so *billed $/result* is that window less B, at spawn's $/hr: what a task running
only the job would bill. The window cost as charged was $0.1186, $0.1426 and $0.1140. *Peak RSS* is the
kernel's `VmHWM` for `kallisto`. Threads per core: 1 on Graviton4 and AMD, 2 on Intel, so
`c8i.4xlarge` has 8 cores. kallisto links no BLAS (none in either image).

- **AMD is fastest and ties Graviton4 on cost.** 1.36× faster, and its hour costs 35% more, so compute
  per result lands within 1% of Graviton4 (0.0388 against 0.0392). Intel is 1.05× slower than
  Graviton4 on half the physical cores and 24% dearer per result.
- **Compute is most of the bill.** Boot, Docker and staging the 3.1 GB of inputs are 44–60 s of each
  window. The rest is kallisto, unlike [salmon-real](../salmon-real/README.md), where overhead is 40%.
- **salmon is still the cheaper quantifier.** On the same reads on `c8g.4xlarge`, salmon-real's quant
  is 74 s and $0.0131 compute, against kallisto's 221 s and $0.0392 here.
- **Memory is the index, not the chip:** 2.70–2.71 GiB everywhere. The `4xlarge` is sized by threads,
  not by memory.
- **n = 1 per chip.** The pseudoalignment counts and the B output are identical across chips, so a
  repeat would re-measure timing only.

## The identities, at scale, on all three chips

| observable | assertion | observed |
|---|---|---|
| reads, index | ENA md5 == published; index sha256 one file across chips | yes |
| `n_targets`, rows, ids | == 465,769, the gene map's transcript set | every chip |
| `n_processed` | **== ENA `read_count`** | 15,800,127 |
| `n_pseudoaligned`, `n_unique` | **identical across chips** (pseudoalignment is per read) | 14,510,404 / 1,219,979 |
| **Σ est_counts − n_pseudoaligned** | **within the half-ulp of every printed value** (≤ 28.9) | −0.030, −1.363, −0.218 |
| Σ tpm − 1e6 | within the half-ulp of every printed value (≤ 2.09) | −0.131, +0.037, −0.004 |
| **tpm = K · est/eff** | **every row on one line, within all three roundings and K's** | 0 off |
| tpm where est_counts == 0 | == 0 | 0 |
| **transcript Spearman vs salmon** | **≥ 0.85**, both detected | 0.9084 / 0.9083 / 0.9083 (n ≈ 87,160) |
| transcript Spearman vs RSEM | ≥ 0.85, both detected | 0.8882 / 0.8880 / 0.8880 (n ≈ 95,860) |
| **gene Spearman vs salmon / RSEM** | **≥ 0.90**, both detected | 0.9815 / 0.9885, every chip |
| log(1+TPM) Pearson vs salmon | observation | 0.9656 |
| B `abundance.tsv` | **byte-identical on all three chips** | yes |

Columns of three are Graviton4 / Intel / AMD. Full output: [`results/check.txt`](results/check.txt).

- **The EM conserves the pseudoaligned reads.** Every equivalence class hands its count to its
  transcripts in proportion, so Σ est_counts is the pseudoaligned total. The one way a class could
  drop out is if all its transcripts were zeroed at convergence (`EMAlgorithm.h`). The measured gap
  is at most 1.4 reads of 14.5 million, inside the printing bound on every chip.
- **The bounds are the printing, not a choice.** `abundance.tsv` is written at 6 significant digits,
  so each value sits within half a unit of its 6th digit. The bound sums those, row by row.
- **TPM is computed from est_counts and eff_length** (`counts_to_tpm`), so the exact identity is
  tpm ∝ est/eff with one constant for all 465,769 rows, and every row is on it.
- **The rank floors were set before the run** from the sibling pairs: salmon~kallisto 0.908 on these
  reads ([quant-depth](../quant-depth/README.md)), RSEM~salmon 0.944 and genes 0.981 (rsem-real).
  kallisto~RSEM lands lower than either pair at transcript level (0.888) and highest of the three
  at gene level (0.988): the tools disagree on which isoform, not on which gene.

## Across chips

- **The default run is a path, and the path is thread order, not the chip.** kallisto estimates the
  fragment-length distribution from whichever pairs the 16 threads reach first. Graviton4 got 155.6,
  Intel and AMD 155.7, and the EM ran 1,263, 1,256 and 1,255 rounds. Intel and AMD run the same binary
  and still differ from each other. Every `eff_length` moves (465,699 of 465,769 rows differ, by at most
  0.32), the largest est_counts change is 12% on a transcript with est ≥ 1, and TPM rank agreement
  across chips is 0.999999. The same pin gave both outcomes on one chip before
  ([recipes/kallisto](../../recipes/kallisto/README.md), "On x86_64"). Reported, not asserted.
- **With the distribution given, the output is a fixed point.** Run B passes `-l 200 -s 30`, which
  removes the one input thread order can move. What remains is pseudoalignment, which is per read,
  and a serial EM over integer class counts. B's `abundance.tsv` has the same sha256
  (`4d6f7f8e…`) on Graviton4, Intel and AMD, and its EM ran 1,420 rounds on each.
- **B is a control, not a better default.** Giving `-l`/`-s` on paired-end data changes which pairs
  pseudoalign (14,434,932 against 14,510,404) and slows the run by 1.7×, and kallisto warns against
  it. Its rank agreement with salmon and RSEM is the same as A's to within 0.003.

## Caveats

One sample, one transcriptome, one thread count, no bootstraps (`--seed` only seeds bootstrap
sampling, so it was not set). The index is a pinned input built once on Graviton4 (491 s on `r8g`,
in the recipe), not measured across chips. Instance size and generation were not swept. Spend for
this write-up: **$0.38** (three quant tasks plus two runs of the check task).
