---
tool: bedtools
tool_version: 2.31.1
image: quay.io/aarchbio/bedtools@sha256:cd1e72a29500369c5576c10e98b2c1723a09a73bde9fb50d80bf4022096b449b
spawn_version: 0.104.0
---

# bedtools — genome-interval set algebra

Merge, intersect, subtract, and cover intervals — the set algebra every genomics pipeline leans on.

## Run it

```bash
bedtools merge     -i a.bed
bedtools intersect -a a.bed -b b.bed
bedtools subtract  -a a.bed -b b.bed
bedtools genomecov -i a.bed -g genome.txt
```

The recipe runs these four on two small BED files. bedtools' interval algebra is deterministic and the answers are *defined* by the inputs, so on fixed intervals every output integer is exact — the same class of check as BLAST's self-hit or salmon's TPM sum, no band.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| two hand-built BED files on `chr1` | your own intervals / a real annotation set | the operations are identical at any scale; the small inputs are there so the answers can be checked by hand. |
| these four ops | `closest`, `coverage`, `flank`, `slop`, … | same binary, same shape — one interval file in, one out. |

Nothing is determinism scaffolding. **Leave the fixture small.** Hand-sized intervals are what make the result checkable by hand; a real annotation set would run the same operations and teach nothing extra about whether bedtools is correct. This is a leave-it, not a scale-it.

## Shape, size, cost

One task, sub-second. `c8g.large`, ~$0.02, **~47s** wall — boot and image pull, not bedtools ([a short task is mostly overhead](../../practices/container-path.md)).

<details>
<summary>As shipped: the hand-derived answers, the conservation identity, pins, smoke check</summary>

Two BED files (half-open coordinates): `a.bed` = `[0,100] [50,150] [200,300]`, `b.bed` = `[75,125] [250,350]`, genome length 400. Every answer is derivable by hand:

- **merge a** → `[0,100]∪[50,150]` collapse to `[0,150]`; `[200,300]` stands. **2 intervals, 250 bp.**
- **intersect a,b** → `[75,100] [75,125] [250,300]`. **3 intervals, 125 bp.**
- **subtract a,b** → `[0,75] [50,75]+[125,150] [200,250]`. **4 intervals, 175 bp.**
- **genomecov a** → depth-0 = 150 bp, depth-1 = 200 bp, depth-2 = 50 bp, and the three **sum to 400 = the genome length** — a conservation identity (every base counted at exactly one depth), the standout check because it falls out of the operation being correct rather than a threshold.

| observable | assertion | observed |
|---|---|---|
| merge / intersect / subtract intervals | 2 / 3 / 4 | 2 / 3 / 4 |
| merge / intersect / subtract bp | 250 / 125 / 175 | 250 / 125 / 175 |
| genomecov depth-0/1/2 bp | 150 / 200 / 50 | 150 / 200 / 50 |
| genomecov total | 400 = genome length (conservation) | 400 |

**Pins.** Image `quay.io/aarchbio/bedtools@sha256:cd1e72a29500…` (2.31.1, cosign-verified, `linux/arm64`). Inputs are inline in the task — nothing staged, nothing to pin.

**Run + verify.**
```sh
spawn task run --spec recipes/bedtools/01-setops.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/bedtools/r1/   # expect merge/intersect/subtract/genomecov + smoke-check.txt
```
Smoke check runs inside the task; the bucket listing is the second half. Re-running: bump the `-r1` suffix.

</details>
