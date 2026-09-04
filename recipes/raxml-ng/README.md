# RAxML-NG — adaptive maximum-likelihood phylogeny from a Pfam seed alignment

One task. `raxml-ng --search` infers a maximum-likelihood tree under LG+G4 from a
curated 114-sequence protein alignment, using RAxML-NG 2.0's **adaptive** search: the
tool predicts the difficulty of the alignment and chooses the number and kind of
starting trees from that prediction rather than from a flag.

## It runs on the same alignment as `recipes/iqtree`, deliberately

There is **no `stage-inputs.sh` in this directory.** The spec reads
`inputs/iqtree/pfam38.2_seed_alignment.fa`, the object
`recipes/iqtree/stage-inputs.sh` already derived and pinned — run that script once if
the object is not staged yet.

That is the point rather than a shortcut. The alignment is a *derived* input (Pfam
Stockholm converted to aligned FASTA by a deterministic rule stated in that script),
so its sha256 pins our conversion, not an upstream object. Copying it under a second
prefix would mean a second copy of the converter to keep true, and the recipe's most
interesting result depends on the two tools seeing byte-identical input:

**RAxML-NG 2.0.2 and IQ-TREE 3.1.3 independently land on the same optimum.**
RAxML-NG reports `-52706.731409`; IQ-TREE, on the same alignment under the same
LG+G4 model, reports `-52706.731`. Two unrelated codebases, two different search
heuristics, agreeing to about 1 part in 10⁸ of the log-likelihood. That is much better
evidence that this is the real ML optimum than either run alone, and it is only
meaningful because the input is the same bytes and the model is pinned in both.

The task still verifies the pinned sha256 before running, so a changed object fails on
the box rather than quietly producing a different tree.

## The search is left on its adaptive default

RAxML-NG 2.0's default `--search` predicts MSA difficulty and sizes the search from
it. On this alignment it predicts **0.55** and generates **13 distinct starting trees
— 4 random and 9 parsimony**. That default is the thing worth demonstrating, so it is
not overridden with `--tree pars{n},rand{n}`; a cheaper fixed search would have been
faster and less representative of what the tool actually does.

