---
tool: zarr
tool_version: "3.4.1"
env: pathology
image: quay.io/aarchsci/pathology@sha256:9c7f7713f34ef818acc7aa07ee9084e8df293666277497bccb297d966797fc5a
env_x86_64: pathology-c817c8dd8540
spawn_version: 0.127.0
last_verified: 2026-10-12
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# zarr + dask — the chunk shape is a storage decision, and the two places it isn't

Writes one array at four chunk shapes on Graviton4 and checks all four read back byte-identical, then measures the two things chunking *does* change: a float sum's last bits, and `argmax` under ties. For anyone doing chunked, lazy array I/O on ARM.

## Run it

```bash
make stage RECIPE=zarr-dask     # once: the checks only — the arrays are synthesised
spawn task run --spec "$(make -s spec RECIPE=zarr-dask)" --wait
make ls RECIPE=zarr-dask
z = zarr.create_array(store="s.zarr", shape=a.shape, chunks=(17, 13), dtype=a.dtype)
z[:] = a ; np.array_equal(z[...], a)                  # True at every chunk shape
da.from_array(a, chunks=(64, 64)).sum().compute()     # NOT numpy's sum — 1 ULP out
zarr.create_array(..., chunks=(64, 64), shards=(256, 256))   # 65 objects -> 5
```

## Which box — [measured](../../measurements/zarr-dask-real/README.md): AMD, then Graviton4 at 10% more (165 years of daily CMIP6 `tas`, 12.49 GB, seven rewrites bit-identical, every float result identical across chips)

| chip | instance | tool wall | $/hr | **billed $/result** |
|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 263 s | 0.9426 | 0.104 |
| Xeon 6975P-C | `r8i.4xlarge` | 319 s | 1.1114 | 0.131 |
| **EPYC 9R45** | `r8a.4xlarge` | **182 s** | 1.2781 | **0.094** |

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| chunk shapes including `(17, 13)` | your own | a non-divisor leaves **1,240 ragged chunks** with partial edges on both axes — the path a divisor-shaped chunking never exercises. Identical data, **7.9% more on disk**. |
| `math.fsum` as the float reference | comparing dask to numpy | **comparing to numpy is the trap.** `fsum` is correctly rounded, so it is *the answer*, not a third opinion — staging proves that against exact rational arithmetic. Against numpy you learn only that they differ, not who is right. |
| the derived `log2(n)·ε` bound | a round number | that bound *is* summation theory: pairwise/blocked error is `O(log₂n·ε)`, a left-to-right pass `O(n·ε)`. A `1e-15` bound I picked first **rejected numpy itself**; the derived one passes both libraries and misses the naive sum by **6,559×**. |
| the unique-maximum `argmax` check | asserting `argmax` directly | **don't.** With 275 tied maxima dask disagrees with numpy at **3 of 4** chunkings — both are right, numpy returns the first occurrence and dask combines per-block winners. |
| three lossy filters as controls | the identity checks alone | **keep them.** `BitRound`, `Quantize` and `FixedScaleOffset` break identity on the same write path, which is what makes four chunkings × three codecs mean anything. |
| the synthetic 512×512 image | a real slide tile | nothing is fetched. [pathology](../pathology/README.md) stages CAMELYON16; [imagecodecs](../imagecodecs/README.md) covers the codec layer this sits on top of. |

**Leave the fixture.** Byte identity needs no reference value, and the one number that does — the true float sum — is computed exactly, so the arrays are synthesised from a pinned seed. **Scale it** to a real pyramidal store: every identity here is per-chunk and holds at any size, and the sharding result gets *more* useful as the object count grows.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All of it — 20 stores, 5 chunkings × 2 reduction cases, a three-level pyramid — is **13 s** of a 1m17s window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: five storage invariances asserted exactly, a derived bound that rejects a naive sum by 6,559×, and two places where chunking legitimately changes the answer</summary>

### The checks

