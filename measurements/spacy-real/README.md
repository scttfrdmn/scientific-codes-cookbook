# spaCy over 89,339 PMC full-text articles, and the whole of UD English-EWT: Graviton4, Intel and AMD

> **`en_core_web_sm` annotates 795 million tokens of PubMed Central full text (4.2 GB of article
> bodies, 89,339 articles) at 32 processes, and every exact check holds on all three chips:**
> lossless tokenization, offsets, sentence partition, DocBin round trip, batch and `n_process`
> invariance, and spaCy's string hash against an independent MurmurHash64A on 39.9M distinct strings.
> On UD English-EWT, spaCy's own `Scorer` and the CoNLL 2018 shared-task scorer give identical
> accuracies. Graviton4 is fastest and cheapest: **$0.84 per corpus billed**, against Intel $1.15
> and AMD $1.23.

Workload: what a literature-mining project runs, at the size it runs it.

- **PMC Open Access** (RODA, `s3://pmc-oa-opendata`): every article in the 100,000-id block
  `PMC10000000`–`PMC10099999` at its latest version, unfiltered. That is 89,339 articles and
  4.42 GB of text: 58,531 CC BY, 4,138 historical OCR scans, 5,203 author manuscripts and 118
  retracted. Each article's body is split into its paragraphs (PMC's non-empty lines), 15.1M of
  them, one `Doc` each, through `nlp.pipe` in 32 worker processes.
- **UD English-EWT r2.18**, all three splits (16,622 sentences, 254,820 words). It is scored two
  ways: on gold tokens, so the tagger and parser are measured alone, and from raw document text, so
  spaCy's tokenizer and sentence splitter are measured too.

## Run it

```sh
B=$(make -s print-bucket)
measurements/spacy-real/stage-inputs.sh "$B"     # fetched ON AWS; every text checked against PMC's own md5
python3 measurements/spacy-real/mkspec.py r1 c8g 32
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py spacy $a $B r1a measurements/spacy-real/real.task.json)" --wait
done
python3 measurements/spacy-real/collect.py "$B" r1 r1a
```

## Results

