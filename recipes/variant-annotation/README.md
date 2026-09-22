---
tool: snpeff-bcftools-csq
tool_version: "SnpEff 5.4c / bcftools 1.24"
images:
  - quay.io/aarchbio/snpeff@sha256:2183d4c0c4a11d58aa6c4cc5939ca26359fa07ba8e444ac4b9de297257eccf3a
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Variant annotation — two annotators on a gene we designed

snpEff and `bcftools csq` annotate the same VCF against the same reference and GFF on Graviton4, both checked against consequences that were chosen before the variants existed. The catalog's first annotation recipe, for anyone downstream of a variant caller.

> **What this covers.** A 1 kb contig with one two-exon gene, five variants — synonymous, missense, nonsense, frameshift, splice-donor. Building a snpEff database from a GFF, `bcftools csq`, and reconciling their output. Not a real annotation database, ClinVar/dbNSFP, VEP plugins, or transcript prioritisation.

## Run it

```bash
snpEff build -c snpEff.config -gff3 -noCheckProtein -v chrA          # toy DB from your GFF
snpEff ann   -c snpEff.config -noStats -hgvs1LetterAa chrA variants.vcf > ann.vcf

bgzip -c genes.gff > genes.gff.gz
bcftools csq -f chrA.fa -g genes.gff.gz -l -Ov variants.vcf > csq.vcf   # the second annotator
```

Two tasks: snpEff builds the fixture and its database and annotates; bcftools then annotates the same bytes and the two are reconciled.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| toy contig + designed gene | your reference + a real GFF | for snpEff, prefer a **pre-built database** (`snpEff download GRCh38.105`) over building your own; build only for non-model organisms. |
| the hand-written GFF3 | your annotation | **both tools need Ensembl-style `ID=gene:`/`ID=transcript:` prefixes and correct CDS `phase`** — this is where custom-GFF annotation actually fails, see below. |
| five constructed variants | your caller's VCF (e.g. from [freebayes](../freebayes/README.md) or [gatk4](../gatk4/README.md)) | annotation is deterministic given a transcript model, so the interesting failures are all in the GFF, not the variants. |
| `-hgvs1LetterAa` | snpEff's default 3-letter HGVS | one-letter output is what makes the two tools mechanically comparable; see the conventions table. |

**Leave the fixture:** 40 codons let every consequence be read off the genetic code by hand, which is what makes these exact assertions rather than plausibility checks. **Scale it** to a real GFF when you need real transcripts — and expect the phase and ID-prefix problems below to be the whole difficulty.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m and 12m, caps $0.05 each. The bioconda snpEff wrapper pins `-Xmx1g`, so 4 GiB is ample; both tools finish in seconds and the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact consequences, two-annotator agreement, the notation problem, the junction-spanning codon, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture is a valid gene | 120 bp CDS, `ATG`…`TAA`, no internal stop, canonical `GT`..`AG` | **holds** |
| truth vs design | every truth ref-AA equals the designed protein at that codon | **holds** |
| snpEff CDS check | `OK: 1 / Errors: 0` — snpEff's CDS extracted from the GFF equals the constructed CDS | **1 / 0** |
| records annotated | 5, with no `ERROR_` codes | **5**, none |
| snpEff vs planted | consequence + codon + ref/alt AA **equal** the planted values | **exact** |
| bcftools vs planted | same | **exact** |
| the two annotators | identical after normalisation | **agree** |
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

| variant | snpEff | bcftools csq |
|---|---|---|
| synonymous | `synonymous_variant` / `p.L3L` | `synonymous` / `3L` |
| missense | `missense_variant` / `p.H8Y` | `missense` / `8H>8Y` |
| nonsense | `stop_gained` / `p.K24*` | `stop_gained` / `24K>24*` |
| frameshift | `frameshift_variant` / `p.T34fs` | `frameshift` / `33STVCYDG*>33SQFVMTA` |
| splice donor | `splice_donor_variant&intron_variant` / — | `splice_donor` / — |

The consequence terms differ by a `_variant` suffix and by how compound terms are joined; the protein notation is HGVS in one and a positional `ref>alt` in the other; synonymous changes carry no `>` at all on the bcftools side. So the recipe normalises both to `(consequence, codon, refAA, altAA)` and asserts on that — [the metric has to measure agreement](../../practices/cross-checks.md), and a raw `diff` here reports five disagreements that do not exist.

**The frameshift is where normalising stops being enough.** snpEff names codon **34** and stops (`p.T34fs`); bcftools starts at codon **33** and spells out the entire downstream retranslation. Both are correct, and no normalisation reconciles the positions — so the recipe asserts only the *consequence* for that variant and drops the position. What it asserts instead is stronger and free: bcftools' **reference** peptide from codon 33 must equal the designed protein's tail, `STVCYDG*`. That pins the reading frame the frameshift is measured against, which is the part that could actually be wrong.

### The junction-spanning codon, and why the CDS check is the load-bearing one

The two CDS exons are **59 bp and 61 bp**, not 60 and 60, so **codon 20 straddles the splice junction** — two bases in exon 1, one in exon 2 — and exon 2's CDS `phase` is therefore `1`, not `0`. That is deliberate: a 60/60 split makes every codon exon-local, and a GFF with the wrong phase annotates *correctly anyway*. With the split codon, a wrong phase shifts the entire second exon's reading frame.

`snpEff build` with a `cds.fa` is what catches it. snpEff extracts the CDS from the GFF itself and compares — so `OK: 1 / Errors: 0` asserts that snpEff's transcript model, phase and junction included, reproduces the CDS the gene was built from. Get the phase wrong and the build reports errors before any variant is annotated.

Two things about making that check work at all, both silent failures otherwise:

- **snpEff matches `cds.fa` on the database's transcript ID**, which keeps the Ensembl prefix — the header must read `>transcript:GENEA.1`. With `>GENEA.1` snpEff reports `Not found: 1` and a `FATAL ERROR`, which is at least loud.
- **bcftools csq silently annotates nothing** when it cannot parse the GFF: records come back with no `BCSQ` field and exit status 0. So the recipe counts records *carrying a consequence*, not records emitted — an exit code cannot tell these apart ([why](../../practices/container-path.md)).

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| snpEff | `quay.io/aarchbio/snpeff@sha256:2183d4c0…` (5.4c) |
| bcftools | `quay.io/aarchbio/bcftools@sha256:8171fe74…` — the same pin the [bcftools](../bcftools/README.md) recipe uses |
| input | none — contig, gene, GFF, VCF and the planted consequences are generated in-task by awk and bash from `srand(7)` |

The designed protein is written once by task 1 and staged to task 2, so the frameshift assertion and the truth table read the same source rather than two copies of it.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### Run + verify

```sh
make run RECIPE=variant-annotation
make ls  RECIPE=variant-annotation
```

Assertions are `test` calls inside both tasks. Expect `smoke-check.txt` with `bcftools_exact yes`, `annotators_agree yes`, `frameshift_ref_frame yes`, and `conventions.txt` holding the notation table above.

</details>