| observable | assertion | observed | x86_64 (m8i.large) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | identical |
| zarr / dask / numpy / numcodecs | read from the running install | **3.4.1** / 2026.8.0 / 2.5.4 / 0.17.0 | identical |
| test image | ≥ 1000 levels, entropy in (4, 15) | 25,977 levels, **14.283 bits/px** | identical |
| **4 chunk shapes** | **byte-identical, pairwise** | 1 / 16 / 64 / **1,240** chunks, all identical | identical |
| `(17, 13)` ragged | must leave partial edges both axes | 31 × 40 grid | identical |
| **zarr format 2 vs 3** | **both exact, and equal to each other** | identical | identical |
| **blosc / gzip / zstd** | **byte-identical** | all, 1.05× | identical |
| control: `BitRound(6)` / `Quantize(2)` / `FixedScaleOffset(10)` | **must break identity** | 2.8e-02 / 3.9e-03 / 5.0e-02 | identical |
| **sharded store** | **identical, and fewer objects** | **65 → 5 files** | identical |
| **region write across chunks** | **exact, fill value elsewhere** | exact | identical |
| **5 slices on a ragged store** | **== numpy**, incl. strided + corner | 5 of 5 | identical |
| **elementwise × 4 chunkings** | **array-identical to numpy** | `x*2+1`, transpose, `where` | identical |
| **integer `sum` / `max`** | **bit-exact at every chunking** | 130,859,506 | identical |
| **float `min` / `max`** | **bit-exact at every chunking** | exact | identical |
| `fsum` reference | == exact rational arithmetic (staging) | identical, both arrays | identical |
| **every sum's backward error** | **< `log2(262144)·ε` = 3.997e-15** | 5.440e-19 / **1.600e-15** | identical |
| **control: naive left-to-right** | **must miss that bound** | 2.621e-11 — **6,559× outside** | identical |
| **1 chunk == numpy** | **exact, both arrays** | exact | identical |
| **control: >1 chunk must differ** | **at least one layout** | **4 of 4** / 2 of 4 | identical |
| argmax with 275 ties | *recorded, not asserted* | differs at 3 of 4 chunkings | identical |
| **argmax, unique maximum** | **== numpy at every chunking** | flat index 63,432 | identical |
| **rechunk (64,64)→(17,13)** | **every element preserved** | exact | identical |
| **`to_zarr` → `from_zarr`** | **exact** | exact | identical |
| **pyramid, dask coarsen** | **== numpy blockwise sum** | 2 levels, exact | identical |
| **pyramid total intensity** | **equal across all levels** | 7,393,804,574 ×3 | identical |
| determinism | repeat store size, repeat sum | identical | identical |

### The chunk shape must not be a scientific decision

Chunking is how an array is laid out on object storage. It changes cost, latency and request
count, and it must not change the data — so the check is `array_equal`, with no tolerance:

```text
chunks        chunks written   bytes on disk   data
(512, 512)         1             501,926       identical
(128, 128)        16             501,925       identical
( 64,  64)        64             499,465       identical
( 17,  13)     1,240             538,993       identical     <- 7.9% more disk, same bytes
```

`(17, 13)` is in there because it divides neither axis of 512, leaving partial chunks along
both edges — the path a tidy power-of-two chunking never touches. Five slices are then read
back from that ragged store, including a strided `[3:500:7, 5:510:11]` and the partial
corner, and all five match numpy exactly. Format 2 and format 3 stores of the same array
agree with each other, and so do blosc, gzip and zstd.

Three **lossy** filters on the same write path are the control — `BitRound(keepbits=6)`,
`Quantize(digits=2)` and `FixedScaleOffset(scale=10)` each break identity by a graded
amount (2.8e-02, 3.9e-03, 5.0e-02). Without them, twelve passing identity checks could
all be a comparison that is not really comparing.

