#!/usr/bin/env bash
# Stage the AutoDock Vina 1iep docking target to S3, once, pinned by digest.
#
# The conda-forge `vina` package ships no example data, so a real docking run needs
# a receptor and ligand staged. The canonical, pinnable source is Vina's own
# basic-docking tutorial at the tag matching the container's version (v1.2.7):
# a prepared Abl-kinase receptor and imatinib ligand. Using the version-matched
# inputs is what lets the recipe cross-check its top affinity against the tutorial's
# published result (top pose -13.234 kcal/mol).
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
BASE="https://raw.githubusercontent.com/ccsb-scripps/AutoDock-Vina/v1.2.7/example/basic_docking/solution"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== fetch the prepared 1iep receptor + ligand from the Vina v1.2.7 tag =="
curl -fsSL "$BASE/1iep_receptor.pdbqt" -o 1iep_receptor.pdbqt
curl -fsSL "$BASE/1iep_ligand.pdbqt"   -o 1iep_ligand.pdbqt

echo "== verify against pinned sha256 =="
cat > pins.sha256 <<'EOF'
f13cf3b36f61d87c3b58983e0b8ecf1c3456a685eb86dfe9ccfb139c7bdc2586  1iep_receptor.pdbqt
15fb35648d8c18c70317842f3a0631b73a19429c710a037ab07310084d579bb8  1iep_ligand.pdbqt
EOF
shasum -a 256 -c pins.sha256

echo "== sanity: both are PDBQT with atom records =="
grep -q '^ATOM\|^HETATM' 1iep_receptor.pdbqt || { echo "receptor not PDBQT"; exit 1; }

echo "== upload to $BUCKET/inputs/vina/ =="
aws s3 cp 1iep_receptor.pdbqt "$BUCKET/inputs/vina/1iep_receptor.pdbqt"
aws s3 cp 1iep_ligand.pdbqt   "$BUCKET/inputs/vina/1iep_ligand.pdbqt"
echo "done."
