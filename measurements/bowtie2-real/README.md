# bowtie2 on a real workload — four Graviton generations, and no knee at all through 64 cores

> **There is no scaling knee: bowtie2 goes 4.00× faster on 4× the cores, at 100.1% parallel
> efficiency. And because c8g's per-core price is exactly flat, the compute cost of the result is
> *invariant* with core count — $0.1730–0.1737 at 16, 32, 48 and 64 cores. The only thing you pay
> for going fast is boot time charged at a higher hourly rate.**

bowtie2 2.5.5 `--local` aligning a full 1000 Genomes sequencing run — **24,148,993 read pairs**,
`SRR062634` — to the complete `GRCh38_full_analysis_set_plus_decoy_hla` index. 99.79% overall
alignment rate, identical on every rung. Same image digest, same input objects, same index.

## Generation sweep — 16 threads everywhere

| generation | instance | CPU part | bowtie2 | billed | $/hr | **$/run** | compute-only $ |
|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | `0xd0c` | 1536 s | 1667 s | 0.5440 | **0.2519** | 0.2321 |
| Graviton3 | `c7g.4xlarge` | `0xd40` | 1122 s | 1240 s | 0.5800 | **0.1998** | 0.1808 |
| Graviton4 | `c8g.4xlarge` | `0xd4f` | 977 s | 1063 s | 0.6381 | **0.1884** | 0.1732 |
| **Graviton5** | `c9g.4xlarge` | `0xd84` | **746 s** | 824 s | 0.6955 | **0.1592** | **0.1441** |

**2.06× faster and 36.8% cheaper per result** from Graviton2 to Graviton5.

Per-step speedup is **1.37×, 1.15×, 1.31×** — and that middle step is the interesting one.
Graviton3→Graviton4 buys only 1.15× here, and the cost falls just 5.7% ($0.1998 → $0.1884), while
the steps either side are worth 20.7% and 15.5%. **So Graviton3→Graviton4 is nearly cost-neutral for
a string-matching aligner too** — the same shape the DFT codes show in
[dft-crosscheck](../dft-crosscheck/README.md). That was previously read as a property of
likelihood/matrix codes; it now has a third, very different data point. Still an observation, not an
explanation.

## Knee sweep — Graviton4, more cores. There is no knee.

| cores | instance | bowtie2 | billed | $/hr | $/run | avg_cores | speedup | parallel efficiency |
|---|---|---|---|---|---|---|---|---|
| 16 | `c8g.4xlarge` | 977 s | 1063 s | 0.6381 | **0.1884** | 15.95 | 1.00× | 100.0% |
| 32 | `c8g.8xlarge` | 490 s | 575 s | 1.2762 | 0.2038 | 31.87 | 1.99× | 99.7% |
| 48 | `c8g.12xlarge` | 326 s | 409 s | 1.9142 | 0.2175 | 47.52 | 3.00× | 99.9% |
| 64 | `c8g.16xlarge` | **244 s** | 326 s | 2.5523 | 0.2311 | 63.44 | **4.00×** | **100.1%** |

Marginal scaling against ideal: **1.994× vs 2.00, 1.503× vs 1.50, 1.336× vs 1.33.** Every step is
within 0.3% of linear, and `avg_cores` confirms it independently — 99.0–99.7% of nominal at every
size. bowtie2 does not bend through 64 cores on this workload.

### Why $/run rises when scaling is perfect

Because the two effects are separable, and separating them is the whole result:

```
c8g per-core-hour price:   16 vCPU $0.03988    32 vCPU $0.03988
                           48 vCPU $0.03988    64 vCPU $0.03988    <- exactly flat

compute-only $/result:     16 cores $0.1732    32 cores $0.1737
                           48 cores $0.1733    64 cores $0.1730    <- invariant, 0.4% spread

fixed overhead:            16 cores 86 s -> $0.0152
                           32 cores 85 s -> $0.0301
                           48 cores 83 s -> $0.0441
                           64 cores 82 s -> $0.0581               <- same seconds, 3.8x the cost
```

Flat per-core pricing times ~100% efficiency means **the compute cost of the answer does not depend
on how many cores you rent.** The ~84 s of boot, image pull and staging is also essentially constant
— but at 64 cores you pay for those same seconds at 4× the hourly rate, and that difference *is* the
entire $/run spread.

**The practical reading, which is the opposite of the usual intuition:** 4× the speed costs **23%
more**, not 4× more. If you can amortise boot — several samples per box, or any longer job — faster
is close to free. If you genuinely run one sample per instance, 16 cores is 23% cheaper and 4×
slower, and that trade is a scheduling decision rather than a scaling one.

This also sharpens, rather than contradicts, [bwa-real](../bwa-real/README.md)'s finding that "the
cost knee is at 16 cores, not 64." The knee is real in $/run terms — but it is an artifact of fixed
overhead, not of the aligner running out of parallelism.

## The identity across chips and thread counts

**All eight runs — the clock plus four generations plus three core counts — produced identical
output statistics:**

