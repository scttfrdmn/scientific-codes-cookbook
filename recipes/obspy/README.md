---
tool: obspy
tool_version: "1.5.1"
env: geoscience
image: quay.io/aarchsci/geoscience@sha256:f0f72f5b0fe3119ecd1578b5d2e475c438bf716e01c0033adfca89a066ebcb70
env_x86_64: geoscience-0ac81cc7b2f4
last_verified_x86_64: 2026-10-17
spawn_version: 0.126.1
last_verified: 2026-10-09
---
# ObsPy — seismology on Graviton, against the original Java TauP

Reproduces the Java TauP reference travel times for 66 arrivals on Graviton4, then checks waveform I/O and filtering with exact identities. For anyone doing seismology on ARM.

## Run it

```bash
make stage RECIPE=obspy      # once: the Java TauP reference tables (no waveform needed)
spawn task run --spec "$(make -s spec RECIPE=obspy)" --wait
make ls RECIPE=obspy

st = obspy.read()                                    # 3 bundled traces, BW.RJOB, 100 Hz
st.write("out.mseed", format="MSEED", encoding="STEIM2")

model = TauPyModel(model="iasp91")                   # matches: taup_time -h 10 -ph ttall -deg 35
model.get_travel_times(source_depth_in_km=10.0, distance_in_degree=35.0, phase_list=["ttall"])
```

## Which box — [measured](../../measurements/obspy-real/README.md) on a network-day: 720 CI broadband channels, 2019-07-06 (Ridgecrest Mw 7.1)

| chip | `4xlarge`, 16 workers | real.py wall | **billed $/result** |
|---|---|---|---|
| **Graviton4** | `m8g` | 488 s | **0.129** |
| Xeon 6975P-C | `m8i` | 712 s | 0.194 |
| EPYC 9R45 | `m8a` | 416 s | 0.141 |

**Take Graviton4:** AMD is 1.17× faster but 9% dearer per result. Decode, PPSD and full-day deconvolution of 6.2 billion samples run with every identity exact on all three chips. Size by RAM (`m8g`), because the 11 GB of input sits in tmpfs. The fixture itself is one `m8g.large` task (TTL 30m as a **backstop**, `cost_limit` $0.10 as the guard). Its recorded **63 s** is boot, Docker install and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `obspy.read()` | your waveform | **called with no argument it returns a real bundled example** — three 100 Hz BW.RJOB traces from 2009-08-24. That is why this recipe stages no waveform at all. |
| `TauPyModel("iasp91")` | `ak135`, your `.tvel`/`.nd` | both models here are checked against Java TauP. A custom model can be built with `obspy.taup.taup_create`. |
| `encoding="STEIM2"` | `"FLOAT64"`, SAC, … | **the encoding decides whether a round-trip is lossless, not the library.** All four MiniSEED encodings tried here and SAC are exact for their dtype; pick one that represents yours. |
| `phase_list=["ttall"]` | `["P", "S"]` | `ttall` is what the reference was generated with. Narrowing it is fine, but then you are no longer comparing against the committed table. |
| the superposition check | — | **this is the part worth copying.** It needs no reference value: filtering a linear combination must equal the combination of the filtered parts, so it catches a filter that is not actually LTI. |
| obspy 1.5.1 | a newer obspy | **the task asserts the version.** The staged reference came from obspy's own tree at 1.5.1; from another tag it would describe a different implementation. |

**Leave the fixture.** 3,000 samples and a 35° source–receiver pair are small on purpose: the point is that someone else published the answer, which no larger dataset here would give you. **Scale it** by pointing `read()` at your own data — every identity below is size-independent.

<details>
<summary>As shipped: 66 arrivals against an independent Java implementation, five bit-exact round-trips, and three identities that need no reference at all</summary>

### The checks

