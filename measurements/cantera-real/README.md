# Cantera on GRI-Mech 3.0's own validation set: Graviton4, Intel and AMD

> **Cantera reproduces GRI-Mech 3.0's published ignition-delay predictions to a median 1.5% (13
> targets), and every flame, reactor and equilibrium identity holds on all three chips.** The flame
> speeds GRI-Mech published sit at the coarse end of a grid ladder: converged, Cantera gives 0.5–5%
> less. The shipped `gri30.yaml` equals the official 1999 files bit for bit. AMD is fastest and
> cheapest ($0.041 per study billed, against $0.051 on Graviton4 and $0.068 on Intel).

Workload: what a mechanism study runs, on the mechanism that ships with Cantera.

- **The mechanism check**: the official GRI-Mech 3.0 files (`grimech30.dat`, `thermo30.dat`,
  `transport.dat`) converted by `ck2yaml` on the box and compared with the packaged `gri30.yaml`.
- **224 laminar flames** (`FreeFlame`): the 8 flame-speed targets on a 4-rung grid ladder, plus
  multicomponent + Soret and unity Lewis number. Also the flame-speed curves a paper plots: CH₄-air
  φ 0.6–1.6 at six (P, T₀) up to 19.7 atm / 400 K, C₂H₆-air at 1 atm, and CH₄-air at unity Lewis
  number.
- **252 reactors**: the 16 shock-tube ignition targets, Seery & Bowman's constant-density curve, a
  CH₄-air map (φ 0.5/1/2 × 1/10/40 atm × 1100–2000 K) at constant volume, and its φ = 1 slice at
  constant pressure. Every reactor is integrated to t = 10⁵ s.
- **2,420 equilibrium states** × 3 solvers (`vcs`, `gibbs`, `element_potential`): CH₄, C₂H₆, C₃H₈,
  H₂ and CO/H₂ in air, φ 0.25–4, 1000–3000 K, 0.1–100 atm.

## Run it

```sh
B=$(make -s print-bucket)
measurements/cantera-real/stage-inputs.sh "$B"     # the GRI-Mech 3.0 files + 24 target pages, sha256-pinned
python3 measurements/cantera-real/mkspec.py r2 c8g 8
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py cantera $a $B r2 measurements/cantera-real/real.task.json)" --wait
done
python3 measurements/cantera-real/collect.py <dir holding each chip's results.jsonl as arm64/ x86_64/ amd/>
```

`real.py` runs the 498 jobs 8 at a time, one thread per process, then runs every check below. A
check that fails fails the task.

## Results

8 workers on a `2xlarge` (8 vCPU, 16 GiB) of each chip, the same `comp-chem` env (aarch.science
arm64; its linux-64 twin on both x86 chips), the same staged bytes:

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 496 s | 577 s | 1.74 GiB | 0.3190 | 0.0440 | 0.0511 |
| Intel (Xeon 6975P-C) | `c8i.2xlarge` | 602 s | 657 s | 4.52 GiB | 0.3748 | 0.0627 | 0.0684 |
| **AMD (EPYC 9R45)** | `c8a.2xlarge` | **294 s** | **342 s** | 4.35 GiB | 0.4311 | **0.0352** | **0.0410** |

A *result* is the whole study, every check passed. *Tool wall* is `real.py`. *Billed* is launch to
the task's end. *Peak mem* is cgroup `memory.peak`, which counts page cache: on x86 that includes
the env twin installed at task start. No single worker went above a few hundred MB.

- **AMD is 1.69× faster than Graviton4 and 20% cheaper per study**, even at a 35% dearer hour.
  Intel's 8 vCPUs are 4 cores × 2 threads (`Thread(s) per core: 2`), and 8 single-threaded workers
  on 4 cores is 1.21× slower than Graviton4's 8 full cores. AMD's and Graviton4's are 8 full cores.
- **Flames are 97% of the CPU** (about 3,850 of 3,960 core-seconds on Graviton4). The 252 reactors take
  64 s, and the 7,260 equilibrium solves 5 s. The slowest single flame (CH₄-air φ 0.9 at 19.7 atm,
  75 s) is shorter than the queue, so all 8 workers stay busy to the end (8.0 cores average).
- **These timings are not compute cost.** At this size, boot, image pull and staging are 48–81 s
  of each window.
- n = 1 per chip. Every cell is deterministic: the run repeats a flame and a reactor in one process
  and asserts identical bits.

## Why this size

