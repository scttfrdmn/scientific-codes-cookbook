# GW150914 on real strain, end to end: Graviton4, Intel and AMD

> **PyCBC finds GW150914 blind as the loudest signal of the GWOSC hour in both detectors. Bilby
> rebuilds the published GWTC-2.1 likelihood from the release's own config and reproduces it: the
> noise evidence to its printed digits, and the log-likelihood at 500 published posterior samples to
> 0.041.** The full 15-parameter run with calibration lands on the published Bayes factor (304.23
> against 303.45 ± 0.14) and the published medians. It does **not** pass a 5-SE quantile test against
> the published posterior: its tails are narrower in the same direction on every chip (max |z| 9.6
> on Graviton4, 14.0 on x86). Graviton4 is cheapest at $3.17 per event billed. AMD is fastest
> (3,543 s billed) at $3.39, and Intel is $3.86.

Workload: **GW150914**, the event every CBC analysis tutorial starts from, run the way a
parameter-estimation paper runs it:

- **Search**: PyCBC over all 4,096 s of GWOSC's O1 strain around the event in H1 and L1. That is a
  946-template non-spinning IMRPhenomD bank, PyCBC's own conditioning (15 Hz high-pass, 2048 Hz, a
  4 s median-Welch PSD), matched filter, power χ² and newSNR on the 20 loudest triggers per detector.
- **Parameter estimation**: Bilby with IMRPhenomXPHM, all 15 BBH parameters plus spline
  calibration in both detectors. The settings are exactly GWTC-2.1's, read from the release's
  `config_file`: 4 s at 2048 Hz, 20–896 Hz, the release's PSDs and priors, f_ref 20 Hz, the H1L1 sky
  frame. Calibration is marginalised over 250 response curves drawn from the release's spline
  priors, as current bilby_pipe does, and the sampler is dynesty (`acceptance-walk`, `naccept=60`,
  `nlive=500`, 64 processes, seeded).

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/gw-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if the inputs are not staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py gw $a "$B" r1 measurements/gw-real/real.task.json)" --wait
done
python3 measurements/gw-real/collect.py measurements/gw-real/results   # after copying each chip's outputs there
```

[`pe.py`](pe.py) is the PE and its checks; [`search.py`](search.py) the search;
[`gwreal.py`](gwreal.py) rebuilds the release's data, PSD, priors and likelihood. The sampling call:

```python
bilby.run_sampler(likelihood, priors, sampler="dynesty", nlive=500, sample="acceptance-walk",
                  naccept=60, npool=64, rstate=np.random.default_rng(20261019), ...)
```

## Results

A `16xlarge` (64 vCPU, 128 GiB) of each chip, the same staged bytes, 64 worker processes with one
BLAS thread each. Graviton4 runs the recipe's aarch.science `gw` image, and both x86 chips run its
locked linux-64 twin:

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.16xlarge` | 4,409 s | 4,476 s | 32.2 GiB | 2.5524 | **3.13** | **3.17** |
| Intel (Xeon 6975P-C) | `c8i.16xlarge` | 4,585 s | 4,636 s | 35.8 GiB | 2.9987 | 3.82 | 3.86 |
| AMD (EPYC 9R45) | `c8a.16xlarge` | **3,498 s** | **3,543 s** | 33.5 GiB | 3.4486 | 3.35 | 3.39 |

A *result* is one event, searched and fully characterised. *Tool wall* is search plus PE; *billed* is
launch to the end of the task. Peak memory is the cgroup's `memory.peak` (page cache included); the
largest single process was 0.97 GiB.

- **The sampler is 96% of the run.** The search takes 52–88 s. Sampling takes 3,376 s (AMD), 4,234 s
  (Graviton4) and 4,401 s (Intel), for about 20M likelihood calls.
- **AMD is 1.25× faster than Graviton4 and 7% dearer per event.** Intel's 64 vCPUs are 32 cores × 2
  threads, and 64 single-threaded workers on them run at Graviton4's speed for an hour that costs
  17% more. AMD's and Graviton4's 64 vCPUs are 64 full cores. Serially, one likelihood call takes
  6.6 ms (Graviton4), 5.6 ms (Intel) and 4.8 ms (AMD).
- **The likelihood picks the box, not memory.** Each call is an XPHM waveform (~6 ms) plus the
  calibration sum over 250 curves (~3 ms). It parallelises over processes with no shared state, so
  the core count sets the wall time. Memory is ~1 GiB per worker.
- n = 1 per chip. The two x86 runs agree in every number they write (one build), so there are two
  independent sampler paths, not three.

## Why this size

