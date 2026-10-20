#!/usr/bin/env python3
"""measurements/popgen-real: the popgen env at the scale it is used.

    real.py <input dir> <out dir> <worker processes> <chromosomes...>

Two workloads, one job per chromosome each, in one process pool:

 vcf  scikit-allel over 1000 Genomes Phase 3 (2,504 samples, five super-populations): every
      biallelic ACGT SNP, its per-population allele counts, within/between-population pairwise
      differences, Hudson's Fst, Watterson's S, Tajima's D, per-sample heterozygosity.
 sim  msprime simulating the same cohort size over the same chromosome lengths (read from the
      VCF headers) under the Gutenkunst et al. 2009 Out-of-Africa model, then the same statistics
      twice: by tskit from the trees and by scikit-allel from the decoded genotypes.

Every per-chromosome job writes one JSON line; the final pass asserts the identities and writes
check.txt. Run with python3 -u so a killed run keeps its output in command.log.
"""
import gzip
import hashlib
import json
import math
import multiprocessing as mp
import os
import resource
import sys
import time

import numpy as np

IN, OUT, NP = sys.argv[1], sys.argv[2], int(sys.argv[3])
CHROMS = [int(c) for c in sys.argv[4:]]
VCF = os.path.join(IN, "ALL.chr%d.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz")
PANEL = os.path.join(IN, "integrated_call_samples_v3.20130502.ALL.panel")
PLINK_COUNTS = os.path.join(IN, "plink-real-id-all.txt")
POPS = ["AFR", "AMR", "EAS", "EUR", "SAS"]
SIM_POPS = ["YRI", "CEU", "CHB"]
SIM_SIZES = {"YRI": 661, "CEU": 503, "CHB": 504}   # diploids, = 1000G's AFR / EUR / EAS
MU, RHO, SEED = 2.35e-8, 1e-8, 20130502            # mu is the model's own fitted rate
EPS = 2.0 ** -52


def pairs(k):
    return [(i, j) for i in range(k) for j in range(i + 1, k)]


# ------------------------------------------------------------------ shared accumulators
class Acc:
    """Per-population sums over sites, accumulated chunk by chunk from allele counts."""

    def __init__(self, names, sizes):
        import allel
        self.allel = allel
        self.names, self.n = names, [2 * s for s in sizes]   # haplotypes
        self.ntot = sum(self.n)
        k = len(names)
        self.pi = np.zeros(k)          # sum of per-site mean pairwise difference, within
        self.pi_all = 0.0
        self.dxy = np.zeros(len(pairs(k)))
        self.hnum = np.zeros(len(pairs(k)))
        self.hden = np.zeros(len(pairs(k)))
        self.S = np.zeros(k, dtype=np.int64)
        self.S_all = 0
        self.part_max_rel = 0.0        # within/between partition, worst site
        self.part_zero_bad = 0
        self.hud_max_abs = 0.0         # hudson num/den vs mpd identities, worst site
        self.ctl_violations = 0
        self.ctl_sites = 0
        self.sites = 0

    def add(self, acs, ac_all):
        a = self.allel
        m = ac_all.shape[0]
        if m == 0:
            return
        self.sites += m
        w = [a.mean_pairwise_difference(ac, fill=0) for ac in acs]
        wt = a.mean_pairwise_difference(ac_all, fill=0)
        self.pi += [float(x.sum()) for x in w]
        self.pi_all += float(wt.sum())
        for i, ac in enumerate(acs):
            self.S[i] += int(np.count_nonzero((ac[:, 0] > 0) & (ac[:, 1] > 0)))
        self.S_all += int(np.count_nonzero((ac_all[:, 0] > 0) & (ac_all[:, 1] > 0)))
        # pairs partition: C(n,2)*pi_T == sum_k C(n_k,2)*pi_k + sum_{k<l} n_k n_l d_kl, per site
        rhs = np.zeros(m)
        for i in range(len(acs)):
            rhs += w[i] * (self.n[i] * (self.n[i] - 1) / 2)
        btw = []
        for p, (i, j) in enumerate(pairs(len(acs))):
            d = a.mean_pairwise_difference_between(acs[i], acs[j], fill=0)
            btw.append(d)
            self.dxy[p] += float(d.sum())
            num, den = a.hudson_fst(acs[i], acs[j])
            self.hnum[p] += float(num.sum())
            self.hden[p] += float(den.sum())
            # Hudson's den is the between-pair difference, num is it minus the mean within
            e = max(float(np.max(np.abs(den - d))),
                    float(np.max(np.abs(num - (d - (w[i] + w[j]) / 2)))))
            self.hud_max_abs = max(self.hud_max_abs, e)
            rhs += d * (self.n[i] * self.n[j])
        lhs = wt * (self.ntot * (self.ntot - 1) / 2)
        pos = lhs > 0
        if np.any(pos):
            self.part_max_rel = max(self.part_max_rel,
                                    float(np.max(np.abs(lhs[pos] - rhs[pos]) / lhs[pos])))
        self.part_zero_bad += int(np.count_nonzero(rhs[~pos] != 0))
        # control: the same identity with the between terms taken from the NEXT site must fail
        if m > 1:
            rc = np.zeros(m - 1)
            for i in range(len(acs)):
                rc += w[i][:-1] * (self.n[i] * (self.n[i] - 1) / 2)
            for p, (i, j) in enumerate(pairs(len(acs))):
                rc += btw[p][1:] * (self.n[i] * self.n[j])
            lc = lhs[:-1]
            ok = lc > 0
            self.ctl_sites += int(np.count_nonzero(ok))
            self.ctl_violations += int(np.count_nonzero(np.abs(lc[ok] - rc[ok]) > 1e-12 * lc[ok]))

    def out(self):
        return {"sites": self.sites, "pi": self.pi.tolist(), "pi_all": self.pi_all,
                "dxy": self.dxy.tolist(), "hnum": self.hnum.tolist(), "hden": self.hden.tolist(),
                "S": self.S.tolist(), "S_all": self.S_all, "part_max_rel": self.part_max_rel,
                "part_zero_bad": self.part_zero_bad, "hud_max_abs": self.hud_max_abs,
                "ctl_violations": self.ctl_violations, "ctl_sites": self.ctl_sites}


