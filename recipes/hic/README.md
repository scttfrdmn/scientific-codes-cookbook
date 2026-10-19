---
tool: cooler
tool_version: "0.10.4"
images:
  - quay.io/aarchbio/pairtools@sha256:2b4ce4761b3e6acf9739ebbd598fbe5b4a9a42ab58fa35b23ae28733efaf913e
  - quay.io/aarchbio/cooler@sha256:5f665b9b2c94e5ca6812216309cb2e8d462d7d4498d4b25206ac0515e89a7dd0
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/cooler@sha256:95ef864cd6fc3a4bf8487d300de8d80f4abcd90e4d545823d8194d60547c2cf7
  - quay.io/biocontainers/pairtools@sha256:29e06e1791a809300c29abd8dea482eca573f2b9d0f8b8e2dddc0c749f561ba4
spawn_version: 0.123.0
last_verified: 2026-10-09
---
# pairtools + cooler — Hi-C contacts, conserved exactly through the pipeline

Deduplicates real Hi-C pairs and loads them into a `.cool` on Graviton4, checking that no contact is created or lost at any step. For anyone doing 3D genome analysis on ARM.

## Run it

```bash
make stage RECIPE=hic           # once: 9,998 real Hi-C contacts + chromsizes
for s in $(make -s spec RECIPE=hic); do spawn task run --spec "$s" --wait; done
make ls RECIPE=hic

pairtools dedup --mark-dups --output-stats dedup.stats -o dedup.pairs.gz aag2.sample1.pairs
cooler cload pairs -c1 2 -p1 3 -c2 4 -p2 5 aag2.chrom.sizes:1000000 dedup.pairs.gz hic.cool
cooler coarsen -k 2 -o hic.2M.cool hic.cool
```

Two tasks: pairtools produces the valid-pair count, then cooler must conserve it.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `-c1 2 -p1 3 -c2 4 -p2 5` | **read your file's `#columns:` line** | 4DN pairs v1.0.0 puts `readID` first. A sibling test file upstream is *headerless with a different order* — wrong indices build a matrix out of read IDs, silently. |
| `aag2.sample1.pairs` | your `.pairs` | the format is self-describing: it declares its columns, sortedness, shape and chromsizes. Trust the header, not a convention. |
| `:1000000` | your resolution | bin size goes in the chromsizes argument, not a flag. Start coarse: 9,998 contacts over 1.3 Gb is already sparse at 1 Mb. |
| `cooler coarsen` | `cooler zoomify` | zoomify builds a whole `.mcool` pyramid in one call; coarsen does one step, which is what makes the conservation check readable. |
| the pairs fixture | aligned Hi-C reads | real pipelines start from FASTQ → `bwa mem` → `pairtools parse`. This recipe starts at `.pairs` to keep the identities the subject. |

**Leave the fixture.** 9,998 contacts across **3,579 contigs** is small but adversarial in the way that matters here — a fragmented assembly means thousands of partial trailing bins, which is exactly where a coarsener loses counts. **Scale it** to a real library once it passes: the [measured run](../../measurements/hic-real/README.md) holds every identity on a whole 4DN experiment.

## Which box — [measured](../../measurements/hic-real/README.md) on a whole 4DN GM12878 experiment (151M contacts, 16 threads)

| chip | instance | pairtools + cooler | $/hr | **billed $/result** |
|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 1,065 + 523 s | 0.6381 | **0.397** |
| Xeon 6975P-C | `c8i.4xlarge` | 1,071 + 502 s | 0.7497 | 0.461 |
| EPYC 9R45 | `c8a.4xlarge` | 801 + 367 s | 0.8622 | 0.421 |

**Take Graviton4; AMD if time matters more** (1.36× faster, 6% dearer). All three rebuild 4DN's published `.mcool` pixel for pixel at all 13 resolutions. Size by `/tmp` (half the RAM), not cores: task 1 stages 9 GB. The fixture tasks above run on `m8g.large`/`xlarge` in seconds, so their windows are boot, and **timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: three exact conservation identities, and what each one would catch</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | both match |
| pairs format | declares v1.0.0, known column order, upper triangle | asserted at staging |
| chroms in pairs ⊆ chromsizes | **0 missing** — else `cload` drops pairs silently | 1,816 of 1,816 present |
| pairtools read everything | `total` == staged record count | 9,998 |
| **pairtools bookkeeping** | **`total == nodups + dups`** | **9998 == 9998 + 0** |
| **load conserves contacts** | **`.cool` total == valid pairs** | **9,998** |
| **coarsening conserves** | **identical total at 2M, 4M, 8M** | **9,998 / 9,998 / 9,998** |
| **matrix symmetry** | **`M == M.T` exactly, genome-wide** | **0.0 over 13,869 nonzeros** |

### The three identities, and what each catches

**1 — nothing is lost in the load.** Every valid pair becomes exactly one contact, so the sum of
the count column must equal what pairtools declared valid. A pair dropped for an unknown
chromosome or an off-end coordinate fails this.

The expected value is **produced by task 1 and carried forward in a file**, never written into
task 2. That matters: an identity satisfied by agreeing with a constant in the same script is not
an identity, it is a restatement.

