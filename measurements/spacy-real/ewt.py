#!/usr/bin/env python3
"""en_core_web_sm on the whole of UD English-EWT r2.18 (train, dev, test), scored twice: by spaCy's own
Scorer and by the CoNLL 2018 shared-task scorer (conll18_ud_eval.py), which share no code.
    ewt.py <dir with the .conllu files and conll18_ud_eval.py> <out dir>

gold-tokenized: each gold sentence goes in as its own words (a pre-tokenized Doc), so both scorers see the
  same tokens; UPOS, XPOS (PTB tags, the tagger's own tagset) and UAS must then agree between the scorers.
raw: each EWT document's text goes in as a string, spaCy tokenizes and segments it, and the CoNLL 2018
  scorer aligns by characters; it refuses outright if the system's characters differ from the gold's,
  so it also checks that tokenization lost nothing. Token P/R/F must agree with spaCy's score_tokenization.
spaCy's dependency LABELS are ClearNLP's, not UD's, so LAS is not comparable and is not reported.
"""
import collections
import io
import json
import os
import re
import sys
import time

D, OUT = sys.argv[1], sys.argv[2]
sys.path.insert(0, D)
import conll18_ud_eval as ce  # noqa: E402
import spacy  # noqa: E402
from spacy.scorer import Scorer  # noqa: E402
from spacy.tokens import Doc  # noqa: E402
from spacy.training import Example  # noqa: E402

ROWS, FAIL = [], []


def rec(k, v):
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def check(ok, msg):
    if not bool(ok):
        FAIL.append(msg)
        print("FAIL: " + msg, flush=True)


def read(path):
    """CoNLL-U -> list of sentences: dict(doc, text, lines, tokens=[(form, space_after)], words=[...])."""
    sents, cur, doc = [], None, None
    for raw in io.open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if cur is None:
            cur = {"lines": [], "tokens": [], "words": [], "text": None}
        if line.startswith("# newdoc"):
            doc = line.split("=", 1)[1].strip() if "=" in line else line
        if line.startswith("# text ="):
            cur["text"] = line[len("# text = "):]
        if not line:
            cur["doc"] = doc
            sents.append(cur)
            cur = None
            continue
        cur["lines"].append(line)
        if line.startswith("#"):
            continue
        c = line.split("\t")
        if "." in c[0]:
            continue                                   # an enhanced empty node, not a word
        sa = ws(c[9])
        if "-" in c[0]:
            a, b = map(int, c[0].split("-"))
            cur["tokens"].append([c[1], sa, b - a + 1])
            cur["mwt_left"] = b - a + 1
            continue
        w = {"id": int(c[0]), "form": c[1], "upos": c[3], "xpos": c[4], "head": int(c[6]), "deprel": c[7]}
        if cur.get("mwt_left"):
            cur["mwt_left"] -= 1
            w["space"] = sa if cur["mwt_left"] == 0 else False
            if cur["mwt_left"] == 0:
                w["space"] = cur["tokens"][-1][1]
        else:
            cur["tokens"].append([c[1], sa, 1])
            w["space"] = sa
        cur["words"].append(w)
    return sents


def ws(misc):
    """The whitespace after a token: SpacesAfter= (UD's escaped form) wins, then SpaceAfter=No, else one space."""
    for f in misc.split("|"):
        if f.startswith("SpacesAfter="):
            v = f[len("SpacesAfter="):]
            v = v.replace("\\s", " ").replace("\\t", "\t").replace("\\n", "\n").replace("\\r", "\r")
            return re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), v)
    return "" if "SpaceAfter=No" in misc.split("|") else " "


def tok_text(tokens):
    return "".join(f + sa for f, sa, _ in tokens)


nlp = spacy.load("en_core_web_sm")
rec("spacy", spacy.__version__)
rec("model", "%s %s" % (nlp.meta.get("name"), nlp.meta.get("version")))
perf = nlp.meta.get("performance", {})
rec("model_meta_performance (OntoNotes 5, LDC-licensed, not reproducible here)",
    json.dumps({k: round(perf[k], 4) for k in ("tag_acc", "pos_acc", "dep_uas", "dep_las", "ents_f", "sents_f")
                if isinstance(perf.get(k), float)}))
