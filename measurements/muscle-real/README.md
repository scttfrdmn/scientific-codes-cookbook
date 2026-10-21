# MUSCLE on a real gene family — 919 sequences, `-align` and `-super5`, three chips

> **`muscle -align` aligns the whole 919-sequence Pfam seed in 24–29 minutes on 8 vCPU, and returns
> the same alignment byte for byte on every repeat, on Graviton4 and Intel identically.** It scores
> SP 0.9224 against Pfam's curated alignment, between mafft's FFT-NS-2 (0.9243 in seconds) and below
> L-INS-i (0.9276); `-super5` is 22× faster and a little less accurate.

Workload: the **whole** Pfam 38.2 seed of `PF01255.25` (Prenyltransf), 919 sequences of 203–349
residues — the **same staged bytes** [mafft-real](../mafft-real/README.md) aligned, so the two
aligners are scored on identical input against the same reference by the same scorer. A gene-family
study aligns hundreds to about a thousand sequences, which is exactly where MUSCLE's own help puts the
line between `-align` and `-super5`; 919 sits on it, so both modes are the real choice a user faces.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/mafft-real/stage-inputs.sh "$B"     # the family, once (mafft-real's inputs)
spawn task run --spec "$(python3 scripts/spec_arch.py mafft arm64 "$B" "$(date +%s)" measurements/mafft-real/01-align.task.json)" --wait   # mafft's L-INS-i to compare against
bash measurements/muscle-real/stage-inputs.sh "$B"     # checks all four pins, stages real.sh
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py muscle $a "$B" "$(date +%s)" measurements/muscle-real/01-align.task.json)" --wait
done
bash measurements/muscle-real/collect.sh "$B" <scratch dir>   # results/<chip>/, results/compare.txt
```

The task runs `real.sh` (pinned by sha256, like the three inputs), which aligns the family six times:

```sh
muscle -super5 pf01255.25_unaligned.fa -output s5t8.fa  -threads 8   # twice
muscle -super5 pf01255.25_unaligned.fa -output s5t1.fa  -threads 1   # twice
muscle -align  pf01255.25_unaligned.fa -output align.fa -threads 8   # twice
```

## Results

`c8*.2xlarge` (8 vCPU, 16 GiB), the same staged bytes. MUSCLE 5.3 is aarch.bio's arm64 build on
Graviton4 and bioconda's x86 build on Intel and AMD. Times are the mean of each mode's two runs.
Billed $/result is the tool wall plus that chip's overhead (billed window minus the six commands:
Docker install, image pull, staging; 51–59 s), at the hourly price spawn printed.

| chip | instance | cores × threads | `-align` | `-super5 -threads 8` | `-super5 -threads 1` | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 8 × 1 | 1,549 s | 70 s | 249 s | 5.20 GiB | 0.3190 | **0.137** / **0.0062** | **0.142** / **0.0109** |
| Xeon 6975P-C | `c8i.2xlarge` | 4 × 2 | 1,765 s | 74 s | 241 s | 5.20 GiB | 0.3748 | 0.184 / 0.0077 | 0.190 / 0.0138 |
| EPYC 9R45 | `c8a.2xlarge` | 8 × 1 | **1,421 s** | **66 s** | **231 s** | 5.20 GiB | 0.4311 | 0.170 / 0.0079 | 0.176 / 0.0140 |

$/result is given as `-align` / `-super5 -threads 8`. Each task's billed window was 3,788 s
(Graviton4), 4,218 s (Intel) and 3,485 s (AMD).

- **`-align` is the job, and Graviton4 is the cheapest place to run it:** 0.142 $ billed against
  0.176 (AMD, 9% faster, 24% dearer) and 0.190 (Intel, 14% slower, 34% dearer). Its log splits the
  1,549 s into posteriors (6 min), two consistency passes (9 min each) and refinement (1 min).
- **`-align` keeps 5.4–5.8 of 8 vCPU busy; `-super5` keeps 3.6–4.0.** Neither is serial, but neither
  fills the box, so more cores are not the first lever for `-super5`.
- **Peak memory is 5.20 GiB on every chip** (cgroup `memory.peak`, the whole task; `-super5` alone
  peaks at 0.23 GiB). A 16 GiB `c8g` holds `-align` at this size with room; `-super5` would fit on
  anything.
- n = 2 per cell, every repeat within 27 s (`-align`) and 1 s (`-super5`) of the first. MUSCLE links no
  BLAS (libstdc++, libgomp, libm), so no OpenBLAS kernel choice is involved.
- `00-probe.task.json` was the sizing run (Graviton4, the same steps once for `-align`: 1,521 s,
  5.20 GiB); it produced the same alignments as the Graviton4 row.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| input == Pfam seed, gaps removed | the staging is lossless | 919 / 207,712 | same | same |
| every row of every output == its input, gaps removed | an aligner may only insert gaps | 6 of 6 | 6 of 6 | 6 of 6 |
| every row ungapped == mafft L-INS-i's, gaps removed | two aligners agree on content | 6 of 6 | 6 of 6 | 6 of 6 |
| every row the same length | the definition of an alignment | 6 of 6 | 6 of 6 | 6 of 6 |
| every Pfam residue located in the output | the scorer saw the whole family | 0 unmapped | 0 | 0 |
| scorer control: Pfam scored against itself | SP = TC = 1 exactly | 1 / 1 | 1 / 1 | 1 / 1 |
| `-super5`, two runs, each thread count | byte-identical (seen in the probe first, then asserted) | yes | yes | yes |
| `-align`, two runs (reported, not asserted) | — | identical | identical | identical |

**Agreement with Pfam's curated alignment** (an observation, not an assertion: an MSA has no defined
precision to set a tolerance from). SP and TC as in [mafft-real](../mafft-real/README.md): SP over
Pfam's 88,476,208 same-column residue pairs, TC over its 473 columns. mafft's rows are mafft-real's.

| aligner, mode | wall (Graviton4) | columns | SP vs Pfam | TC vs Pfam | SP vs mafft L-INS-i |
|---|---|---|---|---|---|
| mafft L-INS-i, `--threadit 0` | 484 s | 631 | **0.927568** | **0.264271** | (itself) |
| mafft `--auto` → FFT-NS-2 | 1 s | 693 | 0.924346 | 0.234672 | — |
| **muscle `-align`** | 1,549 s | 1,079 | 0.922368 | 0.253700 | 0.979320 |
| muscle `-super5 -threads 8` | 70 s | 860 | 0.918339 | 0.232558 | 0.975480 |
| muscle `-super5 -threads 1` | 249 s | 841 | 0.916922 | 0.228330 | 0.973034 |

On this family mafft's L-INS-i is both the most accurate and 3× faster than `muscle -align` on the
same box. `-align` beats FFT-NS-2 on TC (whole columns) and loses on SP; MUSCLE spreads the family over
1,079 columns where mafft uses 631–693. `-super5` gives up 0.4 points of SP for a 22× speed-up.

## What differed across chips

**Intel against Graviton4: nothing.** All six alignments are the same bytes on both
(`results/compare.txt`): `-align` `76a75678…`, `-super5 -threads 8` `49568dd4…`, `-threads 1`
`c57df6f1…`. Two Graviton4 boxes (probe and run) also agree.

**AMD against Graviton4: `-super5 -threads 8` is identical; `-super5 -threads 1` and `-align` are not**,
and the difference is the chip, not the box or the run: AMD's two repeats agree, and a second, fresh
`c8a.2xlarge` (`02-diag.task.json`) reproduced AMD's `-threads 1` alignment (`0e2de0f5…`). The binary
is the same one Intel ran. Narrowing glibc's x86 ISA dispatch (`GLIBC_TUNABLES`, AVX-512 off, then
AVX2/FMA off too) did not move it, so the cause is not established. The size of it:

| | Graviton4 / Intel | EPYC | AMD vs Graviton4, SP / TC |
|---|---|---|---|
| `-align` | 1,079 columns, SP 0.922368 | 1,079 columns, SP 0.922368 | 0.999990 / 0.998700 |
| `-super5 -threads 1` | 841 columns, SP 0.916922 | 911 columns, SP 0.916921 | 0.999990 / 0.862330 |

So an alignment is a **path**: 99.999% of residue pairs agree, the score against Pfam agrees to six
digits, but the bytes and the column count differ. Anything asserted about gap placement is
chip-specific even with threads and inputs pinned; content (every residue, in every row) is not.
**The thread count also changes the answer** on every chip (`-threads 8` 860 columns, `-threads 1`
841), so pin it.

## Caveats

Pfam's seed is a curated alignment, not a structural truth set such as BAliBASE, so SP and TC measure
agreement with Pfam, not correctness. The billed windows start when spawn's wrapper starts, after
boot. Pins: `pins.sha256` (the two Pfam files mafft-real staged, mafft-real's L-INS-i alignment —
one file on all three chips — and `real.sh`).
