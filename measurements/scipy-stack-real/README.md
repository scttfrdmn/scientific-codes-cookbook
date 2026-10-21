# NumPy, SciPy and pandas on a real workload — a year of GHCN-Daily, Graviton4, Intel and AMD

> **A whole year of the global station network, 38.5M rows, in 72 s of Python on an 8-vCPU box — and
> every integer result is byte-identical across chips.** The compute is 12% cheaper per result on
> Graviton4 than on Intel; the bill is 13% cheaper on Intel, because this job is short enough that
> pulling a 3.1 GB image costs more than the work. AMD's `m8a` runs it in 46 s, cheapest in compute,
> and its results are byte-identical to Intel's.

Workload: **all of GHCN-Daily 2024** (RODA, `s3://noaa-ghcn-pds/csv/by_year/2024.csv`): 38,470,120
station-day observations from 44,882 stations, 74 elements, joined to the 133,039-station table.
pandas parses it (pyarrow engine), partitions it by QC flag, groups it by station × element and by
month × element, merges it with the station table and resamples TMAX to monthly per station. NumPy
recomputes the station × element sums by an independent route (factorize + `add.reduceat`). SciPy
then grids the year's station-mean TMAX onto a global 0.25° grid — **1,036,800 unknowns**, solving
`(W + αL) x = b` (W the station count per cell plus 1e-3, L the 5-point Laplacian, periodic in
longitude) with SuperLU (128M fill) and with Jacobi-preconditioned CG (892 iterations).

## Run it

```sh
export AWS_PROFILE=aws
bash stage-inputs.sh "$COOKBOOK_BUCKET"          # 1.4 GB from RODA, checked against its sha256
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" ghcn-m8g.task.json > /tmp/g.json
spawn task run --spec /tmp/g.json --wait
spawn task run --spec "$(python3 ../../scripts/spec_arch.py scipy-stack x86_64 "$COOKBOOK_BUCKET" r1 ghcn-m8g.task.json)" --wait
spawn task run --spec "$(python3 ../../scripts/spec_arch.py scipy-stack amd "$COOKBOOK_BUCKET" a1 ghcn-m8g.task.json)" --wait
```

## Results

8 vCPU / 32 GiB on every chip, default threads (8), same image digest (the x86 legs are the recipe's
`geo-ml-d3f7ea36e8bf` linux-64 twin: same versions), same input bytes:

| chip | instance | workload wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 72 s | 226 s | 11.3 GiB | 0.3590 | **0.0072** | 0.0225 |
| Intel (Granite Rapids) | `m8i.2xlarge` | 70 s | 167 s | 11.2 GiB | 0.4234 | 0.0082 | **0.0196** |
| AMD (EPYC 9R45) | `m8a.2xlarge` | **46 s** | 160 s | 11.2 GiB | 0.4869 | **0.0062** | 0.0216 |
| *sizing probe:* Graviton4 | `r8g.2xlarge` | 65 s | 228 s | 11.3 GiB | 0.4713 | 0.0085 | 0.0299 |

Peak RSS is the Python process's own (`ru_maxrss`); the cgroup peak was 11.3 GiB on arm64 and 14.1
GiB on x86, where the env twin is installed into the container at start. Billed is launch to
terminate as EC2 reports it.

- **The same wall on both chips** (72 s vs 70 s), and it is not one number underneath: Graviton4
  was faster at the CSV read (2.0 s vs 2.6 s), the resample (8.5 s vs 9.1 s) and CG (6.8 s vs
  8.1 s); x86 at date parsing (8.6 s vs 9.5 s), the merge, the duplicate scan and the groupbys;
  SuperLU's factorization was a tie (12.3 s vs 12.5 s). So Graviton4's compute per result is
  cheaper by roughly its hourly rate, 12%.
- **The bill inverts it.** Graviton4 spent 42 s installing Docker and 29 s pulling the 3.1 GB
  aarch.science image; the x86 leg pulled a 2-second micromamba base and installed the twin offline
  in 5 s. Against 70 s of work that is most of the bill ([layout](../../patterns/layout-and-effective-cost.md)),
  so on a job this short **the image is the cost**, not the chip. A cohort of years in one task
  amortizes it; these timings are not compute cost.
- **AMD is 1.5× faster than either** (46 s), and every step moved: SuperLU 8.6 s, CG 4.6 s, the
  merge 4.9 s, date parsing 5.7 s. That is 13% cheaper compute per result than Graviton4 despite a
  36% higher hourly price; the bill, at 160 s, lands between the other two, again set by the
  boot rather than the work. Its 8 vCPUs are 8 full cores (`threads_per_core` 1); Intel's are 4 × 2.
  The AMD leg ran under spawn 0.139.1, which installs Docker during stage-in, so its overhead is
  not comparable with the other rows'.
- **The box was over-sized by the probe and retightened from it.** `r8g.2xlarge` (64 GiB) measured
  11.3 GiB peak and 2.7 average cores busy, so the real runs went to `m8g`/`m8i` 32 GiB: tmpfs
  staging (½ RAM, 16 GiB) holds the 1.4 GB input with room, and the frame fits in what is left.
- **Most of the work is single-threaded.** 2.76 average cores busy of 8 on arm64, 1.90 on x86:
  pandas' groupby/merge/resample and SuperLU run on one core, only pyarrow's CSV parse and BLAS-1
  inside CG spread. A 4-vCPU box would likely cost less per result; that rung is not measured here.

