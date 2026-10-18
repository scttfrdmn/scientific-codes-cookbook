#!/usr/bin/env python3
"""Write colorado.task.json (the arm64 spec; spec_arch.py derives the Intel and AMD ones from it).

    python3 measurements/geospatial-real/mkspec.py > measurements/geospatial-real/colorado.task.json
    DIAG=1 python3 measurements/geospatial-real/mkspec.py 20m 0.30 > measurements/geospatial-real/sumdiag.task.json
    DIAG=2 python3 measurements/geospatial-real/mkspec.py 20m 0.30 > measurements/geospatial-real/sumdiag2.task.json
    DIAG=3 python3 measurements/geospatial-real/mkspec.py 20m 0.30 > measurements/geospatial-real/sumdiag3.task.json
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}/inputs/geospatial-real/"
tiles = [l.split()[1] for l in open(os.path.join(HERE, "tiles.sha256"))]
DIAG = os.environ.get("DIAG") in ("1", "2", "3")
SET = os.environ.get("DIAG", "1")
SCRIPT, SENTINEL, NAME = ("sumdiag.py", "SUMDIAG DONE", "sumdiag" + ("" if SET == "1" else SET)) if DIAG else ("real.py", "GEOSPATIAL REAL OK", "colorado")
real_sha = hashlib.sha256(open(os.path.join(HERE, SCRIPT), "rb").read()).hexdigest()
cmd = r"""set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
cd /tmp
mkdir -p /tmp/out /var/tmp/work
echo "== staged bytes must match their pins =="
sha256sum -c tiles.sha256 > /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a staged tile does not match its pin"; exit 1; }
echo "%s  SCRIPT" | sha256sum -c - || { echo "FAIL: SCRIPT does not match its pin"; exit 1; }
echo "$(wc -l < tiles.sha256) tiles match their pins"
# footprint, every 2 s: container anonymous memory and cumulative CPU (cgroup v2)
( while : ; do echo "$(date +%%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> /tmp/out/samples.txt; sleep 2; done ) &
SPID=$!
T0=$(date +%%s)
@@PFX@@python3 -u SCRIPT > /tmp/out/real.log 2>&1
RC=$?
echo "real.py exit $RC in $(( $(date +%%s) - T0 ))s"
kill $SPID
awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3} END{printf "peak_anon_gib %%.2f\navg_busy_cores %%.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' /tmp/out/samples.txt | tee /tmp/out/footprint.txt
echo "memory.peak_gib $(awk '{printf "%%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)" | tee -a /tmp/out/footprint.txt
cat /tmp/out/real.log
test "$RC" = 0 || { echo "FAIL: the workload failed"; exit 1; }
grep -q 'SENTINEL' /tmp/out/real.log || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
""".replace("SCRIPT", SCRIPT).replace("SENTINEL", SENTINEL).replace(
    "@@PFX@@", ("SUMDIAG_SET=%s " % SET) if DIAG else "") % real_sha
spec = {
    "task_id": "cookbook-geospatial-real" + (("-sumdiag" + ("" if SET == "1" else SET)) if DIAG else ""),
    "container": "quay.io/aarchsci/geospatial@sha256:1827aeb547547b3e7ced8ede5cde82563635a765c2d5455221347ee2c10a74fb",
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": 16, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
    "inputs": [{"source": B + t, "destination": "/tmp/" + t} for t in tiles]
    + [{"source": B + f, "destination": "/tmp/" + f} for f in ("tiles.sha256", SCRIPT)]
    + [{"source": "s3://${COOKBOOK_BUCKET}/inputs/geospatial/gie.tar", "destination": "/tmp/gie.tar"}],
    "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/geospatial-real/%s/" % NAME}],
    "lifecycle": {"ttl": sys.argv[1] if len(sys.argv) > 1 else "90m", "on_complete": "terminate",
                  "cost_limit": float(sys.argv[2]) if len(sys.argv) > 2 else 1.00},
}
json.dump(spec, sys.stdout, indent=2)
print()
