# MODFLOW 6 on a published USGS regional model: Mobile Bay, 1908–2015, three chips

> **AMD is 1.44× faster than Graviton4 and cheapest per result. Graviton4 and Intel tie on
> cost.** Every identity holds on all three chips. All three reproduce the USGS archive's own heads,
> 99% of cells to within 3.2e-4 m. Intel and AMD agree bit for bit. Graviton4 differs from them by
> ~1e-10 m at the median cell, and by up to 0.49 m at no more than 4 of 42,822 cells.

Workload: **the Mobile Bay groundwater model exactly as USGS published it** (Richins, Duncan and
Befus, 2026, [doi:10.5066/P1XKGCSZ](https://doi.org/10.5066/P1XKGCSZ); report
[SIR 2026-5125](https://doi.org/10.3133/sir20265125)). It is one layer of 260 × 232 cells (42,822
active) under the Newton formulation. The model has 14,797 SFR stream reaches, 30,761 UZF
unsaturated-zone cells with 25 trailing waves, RIV, WEL and CHD. It runs 97 stress periods: a
100-year warm-up, then monthly from 2008 to 2015. The archive holds two simulations. *postcal* is the
calibrated model the report presents. *precal* is the same model on its pre-calibration parameters.
Both run here, one mf6 process each, side by side. The archive's own runs took 2 h 41 min and 57 min
on a Windows PC. A published regional model at its published size is what MODFLOW is used for, and
the archive's outputs give a reference to compare against.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/modflow-real/stage-inputs.sh "$B"   # the archive is fetched ON AWS and MD5-checked
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py modflow $a "$B" "$(date +%s)" measurements/modflow-real/real.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py modflow arm64 "$B" "$(date +%s)" measurements/modflow-real/03-compare.task.json)"
```

`real.task.json` unpacks the archive. It makes two edits to the inputs: the Windows path separators
become `/`, and `SAVE_FLOWS` goes into the name file (both below). Then it runs `mf6` on both
simulations and checks each one (`real.py`). `03-compare.task.json` compares the three chips with each
other and with the archive (`compare.py`). The x86 runs use the recipe's `geoscience` env twin. It has
the same modflow6 6.8.1 and flopy 3.11.0.

## Results

`m8g/m8i/m8a.xlarge` (4 vCPU, 16 GiB). Two single-threaded mf6 processes run at once. Same staged
bytes.

| chip | instance | cores × threads | tool wall (postcal / precal) | billed | peak RSS (mf6 / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.xlarge` | 4 × 1 | 4090 / 2598 s | 4247 s | 552 MiB / 3.4 GiB | 0.1795 | 0.204 | 0.212 |
| Xeon 6975P-C | `m8i.xlarge` | 2 × 2 | 3467 / 2131 s | 3586 s | 554 MiB / 4.5 GiB | 0.2117 | 0.204 | 0.211 |
| **EPYC 9R45** | `m8a.xlarge` | 4 × 1 | **2831 / 1746 s** | **2921 s** | 554 MiB / 4.5 GiB | 0.2434 | **0.191** | **0.197** |

A *result* is both published simulations, solved and checked. *Tool wall* is each `mf6` process,
launch to exit. The longer one, postcal, sets the compute cost. *Billed* runs from EC2 launch to
termination at spawn's printed price. *Peak RSS* is each mf6's `ru_maxrss`. *Box* is the cgroup's
`memory.peak`, which also counts page cache and, on x86, the env install.

- **AMD finishes first and is cheapest.** It is 1.44× faster than Graviton4 on postcal and 1.49× on
  precal. Its higher hourly price eats most of that, so it is 7% cheaper per result billed. Intel is
  1.18× faster than Graviton4 and costs 18% more per hour. The two land within $0.001 of each other.
- **The difference is per iteration, not iteration count.** The work is nonlinear outer iterations
  (Newton plus UZF and SFR), each one a sparse linear solve. Postcal took 26,992 outer iterations on
  Graviton4 and 26,771 on both x86 chips, counting 750 for each period that hit `OUTER_MAXIMUM`.
  That is 0.152, 0.130 and 0.106 s per iteration. Precal is the same shape: 17,748 against 17,007
  iterations, and 0.146, 0.125 and 0.103 s.
- **This box is half idle.** mf6 is serial: the conda build links only libgfortran, so there is no
  MPI and no BLAS. The two solves use 2 of the 4 vCPUs, and precal finishes 18–25 min before
  postcal on every chip. The `xlarge` was picked so that Intel's two solves each get a physical
  core. On Graviton4 and AMD a `.large` has two full cores, and both solves would fit in its memory
  (1.1 GiB together), at half the price. That size was not run.
- **Overhead is small.** Boot, staging, Docker, image pull and the checks take 90–157 s of a
  49–71 min bill. Intel's includes installing the env twin.
- **n = 1 per cell.** The solve is deterministic. Intel and AMD give byte-identical heads, so a repeat
  would measure only timing noise. These timings are not compute cost in general
  ([layout](../../patterns/layout-and-effective-cost.md)).

## What held at scale, on all three chips

Checked inside each run by `real.py`, on both simulations, at all 97 periods:

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| both archive zips match ScienceBase's MD5 and their pinned sha256 | provenance | yes | yes | yes |
| mf6 is 6.8.1, and stdout says `Normal termination of simulation` | completion | yes | yes | yes |
| one head and one budget record per period, a finite head on every active cell | output shape | 97 / 97, 0 bad | same | same |
| the grid file's connectivity rows are exactly the 42,822 active cells | grid | yes | yes | yes |
| `FLOW-JA-FACE` is exactly antisymmetric, `q(n→m) = −q(m→n)` on all 213,000 entries | internal conservation | max 0 | 0 | 0 |
| every budget term's **net** flow, and the model's, binary == listing | two read-outs of one budget | ≤ 0.96 of print precision | ≤ 0.92 | ≤ 0.92 |
| every term's gross in and out, binary == listing, CHD excepted (below) | same | ≤ 0.99 | ≤ 0.999 | ≤ 0.999 |
| GWF budget closes within 1%, every converged period | conservation | 0.134% / 0.535% | same | same |
| SFR and UZF budgets close within 1%, every converged period | conservation | 0 / 5.5e-6 % | same | same |

*Print precision* is the listing's: rates are printed to four decimals, so each one is within 5e-5
of the binary value, plus 1e-11 relative for summation order over 60k cells. The 1% closure is the
usual acceptance criterion for a MODFLOW budget, not a value fitted to this run. The worst periods are
the ones nearest it: postcal period 83 at 0.13%, precal period 95 at 0.53%. The non-converged periods
close as well, to 0.0025–0.13%.

**CHD's listing gross is not the budget file's.** In every period, the listing's CHD in and CHD out
each exceed the per-cell values in the budget file by the same amount, to within 1e-4 m³/d.
The excess is 431–2,409 m³/d. CHD's net
agrees to print precision, so the gross check leaves CHD out and the net check keeps it.

## Against the published heads

The archive's heads come from mf6 6.3.0, built with Intel Fortran on Windows. `compare.py` compares
each chip with them period by period on the active cells:

| | Graviton4 | Intel | AMD |
|---|---|---|---|
| postcal, periods converged in both: worst median / p99 / max \|Δh\| | 5.4e-12 / 2.7e-5 / 0.26 m | 6.9e-12 / 3.2e-4 / 0.31 m | same as Intel |
| precal, all 97 periods: worst median / p99 / max \|Δh\| | 5.9e-11 / 5.7e-6 / 0.19 m | 9.0e-11 / 4.0e-6 / 0.24 m | same |
| cells more than 0.1 m off, worst converged period | 1 | 4 (postcal), 1 (precal) | same |
| 36 head observations, max \|Δ\| | 1.6e-4 m | 1.6e-4 m | same |
| 10 streamflow observations, max relative Δ (postcal; precal ≤2.4e-5) | 4.4e-4 | 7.4e-4 | same |
| postcal periods that hit `OUTER_MAXIMUM` (archive: 1, 32, 35, 37, 69, 83) | 1, 17, 32, 83 | 1, 32, 83 | 1, 32, 83 |

**Asserted:** in every period that converged in both runs, 99% of active cells match the archive to
within the model's own `OUTER_DVCLOSE`, 0.1 m. mf6 accepts a step as converged once the largest head
change between iterations is under that value. The worst p99 is 3.2e-4 m, 300× inside it. The
maximum is reported, not asserted. A handful of cells can sit on different sides of a wetting or
UZF-wave threshold in two valid solves.

## Across chips

- **Intel against AMD: bit-identical.** Heads, both observation files and every budget value match
  in all 97 periods of both simulations. Two vendors run the same x86 binary to the same bits.
- **Intel or AMD against Graviton4: no period is bit-identical, starting with period 1.** The median
  cell differs by ≤1.1e-10 m. The worst p99 is 3.1e-4 m (postcal) and 4.5e-6 m (precal). At most 4
  cells differ by more than 0.1 m, the largest by 0.49 m. Head observations agree to 1.5e-4 m, and the
  GWF discrepancy to 0.077 percentage points.
- **Which periods fail to converge is a path, and it moved.** Graviton4 hit `OUTER_MAXIMUM` in
  postcal period 17 and the x86 chips did not. The archive's 6.3.0 run failed in 35, 37 and 69 as
  well, and none of the 6.8.1 runs did. Each step's converged heads are close to a fixed point. The
  bulk of cells agree to 1e-11 m whatever the chip or version. The iteration that reaches them is a
  path, and a last-bit difference can move a period across the 750-iteration limit. This is why the
  cross-chip comparison is reported, and only the closure-scaled archive check is asserted.

## The two edits

1. **Path separators.** The archive was written on Windows, so its package files name their data as
   `external_data\mobile_bay.npf_k.txt` and `.\mobile_bay.dis`. mf6 6.8.1 only looks for `\` when it
   runs on Windows (`Sim.f90`), so on Linux those files are not found. `real.py` rewrites the 416
   backslashes to `/`. It checks that every one is on an `OPEN/CLOSE` or package-file line.
2. **`SAVE_FLOWS` in the name file.** As archived, STO and WEL save nothing to the budget file, and
   a budget with no storage term cannot close. Adding the option changes what mf6 writes, not what it
   solves. The archive comparison above is the evidence that the solution did not move.

## Pins

| | |
|---|---|
| archive | ScienceBase item `674f7ffdd34e5a48687db29b`: `model.zip` (48,586,751 B, MD5 `b00d1adf…`) and `output.zip` (323,006,652 B, MD5 `39cf46ba…`), fetched on AWS by `00-fetch.task.json` and kept only if size, MD5 and every entry's CRC-32 match; sha256 in `pins.sha256` (data tier: a USGS data-release DOI) |
| scripts | `real.sh` and `real.py` in `pins.sha256`, and `compare.py` in `compare.sha256`; all are checked in-task |
| image | the recipe's `geoscience@sha256:f0f72f5b…` (arm64) and its `geoscience-0ac81cc7b2f4` x86 twin (modflow6 6.8.1 `h97cf6a1_0`) |

Raw per-chip outputs are in `results/`. The heads files (47 MB each) and the gzipped listings stay in
the bucket. The staged archive is 372 MB in your bucket; delete `inputs/modflow-real/` when you are done.

## Caveats

- One model and one instance size. There is no knee to sweep, because the solver is serial.
- The archive reference was produced by a different mf6 version on a different OS. So agreement with
  it is held to the solver's closure, not to bits.

## Spend

Eleven launches, about **$0.75**. The three verifying runs cost $0.62. The rest was the archive fetch,
five 3-period probes on Graviton4 and two compare tasks. The probes found the Windows path separators,
the missing storage term, and the CHD gross difference.
