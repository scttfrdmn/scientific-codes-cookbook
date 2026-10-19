# Variant annotation on a real cohort — snpEff, bcftools csq and VEP on three chips

> **All 187,611 coding-SNV (record, transcript) pairs that the three annotators share are
> accounted for on every chip.** 96.1% agree on every field. Every other pair is one of three
> conventions, and each convention is checked against Ensembl's published peptides. Every
> annotated VCF is byte-identical as text across Graviton4, Intel and AMD. One verified run costs
> $0.22–0.27 billed. AMD is the fastest (1.66× Graviton4) and the cheapest.

Workload: the **sites of chromosomes 20, 21 and 22 of the 1000 Genomes Phase 3 release** (all
2,504 samples' variants: 4,021,926 records, 4,044,736 after splitting multiallelics). They are
annotated against **Ensembl GRCh37 release 87**, the release GRCh37's gene set froze at: 13,993
genes and transcripts on those chromosomes, and 5,128 published peptides. A cohort's sites, by
chromosome, against a complete gene set is the job annotation pipelines scatter. This is the
same input [bcftools-real](../bcftools-real/README.md) uses, from the same staged bytes.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/bcftools-real/stage-inputs.sh "$B"            # the VCFs (shared with plink-real) and hs37d5 chr20-22
bash measurements/variant-annotation-real/stage-inputs.sh "$B"  # 00-stage fetches Ensembl 87 on a spawn host
for a in arm64 x86_64 amd; do
  for t in 01-csq 02-snpeff 03-vep; do                          # in order: each reads the one before
    spawn task run --wait --spec "$(python3 scripts/spec_arch.py variant-annotation $a "$B" "$(date +%s)" \
      measurements/variant-annotation-real/$t.task.json)"
  done
done
bash measurements/variant-annotation-real/collect.sh "$B"       # results/<chip>/, results/compare.txt
```

`mkspec.py` writes the four specs. Each task checks every staged input and script against
`pins.sha256` before it runs. What the three annotators run:

```sh
bcftools view -G | bcftools norm -m -any -f hs37d5.chr20-22.fa      # sites.vcf.gz, records numbered v1..vN
bcftools csq -l -f hs37d5.chr20-22.fa -g genes.gff3.gz sites.vcf.gz
snpEff -Xmx6g build -gff3 -v GRCh37e87                              # genes.gff, sequences.fa, cds.fa, protein.fa
snpEff -Xmx6g ann -hgvs1LetterAa GRCh37e87 sites.vcf.gz
vep -i sites.vcf.gz --vcf --gff genes.vep.gff3.gz --fasta ref.fa --species custom --fork 16
```

Records are numbered because the release repeats some records verbatim, so neither the rsID nor
`CHROM:POS:REF:ALT` is a key.

## Results

Each chip runs the same three tasks on the same staged bytes: bcftools (prep + csq) and snpEff on
an `xlarge` (4 vCPU, 8 GiB), and VEP on a `4xlarge` (16 vCPU, 32 GiB, `--fork 16`). A result is
the whole verified chain, three instances.

| chip | instances | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.xlarge` ×2, `c8g.4xlarge` | 1,541 s | 2,180 s | 1.00 GiB (snpEff JVM) | 0.1595 / 0.6381 | 0.1657 | 0.2303 |
| Xeon 6975P-C | `c8i.xlarge` ×2, `c8i.4xlarge` | 1,344 s | 2,362 s | 1.26 GiB | 0.1874 / 0.7497 | 0.1627 | 0.2707 |
| **EPYC 9R45** | `c8a.xlarge` ×2, `c8a.4xlarge` | **927 s** | **1,745 s** | 1.19 GiB | 0.2155 / 0.8622 | **0.1306** | **0.2196** |

| seconds | sites prep (bcftools) | `bcftools csq` | snpEff build ×2 | `snpEff ann` | VEP, 16 forks |
|---|---|---|---|---|---|
| Graviton4 | 301 | 6 | 34 | 467 | 729 |
| Xeon | 272 | 7 | 38 | 433 | 592 |
| EPYC | 204 | 5 | 30 | 270 | 417 |

- **`bcftools csq` is 50–120× faster than the other two.** It annotates 4.04M records in 5–7 s,
  single-threaded. snpEff takes 4.5–8 minutes, also single-threaded. VEP takes 7–12 minutes with
  16 forks: about 5,500 variants/s on Graviton4 and 9,700 on AMD. VEP is 76–78% of every chip's
  compute bill.
- **AMD is the fastest on every step**: 1.75× Graviton4 on VEP and 1.73× on snpEff. Its hour is
  35% dearer, and it is still 21% cheaper in compute and 5% cheaper billed. Intel is 1.23× faster
  than Graviton4 on VEP, and its 8 SMT cores (2 threads per core) run VEP's 16 forks. It is
  2% cheaper in compute and 18% dearer billed.
- **Overhead is 29–47% of each bill.** Each of three boxes boots and pulls its image, and VEP's
  image is the largest in the catalog. The snpEff task's overhead was ~8 minutes on both x86 chips
  against 3.4 on Graviton4 (cause not measured). These timings are not compute cost.
- **Memory is not the constraint.** The largest process was snpEff's JVM at 1.0–1.3 GiB. Each VEP
  fork peaked at ~150 MiB, and csq at under 30 MiB. Peak RSS is the largest per-process `VmHWM`;
  the containers' cgroup `memory.peak` (which includes page cache) was 1.5–2.0 GiB. Staging peaked
  at 1.0 GiB of tmpfs; work files (≤6.1 GiB) go on the container disk.
- n = 1 per chip. All three tools are deterministic. Every output is identical across chips
  (below), so a repeat would only re-measure timing noise.

## What held at scale, on all three chips

`results/<chip>/*/checks.tsv` has 30 checks per chip, both sides of each. All of them passed on all
three chips.

| claim | why it must hold | observed |
|---|---|---|
| staged bytes == pinned sha256, scripts included | provenance | ok |
| split records == ALT alleles in the release | one record per allele | 4,044,736 |
| each annotator emits every record, IDs in the same order (md5 of the ID column) | nothing dropped or reordered | ×3 |
| VEP's own `Variants processed` == sites | VEP's tally | 4,044,736 |
| snpEff: records without an `ANN` == symbolic records left unannotated | its own `Number_of_variants` agrees | 2,284, all `<CN*>` |
| snpEff build: transcripts whose translation ≠ Ensembl's peptide | published truth, whole transcripts | **0** |
| snpEff build: protein-coding transcripts with no Ensembl peptide | the database has the published gene set | **0** |
| csq and VEP annotate exactly the same coding-SNV pairs | same transcript model | 187,611 each |
| snpEff's pairs beyond those are all ones naming no amino acid | VEP calls the same 27 `incomplete_terminal_codon_variant` | 27 |
| every annotator's ref residue == Ensembl's peptide at that position, except the off-by-one below | published truth, per pair | 180,974 snpEff, 180,974 csq, **187,611 VEP** |
| every three-way disagreement is one of the three conventions below | nothing unexplained | **0 unexplained** |

### Where three correct annotators disagree, and who agrees with Ensembl

Of the 187,611 shared pairs, 180,337 agree on class, codon, ref and alt. The remaining 7,274 are
three conventions. Each is recognised from **Ensembl's published peptide**, not from any tool:

| convention | pairs | what happens | which one matches Ensembl |
|---|---|---|---|
| CDS starts mid-codon (peptide begins with `X`) | 7,262 on 332 transcripts | snpEff and csq number from the first *complete* codon; VEP counts the partial one, so it is one higher on the whole transcript | **VEP**: 0 mismatches. snpEff and csq: 6,637 each, all on these transcripts |
| codon 1 changes | 10 | same residues, but VEP calls `start_lost` and snpEff/csq call `missense` (or `stop_gained`). 9 of the 10 transcripts do not start with M | naming |
| stop codon inside the CDS | 2 | Ensembl's peptide also has `*` there; csq names a stop-to-stop change `synonymous` where the others say `stop_retained` | naming |

So "the tools agree" is true of 96.1% of pairs. On transcripts with a 5′-partial codon, pick the
tool by the numbering you need. Positions from snpEff or csq are one less than the peptide
Ensembl publishes for that transcript. Every pair either agrees exactly or falls into one of these
three cases on each chip, and that is asserted, so a version that changes a convention fails here.

**snpEff is given the GFF3 with its types renamed.** Ensembl's GFF3 types transcripts by kind
(`NMD_transcript_variant`, `processed_transcript`, `lincRNA`, …: 14 transcript types and 9 gene
types). `02-snpeff` renames the type column of every `ID=transcript:` line to `mRNA` and every
`ID=gene:` line to `gene`. Coordinates, IDs and `biotype=` are unchanged, and `gff.types.tsv` has the
tally. The task also builds from the GFF3 as published, for comparison. Built that way, 9,693
transcript objects pass the protein check, against 10,256 renamed (0 errors either way), and the CDS
check finds 12,180 transcripts with no CDS record against 16,226. Only the renamed database is used
to annotate, and its coding pairs equal csq's and VEP's. Whether the database built from the GFF3 as
published would have lost pairs was not measured.

## Across chips: all of it is a fixed point

Annotation is integer and string work on a fixed transcript model, so nothing here is a path.
`results/compare.txt` compares, Graviton4 against Intel and Graviton4 against AMD:

- `sites.vcf.gz`, `csq.vcf.gz`, `snpeff.vcf.gz` and `vep.vcf.gz` record text (everything below the
  `##` header) is **identical** on all three chips, about 4M records each.
- So are all three pair tables and every value in `compare.tsv`.

That holds although Graviton4's bcftools links htslib 1.23.1 and the x86 image links 1.24, and the
two snpEff and VEP images are different builds (aarch.bio on Graviton4, bioconda on x86). Nothing
here links OpenBLAS.

## Pins

| | |
|---|---|
| VCFs | `s3://1000genomes/release/20130502/ALL.chr{20,21,22}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` (RODA), sha256 `4a0b61d6…`, `086fc3a2…`, `5d52145d…`; read from `inputs/plink-real/` |
| reference | `hs37d5.chr20-22.fa`, sha256 `13cf156f…`, cut from RODA `hs37d5.fa.gz` by [bcftools-real](../bcftools-real/README.md); read from `inputs/bcftools-real/` |
| annotation | `https://ftp.ensembl.org/pub/grch37/release-87/`: `gff3/homo_sapiens/Homo_sapiens.GRCh37.87.chromosome.{20,21,22}.gff3.gz` (sha256 `7bd2b8ab…`, `dd3431f9…`, `abd51dfd…`), `fasta/homo_sapiens/cds/Homo_sapiens.GRCh37.cds.all.fa.gz` (`9e1c17ba…`) and `…/pep/Homo_sapiens.GRCh37.pep.all.fa.gz` (`fb273bd8…`). Fetched on a spawn host by `00-stage`, checked there against Ensembl's `CHECKSUMS` |
| scripts | `csq.sh`, `snpeff.sh`, `vep.sh`, `lib.sh`, `pairs.awk`, `compare.awk`: all in `pins.sha256` |
| images | the recipe's: snpEff 5.4c, bcftools 1.24, VEP 116.2. `quay.io/aarchbio/*` on Graviton4, `quay.io/biocontainers/*` on Intel and AMD (`recipes/variant-annotation` frontmatter) |
