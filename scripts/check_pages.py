#!/usr/bin/env python3
"""Lean checks for the cookbook's markdown pages — stdlib only, no deps.

  python3 scripts/check_pages.py              # frontmatter + a11y + internal links (fast, offline)
  python3 scripts/check_pages.py --external   # + external link liveness (network, slower)
  python3 scripts/check_pages.py --records a-real,b-real   # only the measurement-record checks

Errors (exit 1): a recipe page missing frontmatter or a required field; a broken
internal link. Warnings (exit 0): the markdown-a11y nits. Site-level a11y — colour
contrast, focus order, Atkinson Hyperlegible — is a site-build gate, not this.
"""
import subprocess
import os, re, sys, glob, json, decimal, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The image DIGEST is the authoritative version-of-record; tool_version is a friendly
# label best resolved accurately from the image (conda list), so it's recommended, not required.
REQUIRED_FM = ("tool", "image", "spawn_version")
# tool_version is a friendly label. last_verified is a DATE only a real verifying run may set —
# absent means "not verified since we started tracking," which is true and is the point: the
# warning is a TODO queue of recipes awaiting a run, never backfilled to silence it. last_updated
# is NOT frontmatter — it's git-derived and generated into the catalog (git already knows it).
RECOMMENDED_FM = ("tool_version",)
BAD_LINK_TEXT = {"here", "click here", "link", "this", "read more"}
# Contract (see CLAUDE.md "Recipe pages"). Enforced structurally; prose quality is human review.
RECIPE_LEDE_MAX = 8      # non-blank lines between the H1 and the first `##` before it's a buried-lede smell
RECIPE_FOLD_MAX = 50     # lines outside the one <details>
ANCILLARY_MAX = 90       # a pattern/practice page over this is probably doing too much
# A phrase a practice/pattern OWNS; a recipe using it must LINK that page, not restate the idea.
OWNED_PHRASES = {
    "compare like with like": "cross-checks.md",
    "reproduce a published": "reference-from-tests.md",
    "silent serial build": "mpi-rank-count.md",
    "one tool per image": "container-path.md",
    "pay for the bytes you touch": "data-movement.md",
}
errors, warns = [], []


def _headings(text):
    return [(i, len(m.group(1)), m.group(2).strip())
            for i, line in enumerate(text.split("\n"))
            if (m := re.match(r"(#{1,6}) (.*)", line))]


def _outside_details_lines(text):
    """Count lines NOT inside a <details>...</details> block (frontmatter + blanks included,
    matching the 'lines before <details>' the ceiling was calibrated against).

    The x86_64 frontmatter keys are NOT counted. The ceiling was calibrated before they
    existed, they add the same 2-4 lines to every page, and no reader prose comes with them --
    counting them would push pages over without a word of new text. Only lines that belong to
    those keys are excluded (an `images:` list entry under the arm64 key still counts)."""
    depth, outside = 0, 0
    fm_end = text.find("\n---\n", 4) if text.startswith("---\n") else -1
    fm_lines = text[:fm_end].count("\n") + 1 if fm_end != -1 else 0
    in_x86 = False
    for i, line in enumerate(text.split("\n")):
        if i < fm_lines:
            if re.match(r"^(images_x86_64:|env_x86_64:|last_verified_x86_64:|last_verified_amd:|gap_x86_64:|gap_arm64:)", line):
                in_x86 = line.startswith("images_x86_64:")
                continue
            if in_x86 and re.match(r"^\s+- ", line):
                continue
            in_x86 = False
        o, c = line.count("<details"), line.count("</details>")
        if depth == 0 and o == 0:
            outside += 1
        depth += o - c
        if depth < 0:
            depth = 0
    return outside


def _st(status, key, value):
    if status is not None:
        status[key] = value


def check_recipe_contract(text, rel, status=None):
    """Enforce the recipe-page contract. `status` is filled in with per-rule booleans so the
    inventory can report the same verdicts the gate enforces -- one implementation, not two."""
    lines = text.split("\n")
    hs = _headings(text)
    h1 = next((i for i, lvl, _ in hs if lvl == 1), None)
    h2s = [(i, t) for i, lvl, t in hs if lvl == 2]
    titles = [t for _, t in h2s]

    # R1 — lede not buried: few non-blank lines between H1 and the first `##`.
    if h1 is not None and h2s:
        gap = [l for l in lines[h1 + 1:h2s[0][0]] if l.strip()]
        if len(gap) > RECIPE_LEDE_MAX:
            warns.append(f"{rel}: {len(gap)} non-blank lines before the first `##` — lede may be buried (R1, human-check)")

    # R2 — `## Run it` is the first `##`, with a fenced invocation inside it.
    if "Run it" not in titles:
        errors.append(f"{rel}: missing `## Run it` (R2)")
        _st(status, "R2", False)
    elif titles[0] != "Run it":
        errors.append(f"{rel}: `## Run it` is not the first section (first is `## {titles[0]}`) (R2)")
    else:
        run_i = h2s[0][0]
        nxt = h2s[1][0] if len(h2s) > 1 else len(lines)
        if not any(l.startswith("```") for l in lines[run_i:nxt]):
            errors.append(f"{rel}: no fenced invocation inside `## Run it` (R2)")

    # R3 — `## Make it yours` present, with a table.
    if "Make it yours" not in titles:
        errors.append(f"{rel}: missing `## Make it yours` (R3)")
        _st(status, "R3", False)
    else:
        mi = next(i for i, t in h2s if t == "Make it yours")
        nxt = next((i for i, _ in h2s if i > mi), len(lines))
        if not any("|" in l and "---" in l for l in lines[mi:nxt]):
            warns.append(f"{rel}: `## Make it yours` has no table (R3, human-check)")

    # R4 — order: Run it < Make it yours < <details>.
    det = text.find("<details")
    det_ln = text[:det].count("\n") if det != -1 else None
    order = [(titles.index(t), t) for t in ("Run it", "Make it yours") if t in titles]
    if order != sorted(order):
        errors.append(f"{rel}: sections out of order — want Run it → Make it yours (R4)")
    if det_ln is not None and "Make it yours" in titles:
        mi = next(i for i, t in h2s if t == "Make it yours")
        if det_ln < mi:
            errors.append(f"{rel}: `<details>` appears before `## Make it yours` (R4)")

    # R5 — exactly one <details>.
    n_det = text.count("<details")
    if n_det == 0:
        errors.append(f"{rel}: no `<details>` — verification must live in one collapsed block (R5)")
        _st(status, "R5", False)
    elif n_det > 1:
        errors.append(f"{rel}: {n_det} `<details>` blocks — verification must be in exactly one (R5)")
        _st(status, "R5", False)

    # R6 — line ceiling outside <details>.
    od = _outside_details_lines(text)
    if od > RECIPE_FOLD_MAX:
        errors.append(f"{rel}: {od} lines outside `<details>` (ceiling {RECIPE_FOLD_MAX}) — cut, or justify (R6)")
        _st(status, "R6", False)

    # R7 — no re-teaching: an owned phrase without a link to its page.
    low = text.lower()
    for phrase, page in OWNED_PHRASES.items():
        if phrase in low and page not in text:
            warns.append(f"{rel}: says \"{phrase}\" but doesn't link {page} — restating, not linking? (R7, human-check)")


