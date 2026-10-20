#!/usr/bin/env python3
"""Task 00 (once, Graviton4): choose the genomes and their published lineage labels.

The truth is pango-designation's lineages.csv at v1.38 -- the curators' own lineage for each
designated sample, and the release the pinned pangolin's pangolin-data (PUSHER-v1.38) was built
from. lineages.csv names samples by their GISAID strain name, and GISAID bytes cannot be
redistributed, so the genomes come from INSDC instead: Nextstrain's open (GenBank) release at a
pinned S3 version, matched to a designated sample by name.

  route "strain": Nextstrain's `strain` == the designated taxon (most non-UK submitters use one
                  name in both databases).
  route "cog":    COG-UK deposited in ENA under a different name; Nextstrain's cog_uk_accessions
                  maps the ENA run (metadata `sra_accession`) to the GISAID name.

A sample is kept only if its name is designated exactly once, maps to exactly one INSDC record,
and that record's accession and FASTA key (the release's FASTA is keyed by `strain`, or by the
accession where there is none) each occur once. From the pool a seeded shuffle draws N; the first
SUB of them are the invariance subset. Each FASTA header written is the INSDC accession.version
alone, so no tool sees a strain name.
    choose.py <N> <SUB> <seed>     (named so as not to shadow the stdlib `select`)
"""
import csv
import gzip
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import time
import urllib.request

N, SUB, SEED = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
COMMIT = "5d58f88f6d2649de35a83951938e047006febb45"   # pango-designation tag v1.38
RAW = "https://raw.githubusercontent.com/cov-lineages/pango-designation/%s/" % COMMIT
NS = "/tmp/nextstrain"
os.makedirs("out", exist_ok=True)
t0 = time.time()


def log(*a):
    print("%7.1f  %s" % (time.time() - t0, " ".join(str(x) for x in a)), flush=True)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def zlines(path, binary=False):
    p = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, bufsize=1 << 24)
    if binary:
        yield from p.stdout
    else:
        yield from io.TextIOWrapper(p.stdout, encoding="utf-8", errors="replace", newline="\n")
    if p.wait() != 0:
        sys.exit("FAIL: zstd -dc %s exited %d" % (path, p.returncode))


pins = []
for f in ("lineages.csv", "pango_designation/alias_key.json"):
    dst = "out/" + os.path.basename(f)
    with urllib.request.urlopen(RAW + f, timeout=120) as r, open(dst, "wb") as fh:
        fh.write(r.read())
    pins.append((sha(dst), os.path.basename(f)))
    log("fetched", f, os.path.getsize(dst), "bytes at", COMMIT[:7])
for f in sorted(os.listdir(NS)):
    pins.append((sha(os.path.join(NS, f)), "nextstrain/" + f))
    log("sha256", f)

# ---- the truth labels ----
lab, seen = {}, {}
with open("out/lineages.csv", newline="") as fh:
    for row in csv.DictReader(fh):
        t = row["taxon"].strip()
        seen[t] = seen.get(t, 0) + 1
        lab[t] = row["lineage"].strip()
dup_taxa = {t for t, c in seen.items() if c > 1}
log("designated taxa", len(lab), "named more than once (dropped)", len(dup_taxa))
for t in dup_taxa:
    del lab[t]

# ---- COG-UK: ENA run accession -> GISAID name ----
run2name = {}
hdr = None
for line in zlines(NS + "/cog_uk_accessions.tsv.zst"):
    f = line.rstrip("\n").split("\t")
    if hdr is None:
        hdr = {k: i for i, k in enumerate(f)}
        ir, ig = hdr["ena_run.accession"], hdr["gisaid.secondary_accession"]
        continue
    if len(f) <= max(ir, ig):
        continue
    run, g = f[ir].strip(), f[ig].strip()
    if run and g.startswith("hCoV-19/"):
        run2name[run] = g[len("hCoV-19/"):]
log("cog_uk runs with a GISAID name", len(run2name))

# ---- the INSDC release: which records are designated samples ----
acc_n, key_n, cand = {}, {}, {}   # accession -> count ; FASTA key -> count ; taxon -> [records]
hdr = None
nrec = 0
for line in zlines(NS + "/metadata.tsv.zst"):
    f = line.rstrip("\n").split("\t")
    if hdr is None:
        hdr = {k: i for i, k in enumerate(f)}
        need = ("strain", "genbank_accession", "genbank_accession_rev", "sra_accession", "date", "country")
        miss = [k for k in need if k not in hdr]
        if miss:
            sys.exit("FAIL: metadata lacks %s" % miss)
        I = [hdr[k] for k in need]
        continue
    nrec += 1
    strain, acc, accv, sra, date, country = (f[i] if i < len(f) else "" for i in I)
    if not acc:
        continue
    acc_n[acc] = acc_n.get(acc, 0) + 1
    # the release's FASTA is keyed by `strain`, or by the accession where there is no strain
    key = strain if strain not in ("", "?") else acc
    key_n[key] = key_n.get(key, 0) + 1
    hits = []
    if strain in lab:
        hits.append((strain, "strain"))
    for r in sra.split(","):
        nm = run2name.get(r.strip())
        if nm and nm in lab and nm != strain:
            hits.append((nm, "cog"))
    for t, route in hits:
        cand.setdefault(t, []).append((accv or acc, acc, route, date, country, key))
