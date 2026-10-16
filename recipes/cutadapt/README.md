---
tool: cutadapt
tool_version: "5.2"
image: quay.io/aarchbio/cutadapt@sha256:434a8a2384580ec718463bac3723a5c407b083855af7bab5f802f9100033781d
images_x86_64:
  - quay.io/biocontainers/fastp@sha256:cbbe2402b6b6704df470d7d77dcb498eefd5bcd01f4c38be0ec69899e79ac134
  - quay.io/biocontainers/cutadapt@sha256:dc10b125b5099682ebd0c17d8ffee70c4bf82ad599fea4a1071b2f46e4d572f3
last_verified_x86_64: 2026-10-16
spawn_version: 0.127.0
last_verified: 2026-10-16
---
# cutadapt — paired-end adapter and quality trimming, exact to the base

Trims TruSeq adapters from paired reads and low-quality ends from single reads, on Graviton4 and x86. For anyone putting adapter trimming in front of an aligner.

## Run it

```bash
make stage RECIPE=cutadapt        # once: builds the fixture locally (~5 s) and pins it
for s in $(make -s spec RECIPE=cutadapt); do spawn task run --spec "$s" --wait; done   # fastp, then cutadapt
make ls RECIPE=cutadapt

cutadapt -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC -A AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT \
         -e 0.1 -O 3 -m 10 --max-n 0 -j 4 --json=report.json \
         -o trim_R1.fq -p trim_R2.fq R1.fq R2.fq
cutadapt -q 15,20 -o out.fq in.fq          # 5' cutoff 15, 3' cutoff 20
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 1,890 synthetic pairs, 100 bp | your FASTQs (`.fq.gz` reads and writes as is) | every pair has a known answer, which is what makes the checks exact. |
| TruSeq R1/R2 adapters | your kit's 3' adapters | `-a` is read 1's adapter, `-A` read 2's. A wrong adapter fails silently: the control run below trims 0 reads and exits 0. |
| `-e 0.1` | your error rate | errors allowed = ⌊0.1 × aligned length⌋: none under 10 bp, 3 on the full adapter. `error_lengths` in the JSON gives the breakpoints. |
| `-O 3` (the default) | a larger overlap | 1–2 bp of adapter at the 3' end is left in place by design; 3 bp is trimmed. Raising `-O` keeps more real sequence and more adapter tails. |
| `-m 10 --max-n 0` | your filters | filtering is per pair: one mate with an `N` drops both. |
| `-j 4` | your core count | output was byte-identical to `-j 1`, so the core count does not change the result. |

**Leave the fixture as it is.** It is small because every read carries a known answer, and a real
run would test nothing more about how trimming behaves. **Scale it** by pointing the same command
at your own data. This recipe has not been timed at real scale, so size that box with a run of your own.

## Shape, size, cost

Two tasks with S3 between them, because one image holds one tool: fastp trims the same staged
bytes, then cutadapt trims them and checks both. `c8g.large` + `c8g.xlarge`: **106 s + 113 s billed,
$0.0074 for both tasks**. All four cutadapt runs take about a second together. Boot, Docker and the
image pull make up nearly all of that time, so **these timings are not compute cost**.

<details>
<summary>As shipped: planted truth for every read, the books balanced, BWA quality trimming reimplemented, fastp on the same bytes, pins, x86</summary>

### The fixture

`stage-inputs.sh` builds each pair as **insert + adapter + filler**, records the insert, and
writes the exact FASTQ a correct trimmer must produce (`expected_R{1,2}.fq`). It writes that
file from the planting itself and never from a cutadapt run, then pins everything by sha256. R2 is
the reverse complement of the insert followed by the R2 adapter. Before a read is kept, it is
checked so that no other match could beat the planted one: an untouched or partial read has no
chance adapter prefix at its 3' end that scores as well, and no read has anything within `-e` of
either control adapter. The same check
confirms that the too-many-errors adapters are beyond `-e` under semi-global edit distance.

| category | pairs | planted | expected |
|---|---|---|---|
| full adapter, filler after it | 600 | insert 10–66 bp | trimmed to insert |
| partial adapter at the 3' end | 330 | every overlap 1–33 bp, ×10 | trimmed if overlap ≥ 3, else untouched |
| substitutions, at the `-e` limit | 300 | 3 per adapter | trimmed |
| one insertion / one deletion | 50 + 50 | in the adapter | trimmed |
| one error over the limit | 100 | 4 substitutions | **untouched** |
| no adapter | 300 | 100 bp insert | untouched |
| insert shorter than `-m` | 100 | 0–9 bp | filtered `too_short` |
| `N` in read 1 only | 60 | 1–3 `N` | pair filtered `too_many_n` |

### The checks, inside the task

| observable | assertion | arm64 | x86_64 |
|---|---|---|---|
| **trimmed reads** | **`trim_R{1,2}.fq` byte-identical to the staged expected output** | **1,730 / 1,730, 0 wrong** | **identical** |
| pairs | in == out + `too_short` + `too_many_n`, equal to file counts | 1890 = 1730 + 100 + 60 | same |
| with adapter | `read{1,2}_with_adapter` == planted (trim + too_short) | 1410 / 1410 | same |
| **bases** | **in == out + adapter-trimmed + in filtered pairs** | **378,000 = 212,282 + 152,818 + 12,900** | same |
| `-j 4` | same reads and same report counts as `-j 1` | byte-identical | byte-identical |
| control | adapters planted nowhere: 0 reads with adapter, output == input bytes | 0 / 0, identical | same |
| `-q 15,20` | every read == an independent BWA-rule implementation; `quality_trimmed` == its sum | 400 / 400, 5,608 bp | same |
| architecture | `platform.machine()` == `uname -m` (not emulated) | aarch64 | x86_64 |

The byte-for-byte check is the strongest one here. Sequence and quality of all 1,730 kept pairs equal
the planted insert, and every filtered pair is the planted one, so the conservation lines are
arithmetic over a result already known to be right. They still earn their place: each count is
cutadapt's own JSON report checked against counts taken from the files. Adapter-trimmed bases are
summed from the report's `trimmed_lengths` histogram, so a report that does not describe the files it
sits beside fails. Because the expected FASTQ is one pinned file, matching it byte for byte on both
chips is a cross-architecture identity by construction.

The quality check implements cutadapt's
[documented algorithm](https://cutadapt.readthedocs.io/en/stable/algorithms.html#quality-trimming-algorithm)
from the docs, not by importing `cutadapt.qualtrim`. Subtract the cutoff, take partial sums inward
from each end, stop when the sum goes negative, cut at the extreme. It has to agree on reads with
low tails, low heads, good sequence after a dip, and 20 all-low reads that trim to nothing.

### fastp on the same bytes: per category, against the planted insert

fastp (`-Q -L -G -w 1`, the same adapter sequences) runs first, on the same staged files, and
cutadapt's task compares both tools **against the planted insert**. A direct fastp-vs-cutadapt diff
would mostly measure a difference in method ([why that is the wrong metric](../../practices/cross-checks.md)).
fastp trims a pair by overlap analysis, which needs its 30 bp overlap, and otherwise by adapter
sequence with substitutions only. cutadapt aligns the adapter with indels and by design leaves
anything beyond `-e` or under `-O`. So agreement is asserted only where both tools are designed to find the insert:

| category | fastp = insert | cutadapt = insert | pairs | |
|---|---|---|---|---|
| full adapter | 600 | 600 | 600 | **both asserted** |
| partial, overlap ≥ 3 | 310 | 310 | 310 | **both asserted** |
| substitutions within `-e` | 300 | 300 | 300 | **both asserted** |
| indel, insert > 30 | 68 | 68 | 68 | **both asserted** |
| no adapter | 300 | 300 | 300 | **both asserted** |
| indel, insert ≤ 30 | 0 | 32 | 32 | observation: no overlap rescue, and fastp's sequence search allows no indels |
| over the error limit | 100 | 0 | 100 | observation: fastp's overlap still finds the insert; cutadapt is designed to leave it |
| partial, overlap 1–2 | 20 | 0 | 20 | observation: the same, for `-O 3` |
| insert 0–9 / `N` in R1 | 70 / 60 | filtered | 100 / 60 | observation: fastp drops inserts under 3 bp even with `-L` |

On those **1,578 pairs** the two codebases write identical bytes, and those bytes are the planted
insert. The edge at 30 was measured: the one indel pair whose insert is exactly 30 bp was missed by fastp, so
the asserted group starts at 31. fastp's own output was also byte-identical across the two chips.

### Pins

| | |
|---|---|
| cutadapt, arm64 | `quay.io/aarchbio/cutadapt@sha256:434a8a23…` (5.2, `5.2--py313h7ebdf71_1`), cosign-verified against `playgroundlogic/aarchbio`. The signature is on the manifest list, which holds one `linux/arm64` entry (`1398ad7b…`), so the list is what is pinned. |
| cutadapt, x86_64 | `quay.io/biocontainers/cutadapt@sha256:dc10b125…` (5.2, `5.2--py313h8c92656_1`, `linux/amd64`, bioconda's build; not cosign-signed, so the digest is the pin) |
| fastp | the [fastp recipe's](../fastp/README.md) pins, both chips (1.3.6) |
| fixture | build tier: `stage-inputs.sh`, seed `20261016`. `R1.fq a47aaa8f…`, `R2.fq 15f07ace…`, `expected_R1.fq eff932bc…`, `expected_R2.fq 89f63969…`, `qual.fq 4bcdae16…` |

The fixture is generated at staging time, not in the task. awk `rand()` draws different streams
in the two images, so generating in the task would give each chip different reads. The images
also differ in prefix (`/opt/conda` vs `/usr/local`) and in user (uid 57439 vs root). Both are on the
task's `PATH`, and every output lands under `/tmp`, which either user can write.

### On x86_64

`c8i.large` + `c8i.xlarge`, 107 s + 106 s billed, $0.0083. `smoke-check.txt` matches arm64 line for
line except the Python patch version (3.13.12 vs 3.13.14) and the architecture. `trim_R1.fq`,
`trim_R2.fq`, `qual_out.fq` and fastp's outputs are byte-identical to the arm64 run's.

### Run + verify

```sh
make stage RECIPE=cutadapt
make run   RECIPE=cutadapt                # Graviton4: 01-fastp, then 02-trim
make run   RECIPE=cutadapt ARCH=x86_64    # same specs, x86 pins, writes runs-x86_64/
make ls    RECIPE=cutadapt                # smoke-check.txt, trimmed FASTQs, four JSON reports, logs
```

Expect `CUTADAPT OK` as the last line of `smoke-check.txt`, with `sha256 trim_R1.fq eff932bc…`.

</details>
