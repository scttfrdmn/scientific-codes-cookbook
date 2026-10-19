---
tool: homer
tool_version: "5.1"
image: quay.io/aarchbio/homer@sha256:b70d88a0e541fef5a5aa5312da987abccc439f3d1f06c358da272913fa7346c2
images_x86_64:
  - quay.io/biocontainers/homer@sha256:d99bf68f31a7d14cdcd4b3016a5741d775b1e779d70d9e36c8e008300bce30a5
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
spawn_version: 0.127.0
last_verified: 2026-10-13
---
# HOMER — CTCF peaks find the CTCF motif, and a planted motif is found exactly

Runs HOMER's known-motif enrichment on macs2's CTCF ChIP peaks and de novo discovery on a planted fixture, on Graviton4 and x86, with a control for each. For anyone doing ChIP-seq motif analysis on ARM.

## Run it

```bash
make stage RECIPE=homer           # once: the de novo fixture (reads chr20 + macs2's CTCF peaks)
spawn task run --spec "$(make -s spec RECIPE=homer)" --wait
make ls RECIPE=homer

findMotifsGenome.pl peaks.txt chr20.fa out -size given -nomotif -p 4   # custom genome FASTA: no install
findMotifs.pl targets.fa fasta out -fasta background.fa -len 10        # de novo, FASTA mode
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `LD_LIBRARY_PATH=<prefix>/lib` | nothing | **keep it.** bioconda's HOMER tools carry an RPATH into the conda *build* env ([bioconda-recipes#70249](https://github.com/bioconda/bioconda-recipes/issues/70249)), so they load only if the image's base has a system libstdc++. Without it, x86 **exits 0 with empty results**. |
| `homer2 … grep "homer2 <command>"` | trusting the exit code | that is the check that catches the line above: HOMER's Perl drivers swallow a binary that will not load. |
| `chr20.fa` as a custom genome | `configureHomer.pl -install hg38` | a FASTA path needs no download, and the run stays offline. |
| `-p 4` | more cores | the known-motif scan scales with them: **973 s at `-p 2`, ~510 s at `-p 4`** on chr20. |
| exact consensus `GATTACAGGT` | a p-value threshold | a planted motif has a known answer, so assert it; HOMER may report either strand, so the reverse complement counts too. |

**Leave the fixtures.** macs2's 1,390 CTCF peaks carry a biological answer (CTCF must come first), and the planted FASTA carries an exact one. **Scale it** to a whole genome: the claims hold, and only preparse time grows.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 120m as a **backstop** with `cost_limit` $0.50 as the real guard. Two known-motif scans take **~8.5 min each** at `-p 4`, de novo seconds; ~19 min on Graviton4, ~27 on x86 ([layout](../../patterns/layout-and-effective-cost.md)).

## Which box: [measured](../../measurements/homer-real/README.md) on all 44,217 ENCODE GM12878 CTCF peaks, genome-wide

| chip | instance | tool wall | $/hr | compute $ | **billed $/result** |
|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | 1,081 s | 0.7181 | **0.2156** | **0.2298** |
| Xeon 6975P-C | `m8i.4xlarge` | 1,311 s | 0.8467 | 0.3083 | 0.3236 |
| EPYC 9R45 | `m8a.4xlarge` | **984 s** | 0.9738 | 0.2662 | 0.2813 |

**Take Graviton4.** AMD is 1.10× faster, but at 36% more per hour it is 22% dearer per result. Every fixed-input output is byte-identical across the three chips. HOMER keeps about 6 of 16 cores busy and peaks at 4.4 GiB.

<details>
<summary>As shipped: CTCF first of 1,049 known motifs and gone 100 kb away, a planted motif recovered exactly, identical across chips where the input is fixed</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged fixture | match pins | 3 of 3 |
| CTCF peaks from macs2 | `== 1390` | 1390 |
| **homer2 loads** | **must print its usage before anything runs** | loads |
| **top known motif in CTCF peaks** | **is CTCF** | **`CTCF(Zf)/CD4+-CTCF`, rank 1 of 1,049** |
| CTCF enrichment in peaks | ≥ 10× | **59.9×** (44.96% of peaks vs 0.75%) |
| **control: same regions +100 kb** | CTCF ≤ 3× | **0.71×, rank 901** |
| **de novo motif 1** | **`== GATTACAGGT` or its reverse complement** | **`GATTACAGGT`**, log p −1060.35 |
| control: nothing planted | must not find the planted motif; ≥ 10× weaker | `AGCTTMRTCV`, log p −25.83 |

The enrichment bounds sit between two regimes rather than near an observed value: in CTCF peaks
the motif is enriched by an order of magnitude or more, and in regions with no CTCF signal it
occurs at its genomic rate, so near 1×. The 100 kb offset keeps the region count, size and
chromosome identical, and moves only the binding.

### What is identical across architectures, and what is a random draw

| | arm64 (`m8g`) | x86_64 (`m8i`) |
|---|---|---|
| top known motif | CTCF, rank 1 | CTCF, rank 1 |
| CTCF in target peaks | **44.96%** | **44.96%** |
| CTCF in background / enrichment | 0.75% / 59.9× | 0.73% / 61.6× |
| offset control | rank 901, 0.71× | rank 887, 0.73× |
| de novo motif 1, log p | **`GATTACAGGT`, −1060.349884** | **`GATTACAGGT`, −1060.349884** |
| unplanted control, log p | **`AGCTTMRTCV`, −25.833490** | **`AGCTTMRTCV`, −25.833490** |

**Everything computed on fixed input is identical to every printed digit** — the target
frequencies and the whole de novo search, run against a staged background. What moves is the
genomic background: HOMER draws it at random positions matched for GC/CpG content (`using random
positions… Choosing background that matches in CpG/GC content`), so its frequency, the enrichment
and the log p shift between any two runs — by as much between two arm64 runs (0.67%, 0.75%) as
between architectures. That is why no assertion uses a background-dependent number as anything but
a wide regime test.

The de novo fixture is generated in staging and pinned by sha256 rather than synthesised in the
task: this image has no `python3`, and synthesising with awk `rand()` would draw different streams
in the two images (mawk vs BusyBox), so the comparison would not be on identical bytes.

### The x86 image's silent failure

On x86, bioconda's own HOMER image ran for 90 seconds and produced a header-only
`knownResults.txt` and no motifs — **with every command exiting 0**:

```text
homer2: error while loading shared libraries: libstdc++.so.6: cannot open shared object file
Illegal division by zero at /usr/local/bin/preparseGenome.pl line 247.
```

The library is in the image (`/usr/local/lib/libstdc++.so.6`, from the declared `libstdcxx` 15.2),
but the binaries' RPATH points at the build environment — `/opt/conda/conda-bld/homer_…/_build_env/lib`,
in **both** the linux-64 and linux-aarch64 packages — so they depend on a system libstdc++ the
x86 image's Debian base does not have. The arm64 image works only because its base has one.
Filed with the fix as [bioconda-recipes#70249](https://github.com/bioconda/bioconda-recipes/issues/70249);
the recipe points the loader at the image's own prefix (found from `perl`, so it is right in both
images) and asserts `homer2` loads before any analysis starts.

### Pins

| | |
|---|---|
| peaks | macs2's 1,390 CTCF peaks on GRCh38 chr20 (ENCODE ENCFF933NSJ vs ENCFF768XTH) — read from **[macs2](../macs2/README.md)'s run** |
| genome | GRCh38 chr20, staged by **[bwa-samtools](../bwa-samtools/README.md)** (64,444,167 bp) |
| de novo fixture | 400 targets each carrying `GATTACAGGT`, 2,000 background and 400 control sequences of 200 bp, from `random.seed(20261013)`, sha256-pinned |
| arm64 | `quay.io/aarchbio/homer@sha256:b70d88a0…` (5.1), cosign-verified against `playgroundlogic/aarchbio` |
| x86_64 | `quay.io/biocontainers/homer@sha256:d99bf68f…` (5.1, `linux/amd64`, bioconda's own build; not cosign-signed, so the digest is the pin) |

### Run + verify

```sh
make stage RECIPE=homer
spawn task run --spec "$(make -s spec RECIPE=homer)" --wait
make run RECIPE=homer ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/homer/r1/smoke-check.txt" -
```

Fails on a fixture pin mismatch, anything other than macs2's 1,390 peaks, a `homer2` that will not
load, a top known motif that is not CTCF, CTCF enriched less than 10× in its own peaks or more than
3× 100 kb away, a de novo motif that is not the planted one, or the planted motif turning up where
it was never planted. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md) — here, literally).

### Not covered

**Peak annotation** (`annotatePeaks.pl`) needs a gene annotation HOMER downloads or a GTF; nothing
here assigns peaks to genes. **Whole-genome runs and HOMER's installed genomes** — chr20 only, as a
custom FASTA. **De novo on the real peaks**: `-nomotif` skips it, because the known result is the
assertable one and de novo on 1,390 peaks is minutes more for an answer that is not a closed form.
**Tag directories and `findPeaks`**, HOMER's own peak caller, which is a second route to macs2's
peaks — a cross-check worth adding.

</details>