summary = {}
for split in ("test", "dev", "train"):
    path = os.path.join(D, "en_ewt-ud-%s.conllu" % split)
    sents = read(path)
    nw = sum(len(s["words"]) for s in sents)
    ntok = sum(len(s["tokens"]) for s in sents)
    # ---- the gold file reproduces its own # text (checks this reader and the file together)
    bad_text = [s for s in sents if tok_text(s["tokens"]).rstrip(" ") != s["text"]]
    rec("%s_sentences_words_tokens" % split, "%d %d %d" % (len(sents), nw, ntok))
    for s in bad_text[:3]:
        print("  # text %r\n  tokens %r" % (s["text"], tok_text(s["tokens"])), flush=True)
    rec("%s_gold_text_reproduced" % split, "%d of %d" % (len(sents) - len(bad_text), len(sents)))
    check(not bad_text, "%s: %d gold sentences do not reproduce their # text" % (split, len(bad_text)))

    # ================= gold-tokenized: same words for both scorers
    t0 = time.time()
    gold_docs = [Doc(nlp.vocab, words=[w["form"] for w in s["words"]], spaces=[bool(w["space"]) for w in s["words"]],
                     pos=[w["upos"] for w in s["words"]], tags=[w["xpos"] for w in s["words"]],
                     heads=[(w["head"] - 1) if w["head"] else i for i, w in enumerate(s["words"])],
                     deps=[w["deprel"] for w in s["words"]]) for s in sents]
    inp = [Doc(nlp.vocab, words=[w["form"] for w in s["words"]], spaces=[bool(w["space"]) for w in s["words"]])
           for s in sents]
    pred = list(nlp.pipe(inp, batch_size=256))
    tg = time.time() - t0
    multi_root, n_up, n_xp, n_ua = 0, 0, 0, 0
    sys_lines, scored = [], []
    for s, g, p in zip(sents, gold_docs, pred):
        heads = [t.head.i for t in p]
        roots = [i for i, h in enumerate(heads) if h == i]
        if len(roots) > 1:                 # the parser split one gold sentence: attach extra roots to the first
            multi_root += 1
            for r in roots[1:]:
                heads[r] = roots[0]
        q = Doc(nlp.vocab, words=[t.text for t in p], spaces=[bool(t.whitespace_) for t in p],
                pos=[t.pos_ or "X" for t in p], tags=[t.tag_ for t in p], heads=heads,
                deps=[t.dep_ or "dep" for t in p])
        scored.append(Example(q, g))
        for i, (w, t) in enumerate(zip(s["words"], q)):
            n_up += t.pos_ == w["upos"]
            n_xp += t.tag_ == w["xpos"]
            n_ua += (heads[i] == ((w["head"] - 1) if w["head"] else i))
        wi = 0
        for line in s["lines"]:
            c = line.split("\t")
            if line.startswith("#") or "-" in c[0] or "." in c[0]:
                sys_lines.append(line)
                continue
            t = q[wi]
            c[3], c[4] = t.pos_, t.tag_
            c[6] = "0" if heads[wi] == wi else str(heads[wi] + 1)
            c[7] = t.dep_.lower() if heads[wi] != wi else "root"
            sys_lines.append("\t".join(c))
            wi += 1
        sys_lines.append("")
    sysf = os.path.join(OUT, "ewt-%s-goldtok.conllu" % split)
    io.open(sysf, "w", encoding="utf-8").write("\n".join(sys_lines) + "\n")
    ev = ce.evaluate(ce.load_conllu_file(path), ce.load_conllu_file(sysf))
    sc_tag = Scorer.score_token_attr(scored, "tag")["tag_acc"]
    sc_pos = Scorer.score_token_attr(scored, "pos")["pos_acc"]
    sc_uas = Scorer.score_deps(scored, "dep")["dep_uas"]
    rec("%s_goldtok_wall_s" % split, "%.1f" % tg)
    rec("%s_goldtok_multiroot_sentences" % split, multi_root)
    for name, mine, cs, sp in (("UPOS", n_up, ev["UPOS"], sc_pos), ("XPOS", n_xp, ev["XPOS"], sc_tag),
                               ("UAS", n_ua, ev["UAS"], sc_uas)):
        rec("%s_goldtok_%s" % (split, name), "conll18 %d/%d = %.6f  spaCy Scorer %.6f  recount %d" % (
            cs.correct, cs.gold_total, cs.aligned_accuracy, sp, mine))
        check(cs.gold_total == nw and cs.aligned_total == nw, "%s %s: conll18 aligned %s of %d words" % (
            split, name, cs.aligned_total, nw))
        check(cs.correct == mine, "%s %s: conll18 correct %d != recount %d" % (split, name, cs.correct, mine))
        check(abs(sp - mine / nw) <= 1e-12, "%s %s: spaCy Scorer %.15f != %d/%d" % (split, name, sp, mine, nw))
        summary["%s_goldtok_%s" % (split, name)] = cs.aligned_accuracy

    # ================= raw text: one string per EWT document
    t0 = time.time()
    docs = collections.OrderedDict()
    for s in sents:
        docs.setdefault(s["doc"], []).append(s)
    golds, texts = [], []
    for did, ss in docs.items():
        toks = [t for s in ss for t in s["tokens"]]
        words = [f for f, _, _ in toks]
        spaces = [bool(sa) for _, sa, _ in toks]
        spaces[-1] = False
        starts = []
        for s in ss:
            starts += [True] + [False] * (len(s["tokens"]) - 1)
        g = Doc(nlp.vocab, words=words, spaces=spaces, sent_starts=starts)
        golds.append(g)
        texts.append(g.text)
    preds = list(nlp.pipe(texts, batch_size=64))
    tr = time.time() - t0
    lossless = sum("".join(t.text_with_ws for t in p) == x for p, x in zip(preds, texts))
    rec("%s_raw_docs" % split, len(texts))
    rec("%s_raw_lossless" % split, "%d of %d documents reproduce from their tokens" % (lossless, len(texts)))
    check(lossless == len(texts), "%s raw: tokenization not lossless" % split)
    out, extra_roots, tp, n_pred, n_gold = [], 0, 0, 0, 0
    for g, p in zip(golds, preds):
        gs = {(t.idx, t.idx + len(t)) for t in g if not t.orth_.isspace()}
        ps = {(t.idx, t.idx + len(t)) for t in p if not t.orth_.isspace()}
        tp += len(gs & ps)
        n_pred += len(ps)
        n_gold += len(gs)
        for sent in p.sents:
            keep = [t for t in sent if not t.orth_.isspace()]
            if not keep:
                continue
            idx = {t.i: k + 1 for k, t in enumerate(keep)}

            def up(t):
                while t.i not in idx and t.head.i != t.i:
                    t = t.head
                return t
            hd = []
            for t in keep:
                h = up(t.head) if t.head.i != t.i else t
                hd.append(0 if (h.i == t.i or h.i not in idx) else idx[h.i])
            roots = [k for k, h in enumerate(hd) if h == 0]
            for k in roots[1:]:
                hd[k] = roots[0] + 1
                extra_roots += 1
            for k, t in enumerate(keep):
                out.append("\t".join([str(k + 1), t.text, t.lemma_ or "_", t.pos_ or "X", t.tag_ or "_", "_",
                                      str(hd[k]), "root" if hd[k] == 0 else (t.dep_.lower() or "dep"),
                                      "_", "_"]))
            out.append("")
    sysf = os.path.join(OUT, "ewt-%s-raw.conllu" % split)
    io.open(sysf, "w", encoding="utf-8").write("\n".join(out) + "\n")
    try:
        ev = ce.evaluate(ce.load_conllu_file(path), ce.load_conllu_file(sysf))
    except ce.UDError as e:
        check(False, "%s raw: the CoNLL 2018 scorer refused the system file: %s" % (split, e))
        continue
    st = Scorer.score_tokenization([Example(p, g) for p, g in zip(preds, golds)])
    T = ev["Tokens"]
    rec("%s_raw_wall_s" % split, "%.1f" % tr)
    rec("%s_raw_tokens" % split, "conll18 P %.6f R %.6f F1 %.6f (%d correct, gold %d, system %d)  spaCy P %.6f R %.6f F %.6f" % (
        T.precision, T.recall, T.f1, T.correct, T.gold_total, T.system_total, st["token_p"], st["token_r"], st["token_f"]))
    check((T.correct, T.gold_total, T.system_total) == (tp, n_gold, n_pred),
          "%s raw tokens: conll18 %s vs recount %s" % (split, (T.correct, T.gold_total, T.system_total), (tp, n_gold, n_pred)))
    for a, b, k in ((T.precision, st["token_p"], "P"), (T.recall, st["token_r"], "R"), (T.f1, st["token_f"], "F")):
        check(abs(a - b) <= 1e-12, "%s raw token %s: conll18 %.15f vs spaCy %.15f" % (split, k, a, b))
    check(n_gold == ntok, "%s raw: %d gold tokens in the docs, %d in the file" % (split, n_gold, ntok))
    rec("%s_raw_extra_roots_attached" % split, extra_roots)
    for k in ("Sentences", "Words", "UPOS", "XPOS", "UAS"):
        rec("%s_raw_%s_F1" % (split, k), "%.6f" % ev[k].f1)
        summary["%s_raw_%s_F1" % (split, k)] = ev[k].f1
    summary["%s_raw_Tokens_F1" % split] = T.f1
with open(os.path.join(OUT, "ewt-summary.json"), "w") as fh:
    json.dump(summary, fh, indent=1, sort_keys=True)
with open(os.path.join(OUT, "ewt-check.txt"), "w") as fh:
    fh.write("".join("%s\t%s\n" % r for r in ROWS))
    fh.write("ewt_failures\t%d\n" % len(FAIL))
if FAIL:
    print("EWT: %d FAILURES" % len(FAIL))
    sys.exit(1)
print("EWT CHECKS OK")