def check_ancillary_contract(text, rel):
    lines = text.split("\n")
    hs = _headings(text)
    h1 = next((i for i, lvl, _ in hs if lvl == 1), None)
    # A1 — lede present: non-blank content within 2 lines after the H1.
    if h1 is not None and not any(l.strip() for l in lines[h1 + 1:h1 + 3]):
        warns.append(f"{rel}: no lede within 2 lines of the H1 (A1, human-check)")
    # A3 — tight.
    if len(lines) > ANCILLARY_MAX:
        warns.append(f"{rel}: {len(lines)} lines — a pattern/practice page over {ANCILLARY_MAX} is likely doing too much (A3)")


def pages():
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "README.md"))):
        out.append((p, True))            # recipe page → needs frontmatter
    for d in ("patterns", "practices"):
        for p in sorted(glob.glob(os.path.join(ROOT, d, "*.md"))):
            out.append((p, False))
    return out


def frontmatter_keys(text):
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return {m.group(1) for m in re.finditer(r"^([a-z0-9_]+):", text[4:end], re.M)}


def check_x86_frontmatter(text, keys, rel):
    """The x86_64 pins live beside the arm64 ones (scripts/spec_arch.py reads them). A recipe
    either has x86 pins, or records why there is no x86 build -- never both -- and only a real
    x86 run may stamp last_verified_x86_64, which therefore needs pins to have run. An
    aarch.science recipe's x86 route is env_x86_64 (a committed linux-64 twin of its env)."""
    has_imgs, has_gap = "images_x86_64" in keys, "gap_x86_64" in keys
    has_env = "env_x86_64" in keys
    if has_env:
        m = re.search(r"^env_x86_64:\s*(\S+)", text, re.M)
        lock = os.path.join(ROOT, "envs-x86_64", (m.group(1) if m else "") + ".linux-64.lock")
        if not m or not os.path.exists(lock):
            errors.append(f"{rel}: env_x86_64 names no committed envs-x86_64/<name>.linux-64.lock")
        if has_gap:
            errors.append(f"{rel}: frontmatter has both env_x86_64 and gap_x86_64 -- one or the other")
    if "gap_arm64" in keys and (has_gap or not has_imgs):
        errors.append(f"{rel}: gap_arm64 needs images_x86_64 and no gap_x86_64 -- an x86-only recipe must have an x86 route")
    if "gap_arm64" in keys and "image" in keys:
        errors.append(f"{rel}: gap_arm64 and an arm64 image: -- one or the other")
    if has_imgs and has_gap:
        errors.append(f"{rel}: frontmatter has both images_x86_64 and gap_x86_64 -- one or the other")
    for k in ("last_verified_x86_64", "last_verified_amd"):     # amd runs the same x86 pins on c8a/m8a/r8a
        if k in keys and not (has_imgs or has_env):
            errors.append(f"{rel}: {k} without images_x86_64 or env_x86_64 -- nothing to have run")
    if has_imgs:
        block = text[4:text.find("\n---\n", 4)]
        m = re.search(r"^images_x86_64:\s*\n((?:\s+-\s+.*\n?)+)", block, re.M)
        entries = re.findall(r"^\s+-\s+(\S+)", m.group(1), re.M) if m else []
        if not entries:
            errors.append(f"{rel}: images_x86_64 is empty")
        for e in entries:
            if "@sha256:" not in e:
                errors.append(f"{rel}: x86_64 image {e} is not pinned by digest")


def _git(*args):
    r = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _mpi_flavours(path):
    """{package: 'nompi' | 'mpi_openmpi' | ...} from the build strings of an @EXPLICIT lock."""
    out = {}
    for line in open(path, encoding="utf-8"):
        if not line.startswith("http"):
            continue
        stem = re.sub(r"\.(conda|tar\.bz2)$", "", line.split("#")[0].rsplit("/", 1)[1])
        parts = stem.rsplit("-", 2)
        if len(parts) == 3:
            m = re.search(r"(^|_)(nompi|mpi_[a-z]+)", parts[2])
            if m:
                out[parts[0]] = m.group(2)
    return out


def check_twin_mpi_flavour(text, rel):
    """An x86 twin that resolves a package's serial (nompi) build where the arm64 image ships an
    MPI build makes an x86 rank-count assertion fail for the twin's sake, not the chip's
    (measured twice: dft gpaw/siesta, md gromacs/lammps/plumed). Warn, naming the packages: a
    recipe on that twin may not use them, which only its page can say."""
    m = re.search(r"^env_x86_64:\s*(\S+)", text, re.M)
    if not m:
        return
    x = os.path.join(ROOT, "envs-x86_64", m.group(1) + ".linux-64.lock")
    a = os.path.join(ROOT, "envs-arm64", m.group(1) + ".linux-aarch64.lock")
    if not (os.path.exists(x) and os.path.exists(a)):
        return
    fa, fx = _mpi_flavours(a), _mpi_flavours(x)
    bad = sorted("%s (arm64 %s, x86 %s)" % (n, fa[n], fx[n]) for n in fa if n in fx and fa[n] != fx[n])
    if bad:
        warns.append(f"{rel}: its x86 twin's MPI flavour differs from the arm64 image's for {', '.join(bad)} "
                     f"-- fine only if this recipe does not run them")