def tajima_sd(S, n):
    a1 = sum(1.0 / i for i in range(1, n))
    a2 = sum(1.0 / i ** 2 for i in range(1, n))
    b1 = (n + 1) / (3.0 * (n - 1))
    b2 = 2.0 * (n * n + n + 3) / (9.0 * n * (n - 1))
    c1 = b1 - 1 / a1
    c2 = b2 - (n + 2) / (a1 * n) + a2 / a1 ** 2
    e1, e2 = c1 / a1, c2 / (a1 ** 2 + a2)
    return a1, math.sqrt(e1 * S + e2 * S * (S - 1))


def tajima(pi, S, n):
    """Tajima's D from the sum of pairwise differences and the segregating-site count."""
    a1, sd = tajima_sd(S, n)
    return (pi - S / a1) / sd


def contig_length(c):
    with gzip.open(VCF % c, "rt") as fh:
        for line in fh:
            if line.startswith("##contig=<ID=%d," % c):
                for kv in line.strip()[10:-1].split(","):
                    if kv.startswith("length="):
                        return int(kv[7:])
            if not line.startswith("##"):
                break
    raise RuntimeError("no ##contig length for chr%d" % c)


# --------------------------------------------------------------------------- vcf job
def job_vcf(c):
    import allel
    t0 = time.time()
    panel = {}
    for line in open(PANEL).read().splitlines()[1:]:
        f = line.split("\t")
        panel[f[0]] = f[2]
    fields = ["variants/POS", "variants/REF", "variants/ALT", "variants/AC", "variants/AN",
              "calldata/GT"] + ["variants/%s_AF" % p for p in POPS]
    # REF/ALT as S2: a single base stays itself and anything longer cannot read as one base
    types = {"variants/REF": "S2", "variants/ALT": "S2", "calldata/GT": "i1"}
    types.update({"variants/%s_AF" % p: "f8" for p in POPS})
    flds, samples, headers, it = allel.iter_vcf_chunks(VCF % c, fields=fields, types=types,
                                                       alt_number=3, chunk_length=65536)
    samples = [s.decode() if isinstance(s, bytes) else s for s in samples]
    if len(samples) != 2504 or len(set(samples)) != 2504:
        raise RuntimeError("expected 2504 distinct samples, got %d" % len(samples))
    idx = [np.array([i for i, s in enumerate(samples) if panel[s] == p]) for p in POPS]
    sizes = [len(x) for x in idx]
    acc = Acc(POPS, sizes)
    bases = np.array([b"A", b"C", b"G", b"T"])
    rec = snp = 0
    ac_bad = an_bad = pop_bad = cons_bad = miss = 0
    private = seg_any = singletons = 0
    het = np.zeros(len(samples), dtype=np.int64)
    homalt = np.zeros(len(samples), dtype=np.int64)
    prev = None
    dup = 0
    for chunk, n, chrom, pos in it:
        rec += n
        ref, alt = chunk["variants/REF"], chunk["variants/ALT"]
        keep = np.isin(ref, bases) & np.isin(alt[:, 0], bases) & (alt[:, 1] == b"")
        k = int(keep.sum())
        if k == 0:
            continue
        snp += k
        gt = allel.GenotypeArray(chunk["calldata/GT"][keep])
        miss += int(np.count_nonzero(gt.values < 0))
        ac_all = gt.count_alleles(max_allele=1)
        acs = [gt.count_alleles(subpop=ix, max_allele=1) for ix in idx]
        AC = chunk["variants/AC"][keep][:, 0]
        AN = chunk["variants/AN"][keep]
        ac_bad += int(np.count_nonzero(ac_all[:, 1] != AC))
        an_bad += int(np.count_nonzero((ac_all.sum(axis=1) != AN) | (AN != 5008)))
        tot = np.zeros(k, dtype=np.int64)
        for p, ac in zip(POPS, acs):
            af = chunk["variants/%s_AF" % p][keep][:, 0].astype(np.float64)
            npop = 2 * sizes[POPS.index(p)]
            pop_bad += int(np.count_nonzero(np.rint(af * npop).astype(np.int64) != ac[:, 1]))
            tot += ac[:, 1]
        cons_bad += int(np.count_nonzero(tot != AC))
        acc.add(acs, ac_all)
        carriers = np.stack([ac[:, 1] > 0 for ac in acs], axis=1).sum(axis=1)
        seg_any += int(np.count_nonzero(carriers > 0))
        private += int(np.count_nonzero(carriers == 1))
        singletons += int(np.count_nonzero(ac_all[:, 1] == 1))
        het += gt.count_het(axis=0)
        homalt += gt.count_hom_alt(axis=0)
        # verbatim repeats: consecutive records with the same POS/REF/ALT
        key = np.rec.fromarrays([chunk["variants/POS"][keep], ref[keep], alt[keep][:, 0]])
        if prev is not None and key[0] == prev:
            dup += 1
        dup += int(np.count_nonzero(key[1:] == key[:-1]))
        prev = key[-1]
    plink = None
    if os.path.exists(PLINK_COUNTS):
        for line in open(PLINK_COUNTS):
            f = line.split()
            if f and f[0] == str(c):
                plink = int(f[1])
    by_pop_het = {p: int(het[ix].sum()) for p, ix in zip(POPS, idx)}
    by_pop_homalt = {p: int(homalt[ix].sum()) for p, ix in zip(POPS, idx)}
    return {"job": "vcf", "chrom": c, "records": rec, "snps": snp, "plink_snps": plink,
            "ac_mismatch": ac_bad, "an_mismatch": an_bad, "pop_af_mismatch": pop_bad,
            "pop_sum_mismatch": cons_bad, "missing_calls": miss, "repeated_records": dup,
            "seg_any": seg_any, "private_one_pop": private, "singletons": singletons,
            "sizes": sizes, "het_by_pop": by_pop_het, "homalt_by_pop": by_pop_homalt,
            "acc": acc.out(), "wall_s": time.time() - t0}


