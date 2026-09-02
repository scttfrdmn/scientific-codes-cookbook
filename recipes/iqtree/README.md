# IQ-TREE — maximum-likelihood phylogeny from a Pfam seed alignment

One task. `iqtree3` infers a maximum-likelihood tree under LG+G4 from a curated
114-sequence protein alignment and writes a Newick tree plus a likelihood report.

## Why a Pfam seed alignment

IQ-TREE's conda package ships only a 20 KB cmaple test file, and depending on a
package's internal test directory is a fragile input — it is not versioned as data
and can move between builds. Pfam seed alignments are hand-curated, live in an
immutable versioned release, and have a durable id. It is also the **same pinned
release** `recipes/hmmer` uses, so the two recipes share one upstream pin.

The selection rule is deterministic and stated in the staging script: the **first**
alignment in `Pfam-A.seed` with between 40 and 150 sequences. That is a property of
Pfam 38.2, so re-running staging picks the same family every time — no "I happened to
grab this one."

Stockholm is not FASTA, so `stage-inputs.sh` converts it: decode as latin-1
explicitly (Pfam-A.seed is not UTF-8), concatenate the interleaved blocks, fold `.`
and `-` into one gap character, uppercase, and sanitise sequence names to
`[A-Za-z0-9_]` because `/` and `.` are legal in Pfam names but awkward in Newick.
A name collision after sanitising is a hard error. The script then **re-checks in
bash what Python wrote** — taxon count in range, one line per sequence, all lines
the same length — so a bug in the converter cannot pass silently.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7` |
| | tag `3.1.3--h2705416_0`, cosign-signed, manifest is `linux/arm64` only |
| alignment | first 40–150-sequence alignment in Pfam `releases/Pfam38.2/Pfam-A.seed.gz`, converted to aligned FASTA |
| | `sha256:b4d4d74594d6213f26dfbd8eeb194ae9a9eaf6c529242384a7395bb56c7c7294` (57,588 B, 114 sequences × 477 columns) |

**Data tier: stable public source with a durable id.** Pfam `releases/Pfam38.2/` is
immutable; `Pfam/current_release/` is not and does not qualify. Unlike the other
recipes' inputs this one is *derived* rather than byte-for-byte, so its sha256 is the
pin on our conversion, not on an upstream object — which is exactly why the staging
script's selection rule has to be deterministic.

## Smoke check

Measured in this image, on this input, before any launch. Note the asymmetry: the
shape assertions are exact, the likelihood is banded **wide on purpose**.

| observable | assertion | observed |
|---|---|---|
| taxa in alignment | exactly 114 | 114 |
| tips in tree | exactly 114 | 114 |
| alignment columns | exactly 477 | 477 |
| best log-likelihood | −54000 … −52000 | **−52706.731** |
| Newick terminated | exactly `;` | `;` |
| IQ-TREE wrote its footer | exactly 1 | 1 |

Every tip must be present and the tree must be syntactically complete — those come
from the input and from Newick, so they are exact.

**The log-likelihood band is deliberately loose, and a tight one would be wrong
here.** IQ-TREE's tree search is a heuristic: the fixed `--seed 12345` makes a run
repeatable against *itself*, but the result still moves with the IQ-TREE version and
with the thread count, because the number of threads changes the order of likelihood
updates and therefore which local optimum the search settles into. A bound of a few
log units would fail for reasons that have nothing to do with correctness. The
band's job is to catch a tree built from garbage — a mis-parsed alignment or a block
of all-gap columns lands nowhere near −52700. For scale, the starting RapidNJ tree
scored −52918.8 and the search improved it to −52706.7.

For the same reason the recipe pins `-T 4` rather than `-T AUTO`, and gives `-m
LG+G4` explicitly rather than letting ModelFinder choose: two fewer sources of
run-to-run drift, so that if this number *does* move later, that is signal.

## Resources, and what the timings mean

4 vCPU / 8 GiB, `c8g` (resolves to `c8g.xlarge`), TTL 35m. Measured work: **7m3s** wall (26m39s CPU, 225
iterations) on 4 threads. IQ-TREE reported needing **34 MB** of RAM, so this box is
sized entirely for the thread count — the smallest of the five recipes by footprint
and the longest by far by runtime.

**These timings are not compute cost.** Boot, image pull and S3 staging come first in
every task. This is the one recipe of the five where the science actually outweighs
the boot overhead.

TTL is 35m against ~7m of work, the loosest ratio of the five recipes and
deliberately so: heuristic search time is not perfectly predictable, and 4 Graviton4
vCPUs may not match the 4 threads this was measured on. A run that hits TTL instead of
completing is a failure by this project's rules. TTL is also the cost cap — at
`c8g.xlarge` the worst case is about $0.09.

## Running it

```sh
spawn task run --spec recipes/iqtree/01-tree.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/iqtree/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared
output fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail
the task; the bucket listing is the second half of the same check.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records. `-redo` is already in the command, so a re-run
does not trip IQ-TREE's checkpoint guard.