def check_lock_immutable(text, rel):
    """practices/environment-durability.md: a committed env lock is never re-solved. The lock a
    verifying x86 run used must not change afterwards -- conda-forge patches repodata, so a
    re-solve can silently swap the env out from under the verification. Compared by git ancestry,
    not dates (merges land after runs): the last commit that changed the lock's PACKAGE lines must
    not come after the last commit that set last_verified_x86_64. Header-only edits don't count."""
    m = re.search(r"^env_x86_64:\s*(\S+)", text, re.M)
    if m:
        arm = os.path.join(ROOT, "envs-arm64", m.group(1) + ".linux-aarch64.lock")
        if not os.path.exists(arm):
            warns.append(f"{rel}: no envs-arm64/{m.group(1)}.linux-aarch64.lock -- the arm64 env as the image ships it, "
                         f"so a reader can rebuild it without a solve (practices/environment-durability.md)")
    if not m or not re.search(r"^last_verified_x86_64:", text, re.M):
        return
    lock = "envs-x86_64/%s.linux-64.lock" % m.group(1)
    body = _git("log", "-1", "--format=%H", "-G", "^https?://", "--", lock)
    verified = _git("log", "-1", "--format=%H", "-G", "^last_verified_x86_64:", "--", rel)
    if not body or not verified:
        return   # uncommitted work in progress: nothing to compare yet
    # Re-verifying on the SAME date leaves the last_verified_x86_64 line unchanged, so git has no newer
    # commit for it; then the verified date itself is the evidence. A lock whose packages last changed
    # on or before the date the recipe says it was verified passes (measured: gemmi re-run on a
    # re-locked md twin the same day as its clean-pass run).
    vdate = re.search(r"^last_verified_x86_64:\s*(\S+)", text, re.M).group(1)
    bdate = _git("log", "-1", "--format=%cs", body)
    if body != verified and bdate > vdate and \
            subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor", body, verified]).returncode != 0:
        errors.append(f"{rel}: {lock} changed its packages after the run that verified it on x86 "
                      f"(commit {body[:8]} is not before {verified[:8]}) -- a committed lock is never re-solved; "
                      f"re-run x86 to re-verify, or restore the lock (practices/environment-durability.md)")


def check_internal_links(text, path):
    """Link text + internal-link existence for one file, relative to its own dir. Factored out
    so the generated catalog and the landing docs get the same check the recipe pages do."""
    rel = os.path.relpath(path, ROOT)
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        txt, target = m.group(1).strip().lower(), m.group(2).strip()
        if txt in BAD_LINK_TEXT:
            warns.append(f"{rel}: non-descriptive link text '{txt}'")
        url = target.split()[0].split("#")[0]
        if not url or url.startswith(("http://", "https://", "mailto:")):
            continue
        dest = os.path.normpath(os.path.join(os.path.dirname(path), url))
        if not os.path.exists(dest):
            errors.append(f"{rel}: broken internal link -> {target}")


def check(path, needs_fm):
    rel = os.path.relpath(path, ROOT)
    text = open(path, encoding="utf-8").read()
    if needs_fm:
        keys = frontmatter_keys(text)
        if keys is None:
            errors.append(f"{rel}: missing YAML frontmatter block")
        else:
            for k in REQUIRED_FM:
                # A pipeline (Shape-F) recipe pins several images, one per dispatched
                # tool, under `images:` — accept it in place of a single `image:`.
                if k == "image" and "images" in keys:
                    continue
                # An x86-only recipe (no arm64 build exists) records gap_arm64 and pins its
                # image under images_x86_64 -- the mirror of gap_x86_64.
                if k == "image" and "gap_arm64" in keys and "images_x86_64" in keys:
                    continue
                if k not in keys:
                    errors.append(f"{rel}: frontmatter missing required field '{k}'")
            for k in RECOMMENDED_FM:
                if k not in keys:
                    warns.append(f"{rel}: frontmatter missing recommended field '{k}' (refresh target fills it)")
            if "last_verified" not in keys and "gap_arm64" not in keys:
                warns.append(f"{rel}: no last_verified — awaiting a verifying run (a real run stamps it; never backfilled)")
            check_x86_frontmatter(text, keys, rel)
            check_lock_immutable(text, rel)
            check_twin_mpi_flavour(text, rel)
    fences = list(re.finditer(r"^```(\S*)", text, re.M))
    for i, m in enumerate(fences):
        if i % 2 == 0 and m.group(1) == "":   # opening fence (even index) with no language; closers are odd
            warns.append(f"{rel}: code fence with no language (line {text[:m.start()].count(chr(10))+1})")
    for m in re.finditer(r"!\[([^\]]*)\]\(", text):
        if not m.group(1).strip():
            warns.append(f"{rel}: image with empty alt text")
    no_code = re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)   # drop fenced blocks so '#' comments aren't headings
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", no_code, re.M)]
    for a, b in zip(levels, levels[1:]):
        if b > a + 1:
            warns.append(f"{rel}: heading level skips h{a}->h{b}")
            break
    check_internal_links(text, path)
    if needs_fm:
        check_recipe_contract(text, rel)
    else:
        check_ancillary_contract(text, rel)


