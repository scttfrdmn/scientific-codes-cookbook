---
tool: macs2
tool_version: 2.2.9.1
image: quay.io/aarchbio/macs2@sha256:ca577fd2e65087538f4d51c3abf263453c9ec1485621d0e12f3a7822c5ab7a92
spawn_version: 0.104.0
---
# MACS2 — ChIP-seq peak calling

Call enriched peaks from a ChIP-seq treatment against its matched input control.

## Run it

```bash
macs2 callpeak -t treatment.bam -c control.bam -f BAM -g hs -n out
```

The recipe calls peaks from a CTCF ChIP-seq treatment **against its matched input control** — the enrichment model, how MACS2 is actually run — and asserts a real enriched peak set on chr20 (exactly 1390 peaks).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the CTCF ChIP + matched input (chr20 subsets) | your own treatment + control | a real ChIP experiment with a control, **not** the WGS fixture the other recipes reuse — see below. |
| **`-c control.bam`** | never drop it for real work | **load-bearing.** The control is what makes the call treatment-vs-input *enrichment* rather than peaks-vs-background; without it the result is a property of threshold luck, not enrichment. |
| `-g hs`, `-f BAM` | your genome size; `-f BAMPE` for paired-end | these reads are single-end (`-f BAM`); MACS2's model is otherwise on defaults (tag size 76 bp, fold [5,50], q<0.05). |

**Leave the fixture — and note *why* it isn't the shared WGS fixture:** run on flat WGS coverage MACS2 finds **0 peaks** (correct — no enrichment — but a degenerate identity that would pass even if peak-calling were broken). Peak-calling only means something on data with real enrichment, so this recipe deliberately uses a genuine CTCF ChIP+control. That's the honest fixture; a whole-genome ChIP is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, seconds of compute on ~2.2M chr20 reads. `c8g.large`, ~$0.02, **~80s** wall — boot and image pull ([why](../../practices/container-path.md)).

<details>
<summary>As shipped: the exact peak count (and why it's exact not a band), pins, smoke check</summary>

MACS2 `callpeak -t CTCF -c input -f BAM -g hs` on chr20 calls **1390** peaks — **bit-identical across architectures** (exactly 1390 on local Apple arm64 *and* Graviton4). The recipe first asserted a wide 1000–2000 band on the theory that cross-arch floating-point at the q<0.05 cutoff might shift borderline peaks; the Graviton run returned 1390 exactly, so that jitter was hypothetical. The assertion is now **exact (`== 1390`)**, the strongest honest form. (What made the *first* band legitimate wasn't a tolerance — it was a correct-vs-degenerate discriminator, catching the 0-peak WGS failure mode, which the exact count now does more sharply.)

| observable | assertion | observed |
|---|---|---|
| **peaks** | exactly 1390 (CTCF chr20 vs input; bit-identical local + Graviton) | 1390 |
| enrichment real | ≥ 1000 (not the degenerate 0-peak WGS case) | yes |

**Pins.** Image `quay.io/aarchbio/macs2@sha256:ca577fd2e650…` (2.2.9.1, cosign-verified, `linux/arm64`). Treatment: ENCODE **ENCFF933NSJ** (CTCF ChIP, HCT116, GRCh38) chr20 subset (`sha256:32db48ec…`, 864,347 reads); control: ENCODE **ENCFF768XTH** (matched input) chr20 subset (`sha256:a33c376a…`, 1,297,910 reads). Both range-subset to chr20 and sha256-pinned — a reusable matched pair for any peak-caller comparison.

**Run + verify.**
```sh
spawn task run --spec recipes/macs2/01-callpeak.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/macs2/r1/   # expect peaks.narrowPeak, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
