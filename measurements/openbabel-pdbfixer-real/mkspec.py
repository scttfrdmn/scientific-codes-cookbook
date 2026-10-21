#!/usr/bin/env python3
"""Write this measurement's arm64 TaskSpecs (x86 comes from scripts/spec_arch.py on these files).

    python3 -I mkspec.py <dir>
"""
import json
import os
import sys

D = sys.argv[1]
IMG = "quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d"
B = "s3://${COOKBOOK_BUCKET}"
P = B + "/inputs/openbabel-pdbfixer-real"
PRE = """set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
cd /tmp
sha256sum -c scripts.sha256 || { echo "FAIL: a script does not match its pin"; exit 1; }
"""
COMMON = [{"source": P + "/" + f, "destination": "/tmp/" + f}
          for f in ("ob_real.py", "fix_real.py", "real.sh", "scripts.sha256")]
OB_IN = [{"source": B + "/inputs/rdkit-real/chembl37/chembl_37_chemreps.txt.gz",
          "destination": "/tmp/chembl37/chembl_37_chemreps.txt.gz"},
         {"source": B + "/runs/rdkit-real/chembl37/rdkit_real.tsv.gz", "destination": "/tmp/rdkit_real.tsv.gz"},
         {"source": P + "/pins.sha256", "destination": "/tmp/pins.sha256"}]
FIX_IN = [{"source": P + "/pdb-20260101/ab/", "destination": "/tmp/pdb/"},
          {"source": P + "/cohort.sha256", "destination": "/tmp/cohort.sha256"}]
OB_CHK = 'sha256sum -c pins.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }\n'
FIX_CHK = ('sha256sum -c --status cohort.sha256 || { echo "FAIL: a staged entry does not match cohort.sha256"; exit 1; }\n'
           'echo "cohort: $(grep -c . cohort.sha256) entries match cohort.sha256"\n')
OB_RUN = "bash real.sh ob /tmp/chembl37/chembl_37_chemreps.txt.gz /tmp/rdkit_real.tsv.gz %d %s\n"
FIX_RUN = "bash real.sh fix /tmp/pdb %d %s\n"


def spec(tid, cmd, inputs, out, cpu, mem, ttl, cap, fam="c8g"):
    return {"task_id": tid, "container": IMG, "command": ["bash", "-c", cmd],
            "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [fam],
                          "disk_gib": 40},
            "inputs": inputs, "outputs": [{"source": "/tmp/out/", "destination": B + "/runs/openbabel-pdbfixer-real/" + out}],
            "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap}}


specs = {
    # sizing probe: 50,000 compounds, and 53 entries (every 5th by size plus the 5 largest)
    "probe": spec("cookbook-openbabel-pdbfixer-real-probe",
                  PRE + OB_CHK + FIX_CHK + "OUTDIR=/tmp/out/ob " + OB_RUN % (16, "50000")
                  + "OUTDIR=/tmp/out/fix " + FIX_RUN % (16, "/tmp/probe-ids.txt"),
                  COMMON + OB_IN + FIX_IN + [{"source": P + "/probe-ids.txt", "destination": "/tmp/probe-ids.txt"}],
                  "probe/", 16, 32, "2h", 1.0),
    # second probe: the 14 slowest entries of the first, with chain-terminal gaps dropped
    "probe2": spec("cookbook-openbabel-pdbfixer-real-probe2",
                   PRE + OB_CHK + FIX_CHK + "OUTDIR=/tmp/out/ob " + OB_RUN % (16, "50000")
                   + "OUTDIR=/tmp/out/fix " + FIX_RUN % (16, "/tmp/probe2-ids.txt"),
                   COMMON + OB_IN + FIX_IN + [{"source": P + "/probe2-ids.txt", "destination": "/tmp/probe2-ids.txt"}],
                   "probe2/", 16, 32, "2h", 1.2),
    # gap census: every entry stops after findMissingResidues (GAP_MAX=-1), recording its gaps
    "gaps": spec("cookbook-pdbfixer-real-gaps", PRE + FIX_CHK + "GAP_MAX=100000000 MISSING_MAX=-1 " + FIX_RUN % (16, ""),
                 COMMON + FIX_IN, "gaps/", 16, 32, "1h", 0.4),
    # the whole ChEMBL 37 release, on rdkit-real's box and worker count so the two toolkits compare
    "obabel": spec("cookbook-openbabel-real-chembl37", PRE + OB_CHK + OB_RUN % (8, ""),
                   COMMON + OB_IN, "chembl37/", 8, 16, "2h", 0.5),
    # every mmCIF in hash directory ab; over 200 internal missing residues or 5,000 missing atoms classified, 16 GiB per entry, the 9 entries over 1 MiB two at a time
    "pdbfixer": spec("cookbook-pdbfixer-real-ab", PRE + FIX_CHK + "GAP_MAX=200 MISSING_MAX=5000 MEM_GIB=16 BIG_SLOTS=2 " + FIX_RUN % (16, ""),
                     COMMON + FIX_IN, "pdb-ab/", 16, 64, "2h", 1.5, fam="m8g"),
}
for name, s in specs.items():
    with open(os.path.join(D, name + ".task.json"), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(name)
