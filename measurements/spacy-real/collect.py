#!/usr/bin/env python3
"""Pull each chip's evidence into results/<chip>/ and compare every chip against Graviton4.
    python3 collect.py <bucket> <run label> <task nonce>
Tokenization is rule-based, so its per-article digest is a fixed point and must be identical on every
chip. Tags, lemmas, parses, sentences and entities come out of a neural network's argmax, so they are
a path: how many articles differ in each layer is reported, not asserted."""
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime

B, RUN, NONCE = sys.argv[1:4]
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "results")
SCR = os.path.join(os.environ.get("XTMP", os.path.expanduser("~/.xtmp")), "spacy-real", "articles")
ACCT = subprocess.check_output(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                               text=True).strip()
KEEP = ["check.txt", "ewt-check.txt", "ewt-summary.json", "host.txt", "footprint.txt", "shards.tsv", "real.log",
        "ewt.log"]
chips = []
for c in ("arm64", "x86_64", "amd"):
    p, sfx = ("runs", NONCE) if c == "arm64" else ("runs-" + c, c.replace("_", "") + "-" + NONCE)
    base = "s3://%s/%s/spacy-real/%s/" % (B, p, RUN)
    if subprocess.call(["aws", "s3", "ls", base + "check.txt"], stdout=subprocess.DEVNULL) != 0:
        print("%s: no run" % c)
        continue
    os.makedirs(os.path.join(R, c), exist_ok=True)
    os.makedirs(os.path.join(SCR, c), exist_ok=True)
    for f in KEEP:
        subprocess.call(["aws", "s3", "cp", "--only-show-errors", base + f, os.path.join(R, c, f)])
    subprocess.check_call(["aws", "s3", "cp", "--only-show-errors", base + "articles.jsonl.gz",
                           os.path.join(SCR, c, "articles.jsonl.gz")])
    subprocess.call(["aws", "s3", "cp", "--only-show-errors",
                     "s3://spawn-results-%s-us-west-2/tasks/cookbook-spacy-real-%s-%s/completion.json" % (ACCT, RUN, sfx),
                     os.path.join(R, c, "completion.json")])
    chips.append(c)


def kv(p):
    d = {}
    for line in open(p, encoding="utf-8"):
        if "\t" in line:
            k, v = line.rstrip("\n").split("\t", 1)
            d[k] = v
    return d


def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")


out, fail = [], []


def line(s):
    out.append(s)
    print(s)


for c in chips:
    k, h, f = (kv(os.path.join(R, c, x)) for x in ("check.txt", "host.txt", "footprint.txt"))
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = float(f["tool_wall_s"])
    line("%-6s %-28s tpc %s  tool %5.0f s (ewt %s, pmc %s)  window %5.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
         "cgroup %s GiB  worker rss %s GiB  %s tok/s  openblas %s  %s" % (
             c, h.get("Model name"), h.get("Thread(s) per core"), tool, f["ewt_wall_s"], f["pmc_wall_s"], win, hr,
             tool / 3600 * hr, j["cost_estimate"], f.get("cgroup_peak_gib"), k.get("max_worker_rss_gib"),
             k.get("tokens_per_s"), h.get("openblas_corename"), j.get("state")))

arts = {}
for c in chips:
    with gzip.open(os.path.join(SCR, c, "articles.jsonl.gz"), "rt") as fh:
        arts[c] = {r["key"]: r for r in map(json.loads, fh)}
LAYERS = ["tok", "lemma", "tag", "pos", "morph", "dep", "sent", "ent"]
# each chip against Graviton4, then Intel against AMD: the same linux-64 build on two x86 vendors
pairs = [(chips[0], c) for c in chips[1:]] + ([("x86_64", "amd")] if {"x86_64", "amd"} <= set(chips) else [])
for ref, c in pairs:
    a, b = arts[ref], arts[c]
    if sorted(a) != sorted(b):
        fail.append("%s: a different article set" % c)
        continue
    n = len(a)
    for L in LAYERS:
        d = sum(a[x].get(L) != b[x].get(L) for x in a)
        line("%s vs %s: %-5s identical on %d of %d articles" % (c, ref, L, n - d, n))
        if L == "tok" and d:
            fail.append("%s: tokenization differs on %d articles" % (c, d))
    for x in ("tokens", "sents", "ents"):
        d = sum(a[k][x] != b[k][x] for k in a)
        line("%s vs %s: %-6s count identical on %d of %d articles; total %d vs %d" % (
            c, ref, x, n - d, n, sum(b[k][x] for k in b), sum(a[k][x] for k in a)))
    ka, kb = kv(os.path.join(R, ref, "check.txt")), kv(os.path.join(R, c, "check.txt"))
    for x in ("pos_counts", "ent_counts", "tag_counts"):
        pa, pb = json.loads(ka[x]), json.loads(kb[x])
        diff = {t: pb.get(t, 0) - pa.get(t, 0) for t in set(pa) | set(pb) if pb.get(t, 0) != pa.get(t, 0)}
        line("%s vs %s: %s totals %s" % (c, ref, x, "identical" if not diff else json.dumps(dict(sorted(diff.items())))))
    ea = json.load(open(os.path.join(R, ref, "ewt-summary.json")))
    eb = json.load(open(os.path.join(R, c, "ewt-summary.json")))
    same = [x for x in ea if ea[x] == eb.get(x)]
    line("%s vs %s: EWT scores identical on %d of %d" % (c, ref, len(same), len(ea)))
    for x in sorted(ea):
        if ea[x] != eb.get(x):
            line("    %-28s %.6f vs %.6f" % (x, eb.get(x), ea[x]))
for f in fail:
    line("FAIL " + f)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
