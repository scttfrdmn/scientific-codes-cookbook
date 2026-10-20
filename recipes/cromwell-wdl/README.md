---
tool: cromwell
tool_version: "0.40"
image: quay.io/aarchbio/cromwell@sha256:0d86efb43d8df392362fb54c0ef1ea2a6449b776cabe5089dd1b5a0cafce230e
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/cromwell@sha256:71738c6e849f45197f746e5aeb9a80d607498ae48d87ebb923ab61faf8f5199b
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Cromwell — running WDL, checked by an identity the engine can't fake

Cromwell executes a scatter-gather WDL workflow on Graviton4 and is checked against a closed-form arithmetic result. For anyone whose pipelines are written in WDL — including GATK's published best-practice workflows.

> **What this covers.** WDL draft-2 on Cromwell's local backend, one scatter of 8 shards and a gather. The workflow is trivial on purpose: the subject under test is the **engine**, not the arithmetic. The real use — Broad's published GVCF workflow over a whole chromosome, with call caching — is measured below.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=cromwell-wdl)" --wait
womtool validate sumcheck.wdl          # static check first
cromwell run sumcheck.wdl -m meta.json # local backend
```

## Which box — [measured](../../measurements/cromwell-wdl-real/README.md) on Broad's GVCF workflow, NA12878 chr20 at 36× (16 shards)

| chip | instance | workflow wall | slowest shard | $/hr | **billed $/result** |
|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 5,089 s | 4,999 s | 0.9426 | 1.357 |
| Xeon 6975P-C | `r8i.4xlarge` | 507 s | 452 s | 1.1114 | 0.190 |
| **EPYC 9R45** | `r8a.4xlarge` | **396 s** | **314 s** | 1.2781 | **0.179** |

**Take AMD; the engine is not the cost.** Cromwell adds about a minute, reproduces all 17 task outputs byte for byte against the same commands run without it, and its call cache reruns the workflow in 38 s, re-running exactly the calls whose inputs changed. The wall is GATK's: Java-only PairHMM on Graviton leaves one pericentromeric shard running 83 minutes while 15 cores idle, so on Graviton split that region finer.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `sumcheck.wdl` (8 shards, sums 1…1000) | your own WDL — e.g. a [GATK best-practice](../gatk4/README.md) workflow | those ship as WDL, which is the reason this recipe exists; the engine is the same, only the tasks change. |
| Cromwell's local backend | a real backend (`-Dconfig.file=…`) | the local backend runs each task on one box. A task with `runtime { docker }` needs the Docker socket, which a spawn container task does not expose: run Cromwell from a host task, as the [measurement](../../measurements/cromwell-wdl-real/README.md) does. Distributing tasks across instances is what [nf-spawn / the workflow adapters](../../patterns/execution-shapes.md) do, and spawn ships a `miniwdl-spawn` adapter for WDL specifically. |
| WDL draft-2 | `version 1.0` | bioconda's `cromwell` 0.40 is Cromwell 40, which ran Broad's `version 1.0` GVCF workflow unmodified; newer WDL features may need a newer Cromwell than this image provides. |

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

### On x86_64

The same task on same-generation Intel (`c8i.large`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/cromwell@sha256:71738c6e…` (0.40, `linux/amd64`; not
cosign-signed, so the digest is the pin):

| | arm64 | x86_64 |
|---|---|---|
| shards materialised | 8 | 8 |
| per-shard partials | identical to closed form | identical to closed form |
| gathered total | 500500 | 500500 |
| `smoke-check.txt` | `a2103ca2…` | `a2103ca2…` — **byte-identical** |

The workflow's tasks declare an empty `runtime { }`, so Cromwell's local backend runs every shard
as a subprocess inside the one pinned image — the x86 run pulls no container but the Cromwell
pin, and the shards' `seq`/`awk` are the x86 image's own.

### Run + verify

```sh
make run RECIPE=cromwell-wdl
make run RECIPE=cromwell-wdl ARCH=x86_64   # same spec, x86 pin, writes runs-x86_64/
make ls  RECIPE=cromwell-wdl
```

Assertions are `test` and `grep` checks inside the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `shards_materialised 8`, `partials_identical yes` and `gathered_total 500500`.

</details>
