# ObsPy on a real network-day: every CI broadband channel on the day of the Ridgecrest Mw 7.1, three chips

> **Graviton4 is cheapest per result. AMD is 1.17× faster but costs 9% more. Intel costs 51% more.**
> Every identity holds exactly on all three chips. Intel and AMD agree byte for byte. Graviton4
> differs from them only in the last bits of the deconvolved traces and the TauP times.

Workload: **all 720 HH? channels** (239 stations, 100 Hz) of the Southern California Seismic
Network for **2019-07-06**, the day of the Ridgecrest Mw 7.1 and its aftershock sequence. That is
6.16 billion samples in 19.0 million 512-byte STEIM records, 9.73 GB, with the 239 StationXML files
(1.23 GB) that describe them. The run is the standard first pass over a network-day: decode every
day file, attach each channel's response from its StationXML, compute a McNamara–Buland PPSD for
every channel, and deconvolve every vertical to ground velocity over the whole day. SCEDC archives
continuous data in this unit (one file per channel-day). Station QC and ambient-noise
preprocessing run one of these per day, and a month is 30 of them fanned out, not a different job.
The recipe's fixture is 3,000 samples, so this is about 2 million times more data.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/obspy-real/stage-inputs.sh "$B"     # scedc-pds -> bucket, server-side, no local disk
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py obspy $a "$B" "$(date +%s)" measurements/obspy-real/real.task.json)" --wait
done
python3 measurements/obspy-real/compare.py measurements/obspy-real/results/arm64 measurements/obspy-real/results/amd
```

One task per chip runs `real.py` (pinned by sha256) over 239 stations in a 16-process pool, with
`OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=1` per process. The x86 legs use the recipe's `geoscience`
env twin, which has the same obspy 1.5.1, numpy 2.5.3, scipy 1.18.1 and python 3.14.8.

## Results

16 vCPU and 64 GiB (`m8.4xlarge`) per chip, 16 workers, the same staged bytes:

| chip | instance | cores × threads | real.py wall | billed | peak RSS (worker / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | 16 × 1 | 488 s | 647 s | 1.88 / 34.0 GiB | 0.7181 | 0.0973 | **0.1291** |
| Xeon 6975P-C | `m8i.4xlarge` | 8 × 2 | 712 s | 826 s | 1.89 / 34.0 GiB | 0.8467 | 0.1674 | 0.1943 |
| EPYC 9R45 | `m8a.4xlarge` | 16 × 1 | **416 s** | **521 s** | 1.89 / 33.7 GiB | 0.9738 | 0.1125 | 0.1409 |

A *result* is the whole verified network-day: every check below. **Billed** runs from launch to
termination. **Box** peak counts the 11 GB of staged input, which lives in tmpfs.

- **AMD is 1.17× faster than Graviton4, and Graviton4 is 8% cheaper per result billed** (14% on
  compute alone). AMD's hour costs 36% more and its speed does not cover that. Intel's 16 vCPUs are
  8 SMT cores, so it runs 1.46× slower than Graviton4 at 18% more per hour.
- **The checks cost more than the work.** On Graviton4 the workload (decode, StationXML,
  PPSD, deconvolution) is 3,260 of 7,346 worker-seconds. Superposition alone takes 3,909, because
  it runs two more full-day deconvolutions per vertical. Per stage, AMD vs Graviton4 is 1.28× on
  PPSD and 1.15× on `remove_response`. Intel trails both on every stage.
- **Memory picks the family.** tmpfs is half of RAM. The 11 GB input plus 16 workers at ~1.4 GiB
  each peaks at 34 GiB, more than a `c8g.4xlarge` (32 GiB) holds, so this runs on `m8g`.
  Staging 10.2 GiB took 31–38 s (about 430 MiB/s). All overhead together (boot, staging, Docker,
  image pull, the in-task sha256 and shutdown) is 105–159 s of each bill.
- n = 1 per cell. Every output is deterministic. On Graviton4, a 16-station probe and the full run
  gave identical per-channel hashes on the 48 channels they share, so a repeat would measure only
  timing noise.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 962 staged files match their sha256 | provenance | 962 | 962 | 962 |
| decoded samples == Σ `nsamp` from the record headers, parsed in `real.py` without libmseed | conservation | 6,159,279,810 both ways | same | same |
| STEIM2 re-encode → decode, every one of 33,774 traces | lossless encoding | bit-identical 720/720 files | 720/720 | 720/720 |
| `endtime − starttime == (npts−1)/rate`, every trace | time arithmetic | 0 s | 0 s | 0 s |
| split each channel's longest trace in three, merge | conservation | bit-identical 720/720 | 720/720 | 720/720 |
| exactly one StationXML epoch covers the whole day | metadata selection | 720/720 | 720/720 | 720/720 |
| stage 1: evalresp (C) == poles and zeros evaluated in numpy, 200 frequencies | cross-implementation | max 1.7e-15 | 1.3e-15 | 1.3e-15 |
| PPSD histogram total == n_PSD × n_period_bins, every channel | conservation | 33,825 PSDs | same | same |
| deconvolution superposition: `rr(3.7x − 1.9y) == 3.7 rr(x) − 1.9 rr(y)`, `y` = `x` reversed, 240 verticals | linearity | max 3.42e-13 | 3.42e-13 | 3.42e-13 |
| Parseval on each 8.6M-sample velocity trace | energy | max 7.9e-16 | 8.3e-16 | 8.3e-16 |
| every velocity trace has energy | the two above are not vacuous | 240/240 | 240/240 | 240/240 |

Bounds: `1e-10` relative on the stage-1 and superposition comparisons, and `1e-12` on Parseval.
Stage 1 is one rational function evaluated twice, so its float64 roundoff is ~1e-15. Superposition
runs through a ~2²⁴-point FFT, the water level and the pre-filter. Its 3e-13 sits 300× inside the
bound on every chip.

**Two things the stage-1 check found, and a naive reader of StationXML would get wrong:**
- **336 of 720 channels define the stage gain at a different frequency from A0's normalisation
  frequency** (STS-2s with gain at 1 Hz and A0 at 0.03 Hz, for example). evalresp scales the
  stage by `G / |T(f_gain)|`. Evaluating `A0 · G · ∏(s−z)/∏(s−p)` directly is off by up to
  **4.6%** on those channels. With the gain frequency honoured, the two implementations agree to
  1e-15.
- The product of the stage gains differs from the reported overall sensitivity by up to 4.6% (range
  0.9989–1.0463). This is a property of the metadata and is the same on every chip.

Reported, not asserted (identical on all three chips):

| | |
|---|---|
| traces / gaps | 33,774 traces; 33,054 gaps, in 24 of the 720 channels |
| PSDs per channel | 45–47 one-hour segments (47 for a gap-free day) |
| PSD values inside Peterson's NLNM–NHNM | median 0.790 per channel (on the mainshock day) |
| peak ground velocity, HHZ | `CI.WBM..HHZ`, 0.0359 m/s, after the pre-filter |
| mainshock picks, SCSN event 38457511 | 95 analyst P/S picks |
| epicentral distance, SCSN vs obspy's WGS84 geodesic | within 0.49% (max 0.71 km at BAK, 144 km) |
| TauP residual, pick − model (median, MAD, n) | P: −0.525 s, 0.253 s, 79 (iasp91 = ak135). S: +0.980 s iasp91, +1.285 s ak135, 16 |

The TauP residuals compare global 1-D models with a regional crust. They show that the picks,
distances and travel times line up to within a second. They do not test TauP, which the recipe
checks against Java TauP.

## Across chips: identical where exact, last bits where not

Compare `results/<chip>/channels.tsv` with `compare.py`:

- **Every integer and every exact output is identical on all three chips.** That covers decoded
  samples (a sha256 per channel), record and gap counts, round-trips, PSD counts and histogram
  totals, and the **binned PSD arrays themselves (byte-identical 720/720)**.
- **Intel against Graviton4:** all 240 deconvolved velocity traces differ in the last bits.
  Per-trace energy agrees to ≤1.8e-15 relative and PGV to ≤9.5e-16. TauP times differ on
  52 of 190 picks by ≤5.2e-13 s. No identity's verdict changes, and the residuals move only in
  their last digits.
- **AMD against Graviton4:** exactly the same differences, because **AMD and Intel are byte-identical
  on every column**, deconvolved traces and TauP included. Two x86 vendors with different cores
  give the same bits, so the arm64/x86 difference comes from the compiled builds and not the chip.
  OpenBLAS is not involved: it reports `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips,
  and nothing here is a large blocked GEMM
  ([why that matters](../openblas-x86/README.md)).