def check_external():
    seen = set()
    for path, _ in pages():
        text = open(path, encoding="utf-8").read()
        for m in re.finditer(r"\]\((https?://[^)\s]+)\)", text):
            u = m.group(1)
            if u in seen:
                continue
            seen.add(u)
            try:
                req = urllib.request.Request(u, method="HEAD",
                                             headers={"User-Agent": "cookbook-linkcheck"})
                urllib.request.urlopen(req, timeout=15)
            except Exception as e:
                code = getattr(e, "code", None)
                if code in (404, 410):   # GitHub 403s HEAD from some UAs; only flag gone
                    errors.append(f"external link dead ({code}): {u}")
                else:
                    warns.append(f"external link unverified ({code or e}): {u}")


# Public read-only data buckets are portable BY CONSTRUCTION: they resolve identically in every
# account, which is the property this check exists to protect. Parameterising one would be wrong
# -- for a pinned public dataset the bucket name IS part of the pin, and reading it in place
# beats copying it (recipes/pathology reads a 546 MB CAMELYON16 slide from RODA in-region in 6 s,
# with no second copy to keep true). Listed explicitly, so a typo or an accidental private
# bucket still fails.
PUBLIC_DATA_BUCKETS = {
    "camelyon-dataset",        # RODA: CAMELYON16/17 digital pathology (us-west-2)
}


def check_portability():
    """The executable path must run in any account. Task specs reference our bucket only as
    ${COOKBOOK_BUCKET} (make run substitutes it); stage scripts take it, not a hardcoded one.
    A declared public dataset bucket is allowed -- see PUBLIC_DATA_BUCKETS."""
    for f in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "*.task.json"))):
        rel = os.path.relpath(f, ROOT)
        text = open(f, encoding="utf-8").read()
        for bucket in {m.group(1) for m in re.finditer(r"s3://([^/\"\s]+)", text)}:
            if bucket != "${COOKBOOK_BUCKET}" and bucket not in PUBLIC_DATA_BUCKETS:
                errors.append(f"{rel}: hardcoded bucket 's3://{bucket}' — use s3://${{COOKBOOK_BUCKET}} (portability)")
    for f in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "stage-inputs.sh"))):
        rel = os.path.relpath(f, ROOT)
        if "942542972736" in open(f, encoding="utf-8").read():
            errors.append(f"{rel}: hardcoded account bucket — default to $COOKBOOK_BUCKET or require the arg (portability)")
    # READMEs: no hardcoded bucket, and the run path is `make run`, not a raw spec (locks in the sweep).
    for f in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "README.md")) +
                    glob.glob(os.path.join(ROOT, "*.md"))):
        rel = os.path.relpath(f, ROOT)
        text = open(f, encoding="utf-8").read()
        if "scicookbook-942542972736" in text:
            errors.append(f"{rel}: hardcoded account bucket in a page — use $COOKBOOK_BUCKET / make ls (portability)")
        if re.search(r"spawn task run --spec recipes/", text):
            errors.append(f"{rel}: raw 'spawn task run --spec recipes/…' — the runnable path is `make run RECIPE=…` (portability)")


def check_diagnostics_survive():
    """Two defects that only bite when a run fails, measured across 118 specs (58 and 22).

    A task's own log is the first thing you want when a run dies, and `command.log` only
    ships at stage-out (spawn#632) -- so a log the task writes but never uploads dies with
    the instance. mdtraj cost two blind-diagnosed failures to this; flye cost one.

    And Python buffers stdout when it is not a tty, so a killed process loses output it had
    already produced: `python3 -u` is the difference between a probe that says where it got
    to and one that says nothing at all.

    Warnings, not errors: the fix changes a spec, and a spec change means the recipe needs
    re-running before its last_verified still covers what ships. So these land when a recipe
    is next run, rather than inviting 58 stale stamps at once.
    """
    for f in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "*.task.json"))):
        rel = os.path.relpath(f, ROOT)
        spec = json.load(open(f, encoding="utf-8"))   # a malformed spec should fail loudly
        cmd = spec.get("command") or []
        if len(cmd) < 3:
            continue
        body = cmd[2]
        staged = " ".join(o.get("source", "") for o in spec.get("outputs", []))
        logs = set(re.findall(r"2?>\s*([\w.-]+\.log)\b", body))
        missing = sorted(l for l in logs if l not in staged)
        if missing:
            warns.append(f"{rel}: writes {', '.join(missing)} but never stages it out — "
                         f"a failed run leaves no log (fix when the recipe is next run)")
        buffered = len(re.findall(r"^python3 - ", body, re.M))
        if buffered:
            warns.append(f"{rel}: {buffered} python heredoc(s) without -u — buffered stdout is "
                         f"lost if the task is killed (fix when the recipe is next run)")


def check_staging_coverage():
    """Every recipe's inputs must be reachable in a clean account: built by a stage script
    (its own or a sibling's), reused from a sibling recipe's run, or build-in-task (no inputs).
    An `inputs/<p>/` a reader can't build is the un-buildable-fixture bug (the 30x-reads case)."""
    built = set()  # inputs/<prefix>/ that some stage script produces
    for f in glob.glob(os.path.join(ROOT, "recipes", "*", "stage-inputs.sh")):
        for m in re.finditer(r"inputs/([\w.-]+)", open(f, encoding="utf-8").read()):
            built.add(m.group(1))
    recipes = {os.path.basename(os.path.dirname(p))
               for p in glob.glob(os.path.join(ROOT, "recipes", "*", "README.md"))}
    for spec in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "*.task.json"))):
        rel = os.path.relpath(spec, ROOT)
        text = open(spec, encoding="utf-8").read()
        for prefix in {m.group(1) for m in re.finditer(r'"source":\s*"s3://[^/]+/inputs/([\w.-]+)/', text)}:
            if prefix not in built:
                errors.append(f"{rel}: reads inputs/{prefix}/ but no stage script builds it — un-buildable in a clean account (staging)")
        for src in {m.group(1) for m in re.finditer(r'"source":\s*"s3://[^/]+/runs/([\w.-]+)/', text)}:
            if src not in recipes:
                errors.append(f"{rel}: reuses runs/{src}/ but no such recipe (staging)")
        # Naming the right recipe is not enough: the specific object has to be something that
        # recipe actually emits. A chain that borrows a sibling's run output breaks SILENTLY when
        # that sibling's workload moves on and renames it -- measured: converting bwa-samtools to
        # the full genome turned aln.sam into aln.sam.gz and left bowtie2 and minimap2 pointing at
        # a key that no longer exists, which the recipe-name check above waved through.
        for m in re.finditer(r'"source":\s*"s3://[^/]+/(runs/([\w.-]+)/[^"]+)"', text):
            key, src = m.group(1), m.group(2)
            if src not in recipes:
                continue
            produced = set()
            for sib in glob.glob(os.path.join(ROOT, "recipes", src, "*.task.json")):
                for d in re.finditer(r'"destination":\s*"s3://[^/]+/(runs/[^"]+)"',
                                     open(sib, encoding="utf-8").read()):
                    produced.add(d.group(1))
            if produced and key not in produced:
                errors.append(f"{rel}: reads {key} but recipes/{src}/ emits no such output — "
                              f"a broken cross-recipe chain (staging)")