# --------------------------------------------------------------------------- sim job
def ooa():
    """Gutenkunst et al. 2009 (PLoS Genet 5:e1000695) three-population Out-of-Africa model, the
    parameterisation in msprime's own documentation; 25-year generations."""
    import msprime
    T_OOA, T_AMH, T_ANC = 21.2e3 / 25, 140e3 / 25, 220e3 / 25
    r_CEU, r_CHB = 0.004, 0.0055
    d = msprime.Demography()
    d.add_population(name="YRI", initial_size=12300)
    d.add_population(name="CEU", initial_size=1000 / math.exp(-r_CEU * T_OOA), growth_rate=r_CEU)
    d.add_population(name="CHB", initial_size=510 / math.exp(-r_CHB * T_OOA), growth_rate=r_CHB)
    d.add_population(name="OOA", initial_size=2100)
    d.add_population(name="AMH", initial_size=12300)
    d.add_population(name="ANC", initial_size=7300)
    d.set_symmetric_migration_rate(["CEU", "CHB"], 9.6e-5)
    d.set_symmetric_migration_rate(["YRI", "CHB"], 1.9e-5)
    d.set_symmetric_migration_rate(["YRI", "CEU"], 3e-5)
    d.add_population_split(time=T_OOA, derived=["CEU", "CHB"], ancestral="OOA")
    d.add_symmetric_migration_rate_change(time=T_OOA, populations=["YRI", "OOA"], rate=25e-5)
    d.add_population_split(time=T_AMH, derived=["YRI", "OOA"], ancestral="AMH")
    d.add_population_split(time=T_ANC, derived=["AMH"], ancestral="ANC")
    d.sort_events()
    return d


