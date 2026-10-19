# HOMER on a real workload: a whole ENCODE CTCF peak set, genome-wide, on three chips

> **AMD is 1.10× faster than Graviton4 and 22% dearer per result; Intel is 0.82× and 41% dearer.**
> Every output computed from fixed input is byte-identical on all three chips, the whole
> known-motif table included. Only HOMER's random background differs, and it differs as much
> between two Graviton4 runs as between chips.

Workload: **all 44,217** conservative IDR peaks of ENCODE's CTCF ChIP-seq in GM12878
(`ENCFF796WRU`, experiment `ENCSR000DZN`) against **all of GRCh38**: `findMotifsGenome.pl`, summit
±100 bp, known and de novo, against HOMER's own GC-matched genomic background. This is the standard
ChIP-seq motif run on a whole experiment's published peak set. It is not a chromosome or a top-N
slice. The shipped [recipe](../../recipes/homer/README.md) runs 1,390 chr20 peaks.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
bash stage-inputs.sh "$COOKBOOK_BUCKET"     # server-side copy from encode-public; reuses minimap2-real's GRCh38
for arch in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 ../../scripts/spec_arch.py homer $arch "$COOKBOOK_BUCKET" "$(date +%s)" whole.task.json)" \
    --region us-west-2 --wait
done
bash collect.sh "$COOKBOOK_BUCKET"          # results/<chip>/ and results/compare.txt
```

`homer-real.sh` is the task, staged as a sha256-pinned file. It runs three `findMotifsGenome.pl`
calls on the same bytes:

- **genome:** what a user runs, with known and de novo motifs against a random GC/CpG-matched
  genomic background.
- **offset:** the control. The same regions moved 100 kb, known motifs, random background.
- **vsoffset:** the peaks with the offset set as an explicit `-bg`. Nothing is drawn at random,
  so the whole output is a function of fixed input.

`mkspec.py` writes `whole.task.json` and a chr21 `canary.task.json`.

## Results

16 vCPU and 64 GiB (`m*.4xlarge`), `-p 16`, the same staged bytes. aarch.bio's image on Graviton4,
bioconda's on the two x86 chips:

| chip | instance | genome + offset + vsoffset | tool wall | billed | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 752 + 192 + 137 s | 1,081 s | 1,152 s | 0.7181 | **0.2156** | **0.2298** |
| Xeon 6975P-C | `m8i.4xlarge` | 882 + 241 + 188 s | 1,311 s | 1,376 s | 0.8467 | 0.3083 | 0.3236 |
| EPYC 9R45 | `m8a.4xlarge` | **680 + 175 + 129 s** | **984 s** | **1,040 s** | 0.9738 | 0.2662 | 0.2813 |

| chip | threads per core | busy cores (genome run) | cgroup `memory.peak` | `/tmp` high-water |
|---|---|---|---|---|
| Graviton4 | 1 | 5.9 | 4.41 GiB | 5.1 GiB |
| Xeon 6975P-C | 2 (`cpu0` siblings `0,8`) | 7.3 | 4.41 GiB | 5.1 GiB |
| EPYC 9R45 | 1 | 6.2 | 4.41 GiB | 5.2 GiB |

A result is one whole peak set through all three runs. Billed is spawn's Started to Ended window,
priced at spawn's own `cost_estimate`.

- **AMD is 1.10× Graviton4 and Intel 0.82×.** Per hour, AMD costs 36% more and Intel 18% more, so
  Graviton4 is cheapest per result: AMD is 23% dearer in compute and 22% dearer billed, Intel 43%
  and 41%.
- **HOMER uses about 6 of 16 cores** at `-p 16`. The preparse, the sequence extraction and the
  Perl driver are serial, and only the homer2 scans fan out. A smaller box is likely cheaper per
  result, but that is unmeasured: this run held the size fixed so that the chips are comparable.
- **Memory and staging are set by the genome, not the chip.** `memory.peak` (anon plus the tmpfs the
  container wrote) was 4.41 GiB to within 1 MB on all three. 16 GiB would fit this run, because the
  `/tmp` tmpfs is half the RAM and needs only 5.2 GiB.
- **Overhead is small:** billed minus tool wall is 56–71 s, because a peak file and one FASTA are
  all that gets staged. The genome run includes HOMER's one-time preparse of GRCh38 for 200 bp
  fragments. `-preparsedDir` caches it, and the offset run reused it
  (`Background files for 200 bp fragments found`), so later peak sets on the same box skip it.
- n = 1 per cell. Every fixed-input output is byte-identical across chips (below), so a repeat
  would move only the timings and the random-background numbers.

## What held at scale, on all three chips

| claim | why it must hold | observed |
|---|---|---|
| staged peak file == ENCODE's md5 | sourcing, checked on the box | `3f36655e…` ok |
| rows == unique summits + duplicates + off-genome | nothing dropped silently | 44,217 = 44,217 + 0 + 0 |
| **HOMER's targets == peaks − our own >70%-N count** | **every scannable region scanned** | **44,217 = 44,217 − 0** |
| **offset control: targets == regions − N-masked** | same, independently counted | **44,157 = 44,217 − 60** |
| top known motif, genomic background | a CTCF ChIP must find CTCF | `CTCF(Zf)/CD4+-CTCF`, rank 1 of 1,049 |
| top known motif, offset as background | same, with no random draw | `CTCF(Zf)/CD4+-CTCF`, rank 1 |
| CTCF enrichment in peaks | ≥ 10× | 81.3–89.7× (52.05% of peaks) |
| **control: CTCF 100 kb away** | ≤ 3× | **0.90–1.10×** |
| de novo motif 1 | best match is CTCF-family | `GCCCCCTRGTGG` → `CTCF/MA0139.2` (0.944–0.946) |
| **per motif: 0 ≤ targets(genome bg) − targets(offset bg) ≤ targets dropped** | **a motif's target count is a scan of the targets alone** | **0 of 1,049 violate; 0 dropped, so all 1,049 counts equal** |

HOMER's own count of the targets it kept is taken from the GC-bin table in its log, not from the
`(of N)` in the `knownResults.txt` header. That header is a sum of autonormalisation weights and
reads **44,221** for 44,217 sequences, so an exact identity against it fails, and a check built on
it could not tell a dropped peak from a weight.

The monotonicity check is the background invariance. On the chr21 canary, where the small
offset background left three GC bins empty, HOMER dropped 6 targets and 188 counts fell, each by
no more than 6. At scale, every bin is filled, nothing is dropped, and the identity tightens to
equality on all 1,049 motifs.

## Fixed point or path, Intel and AMD each against Graviton4

**Byte-identical on all three chips:**

- the 44,217 target regions and their 44,217 offsets;
- the per-motif target counts for all 1,049 known motifs, in both random-background runs;
- the complete `vsoffset` `knownResults.txt` (`0d7a22fd…`): p-values, q-values, background
  percentages and GC weights, on a weighted hypergeometric over fixed sequences.

**Paths, reported and not asserted.** These are everything downstream of HOMER's random
background draw (`Choosing background that matches in CpG/GC content`):

| | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| CTCF in background / enrichment | 0.58% / 89.7× | 0.62% / 84.0× | 0.64% / 81.3× |
| offset control: CTCF rank / enrichment | 870 / 0.90× | 819 / 0.92× | 178 / 1.10× |
| de novo motif 1, log p | `GCCCCCTRGTGG`, −66,744 | `GCCCCCTAGTGG`, −65,742 | `GCCCCCTRGTGG`, −66,067 |

An earlier Graviton4 run of the same task gave 0.64% / 81.3× and de novo log p −66,516, so the
spread between two runs on one chip equals the spread across chips. The `R`/`A` at position 8 is
the de novo optimiser settling on a degenerate base or not, against a different background.

## Pins

| | |
|---|---|
| peaks | `ENCFF796WRU.bed.gz`, 711,745 B, ENCODE md5 `3f36655eb9a421a542ff031fc7b5e9aa`, from `s3://encode-public` (RODA, us-west-2, server-side copy) |
| genome | GRCh38 no-alt analysis set, 195 sequences, staged by [minimap2-real](../minimap2-real/README.md), sha256 in `pins.sha256` |
| arm64 | `quay.io/aarchbio/homer@sha256:b70d88a0…` (5.1) |
| x86_64 / amd | `quay.io/biocontainers/homer@sha256:d99bf68f…` (5.1, `linux/amd64`) |

The task carries the recipe's `LD_LIBRARY_PATH` fix for the x86 image's silent failure
([bioconda-recipes#70249](https://github.com/bioconda/bioconda-recipes/issues/70249)) and asserts
that `homer2` loads before anything runs.

## Caveats

**n = 1 per chip**, which is defensible because every fixed-input output matched byte for byte.
**One instance size:** HOMER keeps about 6 cores busy, so a core sweep would find the knee well
below 16. That sweep was not run. **Known motifs cover HOMER's vertebrate-and-more library as
shipped** in the image (1,049 motifs). `configureHomer.pl` packages were not installed, and the
run stays offline.
