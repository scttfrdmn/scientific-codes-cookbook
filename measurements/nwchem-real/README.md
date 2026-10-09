# NWChem: don't parallelise the calculation, parallelise the campaign

> **At a fixed 16 cores, running 16 one-rank jobs at once finishes each calculation in 9.2 s of
> amortised wall time; running one 16-rank job takes 25 s. That is 2.72× the throughput for the
> same box and the same hour.** And packing is nearly free — each job inside a packed set runs
> within 0.0–6.5% of what it costs alone on a box of its own size, so the memory-bandwidth
> penalty people expect is not there at this size.

NWChem 7.2.3 (aarchsci `dft` env), caffeine at B3LYP/6-31G\*, **217 basis functions**, converging
to `-625.538048227205 Ha` on every rung. `OMP_NUM_THREADS=1` throughout, same image digest, same
input. Three experiments on `c8g`: how far one calculation scales, what a right-sized box costs,
and what happens when you pack several calculations onto one.

## 1. One calculation: it scales, but badly, and that is the point

16 vCPU on `c8g.4xlarge`, one job, ranks varied:

| ranks | wall | speedup | parallel efficiency | marginal, per doubling |
|---|---|---|---|---|
| 1 | 128 s | 1.00× | 100.0% | — |
| 2 | 100 s | 1.28× | 64.0% | 1.28× |
| 4 | 67 s | 1.91× | 47.8% | 1.49× |
| 8 | 41 s | 3.12× | 39.0% | 1.63× |
| **16** | **24 s** | **5.33×** | **33.3%** | **1.71×** |

Reading this as "the knee is at 16, because every doubling still returns ≥1.3×" is defensible and
is what the run recorded — but it is the wrong question, and section 3 is why.

**The marginal return *rises* (1.28 → 1.49 → 1.63 → 1.71) while efficiency falls.** That is
backwards from the usual Amdahl picture, where each doubling buys less than the last. The 1→2 step
is the anomaly: it returns only 1.28× where later doublings do better. A fixed serial cost that
MPI pays once — startup, basis setup, integral screening — is the obvious candidate, since it
would be amortised over more ranks as the count grows. **This measurement does not establish
that**; it establishes the shape. Anyone relying on the explanation should profile it.

At 33.3% efficiency on 16 ranks you are paying for 16 cores to get 5.33× — so two thirds of the
box is being wasted on a calculation that is simply too small to fill it.

## 2. A right-sized box: the utilisation is excellent, which is misleading

Ranks matched to the box, so nothing is paid for and left idle:

| ranks | box | wall | avg cores busy | core utilisation |
|---|---|---|---|---|
| 1 | 2 vCPU | 138 s | 1.00 | **50.0%** |
| 2 | 2 vCPU | 111 s | 1.97 | 98.5% |
| 4 | 4 vCPU | 72 s | 3.94 | 98.5% |
| 8 | 8 vCPU | 42 s | 7.85 | 98.1% |
| 16 | 16 vCPU | 24 s | 15.27 | 95.4% |

**Utilisation says 95–98.5% and utilisation is the wrong metric.** Every one of these rungs keeps
its cores busy; what differs is how much *science* that busyness produces. The 16-rank box is 95.4%
utilised and 33.3% efficient — it is busy doing communication. Utilisation measures whether you
are paying for idle silicon; it says nothing about whether the work is useful.

The 1-rank row is the exception worth knowing: **there is no 1-vCPU box**, so a serial NWChem run
on the smallest `c8g` wastes half of it by construction. If your campaign is serial, that is an
argument for packing (section 3), not for a smaller instance.

## 3. Packing: 2.72× the throughput, for almost nothing

One 16-vCPU box, *J* concurrent jobs of *R* ranks each, 16 cores busy in every configuration:

| jobs × ranks | wall for all *J* | **per calculation** | same *R* alone on its own box | contention |
|---|---|---|---|---|
| 1 × 16 | 25 s | 25.00 s | 24 s | +4.2% |
| 2 × 8 | 42 s | 21.00 s | 42 s | +0.0% |
| 4 × 4 | 72 s | 18.00 s | 72 s | +0.0% |
| 8 × 2 | 110 s | 13.75 s | 111 s | −0.9% |
| **16 × 1** | **147 s** | **9.19 s** | 138 s | **+6.5%** |

Two results, and the second is the surprising one:

1. **Throughput improves monotonically as you pack narrower jobs** — 25.0 → 9.19 s per
   calculation, a **2.72×** gain from the same hardware and the same hour. It is the direct
   consequence of section 1: ranks you add to one calculation return 33% efficiency, while jobs
   you add return nearly 100%.
2. **Packing is nearly free.** Each job inside a packed set runs within **0.0–6.5%** of its cost
   alone on a right-sized box. The expected memory-bandwidth collapse does not happen here —
   a 217-basis-function SCF has a working set small enough that 16 copies coexist.

So for a campaign of small molecules the advice inverts: **stop tuning the rank count and start
packing the box.** The rank sweep's "knee at 16 ranks" is a correct answer to a question nobody
running a campaign should ask.

**Where this stops being true:** the free-packing result is a property of *this* working set. A
larger basis, a bigger molecule, or a method with a real memory footprint will contend, and the
contention column is the thing to re-measure before trusting the conclusion. The measurement is
one molecule at one basis — it establishes that packing *can* be free, not that it always is.

### `--bind-to none` is load-bearing, and silently so

Each concurrent `mpiexec` believes it owns the machine and binds rank *k* to core *k*. Without
`--bind-to none`, eight concurrent 2-rank jobs therefore pile all sixteen ranks onto cores 0 and 1
while fourteen cores sit idle — and nothing reports an error. The wall time simply collapses and
the obvious conclusion ("packing contends, as expected") is exactly wrong.

This is why the contention column above is worth reporting rather than asserting: it is also the
check that the binding worked. 16 cores busy in all five configurations (`avg_cores` 14.96–15.87)
is the evidence.

## What is pinned and asserted on every rung

Each run carries the checks the [nwchem recipe](../../recipes/nwchem/README.md) asserts, so a rung
that silently fell back to a serial build or failed to converge cannot enter the table:

- `nproc` read from NWChem's own output equals the rank count launched — conda-forge ships `nompi`
  builds at higher build numbers, so an unpinned solve under `mpiexec -n 16` runs sixteen
  independent rank-0 calculations, all printing the same energy
  ([rank-count guard](../../practices/mpi-rank-count.md))
- 217 basis functions, from NWChem, not from the input file
- SCF reached an energy (a converged exit, not a parsed number from a dead run)
- serial and 4-rank agree: `|−625.538048227205 − −625.538048181779| = 4.54e-08 Ha`, against a
  1e-6 Ha tolerance

That last one is the cross-check that makes the timings meaningful: the parallel runs are
computing the same answer, to eight decimal places, as the serial one.

## Specs

`rank-sweep.task.json` (section 1), `rightsized-{1,2,4,8,16}.task.json` (section 2),
`pack-{1x16,2x8,4x4,8x2,16x1}.task.json` and `packing.task.json` (section 3). Raw per-rung output
in `results/`.