def check_output_collisions():
    """Two specs in one recipe writing the same destination silently overwrite each other, so
    whichever ran last defines what `make ls` shows. Measured: minimap2's short-read method check
    and its HiFi recipe both emitted runs/minimap2/r1/smoke-check.txt, and the method check's
    numbers stood in for the recipe's headline."""
    for rdir in sorted(glob.glob(os.path.join(ROOT, "recipes", "*"))):
        if not os.path.isdir(rdir):
            continue
        seen = {}
        for spec in sorted(glob.glob(os.path.join(rdir, "*.task.json"))):
            for m in re.finditer(r'"destination":\s*"(s3://[^"]+)"', open(spec, encoding="utf-8").read()):
                dest = m.group(1)
                prev = seen.get(dest)
                if prev and prev != spec:
                    errors.append(
                        f"{os.path.relpath(spec, ROOT)}: writes {dest.split('/')[-1]} which "
                        f"{os.path.basename(prev)} also writes — one silently overwrites the other")
                seen[dest] = spec


def check_verified_freshness():
    """`last_verified` is a date only a real verifying run may set. If a spec in that recipe was
    committed on a LATER day, the claim predates the edit and nobody has run what now ships —
    the "verified number silently invalidated by an edit nobody re-ran" harm. Same-day edits do
    not fire, because editing and re-running in one sitting is the normal sequence."""
    # A shallow clone has no per-file history: `git log -1 -- <spec>` then reports the single
    # available commit for every path, so every spec looks changed today and every stamped
    # recipe fails. Warn instead of inventing 57 errors (CI sets fetch-depth: 0).
    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True, cwd=ROOT).stdout.strip()
    if shallow == "true":
        warns.append("shallow clone — skipping last_verified freshness (needs fetch-depth: 0)")
        return
    for rm in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "README.md"))):
        rdir = os.path.dirname(rm)
        rec = os.path.basename(rdir)
        m = re.search(r"^last_verified:\s*(\S+)", open(rm, encoding="utf-8").read(), re.M)
        if not m:
            continue
        lv = m.group(1)
        newest = ""
        for spec in glob.glob(os.path.join(rdir, "*.task.json")):
            out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", spec],
                                 capture_output=True, text=True, cwd=ROOT).stdout.strip()
            if out > newest:
                newest = out
        if newest and newest > lv:
            errors.append(f"recipes/{rec}/README.md: last_verified {lv} but a spec changed "
                          f"{newest} — re-run and restamp, or the page claims a run that "
                          f"never covered what ships")


def check_page_paths_exist():
    """A page naming a concrete bucket path is describing wiring, and wiring drifts. Measured: after
    the bowtie2 chain was repaired, its spec read aln_chr20smoke.sam while its prose still said
    `runs/bwa-samtools/r1/aln.sam` and told readers to get it from a recipe that had moved on to a
    different workload. The wiring checks could not see that -- they read specs, not sentences."""
    for rm in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "README.md"))):
        rdir = os.path.dirname(rm)
        rec = os.path.basename(rdir)
        specs = glob.glob(os.path.join(rdir, "*.task.json"))
        if not specs:
            continue
        blob = " ".join(open(f, encoding="utf-8").read() for f in specs)
        text = open(rm, encoding="utf-8").read()
        for path in sorted(set(re.findall(r"(?:runs|inputs)/[\w.\-]+/[\w./\-]+", text))):
            if path not in blob:
                errors.append(f"recipes/{rec}/README.md: names {path} but no spec in this recipe "
                              f"uses it — prose describing wiring that no longer exists")


# A measurement directory belongs to a recipe when its name says so. These few do not match by
# name, so the mapping is DECLARED rather than inferred -- a measurement spanning two tools is
# owned by both pages, and `bwa-real` belongs to the recipe that pairs bwa with samtools.
MEASUREMENT_OWNERS = {
    "bwa-real": ("bwa-samtools",),
    "blast-diamond": ("blast", "diamond"),
    "mash-sourmash": ("mash", "sourmash"),
}


def measurement_owners(name, recipes):
    """Recipes a measurement dir belongs to, or () if it belongs to no single recipe.

    Cross-recipe instruments (sizing-ratio, simd-width, the lith data-path work) legitimately
    own no recipe, so they resolve to () and are reported only by --inventory. That is why there
    is no hand-maintained allow-list here: "maps to a recipe" is derived, not curated.
    """
    if name in MEASUREMENT_OWNERS:
        return tuple(r for r in MEASUREMENT_OWNERS[name] if r in recipes)
    cand = re.sub(r"-(real|crosscheck)$", "", name)
    for c in (cand, name):
        if c in recipes:
            return (c,)
    return ()