**2 — coarsening is a regrouping, not a filter.** Merging bins moves contacts between pixels but
cannot create or destroy them, so the total must be *identical* at every resolution. The bin
counts show why this fixture is a real test:

```text
1 Mb   4447 bins   total 9998
2 Mb   3803 bins   total 9998
4 Mb   3598 bins   total 9998
8 Mb   3579 bins   total 9998     <- exactly the contig count: every contig is now one bin
```

With 3,579 contigs there are thousands of partial trailing bins, which is precisely where a
coarsener would silently lose the remainder.

**3 — the matrix is symmetric.** A contact between loci *i* and *j* is the same observation as
*j*→*i*. cooler stores only the upper triangle, so the materialised matrix must satisfy
`M == M.T` **exactly, in integers** — no tolerance anywhere.

This is checked **genome-wide over all 4,447 bins, covering 13,869 nonzero cells**, and the
covered count is reported rather than implied. Restricting it to the largest contig gives an 8×8
block holding almost none of the data, where symmetry holds trivially — so the recipe also fails
if fewer than 1,000 cells are nonzero, because a matrix of zeros is symmetric and proves nothing.

*(13,869 against `nnz` 7,659 is consistent: stored upper-triangle pixels materialise to roughly
twice that, minus the diagonal counted once.)*

### Honest limits on these checks

**The dedup identity is satisfied but not stressed.** This sample contains no duplicates, so
`total == nodups + dups` holds as `9998 = 9998 + 0`. The bookkeeping is verified; duplicate
*removal* is not exercised. A library with real PCR duplicates would test it properly.

**The pipeline starts at `.pairs`, not FASTQ.** Alignment and `pairtools parse` are upstream of
this recipe, so it does not verify that reads were converted to contacts correctly — only that
contacts survive dedup, loading and coarsening intact.

### Pins

| | |
|---|---|
| pairs | `aag2.sample1.pairs`, `a0df07e3…` — 9,998 real contacts, Aedes aegypti |
| chromsizes | `aag2.chrom.sizes`, `b582b74e…` — 3,579 contigs |
| images | pairtools `@sha256:2b4ce476…` (1.1.3), cooler `@sha256:5f665b9b…` (0.10.4) |

Both files come from **`open2c/cooler` at tag `v0.10.4`** — the same version as the cooler in the
image, and the same tag for both files, so the pin is one self-consistent set rather than two
sources to keep aligned. The read IDs still carry their SRA runs (`SRR5319284`, `SRR5319279`), so
this is real sequencing rather than simulated contacts.

**Why not 4DN or pairtools' own test data for the fixture:** 4DN is on RODA
(`s3://4dn-open-data-public`) and the [real workload](../../measurements/hic-real/README.md) uses it,
but its lists are gigabytes.
pairtools' bundled test data is 1.9 KB of mock reads — the identities would hold and prove
nothing at scale.

Both images cosign-verified; signatures cover the **manifest-list** digest, so verify the tag and
pin the arm64 digest.

### On x86_64

The same two tasks on same-generation Intel (`m8i`), from bioconda's own x86 builds of the same
versions — `quay.io/biocontainers/pairtools@sha256:29e06e17…` (1.1.3) and
`quay.io/biocontainers/cooler@sha256:95ef864c…` (0.10.4), both `linux/amd64`; not cosign-signed, so
the digests are the pins:

| | arm64 | x86_64 |
|---|---|---|
| valid pairs / `.cool` total / dups | 9,998 / 9,998 / 0 | 9,998 / 9,998 / 0 |
| totals at 2M / 4M / 8M | 9,998 each | 9,998 each |
| symmetry: max abs diff over nonzeros | 0.0 over 13,869 | 0.0 over 13,869 |
| `score.tsv`, `identities.log`, `dedup_score.tsv`, `denominator.tsv` | — | **byte-identical** to arm64 |

The deduplicated pairs are the same records (byte-identical once decompressed; only the `.gz`
wrapper differs). `dedup.stats` holds the same values in a different line order, except one that is
undefined here: with zero duplicates pairtools' `complexity_naive` divides by zero, and the x86
image (Python 3.10) reports `inf` where the ARM one (Python 3.12) prints `-1937304874880.6`. Nothing
asserts on it. The logs differ only in temp-file names and site-packages paths.

### Run + verify

```sh
make stage RECIPE=hic
for s in $(make -s spec RECIPE=hic); do spawn task run --spec "$s" --wait; done
make run RECIPE=hic ARCH=x86_64   # same specs, x86 pins, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/hic/r1/score.tsv" -
```

Fails on a pin mismatch, a pairs file whose declared format differs, any chromosome missing from
the chromsizes, a contact total that changes at any step, an asymmetric matrix, or a matrix too
sparse for the symmetry check to mean anything — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Alignment and `pairtools parse` from FASTQ, `pairtools select`/`restrict` filtering, matrix
balancing (`cooler balance` / ICE), and everything `cooltools` does downstream — expected
contact-vs-distance curves, insulation scores, eigenvectors and compartments. `cooltools 0.7.1`
is published in the same namespace and is the obvious next recipe.

</details>