**Sharding is the one that pays off in practice:** the same `(64, 64)` chunks written with
`shards=(256, 256)` produce **5 files instead of 65**, bit-identical. On object storage that
is the difference between 65 GETs and 5.

### What chunking does change, and the reference that settles it

A blocked reduction adds the numbers in a different **order**, and floating-point addition
is not associative. Integer addition is, which is why the integer `sum` and `max` are
bit-exact at every chunking while the float sum is not:

```text
                           sampled array (kappa 240)     planted [1e16, 1.0 x 262143]
math.fsum (the answer)     870.54936271289364            10000000000262144
numpy                      870.54936271289364  0 ULP     10000000000262128   8 ULP
dask, 1 chunk              870.54936271289364  0 ULP     10000000000262128   8 ULP  == numpy
dask, (128, 128)           870.54936271289375  1 ULP     10000000000262128   8 ULP
dask, ( 37,  51)           870.54936271289375  1 ULP     10000000000262132   6 ULP
dask, (  8,   8)           870.54936271289353  1 ULP     10000000000262136   4 ULP
```

**The attribution is what makes this a measurement rather than an anecdote.** A single chunk
reproduces numpy *exactly* on both arrays, and multi-chunk layouts differ — so the
difference is the blocking and nothing else. `math.fsum` is correctly rounded (Shewchuk's
algorithm), so it is not a third opinion but the true value, and staging proves that against
exact rational arithmetic, which uses no floating point at all.

**The tolerance is derived, not chosen.** Summation's backward error — error relative to the
absolute magnitude accumulated — is `O(log₂n·ε)` for any pairwise or blocked scheme and
`O(n·ε)` for a left-to-right pass. For n = 262,144 that is `3.997e-15` against `5.8e-11`.
The first bound I wrote was a round `1e-15`, and staging **rejected numpy itself** at
`1.600e-15` on the planted array; the derived bound admits both libraries and still refuses
the naive sum:

```text
naive left-to-right   10000000000000000      131,072 ULP      backward 2.621e-11
                                                              = 6,559x outside the bound
```

That left-to-right pass absorbs all 262,143 ones and returns the big value unchanged,
because `ulp(1e16)` is 2.0 and `1e16 + 1.0 == 1e16`. So **reordering is worth 131,072 ULP
here**, and the 1-to-8 ULP spread between numpy and dask is not evidence that either is
wrong — both are vastly better than the order-naive answer. On the planted array **2 of 4**
dask layouts are *closer to the truth than numpy*, because blocked summation **is** pairwise
summation. Which of two good schemes lands nearer is a property of the data, so it is
reported and not asserted. This is CLAUDE.md's fixed-point-versus-path distinction appearing
inside one library: a sum is a path.

### argmax, and asserting the claim you mean

The second thing chunking changes has nothing to do with rounding:

```text
the integer array's maximum 999 occurs 275 times
numpy returns the first occurrence                     flat index 200
dask differs at 3 of 4 chunkings                       it combines per-block winners
with a single planted maximum, every chunking agrees   flat index 63,432
```

Both libraries are right; the question is underdetermined. So an exact `argmax` assertion on
tied data is flaky by construction, and the recipe asserts the claim that is actually true —
**a unique extremum has no tie to break and must be chunk-invariant** — while recording the
tied behaviour so nobody writes the convenient proxy. Same discipline as asserting "nothing
beats itself" rather than "the best hit is itself" in [blast](../blast/README.md).

### The pyramid, which is what this is for

A whole-slide store is pyramidal, and a 2×2 block **sum** downsample in integer arithmetic
is exactly conservative — so the levels check each other:

```text
level 0   512x512      total 7,393,804,574
level 1   256x256      total 7,393,804,574      dask coarsen == numpy blockwise, exactly
level 2   128x128      total 7,393,804,574
```

Dask's `coarsen` is compared elementwise against numpy's `reshape(...).sum(axis=(1,3))` at
each level, not just on the total — a total alone is blind to a transposed or shuffled
result, which is the blind spot [yt](../yt/README.md) records.

