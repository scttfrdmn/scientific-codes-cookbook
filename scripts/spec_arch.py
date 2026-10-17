#!/usr/bin/env python3
"""Resolve a recipe's TaskSpecs for a target architecture, without a second copy of them.

    python3 scripts/spec_arch.py <recipe> <arch> <bucket> <nonce> [spec.task.json ...]

Prints the resolved spec paths. With trailing spec paths, those specs are rewritten instead of the
recipe's own (a measurement's specs, say) -- the x86 pins still come from the recipe's frontmatter.

arm64 is what the specs say, so it is a plain bucket/nonce substitution (exactly what
`make spec` always did). For x86_64 the same specs are rewritten from the recipe's own
frontmatter, which is the one place the x86 pins live:

    images_x86_64:
      - quay.io/biocontainers/salmon@sha256:...        # matched to the arm64 image by repo name
      - astro=docker.io/some/image@sha256:...          # or named explicitly when names differ
    last_verified_x86_64: 2026-10-13                   # set only by a real verifying run
    env_x86_64: neuroimaging-d0a05a385703              # an aarch.science env's linux-64 twin
    gap_x86_64: <why there is no x86 build>            # instead of either, when there is none

The rewrite: every container swapped for its x86 pin (an arm64 container with no pin is an
error, never a silent pass-through -- that would launch an ARM image on an x86 box), the
architecture set to x86_64, the same-generation family substituted (c8g->c8i, m8g->m8i,
r8g->r8i), the cost_limit scaled for the dearer x86 hour, and every `/runs/` prefix moved to `/runs-x86_64/`. The last one is what keeps an
x86 run from overwriting the arm64 outputs, and it is applied to INPUTS too, so a recipe that
reads a sibling's run output reads the sibling's x86 run -- a dependency chain stays one
architecture end to end. Staged `/inputs/` are data, not builds, and are shared.

`env_x86_64` is for recipes on an aarch.science env, which is built for ARM only. Its x86 twin
(envs-x86_64/<name>.linux-64.lock, from scripts/lock_env_x86.py: the same versions, linux-64
builds) is staged once per env by scripts/stage_env_x86.sh, and each aarch.science container in
the spec becomes a pinned micromamba image whose script first installs that lock OFFLINE --
recipes/fsl's model. The env goes to /var/tmp (container disk), not /tmp, which is the tmpfs
staging budget of half the RAM.
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY = {"x86_64": {"c8g": "c8i", "m8g": "m8i", "r8g": "r8i"}}
# Each spec's cost_limit was sized at Graviton prices; same-generation Intel is ~17% dearer per
# hour (c8i vs c8g). A working x86 run must not die on an ARM-priced cap, so the cap is scaled
# with margin. It stays a cap -- cost_limit is the spend guard, the TTL only a hang backstop.
COST_FACTOR = {"x86_64": 1.3}
# The env route's base image: mambaorg/micromamba 2.9.0-debian13, the amd64 entry of the manifest
# list recipes/fsl already pins (not cosign-signed, so the digest is the pin).
MICROMAMBA_X86 = "docker.io/mambaorg/micromamba@sha256:9a2239d03b9464c761ac87c13f45b37ec0f774db7d1b152a5f9add146bee2e72"
ENV_DISK_GIB = 40   # docker image + the installed env on the root volume; big envs reach ~10 GB
ENV_PREAMBLE = r'''set +e
# --- x86_64: aarch.science env twin %(name)s, installed offline from staged, lock-verified
# packages (scripts/spec_arch.py + envs-x86_64/%(name)s.linux-64.lock). Same versions as arm64.
export MAMBA_ROOT_PREFIX=/var/tmp/mamba
_T0=$(date +%%s)
# the staged file keeps the URL's %%-encoded basename (x264-1%%21164...), and file:// decodes it,
# so escape %% as %%25 or the file is "not found" (and micromamba 2.9.0 then segfaults)
awk -v d=/tmp/xenv-pkgs '/^https?:/{n=split($0,a,"/"); split(a[n],b,"#"); f=b[1]; gsub(/%%/,"%%25",f); print "file://" d "/" f "#" b[2]; next} {print}' /tmp/xenv.lock > /var/tmp/xenv.local.lock
micromamba create -y -q -p /var/tmp/env --offline -f /var/tmp/xenv.local.lock > /var/tmp/xenv-install.log 2>&1 \
  || { echo "FAIL: the x86 env twin %(name)s did not install from its staged packages"; tail -25 /var/tmp/xenv-install.log; exit 1; }
echo "x86 env twin %(name)s: $(grep -c '^https' /tmp/xenv.lock) packages installed offline in $(( $(date +%%s) - _T0 ))s"
export PATH=/var/tmp/env/bin:$PATH CONDA_PREFIX=/var/tmp/env
# what `conda activate` would also do: packages set their variables in activate.d (cmdstan's
# CMDSTAN, GDAL_DATA, PROJ_DATA, compiler CC/CXX), which an install alone never runs
for _f in /var/tmp/env/etc/conda/activate.d/*.sh; do [ -r "$_f" ] && . "$_f"; done
# --- the recipe's own script follows, unchanged
'''


def fm_block(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


def fm_list(block, key):
    """Entries of a YAML list key, e.g. `images_x86_64:` followed by `  - ref` lines."""
    out, on = [], False
    for line in block.splitlines():
        if re.match(r"^%s:\s*$" % re.escape(key), line):
            on = True
            continue
        if on:
            m = re.match(r"^\s+-\s+(\S+)", line)
            if m:
                out.append(m.group(1))
            elif line.strip() and not line.startswith(" "):
                break
    return out


def fm_scalar(block, key):
    m = re.search(r"^%s:\s*(.+)$" % re.escape(key), block, re.M)
    return m.group(1).strip() if m else None


def repo_name(ref):
    """quay.io/aarchbio/salmon@sha256:... -> salmon"""
    return ref.split("@")[0].split(":")[0].rstrip("/").split("/")[-1]


def x86_map(recipe):
    """(per-image pins, env-twin name or None) from the recipe's frontmatter."""
    block = fm_block(open(os.path.join(ROOT, "recipes", recipe, "README.md"), encoding="utf-8").read())
    gap = fm_scalar(block, "gap_x86_64")
    entries = fm_list(block, "images_x86_64")
    env = fm_scalar(block, "env_x86_64")
    if gap and (entries or env):
        sys.exit("%s: has gap_x86_64 and an x86 route -- one or the other" % recipe)
    if gap:
        sys.exit("%s: no x86_64 build -- %s" % (recipe, gap))
    if not entries and not env:
        sys.exit("%s: no images_x86_64 or env_x86_64 in its frontmatter, so there is no x86 run to resolve" % recipe)
    if env and not os.path.exists(os.path.join(ROOT, "envs-x86_64", env + ".linux-64.lock")):
        sys.exit("%s: env_x86_64 %s has no envs-x86_64/%s.linux-64.lock" % (recipe, env, env))
    m = {}
    for e in entries:
        name, sep, ref = e.partition("=")
        if not sep:
            name, ref = repo_name(e), e
        if "@sha256:" not in ref:
            sys.exit("%s: x86 image %r is not pinned by digest" % (recipe, ref))
        m[name] = ref
    return m, env


