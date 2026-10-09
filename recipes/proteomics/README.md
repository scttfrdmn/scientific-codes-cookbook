---
tool: openms
tool_version: "3.5.0"
images:
  - quay.io/aarchbio/openms@sha256:8e9f3d4b0a80c509dc94229a2f02bfd45b6f4375eb6ec7d9e2032e08f4c0e725
  - quay.io/aarchbio/percolator@sha256:4cc39404eeba4f8093fdd380c1bd4f615d0e97c5e0efe4fe0e44bac63fbd6df6
spawn_version: 0.126.1
last_verified: 2026-10-09
---
# OpenMS + Percolator — peptide identification and FDR on Graviton

Searches a real BSA digest against a target-decoy database on Graviton4, then rescores an independent PSM set with Percolator. For anyone doing bottom-up proteomics on ARM.

## Run it

```bash
make stage RECIPE=proteomics      # once: OpenMS 3.5.0 and percolator rel-3-08 fixtures
for s in $(make -s spec RECIPE=proteomics); do spawn task run --spec "$s" --wait; done
make ls RECIPE=proteomics

SimpleSearchEngine -in BSA1.mzML -database db.fasta -out psm.idXML \
  -Search:precursor:mass_tolerance 0.05 -Search:precursor:mass_tolerance_unit Da \
  -Search:fragment:mass_tolerance 0.1   -Search:fragment:mass_tolerance_unit Da \
  -Search:precursor:min_charge 1 -Search:precursor:max_charge 3 -threads 1
PeptideIndexer -in psm.idXML -fasta db.fasta -out indexed.idXML \
  -decoy_string _rev -decoy_string_position suffix
FalseDiscoveryRate -ini fdr.ini -in indexed.idXML -out fdr.idXML -PSM true -protein false

percolator -U -S 1 -m target_psms.tab -M decoy_psms.tab percolatorTab
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `-Search:fragment:mass_tolerance 0.1` | — | **0.1 Da / 100 ppm is the ceiling, not a choice.** Above it the deisotoper aborts the run outright, so a low-res `0.5 Da` never searches at all. |
| `-decoy_string _rev -decoy_string_position suffix` | **read your database** | **match the convention or the FDR is silently meaningless.** This database marks decoys by a `_rev` *suffix*; a prefix test finds none and every decoy scores as a target. |
| `-protein false` | add protein inference | `-protein` defaults to **true** and the tool then refuses, correctly — protein-level FDR needs an inference step. Every check here is PSM-level. |
| `SimpleSearchEngine` | `CometAdapter`, `MSGFPlusAdapter` | the adapters ship in this image; **the Comet and MSGF+ binaries do not.** One tool per image, so a wrapper has nothing to wrap. |
| `-S 1` (percolator seed) | any fixed value | **keep it fixed.** The SVM trains on randomised cross-validation splits: seed 7 changed the q-value of *all* 9,852 target PSMs. |
| `percolatorTab` | your own PIN | percolator gets its own fixture because `PercolatorAdapter` cannot bridge two images — see below. |

**Leave the fixtures.** The point of both is that someone else already published the answer: OpenMS commits the expected output for its own search, and ships an OMSSA identification of this exact mzML. **Scale it** by swapping your own mzML and database; every check here is size-independent.

## Shape, size, cost

Two tasks on `m8g.large` (2 vCPU / 8 GiB), TTL 45m as a **backstop** with `cost_limit` $0.15 and $0.12 as the real guard. The search is **4 s** and three percolator trainings **2 s**; the recorded 93 s and 62 s windows are boot, Docker install and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a committed output reproduced to the last bits, q-values recomputed from scratch, and why this is two fixtures rather than one pipeline</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins, and the count verified is asserted | 9 of 10 (task 1), 1 (task 2) |
| database pairing | **1:1 by `_rev` suffix**, asserted at staging | 9,439 + 9,439, 0 unpaired |
| **committed hits reproduced** | **== 3, same (sequence, charge)** | **3, exact** |
| **committed scores** | **relative agreement < 1e-6** | **2.692e-16** (0 of 3 bit-identical) |
| PSMs through FDR | count unchanged, rows aligned | 957 → 957, **0 misaligned** |
| target / decoy split | decoys present, else FDR is meaningless | 513 / 444 |
| q-values in [0,1] | 0 outside | 0 |
| **q single-valued in score** | **0 scores mapping to two q-values** | **0** |
| **q non-increasing in score** | **0 violations** | **0** |
| **q recomputed from decoy counts** | **all 513 match, < 1e-12** | **max diff 5.551e-16** |
| **top protein vs the reference** | **same protein, derived from the reference file** | **ALBU_BOVIN both** |
| **reference peptides recovered** | **≥ 8 of 23** | **9 (0.3913)** |
| **percolator conservation** | **in == out, per class** | **19,674 = 9,852 + 9,822** |
| **percolator partition** | **0 rows in the wrong class** | **0 wrong, 0 mixed** |
| percolator q / PEP in [0,1] | 0 outside | 0 / 0 |
| **percolator q monotone** | **0 violations across score groups** | **0** |
| decoy fraction at q≤0.01 / 0.05 | ≤ 2× the threshold | 0.0102 (1.02×) / 0.0532 (1.06×) |
| **same seed reproducible** | **byte-identical across two runs** | **`d740351d…` twice** |

### The strongest check: the q-values are recomputed, not just inspected

A target-decoy q-value is not a measurement. It is a function of where a PSM sits in the score
ranking and how many decoys outrank it — so it can be recomputed from nothing but the scores
and the decoy flags, and must come back the same. All **513** reported q-values do, to
**5.551e-16**, which is double-precision representation noise against 17-digit decimals.

**The formula is the measured one, not the advertised one.** The run sets `conservative=true`,
whose description names `(D+1)/T`. The emitted q-values follow a running minimum of **`D/T`**:
at the top of the ranking `D=0` and the reported q is `0`, where `(D+1)/T` would be `1/13`.
What is recorded here is what the output does; why the flag reads that way is not something
this recipe establishes.

An earlier version of this check asserted a *bound* — "the decoy fraction among accepted PSMs
is at most the threshold plus a little" — and it passed while being wrong: at q≤0.01 there are
13 targets and **0** decoys above the threshold, so the quantity it compared was 0.077 against
0.01, 7.7× over, and only the slack hid it. A bound that passes for the wrong reason is worse
than no check. The recomputation has no slack to hide in.

### Reproducing the committed output needs its configuration

OpenMS commits `SimpleSearchEngine_1_out.idXML` **and the `.ini` that produced it**, so the
recipe passes the `.ini` rather than reconstructing settings: it sets `threads=1`, fragment
tolerance 0.1 Da, empty fixed modifications and variable Oxidation (M). Explicit
`-in/-database/-out` override the absolute paths left in it from the committer's machine.

The three hits come back with identical sequences and charges, and scores that agree to
**2.692e-16 relative — about 2 ULP, not bit-identical**:

```text
committed 13.195273686121645   reproduced 13.195273686121649
committed 42.152939453592808   reproduced 42.152939453592801
committed 47.256181302684944   reproduced 47.256181302684936
```

Reported rather than smoothed over, because the distinction is the interesting part: a
hyperscore is a per-spectrum sum over a handful of peaks, so this is a different libm or
compiler rounding the last bit, not a different computation. One of the three expected hits is
a **decoy** (`test2_rev`), which the fixture includes on purpose, so the comparison covers both
classes.

### Two fixtures, not one pipeline — and why that is not a workaround

The appealing design is to hand Percolator the PSMs OpenMS just searched, via
`PercolatorAdapter`. **It cannot work here.** The adapter registers `percolator_executable` as
a *required* input file tagged `is_executable`, so it aborts during parameter validation —
before it ever writes the pin file — unless the percolator binary sits in the same image. One
tool per image means it never does, and there is no flag to stop after writing the PIN.

So each tool is checked against a fixture it can reach: OpenMS against its own committed
output and a real BSA run, percolator against its own committed PIN. The two legs are
**not** scoring the same PSMs, and this page says so rather than implying otherwise.

### The cross-code check, and the honest size of it

`BSA1_OMSSA.idXML` is OpenMS's own OMSSA identification of **this exact mzML against this exact
database** — same spectra, same search space, a different engine. That is what makes it
comparable at all ([cross-checks](../../practices/cross-checks.md)).

The asserted claim is **categorical**: the protein with the most confident PSMs must be the
same one, and the expected value is read out of the reference file rather than written into the
script. Both put **ALBU_BOVIN** first — 35 of 44 reference PSMs, 12 of 13 here — which is the
right answer for a bovine serum albumin digest.

Peptide overlap is **9 of 23** reference backbones. Compared on the bare backbone, because the
reference scored Carbamidomethyl as a *variable* modification while this run fixes it, so the
notation differs by configuration rather than by result. The floor of 8 is justified by being
far above chance — these are peptides of 9,439 proteins — and deliberately loose, because
**the fragment tolerances cannot be matched**: the reference used 0.3 Da and this tool refuses
anything above 0.1 Da, so the search here is 3× tighter and loses real sensitivity on
low-resolution MS2. A floor is the honest shape for a comparison with a known handicap.

### Percolator: a seeded search, so determinism comes before any exact claim

Percolator trains an SVM on randomised cross-validation splits, so its ranking is a property of
the seed as well as the data. Two runs at `-S 1` are **byte-identical**; `-S 7` changes the
q-value of **all 9,852** target PSMs. The seed-sensitivity direction is *reported, not
asserted* — had a different seed happened to give the same answer, that would be a fact about
this dataset, not a failure ([the same rule the assemblers earned](../../practices/cross-checks.md)).

Its conservation identity is exact and needs no tolerance: every one of **19,674** input PSMs
comes back exactly once, **9,852** targets and **9,822** decoys, matching the PIN per class.

Three implementation details that are easy to get wrong here:

- **`SpecId` is not unique in a PIN.** `103111-Yeast-2hr-01_27_3_1` appears twice, so the
  obvious partition check — join each output row back to the PIN's `Label` by `SpecId` — keeps
  whichever row came last and reports thousands of false misplacements. The check instead uses
  the convention each row carries itself; staging anchors that to `Label` by verifying the two
  agree on all 19,674 rows.
- **`proteinIds` is itself tab-separated** and spills across columns 6..NF, so every protein
  field is examined rather than the last one.
- **Score prints to 6 significant figures**, so PSMs with different true scores print the same
  score and carry legitimately different q-values. Counting those as monotonicity violations
  measures the output's precision, not the tool — it reported 2 on targets and 82 on decoys,
  every one at a tied printed score. Ties are allowed any internal order and the ordering is
  asserted across score groups; the tied groups (2 and 78) are reported.

### Pins

| | |
|---|---|
| committed fixture | `OpenMS/OpenMS` at `release/3.5.0`, `src/tests/topp/SimpleSearchEngine_1.{mzML,fasta,ini}` + `_out.idXML` |
| spectra | `share/OpenMS/examples/BSA/BSA1.mzML` — 1,684 spectra, 1,120 MS2 |
| database | `18Protein_SoCe_Tr_detergents_trace_target_decoy.fasta` — 9,439 + 9,439 |
| reference | `share/OpenMS/examples/BSA/BSA1_OMSSA.idXML` — 44 PSMs, 23 peptides |
| PIN | `percolator/percolator` at `rel-3-08`, `data/percolator/tab/percolatorTab` — 19,674 PSMs |
| images | openms `@sha256:8e9f3d4b…` (3.5.0), percolator `@sha256:4cc39404…` (3.09.0) |

Everything is version-matched to the image it runs in, which is what makes the committed
numbers usable at all. The PIN is **byte-identical at `rel-3-08` and `rel-3-09`**, established
by fetching both rather than assumed, so the pin is stable across that step.

*The database's name tells the truth and a prefix test does not:* it really is target-decoy,
9,439 + 9,439, marked by a `_rev` **suffix**. A check for `DECOY_`/`rev_`/`XXX` prefixes finds
zero and reads as "the filename lies" — the first version of this recipe concluded exactly that
and rejected the database. Staging now asserts the pairing and the convention, because
`-decoy_string` has to match the file or every decoy scores as a target.

Both images cosign-verified; signatures cover the **manifest-list** digest, so verify the tag
and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=proteomics
for s in $(make -s spec RECIPE=proteomics); do spawn task run --spec "$s" --wait; done
aws s3 cp "s3://$(make -s print-bucket)/runs/proteomics/r1/score.tsv" -
aws s3 cp "s3://$(make -s print-bucket)/runs/proteomics/r1/score-percolator.tsv" -
```

Fails on a pin mismatch, a database whose decoy pairing is not 1:1, a committed hit that does
not reproduce, a q-value that cannot be recomputed from the decoy counts, a top protein that
disagrees with the reference, fewer than 8 shared peptides, a PSM lost or misfiled by
percolator, or two same-seed runs that differ — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

**The analysis is a staged, pinned file, not inline in the spec.** A spawn task command travels
in EC2 user data, capped at **16,384 bytes**; the inline version pushed task 1 past it and
`RunInstances` refused to launch at all. It is also shell rather than Python, because this
image ships no interpreter — a single-tool bioconda image around a C++ toolkit, so `python3` is
absent even with `/opt/conda/bin` on `PATH`.

### Not covered

Quantification of any kind — label-free (`FeatureFinderCentroided`, `ProteinQuantifier`), TMT
and iTRAQ (`IsobaricAnalyzer`), SILAC — plus protein inference (`Epifany`, `ProteinInference`)
and therefore protein-level FDR, retention-time alignment across runs, `OpenSwath` for DIA, and
a genuine search-engine cross-validation, which needs Comet or MSGF+ in a reachable image.
Percolator's rescoring is also never applied to this recipe's own PSMs, for the adapter reason
above.

</details>
