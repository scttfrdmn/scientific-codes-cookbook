---
tool: mash
tool_version: "2.3"
image: quay.io/aarchbio/mash@sha256:abad0c5f4d3365661ffc5533bc6eb5f1bd07d773ea61b1abd1bfc00c1df813fe
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# mash — all-pairs distances over 20 bacterial genomes, in under a second

Sketches 20 complete RefSeq genomes and recovers all ten species from the distances alone. For anyone using MinHash to compare genomes at scale.

> **The compute here is sub-second; 99% of what you pay for is boot.** That is mash working as
> designed, and it is why this recipe has no instance-generation table — see below.

## Run it

```bash
make stage RECIPE=mash   # once: 20 RefSeq genomes, 10 species x 2 strains
spawn task run --spec "$(make -s spec RECIPE=mash)" --wait   # ~2 min billed, almost all of it boot
make ls    RECIPE=mash   # dist.tsv + smoke-check.txt

mash sketch -p 8 -k 21 -s 10000 -o all GCF_*.fna.gz
mash dist   -p 8 all.msh all.msh > dist.tsv      # 400 ordered pairs
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 20 RefSeq genomes | your genomes or assemblies | `mash sketch` takes FASTA or FASTQ; reads need `-r` so it corrects for coverage. |
| `-s 10000` | `-s 1000` (mash's default) | bigger sketches sharpen the Jaccard estimate; 10000 is what makes the [sourmash](../sourmash/README.md) comparison meaningful. |
| `-k 21` | `-k 31` for larger genomes | k must match on both sides of any cross-tool comparison, or you are comparing methods. |

**Scale it, and the input is the only thing that grows.** mash's whole point is that distance is
sketch-size work, not genome-size work — 20 genomes or 20,000, the per-pair cost is the same. Twenty
is what makes the check below sharp; it is not what makes mash interesting at your scale.

## Shape, size, cost

`c8g.2xlarge`: **sub-second of mash inside a 106 s billed window, $0.0094.**

**This recipe deliberately has no generation table.** Every other converted recipe here carries a
Graviton2→5 ladder, but mash's work is under a second, so a four-chip comparison would measure boot
and nothing else — a table of four near-identical numbers that says nothing about the chip. The
honest statement is the one above: for sketching at this scale the instance generation is irrelevant,
and the lever is batching more genomes into one boot rather than buying a faster core.

<details>
<summary>As shipped: 20 independent tests against NCBI's own labels, clean separation, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| genomes / species | 20 / 10, two strains each | **20 / 10** |
| distance rows | exactly 400 (20×20 ordered) | **400** |
| **self-distance** | **exactly 0** | **0.0000000000** (10000/10000 hashes) |
| **nearest neighbour** | **same species, 20 of 20** | **20 / 20** |
| max within-species | recorded | **0.047498** |
| **min between-species** | **> max within-species** | **0.158119** (3.3× margin) |

**The truth is NCBI's, not mash's.** Each genome's species label is parsed from its own FASTA
defline, so for every genome the nearest of the other nineteen must be its species' second strain —
20 independent tests, no band, and a result mash cannot influence. Self-distance 0 is the free
identity: a sketch compared with itself shares all 10,000 hashes by construction.

The separation check is the stronger claim. Nearest-neighbour only asks that the *closest* genome be
right; requiring **every** within-species distance below **every** between-species distance says the
ten clusters are cleanly resolved, with a 3.3× gap between the worst same-species pair (0.047) and
the closest different-species pair (0.158).

**Why these 20 genomes.** Ten genera, deliberately well separated. Close pairs — *E. coli* against
*Shigella*, the *Klebsiella* complex — would turn the nearest-neighbour test into a coin flip on real
biological ambiguity rather than a check on the tool, and a
[flaky check is worse than none](../../practices/cross-checks.md).

**The species label is derived, not written down, and that caught a real error.** An earlier version
of `stage-inputs.sh` carried hand-written labels beside each accession, and two of twenty were wrong:
`GCF_000008465.1` is *Idiomarina loihiensis*, not *Helicobacter pylori* J99, and `GCF_000215705.1` is
*Ramlibacter tataouinensis*, not a second *Listeria*. Both are real complete genomes of plausible
size that sketch fine and give sensible distances, so nothing downstream would have caught them —
four nearest-neighbour tests would simply have failed, looking like a problem with mash. Parsing the
label from the genome and asserting two-strains-per-species at staging time turns that into a loud
error before any compute is bought.

### Cross-checked against sourmash

[sourmash](../sourmash/README.md) sketches the same 20 genomes at the same k=21 and reproduces both
results — 20/20 nearest neighbours and clean separation against the same NCBI labels. On the ten
within-species pairs the two tools rank identically, Spearman **−1.0000** with no inversions
(negative because one reports similarity and the other distance). Full comparison, including why the
all-pairs rank statistic is the wrong metric, is on sourmash's page.

### Pins

| | data tier |
|---|---|
| mash | `quay.io/aarchbio/mash@sha256:abad0c5f4d33…` (2.3, cosign-verified, `linux/arm64`) |
| genomes | 20 RefSeq assembly accessions — versioned and immutable; `genomes20.tar` sha256 `6fa8884c45af0178…` |

A RefSeq accession like `GCF_000005845.2` is the durable id, but the FTP directory also carries the
assembly *name*, so `stage-inputs.sh` resolves that from NCBI's index rather than hardcoding
`ASM584v2` — a guessed path is how a staging script rots. The 20 genomes travel as one flat tar
because [a directory output cannot work on the container path](../../practices/container-path.md).

### Run + verify

```sh
make stage RECIPE=mash
make run   RECIPE=mash
make ls    RECIPE=mash
```

Expect `smoke-check.txt` with `nearest_same_sp 20`, `max_self_distance 0` and
`min_between_species` above `max_within_species`.

</details>