def resolve(recipe, arch, bucket, nonce, *paths):
    if paths:
        specs = [os.path.abspath(p) for p in paths]
    else:
        d = os.path.join(ROOT, "recipes", recipe)
        specs = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".task.json"))
    if not specs:
        sys.exit("recipe '%s' has no TaskSpec -- see its page for how it runs" % recipe)
    mapping, env = x86_map(recipe) if arch != "arm64" else (None, None)
    # A directory per invocation: the nonce is seconds-resolution, so two runs started in the
    # same second (two recipes both with an 01-call spec) would otherwise write the same file
    # and one would launch the other's spec. Measured -- gatk4 and clair3 collided.
    outdir = tempfile.mkdtemp(prefix="cookbook-spec.", dir=os.environ.get("TMPDIR", "/tmp"))
    # Objects this recipe's own tasks WRITE outside runs/ (a prepared BAM, a built index, a
    # measurement). On x86 they go to <prefix>-<arch>/ instead -- per OBJECT, not per prefix, because the same
    # prefix also holds staged files that must stay shared -- and this recipe's reads of those
    # exact objects follow them. Without this an x86 run overwrites the arm64 copy in place:
    # gatk4's task 00 rewrites the 966 MB 30x BAM that five other recipes read, and the bucket
    # keeps no versions. Other recipes keep reading the arm64-written object, so a cross-arch
    # comparison downstream stays on identical bytes.
    # Not only inputs/: bwa-mem2 also writes measurements/bwa-mem2-real/, and the rule has to
    # be "anything a recipe writes outside runs/", or the next such prefix is the next overwrite.
    written = set()
    for f in specs:
        for o in json.load(open(f, encoding="utf-8")).get("outputs", []):
            d = o.get("destination", "")
            m = re.match(r"s3://[^/]+/([^/]+)/", d)
            if m and m.group(1) != "runs":
                written.add(d)
    for f in specs:
        text = open(f, encoding="utf-8").read()
        text = text.replace("${COOKBOOK_BUCKET}", bucket)
        spec = json.loads(text)
        spec["task_id"] = "%s-%s" % (spec["task_id"], nonce) if arch == "arm64" \
            else "%s-%s-%s" % (spec["task_id"], arch.replace("_", ""), nonce)
        if mapping is not None:
            name = repo_name(spec["container"])
            res = spec.setdefault("resources", {})
            if env and spec["container"].startswith("quay.io/aarchsci/"):
                cmd = spec.get("command", [])
                if len(cmd) != 3 or cmd[0] != "bash" or cmd[1] != "-c":
                    sys.exit("%s/%s: the env route prepends an install step to a `bash -c` script; this command is %r"
                             % (recipe, os.path.basename(f), cmd[:2]))
                spec["container"] = MICROMAMBA_X86
                spec["command"] = ["bash", "-c", ENV_PREAMBLE % {"name": env} + cmd[2]]
                stage = "s3://%s/inputs/env-x86_64/%s" % (bucket, env)
                spec.setdefault("inputs", []).extend([
                    {"source": stage + "/pkgs/", "destination": "/tmp/xenv-pkgs/"},
                    {"source": "%s/%s.linux-64.lock" % (stage, env), "destination": "/tmp/xenv.lock"}])
                res["disk_gib"] = max(int(res.get("disk_gib", 0)), ENV_DISK_GIB)
            elif name in mapping:
                spec["container"] = mapping[name]
            else:
                sys.exit("%s/%s: container %s has no x86_64 pin (images_x86_64 has: %s%s)"
                         % (recipe, os.path.basename(f), spec["container"], ", ".join(sorted(mapping)) or "nothing",
                            "; env_x86_64 covers aarch.science images only" if env else ""))
            res["architecture"] = arch
            res["families"] = [FAMILY[arch].get(x, x) for x in res.get("families", [])]
            life = spec.get("lifecycle", {})
            if "cost_limit" in life:
                life["cost_limit"] = round(float(life["cost_limit"]) * COST_FACTOR[arch], 2)
            written_here = {w.replace("${COOKBOOK_BUCKET}", bucket) for w in written}
            for io_key in ("inputs", "outputs"):
                for io in spec.get(io_key, []):
                    for k in ("source", "destination"):
                        if k in io:
                            if io[k] in written_here:
                                io[k] = re.sub(r"^(s3://[^/]+/)([^/]+)/", r"\g<1>\g<2>-%s/" % arch, io[k])
                            io[k] = io[k].replace("/runs/", "/runs-%s/" % arch)
        base = os.path.basename(f)
        out = os.path.join(outdir, "%s.%s%s.json" % (base[:-len(".task.json")] if base.endswith(".task.json") else base, nonce,
                                                      "" if arch == "arm64" else "." + arch))
        with open(out, "w") as fh:
            json.dump(spec, fh, indent=2)
        print(out)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    resolve(*sys.argv[1:])
