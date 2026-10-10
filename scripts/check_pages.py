#!/usr/bin/env python3
"""Lean checks for the cookbook's markdown pages — stdlib only, no deps.

  python3 scripts/check_pages.py              # frontmatter + a11y + internal links (fast, offline)
  python3 scripts/check_pages.py --external   # + external link liveness (network, slower)

Errors (exit 1): a recipe page missing frontmatter or a required field; a broken
internal link. Warnings (exit 0): the markdown-a11y nits. Site-level a11y — colour
contrast, focus order, Atkinson Hyperlegible — is a site-build gate, not this.
"""
import subprocess
import os, re, sys, glob, json, urllib.request

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
    matching the 'lines before <details>' the ceiling was calibrated against)."""
    depth, outside = 0, 0
    for line in text.split("\n"):
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
    return {m.group(1) for m in re.finditer(r"^([a-z_]+):", text[4:end], re.M)}


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
                if k not in keys:
                    errors.append(f"{rel}: frontmatter missing required field '{k}'")
            for k in RECOMMENDED_FM:
                if k not in keys:
                    warns.append(f"{rel}: frontmatter missing recommended field '{k}' (refresh target fills it)")
            if "last_verified" not in keys:
                warns.append(f"{rel}: no last_verified — awaiting a verifying run (a real run stamps it; never backfilled)")
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


def check_portability():
    """The executable path must run in any account. Task specs reference the bucket only as
    ${COOKBOOK_BUCKET} (make run substitutes it); stage scripts take it, not a hardcoded one."""
    for f in sorted(glob.glob(os.path.join(ROOT, "recipes", "*", "*.task.json"))):
        rel = os.path.relpath(f, ROOT)
        text = open(f, encoding="utf-8").read()
        for bucket in {m.group(1) for m in re.finditer(r"s3://([^/\"\s]+)", text)}:
            if bucket != "${COOKBOOK_BUCKET}":
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
                   if k not in keys and not (k == "image" and "images" in keys)]
        m = re.search(r"^last_verified:\s*(\S+)", text, re.M)
        lv = m.group(1) if m else ""
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
