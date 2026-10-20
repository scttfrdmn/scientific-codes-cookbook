"""viennarna-real: fold every seed sequence of Rfam 15.1 with RNAfold -p and check the output.

    python3 -u real.py prep  <Rfam.seed.gz> <outdir>          seed.fa + ref.tsv from the release
                                                              (#=GC SS_cons; SS_cons_1.. alternatives ignored)
    python3 -u real.py fold  <threads> <outdir>               RNAfold -p over seed.fa, twice, timed;
                                                              non-numeric records refolded at -j1
    python3 -u real.py fold1 <nrec> <outdir>                  first nrec records again at -j1
    python3 -u real.py check <threads> <outdir>               every record of fold.out
    python3 -u real.py enum  <threads> <outdir>               exhaustive enumeration, <= 30 nt

Runs on aarch.bio's arm64 image (Python 3.13) and bioconda's x86 one (Python 3.10). Every check
appends one line to <outdir>/checks.txt; a FAIL line makes the subcommand exit 1.
"""
import gzip
import math
import multiprocessing as mp
import os
import re
import resource
import subprocess
import sys
import time

import RNA

CANON = {"AU", "UA", "GC", "CG", "GU", "UG"}
ENUM_MAX = 30          # longest sequence enumerated exhaustively (3.2M structures at 30 nt)
TOL_G = 1e-9           # kcal/mol: enumeration vs McCaskill, double sums of <= 3.2M terms
TOL_P = 1e-9           # base-pair probability, same reasoning
OUT = None


def row(ok, name, detail):
    line = "%-4s %-58s %s" % ("ok" if ok else "FAIL", name, detail)
    print(line, flush=True)
    with open(os.path.join(OUT, "checks.txt"), "a") as f:
        f.write(line + "\n")
    return bool(ok)


def summary(key, value):
    with open(os.path.join(OUT, "summary.txt"), "a") as f:
        f.write("%s\t%s\n" % (key, value))
    print("%s\t%s" % (key, value), flush=True)


def pairs_of(st):
    """Pair list (i, j), 0-based, of a dot-bracket string; None if unbalanced."""
    stack, out = [], []
    for k, c in enumerate(st):
        if c == "(":
            stack.append(k)
        elif c == ")":
            if not stack:
                return None
            out.append((stack.pop(), k))
    return None if stack else out


def to_db(n, pairs):
    s = ["."] * n
    for i, j in pairs:
        s[i], s[j] = "(", ")"
    return "".join(s)


def dcal(x):
    return int(round(x * 100))


MAXLOOP = 30   # ViennaRNA's MAXLOOP: the MFE recursion never opens an interior loop larger than this


def max_interior(n, pairs):
    """Largest interior loop (unpaired residues between a pair and the single pair inside it).
    A structure whose value exceeds MAXLOOP lies outside the space RNAfold's MFE minimises over."""
    pt = [-1] * n
    for i, j in pairs:
        pt[i], pt[j] = j, i
    worst = 0
    for i, j in pairs:
        inner, k = [], i + 1
        while k < j and len(inner) < 2:
            if pt[k] > k:
                inner.append((k, pt[k]))
                k = pt[k] + 1
            else:
                k += 1
        if len(inner) == 1:
            a, b = inner[0]
            worst = max(worst, (a - i - 1) + (j - b - 1))
    return worst


