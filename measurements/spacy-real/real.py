#!/usr/bin/env python3
"""en_core_web_sm over a block of PMC Open Access full text, one process per core.
    real.py <shard dir> <out dir> <workers> [first N shards]

Each article's body (the text after its third header rule) is split into its non-empty lines -- PMC's
paragraphs -- and every paragraph is one Doc. Per paragraph, exact: the tokens reproduce the text, each
token's offsets index it, tokens are contiguous, sentences partition the doc. Per article, exact: DocBin
round trip changes nothing; and the first article of each shard, re-run one paragraph at a time, matches
nlp.pipe. Every distinct string a worker interns must round-trip and hash to MurmurHash64A. Per article,
digests of each annotation layer are written so chips can be compared layer by layer.
"""
import collections
import gzip
import hashlib
import json
import multiprocessing as mp
import itertools
import os
import re
import resource
import struct
import sys
import time

# PMC's header rules: 30 '=' on a line; the one that opens the body is wrapped in U+009F
RULE = re.compile("^\x9f?={30}\x9f?$", re.M)


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


NLP, CHECKED, SYMBOLS = None, set(), {}


def init():
    global NLP, SYMBOLS
    import spacy
    from spacy.symbols import IDS
    SYMBOLS = dict(IDS)   # spaCy's fixed symbol table (POS, dep labels, attribute names): ids, not hashes
    NLP = spacy.load("en_core_web_sm")
    NLP.max_length = 10 ** 8


def body(text):
    """The article body: everything after the third header rule (journal, article, licence blocks)."""
    m = list(itertools.islice(RULE.finditer(text), 3))
    return text[m[2].end() + 1:] if len(m) == 3 else None


def layers(d):
    return {
        "tok": "|".join("%d,%d,%d" % (t.idx, len(t.text), len(t.whitespace_)) for t in d),
        "lemma": "\x1f".join(t.lemma_ for t in d),
        "tag": " ".join(t.tag_ for t in d),
        "pos": " ".join(t.pos_ for t in d),
        "morph": "\x1f".join(str(t.morph) for t in d),
        "dep": " ".join("%d:%s" % (t.head.i - t.i, t.dep_) for t in d),
        "sent": "".join("1" if t.is_sent_start else "0" for t in d),
        "ent": " ".join("%s%s" % (t.ent_iob_, t.ent_type_) for t in d),
    }


def same(a, b):
    return len(a) == len(b) and layers(a) == layers(b) and a.text == b.text


