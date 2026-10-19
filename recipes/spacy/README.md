---
tool: spacy
tool_version: "3.8.16"
env: nlp
image: quay.io/aarchsci/nlp@sha256:436dc03828933d9e176ee751038a8a63e4d41b393ed693bfed9f442efe90e397
env_x86_64: nlp-60b4d70cf1b7
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# spaCy — its own 4,855 tests, then exact claims on tokenization, hashing, serialization and matching

Runs spaCy's own test suite on Graviton4 with every test accounted for, then holds `en_core_web_sm` to claims that hold whatever the model's weights are. For anyone running NLP pipelines on ARM.

## Run it

```bash
make stage RECIPE=spacy       # once: 10 pinned test-requirement wheels + the checks (the suite and model ship in the image)
spawn task run --spec "$(make -s spec RECIPE=spacy)" --wait
make ls RECIPE=spacy

python -m pytest --pyargs spacy -W error -W 'ignore:Core Pydantic V1:UserWarning:pydantic'   # spaCy's own CI line
docs = list(nlp.pipe(texts, batch_size=64))     # == [nlp(t) for t in texts], every annotation
"".join(t.text_with_ws for t in doc) == text    # lossless, byte for byte
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `en_core_web_sm` | `en_core_web_md`/`lg`/`trf` | **`sm` has no word vectors**: `vocab.vectors.shape == (0, 0)`, yet `token.has_vector` is `True` because the tok2vec context tensor stands in. So `.similarity` here is not static-embedding similarity (spaCy warns `W007`). Only `sm` ships in this env. |
| generated sentences | your corpus | nothing is fetched. Every check holds on any text; the model's labels on yours are yours to judge. |
| `PhraseMatcher(attr="LOWER")` | the default (`ORTH`) | the default is case-sensitive. And a phrase matches **tokens**, not characters: `single-cell` tokenizes as `single - cell`, so it never matches `single cell`. |
| test requirements as 10 staged wheels | `pip install -r requirements.txt` | the image ships spaCy's tests but none of its test requirements. **`ml_datasets` is one**: not a runtime dependency, but `test_registry_population` fails without the three readers it registers. |
| the suite without `--slow` | `--slow` | spaCy's CI leaves the 667 slow tests off; so does this run. |

**Leave the fixture.** No identity depends on what the text says, only that it exercises whitespace, Unicode, URLs, contractions and abbreviations, and generating it keeps the run offline. **Scale it** by pointing `texts` at a real corpus: every identity holds at any size.

## Shape, size, cost

One task on `c8g.xlarge` (4 vCPU / 8 GiB), TTL 40m as a **backstop** with `cost_limit` $0.25 as the real guard. The suite runs serially as spaCy's CI does, **148 s**, and the identities take seconds, inside a ~4-minute window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: 3,591 passing tests with every one accounted for, lossless tokenization, spaCy's hash equal to an independent MurmurHash64A, and exact matches on planted phrases</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged wheels / checks | match pins | 10 of 10 / 4 of 4 |
| spacy / thinc / numpy / model | read from the running install | **3.8.16** / 8.3.13 / 2.5.4 / `core_web_sm` 3.8.0 |
| test runner | read from the running install | pytest 9.1.1, hypothesis 6.155.7 |
| **suite: failed / errors** (warnings are errors) | **`== 0`** | **0 / 0** |
| suite: passed / skipped / xfailed / xpassed | recorded | 3,591 / 1,233 / 24 / 7 |
| **every collected test has one outcome** | **exact: 0 missing, 0 uncollected, 0 multiple** | **4,855 of 4,855** |
| **summary == items + collection-time skips** | **exact** | **4,855 = 4,855 + 0** |
| **subtest reports == the summary's subtests** | **exact, none failed** | **5 = 5** |
| collected | ≥ 4,800 (a floor against a shrinking collection) | 4,855 |
| **tokenization lossless** (`text_with_ws` joined) | **exact, every text** | **120 of 120**, 2,197 tokens |
| **token offsets** `text[idx:idx+len] == token.text` | **0 mismatches** | **0** |
| **tokens contiguous** (next `idx` = `idx + len(text_with_ws)`) | **0 gaps** | **0** |
| **span char offsets round-trip** via `char_span` (sents, ents, noun chunks) | **exact** | **1,060 of 1,060** |
| **sentences partition the doc** | **every token exactly once, in order** | **171 sentences, 0 violations** |
| **StringStore bijection** `strings[strings[s]] == s` | **exact** | **217 of 217** |
| **hash == MurmurHash64A(UTF-8, seed 1)**, independent implementation | **exact** | **217 of 217** |
| **`Doc.to_bytes`/`from_bytes`**, 10 annotation attributes + entities | **identical** | **0 of 120 differ** |
| **`DocBin` round trip** | **identical** | **0 of 120 differ** |
| **`nlp.pipe` at batch 1, 7, 64, 1000 vs `nlp()` one at a time** | **identical annotations** | **0 docs differ**; tensor max\|diff\| recorded: 0 |
| **second pass in one process** | **identical** | **0 of 120 differ** |
| **PhraseMatcher on planted phrase** | **exactly the planted char spans** | **17 of 17**, none extra |
| **Matcher `graviton` + digit** | **exactly the planted char spans** | **17 of 17**; `Graviton four` not matched |
| **static vectors** | **`(0, 0)`, and `has_vector` True** | **(0, 0)**, True (96-d from `doc.tensor`) |
| entity labels / POS | recorded, never asserted | PERSON 170, ORG 81, GPE 63, DATE 35, CARDINAL 33, MONEY 17 |

**x86_64 (`c8i`): every row above is identical.** The same spec on the env's linux-64 twin
(`envs-x86_64/nlp-60b4d70cf1b7`, installed offline, all 91 versions identical to arm64) gives a
`score.tsv` byte-identical to Graviton4's, down to the recorded entity counts, POS counts and the
0.8840 context similarity, and the same suite outcome: 4,855 accounted, 3,591 / 1,233 / 24 / 7,
0 failed, 5 subtests. The small pipeline's inference is not chip-sensitive at the precision it
reports.

### The suite is the verdict, with every test counted

spaCy ships its tests inside the package, so the suite is the
[reference-from-tests](../../practices/reference-from-tests.md) shape, run with spaCy's own CI
line and its `setup.cfg` pytest settings (`pytest.ini`, transcribed: `filterwarnings = error`).
The counting copies [scikit-learn](../scikit-learn/README.md): collection in a separate
`--collect-only` process, every report recorded from inside pytest (`account.py`), and an exact
check that each collected test produced one outcome. One wrinkle is new here: pytest 9 reports
each unittest `subTest` as an extra call report on the same test id, so
`serialize/test_resource_warning.py` reads as one test with six outcomes unless subtest reports
are counted on their own. They are, and their count must equal the summary's `5 subtests passed`.

The 1,233 skips are explained, not waved through: **667 are `--slow`**, which spaCy's CI also
leaves off; **532 are optional tokenizer backends** this env does not carry (`pymorphy3` 290,
`sudachipy` 120, `pyvi` 89, `natto` 17, `jieba` 11, `pythainlp` 3, `spacy_pkuseg` 2); **3 need
`git`**; 1 needs a GPU; the rest are skips upstream wrote into the tests ("step_through API was
removed", "No longer supported"). None of them hides an arm64 problem. One non-strict `xfail` passes
on some runs and not others (24/7 on the verifying run, 25/6 on an earlier one), which is why
xfailed and xpassed are recorded, not asserted.

### Why nothing the model predicts is asserted

A trained pipeline's labels can change with retraining and nothing is broken, so a band on entity
counts would be a check on the weights, not on spaCy. Every assertion above is a claim the library
must satisfy on any text with any model: offsets index the text, serialization loses nothing,
batching cannot change an answer, a matcher finds what was planted and nothing else. The entity
and POS counts are recorded so a reader can see the model ran.

The hash check is a cross-code identity. spaCy's `StringStore` keys every string by a 64-bit hash;
the recipe recomputes it with a pure-Python MurmurHash64A written from the reference C, not from
spaCy, and every string agrees. A changed hash function would silently break every saved `Doc`,
`DocBin` and model a user has.

The matcher checks use planted truth: the target phrase is placed in the text and its character
span computed from the string, before spaCy sees it. The controls must not match —
`single-cell sequencing` (the hyphen splits into its own token) and `Graviton four` (not a digit) —
so "exactly the planted spans" is shown to be able to fail.

### Pins

| | |
|---|---|
| data | **none staged** — 120 texts from 7 templates, filled by `random.Random(20261016)` |
| test requirements | `pytest 9.1.1`, `pluggy 1.6.0`, `iniconfig 2.3.1`, `pytest-timeout 1.4.2`, `mock 2.0.0` (+ `pbr 7.1.3`, `six 1.17.0`), `hypothesis 6.155.7` (+ `sortedcontainers 2.4.0`), `ml_datasets 0.2.1` — pure-Python wheels from PyPI by exact version, each inside spaCy's `requirements.txt` bounds at `release-v3.8.16`, sha256-pinned in `stage-inputs.sh` and installed offline |
| checks | `identities.py`, `account.py`, `suite_check.py`, `pytest.ini`, pinned by sha256 |
| image | `quay.io/aarchsci/nlp@sha256:436dc0382893…` — spaCy 3.8.16, `en_core_web_sm` 3.8.0, thinc 8.3.13, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`sha256:b04882e5c37c…`), so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=spacy
spawn task run --spec "$(make -s spec RECIPE=spacy)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/spacy/r1/suite-summary.txt" -
```

Fails on a pin mismatch, the test requirements not installing from the staged wheels, any failed or
erroring test or warning, a collected test with no outcome or more than one, a report for a test
that was never collected, a summary that does not equal tests plus collection-time skips, subtests
that do not match the summary, fewer than 4,800 tests collected, a text that does not reproduce from its tokens, a token or span whose
offsets do not index its text, sentences that do not partition the doc, a string that does not
round-trip or hashes differently from MurmurHash64A, a doc changed by serialization, batching or a
second pass, a matcher result that is not exactly the planted set, missing controls, or static
vectors appearing in `en_core_web_sm`. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**The 667 `--slow` tests and the 532 backend-dependent ones** (above): the first are off in
spaCy's CI too, and the second need tokenizer packages this env does not carry. **spaCy's other CI
steps** (download, convert, debug, train and assemble CLIs), which fetch models at run time.
**Training** (`spacy train`, config, GPU) and
**accuracy** against a labelled corpus. **Larger models and transformers** (`md`, `lg`, `trf`), not
in this env. **Multiprocess `nlp.pipe(n_process=...)`**.

</details>