The adaptive mix earns its keep here. The parsimony starting trees score around
−55,800 and the random ones around −76,300 — and yet **the best tree of the 13 came
from a random start** (search #1, −76,542.20 → −52,706.73). A parsimony-only search
would have missed it. The 13 searches span −52,706.73 to −52,732.89, so the tree
landscape has local optima within ~26 log units of each other, which is exactly the
case difficulty prediction exists to detect.

Two flags are pinned rather than left on AUTO, for one reason: **the thread count
feeds the parallel parsimony the difficulty prediction is computed from**, so it
decides how many starting trees get generated. `--threads 8 --workers 1` plus
`--seed 12345` is what makes this run comparable to the next one. `--model LG+G4` is
given explicitly rather than inferred, for the same reason — and because it is the
model IQ-TREE was given.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/raxml-ng@sha256:3a6bbc162ff43249ada42bd92828ac0024855c33b614c0cdbaaa1e3e3eed89e5` |
| | tag `2.0.2--h6e375b3_0`, RAxML-NG 2.0.2, cosign-signed, manifest is `linux/arm64` only |
| alignment | first 40–150-sequence alignment in Pfam `releases/Pfam38.2/Pfam-A.seed.gz`, converted to aligned FASTA |
| | `sha256:b4d4d74594d6213f26dfbd8eeb194ae9a9eaf6c529242384a7395bb56c7c7294` (57,588 B, 114 sequences × 477 columns) |

**Data tier: stable public source with a durable id.** Pfam `releases/Pfam38.2/` is
immutable; `Pfam/current_release/` is not and does not qualify. See
`recipes/iqtree/README.md` for the selection rule and the conversion.

## Smoke check

Measured in this image, on this input, before any launch. **One value is banded; the
rest are identities RAxML-NG must satisfy to be self-consistent at all.**

| observable | assertion | observed |
|---|---|---|
| taxa in alignment | exactly 114 | 114 |
| taxa read by RAxML-NG | == taxa in the FASTA | 114 |
| tips in best tree | == taxa in the FASTA | 114 |
| alignment sites | exactly 477 | 477 |
| distinct site patterns | exactly 465, and ≤ sites | 465 |
| free parameters | == 2n−2 | 226 |
| AIC | == 2k − 2·lnL | 105865.462818 |
| AICc | == AIC + 2k(k+1)/(n−k−1) | 106275.878818 |
| BIC | == k·ln(n) − 2·lnL | 106807.321545 |
| final lnL | == best of the per-search values | −52706.731409 |
| ML trees written | == searches reported | 13 == 13 |
| starting trees written | == searches reported | 13 == 13 |
| RAxML-NG reported finished | exactly 1 | 1 |
| Newick terminated | exactly `;` | `;` |
| optimized model | `LG+G4m{alpha}` | `LG+G4m{0.923198}` |
| sites in partition | `= 1-477` | `= 1-477` |
| best log-likelihood | −54000 … −52000 | **−52706.731409** |

Most of that table costs nothing and cannot go flaky, because it is arithmetic rather
than observation:

- **`free parameters == 2n−2`.** An unrooted binary tree on *n* taxa has 2n−3
  branches, and LG+G4 adds exactly one free parameter — the gamma shape α (LG's
  exchangeabilities and equilibrium frequencies are fixed by the model). So k must be
  226 for 114 taxa, and any other value means a wrong taxon count or a wrong model.
- **The three information criteria are closed forms** of the log-likelihood, k, and
  the site count that RAxML-NG prints on adjacent lines. They agree to floating-point
  exactness (the largest residual is 5.9e−08, on BIC), so the check is `≤ 1e-4` with no
  headroom needed. It catches a garbled log or a mismatched lnL/k pair for free.
- **`final lnL == max(per-search lnL)`** is what `--search` *means*: report the best of
  the searches. Exact, and here the margin is comfortable — second best is
  −52707.126874 — so it is not a tie-break coin flip. (Contrast the "best BLAST hit is
  itself" check in `recipes/blast`, which failed 19-of-20 for exactly that reason.)
- **One ML tree and one starting tree per reported search.** A conservation identity,
  and stated as an identity rather than as `== 13` on purpose: 13 is a *prediction* of
  the adaptive heuristic, so pinning the literal would turn a legitimate change in
  difficulty estimation into a red check. What must always hold is that the files and
  the log agree.
- **`Analysis started: … / finished: …`** is a completion sentinel: RAxML-NG writes it
  only after the whole analysis completes cleanly. A search killed part-way leaves
  per-search lines and tree files that would still look plausible to a count.

**The log-likelihood band is the only band, and a tight one would be wrong.** Tree
search is heuristic: `--seed 12345` makes a run repeatable against *itself*, but the
result moves with the RAxML-NG version and with the thread count, because the thread
count changes both the difficulty prediction and the order of likelihood updates. The
band's job is to catch a tree built from garbage — for scale, the best random starting
tree scored −76,542 and the search improved it to −52,706.7, so a mis-parsed alignment
or a block of all-gap columns lands nowhere near the band. It is deliberately the
**same** band `recipes/iqtree` uses, so the two recipes are directly comparable.

There is no `python3` in this image — one tool per image is deliberate — so every
check above is `awk` and `grep`.

## Resources, and what the timings mean

8 vCPU / 16 GiB, `c8g` (resolves to `c8g.2xlarge`), TTL 20m, cap $0.11. RAxML-NG's own
`--parse` estimates **65 MB** of memory and recommends 7 threads, so this box is sized
entirely for the thread count — as with `recipes/iqtree`, the memory request is
irrelevant and the family is `c8g` for that reason.

| | wall for all 13 searches |
|---|---|
| local, in the pinned image (Docker Desktop, 8 threads) | 16m35s (995.5s) |
| **`c8g.2xlarge`, the recorded run** | **7m39s (458.7s)** |

**Graviton4 was 2.17× faster than the local measurement**, doing identical work —
same predicted difficulty 0.55, same 4 random + 9 parsimony starting trees, same
`-52706.731409`. So the TTL here was set from the local run and then **retightened
from the box run**: 35m originally (2.1× the local figure), now 20m (2.6× the real
one), halving the blast radius. `lifecycle.cost_limit` came down with it, $0.19 →
$0.11, and `--dry-run` echoes both back — which is how the field is confirmed honored
rather than merely parsed. The recorded run below used the original 35m/$0.19.

That is the general lesson, not a detail of this recipe: sizing from a local run is
the right *first* move because it costs nothing, but the first real run is better
evidence and should be spent. Guessing in the other direction — a loose TTL "to be
safe" — is not free caution, it is a larger blast radius at 32¢/hr.

**These timings are not compute cost** — boot, Docker install, image pull and staging
come first in every task. This is, however, the one recipe in the cookbook where the
science dominates that overhead by an order of magnitude rather than the reverse: 7m39s
of search inside an 8m25s command window.

Disk is negligible: a 57 KiB alignment in, ~215 KiB of trees, logs and a binary MSA
out.

Scale caveat worth stating plainly: 114 taxa × 477 sites is a *small* alignment. This
recipe shows RAxML-NG running correctly and landing on the right optimum on
Graviton4; it does not exercise the MPI/`--workers` path or the memory behaviour of a
phylogenomic dataset with thousands of taxa.

## Running it

```sh
recipes/iqtree/stage-inputs.sh          # once, if the shared alignment is not staged
spawn task run --spec recipes/raxml-ng/01-search.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/raxml-ng/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared output
fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail the
task; the bucket listing is the second half of the same check. Expect six objects.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records. `--redo` is already in the command, so a re-run
does not trip RAxML-NG's checkpoint guard.
