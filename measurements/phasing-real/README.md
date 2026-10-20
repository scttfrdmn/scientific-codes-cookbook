# WhatsHap on a whole chromosome of HG002: HiFi and Illumina, scored against GIAB's phased truth, three chips

> **Every het call on HG002's chr20 (54,401), phased by WhatsHap from 49× PacBio HiFi and from 69×
> Illumina 2×250, then scored against GIAB's two published phasings.** HiFi phases 99.9% of the
> 53,448 hets WhatsHap can use, with a switch error of 0.10%. That is about the rate at which GIAB's
> two truth sets disagree with each other (0.085%). Illumina phases 79%, in blocks a median of 409 bp
> long, with a switch error of 0.41%. Both phased VCFs are byte-identical on Graviton4, Intel and
> AMD. AMD is fastest (1.5×) and cheapest per result. Each chip ran for under 2.5 cents.

Workload: **all of chr20 for one sample**. That is one unit of a WhatsHap whole-genome job, because
`whatshap phase` is single-threaded and parallelises by chromosome. The autosomes are 22 of these.
- **Sample:** HG002, the GIAB Ashkenazi son, chosen because its phase is known from the trio.
- **Genotypes to phase:** the GIAB NISTv4.2.1 GRCh38 benchmark calls, all 85,951 chr20 records,
  with the phase stripped (`whatshap unphase`). That leaves 54,401 hets. Phasing truth calls
  measures phasing alone, without calling errors mixed in. It is the usual way phasers are benchmarked.
- **Reads:** the GIAB PacBio HiFi Revio 48× BAM (191,592 reads, mean 16.7 kb, 49.2× on chr20).
  Also NIST's Illumina HiSeq 2×250 novoalign BAM (18.3M reads, 68.9× on chr20). Both are aligned to GRCh38.
- **Truth:** two phased supplements of the same release. `hifiasm_v11_phasetransfer` is phase
  transferred from a trio-binned hifiasm assembly (54,038 phased hets). `StrandSeqANDTrio` is a
  conservative Strand-seq-plus-trio phasing (37,867). Both are paternal|maternal.

**Data tier: RODA plus GIAB's own FTP, md5-checked against the publisher.**
- The VCFs come from `s3://giab` (RODA) and are copied server-side into the bucket.
  [`prep.sh`](prep.sh) checks hifiasm's md5 against GIAB's `md5.in` and records sha256 for all three.
- The reads are cut to chr20 over HTTPS through the publisher's own `.bai`. Each index's md5 is
  checked against GIAB's README before it is used.
- **The Illumina BAM comes from NCBI's GIAB FTP, not RODA.** The RODA copy is a different file:
  150.3 GB against NCBI's 130.8 GB, its `.bai` md5 is not the published one, and its index puts
  32,870,310 records on chr20 against 18,289,203. That matches GIAB's February 2019 note that the
  earlier file carried merged-in duplicate reads (`results/prep/ilmn.roda.idxstats`).
- Every staged file is pinned in [`pins.sha256`](pins.sha256), and both tasks check them before running.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/phasing-real/stage-inputs.sh "$B"     # VCFs -> bucket; 00-prep cuts chr20 on AWS, once
N=$(date +%s)
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py phasing $a "$B" "$N" \
    measurements/phasing-real/01-phase.task.json)"
done
bash measurements/phasing-real/collect.sh "$B" "$N"     # results/<chip>/, results/crosschip.txt
```

[`phase.sh`](phase.sh) runs the recipe's invocation twice, side by side, one core each:

```sh
whatshap unphase bench.chr20.vcf.gz > input.vcf
whatshap phase --reference chr20.fa -o hifi.vcf.gz input.vcf hifi.chr20.bam
whatshap phase --ignore-read-groups --reference chr20.fa -o ilmn.vcf.gz input.vcf ilmn.chr20.bam
whatshap compare --ignore-sample-name --names hifiasm,trio_strandseq,hifi,illumina \
  --tsv-pairwise compare.tsv hifiasm.chr20.vcf.gz sst.chr20.vcf.gz hifi.vcf.gz ilmn.vcf.gz
