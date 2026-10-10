---
tool: r-arrow
tool_version: "25.0.0"
env: r
image: quay.io/aarchsci/r@sha256:136bf063a0d04967e2e2dac8622259edf344d5aa74cdd5869502007cf6c5a56c
spawn_version: 0.126.1
last_verified: 2026-10-10
---
# r-arrow — 25,599 of 25,600 cells Apache committed, reproduced on Graviton

Reads Apache's own Parquet conformance files in R and compares every cell against the expected contents they ship. For anyone moving columnar data in R on ARM.

## Run it

```bash
make stage RECIPE=r-arrow     # once: 13 files from parquet-testing at arrow 25.0.0's commit
spawn task run --spec "$(make -s spec RECIPE=r-arrow)" --wait
make ls RECIPE=r-arrow

options(arrow.int64_downcast = FALSE)   # or int64 becomes a double and loses the wide columns
t <- arrow::read_parquet("delta_binary_packed.parquet")                  # 200 x 66
e <- read.csv("delta_binary_packed_expect.csv", colClasses = "character")  # parquet-mr's answer
arrow::write_parquet(t, "rt.parquet", compression = "zstd")              # 2.15x smaller
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `arrow.int64_downcast = FALSE` | the default | **the quietest trap here.** `6374628540732951412` exceeds 2^53, so the default downcast to double loses it — and the comparison still "passes" on the narrow columns. The run asserts an `integer64` actually arrived. |
| comparison **by position** | matching on names | 2 of the 4 reference files have schema names that differ from their CSV header (parquet-mr wrote trailing colons; one header has a stray leading space). Matching on names drops them silently. |
| the 4 delta-encoded files | any of parquet-testing's 100 | only these four ship a committed `*_expect.csv`. The rest need expectations from somewhere else. |
| `compression = "zstd"` | snappy / gzip / brotli / lz4 | all compiled in; the recipe round-trips four and checks the sizes actually differ. |
| the corrupt-CRC files | — | **they are not a negative control here.** r-arrow cannot verify a page CRC, so it reads them happily. See below. |

**Leave the fixture.** It is 515 KB and it is the point: the expected values were produced by **parquet-mr**, an independent Java implementation, so this is cross-implementation rather than Arrow agreeing with itself. **Scale it** by adding more of parquet-testing's files once you have a reference for them.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The inputs total 515 KB, so the box is ample. The analysis is **6 s** of a 1m29s window — 38 s installing Docker and 38 s pulling R, the catalog's largest image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a cross-implementation reference, one characterised exception, a constructed negative control, and an upstream gap that cost a check</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| reference files | all 13 present, sha256-pinned at staging | 13 |
| arrow / libarrow / bit64 | read from the running install | **25.0.0** / 25.0.0 / 4.8.6 |
| libarrow capabilities | parquet, snappy, gzip, zstd compiled in | all, plus brotli lz4 bz2 |
| `integer64` actually arrived | ≥1 column, or the comparison is a double comparison | yes |
| `delta_binary_packed` | 200 × 66 matches its CSV | 13,200 cells |
| `delta_byte_array` | 1000 × 9 | 9,000 cells |
| `delta_encoding_required_column` | 100 × 17 | 1,700 cells |
| `delta_encoding_optional_column` | 100 × 17 | 1,700 cells |
| **cells unexplained** | **== 0** | **0 of 25,600** |
| **cells excused as INT64_MIN** | **== 1 exactly** | **1** |
| **a perturbed cell is detected** | **== 1** | **1** |
| **4 codecs round-trip to the CSV** | **0 cells lost each** | 0 |
| codec sizes distinct, all compress | sizes differ and beat uncompressed | 2.15× best |
| good-CRC files | read to documented shapes | 5120, 5120, 1000 rows |
| `page_checksum_verification` exposed | *reported* | **FALSE** |
| corrupt-CRC files | *reported, **not** asserted* | both read without complaint |

### The reference is another implementation, which is the whole reason to use it

`parquet-testing` ships four files with a companion `*_expect.csv` giving **every cell**, and those
CSVs were written by **parquet-mr** — the Java implementation. So reading them in R and comparing
is a genuine cross-implementation check.

That distinction is load-bearing here. Every env in this catalog that carries `pyarrow` ships the
**same `libarrow 25.0.0`**, so an R-writes/Python-reads comparison would exercise the two *binding*
layers over one shared C++ core — the same shortcoming as comparing terra against rasterio when
both call GDAL. Reproducing parquet-mr's values avoids it entirely.

The reference is **version-matched**: `parquet-testing` at commit `e74785d85a4e`, which is the
submodule `apache/arrow` pins at `apache-arrow-25.0.0`. A reference from another commit is a
different expected value ([the practice](../../practices/reference-from-tests.md)).

```text
delta_binary_packed              200 x 66   DELTA_BINARY_PACKED, 65 different delta bitwidths
delta_byte_array                1000 x  9   DELTA_BYTE_ARRAY strings, with nulls
delta_encoding_required_column   100 x 17   required INT32 + STRING
delta_encoding_optional_column   100 x 17   optional INT64 + STRING
                               --------
                                 25,600 cells, 0 unexplained