def simulate(L, seed):
    import msprime
    t0 = time.time()
    ts = msprime.sim_ancestry(samples=SIM_SIZES, demography=ooa(), sequence_length=L,
                              recombination_rate=RHO, random_seed=seed)
    t1 = time.time()
    # infinite sites: every mutation its own site, so site statistics have an exact
    # expectation given the trees (below) with no multiple-hit correction
    mts = msprime.sim_mutations(ts, rate=MU, random_seed=seed, discrete_genome=False)
    return mts, t1 - t0, time.time() - t1


def tables_digest(ts):
    t = ts.tables
    h = hashlib.sha256()
    for col in (t.nodes.time, t.nodes.population, t.edges.left, t.edges.right, t.edges.parent,
                t.edges.child, t.sites.position, t.mutations.site, t.mutations.node,
                t.mutations.time):
        h.update(np.ascontiguousarray(col).tobytes())
    return h.hexdigest()


def job_sim(c):
    import allel
    import tskit
    t0 = time.time()
    L = contig_length(c)
    ts, t_anc, t_mut = simulate(L, SEED + c)
    n = ts.num_samples
    r = {"job": "sim", "chrom": c, "length": L, "ancestry_s": t_anc, "mutations_s": t_mut,
         "samples": n, "individuals": ts.num_individuals, "trees": ts.num_trees,
         "edges": ts.num_edges, "nodes": ts.num_nodes, "sites": ts.num_sites,
         "mutations": ts.num_mutations, "digest": tables_digest(ts)}
    if c == 22:   # determinism and its control, once: a second whole-chromosome simulation each
        same, _, _ = simulate(L, SEED + c)
        other, _, _ = simulate(L, SEED + c + 1000)
        r["same_seed_equal"] = bool(ts.tables.equals(same.tables, ignore_provenance=True))
        r["other_seed_equal"] = bool(ts.tables.equals(other.tables, ignore_provenance=True))
        del same, other
    # ---- structure: every tree single-rooted, binary, 2n-2 edges
    t1 = time.time()
    bad_roots = bad_edges = ntrees = 0
    for tree in ts.trees():
        ntrees += 1
        if tree.num_roots != 1:
            bad_roots += 1
        if tree.num_edges != 2 * n - 2:
            bad_edges += 1
    # binary, from the edge table alone: sweep each parent's child-edge coverage along the
    # genome; after all events at a position it must be 0 or 2, never anything else
    e = ts.tables.edges
    par = np.concatenate([e.parent, e.parent])
    posn = np.concatenate([e.left, e.right])
    delta = np.concatenate([np.ones(len(e), np.int8), -np.ones(len(e), np.int8)])
    o = np.lexsort((delta, posn, par))
    par, posn, cov = par[o], posn[o], np.cumsum(delta[o].astype(np.int64))
    last = np.ones(len(par), bool)
    last[:-1] = (par[1:] != par[:-1]) | (posn[1:] != posn[:-1])
    bad_binary = int(np.count_nonzero((cov[last] != 0) & (cov[last] != 2)))
    sample_parent = int(np.count_nonzero(np.isin(e.parent, ts.samples())))
    del par, posn, cov, last, o, delta
    r.update({"trees_iterated": ntrees, "trees_not_single_rooted": bad_roots,
              "trees_wrong_edge_count": bad_edges, "parent_segments_not_binary": bad_binary,
              "samples_as_parents": sample_parent, "structure_s": time.time() - t1})
    # ---- tskit statistics, from the trees
    t2 = time.time()
    pops = {ts.population(p.id).metadata["name"]: p.id for p in ts.populations()}
    sets = [ts.samples(population=pops[p]) for p in SIM_POPS]
    allset = ts.samples()
    pp = pairs(3)
    r["tsk_pi"] = [float(x) for x in ts.diversity(sets, mode="site", span_normalise=False)]
    r["tsk_pi_all"] = float(ts.diversity(allset, mode="site", span_normalise=False))
    r["tsk_dxy"] = [float(x) for x in ts.divergence(sets, indexes=pp, mode="site",
                                                    span_normalise=False)]
    r["tsk_S"] = [float(x) for x in ts.segregating_sites(sets, mode="site", span_normalise=False)]
    r["tsk_D"] = [float(x) for x in ts.Tajimas_D(sets, mode="site")]
    r["tsk_fst"] = [float(x) for x in ts.Fst(sets, indexes=pp, mode="site")]
    # ---- mutations given the genealogy: E[site stat] = MU * branch stat, Poisson variance
    B = ts.allele_frequency_spectrum([allset], mode="branch", polarised=True,
                                     span_normalise=False)
    k = np.arange(n + 1)
    wk = 2.0 * k * (n - k) / (n * (n - 1))
    r["pi_expected"] = float(MU * np.dot(B, wk))
    r["pi_variance"] = float(MU * np.dot(B, wk * wk))
    r["S_expected"] = float(MU * B[1:n].sum())
    r["infinite_sites_ok"] = bool(ts.num_sites == ts.num_mutations)
    r["tskit_s"] = time.time() - t2
    # ---- scikit-allel, from the decoded genotype matrix, chunk by chunk
    t3 = time.time()
    acc = Acc(SIM_POPS, [SIM_SIZES[p] for p in SIM_POPS])
    CH = 50000
    buf = np.empty((CH, n), dtype=np.int8)
    m = 0
    nodes_pos = {int(u): i for i, u in enumerate(allset)}
    ix = [np.array([nodes_pos[int(u)] for u in s]) for s in sets]
    S_ok = np.zeros(3, np.int64)
    pi_chunks, S_chunks = [], []

    def flush(m):
        h = allel.HaplotypeArray(buf[:m])
        acs = [h.count_alleles(subpop=x, max_allele=1) for x in ix]
        acc.add(acs, h.count_alleles(max_allele=1))
    first = None
    for v in ts.variants(samples=allset, copy=False):
        buf[m] = v.genotypes
        m += 1
        if m == CH:
            if first is None:   # pin the Tajima formula against allel's own, on one chunk
                h = allel.HaplotypeArray(buf[:m])
                first = (float(allel.tajima_d(h.count_alleles(subpop=ix[0], max_allele=1))),
                         h.count_alleles(subpop=ix[0], max_allele=1))
            flush(m)
            m = 0
    if m:
        flush(m)
    if first is not None:
        ac0 = first[1]
        pi0 = float(allel.mean_pairwise_difference(ac0, fill=0).sum())
        S0 = int(np.count_nonzero((ac0[:, 0] > 0) & (ac0[:, 1] > 0)))
        r["tajima_formula_vs_allel"] = abs(tajima(pi0, S0, 2 * SIM_SIZES["YRI"]) - first[0])
    r["allel"] = acc.out()
    r["allel_s"] = time.time() - t3
    r["wall_s"] = time.time() - t0
    return r


