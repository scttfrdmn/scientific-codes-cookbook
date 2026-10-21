# RELION on a real workload — a complete gold-standard 3D refinement, Graviton4, Intel and AMD

> **Graviton4 runs the whole refinement 1.46× faster and 42% cheaper in compute than
> same-generation Intel; AMD is fastest (1.16× Graviton4) but 17% dearer in compute. All three
> reach the depositors' own 3.15 Å in the same Fourier shell.**
> GPU is the normal platform for RELION's refinement: this is the CPU round, and the depositors'
> own run of the same job was on four GPUs.

Workload: the **whole** first gold-standard auto-refinement of the RELION 5.0 SPA tutorial —
`Refine3D/job019`: all 4,452 re-extracted β-galactosidase particles (box 256 at 1.2445 Å/px, D2),
from the tutorial's own Class3D reference, run to convergence with the depositors' own flags, then
post-processed with their mask and MTF. The data is the RELION authors' 24-movie subset of
EMPIAR-10204, from their precalculated results; their output for this exact job is in the same
archive, which is what the run is checked against.

## Run it

```sh
B=$(make -s print-bucket)
measurements/relion-real/stage-inputs.sh "$B"     # streams + filters the 11.3 GB archive ON AWS
spawn task run --spec "$(python3 scripts/spec_arch.py relion arm64 $B r1 measurements/relion-real/refine3d.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py relion x86_64 $B r1 measurements/relion-real/refine3d.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py relion amd $B r1 measurements/relion-real/refine3d.task.json)" --wait
```

Inside the task this is the depositors' `note.txt` command, minus `--gpu`:

```sh
mpirun --bind-to none -np 9 relion_refine_mpi --o Refine3D/job100/run --auto_refine --split_random_halves \
  --i Extract/job018/particles.star --ref Class3D/job016/run_it025_class002_box256.mrc --firstiter_cc \
  --ini_high 50 --dont_combine_weights_via_disc --preread_images --pool 30 --pad 1 --auto_ignore_angles \
  --auto_resol_angles --ctf --particle_diameter 200 --flatten_solvent --zero_mask --oversampling 1 \
  --healpix_order 2 --auto_local_healpix_order 4 --offset_range 5 --offset_step 2 --sym D2 \
  --low_resol_join_halves 40 --norm --scale --j 8 --random_seed 1697716706
```

## Results

64 vCPU, 9 MPI ranks × 8 threads (the leader rank idles, so 8 × 8 fill the box), same RELION 5.1.0
on every chip (aarch.bio arm64 / bioconda x86 builds), same staged bytes:

