#!/usr/bin/env bash
# Stage Signac's synthetic scATAC fixture: a bgzipped, tabix-indexed fragments file with planted
# structure, the peaks it was planted on, a small gene annotation, the planted truth, and signac.R.
#
# Generated HERE, not in the task, and pinned by sha256: the two images ship different userlands
# (aarchbio's vs bioconda's BusyBox) and neither ships bgzip/tabix binaries, so the bytes are made
# once, with htslib's bgzip/tabix on the staging machine, and are the same on both chips.
# Needs: python3, bgzip and tabix (htslib) on PATH.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=signac does this}"
REGION="${AWS_REGION:-us-west-2}"
command -v bgzip >/dev/null && command -v tabix >/dev/null \
  || { echo "  bgzip and tabix (htslib) are needed to build the fragments file" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/signac.R" .                        # the task's checks; staged because the spec
                                                   # would exceed EC2's 16 KB user-data limit

echo "== the planted fixture, generated and checked without Signac =="
python3 - <<'PY'
import random, sys
random.seed(20261016)
ACGT = "ACGT"

# 200 cells (100 per population) and 20 barcodes that are NOT cells, as in a real 10x file.
seen, bcs = set(), []
while len(bcs) < 220:
    b = "".join(random.choice(ACGT) for _ in range(16)) + "-1"
    if b not in seen:
        seen.add(b); bcs.append(b)
cells, noncells = bcs[:200], bcs[200:]
pop = {b: ("A" if i < 100 else "B") for i, b in enumerate(cells)}

# 60 peaks of 500 bp on chr1, 20 kb apart: 20 open only in A, 20 only in B, 20 in both.
peaks = []
for i in range(60):
    s = 100000 + i * 20000
    kind = ("A", "B", "shared")[i % 3]
    peaks.append((s, s + 500, kind))

def length():
    r = random.random()
    if r < 0.50: return random.randint(30, 146)    # nucleosome-free
    if r < 0.85: return random.randint(147, 294)   # mononucleosomal
    return random.randint(295, 500)                # di-nucleosomal

frags = []                                         # (chrom, start0, end0, barcode)
for b in cells:
    p = pop[b]
    for (s, e, kind) in peaks:
        prob = 0.6 if kind == "shared" else (0.9 if kind == p else 0.0)
        if random.random() < prob:
            for _ in range(random.randint(1, 4)):
                L = length()
                st = random.randint(s - 200, e - 50)
                frags.append(("chr1", st, st + L, b))
    # background, kept >= 1 kb clear of every peak
    n = 0
    while n < 30:
        L = length(); st = random.randint(50000, 1400000)
        if all(st + L < s - 1000 or st > e + 1000 for (s, e, _) in peaks):
            frags.append(("chr1", st, st + L, b)); n += 1
    # the length-bin boundaries, once each per cell, in background
    for L in (146, 147, 294, 295):
        while True:
            st = random.randint(50000, 1400000)
            if all(st + L < s - 1000 or st > e + 1000 for (s, e, _) in peaks):
                frags.append(("chr1", st, st + L, b)); break

# Boundary cases on the shared peaks (open in both populations, so no specificity is touched):
# a fragment ending exactly at the peak's BED start and one starting exactly at its BED end do
# NOT overlap a half-open interval; one ending 1 bp inside does.
for k, b in enumerate(cells[:40]):
    s, e, _ = peaks[2 + 3 * (k % 20)]
    frags.append(("chr1", s - 100, s, b))          # touches, no overlap
    frags.append(("chr1", e, e + 100, b))          # touches, no overlap
    frags.append(("chr1", s - 99, s + 1, b))       # overlaps by 1 bp

# Non-cell barcodes land in A-specific and B-specific peaks: any leak of them into a matrix
# restricted to the 200 cells would break the exact counts.
for b in noncells:
    for (s, e, kind) in peaks:
        if kind != "shared":
            frags.append(("chr1", s + 100, s + 250, b))

# TSS windows on chr2. Signac's TSS score is (cuts in the central 1001 bp / 1001) divided by
# (cuts in the two 100 bp flanks at +-901..1000 / 200), so cuts are planted >= 10 bp clear of
# every window edge, and the planted score per cell is known in closed form.
genes = [  # name, start1, end1, strand, biotype
    ("geneA", 100001, 130000, "+", "protein_coding"),
    ("geneB", 220001, 250000, "-", "protein_coding"),
    ("geneC", 400001, 430000, "+", "protein_coding"),
    ("geneD", 520001, 550000, "-", "protein_coding"),
    ("lncX",  700001, 730000, "+", "lncRNA"),       # excluded by Signac's biotype filter
]
def tss(g):  # 0-based position of the TSS base
    return (g[1] - 1) if g[3] == "+" else (g[2] - 1)

truth = {}
for b in cells:
    c_cut = f_cut = 0
    for g in genes:
        t = tss(g)
        if g[4] != "protein_coding":
            for _ in range(30):                     # strongly "enriched", must not count
                L = random.randint(50, 300); st = t + random.randint(-390, 390 - L)
                frags.append(("chr2", st, st + L, b))
            continue
        nc = random.randint(15, 25) if pop[b] == "A" else random.randint(3, 7)
        for _ in range(nc):                         # both cuts inside +-390 of the TSS
            L = random.randint(50, 300); st = t + random.randint(-390, 390 - L)
            frags.append(("chr2", st, st + L, b)); c_cut += 2
        side = random.choice((-1, 1))               # one flank fragment, both cuts in 911..990
        L = random.randint(20, 60); off = random.randint(911, 990 - L)
        st = t + off if side == 1 else t - off - L
        frags.append(("chr2", st, st + L, b)); f_cut += 2
    truth[b] = (c_cut, f_cut)

# a tenth of records carry a duplicate count (column 5) of 2..5
rows = sorted(set(frags), key=lambda r: (r[0], r[1], r[2], r[3]))
with open("fragments.tsv", "w") as f:
    for (c, s, e, b) in rows:
        n = random.randint(2, 5) if random.random() < 0.1 else 1
        f.write("%s\t%d\t%d\t%s\t%d\n" % (c, s, e, b, n))
with open("peaks.bed", "w") as f:
    for i, (s, e, kind) in enumerate(peaks):
        f.write("chr1\t%d\t%d\tpeak%02d\t%s\n" % (s, e, i, kind))
with open("genes.tsv", "w") as f:
    f.write("seqnames\tstart\tend\tstrand\tgene_name\tgene_id\tgene_biotype\ttx_id\ttype\n")
    for g in genes:
        f.write("chr2\t%d\t%d\t%s\t%s\tID_%s\t%s\tTX_%s\texon\n" % (g[1], g[2], g[3], g[0], g[0], g[4], g[0]))
with open("truth.tsv", "w") as f:
    f.write("barcode\tpopulation\ttss_center_cuts\ttss_flank_cuts\n")
    for b in cells:
        f.write("%s\t%s\t%d\t%d\n" % (b, pop[b], truth[b][0], truth[b][1]))

dup = len(frags) - len(rows)
print("  %d fragment records (%d exact duplicates collapsed), %d cells + %d non-cell barcodes"
      % (len(rows), dup, len(cells), len(noncells)))
print("  60 peaks (20 A-only, 20 B-only, 20 shared); 4 protein-coding TSSs + 1 lncRNA decoy")
if any(t[1] == 0 for t in truth.values()):
    sys.exit("  every cell must have flank cuts, or the TSS score is not defined in closed form")
PY

bgzip -f fragments.tsv
tabix -p bed fragments.tsv.gz
files="fragments.tsv.gz fragments.tsv.gz.tbi peaks.bed genes.tsv truth.tsv signac.R"
sha256sum $files > fixture.sha256 2>/dev/null || shasum -a 256 $files > fixture.sha256
for f in $files fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/signac/$f"
done
cat fixture.sha256
echo "done."
echo "  $BUCKET/inputs/signac/"
