"""Write this directory's specs, with the current sha256 of the staged scripts pinned into them.
   python3 measurements/openmm-mdanalysis-real/mkspec.py
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d"
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/openmm-mdanalysis-real/"


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


FETCH = r'''set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
mkdir -p /tmp/out && cd /tmp/out
# The Amber20 benchmark suite (ambermd.org) -- the archive OpenMM's own
# examples/benchmarks/benchmark.py fetches for its amber20-dhfr/-cellulose/-stmv tests.
# Fetched on AWS; its sha256 is recorded and pinned in inputs.sha256.
python3 -u - <<'PYEOF' || { echo "FAIL: fetch"; exit 1; }
import hashlib, tarfile, urllib.request
URL = "https://ambermd.org/Amber20_Benchmark_Suite.tar.gz"
h = hashlib.sha256()
with urllib.request.urlopen(URL, timeout=600) as r, open("Amber20_Benchmark_Suite.tar.gz", "wb") as fh:
    while True:
        b = r.read(1 << 20)
        if not b:
            break
        h.update(b); fh.write(b)
print("sha256", h.hexdigest())
t = tarfile.open("Amber20_Benchmark_Suite.tar.gz")
names = t.getmembers()
with open("listing.txt", "w") as fh:
    for m in names:
        fh.write("%d %s\n" % (m.size, m.name))
with open("texts.txt", "w") as fh:
    for m in names:
        if m.isfile() and m.size < 200000 and not m.name.endswith((".prmtop", ".inpcrd", ".rst7", ".gz")):
            fh.write("===== %s\n" % m.name)
            fh.write(t.extractfile(m).read().decode("utf-8", "replace"))
print("entries", len(names))
PYEOF
sha256sum Amber20_Benchmark_Suite.tar.gz | tee suite.sha256
head -100 listing.txt
ls -l
echo DONE
'''

def pinned(scripts, run):
    """A command that refuses to run unless every staged script matches the sha256 it had here."""
    pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
                   for f in scripts)
    return ("set +e\n# spawn runs task commands under bash -e (spawn#707); the scripts set their own options.\n"
            "set -uo pipefail\ncd /tmp\n"
            "printf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n") % (pins, run)


SUITE = {"source": IN + "amber20/Amber20_Benchmark_Suite.tar.gz", "destination": "/tmp/Amber20_Benchmark_Suite.tar.gz"}


def staged(names):
    return [{"source": IN + f, "destination": "/tmp/" + f} for f in names]


# sander: the ambertools recipe's pinned image (spec_arch.py ambertools <arch> resolves its x86 twin).
SANDER = ["sander.sh", "inputs.sha256"]
write("01-sander.task.json", {
    "task_id": "cookbook-openmm-mdanalysis-real-sander",
    "container": "quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f",
    "command": ["bash", "-c", pinned(SANDER, "bash /tmp/sander.sh")],
    "resources": {"cpu": 2, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [SUITE] + staged(SANDER),
    "outputs": [{"source": "/tmp/out/", "destination": S3 + "runs/openmm-mdanalysis-real/sander/"}],
    "lifecycle": {"ttl": "40m", "on_complete": "terminate", "cost_limit": 0.10},
})

def openmm(name, run, cpu, mem, threads, jac, stmv, probe="", check=True, ttl="2h", cap=2.0):
    scripts = ["run.sh", "real.py", "inputs.sha256"] + (["check.py"] if check else [])
    write(name, {
        "task_id": "cookbook-openmm-mdanalysis-real-%s" % run,
        "container": IMAGE,
        "command": ["bash", "-c", pinned(scripts, "bash /tmp/run.sh %d %d %d %s" % (threads, jac, stmv, probe))],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": ["c8g"]},
        "inputs": [SUITE] + staged(scripts) + [
            {"source": S3 + "runs/openmm-mdanalysis-real/sander/sander-%s.mdout" % s,
             "destination": "/tmp/sander/sander-%s.mdout" % s} for s in ("JAC", "JAC-novdwcorr", "STMV", "STMV-novdwcorr")],
        "outputs": [{"source": "/tmp/out/", "destination": S3 + "runs/openmm-mdanalysis-real/%s/" % run}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    })


# The sizing probe (r. 2026-10-19): thread scan, the suite's 2,500-step CPU run of JAC, STMV single points.
openmm("probe.task.json", "probe", 32, 64, 32, 2500, 0, "8,16,32", check=False, ttl="90m", cap=1.0)
# The measurement: JAC for the suite's 1 ns production length (250,000 x 4 fs), STMV for 1,000 steps.
openmm("real.task.json", "r1", 32, 64, 32, 250000, 1000, ttl="3h", cap=2.0)

write("00-fetch.task.json", {
    "task_id": "cookbook-openmm-mdanalysis-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", FETCH],
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "outputs": [{"source": "/tmp/out/", "destination": IN + "amber20/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
})
