#!/usr/bin/env python3
"""nf-spawn-real: check every engine run against the direct run, and the chips against each other.

    python3 -I compare.py <bucket> <nonce> <results dir>

Reads the bucket (small objects only, streamed: alignments, trees, logs, records) and writes
<results>/<chip>/check.txt, <results>/<chip>/timing.tsv and <results>/compare.txt. Exits 1 if any
assertion fails. Observations are printed with `obs`, assertions with `ok`/`FAIL`.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

B, N, R = sys.argv[1], sys.argv[2], sys.argv[3]
REGION = os.environ.get("AWS_REGION", "us-west-2")
CHIPS = {"arm64": "runs", "x86_64": "runs-x86_64", "amd": "runs-amd"}
TAG = {"arm64": "", "x86_64": "-x8664", "amd": "-amd"}
EXPECT_SEQS, EXPECT_RES = 919, 207712
INPUT = "s3://%s/inputs/mafft-real/pf01255.25_unaligned.fa" % B
INPUT_SHA = "bf393f177f57230d884547f875594cb4f758a0416257e800c0074cd46d926d9f"
# measurements/mafft-real's `mafft --auto --thread 8` on these bytes, which chose FFT-NS-2 (all chips)
MAFFT_REAL_AUTO = "db0911ee5c7a524781ffd8ba0a2074f044283e48d6a53cbeea5fb4b3ea473aa8"
STEPS = ["mafft", "muscle", "tree-mafft", "tree-muscle", "rf"]


def step_of(name):
    """Nextflow's task name -> the direct run's step: 'TREE (mafft)' -> tree-mafft."""
    if name.startswith("TREE"):
        return "tree-" + re.search(r"\((\w+)\)", name).group(1)
    return {"MAFFT": "mafft", "MUSCLE": "muscle", "OBSERVE_RF": "rf"}[name.split(" ")[0]]

ACCT = subprocess.run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                      capture_output=True, text=True, check=True).stdout.strip()
SPAWN = "s3://spawn-results-%s-%s/tasks" % (ACCT, REGION)
_cache = {}


def get(uri, must=True):
    if uri not in _cache:
        p = subprocess.run(["aws", "s3", "cp", "--only-show-errors", "--region", REGION, uri, "-"],
                           capture_output=True)
        _cache[uri] = p.stdout if p.returncode == 0 else None
    if _cache[uri] is None and must:
        raise FileNotFoundError(uri)
    return _cache[uri]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def fasta(b):
    """[(name, sequence)] in file order."""
    recs, name, seq = [], None, []
    for line in b.decode().splitlines():
        if line.startswith(">"):
            if name is not None:
                recs.append((name, "".join(seq)))
            name, seq = line[1:].split()[0], []
        else:
            seq.append(line.strip())
    if name is not None:
        recs.append((name, "".join(seq)))
    return recs


def newick_splits(text):
    """(tip names, set of non-trivial splits as frozensets of the side without the
    alphabetically first tip, so two trees write a split the same way)."""
    s = re.sub(r"\)[^,():;]*", ")", text.strip())       # drop internal labels / supports
    s = re.sub(r":[^,();]+", "", s).rstrip(";")          # drop branch lengths
    stack, clades, tips = [[]], [], []
    tok = re.findall(r"\(|\)|,|[^(),]+", s)
    for t in tok:
        if t == "(":
            stack.append([])
        elif t == ")":
            c = stack.pop()
            clades.append(frozenset(c))
            stack[-1].extend(c)
        elif t == ",":
            continue
        else:
            tips.append(t.strip())
            stack[-1].append(t.strip())
    allt = frozenset(tips)
    ref = min(tips)   # a tip both trees agree on, so a split is written the same way in each
    splits = set()
    for c in clades:
        side = c if ref not in c else allt - c
        if 1 < len(side) < len(allt) - 1:
            splits.add(frozenset(side))
    return tips, splits


def tree_diff(x, y):
    """(bytes identical, same newick once branch lengths are blanked, max |branch-length diff|)."""
    num = rb":([0-9.eE+-]+)"
    shape = re.sub(num, b":", x) == re.sub(num, b":", y)
    d = max((abs(float(p) - float(q)) for p, q in zip(re.findall(num, x), re.findall(num, y))), default=0.0)
    return x == y, shape, d


def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")


def completion(task_id):
    b = get("%s/%s/completion.json" % (SPAWN, task_id), must=False)
    if not b:
        return None
    cj = json.loads(b)
    cj["billed_s"] = (when(cj["ended_at"]) - when(cj["started_at"])).total_seconds()
    log = get("%s/%s/command.log" % (SPAWN, task_id), must=False) or b""
    ph = {}
    for m in re.finditer(rb"spawn: \[([0-9T:\-]+)Z\] (command start|command exit)", log):
        ph[m.group(2).decode()] = when(m.group(1).decode())
    cj["command_s"] = (ph["command exit"] - ph["command start"]).total_seconds() if len(ph) == 2 else None
    return cj


def trace(prefix, run):
    b = get("%s/driver/trace-%d.txt" % (prefix, run), must=False)
    if not b:
        return None
    rows = [l.split("\t") for l in b.decode().splitlines()]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def kv(b):
    d = {}
    for l in b.decode().splitlines():
        f = l.split(None, 1)
        if len(f) == 2:
            d.setdefault(f[0], f[1].strip())
    return d


FAILS = 0
cross = {}


def check_chip(chip):
    global FAILS
    out = []

    def row(kind, what, detail):
        out.append("%-5s %-62s %s" % (kind, what, detail))

    def ok(cond, what, detail):
        global FAILS
        if not bool(cond):
            FAILS += 1
        row("ok" if bool(cond) else "FAIL", what, detail)

    P = "s3://%s/%s/nf-spawn-real/%s" % (B, CHIPS[chip], N)
    t1 = trace(P, 1)
    if t1 is None:
        return None
    # --- run 1: the cold engine run ---------------------------------------------------------
    names = sorted(r["name"] for r in t1)
    ok(len(t1) == 5 and all(r["status"] == "COMPLETED" and r["exit"] == "0" for r in t1),
       "run 1: 5 processes COMPLETED, exit 0 (Nextflow's trace)", ", ".join(names))
    wd = {}
    for r in t1:
        wd[step_of(r["name"])] = r["workdir"].rstrip("/")
    ec = [get(wd[s] + "/.exitcode").strip() for s in STEPS]
    ok(all(e == b"0" for e in ec), "run 1: every work dir's .exitcode object is 0 (the bucket)", " ".join(x.decode() for x in ec))

    ok(sha(get(P + "/engine-run1/mafft/mafft_aln.fa")) == MAFFT_REAL_AUTO,
       "mafft: == mafft-real's `--auto` (FFT-NS-2) output on the same input", MAFFT_REAL_AUTO[:12])
    seqin = get(INPUT)
    ok(sha(seqin) == INPUT_SHA, "input == pinned PF01255.25 (sha256)", INPUT_SHA[:12])
    inrecs = fasta(seqin)
    pub = P + "/engine-run1"
    eng = {
        "mafft_aln.fa": get(pub + "/mafft/mafft_aln.fa"),
        "muscle_aln.fa": get(pub + "/muscle/muscle_aln.fa"),
        "mafft.treefile": get(pub + "/tree/mafft.treefile"),
        "muscle.treefile": get(pub + "/tree/muscle.treefile"),
        "mafft.iqtree": get(pub + "/tree/mafft.iqtree"),
        "muscle.iqtree": get(pub + "/tree/muscle.iqtree"),
        "rf-observation.txt": get(pub + "/rf/rf-observation.txt"),
    }
    D = P + "/direct"
    dirx = {
        "mafft_aln.fa": get(D + "/mafft/mafft_aln.fa"),
        "muscle_aln.fa": get(D + "/muscle/muscle_aln.fa"),
        "mafft.treefile": get(D + "/tree-mafft/mafft.treefile"),
        "muscle.treefile": get(D + "/tree-muscle/muscle.treefile"),
        "mafft.iqtree": get(D + "/tree-mafft/mafft.iqtree"),
        "muscle.iqtree": get(D + "/tree-muscle/muscle.iqtree"),
    }
    # the engine ran exactly the script the direct run ran
    for s in STEPS:
        e, d = get(wd[s] + "/.command.sh"), get("%s/%s/.command.sh" % (D, s))
        # Nextflow's rendering adds one leading and one trailing blank line; nothing else may differ
        ok(e.strip(b"\n") == d.strip(b"\n"), "%s: engine .command.sh == the direct run's script" % s,
           "%d bytes" % len(e))
    # per-stage identities, as each process printed them: the aligners' engine == direct
    for st in STEPS[:2]:
        e, d = get(wd[st] + "/.command.out"), get("%s/%s/.command.out" % (D, st))
        ok(e == d, "%s: engine .command.out == direct (its identity line)" % st, e.decode().strip().splitlines()[-1])
    # alignments: every row is its input, gaps removed, in order; one width
    for a in ("mafft", "muscle"):
        recs = fasta(eng[a + "_aln.fa"])
        rows = [(n, s.replace("-", "").upper()) for n, s in recs]
        # mafft keeps the input order; muscle writes its own (guide-tree) order. Names are unique
        # (staging asserts it), so the sorted rows are the whole claim.
        ok(sorted(rows) == sorted(inrecs) and len({n for n, _ in rows}) == len(rows),
           "%s: every row == its input row, gaps removed (names, residues)" % a,
           "%d rows, %d residues%s" % (len(recs), sum(len(s) for _, s in rows),
                                       ", input order" if rows == inrecs else ", reordered"))
        w = {len(s) for _, s in recs}
        ok(len(w) == 1, "%s: every row the same width" % a, "%s columns" % ",".join(map(str, w)))
        ok(eng[a + "_aln.fa"] == dirx[a + "_aln.fa"], "%s: engine alignment == direct (bytes)" % a,
           sha(eng[a + "_aln.fa"])[:12])
        cross.setdefault(chip, {})[a + "_aln.fa"] = sha(eng[a + "_aln.fa"])
        cross[chip][a + "_cols"] = w.pop() if len(w) == 1 else None
    # trees. Their ML search is a PATH (measured: one Intel pair, same bytes and seed, ended on two
    # different optima), so engine == direct is reported, not asserted; what is asserted is what
    # any correct run must satisfy, on both runs, and that each tree's logL is a fixed point.
    inames = {n for n, _ in inrecs}
    for a in ("mafft", "muscle"):
        for who, t in (("engine", eng), ("direct", dirx)):
            tips, splits = newick_splits(t[a + ".treefile"].decode())
            ok(set(tips) == inames and len(tips) == EXPECT_SEQS and len(splits) == EXPECT_SEQS - 3,
               "tree-%s (%s): the input's 919 names, unrooted binary (n-3 splits)" % (a, who),
               "%d tips, %d splits" % (len(tips), len(splits)))
            cross[chip]["splits-%s-%s" % (who, a)] = splits
        le = re.search(rb"Log-likelihood of the tree: (\S+)", eng[a + ".iqtree"]).group(1).decode()
        ld = re.search(rb"Log-likelihood of the tree: (\S+)", dirx[a + ".iqtree"]).group(1).decode()
        same, shape, dbl = tree_diff(eng[a + ".treefile"], dirx[a + ".treefile"])
        row("path", "tree-%s: engine vs direct (same image, bytes, seed, -T 4)" % a,
            "%s / %s; %s" % (le, ld, "bytes identical" if same else
                             ("same newick, branch lengths differ by <= %.0e" % dbl) if shape else
                             "DIFFERENT trees, RF %d" % len(cross[chip]["splits-engine-" + a] ^ cross[chip]["splits-direct-" + a])))
        cross[chip]["tree-" + a] = sha(eng[a + ".treefile"])
        cross[chip]["logL-" + a] = le
        cross[chip]["splits-" + a] = cross[chip]["splits-engine-" + a]
    ev = get(D + "/eval/eval.txt", must=False)
    if ev:   # 15-eval: both TREE (muscle) results re-optimised on their own topology, twice
        e = {tuple(l.split()[:2]): float(l.split()[2]) for l in ev.decode().splitlines()}
        for who, t in (("engine", eng), ("direct", dirx)):
            l0 = float(re.search(rb"Log-likelihood of the tree: (\S+)", t["muscle.iqtree"]).group(1))
            l1, l2 = e[(who, "pass1")], e[(who, "pass2")]
            ok(abs(l1 - l0) < 0.01 and abs(l2 - l1) < 0.001,
               "tree-muscle (%s): logL a fixed point (re-optimised twice)" % who, "%.4f -> %.4f -> %.4f" % (l0, l1, l2))
    rep = get(D + "/tree-muscle-repeat/muscle.iqtree", must=False)
    if rep:  # 16: the direct TREE (muscle) run again, same everything
        lr = re.search(rb"Log-likelihood of the tree: (\S+)", rep).group(1).decode()
        row("path", "tree-muscle: a second direct run", "logL %s" % lr)
    # the join: iqtree -rf against an independent bipartition count, on each run's own trees
    rf_e = re.search(rb"robinson_foulds_distance\s+(\S+)", eng["rf-observation.txt"]).group(1).decode()
    rf_d = re.search(rb"robinson_foulds_distance\s+(\S+)", get(D + "/rf/rf-observation.txt")).group(1).decode()
    for who, rf in (("engine", rf_e), ("direct", rf_d)):
        n = len(cross[chip]["splits-%s-mafft" % who] ^ cross[chip]["splits-%s-muscle" % who])
        ok(rf == str(n), "rf (%s): iqtree -rf == independent split count |A xor B|" % who, "%s / %d" % (rf, n))
    row("path", "rf: engine vs direct", "%s / %s" % (rf_e, rf_d))
    cross[chip]["rf"] = rf_e
    # --- run 2: identical resume --------------------------------------------------------------
    t2 = trace(P, 2)
    if t2 is not None:
        log2 = get(P + "/driver/nextflow-2.log")
        sub2 = len(re.findall(rb"Submitting task '", log2))
        ok(len(t2) == 5 and all(r["status"] == "CACHED" for r in t2) and sub2 == 0,
           "run 2 (-resume, unchanged): 5 of 5 CACHED, 0 instances launched", "%d cached, %d submits" %
           (sum(r["status"] == "CACHED" for r in t2), sub2))
        ok(get(P + "/engine-run2/rf/rf-observation.txt") == eng["rf-observation.txt"],
           "run 2: published rf-observation.txt == run 1's", "")
    # --- run 3: OBSERVE_RF's image renamed (tag+digest), same bytes ------------------------------
    # Nextflow keys a task on its image only when a container engine is enabled (TaskHasher), and
    # nf-spawn runs Docker itself with none, so a changed image must NOT invalidate anything.
    t3 = trace(P, 3)
    if t3 is not None:
        sub3 = len(re.findall(rb"Submitting task '", get(P + "/driver/nextflow-3.log")))
        ok(len(t3) == 5 and all(r["status"] == "CACHED" for r in t3) and sub3 == 0,
           "run 3 (OBSERVE_RF image renamed): 5 of 5 CACHED -- the image is not in the key",
           "%d cached, %d submits" % (sum(r["status"] == "CACHED" for r in t3), sub3))
    # --- run 4: OBSERVE_RF's cache off --------------------------------------------------------------
    t4 = trace(P, 4)
    if t4 is not None:
        subs = re.findall(rb"Submitting task '([^']+)'", get(P + "/driver/nextflow-4.log"))
        cached = sorted(step_of(r["name"]) for r in t4 if r["status"] == "CACHED")
        ran = sorted(step_of(r["name"]) for r in t4 if r["status"] == "COMPLETED")
        ok(len(cached) == 4 and ran == ["rf"] and [step_of(x.decode()) for x in subs] == ["rf"],
           "run 4 (OBSERVE_RF cache off): only OBSERVE_RF re-runs, 4 CACHED", "ran %s" % ran)
        rrf = [r for r in t4 if step_of(r["name"]) == "rf"]
        w4 = rrf[0]["workdir"].rstrip("/") if rrf else ""
        pulled = re.search(rb"Unable to find image '([^']+)'", get(w4 + "/.command.err", must=False) or b"")
        ok(w4 and w4 != wd["rf"] and pulled and b":nf-spawn-real-edit@" in pulled.group(1),
           "run 4: OBSERVE_RF ran in a new work dir, on the renamed image (docker's pull line)",
           pulled.group(1).decode()[:58] if pulled else "")
        ok(get(P + "/engine-run4/rf/rf-observation.txt") == eng["rf-observation.txt"],
           "run 4: re-run rf-observation.txt == run 1's (bytes), from run 1's cached trees", "")
    # --- timing: engine processes (spawn's records) and the direct steps ---------------------
    tim = []
    for s in STEPS:
        h = wd[s].split("/")[-2] + wd[s].split("/")[-1]
        ce = completion("nf-" + h[:12])
        cd = completion("cookbook-nf-spawn-real-%s%s-%s" % (s, TAG[chip], N))
        m = kv(get("%s/%s/metrics.txt" % (D, s)))
        host = get("%s/%s/host.txt" % (D, s)).decode()
        tpc = re.search(r"threads_per_core (\d+)", host).group(1)
        tim.append([s, ce and ce["billed_s"], ce and ce["command_s"], ce and ce["cost_estimate"],
                    m["tool_wall_s"], m["memory_peak_kib"], m.get("avg_cores_busy"), m.get("Kernel:", ""),
                    cd and cd["billed_s"], cd and cd["cost_estimate"], tpc])
    os.makedirs(os.path.join(R, chip), exist_ok=True)
    with open(os.path.join(R, chip, "timing.tsv"), "w") as f:
        f.write("step\tengine_billed_s\tengine_command_s\tengine_cost\tdirect_tool_wall_s\tdirect_memory_peak_kib"
                "\tdirect_avg_cores\tkernel\tdirect_billed_s\tdirect_cost\tthreads_per_core\n")
        for r in tim:
            f.write("\t".join("" if x is None else str(x) for x in r) + "\n")
    tm = kv(get(P + "/driver/timing-1.txt"))
    row("obs", "run 1 workflow wall (driver)", tm["wall_s"] + " s")
    for k in (2, 3):
        b = get("%s/driver/timing-%d.txt" % (P, k), must=False)
        if b:
            row("obs", "run %d workflow wall (driver)" % k, kv(b)["wall_s"] + " s")
    with open(os.path.join(R, chip, "check.txt"), "w") as f:
        f.write("\n".join(out) + "\n")
    print("== %s" % chip)
    print("\n".join(out))
    return True


for c in CHIPS:
    check_chip(c)

# --- across chips ------------------------------------------------------------------------------
lines = []
have = [c for c in CHIPS if c in cross]
pairs = [(x, y) for x, y in (("x86_64", "arm64"), ("amd", "arm64"), ("amd", "x86_64")) if x in have and y in have]
for c, r in pairs:
    for k in ("mafft_aln.fa", "muscle_aln.fa", "mafft_cols", "muscle_cols", "tree-mafft", "tree-muscle",
              "logL-mafft", "logL-muscle", "rf"):
        a, b = cross[r].get(k), cross[c].get(k)
        lines.append("%-6s vs %-6s  %-14s %-8s (%s / %s)" % (c, r, k, "same" if a == b else "DIFFERS",
                                                         str(a)[:16], str(b)[:16]))
    for a in ("mafft", "muscle"):
        d = len(cross[r]["splits-" + a] ^ cross[c]["splits-" + a])
        lines.append("%-6s vs %-6s  RF(tree-%s)    %d of %d" % (c, r, a, d, 2 * (EXPECT_SEQS - 3)))
with open(os.path.join(R, "compare.txt"), "w") as f:
    f.write("\n".join(lines) + "\n")
print("== across chips")
print("\n".join(lines))
print("fails", FAILS)
sys.exit(1 if FAILS else 0)
