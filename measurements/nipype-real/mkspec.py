#!/usr/bin/env python3
"""Write measurements/nipype-real's arm64 TaskSpec; the Intel and AMD twins come from
scripts/spec_arch.py (`nipype` -- the recipe's env_x86_64 twin), which also moves the reference maps
read from runs/ to the same chip's runs-x86_64/ or runs-amd/.

    python3 mkspec.py <name> <family> <cpu> <memory_gib> <n_procs> <subjects|0=all> <ttl> <cost_limit>
    python3 mkspec.py xchip      # reads all three chips' reference maps, so written directly, never via spec_arch.py

The scripts are staged files, pinned here by sha256 (EC2 user data is 16 KiB, spawn#1063).
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
B = "s3://${COOKBOOK_BUCKET}"
I = B + "/inputs/nipype-real"
DWI = B + "/inputs/neuroimaging-real"          # measurements/neuroimaging-real's staged, pinned cohort
SCRIPTS = ("run.sh", "pipeline.py", "check.py", "hashprobe.py")

image = re.search(r"^image:\s*(\S+)", open(os.path.join(ROOT, "recipes/nipype/README.md")).read(), re.M).group(1)
if sys.argv[1] == "xchip":
    sha = hashlib.sha256(open(os.path.join(HERE, "xchip.py"), "rb").read()).hexdigest()
    spec = {
        "task_id": "nipypereal-xchip", "container": image,
        "command": ["bash", "-c",
                    "set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\ncd /tmp\n"
                    "printf '%s  /tmp/xchip.py\\n' | sha256sum -c --quiet || { echo \"FAIL: a script does not match its pin\"; exit 1; }\n"
                    "export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1\npython3 -u /tmp/xchip.py > /tmp/xchip.txt 2>&1\nRC=$?\ncat /tmp/xchip.txt\nexit $RC\n" % sha],
        "resources": {"cpu": 4, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"]},
        "inputs": [{"source": I + "/xchip.py", "destination": "/tmp/xchip.py"}]
                  + [{"source": "%s/%s/neuroimaging-real/cohort/dipy/dipy-maps.tar" % (B, pre), "destination": "/tmp/%s.tar" % a}
                     for a, pre in (("arm64", "runs"), ("x86_64", "runs-x86_64"), ("amd", "runs-amd"))],
        "outputs": [{"source": "/tmp/xchip.txt", "destination": B + "/runs/nipype-real/xchip/xchip.txt"}],
        "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.10}}
    p = os.path.join(HERE, "xchip.task.json")
    with open(p, "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(p)
    sys.exit(0)
name, fam, cpu, mem, nprocs, n, ttl, cap = sys.argv[1:9]
pins = "\\n".join("%s  /tmp/%s" % (hashlib.sha256(open(os.path.join(HERE, s), "rb").read()).hexdigest(), s) for s in SCRIPTS)
O = B + "/runs/nipype-real/" + name
spec = {
    "task_id": "nipypereal-" + name,
    "container": image,
    "command": ["bash", "-c",
                "set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\ncd /tmp\n"
                "printf '%s\\n' | sha256sum -c --quiet || { echo \"FAIL: a script does not match its pin\"; exit 1; }\n"
                "bash /tmp/run.sh %s %s\n" % (pins, nprocs, n)],
    "resources": {"cpu": int(cpu), "memory_gib": int(mem), "disk_gib": 60, "architecture": "arm64", "families": [fam]},
    "inputs": [{"source": DWI + "/dwi/", "destination": "/tmp/dwi/"},
               {"source": DWI + "/dwi.sha256", "destination": "/tmp/dwi.sha256"},
               {"source": DWI + "/subjects.txt", "destination": "/tmp/subjects.txt"},
               {"source": B + "/runs/neuroimaging-real/cohort/dipy/dipy-maps.tar", "destination": "/tmp/ref-maps.tar"}]
              + [{"source": "%s/%s" % (I, s), "destination": "/tmp/" + s} for s in SCRIPTS],
    "outputs": [{"source": "/tmp/" + f, "destination": "%s/%s" % (O, f)}
                for f in ("summary.txt", "checks.txt", "phases.txt", "host.txt", "cohort.jsonl", "nipype-logs.tar.gz")],
    "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": float(cap)},
}
p = os.path.join(HERE, name + ".task.json")
with open(p, "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(p)
