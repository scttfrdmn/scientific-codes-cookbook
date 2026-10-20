"""Write this directory's task specs with the sha256 of each staged script pinned into the command.
    python3 measurements/gw-real/mkspec.py <run label> [family] [vcpus] [nlive] [ttl] [cost_limit]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/gw@sha256:18e0856fcb6cdde0725acbed3f4efcaa34a7654eaf42f372ddac55ecd2f052d5"
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
FAMILY = sys.argv[2] if len(sys.argv) > 2 else "c8g"
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 16
NLIVE = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
TTL = sys.argv[5] if len(sys.argv) > 5 else "6h"
COST = float(sys.argv[6]) if len(sys.argv) > 6 else 5.0
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * NP
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/gw-real/"
HEAD = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\ncd /tmp\nexport PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl\n")


def pins(files):
    return "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
                   for f in files)


def check(files):
    return ("printf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            % pins(files))


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


F = ["fetch.py", "inspect_release.py"]
write("00-fetch.task.json", {
    "task_id": "cookbook-gw-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", HEAD + check(F) +
                "mkdir -p /tmp/out /tmp/log\n"
                "python3 -u fetch.py /tmp/out 2>&1 | tee /tmp/log/fetch.log\n"
                "grep -q 'FETCH OK' /tmp/log/fetch.log || { echo 'FAIL: fetch'; exit 1; }\n"
                "python3 -u inspect_release.py /tmp/out/IGWN-GWTC2p1-v2-GW150914_095045_PEDataRelease_mixed_nocosmo.h5"
                " > /tmp/log/inspect.txt 2>&1\n"
                "tail -3 /tmp/log/inspect.txt\nls -l /tmp/out\necho DONE\n"],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": IN + f, "destination": "/tmp/" + f} for f in F],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "data/"},
                {"source": "/tmp/log/", "destination": S3 + "runs/gw-real/fetch/"}],
    "lifecycle": {"ttl": "40m", "on_complete": "terminate", "cost_limit": 0.10},
})

DATA = ["H-H1_LOSC_16_V1-1126256640-4096.hdf5", "L-L1_LOSC_16_V1-1126256640-4096.hdf5",
        "IGWN-GWTC2p1-v2-GW150914_095045_PEDataRelease_mixed_nocosmo.h5"]


def data_check(files):
    return ("grep -E '  (%s)$' inputs.sha256 | sed 's|  |  data/|' > data.sha256\n"
            "test \"$(wc -l < data.sha256)\" = %d || { echo 'FAIL: pins missing'; exit 1; }\n"
            "sha256sum -c data.sha256 || { echo 'FAIL: staged data does not match inputs.sha256'; exit 1; }\n"
            % ("|".join(f.replace(".", "\\.") for f in files), len(files)))


DATA4 = ["H-H1_LOSC_4_V1-1126256640-4096.hdf5", "L-L1_LOSC_4_V1-1126256640-4096.hdf5"]
R = ["run.sh", "gwreal.py", "search.py", "pe.py", "inputs.sha256"]
OUT = S3 + "runs/gw-real/%s/" % RUN
write("real.task.json", {
    "task_id": "cookbook-gw-real-%s" % RUN,
    "container": IMAGE,
    "command": ["bash", "-c", HEAD + check(R) + data_check(DATA + DATA4) +
                "bash /tmp/run.sh %d %d\n" % (NP, NLIVE)],
    "resources": {"cpu": NP, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY]},
    "inputs": [{"source": IN + "data/" + f, "destination": "/tmp/data/" + f} for f in DATA + DATA4]
    + [{"source": IN + f, "destination": "/tmp/" + f} for f in R],
    "outputs": [{"source": "/tmp/out/", "destination": OUT}],
    "lifecycle": {"ttl": TTL, "on_complete": "terminate", "cost_limit": COST},
})
