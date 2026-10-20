"""Score assemblies against the truth by exact edit distance, per truth replicon.

    python3 -u score.py <threads> <outdir> truth.fa label=asm.fa [label=asm.fa ...]

Assignment: minimap2 -x asm5 maps each assembly to the truth; each truth replicon is matched to the
contig with the most matching bases, in that alignment's orientation. Distance: edlib's exact
Levenshtein, end to end, against the replicon rotated to start where the contig starts (see dist()),
so a circular contig scores the same wherever it starts. Writes score.tsv and score-checks.txt;
exits 1 if any assertion fails.

The scorer is tested before it is trusted, on the truth itself: 1,000 planted substitutions and 100
planted deletions must score exactly 1,100 (split 1,000 / 0 / 100), and the truth rotated and
reverse-complemented must score 0.
"""
import math
import os
import subprocess
import sys

import edlib

T, OUT, TRUTH = sys.argv[1], sys.argv[2], sys.argv[3]
ASMS = [a.split("=", 1) for a in sys.argv[4:]]
COMP = str.maketrans("ACGTacgtN", "TGCAtgcaN")


def fasta(p):
    seqs, name, buf = {}, None, []
    for line in open(p):
        if line.startswith(">"):
            if name is not None:
                seqs[name] = "".join(buf).upper()
            name, buf = line[1:].split()[0], []
        else:
            buf.append(line.strip())
    if name is not None:
        seqs[name] = "".join(buf).upper()
    return seqs


def rc(s):
    return s.translate(COMP)[::-1]


def assign(path):
    """{truth replicon: (contig, strand, matching bases, offset hint)} from minimap2's PAF.

    The hint is where the (oriented) contig's first base should sit on the replicon, from the
    contig's longest alignment block; dist() refines it to the base."""
    paf = subprocess.run(["minimap2", "-x", "asm5", "-t", T, "--secondary=no", TRUTH, path],
                         capture_output=True, text=True, check=True).stdout
    per, block = {}, {}
    for line in paf.splitlines():
        f = line.split("\t")
        q, qlen, qs, qe, strand, tname, ts, match = f[0], int(f[1]), int(f[2]), int(f[3]), f[4], f[5], int(f[7]), int(f[9])
        key = (tname, q, strand)
        per[key] = per.get(key, 0) + match
        hint = ts - qs if strand == "+" else ts - (qlen - qe)
        if key not in block or match > block[key][0]:
            block[key] = (match, hint)
    best = {}
    for (tname, q, strand), m in per.items():
        if tname not in best or m > best[tname][2]:
            best[tname] = (q, strand, m, block[(tname, q, strand)][1])
    return best