# ------------------------------------------------------------------------------------- prep
def prep(seed_gz):
    """Rfam Stockholm -> seed.fa (ungapped) and ref.tsv (SS_cons mapped onto each sequence).

    The reference keeps a consensus pair (columns c1 < c2 joined by <> () [] {}) on a sequence
    only where both columns hold a residue, the two residues form a canonical pair, and at least
    three residues lie between them: exactly the structures RNAfold's search space contains.
    Pseudoknot letters (Aa, Bb, ...) are dropped for the same reason.
    """
    opens, closes = "<([{", ">)]}"
    fams = 0
    ids = set()
    stats = dict(seqs=0, nt=0, cons_pairs=0, mapped=0, gapped=0, noncanon=0, short=0, pk=0)
    fa = open(os.path.join(OUT, "seed.fa"), "w")
    ref = open(os.path.join(OUT, "ref.tsv"), "w")
    acc, ss, order, aln = None, "", [], {}
    bad = []
    with gzip.open(seed_gz, "rt", encoding="latin-1") as f:
        for line in f:
            if line.startswith("# STOCKHOLM"):
                acc, ss, order, aln = None, "", [], {}
            elif line.startswith("#=GF AC"):
                acc = line.split()[2]
            elif line.split()[:2] == ["#=GC", "SS_cons"]:
                ss += line.split()[2]
            elif line.startswith("//"):
                fams += 1
                width = len(ss)
                if not acc or not width or any(len(aln[n]) != width for n in order):
                    bad.append(acc)
                    continue
                stack = {o: [] for o in opens}
                cpairs = []
                for c, ch in enumerate(ss):
                    if ch in opens:
                        stack[ch].append(c)
                    elif ch in closes:
                        cpairs.append((stack[opens[closes.index(ch)]].pop(), c))
                    elif ch.isalpha():
                        stats["pk"] += 1
                if any(stack[o] for o in opens):
                    bad.append(acc)
                    continue
                stats["cons_pairs"] += len(cpairs)
                for n in order:
                    a = aln[n].upper().replace("T", "U")
                    pos, k = [], 0
                    for ch in a:
                        if ch in ".-~":
                            pos.append(-1)
                        else:
                            pos.append(k)
                            k += 1
                    seq = "".join(ch for ch in a if ch not in ".-~")
                    keep = []
                    for c1, c2 in cpairs:
                        i, j = pos[c1], pos[c2]
                        if i < 0 or j < 0:
                            stats["gapped"] += 1
                        elif seq[i] + seq[j] not in CANON:
                            stats["noncanon"] += 1
                        elif j - i - 1 < 3:
                            stats["short"] += 1
                        else:
                            keep.append((i, j))
                    stats["mapped"] += len(keep)
                    rid = "%s|%s" % (acc, n)
                    if rid in ids:
                        bad.append(rid)
                    ids.add(rid)
                    fa.write(">%s\n%s\n" % (rid, seq))
                    ref.write("%s\t%s\t%d\t%s\n" % (rid, acc, len(seq), to_db(len(seq), sorted(keep))))
                    stats["seqs"] += 1
                    stats["nt"] += len(seq)
            elif line.strip() and not line.startswith("#"):
                name, s = line.split()
                if name not in aln:
                    aln[name] = ""
                    order.append(name)
                aln[name] += s
    fa.close()
    ref.close()
    ok = True
    ok &= row(fams == 4227, "families in Rfam.seed == 4227 (the 15.1 README)", "%d" % fams)
    ok &= row(not bad, "every family: SS_cons, equal widths, balanced; ids unique", "%d bad %s" % (len(bad), bad[:3]))
    for k, v in stats.items():
        summary("seed_" + k, v)
    return ok


def read_fa(path):
    out, h = [], None
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith(">"):
            h = line[1:]
        elif h is not None:
            out.append((h, line))
            h = None
    return out


# ------------------------------------------------------------------------------------- fold
def fold(threads, args=(), src="seed.fa", dst="fold.out"):
    """RNAfold -p over src. Returns rc, wall, peak RSS of any child so far (GiB), CPU seconds."""
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.time()
    with open(os.path.join(OUT, src)) as fi, open(os.path.join(OUT, dst), "w") as fo, \
            open(os.path.join(OUT, dst + ".err"), "w") as fe:
        rc = subprocess.call(["RNAfold", "-p", "--noPS", "--noDP", "-j%d" % threads] + list(args),
                             stdin=fi, stdout=fo, stderr=fe)
    wall = time.time() - t0
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (ru.ru_utime + ru.ru_stime) - (ru0.ru_utime + ru0.ru_stime)
    return rc, wall, ru.ru_maxrss / 1048576.0, cpu


def numeric(rec):
    """A record whose MFE, ensemble, centroid and frequency lines all parse with finite values.
    RNAfold prints ` [100000.00]` and `frequency ... inf` when its partition function overflows."""
    if len(rec) != 6:
        return False
    m, p, c, q = RE_MFE.match(rec[2]), RE_PF.match(rec[3]), RE_CEN.match(rec[4]), RE_FRQ.match(rec[5])
    if not (m and p and c and q):
        return False
    f = float(q.group(1))
    return math.isfinite(f) and f > 0 and abs(float(p.group(2))) < 100000


