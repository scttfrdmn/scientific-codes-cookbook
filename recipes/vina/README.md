---
tool: vina
tool_version: 1.2.7
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.104.0
---
# AutoDock Vina — dock imatinib into Abl kinase, against the tutorial's own result

`vina` docks the imatinib ligand into the Abl-kinase receptor (PDB 1IEP) — molecular docking at the tutorial's canonical target.

> **What this covers.** One ligand docked into one receptor at the tutorial's box — proof Vina's scoring and Monte-Carlo search are correct and reproducible on Graviton4 against a known answer. Not a virtual-screening benchmark; no ligand library or flexible-receptor docking.

## Run it

```bash
vina --receptor 1iep_receptor.pdbqt --ligand 1iep_ligand.pdbqt \
  --center_x 15.190 --center_y 53.903 --center_z 16.917 \
  --size_x 20 --size_y 20 --size_z 20 --exhaustiveness 32 --seed 42
```

One task, docked twice for the determinism check. The `vina` package ships no example data, so the receptor and ligand are staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 1IEP receptor + imatinib ligand, from Vina's tutorial at tag **v1.2.7** | your own prepared `.pdbqt` pair | **load-bearing** — match the pair to the tool version or you reproduce a different number; it's what makes this a [published-pose reproduction](../../practices/reference-from-tests.md) (−13.234), not a code check. |
| the box center + 20³ Å size (the tutorial's, inline) | your own binding-site box | the box is where the search happens; get it wrong and the score is meaningless even if Vina "ran". |
| **`--seed 42`** | pin *a* seed for a repeatable run | **determinism scaffolding** — the Monte-Carlo search is stochastic, so a fixed seed makes a run repeatable (different seeds spread ~0.05 kcal/mol). |

**Leave the fixture:** 1IEP is a real receptor/ligand reproducing a published number, and one docking is the unit — a ligand library is [job arrays](../../patterns/job-arrays.md), not a bigger input here. Leave-it.

## Shape, size, cost

One task, `c8g.large`, TTL 10m, cap $0.02. Two docks at exhaustiveness 32 ≈ **300 s of real compute** — the batch's heaviest, so here compute is a real share of the 351s window, not just boot. Vina parallelises over the CPUs; see [sizing](../../patterns/sizing.md) for a screening run.

**Sizing:** compute-bound `c8g`, memory light — `--exhaustiveness` is the compute dial, and a ligand library is a fan-out ([job arrays](../../patterns/job-arrays.md)), not a bigger box. Scale cores/exhaustiveness to your knee ([sizing](../../patterns/sizing.md)).

<details>
<summary>As shipped: the reference reproduction, the seed check, pins, smoke check, run + verify</summary>

### The checks — a published reference and a determinism identity

- **Top affinity reproduces the published tutorial result.** Vina's v1.2.7 basic-docking solution records a top pose of **−13.234 kcal/mol** for imatinib in Abl kinase; this run gives −13.207. Monte-Carlo search makes them close rather than bit-identical (~0.05 kcal/mol spread across seeds), so the band is ±0.5 around the reference — wide enough to survive search noise, tight enough that a failed dock (near zero or positive) fails loudly. This is the [reproduce-a-published-number move](../../practices/reference-from-tests.md), manufactured from Vina's own version-matched test data.
- **A fixed seed is deterministic.** The recipe docks twice at the same seed and requires the two top affinities to agree to < 1e-6 (measured: identical) — proof `--seed` actually controls the RNG.

### Pins (data tier: stable public source with a durable id)

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` (tag `2026.09.04`, AutoDock Vina 1.2.7 conda-forge `vina`, cosign-signed, `linux/arm64`) |
| receptor | `1iep_receptor.pdbqt` from `ccsb-scripps/AutoDock-Vina` tag **`v1.2.7`** — `sha256:f13cf3b3…` (216,160 B) |
| ligand | `1iep_ligand.pdbqt`, same tag — `sha256:15fb3564…` (3,841 B) |

**Note the package name.** conda-forge `vina` has a `linux-aarch64` build; bioconda `autodock-vina` does not — searching the obvious name concludes AutoDock has no arm64 route and is wrong (catalog issue #2). `stage-inputs.sh` fetches, verifies and uploads once.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| receptor + ligand sha256 | match the pins | OK | wrong/corrupt input |
| **top affinity** | −13.7 … −12.7 kcal/mol (v1.2.7 ref −13.234) | **−13.207** | broken scoring/search |
| **deterministic seed** | two runs at the same seed agree to < 1e-6 | **identical** | seed ignored |
| poses returned | ≥ 3 | 4 | search collapsed |

### Run + verify

```sh
make stage RECIPE=vina            # once; fetch + verify + upload the 1iep pair (~220 KB)
make run RECIPE=vina
make ls RECIPE=vina
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect three objects (`dock.log`, `dock.json`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