A single event's full PE is the unit of work in this field. Catalogues are this, run once per
event, and a PE paper is this with more live points. Smaller would not be real: the 40 calibration
parameters, the precessing waveform and the sky ring are what make GW PE expensive, and a
three-parameter fixture (the recipe's) has none of them. The search covers the GWOSC hour, which is
the unit GWOSC publishes. The noise statistics and the χ² veto need that hour to mean something: the
loudest raw-SNR trigger in L1 is a glitch, not the event.

`nlive=500` and 250 calibration curves are half of bilby_pipe's defaults, so that three chips fit
the budget. A probe at 1000 curves cost 1.7× per call. GWTC-2.1 itself ran 4 × `nlive=2000`.

## What held at scale, on all three chips

| assertion | bound | observed (Graviton4; x86 in brackets where different) |
|---|---|---|
| staged bytes | sha256 pins; the release also against Zenodo's md5 | 5 of 5 |
| **search: the loudest newSNR of the hour is GW150914**, each detector | within 0.5 s | H1 19.51 at …62.428, L1 13.53 at …62.421 |
| GW150914 is signal-like | reduced χ² < 2 | H1 1.04, L1 0.57 |
| L1 − H1 arrival | within the 10.0 ms light travel time | −6.35 ms |
| loudest off-source newSNR (reported) | — | H1 6.50, L1 6.46; L1's loudest *raw* SNR is a 17.05 glitch at newSNR 4.63 |
| filter normalisation on noise (reported) | E\|ρ\|² = 2, median/(2 ln 2) = 1 | H1 2.009 / 0.999, L1 2.020 / 1.002 |
| **noise log-likelihood == the release's `ln_noise_evidence`** | to its 2 printed decimals | **−7288.1157** vs −7288.12 |
| **marginalised ln L at 500 published samples (incl. max-L) == the release's** | max \|Δ\| < 0.05 | median −0.0043, **max 0.041** |
| matched-filter SNR at the published max-L sample == the release's (complex) | rel < 1e-3 | H1 4.6e-6, L1 4.6e-4 |
| **PyCBC SNR == Bilby SNR** on Bilby's data, PSD and template | rel < 1e-10 | H1 0, L1 2.3e-16 |
| **ln Bayes factor vs the release's 303.45 ± 0.14** | < 5 combined σ | 304.23 ± 0.25, 2.7σ [304.27, 2.8σ] |
| every published 5/50/95% quantile within 5 SE of ours, 17 parameters | max \|z\| < 5 | **fails: 9.57 [14.04]** (below) |

**Why these bounds.** The search's noise-only distribution is χ²₂ per sample. ~4,000 s × 2048 Hz ×
946 templates gives a loudest noise newSNR of ~6.5, so the event at 13.5–19.5 is unambiguous. The
noise evidence is a sum over the same data and PSD, so it must match to the digits the release
prints. The log-likelihood bound covers what differs between the two runs: the distance-marginalisation
lookup table is rebuilt (interpolation), lalsimulation is 6.1.0 against 2.4.0, and GWOSC's file
stands in for the C02 frames. A wrong PSD normalisation or time reference misses by tens to
hundreds (below). The release's own SNRs fix the H1 and L1 values to the same bytes.

## What failed: the quantile test

The test is [cmdstan-real](../cmdstan-real/README.md)'s. For each published quantile, the fraction
of our posterior below it should equal q within the sampler's own error. That error is the SD of
the fraction over 200 `dynesty.utils.resample_run` bootstraps of the run (Higson et al. 2018). The
release's error is added assuming it scales with its 4 × 2000 live points. Medians agree: the
published median sits between our 46th and 59th percentile on every parameter. The tails do not:
F(q05) is 0.018–0.066, F(q95) is 0.939–0.981, and many are more than 5 SE from q.
[`results/crosschip.txt`](results/crosschip.txt) has every value.

- **It is a property of the run, not the chip.** The two independent paths (Graviton4, x86) differ
  from the published posterior in the same direction on most tests: narrower tails in azimuth and
  geocent_time, and the published median of dec at our 58th/59th percentile.
- **The bootstrap SE is close to the run-to-run spread.** Estimated from the two independent paths,
  the per-run SD is 1.4× the bootstrap SE (median over the 51 tests). With each test's error
  replaced by the larger of the two, max |z| is still 9.5. So the miss is not noise the error bar
  left out: these runs measure a slightly narrower posterior than the published one.
- **Two settings differ from the release and could do it**, and this run cannot tell them apart.
  Calibration is marginalised over 250 curves instead of sampled in 40 dimensions (it sets the
  sky and time tails). The sampler explores less: `nlive=500` `acceptance-walk` against 4 ×
  `nlive=2000` `rwalk` with 100-step walks, and narrow tails are what a short-chain nested sampler
  gives. One run at 1000 curves, or at `naccept=120`, would split them.

The check is left asserting at 5 SE and failing, because it is the claim we mean. The fixed points
above are what is verified at scale.

## Fixed point or path

- **Fixed points agree across vendors; arm64 and x86 differ at 1e-6.** Intel and AMD give identical
  output in every file (one build). The noise log-likelihood is identical on all three chips to
  every printed digit. The search's 20 triggers per detector have identical times and templates,
  with SNRs equal to 5e-10 relative. Graviton4 against x86, the matched-filter SNR at the max-L
  sample differs by 1e-6 relative (H1 21.221077 vs 21.221094) and the marginalised ln L by 7e-5.
  The data path is identical, so the difference is inside the waveform. That is the XPHM call
  across two builds of lalsimulation 6.1.0, and it is far inside every bound.
- **The sampler is a path.** Graviton4 and x86 took different paths from the same seed: 20.46M
  against 20.12M likelihood calls, 2,722 against 2,610 posterior samples, ln BF 304.23 against
  304.27. Every derived number is reported per chip, never asserted equal.
- OpenBLAS reports `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips. The likelihood is
  FFTs and dot products, and nothing asserted depends on the kernel.

## Two things the release does that a naive rebuild gets wrong

- **Bilby 1.0.4 scaled the PSD by the Tukey window's power loss; bilby 2.x does not.** The release
  was made with the scaling. Without it, every SNR is ×0.935 (√0.875, the window factor for
  `roll_off=0.4` on 4 s) and the noise evidence is ×0.875 (−6376.32 against −7288.12). Bilby 2.8.2
  keeps the old behaviour behind `BILBY_INCORRECT_PSD_NORMALIZATION=TRUE`, which `pe.py` sets to
  reproduce the release. Comparing a bilby-2 run to a GWTC-2.1 posterior without it compares two
  different likelihoods.
- **With time marginalisation, `geocent_time` is the segment start, not the arrival time.** The
  release stores the reconstructed physical time. Passing that back to a time-marginalised
  likelihood shifts the template by 2 s and returns ln L ≈ −1 instead of 323. The sampler's view is
  the prior's fixed value: `float(ifos.start_time)`.

lalsimulation changed three IMRPhenomXPHM defaults since 2.4.0: `PrecVersion` 223→300,
`FinalSpinMod` 3→4, `PhenomXHMReleaseVersion` 122019→122022. `gwreal.py` sets the 2.4.0 values.
At the max-L sample the 6.1.0 defaults move ln L by only 0.0037 here.

## Pins

| | |
|---|---|
| strain | GWOSC O1, `H-H1_LOSC_16_V1-1126256640-4096.hdf5`, `L-L1_LOSC_16_V1-…` (PE) and the `_4_V1` 4 kHz files (search); sha256 in [`inputs.sha256`](inputs.sha256). L1 has 774,144 NaN samples (189 s outside science); the search analyses the 3,907 s valid stretch that holds the event |
| published posterior | GWTC-2.1 PE release v2, Zenodo [10.5281/zenodo.6513631](https://doi.org/10.5281/zenodo.6513631), `IGWN-GWTC2p1-v2-GW150914_095045_PEDataRelease_mixed_nocosmo.h5`, md5 `f0e04367…` (Zenodo's), run `C01:IMRPhenomXPHM` (199,766 samples, bilby 1.0.4, lalsimulation 2.4.0) |
| image | arm64 `quay.io/aarchsci/gw@sha256:18e0856f…` (the recipe's pin); x86 the env's locked linux-64 twin `envs-x86_64/gw-ab3a5857bd21` |
| versions (all chips) | bilby 2.8.2, dynesty 3.1.0, lalsuite 7.25 (lal 7.6.1, lalsimulation 6.1.0), pycbc 2.10.0, gwpy 4.1.0, numpy 2.3.5 |
| scripts | `run.sh`, `gwreal.py`, `search.py`, `pe.py`, each pinned by sha256 in the spec (`mkspec.py` writes it) |

**Data tier: a durable public id.** GWOSC's versioned strain release (event `GW150914-v4`, the
files' archive URLs in `fetch.py`) and a Zenodo DOI with publisher checksums. [`00-fetch.task.json`](00-fetch.task.json) downloads both
**on AWS** and writes the sha256 manifest. Per-chip `score.tsv`, `quantiles.tsv`, `search.json` and
host details are in `results/`. Bilby's result files are in the bucket under
`runs*/gw-real/r1/`. These timings are not compute cost alone: boot, Docker and the image pull or
twin install are ~1 minute of each bill ([layout](../../patterns/layout-and-effective-cost.md)).
