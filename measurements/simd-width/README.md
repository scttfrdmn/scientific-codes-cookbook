# SIMD width across Graviton generations — run it yourself

Six one-task probes that report what each Graviton generation actually implements for
SIMD, measured on the hardware rather than read off a marketing page. Written because
"should I chase SVE?" comes up constantly and the answer is no — for a reason worth
seeing rather than being told.

**These scripts are meant to be run.** They are tiny (`.large` boxes, ~8 minutes TTL,
about a cent each) and they touch nothing but `/proc` and `lscpu`.

## Run it

```sh
export AWS_PROFILE=aws
for g in c6g c7g c7gn c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" probe-$g.task.json > /tmp/sp-$g.json
  spawn task run --spec /tmp/sp-$g.json --wait
done

# Graviton3E's HPC family is not in us-west-2; spawn takes a region
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" probe-hpc7g.task.json > /tmp/sp-hpc7g.json
spawn task run --spec /tmp/sp-hpc7g.json --region us-east-1 --wait

aws s3 cp "s3://$COOKBOOK_BUCKET/measurements/simd-width/" . --recursive
```

Every task is `spawn task run` with `lifecycle.ttl`, `on_complete: terminate` and a
`cost_limit`, so nothing is launched or reaped by hand.

`probe.sh` reads: `cpu_part` (which Neoverse core), the `Features` line, whether
`sve`/`sve2`/`bf16`/`i8mm` are present, the SVE vector length from
`/proc/sys/abi/sve_default_vector_length` (bytes × 8), and `lscpu` cache/NUMA.

## What came back

Measured us-west-2 (us-east-1 for hpc7g), 2026-09-23:

