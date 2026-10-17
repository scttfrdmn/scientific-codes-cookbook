#!/usr/bin/env python3
"""Write the x86_64 twin of the aarch.science env a recipe pins: same versions, linux-64 builds.

    python3 scripts/lock_env_x86.py <recipe>

aarch.science builds ARM only, so a recipe on one of its envs has no x86 image. This solves the
SAME environment for linux-64 from conda-forge and writes an explicit lock (URL + sha256 per
package, the format of recipes/fsl/fsl-linux-64.lock). The task then installs that lock offline
from staged packages (scripts/stage_env_x86.sh), so the x86 run uses the arm64 run's versions.

"The same environment" means the one in the recipe's pinned image, not today's env file:
  pinned arm64 digest -> the index that contains it -> its `s<lockhash>` tag on quay.io
  -> the aarch.science commit whose envs/<env>.lock.txt carries `lock-hash: <lockhash>`.
The declared packages (envs/<env>.yaml at that commit) are pinned to their lock versions; a second
pass pins every other locked version that also exists on linux-64. Whatever still differs is
written into the lock's header, so a version drift is visible rather than silent.

Writes envs-x86_64/<env>-<lockhash>.linux-64.lock and prints the `env_x86_64:` value to use.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "cookbook", "aarchsci")
REPO = "https://github.com/playgroundlogic/aarchsci"
SOLVE_ENV = dict(os.environ, CONDA_SUBDIR="linux-64", CONDA_OVERRIDE_GLIBC="2.36", CONDA_OVERRIDE_LINUX="6.1")


def frontmatter(recipe):
    text = open(os.path.join(ROOT, "recipes", recipe, "README.md")).read()
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        sys.exit("%s: no frontmatter" % recipe)
    fm = m.group(1)
    env = re.search(r"^env:\s*(\S+)", fm, re.M)
    img = re.search(r"^image:\s*quay\.io/aarchsci/([\w.-]+)@(sha256:[0-9a-f]{64})", fm, re.M)
    if not (env and img):
        sys.exit("%s: needs `env:` and an `image: quay.io/aarchsci/<env>@sha256:...` in its frontmatter" % recipe)
    return env.group(1), img.group(1), img.group(2)


def quay(path, token=None, accept=None):
    req = urllib.request.Request("https://quay.io" + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def lock_hash_for(repo, arm64_digest):
    """The lock hash of the image whose index contains this arm64 entry, from its s<hash> tag."""
    token = quay("/v2/auth?service=quay.io&scope=repository:aarchsci/%s:pull" % repo)["token"]
    index_accept = "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"
    tags, page = [], 1
    while True:
        r = quay("/api/v1/repository/aarchsci/%s/tag/?limit=100&page=%d" % (repo, page))
        tags += r.get("tags", [])
        if not r.get("has_additional"):
            break
        page += 1
    seen = {}
    for t in tags:
        if not re.fullmatch(r"s[0-9a-f]{12}", t["name"]):
            continue
        idx = t["manifest_digest"]
        if idx not in seen:
            try:
                seen[idx] = quay("/v2/aarchsci/%s/manifests/%s" % (repo, idx), token, index_accept)
            except Exception:
                continue
        entries = [m["digest"] for m in seen[idx].get("manifests", [])]
        if arm64_digest in entries or idx == arm64_digest:
            return t["name"][1:], idx
    sys.exit("no s<lockhash> tag on aarchsci/%s points at an index containing %s" % (repo, arm64_digest))


def git(*args):
    return subprocess.run(["git", "-C", CACHE] + list(args), check=True, capture_output=True, text=True).stdout


def env_at(env, lock_hash):
    if not os.path.isdir(os.path.join(CACHE, ".git")):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        subprocess.run(["git", "clone", "-q", REPO, CACHE], check=True)
    git("fetch", "-q", "origin")
    commits = git("log", "origin/main", "--format=%H", "-S", "lock-hash: %s" % lock_hash, "--", "envs/%s.lock.txt" % env).split()
    if not commits:
        sys.exit("no aarch.science commit wrote envs/%s.lock.txt with lock-hash %s" % (env, lock_hash))
    c = commits[-1]  # the commit that introduced this lock
    lock_txt = git("show", "%s:envs/%s.lock.txt" % (c, env))
    yaml_txt = git("show", "%s:envs/%s.yaml" % (c, env))
    lock = {}
    for line in lock_txt.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, version = line.split()[:2]
        lock[name] = version
    channels, declared = [], []
    section = None
    for line in yaml_txt.splitlines():
        if re.match(r"^channels:", line):
            section = "c"
        elif re.match(r"^dependencies:", line):
            section = "d"
        elif re.match(r"^\S", line):
            section = None
        m = re.match(r"\s*-\s*([A-Za-z0-9_.\-]+)", line)
        if m and section == "c":
            channels.append(m.group(1))
        elif m and section == "d":
            declared.append(m.group(1))
    return c[:12], lock, channels or ["conda-forge"], declared


def solve(specs, channels):
    cmd = ["mamba", "create", "-n", "_cookbook_x86", "--dry-run", "--json", "--override-channels"]
    for ch in channels:
        cmd += ["-c", ch]
    out = subprocess.run(cmd + specs, capture_output=True, text=True, env=SOLVE_ENV).stdout
    try:
        d = json.loads(out)
    except ValueError:
        return None, out[:400]
    if not d.get("success"):
        return None, "; ".join(str(p) for p in (d.get("solver_problems") or [d.get("error")]))[:600]
    return d["actions"]["LINK"], None


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    recipe = sys.argv[1]
    env, repo, digest = frontmatter(recipe)
    lock_hash, index = lock_hash_for(repo, digest)
    commit, lock, channels, declared = env_at(env, lock_hash)
    pins = ["%s=%s" % (n, lock[n]) if n in lock else n for n in declared]
    link, err = solve(pins, channels)
    if link is None:
        sys.exit("%s: linux-64 solve of the declared packages at their arm64 versions failed: %s" % (env, err))
    got = {p["name"]: p["version"] for p in link}
    drift = [n for n, v in lock.items() if n in got and got[n] != v]
    if drift:  # second pass: pin the drifted ones too, where that version exists on linux-64
        link2, _ = solve(pins + ["%s=%s" % (n, lock[n]) for n in drift if n not in declared], channels)
        if link2 is not None:
            link = link2
            got = {p["name"]: p["version"] for p in link}
    differ = sorted("%s %s (arm64 %s)" % (n, got[n], lock[n]) for n in lock if n in got and got[n] != lock[n])
    x86_only = sorted(n for n in got if n not in lock)
    arm_only = sorted(n for n in lock if n not in got)
    name = "%s-%s" % (env, lock_hash)
    out = os.path.join(ROOT, "envs-x86_64", name + ".linux-64.lock")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write("# x86_64 twin of aarch.science `%s` (image %s, index %s, lock-hash %s)\n" % (env, digest[:19], index[:19], lock_hash))
        fh.write("# solved for linux-64 from aarch.science %s envs/%s.yaml at commit %s, every locked version pinned where linux-64 has it\n" % (REPO, env, commit))
        fh.write("# %d packages; %d of %d arm64 versions identical\n" % (len(link), len(lock) - len(differ) - len(arm_only), len(lock)))
        for line in differ:
            fh.write("# differs: %s\n" % line)
        if arm_only:
            fh.write("# arm64 only (platform packages): %s\n" % ", ".join(arm_only))
        if x86_only:
            fh.write("# linux-64 only (platform packages): %s\n" % ", ".join(x86_only))
        fh.write("# generated by scripts/lock_env_x86.py %s\n@EXPLICIT\n" % recipe)
        for p in sorted(link, key=lambda p: p["name"]):
            fh.write("%s#%s\n" % (p["url"], p["sha256"]))
    print("wrote %s: %d packages, %d version(s) differ from arm64" % (os.path.relpath(out, ROOT), len(link), len(differ)))
    print("env_x86_64: %s" % name)


if __name__ == "__main__":
    main()