### Pins

| | |
|---|---|
| data | **none.** Arrays synthesised from `default_rng(20261012)`; the only numeric reference is the correctly rounded sum, verified in staging against exact rational arithmetic |
| checks | `identities.py`, pinned by sha256. Staging **lifts `make_arrays()` out of it by AST** rather than restating the generator, so the data validated before the run and the data used in it cannot drift apart |
| image | `quay.io/aarchsci/pathology@sha256:9c7f7713f34ef818ac…` — zarr 3.4.1, dask 2026.8.0, numcodecs 0.17.0, imagecodecs 2026.8.16, tifffile, openslide 4.0.1, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

This gap was named by the catalog itself — [imagecodecs](../imagecodecs/README.md) records
that "`zarr` and `dask`, still unexercised in this env" were "the half of whole-slide I/O
this does not touch". Both are now exercised.

**x86_64.** The same spec with `ARCH=x86_64` runs on `m8i.large` through the `pathology-c817c8dd8540`
twin (`envs-x86_64/`: the pinned image's versions as linux-64 builds, installed offline in 7 s).
zarr, dask, numcodecs and numpy are the same versions; 5 of 208 versions differ (charls,
fonttools, libcurl, libglib, libsqlite). `score.tsv` is **byte-identical** across chips —
including the two values chunking is allowed to move: each chunked float sum's last bits
(backward error 1.600e-15) and the tied-`argmax` indices land on the same values on both chips,
so for these reductions the order of addition is set by the chunking, not by the CPU.

### Run + verify

```sh
make stage RECIPE=zarr-dask
spawn task run --spec "$(make -s spec RECIPE=zarr-dask)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/zarr-dask/r1/score.tsv" -
```

Fails on a pin mismatch, a chunk shape that changes the stored data, a `(17, 13)` grid that
is not ragged, a format or codec that does not round-trip, a lossy filter that *does*, a
sharded store that differs or that fails to reduce the object count, a region write or slice
that disagrees with numpy, any elementwise op that is not array-identical, an integer or
min/max reduction that moves with the chunking, a float sum outside the derived
`log2(n)·ε` bound, a naive sum that meets it (which would mean the bound discriminates
nothing), a single-chunk sum that already differs from numpy (which would make the
attribution unsound), a chunking sweep where *no* layout differs (which would mean the
reordering was never demonstrated), an `argmax` that disagrees on a unique maximum or
agrees at every chunking despite 275 ties, a pyramid level that is not elementwise equal to
numpy's or that fails to conserve the total, or a non-deterministic store — but check the
bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Remote stores.** Everything here is a local directory store. The `fsspec`/`s3fs` path —
which is how a zarr store is actually read in production, and where the chunk-shape decision
turns into a request-count bill — is untested, and that bill is the whole subject of
[the access-pattern work](../../measurements/lith-vs-copy/README.md).

**Parallel and distributed execution.** Every `.compute()` here runs on the default
threaded scheduler on 2 vCPU; nothing exercises `distributed`, a cluster, or concurrent
writers to one store, so zarr's write-locking semantics are untouched. **Dimension names,
coordinates and `xarray`**, which is how most zarr data is actually consumed —
[climate](../climate/README.md) and [cdo-nco](../cdo-nco/README.md) cover the netCDF side of
that. **Consolidated metadata** and the open/listing cost of a many-array group.
**Resizing** (`z.resize`, `da.concatenate` into a store). Appending, the common path for
time-series growth, is covered only by the [real workload](../../measurements/zarr-dask-real/README.md),
which grows a store one ESGF file at a time with `z.append`. **Real slide pyramids**: three levels of a synthetic 512×512, not the
multi-gigabyte pyramidal TIFFs [pathology](../pathology/README.md) stages, where level-0
chunking actually decides whether a viewer is usable.

</details>