There is no path in this workload, meaning no iteration and nothing seeded. Every value is a
direct computation, so each one-ulp difference stays one ulp. A trajectory would amplify it; this
workload has none.

## Pins

| | |
|---|---|
| waveforms | `s3://scedc-pds/continuous_waveforms/2019/2019_187/CI?????HH????2019187.ms`, 720 objects (SCEDC on the AWS Open Data Registry, us-west-2, **Payer: BucketOwner**; RODA tier) |
| metadata | `s3://scedc-pds/FDSNstationXML/CI/CI_<sta>.xml` for the 239 stations, and `event_phases/2019/2019_187/38457511.phase` |
| bytes | sha256 of all 960 objects in `day187.sha256`, taken on AWS from the server-side copy on 2026-10-18 and checked in-task. The StationXML files are living documents upstream, so a re-stage that fails the check means SCEDC re-issued metadata, not that the run is wrong |
| scripts | `real.py` and `corename.py`, sha256 in the staged `pins.sha256`, checked in-task |
| image | the recipe's `geoscience@sha256:f0f72f5b…` (arm64) and its `geoscience-0ac81cc7b2f4` x86 twin |

Raw outputs per chip are in `results/`. The staged copy is 10.96 GB in your bucket (about
$0.25/month in S3 Standard). Delete `inputs/obspy-real/day187/` when you are done.
