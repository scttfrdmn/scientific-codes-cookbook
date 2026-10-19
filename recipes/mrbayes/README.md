---
tool: mrbayes
tool_version: "3.2.8"
images:
  - quay.io/aarchbio/mrbayes@sha256:a320f21c5742641ec3db1bd6b443df81bb513589acfe1f8974640fcd1c6556ab
  - quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/mrbayes@sha256:9f19aafb555767e28c53df9692d63e126cb27e8273f4b13b0f217fad852ebd1c
  - quay.io/biocontainers/iqtree@sha256:3a7fc305372d818447197cfcb2f870797036170d7ecc7ea656a94c0de250b78d
spawn_version: 0.127.0
last_verified: 2026-10-11
---
# MrBayes — a Bayesian tree on the same alignment the ML recipes use

> **This chain is not converged.** ASDSF is **0.038** after 100,000 generations against the
> conventional 0.01 target. The topology checks below are exact and the cross-method
> comparison is real, but a publication run needs roughly 15× more generations — see below.

Samples a posterior over trees for a 114-taxon Pfam alignment under LG+G4, then compares the consensus with the RAxML-NG and IQ-TREE trees from the same bytes. For anyone doing Bayesian phylogenetics on ARM.

## Run it

```bash
make stage RECIPE=iqtree         # the alignment lives here; this recipe does NOT copy it
make stage RECIPE=mrbayes        # just the two awk converters
for s in $(make -s spec RECIPE=mrbayes); do spawn task run --spec "$s" --wait; done

# task 1, inside MrBayes:
lset rates=gamma ngammacat=4;  prset aamodelpr=fixed(lg);   # == the ML recipes' LG+G4
mcmcp ngen=100000 samplefreq=100 nchains=4 nruns=2 stoprule=yes stopval=0.01;  mcmc;
sumt burnin=250 conformat=simple;  sump burnin=250;
```

