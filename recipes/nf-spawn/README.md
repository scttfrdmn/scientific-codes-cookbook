# nf-spawn — a Nextflow workflow whose rules dispatch as spawn tasks (Shape F)

The catalog's **first Shape-F recipe**. All 43 others are Shape B — one headless task on
one box. This one runs a **Nextflow DAG** where every process step lands on its *own*
ephemeral EC2 instance via the `nf-spawn` executor (→ `spawn task run`), and data moves
between steps through an **S3 work dir** (each instance self-terminates before the next
reads its output). That per-rule dispatch + cross-instance S3 handoff is exactly what a
single-task recipe cannot demonstrate.

> **What this recipe proves, and what it does not.** It proves the Shape-F path works on
> Graviton: a fan-out + join DAG dispatched per-rule to ephemeral instances, with the
> executor detecting completion from the S3 work dir. It is **not** a benchmark, and it
> makes **no hard cross-code topology claim** (see the RF observation below). Each stage
> asserts its own identity; the workflow asserts that it *ran as a DAG across instances*.

## The DAG, and why fan-out + join

```
          pfam_unaligned.fa (S3)
            /              \
        MAFFT            MUSCLE          ← fan-out: 2 aligners, 2 instances
          |                |
        TREE(mafft)      TREE(muscle)    ← 2 iqtree instances
            \              /
             OBSERVE_RF                  ← join: 1 instance
```

Five process tasks → five ephemeral instances. The fan-out + join is the point: a linear
chain would prove less about the executor (no concurrent dispatch, no multi-input join).

## Per-stage identities (asserted — a stage fails the task if wrong)

- **MAFFT / MUSCLE — residue conservation.** 114 sequences out; ungapped residues == 49098
  (an aligner must preserve every residue while inserting gaps). Same identity as the
  standalone `recipes/mafft` and `recipes/muscle`.
- **TREE — a valid ML tree.** 114 tips and a finite negative log-likelihood (LG+G4, fixed
  seed, `-T 4`).

## The mafft-vs-muscle topology: an OBSERVATION, not an assertion

The obvious cross-code check — "both alignments give the same tree" — **does not hold, and
the reason is instructive.** Robinson-Foulds distance between the two ML trees is small but
non-zero **and unstable**: measured **26** between the plain ML trees but **4** between the
UFBoot ML trees — *same alignments, same seed*. RF here confounds two things:

1. the **alignment-method** difference (mafft vs muscle) — the signal we'd want to test;
2. **iqtree's own ML-search stochasticity** — `recipes/iqtree` already documents that thread
   order / search path changes which local optimum it lands on, and `-B` changes that path.

