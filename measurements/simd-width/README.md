# SIMD, bandwidth and the SVE question across Graviton — measured

> **Don't select SVE. Select the CPU.** `-mcpu=neoverse-512tvb` emits SVE *and* runs 2.3×
> faster than asking for SVE by hand with `-march=…+sve`, which produces SVE instructions
> without the tuning and performs like plain NEON. Measured on Graviton 2/3/3E/4/5.

Everything here is a `spawn task run` with a TTL, `on_complete: terminate` and a
`cost_limit`. **The scripts are meant to be run** — they are cheap (a few cents to about a
dollar for the whole set) and they are the evidence for the claims below.

## Run it

```sh
export AWS_PROFILE=aws

# 1. what each generation implements (tiny, ~1 cent each)
for g in c6g c7g c7gn c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" probe-$g.task.json > /tmp/p.json
  spawn task run --spec /tmp/p.json --wait
done
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" probe-hpc7g.task.json > /tmp/p.json
spawn task run --spec /tmp/p.json --region us-east-1 --wait   # hpc7g is not in us-west-2

# 2. STREAM thread sweep + the NEON/SVE/SVE2 kernel comparison
for g in c6g c7g c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" sv-$g.task.json > /tmp/s.json
  spawn task run --spec /tmp/s.json --wait
done

# 3. disassemble each build — which one actually emits SVE?
for g in c7g c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" dis-$g.task.json > /tmp/d.json
  spawn task run --spec /tmp/d.json --wait
done

aws s3 cp "s3://$COOKBOOK_BUCKET/measurements/simd-width/" . --recursive
```

STREAM is the official source from `cs.virginia.edu`, staged as a pinned input and verified
in-task by sha256 `a52bae5e175bea3f7832112af9c085adab47117f7d2ce219165379849231692b`. Built
`-DSTREAM_ARRAY_SIZE=40000000` — 305 MiB per array, 916 MiB total, far past any LLC here.

## 1. What the hardware is

