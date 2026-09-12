# sizing-ratio measurement batch (throwaway — NOT a shipped recipe)

Four one-off measurement runs behind the 52-recipe sizing-evidence audit. Goal: for
the recipes where a reader scaling up could pick the wrong instance family, measure
the **ratio** — peak memory + cores *actually used* at legible scale — so the pages
can argue the box the way `relion` does, from a number instead of a guess.

**These are deliberately over-provisioned** so the tool is never the thing being
throttled — the opposite of the lean advice the measurement produces. A page that
says "measured on r8g.4xlarge, recommend <smaller>" is not a contradiction; the big
box is the instrument, not the recommendation.

## The instrument (inline in each script; no shared engine)

A 1 Hz sampler of cgroup v2 `memory.current` / `memory.stat anon` / `cpu.stat
usage_usec`, plus a whole-run `memory.peak` cross-check. From the per-phase sample
file it reports:
- **peak_rss** (max `memory.current`) — conservative ceiling, includes reclaimable cache
- **anon_max** (max `memory.stat anon`) — the hard floor (what OOMs the box)
- **avg_cores** = Δusage_usec / Δwall — efficiency
- **peak_cores** = max per-tick delta — distinguishes "never parallelizes past N"
  from "N-way phase then serial"

Validated inside a pinned image before writing these (cgroup v2 readable, memory.peak
tracks, anon splits, cores math checks). Fail-soft: if cgroup is unreadable the script
warns and the memory numbers still come from `/usr/bin/time`-class fallbacks.

## The four runs

| run | box | why | yields |
|---|---|---|---|
| star | r8g.4xlarge (16/128) | build ~30 GB RAM **+** index ~30 GB in tmpfs (image is non-root → only `/tmp` writable, tmpfs≈½RAM) | build-RAM (hard floor) **and** tmpfs bytes (write-path artifact) **and** align-RAM, reported separately |
| spades | m8g.2xlarge (8/32) | 8 vCPU to see the parallelism ceiling; headroom | k-mer-graph peak + concurrency at ~100× E. coli |
| megahit | c8g.2xlarge (8/16) | same 8-core view; memory-lean | its memory ratio to spades on the same reads |
| picard | m8g.2xlarge (8/32) | 8 vCPU to *show* it uses ~1 (heap, not cores, binds) | post-GC live heap (need) vs cgroup RSS (JVM held) |

Inputs are staged by `stage-inputs.sh`. Specs are generated from the `*.sh` scripts by
`build-specs.py` (single source of truth is the script; the `.task.json` is derived).

Ceiling authorized: **$2.50** (worst-case sum ~$2.19 + re-run margin).
