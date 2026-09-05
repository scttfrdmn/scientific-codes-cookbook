# MUSCLE — multiple sequence alignment, checked by residue conservation

One task. MUSCLE v5 aligns 114 protein sequences (a Pfam seed family with its gaps
stripped) and the smoke check confirms an exact, method-independent identity: the
alignment inserts only gap columns, so ungapping every row reproduces the input
sequences exactly.

> **What this recipe does and does not cover.** It aligns one small protein family and
> verifies the alignment conserves every input residue — enough to prove MUSCLE v5 runs
> and produces a valid MSA on Graviton4. Not a benchmark; no large alignment or profile work.

## Why residue conservation, not a column comparison

Two aligners on the same sequences produce **different alignments by design** — that is what
different algorithms do — so comparing MUSCLE's columns against MAFFT's (or against the Pfam
seed) would fail for a reason unrelated to correctness (CLAUDE.md: a cross-code check must
compare like with like). The honest per-tool identity is one every correct aligner must
satisfy: an MSA may only **insert gap columns**, never add, drop, or alter residues — so
ungapping each aligned row must recover the exact input sequence, and the total residue count
is invariant. Exact-or-wrong, method-independent, no band.

The MAFFT↔MUSCLE cross-code question *is* worth asking — it's just a **downstream** one:
both alignments fed to the same tree-builder should recover the same topology (Robinson-Foulds
= 0). That check lives in `recipes/nf-spawn` (the Shape-F pipeline), where it belongs, on the
same input bytes this recipe uses.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/muscle@sha256:ecfe0f7405a5e3e1237b93202c35bd984aab96e1a3466ef64a6fd0a3b7d5c2e4` |
| | tag `5.3--h163da20_3`, MUSCLE 5.3, cosign-verified (`sign-existing.yml@refs/heads/main`), `linux/arm64` |
| input | 114 Pfam seed sequences, gaps stripped — `sha256:3adadccdf1c0af2d2931b7d6dae68355c4df5ac2cff3905cb5c251bf1d78d43f` |

**Data tier: derived from `recipes/iqtree`'s staged Pfam seed alignment**, gaps removed, and
pinned. The **same bytes** feed `recipes/mafft` — that shared input is what makes their
downstream comparison valid; nothing is re-staged per aligner.

## Smoke check

Measured in the pinned image (`--user 1000:1000`).

| observable | assertion | observed |
|---|---|---|
| sequences | exactly 114 | 114 |
| alignment length | ≥ longest input (440) | 478 |
| **residue conservation** | ungap(row) == input for all; total residues == 49098 | 49098 |
| determinism | re-run byte-identical | identical |

No bands — residue conservation is exact, and MUSCLE v5 is deterministic on this input.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The alignment is **~17 s**
locally, no memory pressure (fits well under a 4 GiB box — MUSCLE v5 was fine here, unlike
`recipes/kallisto`).

**These timings are not compute cost.** Boot, the Docker install, and pulling the MUSCLE
image are most of the task. The recorded run's window was **115s** (18:33:52 → 18:35:47 UTC),
114/114 residue conservation — MUSCLE 5.3 is heavier than MAFFT but comfortable in 4 GiB. TTL 5m
holds (≈2× the window); cap already minimal. Disk is trivial.

## Running it

Input comes from `recipes/iqtree` (gaps stripped); it's already staged. Then:

```sh
spawn task run --spec recipes/muscle/01-align.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/muscle/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
two objects (`aln.fa`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