| chip | instance | refine wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.16xlarge` | 444 s | 550 s | 20.3 GiB | 2.5523 | **0.315** | **0.390** |
| Intel (Xeon 6) | `c8i.16xlarge` | 648 s | 734 s | 22.9 GiB | 2.9987 | 0.540 | 0.611 |
| AMD (EPYC 9R45) | `c8a.16xlarge` | **384 s** | **521 s** | 24.2 GiB | 3.4486 | 0.368 | 0.499 |
| Intel, `--cpu` kernels | `c8i.16xlarge` | 751 s | 854 s | 21.2 GiB | 2.9987 | 0.626 | 0.711 |

- **1.46× faster and 42% cheaper in compute (36% billed) on Graviton4**, on the same code path.
  Per vCPU, which is what is rented: 64 cores on `c8g`, 32 cores × 2 hyperthreads on `c8i`.
  `avg_cores` 47.3 and 51.5 of 64: the idle leader and the serial Maximization between
  Expectation passes are the gap.
- **AMD is 1.16× faster than Graviton4 and 1.69× faster than Intel**, on the same code path, with
  64 full cores on `c8a` (`avg_cores` 48.2 of 64). Its hour is 35% dearer than Graviton4's, so it
  costs 17% more in compute and 28% more billed; against Intel it is 32% cheaper in compute.
- **n = 3 on Graviton4 (444, 444, 451 s), n = 2 on Intel (648, 651 s) and n = 1 on AMD**, so the
  Graviton4–Intel difference is 30× the run-to-run spread. The rows show the first run of each. On
  AMD only the 9 × 8 default path was run; the `--cpu`, 9 × 1 and 3 × 1 rungs were not.
- **The x86 build's vectorised CPU kernels (`--cpu`) were slower here, not faster** (751 s vs
  648 s), and the arm64 build has none — its `--help` lists no `--cpu`. So the like-for-like
  comparison is the default path on both, which is also what the depositors' command asks for
  once `--gpu` is removed.
- **Fewer, wider ranks pay.** The same refinement at 9 ranks × 1 thread on an `m8g.2xlarge`
  (8 vCPU) took 2,439–2,480 s, and at 3 ranks × 1 thread on an `m8g.xlarge` 8,141–8,329 s
  (~$0.41): 64 vCPU is 5.5× the speed of 8 for 8× the cores.
- **Compute-dominated:** 86–106 s of boot, Docker install, image pull and staging around 7–11 min
  of RELION. The 0.88 GB of particles and references stage in seconds.
- Peak RSS is the container cgroup, 20–24 GiB of 128: 8 followers each pre-read the 0.58 GB
  particle stack (`--preread_images`).
- Prices are on-demand us-west-2 from the AWS Price List API; spawn printed none on these
  launches.

**GPU is the normal platform.** The depositors ran this job as 5 ranks × 6 threads on four GPUs
(`--gpu "0:1:2:3"`), and their next job started 11 m 46 s after it, an upper bound on its wall.
Refinement and classification are where cryo-EM's compute goes, and RELION's GPU path is what its
authors tune for. This page answers whether a CPU box completes a real refinement correctly and
what it costs — not whether to prefer one over a GPU, which is Round Two.

## Why this is a real refinement

It is **100% of one refinement**: initial global searches at 50 Å through 16 iterations of
auto-refinement, local searches once the angular step drops below the estimated accuracy, and the
final iteration to Nyquist with the two halves combined. Nothing was shortened. It is the
refinement every RELION user runs first, on the authors' own teaching dataset, with the authors'
own flags. What it is *not* is a publication-scale particle count: 4,452 particles is the
tutorial's 24 movies, where a deposited structure typically refines 10⁵ or more. Expectation cost
grows roughly with particle count, so read the timings as per-4,452-particle, and do not
extrapolate the iteration count — larger datasets converge differently.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| MPI layout, from RELION's own `run.out` | exactly the ranks × threads launched | 9 × 8 (and 3 × 1) | 9 × 8 | 9 × 8 |
| converged, and RELION marked the job successful | `Refinement has converged, stopping now` + `RELION_JOB_EXIT_SUCCESS` | every run | every run | yes |
| every particle in, every particle out | 4,452 = 4,452, same image names | 4,452 | 4,452 | 4,452 |
| **half-set split equals the depositors', particle for particle** | 4,452 of 4,452, seeded with their recorded `--random_seed 1697716706` | 4,452 (2,220 / 2,232) | 4,452 | 4,452 |
| **control: their half-maps through `relion_postprocess` reproduce their PostProcess** | 129 shells × 8 columns, max\|diff\| ≤ 1e-6; resolution and B-factor equal | **0.000e+00**, 3.153109 Å, −31.39202 | **0.000e+00** | **0.000e+00** |
| our map, post-processed the same way | > Nyquist (2.489 Å) and ≤ 4.0 Å | 3.153109 Å | 3.153109 Å | 3.153109 Å |

The half-set identity holds because RELION draws the split from the seed before any floating
point happens; the control holds because post-processing is deterministic arithmetic over fixed
maps (the [recipe](../../recipes/relion/README.md)'s own reproduction, here on a second dataset
and with an MTF). Neither depends on the chip, and neither did.

## Fixed point or path

The refinement is a **path**: each iteration's orientations feed the next reconstruction, so a
last-bit difference anywhere is carried forward. Which layouts reproduce, measured on the
`run_data.star` each run writes (every particle's angles and offsets):

| run | layout | `run_data.star` | iterations | auto B-factor | final resolution |
|---|---|---|---|---|---|
| x86, run 1 and run 2 | 9 × 8 | **byte-identical** (`b8d0cd0c…`) | 16 | −34.71771 | 3.153109 Å |
| AMD, run 1 | 9 × 8 | **byte-identical to Intel's** | 16 | −34.71771 | 3.153109 Å |
| Graviton4, run 1 and run 2 | 3 × 1 | **byte-identical, and identical to x86's** | 16 | −34.71771 | 3.153109 Å |
| Graviton4, runs 1–3 | 9 × 8 | all three differ | 16, 16, 16 | −34.93, −37.26, −36.12 | 3.153109 Å |
| Graviton4, 9 × 1, runs 1–2 | 9 × 1 | both differ | 15, 16 | −30.20, −36.26 | 3.153109, **3.184640** Å |
| x86, `--cpu` kernels | 9 × 8 | differs | 15 | −31.15 | 3.153109 Å |
| depositors, 4 GPUs | 5 × 6 | — | 16 | −31.39 | 3.153109 Å |

- **A fixed seed and a fixed layout make the x86 refinement reproducible, and with one follower
  per half-set Graviton4 lands on exactly the same answer.** Every particle's assignment is
  identical across the two chips; the maps then differ only in the last bits (the post-processed
  FSC in the 7th digit). With more followers per half on Graviton4 — at 8 threads *or* at 1 — no
  two runs agreed, which points at how work is split across followers rather than at threads or at
  the chip's arithmetic. Why x86's 9 × 8 reproduces and Graviton4's does not is not established
  here.
- **AMD lands on Intel's refinement exactly.** `run_data.star`, `run_model.star` and
  `run_optimiser.star` are byte-identical to the Intel 9 × 8 run's, and so is the whole in-task check
  output. The maps differ only in the last bits: one value of the 1,032 in our post-processed FSC
  table differs, in its 6th digit (shell 108, −4.83426e-04 against −4.83427e-04), and the map-to-map
  FSC against the depositors' map is the same file. So AMD against Graviton4 is the Intel-against-Graviton4
  comparison above, at every layout measured.
- **Resolution moves too, but rarely:** 11 of 12 refinements, including the depositors'
  single-precision GPU run, land on 3.153109 Å; one Graviton4 9 × 1 run stopped one Fourier shell
  coarser, at 3.184640 Å. Unmasked resolution was 3.793783 Å in all twelve. So the resolution is
  reported, not asserted, and the only resolution bound above is a gross-failure guard.
- **The maps agree where they should:** against the depositors' final map, the map-to-map FSC of
  every default-path run stays ≥ 0.665 to Nyquist (the `--cpu` run: ≥ 0.413), and their corrected
  gold-standard FSC curves differ from the depositors' by at most 0.034. The same particles,
  refined independently, give the same structure to within noise.
- To make your own CPU refinement reproducible, fix `--random_seed` and the layout, and check the
  `run_data.star` digest across two runs before you rely on it.

The 9 × 1 and 3 × 1 replicates come from `determinism.task.json` and `serial.task.json` (same
`run.sh`, layout and box changed). The 9 × 1 pair predates the `check.py` clause that accepts
RELION's omission of the threads line at one thread, so their check reports that one line as a
failure; every other assertion passed and their digests are what the table uses.

## Pins

| | |
|---|---|
| data | `relion50_tutorial_precalculated_results.tar.gz` (MRC-LMB FTP, 11,326,844,944 B, 2023-10-26), sha256 `7f799e1a…5711d3`, hashed on the same stream it is extracted from; the 40 extracted files are each pinned in `inputs.sha256` and re-checked in-task |
| image | arm64 `quay.io/aarchbio/relion@sha256:74aef381…` (5.1.0); x86 `quay.io/biocontainers/relion@sha256:1a60aa8f…` (5.1.0) — the recipe's pins |
| scripts | `run.sh`, `check.py`, `inputs.sha256`, `fetch.sh`, each pinned by sha256 in its spec; `fetch.task.json` runs in the catalog's pinned `aarchbio/metaphlan` image only because it ships `curl` |

**Data tier: a versioned public release, not RODA.** The RODA cryo-EM archive this recipe already
uses (`cryoem-spa-workflow-records-public`) holds project *metadata and maps* but no particle
stacks, so it cannot feed a refinement. The RELION authors' tutorial release is versioned by its
filename and has not changed since 2023-10-26; `fetch.sh` streams it on an `m8g.large` (446 s, the
11.3 GB never written to disk) and keeps 0.88 GB.

**AMD leg:** one launch, first-attempt green, $0.499 billed; its check output and digests are in
[`results/amd/`](results/amd/). It ran the committed spec with spawn
0.131.1 built from its release tag, because the installed 0.139.1's own wrapper leaves too little of
EC2's 16 KiB user data (spawn#1246), and with `cost_limit` lowered from the resolved $6.40 to $3.00.
The x86 image's BusyBox has no `lscpu`; 8a vCPUs are full cores (the `c8a`/`r8a` boxes of the other
AMD legs report 1 thread per core).
