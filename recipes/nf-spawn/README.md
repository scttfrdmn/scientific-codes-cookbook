---
tool: nf-spawn
tool_version: 0.10.1
shape: pipeline
depends_on: stage:mafft
images:
  mafft: quay.io/aarchbio/mafft@sha256:f23e4545b6c186ffa31ebbb0a70a051c06ff3e7dcc91853e84f6eced74fa3df9
  muscle: quay.io/aarchbio/muscle@sha256:ecfe0f7405a5e3e1237b93202c35bd984aab96e1a3466ef64a6fd0a3b7d5c2e4
  iqtree: quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# nf-spawn — a Nextflow workflow whose rules dispatch as spawn tasks (Shape F)

Hands a whole **Nextflow DAG** to the `nf-spawn` executor, which dispatches each process to its *own* ephemeral instance, data moving between them through an S3 work dir. For anyone running a workflow engine on spore.host.

> **What this proves, and what it doesn't.** That the Shape-F path works on Graviton: engine-controlled per-rule dispatch, which a hand-launched sequence (bwa-samtools, salmon, star) cannot show. Not a benchmark, and **no hard cross-code topology claim** — see the RF observation.

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
export COOKBOOK_BUCKET=$(make print-bucket) AWS_REGION=$(aws configure get region)
cd recipes/nf-spawn && JAVA_HOME=/path/to/jdk17 nextflow run main.nf -c nextflow.config
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 5-task fan-out+join DAG (2 aligners → 2 trees → join) | your own `main.nf` processes | the DAG *shape* is the fixture — it exercises concurrent dispatch and a multi-input join, which a chain wouldn't. |
| the `nf-spawn` executor, **installed from a release zip** | keep the zip install | **load-bearing:** the plugin is *not* in the Nextflow registry ([`#90`](https://github.com/spore-host/nf-spawn/issues/90)), so `nextflow run` won't fetch it — install it first (below), or every process fails to launch. |
| the pinned mafft/muscle/iqtree images | your tools' images | reused byte-for-byte from [mafft](../mafft/README.md)/[muscle](../muscle/README.md)/[iqtree](../iqtree/README.md); each process names its own single-tool image. |
| `ext.region` read from `AWS_REGION` | — | **leave it derived.** It must equal the region holding `COOKBOOK_BUCKET`, and the DAG goes green either way — see below. |

## Shape, size, cost

Shape F costs **per rule × job count**, not one flat instance: five instances here (3× `c8g.large`, 2× `c8g.xlarge` for the trees — each process keeps the instance type from its standalone recipe, [iqtree](../iqtree/README.md)'s `c8g.xlarge` sized for its `-T 4`), **~$0.12 worst case** — each pays boot + pull overhead separately. **These timings are not compute cost**; see [data movement](../../patterns/data-movement.md) for when per-rule boots and S3 handoffs beat one bigger box.

<details>
<summary>As shipped: per-stage identities, the RF observation, the executor findings, install, run + verify</summary>

### Per-stage identities (asserted — a bad stage fails the DAG)

- **MAFFT / MUSCLE — residue conservation.** 114 sequences out, ungapped residues == 49098 (an aligner inserts gaps but preserves every residue). Same identity as standalone [mafft](../mafft/README.md)/[muscle](../muscle/README.md).
- **TREE — a valid ML tree.** 114 tips and a finite negative log-likelihood (LG+G4, `-T 4`, fixed seed).

### The mafft-vs-muscle topology is an OBSERVATION, not an assertion

The obvious cross-code check — "both alignments give the same tree" — does not hold, and the reason is instructive. Robinson-Foulds distance between the two ML trees is small but **unstable**: measured **26** between plain ML trees but **4** between UFBoot trees, *same alignments, same seed*. RF confounds the alignment-method difference (the signal) with iqtree's own ML-search stochasticity (the noise floor — [thread order changes the local optimum](../iqtree/README.md)), and the noise is as large as the signal. A hard RF assertion would pass or fail for a reason unrelated to alignment correctness — [the compare-like-with-like trap](../../practices/cross-checks.md). So the recipe **reports** RF with the confound named and asserts nothing on it; a confident-split comparison is a real phylo investigation, out of recipe scope.

### Two findings from the verifying run, both of which pass green

**The region was hardcoded to a region the bucket is not in.** `ext.region = 'us-east-1'` had been
left behind when the cookbook's bucket moved to `us-west-2`, so every one of the five instances
pulled its inputs and pushed its outputs across the continent. The run succeeded: five stages, all
five `.exitcode` objects `0`, the correct RF. **Nothing in Nextflow's summary or spawn's completion
records mentions the region at all** — this is the Shape-F version of a green check over the wrong
thing, and the only place it is visible is the executor's own submit line
(`Submitting task 'MAFFT (1)' … (c8g.large in us-east-1)`). Fixed by reading `AWS_REGION` from the
environment, with `main.nf` failing loudly if it is unset rather than defaulting to something that
was true once. Measured, both ways, same DAG:

| stage | instances in `us-east-1` | instances in `us-west-2` |
|---|---|---|
| MAFFT | 76 s | **70 s** |
| MUSCLE | 151 s | **135 s** |
| TREE (mafft) | 430 s | 430 s |
| TREE (muscle) | 296 s | 286 s |
| OBSERVE_RF | 89 s | **65 s** |
| **total wall** | **601 s** | **571 s** |

**Read that honestly: 5% overall, on 52 KB of sequence.** The penalty lands only on the
staging-dominated stages (OBSERVE_RF −27%, MUSCLE −11%) and the compute-bound tree stage is
*identical* at 430 s, which is what you would expect. So the reason to fix the region is not speed
at this size — it is that the penalty scales with intermediate size and adds cross-region egress,
both invisible here and neither reported anywhere. A recipe that quietly crosses regions teaches
the wrong default.

**Concurrent launches can lose the IAM race.** The first same-region attempt died at submit:
`CreateInstanceProfile … 409 ConcurrentModification: The previous tagging operation is still
ongoing`. MAFFT and MUSCLE are submitted 60 ms apart, both need the instance profile, and spawn's
`retryIAM` retries throttling but not `ConcurrentModification` — so one task failed and Nextflow
aborted the DAG. It is first-run-only (once the profile exists the window closes), which makes it
easy to mistake for a blip. Filed with the file:line chain and a one-line fix as
[spore-host/spawn#648](https://github.com/spore-host/spawn/issues/648); the local mitigation is
simply to re-run, since the profile now exists. The same report notes that spawn's cleanup message
then said *"the instance may still be running and billing until its TTL"* when nothing had been
created — `spawn list` confirmed zero leaked instances.

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
make stage RECIPE=mafft                       # the shared Pfam family (nf-spawn reuses it), into your bucket
export COOKBOOK_BUCKET=$(make print-bucket)   # main.nf + nextflow.config read this for the input, work dir, and output
export AWS_REGION=$(aws configure get region) # REQUIRED — where the instances launch; main.nf errors if unset
cd recipes/nf-spawn
JAVA_HOME=/path/to/jdk17 nextflow run main.nf -c nextflow.config
aws s3 ls "s3://$COOKBOOK_BUCKET/runs/nf-spawn/r3/"   # expect rf-observation.txt
```

**Verify from S3, not Nextflow's summary.** Recorded run (2026-10-03, spawn 0.115.0, instances in `us-west-2`): `completed=5, failed=0, cached=0`, all five `.exitcode` objects `0`, `rf-observation.txt` published with RF 26 — confirmed by reading the S3 objects, and `cached=0` confirms nothing was reused from an earlier attempt. That earned its place: an earlier failed run showed Nextflow `completed=1` while that task's S3 `.exitcode` was `126` with no output — the executor-path version of [exit code isn't proof](../../practices/container-path.md). Re-run: bump the `-r3` suffix in `nextflow.config`'s `workDir` and the `OBSERVE_RF` `publishDir`, or a stale work dir resumes cached tasks.

</details>
