# REBOUND on the DE440 Solar System for 100 Myr — Graviton4, Intel and AMD

> **REBOUND integrates the whole Solar System for 100 Myr, from JPL's DE440 at J2000, in 2.4–2.5 h on
> Intel or AMD and 4.0 h on Graviton4. Intel is cheapest at $0.48 billed, against $0.53 on AMD and
> $0.65 on Graviton4.** WHFast's step is 1.6× slower on Graviton4, and that decides the ranking. Every
> conservation and order law holds on all three chips. Against DE440 itself, Mercury lands 74 m off
> after a year once general relativity is added, and its relativistic perihelion advance comes out at
> 42.96″ per century. The chaotic 100-Myr trajectory is bit-identical on all three chips.

Workload: the Sun and the nine planetary-system barycentres (Mercury … Pluto, with the Earth–Moon
barycentre for Earth) at **J2000.0 TDB**. Positions, velocities and GM values all come from JPL's
**DE440** ephemeris. REBOUND does what a dynamicist does with them:

- **Against the ephemeris, over DE440's whole span.** IAS15 runs from J2000 forward to 2600 and back
  to 1600, Newtonian and with a Sun-centred 1PN (general-relativity) force. It is compared with DE440
  at ±1, 10, 100 and +300 / −400 / +600 yr.
- **The long term.** WHFast with 17th-order symplectic correctors at dt = 4 days integrates the whole
  system for **100 Myr** (9.13 billion steps), checkpointed every 0.5 Myr. A *shadow* run alongside it
  differs only in Mercury, moved 1 m at J2000.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/rebound-real/stage-inputs.sh "$B"     # fetch DE440 on AWS once (00-fetch), check pins, stage scripts
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py rebound $a "$B" r1 measurements/rebound-real/real.task.json)"
done
python3 measurements/rebound-real/collect.py <dir holding each chip's outputs as arm64/ x86_64/ amd/>
```

[`ic.py`](ic.py) builds the simulation from DE440 with `jplephem`. [`real.py`](real.py) does the
DE440-span work, [`long.py`](long.py) is one long run, and [`longcheck.py`](longcheck.py) checks the
pair. [`run.sh`](run.sh) checks the staged bytes against [`pins.sha256`](pins.sha256). It then runs
the nominal long run on one core, while a second core runs `real.py` and then the shadow.
[`mkspec.py`](mkspec.py) writes the specs and pins the staged scripts by sha256.
[`probe.task.json`](probe.task.json) is the same task at 1 Myr, the run the boxes were sized from.

```python
sim = rebound.Simulation(); sim.G = 1.0                      # AU, day; each mass is DE440's GM
for gm, x, v in zip(gm_de440, pos_j2000, vel_j2000):         # Sun + 9 barycentres, from de440.bsp
    sim.add(m=gm, x=x[0], y=x[1], z=x[2], vx=v[0], vy=v[1], vz=v[2])
sim.integrator = "whfast"; sim.dt = 4.0
sim.integrator.corrector = 17; sim.integrator.safe_mode = 0
sim.steps(45_656_250); sim.synchronize()                     # one 0.5 Myr checkpoint
```

## Results

4 vCPU / 8 GiB (`xlarge`) of each chip, two single-threaded REBOUND processes at a time. The same
`astro` env runs everywhere: aarch.science's arm64 image, and its `astro-c7d877c02e6d` linux-64 twin
on both x86 chips. The staged bytes are the same too.

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.xlarge` | 14,535 s | 14,687 s | 51 MB | 0.1595 | 0.6441 | 0.6508 |
| **Intel (Xeon 6975P-C)** | `c8i.xlarge` | 9,053 s | 9,170 s | 54 MB | 0.1874 | **0.4713** | **0.4774** |
| AMD (EPYC 9R45) | `c8a.xlarge` | 8,700 s | 8,801 s | 54 MB | 0.2155 | 0.5209 | 0.5269 |

A *result* is one 100-Myr nominal + shadow pair plus the DE440-span checks. *Tool wall* runs from the
first integration to the last check, and *billed* from EC2 launch to terminate, at spawn's printed
price. *Peak RSS* is `ru_maxrss` of the largest process (`real.py`; each long run peaks at 40–44 MB).

- **One WHFast step costs 1.57 µs on Graviton4, 0.98 µs on Intel and 0.94 µs on AMD.** That is the
  whole bill: the long runs are 99% of the wall, and each is one single-threaded process. Graviton4's
  hourly price is 15% below Intel's and 26% below AMD's, which does not make up the 1.6×. The IAS15
  work in `real.py` (142 / 96 / 81 s) ranks the chips the same way.
