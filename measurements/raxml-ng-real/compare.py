#!/usr/bin/env python3
"""raxml-ng-real: the checks that need split arithmetic, and the cross-chip comparison.

    python3 -I compare.py <scratch dir>      (holds <chip>/raxml/ and <chip>/iqtree/)

Per chip, asserted: every FBP support on the best tree equals round(100 x the fraction of the
bootstrap replicates that contain that split) -- recomputed here from the replicate trees, an
independent count of what RAxML-NG's --support reports. Across chips, reported: which outputs are
byte-identical, the per-search likelihoods, Robinson-Foulds distance between best trees, and
support differences.
"""
import hashlib
import os
import re
import sys

scr = sys.argv[1]
CHIPS = [c for c in ("arm64", "x86_64", "amd") if os.path.isdir(os.path.join(scr, c, "raxml"))]
fails = []


def parse(nwk):
    """Newick -> (list of tip names, list of (child-index-sets, label) per internal node)."""
    s = nwk.strip().rstrip(";")
    tips, stack, internal = [], [[]], []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "(":
            stack.append([])
            i += 1
        elif c == ")":
            kids = stack.pop()
            members = set().union(*kids)
            m = re.match(r"\)([0-9.eE+-]*)(:[0-9.eE+-]+)?", s[i:])
            internal.append((members, m.group(1)))
            stack[-1].append(members)
            i += m.end()
        elif c == ",":
            i += 1
        else:
            m = re.match(r"([^:,()]+)(:[0-9.eE+-]+)?", s[i:])
            tips.append(m.group(1))
            stack[-1].append({m.group(1)})
            i += m.end()
    return tips, internal


def splits(nwk, labels=False):
    tips, internal = parse(nwk)
    every = frozenset(tips)
    ref = min(tips)
    out = {}
    for members, lab in internal:
        side = frozenset(members)
        if ref in side:
            side = every - side
        if 1 < len(side) < len(tips) - 1:
            out[side] = lab
    return out if labels else set(out)


def rd(chip, sub, name):
    p = os.path.join(scr, chip, sub, name)
    return open(p).read() if os.path.exists(p) else None


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16] if t is not None else "-"


def check(label, ok, detail):
    print("  %-34s %-4s %s" % (label, "ok" if ok else "FAIL", detail))
    if not ok:
        fails.append(label)


best = {}
for c in CHIPS:
    print("== %s: FBP recomputed from the replicate trees" % c)
    bt = rd(c, "raxml", "rx.raxml.bestTree")
    fbp = splits(rd(c, "raxml", "sup.raxml.supportFBP"), labels=True)
    reps = [splits(l) for l in rd(c, "raxml", "bs.raxml.bootstraps").splitlines() if l.strip()]
    n = len(reps)
    off = [(s, lab) for s, lab in fbp.items() if int(lab) != int(100.0 * sum(s in r for r in reps) / n + 0.5)]
    check("fbp == round(100 x fraction)", not off and len(fbp) == len(parse(bt)[0]) - 3,
          "%d of %d supports, %d replicates; %d disagree" % (len(fbp) - len(off), len(fbp), n, len(off)))
    check("support tree topology == best tree", set(fbp) == splits(bt), "same splits")
    # TBE (Lemoine et al. 2018): per replicate 1 - delta/(p-1), delta the transfer distance to the
    # closest bipartition of the replicate (trivial ones included), p the light side. Recomputed here
    # and reported, not asserted: RAxML-NG 2.0.2's --support writes FBP/100 into the TBE file.
    tbe = splits(rd(c, "raxml", "sup.raxml.supportTBE"), labels=True)
    tips = sorted(parse(bt)[0])
    ix = {t: i for i, t in enumerate(tips)}
    nt = len(tips)
    mk = lambda S: sum(1 << ix[t] for t in S)
    rmasks = []
    for l in rd(c, "raxml", "bs.raxml.bootstraps").splitlines():
        if l.strip():
            tt, inn = parse(l)
            rmasks.append([mk(m) for m, _ in inn] + [1 << ix[x] for x in tt])
    pc = lambda x: bin(x).count("1")
    worst, same_as_fbp, nb2, cherry_off = 0.0, 0, 0, 0
    for s, lab in tbe.items():
        b = mk(s)
        p = min(pc(b), nt - pc(b))
        mine = 1 - sum(min(min(pc(b ^ a), nt - pc(b ^ a)) for a in r) for r in rmasks) / len(rmasks) / (p - 1)
        worst = max(worst, abs(mine - float(lab)))
        same_as_fbp += abs(float(lab) * 100 - int(fbp[s])) < 1e-6
        nb2 += p == 2
        cherry_off += p == 2 and abs(mine - float(lab)) > 1e-6
    # On a cherry (p = 2) TBE and FBP coincide by definition, so there the recomputation must match.
    check("tbe recompute == reported on cherries", cherry_off == 0, "%d of %d cherries differ" % (cherry_off, nb2))
    print("  %-34s %-4s %d of %d reported TBE == FBP/100; max |reported - recomputed| %.4f (cherries, where they must agree: %d)"
          % ("tbe recomputed (reported)", "--", same_as_fbp, len(tbe), worst, nb2))
    best[c] = {"tree": bt, "splits": splits(bt), "fbp": fbp,
               "log": rd(c, "raxml", "rx.raxml.log"), "boots": rd(c, "raxml", "bs.raxml.bootstraps")}


