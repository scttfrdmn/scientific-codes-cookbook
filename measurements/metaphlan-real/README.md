# MetaPhlAn on two whole published mock-community runs: three chips

> **Every chip gives the same profile, value for value, from the same set of bowtie2 hits.**
> AMD EPYC is 1.4× faster than Graviton4 and 5% cheaper per sample. Intel is as fast as Graviton4
> and 20% dearer. All three spend about a fifth of the bill on boot and staging.

Workload: the two **whole** Illumina shotgun runs of the ZymoBIOMICS standards from Nicholls et al.
2019 (GigaScience, [PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)), profiled one
after the other on one box against the recipe's full `mpa_vJun23_CHOCOPhlAnSGB_202403` index:

| run | standard | reads | bases | what it tests |
|---|---|---|---|---|
| `ERR2984773` | D6300 **even**: 8 bacteria at 12%, 2 yeasts at 2% | 17,571,462 (MiSeq 2×151) | 2.65 Gbp | the recipe's closed list, at 11× its fixture |
| `ERR2935805` | D6310 **log**: 89.1% down to 0.000089%, one decade per step | 95,665,106 (HiSeq 2×101) | 9.66 Gbp | depth and dynamic range |

Researchers profile shotgun samples of 10–100M reads, so these two whole runs cover that range. They
are also a published benchmark, so other profilers have been scored on the same bytes.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
make stage RECIPE=metaphlan                                  # the recipe's markers + index, once
bash measurements/metaphlan-real/stage-inputs.sh "$B"        # RODA .sra -> bucket, 00-prep -> FASTQ.gz
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py metaphlan $a "$B" "$(date +%s)" \
    measurements/metaphlan-real/01-profile.task.json)"
done
bash measurements/metaphlan-real/collect.sh "$B"             # results/<chip>/, results/compare.txt
```

`01-profile` runs `profile.sh` (pinned by sha256, like every script and input in `pins.sha256`),
which calls the recipe's own invocation once per sample:

```sh
metaphlan ERR2935805_1.fastq.gz,ERR2935805_2.fastq.gz --input_type fastq \
  --db_dir db -x mpa_vJun23_CHOCOPhlAnSGB_202403 --offline --nproc 16 --mapout mapout.bz2 -o profile.txt
