# SPAdes on a whole bacterial isolate: three chips

> **Every chip assembles the same genome, byte for byte, and it covers 98.6% of the closed
> reference.** AMD EPYC is 1.16× faster than Graviton4 and Intel 1.12×, but both cost more per
> assembly: Graviton4 is the cheapest at $0.110 billed. The working directory peaks at 18 GB, four
> times SPAdes' RAM, so it has to go on disk, not in `/tmp`.

Workload: one **whole** MiSeq 2×300 run of wild-type *E. coli* K-12 MG1655, `DRR171213`
(PRJDB8030, library `Ecoli_MG1655_NN`): 760,683 pairs, 450,083,225 bases, about 97× coverage. Not
a slice. It is scored against the strain's closed reference, RefSeq `GCF_000005845.2` (U00096.3,
one 4,641,652 bp chromosome). A single isolate at 50–150× is the job SPAdes `--isolate` exists
for, so this is the size a researcher runs, and the reference makes it a check against truth.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/spades-real/stage-inputs.sh "$B"     # RODA .sra -> bucket, 00-prep -> FASTQ.gz + reference
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py spades $a "$B" "$(date +%s)" \
    measurements/spades-real/01-assemble.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py spades arm64 "$B" "$(date +%s)" \
  measurements/spades-real/02-evaluate.task.json)"     # QUAST: all three assemblies vs U00096.3
```

`01-assemble` runs `assemble.sh` (pinned by sha256 with every input in `pins.sha256`), which calls
the recipe's own invocation at a whole run's depth:

```sh
spades.py --isolate -1 DRR171213_1.fastq.gz -2 DRR171213_2.fastq.gz -o /var/tmp/asm -t 8 -m 14
```

## Results

8 vCPU (`c8*.2xlarge`, 16 GiB), `-t 8`, the same staged bytes. SPAdes 4.3.0: aarch.bio's build on
Graviton4, bioconda's on the other two.

| chip | instance | SPAdes wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.2xlarge` | 1,182 s | 1,242 s | 4.39 GiB | 0.3190 | **0.1047** | **0.1101** |
| Xeon 6975P-C | `c8i.2xlarge` | 1,057 s | 1,109 s | 4.39 GiB | 0.3748 | 0.1101 | 0.1155 |
| EPYC 9R45 | `c8a.2xlarge` | **1,023 s** | **1,066 s** | 4.39 GiB | 0.4311 | 0.1225 | 0.1276 |

| chip | cores × threads | busy CPUs (avg) | K21 | K33 | K55 | K77 | K99 | K127 |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | 8 × 1 | 4.15 | 109 s | 100 s | 206 s | 232 s | 258 s | 274 s |
| Xeon 6975P-C | 4 × 2 | 4.45 | 82 s | 83 s | 182 s | 213 s | 245 s | 252 s |
| EPYC 9R45 | 8 × 1 | 4.31 | 79 s | 86 s | 174 s | 205 s | 235 s | 244 s |

- **AMD is 1.16× Graviton4 and Intel 1.12×.** At 35% and 17% more per hour, AMD costs 17% more per
  assembly and Intel 5% more. Graviton4 is the cheapest on both compute and billed.
- **Most of the gap is K21 and K33**, where Graviton4 is 1.2–1.4× slower. From K77 up the three
  chips are within 13%. The six k-mer passes are all of the wall (1,181 of 1,182 s on Graviton4):
  2×300 reads put SPAdes on its long-read k ladder, which ends at 127.
- **SPAdes keeps about 4 of 8 CPUs busy on every chip.** Intel's 4 physical cores with SMT do as
  well as 8 full cores elsewhere. More vCPUs would mostly sit idle.
- **Peak RSS is the same on all three: 4.39 GiB** of anonymous memory (cgroup `anon`; SPAdes logs
  its own peak as 4,641 MB). The cgroup's `memory.peak` reads 10.8 GiB because it counts the page
  cache from the work directory.
- **The work directory peaks at 18.2–18.4 GB, then drops to 75 MB at the end.** It holds the
  per-k graphs and the k-mer index spill. On the 8 GiB `/tmp` tmpfs of a 16 GiB box SPAdes fails at
  K77 with `No space left on device`. So the spec puts `-o` on the container's root disk
  (`disk_gib: 60`) and keeps `/tmp` for staging, which peaked at 0.33 GiB.
- **Boot and staging are only 43–60 s** of a 1,066–1,242 s bill (4–5%), so billed tracks compute.

## What held at scale, on all three chips

| observable | assertion | observed (all three) |
|---|---|---|
| staged reads | pairs == ENA `read_count`, bases == ENA `base_count` | 760,683 / 450,083,225 |
| reference | md5 == NCBI's `md5checksums.txt`; one 4,641,652 bp sequence | ok |
| completion | `spades.py` exit 0 **and** `SPAdes pipeline finished` in `spades.log` | ok |
| genome fraction | ≥ 95% of U00096.3 (QUAST) | **98.599%** |
| mismatches / indels per 100 kbp | ≤ 10 each | **1.11 / 0.17** (51 mismatches, 8 indels) |
| total length (≥ 500 bp) | within 5% of 4,641,652 | 4,589,418 |
| **`contigs.fasta`** | **byte-identical on all three chips** | `fa4a10ef…` on all three, 1 distinct |

Also observed, not asserted: 228 contigs (83 ≥ 500 bp), N50 178,400, NGA50 176,019, largest
alignment 414,105, duplication ratio 1.000, 2 misassemblies (1 relocation, 1 inversion), and
10,830 bp unaligned.

**Nothing differed across chips.** An assembly is a deterministic integer computation at a fixed
`-t`, with no floating-point path to drift, so the three builds give the same bytes. That includes
`scaffolds.fasta` (`c1829bc7…`). The recipe's fixture showed the same at `-t 4` on 400 kb. This run
shows it on a whole genome at `-t 8`, with Graviton4 against both x86 vendors. The bounds against
the reference are loose on purpose: a resequenced MG1655 lab stock is not the 1997 reference
strain. 51 mismatches in 4.6 Mb is about 1 per 90 kb, which is what a real assembly against a
closely related reference looks like.

## Caveats

- n = 1 per chip. The tool is deterministic, as the identical bytes on three chips show, so
  the numbers are the assembly's and the timings are the only thing a second run could move.
- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- SPAdes' `--isolate` logs `Mode: ONLY assembling (without read error correction)`, so this run
  does not include BayesHammer. A default-mode run would add that step.

## Files

`stage-inputs.sh` (RODA copy + `00-prep`), `prep.sh`, `assemble.sh`, `evaluate.sh`, `mkspec.py`
(writes the three specs), `pins.sha256`. Per-chip evidence is in `results/<chip>/`, the QUAST
report and cross-chip comparison in `results/evaluate/`, and the staging checks in `results/prep/`.
