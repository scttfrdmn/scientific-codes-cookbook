---
tool: racon
tool_version: "1.5.0"
images:
  - quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
  - quay.io/aarchbio/racon@sha256:75020311bdb6a635ee67718984db28029e95ee2272e32de9d0def8a9826ed4ee
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Assembly polishing — errors we put in, taken back out

racon rebuilds a consensus on Graviton4 from reads aligned to a draft, recovering a sequence whose errors were planted before the reads existed. The catalog's first polisher, for anyone finishing a long-read assembly.

> **What this covers.** A 10 kb truth sequence, a draft carrying 20 substitutions and 3 deletions, 27× 1 kb error-free reads, minimap2 overlaps and one racon round. Not real long-read error profiles, multiple rounds, `medaka`'s neural consensus, or diploid/heterozygous polishing.

## Run it

```bash
minimap2 -x map-ont draft.fa reads.fq > ov.paf
racon -t 2 reads.fq ov.paf draft.fa > polished.fa
```

Two tasks: minimap2 builds the fixture and aligns the reads to the draft, racon polishes and the result is compared to the truth byte for byte.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| planted-error draft | your assembly (e.g. from [flye](../flye/README.md) or [spades](../spades/README.md)) | real drafts have no answer key, so the check becomes *directional* — see below. |
| error-free reads | your real reads | a consensus cannot beat its evidence: exact recovery here is a property of perfect reads, not of racon. |
| one round | two to four rounds | racon is normally run iteratively, re-aligning between rounds. Each round needs a fresh PAF. |
| `-x map-ont` | `map-pb`, `map-hifi` | match your chemistry; the preset changes which overlaps minimap2 reports and therefore what racon sees. |

**Leave the fixture:** planted errors and perfect reads make "the polished sequence *is* the truth" an exact assertion instead of a plausibility check. **Scale it** to a real draft when you want to measure how much polishing actually helps.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 12m each, caps $0.05 each. Alignment and polishing are each about a second at this size; the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact recovery, why 780 is not an error count, racon crashes on Apple Silicon and not on Graviton, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture | truth 10000 bp, draft short by exactly the 3 deletions | **10000 / 9997** |
| draft ≠ truth | the fixture must actually plant something | **differs** |
| overlaps | ≥ one alignment per read | **273 for 273** |
| polished length | == truth | **10000** |
| polished sequence | **byte-identical** to the truth | **yes** |

```text
planted errors      20 substitutions + 3 deletions
draft   vs truth    780 positional mismatches
polished vs truth     0
```

### Why exact equality is the right assertion here — and would not be on real data

The reads are drawn from the truth with **no errors**, so every planted mistake in the draft is contradicted by ~27 reads that agree with each other. The consensus is therefore fully determined, and "polished == truth" is an identity rather than a hopeful threshold.

That is a property of the fixture, not a claim about racon's accuracy. On real noisy reads **a consensus cannot beat its evidence**: residual errors survive wherever the reads themselves are wrong or coverage is thin, and the honest assertion becomes *directional* — fewer differences after polishing than before, which is what the `780 → 0` pair would measure on real data. The recipe asserts the exact form because this fixture earns it, and says plainly what to assert instead when it does not.

### 780 is not 780 errors — and that is why the check is byte-identity

The draft has **23** planted errors, but a positional comparison against the truth reports **780 mismatches**. Both numbers are correct; they measure different things.

Three of those errors are **deletions**, the first at position 9000. Everything downstream of it is shifted one base out of register, so roughly three quarters of the ~1000 remaining bases disagree by chance:

```text
20 substitutions  +  ~3/4 × 1000 frame-shifted bases  ≈  770      observed 780
```

**Positional mismatch counting is meaningless across an indel.** It is reported here because the *ratio* is informative and because the arithmetic explains itself, but the assertion is `cmp` — exact byte equality — precisely so that it cannot be fooled by a frameshift in either direction. Anything looser would need a real alignment to be meaningful, which is a second tool for a question byte-identity already answers.

### racon crashes on Apple Silicon and runs fine on Graviton4

While developing this, `racon` died with **`Illegal instruction`** (SIGILL) under Docker Desktop on Apple Silicon — after `racon --version` printed normally, so the binary loads and fails inside the compute kernel. On Graviton4 the identical image, digest and input **complete cleanly and produce a byte-identical consensus**.

The CPU feature lists explain it:

```text
Graviton4       … sve sve2 sveaes svepmull svebitperm svesha3 svei8mm svebf16 i8mm bf16 …
Apple Silicon   asimd fphp
```

racon's alignment kernel (SPOA) is SIMD-accelerated, and the `linux-aarch64` build uses instructions **Apple Silicon does not implement**. So the crash is a host-feature mismatch, not a broken package — and nothing about it is filable upstream, because the target platform works.

The reason this matters beyond racon: the project already knows that [local Docker on macOS cannot prove uid or permission behaviour](../../practices/container-path.md). **CPU instruction set is a third dimension of the same trap**, alongside memory — a cgroup limit does not change `/proc/meminfo`, and an Apple Silicon host does not advertise SVE. A green local run proves the logic; it does not prove the binary will execute, fit, or be permitted on the real host. Here the failure was loud and pointed the wrong way: *away* from a tool that works.

Because a SIGILL produces no output at all, the racon task writes its version and the host's CPU features **before** polishing and copies racon's log to a flat path on exit, so a crash still delivers the evidence needed to tell a build problem from a host problem.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| minimap2 | `quay.io/aarchbio/minimap2@sha256:ef4a5fb7…` (2.31-r1302) — the same pin [minimap2](../minimap2/README.md) uses |
| racon | `quay.io/aarchbio/racon@sha256:75020311…` (1.5.0) |
| input | none — truth, draft and reads are generated in-task by awk from `srand(89)` |

racon takes the PAF, the reads and the draft as three plain files and writes FASTA to stdout, so nothing here needs a directory staged. `awk` defines its helper functions at top level rather than inside `BEGIN`, because not every `awk` accepts the latter.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in both tasks.

### Run + verify

```sh
make run RECIPE=polish
make ls  RECIPE=polish
```

Assertions are `test` and `cmp` calls inside both tasks. Expect `smoke-check.txt` with `byte_identical yes`, `polished_vs_truth 0`, and `lengths truth 10000 / draft 9997 / polished 10000`.

</details>
