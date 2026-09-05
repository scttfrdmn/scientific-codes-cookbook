# MAFFT — multiple sequence alignment, validated by residue conservation

One task. MAFFT aligns 114 protein sequences and the smoke check confirms an exact,
method-independent identity: an aligner may only *insert gaps*, never alter or drop a
residue — so ungapping every aligned row must reproduce the input exactly.

> **What this recipe does and does not cover.** It aligns one 114-sequence protein family
> and checks the alignment is faithful and reproducible — enough to prove MAFFT works on
> Graviton4. Not a benchmark; no large alignment or profile/structural mode.

## Why residue conservation, not a column comparison

Two aligners on the same sequences produce **different alignments by design** — that is what
different algorithms do — so comparing MAFFT's columns to another tool's (or to the original
Pfam alignment) would fail for a reason unrelated to correctness, exactly the trap the
CLAUDE.md "compare like with like" rule names. The honest self-contained identity is
**residue conservation**: for every sequence, stripping the gaps from MAFFT's output row
must return the exact input sequence, and the total residue count must be preserved. That is
an exact-or-wrong fact any correct aligner satisfies, independent of algorithm.

The **cross-code** check against MUSCLE lives downstream and is genuinely like-with-like:
both alignments, fed to the same tree-builder, must yield the same topology (Robinson-Foulds
= 0). That comparison is demonstrated by the `recipes/nf-spawn` Shape-F pipeline (sequences →
aligner → IQ-TREE), not asserted here.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/mafft@sha256:f23e4545b6c186ffa31ebbb0a70a051c06ff3e7dcc91853e84f6eced74fa3df9` |
| | tag `7.525--h8a409c4_1`, MAFFT v7.525, cosign-verified (`sign-existing.yml@refs/heads/main`), `linux/arm64` |
| input | `inputs/mafft-muscle/pfam_unaligned.fa` — 114 proteins, 49,098 residues |
| | `sha256:3adadccdf1c0af2d2931b7d6dae68355c4df5ac2cff3905cb5c251bf1d78d43f` |

**Data tier: derived from a staged input, pinned.** The sequences are the gaps-stripped
records of `recipes/iqtree`'s `pfam38.2_seed_alignment.fa` — identical residues, columns
removed — so MAFFT and MUSCLE re-align the *same bytes* and the downstream tree comparison is
valid. `recipes/bcftools/stage-inputs.sh`-style provenance: strip `-`/`.` from the Pfam seed
alignment; that derivation is deterministic, so the sha256 is a real pin.

## Smoke check

Measured in the pinned image. FFT-NS-2, single thread (`--retree 2 --maxiterate 0 --thread 1`)
— chosen because it is deterministic.

| observable | assertion | observed |
|---|---|---|
| aligned sequences | exactly 114 (== input) | 114 |
| **residue conservation** | ungap(row) == input for every sequence → 114 | 114 |
| ungapped total | exactly 49098 (residues preserved) | 49098 |
| rectangular | 1 distinct row length | 1 (487) |
| width ≥ max input | alignment width ≥ longest input (440) | 487 ≥ 440 |
| **deterministic** | identical alignment on rerun | YES |

No bands — every check is exact.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The alignment (and the
determinism rerun) is **~1 s** each.

**These timings are not compute cost.** Boot, the Docker install, and pulling the MAFFT image
are the whole task. The recorded run's window was **50s** (18:33:50 → 18:34:40 UTC), 114/114
residue conservation, deterministic. TTL/cap already minimal; no retighten needed. Disk is trivial.

## Running it

Input is staged by the mafft-muscle prep (gaps stripped from the iqtree Pfam alignment). Then:

```sh
spawn task run --spec recipes/mafft/01-align.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/mafft/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
two objects (`mafft_aln.fa`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
