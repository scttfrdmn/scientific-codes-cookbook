#!/usr/bin/env python3
"""Write the imputation-real TaskSpecs (arm64; Intel and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-sites   (once) hash the 1000 Genomes Omni2.5 chip VCF on AWS and keep its chr20 site list.
01-prep    minimac4 image: panel / held-out cohort split of Phase 3 chr20, array mask, .msav panel.
02-beagle  beagle image: phase and impute the cohort.
03-mm4     minimac4 image: impute the same cohort from Beagle's phase, then score both tools.
04-diff    (arm64 only, after all three chips) where Minimac4's VCFs differ across chips, field by field.
"""
import json
import os
import sys

out = sys.argv[1]
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/imputation-real/"
RUN = B + "runs/imputation-real/"
MM4 = "quay.io/aarchbio/minimac4@sha256:cea08321a8ab3632633b2ba0f2b68c98d7e6fcfb1c9c624f9b790a61a0e4b3c2"
BEAGLE = "quay.io/aarchbio/beagle@sha256:7a22203b34a80d9b832ea84c3c46d9fdf83f4f679b6e53fcf1baebeeba06ce63"
VCF = "ALL.chr20.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"
OMNI = "ALL.chip.omni_broad_sanger_combined.20140818.snps.genotypes.vcf.gz"
SITES = "omni.chr20.sites.tsv"

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -ge %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


def task(tid, image, script, files, ins, out_src, out_dst, ttl, cap, args=""):
    return {
        "task_id": tid,
        "container": image,
        "command": ["bash", "-c", pinned(files) + "bash %s %s\n" % (script, args)],
        "resources": {"cpu": 16, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
        "inputs": ins + [io(IN + "pins.sha256", "pins.sha256")],
        "outputs": [{"source": out_src, "destination": out_dst}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


sites = task("cookbook-imputation-real-sites", MM4, "sites.sh", ["sites.sh", "lib.sh"],
             [io(IN + f, f) for f in ("sites.sh", "lib.sh", OMNI, OMNI + ".tbi")],
             "/tmp/out/sites/", IN, "30m", 0.1)
sites["resources"] = {"cpu": 4, "memory_gib": 8, "architecture": "arm64", "families": ["c8g"]}

prep_files = ["prep.sh", "lib.sh", VCF, SITES]
prep = task("cookbook-imputation-real-prep", MM4, "prep.sh", prep_files,
            [io(IN + f, f) for f in ("prep.sh", "lib.sh", SITES)] + [io(B + "inputs/plink-real/" + VCF, VCF)],
            "/tmp/out/", RUN + "prep/", "2h", 1.0)

beagle = task("cookbook-imputation-real-beagle", BEAGLE, "beagle.sh", ["beagle.sh", "lib.sh"],
              [io(IN + f, f) for f in ("beagle.sh", "lib.sh")]
              + [io(RUN + "prep/" + f, f) for f in ("ref.vcf.gz", "target.vcf.gz")],
              "/tmp/out/", RUN + "beagle/", "3h", 1.5, "24g")

mm4 = task("cookbook-imputation-real-mm4", MM4, "mm4.sh", ["mm4.sh", "score.py", "lib.sh"],
           [io(IN + f, f) for f in ("mm4.sh", "score.py", "lib.sh")]
           + [io(RUN + "prep/" + f, f) for f in ("ref.msav", "truth.vcf.gz", "ref.sites.tsv", "typed.pos")]
           + [io(RUN + "beagle/beagle.vcf.gz", "beagle.vcf.gz")],
           "/tmp/out/", RUN + "mm4/", "3h", 1.5)

DIFF = r"""set -e
export PATH=/opt/conda/bin:$PATH
mkdir -p /tmp/out
python3 -u -I vcfdiff.py arm64.vcf.gz x86_64.vcf.gz arm64-vs-intel > /tmp/out/intel.tsv &
python3 -u -I vcfdiff.py arm64.vcf.gz amd.vcf.gz arm64-vs-amd > /tmp/out/amd.tsv &
wait
cat /tmp/out/intel.tsv /tmp/out/amd.tsv
"""
diff = task("cookbook-imputation-real-diff", MM4, "", ["vcfdiff.py"],
            [io(IN + "vcfdiff.py", "vcfdiff.py")]
            + [io(B + p + "/imputation-real/mm4/mm4.vcf.gz", a + ".vcf.gz")
               for p, a in (("runs", "arm64"), ("runs-x86_64", "x86_64"), ("runs-amd", "amd"))],
            "/tmp/out/", RUN + "diff/", "1h", 0.2)
diff["command"][2] = pinned(["vcfdiff.py"]) + DIFF
diff["resources"] = {"cpu": 4, "memory_gib": 8, "architecture": "arm64", "families": ["c8g"]}

for name, s in {"00-sites": sites, "01-prep": prep, "02-beagle": beagle, "03-mm4": mm4, "04-diff": diff}.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
