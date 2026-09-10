#!/usr/bin/env python3
"""Lean checks for the cookbook's markdown pages — stdlib only, no deps.

  python3 scripts/check_pages.py              # frontmatter + a11y + internal links (fast, offline)
  python3 scripts/check_pages.py --external   # + external link liveness (network, slower)

Errors (exit 1): a recipe page missing frontmatter or a required field; a broken
internal link. Warnings (exit 0): the markdown-a11y nits. Site-level a11y — colour
contrast, focus order, Atkinson Hyperlegible — is a site-build gate, not this.
"""
import os, re, sys, glob, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The image DIGEST is the authoritative version-of-record; tool_version is a friendly
# label best resolved accurately from the image (conda list), so it's recommended, not required.
REQUIRED_FM = ("tool", "image", "spawn_version")
RECOMMENDED_FM = ("tool_version", "run_date")
BAD_LINK_TEXT = {"here", "click here", "link", "this", "read more"}
errors, warns = [], []


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


def main():
    ps = pages()
    for path, needs_fm in ps:
        check(path, needs_fm)
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