32 worker processes on an `8xlarge` (32 vCPU, 64 GiB) of each chip. Each run uses the same `nlp`
env (aarch.science arm64 on Graviton4, its linux-64 twin on both x86 chips) and the same staged bytes:

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.8xlarge` | **2,299 s** | 2,364 s | 30.7 GiB | 1.2762 | **0.815** | **0.838** |
| Intel (Xeon 6975P-C) | `c8i.8xlarge` | 2,708 s | 2,766 s | 30.9 GiB | 1.4994 | 1.128 | 1.152 |
| AMD (EPYC 9R45) | `c8a.8xlarge` | 2,531 s | 2,577 s | 30.3 GiB | 1.7243 | 1.212 | 1.234 |

A *result* is the whole corpus annotated and checked, plus the EWT evaluation (45–58 s of each wall).
*Billed* is spawn's task window, wrapper start to stage-out done. Peak memory is the cgroup's `memory.peak`.

- **Graviton4 annotates 356k tokens/s, Intel 300k, AMD 324k.** That is 1.18× Intel and 1.10× AMD
  on the PMC phase (2,251 / 2,663 / 2,473 s). Graviton4's hour is also the cheapest, so per corpus it
  costs 27% less than Intel and 32% less than AMD. Billed, that is **$1.05 per billion tokens** on
  Graviton4.
- **`nlp.pipe` is 81–88% of worker time.** The rest is this measurement's own checks: a DocBin
  round trip of all 9.67 GB and the per-layer digests. A run without them scales by that share.
- **Each worker peaks at 1.6 GiB, and the box at 31 GiB.** 32 processes × 1.6 GiB fits a `c` box
  (2 GiB/vCPU). Memory does not pick the family here; `sm` is small.
- **Intel's 32 vCPUs are 16 cores × 2 threads.** AMD's and Graviton4's are 32 full cores. With one
  process per vCPU, Intel's SMT pairs share a core, and it is the slowest of the three.
- **Boot and staging are 46–65 s of each billed window**: 2–3%. This is a compute-bound job.
- n = 1 per chip. The checks are exact and the timings measure one computation.

## Why this size

Annotating a literature corpus once and querying it is how spaCy meets real research text: tens of
thousands of full-text papers, not a page of sample sentences. A contiguous id block takes PMC as it
is, with nothing filtered. That includes 1867 OCR scans, author manuscripts and 192k-character table
paragraphs, which a curated sample would leave out. The cost is per token (every one of the 256 shards ran at
86–96 µs per token per worker), so a reader can scale it to their own corpus.

EWT is used whole because it is a treebank: its test split is the standard evaluation, and train and
dev triple the words being scored.

## What held at scale, on all three chips

| assertion | bound | observed (Graviton4; both x86 chips the same) |
|---|---|---|
| every staged PMC text matches the md5 in PMC's own `metadata/<id>.json` | exact | 89,339 of 89,339 |
| every article in the manifest processed once | exact | 89,339 of 89,339 |
| **every paragraph reproduces from its tokens** (`text_with_ws` joined) | exact | 15,136,783 of 15,136,783 |
| every token's offsets index the text, and tokens are contiguous | 0 failures | 0 of 795,195,869 tokens |
| sentences partition each doc | 0 failures | 42.6M sentences, 0 |
| **DocBin round trip** changes no token, tag, lemma, morph, head, sentence or entity | exact | 89,339 articles, 9.67 GB of DocBin |
| one paragraph at a time == `nlp.pipe` (first article of each shard) | exact | 42,539 paragraphs |
| `nlp.pipe(n_process=4)` == serial | exact | 2,000 of 2,000 paragraphs |
| **every distinct string interned: round trip, and hash == MurmurHash64A** (independent implementation; spaCy's fixed symbols == their table ids) | exact | 39,859,261 strings (2,656 symbols) |
| EWT gold text: tokens + `SpaceAfter`/`SpacesAfter` reproduce each `# text` | exact | 16,622 of 16,622 |
| **EWT gold tokens: UPOS, XPOS and UAS, spaCy `Scorer` == CoNLL 2018 scorer == a recount** | integer counts equal; Scorer within 1e-12 | all 9 (3 splits × 3) |
| **EWT raw text: the CoNLL 2018 scorer accepts the system output** (it refuses if the characters differ from gold) | accepted | 3 of 3 splits; 1,174 of 1,174 documents lossless |
| EWT raw text: token P/R/F, spaCy `score_tokenization` == CoNLL 2018 | counts equal; within 1e-12 | all 3 splits |

The two scorers share no code. spaCy's aligns `Example`s token by token. The CoNLL 2018 scorer aligns
whitespace-stripped character spans, and its UAS treats the root as a word with no parent; spaCy's
treats it as its own head. On the same predictions they agree to the integer count. That makes the
accuracy numbers below a measurement, not one scorer's convention.

### The accuracies (identical on all three chips)

| EWT r2.18, `en_core_web_sm` 3.8.0 | test | dev | train |
|---|---|---|---|
| XPOS (PTB tags), gold tokens | 0.9234 | 0.9269 | 0.9388 |
| UPOS, gold tokens | 0.9259 | 0.9247 | 0.9370 |
| UAS, gold tokens | 0.5643 | 0.5606 | 0.5563 |
| tokens F1, raw text | 0.9564 | 0.9568 | 0.9662 |
| sentences F1, raw text | 0.6894 | 0.7296 | 0.7626 |

These are recorded, not asserted: a retrained model would move them and nothing would be broken.

- **The model's published scores are on OntoNotes 5** (`tag_acc` 0.9729, `dep_uas` 0.9177 in its
  `meta.json`). OntoNotes is LDC-licensed, so they cannot be reproduced here. EWT is web text from a
  different source, and XPOS 0.92 against 0.97 is the domain shift.