Two tasks: MrBayes samples and converts its consensus to Newick, then IQ-TREE computes Robinson-Foulds distances against both ML trees.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ngen=100000` | 1.5M+ | ASDSF fell 0.1515 → 0.0382 from 2k → 100k generations, so convergence to 0.01 needs **~1.5M** — about **25 h** on this box at the measured 16.4 gen/s. |
| `fixed(lg)` | WAG, or `mixed` | **LG is what makes the comparison honest** — it is the model [raxml-ng](../raxml-ng/README.md) and [iqtree](../iqtree/README.md) ran. A different matrix would make a method difference look like disagreement. `prset` needs the data loaded *first* or every model is rejected. |
| the shared alignment | your own | staging **fails** if iqtree has not staged it rather than making a copy; a cross-check only means anything on identical bytes. |
| `nchains=4` | 2 | measured 16.4 gen/s at 4 chains versus 33.3 at 2 — cost is roughly linear in chains, so halving them buys twice the generations at the same price. |
| `seed=`/`swapseed=` fixed | unset | a fixed seed reproduces **exactly** here; without it nothing exact can be asserted ([same rule as the assemblers](../../practices/cross-checks.md)). |

**Leave the alignment.** 114 taxa × 477 sites is a real tree, and it is the one both ML recipes used — which is the whole point. **Scale it** by raising `ngen` first; the chain length, not the data, is what is short here.

## Shape, size, cost

Task 1 on `c8g.large` (2 vCPU / 4 GiB): the MCMC is **5,787 s** of a 1h39m window, so unlike most recipes here the compute *is* the cost. TTL 3h is a **backstop** with `cost_limit` $0.25 as the real guard; actual ≈ **$0.13**. Task 2 is 48 s and about a cent. Single-threaded — 115.88 s CPU for 116 s wall in the probe, so extra cores buy nothing ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: exact sampling arithmetic, a sum-to-one over topologies, three methods on one alignment with a calibrating control, and a convergence figure deliberately not asserted</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | 2 of 2 awk scripts |
| MrBayes | read from the running install | **3.2.8** |
| NEXUS taxa / sites | **== 114 / 477** | 114 / 477 |
| **FASTA and NEXUS label sets** | **byte-identical** | identical |
| **model actually used** | **`Fixed(LG)` in the log** | **LG+G4, 4 gamma categories** |
| completion sentinel | `Analysis completed in` present | present |
| **`.p` rows per run** | **== 1002** (header + ngen/samplefreq + 1) | **1002** ×2 |
| **sampled trees per run** | **== 1001** | **1001** ×2 |
| **cumulative topology probability** | **== `1.000`** | **1.000** |
| distinct topologies sampled | — | 1,302 |
| **Newick consensus labels** | **== the alignment's 114** | identical |
| **determinism** | **same seed reproduces** | **yes** |
| **leaf sets, all three trees** | **identical** | 114 / 114 / 114 |
| **RF: MrBayes vs IQ-TREE** | *reported* | **16** |
| **RF: MrBayes vs RAxML-NG** | *reported* | **16** |
| **RF: IQ-TREE vs RAxML-NG** | *reported* | **0** |
| RF: majrule vs all-compatible | *reported* | 0 |
| **control: vs an unrelated topology** | must be large | **446** / 452 |
| bipartitions with PP ≥ 0.95 | *reported* | 160 of 161 |
| ASDSF | *reported, **not** asserted* | **0.038156** |

### What is exact here, and why those things and not the tree

A posterior is not a point estimate, so there is no number to reproduce. What *is* exact is the
bookkeeping, and it is worth asserting because every one of these would pass silently if wrong:

```text
ngen 100000 / samplefreq 100 + 1   ->  1001 sampled trees per run      observed 1001
.p rows = header + samples         ->  1002 per run                    observed 1002
sum of topology posteriors         ->  exactly 1.000                   observed 1.000
```

The sum-to-one over the 1,302 sampled topologies is a conservation identity: a `.trprobs`
truncated anywhere, or a weight normalised against the wrong denominator, cannot reach exactly
1.000. And **determinism is real** — a fixed `seed`/`swapseed` reproduces the `.p` columns and the
`.t` trees exactly. That needs one caveat to use: MrBayes stamps a per-run `[ID: 8762360742]`
header, so a raw byte comparison says "differs" while the numbers are identical. The check drops
that line; believing the byte comparison would have meant wrongly reporting a correct sampler as
irreproducible.

**The model is asserted from the log, not assumed.** `Setting Aamodelpr to Fixed(LG)` must appear.
A silent fall back to the default (Poisson) would still produce a plausible tree, and the
comparison below would then be a method difference wearing the clothes of a disagreement — the
same failure the [cross-checks](../../practices/cross-checks.md) page exists to prevent. Getting
there needed one non-obvious thing: `prset aamodelpr=` is rejected for **every** model, WAG
included, unless the data is loaded first, because the datatype has to be protein. An earlier pass
read that as "LG is unavailable in 3.2.8" and was wrong.

### Three methods, one alignment, and a control that makes the number mean something

The alignment is not copied: this recipe reads the object [iqtree](../iqtree/README.md) staged,
which [raxml-ng](../raxml-ng/README.md) already reuses. Same bytes, same LG+G4 model, three
different inference procedures:

```text
IQ-TREE      vs RAxML-NG        RF =   0      two ML searches, identical topology
MrBayes      vs IQ-TREE         RF =  16
MrBayes      vs RAxML-NG        RF =  16
CONTROL: MrBayes vs caterpillar RF = 446      an unrelated topology, same 114 leaves
CONTROL: IQ-TREE vs caterpillar RF = 452
```

**The ML pair at RF = 0 is the calibration, and it is the strongest part of this.** Those two
tools already agree on log-likelihood to 1 part in 1e8; here they return the *same topology*, so
tree search contributes **nothing** to the spread. That means the 16 splits separating the Bayesian
consensus are attributable to the paradigm and the chain, not to search noise — a statement
impossible to make without the ML pair as a floor.

**And the control supplies the scale.** Against an unrelated topology on the same leaves the same
tool reports 446–452, so 16 is about **3.6%** of unrelated. Without that, 16 could have been
near-identical or near-random and the page could have spun it either way. The percentage is quoted
against the *measured* control rather than a theoretical maximum, because IQ-TREE's RF
normalisation is not something this recipe established.

RF is **reported, not asserted**, for the reason the mafft↔muscle comparison is also an
observation: a consensus from an unconverged chain is partly a statement about chain length.
160 of 161 bipartitions carry PP ≥ 0.95, which is a strong-looking posterior — on a chain whose
ASDSF says the two runs still disagree about split frequencies.

### The convergence figure, and what it would cost to fix

```text
2,000 generations      ASDSF 0.1515
100,000 generations    ASDSF 0.0382        <- as shipped
target                 ASDSF 0.01
```

The fall is close to 1/√n, which puts the 0.01 target near **1.5M generations ≈ 25 h** on this box.
That is why the caveat is at the top of the page instead of buried: the exact checks hold
regardless, but anyone reading the consensus as a settled posterior would be misreading it.
`stoprule=yes stopval=0.01` is set, so a longer run stops itself the moment it converges rather
than burning the remaining budget.

### Pins

| | |
|---|---|
| alignment | Pfam 38.2 `PF26127.1` seed, 114 × 477 — **staged by [iqtree](../iqtree/README.md)**, not re-staged here |
| checks | `fasta2nexus.awk`, `nexus2newick.awk`, both pinned by sha256 |
| images | mrbayes `@sha256:a320f21c5742641ec3…` (3.2.8), iqtree `@sha256:dc6d9f62d56fd1ca92…` (3.1.3) |

cosign-verified against `playgroundlogic/aarchbio`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

MrBayes needs NEXUS, so a conversion is unavoidable — and staging verifies it label-for-label and
residue-for-residue, then asserts the headers need no quoting. That last check is load-bearing: a
sanitised label would turn the topology comparison into a label-matching exercise without
anything failing. These Pfam headers are `[0-9A-Z_]` only, so they pass through untouched.

### On x86_64

The same two tasks on same-generation Intel (`c8i.large`), from bioconda's own x86 builds of the
same versions — `quay.io/biocontainers/mrbayes@sha256:9f19aafb…` (3.2.8) and
`quay.io/biocontainers/iqtree@sha256:3a7fc305…` (3.1.3), both `linux/amd64`; not cosign-signed, so
the digest is the pin. Task 2 reads the x86 IQ-TREE and RAxML-NG trees:

| | arm64 | x86_64 |
|---|---|---|
| sampled trees per run / cumulative P | 1001 / 1.000 | 1001 / 1.000 |
| distinct topologies / ASDSF | 1,302 / 0.038156 | 1,302 / 0.038156 |
| RF vs IQ-TREE / RAxML-NG / caterpillar | 16 / 16 / 446 | 16 / 16 / 446 |
| `mb.consensus.nwk`, `compare.log` | — | **byte-identical** to arm64 |

**The same chain, not merely the same summaries.** The two builds use different likelihood
calculators (`non-SSE` on arm64, `SSE` on x86, both single-precision), and the starting
log-likelihoods differ in the fifth decimal (−92947.187471 vs −92947.187482) — yet every chain
log-likelihood the log prints is identical, and `mb.tstat`, `mb.trprobs`, `mb.pstat` and `mb.con.tre` differ
only in their per-run `[ID: …]` header: a difference that small evidently never flipped a Metropolis
accept/reject under the same seed. `mb.out` otherwise differs only in the build banner, that
calculator line and timings.

### Run + verify

```sh
make stage RECIPE=iqtree && make stage RECIPE=mrbayes
for s in $(make -s spec RECIPE=mrbayes); do spawn task run --spec "$s" --wait; done
make run RECIPE=mrbayes ARCH=x86_64      # same specs, x86 pins, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/mrbayes/r1/score.tsv" -
aws s3 cp "s3://$(make -s print-bucket)/runs/mrbayes/r1/compare.log" -
```

Task 1 fails on a pin mismatch, a NEXUS conversion that loses a label or a residue, a run that did
not use LG, a missing completion sentinel, a sample count that is not exactly 1001 per run, a
cumulative topology probability that is not 1.000, a Newick consensus whose labels are not the
alignment's, or a fixed seed that fails to reproduce. Task 2 fails if the three trees do not share
a leaf set. Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md));
`mb.out` is staged so a failure is diagnosable without the 1.6 h rerun.

**Always resolve the spec through `make spec`** (or `make run`), never by handing a path from
`recipes/` straight to spawn. The checked-in spec still holds `${COOKBOOK_BUCKET}` and no task-id
nonce, so an unresolved one fails in a second with `staging_error` — and without the nonce a retry
can return the *previous* completion record and read as a pass.

### Not covered

**Convergence**, as stated at the top. **Marginal likelihoods and model comparison** — no stepping-stone
or Bayes factor run, which is much of why people choose MrBayes; the harmonic-mean estimator in
`.lstat` is known unreliable and is not asserted.

**Partitioned models, relaxed clocks and calibrated dating** — all absent, and the clock models are
the other main reason to reach for this tool. **`aamodelpr=mixed`**, which samples over the first
ten matrices (LG is index 10 and so excluded — worth knowing before reaching for it).
**MPI**: this image is the serial build, so nothing here speaks to `mb-mpi` scaling across chains.
**Nucleotide data** entirely. **Prior sensitivity** — one prior set is run, so nothing shows how
much the posterior owes to it.

</details>
