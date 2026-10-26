# StringTie on a whole RNA-seq run — Graviton4, Intel and AMD

> **The same assemblies on all three chips, to the last printed digit: 28,999 transcripts de novo,
> 85,818 guided.** Every de novo intron is one a read spans. gffcompare's intron-level score and
> `=` count are recomputed independently and match. StringTie's thread count changes the file
> but not the assembly. AMD is fastest and cheapest; the job costs about $0.05 per sample.

Workload: the **complete** Geuvadis run `ERR188026` (15,800,127 read pairs), aligned by HISAT2
`--dta` to the **whole** Ensembl 116 GRCh38 primary assembly, assembled by StringTie de novo and
guided by the **whole** Ensembl 116 annotation (646,577 transcripts), and scored with gffcompare
against that annotation. One sample is the unit the HISAT2/StringTie protocol assembles before
`--merge`, so this is the real per-sample step at its real size. These are the same reads as
[salmon-real](../salmon-real/README.md), [star-real](../star-real/README.md) and
[rnaseq-counts-real](../rnaseq-counts-real/README.md).

**Data tier: durable public ids, plus one built intermediate.** The reads are pinned by ENA's
`fastq_md5`, the genome and GTF by Ensembl release-116's `CHECKSUMS`, all checked on the box
([`results/align/pins.txt`](results/align/pins.txt)). The HISAT2 index and the sorted BAM are
built once on Graviton4 (tasks 00 and 01), pinned by sha256 in [`inputs.sha256`](inputs.sha256)
and the index in [`results/align/index.sha256`](results/align/index.sha256). Every StringTie task
checks `inputs.sha256` before it runs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/transcript-assembly-real/stage-inputs.sh "$B"     # scripts + pins; the data is already staged
for s in 00-hisat2-align 01-sort; do                                 # once, Graviton4
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py transcript-assembly arm64 "$B" r1 measurements/transcript-assembly-real/$s.task.json)"
done
for a in arm64 x86_64 amd; do                                        # each 03 reads its own chip's 02
  for s in $(python3 scripts/spec_arch.py transcript-assembly $a "$B" r3 \
      measurements/transcript-assembly-real/02-stringtie.task.json measurements/transcript-assembly-real/03-gffcompare.task.json); do
    spawn task run --wait --spec "$s"
  done