```

## Results

16 vCPU (`r8*.4xlarge`, 128 GiB: the RAM is for the tmpfs that holds the 23 GB index twice, as in
the [recipe](../../recipes/metaphlan/README.md)), `--nproc 16`, the same staged bytes. MetaPhlAn
4.2.5 (aarch.bio's build on Graviton4, bioconda's on the other two). One task profiles both samples:

| chip | instance | even wall | log wall | tool wall | billed | peak RSS | $/hr | compute $/result (even / log) | billed $ (both) |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 338 s | 975 s | 1,313 s | 1,658 s | 22.2 GiB | 0.9426 | 0.0885 / 0.2553 | 0.4341 |
| Xeon 6975P-C | `r8i.4xlarge` | 347 s | 1,005 s | 1,352 s | 1,687 s | 22.2 GiB | 1.1114 | 0.1071 / 0.3103 | 0.5208 |
| **EPYC 9R45** | `r8a.4xlarge` | **248 s** | **686 s** | **934 s** | **1,163 s** | 22.2 GiB | 1.2781 | **0.0880 / 0.2435** | **0.4129** |

| chip | cores × threads | busy cores (even / log) | log run: reads/s | log run: compute $ per Gbp |
|---|---|---|---|---|
| Graviton4 | 16 × 1 | 12.5 / 12.5 | 98,100 | 0.0264 |
| Xeon 6975P-C | 8 × 2 | 11.9 / 12.5 | 95,200 | 0.0321 |
| EPYC 9R45 | 16 × 1 | 10.7 / 11.6 | 139,500 | 0.0252 |

- **AMD is 1.42× Graviton4** on the 9.66 Gbp run and 1.36× on the 2.65 Gbp one. At 36% more per hour,
  that makes it 5% cheaper per sample. **Intel's 8 cores with SMT match Graviton4's 16** to within 3%,
  and at 18% more per hour that makes Intel 20% dearer.
- **The x86 runs are bioconda's baseline bowtie2.** Each logs `Failed to launch x86-64-v3 version,
  staying with default`, so neither x86 chip ran bowtie2's AVX2 build. AMD's lead is with that
  handicap, and this compares builds as well as chips, as in [iqtree-real](../iqtree-real/README.md).
- **Overhead is 229–345 s** per task: stage-in of 35 GB (index, markers, reads), Docker, the image
  pull. That is 20–21% of every bill, and it is paid once however many samples the task profiles.
  Profile a cohort in one task, as here, not one task per sample.
- **Peak RSS is 22.2 GiB on every chip and both samples.** That is bowtie2 holding the index, so it
  does not grow with depth. The box is sized by the tmpfs (54–55 of 62 GiB used), not by compute.
- **Wall time is not proportional to reads.** On Graviton4 it was 19 µs per read on the 151 bp MiSeq
  run and 10 µs on the 101 bp HiSeq run. Read length, hit rate and a fixed start-up all count, so size
  a TTL from a measured run of similar data, not by scaling read count.
- n = 1 per cell. Both runs are deterministic (identical output on all three chips, below), so a
  repeat would change the timings and nothing else.

## What held at scale, on all three chips

| claim | why it must hold | even | log |
|---|---|---|---|
| reads processed == reads ≥ 70 bp in the FASTQ | MetaPhlAn's `--read_min_len`; nothing dropped | 17,571,462 | 95,665,106 |
| FASTQ pairs and bases == ENA's `read_count`, `base_count` | the RODA dump is the archived run | yes | yes |
| `UNCLASSIFIED` + kingdoms == 100 | relative abundance | 100.000001 (tol 1.5e-6) | same |
| every clade == the sum of its children | the profile is a tree | 42 parents, worst 1e-6 | 38, worst 1e-6 |
| **required Zymo species detected** | closed list | **8/8 bacteria** | **2/2** (89.1%, 8.9%) |
| **unexpected species above 1%** | closed list | **0** | **0** |

Both conservation tolerances are the printed rounding of the summed terms (half a unit in the last
place, per term), not a band. `score.py` carries these checks. It is the recipe's scorer plus the
read and conservation checks, and its truth tables are `zymo_d6300.tsv` (the recipe's) and
`zymo_d6310.tsv` (Zymo's D6310 composition, with a `must_detect` column).

Observed, not asserted:
- **The log standard gives the detection floor: down to 0.0089%, not 0.00089%.** *L. fermentum* at
  0.0089% of DNA was found (0.0085%), as were *E. coli* and *S. enterica* at 0.089%. *E. faecalis* and
  *C. neoformans* (0.00089%) and *S. aureus* (0.000089%) were not, at 95.7M reads. Only the two
  species at 8.9% and above are asserted. The rest is the measured ladder.
- ***Bacillus subtilis* is under-called about 200×,** at 0.055% of an even mix that has it at 12%. The
  recipe's fixture showed the same (0.026%), so depth does not fix it. It is detected, which passes the
  presence check, but no one should read its abundance. A few hundredths of a percent go to
  *B. vallismortis*, *B.* sp. FSQ1 and *B. tequilensis*, below the 1% line.
- *S. cerevisiae* is detected in both runs (0.32% even, 0.14% log). *C. neoformans* is not.
- The even run's abundances (6.5–16.2% for the eight bacteria) are marker-coverage estimates against
  12% by genomic DNA, which is the recipe's reason they are reported, not asserted.

## Across chips: what is a fixed point and what is a path

**Read placement is identical.** Each run's bowtie2 hits, sorted, are byte-identical on all three
chips: 840,789 marker hits (even) and 1,968,073 (log). The FASTQ, index and bowtie2 version are the
same, so this is the expected result, and it is checked rather than assumed.

**Every profile value is identical.** The even profile body is byte-identical on all three. In the log
run it is byte-identical on Graviton4 and Intel. AMD differs in one cell: the `additional_species` list
for the merged *S. cerevisiae* clade gives the same two names (*S. boulardii*, *S. uvarum*) in the
other order. No abundance, taxid or clade differs, and `score.tsv` is byte-identical everywhere. An
unordered collection's iteration order is the likely cause; it was not tested on a repeat run. Compare
profiles by clade, not by bytes.

MetaPhlAn's numerics are not BLAS-bound (bowtie2 alignment, then marker-coverage averaging), so the
[OpenBLAS kernel question](../../patterns/cross-architecture.md) does not arise here.

## Caveats

- **A sample titled "zymo mock community" was not one.** The first candidate was `ERR12710526`
  (PRJEB73321, 39.3M reads, WGS, NovaSeq). It profiles to a gut-like community: 15 species above 1%,
  *Bacteroides*, *Blautia*, *Roseburia* and *Bifidobacterium* among them, and only *E. coli* of the
  D6300 eight (`results/ERR12710526/`). All 18 runs in that study carry the same title. A defined
  mixture's species list only checks a sample that is that mixture: confirm the product number from
  its paper before you use it as truth.
- Two samples are a small cohort. Per-read cost is what should transfer. Overhead is per task.
- Prices are on-demand us-west-2, as spawn printed them on these instances.
