"""Write real.task.json with the current sha256 of the staged scripts pinned into its command.
   python3 measurements/ase-phonopy-real/mkspec.py <run label>"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e"
RANKS = 16
RUN = sys.argv[1]
SCRIPTS = ["run.sh", "forces.py", "analyse.py", "inputs.sha256"]
DATA = ["Si-QE_phonopy_disp.yaml", "Si-QE_supercell-001.out", "Si_phonopy_disp.yaml", "Si_vasprun.xml"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in SCRIPTS)
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '== the staged scripts must match their pins =='\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %d\n") % (pins, RANKS)
S3 = "s3://${COOKBOOK_BUCKET}/"
OUT = S3 + "runs/ase-phonopy-real/%s/" % RUN
spec = {
    "task_id": "cookbook-ase-phonopy-real-%s" % RUN,
    "command": ["bash", "-c", cmd],
    "container": IMAGE,
    "resources": {"cpu": RANKS, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [{"source": S3 + "inputs/ase-phonopy-real/" + f, "destination": "/tmp/" + f} for f in DATA + SCRIPTS],
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f}
                for f in ["check.txt", "results.tsv", "freqs.tsv", "host.txt", "footprint.txt",
                          "forces-vasp16.out", "forces-qe64.out", "gpaw-vasp16.txt", "gpaw-qe64.txt"]]
    + [{"source": "/tmp/ase-phonopy-real-out.tar", "destination": OUT + "ase-phonopy-real-out.tar"}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 1.5},
}
with open(os.path.join(D, "real.task.json"), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, "real.task.json"))