def searches(log):
    return [l.split()[-1] for l in log.splitlines() if "ML tree search #" in l]


def final(log):
    m = re.search(r"^Final LogLikelihood: (\S+)", log, re.M)
    return m.group(1) if m else None


best_logs = {"bs": {c: rd(c, "raxml", "bs.raxml.log") for c in CHIPS}}
a = best.get("arm64")
for c in CHIPS:
    b = best[c]
    print("== %s: %s, %d searches, final %s" % (c, sha(b["tree"]), len(searches(b["log"])), final(b["log"])))
if a:
    for c in CHIPS:
        if c == "arm64":
            continue
        b = best[c]
        print("== %s against Graviton4" % c)
        print("  bestTree byte-identical          %s" % (a["tree"] == b["tree"]))
        print("  bootstraps byte-identical        %s" % (a["boots"] == b["boots"]))
        ra = [l for l in a["boots"].splitlines() if l.strip()]
        rb = [l for l in b["boots"].splitlines() if l.strip()]
        la = re.findall(r"Bootstrap tree #(\d+), logLikelihood: (\S+)", best_logs["bs"]["arm64"])
        lb = re.findall(r"Bootstrap tree #(\d+), logLikelihood: (\S+)", best_logs["bs"][c])
        for k, (x, y) in enumerate(zip(ra, rb)):
            if x != y:
                print("  replicate #%-3d differs            same topology %s; logL %s vs %s"
                      % (k + 1, splits(x) == splits(y), la[k][1], lb[k][1]))
        sa, sb = searches(a["log"]), searches(b["log"])
        same = sum(x == y for x, y in zip(sa, sb))
        print("  per-search logL identical        %d of %d/%d" % (same, len(sa), len(sb)))
        if same < min(len(sa), len(sb)):
            k = next(i for i, (x, y) in enumerate(zip(sa, sb)) if x != y)
            print("  first differing search           #%d: %s vs %s" % (k + 1, sa[k], sb[k]))
        print("  final logL                       %s vs %s" % (final(a["log"]), final(b["log"])))
        rf = len(a["splits"] ^ b["splits"])
        print("  RF distance best trees           %d of max %d" % (rf, 2 * (len(parse(a["tree"])[0]) - 3)))
        shared = a["splits"] & b["splits"]
        d = [abs(int(a["fbp"][s]) - int(b["fbp"][s])) for s in shared]
        if d:
            print("  FBP on shared splits             %d shared, max |diff| %d, mean %.2f"
                  % (len(shared), max(d), sum(d) / len(d)))
for c in CHIPS:
    for sub, f in (("iqtree", "crosscheck.txt"),):
        t = rd(c, sub, f)
        if t:
            print("== %s %s\n%s" % (c, sub, t.rstrip()))
print("COMPARE FAILED: " + ", ".join(fails) if fails else "COMPARE OK")
sys.exit(1 if fails else 0)
