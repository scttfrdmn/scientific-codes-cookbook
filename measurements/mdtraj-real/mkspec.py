"""Write this directory's specs, with the current sha256 of the staged scripts pinned into them.
   python3 measurements/mdtraj-real/mkspec.py
The x86 specs come from these: scripts/spec_arch.py mdtraj {x86_64,amd} <bucket> <nonce> <spec>.
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f"   # recipes/mdtraj
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/mdtraj-real/"
DATA = [
    {"source": IN + "fah-nsp5-fast/FAST_simulations/", "destination": "/tmp/fah/FAST_simulations/"},
    {"source": IN + "fah-nsp5-fast/input_files/", "destination": "/tmp/fah/input_files/"},
    {"source": IN + "jac/", "destination": "/tmp/jacin/"},
    # openmm-mdanalysis-real's pinned suite, for JAC.prmtop (same object, not a copy)
    {"source": S3 + "inputs/openmm-mdanalysis-real/amber20/Amber20_Benchmark_Suite.tar.gz",
     "destination": "/tmp/Amber20_Benchmark_Suite.tar.gz"},
]


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


def pinned(scripts, run):
    """A command that refuses to run unless every staged script matches the sha256 it had here."""
    pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
                   for f in scripts)
    return ("set +e\n# spawn runs task commands under bash -e (spawn#707); run.sh sets its own options.\n"
            "set -uo pipefail\ncd /tmp\n"
            "printf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n") % (pins, run)


def task(name, run, cpu, mem, workers, nfiles, pins, ttl, cap):
    scripts = ["run.sh", "real.py", "check.py"] + (["inputs.sha256"] if pins else [])
    write(name, {
        "task_id": "cookbook-mdtraj-real-%s" % run,
        "container": IMAGE,
        "command": ["bash", "-c", pinned(scripts, "bash /tmp/run.sh %d %s" % (workers, nfiles or ""))],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": ["c8g"]},
        "inputs": DATA + [{"source": IN + f, "destination": "/tmp/" + f} for f in scripts],
        "outputs": [{"source": "/tmp/out/", "destination": S3 + "runs/mdtraj-real/%s/" % run}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    })


# The sizing probe: records the input pins on AWS, and runs the whole analysis on 4 of the 60 files.
task("probe.task.json", "probe", 4, 16, 4, 4, False, "60m", 0.30)
if os.path.exists(os.path.join(D, "inputs.sha256")):
    # The measurement: all 60 files on a 32-vCPU 8xlarge, 30 worker processes -- each file is ~530 s of
    # one core on Graviton4 (the probe), so 30 workers take the 60 in exactly two rounds.
    task("real.task.json", "r1", 32, 64, 30, 0, True, "3h", 1.50)