| | |
|---|---|
| `primary_records` | **48,297,986** on every run = exactly 2 × 24,148,993 read pairs |
| `primary_mapped` | 48,198,738 |
| `primary_mapq30` | 40,042,364 |
| overall alignment rate | 99.79% |

`primary_records` is **asserted**, because it is fixed by the input: every read gets exactly one
primary record whether it aligns or not. A truncated run, a dropped mate or a silently skipped tile
fails that even while "% mapped" still looks entirely plausible — which a band on alignment rate
would wave through.

The mapped and MAPQ≥30 counts were deliberately **reported rather than asserted**, because
bowtie2's thread-count independence (it re-seeds its RNG per read) was a documented claim that no
run here had yet confirmed. All eight agree to the read, across four CPU generations *and* four
thread counts, so the claim now has evidence — and `n = 1` per rung is defensible on that basis
rather than on hope.

## Sizing, measured

`tmpfs_peak_mib` is **11,924 on all seven sweep rungs**, identical to the byte. The clocking run
reported 12,259 — the 335 MiB difference is precisely the gzipped MAPQ≥30 TSV that only the clock
writes, which is a satisfying internal consistency check on the instrument.

So `/tmp` needs ~11.7 GiB. Since tmpfs is half of instance RAM that means **≥ 23.4 GiB of RAM**, and
`c*.4xlarge` (32 GiB → 16 GiB tmpfs) fits with 33% headroom. The staged index tar (4.02 GiB) cannot
be deleted — it is a staged input, and the container would get `EPERM` on a sticky `/tmp` — so budget
for holding it alongside its own 4.02 GiB expansion.

`peak_rss_mib` grows with threads: **7,960 → 8,351 → 8,734 → 9,110** at 16/32/48/64, roughly 18 MiB
per additional core. Modest, and nowhere near the box at any size.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>

# once: build the index on bwa's exact reference (~22 min, ~$0.29)
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" 00-build-index.task.json > /tmp/b.json
spawn task run --spec /tmp/b.json --region us-west-2 --wait

for r in gen-c6g gen-c7g gen-c8g gen-c9g knee-32 knee-48 knee-64; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" $r.task.json > /tmp/r.json
  spawn task run --spec /tmp/r.json --region us-west-2 --wait
done
```

Serially. truffle's static price table has no Graviton coverage
([truffle#175](https://github.com/spore-host/truffle/issues/175)), so concurrent launches carrying a
`cost_limit` can be refused. `spawn task run` does not substitute `${COOKBOOK_BUCKET}`.

## Pins

| | |
|---|---|
| reads | `s3://1000genomes/phase3/data/HG00096/sequence_read/SRR062634_{1,2}.filt.fastq.gz` |
| reference | `GRCh38_full_analysis_set_plus_decoy_hla.fa`, 3,263,683,042 B (asserted in the build task) |
| index | built here; `bt2index.tar` 4,320,890,880 B, sha256 `e2d19a2d49f5b4244b797a017be287b8fc279d66611d4719566e182d39b4a5bc` |
| image | `quay.io/aarchbio/bowtie2@sha256:a6807f06…` (bowtie2 2.5.5) |

**Why the index is built rather than fetched.** 1000genomes publishes a prebuilt *bwa* index beside
this reference but no `.bt2`, and bowtie2's own published indexes are built on different references
(`GRCh38_noalt_as`, `_noalt_decoy_as`). Aligning to a different reference than bwa used would turn
the bwa↔bowtie2 comparison into a method difference wearing the clothes of a disagreement. The build
is 22 min once, and `bowtie2-inspect` reading the index back (3,366 sequences) is the gate — a
completion sentinel, not a size band.

**`--local`, not the default end-to-end.** bwa soft-clips and bowtie2's default does not; that
mismatch measured 82% agreement where `--local` on the mapped set gave 0.9462. Matching the modes is
the entire reason this index exists.

## Caveats

**n = 1 per rung.** Defensible because all eight runs agree on every output statistic to the read,
and because the generation steps and the scaling curve are each smooth. A rung landing off trend
would need repeating before it could be reported.

**One input, one library.** 24.1M 100 bp pairs of human whole-genome DNA. A different read length,
a smaller reference, or an RNA library would change the memory profile and could change the scaling
— 100% efficiency through 64 cores is a property of *this* workload, not a general claim about
bowtie2.

**Scaling was not tested beyond 64 cores.** The curve is still perfectly linear at 64, so the knee —
if there is one — is somewhere above what was measured. `c8g` goes to 192 vCPU.

**The bwa cross-check has not been run.** The clocking run staged out a 352 MB gzipped TSV of
MAPQ≥30 primary alignments specifically for it, and `bwa-real`'s BAM over the identical bytes already
exists, so the comparison is set up and cheap. It is not done, and nothing here claims the two
aligners agree. That is the next task, and its metric must be MAPQ-gated concordance on the
confident set rather than naive all-mapped agreement
([why](../../practices/cross-checks.md)).
