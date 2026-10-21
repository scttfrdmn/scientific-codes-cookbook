---
tool: r-jsonlite
tool_version: "2.0.0"
env: r
image: quay.io/aarchsci/r@sha256:db2fcb3c8d707afe6cccb5796f78964dc79ff4c648d9eb75a5e1c09608a9a54f
env_x86_64: r-d98eb9641259
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# jsonlite — a lossless JSON round-trip needs `digits = I(17)` and `always_decimal = TRUE`

Round-trips doubles through JSON on Graviton4 and checks the result with `identical()`, cross-read by Arrow's C++ parser. For anyone serializing R numerics and expecting them back.

## Run it

```bash
make stage RECIPE=r-jsonlite     # once: the checks only — six doubles, literal in the script
spawn task run --spec "$(make -s spec RECIPE=r-jsonlite)" --wait
make ls RECIPE=r-jsonlite

x <- c(pi, exp(1), 1/3, .Machine$double.eps)
identical(x, fromJSON(toJSON(x)))                 # FALSE — default keeps 4 digits
identical(x, fromJSON(toJSON(x, digits = NA)))    # FALSE — "full precision" keeps 15
identical(x, fromJSON(toJSON(x, digits = I(17)))) # TRUE
```

## Which box — [measured](../../measurements/r-jsonlite-real/README.md) (all 143,455 OpenAlex institutions, 2.28 GB of JSON Lines, 8 workers)

| chip | instance | R wall | billed | peak RSS | **billed $/result** |
|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 1,944 s | 2,088 s | 19.0 GiB | 0.2734 |
| Intel | `r8i.2xlarge` | 2,204 s | 2,326 s | 19.1 GiB | 0.3590 |
| **AMD** | `r8a.2xlarge` | **1,350 s** | **1,484 s** | 19.2 GiB | **0.2634** |

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `digits = I(17)` | the default | **the default is `digits = 4`** and silently truncates: π becomes `3.1416`, a relative error of **1e-4**. Nothing warns you. |
| `digits = I(17)` | `digits = NA` | **`NA` is the usual advice and is still lossy** — 15 significant digits, `4.885e-15` off, and on real data it changed **84%** of records. The `I()` wrapper is what passes the number through literally. |
| `dataframe = "rows"` | `"columns"` / `"values"` | only `rows` round-trips to an identical `data.frame`; the other two come back **not a data.frame at all**. |
| expecting a double back | — | a **whole-valued** double vector returns as `integer`: `c(1, 3)` → `[1,3]` → `integer`. No NA needed. **`always_decimal = TRUE`** fixes it: with `I(17)` it round-trips every one of 143,455 real records, without it 95% fail. |
| Arrow as the referee | Python's `json` | **this env ships no Python** — the first in the catalog where that is true. `r-arrow`'s C++ reader is the independent parser available. |

**Leave the fixture.** Nothing is fetched: six doubles chosen so that four of them cannot be represented in 15 significant digits, which is what makes the finding visible. **Scale it** by round-tripping your own data — `identical()` is the right check at any size, and [r-arrow](../r-arrow/README.md) covers the binary path when JSON is the wrong format.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All checks are **6 s** of a 1m13s window; 38 s is installing Docker and 28 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: one exact round-trip, two lossy ones, an independent parser that localizes the loss to the bytes, and defects recorded rather than asserted</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| jsonlite / arrow / R | read from the running install | **2.0.0** / 25.0.0 / 4.5.3 |
| **`digits = I(17)` round-trip** | **`identical()` TRUE** | **TRUE, diff 0.000e+00** |
| control: default round-trip | **must be lossy** | FALSE, rel err **1.000e-04** |
| `digits = NA` round-trip | *recorded, **not** asserted* | FALSE, **4.885e-15** |
| **Arrow on the `I(17)` bytes** | **`max diff == 0`, identical** | **0.000e+00, TRUE** |
| Arrow on the default bytes | must be lossy | 3.333e-05 |
| Arrow on the `NA` bytes | *recorded* | 4.885e-15 |
| **integer / character / logical + NA** | **`identical()` TRUE** | all three TRUE |
| whole-valued doubles | *recorded* | `c(1,3)` → **integer**, FALSE |
| fractional doubles + NA | *recorded* | `c(1.5,NA,3.5)` → numeric, **TRUE** |
| **`dataframe = "rows"`** | **`identical()` TRUE** | **TRUE** |
| `columns` / `values` | *recorded* | not a data.frame |
| determinism | two calls, same bytes | byte-identical |

### The finding: `digits = NA` is not full precision

Three serializations of the same six doubles, with the byte counts that explain why:

```text
                       bytes   round-trips            Arrow max|diff|
default (digits = 4)      50   FALSE  rel err 1e-4        3.333e-05
digits = NA               97   FALSE                      4.885e-15
digits = I(17)           124   TRUE   diff 0              0.000e+00
```

`digits = NA` is the answer most readily reached for when the default turns out to be lossy,
and it **is not exact** — it keeps 15 significant digits. `I(17)` is what passes the
requested precision through literally. This page exists because that is not what the obvious
reading predicts: a recipe written from the documentation rather than from a run would have
asserted `digits = NA` and claimed an exactness it does not have.

