# GSW (TEOS-10) on the whole Argo archive — three chips

> **All 1.196 billion QC-good Argo levels since 1997 convert to TEOS-10 in 36–61 s on 64 vCPUs, and
> every salinity and temperature round trip returns its input to within 4e-14 on all three chips.**
> The conversion is 3–4% of the bill. The rest is moving 215 GB onto the box.

Workload: **every daily profile file in the Argo GDAC**, July 1997 to 20 October 2026. That is
30,229 files across the Atlantic, Pacific and Indian oceans, 215.1 GB, holding 3,388,543 profiles.
Each QC-good level goes through the standard Argo-to-TEOS-10 conversion. It uses adjusted values where
`DATA_MODE` is `A` or `D`, keeps P, T and S with QC 1/2/5/8, and computes `SA_from_SP` at the
profile's position, then `CT_from_t`, `pt0_from_t`, `sigma0`, `rho`, `spiciness0`, `z_from_p` and
N² along each profile. That conversion is the first step of every Argo climatology and
ocean-heat-content estimate, and those are built from the whole record. A year of it would be 13 GB
of exactly the same per-file work (the per-file table shows no file takes longer than 0.6 s), so
the archive is the honest scale and the real cost lives there: data movement.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/gsw-real/stage-inputs.sh "$B"     # server-side copy + S3 SHA256 of each object
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py gsw $a "$B" "$(date +%s)" \
    measurements/gsw-real/argo.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py gsw arm64 "$B" "$(date +%s)" \
  measurements/gsw-real/compare.task.json)"         # elementwise diff of 12 sample days
bash measurements/gsw-real/collect.sh "$B"          # results/<chip>/, results/digests.txt
```

`argo.task.json` checks all 30,230 staged objects against `pins.sha256` (64 `sha256sum` in parallel,
8 s). Then `argo.py` converts every file in a 64-process pool and fails the task on any identity
below.

## Results

64 processes on one `*.16xlarge` (512 GiB) per chip, same staged bytes, gsw 3.6.23 and numpy 2.5.3
on all three. aarch.science's `climate` image runs on Graviton4. Its linux-64 lock twin runs on the
other two:

| chip | instance | cores × threads | stage-in | convert wall | argo.py | billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.16xlarge` | 64 × 1 | 1202 s | 46.1 s | 51 s | 1370 s | 6.7 GiB | 3.7702 | 0.0534 | **1.43** |
| Xeon 6975P-C | `r8i.16xlarge` | 32 × 2 | 1509 s | 61.2 s | 65 s | 1667 s | 9.7 GiB | 4.4454 | 0.0803 | 2.06 |
| EPYC 9R45 | `r8a.16xlarge` | 64 × 1 | 1149 s | **35.8 s** | 38 s | 1303 s | 9.5 GiB | 5.1123 | 0.0540 | 1.85 |

A *result* is the whole archive converted and verified. **Billed** runs from launch to terminate.
**Peak RSS** is the container cgroup's `memory.peak`. No worker exceeded 401 MiB (`ru_maxrss`). On
x86 the same container also installs the env, which the arm64 image ships prebuilt.

- **The conversion is free and the data path is the bill.** spawn's stage-in moved 215 GB at
  143–187 MB/s, which took 19–25 minutes of a 22–28 minute bill. `argo.py` took under a minute
  everywhere. That puts compute at **2.9–3.9%** of billed on every chip. The 143–187 MB/s is the
  CLI default. With the CRT client the same copy runs at 2.68 GB/s, in 80 s, for $0.16 per archive.
  Reading through lith instead did not finish under a $1.33 cap
  ([lith-vs-copy-real](../lith-vs-copy-real/README.md)).
- **Memory picks the family, not the cores.** Staging lives in tmpfs (½ RAM), so 215 GB needs a
  512 GiB box before any compute: `r8*.16xlarge`. The run itself peaks under 10 GiB
  ([data movement](../../patterns/data-movement.md)).
