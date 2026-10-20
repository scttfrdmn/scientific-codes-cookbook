# MEGAHIT on two whole mock-community metagenomes: three chips

> **Every chip recovers all eight bacteria of the even community at 95–99% of Zymo's own
> references, and 99.6% of the assembly aligns to the ten strains.** Graviton4 is the cheapest
> per assembly: AMD EPYC is 1.23–1.29× faster but costs 5–10% more, and Intel is both slower and
> 35–48% dearer. The x86 and arm64 builds assemble the same reads into slightly different
> contig sets (about 2% of bases), even at `-t 1`. Within a build, `-t 1` is byte-identical on
> Intel and AMD.

Workload: the two **whole** Illumina shotgun runs of the ZymoBIOMICS standards from Nicholls et al.
2019 (GigaScience, [PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)), the same staged
bytes [metaphlan-real](../metaphlan-real/README.md) profiles, assembled one after the other on one box:

| run | standard | pairs | bases | what it tests |
|---|---|---|---|---|
| `ERR2984773` | D6300 **even**: 8 bacteria at 12%, 2 yeasts at 2% | 8,785,731 (MiSeq 2×151) | 2.65 Gbp | recovery of every genome |
| `ERR2935805` | D6310 **log**: 89.1% down to 0.000089%, one decade per step | 47,832,553 (HiSeq 2×101) | 9.66 Gbp | depth and dynamic range |

A shotgun metagenome of 3–10 Gbp is what MEGAHIT is used on. A mock community is the only kind
whose answer is known: Zymo publishes a reference for each of its ten strains
(`ZymoBIOMICS.STD.refseq.v2.zip`, 73.0 Mb, copied from Zymo's S3 bucket by object version id and
pinned by sha256). This is a metagenome, where [spades-real](../spades-real/README.md) is a single
isolate.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/metaphlan-real/stage-inputs.sh "$B"    # the reads, once (RODA .sra -> FASTQ.gz)
bash measurements/megahit-real/stage-inputs.sh "$B"      # Zymo's references + scripts
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py megahit $a "$B" "$(date +%s)" \
    measurements/megahit-real/01-assemble.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py megahit arm64 "$B" "$(date +%s)" \
  measurements/megahit-real/02-evaluate.task.json)"    # metaQUAST: every assembly vs Zymo's references
bash measurements/megahit-real/collect.sh "$B"
```

`01-assemble` runs `assemble.sh` (pinned by sha256, with every input, in `pins.sha256`), which
calls the recipe's own invocation at a whole run's depth:

```sh
megahit -1 ERR2984773_1.fastq.gz -2 ERR2984773_2.fastq.gz -t 16 --min-count 2 -m 16000000000 \
  --tmp-dir /var/tmp/mhtmp -o /var/tmp/asm
