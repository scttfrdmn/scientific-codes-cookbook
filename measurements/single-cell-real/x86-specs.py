#!/usr/bin/env python3
"""Resolve this measurement's two specs for x86 (Intel or AMD), on bioconda's own linux-64 builds.

    python3 measurements/single-cell-real/x86-specs.py <x86_64|amd> <bucket> <nonce> [--cost-limit USD]

quay.io/biocontainers has no alevin-fry 0.18.3 image (newest 0.18.2 on 2026-10-24), so
scripts/spec_arch.py has no x86 pin to swap in. Each aarch.bio image is instead replaced by the
micromamba image that spec_arch.py's env route uses, installing a committed lock OFFLINE first:
envs-x86_64/bioconda-<tool>-<version>.linux-64.lock, the arm64 image's own package set (read from its
conda-meta) at the same versions and build numbers, staged by scripts/stage_env_x86.sh. Everything
else -- family, architecture, the runs-<arch>/ prefixes, the preamble -- is spec_arch.py's, imported.
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import spec_arch as sa  # noqa: E402

LOCK = {"quay.io/aarchbio/piscem@sha256:40c8a261dc5d152a1aa9c5db58aa14287a56580f42a483cc1ffec9c5b6e21603": "bioconda-piscem-0.23.0",
        "quay.io/aarchbio/alevin-fry@sha256:1c5b8393d66e206e44cdaa928b63ffed2b798a323a5c04b3bed1349a216aeb6d": "bioconda-alevin-fry-0.18.3"}
# threads per core and the CPU, into command.log (Intel 8i vCPUs are SMT threads, AMD 8a full cores)
CPU = "lscpu 2>/dev/null | grep -E '^(Model name|Thread\\(s\\) per core|Core\\(s\\) per socket|Socket\\(s\\)):' || true\n"


def main():
    a = sys.argv[1:]
    cap = None
    if "--cost-limit" in a:
        i = a.index("--cost-limit"); cap = float(a[i + 1]); del a[i:i + 2]
    if len(a) != 3 or a[0] not in sa.ARCH:
        sys.exit(__doc__)
    arch, bucket, nonce = a
    sha = hashlib.sha256(open(sa.INSTALLER, "rb").read()).hexdigest()
    for f in ("01-map.task.json", "02-quant.task.json"):
        spec = json.loads(open(os.path.join(HERE, f)).read().replace("${COOKBOOK_BUCKET}", bucket))
        env = LOCK[spec["container"]]
        if not os.path.exists(os.path.join(ROOT, "envs-x86_64", env + ".linux-64.lock")):
            sys.exit("no envs-x86_64/%s.linux-64.lock" % env)
        spec["task_id"] = "%s-%s-%s" % (spec["task_id"], arch.replace("_", ""), nonce)
        spec["container"] = sa.MICROMAMBA_X86
        spec["command"] = ["bash", "-c", (sa.ENV_PREAMBLE % {"name": env, "sha": sha}).replace("aarch.science env twin", "aarch.bio image twin") + CPU + spec["command"][2]]
        stage = "s3://%s/inputs/env-x86_64/%s" % (bucket, env)
        spec["inputs"] += [{"source": stage + "/pkgs/", "destination": "/tmp/xenv-pkgs/"},
                           {"source": "%s/%s.linux-64.lock" % (stage, env), "destination": "/tmp/xenv.lock"},
                           {"source": "s3://%s/inputs/env-x86_64/xenv_install.sh" % bucket, "destination": "/tmp/xenv-install.sh"}]
        res = spec["resources"]
        res["disk_gib"] = max(int(res.get("disk_gib", 0)), sa.ENV_DISK_GIB)
        res["architecture"] = sa.ARCH[arch]
        res["families"] = [sa.FAMILY[arch].get(x, x) for x in res["families"]]
        life = spec["lifecycle"]
        life["cost_limit"] = cap if cap is not None else round(float(life["cost_limit"]) * sa.COST_FACTOR[arch], 2)
        for io in spec["inputs"] + spec["outputs"]:
            for k in ("source", "destination"):
                io[k] = io[k].replace("/runs/", "/runs-%s/" % arch)
        out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "%s.%s.%s.json" % (f[:-len(".task.json")], nonce, arch))
        json.dump(spec, open(out, "w"), indent=2)
        print(out)


if __name__ == "__main__":
    main()