| generation | family probed | `cpu_part` | core | SVE | SVE2 | **SVE bits** | NEON bits |
|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g` | `0xd0c` | Neoverse-N1 | no | no | — | 128 |
| Graviton3 | `c7g` | `0xd40` | Neoverse-V1 | yes | no | **256** | 128 |
| Graviton3E | `c7gn` | `0xd40` | Neoverse-V1 | yes | no | **256** | 128 |
| Graviton3E | `hpc7g` | `0xd40` | Neoverse-V1 | yes | no | **256** | 128 |
| Graviton4 | `c8g` | `0xd4f` | Neoverse-V2 | yes | **yes** | **128** | 128 |
| Graviton5 | `c9g` | `0xd84` | — | yes | **yes** | **128** | 128 |

Two results, neither of which you would guess from the generation number:

**Graviton3E has the same vector hardware as Graviton3 — and AWS claims 35% more vector
performance anyway.** Both statements are sourced, and reconciling them is the interesting
part.

`c7gn` and `hpc7g` report the *identical* `cpu_part 0xd40` and the identical 256-bit SVE as
plain `c7g`. AWS's own
[Graviton technical guide](https://github.com/aws/aws-graviton-getting-started) agrees: it
puts them in a single combined **"Graviton3(E)"** column — same Neoverse-V1, 2600 MHz,
ARMv8.4-a, 8× DDR5, 32 MB LLC, same `4x Neon 128bit / 2x SVE 256bit` — with no E-only row.

But the [hpc7g product page](https://aws.amazon.com/ec2/instance-types/hpc7g/) claims *"up to
35% higher vector instruction performance compared to existing AWS Graviton3 instances"*,
*"35% higher floating-point performance"*, and *"20% higher performance as measured by Life
Science applications such as GROMACS"*.

So the claim is real and the vector units are the same. What differs is **how well those
units are fed** — and AWS advertises exactly that knob:

| type | cores | memory | **GB/core** | network | EFA |
|---|---|---|---|---|---|
| hpc7g.4xlarge | 16 | 128 GiB | **8** | 200 Gbps | yes |
| hpc7g.8xlarge | 32 | 128 GiB | **4** | 200 Gbps | yes |
| hpc7g.16xlarge | 64 | 128 GiB | **2** | 200 Gbps | yes |
| c7g.4xlarge | 16 | 32 GiB | **2** | up to 15 Gbps | **no** |
| c7g.16xlarge | 64 | 128 GiB | 2 | 30 Gbps | yes |

**Every hpc7g size gets the whole socket** — 128 GiB and 200 Gbps — so `hpc7g.4xlarge` gives
16 cores 4× the memory and >13× the network of `c7g.4xlarge`, and those 16 cores are not
contending with 48 neighbours for DDR5 channels. Vector and floating-point kernels are
usually **bandwidth-bound, not issue-width-bound**: the same 512 bit/cycle of vector
capability produces more finished work when the operands arrive faster.

**That is the hypothesis, not a proven result:** the 35% looks like a bandwidth-per-core
figure wearing vector-performance language, because the vector hardware is provably
identical. See *what this does not establish* below for the experiment that would settle it.

**SVE gets *narrower* with newer generations.** 256-bit on Graviton3, 128-bit on Graviton4
and Graviton5. Newer, faster, cheaper-per-result silicon with a *smaller* vector register.

## Why that is not a regression — and why chasing SVE is pointless

The width is the wrong number. AWS's table gives pipes as well as width:

| generation | SIMD units × width | peak vector bandwidth |
|---|---|---|
| Graviton2 | 2 × NEON 128 | 256 bit/cycle |
| Graviton3(E) | 4 × NEON 128, or **2 × SVE 256** | **512 bit/cycle** |
| Graviton4 | **4 × NEON/SVE 128** | **512 bit/cycle** |
| Graviton5 | **4 × NEON/SVE 128** | **512 bit/cycle** |

**Graviton3's 2×256 and Graviton4's 4×128 are the same 512 bits per cycle.** The wider SVE
register on Graviton3 is not more throughput — it is the same throughput expressed in fewer,
wider operations. AWS's recommended compiler target for Graviton3, 3E, 4 and 5 is the same
`-mcpu=neoverse-512tvb`, and that name says it outright: **512-bit total vector bandwidth**,
constant across four generations.

So there is no SVE dividend to chase across Graviton. NEON at 128 bits is present on every
generation including Graviton2, it saturates the same peak bandwidth, and a build targeting
plain NEON runs everywhere. The generation-to-generation gains that *are* real come from
clock, IPC, cache and memory — which is why the cookbook's family comparison is a
[$/result measurement](../../patterns/cost-per-result.md), not a feature checklist.

## What this establishes, and what it does not

**Established:** what each generation implements; that Graviton3E's *core and vector width
are identical to Graviton3* (`cpu_part`, SVE length, and AWS's own combined spec column);
that peak vector bandwidth is flat at 512 bit/cycle from Graviton3 through Graviton5; and
that hpc7g's real, documented differentiators are memory-per-core, 200 Gbps and EFA.

**Not established — two open legs, both cheap:**

1. **Is the "35% vector" figure a bandwidth result?** The decisive comparison is
   `hpc7g.4xlarge` against `c7g.4xlarge`: *same core, same 256-bit SVE, same 2.6 GHz, same
   16 cores* — differing only in memory-per-core (8 vs 2 GB) and network. A bandwidth probe
   plus an FP kernel across that pair, and across the three hpc7g sizes (8/4/2 GB per core
   at constant socket), isolates bandwidth from everything else. AWS's own GROMACS claim
   makes this directly testable with a recipe the catalog already has.
2. **Does enabling SVE do anything?** The same kernel built `-march=armv8-a` versus
   `+sve`/`+sve2`, timed per generation, plus a scan of the shipped conda binaries for SVE
   opcodes at all. The architectural argument says the ceiling is identical; it does not
   measure what a compiler does with it.

Until those run, the honest summary is: **there is no vector-width dividend to chase across
Graviton, and the thing that actually varies between 3 and 3E is how fast you can feed the
cores.**