## What held at scale, on all three chips

The AMD column is not shown separately: every check value in its result file, residuals
included, is identical to Intel's (`checks_failed 0`).

| check | kind | arm64 | x86_64 (Intel and AMD) |
|---|---|---|---|
| rows parsed == newline count of the file | count | 38,470,120 | identical |
| one value per (station, date, element) | format guarantee | 0 duplicates | identical |
| QC kept + flagged == all, and their value sums partition the total | partition | 38,433,260 + 36,860 | identical |
| sum of group sums == column sum; group counts == rows (station × element, month × element, country after merge) | conservation, int64 exact | 202,591 / 74·12 / 195 groups | identical |
| NumPy factorize + `add.reduceat` == pandas groupby, every group | cross-implementation | 202,591 groups equal | identical |
| left m:1 merge keeps every row; matched == independent `isin`; inner == matched | merge cardinality | 38,470,120, 0 unmatched | identical |
| monthly resample sums and counts == TMAX totals | conservation | 156,636 station-months | identical |
| `\|x_lu − x_cg\|/\|x_lu\| ≤ κ₂ \|A(x_lu − x_cg)\|/\|A x_lu\|` | theorem | 2.146e-09 ≤ 1.694e-06 | identical |
| forward error on a known solution ≤ κ₂ × relative residual | theorem | 1.265e-13 ≤ 4.768e-11 | 1.238e-13 ≤ 4.738e-11 |
| `\|Σb − Σw x\| ≤ √n ‖r‖` (1ᵀL = 0: the gridding conserves the total) | conservation | 1.48e-10 ≤ 6.60e-09 | 1.25e-10 ≤ 6.48e-09 |
| CG reaches rtol 1e-10 | convergence | 892 iterations, 9.964e-11 | identical |

κ₂ is a **bound, not an estimate**: L is positive semi-definite with zero row sums, so λmin(A) ≥
min w = 1e-3 (Weyl), and Gershgorin on L gives λmax(A) ≤ max w + 2α·max deg. That is κ₂ ≤ 17,001,
and the two inequalities above are then theorems that any correct solver must satisfy, with no
tolerance chosen. Residuals are computed in `np.longdouble` (128-bit on aarch64, 80-bit x87 on
x86_64) so rounding in the residual itself cannot decide the check.

## What differed across chips

- **Fixed points agree to the bit.** The four integer result tables (station × element, month ×
  element, country, station-month) hash to the **same sha256 on all four runs** (`digest-*.txt`).
  Integer aggregation has no rounding to move, so this is the expected result, and it is asserted
  by comparing the digests, not assumed.
- **The solved grid differs in its last bits**: 1,008,216 of 1,036,800 cells, max 1.2e-13 °C,
  relative 2.9e-15 — the same order as the solver's own residual (2.75e-15 vs 2.70e-15). The two
  Graviton4 runs (`m8g`, `r8g`) produced **bit-identical** grids, so the difference is the chip, not
  run-to-run noise. The image's OpenBLAS selects `neoversev2` on Graviton4 and `Cooperlake` on
  `m8i` ([why that matters](../openblas-x86/README.md)); which arithmetic moves the bits (BLAS, the
  compiler's vector paths, or reduction order in CG's dot products) is not separated here. It is a
  reported difference, not an asserted one; both grids satisfy every bound above, and the mean over
  station cells, 16.682887 °C, agrees to the printed digit.
- **Intel and AMD produce the same grid, bit for bit** (0 of 1,036,800 cells differ). Both select
  OpenBLAS `Cooperlake` (0.3.34 has no Zen 5 kernel), so the split is ARM vs x86, not vendor.

## The dataset and its pin

RODA tier: NOAA GHCN-Daily, version `3.35-upd-2026101517`. NOAA rewrites every by-year file daily
as late reports arrive, so the object is **not immutable**: the pin is the sha256 of the staged
bytes (`2024.csv` `62a950b9…`, `ghcnd-stations.txt` `428eabc9…`, in `stage-inputs.sh`), and a
re-stage after NOAA's next update fails its own check. Nothing asserted above is a GHCN-specific
value, so re-pinning to a newer snapshot needs a re-run, not a re-derivation.

**Why a year is the legible size.** The by-year file is the dataset's own unit, and one year of the
whole global network is a typical in-memory pandas job, 11.3 GiB resident. A decade is ten of
these files, roughly ten times the frame, and would mostly measure memory, not the identities, which are per-row and already exercised
on every row here. The 0.25° grid is a standard climate-gridding resolution, and at 1M unknowns
SuperLU's fill (128M) is the size where direct solvers start to cost real memory.

## Caveats

n = 1 per chip; the workload is deterministic (integer results identical on four boxes, the
float grid identical on two Graviton4 boxes and on Intel and AMD), which is what makes one run per cell defensible. The
x86 env is the twin, not the aarch.science image: same package versions, linux-64 builds. The AMD
leg (spend $0.02) staged the resolved command as a sha256-checked `task.sh`, byte for byte what
`spec_arch.py` emitted plus a non-failing CPU-topology print: spawn 0.139.1's wrapper leaves too
little of EC2's 16 KiB user data for it inline (spawn#1246).
