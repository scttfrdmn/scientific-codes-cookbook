---
tool: vg
tool_version: "1.76.1"
images:
  - quay.io/aarchbio/vg@sha256:0c9767627faac4a98586fe99ffebb4e701903deb8cd5983ff12746344e8b0ccf
  - quay.io/aarchbio/odgi@sha256:51b2b70d3162e83453f95626932cfee54f10f5c63f679d41f6572371d1a1755d
spawn_version: 0.123.0
last_verified: 2026-10-09
---
# vg + odgi — a variation graph that spells its reference back exactly

Builds a pangenome graph from chr20 plus 82,818 real variants on Graviton4, then checks the reference path reproduces the input FASTA character for character. For anyone moving from a linear reference to graphs on ARM.

## Run it

```bash
# nothing new to stage -- reuses the chr20 reference and GIAB VCF from bwa and gatk4
for s in $(make -s spec RECIPE=pangenome); do spawn task run --spec "$s" --wait; done
make ls RECIPE=pangenome

vg construct -r chr20.fa -v truth.chr20.vcf.gz -R chr20 -t 8 > chr20.vg
vg paths -F -x chr20.vg          # must spell chr20.fa back, base for base
vg view -g chr20.vg > chr20.gfa
odgi build -g chr20.gfa -o chr20.og && odgi stats -i chr20.og -S
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `-R chr20` | your region, or drop it | restricts construction to one contig. Whole-genome from a population VCF is a much bigger graph — build per chromosome and join. |
| GIAB truth VCF | your cohort VCF | **more samples means a denser graph, not a bigger reference.** 82,818 variants added 91,701 bases to a 64 Mb reference. |
| `vg view -g` | `vg convert` | **odgi reads GFA, not vg's native format.** GFA is the interchange format between every graph tool here. |
| `vg paths -F` | — | **this is the part worth copying** — extracting the reference path and diffing it against your FASTA is a free, exact check that construction was lossless. |
| `vg construct` | `minigraph-cactus`, `pggb` | construct is reference-plus-VCF. True multi-assembly pangenomes need `seqwish`/`wfmash`, both published in the same namespace. |

**Leave the fixture.** chr20 plus a real truth set is 2.2M nodes — big enough that a construction bug shows up, small enough to build in minutes. **Scale it** once the identity passes; the check is size-independent.

## Shape, size, cost

Two tasks on `m8g.2xlarge` / `m8g.xlarge`, TTL 90m and 45m as backstops, caps $0.40 and $0.20. Construction dominates; the GFA export and odgi conversion are seconds.

<details>
<summary>As shipped: a byte-exact reference path, a cross-tool structural check, and what each one proves</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| reference bases | matches the staged bwa fixture | **64,444,167** |
| input variants | > 50,000 | 82,818 |
| paths in graph | — | 1 |
| nodes / edges | — | 2,206,602 / 2,290,966 |
| graph total length | — | 64,535,868 (+91,701 alt bases) |
| **reference path length** | **== input length, exactly** | **64,444,167** |
| **reference path sha256** | **== input sha256, exactly** | **`2442701218d3b2f7…`** |
| GFA segments | == `vg stats` node count | 2,206,602 |
| **odgi total length** | **== vg total length** | **64,535,868** |
| **odgi nodes / edges** | **== vg's** | 2,206,602 / 2,290,966 |

### The identity: the graph still contains the genome

A variation graph embeds the reference as a named path. Walking that path must reproduce the
input FASTA **character for character**, because the graph is a lossless re-encoding of the
linear reference plus alternatives. A construction that dropped a node, mis-ordered a segment or
corrupted a base fails this.

Asserted as a **sha256 over the base string**, with both sides normalised identically — headers
removed, newlines removed, uppercased — so what is compared is sequence, not FASTA formatting:

```text
input   chr20.fa reference path   2442701218d3b2f7e2d7cacd78774e1628fd48a7a13b58bee53ab8b7cb3d1af4
graph   vg paths -F               2442701218d3b2f7e2d7cacd78774e1628fd48a7a13b58bee53ab8b7cb3d1af4
```

No tolerance, no band, and it needs no external truth — the graph is checked against the thing
it was built from. 82,818 variants added **91,701 bases** of alternate sequence, so the graph is
larger than the reference while still containing it unchanged.

### The cross-tool check, and precisely what it proves

odgi is an unrelated codebase with its own graph representation. Converting vg's GFA and
recovering the **same total sequence length** is two implementations agreeing about what the
graph contains.

**Total length is the strong claim; node count is the weaker one.** Sequence content is not a
representational choice, but where you cut that sequence into nodes is — and odgi *honours the
segmentation declared in the GFA* rather than re-deriving its own. So the node and edge agreement
shows the two tools read a graph the same way; it does not show they would independently pick the
same boundaries. Both are asserted, with the distinction stated rather than blurred.

The GFA export is verified before odgi sees it: segment count must equal the node count
`vg stats` reported, so a truncated export fails at the boundary rather than downstream.

### One parsing hazard, because it recurs across unrelated tools

`odgi stats -S` writes `#length  nodes  edges  paths  steps` — the leading `#` is part of the
**first column's name**. A lookup for `length` misses, awk returns field 0, and `$0` is the whole
line, so a comparison silently pits a line against a number. The same convention appears in
cfgrib's output and in PLINK 2's `#CHROM ID REF ALT`.

Strip it when building a header map, and **guard the parse**: this recipe fails with "the header
lookup missed" if a field that should be numeric is not, rather than reporting a tool
disagreement that does not exist.

### Pins

| | |
|---|---|
| reference | `inputs/bwa-samtools/chr20.fa` — 64,444,167 bases, asserted |
| variants | `inputs/gatk4/truth.chr20.vcf.gz` — GIAB HG001 v4.2.1, 82,818 records |
| images | vg `@sha256:0c976762…` (1.76.1), odgi `@sha256:51b2b70d…` (0.9.4) |

**Nothing new is staged.** Both inputs are already verified by other recipes in this catalog —
the reference by [bwa](../bwa-samtools/README.md), the VCF by [gatk4](../gatk4/README.md) and
[clair3](../clair3/README.md) — so a graph built from them inherits that provenance.

Both images cosign-verified; signatures cover the **manifest-list** digest, so verify the tag and
pin the arm64 digest.

### Run + verify

```sh
for s in $(make -s spec RECIPE=pangenome); do spawn task run --spec "$s" --wait; done
aws s3 cp "s3://$(make -s print-bucket)/runs/pangenome/r1/score.tsv" -
```

Fails if the reference is not the staged fixture, the reference path differs from the input by a
single base, the GFA segment count disagrees with vg's node count, or odgi and vg disagree on
what the graph contains — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Read mapping onto the graph** — `vg giraffe` against the staged chr20 BAM, MAPQ-gated against
bwa's linear alignments, is the obvious next check and the one that would make this a caller-grade
comparison. Also: `vg autoindex`/`giraffe` index construction, graph-aware variant calling
(`vg call`), `odgi viz`/`odgi sort` layout operations, and true multi-assembly pangenomes via
`seqwish` and `wfmash` (both published in the same namespace).

</details>