RE_MFE = re.compile(r"^([.()]+) \(\s*(-?\d+\.\d\d)\)$")
RE_PF = re.compile(r"^(\S+) \[\s*(-?\d+\.\d\d)\]$")
RE_CEN = re.compile(r"^([.()]+) \{\s*(-?\d+\.\d\d) d=(\d+\.\d\d)\}$")
RE_FRQ = re.compile(r"^ frequency of mfe structure in ensemble (\S+); ensemble diversity (\d+\.\d\d)\s*$")


def records(path):
    """fold.out as 6-line records; a malformed record is returned as its raw lines."""
    with open(path) as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for k in range(0, len(lines), 6):
        yield lines[k:k + 6]


# ------------------------------------------------------------------------------------ check
def check_one(arg):
    rec, rid, seq, refst = arg
    md = RNA.md()
    r = dict(id=rid, n=len(seq), err=[])
    if len(rec) != 6 or rec[0] != ">" + rid or rec[1] != seq:
        r["err"].append("header/sequence")
        return r
    m, p, c, q = RE_MFE.match(rec[2]), RE_PF.match(rec[3]), RE_CEN.match(rec[4]), RE_FRQ.match(rec[5])
    if not (m and p and c and q):
        r["err"].append("format")
        return r
    mfe_st, e_mfe = m.group(1), dcal(float(m.group(2)))
    g, e_cen, dist = dcal(float(p.group(2))), dcal(float(c.group(2))), float(c.group(3))
    cen_st = c.group(1)
    freq, div = float(q.group(1)), float(q.group(2))
    fc = RNA.fold_compound(seq, md, RNA.OPTION_EVAL_ONLY)
    for name, st in (("mfe", mfe_st), ("centroid", cen_st)):
        pp = pairs_of(st)
        if pp is None or len(st) != len(seq):
            r["err"].append(name + " unbalanced")
            return r
        for i, j in pp:
            if seq[i] + seq[j] not in CANON and set(seq[i] + seq[j]) <= set("ACGU"):
                r["err"].append(name + " noncanonical")
            if j - i - 1 < 3:
                r["err"].append(name + " hairpin<3")
        r[name + "_pairs"] = set(pp)
    if dcal(fc.eval_structure(mfe_st)) != e_mfe:
        r["err"].append("eval(mfe) != printed mfe")
    if dcal(fc.eval_structure(cen_st)) != e_cen:
        r["err"].append("eval(centroid) != printed")
    if not g <= e_mfe:
        r["err"].append("G > mfe")
    # The MFE is the minimum over structures with interior loops <= MAXLOOP; the centroid and the
    # Rfam structure are not confined to that space, so the bound is asserted only inside it, and
    # every structure below the MFE must be one with a larger interior loop.
    cen_out = max_interior(len(seq), r["centroid_pairs"]) > MAXLOOP
    if not cen_out and not e_cen >= e_mfe:
        r["err"].append("in-space centroid below mfe")
    if cen_out and e_cen < e_mfe:
        r["cen_below"] = 1
    kT = (37.0 + 273.15) * 1.98717 / 1000.0
    # freq = exp(-(E-G)/kT); E and G printed to 0.01, freq to 6 significant digits
    if not (freq > 0 and abs(math.log(freq) + (e_mfe - g) / 100.0 / kT) <= 0.01 / kT + 1e-5):
        r["err"].append("frequency inconsistent with E, G")
    ref_pairs = set(pairs_of(refst))
    e_ref = dcal(fc.eval_structure(refst))
    ref_out = max_interior(len(seq), ref_pairs) > MAXLOOP
    if not ref_out and not e_ref >= e_mfe:
        r["err"].append("in-space Rfam structure below the mfe")
    if ref_out and e_ref < e_mfe:
        r["ref_below"] = 1
    r.update(cen_out=int(cen_out), ref_out=int(ref_out),e_mfe=e_mfe, g=g, e_cen=e_cen, e_ref=e_ref, dist=dist, div=div, ref=len(ref_pairs),
             np_mfe=len(r["mfe_pairs"]), np_cen=len(r["centroid_pairs"]),
             tp_mfe=len(ref_pairs & r["mfe_pairs"]), tp_cen=len(ref_pairs & r["centroid_pairs"]))
    del r["mfe_pairs"], r["centroid_pairs"]
    return r


