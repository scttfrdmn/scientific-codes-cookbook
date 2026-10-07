---
tool: humann
tool_version: "3.9"
image: quay.io/aarchbio/humann@sha256:c614308013fd2a7f7cef181d18ad0ce3c785b1b3243f62f0428fa313c1852ac6
spawn_version: 0.123.0
last_verified: 2026-10-07
---
# HUMAnN — functional profiling on Graviton, verified by its own 186 tests

Runs HUMAnN's bundled demo end to end on Graviton4 — gene families, pathway abundance, pathway coverage — and checks the build with the 186 assertions HUMAnN's authors ship with it. For anyone doing metagenomic functional profiling on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=humann)" --wait   # nothing to stage
make ls RECIPE=humann

humann --input demo.fastq --output out --threads 4 --bypass-prescreen \
  --nucleotide-database .../chocophlan_DEMO --protein-database .../uniref_DEMO
```

**Nothing is staged.** The demo reads, both DEMO databases and the test suite all ship inside the image.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `demo.fastq` (21,000 reads) | your metagenome | the DEMO databases are a tiny subset; real work needs the full ChocoPhlAn + UniRef (~30 GB via `humann_databases`). |
| `--bypass-prescreen` | **you have no choice yet** | HUMAnN cannot start otherwise — [biobakery/humann#80](https://github.com/biobakery/humann/issues/80). It also means `--taxonomic-profile` is **ignored**. See below; this is the recipe's real limitation. |
| the DEMO databases | `humann_databases --download ...` | a full-database run changes the answer substantially, not marginally. |
| 4 threads | more | `--threads` is passed to bowtie2 and diamond, which is where the time goes. |

**Leave the fixture.** 21,000 reads against the DEMO databases is the configuration HUMAnN's own tests use, and it is what makes the run finish in 46 s while still exercising nucleotide search, translated search and pathway quantification. **Scale it** by downloading the full databases — but read the prescreen caveat first, because it changes what the run means.

## Shape, size, cost

One task on `c8g.large` (4 vCPU / 8 GiB), TTL 40m, cap $0.15. Measured: **186 unit tests in 30 s**, the demo in **46 s**, then a second identical demo run for the determinism check — all inside a ~4 min window that is mostly image pull. **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: 186 author-written tests, and the one number this recipe refuses to assert</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| aligner architecture | the binaries run natively | `diamond 2.2.8`, `bowtie2 2.5.5`, `uname aarch64` |
| **HUMAnN's own unit tests** | **all pass, ≥180 of them** | **186 in 30 s, `OK`** |
| functional tool tests | pass | `rc=0` |
| demo run | completes | `rc=0`, 46 s, 21,000 reads |
| gene families | ≥2 rows, header is an abundance table | **1,486 rows** |
| pathway abundance | ≥2 rows | 15 rows |
| pathway coverage | ≥2 rows | 15 rows |
| **`UNMAPPED` row** | **present — every read is assigned or counted unmapped** | **17,406.0** |
| **determinism** | **two runs byte-identical** | **identical** |

**The correctness claim comes from the authors' suite, not from a number we invented.** 186 unit
tests plus the tool-level functional tests are HUMAnN's own assertions about nucleotide search,
translated search, pathway quantification and its table utilities — the
[reference-from-tests](../../practices/reference-from-tests.md) move, and much stronger than any
band this recipe could put on an abundance value.

`UNMAPPED` earns its place for the same reason as a conservation identity: every read is either
assigned to a gene family or counted as unmapped, so the row's presence is a structural property
of a *complete* run. A search killed part-way would still leave a plausible-looking table.

### The number this recipe does not assert, and why

HUMAnN ships `tests/data/demo_genefamilies.tsv` — a committed expected output, 2,926 gene
families. It looks like a free published reference. **It is not reproducible here, and the recipe
says so rather than quietly dropping it.** Measured against this run:

| | gene families | shared with committed | only in committed | only here |
|---|---|---|---|---|
| committed `demo_genefamilies.tsv` | 2,926 | — | — | — |
| this run | **1,486** | **1** (`UNMAPPED`) | 2,925 | 1,485 |

Almost entirely disjoint. The committed file carries `|g__Bacteroides.s__Bacteroides_stercoris`
style stratification, which points to it having been produced with the **full** ChocoPhlAn and
UniRef databases rather than the DEMO subsets in the image. So it is a reference for a different
configuration, and asserting against it would be borrowing a number from a run nobody here made.

### `--bypass-prescreen` is required, not chosen — and it is the real limitation

HUMAnN probes `metaphlan --version` at startup and parses the **last** line. MetaPhlAn always
appends a database status line after the version, so the last line is never a version:

```text
no database:    MetaPhlAn version 4.2.5 (13 Jul 2026)
                No complete MetaPhlAn Bowtie2 database found      <- parsed
with database:  MetaPhlAn version 4.2.5 (13 Jul 2026)
                Installed databases: mpa_vJan21_TOY_...           <- parsed
→ CRITICAL ERROR: Can not call software version for metaphlan
```

**Installing a database does not fix this**, which is worth stating because it is the obvious
thing to try. Measured: a MetaPhlAn database staged and verified present (all six `.bt2l` files
plus the `.pkl`) with `METAPHLAN_DB_DIR` set still produced the same `CRITICAL ERROR` — the
warning line is simply replaced by an "Installed databases:" line. Filed as
[biobakery/humann#80](https://github.com/biobakery/humann/issues/80) with the suggested fix
(match the version by pattern, or read the first line).

So `--bypass-prescreen` is the only way in, and the cost is specific: **`--taxonomic-profile` is
ignored.** Running with and without the profile produced *identical* gene-family tables, so
HUMAnN searches the whole nucleotide database rather than the pangenomes a profile would select.
That is why this recipe profiles a demo rather than claiming a taxonomy-guided functional profile
— the path that consumes a profile is gated behind the broken check.

### Pins

| | |
|---|---|
| image | `@sha256:c6143080…` (humann 3.9, noarch package, arm64 entry of the manifest list) |
| contents | diamond 2.2.8, bowtie2 2.5.5, MetaPhlAn 4.2.5, `chocophlan_DEMO` + `uniref_DEMO`, `demo.fastq`, the test suite |

cosign-verified against `playgroundlogic/aarchbio`. Note the signature covers the **manifest-list**
digest, so verifying the per-architecture digest directly returns `no signatures found` — verify
the tag, pin the arm64 digest.

**This image previously shipped x86_64 `diamond` and `bowtie2` inside an arm64 image**, so HUMAnN
exited before doing any work. The cause was not wrong-subdir resolution: the **noarch `humann`
package carries its own copies of those binaries** and overwrote the correct `linux-aarch64` ones
that `bowtie2` and `diamond` had installed. Three packages claimed the same paths and noarch won.
Fixed and republished under the same tag ([aarchbio#66](https://github.com/playgroundlogic/aarchbio/issues/66));
the version lines in the checks table are the evidence, since an x86 binary could not print them
here.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=humann)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/humann/r1/humann-diag.txt" -
```

The checks run inside the task and fail it on a failed unit test, a missing output, a missing
`UNMAPPED` row or a non-deterministic second run — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

A full-database run (~30 GB of ChocoPhlAn + UniRef), taxonomy-guided profiling (blocked, above),
`humann_barplot` and the regroup/rename table utilities beyond what the functional tests exercise,
and MetaPhlAn profiling itself — that is [metaphlan](../metaphlan/README.md).

</details>
