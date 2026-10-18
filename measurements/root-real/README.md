# ROOT on a real dataset — 61.5 M CMS events through df102, three chips

> **ROOT's own dimuon tutorial over the whole of a CMS Open Data release takes 14–27 s of an 8-core
> event loop, and every number it produces is identical on Graviton4, Intel and AMD.** An
> independent reader (uproot) decodes the same 2.2 GB to the same bits, and reads ROOT's
> multi-threaded Snapshot back bit-identical. AMD is the fastest; Graviton4 is the cheapest per
> result billed, because 75–80% of each bill is boot, image and staging.

Workload: the **complete** CMS Open Data record 12341, *DoubleMuParked dataset from 2012 in NanoAOD
format reduced on muons* (DOI [10.7483/OPENDATA.CMS.LVG5.QT81](https://doi.org/10.7483/OPENDATA.CMS.LVG5.QT81)):
Run2012B + C, **61,540,413 events, 149,322,456 muons**, one 2,244,449,133-byte file. This is the
input of ROOT's own [`df102_NanoAODDimuonAnalysis`](https://root.cern/doc/master/df102__NanoAODDimuonAnalysis_8py.html)
tutorial, run here the way an analysis runs one file: one multi-threaded `RDataFrame` pass that
selects exactly-two-opposite-charge muon events, histograms their mass on df102's 30,000 bins,
fits the resonances and writes a skim. Not a slice; the recipe's fixture is 20,000 seeded floats.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/root-real/stage-inputs.sh "$B"     # record 12341 -> bucket, fetched on AWS (00-fetch), sha256-pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py root $a "$B" r2 measurements/root-real/01-dimuon.task.json)" --wait
done
bash measurements/root-real/collect.sh "$B" r2        # results/<chip>/, results/compare.txt
```

The task runs `real.sh` → `analysis.py` (ROOT) then `check.py` (uproot + numpy, **no ROOT
imported**), all pinned by sha256 beside the data. The core of `analysis.py`:

```python
ROOT.EnableImplicitMT(8)
df  = ROOT.RDataFrame("Events", "Run2012BC_DoubleMuParked_Muons.root")
dos = df.Filter("nMuon == 2").Filter("Muon_charge[0] != Muon_charge[1]")
dm  = dos.Define("Dimuon_mass", "rr_mass(Muon_pt, Muon_eta, Muon_phi, Muon_mass)")   # PtEtaPhiMVector, double
h   = dm.Histo1D(("Dimuon_mass", "", 30000, 0.25, 300), "Dimuon_mass")
dm.Snapshot("Events", "os.root", [...], lazy)       # every result booked on ONE event loop
```

## Results

8 vCPU, 16 GiB (`c8*.2xlarge`), `EnableImplicitMT(8)`, the same staged bytes. ROOT 6.40.04 is
aarch.science's `hep` image on Graviton4 and its linux-64 lock twin on both x86 chips.

| chip | instance | event loop (tool wall) | cores busy | task window | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 22.3 s | 7.11 | 187 s | 2.74 GiB | 0.3190 | 0.0020 | **0.0166** |
| Xeon 6975P-C | `c8i.2xlarge` | 27.2 s | 7.24 | 173 s | 2.71 GiB | 0.3748 | 0.0028 | 0.0180 |
| **EPYC 9R45** | `c8a.2xlarge` | **14.3 s** | 7.05 | **142 s** | 2.46 GiB | 0.4311 | **0.0017** | 0.0170 |

- **AMD runs the event loop 1.56× faster than Graviton4 and Intel 0.82×.** That is 4.29, 2.75 and
  2.27 M events/s. Per unit of compute AMD is 13% cheaper than Graviton4 and Intel 43% dearer.
  AMD's 8 vCPUs are 8 cores; Intel's are 4 cores × 2 threads (`lscpu` on each box, `results/*/host.txt`).
- **Billed, Graviton4 is cheapest, but only by 3–9%.** The event loop is 10–16% of each window. The
  rest is boot, Docker, and either the 2.86 GB `hep` image pull (80 s on Graviton4) or the x86 env
  twin's offline install (63 s). Staging the 2.2 GB file took 8 s (18 s with the x86 packages).
  These timings are not compute cost; [the layout page](../../patterns/layout-and-effective-cost.md)
  is why. Only a longer job, or many files per task, makes the chip the deciding term.
- **The box is sized by cores.** ROOT peaks at 2.7 GiB and uproot at 3.0 GiB. tmpfs holds the
  2.2 GB input, the 0.86 GB skim and, on x86, the 2.3 GiB package set: 5.4 of 7.7 GiB. The first
  run, on `m8g.2xlarge` (32 GiB), measured that, so the sweep dropped to `c8*`.
- ROOT keeps 7.05–7.24 of 8 cores busy across the loop, so the IMT pool is used.
- n = 1 per chip. Every asserted value is identical across chips (below), so a repeat would only
  measure timing noise.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| event count | CERN Open Data's `number_events` | 61,540,413 | same | same |
| nMuon distribution sums to the events; its `==2` bin equals the filter; OS + SS = 2-muon | a cutflow partitions | 31,104,343 = 24,067,843 + 7,036,500 | same | same |
| ROOT and uproot decode the file to the **same bits** | two implementations of one format | count, Σ nMuon = 149,322,456, all 66 nMuon bins, and the bit fingerprint of all five muon branches | same | same |
| ROOT's MT Snapshot (jagged branches) reads back in uproot = the same selection made by uproot on the source | format round-trip | 24,067,843 events, five fingerprints equal | same | same |
| mass histogram: bins + under/overflow = selected pairs | partition | 24,067,843 (112,529 under, 7,909 over) | same | same |
| per event, numpy's `E² − |p|²` vs ROOT's `PtEtaPhiMVector` | double-precision forward error, ≤ 64 ε (E₁+E₂)² | 6.76 ε | 7.40 ε | 7.40 ε |
| one event loop for every booked result | lazy booking | `GetNRuns() == 1` | same | same |
| J/ψ, ψ(2S), Υ(1S), Z fitted within 1% of PDG | see below | worst −0.35% (Z) | same | same |

The **bit fingerprint** is Σ over muons of the value's `uint32` bits × (its index in the event + 1),
mod 2⁶⁴. Integer addition is associative, so this is exact in whatever order the 8 threads visit
the clusters. Equality means every decoded value of every branch matched, in its position. A
float sum would not be exact under thread reordering, and could not be held to equality.

**The 1% resonance bound** is a physics bound, not a fit to the observed values. This is
uncalibrated 2012 muon momentum: the scale is off by ~0.1–0.3%. A Gaussian fit is also pulled low
by final-state radiation. Measured: J/ψ −0.12%, ψ(2S) −0.11%, Υ(1S) −0.12%, Z −0.35%. A
mass formula with a wrong term, or a column swap, would move every peak by far more than 1%. The
J/ψ fit's χ²/ndf is 589: one Gaussian does not describe a 1.9 M-pair peak with an η-dependent
resolution. The fitted mean is still the claim, and it is reported with that caveat.

## Across chips: fixed points and paths

**Everything ROOT computed is identical on all three chips.** That is 24 of 24 values in
`root.tsv`, including the four fits to every printed digit, and all 30,002 mass-histogram bins.
On x86 the uproot side matches Graviton4 too: 25 of 28 values, every assertion among them.

**One value is a path: the worst per-event disagreement between numpy's mass and ROOT's.** It is
6.756 ε on Graviton4 and 7.401 ε on both Intel and AMD. Intel and AMD agree exactly, so this is
the architecture's math library (numpy's and glibc's `sin`/`cos`/`sinh` per ISA), not the
vendor. The worst relative mass difference is 1.6e-8 against 7.6e-9. Both sit ~9× inside the
bound, and not one event changes bin. ROOT's own `Dimuon_mass`
values give an identical histogram on all three chips, so ROOT's mass is not what moved.

The skim's size differs by chip (862,200,367 / 862,200,861 / 862,201,128 B). The multi-threaded
Snapshot merges clusters in the order the threads finish them, so the bytes are a path. The
contents are not: all three read back to the same fingerprints. Compare skims by content, never
by checksum.

ROOT does not reach OpenBLAS on this path (Minuit fits, no linear algebra), so no
`openblas_get_corename()` is recorded.

## Caveats

- One file of 2.2 GB is one analysis job's input. A real analysis runs many such files, and its
  cost is this per-file result times the file count, plus one boot per task. Put several files in
  one task to amortise the 128–165 s of each window that is not the event loop.
- Prices are on-demand us-west-2: spawn's `cost_estimate` over the task window (wrapper start to
  stage-out done). The window excludes instance boot before the wrapper starts.
- The x86 runs use the `hep` env's linux-64 twin. ROOT, uproot, awkward and numpy versions are
  identical (`envs-x86_64/hep-4f0bac796086.linux-64.lock`).
