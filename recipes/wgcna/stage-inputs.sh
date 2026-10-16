#!/usr/bin/env bash
# Stage WGCNA's planted-module fixture: an expression matrix generated HERE from a fixed seed
# and pinned by sha256, so both architectures read identical bytes (generating it in the task
# would tie the input to each image's RNG and number formatting).
#
#   60 samples x 600 genes. Four latent eigengenes E1..E4 (iid N(0,1) per sample). Genes 1-400
#   load on exactly one: x = a*E_m + sqrt(1-a^2)*noise, a ~ U(0.6, 0.9). In modules 1 and 2,
#   40 of the 100 genes load NEGATIVELY -- one module to an unsigned network, two to a signed
#   one. Genes 401-600 are pure noise (planted label 0).
#   control.csv is expr.csv with every gene's column permuted independently: the same marginals,
#   no co-expression, so no module can be real.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=wgcna does this}"
REGION="${AWS_REGION:-us-west-2}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

python3 - <<'PY'
import math, random
random.seed(20261016)
N, K, G_MOD, G_NOISE = 60, 4, 100, 200
E = [[random.gauss(0, 1) for _ in range(N)] for _ in range(K)]
cols, names, uns, sgn = [], [], [], []
for m in range(K):
    for j in range(G_MOD):
        a = random.uniform(0.6, 0.9)
        neg = m < 2 and j >= 60
        s = -1.0 if neg else 1.0
        cols.append([s * a * E[m][i] + math.sqrt(1 - a * a) * random.gauss(0, 1) for i in range(N)])
        names.append("g%03d" % (len(names) + 1)); uns.append(m + 1)
        sgn.append(10 * (m + 1) + (2 if neg else 1))
for j in range(G_NOISE):
    cols.append([random.gauss(0, 1) for _ in range(N)])
    names.append("g%03d" % (len(names) + 1)); uns.append(0); sgn.append(0)
ctrl = []
for c in cols:
    c = c[:]; random.shuffle(c); ctrl.append(c)
def write(fn, cs, hdr):
    with open(fn, "w") as f:
        f.write(",".join(["sample"] + hdr) + "\n")
        for i in range(N):
            f.write(",".join(["s%02d" % (i + 1)] + ["%.12f" % c[i] for c in cs]) + "\n")
write("expr.csv", cols, names)
write("control.csv", ctrl, names)
write("latent.csv", E, ["E%d" % (m + 1) for m in range(K)])
with open("labels.tsv", "w") as f:
    f.write("gene\tunsigned\tsigned\n")
    for n, u, s in zip(names, uns, sgn):
        f.write("%s\t%d\t%d\n" % (n, u, s))
print("  %d samples x %d genes: %d planted modules (%d signed), %d noise genes"
      % (N, len(names), K, len(set(sgn)) - 1, G_NOISE))
PY

sha256sum expr.csv control.csv latent.csv labels.tsv > fixture.sha256 2>/dev/null \
  || shasum -a 256 expr.csv control.csv latent.csv labels.tsv > fixture.sha256
for f in expr.csv control.csv latent.csv labels.tsv fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/wgcna/$f"
done
cat fixture.sha256
echo "done.  $BUCKET/inputs/wgcna/"
