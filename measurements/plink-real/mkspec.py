#!/usr/bin/env python3
"""Write the plink-real TaskSpec for a chromosome set (the full run is 1..22; the probe 21 22).

    python3 mkspec.py "<chroms>" <task_id> <run label> <ttl> <cost_limit> > spec.task.json
"""
import json
import sys

chroms = sys.argv[1].split()
tid, out, ttl, cap = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
B = "s3://${COOKBOOK_BUCKET}/inputs/plink-real/"
V = "ALL.chr%s.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"
cmd = r"""set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp CAUSAL=22:49552130:A:G
cd /tmp
mkdir -p /tmp/out
CHROMS="%s"
echo "== staged bytes must match their pins =="
for c in $CHROMS; do grep -F ".chr$c.phase3" pins.sha256; done > pins.sel
grep -E 'panel|noise|real.sh' pins.sha256 >> pins.sel
sha256sum -c pins.sel > /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a staged input does not match its pin"; exit 1; }
echo "$(wc -l < pins.sel) files match their pins"
# footprint, every 5 s: anonymous memory (RSS, not page cache), the container-disk work dir,
# and cumulative CPU (cpu.stat usage_usec), from which the average busy cores follow
( while : ; do echo "$(date +%%s) $(awk '/^(anon|rss) /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(du -sk /var/tmp/plink-real 2>/dev/null | cut -f1) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null) $(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6}' /proc/stat)" >> /tmp/out/samples.txt; sleep 5; done ) &
SPID=$!
T0=$(date +%%s)
bash real.sh /tmp $CHROMS > /tmp/out/real.log 2>&1
RC=$?
echo "real.sh exit $RC in $(( $(date +%%s) - T0 ))s"
kill $SPID
awk 'NR==1{t0=$1; u0=$4; j0=$5; w0=$6} $2>m{m=$2} $3>d{d=$3} {t=$1; u=$4; j=$5; w=$6} END{printf "peak_anon_gib %%.2f\npeak_workdir_gib %%.1f\navg_busy_cores %%.2f\nhost_iowait_fraction %%.3f\n", m/1073741824, d/1048576, (t>t0)?(u-u0)/1e6/(t-t0):0, (j>j0)?(w-w0)/(j-j0):0}' /tmp/out/samples.txt | tee /tmp/out/footprint.txt
cat /tmp/out/real.log
test "$RC" = 0 || { echo "FAIL: the workload failed"; exit 1; }
grep -q 'PLINK REAL OK' /tmp/out/real.log || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
""" % " ".join(chroms)
spec = {
    "task_id": tid,
    "container": "quay.io/aarchbio/plink@sha256:9d0a839edcdba396b6a6d374f60c3ea28fdc10954dbcbc5edef5795a1ae4f4f7",
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"], "disk_gib": 120},
    "inputs": [{"source": B + V % c, "destination": "/tmp/" + V % c} for c in chroms]
    + [{"source": B + f, "destination": "/tmp/" + f} for f in
       ["integrated_call_samples_v3.20130502.ALL.panel", "noise.txt", "real.sh", "pins.sha256"]],
    "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/plink-real/%s/" % out}],
    "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
}
json.dump(spec, sys.stdout, indent=2)
print()
