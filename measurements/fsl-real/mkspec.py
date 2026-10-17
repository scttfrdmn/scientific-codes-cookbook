#!/usr/bin/env python3
"""Write measurements/fsl-real's arm64 TaskSpecs; the x86 twins come from scripts/spec_arch.py.

    python3 mkspec.py <name> <family> <cpu> <memory_gib> <parallel> <subjects|0=all> <ttl> <cost_limit>

The scripts are staged files, pinned here by sha256 (EC2 user data is 16 KiB, spawn#1063).
"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "docker.io/mambaorg/micromamba@sha256:26c186ed2c9fdfd7aab1e7f8416b6ef54358557db8a0d9a7d11a07d1d224f1b2"
name, family, cpu, mem, par, n, ttl, cap = sys.argv[1:9]
sha = {s: hashlib.sha256(open(os.path.join(HERE, s), "rb").read()).hexdigest() for s in ("run.sh", "subject.sh")}
I = "s3://${COOKBOOK_BUCKET}/inputs/fsl-real"
O = "s3://${COOKBOOK_BUCKET}/runs/fsl-real/" + name
cmd = f"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
printf '%s  run.sh\\n%s  subject.sh\\n' {sha['run.sh']} {sha['subject.sh']} | sha256sum -c --quiet || {{ echo "FAIL: a script does not match its pin"; exit 1; }}
bash /tmp/run.sh {par} {n}
"""
spec = {
    "task_id": f"fslreal-{name}",
    "container": IMAGE,
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": int(cpu), "memory_gib": int(mem), "architecture": "arm64", "families": [family]},
    "inputs": [
        {"source": "s3://${COOKBOOK_BUCKET}/inputs/fsl/pkgs/", "destination": "/tmp/pkgs/"},
        {"source": f"{I}/pkgs/", "destination": "/tmp/xpkgs/"},
        {"source": f"{I}/fsl-real-linux-aarch64.lock", "destination": "/tmp/locks/fsl-real-linux-aarch64.lock"},
        {"source": f"{I}/fsl-real-linux-64.lock", "destination": "/tmp/locks/fsl-real-linux-64.lock"},
        {"source": f"{I}/NFBS_Dataset.tar.gz", "destination": "/tmp/NFBS_Dataset.tar.gz"},
        {"source": f"{I}/nfbs.sha256", "destination": "/tmp/nfbs.sha256"},
        {"source": f"{I}/run.sh", "destination": "/tmp/run.sh"},
        {"source": f"{I}/subject.sh", "destination": "/tmp/subject.sh"},
    ],
    "outputs": [{"source": f"/tmp/{f}", "destination": f"{O}/{f}"} for f in
                ("summary.txt", "results.tsv", "samples.tsv", "subjects.err", "install.log", "bet-masks.tar")],
    "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": float(cap)},
}
path = os.path.join(HERE, f"{name}.task.json")
json.dump(spec, open(path, "w"), indent=2)
open(path, "a").write("\n")
print(path)
