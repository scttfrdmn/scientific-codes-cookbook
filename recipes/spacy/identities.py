#!/usr/bin/env python3
"""spaCy on Graviton4: what the library must satisfy to be correct, not what the model predicts.

A trained pipeline's labels can change with retraining without anything being broken, so no
prediction is asserted. Every assertion below holds whatever the weights are:

 1. Tokenization is lossless: joining text_with_ws reproduces the input byte for byte, and every
    token's character offset indexes its own text.
 2. Spans (sentences, entities, noun chunks) carry char offsets that round-trip through
    doc.char_span to the same token range; sentences partition the doc exactly once.
 3. StringStore is a bijection on the strings it holds, and its hash is MurmurHash64A (seed 1)
    of the UTF-8 bytes -- checked against an independent pure-Python implementation.
 4. Serialization is lossless: Doc.to_bytes/from_bytes and DocBin preserve every annotation.
 5. Batching must not change answers: nlp.pipe over a batch == nlp() one text at a time, and two
    passes in one process agree exactly.
 6. Matcher and PhraseMatcher return exactly the planted spans, and a planted near-miss control
    never matches.
 7. en_core_web_sm ships no static vectors: vectors.shape == (0, 0), yet has_vector is True
    (the context tensor from tok2vec stands in), so .similarity is not word-vector similarity.

Model outputs (entity labels, POS counts) are recorded, never asserted. Run with `python3 -u`.
"""
import random
import struct
import sys

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    import numpy as np
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite" % name)


import numpy as np
import spacy
from spacy.attrs import (DEP, ENT_IOB, ENT_TYPE, HEAD, LEMMA, MORPH, ORTH, POS, SENT_START,
                         TAG)
from spacy.matcher import Matcher, PhraseMatcher
from spacy.tokens import Doc, DocBin

ver("spacy", lambda: spacy.__version__)
ver("thinc", lambda: __import__("thinc").__version__)
ver("numpy", lambda: np.__version__)
ver("python", lambda: sys.version.split()[0])

nlp = spacy.load("en_core_web_sm")
ver("model", lambda: "%s %s (spacy_version %s)" % (nlp.meta.get("name"), nlp.meta.get("version"),
                                                    nlp.meta.get("spacy_version")))
ver("pipeline", lambda: ",".join(nlp.pipe_names))

# ------------------------------------------------------------------ the corpus (generated)
SEED = 20261016
rng = random.Random(SEED)
PEOPLE = ["Ada Lovelace", "Alan Turing", "Grace Hopper", "Marie Curie", "Rosalind Franklin"]
PLACES = ["Seattle", "Paris", "New York", "Nairobi", "São Paulo", "Zürich"]
ORGS = ["the National Science Foundation", "Amazon Web Services", "the European Space Agency"]
VERBS = ["visited", "wrote to", "met", "funded", "criticised"]
PLANT = "single cell sequencing"          # PhraseMatcher target (LOWER)
NEAR = "single-cell sequencing"           # control: the tokenizer splits the hyphen -> no match
TEMPL = [
    "{p} {v} {o} in {pl} on {d} March {y}.",
    "In {y}, {p} said: \"{pl} is wonderful!\"  Nobody agreed.",
    "Dr. {p}'s grant ($ {n},000) didn't cover {pl}'s costs -- e.g. travel, i.e. flights.",
    "The {o} report (see https://example.org/r/{n}?q=1) cites {p} {n} times.\t{pl} too.",
    "Emoji test 🧬 and accents: café, naïve, Ångström; {p} wasn't there.\n\nNew paragraph.",
    "We ran Graviton {g} jobs with {PLANT} data; {p} reviewed it.",
    "Contrast: {NEAR} is hyphenated, and Graviton four is spelled out.   ",
]
texts, planted_phrase, planted_grav = [], [], []
for i in range(120):
    t = TEMPL[i % len(TEMPL)].format(
        p=rng.choice(PEOPLE), v=rng.choice(VERBS), o=rng.choice(ORGS), pl=rng.choice(PLACES),
        d=rng.randint(1, 28), y=rng.randint(1900, 2026), n=rng.randint(2, 99),
        g=rng.randint(2, 5), PLANT=PLANT.upper() if i % 2 else PLANT, NEAR=NEAR)
    # planted truth by CHARACTER position, computed from the string, not from spaCy
    lo = t.lower()
    k = lo.find(PLANT)
    planted_phrase.append([] if k < 0 else [(k, k + len(PLANT))])
    k = t.find("Graviton ")
    if k >= 0 and t[k + 9].isdigit():
        planted_grav.append([(k, k + 10)])
    else:
        planted_grav.append([])
    texts.append(t)
