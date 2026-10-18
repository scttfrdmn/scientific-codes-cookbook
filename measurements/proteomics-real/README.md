# OpenMS + Percolator on a real workload — a six-run HeLa experiment, Graviton4 vs x86

> **The whole experiment costs $0.062 on Graviton4 and $0.074 on x86, and both chips accept the same
> 147,880 PSMs at 1% FDR.** On the spectra where both this pipeline and the submitters' own
> Proteome Discoverer search are confident, they name the same peptide **99.35%** of the time.

Workload: **every run of [PXD083239](https://www.ebi.ac.uk/pride/archive/projects/PXD083239)**
(PRIDE, CC0) — six Orbitrap Exploris 480 DDA runs of HeLa (MCL1 vs GFP, triplicate), **582,823 MS2
spectra**, searched against the submitters' own Swiss-Prot human FASTA (26,836 entries, 20,420 of
them `OX=9606`) plus 26,836 OpenMS decoys, at the submitters' settings: trypsin, 2 missed
cleavages, 50 ppm / 0.06 Da, fixed carbamidomethyl, variable Met oxidation and protein N-terminal
acetyl. Not a slice: one complete experiment is the unit a lab searches, and each run is ~97k
MS2 spectra from a current Orbitrap.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/proteomics-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # a spawn task fetches PRIDE -> bucket
for t in search rescore; do for a in arm64 x86_64; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py proteomics $a "$COOKBOOK_BUCKET" "$(date +%s)" \
    measurements/proteomics-real/$t.task.json)"
done; done
```

Two tasks, one tool each, S3 between them. `search` (openms image) builds the decoys, searches all
six runs, runs the target-decoy FDR, and writes one Percolator input per run with OpenMS's own
writer; `rescore` (percolator image) reads those PINs back from the bucket. The recipe's own pins,
`quay.io/aarchbio/openms@sha256:8e9f3d4b…` / `percolator@sha256:4cc39404…` and their bioconda
x86 twins.

## Results

8 threads for the search, Percolator at its defaults for the rescoring, same box size on both chips,
same staged bytes. A result is the whole six-run experiment, searched and rescored.

| chip | task | instance | tool wall | task script | billed | $/hr | compute $ | billed $ | peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | search | `c8g.2xlarge` | 378 s | 474 s | 647 s | 0.3190 | 0.0335 | 0.0573 | 2.40 GiB |
| Graviton4 | rescore | `c8g.large` | 52 s | 115 s | 227 s | 0.0798 | 0.0012 | 0.0050 | 0.25 GiB |
| Xeon 6975P-C | search | `c8i.2xlarge` | 338 s | 504 s | 651 s | 0.3748 | 0.0352 | 0.0678 | 2.40 GiB |
| Xeon 6975P-C | rescore | `c8i.large` | 61 s | 138 s | 230 s | 0.0937 | 0.0016 | 0.0060 | 0.27 GiB |

**Per experiment: Graviton4 $0.0346 compute / $0.0624 billed ($0.0104 per run); x86 $0.0368 /
$0.0738 ($0.0123 per run).** Tool wall is `SimpleSearchEngine` summed over six runs, or six seed-1
Percolator trainings; task script adds every check below; billed is launch to terminate.

- **x86 searches 1.12× faster, Graviton4 rescores 1.17× faster, and Graviton4 is cheaper per
  result either way** — 6% on compute and 15% billed, which is the hourly price.
- **Everything around the search is slower on x86.** Decoy generation, indexing, FDR, PIN writing
  and the awk checks over 538,544 PSMs take 96 s of the Graviton4 search task and 166 s of the x86
  one, which is why the x86 task is slower overall despite the faster search. Which step carries the
  gap is not separated here; the x86 image's userland is BusyBox.
- **Half the bill is not the tools**: 430 s of tool time inside 874 s billed on Graviton4. Boot,
  Docker install and a 3.2 GB stage-in are fixed per task, so a lab with more runs should put them
  in one task, not one task per run.
- **Staging sizes the box, not the search.** Peak RSS is 2.4 GiB, but the tmpfs high-water mark is
  **4.9 GiB** (six mzML plus outputs) against the 8 GiB a 16 GiB box gives it — which is what the
  [staging budget](../../patterns/data-movement.md) predicts and why the search is not on a
  `large`. A 16-thread probe on `c8g.4xlarge` searched one run in 37 s against 59 s at 8: 1.6× for
  2× the price, so 8 is the cost choice.
- n = 1 per cell. Every count below is identical on both chips, so a repeat would re-measure only
  the timing noise.

## What held at scale, on both chips

| claim | why it must hold | Graviton4 | x86 |
|---|---|---|---|
| staged bytes == the sha256 of what PRIDE served (`source.sha256`) | provenance; PRIDE publishes none for this project | 15 files | same |
| decoys 1:1 with targets, same length and amino-acid composition | a mass-matched decoy space is the FDR's premise | 26,836 / 26,836, 0 mismatched | same |
| every PSM survives FDR, rows aligned; targets + decoys = total | accounting | 538,544 = 343,121 + 195,423 | same |
| q single-valued and non-increasing in the score | definition of a q-value | 0 violations, all 6 runs | same |
| **every q recomputed from the decoy counts** (running min of D/T) | a q-value is a function of the ranking | **343,121 of 343,121, max diff 5.551e-16** | same |
| decoys above the 1% threshold ≤ 1% of the targets it accepts | follows from the recomputation | 660 over 66,370 | same |
| PIN labels == the indexer's target/decoy classes | the writer accounts for every PSM | 6 of 6 runs | same |
| Percolator: in == out, per class; 0 rows in the wrong class | conservation and partition | 538,544, 0 misfiled | same |
| Percolator q non-increasing across score groups | definition | 0 violations | same |
| **same seed twice, byte-identical** | the SVM's CV splits are seeded | **6 of 6 runs** | **6 of 6** |
| **same peptide as the submitters' search, where both accept at 1%** | cross-engine; floor 0.98 from the two FDR budgets | **131,190 / 132,054 = 0.9935** (worst run 0.9914) | same |

The cross-engine row is the strong one. The submitters' answer is Proteome Discoverer 2.5 —
SEQUEST plus *its* Percolator — read out of their own `.pdResult` files, so on each spectrum two
unrelated scoring functions, two decoy constructions and two Percolator trainings are being asked
the same question. At 1% FDR each, about 2% of shared calls can be wrong in expectation, so a
correct pair agrees on ≥ 98%; it agrees on 99.35% ([like with like](../../practices/cross-checks.md): same
spectra, same FASTA, same settings, compared only where both are sure, I/L folded).

Observed, not asserted: **Percolator accepts 147,880 PSMs** against the submitters' published
**168,552** (0.88×), and the OpenMS hyperscore FDR alone accepts 66,370 — Percolator's rescoring is
worth 2.2× here. Seed 7 changes the q-value of 53,771 of GFP_C001_1's 53,772 targets: the seed is
an input. The probe on a 4-vCPU box and the real run on a 2-vCPU box gave GFP_C001_1 the same
seed-1 bytes, so the box size does not enter.

## Across chips: what is a fixed point and what is a path

- **The search result is identical.** On all 538,544 PSMs the peptide, the charge, the decoy flag
  and the q-value match. 21,966 hyperscores (4.1%) differ in the last bits, **max 4.1e-15
  relative**, which changes no q-value — the [recipe](../../recipes/proteomics/README.md) saw the
  same ~2-ULP difference on its committed fixture; this is that, at 538k PSMs.
- **Percolator's ranking is identical; its PEP is not.** PSM order, SVM score, q-value and the
  accepted set match on every run. PEP differs on 20,027–36,692 target rows per run, by at most
  **1.1e-2 absolute** — a fitted smooth over the score distribution, and the same column the recipe
  found differing on percolator's own fixture, where the input bytes were identical. Reported, not
  asserted: nothing here thresholds on PEP. Details in `results/cross-chip.txt`.

## Pins and provenance

| | |
|---|---|
| data | PXD083239, `https://ftp.pride.ebi.ac.uk/pride/data/archive/2026/08/PXD083239/` — 6 mzML, 6 `.pdResult`, the FASTA; sha256 of each in `source.sha256` |
| derived | `ref/ref_<run>.tsv`, the submitters' q ≤ 0.01 target PSMs as (mzML spectrum, peptide), built by `stage.py`; sha256 in `derived.sha256` |
| scripts | `search.sh`, `rescore.sh`, checked in-task against the staged `pins.sha256` |
| images | the recipe's openms 3.5.0 and percolator 3.09.0 pins, arm64 and x86 |

