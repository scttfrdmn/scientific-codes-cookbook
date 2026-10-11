---
tool: nipype
tool_version: "1.10.0"
env: neuroimaging
image: quay.io/aarchsci/neuroimaging@sha256:6ede8870c83b095c25c9f08db34e56f66318e2cb6b668391d9d5642147856457
spawn_version: 0.126.1
last_verified: 2026-10-11
---
# Nipype — a workflow engine held to order, data flow and cardinality

Runs a four-node DAG on Graviton4 and checks what a pipeline engine can actually be held to: execution order, exact composition, and fan-out counts. For anyone building neuroimaging pipelines on ARM.

## Run it

```bash
make stage RECIPE=nipype     # once: the checks only — the reference is arithmetic
spawn task run --spec "$(make -s spec RECIPE=nipype)" --wait
make ls RECIPE=nipype

wf = pe.Workflow(name="chain", base_dir="/tmp/wf")
wf.connect([(src, d, [("x","x")]), (d, a, [("y","x")]),
            (a, s, [("y","x")]), (s, m, [("y","x")])])
wf.run(plugin="Linear")      # double -> addone -> square -> minus3; ((2·5)+1)²−3 = 118
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| execution read from **side effects** | parsing nipype's console output | each node appends to a log file, so "what ran" is a fact rather than a log format that can change between releases. |
| `Function` nodes | AFNI/FSL/ANTs interfaces | **this env ships only a subset of AFNI** and no FSL/ANTs/SPM, so interface-based nodes would depend on which binaries exist ([what the neuroimaging recipe found](../neuroimaging/README.md)). Pure-Python nodes test the *engine*. |
| four non-commuting ops | your own | staging checks that **0 of 23** wrong orderings give the right answer. Commuting ops would make "ran in order" unfalsifiable. |
| expecting cached re-runs | — | **an identical re-run re-executed everything**, under `hash_method` `timestamp` *and* `content`. See below — this recipe does not claim caching. |
| `plugin="Linear"` | `MultiProc` | linear is deterministic, which is what lets the order assertion be exact. |

**Leave the fixture.** Nothing is fetched: the nodes do integer arithmetic so the answer is derivable, and the composition is chosen to be order-sensitive. **Scale it** by substituting real interfaces — the structural checks transfer unchanged.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The whole DAG plus the MapNode fan-out is **4 s** of a 1m05s window; 40 s is installing Docker and 19 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: four exact structural checks, an order-sensitivity guard, and a caching claim deliberately withheld</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| nipype / networkx / traits | read from the running install | **1.10.0** / 3.7 / 7.2.0 |
| DAG acyclic | networkx agrees | True |
| **execution order** | **== dependency order** | **double → addone → square → minus3** |
| **composition** | **`== ((2x)+1)²−3` exactly** | **118** for x = 5 |
| **each node's input** | **== its predecessor's output** | 5, 10, 11, 121 |
| **changed input re-runs all 4** | **== 4 executions** | 4 |
| **and propagates the new value** | **== 222** for x = 7 | **222** |
| **MapNode over 7 inputs** | **7 outputs, each 2x, 7 executions** | `[2,4,6,8,10,12,14]` |
| result reuse on an unchanged re-run | *reported, **not** asserted* | did not happen |

### What a DAG runner can be held to

The nodes here compute `2x`, `x+1`, `x²`, `x−3`. That triviality is the point: the engine's job is
not arithmetic but **order, data flow and cardinality**, and all three are exact.

**Order is observed, not asked for.** Each node appends to a log as it runs, so the recorded
sequence is what actually happened rather than what the graph intended. And staging proves the
check can fail: of the 23 wrong orderings of these four operations, **none** produces the right
answer at either seed. With commuting operations — four additions, say — a mis-ordered DAG would
give the correct result and "ran in order" would be unfalsifiable.

**Data flow is checked link by link, not just end to end.** The composed result could be right
while an intermediate was wrong in a way that cancelled, so every node's *recorded input* is
matched against its predecessor's output: 5 → 10 → 11 → 121 → 118.

**Propagation is checked with a different seed.** Changing x from 5 to 7 must recompute all four
nodes *and* carry the new value through: 222, not 118. Staging confirms the two seeds give
different answers, so a stale cached value cannot pass.

**Cardinality is an integer claim.** A MapNode over 7 inputs produced exactly 7 outputs, each
twice its input, in exactly 7 executions — fan-out that silently dropped or duplicated an item
fails on the count alone. The fixture is injective under doubling so outputs can be matched to
inputs rather than coincidentally equal.

### The caching claim this recipe does not make

Result reuse is a large part of why people adopt nipype, so its absence here is worth stating
precisely:

```text
hash_method = timestamp   run 2, identical inputs   RE-EXECUTED (side effects 2 → 4)
hash_method = content     run 2, identical inputs   RE-EXECUTED (side effects 2 → 4)
```

An identical re-run of the same workflow in the same `base_dir` re-executed **every** node under
both hashing modes. So "an unchanged node is cached" is not true for this configuration, and the
recipe asserts nothing about it.

**The mechanism is not established, and this page does not guess at one.** It could be specific to
`Function` nodes, to each run happening in a fresh interpreter, or to something else entirely —
`hash_method` was the obvious hypothesis and measuring it refuted it. Diagnosing nipype from one
observation would be exactly the overreach this catalog has been bitten by; what is reported is
what was measured, with the configuration stated so anyone can reproduce it.

A consequence worth noting: because nothing is cached, the "changed input re-runs all four nodes"
check above is a **propagation** check rather than a cache-invalidation one. Had caching worked, the
pair (cached when unchanged / re-run when changed) would be the stronger claim — and either half
alone is satisfiable by a broken engine, which is why neither is asserted on its own.

### Pins

| | |
|---|---|
| data | **none.** Integer arithmetic over a chosen composition; the reference is derivable |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/neuroimaging@sha256:6ede8870c83b…` — nipype 1.10.0, networkx 3.7, traits 7.2.0, afni 25.0.00, dipy 1.12.1, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=nipype
spawn task run --spec "$(make -s spec RECIPE=nipype)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/nipype/r1/score.tsv" -
```

Fails on a pin mismatch, a cyclic graph, nodes running out of dependency order, a composed result
that is not exactly 118, any node receiving something other than its predecessor's output, a
changed input that does not recompute all four nodes or does not carry the new value, or a MapNode
whose output count or execution count differs from its input count — but check the bucket
regardless ([exit 0 isn't proof](../../practices/container-path.md)). Nipype's crash files and node
reports are tarred to `nipype-diag.tar.gz` so a failure is diagnosable without a rerun.

### Not covered

**Every real interface.** This exercises the engine with `Function` nodes only. The AFNI, FSL,
ANTs, SPM and FreeSurfer wrappers that make nipype useful are untested — FSL/ANTs/SPM are absent
from this env, and AFNI ships only a subset of its programs.

**Caching and provenance**, for the reason above. **`JoinNode`**, present and unexercised, which
would complete the fan-out/fan-in pair with MapNode. **`MultiProc` and cluster plugins** (SGE,
SLURM, DAGMan) — `Linear` is used because it is deterministic, so nothing here speaks to parallel
execution or scheduler submission. **Iterables and nested workflows**, the parameter-sweep
machinery most pipelines rely on. **Failure handling**: no node is made to crash, so the
`stop_on_first_crash` and retry behaviour is unverified.

</details>
