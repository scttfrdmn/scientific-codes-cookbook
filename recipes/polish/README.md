---
tool: racon-medaka
tool_version: "racon 1.5.0 / medaka 2.2.2"
images:
  minimap2: quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
  racon: quay.io/aarchbio/racon@sha256:75020311bdb6a635ee67718984db28029e95ee2272e32de9d0def8a9826ed4ee
  medaka: quay.io/aarchbio/medaka@sha256:9389bbfdcd569497790eae188c825adf70f33236c284f16dbeeb016128a1015e
spawn_version: 0.121.0
last_verified: 2026-10-05
---
# Assembly polishing — two polishers, real nanopore reads, a published answer key

racon and medaka each rebuild a consensus from real Oxford Nanopore reads on Graviton4, scored against the depositors' own plasmid reference. For anyone finishing a long-read assembly and choosing a polisher.

## Run it

```bash
make stage RECIPE=polish                    # once: 165 ONT reads (120x) + the 6,361 bp reference
for s in $(make -s spec RECIPE=polish); do spawn task run --spec "$s"; done
make ls RECIPE=polish                       # score.tsv is the answer

minimap2 -x map-ont -t 2 draft.fa reads.fastq > ov.paf
racon -t 2 reads.fastq ov.paf draft.fa > polished-racon.fa
medaka_consensus -i reads.fastq -d draft.fa -o med -t 2 -m r1041_e82_400bps_hac_v6.0.0
```

Three tasks, one tool each: minimap2 aligns, racon polishes, medaka polishes and scores all three.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| planted-error draft | your assembly (e.g. from [flye](../flye/README.md)) | a real draft has no answer key, so the check becomes *directional* — fewer errors after than before. |
| `sample_01` / `barcode01` | any of the 192 samples in the dataset | `sample_sheet.csv` maps alias → barcode → reference; keep the triple consistent or you score against the wrong plasmid. |
| `r1041_e82_400bps_hac_v6.0.0` | the model matching **your** basecaller | medaka's accuracy depends on this more than on anything else here. `medaka tools list_models` lists what the image carries. |
| one racon round | two to four rounds | racon is normally iterated, re-aligning between rounds. Each round needs a fresh PAF. |

**Leave the fixture.** The errors are planted so the count is exact (21), but the *sequence*, the *reads* and the *truth* are all the depositors', so the reads carry a real basecaller error profile — which is the thing a polisher exists to remove. **Scale it** by swapping the draft for a real assembly when you want to measure how much polishing actually helps.

## Shape, size, cost

Three tasks: minimap2 and racon on `c8g.large` (2 vCPU / 4 GiB, caps $0.05), medaka on `c8g.xlarge` (4 vCPU / 8 GiB, cap $0.12) because it loads a PyTorch model. Measured: alignment and racon are each under a second, **medaka 14 s**. Windows are almost entirely image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: both reach zero errors, why racon's zero is weaker than medaka's, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match their pinned sha256 before anything derives from them | both match |
| reference length | 6,361 bp, and `sample_sheet.csv` independently says `approx_size 6361` | **6,361 / 6,361** |
| fixture | draft short by exactly the 3 planted deletions | 6,361 → **6,358** |
| draft ≠ truth | the fixture must actually plant something | differs |
| overlaps | ≥100 alignments (a floor, not an equality — see below) | **266 for 165 reads** |
| **draft error count** | **== the 21 planted (18 substitutions + 3 deletions)** | **21** |
| racon | fewer errors than the draft | **0** over the 6,350 bp it emitted |
| **medaka** | fewer errors than the draft | **0 at exactly 6,361 bp** |
| model | bundled in the image, nothing fetched at run time | `r1041_e82_400bps_hac_v6.0.0` |

**The draft scoring exactly 21 is the check that validates the scorer**, not just the fixture: an
independent edit-distance implementation recovered precisely the number of errors planted by an
awk script, so the metric is measuring what it claims to.

### Why racon's zero is weaker than medaka's, and the metric that hides it

A plasmid is circular, so a linear consensus can begin anywhere. Scoring therefore compares
against the **doubled** reference in `edlib`'s `HW` (infix) mode — without that, a correct
consensus with a different start point scores as thousands of errors.

But `HW` asks *"is the query an exact substring of the target"*, and **it does not penalise
truncation.** So:

- **medaka: 0 edits at 6,361 bp** — the full plasmid, exactly.
- **racon: 0 edits at 6,350 bp** — everything it emitted is right, and it is **11 bp short**.

Both read as "0 errors" and they are not the same result. The length column is what separates
them, which is why it is in the table rather than left implicit. On this fixture medaka recovered
the complete sequence and racon trimmed the ends; a check that only reported edit distance would
have called that a tie.

For reference, ONT's own pipeline reports a 6,361 bp assembly at mean quality **Q54.79** for this
sample, so full-length recovery is the expected outcome rather than an impressive one.

### 266 alignments for 165 reads is not a bug

Reads longer than the plasmid are real: a circular 6.4 kb construct read by a long-read
sequencer produces reads that wrap past the origin (observed maximum 12,697 bp), which minimap2
reports as supplementary alignments. Hence the assertion is a floor.

### Pins (data tier: RODA)

| | |
|---|---|
| reads | staged to `inputs/polish/reads.fastq` from ONT's `plasmid_2025.04` hac basecalls for `FBC24981` / `barcode01` — R10.4.1, `SQK-RBK114-96`. 165-read subsample, `8eeaa55e…` |
| truth | staged to `inputs/polish/truth.fa` from the same dataset's published per-sample full reference for `sample_01`, `84b52268…` |
| minimap2 | `@sha256:ef4a5fb7…` (2.31-r1302) |
| racon | `@sha256:75020311…` (1.5.0) |
| medaka | `@sha256:9389bbfd…` (2.2.2) |

The subsample's **own** hash is pinned, not the 112 MB source object's, because the subsample is
what the recipe depends on. Staging takes a range GET of the first 8 MB rather than the whole
file.

**cosign signs the manifest-list digest, not the per-architecture one.** So
`cosign verify quay.io/aarchbio/medaka@sha256:9389bbfd…` fails with `no signatures found`, which
looks alarming and means nothing — verify the **tag**, pin the **arm64 digest**. Verified here
against `playgroundlogic/aarchbio/.github/workflows/publish.yml@refs/heads/main`.

### Run + verify

```sh
make stage RECIPE=polish
for s in $(make -s spec RECIPE=polish); do spawn task run --spec "$s"; done
make ls RECIPE=polish
```

`--wait` is omitted deliberately: on spawn 0.121.0 it never reads the completion record and times
out at TTL on a task that succeeded ([spawn#715](https://github.com/spore-host/spawn/issues/715)).
Polling the bucket for the declared outputs is the workaround and is stronger anyway — it checks
artifacts rather than a status field ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

`nanopolish`, which is a **signal-level** tool: it needs raw fast5, and this dataset ships pod5
(as does all current ONT output), so it has no route here that does not involve converting 1.9 GB
files. Also not covered: multiple racon rounds, diploid/heterozygous polishing, and medaka's
variant mode.

</details>
