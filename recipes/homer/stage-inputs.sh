#!/usr/bin/env bash
# Stage HOMER's synthetic de novo fixture. The genome-scale half needs nothing new: it reads
# GRCh38 chr20 (staged by bwa-samtools) and the CTCF peaks macs2 calls on it.
#
# The planted-motif FASTA is generated HERE, not in the task, and pinned by sha256: the HOMER
# image has no python3, and generating it with awk rand() in the task would give different
# sequences per architecture (mawk vs BusyBox awk draw different streams), so a cross-arch
# comparison would not be on identical bytes. Generated once, the bytes are the same anywhere.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=homer does this}"
REGION="${AWS_REGION:-us-west-2}"

aws s3 ls --region "$REGION" "$BUCKET/inputs/bwa-samtools/chr20.fa" >/dev/null \
  || { echo "  inputs/bwa-samtools/chr20.fa missing -- run: make stage RECIPE=bwa-samtools" >&2; exit 1; }
aws s3 ls --region "$REGION" "$BUCKET/runs/macs2/r1/peaks.narrowPeak" >/dev/null \
  || { echo "  runs/macs2/r1/peaks.narrowPeak missing -- run: make run RECIPE=macs2" >&2; exit 1; }
echo "  chr20.fa and macs2's CTCF peaks are present"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== the planted-motif fixture, generated and verified without HOMER =="
python3 - <<'PY'
import random, sys
MOTIF = "GATTACAGGT"
RC = MOTIF[::-1].translate(str.maketrans("ACGT", "TGCA"))
random.seed(20261013)
def rs(n): return "".join(random.choice("ACGT") for _ in range(n))
def write(name, n, plant):
    with open(name, "w") as f:
        for i in range(n):
            s = rs(200)
            if plant:
                p = random.randrange(0, 191)
                s = s[:p] + MOTIF + s[p + 10:]
            f.write(">%s%d\n%s\n" % (name[0], i, s))
write("targets.fa", 400, True)
write("background.fa", 2000, False)
write("control.fa", 400, False)
def count(name):
    seqs = [l.strip() for l in open(name) if not l.startswith(">")]
    return sum(1 for s in seqs if MOTIF in s or RC in s), len(seqs)
for name in ("targets.fa", "background.fa", "control.fa"):
    hit, n = count(name)
    print("  %-14s %4d sequences, %4d carry %s or its reverse complement" % (name, n, hit, MOTIF))
t, _ = count("targets.fa"); b, nb = count("background.fa"); c, _ = count("control.fa")
if t != 400:
    sys.exit("  every target must carry the planted motif")
# A random 10-mer occurs at ~2*191/4^10 = 3.6e-4 per 200 bp sequence, so a background hit
# is possible but rare; the claim is only that planting is what separates the sets.
if b > 5 or c > 3:
    sys.exit("  the unplanted sets carry the motif too often to be a background")
print("  planted motif in every target; background and control are unplanted")
PY

sha256sum targets.fa background.fa control.fa > fixture.sha256 2>/dev/null \
  || shasum -a 256 targets.fa background.fa control.fa > fixture.sha256
for f in targets.fa background.fa control.fa fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/homer/$f"
done
cat fixture.sha256
echo "done."
echo "  $BUCKET/inputs/homer/  (+ reads inputs/bwa-samtools/chr20.fa and runs/macs2/r1/peaks.narrowPeak)"