```

`01-assemble-t1` is the same on the even run at `-t 1` (the recipe's determinism setting), and
`01-assemble-nohw` is that again with `--no-hw-accel`, run with the `amd` target. `02-evaluate`
expects all of them, plus a second Graviton4 run of `01-assemble` under `runs/megahit-real-repeat/`.

## Results

16 vCPU (`c8*.4xlarge`, 32 GiB), `-t 16`, the same staged bytes. MEGAHIT 1.2.9: aarch.bio's build
on Graviton4, bioconda's on the other two. One task assembles both runs:

| chip | instance | even wall | log wall | tool wall | billed | peak RSS (even / log) | $/hr | compute $/result (even / log) | billed $ (both) |
|---|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 343 s | 641 s | 984 s | 1,083 s | 2.10 / 7.67 GiB | 0.6381 | **0.0608 / 0.1137** | **0.1920** |
| Xeon 6975P-C | `c8i.4xlarge` | 394 s | 810 s | 1,204 s | 1,282 s | 2.10 / 7.67 GiB | 0.7497 | 0.0821 / 0.1686 | 0.2670 |
| EPYC 9R45 | `c8a.4xlarge` | **266 s** | **520 s** | **786 s** | **847 s** | 2.10 / 7.67 GiB | 0.8622 | 0.0638 / 0.1245 | 0.2029 |

At `-t 1`, on the even run (`c8*.2xlarge`, 16 GiB, one core busy):

| chip | instance | wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 3,932 s | 3,996 s | 2.07 GiB | 0.3190 | 0.3484 | 0.3541 |
| Xeon 6975P-C | `c8i.2xlarge` | 3,527 s | 3,580 s | 2.07 GiB | 0.3748 | 0.3672 | 0.3727 |
| EPYC 9R45 | `c8a.2xlarge` | 3,117 s | 3,163 s | 2.07 GiB | 0.4311 | 0.3733 | 0.3788 |

| chip | cores × threads | busy CPUs (even / log) | MEGAHIT core it chose |
|---|---|---|---|
| Graviton4 | 16 × 1 | 12.8 / 12.7 | portable (aarch64 has no POPCNT/BMI2) |
| Xeon 6975P-C | 8 × 2 | 13.5 / 13.4 | POPCNT + BMI2 |
| EPYC 9R45 | 16 × 1 | 13.2 / 13.0 | POPCNT + BMI2 |

- **Graviton4 is the cheapest assembly at `-t 16`.** AMD is 1.29× faster on the even run and 1.23×
  on the log run, but 35% dearer per hour, so it costs 5–10% more. Intel's 8 SMT cores are
  0.87× and 0.79× Graviton4's speed at 17% more per hour: 35–48% more per assembly.
- **At one thread the order flips.** AMD is 1.26× and Intel 1.11× faster than Graviton4, and the
  three cost within 7% of each other. Graviton4 gains on the other two as threads are added.
- **`-t 16` is 9–12× faster than `-t 1` and 4–6× cheaper per assembly.** MEGAHIT keeps about 13 of
  16 CPUs busy on every chip. Don't run it at `-t 1` for throughput; the recipe does so only to
  pin the bytes.
- **Peak RSS is set by the data, not the chip:** 2.10 GiB on the 2.65 Gbp even run and 7.67 GiB on
  the 9.66 Gbp log run, the same to within 0.02% on all three (`ru_maxrss`). The 32 GiB box is sized for
  the tmpfs, which holds 8.5 GiB of staged reads. The work directory peaks at 4.0 GiB on the root disk.
- **Boot and staging are 61–99 s** of a 847–1,282 s bill (6–9%), paid once for both samples.
- n = 1 per cell, plus a second Graviton4 run. Timings repeated to 1–2% (343 vs 340 s, 641 vs 631 s).

## What held at scale, on every assembly

Every assembly: three chips at `-t 16`, a Graviton4 repeat, three chips at `-t 1`, and AMD
`--no-hw-accel`. That is 8 assemblies of the even run and 4 of the log run.

| claim | why it must hold | even | log |
|---|---|---|---|
| reads in MEGAHIT's library == staged reads | its own `pe, N reads` line; nothing dropped | 17,571,462 | 95,665,106 |
| every header's `len=` == its sequence; count and total == MEGAHIT's summary line | the file is the assembly MEGAHIT reports | 20,597–20,614 contigs | 14,205–14,212 |
| completion | exit 0 **and** `ALL DONE` in MEGAHIT's log | ok | ok |
| **recovered:** genome fraction ≥ 90% (metaQUAST) | each organism abundant enough to assemble | **8/8 bacteria, 95.29–99.27%** | **3/3** (89.1%, 8.9%, 0.89%): 98.47–99.17% |
| **consensus:** ≤ 50 mismatches per 100 kbp vs that strain's own reference | the references are the standard's strains | worst 17.29 (*E. coli*) | worst 9.55 (*P. aeruginosa*) |
| **closed community:** fully unaligned contigs ≤ 0.5% of the assembly (≥ 500 bp) | an off-community genome at assemblable depth would be megabases | 6,790 bp (0.019%) | 17,902 bp (0.081%) |
| **`-t 1`, x86 build: Intel == AMD == AMD `--no-hw-accel`** | one thread, one binary package: nothing to reorder | **byte-identical** (`4125aa71…`) | — |

The recovery and consensus bounds are floors that a dropped genome or a wrong consensus misses by a
wide margin, not bands on the observed values. The lowest recovery is *Lactobacillus fermentum*,
at 95.3%.

Observed, not asserted:
- **The log standard gives the assembly floor: 0.89%, not 0.089%.** *B. subtilis* and
  *S. cerevisiae* at 0.89% of DNA assemble to 98.5% and 61.1% of their genomes. *E. coli* and
  *S. enterica* at 0.089% give 3.4% and 5.3%. Below that is under 0.2%. MetaPhlAn
  [detects](../metaphlan-real/README.md) species down to 0.0089%, two decades below what MEGAHIT assembles.
- **The yeasts at 2% in the even standard assemble partly:** *S. cerevisiae* to 30.2% and
  *C. neoformans* to 5.0%. Most of the 664–745 misassemblies metaQUAST reports are against these two
  draft references (470–547 of them); the eight bacteria have 1–8 each.
- 99.62% of the even assembly's 36.3 Mbp (≥ 500 bp) aligns. N50 is 117 kbp and the largest contig
  1.21 Mbp. Duplication ratio is 1.011.

## Across chips and runs: what is a fixed point and what is a path

Contig sets are compared by `compare.py`, ignoring contig names, order and orientation, since
MEGAHIT names and orders contigs as its threads finish:

| pair (even run) | contigs | in only one of the two | lengths |
|---|---|---|---|
| Intel `-t 1` vs AMD `-t 1` vs AMD `--no-hw-accel` | 20,614 each | **0** (byte-identical) | identical |
| Graviton4 `-t 16`, run 1 vs run 2 | 20,597 each | 2 (702 bp) | identical |
| Intel `-t 16` vs AMD `-t 16` | 20,614 each | 15 (10,011 bp) | identical |
| Graviton4 `-t 16` vs `-t 1` | 20,597 each | 14 (42,165 bp) | identical |
| **Graviton4 `-t 1` vs Intel `-t 1`** | 20,597 vs 20,614 | **234 vs 251 (0.90 vs 0.92 Mbp)** | differ |

- **The chip does not change the assembly.** At `-t 1` Intel and AMD give the same bytes, so a
  same-build cross-chip comparison is exact.
- **Threads change a few contigs, never their lengths.** At `-t 16`, two runs on one box differ in
  2 contigs (log run: 6). `-t 16` and `-t 1` differ in 14. In every same-build pair, on both
  samples, the multiset of contig lengths was identical, and only the bases of a handful of equal-length contigs moved.
- **The arm64 and x86 builds differ, and not because of the hardware-accelerated core.** Forcing
  the x86 build onto MEGAHIT's portable core (`--no-hw-accel`, the one aarch64 runs) gave the x86
  bytes, not the arm64 ones. So the difference is in how the same 1.2.9 source compiles for each
  architecture. This was not traced further. About 2.2% of assembled bases sit in contigs only one
  build produces. The recipe's 400 kb fixture is byte-identical across the two builds; a whole
  metagenome is not.
- **None of this moves the science past the third significant figure.** Per-genome genome fraction
  agrees to within 0.09 points across all eight even-run assemblies and both builds. Every claim
  above holds on every one. The QUAST counts that move most are the yeasts' misassemblies (arm64
  build 470, x86 build 547), which sit on the two low-coverage draft genomes.

MEGAHIT does not link a BLAS, so the [OpenBLAS kernel question](../../patterns/cross-architecture.md)
does not arise.

## Caveats

- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- `-m 16000000000` is fixed so every chip gets the same SdBG memory bound. MEGAHIT used at most 7.7 GiB.
- Zymo's v2 references (2023) postdate the 2019 runs. They are the same strains, re-sequenced.

## Files

`stage-inputs.sh` (Zymo zip by version id, scripts), `mkspec.py` (writes the four specs),
`assemble.sh`, `evaluate.sh`, `canon.py` (order- and orientation-free fingerprints), `compare.py`
(contig-set differences), `collect.sh`, `mkpins.sh`, `pins.sha256`. Per-run evidence is in
`results/<label>/<accession>/` and the metaQUAST summaries and cross-run comparisons in
`results/evaluate/<accession>/`. `assemble.sh` gained its optional fifth argument (`--no-hw-accel`)
after the `-t 16` and `-t 1` runs; those runs passed four, and the commit before this one holds the
script exactly as they ran it.
