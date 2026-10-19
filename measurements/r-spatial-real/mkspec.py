#!/usr/bin/env python3
"""Write this measurement's two arm64 specs. x86 and AMD come from scripts/spec_arch.py.

    python3 measurements/r-spatial-real/mkspec.py
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/r@sha256:136bf063a0d04967e2e2dac8622259edf344d5aa74cdd5869502007cf6c5a56c"
B = "s3://${COOKBOOK_BUCKET}"
FIPS = ("01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 "
        "33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 60 66 69 72 78")

PRE = """set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
"""

FETCH = PRE + """mkdir -p /tmp/tract && cd /tmp/tract
# Census TIGER/Line 2024 tracts, one zip per state-equivalent (50 states, DC, PR, 4 island areas).
# Fetched on AWS, never through a workstation; pinned by the sha256 of the bytes as fetched.
for s in %s; do
  f=tl_2024_${s}_tract.zip
  Rscript -e "options(timeout=600); download.file('https://www2.census.gov/geo/tiger/TIGER2024/TRACT/$f', '$f', mode='wb', quiet=TRUE, method='libcurl')" || { echo "FAIL: $f did not download"; exit 1; }
  test -s "$f" || { echo "FAIL: $f empty"; exit 1; }
done
sha256sum tl_2024_*_tract.zip > /tmp/tracts.sha256
echo "files $(wc -l < /tmp/tracts.sha256)  bytes $(cat tl_2024_*_tract.zip | wc -c)"
cat /tmp/tracts.sha256
echo DONE
""" % FIPS


def run_cmd():
    sha = hashlib.sha256(open(os.path.join(HERE, "real.R"), "rb").read()).hexdigest()
    return PRE + """cd /tmp
mkdir -p /tmp/out /var/tmp/work
echo "== staged bytes must match their pins =="
( cd /tmp/tract && sha256sum -c /tmp/tracts.sha256 ) > /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a tract zip does not match its pin"; exit 1; }
( cd /tmp/dem && sha256sum -c tiles.sha256 ) >> /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a DEM tile does not match its pin"; exit 1; }
echo "04e668d3502757c837c13444730547cd967f28a2c49aeffb873d1792ab2cb97b  /tmp/tiger.zip" | sha256sum -c - || { echo "FAIL: counties do not match geo-ml's pin"; exit 1; }
echo "%s  /tmp/real.R" | sha256sum -c - || { echo "FAIL: real.R does not match its pin"; exit 1; }
echo "$(wc -l < /tmp/tracts.sha256) tract zips + $(wc -l < /tmp/dem/tiles.sha256) DEM tiles + counties match their pins"
uname -m | tee /tmp/out/arch.txt
lscpu | grep -E '^(Model name|Thread\\(s\\) per core|Core\\(s\\) per socket|Socket\\(s\\)|CPU\\(s\\)):' | tee /tmp/out/lscpu.txt
T0=$(date +%%s)
Rscript real.R > /tmp/out/real.log 2>&1
RC=$?
echo "real.R exit $RC in $(( $(date +%%s) - T0 ))s" | tee /tmp/out/wall.txt
echo "memory.peak_gib $(awk '{printf "%%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)" | tee /tmp/out/footprint.txt
cat /tmp/out/real.log
test "$RC" = 0 || { echo "FAIL: the workload failed"; exit 1; }
grep -q 'R-SPATIAL REAL OK' /tmp/out/real.log || { echo "FAIL: no completion sentinel"; exit 1; }
test -s /tmp/out/score.tsv || { echo "FAIL: no score.tsv"; exit 1; }
echo DONE
""" % sha


def write(name, spec):
    with open(os.path.join(HERE, name), "w") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")


write("fetch.task.json", {
    "task_id": "cookbook-r-spatial-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", FETCH],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "outputs": [
        {"source": "/tmp/tract/", "destination": B + "/inputs/r-spatial-real/tract/"},
        {"source": "/tmp/tracts.sha256", "destination": B + "/inputs/r-spatial-real/tracts.fetched.sha256"},
    ],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.1},
})

if os.path.exists(os.path.join(HERE, "real.R")):
    write("real.task.json", {
        "task_id": "cookbook-r-spatial-real",
        "container": IMAGE,
        "command": ["bash", "-c", run_cmd()],
        "resources": {"cpu": 8, "memory_gib": 64, "architecture": "arm64", "families": ["r8g"],
                      "disk_gib": 40},
        "inputs": [
            {"source": B + "/inputs/r-spatial-real/tract/", "destination": "/tmp/tract/"},
            {"source": B + "/inputs/r-spatial-real/tracts.sha256", "destination": "/tmp/tracts.sha256"},
            {"source": B + "/inputs/r-spatial-real/real.R", "destination": "/tmp/real.R"},
            {"source": B + "/inputs/geo-ml/tiger.zip", "destination": "/tmp/tiger.zip"},
            {"source": B + "/inputs/geospatial-real/", "destination": "/tmp/dem/"},
        ],
        "outputs": [
            {"source": "/tmp/out/", "destination": B + "/runs/r-spatial-real/r1/"},
        ],
        "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 1.0},
    })