This is GRI-Mech 3.0's own flame-speed and ignition validation set, with the sweeps a combustion
paper validates a mechanism on: flame-speed curves across φ and pressure, ignition maps across T, P
and φ, and the equilibrium tables a CFD chemistry lookup uses. A study with a 50-species mechanism
is minutes of work. That is the honest scale for GRI-Mech, and what most Cantera users run. A
larger mechanism would scale the flame cost (roughly with species²), not change what is checked.

## What held at scale, on all three chips

| assertion | bound | observed (Graviton4; x86 within the same bounds) |
|---|---|---|
| packaged `gri30.yaml` == `ck2yaml` of the official GRI-Mech 3.0 files: k_f, k_r, h/RT, s/R, c_p/R, viscosity, conductivity, mixture, binary and thermal diffusion coefficients, 84 (T, P) states | exactly 0 | 0 in every property, 53 species and 325 reactions, same order and duplicate flags |
| `targets.tsv` == the 24 pinned target pages (values, mixture, φ, P, T) | exact | all 24 |
| every flame converges, S_u > 0 | 224 of 224 | 224, 56,087 grid points |
| continuity: ρu uniform through the flame | < 1e-4 (the steady solver's rtol) | 2.41e-05 |
| Σ Y = 1 at every grid point | rtol + K·atol = 1.0005e-4 | 2.17e-09 |
| **unity Lewis number: element mass fractions equal the inlet's at every grid point** | < 1e-5 | 3.05e-07 over 29 flames |
| control: mixture-averaged / multicomponent flames move them (differential diffusion) | > 100× the above | least 2.76e-03 |
| every reactor ignites | T rise > ½ its equilibrium rise | 252 of 252 |
| constant volume: density | < 1e-12 | 0 |
| constant volume: internal energy, constant pressure: enthalpy | ≤ rtol × CVODES steps, per trajectory | at most 1.8% of that bound (Intel/AMD 3.8%) |
| elements through every trajectory | < 1e-9 | 6.9e-13 |
| **integrator end state == the equilibrium solver's UV / HP state** | 1e-3 K, max\|ΔX\| 1e-6 | 1.32e-4 K, 1.9e-7 |
| page φ == the φ of the page's own mole fractions, within print precision | all but IG.6a/6b, those two off by one shared factor 2.75/3.5 | as bound (below) |
| `vcs` converges | every state | 2,420 of 2,420 |
| **Gibbs-minimum certificate** μ_k/RT = Σ_j a_kj λ_j, every species above 1e-12, every solver | ≤ 1e-6 (VCS's own minor-species tolerance, `m_tolmin`) | vcs 6.7e-07, gibbs 7.9e-10, element_potential 3.8e-13 |
| `gibbs` and `element_potential` == `vcs` where they converge | max\|ΔX\| < 1e-6 | 5.6e-10, 5.2e-10 |
| elements through every equilibrium solve | < 1e-9 | 2.4e-10 |

The u/h bound came from a failed first run. The first spec asserted a flat 1e-8, a number picked
before any run. Intel then measured 1.66e-8 on the 40 atm φ = 1 reactor at 1250 K, which took
3,838 steps (`results/r1-first-run/`). The integrator carries T and Y, not u, so u drifts by the
per-step error summed over steps, and CVODES holds each step to rtol = 1e-10. The bound is now
rtol × steps for each trajectory. All three chips were re-run on the changed spec, and every chip
sits at under 4% of the bound.

**Not asserted, because it does not hold:** the outlet of a mixture-averaged flame is not at the
inlet's element composition or enthalpy (differential diffusion carries them, up to 207 K-equivalent
inside the flame). And no flame's outlet, in any transport model, is the HP equilibrium state: it
is up to 13 K off at a 3–6 cm outlet. Unity
Lewis number is the case where the theory says element fractions are constant, and that is what is
asserted.

## Against GRI-Mech's published numbers (reported, not asserted)

**Ignition (max dT/dt, constant volume) vs GRI-Mech 3.0's own SENKIN predictions:** median 1.5%
over the 13 targets with a GRI-Mech 3.0 value. 9 are within 2% (IG.1a 664.7 vs 666 µs, IG.St1a
1,234 vs 1,240). IG.T1 is 4.3% off and IG.St4a, at 34.6 atm, 8.5%. The pages do not say which
ignition criterion each target used, so the tolerance can't be justified and nothing is asserted.
Against experiment, the median is 9.3%. That measures the mechanism, not the code.

**Flame speed** (cm/s) on the Graviton4 run. The ladder is mixture-averaged at refine slope 0.1 → 0.0125
(`curve` = 2 × slope), then multicomponent + Soret at 0.025:

| target | 0.1 | 0.0125 | multi + Soret | GRI-Mech 3.0 published | experiment |
|---|---|---|---|---|---|
| F1 CH₄ φ 0.98, 1 atm | 37.96 | 36.99 | 36.81 | 37.92 | 35.55 |
| F4 CH₄ φ 1.0, 3 atm | 24.61 | 24.12 | 24.09 | 25.46 | 22.7 |
| F6 CH₄ φ 1.0, 19.7 atm, 400 K | 17.27 | 16.82 | 16.96 | 17.18 | 20.4 |
| SF7 CO/H₂ φ 1.69 | 194.85 | 194.53 | **179.78** | **181.6** | 180 |
| StF8 C₂H₆ φ 1.0 | 42.83 | 41.64 | 41.48 | 42.57 | 40.21 |

GRI-Mech's CH₄ and C₂H₆ flame speeds match Cantera's **coarsest** grid to 0.1–1.3% (F1, F2, F6,
StF8). The converged values are 0.5–5% lower. The site itself warns that "some computational
inaccuracies exist in the flame speed computations." SF7, the H₂-rich flame, is where transport
models diverge (mixture-averaged 194.5, multicomponent 179.8), and GRI's 181.6 sits with
multicomponent. All 8 rows are in `results/*/check.txt`.

**A slip on two target pages:** IG.6a and IG.6b state φ = 0.764 and 0.275. Their own mole fractions
(0.5% C₂H₆ with 1.8% / 5.0% O₂) give 0.972 and 0.350, so both are off by the same factor, 2.75/3.5.
That is a stoichiometric O₂ of 2.75 per C₂H₆ instead of 3.5. The mole fractions are what is
simulated. The run asserts that these two are the only mismatches and that they share the factor.

**What the solvers can't do:** within GRI-Mech's 300–3000 K thermo range, `gibbs` fails on 19 of
2,420 states (rich C₂H₆/C₃H₈ near 3000 K, and stoichiometric CH₄ at 100 atm, 1600–1800 K) and
`element_potential` on 4 (stoichiometric at 1000 K, 100 atm). `vcs` converges everywhere. Above
3000 K, `element_potential` refuses: the thermo fits end there. `vcs` and `gibbs` extrapolate the
polynomials, which is why the table stops at 3000 K.

## Fixed point or path

- **Intel and AMD are bit-identical to each other on everything**: all 224 flame speeds, all 252
  ignition delays and end states, all 7,237 converged equilibria. Both run the same linux-64 twin,
  and OpenBLAS picks `Cooperlake` on both (Graviton4: `neoversev2`). The results follow the build,
  not the vendor.
- **Equilibrium is a fixed point**: x86 vs Graviton4, g/RT is within 1.1e-15 relative for `vcs`,
  1.7e-15 for `gibbs` and 4.3e-12 for `element_potential` (bit-identical on 996, 932 and 519 states).
  The UV equilibrium temperatures agree to 3.6e-14.
- **Reactor trajectories are paths**: no ignition delay is bit-identical across the build boundary.
  The largest difference is 8.4e-7 relative (IG.6b), and end temperatures differ by 1.3e-9.
- **Adaptive flames are paths too.** The grid refiner's decisions move with the last bits, so 20 of
  224 flames end on a different number of points. The largest S_u difference is 0.41% (CH₄ φ 1.45,
  4.9 atm, 205 vs 209 points), and 0.20% on a same-size grid. That is the size of the grid error
  the ladder shows, not solver noise: each flame converges to rtol 1e-4 on its own grid.

## Pins

| | |
|---|---|
| input | GRI-Mech 3.0 (`combustion.berkeley.edu/gri-mech/version30`): `files30/grimech30.dat`, `thermo30.dat`, `transport.dat` and the 24 flame-speed and ignition target pages under `targets30/`. 27 files, 156 KB, each pinned in `sources.sha256` and re-checked in every run. `targets.tsv` is transcribed from the pages, and every run re-parses the pages and asserts the transcription. |
| image | arm64 `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec…` (the recipe's pin); x86 the env's linux-64 twin `envs-x86_64/comp-chem-1243f25ebfec` |
| versions (all chips) | cantera 3.2.0, numpy 2.5.4 |
| scripts | `run.sh`, `real.py`, `targets.tsv` and `sources.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it). `collect.py` runs locally on the three outputs. |

**Data tier: a durable public id.** GRI-Mech 3.0 is the 1999 release, served unchanged from its
home page, which Cantera's own `gri30.yaml` cites. It has no DOI, so the sha256 of every file is
the pin. The per-chip `check.txt`, `host.txt`, `footprint.txt` and `phases.txt` are in `results/`,
and the cross-chip comparison is in `results/crosschip.txt`. Each run's per-job `results.jsonl`
(1.6 MB) is in the bucket under `runs*/cantera-real/r2/`.
