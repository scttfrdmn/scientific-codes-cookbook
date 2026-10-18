#!/usr/bin/env python3
"""Write pins.sha256 (data + scripts) and study.task.json, whose command checks the pins file's own hash.

    python3 mkspec.py        # after any edit to dada2-real.R or run.sh; then re-stage
"""
import hashlib, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = {"StabilityNoMetaG.tar": "bbbe46cef5f2bed3030aca68d1557b35240c503427d6a28a582a2a121e5777ba",
        "miseqsopdata.zip": "728f7544824f8ca0223618b18bb60fce6a5dc20ae3d476267dbd5cf5594a35d4"}
SCRIPTS = ["dada2-real.R", "run.sh"]
THREADS = 16
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
lines = [f"{h}  {f}" for f, h in DATA.items()] + [f"{sha(os.path.join(HERE, f))}  {f}" for f in SCRIPTS]
pins = "\n".join(lines) + "\n"
open(os.path.join(HERE, "pins.sha256"), "w").write(pins)
ph = hashlib.sha256(pins.encode()).hexdigest()
cmd = f"""set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
echo "{ph}  pins.sha256" | sha256sum -c || {{ echo "FAIL: pins.sha256 does not match its pin"; exit 1; }}
sha256sum -c pins.sha256 || {{ echo "FAIL: a staged input does not match its pin"; exit 1; }}
bash run.sh {THREADS}
"""
inp = lambda f: {"source": f"s3://${{COOKBOOK_BUCKET}}/inputs/dada2-real/{f}", "destination": f"/tmp/{f}"}
spec = {
    "task_id": "cookbook-dada2-real",
    "container": "quay.io/aarchbio/bioconductor-dada2@sha256:1dbe837ab143941ae1864da332632f2fca92b1c191a1c8f8644c6a2267148478",
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": THREADS, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
    "inputs": [inp(f) for f in list(DATA) + SCRIPTS + ["pins.sha256"]],
    "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/dada2-real/study/"}],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.5},
}
open(os.path.join(HERE, "study.task.json"), "w").write(json.dumps(spec, indent=2) + "\n")
print("pins.sha256", ph)