| generation | family | `cpu_part` | core | SVE | SVE2 | **SVE bits** | NEON bits | LLC |
|---|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g` | `0xd0c` | Neoverse-N1 | no | no | — | 128 | 32 MiB |
| Graviton3 | `c7g` | `0xd40` | Neoverse-V1 | yes | no | **256** | 128 | 32 MiB |
| Graviton3E | `c7gn` | `0xd40` | Neoverse-V1 | yes | no | **256** | 128 | 32 MiB |
| Graviton3E | `hpc7g` | `0xd40` | Neoverse-V1 | yes | no | **256** | 128 | 32 MiB |
| Graviton4 | `c8g` | `0xd4f` | Neoverse-V2 | yes | **yes** | **128** | 128 | 36 MiB |
| Graviton5 | `c9g` | `0xd84` | — | yes | **yes** | **128** | 128 | 48 MiB |

No SMT on any of them — `threads/core = 1`, so 16 vCPU is 16 physical cores throughout.

**SVE gets narrower with newer generations** (256 → 128), which is not a regression: AWS's
guide gives pipes as well as width, and Graviton3's 2×SVE-256 and Graviton4/5's 4×SVE-128
are both **512 bits/cycle**. The recommended target for Graviton3 through 5 is the same
`-mcpu=neoverse-512tvb` — "512-bit total vector bandwidth", held constant by design.

## 2. The SVE question, settled

A compute-bound dot product (32 KB arrays, L1-resident, so this is issue-limited not
memory-limited), built four ways, run on each chip. `sve_insns`/`neon_insns` are counted by
disassembling the hot loop:

| chip | `-march=armv8-a` | **`-mcpu=neoverse-512tvb`** | `-march=armv8.2-a+sve` | `-march=armv9-a+sve2` |
|---|---|---|---|---|
| c6g (Gv2) | 4.74 | 4.99 | n/a | n/a |
| c7g (Gv3) | 5.15 &nbsp;(0 sve / 12 neon) | **11.84** &nbsp;(38 sve / 0 neon) | 10.39 &nbsp;(22 sve) | n/a |
| hpc7g (Gv3E) | 5.15 | **11.87** | 10.39 | n/a |
| c8g (Gv4) | 5.59 &nbsp;(0 sve / 12 neon) | **12.93** &nbsp;(38 sve / 0 neon) | 5.63 &nbsp;(22 sve) | 10.22 &nbsp;(38 sve) |
| c9g (Gv5) | 6.56 &nbsp;(0 sve / 12 neon) | **16.18** &nbsp;(38 sve / 0 neon) | 6.63 &nbsp;(22 sve) | 12.17 &nbsp;(38 sve) |

GFLOP/s, single-threaded, best of three.

**The winning build uses SVE.** `-mcpu=neoverse-512tvb` emits 38 SVE instructions and zero
NEON, and it is fastest on every chip that has SVE. So SVE is not useless — it is what the
fast code runs.

**But selecting SVE by hand is much slower than selecting the CPU.**
`-march=armv8.2-a+sve` emits SVE too — only 22 instructions — and on Graviton4 it scores
**5.63 against 12.93**, i.e. *no better than plain NEON* (5.59). Same ISA, same chip, 2.3×
less throughput. The difference is the **scheduling model and unrolling** a `-mcpu` target
brings, not the instruction set: 38 versus 22 instructions in the same loop is the compiler
pipelining far more aggressively when it knows the microarchitecture.

So the practical rule is sharper than "don't chase SVE": **chasing the SVE feature flag is
the mistake, and it costs up to 2.3×. Name the CPU and you get SVE and the tuning.** The
uplift people attribute to SVE is mostly the tuning that arrives with `-mcpu`.

Generation trend with the right flags: 4.99 → 11.84 → 12.93 → 16.18. The big step is
**Graviton2 → Graviton3 (2.4×)**; after that +9% and +25%.

## 3. Memory bandwidth — and where "newer is better" breaks

STREAM Triad, GB/s, by thread count on a 16-core box (hpc7g is 64-core):

| chip | t1 | t2 | t4 | t8 | t16 | t64 | peak |
|---|---|---|---|---|---|---|---|
| c6g (Gv2) | 25 | — | — | — | 171 | — | 171 |
| c7g (Gv3) | 48 | — | — | — | **225** | — | 225 |
| hpc7g (Gv3E) | 47 | — | — | — | **227** | 252 | 252 |
| c8g (Gv4) | 50 | 68 | 113 | 173 | **337** | — | **337** |
| c9g (Gv5) | 50 | 69 | 125 | 156 | **141** | — | 156 |

Two results worth acting on:

**A 4xlarge is not given a quarter of the socket.** `c7g.4xlarge` pulls 225 GB/s on 16 of
its host's 64 cores; `c8g.4xlarge` pulls 337 GB/s on 16 of 96. AWS does not proportion
memory bandwidth by vCPU share, so a small instance can be far more bandwidth-rich per core
than the core count suggests.

**Graviton5 `c9g.4xlarge` saturates early and then declines** — 125 GB/s at 4 threads, 156
at 8, **141 at 16** — while `c8g.4xlarge` climbs to 337. Reproduced in a second independent
run (149.8 GB/s at 16 threads), with STREAM's own `Solution Validates` passing every time.
So at this instance size, **for bandwidth-bound work Graviton4 delivers ~2.2× Graviton5** —
while Graviton5 is 25% *faster* per core on compute. Which generation wins depends entirely
on which resource your code is bound by, which is the whole argument of
[sizing](../../patterns/sizing.md) and [cost per result](../../patterns/cost-per-result.md).

## 4. Graviton3 vs 3E: the 35% claim does not reproduce

AWS's [hpc7g page](https://aws.amazon.com/ec2/instance-types/hpc7g/) claims *"up to 35%
higher vector instruction performance compared to existing AWS Graviton3 instances"* and
*"35% higher floating-point performance"*. AWS's own
[technical guide](https://github.com/aws/aws-graviton-getting-started) meanwhile puts the two
in a single **"Graviton3(E)"** column — same Neoverse-V1, 2600 MHz, same
`4x Neon 128bit / 2x SVE 256bit` — with no E-only row.

At equal thread count, measured:

| | c7g (Graviton3) | hpc7g (Graviton3E) | difference |
|---|---|---|---|
| STREAM Triad, 16 threads | 224.6 GB/s | 226.5 GB/s | **+0.8%** |
| FP kernel, `-mcpu=…512tvb` | 11.85 GFLOP/s | 11.87 GFLOP/s | **+0.2%** |
| FP kernel, plain NEON | 5.15 | 5.15 | **0%** |
| `cpu_part` / SVE bits | `0xd40` / 256 | `0xd40` / 256 | identical |

**No 35%, and no measurable vector or FP advantage at all** — under 1% on both axes, which is
run-to-run noise. An earlier draft of this page guessed the 35% was a *bandwidth-per-core*
effect, since hpc7g gives every size the full socket. That guess is also **not supported**:
`c7g.4xlarge` already gets ~full-socket bandwidth for 16 cores (225 vs hpc7g's 227 at the same
16 threads), so "the whole socket for fewer cores" is not a differentiator against c7g at
equal core count either.

What *is* real and documented about hpc7g: **200 Gbps EFA** (against 30 Gbps and no EFA on a
c7g.4xlarge), single-AZ placement, and selectable cores per node — all of which matter for
tightly-coupled multi-node MPI and none of which show up in single-node vector math. The
honest reading is that the 35% is not a per-core property we can observe; it is either
workload-and-toolchain specific or it lives in the interconnect.

**Note the pricing, because it is counter-intuitive:** `hpc7g.4xlarge`, `.8xlarge` and
`.16xlarge` all cost **$1.6832/hr**. You rent the socket, not the cores. So there is no cost
saving in taking the small one — and a 16-core hpc7g costs 4× a `c7g.4xlarge` ($0.58/hr) for
bandwidth and FP we measured as equal.

## Caveats

n = 1 per cell except where stated (c9g's 16-thread collapse reproduced twice; hpc7g and c7g
each measured once on identical binaries). STREAM's Triad counts 24 bytes/element; if the
compiler is not emitting non-temporal stores the true DRAM traffic is higher, which shifts
all rows equally and leaves the comparisons intact. One instance size per generation — the
c9g result in particular may be specific to how a `4xlarge` slice of a Graviton5 host is
provisioned, and deserves a second size before anyone generalises it.

## A spawn rough edge found here

Asked for `cpu=16` in the `hpc7g` family, spawn sized **`hpc7g.16xlarge`** (64 vCPU) — for all
three of my specs. Not a pricing error: every hpc7g size costs the same, so on a price tie
spawn's "cheapest that fits" has no preference and returned the largest. Two things would
help: **prefer the smallest type that fits on a tie**, and let a spec **pin an exact instance
type**, which a bandwidth-per-core sweep needs by construction. Worked around by sweeping
threads on one socket instead — which is the better experiment anyway, and cheaper.
