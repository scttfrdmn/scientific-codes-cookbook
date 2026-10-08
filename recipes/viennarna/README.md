---
tool: viennarna
tool_version: "2.7.2"
image: quay.io/aarchbio/viennarna@sha256:df02a2dc5052d8afdba41acbf18b69df253a2ca7d93b070df247bd4cdd47c51e
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# ViennaRNA — RNA secondary structure, byte-identical to its own golds in 7 modes

Folds 50 RNAs on Graviton4 and compares the complete output to ViennaRNA's committed reference files, across four dangling-end treatments, `--noLP`, and two temperatures. For anyone doing RNA structure prediction on ARM.

## Run it

```bash
make stage RECIPE=viennarna     # once: the test input and 7 golds from tag v2.7.2
spawn task run --spec "$(make -s spec RECIPE=viennarna)" --wait
make ls RECIPE=viennarna

RNAfold --noPS -d 2 < rnafold.small.seq > out
diff -ru rnafold.small.d2.mfe.gold out          # must be empty
#  .........(((.((((...(((((((....((.((((....))))))...)))))))...)))))))(((.(((...))).)))....  (-19.40)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `rnafold.small.seq` | your FASTA or bare sequences | RNAfold reads one sequence per line, or FASTA. Output is `sequence` / `structure (energy)` pairs. |
| `-d 2` | `-d 0/1/3` | **dangling-end treatment changes the answer** — the same sequence gives −19.40 at `-d 2` and −17.30 at `-d 0`. `-d 2` is the default and what most published work uses. |
| `-T 37` (default) | `-T 25`, `-T 40` | temperature enters the energy model, not just as a scale factor. Match it to your experiment. |
| `--noLP` | — | forbids lonely base pairs. Produces more realistic structures; changes the MFE. |
| MFE only | `-p` (partition function) | gives ensemble free energy and base-pair probabilities. **Ensemble free energy ≤ MFE always** — a free inequality to check if you add it. |

**Leave the fixture.** 50 sequences with *committed* expected output is the whole value — ViennaRNA publishes exactly what RNAfold should print for them, which no sequence of your own can give you. **Scale it** to real transcripts once the check passes; RNAfold is O(n³) in sequence length, so a 10 kb RNA is minutes rather than milliseconds.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 20m, cap $0.05. Seven folds over 50 short sequences finish in well under a second; the recorded window is boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: seven byte-exact whole-file comparisons, why that is stronger than an MFE value, and two gold-free identities</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match their pins | all 8 match |
| binary version | **must be 2.7.2** — the golds are version-specific | `RNAfold 2.7.2` |
| **`-d 0`** | **byte-identical to its gold** | **identical** |
| **`-d 1`** | byte-identical | identical |
| **`-d 2`** | byte-identical | identical |
| **`-d 3`** | byte-identical | identical |
| **`--noLP`** | byte-identical | identical |
| **`-T 25`** | byte-identical | identical |
| **`-T 40`** | byte-identical | identical |
| **outputs mutually distinct** | **7 of 7** — else the diffs are one check repeated | **7 of 7** |
| **brackets balanced** | every record, gold-free | **50 checked, 0 malformed** |

### Why byte-exact whole-file beats an MFE value

Each comparison is `diff` over the **entire** output — all 50 sequences, every structure string
and every energy — not a single number. A prediction that got one structure subtly wrong while
landing on the right energy still fails, and so does one that rounded an energy differently.

The invocation is ViennaRNA's own, from `tests/RNAfold/general` at the matching tag:

```sh
RNAfold --noPS -d ${dangles} < ${DATADIR}/rnafold.small.seq > rnafold.fold
diff -ru ${RESULTSDIR}/rnafold.small.d${dangles}.mfe.gold rnafold.fold
```

**Seven modes rather than one, because that is what makes it discriminating.** The golds are
mutually distinct — verified at staging, 7 files, 7 hashes — so an implementation that silently
ignored `-d` or `-T` would match one gold and fail the other six. The task re-checks the same
property on its *outputs*, so a build that collapsed the modes fails even if the individual diffs
somehow passed. Visible in the result: the first sequence folds to **−19.40** at `-d 2` and
**−17.30** at `-d 0`.

### Two identities that need no gold at all

Dot-bracket notation is a balanced-parenthesis language and a structure spans its sequence, so for
every record: brackets balance to zero, never close before opening, and `|structure| == |sequence|`.
Measured 50 structures, 0 malformed.

These hold **whatever the golds say**, so they survive a version bump that changes the predictions —
the same role the C2v symmetry check plays in [xtb](../xtb/README.md). If the golds ever go stale,
this part of the recipe still verifies something real.

### The golds have to be staged, and the version match is load-bearing

The bioconda package ships the `RNAfold` binary but **not** the test suite, so the reference
outputs are not in the image. They are fetched from the `v2.7.2` git tag — the same version as the
image — and pinned by sha256. A gold from another release is a different expected output, which is
why the task refuses to run unless `RNAfold --version` reports 2.7.2: a mismatched binary would
otherwise produce a wall of diffs that looks like a correctness failure.

Staging a pinned file is permitted where a package's build constraints exclude the data
([reference-from-tests](../../practices/reference-from-tests.md)).

### Pins

| | |
|---|---|
| input | `rnafold.small.seq`, `d6bb9afb…` — 50 sequences |
| golds | 7 files from `ViennaRNA/ViennaRNA` tag `v2.7.2`, `tests/RNAfold/results/`, each pinned |
| image | `quay.io/aarchbio/viennarna@sha256:df02a2dc…` (RNAfold 2.7.2) |

cosign-verified against `playgroundlogic/aarchbio`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=viennarna
spawn task run --spec "$(make -s spec RECIPE=viennarna)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/viennarna/r1/score.tsv" -
```

Fails on a version mismatch, any mode differing from its gold, fewer than 7 distinct outputs, or a
malformed structure — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

The partition function and base-pair probabilities (`-p`), which would add the free
ensemble-free-energy ≤ MFE inequality; constrained folding; RNAalifold and consensus structures;
RNAcofold for hybridisation; the alternative energy parameter sets (`dna_mathews*`,
`rna_andronescu2007`) that ViennaRNA's own suite also covers; and comparison against
[infernal](https://github.com/playgroundlogic/aarchbio)'s covariance models, which is a
cross-code check this recipe does not yet make.

</details>
