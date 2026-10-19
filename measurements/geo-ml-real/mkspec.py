#!/usr/bin/env python3
"""Write real.task.json, the arm64 spec. Intel and AMD come from scripts/spec_arch.py.

    python3 measurements/geo-ml-real/mkspec.py
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/geo-ml@sha256:ed8c59dedeb4c1bc9d4cfef85f14740947f9b1ed23bb00d083c6f163b25529e0"
B = "s3://${COOKBOOK_BUCKET}"
sha = hashlib.sha256(open(os.path.join(HERE, "real.py"), "rb").read()).hexdigest()

CMD = """set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1
cd /tmp
mkdir -p /tmp/out /var/tmp/work
echo "== staged bytes must match their pins =="
( cd /tmp/tract && sha256sum -c /tmp/tracts.sha256 ) > /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a tract zip does not match its pin"; exit 1; }
( cd /tmp/dem && sha256sum -c tiles.sha256 ) >> /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a DEM tile does not match its pin"; exit 1; }
echo "04e668d3502757c837c13444730547cd967f28a2c49aeffb873d1792ab2cb97b  /tmp/tiger.zip" | sha256sum -c - || { echo "FAIL: counties do not match geo-ml's pin"; exit 1; }
echo "%s  /tmp/real.py" | sha256sum -c - || { echo "FAIL: real.py does not match its pin"; exit 1; }
echo "$(wc -l < /tmp/tracts.sha256) tract zips + $(wc -l < /tmp/dem/tiles.sha256) DEM tiles + counties match their pins"
uname -m | tee /tmp/out/arch.txt
lscpu | grep -E '^(Model name|Thread\\(s\\) per core|Core\\(s\\) per socket|Socket\\(s\\)|CPU\\(s\\)):' | tee /tmp/out/lscpu.txt
T0=$(date +%%s)
python3 -u real.py > /tmp/out/real.log 2>&1
RC=$?
echo "real.py exit $RC in $(( $(date +%%s) - T0 ))s" | tee /tmp/out/wall.txt
echo "memory.peak_gib $(awk '{printf "%%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)" | tee /tmp/out/footprint.txt
cat /tmp/out/real.log
test "$RC" = 0 || { echo "FAIL: the workload failed"; exit 1; }
grep -q 'GEO-ML REAL OK' /tmp/out/real.log || { echo "FAIL: no completion sentinel"; exit 1; }
test -s /tmp/out/tract-areas.csv && test -s /tmp/out/co-tract-zonal.csv || { echo "FAIL: missing CSVs"; exit 1; }
echo DONE
""" % sha

spec = {
    "task_id": "cookbook-geo-ml-real",
    "container": IMAGE,
    "command": ["bash", "-c", CMD],
    "resources": {"cpu": 4, "memory_gib": 32, "architecture": "arm64", "families": ["r8g"], "disk_gib": 40},
    "inputs": [
        {"source": B + "/inputs/r-spatial-real/tract/", "destination": "/tmp/tract/"},
        {"source": B + "/inputs/r-spatial-real/tracts.sha256", "destination": "/tmp/tracts.sha256"},
        {"source": B + "/inputs/geo-ml/tiger.zip", "destination": "/tmp/tiger.zip"},
        {"source": B + "/inputs/geospatial-real/", "destination": "/tmp/dem/"},
        {"source": B + "/inputs/geo-ml-real/real.py", "destination": "/tmp/real.py"},
        # terra's results on the same bytes; spec_arch.py maps runs/ to the same chip's runs-*/
        {"source": B + "/runs/r-spatial-real/r1/tract-areas.csv", "destination": "/tmp/terra-tract-areas.csv"},
        {"source": B + "/runs/r-spatial-real/r1/co-tract-zonal.csv", "destination": "/tmp/terra-co-tract-zonal.csv"},
    ],
    "outputs": [{"source": "/tmp/out/", "destination": B + "/runs/geo-ml-real/r1/"}],
    "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.6},
}
with open(os.path.join(HERE, "real.task.json"), "w") as f:
    json.dump(spec, f, indent=2)
    f.write("\n")
