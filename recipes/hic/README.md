---
tool: cooler
tool_version: "0.10.4"
images:
  - quay.io/aarchbio/pairtools@sha256:2b4ce4761b3e6acf9739ebbd598fbe5b4a9a42ab58fa35b23ae28733efaf913e
  - quay.io/aarchbio/cooler@sha256:5f665b9b2c94e5ca6812216309cb2e8d462d7d4498d4b25206ac0515e89a7dd0
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

**Leave the fixture.** 9,998 contacts across **3,579 contigs** is small but adversarial in the way that matters here — a fragmented assembly means thousands of partial trailing bins, which is exactly where a coarsener loses counts. **Scale it** to a real library once it passes; the identities are size-independent.

## Shape, size, cost

Two tasks on `m8g.large`/`m8g.xlarge`, TTL 25m and 30m, caps $0.08 and $0.10. Both finish in seconds; the recorded windows are boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

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

**Why not 4DN or pairtools' own test data:** 4DN is not on the AWS Open Data Registry (probed:
404), and pairtools' bundled test data is 1.9 KB of mock reads — the identities would hold and
prove nothing at scale.

Both images cosign-verified; signatures cover the **manifest-list** digest, so verify the tag and
pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=hic
for s in $(make -s spec RECIPE=hic); do spawn task run --spec "$s" --wait; done
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
