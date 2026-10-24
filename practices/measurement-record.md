# The measurement record

> **[`catalog/record.json`](../catalog/record.json) is every verified run as data: what ran, on
> which box, what it cost, and which checks held.** It is generated. The only hand-written half is
> a `record.toml` beside each measurement, and `make check` proves each one against its README.

## Who reads it

A program that has to answer "which box, what did it cost, did it hold" without scraping prose
tables: spore-mcp-server reads it at a pinned commit. So does anyone asking a question across the
whole catalog, such as every failed assertion, every unpinned input, or every quantity that is a
path rather than a fixed point. People still read the measurement's README. The record summarises
that page and never replaces it.

## What is in it

| | where it comes from |
|---|---|
| per recipe: pins per arch, verified dates, gaps, spec resources / TTL / cost cap, inputs, owned measurements | derived from frontmatter and specs; nothing to write |
| per run: chip, vendor, generation; threads per core, CPU part, OpenBLAS corename; resolved image and `pin_current` | derived from the instance name, the run's host file and git |
| `status` (`fail-reported` if any check failed), reverse cross-code links, the trap and upstream indexes | derived from the records |
| result unit, datasets, runs, checks, cross-code links, traps, upstream refs | authored in `measurements/<dir>/record.toml`, format in [`record.schema.json`](../measurements/record.schema.json) |

## How to update it

1. Write the measurement's README as usual. Then mirror it in `record.toml`: one `[[run]]` per
   priced or timed row and one `[[check]]` per assertion row, with the claim copied verbatim.
   [`salmon-real`](../measurements/salmon-real/record.toml) is the simple case and
   [`openfoam-real`](../measurements/openfoam-real/record.toml) the messy one.
2. `python3 scripts/check_pages.py --records <dir>` checks that the record validates and that every
   $ equals seconds × $/hr ÷ results per run. It also checks that every number is printed in the
   README or in a results file the run cites, that every `repo#N` the README cites is listed, and
   that a `fail` is visible on the page.
3. `make catalog` regenerates `record.json`, and `make check` fails while it is stale.

If a record check fails, the record and the README disagree. Correct whichever one is wrong. A
README number can only change with a run behind it.

## What it does not license

- `usd_hr` is the on-demand rate on the run's date and in its region, not a current price.
- `pin_current: false` means the recipe repinned after the run. The row is still true, but of the
  old bytes.
- A `diagnostic` run is never a box recommendation, and a `path` value is never reproducible across
  chips. An `unpinned` dataset is a recorded gap.
- The record never stores an upstream issue's open or closed state. GitHub holds that.
