# The measurement record

> **[`catalog/record.json`](../catalog/record.json) is every verified run as data: what ran, on
> which box, what it cost, how fast, and which checks held.** It is generated. The only hand-written
> half is a `record.toml` beside each measurement, and `make check` proves each one against its README.

## Who reads it

A program that has to answer "which box, what did it cost, did it hold" without scraping prose
tables: spore-mcp-server reads it at a pinned commit (`schema_version` says which format). So does
anyone asking a question across the whole catalog, such as every failed assertion, every unpinned
input, or every quantity that is a path rather than a fixed point. People still read the
measurement's README. The record summarises that page and never replaces it.

## What is in it

| | where it comes from |
|---|---|
| per recipe: pins per arch, verified dates, gaps, spec resources / TTL / cost cap, inputs, owned measurements | derived from frontmatter and specs; nothing to write |
| per run: chip, vendor, generation; threads per core, vCPUs, CPU part, OpenBLAS corename, BLAS library; resolved image and `pin_current` | derived from the instance name, the run's host file and git |
| `status` (`fail-reported` if any check failed), reverse cross-code links, the trap and upstream indexes | derived from the records |
| result unit, datasets, runs, checks, cross-code links, traps, upstream refs | authored in `measurements/<dir>/record.toml`, format in [`record.schema.json`](../measurements/record.schema.json) |
| schema 2, authored: `workload.size`, `timing` (what wall and billed time), per run `throughput`, layout (`ranks`, `threads_per_rank`, `concurrent`), `avg_cores`, `tmpfs_peak_gib`, `data_path`, `variant` / `task` / `attempt`, `superseded_by`; `[[series]]` (axis, `held`, `equal_work`, attribution, verdict; `axis = "parameter"` sweeps a method setting, which gets no cost knee, and `verdict.on = "answer"` judges the result rather than time) and `[[not_swept]]` (axis, the README's reason) | the README and `results/`; billed windows from spawn's completion records in `results/billed-windows.txt` |
| schema 2, derived: `n` and `wall_spread` per configuration, `usd_per_unit` ($ per throughput unit; `usd_per_result` when the rate's unit is the result), `util` and `effective_usd_hr`, pipeline `results[]` (sum of tasks), each series' `curve` (speedup, efficiency, cost knee), the GPU plateau | `gen_catalog.py`, never authored |

Three of those are decisions, not conventions. **Throughput** is recorded only where a rate is the
tool's natural unit (ns/day, reads/s). **The GPU plateau** is computed from the committed 1 Hz
`gpu.util_series` CSV, never typed: the active window runs from the first to the last sample at or
above half the series' 90th-percentile sample, and the plateau is that window's median, given only
when the window holds at least 5 samples. A spike shorter than a tenth of the run cannot set it: half
the series *maximum* made one startup sample a 53% "plateau" for PME on the CPU, where the steady
value is 11%. **The speed knee** is the author's `verdict.at`, confined to the series' rungs; only the
cost knee (the cheapest rung) is derived. A GPU row quotes the tool's log in `offload_evidence`, or
says in `offload_evidence_absent` why only the launch flags survive; `offload_basis` then reads
`flags`, an assertion rather than a proof.

## How to update it

1. Write the measurement's README as usual. Then mirror it in `record.toml`: one `[[run]]` per
   priced or timed row and one `[[check]]` per assertion row, with the claim copied verbatim.
   [`salmon-real`](../measurements/salmon-real/record.toml) is the simple case and
   [`bwa-real`](../measurements/bwa-real/record.toml) the sweep case.
2. `python3 scripts/check_pages.py --records <dir>` checks that the record validates and that every
   $ equals seconds × $/hr ÷ results per run. It also checks that every number is printed in the
   README or in a results file the run cites, that every `repo#N` the README cites is listed, and
   that a `fail` is visible on the page. On schema 2 it adds: `workers == ranks × threads × concurrent`;
   every billed figure names its `billed_basis`; rows with one configuration key are marked repeats;
   each series' `held` dimensions really are equal across its members, its `equal_work` names a
   passing check, and `verdict.through` is the top rung; a GPU row quotes its offload evidence or
   says why there is none.
3. `make catalog` regenerates `record.json`, and `make check` fails while it is stale.

If a record check fails, the record and the README disagree. Correct whichever one is wrong. A
README number can only change with a run behind it.

## What it does not license

- `usd_hr` is the on-demand rate on the run's date and in its region, not a current price.
- `pin_current: false` means the recipe repinned after the run. The row is still true, but of the
  old bytes.
- A `diagnostic` run is never a box recommendation, and a `path` value is never reproducible across
  chips. An `unpinned` dataset is a recorded gap.
- `workload.size` lets a consumer match a measured point. With no `size` series it licenses no
  extrapolation, and `n = 1` is printed as such. `effective_usd_hr` is an upper bound on what
  right-sizing could recover, never a ranking; rank by $/result.
- The record never stores an upstream issue's open or closed state. GitHub holds that.

## Queries

[`scripts/record-queries.sh`](../scripts/record-queries.sh) holds twelve worked `jq` questions
(cheapest box per result, where the knee is, idle rented capacity, capacity trouble), run from the
repo root.