def measurement_state():
    """Map recipe -> (dir, state) for every measurement that belongs to a recipe.

    state is one of:
      linked    -- a write-up exists and the recipe page links it
      unlinked  -- a write-up exists and the page does NOT link it   (ERROR)
      data-only -- result artifacts but no README.md to cite         (WARN)
      empty     -- the directory holds nothing usable
    """
    recipes = {os.path.basename(os.path.dirname(f))
               for f in glob.glob(os.path.join(ROOT, "recipes", "*", "README.md"))}
    out, unowned = {}, []
    for d in sorted(glob.glob(os.path.join(ROOT, "measurements", "*/"))):
        name = os.path.basename(d.rstrip("/"))
        owners = measurement_owners(name, recipes)
        has_writeup = os.path.exists(os.path.join(d, "README.md"))
        has_data = any(f != "README.md" for f in os.listdir(d))
        if not owners:
            unowned.append((name, has_writeup))
            continue
        for r in owners:
            page = os.path.join(ROOT, "recipes", r, "README.md")
            links = ("measurements/" + name) in open(page, encoding="utf-8").read()
            if has_writeup:
                state = "linked" if links else "unlinked"
            elif has_data:
                state = "data-only"
            else:
                state = "empty"
            out.setdefault(r, []).append((name, state))
    return out, unowned


def check_measurement_linkage():
    """A measurement that exists and is not on its recipe page means the page understates what the
    project has already paid to learn, and the next reader re-derives it.

    Measured harm: bowtie2's page told readers to "scale cores to your knee" while
    measurements/bowtie2-real had already established there is no knee through 64 cores. Worse,
    answering "which pages are missing their measurement?" by ad-hoc grep gave three different
    answers (11, then 6, then 4) because it counted DIRECTORIES rather than write-ups. That is
    what this check exists to make deterministic.
    """
    state, unowned = measurement_state()
    # An UNOWNED dir with no write-up is invisible to the per-recipe loop below, and that blind
    # spot is how chembl-crosscheck, msa-crosscheck and openmm-real survived a cleanup that
    # removed ten of their siblings: their recipes are named openbabel-pdbfixer, mafft/muscle and
    # openmm-mdanalysis, so no name derivation reaches them.
    for name, has_writeup in unowned:
        if not has_writeup:
            warns.append(f"measurements/{name}: artifacts, no README.md, and no recipe matches its "
                         f"name — either add it to MEASUREMENT_OWNERS, write it up, or remove it")
    for rec in sorted(state):
        for name, st in state[rec]:
            if st == "unlinked":
                errors.append(f"recipes/{rec}/README.md: measurements/{name} has a write-up the "
                              f"page never cites — link the run, or surface its result if the page omits it too")
            elif st == "data-only":
                warns.append(f"measurements/{name}: artifacts, no README.md — decide write-up or relocate. "
                             f"CHECK recipes/{rec} FIRST: 7 of 8 audited dirs were already fully "
                             f"published on the page, so this is usually a filing question")


# --- measurements/<dir>/record.toml (#118) -------------------------------------------------------
# The record is a second statement of numbers the README already prints, so these checks are what
# stop the two drifting: each record must validate against measurements/record.schema.json, its
# costs must equal wall x rate, every number it carries must be printed in the README or a results
# file it cites, and every upstream ref the README cites must be listed. Any failure is an error.
# A missing record is a warning until the backfill closes.
RECORD_SCHEMA = os.path.join(ROOT, "measurements", "record.schema.json")