**The default being lossy is asserted; `digits = NA` being lossy is only recorded.** `digits = 4`
is documented behaviour and will not change, and asserting it is what proves `identical()` is a
real constraint rather than something any output satisfies. But if upstream makes `NA` exact that
is an improvement, and a check demanding the loss would fail on it — the same call the
[mesalib](../mesalib/README.md) recipe makes about its missing OSMesa backend.

### Arrow localizes the loss to the serialization

`identical()` alone cannot say *where* precision went: jsonlite writes the text and jsonlite reads
it back, so a symmetric bug in both directions would cancel and look exact. Arrow's C++ JSON
reader shares no code with jsonlite's C parser, and reading **jsonlite's own bytes** it lands the
same distance from the original:

```text
jsonlite reader on the NA bytes    4.885e-15
Arrow     reader on the NA bytes   4.885e-15      <- the same, independently
Arrow     reader on the I(17) bytes 0.000e+00     <- exact, independently
```

Two unrelated parsers agreeing means neither is misparsing: the 15-digit **text does not carry the
bits**. Converting jsonlite's rows-array to the newline-delimited form Arrow wants is a reshape of
the same bytes and reformats no number. The `r` env ships no Python, so this referee had to come
from inside the env ([cross-checks](../../practices/cross-checks.md)).

### The type that does not survive, and what actually causes it

A double vector can come back as `integer`. The first reading of this was that `NA` was to blame,
since `c(1, NA, 3)` serializes to `[1,"NA",3]`. **That was wrong**, and one extra pair of cases
settled it:

```text
c(1, NA, 3)     na="string"  ->  [1,"NA",3]        class integer   identical FALSE
c(1, NA, 3)     na="null"    ->  [1,null,3]        class integer   identical FALSE
c(1, 3)         no NA at all ->  [1,3]             class integer   identical FALSE
c(1.5, NA, 3.5) NA present   ->  [1.5,"NA",3.5]    class numeric   identical TRUE
```

`na = "null"` does not fix it, and a vector with **no NA** fails too, while a vector **with** NA
succeeds when its values are fractional. So NA is not implicated: `fromJSON`'s simplification
infers `integer` from whole-valued JSON numbers. JSON has one number type, so the class cannot be
recovered from the text — this is a real limit of the format, not a jsonlite bug, which is why it
is recorded with the mechanism named rather than asserted as a defect.

Integer, character and logical vectors **do** round-trip identically with `NA` present (`NA` →
`null` for character and logical), so those three are asserted.

### Pins

| | |
|---|---|
| data | **none.** Six doubles literal in the script; the reference is `identical()` plus an independent parser |
| checks | `identities.R`, pinned by sha256 |
| image | `quay.io/aarchsci/r@sha256:db2fcb3c8d707afe6c…` — jsonlite 2.0.0, arrow 25.0.0, R 4.5.3, r-tidyverse, r-data.table |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

Staging checks the fixture is hostile enough to show the finding: **4 of 6** values cannot be
represented in 15 significant digits, and **1 of 6** (`1e-17`, a clean power of ten) survives even
4 digits — kept deliberately, because it shows the loss is a property of the value rather than of
serialization in general.

### On x86_64

The same spec on `m8i.large`, through the `r` env's linux-64 twin
(`envs-x86_64/r-d98eb9641259.linux-64.lock`: jsonlite, arrow and R at the same versions; it differs
from the image only in `curl`/`libcurl`, `libglib`, `sqlite`/`libsqlite`, `r-future`,
`r-future.apply` and `r-globals`, none of them on this path). **Every line of `score.tsv` is
identical to the arm64 run except `arch`**: the same JSON bytes for all three serializations
(50 / 97 / 124), the same `4.885e-15` loss at `digits = NA` from both parsers, the same exact
`I(17)` round-trip and the same `integer` inference.

### Run + verify

```sh
make stage RECIPE=r-jsonlite
spawn task run --spec "$(make -s spec RECIPE=r-jsonlite)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/r-jsonlite/r1/score.tsv" -
make stage RECIPE=r-jsonlite ARCH=x86_64   # also stages the env twin, once
spawn task run --spec "$(make -s spec RECIPE=r-jsonlite ARCH=x86_64)" --wait   # writes runs-x86_64/
```

Fails on a pin mismatch, a `digits = I(17)` round-trip that is not `identical()`, a default that
round-trips exactly (which would mean the whole page needs re-deriving), Arrow reading the `I(17)`
bytes as anything but exact, Arrow reading the default bytes as exact, an integer/character/logical
vector with `NA` that does not survive, a `dataframe = "rows"` frame that is not identical, or
non-byte-identical output from two identical calls — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Parsing hostile input.** No malformed JSON, no deep nesting, no duplicate keys, no unicode escape
edge cases — the JSONTestSuite-style conformance corpus would be the right reference and is not
staged here.

**`stream_in`/`stream_out`** and nested, ragged structures are not exercised by this fixture. The
[measurement](../../measurements/r-jsonlite-real/README.md) covers both on 2.28 GB of real records, and
the data.frame path is where its round trip stops being exact (2 of 28 columns). It also measures
throughput and memory at that size.

**`auto_unbox`** on R-built data, which changes whether a length-1 vector becomes a scalar. **Dates,
factors and POSIXct**, none of which have a JSON representation and all of which are common in R
data frames.

</details>
