#!/usr/bin/env bash
# Stage ArchR's synthetic scATAC fixture: a 20 Mb three-chromosome genome, 400 genes, and two
# 10x-style fragment files (bgzip + tabix) whose every count is planted and recorded.
#
#   planted   240 cells, two populations of 120. Every cell carries uniform background, fragments
#             landing on a TSS, and fragments inside 200 tiles specific to its population
#             (10 tiles in the body of each of 20 population-specific genes).
#   flat       60 cells of background only -- the TSS-enrichment control.
#
# Generated HERE, once, and pinned by sha256, so both architectures read identical bytes.
# Nothing is downloaded: ArchR runs on this genome through genome = "nullGenome".
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=archr does this}"
REGION="${AWS_REGION:-us-west-2}"
command -v bgzip >/dev/null && command -v tabix >/dev/null \
  || { echo "  needs bgzip and tabix (htslib) on PATH" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
# The task script is staged and pinned with the data: inline, the spec would exceed EC2's 16 KB user data.
cp "$SCRIPT_DIR/archr.R" .

python3 - <<'PY'
import random
random.seed(20261016)
CHROMS = [("chr1", 10_000_000), ("chr2", 6_000_000), ("chr3", 4_000_000)]
TILE = 500
GLEN = sum(l for _, l in CHROMS)

# Genes: one every 50 kb, 9-16 kb long, alternating strand. Single exon = the gene body.
# Lengths must vary: ArchR 1.0.3's gene-body weight divides by (max - min) of 1/width, so a
# genome of equal-length genes gives an all-NaN GeneScoreMatrix without an error.
genes = []
for c, L in CHROMS:
    for j in range(L // 50_000):
        s = j * 50_000 + 20_001; e = s + random.randint(9_000, 16_000) - 1   # 1-based closed
        strand = "+" if len(genes) % 2 == 0 else "-"
        genes.append((c, s, e, strand, "G%03d" % len(genes)))
tss = [(g[0], g[1] if g[3] == "+" else g[2]) for g in genes]

idx = list(range(len(genes))); random.shuffle(idx)
popgenes = {"A": sorted(idx[:20]), "B": sorted(idx[20:40])}
poptiles = {}
for p, gi in popgenes.items():
    tl = []
    for i in gi:
        c, s, e, strand, _ = genes[i]
        t_ss = s if strand == "+" else e
        # tiles fully in the body, >= 2.5 kb from the TSS (clear of TSS windows and flanks)
        cand = [k for k in range(s // TILE + 1, e // TILE)
                if TILE * k >= s and TILE * k + TILE - 1 <= e
                and min(abs(TILE * k - t_ss), abs(TILE * k + TILE - 1 - t_ss)) >= 2500]
        tl += [(c, k) for k in sorted(random.sample(cand, 10))]
    poptiles[p] = tl

# Blacklist: 5 intergenic regions of exactly 4 tiles, [500k, 500k+1999] in 1-based coordinates.
black = []
for c, L in CHROMS[:1]:
    for j in (10, 40, 70, 100, 130):
        k = (j * 50_000 + 40_000) // TILE                 # 10 kb past the gene end
        black.append((c, TILE * k, TILE * k + 1999))

def rpos():
    x = random.randrange(GLEN)
    for c, L in CHROMS:
        if x < L: return c, x + 1
        x -= L

frags = {"planted": [], "flat": []}
truth = []
def add(sample, bc, c, s1, w):
    L = dict(CHROMS)[c]
    if s1 < 1 or s1 + w - 1 > L: return False
    frags[sample].append((c, s1 - 1, s1 + w - 1, bc))   # BED: V2 = start1 - 1, V3 = end1
    return True

def cell(sample, bc, pop):
    n = {"mono": 0, "di": 0, "multi": 0}
    def bg(cls, lo, hi, count):
        k = 0
        while k < count:
            c, s1 = rpos()
            if add(sample, bc, c, s1, random.randint(lo, hi)): k += 1
        n[cls] += count
    bg("mono", 20, 146, random.randint(1600, 2000))
    bg("di", 147, 293, random.randint(800, 1000))
    bg("multi", 294, 1000, random.randint(250, 350))
    if pop != "flat":
        k = random.randint(250, 350)
        for _ in range(k):                              # one end on a TSS, +-50 bp
            c, t = random.choice(tss)
            add(sample, bc, c, t + random.randint(-50, 50), random.randint(20, 146))
        n["mono"] += k
        k = random.randint(250, 350)
        for _ in range(k):                              # both ends inside an own-population tile
            c, t = random.choice(poptiles[pop]); w = random.randint(20, 146)
            add(sample, bc, c, TILE * t + random.randint(0, TILE - w), w)
        n["mono"] += k
    bad = random.randint(3, 8)                          # outside ArchR's 10..2000 bp window
    for _ in range(bad):
        c, s1 = rpos(); add(sample, bc, c, min(s1, 3_000_000), random.choice([5, 9, 2001, 2500]))
    truth.append((bc, sample, pop, n["mono"] + n["di"] + n["multi"], n["mono"], n["di"], n["multi"], bad))

cells = [("planted", "A")] * 120 + [("planted", "B")] * 120
random.shuffle(cells)
for i, (s, p) in enumerate(cells): cell(s, "BC%04d" % i, p)
for i in range(60): cell("flat", "FL%04d" % i, "flat")

order = {c: i for i, (c, _) in enumerate(CHROMS)}
for s, fr in frags.items():
    fr.sort(key=lambda r: (order[r[0]], r[1], r[2], r[3]))
    with open("%s.fragments.tsv" % s, "w") as f:
        for r in fr: f.write("%s\t%d\t%d\t%s\t1\n" % r)
with open("cells.tsv", "w") as f:
    f.write("barcode\tsample\tpop\tnFrags\tnMono\tnDi\tnMulti\tnOutOfRange\n")
    for r in truth: f.write("%s\t%s\t%s\t%d\t%d\t%d\t%d\t%d\n" % r)
with open("genes.tsv", "w") as f:
    f.write("chr\tstart\tend\tstrand\tsymbol\tpop\n")
    pg = {i: p for p, gi in popgenes.items() for i in gi}
    for i, g in enumerate(genes): f.write("%s\t%d\t%d\t%s\t%s\t%s\n" % (g + (pg.get(i, "none"),)))
with open("tiles.tsv", "w") as f:
    f.write("chr\ttile_start\tpop\n")
    for p, tl in poptiles.items():
        for c, k in tl: f.write("%s\t%d\t%s\n" % (c, TILE * k, p))
with open("genome.tsv", "w") as f:
    f.write("chr\tlength\n")
    for c, L in CHROMS: f.write("%s\t%d\n" % (c, L))
with open("blacklist.tsv", "w") as f:
    f.write("chr\tstart\tend\n")
    for b in black: f.write("%s\t%d\t%d\n" % b)
print("  planted %d fragments, flat %d; %d cells; A/B tiles %d/%d" % (
    len(frags["planted"]), len(frags["flat"]), len(truth), len(poptiles["A"]), len(poptiles["B"])))
PY

for s in planted flat; do
  bgzip -f "$s.fragments.tsv"
  tabix -p bed "$s.fragments.tsv.gz"
done
FILES="archr.R planted.fragments.tsv.gz planted.fragments.tsv.gz.tbi flat.fragments.tsv.gz flat.fragments.tsv.gz.tbi cells.tsv genes.tsv tiles.tsv genome.tsv blacklist.tsv"
# shellcheck disable=SC2086
sha256sum $FILES > fixture.sha256 2>/dev/null || shasum -a 256 $FILES > fixture.sha256
for f in $FILES fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/archr/$f"
done
cat fixture.sha256
echo "done.  $BUCKET/inputs/archr/"
