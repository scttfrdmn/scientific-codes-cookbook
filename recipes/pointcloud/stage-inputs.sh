#!/usr/bin/env bash
# Stage PDAL's canonical autzen point cloud to S3, once, pinned by digest.
#
# The pointcloud recipe reads a REAL cloud (the env's own D3 uses synthetic faux.reader
# points). autzen is PDAL's reference dataset — 10,653,336 points of Oregon LiDAR — hosted
# in the PDAL/data repo via Git LFS. Pinned at a specific commit + sha256, so the LFS
# media bytes are immutable.
set -euo pipefail
BUCKET="s3://scicookbook-942542972736-us-east-1"
COMMIT="360327d2ae791b9d52c57b610a5a6b5c1b08c878"
SRC="https://github.com/PDAL/data/raw/${COMMIT}/autzen/autzen.laz"
SHA="944b947501156e45df1b3b9d25bc1dc04ff5ef377e7e169576ba59231c2896ba"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT; cd "$tmp"
echo "== fetch autzen.laz (PDAL/data, Git LFS media) =="
curl -fsSL "$SRC" -o autzen.laz
echo "== verify against pinned sha256 =="
printf '%s  %s\n' "$SHA" autzen.laz > pins.sha256; shasum -a 256 -c pins.sha256
echo "== sanity: LAS/LAZ magic 'LASF' =="
head -c 4 autzen.laz | grep -q "LASF" || { echo "not a LAS/LAZ"; exit 1; }
echo "== upload =="
aws s3 cp autzen.laz "$BUCKET/inputs/pointcloud/autzen.laz"
echo "done."