def dist(query, target, hint):
    """(edit distance, X, I, D) of query against the replicon read from the query's own start.

    A replicon is circular, so a contig can start anywhere: the first 2 kb of the contig is placed
    exactly (edlib, infix mode, in a 10 kb window around minimap2's hint), the replicon is rotated to
    start there, and the two are aligned end to end (edlib, global, exact)."""
    L, A = len(target), min(2000, len(query))
    ttt = target * 3
    w0 = (hint % L) + L - 5000
    a = edlib.align(query[:A], ttt[w0:w0 + 10000 + A], mode="HW", task="locations")
    off = (w0 + a["locations"][0][0]) % L
    rot = target[off:] + target[:off]
    # edlib's cost grows with its band k, so start narrow and widen; give up at 1% of the replicon,
    # so a misassembly fails fast instead of running for hours.
    bound, k = max(20000, L // 100), 256
    while True:
        r = edlib.align(query, rot, mode="NW", task="path", k=min(k, bound))
        if r["editDistance"] >= 0 or k >= bound:
            break
        k *= 4
    if r["editDistance"] < 0:
        return None
    ops = {"X": 0, "I": 0, "D": 0}
    n = ""
    for c in r["cigar"]:
        if c.isdigit():
            n += c
        else:
            if c in ops:
                ops[c] += int(n)
            n = ""
    return r["editDistance"], ops["X"], ops["I"], ops["D"]


truth = fasta(TRUTH)
rows, checks, fail = [], [], []


def check(name, ok, detail):
    checks.append("%-44s %-4s %s" % (name, "ok" if ok else "FAIL", detail))
    if not bool(ok):
        fail.append(name)


# --- the scorer, tested on the truth itself -------------------------------------------------------
chrom = max(truth, key=lambda k: len(truth[k]))
t = truth[chrom]
s = list(t)
for i in range(1000):
    p = 1234 + 5000 * i
    s[p] = {"A": "C", "C": "G", "G": "T", "T": "A"}.get(s[p], "A")
for i in range(100):
    s[3711 + 5000 * i] = ""
planted = "".join(s)
d = dist(planted, t, 0)
# Which CIGAR letter means "the assembly lacks a truth base" is read off this known case, not assumed.
MISSING = "I" if d and d[2] == 100 else "D"
check("scorer: 1000 subs + 100 dels planted", d is not None and d[:2] == (1100, 1000) and sorted(d[2:]) == [0, 100],
      "got %s; missing-base op is %s" % (d, MISSING))
rot = rc(t[1234567:] + t[:1234567])
rp = os.path.join(OUT, "selftest-rotated-rc.fa")
open(rp, "w").write(">rot\n%s\n" % rot)
b = assign(rp).get(chrom)
d = dist(rc(rot), t, b[3]) if b and b[1] == "-" else None
check("scorer: rotated + reverse-complemented truth", d is not None and d[0] == 0, "assigned %s, got %s" % (b, d))
os.remove(rp)

# --- every assembly against every truth replicon --------------------------------------------------
total = {}
for label, path in ASMS:
    asm = fasta(path)
    best = assign(path)
    tot = 0
    used = set()
    for rep in sorted(truth, key=lambda k: -len(truth[k])):
        L = len(truth[rep])
        if rep not in best:
            rows.append((label, rep, "-", 0, L, L, "", "", "", "missing"))
            tot += L
            continue
        q, strand, _, hint = best[rep]
        used.add(q)
        seq = asm[q] if strand == "+" else rc(asm[q])
        d = dist(seq, truth[rep], hint)
        if d is None:
            rows.append((label, rep, q, len(seq), L, L, "", "", "", "beyond bound"))
            tot += L
            continue
        e, x, i, dd = d
        ins, dele = (dd, i) if MISSING == "I" else (i, dd)
        tot += e
        rows.append((label, rep, q, len(seq), L, e, x, ins, dele,
                     "inf" if e == 0 else "%.1f" % (-10 * math.log10(e / L))))
    extra = [(q, len(v)) for q, v in asm.items() if q not in used]
    rows.append((label, "unassigned", ",".join(q for q, _ in extra) or "-", sum(n for _, n in extra), "", "", "", "", "", ""))
    total[label] = tot

with open(os.path.join(OUT, "score.tsv"), "w") as fh:
    fh.write("assembly\ttruth_replicon\tcontig\tcontig_bp\ttruth_bp\tedit_distance\tsubstitutions\textra_bases\tmissing_bases\tQ\n")
    for r in rows:
        fh.write("\t".join(str(v) for v in r) + "\n")

for label in total:
    reps = [r for r in rows if r[0] == label and r[1] != "unassigned"]
    check("%s: every truth replicon has a contig" % label, all(r[2] != "-" for r in reps),
          ", ".join("%s<-%s" % (r[1], r[2]) for r in reps))
for label in ("racon", "medaka"):
    if label in total and "draft" in total:
        check("%s: fewer errors than the draft" % label, total[label] < total["draft"],
              "%d vs draft %d" % (total[label], total["draft"]))
checks.append("totals " + " ".join("%s=%d" % kv for kv in total.items()))
checks.append("SCORE PASSED" if not fail else "SCORE FAILED: %s" % "; ".join(fail))
open(os.path.join(OUT, "score-checks.txt"), "w").write("\n".join(checks) + "\n")
print("\n".join(checks), flush=True)
sys.exit(1 if fail else 0)
