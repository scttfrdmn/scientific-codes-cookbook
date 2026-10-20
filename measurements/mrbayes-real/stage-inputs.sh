#!/usr/bin/env bash
# Stage mrbayes-real. Nothing is downloaded and the alignment is not copied: the run reads the
# object recipes/iqtree staged (Pfam 38.2 PF26127.1 seed, 114 x 477), which recipes/raxml-ng and
# recipes/mrbayes already read, so the Bayesian and ML trees stay on identical bytes. The two ML
# results it is compared with -- IQ-TREE's and RAxML-NG's Graviton4 runs of those recipes -- are
# copied server-side into inputs/mrbayes-real/ so every chip compares against the same files.
# Every object, staged or reused, is checked against pins.sha256 here and again inside each task.
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/mrbayes-real"
cp_s3() { aws s3 cp --region "$REGION" --only-show-errors "$@"; }

for f in iqout.treefile iqout.iqtree; do cp_s3 "$BUCKET/runs/iqtree/r1/$f" "$P/$f"; done
for f in rx.raxml.bestTree rx.raxml.bestModel rx.raxml.log; do cp_s3 "$BUCKET/runs/raxml-ng/r1/$f" "$P/$f"; done
for f in mcmc.sh compare.sh splits.awk pins.sha256; do cp_s3 "$HERE/$f" "$P/$f"; done
for f in fasta2nexus.awk nexus2newick.awk; do cp_s3 "$HERE/../../recipes/mrbayes/$f" "$P/$f"; done

echo "== every staged object against its pin =="
WORK="$HOME/.xtmp/mrbayes-real-stage"; [ -d "$HOME/.xtmp" ] || WORK="$(mktemp -d)"
mkdir -p "$WORK"; trap 'rm -rf "$WORK"' EXIT
cp_s3 "$BUCKET/inputs/iqtree/pfam38.2_seed_alignment.fa" "$WORK/aln.fa"
for f in iqout.treefile iqout.iqtree rx.raxml.bestTree rx.raxml.bestModel rx.raxml.log mcmc.sh compare.sh splits.awk fasta2nexus.awk nexus2newick.awk; do
  cp_s3 "$P/$f" "$WORK/$f"
done
(cd "$WORK" && shasum -a 256 -c "$HERE/pins.sha256")
echo "staged to $P/"
