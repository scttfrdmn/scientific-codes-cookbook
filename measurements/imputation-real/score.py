"""Score Beagle and Minimac4 against the held-out truth, genotype by genotype.

    python3 -u -I score.py truth.vcf.gz beagle.vcf.gz mm4.vcf.gz ref.sites.tsv typed.pos <procs> <outdir>

The three VCFs are read in lockstep, one panel-site chunk per process (bcftools query -r), and
every record key must match the panel's key at that line. Dosages are summed as integer
thousandths, so every total is exact and independent of how the chunks are split.

Masked = a panel site not on the array (the tools never saw any target genotype there).
Typed  = an array site (the tools were given the genotype). Bins are panel minor-allele frequency.

Writes <outdir>/scores.tsv (tool, bin, metric, value) and <outdir>/score.checks.tsv; exits 1 if
any check fails.
"""
import subprocess
import sys
from multiprocessing import Pool

TRUTH, BEAGLE, MM4, SITES, TYPED, PROCS, OUT = sys.argv[1:8]
PROCS = int(PROCS)
BINS = [(0.0, 0.001), (0.001, 0.005), (0.005, 0.01), (0.01, 0.05), (0.05, 0.5000001)]
BIN_NAMES = ["maf<0.1%", "0.1-0.5%", "0.5-1%", "1-5%", ">=5%"]
GT = {"0|0": 0, "0|1": 1, "1|0": 1, "1|1": 2, "0/0": 0, "0/1": 1, "1/0": 1, "1/1": 2}
PHASED = {"0|0", "0|1", "1|0", "1|1"}
TOOLS = ("beagle", "minimac4")
FIELDS = ("n", "conc", "nr_n", "nr_ok", "sx", "sy", "sxx", "syy", "sxy", "sites", "r2est_sum")


def binof(maf):
    for i, (lo, hi) in enumerate(BINS):
        if lo <= maf < hi:
            return i
    raise ValueError(maf)


def query(path, fmt, region):
    p = subprocess.Popen(["bcftools", "query", "-r", region, "-f", fmt, path], stdout=subprocess.PIPE, text=True,
                         bufsize=1 << 20)
    return p


def ds_int(s, cache={}):
    v = cache.get(s)
    if v is None:
        v = cache[s] = int(round(float(s) * 1000)) if s != "." else None
    return v


def new_acc():
    return {
        "masked": {t: [dict.fromkeys(FIELDS, 0) for _ in BINS] for t in TOOLS},
        "typed": {t: {"n": 0, "changed": 0} for t in TOOLS},
        "cross": [{"n": 0, "agree": 0, "both_wrong": 0, "b_wrong": 0, "m_wrong": 0,
                   "sx": 0, "sy": 0, "sxx": 0, "syy": 0, "sxy": 0} for _ in BINS],
        "holes": {t: 0 for t in TOOLS}, "ds_oob": {t: 0 for t in TOOLS},
        "af_maxdiff": {t: 0.0 for t in TOOLS},
        "lines": 0, "key_mismatch": 0, "truth_bad": 0, "short": 0,
    }


