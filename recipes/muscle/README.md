---
tool: muscle
tool_version: "5.3"
image: quay.io/aarchbio/muscle@sha256:ecfe0f7405a5e3e1237b93202c35bd984aab96e1a3466ef64a6fd0a3b7d5c2e4
spawn_version: 0.104.0
---
# MUSCLE — multiple sequence alignment

Align a set of sequences with MUSCLE v5; the check confirms the alignment conserves every residue.

## Run it

```bash
muscle -align sequences.fasta -output aligned.fasta
```

The recipe aligns a 114-protein Pfam family (the same bytes [MAFFT](../mafft/README.md) aligns) and verifies **residue conservation** — an MSA may only insert gap columns, so ungapping every row must recover the input sequences exactly.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 114-sequence Pfam family | your own sequences (FASTA) | reused byte-for-byte from [IQ-TREE](../iqtree/README.md) (gaps stripped) so it shares input with [MAFFT](../mafft/README.md) — that shared input is what makes their downstream tree comparison valid. |

MUSCLE v5 is deterministic on this input (verified: byte-identical on rerun) — **nothing here is determinism scaffolding**. **Leave the fixture:** the residue-conservation identity is exact-or-wrong for any correct aligner, and a bigger family is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, **~17 s** align — MUSCLE v5 is heavier than MAFFT but comfortable in 4 GiB. `c8g.large`, ~$0.02, **~115s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: why residue conservation not a column comparison, pins, smoke check</summary>

Two aligners on the same sequences produce **different alignments by design**, so comparing MUSCLE's columns against MAFFT's (or the Pfam seed) would fail for a reason unrelated to correctness ([compare like with like](../../practices/cross-checks.md)). The honest per-tool identity is one every correct aligner satisfies: ungapping each row recovers the exact input sequence, total residue count invariant — exact-or-wrong, no band.

| observable | assertion | observed |
|---|---|---|
| sequences | exactly 114 | 114 |
| alignment length ≥ longest input (440) | ≥440 | 478 |
| **residue conservation** | ungap(row) == input for all; total == 49098 | 49098 |
| determinism | re-run byte-identical | identical |

The MAFFT↔MUSCLE cross-code question *is* worth asking, but it's a **downstream** one: both alignments → same tree-builder → same topology (Robinson-Foulds = 0). That check lives in the [nf-spawn](../nf-spawn/README.md) Shape-F pipeline on these same input bytes.

**Pins.** Image `quay.io/aarchbio/muscle@sha256:ecfe0f7405a5…` (5.3, cosign-verified, `linux/arm64`). Input: 114 Pfam seed proteins with gaps stripped (`sha256:3adadccd…`, 49,098 residues) — *derived* from [IQ-TREE](../iqtree/README.md)'s alignment, the same bytes [MAFFT](../mafft/README.md) aligns.

**Run + verify.**
```sh
spawn task run --spec recipes/muscle/01-align.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/muscle/r1/   # expect aln.fa, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