```

The specs come from [`mkspec.py`](mkspec.py). [`check.py`](check.py) holds the structural checks.
The x86 legs run bioconda's WhatsHap 2.8, which is the recipe's `images_x86_64`.

## Results

One `xlarge` (4 vCPU, 16 GiB) per chip. The two `whatshap phase` processes run concurrently, one
thread each. Same image digest per architecture, same staged bytes:

| chip | instance | HiFi phase | Illumina phase | tool wall | billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.xlarge` | 190 s | 325 s | 341 s | 429 s | 3.14 GB | 0.1795 | 0.0170 | **0.0214** |
| Xeon 6975P-C | `m8i.xlarge` | 179 s | 298 s | 312 s | 385 s | 3.31 GB | 0.2117 | 0.0183 | **0.0226** |
| **EPYC 9R45** | `m8a.xlarge` | **134 s** | **217 s** | **225 s** | **292 s** | 3.31 GB | 0.2434 | **0.0152** | **0.0197** |

- *Tool wall* is the timed chain: unphase, the two concurrent phasings (the longer one counts),
  `stats` and `compare`. *Billed* runs from launch to terminate, from spawn's completion record.
  $/hr is spawn's price for the instance. A result is the whole chromosome phased twice and scored.
- Peak RSS is WhatsHap's own `Maximum memory usage` (getrusage) for the Illumina run. The HiFi run
  peaks at 0.57 GB on every chip.
- **AMD is fastest and cheapest.** It is 1.52× Graviton4 on the tool wall and 11% cheaper per
  result in compute (8% billed), even though its hour costs 36% more.
- **Intel is 1.09× Graviton4 and the dearest per result.** Its 4 vCPU are 2 cores with SMT
  (2 threads per core; 1 on Graviton4 and AMD). Each whatshap process still gets its own core.
- **The gap is in reading the BAM, not in phasing.** WhatsHap's own timers give the HiFi run
  54 / 39 / 29 s reading on Graviton4 / Intel / AMD, and 110 / 112 / 89 s phasing. The Illumina run
  reads 18.3M records, and reading is 60% of its time on Graviton4.
- **The box is sized by staging, not compute.** The 4.2 GB of BAMs land in `/tmp`, which is a
  tmpfs of half the RAM. `m8g.xlarge` gives 8 GiB of that, where `c8g.xlarge` would give 4 GiB.
  4.0 GiB was used.
- **Overhead is 19–23% of each bill.** Boot and staging 4.2 GB take 67–88 s. These timings are not
  compute cost.
- The one-time prep (`00-prep`, `m8g.2xlarge`) cut both chr20 slices in 30 s each, over HTTPS from
  us-east-1 and NCBI: 4 m 55 s billed, $0.029.
- n = 1 per chip. WhatsHap is deterministic here (`PYTHONHASHSEED=0` pins the one hash it uses, an
  id for supplementary alignments), and both outputs are byte-identical across chips, so a repeat
  would only re-measure timing.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| sha256 of every staged input == pins; .bai and hifiasm md5 == GIAB's | provenance | ok | ok | ok |
| `chr20.fa` M5 == GRCh38 chr20 (`b18e6c53…`); each slice holds every record the GIAB index puts on chr20 | right reference, nothing dropped | 201,925 HiFi / 18,289,203 Illumina | (prep, once) | |
| both truths: same records as the benchmark, same genotypes ignoring phase | phase transfer changes only phase | 0 / 0 differ | 0 | 0 |
| both outputs: same records as the input, in order; genotypes unchanged ignoring phase | WhatsHap phases, it does not re-call | 0 / 0 | 0 | 0 |
| no homozygous or missing call phased or given a PS | only hets carry phase | 0 | 0 | 0 |
| every phased het has an integer PS, at or before its block's first phased variant, at an input het's position | WhatsHap's block naming | 0 violations | 0 | 0 |
| phased hets == `whatshap stats` phased + singletons; block list == PS sets | the two tools count the same thing | 53,385 / 42,182 | same | same |
| every pairwise score unchanged when every phased genotype is flipped | phase is defined only up to a flip | identical | identical | identical |
| different genotypes vs either truth | same calls on both sides | 0 | 0 | 0 |
| switch error rate vs either truth ≤ 0.05 | a phasing with no information scores 0.5 | 0.10% / 0.41% | same | same |
| HiFi median block (bp) > Illumina's | read length sets reach | 91,234 > 409 | same | same |
| WhatsHap's core `.so` architecture == `uname -m` | not emulated | aarch64 | x86_64 | x86_64 |

