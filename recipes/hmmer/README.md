---
tool: hmmer
tool_version: "3.4"
image: quay.io/aarchbio/hmmer@sha256:ecae1325123858761f0d744aacae948a89368440fd6d91f69c178402294fc792
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# HMMER — annotate the human proteome with all of Pfam

Searches all 30,134 Pfam-A 38.2 families against one protein per human gene at Pfam's own thresholds, in 28 minutes. For anyone running domain annotation for real.

## Run it

```bash
make stage RECIPE=hmmer   # once: Pfam-A 38.2 + Ensembl 116, one protein per gene
make run   RECIPE=hmmer   # ~31 min on c8g.2xlarge, self-terminating
make ls    RECIPE=hmmer   # hits.tbl.gz + smoke-check.txt

hmmsearch --cpu 8 --cut_ga --noali --tblout hits.tbl -o hmmer.out Pfam-A.hmm pep.fa
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| one protein per human gene | your proteome | the full Ensembl file is 382,428 sequences because it carries every isoform; annotating all of them is 16× the work for a question nobody asks. |
| all of Pfam-A 38.2 | your own HMM library | `--cut_ga` needs every model to carry a curated `GA` line, which `stage-inputs.sh` verifies. |
| `--cut_ga` | `-E 1e-5` | the GA thresholds *are* Pfam's family assignments; an E-value cut is a number of your own choosing. |
| `--noali` | drop it | the per-domain alignments are gigabytes at this scale. The `[ok]` trailer and the tblout survive either way. |

**Leave the workload** — all of Pfam against a real proteome is the job, and the timings transfer.
**Scale it** by proteome size; runtime is models × residues, so it is linear and predictable.

## Which box — measured, same inputs, 8 vCPU throughout

| generation | instance | `hmmsearch` | **compute $** | billed $ |
|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 2514 s | 0.1900 | 0.2085 |
| Graviton3 | `c7g.2xlarge` | 1763 s | **0.1420** | **0.1539** |
| Graviton4 | `c8g.2xlarge` | 1669 s | 0.1479 | 0.1635 |
| **Graviton5** | `c9g.2xlarge` | **1322 s** | **0.1277** | 0.1373 |

**1.90× over four generations — but Graviton4 is the wrong buy.** It is only 1.06× faster than
Graviton3 for 10% more per hour, so it costs **4% more per result on compute and 6% more billed**.
This is the clearest instance of that reversal in the catalog: the job is long enough that boot is a
tenth of the bill, so compute and billed agree on the direction
([the weak rung](../../patterns/cost-per-result.md)). On Graviton3, skip to Graviton5.

<details>
<summary>As shipped: a completion sentinel, exact counts confirmed four times, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| models searched | exactly 30,134 — every family in Pfam-A 38.2 | **30,134** |
| target sequences | exactly 23,879 — one protein per gene | **23,879** |
| **trailer** | **exactly `[ok]`** | **`[ok]`** |
| tblout hits | exactly 65,607 | **65,607** |
| families with a hit | exactly 9,473 | **9,473** |
| proteins with a hit | exactly 22,740 | **22,740** (95.23%) |

**`[ok]` is the check that earns its place.** `hmmsearch` writes that literal as its last line only
on a clean exit, so a search killed part-way — by a TTL, an OOM, a lost spot instance — is caught
even though its partial `tblout` would sit comfortably inside any hit-count band. A count alone
cannot distinguish "finished" from "stopped at model 20,000".

The three hit counts are asserted exactly rather than banded because `hmmsearch` is deterministic on
fixed inputs, and **all four generation runs reproduced them to the digit** — that sweep is what
confirmed the numbers rather than merely recording them, which is why the exact values went in before
the sweep rather than after.

95.23% of genes carrying at least one Pfam domain is an observation, not an assertion: it is a
property of Pfam 38.2's coverage and of the one-per-gene selection, and it would move with either.

### Why one protein per gene

Ensembl's `pep.all` is 382,428 sequences because it includes every isoform of every gene. Pfam
annotation is done on one representative per gene, so the staging script keeps the longest protein
per `gene:` tag, ties broken by the smallest protein id so the result does not depend on input order.
That is **16× less work than the full isoform set** — the difference between 28 minutes and most of a
day — and it is the more realistic job, not a shortcut.

An earlier version of this recipe searched 200 models against all 382,428 isoforms and argued that
"scaling to all of Pfam is a longer run, not a more legible one." That was true under the old bar and
is wrong under this one: all of Pfam against one protein per gene is a job a reader recognises, and
200 arbitrary models against every isoform is not.

### Pins

| | data tier |
|---|---|
| HMMER | `quay.io/aarchbio/hmmer@sha256:ecae13251238…` (3.4, cosign-verified, `linux/arm64`) |
| models | Pfam `releases/Pfam38.2/Pfam-A.hmm.gz`, sha256 `2d82087b6c5c60d7…` — versioned release |
| proteome | Ensembl `release-116` human `pep.all`, filtered to one per gene, sha256 `c753ba28b98b7506…` |

Both upstream paths are versioned and immutable. The mutable siblings — Pfam `current_release/`,
Ensembl `current_*/` — are deliberately not used, because they cannot be pinned. Neither input is
readable gzipped by `hmmsearch`, so the task decompresses both; `gzip` is a base-image utility, not a
second scientific tool.

### Run + verify

```sh
make stage RECIPE=hmmer
make run   RECIPE=hmmer
make ls    RECIPE=hmmer
```

Expect `smoke-check.txt` with `models_searched 30134`, `hmmsearch_trailer [ok]` and
`tblout_hits 65607`.

This recipe's output is also an input: [mafft](../mafft/README.md) builds its alignment from
`hits.tbl.gz`, taking the 906 human members of `7tm_1` — so the family being aligned is one hmmer
found, not one curated by hand.

</details>
