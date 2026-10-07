---
tool: metaphlan
tool_version: "4.2.5"
image: quay.io/aarchbio/metaphlan@sha256:7149c1bb1e23d01d273d56cc86502e36a2b976c598175327f6a32c42abfbbe48
spawn_version: 0.123.0
last_verified: 2026-10-07
---
# MetaPhlAn — who is in a defined mixture, checked against a closed species list

Profiles a shotgun metagenome against 23 GB of clade-specific markers on Graviton4, and scores the answer against a mock community whose species list is known in advance. For anyone doing taxonomic profiling on ARM.

## Run it

```bash
make stage RECIPE=metaphlan     # once: 3.3 GB markers + 23 GB index + 117 MB reads
spawn task run --spec "$(make -s spec RECIPE=metaphlan)" --wait
make ls RECIPE=metaphlan        # score.tsv is the answer

metaphlan reads_1.fastq.gz,reads_2.fastq.gz --input_type fastq \
  --db_dir db -x mpa_vJun23_CHOCOPhlAnSGB_202403 --offline \
  --nproc 16 --mapout mapout.bz2 -o profile.txt
```

Staging runs on a box, not your laptop — see below.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ERR15105294` (Zymo mock) | your metagenome | **must be `library_strategy=WGS`.** An AMPLICON library processes every read, exits 0 and reports *no species* — MetaPhlAn maps to clade-specific markers. Staging asserts this. |
| `mpa_vJun23_CHOCOPhlAnSGB_202403` | another published index | pin a **named** index, never `mpa_latest`. Sizes run 1.4 MB (TOY) to 6.0 GB; the matching bowtie2 index is the big artifact. |
| `--offline` | **keep it** | a recipe here may not fetch at run time, and it turns a wrong `--db_dir` into a loud failure instead of a silent 23 GB download. |
| the 8-species truth table | your own expected list | **this is the part worth copying** — a defined mixture has a *closed* species list, which is what makes presence assertable without a tolerance. |

**Leave the fixture.** 794,279 read pairs over ~40 Mb of combined genomes is ~6× — thin by WGS standards but ample for marker-based profiling, and it keeps a 23 GB-index run to minutes. **Scale it** with `--subsampling_paired N --subsampling_seed S` on a deeper run; `ERR12710526` is the same community at 39.3M reads.

## Shape, size, cost

One profile task on `r8g.4xlarge` (16 vCPU, **124 GiB**), TTL 50m, cap $0.90. The RAM is for **tmpfs, not compute**: `/tmp` is a tmpfs at half the instance RAM and must hold the 23 GB index tar — a staged input, so it can never be `rm`-ed — plus its 23 GB expansion. Measured **108 s** of MetaPhlAn on 16 threads inside a ~6.5 min window, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)). Staging is three one-off tasks: markers (3 min), index (26 min), reads (2 min).

<details>
<summary>As shipped: 8/8 present and nothing else, why the abundances are not asserted, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged tars | match their pinned sha256 | both match |
| database | ≥6 `.bt2l` files after untar | **6** |
| library strategy | `WGS`, asserted at staging | `WGS / PCR` |
| mate counts | R1 == R2, and 2× == ENA's own count | 794,279 == 794,279; **2×794,279 == 1,588,558** |
| reads processed | all of them | **1,588,558** |
| **Zymo bacteria present** | **all 8 detected** | **8/8** |
| **false positives** | **nothing unexpected above 1%** | **0** |
| species rows | the profile *is* the closed list | **8** |

```text
expected_species               zymo_dna_pct  detected_as                    metaphlan_pct  share_of_bact
Listeria_monocytogenes         12.0          Listeria_monocytogenes          5.2342          6.40
Pseudomonas_aeruginosa         12.0          Pseudomonas_aeruginosa         12.9881         15.87
Bacillus_subtilis              12.0          Bacillus_subtilis               0.0263          0.03
Escherichia_coli               12.0          Escherichia_coli               13.9227         17.01
Salmonella_enterica            12.0          Salmonella_enterica            17.5976         21.50
Limosilactobacillus_fermentum  12.0          Limosilactobacillus_fermentum   5.7190          6.99
Enterococcus_faecalis          12.0          Enterococcus_faecalis          14.1877         17.34
Staphylococcus_aureus          12.0          Staphylococcus_aureus          12.1669         14.87
Saccharomyces_cerevisiae        2.0          NOT_FOUND                       0.0000          0.00
Cryptococcus_neoformans         2.0          NOT_FOUND                       0.0000          0.00
```

**Presence is the assertion because a defined mixture has a closed species list.** Finding
exactly those eight and *nothing else above 1%* is a two-sided categorical claim — no band, no
tolerance — and it is self-validating: a profile containing precisely the expected set is strong
evidence the sample is that standard.

### Why the abundance column is reported and not asserted

Three separate reasons, and each would have made a band meaningless:

1. **The sample is not identified as D6300.** Its title is `15345.ZymoMockD.D7.P3`, not
   `ZymoBIOMICS_D6300`. The 12% figures come from D6300's datasheet, so comparing them to an
   unidentified Zymo mock — possibly a different product or a dilution — would be asserting a
   number that may not apply. The species *list* is what the presence check relies on.
2. **Different quantities.** Zymo's 12% is by **genomic DNA**; MetaPhlAn estimates relative
   abundance from marker coverage. Genome sizes differ, so the two are not the same measure even
   for a correctly profiled D6300.
3. ***Bacillus subtilis* at 0.03% is the one to look at.** Zymo's Bacillus has been reclassified
   between datasheet revisions (*subtilis* / *spizizenii*), and MetaPhlAn 4's SGB taxonomy may
   place those reads under a name the closed list did not anticipate. The truth table accepts
   both spellings and matched `Bacillus_subtilis`, so this is a real near-absence rather than a
   naming miss — worth investigating before anyone quotes it.

The two yeasts are expected to be absent: CHOCOPhlAn's SGB catalogue is bacteria/archaea/virus
centred and fungal coverage is limited. The scorer therefore fails only on missing **bacteria**,
by design.

### Pins (data tier: published source with its own checksums)

| | |
|---|---|
| markers | `mpa_vJun23_CHOCOPhlAnSGB_202403.tar`, 3.32 GB — published md5 `d985de75…`, staged sha256 `a3290140…` |
| bowtie2 index | `…_bt2.tar`, 22.99 GB — published md5 `8caae86b…` |
| reads | `ERR15105294_{1,2}.fastq.gz`, 117 MB — ENA per-file md5, plus ENA's own read count |
| image | `@sha256:7149c1bb…` (package 4.2.6, **binary reports 4.2.5**) |

A single unmirrored university HTTP host is acceptable here **because the depositors publish a
`.md5` beside every tarball** — the bytes are checkable at the source, not merely against
ourselves. The index is **fetched, not built**: `bowtie2-build` over the full marker set is hours
of CPU and would produce bytes nobody can verify, while the published index is what
`metaphlan --install` fetches anyway.

**The database is two artifacts.** The 3.32 GB tar is markers only — `.pkl`, SGB/VSG FASTAs,
`VINFO.csv` — and only the `.pkl` is needed at profile time. This is also where the "~20 GB"
figure in the catalog's queue came from: the index, not the download.

### Four traps, each of which cost a run

**MetaPhlAn 4.2.x renamed every flag.** `--bowtie2db`→`--db_dir`, `--index`→`-x`,
`--bowtie2out`→`--mapout`. The 4.0-era form fails *after* the 23 GB index has been unpacked. And
`-1`/`-2` belong to the subsampling path — they error out without `--subsampling_paired`, so
plain profiling takes the reads as one comma-joined positional argument.

**An AMPLICON library looks exactly like a broken database.** The first fixture here was
`ERR12736123` — same Zymo standard, same platform, chosen on `sample_title`. It is
`library_strategy=AMPLICON`. MetaPhlAn processed all 2,514,728 reads, exited 0, and reported *no
species*: the tool being correct about the wrong input. Staging now asserts `WGS`. Note the check
is on **strategy, not selection** — `library_selection=PCR` means the library prep amplified,
which is routine and still whole-genome.

**EBI is slow to us-west-2, and one host says nothing about another.** The 3.32 GB marker set came
from `unitn.it` at **18.2 MB/s**; EBI FASTQ to the same region ran at **0.8 MB/s** — slower than a
home connection. Generalising the first number to the second is what made a 2.97 GB fixture look
cheap. Hence a 117 MB fixture.

**`curl -C -` corrupts under retry.** A 1.55 GB fetch returned `rc=0` with the *right byte count*
and a *wrong md5*. Resume-across-retries produced a plausible-looking, corrupt file. The published
md5 is the gate; the byte count is not.

### Run + verify

```sh
make stage RECIPE=metaphlan
spawn task run --spec "$(make -s spec RECIPE=metaphlan)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/metaphlan/r1/score.tsv" -
```

The scorer runs inside the task and fails it on a missing bacterium or more than two unexpected
species, so `score.tsv` existing with 8/8 is the result — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Strain-level profiling (`--profile_vsc`, StrainPhlAn), long reads (`--long_reads`), HUMAnN
functional profiling — which this unblocks, since a present database stops MetaPhlAn emitting the
no-database warning that breaks HUMAnN's version probe — and the eukaryotic fraction.

</details>
