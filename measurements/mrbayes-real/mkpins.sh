#!/usr/bin/env bash
# Rewrite pins.sha256: the two scripts, the recipe's two converters, and the recorded sha256 of the
# shared alignment and of the two ML runs' outputs (Graviton4 runs of recipes/iqtree and raxml-ng).
set -euo pipefail
cd "$(dirname "$0")"
{
  shasum -a 256 mcmc.sh compare.sh splits.awk
  (cd ../../recipes/mrbayes && shasum -a 256 fasta2nexus.awk nexus2newick.awk)
  echo "b4d4d74594d6213f26dfbd8eeb194ae9a9eaf6c529242384a7395bb56c7c7294  aln.fa"
  echo "f04018065af132eb1567dbaeb8270a614b02490fd470281be88f6a6aed865fff  iqout.treefile"
  echo "0b38645699ca4cd69b7fd6a44ecc2cd05a7887702678e90513e4588b723ec9a7  iqout.iqtree"
  echo "8923f36243e2c27e041da9e4e823408429ceef80fe17f4b2994b8c18c9608ced  rx.raxml.bestTree"
  echo "6ce86e928951e3d3b09a30ce7a5567607b82e9a0589eae5686dc201436930606  rx.raxml.bestModel"
  echo "05cb465e183f6e39aa17c7aa60f4754bd6891d0e471e8a4b8b3d930b03778721  rx.raxml.log"
} > pins.sha256
cat pins.sha256