```

### The one exception, characterised rather than tolerated

One cell differs: `bitwidth64` row 2, whose committed value is **−9223372036854775808** —
`INT64_MIN`. **bit64 reserves that exact value as its NA sentinel**, so Arrow decodes it correctly
and R cannot represent it. Nothing is wrong with either.

The assertion is written so that this stays precise: **every** mismatch must be a cell whose
expected value is `INT64_MIN`, **and** there must be exactly one. A tolerance would have swallowed
any other drift; this does not. Staging also confirms the reference still contains exactly one such
cell, because if upstream regenerated the file without it the assertion would become wrong rather
than quietly keep passing.

### The negative control is constructed, because upstream's does not work here

parquet-testing ships `datapage_v1-corrupt-checksum.parquet` and
`rle-dict-uncompressed-corrupt-checksum.parquet` **specifically** so a reader can be shown to
reject a bad page CRC. r-arrow reads both without complaint — 5120 and 1000 rows — because
`ParquetReaderProperties` exposes only:

```text
thrift_container_size_limit  set_thrift_container_size_limit
thrift_string_size_limit     set_thrift_string_size_limit
```

There is **no `page_checksum_verification`**, although Arrow C++ has had one since 13.0. So those
two files are **reported, not asserted** — claiming them as a passing check would be claiming a
guarantee that is not armed.

Something had to replace them, because "0 unexplained cells" is unfalsifiable on its own. So the
comparator is tested directly: one cell of an expected CSV is altered, and the comparison must
report **exactly one** mismatch. It does. That is what licenses reading the zero as a zero
([agreement needs disagreement to have been possible](../../practices/cross-checks.md)).

### The write path is checked against the same published reference

Round-tripping through each codec and re-comparing **to parquet-mr's CSV** — not to what was just
written — means a writer that corrupts something shows up as a disagreement with Apache, not merely
as self-consistency:

```text
uncompressed  79,869 B      snappy  51,691 B
gzip          37,187 B      zstd    37,229 B      best 2.15x
```

All four reproduce the committed values with nothing lost. The sizes are asserted to be **distinct**
and all smaller than uncompressed, which is what shows each codec actually ran rather than silently
falling through to no compression.

### Pins

| | |
|---|---|
| reference files | 13 from `apache/parquet-testing` at **`e74785d85a4e`**, each sha256-pinned in `stage-inputs.sh` |
| why that commit | it is the `cpp/submodules/parquet-testing` commit at `apache-arrow-25.0.0` |
| checks | `identities.R`, pinned by sha256 |
| image | `quay.io/aarchsci/r@sha256:136bf063a0d0…` — R 4.5.3, arrow 25.0.0, libarrow 25.0.0, bit64 4.8.6 |

Staging verifies the four expected-contents CSVs still have their documented shapes (200×66,
1000×9, 100×17, 100×17) and that exactly one `INT64_MIN` cell remains, before an instance is paid
for — a pin fixes bytes, not meaning.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=r-arrow
spawn task run --spec "$(make -s spec RECIPE=r-arrow)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/r-arrow/r1/score.tsv" -
```

Fails on a pin mismatch, a missing reference file, a libarrow without parquet/snappy/gzip/zstd, an
`integer64` column that never arrived, any cell differing from parquet-mr's committed value, more or
fewer than one `INT64_MIN` cell, a perturbed cell going undetected, a codec that loses data or
produces a suspiciously identical file size — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Page-CRC verification**, which r-arrow cannot do (filed upstream). **Arrow IPC/Feather, Flight and
Substrait**, all compiled into this build and unexercised. **Datasets and Acero** — multi-file
partitioned scans and the compute engine are what most Arrow users actually reach for, and this
recipe only does single-file read/write. **Encryption**, which parquet-testing covers extensively.
**The 96 other parquet-testing files**, including `float16`, `byte_stream_split`, bloom filters,
page indexes and the legacy list structures — they exercise real decoder paths but ship no
committed expected values, so asserting against them needs a reference from elsewhere.

Also: no performance or scaling claim. 515 KB on one box says nothing about throughput, and
`r-arrow`'s real purpose — larger-than-memory datasets — is untouched.

</details>