- **Intel's 4 vCPUs are 2 cores × 2 threads** (`Thread(s) per core: 2`); Graviton4's and AMD's are 4
  full cores. Two processes ran on each box, so every process could have its own core. Two vCPUs sat
  idle on Graviton4 and AMD; running four ensemble members there was not measured.
- **Memory is irrelevant.** 40–54 MB per process. The box is sized by cores, not RAM.
- **Boot, staging and termination are 101–152 s of a 2.4–4.0 h bill.** Here the timings
  are nearly all compute, unlike most recipes in this catalog.
- n = 1 per chip. Every output is bit-identical on the three chips (below), so a repeat would measure
  only timing noise.

## Why this size

The inner Solar System's Lyapunov time is about 5 Myr (Laskar 1989; Sussman & Wisdom 1992), and
100 Myr is twenty of them. That is the span over which a 1 m uncertainty in Mercury grows to a
measurable change in its orbit (below). Billion-year studies (Laskar & Gastineau 2009) run ensembles
of longer integrations of this kind, so this is the first 100 Myr of one member, at its real cost. dt = 4 days is about
P_Mercury / 22. IAS15 covers DE440's full 1550–2650 span, the whole interval the ephemeris exists for.

## What held at scale, on all three chips

| assertion | why it must hold | observed (identical on all three) |
|---|---|---|
| staged `de440.bsp`, `gm_de440.tpc` match their pins; ten bodies; DE440 covers every checkpoint | the input is the published ephemeris | sha256 OK; N = 10; J2000 −450 … +650 yr |
| `reb_check_fp_contract() == 0` | REBOUND's own `test_fpcontract`: no fused multiply-adds | 0 |
| IAS15 \|ΔE/E\| and \|ΔL\|/\|L\| < ε√N, forward 600 yr and back 400 yr | IAS15's error is a round-off random walk (Rein & Spiegel 2015) | 3.1e-15 / 3.8e-16 < 7.0e-14 (N = 98,161); 3.6e-15 / 3.8e-16 < 5.7e-14 |
| **±1 yr with the 1PN Sun, every body but the EMB within 1e-8 of its distance from DE440** | a band set from the first Graviton4 run; the EMB is reported, below | worst 1.1e-9 (Mercury, 74 m) |
| **Newtonian Mercury error > 10× the 1PN error, at +600 and −400 yr** | GR is the physics DE440 has and a Newtonian model lacks, for Mercury | 45,772 vs 9.6 km (4,790×); 28,527 vs 49 km (579×) |
| **Mercury's 1PN − Newtonian perihelion advance == 6πGM/(c²a(1−e²)) per orbit, ±1%** | Einstein's precession, from DE440's a and e | **42.958 / 42.996 vs 42.981″ per century** |
| plain WHFast: halving dt (4 → 2 → 1 d) cuts max \|ΔE/E\| by 4 ± 10%, over 1,000 yr | WHFast is a second-order method | ×4.040, ×4.026 |
| WHFast \|ΔL\|/\|L\| < 10ε√N at every dt | every WHFast sub-step conserves L exactly, so only round-off remains | 1.9e-14 … 8.3e-14 |
| 17th-order correctors beat plain WHFast at dt = 4 d by > 100×; \|ΔE/E\| < 1e-9 | the correctors' purpose; 1e-9 is REBOUND's own `test_whfast_advanced` bound for corrector 17 | 7.9e-13 vs 8.2e-10 |
| two IAS15 reruns to +100 yr give identical bytes | determinism | identical |
| **both 100-Myr runs: all 201 checkpoints; \|ΔE/E\| < 1e-9; \|ΔL\|/\|L\| < 10ε√N; every orbit bound** | the same laws, at 9.13 billion steps | ΔE 1.46e-11 / 9.97e-12; ΔL 1.02e-11 / 4.1e-12 < 2.1e-10; max e 0.275 (Pluto) |
| the shadow starts 1 m from the nominal run | the experiment is what it says | 1.000002 m |

The perihelion advance is a differential measurement. The two IAS15 runs start from the same DE440
state, and the angle between their Laplace–Runge–Lenz vectors is fitted against time. The planets'
own 532″/century cancels to first order, which is why 1% has wide headroom (≤ 0.06% observed).

## What is reported, not asserted

- **DE440 residuals** (heliocentric, km, the 1PN model):

  | | Mercury | Venus | EMB | Mars | Jupiter | Saturn | Uranus | Neptune | Pluto |
  |---|---|---|---|---|---|---|---|---|---|
  | +1 yr | 0.074 | 0.039 | 117 | 0.067 | 0.19 | 0.25 | 0.28 | 0.29 | 0.24 |
  | +100 yr | 6.3 | 4.4 | 11,700 | 26 | 33 | 17 | 154 | 960 | 739 |
  | +600 yr | 9.6 | 43 | 70,100 | 390 | 202 | 96 | 1,200 | 7,660 | 11,500 |

  The EMB's residual grows linearly, about 117 km a year: a mean-motion offset. The Earth and Moon
  are modelled as a single point mass at their barycentre. That is the model's largest omission
  there, and the likely cause, but it was not tested. The outer planets' residuals grow toward the
  1,000-km level by a century. DE440 also integrates asteroids and Kuiper-belt mass, which this
  ten-body model leaves out. Without GR, Mercury is 7,370 km off at +100 yr.