log("INSDC records", nrec, "designated taxa matched", len(cand))

pool = []
drop_multi = drop_dupacc = drop_dupkey = 0
for t, recs in cand.items():
    uniq = {r[1] for r in recs}
    if len(uniq) != 1:
        drop_multi += 1
        continue
    r = recs[0]
    if acc_n.get(r[1], 0) != 1:
        drop_dupacc += 1
        continue
    if key_n.get(r[5], 0) != 1:
        drop_dupkey += 1
        continue
    pool.append((r[1], r[0], t, lab[t], r[2], r[3], r[4], r[5]))
pool.sort()
accs = [p[0] for p in pool]
if len(set(accs)) != len(accs):   # two taxa onto one record (one strain + one cog route)
    c = {}
    for a in accs:
        c[a] = c.get(a, 0) + 1
    pool = [p for p in pool if c[p[0]] == 1]
log("pool", len(pool), "dropped: taxon->several records", drop_multi, "duplicate accession", drop_dupacc,
    "FASTA key not unique", drop_dupkey)
if len(pool) < N:
    sys.exit("FAIL: pool %d < N %d" % (len(pool), N))

# A seeded shuffle of the whole pool, then the first N that have a sequence in the release
# (a metadata row can lack one). Deterministic given the seed and the pinned release.
order = random.Random(SEED).sample(pool, len(pool))
head = order[:N + N // 10]
want = {p[7]: p[0] for p in head}   # FASTA key -> accession

# ---- the genomes, streamed out of the 1.3 GB release (~220 GB uncompressed, one line each) ----
seqs, kseen = {}, {}
cur = None
buf = []
nseq = 0
for line in zlines(NS + "/sequences.fasta.zst", binary=True):
    if line[:1] == b">":
        if cur is not None:
            seqs[cur] = b"".join(buf).decode("ascii")
        nseq += 1
        cur = want.get(line[1:].rstrip(b"\r\n").decode("utf-8", "replace"))
        if cur is not None:
            kseen[cur] = kseen.get(cur, 0) + 1
        buf = []
    elif cur is not None:
        buf.append(line.strip())
if cur is not None:
    seqs[cur] = b"".join(buf).decode("ascii")
have = [p for p in head if seqs.get(p[0]) and kseen.get(p[0]) == 1]
pick = have[:N]
log("FASTA records", nseq, "of the first", len(head), "shuffled, with exactly one sequence", len(have))
if len(pick) < N:
    sys.exit("FAIL: only %d of the first %d shuffled samples have a sequence" % (len(pick), len(head)))

with gzip.open("out/query.fasta.gz", "wt", compresslevel=6) as q, \
        gzip.open("out/sub.fasta.gz", "wt", compresslevel=6) as s:
    for i, p in enumerate(pick):
        rec = ">%s\n%s\n" % (p[1], seqs[p[0]])
        q.write(rec)
        if i < SUB:
            s.write(rec)
with open("out/truth.tsv", "w") as fh:
    fh.write("seq\ttaxon\tdesignated\troute\tdate\tcountry\n")
    for p in pick:
        fh.write("\t".join((p[1], p[2], p[3], p[4], p[5], p[6])) + "\n")
with open("out/accessions.txt", "w") as fh:
    fh.write("".join(p[1] + "\n" for p in pick))
routes = {}
for p in pick:
    routes[p[4]] = routes.get(p[4], 0) + 1
lins = len({p[3] for p in pick})
for f in ("query.fasta.gz", "sub.fasta.gz", "truth.tsv", "accessions.txt"):
    pins.append((sha("out/" + f), f))
with open("out/inputs.sha256", "w") as fh:
    fh.write("".join("%s  %s\n" % p for p in pins))
with open("out/choose.json", "w") as fh:
    json.dump({"N": N, "SUB": SUB, "seed": SEED, "designation_commit": COMMIT,
               "designated_taxa": len(lab), "dup_taxa_dropped": len(dup_taxa), "insdc_records": nrec,
               "taxa_matched": len(cand), "pool": len(pool), "routes": routes,
               "dropped": {"taxon_several_records": drop_multi, "dup_accession": drop_dupacc,
                           "dup_fasta_key": drop_dupkey},
               "distinct_designated_lineages": lins, "wall_s": round(time.time() - t0, 1)}, fh, indent=1)
log("wrote", N, "genomes,", lins, "distinct designated lineages, routes", routes)
print("SELECT OK")
