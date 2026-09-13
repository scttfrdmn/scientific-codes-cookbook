#!/usr/bin/env python3
"""Cross-family specs: one code, one fixture, one command, five families.
Usage: build-xfam.py <megahit|spades>

arm64 families (c8g/c9g/m9g) run the recipe's aarchbio image; x86 families (c8i/c8a)
run the biocontainers same-version amd64 build, pinned by digest (no emulation risk).
Both are bioconda <tool> at the recipe's version -- different arch builds, inherent to
a cross-arch comparison (noted on the page). Same fixture (staged ecoli reads) across
every run; if the input differs the comparison is void. Reuses <tool>.sh + _instrument.sh.
"""
import json, pathlib, sys

TOOL = sys.argv[1] if len(sys.argv) > 1 else "megahit"
HERE = pathlib.Path(__file__).parent
INSTR = (HERE / "_instrument.sh").read_text()
BODY = (HERE / f"{TOOL}.sh").read_text()
S3 = "s3://${COOKBOOK_BUCKET}"

IMAGES = {
    "megahit": ("quay.io/aarchbio/megahit@sha256:d82953bf0096098b0b892edf7180f99b599e8ad17be14c47b1e8c2e1b6a8bdfd",
                "quay.io/biocontainers/megahit@sha256:a1e51b68962e54eb5b0576db7dc366d05f81ee1aa5d56a983133a8675e905c75"),
    "spades":  ("quay.io/aarchbio/spades@sha256:f8b7ad9acda742d695be9176c1fec0e9a33579a6a19294d3d2a3516ade3de81c",
                "quay.io/biocontainers/spades@sha256:db9b8323afbae620801fcd79798af88afa1925f42ef54195ae3622ca0cf7dbcf"),
}
ARM_IMG, X86_IMG = IMAGES[TOOL]
# spades runs ~4.5 min (vs megahit ~55 s) -> looser TTL/caps
TTL = "15m" if TOOL == "spades" else "12m"
# family: (arch, rate $/hr, instance, mem_gib)   cap = ceil(TTL_hours * rate) + a cent
FAM = {
    "c8g": ("arm64",  0.3190, "c8g.2xlarge", 16),
    "c9g": ("arm64",  0.3478, "c9g.2xlarge", 16),
    "m9g": ("arm64",  0.3914, "m9g.2xlarge", 32),
    "c8i": ("x86_64", 0.3748, "c8i.2xlarge", 16),
    "c8a": ("x86_64", 0.4311, "c8a.2xlarge", 16),
}
ttl_h = int(TTL.rstrip("m")) / 60.0

for fam, (arch, rate, inst, mem) in FAM.items():
    image = X86_IMG if arch == "x86_64" else ARM_IMG
    cap = round(ttl_h * rate + 0.01, 2)          # cost_limit just above TTL*rate so TTL binds
    script = f"RATE={rate}\nINSTANCE={inst}\n" + BODY.replace("__INSTRUMENT__", INSTR.rstrip())
    spec = {
        "task_id": f"sizing-xfam-{TOOL}-{fam}-r1",
        "command": ["bash", "-c", script],
        "container": image,
        "resources": {"cpu": 8, "memory_gib": mem, "architecture": arch, "families": [fam]},
        "inputs": [
            {"source": f"{S3}/inputs/sizing/ecoli_R1.fq.gz", "destination": "/tmp/ecoli_R1.fq.gz"},
            {"source": f"{S3}/inputs/sizing/ecoli_R2.fq.gz", "destination": "/tmp/ecoli_R2.fq.gz"},
        ],
        "outputs": [
            {"source": "/tmp/measure.txt", "destination": f"{S3}/runs/xfam/{TOOL}/{fam}/measure.txt"},
            {"source": "/tmp/samples.tsv", "destination": f"{S3}/runs/xfam/{TOOL}/{fam}/samples.tsv"},
        ],
        "lifecycle": {"ttl": TTL, "on_complete": "terminate", "cost_limit": cap},
    }
    (HERE / f"xfam-{TOOL}-{fam}.task.json").write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote xfam-{TOOL}-{fam}.task.json  ({arch} {inst} ${rate}/hr ttl={TTL} cap=${cap})")