- **The long run's integrator against IAS15**: the worst body differs by 3.0 km at +100 yr and 21 km
  at +600 yr.
- **The shadow.** Mercury's position separates first by shear: the 1 m shift changes its mean motion.
  The separation reaches 13,000 km at 1 Myr and 1.9 million km at 100 Myr, so position is the wrong
  quantity for chaos. Its eccentricity is not sheared:

  | Myr | 1 | 10 | 25 | 50 | 75 | 100 |
  |---|---|---|---|---|---|---|
  | \|Δe\|, Mercury | 3.0e-10 | 1.6e-8 | 3.4e-8 | 4.2e-7 | 7.6e-6 | 2.6e-5 |

  The envelope e-folds in 8.3 Myr over 10–100 Myr, and in 6.9 Myr over 20–80 Myr. That is the
  published ~5 Myr order. It is a coarse estimator: two trajectories, osculating elements sampled every
  0.5 Myr. `longcheck.py`'s position-based fit (26 Myr) is shear, not a Lyapunov time.
- **Over 100 Myr** Mercury's eccentricity ranges over 0.075–0.257 and its semi-major axis by 1.2e-5.
  The energy error grows from 8.4e-12 in the first 50 Myr to 1.46e-11 in the second, which is the
  shape of a round-off random walk.

## Across chips

- **Everything is bit-identical on all three chips**: the DE440 evaluations, every IAS15 position
  (Newtonian and 1PN), and **every one of the 201 checkpoints of both 100-Myr WHFast runs**, positions
  and velocities. [`results/crosschip.txt`](results/crosschip.txt) has the comparison.
- **Fixed point or path.** A 100-Myr N-body trajectory is the extreme case of a path. The shadow shows
  what one perturbation does to it: 1 m at J2000 becomes 1.9 million km. Any single rounding that
  differed between chips would grow the same way. None did, so these trajectories are a path that
  happens to agree to the bit, not a fixed point. That agreement rests on the build. REBOUND is
  compiled with FP contraction off, asserted on every chip, so a·b + c is never fused on one chip and
  not on another. The arm64 and linux-64 builds of rebound 5.0.1 evidently compute the same IEEE
  operations in the same order. A different compiler flag, a different REBOUND version or OpenMP
  would void this. Don't treat bit-identity as a property of N-body integration.
- OpenBLAS reports `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips. REBOUND calls no BLAS,
  so those kernel names don't bear on these results.

## Pins

| | |
|---|---|
| ephemeris | `de440.bsp` (119,799,808 bytes) and `gm_de440.tpc`, from NAIF's generic kernels (`naif.jpl.nasa.gov/pub/naif/generic_kernels/{spk/planets,pck}/`) |
| both | sha256 in [`pins.sha256`](pins.sha256), recorded by [`00-fetch.task.json`](00-fetch.task.json) on AWS and re-checked in-task (`results/<chip>/staged.sha256`) |
| epoch | J2000.0 TDB = JD 2451545.0; DE440's AU, 149,597,870.700 km |
| image | arm64 `quay.io/aarchsci/astro@sha256:9568095c…` (the recipe's pin); x86 its `astro-c7d877c02e6d` twin |
| versions (all chips) | rebound 5.0.1, jplephem 2.24, numpy 2.5.3 |

**Data tier: a versioned public release.** DE440 is a frozen JPL product (Park et al. 2021, AJ 161:105,
doi:10.3847/1538-3881/abd414), with no copy on the Registry of Open Data on AWS. `00-fetch` downloads
it from NAIF inside a spawn task, so nothing touches a laptop. NAIF publishes no checksums, so the sha256
recorded at fetch is the pin, and a re-fetch that differs aborts `stage-inputs.sh`. Per-chip
`check.txt`, `results.json`, `long.json`, `host.txt` and `footprint.txt` are in [`results/`](results/).
The checkpoint arrays (`long-*.npz`, `ephem.npz`) are in each run's bucket prefix.

## Caveats

One system, one timestep, one size (`xlarge`), n = 1 per chip. Neither generation nor core count was
swept, because each REBOUND process is single-threaded. The 1PN force is the Sun-centred
test-particle form, added through `additional_forces`, not REBOUNDx (which the `astro` env does not
ship). The spend for this write-up was **$1.71**: the DE440 fetch, three 1-Myr sizing probes and the
three runs above.
