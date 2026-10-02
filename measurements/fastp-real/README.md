# fastp: the cheaper box has half the cores

> **44% cheaper for 5 seconds slower.** `m8g.2xlarge` against `c8g.4xlarge` on the same 48M-read run —
> and reading the core count right would still have picked the expensive one, because the binding
> resource was staging space.

fastp 1.3.6 on the complete SRR062634 run (48,297,986 reads, 4.83 Gbp, 100 bp), same image digest,
same input objects.

| instance | vCPU | RAM | `-w` | fastp | billed | $/hr | **$/run** | tmpfs peak |
|---|---|---|---|---|---|---|---|---|
| `c8g.4xlarge` | 16 | 32 GiB | 16 | **31 s** | 226 s | 0.6381 | 0.0401 | 7,116 MB |
| **`m8g.2xlarge`** | 8 | 32 GiB | 8 | 36 s | 226 s | **0.3590** | **0.0225** | 7,154 MB |

Every count in the two runs is identical — reads in, each filter bucket, both output read counts. Only
the wall clock and a 38 MB difference in tmpfs high-water mark differ.

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

36 s of fastp inside a 226 s billed window: **84% of what you pay for is boot, Docker install and image
pull.** For a tool this fast the task boundary costs more than the task, which is an argument for
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

## Caveats

n = 1 per box. The counts being bit-identical across the two runs is what makes that tolerable for the
*result*; the 31-vs-36 s difference is a single pair of observations and should not be read as a
precise thread-scaling figure, only as "threads are not the lever here."

One library, 100 bp reads, ~1.5× genome-wide. Longer reads or a deeper library raise the staging
footprint, which is the quantity that picks the instance — so re-measure the peak before reusing this
sizing.
