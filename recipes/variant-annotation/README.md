---
tool: snpeff-bcftools-vep
tool_version: "SnpEff 5.4c / bcftools 1.24 / VEP 116"
images:
  - quay.io/aarchbio/snpeff@sha256:2183d4c0c4a11d58aa6c4cc5939ca26359fa07ba8e444ac4b9de297257eccf3a
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
  - quay.io/aarchbio/ensembl-vep@sha256:48593f3f9f40cd15c77adcf68f83fb7ee00d7fa35849d449cece48a504c76e01
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Variant annotation — three annotators on a gene we designed

snpEff, `bcftools csq` and VEP annotate the same VCF against the same reference and GFF on Graviton4, all three checked against consequences chosen before the variants existed. The catalog's annotation recipe, for anyone downstream of a variant caller.

> **What this covers.** A 1 kb contig with one two-exon gene, five variants — synonymous, missense, nonsense, frameshift, splice-donor. A snpEff database built from a GFF, `bcftools csq`, VEP in offline GFF mode, and reconciling three notations. Not a real annotation database, ClinVar/dbNSFP, VEP plugins, or transcript prioritisation.

## Run it

```bash
snpEff build -c snpEff.config -gff3 -noCheckProtein -v chrA          # toy DB from your GFF
snpEff ann   -c snpEff.config -noStats -hgvs1LetterAa chrA variants.vcf > ann.vcf

bgzip -c genes.gff > genes.gff.gz
bcftools csq -f chrA.fa -g genes.gff.gz -l -Ov variants.vcf > csq.vcf   # the second annotator

tabix -p gff genes.sorted.gff.gz                                       # VEP wants it indexed
vep -i variants.vcf --gff genes.sorted.gff.gz --fasta chrA.fa --vcf -o vep.vcf --species custom
```

Three tasks, one per annotator: snpEff builds the fixture, its database and the truth table; bcftools and then VEP annotate the same bytes and each is reconciled against the truth and the others.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| toy contig + designed gene | your reference + a real GFF | for snpEff, prefer a **pre-built database** (`snpEff download GRCh38.105`) over building your own; build only for non-model organisms. |
| the hand-written GFF3 | your annotation | **both tools need Ensembl-style `ID=gene:`/`ID=transcript:` prefixes and correct CDS `phase`** — this is where custom-GFF annotation actually fails, see below. |
| five constructed variants | your caller's VCF (e.g. from [freebayes](../freebayes/README.md) or [gatk4](../gatk4/README.md)) | annotation is deterministic given a transcript model, so the interesting failures are all in the GFF, not the variants. |
| `-hgvs1LetterAa` | snpEff's default 3-letter HGVS | one-letter output is what makes the tools mechanically comparable; see the conventions table. |
| VEP `--gff` + `--fasta` | `--cache` or `--database` | **offline GFF mode needs no annotation cache at all**, which is why VEP is reachable here; a real run usually wants the species cache. |

**Leave the fixture:** 40 codons let every consequence be read off the genetic code by hand, which is what makes these exact assertions rather than plausibility checks. **Scale it** to a real GFF when you need real transcripts — and expect the phase and ID-prefix problems below to be the whole difficulty.

## Shape, size, cost

Three tasks. snpEff and bcftools on `c8g.large` (TTL 15m/12m, caps $0.05); **VEP on `c8g.xlarge` with `disk_gib: 40`** because its image is **4.68 GB** — by far the largest in the catalog, where the others are tens of MB. That pull *is* the runtime: VEP's task took **5m43s** end to end for seconds of annotation. The bioconda snpEff wrapper pins `-Xmx1g`, so 4 GiB is ample there. **These timings are not compute cost.**

<details>
<summary>As shipped: exact consequences, three-annotator agreement, the notation problem, which tool is the frameshift outlier, the junction-spanning codon, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture is a valid gene | 120 bp CDS, `ATG`…`TAA`, no internal stop, canonical `GT`..`AG` | **holds** |
| truth vs design | every truth ref-AA equals the designed protein at that codon | **holds** |
| snpEff CDS check | `OK: 1 / Errors: 0` — snpEff's CDS extracted from the GFF equals the constructed CDS | **1 / 0** |
| records annotated | 5, with no `ERROR_` codes | **5**, none |
| snpEff vs planted | consequence + codon + ref/alt AA **equal** the planted values | **exact** |
| bcftools vs planted | same | **exact** |
| VEP vs planted | same | **exact** |
| all three annotators | identical after normalisation | **agree** |
| frameshift codon | VEP == snpEff, bcftools differs | **34 / 34 / 33** |
| frameshift reference frame | bcftools' peptide from codon 33 equals the designed tail | **`STVCYDG*`** |

The planted consequences, read off the codons the gene was built from:

```text
v1_syn  chrA:108 G>A   codon  3  CTG->CTA   synonymous    L3L
v2_mis  chrA:121 C>T   codon  8  CAC->TAC   missense      H8Y
v5_spl  chrA:160 T>A   intron donor +2      splice_donor
v3_non  chrA:269 A>T   codon 24  AAA->TAA   stop_gained   K24*
v4_fs   chrA:298 TA>T  delete CDS base 100  frameshift
```

Every `REF` base is read out of the generated fasta rather than typed, so the VCF cannot disagree with the reference it was built from.

### The notation problem — the reason this needs normalising, not diffing

Both tools are right about all five variants. Almost nothing about how they *say* so matches:

| variant | snpEff | bcftools csq | VEP |
|---|---|---|---|
| synonymous | `synonymous_variant` / `p.L3L` | `synonymous` / `3L` | `synonymous_variant` / `3:L` |
| missense | `missense_variant` / `p.H8Y` | `missense` / `8H>8Y` | `missense_variant` / `8:H/Y` |
| nonsense | `stop_gained` / `p.K24*` | `stop_gained` / `24K>24*` | `stop_gained` / `24:K/*` |
| frameshift | `frameshift_variant` / `p.T34fs` | `frameshift` / `33STVCYDG*>33SQFVMTA` | `frameshift_variant` / `34:T/X` |
| splice donor | `splice_donor_variant&intron_variant` / — | `splice_donor` / — | `splice_donor_variant` / — |

The consequence terms differ by a `_variant` suffix and by how compound terms are joined; the protein notation is HGVS in one and a positional `ref>alt` in the other; synonymous changes carry no `>` at all on the bcftools side. So the recipe normalises both to `(consequence, codon, refAA, altAA)` and asserts on that — [the metric has to measure agreement](../../practices/cross-checks.md), and a raw `diff` here reports five disagreements that do not exist.

**The frameshift is where normalising stops being enough.** snpEff names codon **34** and stops (`p.T34fs`); bcftools starts at codon **33** and spells out the entire downstream retranslation; VEP names **34** and writes `T/X`, where `X` means "frameshifted, unknown" — not an amino acid, so it is excluded from the comparison rather than matched against a letter. No normalisation reconciles the positions, so the recipe asserts only the *consequence* there and drops the position. What it asserts instead is stronger and free: bcftools' **reference** peptide from codon 33 must equal the designed protein's tail, `STVCYDG*`. That pins the reading frame the frameshift is measured against, which is the part that could actually be wrong.

**And the third annotator settles something two could not.** With snpEff and bcftools alone the page could only say the frameshift positions were irreconcilable — it could not say which was unusual. VEP agrees with snpEff on **34**, so bcftools is the outlier: two tools name the first *affected* codon, one starts at the last *intact* one. Majority is not correctness, and none of the three is wrong — but it tells you which convention to expect by default and which to special-case, and that is a different and more useful thing than "they disagree". The recipe asserts `VEP == snpEff` and asserts bcftools **differs**, so a future convention change surfaces here rather than in someone's merged output.

### The junction-spanning codon, and why the CDS check is the load-bearing one

The two CDS exons are **59 bp and 61 bp**, not 60 and 60, so **codon 20 straddles the splice junction** — two bases in exon 1, one in exon 2 — and exon 2's CDS `phase` is therefore `1`, not `0`. That is deliberate: a 60/60 split makes every codon exon-local, and a GFF with the wrong phase annotates *correctly anyway*. With the split codon, a wrong phase shifts the entire second exon's reading frame.

`snpEff build` with a `cds.fa` is what catches it. snpEff extracts the CDS from the GFF itself and compares — so `OK: 1 / Errors: 0` asserts that snpEff's transcript model, phase and junction included, reproduces the CDS the gene was built from. Get the phase wrong and the build reports errors before any variant is annotated.

Two things about making that check work at all, both silent failures otherwise:

- **snpEff matches `cds.fa` on the database's transcript ID**, which keeps the Ensembl prefix — the header must read `>transcript:GENEA.1`. With `>GENEA.1` snpEff reports `Not found: 1` and a `FATAL ERROR`, which is at least loud.
- **bcftools csq silently annotates nothing** when it cannot parse the GFF: records come back with no `BCSQ` field and exit status 0. So the recipe counts records *carrying a consequence*, not records emitted — an exit code cannot tell these apart ([why](../../practices/container-path.md)). **VEP behaves the same way** and is counted the same way.
- **VEP's `--gff` needs the GFF sorted by position**, bgzipped and tabix-indexed. The file this recipe writes is grouped by feature (gene, mRNA, exons, CDSs), which is valid GFF3 and not position-sorted, so `sort -k1,1 -k4,4n` is required before `bgzip`. The Ensembl-style `ID=gene:`/`ID=transcript:` prefixes VEP wants are the same ones bcftools needs, which is the only reason one GFF serves all three.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| snpEff | `quay.io/aarchbio/snpeff@sha256:2183d4c0…` (5.4c) |
| bcftools | `quay.io/aarchbio/bcftools@sha256:8171fe74…` — the same pin the [bcftools](../bcftools/README.md) recipe uses |
| VEP | `quay.io/aarchbio/ensembl-vep@sha256:48593f3f…` (116.2; reports `ensembl 116.0d85231`) — **4.68 GB** |
| input | none — contig, gene, GFF, VCF and the planted consequences are generated in-task by awk and bash from `srand(7)` |

The designed protein is written once by task 1 and staged to task 2, so the frameshift assertion and the truth table read the same source rather than two copies of it.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### Run + verify

```sh
make run RECIPE=variant-annotation
make ls  RECIPE=variant-annotation
```

Assertions are `test` calls inside all three tasks. Expect `smoke-check.txt` with `bcftools_exact yes`, `annotators_agree yes`, `frameshift_ref_frame yes`, and `smoke-check-vep.txt` with `three_way_agreement yes` and `frameshift_codon snpEff 34 / VEP 34 / bcftools 33`. The notation tables are in `conventions.txt` and `conventions3.txt`.

</details>
