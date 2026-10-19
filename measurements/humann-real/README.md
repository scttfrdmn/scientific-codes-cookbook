# HUMAnN on a whole published mock-community run: three chips

> **One whole 17.6M-read sample costs about a dollar on every chip, and the 117,726 species-level
> gene-family values are identical on all three.** AMD is 1.45× faster than Graviton4 and 3% cheaper
> per sample. Intel is as fast as Graviton4 and 19% dearer. The values that differ all come from the
> translated search, and its builds differ between the images.

Workload: `ERR2984773`, the **whole** Illumina shotgun run of the ZymoBIOMICS D6300 even standard
from Nicholls et al. 2019 (GigaScience, [PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)),
8,785,731 MiSeq 2×151 pairs. HUMAnN 3.9 profiles it against its **full** databases (ChocoPhlAn
v201901_v31 and UniRef90 v201901b, 49.2 GiB unpacked), guided by MetaPhlAn's profile of the same reads.
Researchers run HUMAnN on 5–20M-read samples, one sample per invocation, so this is one sample at a
typical depth. It is also a defined mixture, so the species HUMAnN stratifies by can be checked
against a closed list.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/metaphlan-real/stage-inputs.sh "$B"   # the reads, and the MetaPhlAn profile from its run
bash measurements/humann-real/stage-inputs.sh "$B"      # the profile -> inputs/; 00-dbs streams both databases
spawn task run --wait --spec "$(python3 scripts/spec_arch.py humann arm64 "$B" "$(date +%s)" \
  measurements/humann-real/01-canary.task.json)"         # 200k pairs; writes db.sha256, pinned in pins.sha256
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py humann $a "$B" "$(date +%s)" \
    measurements/humann-real/01-humann.task.json)"
done
bash measurements/humann-real/collect.sh "$B" ~/.xtmp/humann-cmp   # results/<chip>/, results/compare.txt
```

`01-humann` runs `run.sh` (pinned by sha256, like every script and input in `pins.sha256`). It joins
the two mates into one file, as HUMAnN expects, and then runs:

```sh
humann --input ERR2984773.fastq --output hout --output-basename ERR2984773 --threads 16 \
  --taxonomic-profile ERR2984773.profile.txt --metaphlan /tmp/shim \
  --nucleotide-database db/chocophlan --protein-database db/uniref
