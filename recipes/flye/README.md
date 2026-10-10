---
tool: flye
tool_version: 2.9.6
image: quay.io/aarchbio/flye@sha256:d87ccd4e29f2995e6bbcea9f72e90f575897a5489b472111695320bd8528dc12
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# Flye — a closed bacterial genome from a real nanopore run, in five minutes

Assembles a 65× MinION run of *E. coli* into one circular 4.72 Mb chromosome plus its plasmid. For anyone doing long-read de novo assembly.

## Run it

```bash
make stage RECIPE=flye   # once: ERR10114907, 55,898 reads / 299.5 Mbp
spawn task run --spec "$(make -s spec RECIPE=flye)" --wait   # 306 s on c8g.2xlarge, self-terminating
make ls    RECIPE=flye   # assembly.fasta + assembly_info.txt + flye.log

flye --nano-hq ERR10114907.fastq.gz -g 4.6m -t 8 -o out
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ERR10114907` (65×) | your ONT reads | `stage-inputs.sh` asks ENA for the path and checks the download against ENA's own read and base counts. |
| `--nano-hq` | `--nano-raw` / `--nano-corr` | measured in this catalog: the raw modes OOM on inputs where `--nano-hq` fits. |
| `-g 4.6m` | your genome size estimate | a hint, not a constraint; Flye tolerates being wrong by a factor. |
| `-t 8` | more cores | **changes which assembly you get** — 8 is what this recipe asserts against. |

**Leave the depth** — 65× is what ONT assembly is actually run at: enough to close a chromosome, quick enough to finish in five minutes. **Scale it** by genome size, which moves runtime and the ~4.3 GB footprint together.

## Which box — [measured](../../measurements/flye-real/README.md), same reads, 8 vCPU throughout

| generation | instance | Flye | **compute $** | billed $ |
|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 511 s | 0.0386 | 0.0543 |
| Graviton3 | `c7g.2xlarge` | 377 s | 0.0304 | 0.0428 |
| Graviton4 | `c8g.2xlarge` | 306 s | 0.0271 | 0.0360 |
| **Graviton5** | `c9g.2xlarge` | **245 s** | **0.0237** | **0.0331** |

**2.09× over four generations, every step paying for itself** (1.36×, 1.23×, 1.25×) — unlike
[hmmer](../hmmer/README.md), where Graviton4 is 4% *dearer* than Graviton3 on these same two chips, so
that weak rung belongs to particular inner loops rather than to the step
([which comparison applies](../../patterns/cost-per-result.md)). **Size by the assembler's working
set, not by staging:** tmpfs peaks at **768 MB** against Flye's own **~4.3 GB**, so a 16 GiB box is
the fit — the opposite of [fastp](../fastp/README.md), where staging picked the family.

<details>
<summary>As shipped: a completion sentinel from the right code path, seven runs of exact agreement, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| **completion** | **`INFO: Final assembly:` present** | **yes** |
| total length | exactly 4,754,056 | **4,754,056** |
| largest contig | exactly 4,722,868 | **4,722,868** |
| largest is circular | `Y` | **Y** |
| contigs | exactly 2 | **2** |
| circular contigs | exactly 2 | **2** |

`assembly_info.txt` is the whole result in two lines: `contig_1` 4,722,868 bp circular at 63×, and
`contig_2` 31,188 bp circular at 29× — a closed chromosome and a plasmid, which is what a good ONT
assembly of a bacterial isolate looks like.

**The exact values are asserted because seven runs produced them.** The recipe's own runs plus four
generation rungs on `c6g`/`c7g`/`c8g`/`c9g` all gave the same three numbers. That matters because this
catalog's standing rule is that **Flye's thread count changes which assembly the search lands on** —
measured at `-t 4`, where the contig count moved between runs. What the sweep establishes is narrower
and more useful: at a *fixed* thread count the chip does not move the answer, so `-t 8` can carry an
exact assertion. Change `-t` and these numbers are no longer yours to expect.

[muscle](../muscle/README.md) is the counter-example from the same batch: two runs with threads
pinned gave 5,633 then 5,636 columns, so nothing about its output may be asserted. Only running twice
tells you which kind of tool you have ([the rule](../../practices/cross-checks.md)).

### The sentinel came from the wrong code path first

The first version of this check grepped for `INFO: Done`, and returned **0 on a perfect assembly**.
Flye 2.9.6 does contain `logger.info("Done!")` — at `main.py:443`, inside the standalone
`flye-polish` path, which `flye --nano-hq … -o out` never takes. The real end-of-pipeline marker is
`logger.info("Final assembly: %s")` at `main.py:261`, the last line of `JobFinalize`, and
`JobFinalize` is appended last (`main.py:395`, after `JobPolishing`), so it prints only once polishing
and scaffolding have finished.

A sentinel that exists in the tool but on a path your invocation skips is worse than a typo: it
*looks* verified, and a zero match reads as "the tool failed" rather than "I asked the wrong
question." `flye.log` is uploaded so the evidence is in the bucket rather than dying with the
instance.

### Pins

| | data tier |
|---|---|
| Flye | `quay.io/aarchbio/flye@sha256:d87ccd4e29f2…` (2.9.6, cosign-verified, `linux/arm64`) |
| reads | ENA run `ERR10114907` — 55,898 reads / 299,527,299 bases, MinION; sha256 `b9c775431270…` |

The FASTQ path is **not constructed** by `stage-inputs.sh`; ENA's portal API is asked for it, because
the `vol1/fastq/<prefix>/<subdir>/` layout is not something to guess — an earlier attempt at another
accession built the path by hand and 404'd. The script then checks the downloaded file's read and base
counts against ENA's own reported values: a truncated download is still valid gzip and still
assembles, into a worse genome, silently.

### Run + verify

```sh
make stage RECIPE=flye
make run   RECIPE=flye
make ls    RECIPE=flye
```

Expect `smoke-check.txt` with `flye_done 1`, `total_length 4754056` and `contigs 2`.

</details>
