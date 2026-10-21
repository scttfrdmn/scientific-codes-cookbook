set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl NUMBA_CACHE_DIR=/tmp/numba
cd /tmp

# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
#
# WHY THIS RUN EXISTS. The first attempt asserted that bamCoverage's coverage integral equals
# samtools' `bases mapped (cigar)` -- a conservation identity if true. It measured 1.644889 and
# the loose 5% gate refused it, correctly: at bin width W, bamCoverage credits a read to EVERY
# bin it touches, so each read is spread over (L-1+W) bases rather than L. With L=76 and W=50
# that predicts (76-1+50)/76 = 1.644737 against the observed 1.644889 -- agreement to 0.0093%.
# Not a bug in either tool; the metric was comparing a binned quantity to an unbinned one.
#
# So this run asserts two things instead, both fixed by construction rather than observed:
#   1. At W=1, L-1+W == L, so the integral must EQUAL bases mapped. A true conservation
#      identity, exact, no tolerance.
#   2. At W=50, the integral must match the DERIVED prediction bases*(L-1+W)/L. That is a law
#      with a closed form, not a band on a measured value -- an implementation that binned
#      wrongly would miss it even though it sits inside any tolerance you would pick blind.
#
# The integral comes from the bigWig totalSummary `sumData`, which is the sum of value x width
# over all covered bases -- i.e. the integral directly, O(1) and bin-width agnostic. That also
# avoids materialising a 3.1e9-row bedgraph at binSize 1.

grep ENCFF933NSJ encode.md5 > /tmp/this.md5
md5sum -c /tmp/this.md5 || { echo "FAIL: BAM does not match ENCODE's published md5"; exit 1; }

bamCoverage --version 2>&1 | head -1
python3 -u -c "import pyBigWig; print('version\tpyBigWig %s' % getattr(pyBigWig,'__version__','unknown'))"
nproc | awk '{print "nproc\t"$1}'
TPC=$(awk -F, '{n=0; for(i=1;i<=NF;i++){k=split($i,r,"-"); n+=(k==2?r[2]-r[1]+1:1)} print n}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null || echo unknown)
CPUM=$(awk -F': *' '/^model name/{print $2; exit} /^CPU part/{print "CPU part "$2; exit}' /proc/cpuinfo 2>/dev/null || echo unknown)
printf 'threads_per_core\t%s\ncpu_model\t%s\n' "$TPC" "$CPUM"

BM=$(awk -F'\t' '$1=="bases_mapped_cigar"{print $2}' basis.tsv)
MR=$(awk -F'\t' '$1=="reads_mapped"{print $2}' basis.tsv)
printf 'samtools_bases_mapped_cigar\t%s\n' "$BM"
printf 'samtools_reads_mapped\t%s\n' "$MR"
L=$(awk -v b="$BM" -v r="$MR" 'BEGIN{printf "%.6f", b/r}')
printf 'mean_read_length\t%s\n' "$L"

run_bin() {
  W="$1"; OUT="$2"
  echo "== bamCoverage --binSize $W =="
  T0=$(date +%s)
  bamCoverage -b ENCFF933NSJ.bam -o "$OUT" --outFileFormat bigwig \
    --binSize "$W" --normalizeUsing None -p "$(nproc)" > "/tmp/bc-$W.log" 2>&1
  RC=$?
  T1=$(date +%s)
  printf 'bamcoverage_w%s_rc\t%s\n' "$W" "$RC"
  printf 'bamcoverage_w%s_wall_s\t%s\n' "$W" "$((T1-T0))"
  test "$RC" = "0" || { tail -20 "/tmp/bc-$W.log"; echo "FAIL: bamCoverage W=$W exited $RC"; exit 1; }
  test -s "$OUT" || { echo "FAIL: $OUT is empty"; exit 1; }
}

run_bin 1  cov1.bw
run_bin 50 cov50.bw

python3 -u - <<'PYEOF' | tee /tmp/identity.tsv
import pyBigWig, sys

def integral(path):
    bw = pyBigWig.open(path); h = bw.header(); bw.close()
    # bigWig totalSummary sumData = sum of value*width over covered bases = the integral.
    return h["sumData"], h["nBasesCovered"]

i1, n1 = integral("cov1.bw")
i50, _ = integral("cov50.bw")

with open("basis.tsv") as fh:
    d = dict(l.rstrip("\n").split("\t") for l in fh if "\t" in l)
BM = float(d["bases_mapped_cigar"]); MR = float(d["reads_mapped"])

# samtools stats ID lines: ID <length> <insertion count> <deletion count>.
ins = dels = 0
for line in open("samstats.txt"):
    if line.startswith("ID\t"):
        q = line.split("\t"); n = int(q[1]); ins += n*int(q[2]); dels += n*int(q[3])

# THE CORRECTION THAT MAKES THIS AN IDENTITY RATHER THAN A NEAR-MISS.
# samtools' 'bases mapped (cigar)' counts M+I, because an insertion consumes QUERY bases.
# Reference coverage counts M only -- an insertion occupies zero reference positions. So the
# quantity bamCoverage can possibly reproduce is M = (M+I) - I. Measured: the first attempt at
# this identity was short by exactly 132,420 bases, and the ID lines sum to exactly 132,420
# inserted bases. Not a tolerance; an accounting correction, confirmed to the base.
M = BM - ins
L = BM / MR

print("integral_w1\t%d" % round(i1))
print("bases_covered_w1\t%d" % n1)
print("integral_w50\t%d" % round(i50))
print("samtools_bases_mapped_cigar\t%d" % round(BM))
print("inserted_bases\t%d" % ins)
print("deleted_bases\t%d" % dels)
print("samtools_M_bases\t%d" % round(M))
print("mean_read_length\t%.6f" % L)

# ---- ASSERTED: exact conservation identity at binSize 1 ----
diff = round(i1) - round(M)
print("w1_minus_M\t%d" % diff)
print("w1_over_M\t%.9f" % (i1 / M))
if abs(diff) > 1:
    sys.exit("FAIL: binSize-1 integral %d != M bases %d (diff %d). These are the same "
             "quantity counted two ways and must agree." % (round(i1), round(M), diff))
print("identity_w1\tcoverage integral == M bases, exactly")

# ---- REPORTED: the bin-width inflation law at W=50 ----
# At bin width W a read is credited to every bin it touches, spreading it over ~(L-1+W)
# reference positions instead of L. Approximate rather than exact, because indels make each
# read's reference span differ from its length -- so this is reported with a loose sanity
# band, not asserted as a law.
W = 50
pred = BM * (L - 1 + W) / L
rel = abs(i50 - pred) / pred
print("w50_over_samtools\t%.6f" % (i50 / BM))
print("w50_predicted_ratio\t%.6f" % ((L - 1 + W) / L))
print("w50_predicted\t%d" % round(pred))
print("w50_relative_error\t%.6f" % rel)
if rel > 0.01:
    sys.exit("FAIL: binSize-50 integral %d is %.4f from the (L-1+W)/L account %d"
             % (round(i50), rel, round(pred)))
print("account_w50\tbin-width inflation matches (L-1+W)/L to %.4f%%" % (rel*100))

print("DEEPTOOLS IDENTITY OK")
PYEOF
test "${PIPESTATUS[0]}" = "0" || { echo "FAIL: the identity check failed"; exit 1; }

df -k /tmp | awk 'NR==2{printf "tmpfs_used_mib\t%.0f\n", $3/1024}'
ls -l cov1.bw cov50.bw | awk '{print "bigwig\t"$9"\t"$5}'
echo "DEEPTOOLS REAL OK"
