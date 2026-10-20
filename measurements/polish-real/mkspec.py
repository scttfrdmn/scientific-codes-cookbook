#!/usr/bin/env python3
"""Write the polish-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-prep, 00-fetch, 01-draft and 02-overlap run once, on Graviton4, and write under inputs/polish-real/, so
every chip polishes the same draft from the same reads and overlaps. 03-racon and 04-medaka are the
workload, run on every chip; 04-medaka also scores everything against the truth.
"""
import json
import os
import sys

out = sys.argv[1]
THREADS = 8
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/polish-real/"
RUN = B + "runs/polish-real/"
FLYE = "quay.io/aarchbio/flye@sha256:d87ccd4e29f2995e6bbcea9f72e90f575897a5489b472111695320bd8528dc12"
MINIMAP2 = "quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a"
RACON = "quay.io/aarchbio/racon@sha256:75020311bdb6a635ee67718984db28029e95ee2272e32de9d0def8a9826ed4ee"
SRA = "quay.io/aarchbio/sra-tools@sha256:7145cf2f6f2e9265b7ace204d86b6ed1cbbd0b8b3ef31cbadf32bfa271229afd"
MEDAKA = "quay.io/aarchbio/medaka@sha256:9389bbfdcd569497790eae188c825adf70f33236c284f16dbeeb016128a1015e"
READS = "ERR12813217.fastq.gz"
MODEL = "r1041_e82_400bps_sup_v4.3.0_model_pt.tar.gz"

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=/opt/conda/bin:$PATH TMPDIR=/var/tmp HOME=/var/tmp
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -eq %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


def spec(task, image, names, inputs, body, outputs, cpu, mem, family, disk, ttl, cap):
    return {
        "task_id": "cookbook-polish-real-" + task,
        "container": image,
        "command": ["bash", "-c", pinned(names) + body],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family], "disk_gib": disk},
        "inputs": inputs + [io(IN + "pins.sha256", "pins.sha256")],
        "outputs": outputs,
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


SCRIPTS = ["common.sh", "run.py"]
specs = {
    "00-prep": spec("prep", SRA, ["prep.sh"], [io(IN + "prep.sh", "prep.sh"), io(IN + "ERR12813217.sra", "ERR12813217.sra")],
                    "bash prep.sh\n", [{"source": "/var/tmp/out/", "destination": IN + "reads/"}],
                    2, 8, "m8g", 40, "2h", 0.30),
    "00-fetch": spec("fetch", MEDAKA, ["fetch.sh"], [io(IN + "fetch.sh", "fetch.sh")], "bash fetch.sh\n",
                     [{"source": "/var/tmp/out/", "destination": IN + "fetched/"}],
                     2, 8, "m8g", 30, "2h", 0.30),
    "01-draft": spec("draft", FLYE, SCRIPTS + ["draft.sh", READS],
                     [io(IN + f, f) for f in SCRIPTS + ["draft.sh"]] + [io(IN + "reads/" + READS, READS)],
                     "bash draft.sh %d\n" % THREADS,
                     [{"source": "/var/tmp/out/", "destination": IN + "draft/"}],
                     THREADS, 32, "m8g", 100, "6h", 2.00),
    "02-overlap": spec("overlap", MINIMAP2, SCRIPTS + ["overlap.sh", READS, "draft.fa"],
                       [io(IN + f, f) for f in SCRIPTS + ["overlap.sh"]]
                       + [io(IN + "reads/" + READS, READS), io(IN + "draft/draft.fa", "draft.fa")],
                       "bash overlap.sh %d\n" % THREADS,
                       [{"source": "/var/tmp/out/", "destination": IN + "overlap/"}],
                       THREADS, 32, "m8g", 40, "2h", 0.80),
    "03-racon": spec("racon", RACON, SCRIPTS + ["racon.sh", READS, "draft.fa", "ov.paf"],
                     [io(IN + f, f) for f in SCRIPTS + ["racon.sh"]]
                     + [io(IN + "reads/" + READS, READS), io(IN + "draft/draft.fa", "draft.fa"),
                        io(IN + "overlap/ov.paf", "ov.paf")],
                     "bash racon.sh %d\n" % THREADS,
                     [{"source": "/var/tmp/out/", "destination": RUN}],
                     THREADS, 32, "m8g", 40, "6h", 2.00),
    "04-medaka": spec("medaka", MEDAKA,
                      SCRIPTS + ["medaka.sh", "score.py", READS, "draft.fa", "truth.fa", "paper-flye-medaka-100x.fa", MODEL],
                      [io(IN + f, f) for f in SCRIPTS + ["medaka.sh", "score.py"]]
                      + [io(IN + "fetched/" + f, f) for f in ("truth.fa", "paper-flye-medaka-100x.fa", MODEL)]
                      + [io(IN + "reads/" + READS, READS)]
                      + [io(IN + "draft/draft.fa", "draft.fa"), io(RUN + "racon/racon.fa", "racon.fa")],
                      "bash medaka.sh %d\n" % THREADS,
                      [{"source": "/var/tmp/out/", "destination": RUN}],
                      THREADS, 32, "m8g", 60, "6h", 2.00),
}
for name, s in specs.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