def run(job):
    kind, c = job
    try:
        r = job_vcf(c) if kind == "vcf" else job_sim(c)
    except Exception as ex:   # report, never hide: the final pass fails on any error row
        import traceback
        r = {"job": kind, "chrom": c, "error": "%s: %s" % (type(ex).__name__, ex),
             "trace": traceback.format_exc()}
    r["maxrss_gib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
    return r


# ------------------------------------------------------------------------- the checks
CHECKS = []


def check(name, ok, observed):
    ok = bool(ok)
    CHECKS.append((name, "ok" if ok else "FAIL", str(observed)))
    print("%-4s %-46s %s" % ("ok" if ok else "FAIL", name, observed), flush=True)


def jack_fst(num, den):
    """Delete-one-chromosome jackknife of a ratio of sums."""
    num, den = np.asarray(num), np.asarray(den)
    g = len(num)
    full = num.sum() / den.sum()
    loo = (num.sum() - num) / (den.sum() - den)
    se = math.sqrt((g - 1) / g * float(((loo - loo.mean()) ** 2).sum()))
    return full, se


def final(rows):
    from scipy import stats as sst
    errs = [r for r in rows if "error" in r]
    check("jobs without an exception", not errs,
          "%d of %d" % (len(rows) - len(errs), len(rows)) + "".join(
              "\n%s chr%d %s\n%s" % (r["job"], r["chrom"], r["error"], r["trace"]) for r in errs))
    V = sorted([r for r in rows if r["job"] == "vcf" and "error" not in r], key=lambda r: r["chrom"])
    M = sorted([r for r in rows if r["job"] == "sim" and "error" not in r], key=lambda r: r["chrom"])
    res = []

    def out(k, v):
        res.append((k, v))
        print("     %-46s %s" % (k, v), flush=True)

    # ------------------------------------------------------------- 1000 Genomes, real data
    if V:
        tot = lambda k: sum(r[k] for r in V)
        out("vcf_chromosomes", len(V))
        out("vcf_records", tot("records"))
        out("biallelic_acgt_snps", tot("snps"))
        pl = [r for r in V if r["plink_snps"] is not None]
        check("SNPs per chromosome == PLINK 1.9 import (plink-real)",
              len(pl) == len(V) and all(r["snps"] == r["plink_snps"] for r in V),
              "%d of %d chromosomes equal, %d vs %d SNPs" % (
                  sum(r["snps"] == r["plink_snps"] for r in V), len(V), tot("snps"),
                  sum(r["plink_snps"] or 0 for r in V)))
        check("ALT count from GT == INFO AC, every SNP", tot("ac_mismatch") == 0,
              "%d mismatches" % tot("ac_mismatch"))
        check("called alleles == INFO AN == 5008, every SNP", tot("an_mismatch") == 0,
              "%d mismatches" % tot("an_mismatch"))
        check("per-population ALT count == round(<POP>_AF x 2N)", tot("pop_af_mismatch") == 0,
              "%d mismatches over %d rows" % (tot("pop_af_mismatch"), 5 * tot("snps")))
        check("five populations' counts sum to INFO AC", tot("pop_sum_mismatch") == 0,
              "%d mismatches" % tot("pop_sum_mismatch"))
        check("no missing genotype calls", tot("missing_calls") == 0, tot("missing_calls"))
        A = [r["acc"] for r in V]
        sites = sum(a["sites"] for a in A)
        check("pair partition C(n,2)pi_T == within + between, every site",
              max(a["part_max_rel"] for a in A) < 1e-12 and sum(a["part_zero_bad"] for a in A) == 0,
              "worst relative %.2e over %d sites" % (max(a["part_max_rel"] for a in A), sites))
        cv, cs = sum(a["ctl_violations"] for a in A), sum(a["ctl_sites"] for a in A)
        check("control: partition with next-site between terms fails", cv > 0.5 * cs,
              "fails at %d of %d sites (%.1f%%)" % (cv, cs, 100.0 * cv / max(cs, 1)))
        check("hudson_fst num/den == between - mean within / between",
              max(a["hud_max_abs"] for a in A) < 1e-12,
              "worst abs %.2e" % max(a["hud_max_abs"] for a in A))
        sizes = V[0]["sizes"]
        out("sample_sizes", " ".join("%s=%d" % (p, s) for p, s in zip(POPS, sizes)))
        pi = np.sum([a["pi"] for a in A], axis=0)
        S = np.sum([a["S"] for a in A], axis=0)
        for i, p in enumerate(POPS):
            out("real_%s_pairwise_diffs_per_haplotype_pair" % p, "%.1f" % pi[i])
            out("real_%s_segregating_snps" % p, "%d" % S[i])
            out("real_%s_tajima_D" % p, "%.4f" % tajima(pi[i], S[i], 2 * sizes[i]))
            out("real_%s_het_per_sample" % p,
                "%.0f" % (sum(r["het_by_pop"][p] for r in V) / sizes[i]))
            out("real_%s_nonref_snps_per_sample" % p,
                "%.0f" % (sum(r["het_by_pop"][p] + r["homalt_by_pop"][p] for r in V) / sizes[i]))
        for q, (i, j) in enumerate(pairs(5)):
            f, se = jack_fst([a["hnum"][q] for a in A], [a["hden"][q] for a in A])
            out("real_hudson_fst_%s_%s" % (POPS[i], POPS[j]), "%.5f +- %.5f (jackknife SE)" % (f, se))
        seg, prv = tot("seg_any"), tot("private_one_pop")
        out("real_snps_seen_in_one_superpop_only", "%d of %d (%.1f%%)" % (prv, seg, 100.0 * prv / seg))
        out("real_singletons", tot("singletons"))
        out("real_repeated_records", tot("repeated_records"))

    # ----------------------------------------------------------------- msprime, simulated
    if M:
        tot = lambda k: sum(r[k] for r in M)
        n = M[0]["samples"]
        out("sim_chromosomes", len(M))
        out("sim_bp", tot("length"))
        out("sim_samples", "%d haplotypes = %d diploids" % (n, M[0]["individuals"]))
        out("sim_trees", tot("trees"))
        out("sim_edges", tot("edges"))
        out("sim_sites", tot("sites"))
        check("individuals -> sample nodes: 1668 -> 3336",
              all(r["individuals"] == 1668 and r["samples"] == 3336 for r in M), "%d / %d" % (
                  M[0]["individuals"], n))
        check("every tree iterated (== num_trees)", all(r["trees_iterated"] == r["trees"] for r in M),
              tot("trees_iterated"))
        check("every tree single-rooted", tot("trees_not_single_rooted") == 0,
              "%d of %d" % (tot("trees_not_single_rooted"), tot("trees")))
        check("every tree has 2n-2 = %d edges" % (2 * n - 2), tot("trees_wrong_edge_count") == 0,
              "%d wrong" % tot("trees_wrong_edge_count"))
        check("every internal node binary, along the genome", tot("parent_segments_not_binary") == 0,
              "%d parent segments with != 0 or 2 children" % tot("parent_segments_not_binary"))
        check("no sample node is a parent", tot("samples_as_parents") == 0, tot("samples_as_parents"))
        check("infinite sites: num_sites == num_mutations", all(r["infinite_sites_ok"] for r in M),
              "%d / %d" % (tot("sites"), tot("mutations")))
        d22 = [r for r in M if "same_seed_equal" in r]
        if d22:
            check("same seed reproduces chr22's tables", d22[0]["same_seed_equal"], d22[0]["same_seed_equal"])
            check("control: another seed does not", not d22[0]["other_seed_equal"],
                  d22[0]["other_seed_equal"])
        # tskit (trees) vs scikit-allel (genotypes): the same sums in two orders, so the bound
        # is the worst case for summing m positive terms, m * eps, per chromosome
        worst = {}
        for r in M:
            a, m = r["allel"], max(r["sites"], 1)
            bound = m * EPS
            for key, tv, av in [("pi", r["tsk_pi"], a["pi"]), ("dxy", r["tsk_dxy"], a["dxy"]),
                                ("pi_all", [r["tsk_pi_all"]], [a["pi_all"]])]:
                for x, y in zip(tv, av):
                    rel = abs(x - y) / abs(x)
                    worst[key] = max(worst.get(key, (0, 0))[0], rel), bound
                    if rel > bound:
                        worst[key + "_fail"] = True
            if any(int(round(x)) != y for x, y in zip(r["tsk_S"], a["S"])):
                worst["S_fail"] = True
            if a["sites"] != r["sites"]:
                worst["sites_fail"] = True
        for key, label in [("pi", "pi within YRI/CEU/CHB"), ("dxy", "divergence, 3 pairs"),
                           ("pi_all", "pi, pooled")]:
            check("tskit == scikit-allel: %s (< m*eps)" % label, key + "_fail" not in worst,
                  "worst relative %.2e" % worst[key][0])
        check("tskit == scikit-allel: segregating sites, exact", "S_fail" not in worst,
              "%d chromosomes" % len(M))
        check("allel decoded every site", "sites_fail" not in worst, tot("sites"))
        A = [r["allel"] for r in M]
        check("sim pair partition, every site", max(a["part_max_rel"] for a in A) < 1e-12
              and sum(a["part_zero_bad"] for a in A) == 0,
              "worst relative %.2e" % max(a["part_max_rel"] for a in A))
        check("sim hudson_fst num/den identities", max(a["hud_max_abs"] for a in A) < 1e-12,
              "worst abs %.2e" % max(a["hud_max_abs"] for a in A))
        # Hudson Fst from tskit's diversity/divergence vs allel's hudson_fst, genome-wide
        hw = []
        for q, (i, j) in enumerate(pairs(3)):
            tn = sum(r["tsk_dxy"][q] - (r["tsk_pi"][i] + r["tsk_pi"][j]) / 2 for r in M)
            td = sum(r["tsk_dxy"][q] for r in M)
            an = sum(r["allel"]["hnum"][q] for r in M)
            ad = sum(r["allel"]["hden"][q] for r in M)
            hw.append((abs(tn / td - an / ad) / abs(an / ad), 2 * tot("sites") * EPS * ad / an))
            out("sim_hudson_fst_%s_%s" % (SIM_POPS[i], SIM_POPS[j]), "%.5f" % (an / ad))
            tf = [r["tsk_fst"][q] for r in M]
            out("sim_tskit_Fst_%s_%s_chr_range" % (SIM_POPS[i], SIM_POPS[j]),
                "%.5f .. %.5f" % (min(tf), max(tf)))
        # bound: the sums' worst case (m*eps), amplified by 1/Fst through the subtraction
        check("Hudson Fst: tskit pi/dxy == allel hudson_fst", all(e < b for e, b in hw),
              "worst relative %.2e (bound %.1e)" % (max(e for e, b in hw), min(b for e, b in hw)))
        # tskit's Fst against its own documented formula, per chromosome
        fe = 0.0
        for r in M:
            for q, (i, j) in enumerate(pairs(3)):
                dx, dy, dxy = r["tsk_pi"][i], r["tsk_pi"][j], r["tsk_dxy"][q]
                fe = max(fe, abs(r["tsk_fst"][q] - (1 - 2 * (dx + dy) / (dx + 2 * dxy + dy))))
        check("tskit Fst == 1 - 2(dX+dY)/(dX+2dXY+dY)", fe < 1e-12, "worst abs %.2e" % fe)
        tf = max(r.get("tajima_formula_vs_allel", 0.0) for r in M)
        check("Tajima formula == allel.tajima_d on a chunk", tf < 1e-12, "%.2e" % tf)
        # bound: pi's sum error (m*eps*pi) over D's standard deviation; S is an exact integer
        dd, dbad = 0.0, 0
        for r in M:
            for i, p in enumerate(SIM_POPS):
                nn, Sx, px = 2 * SIM_SIZES[p], r["allel"]["S"][i], r["allel"]["pi"][i]
                sd = tajima_sd(Sx, nn)[1]
                e = abs(tajima(px, Sx, nn) - r["tsk_D"][i])
                dd = max(dd, e)
                dbad += e > max(r["sites"], 1) * EPS * px / abs(sd)
        check("Tajima's D: tskit == allel's pi and S, per chromosome", dbad == 0, "worst abs %.2e" % dd)
        # the mutation process, given the genealogy
        zp = np.array([(r["tsk_pi_all"] - r["pi_expected"]) / math.sqrt(r["pi_variance"]) for r in M])
        zs = np.array([(r["mutations"] - r["S_expected"]) / math.sqrt(r["S_expected"]) for r in M])
        lim = float(sst.chi2.ppf(0.9999, len(M)))
        check("site pi == MU x branch pi: sum z^2 < chi2_%d(0.9999)" % len(M),
              float((zp ** 2).sum()) < lim and np.all(np.isfinite(zp)),
              "sum z^2 %.2f < %.2f, max |z| %.2f" % ((zp ** 2).sum(), lim, np.abs(zp).max()))
        check("mutations == MU x branch length: sum z^2 < chi2_%d(0.9999)" % len(M),
              float((zs ** 2).sum()) < lim and np.all(np.isfinite(zs)),
              "sum z^2 %.2f < %.2f, max |z| %.2f" % ((zs ** 2).sum(), lim, np.abs(zs).max()))
        piS = np.sum([a["pi"] for a in A], axis=0)
        SS = np.sum([a["S"] for a in A], axis=0)
        for i, p in enumerate(SIM_POPS):
            out("sim_%s_pairwise_diffs_per_haplotype_pair" % p, "%.1f" % piS[i])
            out("sim_%s_tajima_D" % p, "%.4f" % tajima(piS[i], SS[i], 2 * SIM_SIZES[p]))
        out("sim_tables_sha256_by_chrom", " ".join("%d:%s" % (r["chrom"], r["digest"][:16]) for r in M))
    with open(os.path.join(OUT, "check.txt"), "w") as fh:
        for k, s, v in CHECKS:
            fh.write("%s\t%s\t%s\n" % (s, k, v.replace("\n", " | ")))
        for k, v in res:
            fh.write("obs\t%s\t%s\n" % (k, v))
    return all(s == "ok" for _, s, _ in CHECKS)


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [("vcf", c) for c in sorted(CHROMS, key=lambda c: -os.path.getsize(VCF % c))]
    jobs += [("sim", c) for c in sorted(CHROMS, key=lambda c: -os.path.getsize(VCF % c))]
    t0 = time.time()
    rows = []
    with open(os.path.join(OUT, "jobs.jsonl"), "w") as fh, \
            mp.get_context("fork").Pool(NP, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(run, jobs):
            r["finished_s"] = time.time() - t0
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            rows.append(r)
            print("done %s chr%d in %.0fs (rss %.2f GiB)%s" % (
                r["job"], r["chrom"], r.get("wall_s", -1), r["maxrss_gib"],
                " ERROR " + r["error"] if "error" in r else ""), flush=True)
    with open(os.path.join(OUT, "phases.txt"), "w") as fh:
        fh.write("pool_wall_s\t%.1f\n" % (time.time() - t0))
        for kind in ("vcf", "sim"):
            rr = [r for r in rows if r["job"] == kind]
            if rr:
                fh.write("%s_last_finished_s\t%.1f\n" % (kind, max(r["finished_s"] for r in rr)))
                fh.write("%s_job_seconds_summed\t%.1f\n" % (kind, sum(r.get("wall_s", 0) for r in rr)))
                fh.write("%s_max_job_rss_gib\t%.2f\n" % (kind, max(r["maxrss_gib"] for r in rr)))
    ok = final(rows)
    print("POPGEN REAL OK" if ok else "POPGEN REAL FAILED", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
