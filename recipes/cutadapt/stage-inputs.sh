#!/usr/bin/env bash
# Stage cutadapt's planted-truth fixture: paired 100 bp reads where every read is a KNOWN insert
# followed by a known adapter at a known position, plus the exact FASTQ a correct trimmer must
# write for them, plus a separate set of reads for quality trimming.
#
# Generated HERE, not in the task, and pinned by sha256: awk rand() draws different streams in
# aarchbio's image and bioconda's BusyBox x86 image, so in-task generation would not give the
# two chips the same bytes. Generated once, the bytes are the same anywhere.
#
# The expected output is written by this script from the planting, never from a cutadapt run,
# so "cutadapt's output == expected" is a claim about cutadapt rather than about itself.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=cutadapt does this}"
REGION="${AWS_REGION:-us-west-2}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

python3 - <<'PY'
import random, sys
rng = random.Random(20261016)
AD1 = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC"   # TruSeq read 1 adapter (34 bp)
AD2 = "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT"    # TruSeq read 2 adapter (33 bp)
RL, E, OMIN, MINLEN = 100, 0.1, 3, 10         # read length, -e, -O, -m
COMP = str.maketrans("ACGT", "TGCA")
def rc(s): return s.translate(COMP)[::-1]
def rs(n): return "".join(rng.choice("ACGT") for _ in range(n))
def rq(n): return "".join(chr(33 + rng.randint(30, 40)) for _ in range(n))
def allowed(n): return int(E * n)

def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]