def check(threads):
    seqs = read_fa(os.path.join(OUT, "seed.fa"))
    refs = [line.rstrip("\n").split("\t") for line in open(os.path.join(OUT, "ref.tsv"))]
    recs = list(records(os.path.join(OUT, "fold.out")))
    # records the fold phase found non-numeric (partition-function overflow) are checked from their
    # serial refold instead; that they were refolded is reported, not hidden
    refold = {rec[0]: rec for rec in records(os.path.join(OUT, "refold.out"))}
    recs = [refold.get(rec[0], rec) for rec in recs]
    ok = row(len(recs) == len(seqs) == len(refs), "fold.out records == input sequences",
             "%d / %d" % (len(recs), len(seqs)))
    ok &= row(all(a[0] == b[0] for a, b in zip(seqs, refs)), "seed.fa and ref.tsv in step", "")
    work = [(rec, rid, seq, rf[3]) for rec, (rid, seq), rf in zip(recs, seqs, refs)]
    with mp.Pool(threads) as pool:
        res = pool.map(check_one, work, chunksize=64)
    errs = {}
    for r in res:
        for e in r["err"]:
            errs.setdefault(e, []).append(r["id"])
    for name in ("header/sequence", "format", "mfe unbalanced", "centroid unbalanced",
                 "mfe noncanonical", "centroid noncanonical", "mfe hairpin<3", "centroid hairpin<3",
                 "eval(mfe) != printed mfe", "eval(centroid) != printed", "G > mfe",
                 "in-space centroid below mfe", "frequency inconsistent with E, G",
                 "in-space Rfam structure below the mfe"):
        bad = errs.pop(name, [])
        ok &= row(not bad, "every record: not " + name, "%d of %d %s" % (len(bad), len(res), bad[:3]))
    ok &= row(not errs, "no other error", str(list(errs))[:200])
    good = [r for r in res if not r["err"]]
    for k in ("cen_out", "ref_out", "cen_below", "ref_below"):
        summary("loops_gt30_" + k, sum(r.get(k, 0) for r in good))
    summary("refolded_serially", len(refold))
    with gzip.open(os.path.join(OUT, "records.tsv.gz"), "wt") as f:
        cols = ["id", "n", "e_mfe", "g", "e_cen", "e_ref", "dist", "div", "ref", "np_mfe", "np_cen", "tp_mfe", "tp_cen"]
        f.write("\t".join(cols) + "\n")
        for r in good:
            f.write("\t".join(str(r[c]) for c in cols) + "\n")
    # agreement with Rfam's curated structure: an observation, not an assertion
    R = sum(r["ref"] for r in good)
    for kind in ("mfe", "cen"):
        tp, npred = sum(r["tp_" + kind] for r in good), sum(r["np_" + kind] for r in good)
        summary("pairs_%s_sensitivity" % kind, "%.6f" % (tp / R))
        summary("pairs_%s_ppv" % kind, "%.6f" % (tp / npred))
    f1 = sorted(2.0 * r["tp_mfe"] / (r["ref"] + r["np_mfe"]) for r in good if r["ref"] + r["np_mfe"] > 0)
    summary("f1_mfe_median", "%.4f" % f1[len(f1) // 2])
    summary("rfam_structure_is_mfe", sum(1 for r in good if r["e_ref"] == r["e_mfe"]))
    summary("ref_pairs_total", R)
    summary("records_checked", len(good))
    summary("mfe_dcal_sum", sum(r["e_mfe"] for r in good))
    summary("g_dcal_sum", sum(r["g"] for r in good))
    return ok


# ------------------------------------------------------------------------------------- enum
def count_structures(seq):
    """Number of nested structures with canonical pairs and >= 3 unpaired in every hairpin.
    Independent of ViennaRNA: a plain counting recursion."""
    n = len(seq)
    C = [[1] * (n + 2) for _ in range(n + 2)]
    for length in range(1, n + 1):
        for i in range(0, n - length + 1):
            j = i + length - 1
            c = C[i + 1][j]
            for k in range(i + 4, j + 1):
                if seq[i] + seq[k] in CANON:
                    c += C[i + 1][k - 1] * C[k + 1][j]
            C[i][j] = c
    return C[0][n - 1]


def enum_one(arg):
    rid, seq, printed_g = arg
    n = len(seq)
    md = RNA.md()
    md.uniq_ML = 1
    md.pf_smooth = 0
    fc = RNA.fold_compound(seq, md)
    mfe_st, mfe = fc.mfe()
    mfe = dcal(mfe)
    fc.exp_params_rescale(mfe / 100.0)
    fc.pf()
    kT = fc.exp_params.kT / 1000.0
    g_pf = mfe / 100.0 + kT * math.log(fc.pr_energy(mfe / 100.0))
    bpp = fc.bpp()
    acc = {"z": 0.0, "n": 0, "min": None, "seen": set(), "p": {}, "mfe_seen": False}

    def cb(st, e, d):
        if st is None:
            return
        e = dcal(e)
        w = math.exp(-(e - mfe) / 100.0 / kT)
        d["z"] += w
        d["n"] += 1
        d["seen"].add(st)
        if d["min"] is None or e < d["min"]:
            d["min"] = e
        if st == mfe_st:
            d["mfe_seen"] = True
        stack = []
        p = d["p"]
        for k, c in enumerate(st):
            if c == "(":
                stack.append(k)
            elif c == ")":
                key = (stack.pop(), k)
                p[key] = p.get(key, 0.0) + w

    fc.subopt_cb(100000, cb, acc)
    g_en = mfe / 100.0 - kT * math.log(acc["z"])
    dp = 0.0
    for i in range(1, n + 1):
        for j in range(i + 1, n + 1):
            dp = max(dp, abs(bpp[i][j] - acc["p"].get((i - 1, j - 1), 0.0) / acc["z"]))
    # the same sequence with RNAfold's defaults (pf_smooth on): what the CLI printed
    md2 = RNA.md()
    fc2 = RNA.fold_compound(seq, md2)
    fc2.exp_params_rescale(mfe / 100.0)
    fc2.pf()
    g_smooth = mfe / 100.0 + kT * math.log(fc2.pr_energy(mfe / 100.0))
    return dict(id=rid, n=n, count=acc["n"], distinct=len(acc["seen"]), dp_count=count_structures(seq),
                min_ok=acc["min"] == mfe and acc["mfe_seen"], dg=abs(g_en - g_pf), dp=dp,
                dsmooth=g_smooth - g_en, dprinted=abs(g_smooth - printed_g))


def enum(threads):
    seqs = read_fa(os.path.join(OUT, "seed.fa"))
    printed = {}
    for rec in records(os.path.join(OUT, "fold.out")):
        if numeric(rec):
            printed[rec[0][1:]] = float(RE_PF.match(rec[3]).group(2))
    short = [(h, s) for h, s in seqs if len(s) <= ENUM_MAX]
    work = [(h, s, printed[h]) for h, s in short if set(s) <= set("ACGU") and h in printed]
    work.sort(key=lambda w: -len(w[1]))
    summary("enum_short_records", len(short))
    summary("enum_iupac_excluded", len(short) - len(work))
    with mp.Pool(threads) as pool:
        res = list(pool.imap_unordered(enum_one, work, chunksize=1))
    res.sort(key=lambda r: r["id"])
    ok = row(len(res) == len(work) and len(res) > 1000, "enumerated records", "%d" % len(res))
    if not res:
        return False
    bad = [r["id"] for r in res if not (r["count"] == r["distinct"] == r["dp_count"])]
    ok &= row(not bad, "subopt: every structure, once (== independent count)", "%d bad %s" % (len(bad), bad[:3]))
    bad = [r["id"] for r in res if not r["min_ok"]]
    ok &= row(not bad, "subopt: lowest energy and its structure == mfe", "%d bad" % len(bad))
    w = max(res, key=lambda r: r["dg"])
    ok &= row(w["dg"] <= TOL_G, "McCaskill G == -kT ln sum exp(-E/kT), pf_smooth=0",
              "max |dG| %.3e kcal/mol (%s)" % (w["dg"], w["id"]))
    w = max(res, key=lambda r: r["dp"])
    ok &= row(w["dp"] <= TOL_P, "McCaskill bpp == enumerated pair probabilities",
              "max |dp| %.3e (%s)" % (w["dp"], w["id"]))
    w = max(res, key=lambda r: r["dprinted"])
    ok &= row(w["dprinted"] <= 0.005 + 1e-5, "RNAfold's printed G == library G to its 2 decimals",
              "max %.4f" % w["dprinted"])
    summary("enum_structures", sum(r["count"] for r in res))
    summary("enum_max_structures", max(r["count"] for r in res))
    ds = [r["dsmooth"] for r in res]
    summary("enum_smoothing_dG_min_max", "%.6f %.6f" % (min(ds), max(ds)))
    summary("enum_smoothing_nonzero", sum(1 for d in ds if abs(d) > 1e-9))
    with open(os.path.join(OUT, "enum.tsv"), "w") as f:
        f.write("id\tn\tstructures\tdG_pf_enum\tdp_max\tdG_smooth\n")
        for r in res:
            f.write("%s\t%d\t%d\t%.3e\t%.3e\t%.6f\n" % (r["id"], r["n"], r["count"], r["dg"], r["dp"], r["dsmooth"]))
    return ok


def main():
    global OUT
    cmd = sys.argv[1]
    OUT = sys.argv[-1]
    ok = True
    t0 = time.time()
    if cmd == "prep":
        ok = prep(sys.argv[2])
    elif cmd == "fold":
        T = int(sys.argv[2])
        runs = {}
        for k, dst in ((1, "fold.out"), (2, "fold2.out")):
            rc, wall, rss, cpu = fold(T, dst=dst)
            ok &= row(rc == 0, "RNAfold -p -j%d run %d exit 0" % (T, k), "rc %d" % rc)
            summary("fold%d_wall_s" % k, "%.1f" % wall)
            summary("fold%d_maxrss_gib" % k, "%.3f" % rss)
            summary("fold%d_cpu_s" % k, "%.1f" % cpu)
            summary("fold%d_avg_cores" % k, "%.2f" % (cpu / wall))
            err = open(os.path.join(OUT, dst + ".err")).read()
            summary("fold%d_warn_pf_scale_too_large" % k, err.count("pf_scale too large"))
            summary("fold%d_warn_overflow" % k, err.count("overflow while computing partition function"))
            runs[k] = {rec[0]: rec for rec in records(os.path.join(OUT, dst))}
            bad = [h for h, rec in runs[k].items() if not numeric(rec)]
            summary("fold%d_non_numeric" % k, "%d %s" % (len(bad), " ".join(bad)))
        bad = set(h for k in runs for h, rec in runs[k].items() if not numeric(rec))
        differ = [h for h in runs[1] if runs[1][h] != runs[2].get(h)]
        summary("fold_runs_differ", "%d %s" % (len(differ), " ".join(differ[:20])))
        ok &= row(set(differ) <= bad, "two -j%d runs agree on every record both computed" % T,
                  "%d differ, %d of them non-numeric in a run" % (len(differ), len(set(differ) & bad)))
        with open(os.path.join(OUT, "refold.fa"), "w") as f:
            for h in sorted(bad):
                f.write("%s\n%s\n" % (h, runs[1][h][1]))
        rc, wall = 0, 0.0
        if bad:
            rc, wall, _, _ = fold(1, src="refold.fa", dst="refold.out")
        else:
            open(os.path.join(OUT, "refold.out"), "w").close()
        again = list(records(os.path.join(OUT, "refold.out")))
        ok &= row(rc == 0 and len(again) == len(bad) and all(numeric(r) for r in again),
                  "every non-numeric record refolds cleanly at -j1", "%d records, %.1f s" % (len(bad), wall))
        for rec in again:
            same = [k for k in runs if runs[k][rec[0]] == rec]
            summary("refold " + rec[0], "== run %s" % (",".join(map(str, same)) or "neither"))
    elif cmd == "fold1":
        nrec = int(sys.argv[2])
        with open(os.path.join(OUT, "seed.fa")) as f, open(os.path.join(OUT, "head.fa"), "w") as g:
            for k, line in enumerate(f):
                if k >= 2 * nrec:
                    break
                g.write(line)
        rc, wall, _, _ = fold(1, src="head.fa", dst="head.out")
        a = open(os.path.join(OUT, "head.out")).read()
        with open(os.path.join(OUT, "fold.out")) as f:
            b = "".join(f.readline() for _ in range(6 * nrec))
        ok = row(rc == 0 and a == b, "first %d records: -j1 output == -j<threads> output" % nrec,
                 "%d bytes, %.1f s" % (len(a), wall))
    elif cmd == "check":
        ok = check(int(sys.argv[2]))
    elif cmd == "enum":
        ok = enum(int(sys.argv[2]))
    summary("phase_%s_s" % cmd, "%.1f" % (time.time() - t0))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