n_plant = sum(len(x) for x in planted_phrase)
n_grav = sum(len(x) for x in planted_grav)
rec("corpus", "%d generated texts, %d chars; %d planted phrases, %d planted 'Graviton <digit>'"
    % (len(texts), sum(map(len, texts)), n_plant, n_grav))

ATTRS = [ORTH, TAG, POS, DEP, HEAD, LEMMA, ENT_IOB, ENT_TYPE, MORPH, SENT_START]


def ann(doc):
    """Every annotation the pipeline writes, as one integer array (hashes are exact)."""
    return doc.to_array(ATTRS)


docs = [nlp(t) for t in texts]
n_tok = sum(len(d) for d in docs)

# ========================================= 1. lossless tokenization and char offsets
bad_join = [i for i, (t, d) in enumerate(zip(texts, docs))
            if "".join(tok.text_with_ws for tok in d) != t or d.text != t]
rec("tokenization_lossless", "%d of %d texts reproduce byte for byte from text_with_ws (%d tokens)"
    % (len(texts) - len(bad_join), len(texts), n_tok))
if bad_join:
    die("text_with_ws does not reproduce text %d: %r" % (bad_join[0], texts[bad_join[0]]))
bad_off = sum(1 for d in docs for tok in d if d.text[tok.idx:tok.idx + len(tok)] != tok.text)
rec("token_offsets", "%d of %d tokens with doc.text[idx:idx+len] != token.text" % (bad_off, n_tok))
if bad_off:
    die("%d tokens do not index their own text" % bad_off)
# offsets strictly increasing and gap-free: next idx == idx + len(text_with_ws)
gaps = sum(1 for d in docs for a, b in zip(d[:-1], d[1:]) if b.idx != a.idx + len(a.text_with_ws))
rec("token_contiguity", "%d gaps or overlaps between consecutive tokens" % gaps)
if gaps:
    die("%d token pairs are not contiguous" % gaps)

# ========================================= 2. spans round-trip; sentences partition
n_span, bad_span = 0, 0
for d in docs:
    spans = list(d.sents) + list(d.ents) + list(d.noun_chunks)
    for s in spans:
        n_span += 1
        cs = d.char_span(s.start_char, s.end_char)
        if cs is None or (cs.start, cs.end) != (s.start, s.end) or \
                d.text[s.start_char:s.end_char] != s.text:
            bad_span += 1
rec("span_char_roundtrip", "%d of %d spans (sents + ents + noun_chunks) round-trip through "
    "char_span" % (n_span - bad_span, n_span))
if bad_span:
    die("%d spans do not round-trip through their char offsets" % bad_span)
bad_part = 0
for d in docs:
    cov = [0] * len(d)
    prev_end = 0
    for s in d.sents:
        if s.start != prev_end:
            bad_part += 1
        prev_end = s.end
        for i in range(s.start, s.end):
            cov[i] += 1
    if prev_end != len(d) or any(c != 1 for c in cov):
        bad_part += 1
n_sents = sum(len(list(d.sents)) for d in docs)
rec("sentence_partition", "%d sentences; %d docs where sentences fail to cover every token "
    "exactly once, in order" % (n_sents, bad_part))
if bad_part:
    die("sentences do not partition the doc in %d places" % bad_part)


