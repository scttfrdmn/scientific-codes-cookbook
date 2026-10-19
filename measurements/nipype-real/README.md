# nipype on a real workload — a 616-node DTI workflow over 123 subjects, Graviton4, Intel and AMD

> **The engine is transparent and exact at cohort scale on all three chips:** 616 nodes, each run
> once, in dependency order, never more than `n_procs` at a time. The maps are bit-identical to the same
> computation driven by a shell loop. `MultiProc` and `Linear` write the same bytes. An unchanged re-run
> reuses every node, and a downstream change re-runs exactly the downstream nodes. That caching
> holds only because the Function nodes are *not* handed a file they write: give a node its own
> side-effect log as an input and it re-runs every time. That mechanism explains
> [the recipe's](../../recipes/nipype/README.md) withheld caching claim. AMD runs the cohort 1.14× faster
> than Graviton4 and Intel 1.21× slower; Graviton4 is cheapest per subject.

Workload: the 123-subject Rockland diffusion cohort that [neuroimaging-real](../neuroimaging-real/README.md)
fits directly. That is the baseline `DIFF_137_AP` scan (2 mm, 9 b=0 + 128 directions at b=1500) of every NFBS
subject that has one, reused from that measurement's staged, pinned bytes. Here it runs as a nipype
workflow, the way a DTI study builds one. An `infosource` with `iterables` over the subjects feeds:

- `SelectFiles`;
- AFNI's `3dTstat -mean` through `afni.TStat`;
- a median-Otsu brain mask (`Function`);
- `nipype.interfaces.dipy.DTI` (DIPY's WLS fit, float32 FA/MD/RD/AD maps);
- a per-subject `stats` node (`Function`);
- a `JoinNode` that collects one row per subject.

That makes 5 × 123 + 1 = 616 executed nodes and 1,107 edges under `MultiProc` at 16 processes.
A cohort is the unit a pipeline engine is used on, and 123 is a typical study size.

## Run it

```sh
measurements/neuroimaging-real/stage-inputs.sh "$(make -s print-bucket)"   # the cohort, once (and that measurement's run, per chip: its maps are the reference)
measurements/nipype-real/stage-inputs.sh "$(make -s print-bucket)"         # the scripts
B=$(make -s print-bucket); M=measurements/nipype-real
sed "s|\${COOKBOOK_BUCKET}|$B|g" $M/cohort.task.json > /tmp/n.json && spawn task run --spec /tmp/n.json --wait
spawn task run --spec "$(python3 scripts/spec_arch.py nipype x86_64 $B r1 $M/cohort.task.json)" --wait   # or amd
sed "s|\${COOKBOOK_BUCKET}|$B|g" $M/xchip.task.json > /tmp/x.json && spawn task run --spec /tmp/x.json --wait   # all three chips
```

One task per chip runs four workflows in sequence: **A**, the cohort under `MultiProc`, which is the
workload and is timed; **B**, 4 subjects under `Linear` in a fresh `base_dir`; **C**, A again, unchanged;
**D**, A with one parameter of `stats` changed. Then **E**, a one-node probe of the caching mechanism, and
the checks.

## Results

16 vCPU / 64 GiB, `n_procs` 16, every node single-threaded (`OPENBLAS_NUM_THREADS=1`), same image per
architecture, same bytes:

| chip | instance | cohort wall (A) | billed (A–E) | $/hr | compute $/subject | billed $/subject | peak RSS |
|---|---|---|---|---|---|---|---|
| Graviton4 (Neoverse-V2) | `m8g.4xlarge` | 214 s | 553 s | 0.7181 | **0.000347** | **0.000897** | 20.1 GiB |
| Intel Xeon 6975P-C | `m8i.4xlarge` | 258 s | 581 s | 0.8467 | 0.000493 | 0.001111 | 20.8 GiB |
| AMD EPYC 9R45 | `m8a.4xlarge` | **188 s** | **488 s** | 0.9738 | 0.000413 | 0.001073 | 19.8 GiB |

- **AMD is 1.14× faster than Graviton4 and Intel 1.21× slower.** AMD's 36% higher hourly price more
  than eats its speed, so **Graviton4 is cheapest per subject**: 16% under AMD in compute and 16% billed.
  `lscpu` threads per core is 1 / 2 / 1: 16 vCPU is 8 cores × 2 hyperthreads on `m8i`, which is why Intel
  is last.
- **The engine keeps the pool full.** Summed node time over wall × 16 is 0.961 / 0.962 / 0.955
  (`avg_cores` 11.8 / 13.0 / 11.1). Node time on Graviton4: mask 53%, DTI 25%. The other 22% is
  `select`, `tstat` and `stats` at ~1.9 s each, inflated by `MultiProc`'s polling interval: under
  `Linear` the same `tstat` takes ~1 s and `stats` ~0.5 s.
- **Bookkeeping costs the same on every chip.** The unchanged re-run (C) takes 80–83 s at
  0.1 cores on all three, and the one-parameter change (D) 82–84 s. That cost does not scale with the
  chip, so it is not compute. It is about 38% of the cohort's own wall time on Graviton4.
- **Billed is the whole task, A through E.** Phases B–D add 232–245 s of checking re-runs on top of
  boot, Docker, the env install on x86 and staging 9.8 GB. Without them a cohort run bills ~256–339 s, so
  **these timings are not compute cost**.
- Peak RSS is the container cgroup's `memory.peak`: 16 workers at ≤1.4 GiB each, plus the main
  process. The 9.2 GB of staged scans sit in the host tmpfs. The work directory is on the root disk.
- n = 1 per cell. Every check is deterministic, and Intel and AMD produce byte-identical outputs.

## What held at scale, on all three chips

| assertion | bound | observed (identical on all three) |
|---|---|---|
| executed graph | 5 nodes × 123 + 1 join; 9 edges × 123 | 616 nodes, 1,107 edges |
| each node started and ended once, no exceptions, no crash files | exact | 616 / 616, 0, 0 |
| Function-node side effects == nipype's start callbacks | exact | mask 123, stats 123, join 1 on both counts |
| **dependency order**: no child starts before its parent ends | 0 violations | 0 of 1,107 edges |
| **concurrency** never exceeds `n_procs` | ≤ 16 | 16 |
| **JoinNode cardinality**: one row per subject, ids == the cohort | exact | 123 rows, 123 distinct, equal set |
| no cross-wiring: each row computed from its own subject's scan | 123 / 123 | 123 / 123 |
| **mask bit-identical to neuroimaging-real's** (same chip, xargs-driven) | identical | 123 / 123 |
| **WLS FA and MD** == neuroimaging-real's, rounded to float32 | ≤ 1 float32 ulp | **0 ulp**: 123 / 123 bit-identical, 19,954,065 voxels |
| **`Linear` == `MultiProc`**, fresh `base_dir`, 4 subjects | array-identical, all 8 maps | 4 / 4 |
| **unchanged re-run** (C) | 0 outputs rewritten, 0 side effects | 0, 0 (only `SelectFiles` is resubmitted) |
| **downstream change** (D) re-runs only `stats` and `join` | `stats` 123 + `join` 1; no map rewritten | exactly that; every other field unchanged |
| FA zero outside the mask, float32 | exact | 0 |
| AFNI `3dTstat -mean` vs numpy | < 1e-6 relative | 6.695e-08 |

The 1-ulp bound is the precision of the output: `DTI` writes float32 maps of a float64 fit. Reported,
not asserted: the cohort median FA in the mask is 0.1789 (subject range 0.1446–0.2066).

## Why a Function node re-runs: its own log is an input

nipype hashes a string input that names an existing file *by that file*, through its timestamp or
content, and ignores the string itself (`BaseTraitedSpec._get_sorteddict`, nipype 1.10.0). So a node
that is handed the path of a log it appends to changes its own hash every time it runs. Phase E
measures this with one `Function` node, run twice in fresh interpreters:

| the node's input | executions in two identical runs |
|---|---|
| the log **file** it appends to | **2**: re-run |
| the log's **directory** | 1: reused |

This workflow passes the directory, which is why C and D cache exactly. The first probe passed the
file, and an unchanged re-run then recomputed every `mask`, and so every `dti` and `stats` below it.
`tstat`, an interface node, was reused. The recipe's fixture passes `logpath` as a file, so this
explains its "identical re-run re-executed everything, under `timestamp` and `content`": the content
changed too.

## Fixed point or path, across chips

Every quantity here is a **fixed point**. The engine's counts, order, cardinality and caching results are
integers, identical on all three chips. The mask is a filter plus a histogram threshold, and DIPY's
WLS is a closed-form reweighting. Each chip's maps equal that chip's neuroimaging-real maps rounded to
float32, bit for bit. So [`xchip.py`](xchip.py) compares the three chips' nipype outputs exactly, from
those references ([`results/xchip.txt`](results/xchip.txt)):

| quantity | Intel vs Graviton4 | AMD vs Graviton4 |
|---|---|---|
| brain masks | 123 / 123 identical | 123 / 123 identical |
| float32 FA map | 21 / 123 subjects identical; 222 of 19,954,065 voxels differ, by **1 ulp** | the same |
| float32 MD map | 111 / 123 identical; 13 voxels differ, by 1 ulp | the same |
| per-subject median FA / MD, `3dTstat` agreement | identical | identical |

The ~1e-11 cross-architecture difference in the float64 fit that neuroimaging-real measured
crosses a float32 rounding boundary in 0.001% of voxels. Intel and AMD run the same x86 build and are
byte-identical in every output.

## Pins

| | |
|---|---|
| data | RODA, via [neuroimaging-real](../neuroimaging-real/README.md): 123 subjects, 492 files, 9.21 GB, pinned by its `dwi.sha256`, checked on every box before any compute |
| reference | neuroimaging-real's DIPY maps on the same chip (`runs*/neuroimaging-real/cohort/dipy/dipy-maps.tar`) |
| image | the recipe's `quay.io/aarchsci/neuroimaging` arm64 digest, and on x86 its `env_x86_64` twin `neuroimaging-add4ba1f213a`. nipype 1.10.0, networkx 3.7, traits 7.2.0, dipy 1.12.1, nibabel 5.4.2, numpy 2.5.3, AFNI 25.0.00. OpenBLAS `neoversev2` on Graviton4, `Cooperlake` on Intel and AMD |
| scripts | `run.sh`, `pipeline.py`, `check.py`, `hashprobe.py`, `xchip.py`, pinned by sha256 in each spec; `mkspec.py` writes the specs |

Per-chip summaries, checks and the final cohort rows are in [`results/`](results/). Callbacks,
side-effect logs and nipype's `pypeline.log` are in `nipype-logs.tar.gz` beside each run in the bucket.

## Caveats

n = 1 per chip. Sized from a 16-subject probe on `m8g.4xlarge`: 38 s for A, cgroup peak 16.4 GiB,
~19 s per subject serially. `MultiProc` on Python 3.14 starts workers by `forkserver`, which
re-imports the script. Without an `if __name__ == "__main__":` guard, the first probe died with *"An
attempt has been made to start a new process before the current process has finished its
bootstrapping phase"* followed by `ConnectionResetError`. Nothing in that message names nipype.