```

`--metaphlan /tmp/shim` puts a stand-in `metaphlan` first on `PATH`. It answers `--version` with
the version line last and refuses any other call. HUMAnN 3.9 parses the last line of
`metaphlan --version`, and MetaPhlAn 4.2.5 prints a database status line there, so without the shim
the only way past the check is `--bypass-prescreen`
([biobakery/humann#80](https://github.com/biobakery/humann/issues/80)). That flag also builds the
nucleotide index from all of ChocoPhlAn and ignores the profile. With the shim, the profile picks
**25 pangenomes**, and the refusal proves MetaPhlAn was never run. The x86 image ships MetaPhlAn
4.1.1, which prints no status line, so its check would pass without the shim.

## Results

16 vCPU (`r8*.4xlarge`, 128 GiB). The RAM holds the 51 GiB of databases in the staging tmpfs (½ RAM,
[data movement](../../patterns/data-movement.md)). `--threads 16`, the same staged bytes. HUMAnN 3.9
is aarch.bio's build on Graviton4 (diamond 2.2.8, bowtie2 2.5.5) and bioconda's on the other two
(diamond 2.0.15, bowtie2 2.2.3, the copies the noarch package bundles; see the
[recipe](../../recipes/humann/README.md)).

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 3,785 s | 4,284 s | 16.7 GiB | 0.9426 | 0.9911 | 1.1217 |
| Xeon 6975P-C | `r8i.4xlarge` | 3,758 s | 4,336 s | 16.5 GiB | 1.1114 | 1.1603 | 1.3386 |
| **EPYC 9R45** | `r8a.4xlarge` | **2,602 s** | **3,071 s** | 16.5 GiB | 1.2781 | **0.9239** | **1.0903** |

| chip | cores × threads | busy cores | index build | bowtie2 | diamond | Python post-processing |
|---|---|---|---|---|---|---|
| Graviton4 | 16 × 1 | 8.1 | 1,153 s | 249 s | 1,815 s | 539 s |
| Xeon 6975P-C | 8 × 2 | 9.6 | 961 s | 283 s | 2,011 s | 476 s |
| EPYC 9R45 | 16 × 1 | 8.4 | 827 s | 189 s | 1,203 s | 362 s |

- **AMD is 1.45× Graviton4** end to end and 1.51× on diamond. At 36% more per hour, that makes it
  3% cheaper per sample billed. **Intel's 8 SMT cores match Graviton4's 16** to within 1%, and at 18%
  more per hour Intel is 19% dearer. That is the same ranking as
  [metaphlan-real](../metaphlan-real/README.md), on the same reads.
- **This compares builds as well as chips.** Graviton4 ran diamond 2.2.8 and the x86 chips 2.0.15
  ([iqtree-real](../iqtree-real/README.md) has the same caveat). Intel against AMD is the one
  like-for-like chip comparison.
- **38–46% of the wall time runs on one core.** HUMAnN 3.9 calls `bowtie2-build` with no `--threads`
  (`bowtie2_build_opts=[]`, and no flag to set it), so indexing the 25-pangenome custom database
  takes 827–1,153 s on one core. HUMAnN's own Python steps are serial too. That is why 16 vCPUs average
  8–10 busy. The index build depends on the profile, not the depth: it took 1,160 s on the 400k-read
  canary too. Extra cores only shorten bowtie2 and diamond.
- **Overhead is 468–577 s** per task. About 4.5 min of that is staging the 51 GiB of databases (12,774
  files). That is 12–13% of each bill, and it is paid again for every task.
- **Peak RSS is 16.5–16.7 GiB** (`ru_maxrss`, the largest single process). The 128 GiB box is
  for the tmpfs, not for the processes.
- n = 1 per cell. The tool's own numbers on the nucleotide tier are identical across chips (below),
  so a repeat would change the timings. It could also move a few translated-tier values, as the
  Intel/AMD comparison shows.

## What held at scale, on all three chips

`check.py` runs inside the task and fails it on any failed check. None is a band.

| claim | why it must hold | Graviton4 | Intel / AMD |
|---|---|---|---|
| `UNMAPPED` == reads × HUMAnN's final unaligned % | every read is either assigned or counted unmapped | 5,569,932 of 17,571,462 | 5,538,368 |
| every family == the sum of its strata | the table is a tree | 119,726 families, worst 2e-10 | 120,806, worst 2e-10 |
| every stratified family has a community row, and the reverse | same | 0 orphans | 0 |
| unaligned: translated ≤ nucleotide ≤ 100% | the translated search only takes reads the nucleotide search left | 31.70 ≤ 35.47 | 31.52 ≤ 35.47 |
| custom database == the profile's species | the profile was used | 25 pangenomes | 25 |
| every stratum is one of those species | stratification comes from the guided search | 24 of 24 | 24 of 24 |
| **every Zymo bacterium recruits gene families** | closed list | **8/8** | **8/8** |
| pathway coverage in [0, 1]; `UNMAPPED`, `UNINTEGRATED` present | HUMAnN's accounting rows | 2,251 rows | 2,257 rows |
| database files == `db.sha256` | the bytes the run used | 12,774 files, 0 mismatches | same |

Each tolerance is the rounding of HUMAnN's printed values: half a unit in the last printed place
per term.

Observed, not asserted (`species.tsv`, share of species-stratified RPK):
- **The eight bacteria get 7.6–22.1% each**, against 12% each by genomic DNA. *E. coli* has the most
  (22.1%) and *L. fermentum* the least (7.6%). *S. cerevisiae* gets 0.65%. *C. neoformans* gets none,
  because MetaPhlAn did not detect it, so it has no pangenome in the custom database.
- ***B. subtilis* gets 8.1% here** after MetaPhlAn called it at 0.055%, about 200× under. The 0.01%
  prescreen threshold still selects its pangenome, and the reads then map to its genes. Don't
  infer a species' functional share from its MetaPhlAn abundance.
- 64.5% of reads are explained by the nucleotide tier, and a further 3.8% by the translated tier.

## Across chips: what is a fixed point and what is a path

**The nucleotide tier is identical, value for value.** All 117,726 species-stratified gene-family
rows match exactly on all three chips (RPK sum 29,404,907.9932). So does the nucleotide unaligned
fraction, `35.4730414578%`. That holds even though Graviton4 ran bowtie2 2.5.5 and x86 ran 2.2.3.

**The translated tier differs, and the image explains most of it.** Graviton4's diamond 2.2.8 gives
36,151 `unclassified` families against 38,023 for x86's 2.0.15 (470 only on Graviton4, 2,342 only
on x86), and 31,564 more unmapped reads. Those 470 + 2,342 families account for every row-set
difference in the tables, and 6 pathways appear only on x86. Expect this from any change of
diamond version. It is not evidence about the chip.

**Intel against AMD (same image, same diamond) is nearly identical.** The row sets, `UNMAPPED`, and
the pathway coverage table all match exactly. 6 of 38,023 translated-tier families differ, by up to
0.64% (all low-abundance `unclassified`, RPK ≈ 4–5). The pathway `UNINTEGRATED` sums differ in about
the 16th significant digit, which looks like summation order. Neither was tested on a repeat run, so
it is open whether a chip or the run caused these. Compare HUMAnN tables by row, not by bytes, and
don't expect `unclassified` values to repeat to the last digit.

HUMAnN is search-and-count, not BLAS-bound, so the [OpenBLAS kernel question](../../patterns/cross-architecture.md)
does not arise.

## Caveats

- **Paired reads from `fasterq-dump --split-files` need unique IDs before you concatenate them.**
  Both mates come out as `@ERR2984773.1 …`. HUMAnN keys reads by the first word of the ID, so it
  merges the two mates. Measured on the 200k-pair canary, nucleotide tier only: unique `/1` `/2` IDs
  gave `UNMAPPED` 228,931 and a community RPK sum of 539,235. The raw IDs gave 130,474 and 377,084,
  **30% less RPK**, and printed 65.2% unaligned instead of 57.2%. Nothing fails. `run.sh` adds the suffixes.
- The databases come from the bioBakery server (`humann_databases`'s URLs, versioned filenames).
  That server is not RODA or a DOI, so the pin is the tarballs' sha256 (in `pins.sha256`, printed
  by `00-dbs`) plus a per-file manifest of the unpacked tree. The server streams without a
  `Content-Length`, so `fetch_dbs.py` checks the byte count against the length its HEAD reports. The
  first `00-dbs` run reported that as a failure after a complete, CRC-checked download, and the
  script was corrected afterwards.
- The Graviton4 run was launched with a cost cap of $6.00 and a TTL of 10 h, before any whole-sample
  run had been timed. The committed spec is retightened from that run ($3.00, 5 h), and the x86 runs
  used it (spec_arch scales the cap).
- One sample. Per-sample cost is what should transfer, but the index build scales with how many
  species the profile selects. A gut sample with 100+ species will build a much bigger custom
  database than these 25.
- Prices are on-demand us-west-2, as spawn printed them on these instances.