# ========================================= 3. StringStore: bijection + MurmurHash64A
def murmur64a(data, seed=1):
    """MurmurHash64A, written from the reference C (Austin Appleby), not from spaCy."""
    m, r, mask = 0xc6a4a7935bd1e995, 47, (1 << 64) - 1
    h = (seed ^ (len(data) * m)) & mask
    nb = len(data) // 8
    for (k,) in struct.iter_unpack("<Q", data[:nb * 8]):
        k = (k * m) & mask
        k ^= k >> r
        k = (k * m) & mask
        h ^= k
        h = (h * m) & mask
    tail = data[nb * 8:]
    for i in range(len(tail) - 1, -1, -1):
        h ^= tail[i] << (8 * i)
    if tail:
        h = (h * m) & mask
    h ^= h >> r
    h = (h * m) & mask
    h ^= h >> r
    return h


strings = sorted({tok.text for d in docs for tok in d} | {tok.lemma_ for d in docs for tok in d})
ss = nlp.vocab.strings
bad_bij = [s for s in strings if ss[ss[s]] != s or ss.as_string(ss[s]) != s]
rec("stringstore_bijection", "%d of %d distinct strings: strings[strings[s]] == s"
    % (len(strings) - len(bad_bij), len(strings)))
if bad_bij:
    die("StringStore does not round-trip %r" % bad_bij[0])
bad_hash = [s for s in strings if ss[s] != murmur64a(s.encode("utf-8"))]
rec("stringstore_hash", "%d of %d strings hash to MurmurHash64A(utf-8, seed=1) from an "
    "independent implementation" % (len(strings) - len(bad_hash), len(strings)))
if bad_hash:
    die("StringStore hash of %r is %d, MurmurHash64A gives %d"
        % (bad_hash[0], ss[bad_hash[0]], murmur64a(bad_hash[0].encode("utf-8"))))
# the control: one changed byte must change the hash
if murmur64a("Zürich".encode("utf-8")) == murmur64a("Zurich".encode("utf-8")):
    die("the reference hash does not distinguish one changed byte")

# ========================================= 4. serialization is lossless
bad_bytes = 0
for d in docs:
    d2 = Doc(nlp.vocab).from_bytes(d.to_bytes())
    if d2.text != d.text or not np.array_equal(ann(d2), ann(d)) or \
            [(e.start_char, e.end_char, e.label_) for e in d2.ents] != \
            [(e.start_char, e.end_char, e.label_) for e in d.ents]:
        bad_bytes += 1
rec("doc_bytes_roundtrip", "%d of %d docs differ after to_bytes/from_bytes (%d attributes)"
    % (bad_bytes, len(docs), len(ATTRS)))
if bad_bytes:
    die("%d docs changed through Doc.to_bytes/from_bytes" % bad_bytes)
blob = DocBin(store_user_data=True, docs=docs).to_bytes()
back = list(DocBin().from_bytes(blob).get_docs(nlp.vocab))
bad_bin = sum(1 for a, b in zip(docs, back)
              if a.text != b.text or not np.array_equal(ann(a), ann(b)))
rec("docbin_roundtrip", "%d docs in %d bytes; %d differ after the round trip"
    % (len(back), len(blob), bad_bin + abs(len(back) - len(docs))))
if len(back) != len(docs) or bad_bin:
    die("DocBin round trip changed %d docs (or the count: %d -> %d)"
        % (bad_bin, len(docs), len(back)))

# ========================================= 5. batching and repetition do not change answers
ref = [ann(d) for d in docs]
ref_t = [d.tensor for d in docs]
results = {}
for bs in (1, 7, 64, 1000):
    piped = list(nlp.pipe(texts, batch_size=bs))
    nd = sum(1 for a, b in zip(ref, piped) if not np.array_equal(a, ann(b)))
    tdiff = max(float(np.max(np.abs(a - b.tensor))) if a.size else 0.0
                for a, b in zip(ref_t, piped))
    finite("the batched tensor", *[b.tensor for b in piped])
    results[bs] = (nd, tdiff)
