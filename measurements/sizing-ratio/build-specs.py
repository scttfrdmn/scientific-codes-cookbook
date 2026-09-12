#!/usr/bin/env python3
"""Generate the four sizing-ratio .task.json specs from the readable *.sh scripts.

The script is the single source of truth; the .task.json is derived (this avoids
hand-escaping bash into JSON, a real correctness risk). Re-run after editing any
*.sh or _instrument.sh. Nothing here launches or stages -- it only writes JSON.
"""
import json, pathlib

HERE = pathlib.Path(__file__).parent
INSTR = (HERE / "_instrument.sh").read_text()
S3 = "s3://${COOKBOOK_BUCKET}"
IN, OUT = f"{S3}/inputs/sizing", f"{S3}/runs/sizing"

def outs(tool, extra):
    base = [("measure.txt", f"{OUT}/{tool}/measure.txt")] + extra
    return [{"source": f"/tmp/{s}", "destination": d} for s, d in base]

# rate/inst are the us-east-1 on-demand figures from `spawn --dry-run`; re-dry-run per
# region before launch. They are injected into each script so the box reports its own $/result.
RUNS = {
    "spades": dict(
        image="quay.io/aarchbio/spades@sha256:f8b7ad9acda742d695be9176c1fec0e9a33579a6a19294d3d2a3516ade3de81c",
        cpu=8, mem=32, fam="m8g", ttl="60m", cost=0.36, inst="m8g.2xlarge", rate=0.3590,
        inputs=[("ecoli_R1.fq.gz", "ecoli_R1.fq.gz"), ("ecoli_R2.fq.gz", "ecoli_R2.fq.gz")],
        out=[("samples.tsv", f"{OUT}/spades/samples.tsv")]),
    "megahit": dict(
        image="quay.io/aarchbio/megahit@sha256:d82953bf0096098b0b892edf7180f99b599e8ad17be14c47b1e8c2e1b6a8bdfd",
        cpu=8, mem=16, fam="c8g", ttl="45m", cost=0.24, inst="c8g.2xlarge", rate=0.3190,
        inputs=[("ecoli_R1.fq.gz", "ecoli_R1.fq.gz"), ("ecoli_R2.fq.gz", "ecoli_R2.fq.gz")],
        out=[("samples.tsv", f"{OUT}/megahit/samples.tsv")]),
    "star": dict(
        image="quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae",
        cpu=16, mem=128, fam="r8g", ttl="90m", cost=1.45, inst="r8g.4xlarge", rate=0.9426,
        inputs=[("GRCh38.primary_assembly.fa.gz", "GRCh38.fa.gz"),
                ("GRCh38.116.gtf.gz", "GRCh38.gtf.gz"),
                ("ERR188026_R1.fq.gz", "r1.fq.gz"), ("ERR188026_R2.fq.gz", "r2.fq.gz")],
        out=[("samples-build.tsv", f"{OUT}/star/samples-build.tsv"),
             ("samples-align.tsv", f"{OUT}/star/samples-align.tsv")]),
    "picard": dict(
        image="quay.io/aarchbio/picard@sha256:c6a742e8277b9010df9aa3b9a6bb40651792ff319627cc1c2bf8a70ac633e6bd",
        cpu=8, mem=32, fam="m8g", ttl="30m", cost=0.18, inst="m8g.2xlarge", rate=0.3590,
        inputs=[("HG00096.chr1_1-100Mb.30x.bam", "chr1.bam"),
                ("HG00096.chr1_1-100Mb.30x.bam.bai", "chr1.bam.bai")],
        out=[("samples.tsv", f"{OUT}/picard/samples.tsv"), ("gc.log", f"{OUT}/picard/gc.log")]),
}

for tool, r in RUNS.items():
    body = (HERE / f"{tool}.sh").read_text().replace("__INSTRUMENT__", INSTR.rstrip())
    # inject the box's own rate + instance so measure.txt reports $/result on the box itself
    script = f"RATE={r['rate']}\nINSTANCE={r['inst']}\n" + body
    spec = {
        "task_id": f"sizing-ratio-{tool}-r1",
        "command": ["bash", "-c", script],
        "container": r["image"],
        "resources": {"cpu": r["cpu"], "memory_gib": r["mem"],
                      "architecture": "arm64", "families": [r["fam"]]},
        "inputs": [{"source": f"{IN}/{s}", "destination": f"/tmp/{d}"} for s, d in r["inputs"]],
        "outputs": outs(tool, r["out"]),
        "lifecycle": {"ttl": r["ttl"], "on_complete": "terminate", "cost_limit": r["cost"]},
    }
    (HERE / f"{tool}.task.json").write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {tool}.task.json  ({r['cpu']}vcpu/{r['mem']}GiB {r['fam']} ttl={r['ttl']} cap=${r['cost']})")
print(f"total worst-case cap: ${sum(r['cost'] for r in RUNS.values()):.2f}")
