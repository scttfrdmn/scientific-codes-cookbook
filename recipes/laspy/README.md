---
tool: laspy
tool_version: "2.7.0"
env: pointcloud
image: quay.io/aarchsci/pointcloud@sha256:7817e0291f05f6bc209aa5999363503897647daf4eb9d6df153b1965629d6ffc
gap_x86_64: the aarch.science `pointcloud` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-13) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-12
---
# laspy — a second LAZ reader against PDAL's numbers, and the format's exact law

Decodes the same 10.6M-point cloud PDAL already decoded on Graviton4 and checks the two agree, then holds LAS's integer quantisation to the spec. For anyone reading LiDAR on ARM.

## Run it

```bash
make stage RECIPE=pointcloud      # the cloud lives here; this recipe does NOT copy it
make stage RECIPE=laspy           # just the checks
spawn task run --spec "$(make -s spec RECIPE=laspy)" --wait

las = laspy.read("autzen.laz")                      # needs lazrs; see below
las.x == las.X * las.header.scales[0] + las.header.offsets[0]    # exact, by spec
np.asarray(las.z).mean()                            # 434.1025 — PDAL's published value
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the shared `autzen.laz` | your own cloud | staging **fails** if [pointcloud](../pointcloud/README.md) has not staged it rather than fetching a copy — the cross-check only means anything on identical bytes, and the sha256 is asserted in-run. |
| comparing the mean at **5e-05** | a tighter bound | PDAL published `434.1025` — four decimals. laspy lands `2.159e-07` away, so a tighter bound would pass, but it would claim agreement PDAL never stated. The residual is the published value's rounding. |
| the off-grid control | the on-grid result alone | **keep it.** On-grid coordinates round-trip exactly; that proves nothing unless off-grid ones demonstrably quantise — measured `4.998678e-03` against the `5.0e-03` the scale permits. |
| `lazrs` | `laszip` | only **lazrs** is importable here (it ships as `lazrs-python`, which is why a naive `lazrs` grep of the lock finds nothing). Without a backend laspy cannot open a `.laz` at all. |
| `scales = 0.01` | finer | the scale *is* the precision: every coordinate is an int32 multiple of it. Writing data finer than your scale silently quantises it. |

**Leave the cloud.** 10,653,336 points of Oregon LiDAR, pinned by sha256 and already decoded by a different tool — that pairing is the point. **Scale it** to your own survey; the format law holds at any size and the header checks are O(1).

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 30m as a **backstop** with `cost_limit` $0.15 as the real guard. Decoding 10.6M points and the write/read tests take **12 s** of a 1m30s window; 33 s is pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: two independent readers on identical bytes, an exact format law, and a control with a predictable magnitude</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged cloud | **sha256 == the pointcloud pin** | `944b9475…` (56,350,988 B) |
| laspy / lazrs | read from the running install | **2.7.0** / present (`laszip` absent) |
| point format / version | — | 3 / 1.2 |
| **header count == decoded length** | **exact** | **10,653,336** both |
| **header extents == data extents** | **exact, all three axes** | exact |
| **count vs PDAL** | **== 10,653,336** | match |
| **bounds vs PDAL** | within half a scale step | all three match |
| **mean Z vs PDAL's 434.1025** | **< 5e-05** (its published precision) | **2.159e-07** |
| **`x == X_raw·scale + offset`** | **`== 0` exactly, all axes** | **0.000e+00** ×3 |
| **on-grid write/read** | **`== 0` exactly** | **0.000000e+00** |
| **control: off-grid write/read** | **`0 < diff ≤ scale/2`** | **4.998678e-03** of 5.0e-03 |
| LAZ vs LAS | identical integers, smaller file | 4.86×, identical |
| determinism | two reads agree | exact |

### Two readers, one file

[pointcloud](../pointcloud/README.md) decoded these exact bytes with PDAL and published the
numbers. laspy shares no code with PDAL, so this is a statement about the decoding rather than
either tool's self-consistency:

```text
point count   10,653,336   ==   10,653,336
x bounds      635577.79 .. 639003.73   ==   PDAL
y bounds      848882.15 .. 853537.66   ==   PDAL
z bounds         406.14 ..    615.26   ==   PDAL
mean Z        434.102500216   vs   434.1025      abs diff 2.159e-07
```

**The mean is the one that carries weight.** As that recipe argues, header integrity is
exact-or-wrong but it is all *metadata* — a header can parse perfectly while the points decode
wrongly. A mean over all 10,653,336 Z values cannot be satisfied by metadata alone.

**And it is compared at PDAL's precision, not laspy's.** PDAL published four decimals, so the
bound is 5e-05 — half the last published digit. The actual agreement is `2.159e-07`, which means
a far tighter assertion would have passed; writing one would have claimed a precision the
reference never offered. The residual *is* the rounding of `434.1025`. Same discipline as taking
the [SLEPc](../slepc/README.md) tolerance from scipy's own error.

### The format is a law, so the control has a known size

LAS stores coordinates as int32 with a per-axis scale and offset. That makes
`x = X_raw·scale + offset` an **exact** relation, not an approximation — and it holds to
`0.000e+00` on all three axes of a real 10.6M-point cloud.

Which gives a control whose magnitude is predictable rather than a tolerance someone chose:

```text
coordinates written ON the grid   (int × 0.01 + 1000)    max |diff| 0.000000e+00
coordinates written OFF the grid  (+ U(-0.005, 0.005))   max |diff| 4.998678e-03
the scale permits at most                                           5.000000e-03
```

On-grid exactness alone would be a weak result — it could hold for a reader that ignored the
scale entirely. The off-grid case must lose something, and must lose **at most half a step**; at
`4.9987e-03` of a `5.0e-03` bound it sits just inside, which is the format behaving exactly as
specified. Staging demonstrates both halves in plain numpy first, so the law is verified without
laspy before laspy is judged against it.

LAZ compression is separately checked as lossless: the same points written as `.las` and `.laz`
decode to **identical integers**, at 4.86× smaller.

### The backend that is easy to miss

laspy cannot open a `.laz` without a decompression backend, and this env has exactly one:

```text
lazrs    present      (the conda package is named lazrs-python)
laszip   NOT importable
```

A grep for `lazrs` in the env lock finds nothing, because the package is `lazrs-python` — which
nearly led me to build a weaker recipe on synthetic files only, losing the cross-check. The run
asserts a backend is importable and names which, so a future env without one fails with the
reason rather than an opaque read error.

### Pins

| | |
|---|---|
| cloud | `autzen.laz` — **staged by [pointcloud](../pointcloud/README.md)**, not re-staged here. From `PDAL/data` at commit `360327d2`, sha256 `944b9475…`, 56,350,988 B, 10,653,336 points |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/pointcloud@sha256:7817e0291f05f6bc20…` — laspy 2.7.0, lazrs-python 0.8.2, pdal 2.10.2, python-pdal 3.5.5, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=pointcloud && make stage RECIPE=laspy
spawn task run --spec "$(make -s spec RECIPE=laspy)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/laspy/r1/score.tsv" -
```

Fails on a pin mismatch, a missing LAZ backend, a header whose count or extents disagree with the
array it describes, a count or bounds differing from PDAL's, a mean Z beyond PDAL's published
precision, a scaled-integer relation that is not exact, an on-grid round-trip that loses anything,
an off-grid round-trip that loses **nothing** (which would make the on-grid result vacuous) or
more than half a scale step, a `.laz` that decodes to different integers than the `.las`, or a
non-deterministic read — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Everything but geometry.** Classification, return number, intensity, GPS time, RGB and the
extra-bytes VLRs all ride in point format 3 and none is asserted — a cloud can have correct
coordinates and scrambled attributes.

**CRS handling.** The WKT/GeoTIFF VLRs are not read, so nothing here checks that laspy and PDAL
agree on the *projection* — only on the numbers. **Chunked and streamed reading**
(`laspy.open`, `chunk_iterator`), which is how a cloud larger than memory is actually processed;
this reads all 10.6M points at once into 16 GiB. **Writing at scale** — the write tests use 1,000
synthetic points. **LAS 1.4 and point formats 6–10**, including the 64-bit point counts that
matter for modern surveys. **Spatial indexing** (`.lax`) and the COPC format, both of which
`pointcloud` also leaves alone.

</details>