rec("pipe_vs_single", "; ".join("batch_size=%d: %d docs differ, tensor max|diff| %.3e"
                                % (bs, nd, td) for bs, (nd, td) in results.items()))
bad = [bs for bs, (nd, _) in results.items() if nd]
if bad:
    die("nlp.pipe(batch_size=%d) changed the annotations of %d docs versus nlp() one at a time"
        % (bad[0], results[bad[0]][0]))
again = [ann(nlp(t)) for t in texts]
nrep = sum(1 for a, b in zip(ref, again) if not np.array_equal(a, b))
rec("repeat_in_process", "%d of %d docs differ on a second pass" % (nrep, len(texts)))
if nrep:
    die("a second pass in the same process changed %d docs" % nrep)

# ========================================= 6. matchers return exactly the planted spans
pm = PhraseMatcher(nlp.vocab, attr="LOWER")
pm.add("PLANT", [nlp.make_doc(PLANT)])
mt = Matcher(nlp.vocab)
mt.add("GRAV", [[{"LOWER": "graviton"}, {"IS_DIGIT": True}]])
miss_p = miss_g = 0
got_p = got_g = 0
for d, want_p, want_g in zip(docs, planted_phrase, planted_grav):
    hp = sorted((d[s:e].start_char, d[s:e].end_char) for _, s, e in pm(d))
    hg = sorted((d[s:e].start_char, d[s:e].end_char) for _, s, e in mt(d))
    got_p += len(hp)
    got_g += len(hg)
    miss_p += hp != sorted(want_p)
    miss_g += hg != sorted(want_g)
rec("phrasematcher", "%d matches vs %d planted; %d docs where the match set != planted set "
    "(control '%s' and 'Graviton four' present in %d texts)"
    % (got_p, n_plant, miss_p, NEAR, sum(NEAR in t for t in texts)))
rec("matcher", "%d matches vs %d planted 'Graviton <digit>'; %d docs differ" % (got_g, n_grav, miss_g))
if miss_p or got_p != n_plant:
    die("PhraseMatcher returned %d matches for %d planted phrases (%d docs differ)"
        % (got_p, n_plant, miss_p))
if miss_g or got_g != n_grav:
    die("Matcher returned %d matches for %d planted patterns (%d docs differ)"
        % (got_g, n_grav, miss_g))
if not any(NEAR in t for t in texts) or not any("Graviton four" in t for t in texts):
    die("the near-miss controls are absent, so 'nothing else matched' shows nothing")

# ========================================= 7. no static vectors in en_core_web_sm
vshape = tuple(nlp.vocab.vectors.shape)
tok0 = docs[0][0]
has_vec = bool(tok0.has_vector)
rec("static_vectors", "vocab.vectors.shape %s; token.has_vector %s (vector length %d, from "
    "doc.tensor)" % (vshape, has_vec, len(tok0.vector)))
if vshape != (0, 0):
    die("en_core_web_sm %s ships static vectors %s; this recipe's caveat is wrong"
        % (nlp.meta.get("version"), vshape))
if not has_vec:
    die("has_vector is False; the caveat about context-tensor similarity no longer applies")
finite("token vectors", tok0.vector)

# ------------------------------------------------------------------ recorded, never asserted
from collections import Counter
ents = Counter(e.label_ for d in docs for e in d.ents)
pos = Counter(t.pos_ for d in docs for t in d)
rec("observed_entities", ", ".join("%s %d" % kv for kv in ents.most_common()))
rec("observed_pos_top", ", ".join("%s %d" % kv for kv in pos.most_common(8)))
rec("observed_example", " | ".join("%s/%s" % (e.text, e.label_) for e in docs[0].ents))
ver("observed_similarity_ctx", lambda: "%.4f" % nlp("a cat").similarity(nlp("a dog")))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k_, v in ROWS:
        fh.write("%s\t%s\n" % (k_, v))

print("SPACY IDENTITIES OK")
