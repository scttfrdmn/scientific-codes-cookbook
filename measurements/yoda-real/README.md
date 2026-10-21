# YODA on a real dataset — 61.5 M CMS events and all of Rivet's reference data, three chips

> **YODA fills 408 M entries from the whole of CMS Open Data record 12341 in 16–34 cpu-s, and every
> bin count is identical on Graviton4, Intel and AMD — and identical to ROOT's TH1D of the same
> bytes.** It reads all 1,955 reference files Rivet 4.1.4 ships (48,368 objects) exactly as an
> independent parser does. What is not exact is its text writer: a `.yoda` file holds 7 significant
> digits whatever `precision=` asks, so a written file does not reproduce its own bin edges.

Workload, two parts:

- **Filling.** The **complete** CMS record 12341, *DoubleMuParked 2012 NanoAOD reduced on muons*
  (DOI [10.7483/OPENDATA.CMS.LVG5.QT81](https://doi.org/10.7483/OPENDATA.CMS.LVG5.QT81)): 61,540,413
  events, 149,322,456 muons, the 2.2 GB file [root-real](../root-real/README.md) staged — reused, not
  copied. It is split into 247 jobs of 250,000 events, each filling its own five objects, and merged
  with `+`, the way a batch of Rivet jobs is run and `yodamerge`d. Not a slice.
- **Reading.** Every `.yoda.gz` reference file in Rivet 4.1.4 (`analyses/` at commit `74816bd4cf29`,
  the `rivet-4.1.4` tag): the experiments' published measurements, HEPData-derived, as every Rivet
  comparison loads them. 6.37 M rows, 7.23 M error values. The recipe's fixture is 5,000 seeded floats.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/root-real/stage-inputs.sh "$B"     # record 12341, if not already staged
bash measurements/yoda-real/stage-inputs.sh "$B"     # Rivet refs -> bucket on AWS (00-fetch), sha256 + manifest pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py yoda $a "$B" r7 measurements/yoda-real/01-real.task.json)" --wait
done
bash measurements/yoda-real/collect.sh "$B" r7        # results/<chip>/, results/compare.txt
```

The task runs `real.sh` → `fill.py` (uproot reads, YODA fills, numpy checks) then `refs.py`, all
pinned by sha256 beside the data. The fill core, per 250,000-event job:

```python
h = {"/CMS/mass": yoda.Histo1D(30000, 0.25, 300.0, "/CMS/mass"),           # df102's binning
     "/CMS/pt_mass": yoda.Profile1D(list(yoda.logspace(300, 0.25, 300.0)), "/CMS/pt_mass"),
     "/CMS/eta_phi": yoda.Histo2D(48, -2.4, 2.4, 64, -pi, pi, "/CMS/eta_phi"), ...}
for x in masses: h["/CMS/mass"].fill(x)                                    # python floats: ~60 ns a fill
merged["/CMS/mass"] += h["/CMS/mass"]                                      # 247 jobs, in order
```

## Results

8 vCPU, 32 GiB (`m8*.2xlarge`), 8 worker processes with one BLAS/OpenMP thread each, the same staged
bytes. YODA 2.1.4 is aarch.science's `hep` image on Graviton4 and its linux-64 lock twin on both x86
chips.

| chip | instance | fill pass (YODA fills) | ref read (YODA) | task window | peak RSS / worker | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.2xlarge` | **47.9 s** (25.5 cpu-s) | 7.9 cpu-s | **390 s** | 1.29 GiB | 0.3590 | **0.0048** | **0.0389** |
| Xeon 6975P-C | `m8i.2xlarge` | 91.0 s (34.2 cpu-s) | 11.0 cpu-s | 857 s | 1.30 GiB | 0.4234 | 0.0107 | 0.1008 |
| EPYC 9R45 | `m8a.2xlarge` | 59.7 s (**16.4 cpu-s**) | **5.7 cpu-s** | 351 s | 1.30 GiB | 0.4869 | 0.0081 | 0.0475 |

- **The fill pass is the result**: uproot reading 61.5 M events, building the values, filling five
  objects in 8 workers, plus the numpy references. YODA's own fills are the bracketed cpu-s: 16.0 M
  fills per cpu-s on Graviton4, 12.0 M on Intel, 24.9 M on AMD. AMD fills fastest but its pass is
  slower than Graviton4's, because worker start-up and reading dominate a 60 s pass. Intel's 8 vCPUs
  are 4 cores × 2 threads; Graviton4's and AMD's are 8 cores (`lscpu`, `results/*/host.txt`).
- **Intel's window is 2.2× the others' because of `yodamerge`, not filling.** Merging the 247 job files
  with the `yodamerge` CLI took **404 s** on Intel against 30.5 s on Graviton4 and 12.3 s on AMD, the
  same in both runs (r6 and r7). Reading the one merged 30,000-bin file took 1.64 s against 0.13 and
  0.05. Reading Rivet's 1,955 small files is not slow on Intel (11.0 cpu-s). The cause is not
  established; it is the same x86 package set on AMD, which is fast.
- **Billed, Graviton4 is cheapest**: $0.039 against $0.047 (AMD) and $0.101 (Intel). The rest of each
  window is boot, Docker, the 2.86 GB `hep` image pull (about a minute on Graviton4) or the x86 env twin's
  offline install, and the checks: `refs.py`'s independent parser is 136–226 s, YODA's reads 6–11
  cpu-s of it. These timings are not compute cost; [the layout page](../../patterns/layout-and-effective-cost.md)
  is why.
- **The box is sized by memory.** A worker peaks at 1.30 GiB (`ru_maxrss`) on 250,000 events and a
  reference-read worker at 3.47 GiB. The container peaks at 10.5 GiB on Graviton4 and 20.6 GiB on x86
  (cgroup `memory.peak`), where the installed env's page cache counts too. 1 M-event jobs peaked at
  1.66 GiB per worker and did not fit 16 GiB with the staged file, so the sweep runs on `m8*`.
- n = 1 per chip for the table. Every count is identical across chips (below), so a repeat would
  only measure timing noise. The earlier r6 runs repeated each fill pass within 4% and `yodamerge` within 1%.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| bins + under/overflow hold every fill; `numEntries() == sumW ==` fills | a histogram partitions its fills | 5 objects, 408,321,011 fills | same | same |
| every bin, flows included, == numpy's count of the same values on the object's own edges | an independent binning | 30,002 + 302 + 202 + 3,300 + 66 bins, 0 differ | same | same |
| `/CMS/mass` and `/CMS/nmuon` == root-real's ROOT TH1D of the same bytes | two codes, one histogram | all 30,002 bins (112,529 under, 7,909 over) and all 66 | same | same |
| `sumWX`, `sumWX2`, `sumWY`, `sumWY2` == numpy's sum of the same terms in fill order | sequential sums, same order | bit-identical, every bin | same | same |
| `sumWXY` of the profile, same comparison | within 3 n u Σ\|t\| (Higham) | 240 of 302 bit-equal, worst 1.0e-3 of the bound | 302 of 302 | 302 of 302 |
| serialize in the worker → deserialize in the parent | the merge is built on it | lossless, 1,235 objects | same | same |
| merged in reverse job order | addition of counts is exact | every count identical | same | same |
| `rebinXBy(100)`: 30,000 → 300 bins | conservation | each bin == the sum of its 100, flows unchanged | same | same |
| `scaleW(1/11.6)` | a multiply per bin | `sumW == fl(sumW k)`, `sumW2 == fl(sumW2 fl(k²))`, bitwise | same | same |
| `normalize(1.0)` on 30,002 bins | 2 n u for re-summing n bins | 1 + 1.155e-13: 0.017 of the bound | same | same |
| the first job filled twice | determinism | identical | same | same |
| every Rivet reference file == the commit's | `refs.manifest` | 1,955 of 1,955 | same | same |
| `yoda.read` == an independent parser of the text | two readers, same decimal strings | 48,368 objects, 6,374,501 values, 7,230,958 errors and 842,001 edges, bit for bit | same | same |
| write what was read → read back | %.6e holds a ≤7-digit source exactly | all 13,605,459 values unchanged | same | same |

**The ROOT comparison** is the sibling-code check on identical bytes: the masses are numpy's, with
root-real's own formula, and its committed histogram is ROOT's `TH1D` with `EnableImplicitMT(8)`. It
holds even though YODA's bin edges differ between chips (below): no muon pair sits within a few ulps
of an edge.

**The text writer, reported.** `writeYODA` prints `%.6e`; `precision=17` writes byte-identical files.
Written and read back, the merged objects change in 72,606 of 180,166 bin fields (worst relative
4.94e-7, the format's half unit in the 7th digit), 2 bin *counts* at or above 10⁷ change, and the
re-read objects carry different edges in 27,824–27,830 of 30,001 (mass) and 90–95 of 114 (η, φ). `yodamerge`
over the 247 job files gives the same 72,606 and 2 differences. Merge in memory, or keep counts below
10⁷ per bin per file.

Reference data is written to 6 decimals in the first place, so its round trip is exact: no value in
Rivet's set carries more than 7 significant digits. One object (`ATLAS_2015_I1397635/d01-x01-y01`, an
`Estimate0D`) has its error sources written back in a different order; compared by label, they are
unchanged.

## Across chips: fixed points and paths

**Every count is identical on all three chips**: the fingerprints of all five objects' bin counts, the
ROOT comparison, every number in `refs.tsv`. 93 of the 104 non-timing values in `fill.tsv` and
`refs.tsv` are identical on all three; Intel and AMD agree with each other on all 104.

**Three things are a path, and each follows the architecture, not the vendor:**

- **YODA's bin edges.** On x86 the 30,000 linear mass edges equal `numpy.linspace` exactly; on
  Graviton4, 68 of them differ by an ulp, and the η axis's middle edge is `-1.11e-16` instead of `0.0`.
  This is consistent with the aarch64 build fusing the multiply-add in `lo + i·width`, which is not
  established. The log-spaced `mu_pt` edges are identical on all three.
- **The mass and profile sums** (`fp_bin_fields_mass`, `_pt_mass`): the masses themselves differ in
  the last bits, from numpy's `sin`/`cos`/`sinh` per ISA, as root-real measured. The counts do not move.
- **`sumWXY`** on Graviton4 differs from numpy's sequential sum in 62 of 302 bins, within the bound;
  on x86 it is bit-equal. The other four sums are bit-equal everywhere.

YODA does not reach OpenBLAS on this path (binning and summation), so no `openblas_get_corename()` is
recorded.

## Caveats

- Data, not Monte Carlo: every fill has weight 1, so `sumW == numEntries` and the sumW/sumW2
  distinction is exercised through `scaleW` and the profile's `y` moments, not event weights.
- Rivet itself is not in the `hep` env; this exercises the objects Rivet produces and the references
  it loads, not an analysis.
- HEPData's own export was not usable as the reference source: `hepdata.net` serves a Cloudflare
  challenge to scripted clients. Rivet's reference files are HEPData-derived (their titles carry the
  `10.17182/hepdata.*` DOIs) and pinned to a commit, so they are the stabler pin anyway.
- Prices are on-demand us-west-2: spawn's `cost_estimate` over the task window (wrapper start to
  stage-out done). The window excludes instance boot before the wrapper starts.
- The x86 runs use the `hep` env's linux-64 twin (`envs-x86_64/hep-327e2aedf1d0.linux-64.lock`);
  yoda, uproot, awkward and numpy versions are identical.
