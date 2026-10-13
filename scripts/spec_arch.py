#!/usr/bin/env python3
"""Resolve a recipe's TaskSpecs for a target architecture, without a second copy of them.

    python3 scripts/spec_arch.py <recipe> <arch> <bucket> <nonce>     # prints resolved paths

arm64 is what the specs say, so it is a plain bucket/nonce substitution (exactly what
`make spec` always did). For x86_64 the same specs are rewritten from the recipe's own
frontmatter, which is the one place the x86 pins live:

    images_x86_64:
      - quay.io/biocontainers/salmon@sha256:...        # matched to the arm64 image by repo name
      - astro=docker.io/some/image@sha256:...          # or named explicitly when names differ
    last_verified_x86_64: 2026-10-13                   # set only by a real verifying run
    gap_x86_64: <why there is no x86 build>            # instead of images, when there is none

The rewrite: every container swapped for its x86 pin (an arm64 container with no pin is an
error, never a silent pass-through -- that would launch an ARM image on an x86 box), the
architecture set to x86_64, the same-generation family substituted (c8g->c8i, m8g->m8i,
r8g->r8i), the cost_limit scaled for the dearer x86 hour, and every `/runs/` prefix moved to `/runs-x86_64/`. The last one is what keeps an
x86 run from overwriting the arm64 outputs, and it is applied to INPUTS too, so a recipe that
reads a sibling's run output reads the sibling's x86 run -- a dependency chain stays one
architecture end to end. Staged `/inputs/` are data, not builds, and are shared.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY = {"x86_64": {"c8g": "c8i", "m8g": "m8i", "r8g": "r8i"}}
# Each spec's cost_limit was sized at Graviton prices; same-generation Intel is ~17% dearer per
# hour (c8i vs c8g). A working x86 run must not die on an ARM-priced cap, so the cap is scaled
# with margin. It stays a cap -- cost_limit is the spend guard, the TTL only a hang backstop.
COST_FACTOR = {"x86_64": 1.3}


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
    block = fm_block(open(os.path.join(ROOT, "recipes", recipe, "README.md"), encoding="utf-8").read())
    gap = fm_scalar(block, "gap_x86_64")
    entries = fm_list(block, "images_x86_64")
    if gap and entries:
        sys.exit("%s: has both gap_x86_64 and images_x86_64 -- one or the other" % recipe)
    if gap:
        sys.exit("%s: no x86_64 build -- %s" % (recipe, gap))
    if not entries:
        sys.exit("%s: no images_x86_64 in its frontmatter, so there is no x86 run to resolve" % recipe)
    m = {}
    for e in entries:
        name, sep, ref = e.partition("=")
        if not sep:
            name, ref = repo_name(e), e
        if "@sha256:" not in ref:
            sys.exit("%s: x86 image %r is not pinned by digest" % (recipe, ref))
        m[name] = ref
    return m


def resolve(recipe, arch, bucket, nonce):
    specs = sorted(f for f in os.listdir(os.path.join(ROOT, "recipes", recipe)) if f.endswith(".task.json"))
    if not specs:
        sys.exit("recipe '%s' has no TaskSpec -- see its page for how it runs" % recipe)
    mapping = x86_map(recipe) if arch != "arm64" else None
    outdir = os.environ.get("TMPDIR", "/tmp")
    # Objects this recipe's own tasks WRITE under inputs/ (a prepared BAM, a built index).
    # On x86 they go to inputs-<arch>/ instead -- per OBJECT, not per prefix, because the same
    # prefix also holds staged files that must stay shared -- and this recipe's reads of those
    # exact objects follow them. Without this an x86 run overwrites the arm64 copy in place:
    # gatk4's task 00 rewrites the 966 MB 30x BAM that five other recipes read, and the bucket
    # keeps no versions. Other recipes keep reading the arm64-written object, so a cross-arch
    # comparison downstream stays on identical bytes.
    written = set()
    for f in specs:
        for o in json.load(open(os.path.join(ROOT, "recipes", recipe, f), encoding="utf-8")).get("outputs", []):
            if "/inputs/" in o.get("destination", ""):
                written.add(o["destination"])
    for f in specs:
        text = open(os.path.join(ROOT, "recipes", recipe, f), encoding="utf-8").read()
        text = text.replace("${COOKBOOK_BUCKET}", bucket)
        spec = json.loads(text)
        spec["task_id"] = "%s-%s" % (spec["task_id"], nonce) if arch == "arm64" \
            else "%s-%s-%s" % (spec["task_id"], arch.replace("_", ""), nonce)
        if mapping is not None:
            name = repo_name(spec["container"])
            if name not in mapping:
                sys.exit("%s/%s: container %s has no x86_64 pin (images_x86_64 has: %s)"
                         % (recipe, f, spec["container"], ", ".join(sorted(mapping))))
            spec["container"] = mapping[name]
            res = spec.setdefault("resources", {})
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
                                io[k] = io[k].replace("/inputs/", "/inputs-%s/" % arch)
                            io[k] = io[k].replace("/runs/", "/runs-%s/" % arch)
        out = os.path.join(outdir, "%s.%s%s.json" % (f[:-len(".task.json")], nonce,
                                                      "" if arch == "arm64" else "." + arch))
        with open(out, "w") as fh:
            json.dump(spec, fh, indent=2)
        print(out)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    resolve(*sys.argv[1:])
