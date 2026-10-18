#!/usr/bin/env python3
"""Write measurements/ants-real's TaskSpecs. ANTs is x86-only, so they are written for Intel (c8i)
directly, like recipes/ants; the AMD twin comes from `scripts/spec_arch.py ants amd ...`.

    python3 mkspec.py template
    python3 mkspec.py <name> <family> <cpu> <memory_gib> <parallel> <subjects|0=all> <ttl> <cost_limit>

The scripts are staged files, pinned here by sha256 (EC2 user data is 16 KiB, spawn#1063).
"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ANTS = "docker.io/antsx/ants@sha256:59c45f54a1f1dc69134f63bec91a726e41c71c64a16cc21cda0b54526910a3c3"
MICROMAMBA_X86 = "docker.io/mambaorg/micromamba@sha256:9a2239d03b9464c761ac87c13f45b37ec0f774db7d1b152a5f9add146bee2e72"
I = "s3://${COOKBOOK_BUCKET}/inputs/ants-real"
PKG = "fsl-data_standard-2208.0-0.tar.bz2"


def sha(f):
    return hashlib.sha256(open(os.path.join(HERE, f), "rb").read()).hexdigest()


def pin(*files):
    lines = "".join("%s  %s\\n" % (sha(f), f) for f in files)
    return (f"printf '{lines}' | sha256sum -c --quiet || "
            "{ echo \"FAIL: a script does not match its pin\"; exit 1; }")


def write(name, spec):
    path = os.path.join(HERE, f"{name}.task.json")
    with open(path, "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(path)


if sys.argv[1] == "template":
    write("00-template", {
        "task_id": "antsreal-template",
        "container": MICROMAMBA_X86,
        "command": ["bash", "-c", f"set +e\ncd /tmp\n{pin('template.sh')}\nbash /tmp/template.sh\n"],
        "resources": {"cpu": 2, "memory_gib": 4, "architecture": "x86_64", "families": ["c8i"]},
        "inputs": [
            {"source": f"s3://${{COOKBOOK_BUCKET}}/inputs/fsl/pkgs/linux-64/{PKG}", "destination": f"/tmp/{PKG}"},
            {"source": f"{I}/template.sh", "destination": "/tmp/template.sh"},
        ],
        "outputs": [{"source": "/tmp/mni/MNI152_T1_2mm_brain.nii.gz", "destination": f"{I}/MNI152_T1_2mm_brain.nii.gz"},
                    {"source": "/tmp/mni/MNI152_T1_2mm_brain_mask.nii.gz", "destination": f"{I}/MNI152_T1_2mm_brain_mask.nii.gz"},
                    {"source": "/tmp/mni.sha256", "destination": f"{I}/template-out.sha256"},
                    {"source": "/tmp/template.log", "destination": f"{I}/template.log"}],
        "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
    })
    sys.exit()

name, family, cpu, mem, par, n, ttl, cap = sys.argv[1:9]
O = "s3://${COOKBOOK_BUCKET}/runs-x86_64/ants-real/" + name
cmd = f"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
{pin('run.sh', 'subject.sh')}
bash /tmp/run.sh {par} {n}
"""
write(name, {
    "task_id": f"antsreal-{name}",
    "container": ANTS,
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": int(cpu), "memory_gib": int(mem), "architecture": "x86_64", "families": [family]},
    "inputs": [
        # NFBS, staged once by measurements/fsl-real and shared, not copied: the same bytes FSL registered
        {"source": "s3://${COOKBOOK_BUCKET}/inputs/fsl-real/NFBS_Dataset.tar.gz", "destination": "/tmp/NFBS_Dataset.tar.gz"},
        {"source": "s3://${COOKBOOK_BUCKET}/inputs/fsl-real/nfbs.sha256", "destination": "/tmp/nfbs.sha256"},
        {"source": f"{I}/MNI152_T1_2mm_brain.nii.gz", "destination": "/tmp/MNI152_T1_2mm_brain.nii.gz"},
        {"source": f"{I}/MNI152_T1_2mm_brain_mask.nii.gz", "destination": "/tmp/MNI152_T1_2mm_brain_mask.nii.gz"},
        {"source": f"{I}/mni.sha256", "destination": "/tmp/mni.sha256"},
        {"source": f"{I}/run.sh", "destination": "/tmp/run.sh"},
        {"source": f"{I}/subject.sh", "destination": "/tmp/subject.sh"},
    ],
    "outputs": [{"source": f"/tmp/{f}", "destination": f"{O}/{f}"} for f in
                ("summary.txt", "results.tsv", "samples.tsv", "subjects.err", "logs.tar")],
    "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": float(cap)},
})
