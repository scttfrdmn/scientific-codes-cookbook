# MDTraj ← GROMACS — read an XTC trajectory, cross-checked against MDAnalysis

One task, a producer and two readers. GROMACS writes a compressed `.xtc` trajectory;
MDTraj reads it back, and MDAnalysis reads the *same file* independently. The smoke check
confirms MDTraj recovers what GROMACS wrote **and** that the two parsers agree on a
computed geometry from the same bytes.

> **What this recipe does and does not cover.** It writes a 50-step rigid-water trajectory
> and reads it two ways — enough to prove MDTraj's GROMACS-XTC reader works on Graviton4
> and agrees with a second parser. Not a benchmark; no large trajectory or analysis
> pipeline.

## Two identities: cross-layer decode, and a two-reader cross-check

- **Cross-layer decode (MDTraj ← GROMACS).** GROMACS writes `.xtc`; MDTraj recovers the
  exact atom count (648), frame count (6) and box (1.8621 nm), plus the coordinates. This
  is the decode-statistic move — the **fourth instance** after `recipes/earth-observation`
  (GDAL COG checksum), `recipes/pointcloud` (PDAL Z-mean) and `recipes/openmm-mdanalysis`
  (MDAnalysis on DCD) — now on **XTC, a format neither MDAnalysis recipe covered**.
- **Two-reader cross-check (MDTraj vs MDAnalysis).** Both read the same `out.xtc` and
  compute the same O-H distance; they agree to **< 1e-6 nm**. Two unrelated trajectory
  parsers landing on the same geometry from the same bytes is the cross-code move
  (RAxML-NG/IQ-TREE) applied to trajectory parsing — stronger than either reader's
  self-report, and it came for free since both ship in the image. Counts and box could be
  preserved by a handoff that still mangled coordinates; the agreed distance is what proves
  the coordinates survived.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` |
| | tag `2026.09.04`, GROMACS 2026.3 + MDTraj 1.11.1 + MDAnalysis, cosign-signed, `linux/arm64` |
| input | spc216 water + tip3p, **bundled in the gromacs package** — nothing staged |

**Data tier: bundled in the image.** Same `md` image as `recipes/gromacs`; this recipe uses
GROMACS, MDTraj and MDAnalysis.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| MDTraj atoms | exactly 648 (216 waters × 3) | 648 |
| MDTraj frames | exactly 6 (50 steps / 10 + t=0) | 6 |
| MDTraj box | 1.8621 nm (spc216 box, from the XTC) | 1.8621 |
| MDAnalysis atoms / frames | 648 / 6 (agree) | 648 / 6 |
| distance physical | O-H ≈ 0.0956 nm | 0.09560 |
| **two-reader agreement** | \|MDTraj − MDAnalysis\| < 1e-6 nm (same file) | ~1e-8 |

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The MD + both reads
take **~1 second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**1.19 GB** `md` image are the whole task. The recorded run's command window was **100s**
(02:33:59 → 02:35:39 UTC), MDTraj and MDAnalysis agreeing to ~1e-8 nm. TTL was
**retightened from that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose
TTL is a larger blast radius, not caution; the recorded run used the original 10m. Disk is
trivial.

## Running it

No `stage-inputs.sh` — the system is bundled in the image.

```sh
spawn task run --spec recipes/mdtraj/01-read.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/mdtraj/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect two
objects (`out.xtc`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
re-run.