- **UAS 0.56 does not measure parser quality.** spaCy's English parser follows ClearNLP's dependency
  conventions, and UD makes content words heads where ClearNLP makes function words heads (copulas,
  auxiliaries, case markers). The same tree scores low against the other convention. The number is here
  because the scorers' agreement on it is a check; LAS is not reported, because the label sets differ.
- 50 of 2,077 test sentences come back from the parser as more than one sentence. The extra roots are
  attached to the first before scoring, for both scorers alike.

## Fixed point or path

**Tokenization is rule-based and a fixed point: identical on every chip.** The tokenization digest
matches on all 89,339 articles, so do the per-article token counts, 795,195,869 on all three. The EWT
scores are identical to the last digit on all three chips (27 of 27 values).

**The neural layers are a path.** Tags, lemmas, morphology, parses, sentence starts and entities are
argmaxes over thinc's matrix products (cython-blis, which picks its kernels for the CPU at run time).
A last-bit difference can flip a near-tie. Articles whose layer digest differs from Graviton4's:

| layer | Intel | AMD | Intel vs AMD |
|---|---|---|---|
| dependency parse | 193 | 205 | 113 |
| tag | 59 | 57 | 36 |
| POS | 50 | 52 | 30 |
| morphology | 44 | 43 | 27 |
| lemma | 24 | 22 | 8 |
| entity | 23 | 17 | 16 |
| sentence starts | 9 | 10 | 3 |

That is at most 0.23% of articles. In whole-corpus totals the differences are tens of tokens out of
795M: Intel has 3 fewer sentences than Graviton4 and the same 85,216,979 entities, and AMD has one
more entity. **Intel and AMD differ from each other on 113 articles, though they run the same linux-64
build.** So the path follows the microarchitecture, not the build, as kernel dispatch would predict.
OpenBLAS reports `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips, but spaCy's inference
does not go through it.

## Pins

| | |
|---|---|
| corpus | `s3://pmc-oa-opendata`, PMCIDs 10000000–10099999, latest version of each: 89,339 articles, 4,416,694,237 bytes. Fetched on AWS by `00-fetch.task.json`; each `.txt` kept only if its md5 equals the one in PMC's `metadata/<id>.<v>.json`. Written as 256 reproducible gzip shards, pinned in `shards.sha256` and re-checked in every run. `manifest.tsv` (per-article md5, sha256, licence) is in the bucket beside them |
| treebank | `UniversalDependencies/UD_English-EWT` tag `r2.18` (commit `b7711cce`), each file checked against the tag's git blob id, then pinned in `ewt.sha256` |
| scorer | `conll18_ud_eval.py` from `ufal/conll2018` at `1a2c3733`, sha256 `589506a8…` |
| image | arm64 `quay.io/aarchsci/nlp@sha256:436dc038…` (the recipe's pin); x86 the env's linux-64 twin `envs-x86_64/nlp-60b4d70cf1b7` |
| versions (all chips) | spaCy 3.8.16, `en_core_web_sm` 3.8.0, thinc 8.3.13, numpy 2.5.4 |
| scripts | `run.sh`, `real.py`, `ewt.py`, `shards.sha256`, `ewt.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it); `collect.py` runs locally |

**Data tier: RODA** for the corpus, checked against the publisher's own checksums. The `.txt`
objects of 2023 article versions carry 2026 modification dates, so PMC can rewrite them in place and a
re-fetch could differ: `shards.sha256` is the pin, and `stage-inputs.sh` fails if a re-fetch does not
match it. Each chip's `check.txt`, `ewt-check.txt`, `host.txt`, `footprint.txt` and `shards.tsv` are
in `results/`, with the comparison in `results/crosschip.txt`. The per-article digests
(`articles.jsonl.gz`) and the EWT system CoNLL-U files are in the bucket under
`runs*/spacy-real/r1/`.