def _schema_errors(v, s, where):
    """The subset of JSON Schema record.schema.json uses -- so the schema stays the one statement
    of the format, with no validator dependency."""
    out = []
    if "const" in s and v != s["const"]:
        out.append(f"{where}: must be {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        out.append(f"{where}: {v!r} not one of {s['enum']}")
    t = s.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t and (not isinstance(v, types[t]) or (t in ("integer", "number") and isinstance(v, bool))):
        return out + [f"{where}: expected {t}, got {type(v).__name__}"]
    if "pattern" in s and isinstance(v, str) and not re.search(s["pattern"], v):
        out.append(f"{where}: {v!r} does not match {s['pattern']}")
    if s.get("format") == "date" and isinstance(v, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        out.append(f"{where}: {v!r} is not YYYY-MM-DD")
    if isinstance(v, dict):
        out += [f"{where}: missing '{k}'" for k in s.get("required", []) if k not in v]
        props, extra = s.get("properties", {}), s.get("additionalProperties", True)
        for k, x in v.items():
            if k in props:
                out += _schema_errors(x, props[k], f"{where}.{k}")
            elif extra is False:
                out.append(f"{where}: unknown key '{k}'")
            elif isinstance(extra, dict):
                out += _schema_errors(x, extra, f"{where}.{k}")
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            out.append(f"{where}: needs at least {s['minItems']} item(s)")
        for i, x in enumerate(v):
            out += _schema_errors(x, s.get("items", {}), f"{where}[{i}]")
    if "oneOf" in s and sum(not _schema_errors(v, o, where) for o in s["oneOf"]) != 1:
        out.append(f"{where}: {v!r} matches none of the allowed forms")
    if "anyOf" in s and not any(not _schema_errors(v, o, where) for o in s["anyOf"]):
        out.append(f"{where}: needs one of {[o.get('required') for o in s['anyOf']]}")
    return out


def _norm(s):
    return re.sub(r"[*`,\\]", "", s.replace("−", "-").replace(" ", " ").replace(" ", " "))


_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?:[eE]([-+]?\d+))?(?![\d])")


def _numbers(text):
    """The numbers printed in a text, keyed at their printed precision: {"6.50"} for 6.50, and the
    floats of anything in e-notation."""
    fixed, sci, timed = set(), [], set()
    for m in _NUM.finditer(text):
        if m.group(2):
            sci.append(float(m.group(1) + "e" + m.group(2)))
        else:
            fixed.add(m.group(1))
            if re.match(r"\s?(?:min|minutes|h|hours?|hr)\b", text[m.end():m.end() + 9]):
                timed.add(m.group(1))
    return fixed, sci, timed


def _printed(key, v, nums):
    """Is v printed, at whatever precision the page printed it? A rounding of v counts if it keeps
    two significant figures (so 6.48 matches "6.5" but not "6"); a time may be printed in min or h
    (with the unit after it), memory in MiB."""
    fixed, sci, timed = nums
    vals = [(v, fixed)] + ([(v / 60, timed), (v / 3600, timed)] if key in ("wall_s", "billed_s", "overhead_s") else []) + \
           ([(v * 1024, fixed)] if key == "peak_gib" else [])
    for x, pool in vals:
        for d in range(0, 11):
            f = f"{x:.{d}f}"
            if f in pool and (float(f) == x or len(f.replace(".", "").lstrip("0")) >= 2):
                return True
        if x and any(abs(t - x) <= 0.01 * abs(x) for t in sci):
            return True
    return False


def _text_of(path, cap=256 << 10):
    """A results file's text, or a results dir's report-like files. Data tables (tsv, csv, dat,
    histories) are skipped: thousands of numbers would let any value 'round-trip'."""
    files = [path] if os.path.isfile(path) else \
        sorted(os.path.join(r, f) for r, _, fs in os.walk(path) for f in fs
               if f.endswith((".txt", ".json", ".log", ".out", ".md"))) if os.path.isdir(path) else []
    parts = []
    for f in files:
        if os.path.getsize(f) <= cap:
            try:
                parts.append(open(f, encoding="utf-8").read())
            except (UnicodeDecodeError, OSError):
                pass
    return _norm("\n".join(parts))


def _refs(text):
    """repo#N and github.com/<org>/<repo>/(issues|pull)/N, normalised to repo#N."""
    refs = {m.group(1) + "#" + m.group(2) for m in
            re.finditer(r"github\.com/[\w.-]+/([\w.-]+)/(?:issues|pull)/(\d+)", text)}
    no_urls = re.sub(r"\(?https?://\S+", " ", text)
    refs |= {m.group(1) + "#" + m.group(2) for m in
             re.finditer(r"(?<![\w/.#-])(?:[\w.-]+/)?([A-Za-z][\w.-]*)#(\d+)\b", no_urls)}
    return refs


def check_records(only=None):
    import tomllib
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from gen_catalog import host_facts, check_status
    schema = json.load(open(RECORD_SCHEMA, encoding="utf-8"))
    all_traps, trap_refs, recs = {}, [], {}
    for mdir in sorted(glob.glob(os.path.join(ROOT, "measurements", "*/"))):
        name = os.path.basename(mdir.rstrip("/"))
        rpath = os.path.join(mdir, "record.toml")
        if not os.path.exists(rpath):
            if (not only or name in only) and os.path.exists(os.path.join(mdir, "README.md")):
                warns.append(f"measurements/{name}: no record.toml -- not yet in catalog/record.json (#118)")
            continue
        try:
            rec = tomllib.load(open(rpath, "rb"))
        except tomllib.TOMLDecodeError as e:
            errors.append(f"measurements/{name}/record.toml: not TOML: {e}")
            continue
        recs[name] = rec
        for t in rec.get("trap", []):
            if t.get("id") in all_traps:
                errors.append(f"measurements/{name}/record.toml: trap id '{t['id']}' already owned by {all_traps[t['id']]}")
            all_traps[t.get("id")] = name
        trap_refs += [(name, t) for t in rec.get("trap_refs", [])]
    for name, rec in recs.items():
        if only and name not in only:
            continue
        rel = f"measurements/{name}/record.toml"
        mdir = os.path.join(ROOT, "measurements", name)
        bad = _schema_errors(rec, schema, rel)
        if bad:
            errors.extend(bad)
            continue
        readme = _norm(open(os.path.join(mdir, "README.md"), encoding="utf-8").read())
        rnums, everything = _numbers(readme), None
        rpr = rec.get("results_per_run", 1)
        for i, run in enumerate(rec["run"]):
            tag = f"{rel}: run[{i}] {run['instance']}"
            # 1. arithmetic: a $/result is wall x rate over the result count
            billed_s = run.get("billed_s", run["wall_s"] + run["overhead_s"] if "overhead_s" in run and "wall_s" in run else None)
            for secs, key in ((run.get("wall_s"), "compute_usd"), (billed_s, "billed_usd")):
                if key in run and (secs is None or "usd_hr" not in run):
                    errors.append(f"{tag}: {key} without the seconds and $/hr it was computed from")
                elif key in run:
                    pred = secs * run["usd_hr"] / 3600 / rpr
                    half_ulp = 0.5 * 10 ** decimal.Decimal(repr(run[key])).as_tuple().exponent
                    if abs(pred - run[key]) > max(0.01 * run[key], half_ulp) * 1.0001:
                        errors.append(f"{tag}: {key} {run[key]} != {secs} s x ${run['usd_hr']}/hr / {rpr} = {pred:.5f}")
            # 2. round-trip: every number is printed in the README or in the results the run cites
            for key in ("results", "host"):
                if key in run and not os.path.exists(os.path.join(mdir, run[key])):
                    errors.append(f"{tag}: {key} path {run[key]} does not exist")
            art = _numbers(_text_of(os.path.join(mdir, run["results"])) if "results" in run else "")
            for key in ("wall_s", "billed_s", "overhead_s", "usd_hr", "compute_usd", "billed_usd", "peak_gib"):
                if key in run and not (_printed(key, run[key], rnums) or _printed(key, run[key], art)):
                    errors.append(f"{tag}: {key} = {run[key]} is printed in neither README.md nor {run.get('results', 'a cited results file')}")
            # 3. a host file's facts are read, never re-authored
            if "host" in run and "threads_per_core" in run and \
                    host_facts(os.path.join(mdir, run["host"])).get("threads_per_core") is not None:
                errors.append(f"{tag}: threads_per_core authored although {run['host']} states it")
        for ds in rec.get("dataset", []):
            if "pins" in ds and not os.path.exists(os.path.normpath(os.path.join(mdir, ds["pins"]))) \
                    and not os.path.exists(os.path.join(ROOT, ds["pins"])):
                errors.append(f"{rel}: dataset '{ds['id']}' pins {ds['pins']}, which does not exist")
        ids = set()
        for c in rec["check"]:
            if c["id"] in ids:
                errors.append(f"{rel}: check id '{c['id']}' repeats")
            ids.add(c["id"])
            for key in ("observed", "bound"):
                for n in _NUM.findall(_norm(c.get(key, ""))):
                    n = n[0] + ("e" + n[1] if n[1] else "")
                    if not _printed("", float(n), rnums) and n not in readme:
                        everything = _numbers(_text_of(os.path.join(mdir, "results"))) if everything is None else everything
                        if not _printed("", float(n), everything):
                            errors.append(f"{rel}: check '{c['id']}' {key} number {n} is printed in neither README.md nor results/")
            if check_status(c) == "fail" and not re.search(r"\bFAIL|\bfail", readme):
                errors.append(f"{rel}: check '{c['id']}' records a fail the README never states")
            if c.get("across_chips") in ("fixed-point", "path", "last-bits") and not c.get("across_chips_evidence"):
                errors.append(f"{rel}: check '{c['id']}' is across_chips = {c['across_chips']} with no evidence")
        for x in rec.get("cross_code", []):
            if x["check"] not in ids:
                errors.append(f"{rel}: cross_code with {x['with']} names unknown check '{x['check']}'")
            if not x["with"].startswith("published:") and not os.path.isdir(os.path.join(ROOT, x["with"])):
                errors.append(f"{rel}: cross_code target {x['with']} does not exist")
        # 5. completeness: every upstream ref the README cites is in the record
        listed = {re.sub(r"^.*/", "", u["ref"]) for u in rec.get("upstream", [])}
        for ref in sorted(_refs(open(os.path.join(mdir, "README.md"), encoding="utf-8").read()) - listed):
            errors.append(f"{rel}: README cites {ref}; the record has no upstream entry for it")
    for name, t in trap_refs:
        if t not in all_traps:
            errors.append(f"measurements/{name}/record.toml: trap_refs names '{t}', which no record owns")


def inventory():
    """Print a per-recipe matrix of the contract elements, so "is this recipe complete?" is a
    query rather than a re-scan. Same verdicts the gate enforces -- one implementation."""
    state, unowned = measurement_state()
    rows = []
    for rm in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "README.md"))):
        rec = os.path.basename(os.path.dirname(rm))
        text = open(rm, encoding="utf-8").read()
        rel = os.path.relpath(rm, ROOT)
        st = {}
        before_e, before_w = len(errors), len(warns)
        check_recipe_contract(text, rel, st)
        del errors[before_e:], warns[before_w:]        # inventory reports, it does not accuse
        keys = frontmatter_keys(text)          # a set of key names
        missing = [k for k in REQUIRED_FM
                   if k not in keys and not (k == "image" and ("images" in keys
                       or ("gap_arm64" in keys and "images_x86_64" in keys)))]
        m = re.search(r"^last_verified:\s*(\S+)", text, re.M)
        lv = m.group(1) if m else ""
        if not lv and "gap_arm64" in keys:            # x86-only: its verification is the x86 one
            m = re.search(r"^last_verified_x86_64:\s*(\S+)", text, re.M)
            lv = (m.group(1) + " (x86)") if m else ""
        meas = ",".join(f"{n}:{x}" for n, x in state.get(rec, [])) or "-"
        rows.append((rec, "ok" if not missing else "MISSING:" + ",".join(missing),
                     "".join("." if st.get(k, True) else k for k in ("R2", "R3", "R5", "R6")) or ".",
                     lv or "NEVER", meas))
    w = max(len(r[0]) for r in rows)
    print(f"{'recipe'.ljust(w)}  frontmatter  contract  last_verified  measurement")
    for rec, fmv, con, lv, meas in rows:
        print(f"{rec.ljust(w)}  {fmv:<11}  {con:<8}  {lv:<13}  {meas}")
    # A real gap is a missing required element or a write-up the page never cites. Stray
    # artifacts are a FILING question, counted separately -- conflating them cried wolf on 15
    # recipes when only one had anything unpublished.
    bad = [r for r in rows if r[1] != "ok" or r[2].strip(".") or r[3] == "NEVER"
           or ":unlinked" in r[4]]
    stray = [r for r in rows if ":data-only" in r[4] and r not in bad]
    print(f"\n{len(rows)} recipes | {len(rows) - len(bad)} complete | {len(bad)} with a gap"
          f" | {len(stray)} carrying stray measurement artifacts")
    for r in bad:
        print(f"  GAP    {r[0]}: fm={r[1]} contract={r[2] or 'ok'} verified={r[3]} meas={r[4]}")
    for r in stray:
        print(f"  STRAY  {r[0]}: {r[4]} — check the page before writing anything up")
    if unowned:
        print("\nmeasurements owned by no single recipe (cross-recipe instruments, not a gap):")
        for n, hw in unowned:
            print(f"  {n}{'' if hw else '  (no write-up)'}")


