---
tool: vina
tool_version: 1.2.7
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d
env_x86_64: comp-chem-1243f25ebfec
spawn_version: 0.130.1
last_verified: 2026-10-17
---
# AutoDock Vina — reproduce the published imatinib/Abl docking result

Docks imatinib into Abl kinase (1iep) and lands 0.027 kcal/mol from Vina's own published v1.2.7 value. For anyone docking on ARM.

## Run it

```bash
make stage RECIPE=vina   # once: the 1iep receptor + ligand from the v1.2.7 tag
spawn task run --spec "$(make -s spec RECIPE=vina)" --wait   # ~6 min on c8g.large, self-terminating
make ls    RECIPE=vina   # smoke-check.txt + dock.json

python3 -c "
from vina import Vina
v = Vina(sf_name='vina', seed=42, cpu=2, verbosity=0)
v.set_receptor('1iep_receptor.pdbqt'); v.set_ligand_from_file('1iep_ligand.pdbqt')
v.compute_vina_maps(center=[15.190,53.903,16.917], box_size=[20,20,20])
v.dock(exhaustiveness=32, n_poses=5); print(v.energies(n_poses=1))"
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 1iep receptor + imatinib | your target and ligand | the version-matched pair is what makes the published number the right one to expect. |
| `center` / `box_size` | your binding site | the box is the single biggest determinant of the result; a wrong centre still docks and still scores. |
| `exhaustiveness=32` | 8 for screening | 32 is the tutorial's careful-single-dock setting; the published value belongs to it. |
| `cpu=2`, `seed=42` | your own — but pin both | **both**, not just the seed. See below. |

**Leave the target** — it is the one that comes with a published answer. **Scaling this to a virtual
screen is deliberately not in this recipe**: measured, Vina's cost scales steeply with ligand
flexibility, and a random ChEMBL slice contains peptides with 20+ rotatable bonds that dominate the
runtime. A screen needs a drug-likeness filter and its own sizing pass.

## Shape, size, cost

`c8g.large` (2 vCPU): **5 min 52 s command window, ~$0.01** for two 32-exhaustiveness docks.

**Which box, [measured](../../measurements/vina-real/README.md) on all 513 PoseBusters redocks** (32 × `cpu=1`): AMD `c8a.8xlarge` 46 min, **$1.34**. Graviton4 `c8g.8xlarge` 61 min, **$1.31**. Intel `c8i.8xlarge` 82 min, $2.05. The top pose is within 2 Å on 49–50 of 85 Astex complexes, and PoseBusters' own Vina run got 49. Crystal-pose scores are identical on all three chips. The docked poses differ on Graviton4 only, because the search path depends on the build.

<details>
<summary>As shipped: a published number, and why the seed alone was not enough</summary>

### The checks

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) |
|---|---|---|---|
| **top affinity** | **−13.207 ± 0.001** (seed+cpu pinned) | **−13.207** | **−13.286 — fails** |
| **vs published v1.2.7** | **within 0.05 kcal/mol of −13.234** | **0.027** | **0.052 — fails** |
| same seed twice | identical to 1e-6 | **identical** | identical |
| poses | ≥ 3 | **4** | 5 |

**x86_64 does not verify.** The same spec on the env's linux-64 twin
(`envs-x86_64/comp-chem-1243f25ebfec`; three versions differ from arm64, none of them Vina's), on
`c8i.large` with the same `seed=42` and `cpu=2`, docks to **−13.286**, identically twice on the box,
with 5 poses. Both affinity claims fail: the pinned value is a property of the Graviton4 run, and the
published-reference distance is 0.052 against a 0.05 bound. The top affinity is a **path**: a
seeded Monte Carlo search, whose trajectory a different chip's build is free to change while staying
deterministic on its own box. The assertion is left as written.

**Two claims, separated because they fail for different reasons.** The reproducibility assertion
breaks if the search stops being pinned — someone changes `cpu`, or a resolver swaps the build. The
published-reference assertion breaks if the receptor, box centre, or ligand is wrong. A single band
around the published value would conflate them, and each tolerance is now set by what it checks: the
0.05 kcal/mol is what a 3-decimal affinity from a stochastic search supports, loose enough not to
require our thread count to match the tutorial's exactly.

This is a **published-number reproduction**, the same tier as [SIESTA](../siesta/README.md)
reproducing its committed −214.377236: the number is not ours, so agreeing with it tests more than
internal consistency. The version match is load-bearing — the receptor and ligand come from the
AutoDock-Vina `v1.2.7` tag matching the container, and a reference pose from another release is a
different number.

### Pinning the seed was not enough, and this is why

Vina parallelises its Monte Carlo search, so **the thread count changes the search trajectory and
therefore the top pose.** The recipe originally pinned only `seed`, which makes the result a property
of the box rather than of the recipe: run the same spec on a 16-core instance and there is no reason
to expect −13.207, and the assertion would look flaky when it was really under-specified.

`cpu=2` is now pinned alongside `seed=42`, on a 2-vCPU box so the pinned count matches the hardware.
Honest caveat: this did **not** change the observed value, because the original run was already on a
2-vCPU box. What it changes is whether the number is reproducible by someone else. Verified across
**three runs on three separate instances**, all −13.207 — which is what licensed replacing the old
±0.5 band.

Same rule as [IQ-TREE, Flye and muscle](../../practices/cross-checks.md), reached independently in a
fourth domain.

### Why there is no virtual screen here

A screen is the obvious "real workload" for a docking tool, and it was measured and set aside.
Vina's cost scales steeply with torsion count, and a deterministic slice of ChEMBL contains peptides
with dozens of rotatable bonds — enough to consume a 15-minute budget on a handful of ligands. A
usable screen therefore needs a drug-likeness filter (MW ≤ 500, rotatable bonds ≤ 10) and its own
throughput measurement, which is a separate piece of work rather than a parameter change here.

Two incidental findings from that attempt, both worth reusing: Open Babel's `make3D` is **0.04 s per
ligand** against RDKit ETKDG+MMFF's **0.55 s** — 14× faster, the opposite of what we assumed — and a
Python task should run `python3 -u`, because buffered stdout is lost when a process is killed and
[the log only ships at the end](https://github.com/spore-host/spawn/issues/632).

### Pins

| | data tier |
|---|---|
| Vina | 1.2.7, in `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cf…` (`linux/arm64`) |
| receptor | `AutoDock-Vina` @ `v1.2.7` → `example/basic_docking/solution/1iep_receptor.pdbqt`, sha256 `f13cf3b36f61d87c…` |
| ligand | same tag → `1iep_ligand.pdbqt`, sha256 `15fb35648d8c18c7…` |

The previous pin, `comp-chem@sha256:a06f130ca3c8…` (lock-hash `c8cd4a3c4c30`), can no longer be
re-solved: conda-forge later capped its mdtraj 1.11.1 at `numpy <2.5`, and it shipped numpy 2.5.2
(found 2026-10-17). It still runs, and its arm64 lock still installs without a solve
([why](../../practices/environment-durability.md)); this recipe moved to the 2026.10.11 env.

conda-forge's `vina` ships no example data, so the inputs come from the code's own
version-matched tutorial — [the sourcing move](../../practices/reference-from-tests.md) that turns
"produce a number" into "reproduce a published number".

### Run + verify

```sh
make stage RECIPE=vina
make run   RECIPE=vina
make ls    RECIPE=vina
```

Expect `smoke-check.txt` with `top_affinity_pinned -13.207` and `vs_published_v127` under 0.05.

</details>