def shard(path):
    from spacy.tokens import DocBin
    t0 = time.time()
    arts, fails = [], []
    cnt = {"pos": collections.Counter(), "ent": collections.Counter(), "tag": collections.Counter()}
    st = collections.Counter()
    longest = 0
    first = True
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            b = body(r["text"])
            if b is None:
                st["articles_without_body"] += 1
                b = r["text"]
            paras = [p for p in b.split("\n") if p.strip()]
            longest = max([longest] + [len(p) for p in paras])
            tp = time.time()
            docs = list(NLP.pipe(paras, batch_size=64))
            st["pipe_ms"] += int((time.time() - tp) * 1000)
            dig = collections.defaultdict(hashlib.sha256)
            ntok = nsent = nent = 0
            for p, d in zip(paras, docs):
                if "".join(t.text_with_ws for t in d) != p:
                    fails.append("%s: a paragraph does not reproduce from its tokens" % r["key"])
                pos = 0
                for t in d:
                    if p[t.idx:t.idx + len(t.text)] != t.text or t.idx != pos:
                        fails.append("%s: token %d offsets do not index the text contiguously" % (r["key"], t.i))
                        break
                    pos = t.idx + len(t.text_with_ws)
                seen = 0
                for s in d.sents:
                    if s.start != seen:
                        fails.append("%s: sentences do not partition the doc" % r["key"])
                    seen = s.end
                    nsent += 1
                if seen != len(d):
                    fails.append("%s: sentences do not cover the doc" % r["key"])
                for k, v in layers(d).items():
                    dig[k].update(v.encode("utf-8") + b"\n")
                ntok += len(d)
                nent += len(d.ents)
                for t in d:
                    cnt["pos"][t.pos_] += 1
                    cnt["tag"][t.tag_] += 1
                for e in d.ents:
                    cnt["ent"][e.label_] += 1
            # DocBin round trip, every article
            db = DocBin(store_user_data=False)
            for d in docs:
                db.add(d)
            blob = db.to_bytes()
            st["docbin_bytes"] += len(blob)
            back = list(DocBin().from_bytes(blob).get_docs(NLP.vocab))
            if len(back) != len(docs) or not all(same(a, c) for a, c in zip(docs, back)):
                fails.append("%s: DocBin round trip changed a doc" % r["key"])
            # batching: one paragraph at a time == nlp.pipe, on the first article of each shard
            if first and paras:
                first = False
                one = [NLP(p) for p in paras]
                st["batch1_docs_checked"] += len(one)
                if not all(same(a, c) for a, c in zip(docs, one)):
                    fails.append("%s: nlp(p) one at a time differs from nlp.pipe" % r["key"])
            # every new string this worker interned: bijection, and spaCy's hash == MurmurHash64A
            ss = NLP.vocab.strings
            new = {x for d in docs for t in d for x in (t.text, t.lemma_, t.norm_, t.tag_, t.dep_, t.ent_type_)} - CHECKED
            for x in new:
                h = ss[x]
                if x in SYMBOLS:
                    st["strings_symbols"] += 1
                if ss[h] != x or h != (SYMBOLS[x] if x in SYMBOLS else murmur64a(x.encode("utf-8"))):
                    fails.append("%s: string %r does not round-trip or hash to MurmurHash64A" % (r["key"], x))
                    break
            CHECKED.update(new)
            st["strings_checked"] += len(new)
            st["articles"] += 1
            st["paragraphs"] += len(paras)
            st["chars"] += len(b)
            st["tokens"] += ntok
            st["sentences"] += nsent
            st["entities"] += nent
            arts.append({"key": r["key"], "paras": len(paras), "tokens": ntok, "sents": nsent, "ents": nent,
                         **{k: v.hexdigest()[:20] for k, v in sorted(dig.items())}})
    st["longest_paragraph_chars"] = longest
    return os.path.basename(path), arts, {k: dict(v) for k, v in cnt.items()}, dict(st), fails[:20], len(fails), \
        time.time() - t0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def main():
    src, out, nw = sys.argv[1], sys.argv[2], int(sys.argv[3])
    shards = sorted(f for f in os.listdir(src) if f.startswith("shard-") and f.endswith(".jsonl.gz"))
    if len(sys.argv) > 4:
        shards = shards[:int(sys.argv[4])]
    want = {}
    for line in open(os.path.join(src, "manifest.tsv")).read().splitlines()[1:]:
        c = line.split("\t")
        if "shard-%s.jsonl.gz" % c[0] in shards:
            want[c[1]] = c[0]
    print("shards %d, articles %d, workers %d" % (len(shards), len(want), nw), flush=True)
    ctx = mp.get_context("forkserver")
    t0 = time.time()
    arts, tot, cnt, fails, nfail, per = [], collections.Counter(), collections.defaultdict(collections.Counter), [], 0, []
    longest = 0
    # biggest shards first, so the tail is short
    order = sorted(shards, key=lambda f: -os.path.getsize(os.path.join(src, f)))
    with ctx.Pool(nw, initializer=init) as pool:
        for i, (name, a, c, st, f, nf, wall, rss) in enumerate(
                pool.imap_unordered(shard, [os.path.join(src, s) for s in order], chunksize=1)):
            arts += a
            longest = max(longest, st.pop("longest_paragraph_chars", 0))
            tot.update(st)
            for k, v in c.items():
                cnt[k].update(v)
            fails += f
            nfail += nf
            per.append((name, wall, rss, st.get("tokens", 0)))
            if i % 16 == 0:
                print("  %3d/%d shards  %6.0f s  %d tokens so far" % (i + 1, len(shards), time.time() - t0, tot["tokens"]),
                      flush=True)
    wall = time.time() - t0
    arts.sort(key=lambda r: int(r["key"][3:].split(".")[0]))
    with open(os.path.join(out, "articles.jsonl"), "w") as fh:
        for r in arts:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    with open(os.path.join(out, "shards.tsv"), "w") as fh:
        fh.write("shard\twall_s\tmaxrss_kib\ttokens\n")
        for name, w, rss, n in sorted(per):
            fh.write("%s\t%.1f\t%d\t%d\n" % (name, w, rss, n))
    got = [r["key"] for r in arts]
    check = []

    def rec(k, v):
        v = v if isinstance(v, str) else str(v)
        check.append("%s\t%s" % (k, v))
        print("%s\t%s" % (k, v), flush=True)

    rec("pipeline_wall_s", "%.1f" % wall)
    for k in ("articles", "paragraphs", "chars", "tokens", "sentences", "entities", "docbin_bytes",
              "batch1_docs_checked", "strings_checked", "strings_symbols", "articles_without_body"):
        rec(k, tot.get(k, 0))
    rec("tokens_per_s", "%.0f" % (tot["tokens"] / wall))
    rec("pipe_share_of_worker_time", "%.3f" % (tot["pipe_ms"] / 1000 / sum(p[1] for p in per)))
    rec("longest_paragraph_chars", longest)
    rec("max_worker_rss_gib", "%.2f" % (max(p[2] for p in per) / 2 ** 20))
    for k in ("pos", "ent"):
        rec("%s_counts" % k, json.dumps(dict(sorted(cnt[k].items(), key=lambda x: -x[1])), sort_keys=False))
    rec("tag_counts", json.dumps(dict(sorted(cnt["tag"].items())), sort_keys=True))
    ok = True
    if sorted(got) != sorted(want) or len(set(got)) != len(got):
        ok = False
        rec("FAIL", "articles processed != manifest (%d vs %d, %d distinct)" % (len(got), len(want), len(set(got))))
    else:
        rec("every_article_once", "%d of %d" % (len(got), len(want)))
    rec("check_failures", nfail)
    for f in fails:
        rec("FAIL", f)
    if nfail:
        ok = False
    # multiprocess: nlp.pipe(n_process=4) == serial, on 2,000 paragraphs from the first shard
    import spacy
    nlp = spacy.load("en_core_web_sm")
    paras = []
    with gzip.open(os.path.join(src, shards[0]), "rt", encoding="utf-8") as fh:
        for line in fh:
            t = json.loads(line)["text"]
            b = body(t) or t
            paras += [p for p in b.split("\n") if p.strip()]
            if len(paras) >= 2000:
                break
    paras = paras[:2000]
    a = list(nlp.pipe(paras, batch_size=64))
    b = list(nlp.pipe(paras, batch_size=64, n_process=4))
    nd = sum(not same(x, y) for x, y in zip(a, b)) + abs(len(a) - len(b))
    rec("n_process4_vs_serial", "%d of %d paragraphs differ" % (nd, len(paras)))
    if nd:
        ok = False
    with open(os.path.join(out, "check.txt"), "w") as fh:
        fh.write("\n".join(check) + "\n")
    if not ok:
        print("FAIL: a check failed")
        sys.exit(1)
    print("PMC CHECKS OK")


if __name__ == "__main__":
    main()