def main():
    if "--inventory" in sys.argv:
        inventory()
        return
    if "--records" in sys.argv:          # just the record checks, for some dirs: --records a-real,b-real
        i = sys.argv.index("--records")
        check_records(set(sys.argv[i + 1].split(",")) if len(sys.argv) > i + 1 else None)
        for m in warns + [f"ERROR {e}" for e in errors]:
            print(m)
        sys.exit(1 if errors else 0)
    ps = pages()
    for path, needs_fm in ps:
        check(path, needs_fm)
    # The generated catalog and the landing docs carry internal links too — and the catalog is the
    # one artifact nobody hand-edits, so it's exactly where a generator bug hides (the copied-lede
    # links). Link-check them with the same pass, so the gate covers what it produces.
    for extra in ("catalog/recipes.md", "README.md", "CHARTER.md"):
        p = os.path.join(ROOT, extra)
        if os.path.exists(p):
            check_internal_links(open(p, encoding="utf-8").read(), p)
    check_portability()
    check_staging_coverage()
    check_output_collisions()
    check_verified_freshness()
    check_page_paths_exist()
    check_diagnostics_survive()
    check_measurement_linkage()
    check_records()
    if "--external" in sys.argv:
        check_external()
    for w in warns:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"\n{len(ps)} pages checked | {len(errors)} errors | {len(warns)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
