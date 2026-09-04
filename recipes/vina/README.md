# AutoDock Vina — dock imatinib into Abl kinase, against the tutorial's own result

One task. `vina` docks the imatinib ligand into the Abl-kinase receptor (PDB 1IEP), and
the smoke check confirms the top binding affinity reproduces the published tutorial
result and that a fixed seed is deterministic.

> **What this recipe does and does not cover.** It runs one real docking of one ligand
> into one receptor at the tutorial's box and exhaustiveness — enough to prove Vina's
> native scoring and Monte-Carlo search are correct and reproducible on Graviton4
> against a known answer. It is not a virtual-screening benchmark and does not dock a
> library or exercise flexible-receptor docking.

## Why one task, and why the input is staged

Vina is one tool and this is one docking run. The conda `vina` package ships **no
example data** (the module plus `bin/vina`, `bin/vina_split`), so a *real* docking —
one whose score means something — needs a receptor and ligand staged. A synthetic
inline system would run the code but assert nothing chemical (that is what the env's own
smoke test does, and it says as much); the point of this recipe is a result you can
trust.

The canonical, pinnable source is **Vina's own basic-docking tutorial at the tag
matching the container's version** — the prepared 1IEP receptor and imatinib ligand
from `AutoDock-Vina` tag `v1.2.7`. Because the inputs *and* a reference docked output
come from the same version, the recipe is a **reproduction of Vina's published tutorial
result**, the same move `recipes/relion` and `recipes/siesta` make. Staging pinned files
to S3 is the cookbook's normal model; the digests are verified on the box before docking.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` |
| | tag `2026.09.04`, AutoDock Vina 1.2.7 (conda-forge `vina`, **not** bioconda `autodock-vina`), cosign-signed, `linux/arm64` |
| receptor | `1iep_receptor.pdbqt` from `ccsb-scripps/AutoDock-Vina` tag **`v1.2.7`** |
| | `sha256:f13cf3b36f61d87c3b58983e0b8ecf1c3456a685eb86dfe9ccfb139c7bdc2586` (216,160 B) |
| ligand | `1iep_ligand.pdbqt`, same tag |
| | `sha256:15fb35648d8c18c70317842f3a0631b73a19429c710a037ab07310084d579bb8` (3,841 B) |

**Data tier: stable public source with a durable id.** Files at an immutable git tag,
pinned by sha256, version-matched to the container's Vina so the score is comparable to
the tutorial reference. The box center `(15.190, 53.903, 16.917)` and 20³ Å box are the
tutorial's own, inline in the task. `stage-inputs.sh` fetches, verifies and uploads once.

**Note the package name.** conda-forge `vina` has a `linux-aarch64` build; bioconda
`autodock-vina` does not — searching the obvious name concludes AutoDock has no arm64
route and is wrong (issue #2).

The image is aarch.science's curated `comp-chem` env; this recipe invokes only the
`vina` Python module.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| receptor + ligand sha256 | match the pins | OK |
| **top affinity** | **−13.7 … −12.7 kcal/mol** (v1.2.7 reference −13.234) | **−13.207** |
| **deterministic seed** | two runs at the same seed agree to < 1e-6 | **identical** |
| poses returned | ≥ 3 | 4 |

Two of these earn their place:

**The top affinity reproduces the published tutorial result.** Vina's v1.2.7 basic-docking
solution records a top pose of **−13.234 kcal/mol** for imatinib in Abl kinase; this run
gives −13.207. Monte-Carlo search means the two are close rather than bit-identical
(different seeds explore slightly different best poses — measured spread across seeds is
~0.05 kcal/mol), so the band is ±0.5 around the reference: wide enough to survive search
noise across hosts, tight enough that a failed dock (which lands near zero or positive)
fails loudly. Reproducing an external published number is the correctness claim.

**A fixed seed is deterministic** — the recipe docks twice at the same seed and requires
the two top affinities to agree to < 1e-6 (measured: identical). This is the
reproducibility identity docking makes available: it proves `--seed` actually controls
the Monte-Carlo RNG, so the run is repeatable rather than a fresh random draw each time.
A broken or ignored seed would show up here as two different scores.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 10m, cap $0.02. Each docking at
exhaustiveness 32 takes **~150 seconds on 2 vCPUs**, and the recipe docks twice (for the
determinism check), so ~300 s of real compute — by far the heaviest recipe in this
batch (the others are seconds). Vina parallelises over the CPUs, so both vCPUs are
working; memory is not the constraint.

**These timings are still mostly not compute cost, but here compute is a real share.**
The recorded run's command window was **351s / 5m51s** (20:08:25 → 20:14:16 UTC) — most
of it the two docks, on top of boot, the Docker install, and the **0.62 GB** `comp-chem`
pull. The top affinity came back `-13.207` (reference −13.234) and the two same-seed
docks were identical. TTL was **retightened from that first real run**: 12m originally,
now **10m** (~1.4× the ~7-minute instance life — tighter than the trivial recipes'
margin because the compute genuinely fills most of the window), `cost_limit` $0.03 →
$0.02. A loose TTL is a larger blast radius, not caution; the recorded run used the
original 12m. Disk is trivial: the image plus a ~220 KB receptor and small text output.

## Running it

```sh
recipes/vina/stage-inputs.sh            # once; fetch + verify + upload the 1iep pair (~220 KB)
spawn task run --spec recipes/vina/01-dock.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/vina/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the
smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect three objects (`dock.log`, `dock.json`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump
the `-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** If launched alongside other tasks and it dies with an AWS
`Invalid IAM Instance Profile name` error, that is a transient IAM-propagation race
(spore-host/spawn#572), not a recipe fault — no instance was created, so just re-run it.
