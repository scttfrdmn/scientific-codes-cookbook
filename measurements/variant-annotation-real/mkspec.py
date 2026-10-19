#!/usr/bin/env python3
"""Write the variant-annotation-real TaskSpecs (arm64; Intel and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-stage   fetches Ensembl GRCh37.87 (chr20-22 GFF3, CDS + peptide FASTA) on a host, into the bucket.
01-csq     bcftools: sites-only, split, numbered VCF of 1000 Genomes chr20-22, then `bcftools csq`.
02-snpeff  snpEff: build a database from the same GFF3 + reference (checked against Ensembl's
           CDS and peptides), annotate the same sites.
03-vep     VEP in offline GFF mode on the same sites, then the three-way comparison.
"""
import json
import os
import sys

out = sys.argv[1]
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/variant-annotation-real/"
RUN = B + "runs/variant-annotation-real/"
SNPEFF = "quay.io/aarchbio/snpeff@sha256:2183d4c0c4a11d58aa6c4cc5939ca26359fa07ba8e444ac4b9de297257eccf3a"
BCFTOOLS = "quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e"
VEP = "quay.io/aarchbio/ensembl-vep@sha256:48593f3f9f40cd15c77adcf68f83fb7ee00d7fa35849d449cece48a504c76e01"
VCF = "ALL.chr%s.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"
GFF = "Homo_sapiens.GRCh37.87.chromosome.%s.gff3.gz"
CDS = "Homo_sapiens.GRCh37.cds.all.fa.gz"
PEP = "Homo_sapiens.GRCh37.pep.all.fa.gz"
REF = "hs37d5.chr20-22.fa"
CHRS = ("20", "21", "22")

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


stage = {
    "task_id": "cookbook-varann-real-stage",
    "command": ["bash", "-c", "set +e\nbash /tmp/fetch-ensembl.sh\n"],
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [io(IN + "fetch-ensembl.sh", "fetch-ensembl.sh")],
    "outputs": [{"source": "/tmp/out/", "destination": IN}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
}


def task(tid, image, script, files, ins, prefix, cpu, mem, disk, ttl, cap, args=""):
    return {
        "task_id": tid,
        "container": image,
        "command": ["bash", "-c", pinned(files) + "bash %s %s\n" % (script, args)],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": ["c8g"], "disk_gib": disk},
        "inputs": ins + [io(IN + "pins.sha256", "pins.sha256")],
        "outputs": [{"source": "/tmp/out/", "destination": RUN + prefix + "/"}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


common = ["lib.sh", "pairs.awk"]
gffs = [GFF % c for c in CHRS]
vcfs = [VCF % c for c in CHRS]

csq_files = ["csq.sh", REF] + common + gffs + vcfs
csq_in = [io(IN + f, f) for f in ["csq.sh"] + common + gffs] \
    + [io(B + "inputs/bcftools-real/" + REF, REF)] \
    + [io(B + "inputs/plink-real/" + v, v) for v in vcfs]

snp_files = ["snpeff.sh", REF, CDS, PEP] + common + gffs
snp_in = [io(IN + f, f) for f in ["snpeff.sh", CDS, PEP] + common + gffs] \
    + [io(B + "inputs/bcftools-real/" + REF, REF), io(RUN + "csq/sites.vcf.gz", "sites.vcf.gz")]

vep_files = ["vep.sh", "compare.awk", REF, PEP] + common + gffs
vep_in = [io(IN + f, f) for f in ["vep.sh", "compare.awk", PEP] + common + gffs] \
    + [io(B + "inputs/bcftools-real/" + REF, REF), io(RUN + "csq/sites.vcf.gz", "sites.vcf.gz"),
       io(RUN + "csq/csq.pairs.tsv", "csq.pairs.tsv"), io(RUN + "snpeff/snpeff.pairs.tsv", "snpeff.pairs.tsv")]

specs = {
    "00-stage": stage,
    "01-csq": task("cookbook-varann-real-csq", BCFTOOLS, "csq.sh", csq_files, csq_in, "csq", 4, 8, 40, "2h", 0.4),
    "02-snpeff": task("cookbook-varann-real-snpeff", SNPEFF, "snpeff.sh", snp_files, snp_in, "snpeff", 4, 8, 40, "2h", 0.4),
    "03-vep": task("cookbook-varann-real-vep", VEP, "vep.sh", vep_files, vep_in, "vep", 16, 32, 60, "3h", 1.5, "16"),
}
for name, s in specs.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