done
bash measurements/transcript-assembly-real/collect.sh "$B" r3 r3    # results/<chip>/, results/crosschip.txt
```

```sh
hisat2-build -p 16 genome.fa GRCh38_116
hisat2 -p 16 --dta --reorder -x GRCh38_116 -1 r1.fq.gz -2 r2.fq.gz -S aln.sam
samtools sort -@ 8 -o sample.bam aln.sam && samtools index sample.bam
stringtie -p 8 -l STRG -o denovo.gtf sample.bam                     # de novo
stringtie -p 8 -l STRG -G GRCh38.116.gtf -o guided.gtf sample.bam   # reference-guided
gffcompare -r GRCh38.116.gtf -o gc_denovo denovo.gtf                # and -R, for expressed-only sensitivity
```

The scripts ([`align.sh`](align.sh), [`sort.sh`](sort.sh), [`stringtie.sh`](stringtie.sh),
[`check_st.py`](check_st.py), [`gffcompare.sh`](gffcompare.sh), [`timed.py`](timed.py)) are staged
as files. [`mkspec.py`](mkspec.py) writes the four specs and pins those files by sha256.

## Results

Same image digests on each chip: aarch.bio's StringTie 3.0.3 and gffcompare 0.12.10 on Graviton4,
and bioconda's x86 builds of the same versions on both x86 chips. Same staged bytes. StringTie ran
on a `2xlarge` (8 vCPU, 32 GiB) at 8 threads and again at 1 thread, in both modes, in one task.
gffcompare is single-threaded and ran on an `xlarge` (4 vCPU, 16 GiB).

| step | chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| StringTie de novo `-p 8` | Graviton4 | `m8g.2xlarge` | 48.6 s | 454 s | 0.09 GiB | 0.3590 | 0.0048 | 0.0453 |
| | Xeon 6975P-C | `m8i.2xlarge` | 40.3 s | 526 s | 0.09 GiB | 0.4234 | 0.0047 | 0.0619 |
| | EPYC 9R45 | `m8a.2xlarge` | **27.2 s** | **325 s** | 0.09 GiB | 0.4869 | **0.0037** | **0.0440** |
| StringTie guided `-p 8` | Graviton4 | same box | 100.6 s | — | 1.24 GiB | | 0.0100 | |
| | Xeon | same box | 138.0 s | — | 1.24 GiB | | 0.0162 | |
| | EPYC | same box | **63.4 s** | — | 1.24 GiB | | **0.0086** | |
| StringTie `-p 1`, de novo / guided | Graviton4 | same box | 60.0 / 122.8 s | — | 0.04 / 1.04 GiB | | | |
| | Xeon | same box | 48.0 / 156.2 s | — | | | | |
| | EPYC | same box | 34.6 / 77.2 s | — | | | | |
| gffcompare (de novo) | Graviton4 | `m8g.xlarge` | 43 s | 242 s | 6.3 GB† | 0.1795 | 0.0021 | 0.0121 |
| | Xeon | `m8i.xlarge` | 84 s | 407 s | 6.3 GB† | 0.2117 | 0.0049 | 0.0239 |
| | EPYC | `m8a.xlarge` | **27 s** | **171 s** | 6.3 GB† | 0.2434 | **0.0018** | **0.0116** |

A *result* is one sample's assembly, checked. *Billed* is spawn's task window, wrapper start to stage-out done, at
spawn's printed price. The StringTie window covers all four assemblies and the checks, and the gffcompare
window covers four comparisons (both modes, with and without `-R`). *Peak RSS* is `ru_maxrss` of
StringTie itself. † gffcompare has no `ru_maxrss` here, so that column is the container's
`memory.peak`, which includes page cache for the uncompressed annotation. Threads per core: 1 on
Graviton4 and AMD, 2 on Intel, so `m8i.2xlarge` is four cores. Neither tool links a BLAS. The
shared alignment (Graviton4 only): `hisat2-build` 520 s at 6.2 GiB and alignment 133 s at 4.5 GiB
on `m8g.4xlarge`, $0.156 billed, 93.68% overall alignment rate. Sorting and junction extraction
took 55 s on `r8g.2xlarge`, $0.021 billed.

- **The boot is most of the bill.** 27–49 s of de novo assembly sits inside a 325–526 s window.
  Per sample, the chips differ by under 2 cents.
- **AMD is fastest at every step:** 1.8× Graviton4 de novo, 1.6× guided, 1.6× for gffcompare.
  That makes it cheapest per result, despite the dearest hour.
- **Intel's ordering flips by mode.** It beats Graviton4 de novo (40 vs 49 s) and loses guided
  (138 vs 101 s), and the single-thread runs show the same split, so SMT is not the explanation.
  What in the guided path is slower on this chip is not established here.
- **Threads buy little.** `-p 8` is 1.1–1.3× faster than `-p 1` on every chip and in both modes.
  For a cohort, run samples in parallel at a few threads each, rather than one sample at many.
- **Memory doesn't constrain StringTie:** 0.09 GiB de novo, 1.24 GiB guided, which is the
  annotation it holds. The staged 1.9 GB BAM and the uncompressed GTF size the box.
- n = 1 per cell. The assemblies are identical on every chip and at both thread counts, so a
  repeat re-measures only timing. Two earlier Graviton4 runs gave the same canonical sha256 for
  all four assemblies.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| reads / genome / GTF | md5 / `sum` / sha256 == published pin | yes |
| hisat2 pairs | == ENA `read_count` | 15,800,127 |
| BAM primary records | **== 2 × input pairs** | 31,600,254 |
| transcripts (de novo / guided) | structure valid: span == exon span, exons sorted and disjoint, within chromosome, multi-exon stranded | 28,999 / 85,818, all valid |
| **de novo introns spanned by no read** | **0** (every intron is a CIGAR `N` in the BAM) | 0 of 116,972 |
| TPM vs cov, FPKM vs cov | **one constant per assembly, every transcript, within print rounding** | yes |
| **FPKM/cov, de novo vs guided** | **equal** (it is a property of the BAM, not of the assembly) | 0.451816409387 vs …382 (1.2e-11) |
| **`-p 8` vs `-p 1`** | **same transcripts, same cov/FPKM/TPM** (ids and order set aside) | identical, both modes |
| gffcompare tmap rows / query mRNAs / class-code sum | **== transcripts** | 28,999 / 85,818 |
| gffcompare query introns / reference introns | **== independent count** | 116,972 / 698,582 |
| **gffcompare intron sensitivity / precision** | **== recomputed from the independent counts** | de novo 16.3 / 97.6, guided 17.8 / 98.4 |
| **gffcompare `=` on multi-exon transcripts** | **== independent intron-chain matches** | 12,330 / 64,307 |
| TPM sum | observation | 995,439.9 de novo / 978,157.8 guided |
| guided introns spanned by no read | observation | 0 of 126,568 |
| gffcompare intron chain sens / prec, de novo | observation | 2.0 / 45.1 (`-R`: 3.2 / 45.1) |
| gffcompare base sens / prec, guided | observation | 32.2 / 98.8 (`-R`: 57.7 / 98.8) |

- **De novo StringTie invents no junctions.** All 116,972 distinct introns it built are among
  the 148,635 distinct junctions some read's CIGAR spans. 97.6% are annotated. The other 2,757 are
  read-supported and unannotated, which is what de novo assembly is for.
- **StringTie's thread count changes the file, not the assembly.** At `-p 8` the record order and
  the `STRG` numbering follow which bundle finishes first, so the GTF's sha256 differs between
  runs. Put in a canonical form (ids dropped, records sorted), `-p 8` and `-p 1` are identical,
  coverage and expression included. So compare StringTie outputs by content, never with `diff`.
- **TPMs don't sum to 1e6, and the identity is the constant instead.** Every transcript's TPM is
  `cov × 1e6 / C`, with C = 1,306,215 de novo. That is larger than the 1,300,259 total cov of
  the transcripts printed, so the normaliser includes coverage that is not in the output. The
  sum is reported, not asserted.
- **gffcompare and an independent count agree only after applying gffcompare's own rule for
  short introns.** gclib treats a gap of 4 bp or less as no intron at all (`GFF_MIN_INTRON = 4`,
  `gff.h`). Ensembl 116 has 57 such gaps. Two of them are the 1 bp gaps that annotate the +1
  ribosomal frameshift in OAZ1 (`ENST00000582888`) and OAZ2 (`ENST00000326005`). An assembled
  transcript across either gap is `=` to gffcompare, but under a naive intron chain it has no
  match. With the rule applied, both reference intron counts (698,582) and both `=` counts agree
  exactly.
- **Single-sample sensitivity is low, as it should be.** One lymphoblastoid sample at 15.8M pairs
  expresses a fraction of 646,577 annotated transcripts, so base sensitivity against the whole
  annotation is 15.4% de novo. `-R` restricts the denominator to expressed loci and raises it to
  30.7%. Precision is the informative number for one sample: 95.6% de novo, 98.8% guided.

## Across chips

- **Identical on all three.** All four assemblies have the same canonical sha256 on Graviton4,
  Intel and AMD, coverage, FPKM and TPM included to their six printed decimals
  ([`results/crosschip.txt`](results/crosschip.txt)). The `-p 1` GTFs are byte-identical, too (same
  S3 multipart ETag, i.e. the same MD5 for every part). Every line of gffcompare's report and
  class-code table is identical. That holds across two builds of each tool (aarch.bio arm64,
  bioconda x86). StringTie's coverage is single precision, but no transcript or printed value
  moved, so nothing here is a path that diverges by chip. It is asserted (`collect.sh`).

## Caveats

One sample, one aligner, one annotation. A STAR alignment, or HISAT2 with a splice-site index,
gives different BAM bytes and a different assembly. The alignment is a pinned input, built once
on Graviton4, not measured across chips. Instance size and generation were not swept. Spend for
this write-up: **$0.49**, including two Graviton4 iterations of the StringTie and gffcompare
checks.
