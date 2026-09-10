---
tool: nf-spawn
tool_version: 0.10.1
shape: pipeline
images:
  mafft: quay.io/aarchbio/mafft@sha256:f23e4545b6c186ffa31ebbb0a70a051c06ff3e7dcc91853e84f6eced74fa3df9
  muscle: quay.io/aarchbio/muscle@sha256:ecfe0f7405a5e3e1237b93202c35bd984aab96e1a3466ef64a6fd0a3b7d5c2e4
  iqtree: quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7
spawn_version: 0.104.0
---
# nf-spawn — a Nextflow workflow whose rules dispatch as spawn tasks (Shape F)

The catalog's **first Shape-F recipe.** Every other recipe is one headless task on one box; this one runs a **Nextflow DAG** where each process step lands on its *own* ephemeral instance via the `nf-spawn` executor, and data moves between steps through an **S3 work dir** (each instance self-terminates before the next reads its output). That per-rule dispatch + cross-instance handoff is exactly what a single-task recipe can't demonstrate.

> **What this proves, and what it doesn't.** That the Shape-F path works on Graviton: a fan-out + join DAG dispatched per-rule to ephemeral instances, the executor detecting completion from the S3 work dir. It is **not** a benchmark and makes **no hard cross-code topology claim** (see the RF observation). Each stage asserts its own identity; the workflow asserts it *ran as a DAG across instances*.

## Run it

```text
          pfam_unaligned.fa (S3)
            /              \
        MAFFT            MUSCLE          ← fan-out: 2 aligners, 2 instances
          |                |
        TREE(mafft)      TREE(muscle)    ← 2 iqtree instances
            \              /
             OBSERVE_RF                  ← join: 1 instance
```

```bash
cd recipes/nf-spawn
JAVA_HOME=/path/to/jdk17 nextflow run main.nf -c nextflow.config
```

Five process tasks → five ephemeral instances. The `nextflow` head process runs locally and is free; the fan-out + join is the point — a linear chain would prove less (no concurrent dispatch, no multi-input join).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 5-task fan-out+join DAG (2 aligners → 2 trees → join) | your own `main.nf` processes | the DAG *shape* is the fixture — it exercises concurrent dispatch and a multi-input join, which a chain wouldn't. |
| the `nf-spawn` executor, **installed from a release zip** | keep the zip install | **load-bearing:** the plugin is *not* in the Nextflow registry ([`#90`](https://github.com/spore-host/nf-spawn/issues/90)), so `nextflow run` won't fetch it — install it first (below), or every process fails to launch. |
| the pinned mafft/muscle/iqtree images | your tools' images | reused byte-for-byte from [mafft](../mafft/README.md)/[muscle](../muscle/README.md)/[iqtree](../iqtree/README.md); each process names its own single-tool image. |

The trees pin `-T 4` + a fixed seed (iqtree's determinism scaffolding — [why](../iqtree/README.md)); the RF *observation* between the two is deliberately **not** asserted (below).

## Shape, size, cost

Shape F costs **per rule × job count**, not one flat instance: five instances here (3× `c8g.large`, 2× `c8g.xlarge` for the trees), **~$0.12 worst case** — each pays boot + pull overhead separately. **These timings are not compute cost**; see [data movement](../../patterns/data-movement.md) for when per-rule boots and S3 handoffs beat one bigger box.

<details>
<summary>As shipped: per-stage identities, the RF observation, the executor findings, install, run + verify</summary>

### Per-stage identities (asserted — a bad stage fails the DAG)

- **MAFFT / MUSCLE — residue conservation.** 114 sequences out, ungapped residues == 49098 (an aligner inserts gaps but preserves every residue). Same identity as standalone [mafft](../mafft/README.md)/[muscle](../muscle/README.md).
- **TREE — a valid ML tree.** 114 tips and a finite negative log-likelihood (LG+G4, `-T 4`, fixed seed).

### The mafft-vs-muscle topology is an OBSERVATION, not an assertion

The obvious cross-code check — "both alignments give the same tree" — does not hold, and the reason is instructive. Robinson-Foulds distance between the two ML trees is small but **unstable**: measured **26** between plain ML trees but **4** between UFBoot trees, *same alignments, same seed*. RF confounds the alignment-method difference (the signal) with iqtree's own ML-search stochasticity (the noise floor — [thread order changes the local optimum](../iqtree/README.md)), and the noise is as large as the signal. A hard RF assertion would pass or fail for a reason unrelated to alignment correctness — [the compare-like-with-like trap](../../practices/cross-checks.md). So the recipe **reports** RF with the confound named and asserts nothing on it; a confident-split comparison is a real phylo investigation, out of recipe scope.

### The executor findings (all filed here, all fixed in v0.10.1)

This project's use first exercised the Shape-F path, and drove the release: `#90` (not in the plugin registry — still zip-install, but the README no longer implies otherwise), `#91` (zip shipped a wrong manifest — fixed), `#92` (the blocker: docker-socket permission → every process exit 126 — fixed; 0.10.1 runs `completed=5` with no workaround). Verified by re-running on 0.10.1 with the mitigations removed, not by reading the changelog. **Termination is fine** — a just-completed instance showing `up` for a minute or two is the bounded `spored` self-termination tick, not a leak (`#96`, closed not-reproducible); TTLs are the backstop.

### Install (zip-based — `#90`)

```sh
curl -sSL -o /tmp/nf-spawn-0.10.1.zip \
  https://github.com/spore-host/nf-spawn/releases/download/v0.10.1/nf-spawn-0.10.1.zip
mkdir -p ~/.nextflow/plugins/nf-spawn-0.10.1
unzip -o /tmp/nf-spawn-0.10.1.zip -d ~/.nextflow/plugins/nf-spawn-0.10.1/
export JAVA_HOME=/path/to/jdk17    # Nextflow 26.04.x needs JDK 17+
```

### Run + verify

```sh
cd recipes/nf-spawn
JAVA_HOME=/path/to/jdk17 nextflow run main.nf -c nextflow.config
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/nf-spawn/r1/   # expect rf-observation.txt
```

**Verify from S3, not Nextflow's summary.** Recorded run: `completed=5, failed=0`, all five `.exitcode` objects `0`, `rf-observation.txt` published (RF 26) — confirmed by reading the S3 objects. That earned its place: an earlier failed run showed Nextflow `completed=1` while that task's S3 `.exitcode` was `126` with no output — the executor-path version of [exit code isn't proof](../../practices/container-path.md) (spawn#561's shape). Re-run: bump the `-r1` suffix in `nextflow.config`'s `workDir` and the `OBSERVE_RF` `publishDir`, or a stale work dir resumes cached tasks.

</details>