47 checks per chip, 0 failed (`results/<chip>/checks.tsv`). The 0.05 bound is a band, set ten times
below an uninformative phasing, so it catches a broken run (wrong sample, wrong reference) rather
than noise. Everything else is exact.

Scores against the truth, from `results/arm64/compare.tsv`:

| | hets phased | blocks | median block | switch error vs hifiasm | vs StrandSeqANDTrio | block-wise Hamming vs hifiasm |
|---|---|---|---|---|---|---|
| **HiFi** 49× | 53,385 (99.9% of 53,448 usable) | 214 | 91.2 kb, 68 variants | **0.102%** (54 of 52,873) | 0.085% (32 of 37,599) | 0.081% |
| **Illumina** 69× | 42,182 (78.9%) | 10,338 | 409 bp, 3 variants | **0.410%** (130 of 31,722) | 0.434% (87 of 20,041) | 0.464% |
| hifiasm vs StrandSeqANDTrio | — | — | — | 0.085% (32 of 37,690) | — | 0.048% |

Observed, not asserted:
- **HiFi's error is at the level of the truth.** WhatsHap-from-HiFi disagrees with each GIAB phasing
  about as often as the two disagree with each other. Below about 0.1% this benchmark cannot rank
  a phaser.
- **Read length is the whole game, at scale.** On the same calls, Illumina links 3 variants per
  block where HiFi links 68, and it phases 11,200 fewer hets. Its 2×250 pairs on a 350 bp insert
  reach about one insert. HiFi's largest block holds 2,223 variants over 1.71 Mb, and its blocks
  have an NG50 of 359 kb. Illumina's NG50 is 0, because its blocks cover only 6.2 Mb of chr20.
- **Ten Illumina phase sets span over 1 Mb (up to 45.5 Mb) with 2–32 variants each.** These are
  pieces joined by a single read pair mapped far apart. Oriented piece by piece against hifiasm, 7
  of the 10 joins are right. That is not distinguishable from chance at n = 10, so treat a
  short-read phase set's span as noise. `whatshap stats` measures such a set by its pieces (largest
  Illumina block 17.5 kb), which is why the table uses its numbers.
- **A PS need not be one of its block's own variants.** In 7 of 10,338 Illumina blocks the PS names
  the connected component's first variant, and that variant itself ended up unphased. The PS is
  still an input het's position, at or before the block, which is what is asserted.
- WhatsHap selects 33,511 of 146,954 HiFi reads (its default 15× cap) and 150,591 of 2,013,903
  Illumina reads. Only 610,932 Illumina reads cover two hets, so 70% of the short reads carry no
  phase information at all.

## Across chips: what is a fixed point and what is a path

**Everything is a fixed point here** (`results/crosschip.txt`):
- the record body of both phased VCFs (md5 `a6e359c4…` HiFi, `d359c291…` Illumina);
- every read count WhatsHap logs;
- every block statistic;
- all 66 pairwise scores.

These come from two builds: aarch.bio's on Python 3.12 / pysam 0.24.0, bioconda's on Python 3.9 /
pysam 0.23.2. WhatsHap's read selection and MEC solver are integer and combinatorial, with no
floating point that could take a different path, so the same bytes on every chip is the expected
result and not a coincidence. Intel and AMD each agree with Graviton4 on every recorded value.

## Caveats

- One chromosome of one sample. chr20 is about 2% of the genome, so read the costs as one shard of
  a whole-genome job, not the whole job. Chromosomes differ in het density and repeat content, so
  scaling by length is an estimate, not a measurement.
- Phasing truth genotypes isolates phasing from calling. With your own calls, genotype errors add
  switch errors that this benchmark does not see.
- The first arm64 run failed two checks in `check.py`. Both were assumptions about WhatsHap that
  the real data disproved: that a PS is always one of its block's variants, and that span
  measures a short-read block. The two bullets above record what is actually true. The checks were
  corrected, and all three chips then ran the corrected spec.
