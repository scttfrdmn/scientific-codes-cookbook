#!/usr/bin/env bash
# Stage MAGeCK's synthetic screen: an sgRNA library, six single-end FASTQs whose per-sgRNA read
# counts are planted, and the truth tables. Generated HERE and pinned by sha256, not in the task:
# the two images draw different random streams (and the x86 one has a BusyBox userland), so a
# cross-architecture comparison is only on identical bytes if the bytes are made once.
#
# Design (seed 20261016, numpy PCG64):
#   840 genes x 4 sgRNAs + 200 non-targeting sgRNAs = 3,560 random 20-mers, pairwise Hamming >= 3
#   counts ~ negative binomial (mean 100 x per-sgRNA abundance x sample depth, dispersion 0.05)
#   samples ctrl1 ctrl2 treat1 treat2 null1 null2; depths 1.0 1.3 0.8 1.1 0.9 1.2
#   treat*: 20 genes depleted 4x (log2FC -2), 20 enriched 4x (+2); null*: no effect at all
#   15 sgRNAs of null genes are library dropouts: zero reads in every sample
# Each counted read is 23 nt of random prefix + the guide + 7 nt, so the guide sits at offset 23.
# Three read classes must NOT count, and per sample they are planted as:
#   1% one-mismatch copies of a guide at offset 23   (MAGeCK counts exact matches only)
#   1% exact guides at offset 24                     (auto trim-5 picks the dominant offset only)
#   1% random 50-mers
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=mageck does this}"
REGION="${AWS_REGION:-us-west-2}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== the planted screen, generated and verified without MAGeCK =="
python3 - <<'PY'
import gzip, sys
import numpy as np
rng = np.random.Generator(np.random.PCG64(20261016))
B = np.array(list("ACGT"))
NGENE, NSG, NNT = 840, 4, 200
DEP = ["ESS%02d" % i for i in range(1, 21)]
ENR = ["ENR%02d" % i for i in range(1, 21)]
NUL = ["GENE%03d" % i for i in range(1, NGENE - 40 + 1)]
genes = DEP + ENR + NUL
SAMPLES = ["ctrl1", "ctrl2", "treat1", "treat2", "null1", "null2"]
DEPTH = [1.0, 1.3, 0.8, 1.1, 0.9, 1.2]

# library: random 20-mers, every pair at Hamming distance >= 3, so a one-mismatch read can never
# be an exact copy of another guide
seqs = []
seen = set()
def ok(s):
    a = np.frombuffer(s.encode(), dtype=np.uint8)
    for t in seqs_arr:
        if (a != t).sum() < 3:
            return False
    return True
seqs_arr = []
while len(seqs) < NGENE * NSG + NNT:
    s = "".join(rng.choice(B, 20))
    if s in seen:
        continue
    a = np.frombuffer(s.encode(), dtype=np.uint8)
    if seqs_arr and (np.stack(seqs_arr) != a).sum(axis=1).min() < 3:
        continue
    seen.add(s); seqs.append(s); seqs_arr.append(a)
lib = []   # (id, seq, gene)
k = 0
for g in genes:
    for j in range(NSG):
        lib.append(("%s_sg%d" % (g, j + 1), seqs[k], g)); k += 1
for j in range(NNT):
    lib.append(("NT_%03d" % (j + 1), seqs[k], "NonTargeting")); k += 1
n = len(lib)
abund = rng.lognormal(0.0, 0.5, n)
lfc = np.zeros(n)
for i, (sid, s, g) in enumerate(lib):
    if g in DEP: lfc[i] = -2.0
    if g in ENR: lfc[i] = 2.0
dropout = rng.choice([i for i, x in enumerate(lib) if x[2] in NUL], 15, replace=False)
PHI = 0.05
counts = np.zeros((n, len(SAMPLES)), dtype=np.int64)
for j, sm in enumerate(SAMPLES):
    mu = 100.0 * abund * DEPTH[j] * (2.0 ** lfc if sm.startswith("treat") else 1.0)
    lam = rng.gamma(1.0 / PHI, mu * PHI)          # gamma-Poisson = NB(mean mu, dispersion PHI)
    counts[:, j] = rng.poisson(lam)
