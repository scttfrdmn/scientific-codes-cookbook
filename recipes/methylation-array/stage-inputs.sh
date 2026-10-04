#!/usr/bin/env bash
# Stage SeSAMe's ExperimentHub cache, so the second task can run offline.
#
# WHY this exists: `openSesame` fetches its platform manifest, probe mask and IDAT
# signature from ExperimentHub at first use, and a recipe here may not fetch data at run
# time. Measured with the network disabled, which is the only way to find this out rather
# than being misled by a green run on a machine that has internet:
#
#   openSesame FAILED: File idatSignature either not found or needs to be cached
#
# Same shape as conda-forge SIESTA shipping no pseudopotentials: caching once here and
# shipping the pinned result is staging, not runtime-fetching
# (practices/reference-from-tests.md).
#
# The minimal set was found by iterating: cache, run, read which resource the error names,
# repeat. Three, not the whole archive — `sesameDataCache()` with no argument downloads
# everything, which is gigabytes for a 21 MB need.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
SESAME="quay.io/aarchbio/bioconductor-sesame@sha256:624254ed82a905cef9814b27a1e4ec417072d8bd002899498c080301dca3295b"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/cache"

echo "== cache the three resources openSesame needs for HM450 (needs network, once) =="
cat > "$tmp/cache.R" <<'REOF'
suppressMessages(library(sesame))
for (res in c("idatSignature", "HM450.address", "KYCG.HM450.Mask.20220123")) {
  suppressMessages(sesameDataCache(res))
  cat("cached", res, "\n")
}
cat("sesameData", as.character(packageVersion("sesameData")),
    "sesame", as.character(packageVersion("sesame")), "\n")
REOF
docker run --rm --platform linux/arm64 \
  -v "$tmp/cache:/cache" -v "$tmp:/work" \
  "$SESAME" bash -c 'export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/cache
                     Rscript /work/cache.R' 2>&1 | grep -E "^cached|^sesameData"

CACHE="$tmp/cache/.cache/R/ExperimentHub"
test -d "$CACHE" || { echo "cache dir missing" >&2; exit 1; }

echo "== verify the resource CONTENTS against the pins =="
# The pin is on content, not on the tar. BiocFileCache names each blob with a random
# prefix and the sqlite/LOCK files carry timestamps, so neither the filenames nor the
# tar's own sha256 are reproducible — but the downloaded resources are immutable, and
# their EH id is the stable suffix. So: hash every non-sqlite blob and require the SET
# to match. A silent upstream change fails here, not on the box.
cat > "$tmp/pins.sha256" <<'EOF'
7f361fdce9de733ab7b59461d89d950bcca3bf363cea999176f281ab7df2d982  hub_index.rds
73578ce17c439acc8e1443f9a6c55c3f02fdcc18cdff287b7b93f74cb86c835e  EH8618
b4eb0da4ef492655c2fe18b45fa65e969967c9f4a0c2149a5fae9241b510c7b2  EH7369
99300ba1d45aec5d5fdcdb12b61923f7313b1600c4ec9c8b9ca573d09bfc1655  EH7367
EOF
( cd "$CACHE" && for f in $(ls | grep -vE "sqlite|LOCK"); do
    shasum -a 256 "$f" | cut -d' ' -f1
  done | sort ) > "$tmp/got.txt"
cut -d' ' -f1 "$tmp/pins.sha256" | sort > "$tmp/want.txt"
if ! diff -q "$tmp/want.txt" "$tmp/got.txt" >/dev/null; then
  echo "ExperimentHub resource contents do not match the pins:" >&2
  diff "$tmp/want.txt" "$tmp/got.txt" >&2 || true
  exit 1
fi
echo "  OK: $(wc -l < "$tmp/got.txt" | tr -d ' ') resource blobs match their pinned sha256"

echo "== tar the cache to ONE flat file =="
# A directory cannot be a spawn output on the container path, and the cache is a tree
# (practices/container-path.md). The tar's own sum is deliberately NOT a pin — see above.
tar -cf "$tmp/sesame-cache.tar" -C "$tmp/cache" .
echo "  sesame-cache.tar: $(du -h "$tmp/sesame-cache.tar" | cut -f1)"

echo "== upload =="
aws s3 cp "$tmp/sesame-cache.tar" "$BUCKET/inputs/methylation-array/sesame-cache.tar" --only-show-errors
echo "  -> $BUCKET/inputs/methylation-array/sesame-cache.tar"
echo "done."