def chunk(job):
    region, keys, mafs, typed = job
    a = new_acc()
    fmt_t = "%CHROM:%POS:%REF:%ALT[\t%GT]\n"
    fmt_b = "%CHROM:%POS:%REF:%ALT\t%INFO/AF\t%INFO/DR2[\t%GT][\t%DS]\n"
    fmt_m = "%CHROM:%POS:%REF:%ALT\t%INFO/AF\t%INFO/R2[\t%GT][\t%DS]\n"
    ps = [query(TRUTH, fmt_t, region), query(BEAGLE, fmt_b, region), query(MM4, fmt_m, region)]
    it = iter(zip(keys, mafs, typed))
    for lt, lb, lm in zip(ps[0].stdout, ps[1].stdout, ps[2].stdout):
        try:
            key, maf, is_typed = next(it)
        except StopIteration:
            a["key_mismatch"] += 1
            break
        a["lines"] += 1
        ft = lt.rstrip("\n").split("\t")
        fb = lb.rstrip("\n").split("\t")
        fm = lm.rstrip("\n").split("\t")
        if not (ft[0] == fb[0] == fm[0] == key):
            a["key_mismatch"] += 1
            continue
        tv = ft[1:]
        n = len(tv)
        for g in tv:
            if g not in PHASED:
                a["truth_bad"] += 1
        truth = [GT.get(g, -9) for g in tv]
        calls = {}
        for tool, f in (("beagle", fb), ("minimac4", fm)):
            gts = f[3:3 + n]
            dss = f[3 + n:3 + 2 * n]
            if len(gts) != n or len(dss) != n:
                a["short"] += 1
                calls[tool] = None
                continue
            g = [GT.get(x, -1) for x in gts]
            a["holes"][tool] += g.count(-1)
            calls[tool] = (g, dss, f[1], f[2])
        if calls["beagle"] is None or calls["minimac4"] is None:
            continue
        if is_typed:
            for tool in TOOLS:
                g = calls[tool][0]
                ty = a["typed"][tool]
                ty["n"] += n
                ty["changed"] += sum(1 for x, y in zip(g, truth) if x != y)
            continue
        bi = binof(maf)
        ds_by_tool = {}
        for tool in TOOLS:
            g, dss, af, r2 = calls[tool]
            d = [ds_int(x) for x in dss]
            if None in d:
                a["holes"][tool] += d.count(None)
                d = [0 if x is None else x for x in d]
            a["ds_oob"][tool] += sum(1 for x in d if x < 0 or x > 2000)
            ds_by_tool[tool] = d
            m = a["masked"][tool][bi]
            m["n"] += n
            m["sites"] += 1
            if r2 != ".":
                m["r2est_sum"] += float(r2)
            conc = nr_n = nr_ok = 0
            sx = sy = sxx = syy = sxy = 0
            for x, y, dx in zip(g, truth, d):
                if x == y:
                    conc += 1
                if x or y:
                    nr_n += 1
                    if x == y:
                        nr_ok += 1
                sx += dx; sy += y; sxx += dx * dx; syy += y * y; sxy += dx * y
            m["conc"] += conc; m["nr_n"] += nr_n; m["nr_ok"] += nr_ok
            m["sx"] += sx; m["sy"] += sy; m["sxx"] += sxx; m["syy"] += syy; m["sxy"] += sxy
            if af != ".":
                diff = abs(float(af) - sum(d) / (2000.0 * n))
                if diff > a["af_maxdiff"][tool]:
                    a["af_maxdiff"][tool] = diff
        gb, gm = calls["beagle"][0], calls["minimac4"][0]
        db, dm = ds_by_tool["beagle"], ds_by_tool["minimac4"]
        c = a["cross"][bi]
        c["n"] += n
        for xb, xm, y, vb, vm in zip(gb, gm, truth, db, dm):
            if xb == xm:
                c["agree"] += 1
            if xb != y:
                c["b_wrong"] += 1
                if xm != y:
                    c["both_wrong"] += 1
            if xm != y:
                c["m_wrong"] += 1
            c["sx"] += vb; c["sy"] += vm; c["sxx"] += vb * vb; c["syy"] += vm * vm; c["sxy"] += vb * vm
    for p in ps:
        rest = p.stdout.read()
        if rest:
            a["key_mismatch"] += 1
        p.wait()
        if p.returncode != 0:
            a["key_mismatch"] += 1
    if next(it, None) is not None:
        a["key_mismatch"] += 1
    return a


def merge(acc, a):
    for t in TOOLS:
        for i in range(len(BINS)):
            for k in FIELDS:
                acc["masked"][t][i][k] += a["masked"][t][i][k]
        for k in ("n", "changed"):
            acc["typed"][t][k] += a["typed"][t][k]
        acc["holes"][t] += a["holes"][t]
        acc["ds_oob"][t] += a["ds_oob"][t]
        acc["af_maxdiff"][t] = max(acc["af_maxdiff"][t], a["af_maxdiff"][t])
    for i in range(len(BINS)):
        for k in acc["cross"][i]:
            acc["cross"][i][k] += a["cross"][i][k]
    for k in ("lines", "key_mismatch", "truth_bad", "short"):
        acc[k] += a[k]