- **AMD converts fastest** at 33.4 M levels/s, against 26.0 M on Graviton4 and 19.5 M on Intel's 32
  SMT cores. At 36% more per hour than Graviton4, AMD's compute $/result comes out level with
  Graviton4's. **Graviton4 is cheapest billed**, by 23% against AMD and 30% against Intel, because it
  staged at near-AMD speed at the lowest hourly rate.
- Work per file is 0.09 s of CPU on average and 0.6 s at most, and 64 workers reached ~94% parallel
  efficiency. So more cores buys nothing here, and nor does a sweep: the
  [knee](../../patterns/sizing.md) is not the question for this code.
- n = 1 per cell. The conversion is deterministic, and every non-timing number repeats across chips
  (below). A repeat would only re-measure staging noise.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 30,230 staged objects match their pins | provenance | ok | ok | ok |
| the TEOS-10 check-value table, 9 functions at their published tolerances | published reference (the recipe's check) | 0 fail | 0 fail | 0 fail |
| every QC-good level converts to finite SA and CT | no silent NaN | 0 of 1,196,275,148 | 0 | 0 |
| SA → SP → SA, every level, at its own lon/lat | involution | 1.07e-14 | 1.07e-14 | 1.07e-14 |
| t → CT → t (`t_from_CT`) | involution | 3.91e-14 °C | 3.55e-14 | 3.55e-14 |
| CT → pt → CT | involution | 1.78e-14 °C | 1.78e-14 | 1.78e-14 |
| p → z → p | involution | 4.55e-12 dbar | 4.55e-12 | 4.55e-12 |
| `CT_from_pt(pt0_from_t)` == `CT_from_t` | two routes, same quantity | **0** | **0** | **0** |
| `sigma0` == `rho(SA, CT, 0) − 1000` | two routes, same polynomial | **0** | **0** | **0** |
| `rho · specvol` − 1 | definition | 1.1e-16 | 1.1e-16 | 1.1e-16 |
| ~25 profiles a file converted alone == the whole-file call | the ufuncs are elementwise | 0 differ | 0 | 0 |

Each involution's bound is **1e-9**, the recipe's own. The worst salinity or temperature round
trip sits 2.6e4× inside it. A conversion that normalised wrongly misses by orders of magnitude,
not by ulps. p → z → p is the loosest, at 4.5e-12 dbar on pressures up to ~6,000 dbar. That is
1e-15 relative, so `p_from_z`'s iteration converges to float precision.

**Observed, not asserted** (identical on all three chips):

| | value |
|---|---|
| profiles; with ≥ 1 good level | 3,388,543; 3,017,406 |
| level slots; filled; converted; rejected by QC | 4,481,666,253; 1,339,445,275; 1,196,275,148; 143,170,127 |
| `DATA_MODE` D / A / R | 2,929,043 / 269,699 / 189,801 |
| files with no salinity at all (early temperature-only floats) | 829 |
| duplicate (float, cycle, direction) among good profiles | 41 |
| profiles per ocean, daily files vs the GDAC's own index | A 1,062,630 vs 1,062,595; P 1,672,031 vs 1,672,034; I 653,882 vs 653,885; 76 of 90 ocean-years exact |
| 75-term polynomial `rho` − Gibbs-function `rho_t_exact` | max 6.27e-4, rms 1.78e-4, mean −5.5e-5 kg m⁻³ |
| levels that pass QC but sit below the freezing CT | 4,465 |
| N² pairs; negative; with non-increasing pressure | 1,192,481,240; 74,865,493 (6.3%); 78,987 |
| mean SA, CT, σ₀ over every converted level | 34.9106255801 g/kg, 7.2483021716 °C, 26.9785325950 kg m⁻³ |

The polynomial-vs-Gibbs gap is the 75-term fit's own error, measured over the real ocean rather
than over the fit's design points. TEOS-10 publishes that fit's accuracy for α (0.03e-6 K⁻¹ rms)
but not for density, so the gap is reported here as a number and is not asserted. The index
mismatches are the live GDAC's two products disagreeing by a few profiles. They cluster in
1998–2003 and in 2026, the years still being revised. They are a property of the archive, not of
gsw.

## Across chips: round-off, not path, and x86 agrees with itself

**Intel and AMD are byte-identical**: every one of the 30,229 per-file digests matches, and so does
every element of the 12 sample days. That is one linux-64 binary on two FMA-capable chips.

**Graviton4 differs from both in the last bits of every file that converts anything.** Only the
1,078 files with no good level match. Over the 12 staged sample days (2023-01-01, 04-01, 07-01 and
10-01, all three oceans; 1,029,853 levels):

| quantity | elements that differ | max \|Graviton4 − x86\| |
|---|---|---|
| p, t, SP (the decoded input) | 0 | 0 |
| SA | 261 | 7.1e-15 g/kg |
| CT | 410,402 | 2.1e-14 °C |
| pt0 | 352,249 | 1.8e-14 °C |
| σ₀ / ρ (75-term) / ρ (Gibbs) | 260,692 / 264,108 / 13,119 | 4.5e-13 kg m⁻³ |
| z | 52,294 | 1.8e-12 m |
| spiciness0, N² | 1,028,071, 997,745 | 7.9e-13 kg m⁻³, 6.9e-16 s⁻² |

This is neither a fixed point nor a path. Each output is a closed-form polynomial or a
short fixed-count Newton step evaluated once per level, so a contraction to FMA or a different libm
rounding changes the last bits and **nothing accumulates**. The largest CT difference is 2e-14 °C,
eleven orders of magnitude below Argo's 0.002 °C sensor accuracy. Everything that is asserted
agrees: the counts, the check values, every identity but one to the digit, and all four means to
ten decimals. The exception is `t → CT → t`, whose worst level lands one ulp apart
(3.9e-14 vs 3.6e-14), and both values are 2.5e4× inside the bound. Nothing here touches BLAS
(`openblas_get_corename()`: `neoversev2`, and `Cooperlake` on both x86 chips), so OpenBLAS's
[kernel choice](../openblas-x86/README.md) plays no part.

## Pins

| | |
|---|---|
| data | the Argo GDAC on the AWS Open Data Registry, `s3://argo-gdac-sandbox/pub/geo/<ocean>/<YYYY>/<MM>/<YYYYMMDD>_prof.nc` (eu-west-3; RODA tier), plus `pub/idx/ar_index_global_prof.txt.gz`. Copied server-side on 2026-10-20, each with S3's full-object SHA256. `argo.sha256` holds all 30,230 hashes, and `argo.source.tsv.gz` holds the source key, ETag, LastModified and size |
| scripts | `argo.py` and `compare.py`, both sha256-checked in-task |
| image | the recipe's `climate@sha256:53ce76bc…` (arm64) and its `climate-b7b5178faf93` x86 lock twin |

**The GDAC is live.** Its daily files are rebuilt whenever a float's delayed-mode QC arrives. Every
2023 file had been rewritten within the 30 days before staging. So `stage-inputs.sh` will report
revised files when a reader re-stages, and it fails unless `REPIN=1` accepts the new bytes. Results
from new bytes are a new measurement. The citable frozen copy is the SEANOE monthly snapshot,
[doi:10.17882/42182](https://doi.org/10.17882/42182). The 2026-10-14 snapshot is #134998, 89 GB,
and its `dac/` tree holds the per-cycle files that these daily files aggregate.

The Intel task ran a version of `argo.task.json` with two differences in shell glue. It lacked the
`timeout 40m` wrapper around `argo.py`, and its `cpu.txt` line called `free`, which the image lacks.
`argo.py`, the inputs and the box were the same.

Raw per-chip outputs are in `results/`. The staged archive is 215 GB in your bucket, about $4.95 a
month in S3 Standard. Delete `inputs/gsw-real/argo/` when done.
