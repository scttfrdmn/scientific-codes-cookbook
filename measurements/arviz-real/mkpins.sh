#!/usr/bin/env bash
# Regenerate pins.sha256: cmdstan-real's posteriordb pins, plus this directory's two task scripts.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
{
  grep ' pdb/' ../cmdstan-real/pins.sha256
  shasum -a 256 arviz_real.py real.sh
} > pins.sha256
echo "pins.sha256: $(grep -c . pins.sha256) entries"
