---
tool: mafft
tool_version: 7.525
image: quay.io/aarchbio/mafft@sha256:f23e4545b6c186ffa31ebbb0a70a051c06ff3e7dcc91853e84f6eced74fa3df9
spawn_version: 0.104.0
---
# MAFFT — multiple sequence alignment

Align a set of sequences; the check confirms the alignment is faithful and reproducible.

## Run it

```bash
mafft --retree 2 --maxiterate 0 --thread 1 sequences.fasta > aligned.fasta
```

The recipe aligns a 114-protein Pfam family (FFT-NS-2) and verifies **residue conservation** — an aligner may only insert gaps, so ungapping every row must reproduce the input exactly.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 114-sequence Pfam family | your own sequences (FASTA) | reused byte-for-byte from [IQ-TREE](../iqtree/README.md) (gaps stripped) so it shares input with [MUSCLE](../muscle/README.md); a bigger family is a longer run, not a more legible one. |
| **`--retree 2 --maxiterate 0 --thread 1`** (FFT-NS-2) | MAFFT's iterative/accuracy modes (`--maxiterate 1000`, L-INS-i…) | **determinism scaffolding** — this progressive mode is deterministic (verified: byte-identical on rerun); iterative refinement and multi-thread can reorder and move the result, so if you change them, drop the exact-alignment assertion. |

**Leave the fixture:** a curated family aligns in ~1 s and every residue-conservation check is exact-or-wrong — the identity holds for any correct aligner regardless of algorithm. Leave-it.

## Shape, size, cost

One task, **~1 s** align. `c8g.large`, ~$0.02, **~50s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: why residue conservation not a column comparison, pins, smoke check</summary>

Two aligners on the same sequences produce **different alignments by design**, so comparing MAFFT's columns to another tool's (or to the original Pfam alignment) would fail for a reason unrelated to correctness — the [compare-like-with-like](../../practices/cross-checks.md) trap. The honest self-contained identity is **residue conservation**: ungapping each output row must return the exact input sequence and the total residue count is invariant — exact-or-wrong, method-independent.

| observable | assertion | observed |
|---|---|---|
| aligned sequences | exactly 114 (== input) | 114 |
| **residue conservation** | ungap(row) == input for every sequence | 114/114 |
| ungapped total | exactly 49098 residues | 49098 |
| rectangular / width ≥ longest input (440) | 1 row length / ≥440 | 1 (487) / 487 |
| deterministic | identical alignment on rerun | yes |

No bands — every check is exact. The genuine **cross-code** check (MAFFT vs [MUSCLE](../muscle/README.md): both alignments → same tree-builder → same topology, Robinson-Foulds = 0) is like-with-like and lives downstream in the [nf-spawn](../nf-spawn/README.md) Shape-F pipeline on these same bytes, not asserted here.

**Pins.** Image `quay.io/aarchbio/mafft@sha256:f23e4545b6c1…` (7.525, cosign-verified, `linux/arm64`). Input: 114 Pfam seed proteins with gaps stripped (`sha256:3adadccd…`, 49,098 residues) — *derived* from [IQ-TREE](../iqtree/README.md)'s alignment by a deterministic rule, the same bytes [MUSCLE](../muscle/README.md) aligns.

**Run + verify.**
```sh
spawn task run --spec recipes/mafft/01-align.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/mafft/r1/   # expect mafft_aln.fa, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