counts[dropout, :] = 0

with open("library.txt", "w") as f:
    for sid, s, g in lib:
        f.write("%s\t%s\t%s\n" % (sid, s, g))
with open("controls.txt", "w") as f:
    for sid, s, g in lib:
        if g == "NonTargeting": f.write(sid + "\n")
with open("truth_counts.txt", "w") as f:
    f.write("sgRNA\tGene\t" + "\t".join(SAMPLES) + "\n")
    for i, (sid, s, g) in enumerate(lib):
        f.write("%s\t%s\t%s\n" % (sid, g, "\t".join(str(c) for c in counts[i])))
with open("truth_genes.txt", "w") as f:
    f.write("gene\tplanted\n")
    for g in genes:
        f.write("%s\t%s\n" % (g, "depleted" if g in DEP else "enriched" if g in ENR else "null"))

libset = set(s for _, s, _ in lib)
def rs(m): return "".join(rng.choice(B, m))
reads_total = {}
for j, sm in enumerate(SAMPLES):
    reads = []
    for i, (sid, s, g) in enumerate(lib):
        for _ in range(counts[i, j]):
            reads.append(rs(23) + s + rs(7))
    nc = len(reads); nbad = nc // 100
    for _ in range(nbad):                          # one mismatch, at offset 23
        s = lib[rng.integers(n)][1]; p = int(rng.integers(20))
        alt = rng.choice([b for b in "ACGT" if b != s[p]])
        reads.append(rs(23) + s[:p] + alt + s[p + 1:] + rs(7))
    for _ in range(nbad):                          # exact guide, one base late
        reads.append(rs(24) + lib[rng.integers(n)][1] + rs(6))
    for _ in range(nbad):                          # junk
        reads.append(rs(50))
    order = rng.permutation(len(reads))
    # independent audit of the planted truth: a guide is found at offset 23 in exactly the
    # planted reads, and nowhere else in any read except the planted offset-24 class
    at23 = sum(1 for r in reads if r[23:43] in libset)
    late = sum(1 for r in reads if r[24:44] in libset)
    other = sum(1 for r in reads for o in range(31) if o not in (23, 24) and r[o:o + 20] in libset)
    if at23 != nc or late != nbad or other != 0:
        sys.exit("  %s: planted truth fails audit (at23 %d vs %d, late %d vs %d, other %d)" % (sm, at23, nc, late, nbad, other))
    with gzip.GzipFile("%s.fastq.gz" % sm, "wb", mtime=0) as f:
        for q, idx in enumerate(order):
            f.write(("@%s_%d\n%s\n+\n%s\n" % (sm, q, reads[idx], "I" * 50)).encode())
    reads_total[sm] = len(reads)
    print("  %-7s %7d reads, %7d counted, %5d x3 that must not count, %4d zero-count sgRNAs"
          % (sm, len(reads), nc, nbad, int((counts[:, j] == 0).sum())))
with open("truth_reads.txt", "w") as f:
    f.write("sample\treads\n")
    for sm in SAMPLES: f.write("%s\t%d\n" % (sm, reads_total[sm]))
print("  %d sgRNAs (%d non-targeting), %d genes; planted: %d depleted, %d enriched" % (n, NNT, len(genes), len(DEP), len(ENR)))
PY

FILES="library.txt controls.txt truth_counts.txt truth_genes.txt truth_reads.txt ctrl1.fastq.gz ctrl2.fastq.gz treat1.fastq.gz treat2.fastq.gz null1.fastq.gz null2.fastq.gz"
sha256sum $FILES > fixture.sha256 2>/dev/null || shasum -a 256 $FILES > fixture.sha256
if [ -n "${MAGECK_LOCAL_OUT:-}" ]; then cp $FILES fixture.sha256 "$MAGECK_LOCAL_OUT"/; fi
for f in $FILES fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/mageck/$f"
done
cat fixture.sha256
echo "done.  $BUCKET/inputs/mageck/"