def frac(a, b):
    return "%.6f" % (a / b) if b else "nan"


def r2(n, sx, sy, sxx, syy, sxy):
    """Squared Pearson correlation from exact integer sums."""
    vx = n * sxx - sx * sx
    vy = n * syy - sy * sy
    if vx <= 0 or vy <= 0:
        return float("nan")
    cov = n * sxy - sx * sy
    return (cov * cov) / (vx * vy)   # exact integers until the one division


def main():
    keys, mafs, pos = [], [], []
    for line in open(SITES):
        k, ac, an = line.rstrip("\n").split("\t")
        ac, an = int(ac), int(an)
        keys.append(k)
        mafs.append(min(ac, an - ac) / an)
        pos.append(int(k.split(":")[1]))
    chrom = keys[0].split(":")[0]
    typed_pos = set(int(l.split("\t")[1]) for l in open(TYPED))
    typed = [p in typed_pos for p in pos]
    nchunks = PROCS * 8
    step = (len(keys) + nchunks - 1) // nchunks
    jobs = []
    for s in range(0, len(keys), step):
        e = min(s + step, len(keys))
        jobs.append(("%s:%d-%d" % (chrom, pos[s], pos[e - 1]), keys[s:e], mafs[s:e], typed[s:e]))
    samples = [subprocess.run(["bcftools", "query", "-l", f], capture_output=True, text=True, check=True).stdout
               for f in (TRUTH, BEAGLE, MM4)]
    nsamp = len(samples[0].split())
    acc = new_acc()
    with Pool(PROCS) as pool:
        for a in pool.imap(chunk, jobs):      # imap keeps chunk order, so every float sum is in a fixed order
            merge(acc, a)

    rows = []
    def put(tool, b, k, v):
        rows.append("%s\t%s\t%s\t%s" % (tool, b, k, v))
    put("all", "all", "panel_sites", len(keys))
    put("all", "all", "typed_sites", sum(typed))
    put("all", "all", "masked_sites", len(keys) - sum(typed))
    put("all", "all", "target_samples", nsamp)
    for t in TOOLS:
        tot = dict.fromkeys(FIELDS, 0)
        for i, name in enumerate(BIN_NAMES):
            m = acc["masked"][t][i]
            for k in FIELDS:
                tot[k] += m[k]
            put(t, name, "sites", m["sites"])
            put(t, name, "genotypes", m["n"])
            put(t, name, "concordance", "%.6f" % (m["conc"] / m["n"]) if m["n"] else "nan")
            put(t, name, "nonref_concordance", "%.6f" % (m["nr_ok"] / m["nr_n"]) if m["nr_n"] else "nan")
            put(t, name, "dosage_r2", "%.6f" % r2(m["n"], m["sx"], m["sy"], m["sxx"], m["syy"], m["sxy"]))
            put(t, name, "mean_estimated_r2", "%.6f" % (m["r2est_sum"] / m["sites"]) if m["sites"] else "nan")
        put(t, "all", "genotypes", tot["n"])
        put(t, "all", "concordance", frac(tot["conc"], tot["n"]))
        put(t, "all", "nonref_concordance", frac(tot["nr_ok"], tot["nr_n"]))
        put(t, "all", "dosage_r2", "%.6f" % r2(tot["n"], tot["sx"], tot["sy"], tot["sxx"], tot["syy"], tot["sxy"]))
        put(t, "typed", "genotypes", acc["typed"][t]["n"])
        put(t, "typed", "changed", acc["typed"][t]["changed"])
        put(t, "all", "holes", acc["holes"][t])
        put(t, "all", "dosage_out_of_range", acc["ds_oob"][t])
        put(t, "all", "af_vs_mean_dosage_maxdiff", "%.6f" % acc["af_maxdiff"][t])
    ctot = dict.fromkeys(acc["cross"][0], 0)
    for i, name in enumerate(BIN_NAMES):
        c = acc["cross"][i]
        for k in c:
            ctot[k] += c[k]
        put("cross", name, "hardcall_agreement", "%.6f" % (c["agree"] / c["n"]) if c["n"] else "nan")
        put("cross", name, "dosage_r2_between_tools", "%.6f" % r2(c["n"], c["sx"], c["sy"], c["sxx"], c["syy"], c["sxy"]))
        put("cross", name, "both_wrong", c["both_wrong"])
        put("cross", name, "beagle_wrong", c["b_wrong"])
        put("cross", name, "minimac4_wrong", c["m_wrong"])
    put("cross", "all", "hardcall_agreement", frac(ctot["agree"], ctot["n"]))
    put("cross", "all", "both_wrong", ctot["both_wrong"])
    put("cross", "all", "beagle_wrong", ctot["b_wrong"])
    put("cross", "all", "minimac4_wrong", ctot["m_wrong"])
    with open(OUT + "/scores.tsv", "w") as f:
        f.write("\n".join(rows) + "\n")
    print("\n".join(rows))

    def val(tool, b, k):
        for r in rows:
            t, bb, kk, v = r.split("\t")
            if (t, bb, kk) == (tool, b, k):
                return v
        raise KeyError((tool, b, k))

    checks = []
    def chk(claim, lhs, rhs, ok):
        checks.append("%s\t%s\t%s\t%s" % (claim, lhs, rhs, "ok" if bool(ok) else "FAIL"))
    chk("three VCFs list the same samples, in order", nsamp, len(samples[1].split()),
        samples[0] == samples[1] == samples[2] and nsamp > 0)
    chk("records read == panel sites, every file in lockstep", acc["lines"], len(keys), acc["lines"] == len(keys))
    chk("record keys out of step with the panel", acc["key_mismatch"], 0, acc["key_mismatch"] == 0)
    chk("short records (fewer fields than samples)", acc["short"], 0, acc["short"] == 0)
    chk("truth genotypes not called-and-phased", acc["truth_bad"], 0, acc["truth_bad"] == 0)
    for t in TOOLS:
        chk("%s: holes (missing GT or DS) at any genotype" % t, acc["holes"][t], 0, acc["holes"][t] == 0)
        chk("%s: dosages outside [0,2]" % t, acc["ds_oob"][t], 0, acc["ds_oob"][t] == 0)
        n_masked = sum(acc["masked"][t][i]["n"] for i in range(len(BINS)))
        chk("%s: masked genotypes scored == masked sites x samples" % t, n_masked,
            (len(keys) - sum(typed)) * nsamp, n_masked == (len(keys) - sum(typed)) * nsamp)
        r2c = float(val(t, ">=5%", "dosage_r2"))
        chk("%s: dosage r2 vs truth, panel MAF >= 5%%, >= 0.90" % t, "%.6f" % r2c, ">=0.90", r2c >= 0.90)
    # A tool's INFO AF is the mean of its own dosages / 2, up to print rounding: Beagle prints DS to
    # 2 decimals and AF to 4 (bound 0.005/2 + 0.00005), Minimac4 DS to 3 decimals and AF to 6
    # significant figures (bound 0.0005/2 + 0.0000005).
    for t, bound in (("beagle", 0.00255), ("minimac4", 0.0002505)):
        d = acc["af_maxdiff"][t]
        chk("%s: max |INFO AF - mean(DS)/2| within print rounding" % t, "%.6f" % d, "<=%g" % bound, d <= bound)
    chk("beagle: typed genotypes changed", acc["typed"]["beagle"]["changed"], 0, acc["typed"]["beagle"]["changed"] == 0)
    agr = float(val("cross", "all", "hardcall_agreement"))
    chk("beagle vs minimac4 hard-call agreement, masked, >= 0.99", "%.6f" % agr, ">=0.99", agr >= 0.99)
    with open(OUT + "/score.checks.tsv", "w") as f:
        f.write("\n".join(checks) + "\n")
    print("\n".join(checks))
    sys.exit(0 if all(c.endswith("\tok") for c in checks) else 1)


if __name__ == "__main__":
    main()