The noise floor (2) is as large as the signal (1), so a hard RF assertion would fail (or
pass) for a reason unrelated to alignment correctness — the cross-code-metric trap
(CLAUDE.md's "compare like with like"). So the recipe **reports** the RF distance with the
confound named, and asserts nothing on it. A reader learns something true (two correct MSAs
give nearly-but-not-identical topologies, and why) without a claim that isn't. A proper
confident-split comparison (agreement on strongly-supported clades only) is a real phylo
investigation, not a recipe-sized check — deliberately out of scope.

## Pins

| | |
|---|---|
| executor | `nf-spawn@0.10.1` (see install note — **not** in the Nextflow plugin registry) |
| MAFFT | `quay.io/aarchbio/mafft@sha256:f23e4545…` (cosign-verified, `linux/arm64`) |
| MUSCLE | `quay.io/aarchbio/muscle@sha256:ecfe0f74…` (cosign-verified) |
| iqtree | `quay.io/aarchbio/iqtree@sha256:dc6d9f62…` (cosign-verified) |
| input | `inputs/mafft-muscle/pfam_unaligned.fa` (114 proteins, 49098 residues) — reused from `recipes/mafft`/`recipes/muscle` |
| Nextflow | 26.04.x (plugin built against 26.04.3) |

**Three nf-spawn findings, filed building this and all FIXED in v0.10.1** (this project's
findings drove the release — the workflow path had never been exercised before) — kept as
history, verified fixed by re-running on 0.10.1 with the workaround removed, not by reading the
changelog:
- `spore-host/nf-spawn#90` (README install) — the plugin still isn't in the Nextflow registry,
  but the upstream README no longer implies it is; install from the release zip (below).
- `#91` (manifest mismatch) — **fixed**: the v0.10.0 zip shipped a `0.8.0` manifest; the 0.10.1
  zip genuinely reports `Plugin-Version: 0.10.1` (re-verified from the downloaded asset).
- `#92` (the blocker — docker-socket permission → every process exit 126) — **fixed**: on 0.10.1
  the DAG runs `completed=5` with **no `ext.setup` socket workaround** and zero permission errors
  in any `.command.err`. Dropping the mitigation and watching it run clean is what confirms the
  fix is in the executor, not incidental.

**Still open — `#96` (instance termination at DAG scale).** The terminal task's instance lingers
after a clean run (the join, both on 0.8.0 and 0.10.1), and a failed task's instance didn't
self-terminate at all. Not part of the #92 fix. **Verify terminations explicitly after a Shape-F
run** (below); TTL is the backstop.

## Install (still zip-based — #90's plugin isn't in the registry; the 0.10.1 zip extracts a bare `classes/`)

```sh
curl -sSL -o /tmp/nf-spawn-0.10.1.zip \
  https://github.com/spore-host/nf-spawn/releases/download/v0.10.1/nf-spawn-0.10.1.zip
mkdir -p ~/.nextflow/plugins/nf-spawn-0.10.1
unzip -o /tmp/nf-spawn-0.10.1.zip -d ~/.nextflow/plugins/nf-spawn-0.10.1/
export JAVA_HOME=/path/to/jdk17    # Nextflow 26.04.x needs a JDK 17+ on PATH
```

## Resources — a per-job cost model, not one flat task

Shape F costs **per rule × job count**, not one instance. This DAG is 5 tasks with knowable
sizes:

| process | instance | TTL | worst-case (rate×TTL) |
|---|---|---|---|
| MAFFT | c8g.large ($0.0798/hr) | 10m | $0.013 |
| MUSCLE | c8g.large | 10m | $0.013 |
| TREE (×2) | c8g.xlarge ($0.1595/hr) | 15m | $0.040 each |
| OBSERVE_RF | c8g.large | 10m | $0.013 |
| **total** | 5 instances | | **~$0.12 worst case** |

**These timings are not compute cost**, same as every recipe — boot + Docker install + image
pull dominate each of the five instances, so Shape F pays that overhead *per rule*. The head
(the `nextflow` process) runs locally and is free; the S3 work dir holds a few MB.

**Recorded run (v0.10.1, no workaround): `completed=5, failed=0`.** All five `.exitcode` objects
in the S3 work dir were `0`, and `rf-observation.txt` published (RF **26**) — verified by reading
the `.exitcode`/objects from S3, **not** by trusting Nextflow's summary line. That distinction
earned its place: on an earlier failed run, Nextflow's summary showed `completed=1` while that
task's `.exitcode` in S3 was `126` with no output — the executor-path version of the "an
exit/summary is not evidence the output is real" rule (spawn#561's shape). The S3 check is what
both caught that and confirmed this clean run is real.

**Verify termination explicitly (`spore-host/nf-spawn#96`, still open).** Instances do not
reliably self-terminate at DAG scale: the **terminal task's instance lingers** after a clean run
(the OBSERVE_RF join, both on 0.8.0 and 0.10.1 — a consistent pattern, not lag), and a *failed*
task's instance stayed `running` after abort. At Shape F (N instances, N lifecycles) a mid-DAG
failure can leave several boxes up — check `aws ec2 describe-instances` / `spawn list` after a
run rather than assuming. TTLs (10–15m) are the backstop and **retighten from the first run**.

## Running it

```sh
cd recipes/nf-spawn
JAVA_HOME=/path/to/jdk17 nextflow run main.nf -c nextflow.config
```

Then **check the bucket**:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/nf-spawn/r1/
```

Expect `rf-observation.txt`. Per-stage identities are asserted inside each task (a bad stage
fails the DAG); the published observation is the join's output.

**Re-running.** Bump the `-r1` suffix in `nextflow.config`'s `workDir` and the `OBSERVE_RF`
`publishDir` to keep both records; a stale S3 work dir will otherwise resume cached tasks.
