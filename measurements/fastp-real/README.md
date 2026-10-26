# fastp: the cheaper box has half the cores

> **41% cheaper per run for 5 seconds slower.** `m8g.2xlarge` against `c8g.4xlarge` on the same 48M-read run —
> and reading the core count right would still have picked the expensive one, because the binding
> resource was staging space.

fastp 1.3.6 on the complete SRR062634 run (48,297,986 reads, 4.83 Gbp, 100 bp), same input objects,
on Graviton4, same-generation Intel and AMD (aarch.bio's arm64 image; bioconda's `linux/amd64` build
of the same version on both x86 chips).

| instance | vCPU | RAM | `-w` | fastp | billed | $/hr | **$/run** | tmpfs peak |
|---|---|---|---|---|---|---|---|---|
| `c8g.4xlarge` | 16 | 32 GiB | 16 | **31 s** | 154 s | 0.6381 | 0.0273 | 7,116 MB |
| **`m8g.2xlarge`** | 8 | 32 GiB | 8 | 36 s | 162 s | **0.3590** | **0.0162** | 7,154 MB |
| `c8i.4xlarge` | 16 | 32 GiB | 16 | 29 s | 143 s | 0.7497 | 0.0298 | 7,019 MB |
| `m8i.2xlarge` | 8 | 32 GiB | 8 | 52 s | 169 s | 0.4234 | 0.0199 | 7,129 MB |
| `c8a.4xlarge` | 16 | 32 GiB | 16 | 22 s | 120 s | 0.8622 | 0.0287 | 7,230 MB |
| `m8a.2xlarge` | 8 | 32 GiB | 8 | 32 s | 130 s | 0.4869 | 0.0176 | 7,180 MB |

*Billed* is spawn's task window (wrapper start to stage-out done, from each task's completion record in
[`results/billed-windows.txt`](results/billed-windows.txt)); boot before the wrapper is not in it.

Every count in the six runs is identical — reads in, each filter bucket, both output read counts,
Q30 rate and fastp's duplication estimate — so all four assertions (the exact read count, the
conservation identity, mates paired, outputs == passed) hold on all three chips, and the counts are
a fixed point across chips. Only the wall clock and the tmpfs high-water mark (7,019–7,230 MB) differ.

- **The m-over-c lesson holds on every chip:** the 8-vCPU `m` box costs 56% of the 16-vCPU `c` box's
  hourly price on Graviton4, Intel and AMD alike, because all three need 32 GiB for staging; per run it
  is 41% cheaper on Graviton4, 39% on AMD and 33% on Intel.
- **Threads are worth more on Intel**, where 8 vCPU is 4 cores × 2 hyperthreads (`lscpu`; Graviton4
  and AMD 8a are 1 thread per core): 8 → 16 threads bought 1.79× there, 1.45× on AMD, 1.16× on
  Graviton4. Even so, `c8i` does not pay for itself.
- **AMD's `m8a.2xlarge` billed 9% over Graviton4's `m8g.2xlarge`**: a shorter window (130 s against
  162 s) at a 36% higher hourly rate. Most of each window is the image pull, the in-task hashing and
  gzip counts, and stage-out, not fastp, which is also cheapest on Graviton4 ($0.0036 of fastp seconds
  against AMD's $0.0043 and Intel's $0.0061). n = 1 per cell, so the 9% is one pair of observations.

## Why RAM picks the box and cores do not

Input is 3.6 GiB compressed; output is ~3.4 GiB. Both live in host `/tmp` at the same time, which on
the spawn task path is [tmpfs sized at half of RAM](../../practices/container-path.md). The measured
peak is **7,154 MB**, so the task needs ≥8 GiB of tmpfs with headroom — call it 16 GiB — and therefore
≥32 GiB of RAM.

On the compute-optimised family, 32 GiB only comes with 16 vCPU (`c8g.4xlarge`). On the
general-purpose family it comes with 8 (`m8g.2xlarge`), at 56% of the price. fastp cannot spend the
extra cores: doubling threads from 8 to 16 bought 5 s on a 36 s job, because this workload is gzip
decompression and recompression around a cheap per-read filter.

`c8g.2xlarge` is cheaper still ($0.3190) but has 16 GiB of RAM, so 8 GiB of tmpfs against a 7,154 MB
peak — 87% full, with nothing left for a longer read or a deeper library. The 11% saving is not worth
sizing to 13% headroom.

**The general lesson is not "use m8g".** It is that on this path the staging footprint is a first-class
sizing input, and it can select a *family* the compute profile would never have chosen. A sizing pass
that reads only CPU time and peak RSS misses it entirely.

## Boot dominates, by a lot

36 s of fastp inside a 162 s billed window: **78% of the task window is Docker install, image pull,
staging and the in-task checks**, and boot comes on top of it. For a tool this fast the task boundary costs more than the task, which is an argument for
putting QC in the same task as whatever consumes its output rather than paying a second boot — the one
place where [one tool per image](../../practices/container-path.md) has a real cost rather than just a
shape.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make stage RECIPE=bwa-samtools   # fastp reads bwa's reads
make run   RECIPE=fastp          # the m8g rung, as shipped
```

The `c8g.4xlarge` leg is the same spec with `resources.families` set to `["c8g"]`, `cpu` 16 and `-w 16`.
The x86 legs are [`m8-2xl.task.json`](m8-2xl.task.json) and [`c8-4xl.task.json`](c8-4xl.task.json) —
the recipe's spec with those changes, its own output prefix, and a topology record — resolved by
`python3 scripts/spec_arch.py fastp x86_64|amd <bucket> <nonce> measurements/fastp-real/<rung>.task.json`.
On spawn 0.139.1 these exceed EC2's 16 KiB user data (spawn#1063/#1246), so they ran with the inline
command moved, byte for byte, into a staged sha256-checked file. Spend for the four x86 legs: $0.13 (EC2 launch to terminate, CloudTrail).

## Caveats

n = 1 per box. The counts being bit-identical across the two runs is what makes that tolerable for the
*result*; the 31-vs-36 s difference is a single pair of observations and should not be read as a
precise thread-scaling figure, only as "threads are not the lever here."

One library, 100 bp reads, ~1.5× genome-wide. Longer reads or a deeper library raise the staging
footprint, which is the quantity that picks the instance — so re-measure the peak before reusing this
sizing.
