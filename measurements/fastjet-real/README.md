# FastJet on a real dataset — 1.1 M LHC Olympics events, three chips

> **anti-k_t R = 1.0 over all 1.1 M events of the LHC Olympics 2020 R&D set (195.9 M hadrons) takes
> 20–33 s of 8 cores for FastJet itself, and every clustering decision is identical on Graviton4,
> Intel and AMD.** An independent anti-k_t written from the definition finds the same jets, bit for
> bit, on 11,000 events. The organisers' published jets are reproduced on all 1.1 M rows. AMD is the
> fastest and the cheapest billed.

Workload: the **complete** R&D dataset of the [LHC Olympics 2020](https://lhco2020.github.io/homepage/)
anomaly-detection challenge, Zenodo record 6466204 (v5, DOI
[10.5281/zenodo.6466204](https://doi.org/10.5281/zenodo.6466204)): 1,000,000 QCD dijet and 100,000
W′ → XY events from Pythia 8 + Delphes 3.4.1, every reconstructed particle as (pT, η, φ). This is the
dataset the challenge's participants clustered, and the record's own features file is the organisers'
anti-k_t R = 1.0 clustering of it ("calculated using fastjet plugins"). So the run reproduces a
published result on its full input, not on a slice. The recipe's own fixture is four hand-written
particles plus one Pythia event.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/fastjet-real/stage-inputs.sh "$B"   # Zenodo -> bucket (00-fetch), -> flat arrays (01-convert), on AWS, sha256-pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py fastjet $a "$B" r3 measurements/fastjet-real/02-cluster.task.json)" --wait
done
bash measurements/fastjet-real/collect.sh "$B" r3     # results/<chip>/, results/compare.txt
```

`01-convert` runs in aarch.science's `md` env, because `hep` has no HDF5 reader for Python. It writes
the particles as flat float64 arrays with the zero padding dropped, and asserts that re-padding them
gives back every source row exactly. The cluster task runs `real.sh` → `cluster.py` (with `brute.py`),
all pinned by sha256 beside the data. Its core:

```python
ev = ak.unflatten(ak.zip({"px": pt*np.cos(phi), "py": pt*np.sin(phi),
                          "pz": pt*np.sinh(eta), "E": pt*np.cosh(eta)}), counts)   # massless, as published
cs = fastjet.ClusterSequence(ev, fastjet.JetDefinition(fastjet.antikt_algorithm, 1.0))
jets, idx = cs.inclusive_jets(), cs.constituent_index()       # 20,000 events per call, 8 worker processes
```

## Results

8 vCPU, 32 GiB (`m8*.2xlarge`), 8 worker processes with one BLAS/OpenMP thread each, the same staged
bytes. FastJet 3.5.2 (Python 3.5.2.0) is aarch.science's `hep` image on Graviton4 and its linux-64 lock
twin on both x86 chips.

| chip | instance | FastJet pass | whole task (pass + checks) | task window | peak RSS / worker | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 189.8 cpu-s (23.7 s) | 150.0 s | 378 s | 2.15 GiB | 0.3590 | 0.0150 | 0.0377 |
| Xeon 6975P-C | `m8i.2xlarge` | 264.9 cpu-s (33.1 s) | 199.3 s | 335 s | 2.15 GiB | 0.4234 | 0.0234 | 0.0394 |
| **EPYC 9R45** | `m8a.2xlarge` | **159.4 cpu-s (19.9 s)** | **107.4 s** | **219 s** | 2.15 GiB | 0.4869 | **0.0145** | **0.0296** |

- **FastJet itself clusters 5,800 events/s per worker on Graviton4, 4,150 on Intel and 6,900 on AMD.**
  The pass column is the anti-k_t clustering summed over the 8 workers; in brackets is that time divided
  by 8. AMD's vCPUs are 8 cores; Intel's are 4 cores × 2 threads (`lscpu`, `results/*/host.txt`). So the
  Intel row is 8 processes on 4 cores, as an `m8i.2xlarge` is sold.
- **The checks cost more than the clustering.** Of the workers' CPU time on Graviton4 the FastJet pass is
  about a sixth. The rest is a second clustering with the hardest particle split, N-subjettiness
  for 2.2 M jets (492 cpu-s), and the brute-force anti-k_t (239 cpu-s). A production pass is the first
  of those alone.
- **Compute is 40–59% of the bill.** The rest is boot, Docker, and either the `hep` image pull (130 s
  on Graviton4) or the x86 env twin's offline install. Staging the 4.7 GB of arrays took 21 s. These
  timings are not compute cost; [the layout page](../../patterns/layout-and-effective-cost.md) is why.
- **The box is sized by memory, not cores.** Each worker peaks at 2.15 GiB (`ru_maxrss`). The container
  peaks at 14.0 GiB on Graviton4 (cgroup `memory.peak`) and 21.8–22.0 GiB on x86, where the installed env's
  page cache counts too. tmpfs holds 4.6 GB of input (7.0 GB on x86 with the env packages). That does
  not fit a 16 GiB `c8*.2xlarge`, so the sweep runs on `m8*`.
- n = 1 per chip. Every clustering decision is identical across chips (below), so a repeat would only
  measure timing noise.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| the inclusive jets' constituents are every particle exactly once | clustering partitions its input | 1,100,000 events, 195,918,046 particles | same | same |
| Σ jets = Σ particles, per component, per event | E-scheme recombination adds 4-vectors | worst 7.1% of the rigorous bound 2(n−1)u Σ\|x\| | same | same |
| splitting each event's hardest particle into two exact halves changes **no** jet | collinear safety | 0 of 1.1 M events change, bit-identical | same | same |
| an independent anti-k_t (`brute.py`) finds the same jets | two implementations of one definition | every 100th event (11,000): same partition, 4-momenta **bit-identical** | same | same |
| the organisers' features file describes these events row for row | the record says so for v5 | 1,100,000 rows, labels 1,100,000 equal, no row matches any other event | same | same |
| their two leading jets (px, py, pz, m) are ours, to their published precision | see below | all 8.8 M values; **98.7–99.8%** bit-equal once ours is printed their way | same, one m₁ fewer | same, one m₁ fewer |
| the first chunk clustered twice | determinism | identical | same | same |

**The precision bound** is built from what the record publishes, not fitted to the differences. The
features are printed to 6 significant figures and stored as float32: −1467.239990 is float32(−1467.24).
That gives half a unit in the 6th figure plus float32's half-ulp. The particles are float32 too (every
value is float32-representable, asserted), so a pipeline that formed and summed the 4-vectors in float32
is as faithful as ours. That adds (n + 2)·2⁻²⁴·E_jet per component. Masses are compared as signed m².
The worst value reaches 0.94 of the bound. The sharper statement is the bit-equality: printed to 6
figures and rounded to float32, our px/py/pz/m equal the published bits for 1,085,757–1,097,266 of
1,100,000 values per column.

**The brute-force anti-k_t** searches the full pairwise d_ij matrix at every step, with no tiling and no
nearest-neighbour geometry. Its rapidity and φ follow PseudoJet's documented definitions. That its
4-momenta are bit-identical, not just within round-off, says it merges in exactly FastJet's order. It
is 11,000 events because it is O(n³) in Python: 239 cpu-s for 1% of the dataset.

**The control.** The same check, run against the raw events file of record v4 and earlier, matched
**0** of 1.1 M rows under any permutation (`results/control-v4-raw/score.tsv`, from an earlier revision of `cluster.py`). The record's 2022 note
says that file was never the one the features describe. So the identification can fail, and does when
it should.

**N-subjettiness** τ₁–τ₃ is reported, not asserted. The organisers name no axes. Of KT, WTA-KT and
OnePass-KT axes (unnormalised, β = 1), only OnePass-KT matches: the median relative difference is 7e-7
against 14% and 0.5–2% for the others (`results/probe-axes/score.tsv`, from a run that computed all three).
With it, 92–96% of the 6.6 M published τ values are bit-equal after the same printing. At most 7 per
column differ by more than 1%. OnePass is an iterative minimisation from k_t seeds, so it is a path:
that tail is a converged-elsewhere minimum, not a clustering difference.

## Across chips: fixed points and paths

**Every clustering decision is identical on all three chips.** The integer fingerprint of every
particle's jet assignment (195.9 M labels, pt-ordered jet rank) is the same on all three. So are the
jet counts (15,983,073; 4,231,032 above 20 GeV) and every assertion above. 37 of the 42 non-timing
values in `score.tsv` are identical; the five that differ are below.

**The floating-point values are a path, and it follows the architecture, not the vendor.** The input
4-vectors' bit fingerprint differs between Graviton4 and x86. So do the jets', the leading jets' and
the τ fingerprints that follow from them. Intel and AMD agree with each other exactly on all four. The
cause is upstream of FastJet: the only inexact operations forming `pt·cos φ`, `pt·sinh η` and the rest
are numpy's transcendental functions, and their last bit is rounded differently on aarch64 and x86-64. FastJet then sums slightly
different inputs. The size of the difference shows in one published value: exactly one m₁ of
1,100,000 crosses a 6th-significant-figure rounding boundary on x86 (1,087,394 bit-equal against
1,087,395). Every bound's worst case is the same to the printed digit.

FastJet does not reach OpenBLAS on this path (geometric clustering, no linear algebra), so no
`openblas_get_corename()` is recorded.

## Caveats

- Delphes particles, not detector data: there is no pileup or MPI in this sample, so jet areas and
  subtraction are untouched here as in the recipe.
- One task of 1.1 M events is one analysis pass. A real analysis clusters more events, or the same
  events with several radii. Its cost is this per-pass result times the pass count, plus one boot per
  task.
- Prices are on-demand us-west-2: spawn's `cost_estimate` over the task window (wrapper start to
  stage-out done). The window excludes instance boot before the wrapper starts.
- The x86 runs use the `hep` env's linux-64 twin (`envs-x86_64/hep-327e2aedf1d0.linux-64.lock`).
  fastjet, awkward and numpy versions are identical.
