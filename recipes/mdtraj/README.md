---
tool: mdtraj
tool_version: 1.11.1
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.104.0
---
# MDTraj ← GROMACS — read an XTC trajectory, cross-checked against MDAnalysis

GROMACS writes a compressed `.xtc`; MDTraj reads it back, and MDAnalysis reads the *same file* independently — two trajectory parsers on one compressed format.

> **What this covers.** Write a 50-step rigid-water trajectory and read it two ways — proof MDTraj's GROMACS-XTC reader works on Graviton4 and agrees with a second parser. Not a benchmark; no large trajectory or analysis pipeline.

## Run it

```python
import mdtraj, MDAnalysis as mda
t = mdtraj.load("out.xtc", top="spc216.gro")  # GROMACS-written XTC + its topology → MDTraj
u = mda.Universe("spc216.gro", "out.xtc")     # the same files → MDAnalysis, independently
t.n_atoms, t.n_frames, t.unitcell_lengths[0]  # 648, 6, 1.8621 nm — and the two agree on an O-H distance
```

One task: GROMACS produces the trajectory, both readers parse it in the same container. The XTC handoff is the point, so routing it through S3 would add a boot for no scientific gain — the input (spc216 water) is bundled in the gromacs package, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| GROMACS-written spc216 `.xtc` | your own trajectory + topology | MDTraj reads many formats; XTC is chosen because it's the compressed format neither MDAnalysis recipe covered. |
| the O-H distance as the agreed quantity | any geometry your analysis needs | counts + box can survive a handoff that still mangles coordinates; an agreed *distance* is what proves the coordinates round-tripped. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** two readers agreeing on the same bytes is exact-or-wrong at any trajectory length, and a short trajectory keeps the check fast. (Real trajectory analysis is a different sizing problem — a large trajectory is memory- or I/O-bound, not this ~1 s decode.) Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. MD + both reads take ~1 s. Recorded command window **100s** — boot, Docker install, and the 1.19 GB `md` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the two identities, pins, smoke-check table, run + verify</summary>

### The checks — cross-layer decode + a two-reader cross-check

- **Cross-layer decode (MDTraj ← GROMACS).** MDTraj recovers the exact atom count (648), frame count (6) and box (1.8621 nm) that GROMACS wrote.
- **Two-reader cross-check (MDTraj vs MDAnalysis).** Both read the same `out.xtc` and compute the same O-H distance, agreeing to < 1e-6 nm. Two unrelated parsers landing on the same geometry from the same bytes is [comparing like with like](../../practices/cross-checks.md) — a stronger statement than either reader's self-report, and free since both ship in the image.

### Pins (data tier: bundled in the image)

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` (tag `2026.09.04`, GROMACS 2026.3 + MDTraj 1.11.1 + MDAnalysis, cosign-signed, `linux/arm64`) |
| input | spc216 water + tip3p, bundled in the gromacs package — nothing staged |

Same `md` image as [gromacs](../gromacs/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| MDTraj atoms | exactly 648 (216 waters × 3) | 648 | wrong decode |
| MDTraj frames | exactly 6 (50 steps / 10 + t=0) | 6 | wrong frame stride |
| MDTraj box | 1.8621 nm (from the XTC) | 1.8621 | box not recovered |
| MDAnalysis atoms / frames | 648 / 6 (agree) | 648 / 6 | reader disagreement |
| distance physical | O-H ≈ 0.0956 nm | 0.09560 | coordinates mangled |
| **two-reader agreement** | \|MDTraj − MDAnalysis\| < 1e-6 nm | ~1e-8 | one parser wrong |

### Run + verify

```sh
make run RECIPE=mdtraj
make ls RECIPE=mdtraj
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect two objects (`out.xtc`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