| observable | assertion | observed (arm64) | x86_64 |
|---|---|---|---|
| staged bytes | match pins, count asserted so it can't pass vacuously | 3 of 3 | identical |
| **obspy version** | **== 1.5.1, the tag the reference came from** | **1.5.1** | identical |
| reference arrivals parsed | ≥ 20 per table, P and S present | 33 + 33 | identical |
| **arrivals reproduced** | **every Java TauP arrival, both models** | **66 of 66** | identical |
| **max travel-time difference** | **≤ 0.01 s (the reference's print quantum)** | **0.0050 s** | identical |
| max ray-parameter difference | ≤ 0.01 s/deg | 0.0008 | identical |
| extra arrivals found by obspy | *reported, not asserted* | 0 / 0 | identical |
| **MiniSEED + SAC round-trips** | **bit-identical, dtype preserved** | **5 of 5** | identical |
| **filter superposition** | **relative error < 1e-10** | **8.484e-15** | identical |
| **Parseval's identity** | **relative error < 1e-12** | **1.290e-16** | **3.869e-16** |
| **split / merge** | **bit-identical, sample count unchanged** | **3,000 → 3,000** | identical |
| time arithmetic | `endtime − starttime == (npts−1)/rate` | **0.000e+00** | identical |

**x86_64 (m8i, the `geoscience` env's linux-64 twin)** matches arm64 on every travel time, ray
parameter, round-trip byte and filter residual. Only Parseval's residual moved, `1.290e-16` to
`3.869e-16`: two roundings of an identity whose exact value is zero, a few machine epsilons apart
and over three orders inside the bound on both. The twin differs from arm64 in backports.zstd,
charset-normalizer, fonttools, libglib and libsqlite
([lock](../../envs-x86_64/geoscience-0ac81cc7b2f4.linux-64.lock)).

### The reference is another implementation, in another language

obspy's `taup` is a Python reimplementation of the original **Java TauP** (Crotwell et al.), and
obspy commits *the Java tool's own output* as test data. Reproducing it is therefore a published
reference **and** a cross-code check against an unrelated codebase — strictly stronger than any
identity obspy could satisfy about itself. The filenames encode the Java command line verbatim:

```text
taup_time_-h_10_-ph_ttall_-deg_35   ->   taup_time -h 10 -ph ttall -deg 35
```

33 arrivals per model, two models, **66 of 66 reproduced** — and the agreement is as tight as the
reference can express. The maximum difference is **0.0050 s against a table printed to 0.01 s**,
which is exactly half a quantum: the bound you would get from rounding alone, with nothing left
over.

That is also where the tolerance comes from. It is **set by the reference's precision, not by how
closely the two happen to land** — one print quantum is the tightest claim a table rounded to
0.01 s can support, and the observed value landing at the rounding bound is the evidence that
nothing but rounding separates them ([cross-checks](../../practices/cross-checks.md)).

Two details that matter for the comparison to mean anything:

- **Phase names repeat.** `PP` arrives five times here and `SS` five times, at different ray
  parameters. Each obspy arrival is consumed **once**, so a reference row cannot be satisfied
  twice by the same computed arrival.
- **Extra arrivals are reported, not failed.** The asserted claim is that obspy reproduces
  everything Java TauP reported; an additional branch found by obspy is not evidence either tool
  is wrong. Both models happened to give exactly 33, so the phase *sets* match too.

### Exactness where exactness is available

**Round-trips are bit-identical, not close.** A waveform library's first duty is not to alter
data it is merely storing, and each encoding below represents its dtype exactly — so the
assertion is `array_equal`, with no tolerance anywhere, plus the dtype coming back unchanged:

```text
MSEED  int32    STEIM2     bit-identical, dtype preserved
MSEED  int32    default    bit-identical, dtype preserved
MSEED  float32  default    bit-identical, dtype preserved
MSEED  float64  default    bit-identical, dtype preserved
SAC    float32  default    bit-identical, dtype preserved
```

Five cases rather than one because **lossless-ness is a property of the encoding, not of the
library** — the useful thing to know is which combinations are safe for your data.

**Three identities need no reference value at all**, which is what makes them worth copying:

- **Superposition.** A bandpass filter claims to be linear, so
  `bandpass(3.7x − 1.9y) == 3.7·bandpass(x) − 1.9·bandpass(y)`. Measured to **8.484e-15**
  relative on a signal of scale 5.4e+03 — float64 roundoff through a 4-corner zero-phase
  filter, nothing more. An implementation that normalised per-trace, or leaked state between
  calls, could not satisfy this however plausible its output looked.
- **Parseval.** Time-domain and spectral energy agree to **1.290e-16**. The one-sided spectrum
  needs its interior bins doubled; getting that wrong is the easy mistake, and it would show up
  here as a factor-of-two miss rather than a subtle one.
- **Split and merge.** Cutting a trace into three contiguous pieces and merging returns
  **bit-identical** samples and the same 3,000-sample count — a conservation identity on the
  data itself.

Both energy checks assert the signal is non-zero first, because a trace of zeros satisfies
Parseval and superposition trivially and would prove nothing.

### Pins

| | |
|---|---|
| reference | `obspy/obspy` at tag **1.5.1**, `obspy/taup/tests/data/TauP_test_data/taup_time_-h_10_-ph_ttall_-deg_35{,_-mod_ak135}` |
| waveform | **none staged** — `obspy.read()` returns three bundled 100 Hz BW.RJOB traces |
| image | `quay.io/aarchsci/geoscience@sha256:f0f72f5b…` — obspy 1.5.1, numpy 2.5.3, python 3.14.8 |

**The version match is load-bearing and asserted at run time**, not just at staging: the task
reads `obspy.__version__` and fails unless it is 1.5.1, because a reference taken from another
tag describes a different implementation than the one being checked.

The reference directory also ships the `gendata.sh` that produced the tables, so their
provenance is documented upstream rather than inferred here.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest (the list also carries an `unknown/unknown`
attestation entry, which is not an image).

### Run + verify

```sh
make stage RECIPE=obspy
spawn task run --spec "$(make -s spec RECIPE=obspy)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/obspy/r1/score.tsv" -
```

Fails on a pin mismatch, an obspy that is not 1.5.1, any Java TauP arrival not reproduced within
one print quantum, a round-trip that is not bit-identical, a filter that is not linear, an energy
mismatch, a merge that loses a sample, or a trace with no energy to measure — but check the
bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

In this fixture: instrument-response removal and PPSD (the
[real workload](../../measurements/obspy-real/README.md) checks both on a network-day: evalresp's
stage 1 against numpy, deconvolution superposition and Parseval, PPSD histogram totals). Not anywhere:
response simulation, FDSN web services and any network fetch, event and station metadata
beyond reading it, spectrogram products, beamforming and array analysis, cross-correlation
and template matching, magnitude and moment-tensor work, ray *paths* and pierce points — this
recipe asserts travel times only — and the deeper TauP references in the same directory
(`java_tauptime_testoutput`, the ak135 phase tables), which would widen the comparison
considerably for no extra staging.

</details>