**Nothing is downloaded locally.** `stage.task.json` runs `stage.py` in a spawn task that fetches
from PRIDE straight into the bucket. PRIDE's HTTPS front end gives ~0.6 MB/s per connection even
from us-west-2, so all 13 files are fetched at once (6.4 GB in ~10 minutes).

**The join to the published answer is proved, not assumed.** These mzML are Proteome Discoverer's
own MS2 export, and their `scan=N` is the exporter's numbering — neither the raw scan number PD
stores nor PD's SpectrumID, which drift apart from it. So `stage.py` maps each published PSM onto
the mzML by PD's own retention time: all 168,552 land on exactly one spectrum within 1e-6 min, and
the precursor charge agrees on every one.

## Caveats

- **The PIN is written with a no-op `percolator`.** `PercolatorAdapter` writes `-out_pin` before it
  calls the binary, but insists the binary exists, and one tool per image means it never does in
  the openms image. Pointed at `/bin/true`, it writes the PIN and then exits 1 looking for output;
  that exit is expected, and the PIN's label counts are what is checked.
- **Searched peak lists, not raw.** These are the submitters' deposited PEAK files; the raw files
  would need ThermoRawFileParser, a further image this recipe does not have.
- Settings match the submitters' where SimpleSearchEngine has them; PD's Met-loss+acetyl variant
  and the MCL1-only phosphorylation were not searched.
- 4 of the 26,836 pseudo-reversed decoys equal their target (palindromic tryptic peptides);
  reported, not removed, because DecoyDatabase made them and the FDR saw them.
