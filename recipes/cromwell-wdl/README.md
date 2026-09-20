---
tool: cromwell
tool_version: "0.40"
image: quay.io/aarchbio/cromwell@sha256:0d86efb43d8df392362fb54c0ef1ea2a6449b776cabe5089dd1b5a0cafce230e
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Cromwell — running WDL, checked by an identity the engine can't fake

Cromwell executes a scatter-gather WDL workflow on Graviton4 and is checked against a closed-form arithmetic result. For anyone whose pipelines are written in WDL — including GATK's published best-practice workflows.

> **What this covers.** WDL draft-2, Cromwell's local backend, one scatter of 8 shards and a gather. The workflow computes something trivial on purpose: the subject under test is the **engine**, not the arithmetic. Not Cromwell's server mode, call caching, or a cloud backend.

## Run it

```bash
womtool validate sumcheck.wdl          # static check first
cromwell run sumcheck.wdl -m meta.json # local backend
```

```wdl
scatter (i in range(shards)) {
  call partial { input: lo = i*per + 1, hi = (i+1)*per }
}
call gather { input: parts = partial.sum }
```

One task: validate the WDL, run it, then verify the shards and the gathered total independently.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `sumcheck.wdl` (8 shards, sums 1…1000) | your own WDL — e.g. a [GATK best-practice](../gatk4/README.md) workflow | those ship as WDL, which is the reason this recipe exists; the engine is the same, only the tasks change. |
| Cromwell's local backend | a real backend (`-Dconfig.file=…`) | the local backend runs each task as a shell command on one box. Distributing tasks across instances is what [nf-spawn / the workflow adapters](../../patterns/execution-shapes.md) do, and spawn ships a `miniwdl-spawn` adapter for WDL specifically. |
| WDL draft-2 | `version 1.0` | Cromwell 0.40 is from 2018 and is what bioconda currently packages; newer WDL features may need a newer Cromwell than this image provides. |

**Leave the fixture:** a trivial computation with a known closed form is precisely what makes engine failures visible — if a shard is dropped or a gather mis-collects, the number is wrong and nothing else has to be interpreted. **Scale it** to your real workflow once you trust the engine.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 15m, cap $0.05. Recorded window **1m37s**, nearly all of it JVM startup and Cromwell's own initialisation rather than the workflow. **These timings are not compute cost.**

<details>
<summary>As shipped: three layered checks, why the total alone isn't enough, the WDL brace trap, pins</summary>

### Three checks, layered so each catches what the others miss

| observable | assertion | observed |
|---|---|---|
| static validation | `womtool validate` succeeds | Success! |
| scatter materialised | exactly **8** shard execution directories exist | 8 |
| per-shard results | each shard's partial sum equals an **independently computed** value | identical |
| gathered total | `== n(n+1)/2` for n=1000 | **500500** |

**Why the total alone would be a weak check.** 500500 is the right answer, but it is also the right answer if two shards *swapped* their input ranges — the sum is invariant to that permutation. So the recipe also compares the **set of per-shard partials** (`7875 23500 39125 54750 70375 86000 101625 117250`) against the same values computed independently in awk. That catches mis-sharding a correct total would hide, and counting the shard directories catches a scatter that silently collapsed to one job. Three different failure modes, three different checks — the same reasoning as [asserting the claim you mean](../../practices/cross-checks.md).

The arithmetic is deliberately trivial. Cromwell cannot get `sum(1..1000)` wrong; what it *can* get wrong is fanning out, passing the right inputs to each shard, and collecting all the results. Those are the things under test.

### Two traps recorded, both cost a run

**WDL brace matching.** A `command { … }` block containing `awk '{s+=$1} END{print s+0}'` fails to parse — `Unrecognized token`, pointing at the awk. WDL matches braces to close the command block, and awk's braces confuse it. **Use the heredoc form `command <<< … >>>`**, which is why every command block here is written that way.

**Cromwell prints its outputs block twice.** Parsing `"sumcheck.total"` out of the log with a plain `awk` match captured *both* copies, giving a two-line variable; `test "$TOTAL" -eq …` then failed with a shell syntax error (exit 2) while every actual check had passed. The fix is `awk '…{print; exit}'` — safe here because awk is reading a **file**, so there is no upstream writer to send SIGPIPE, unlike the `| head` form that this project has been bitten by twice.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchbio/cromwell@sha256:0d86efb4…` (Cromwell 0.40, OpenJDK 1.8.0_472, `womtool` included) |
| input | none — the WDL is written in-task |

**Version note:** bioconda's `cromwell` is at **0.40** (2018) while upstream Cromwell is far newer. That is a packaging lag, not a choice — worth knowing before writing WDL that depends on recent features. Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before `cromwell` is callable.

### Run + verify

```sh
make run RECIPE=cromwell-wdl
make ls  RECIPE=cromwell-wdl
```

Assertions are `test` and `grep` checks inside the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `shards_materialised 8`, `partials_identical yes` and `gathered_total 500500`.

</details>