def semiglobal(ad, read):
    """min edits to align ALL of ad to any substring of read (read gaps free at both ends)."""
    prev = [0] * (len(read) + 1)
    for i, x in enumerate(ad, 1):
        cur = [i]
        for j, y in enumerate(read, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return min(prev)

def tail_hits(read, ad, planted=0, smax=20):
    """Suffix lengths s (3..smax) at which the read's 3' end matches an adapter prefix within
    cutadapt's error allowance AND would compete with the planted overlap (`planted` bp, exact):
    cutadapt scores +1 match, -1 mismatch, -2 indel, so an alternative scoring below the planted
    exact match (e.g. the planted match shifted by one indel) cannot be chosen over it."""
    hits = []
    for s in range(OMIN, smax + 1):
        if s == planted:
            continue
        suf = read[-s:]
        k = allowed(s)
        if k == 0:
            d = 0 if ad.startswith(suf) else None
        else:
            d = min(lev(suf, ad[:l]) for l in range(max(1, s - k), min(len(ad), s + k) + 1))
            d = d if d <= k else None
        if d is not None and s - 2 * d >= planted:
            hits.append(s)
    return hits

def mutate(ad, kind, n):
    """n substitutions, or one insertion/deletion, all at interior positions."""
    a = list(ad)
    if kind == "sub":
        for p in rng.sample(range(4, len(a) - 4), n):
            a[p] = rng.choice([b for b in "ACGT" if b != a[p]])
    elif kind == "ins":
        p = rng.randrange(6, len(a) - 6); a.insert(p, rng.choice("ACGT"))
    elif kind == "del":
        p = rng.randrange(6, len(a) - 6); del a[p]
    return "".join(a)

# Control adapters: planted nowhere. A partial read ends inside the real adapter, so a control
# whose 3-mer prefix occurs in either real adapter would "occur" by construction; draw until not.
KMERS = {a[i:i + OMIN] for a in (AD1, AD2) for i in range(len(a) - OMIN + 1)}
def ctrl(n):
    while True:
        c = rs(n)
        if c[:OMIN] not in KMERS:
            return c
CTRL1, CTRL2 = ctrl(34), ctrl(33)

pairs = []   # (category, insert, r1, r2, expect)
def add(cat, L, expect, mut=None, nbases=0):
    while True:
        ad1, ad2 = (mutate(AD1, *mut), mutate(AD2, *mut)) if mut else (AD1, AD2)
        ins = rs(L)
        if nbases:
            ins = list(ins)
            for p in rng.sample(range(L), nbases): ins[p] = "N"
            ins = "".join(ins)
        s1 = (ins + ad1 + rs(RL))[:RL]
        s2 = (rc(ins.replace("N", "A")) + ad2 + rs(RL))[:RL]   # N planted in R1 only
        ok = True
        for s, ad, real in ((s1, AD1, ad1), (s2, AD2, ad2)):
            # the only adapter occurrence is the planted one
            if expect in ("untouched", "too_many_n") and tail_hits(s, ad):
                ok = False
            if cat == "partial" and tail_hits(s, ad, planted=RL - L if RL - L >= OMIN else 0):
                ok = False
            if cat == "too_many_errors" and semiglobal(ad, s) <= allowed(len(ad)):
                ok = False
            # nothing anywhere resembles the control adapter
            ctrl = CTRL1 if ad is AD1 else CTRL2
            if tail_hits(s, ctrl) or semiglobal(ctrl, s) <= allowed(len(ctrl)):
                ok = False
        if ok:
            break
    pairs.append((cat, L, s1, s2, rq(RL), rq(RL), expect))

for _ in range(600):                       # full adapter, read continues past it
    add("full", rng.randint(MINLEN, RL - len(AD1)), "trim")
for k in range(1, len(AD1)):               # partial adapter at the 3' end, every overlap 1..33
    for _ in range(10):
        add("partial", RL - k, "trim" if k >= OMIN else "untouched")
for _ in range(300):                       # substitutions, as many as -e allows on 34/33 bp
    add("sub_within_e", rng.randint(MINLEN, RL - len(AD1) - 1), "trim",
        mut=("sub", allowed(len(AD1))))
for kind in ("ins", "del"):
    for _ in range(50):
        add(kind + "_within_e", rng.randint(MINLEN, RL - len(AD1) - 1), "trim",
            mut=(kind, 1))
for _ in range(100):                       # one error more than -e allows: must NOT trim
    add("too_many_errors", rng.randint(MINLEN, RL - len(AD1) - 1), "untouched",
        mut=("sub", allowed(len(AD1)) + 1))
for _ in range(300):                       # no adapter at all
    add("no_adapter", RL, "untouched")
for L in range(0, MINLEN):                 # insert shorter than -m: filtered too_short
    for _ in range(10):
        add("short_insert", L, "too_short")
for _ in range(60):                        # N in R1 only: --max-n 0 filters the PAIR (pair filter any)
    add("n_in_r1", RL, "too_many_n", nbases=rng.randint(1, 3))

rng.shuffle(pairs)
with open("R1.fq", "w") as f1, open("R2.fq", "w") as f2, open("truth.tsv", "w") as t, \
     open("expected_R1.fq", "w") as e1, open("expected_R2.fq", "w") as e2:
    t.write("id\tcategory\tinsert_len\texpect\n")
    for i, (cat, L, s1, s2, q1, q2, expect) in enumerate(pairs):
        rid = "p%05d" % i
        f1.write("@%s 1:N:0\n%s\n+\n%s\n" % (rid, s1, q1))
        f2.write("@%s 2:N:0\n%s\n+\n%s\n" % (rid, s2, q2))
        t.write("%s\t%s\t%d\t%s\n" % (rid, cat, L, expect))
        if expect in ("too_short", "too_many_n"):
            continue
        n = L if expect == "trim" else RL
        e1.write("@%s 1:N:0\n%s\n+\n%s\n" % (rid, s1[:n], q1[:n]))
        e2.write("@%s 2:N:0\n%s\n+\n%s\n" % (rid, s2[:n], q2[:n]))
with open("control_adapters.txt", "w") as f:
    f.write("%s\n%s\n" % (CTRL1, CTRL2))

# Quality-trimming reads: no adapter in play, quality profiles chosen to exercise the BWA rule
# (low tails, low heads, dips inside good sequence, all-low reads that trim to nothing).
with open("qual.fq", "w") as f:
    for i in range(400):
        q = [rng.randint(25, 40) for _ in range(RL)]
        kind = i % 5
        if kind == 1:                       # low 3' tail of random length
            for p in range(RL - rng.randint(1, 40), RL): q[p] = rng.randint(2, 19)
        elif kind == 2:                     # low 5' head
            for p in range(rng.randint(1, 30)): q[p] = rng.randint(2, 14)
        elif kind == 3:                     # a dip inside good sequence, then a noisy tail
            d = rng.randint(20, 60)
            for p in range(d, d + rng.randint(2, 8)): q[p] = rng.randint(2, 10)
            for p in range(RL - 25, RL): q[p] = rng.randint(5, 30)
        elif kind == 4 and i % 20 == 4:     # everything low: trims to an empty read
            q = [rng.randint(2, 12) for _ in range(RL)]
        f.write("@q%04d\n%s\n+\n%s\n" % (i, rs(RL), "".join(chr(33 + x) for x in q)))

from collections import Counter
c = Counter((p[0], p[6]) for p in pairs)
for k in sorted(c): print("  %-18s %-11s %4d pairs" % (k[0], k[1], c[k]))
print("  %d pairs, 400 quality reads" % len(pairs))
PY

FILES="R1.fq R2.fq truth.tsv expected_R1.fq expected_R2.fq control_adapters.txt qual.fq"
sha256sum $FILES > fixture.sha256 2>/dev/null || shasum -a 256 $FILES > fixture.sha256
for f in $FILES fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/cutadapt/$f"
done
cat fixture.sha256
echo "done: $BUCKET/inputs/cutadapt/"
