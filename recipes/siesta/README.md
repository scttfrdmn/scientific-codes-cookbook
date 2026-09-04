# SIESTA — bulk-silicon DFT, reproducing SIESTA's own committed reference energy

One task. `siesta` runs a self-consistent DFT calculation on bulk silicon over two MPI
ranks, and the smoke check confirms the total energy matches the reference output
SIESTA ships for this exact test at this exact version.

> **What this recipe does and does not cover.** It runs one SCF on a 2-atom silicon
> cell with a single-ζ-polarised basis and a 3×3×3 k-grid — enough to prove SIESTA
> 5.4.2 runs a real, converged, MPI-parallel DFT calculation on Graviton4 and lands on
> the published energy. It is not a benchmark and does not exercise a large cell,
> geometry relaxation, or many-node scaling.

## The pseudopotential problem, and how this recipe resolves it

conda-forge's `siesta` ships **no pseudopotentials** — the package is binaries and the
psml/psf machinery, with no `.psf`/`.psml` data and no example inputs. Without a
pseudopotential per element there is no SCF, so a naive SIESTA recipe could only prove
the binary starts and parses input: the one recipe in this cookbook that asserts
nothing physical.

This recipe stages one instead. The canonical, pinnable source is **SIESTA's own test
suite at the tag matching the container's version** — `Tests/Pseudos/Si.psf` at
`siesta-project/siesta` tag `5.4.2`. That is not a workaround; it is what turns a weak
recipe into the strongest kind: because the pseudopotential *and* the input *and* a
committed reference output all come from the same version, the recipe is a
**reproduction of SIESTA's own published result**, the same move `recipes/relion` makes
against the RODA archive.

Staging a pinned file to S3 and reading it back is the cookbook's normal model — it is
**not** the same as fetching a pseudopotential at run time (which would be
unreproducible). The digest is verified on the box before SIESTA runs.

## Why one task, and the input

SIESTA is one tool and this is one invocation. The `.fdf` input (the `psf` case from
`Tests/01.PseudoPotentials`, `base_si2.fdf` + `psf.fdf` inlined) is a few lines of
config, generated in the task by heredoc; only the pseudopotential is data, and only it
is staged.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049` |
| | tag `2026.09.04`, SIESTA 5.4.2 (aarch64, MPI), cosign-signed, index has one `linux/arm64` manifest |
| pseudopotential | `Tests/Pseudos/Si.psf` from `siesta-project/siesta` tag **`5.4.2`** (Troullier-Martins) |
| | `sha256:0afddde32f30e43fa8d603822f3dd1ddf357e8ff33a1af21eb4982b6e63080d7` (152,736 B) |

**Data tier: stable public source with a durable id.** A file at an immutable git tag,
pinned by sha256. The version deliberately matches the container's SIESTA so the energy
is comparable to the committed reference. `stage-inputs.sh` fetches, verifies and
uploads it once.

The image is aarch.science's curated `dft` env (gpaw + siesta + psi4 + …), not a
per-tool image; this recipe invokes only `siesta`. `recipes/psi4` uses the same image
under the same digest.

## Smoke check

Measured in this image, on this input, before any launch. The total energy is checked
against SIESTA's committed reference, not a band around a guess.

| observable | assertion | observed |
|---|---|---|
| pseudopotential sha256 | matches the pin | OK |
| SCF converged | SIESTA writes `SCF cycle converged after N iterations` | yes (4 iters) |
| MPI ranks | `Running on 2 nodes` | 2 |
| **total energy** | **−214.377236 ± 0.01 eV** (SIESTA 5.4.2 committed reference) | **−214.377236** |

Two of these earn their place:

**The total energy reproduces SIESTA's own reference exactly.** `Tests/01.PseudoPotentials/Reference/psf.out`
at tag 5.4.2 records `siesta: Total = -214.377236 eV`; this run gives `-214.377236` —
identical to six decimals, and identical between serial and 2-rank (measured). That is
cross-validation against a published number produced by the same code at the same
version, which confirms the numerics, not just that SIESTA ran. The ±0.01 band is a
cross-host floating-point margin, far tighter than SIESTA's own test tolerance; the
value itself was bit-identical locally.

**`SCF cycle converged` is a completion sentinel.** SIESTA writes it only when the
self-consistency loop actually converges — a run that hit the iteration limit, or died
mid-cycle, cannot pass, even though a truncated output might still carry a plausible-looking
energy from an earlier iteration.

Running over 2 ranks is deliberate: the `dft` env pins the OpenMPI build
(`siesta=*=mpi_openmpi*`) specifically because the nompi variant has a higher build
number and would otherwise win, so exercising the MPI path is what proves the env
shipped what it meant to.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The SCF takes
**~2 seconds** on 2 ranks; this box is sized for the 2 MPI ranks, not for throughput,
and memory is irrelevant for a 2-atom cell.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**0.87 GB** `dft` image are the whole task; the science is ~2s. The recorded run's
command window was **93s** (20:06:01 → 20:07:34 UTC), and the total energy came back
`-214.377236` — matching SIESTA's committed reference exactly. TTL was **retightened
from that first real run**: 10m originally, now **5m** (~2.3× the ~2-minute instance
life), with `cost_limit` following it down $0.03 → $0.02. A loose TTL is a larger blast
radius, not caution; the recorded run used the original 10m. Disk is trivial: the image
plus a 150 KB pseudopotential and small text output.

## Running it

```sh
recipes/siesta/stage-inputs.sh          # once; fetch + verify + upload Si.psf (~150 KB)
spawn task run --spec recipes/siesta/01-scf.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/siesta/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the
smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect two objects (`psf.out`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump
the `-r1` suffix in both `task_id` and the output prefix to keep both. SIESTA overwrites
its own output, so there is no checkpoint guard to defeat.

**Note on parallel launches.** If you launch this alongside other tasks and it dies with
an AWS `Invalid IAM Instance Profile name` error, that is a transient IAM-propagation
race (spore-host/spawn#572), not a recipe fault — no instance was created, so just
re-run it.
