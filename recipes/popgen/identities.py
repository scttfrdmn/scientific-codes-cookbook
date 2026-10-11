#!/usr/bin/env python3
"""msprime + tskit on Graviton4: coalescent theory supplies the references.

Population genetics is unusually well served by this catalog's preferences, because the
neutral coalescent has CLOSED FORMS and a tree sequence has EXACT INTEGER INVARIANTS. So
almost nothing here is a band:

 * every tree must have exactly one root, be strictly binary, and carry 2n-1 nodes and
   2n-2 edges. Integers a coalescent simulator must satisfy to be simulating coalescents.
 * the polarised allele-frequency spectrum sums to the mutation count, exactly.
 * tskit and scikit-allel compute pairwise diversity from completely different data -- a
   tree sequence versus a genotype matrix -- and agree to ~5e-15.
 * E[pi] = 4*Ne*mu is a closed form, but a SINGLE replicate ranges 0.81-1.04 of it. So the
   assertion is on the MEAN of 24 replicates against its own standard error, which is a
   bound from the sampling distribution rather than a band drawn around one run.

Three comparisons had to be fixed before they measured anything, all the same mistake in
different clothes -- comparing metadata or mismatched units instead of the science:

 1. `tables == tables` includes the PROVENANCE table (timestamps, command line), so it is
    False on every run whatever the seed. Determinism needs ignore_provenance=True.
 2. The `.trees` file sha256 differs while the tables are equal, so bytes are the wrong
    granularity for a round-trip check.
 3. tskit's mode="site" statistics are already span-normalised. Dividing by L again made
    tskit and scikit-allel look 1e6 apart -- an arithmetic error of mine, not a finding.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys

import numpy as np
import msprime
import tskit
import allel

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("msprime", msprime.__version__)
rec("tskit", tskit.__version__)
rec("scikit_allel", allel.__version__)
rec("numpy", np.__version__)

# Diploid individuals: samples=20 gives 40 sample NODES, which is the n in every formula
# below. Stated because a_n and 2n-1 are both wrong by a factor of two if you use 20.
INDIV, NE, MU, RHO, L = 20, 10_000, 1e-8, 1e-8, 1_000_000
SEED = 20261011
NREP = 24


def sim(seed):
    ts = msprime.sim_ancestry(samples=INDIV, population_size=NE, recombination_rate=RHO,
                              sequence_length=L, random_seed=seed)
    return msprime.sim_mutations(ts, rate=MU, random_seed=seed)


ts = sim(SEED)
n = ts.num_samples
rec("individuals", "%d" % ts.num_individuals)
rec("sample_nodes", "%d" % n)
rec("sequence_length", "%g" % L)
rec("trees", "%d" % ts.num_trees)
rec("sites", "%d" % ts.num_sites)
rec("mutations", "%d" % ts.num_mutations)
if ts.num_individuals != INDIV or n != 2 * INDIV:
    die("asked for %d diploid individuals and got %d individuals / %d sample nodes"
        % (INDIV, ts.num_individuals, n))

# -------------------------- 1. exact structural invariants, on EVERY marginal tree
want_nodes, want_edges = 2 * n - 1, 2 * n - 2
bad_root = bad_binary = bad_nodes = bad_edges = 0
for tree in ts.trees():
    if tree.num_roots != 1:
        bad_root += 1
    nn = ne = 0
    for u in tree.nodes():
        nn += 1
        k = len(tree.children(u))
        if k not in (0, 2):
            bad_binary += 1
        if tree.parent(u) != tskit.NULL:
            ne += 1
    if nn != want_nodes:
        bad_nodes += 1
    if ne != want_edges:
        bad_edges += 1
rec("trees_checked", "%d" % ts.num_trees)
rec("expected_nodes_per_tree", "2n-1 = %d" % want_nodes)
rec("expected_edges_per_tree", "2n-2 = %d" % want_edges)
rec("trees_not_single_rooted", "%d" % bad_root)
rec("nodes_not_binary", "%d" % bad_binary)
rec("trees_wrong_node_count", "%d" % bad_nodes)
rec("trees_wrong_edge_count", "%d" % bad_edges)
if bad_root:
    die("%d of %d trees are not fully coalesced (more than one root)" % (bad_root, ts.num_trees))
if bad_binary:
    die("%d nodes have neither 0 nor 2 children; a coalescent tree is binary" % bad_binary)
if bad_nodes or bad_edges:
    die("%d trees have the wrong node count and %d the wrong edge count; a binary tree on "
        "%d samples has exactly %d nodes and %d edges"
        % (bad_nodes, bad_edges, n, want_nodes, want_edges))
rec("structural_invariants", "1 root, binary, %d nodes, %d edges -- on all %d trees"
    % (want_nodes, want_edges, ts.num_trees))

# ------------------------------------------- 2. determinism, and the control for it
same = sim(SEED)
other = sim(SEED + 1)
det = bool(ts.tables.equals(same.tables, ignore_provenance=True))
ctl = bool(ts.tables.equals(other.tables, ignore_provenance=True))
rec("determinism_same_seed", "identical" if det else "DIFFERS")
rec("control_different_seed", "differs" if not ctl else "IDENTICAL")
rec("provenance_note", "tables == is False even for one seed; provenance holds timestamps")
if not det:
    die("the same random_seed did not reproduce the tables (ignoring provenance)")
if ctl:
    die("a different seed produced identical tables, so the determinism check above is "
        "vacuous -- it would pass for a simulator that ignored the seed")

# ------------------------------------------------- 3. serialization round-trip
ts.dump("ts.trees")
back = tskit.load("ts.trees")
rt = bool(ts.tables.equals(back.tables, ignore_provenance=True))
rec("roundtrip_tables_equal", "yes" if rt else "NO")
if not rt:
    die("dump -> load did not preserve the tables")

# ------------------------------- 4. the AFS conservation identity, stated correctly
afs = ts.allele_frequency_spectrum(mode="site", polarised=True, span_normalise=False)
tot = float(afs.sum())
rec("afs_bins", "%d" % len(afs))
rec("afs_sum", "%.1f" % tot)
rec("afs_monomorphic_and_fixed_bins", "%.1f / %.1f" % (afs[0], afs[-1]))
if tot != float(ts.num_mutations):
    die("the polarised AFS sums to %.1f but there are %d mutations; it is the MUTATION "
        "count the spectrum conserves, not the site count (%d)"
        % (tot, ts.num_mutations, ts.num_sites))
rec("conservation_afs", "polarised AFS sums to num_mutations exactly")

# ------------- 5. two independent implementations of pairwise diversity, same data
G = ts.genotype_matrix()
ac = allel.HaplotypeArray(G).count_alleles()
pos = np.array([s.position for s in ts.sites()])
pi_tskit = float(ts.diversity(mode="site"))
pi_allel = float(allel.sequence_diversity(pos, ac, start=1, stop=L))
if not (np.isfinite(pi_tskit) and np.isfinite(pi_allel)):
    die("a non-finite diversity")
rel = abs(pi_allel - pi_tskit) / pi_tskit
rec("pi_tskit", "%.12e" % pi_tskit)
rec("pi_scikit_allel", "%.12e" % pi_allel)
rec("pi_relative_difference", "%.3e" % rel)
if rel > 1e-12:
    die("tskit computes pi from the tree sequence and scikit-allel from a genotype "
        "matrix; they differ by %.3e, which is far more than float noise" % rel)
rec("cross_validation_pi", "tskit and scikit-allel agree to %.1e" % rel)

# ---- 6. Watterson: the apparent disagreement is EXACTLY the counting convention
# tskit's segregating_sites counts MUTATIONS; scikit-allel counts segregating SITES from
# the allele-count matrix. When a site carries more than one mutation those differ, and
# the whole gap is that ratio -- so it is an identity, not a discrepancy.
a_n = sum(1.0 / i for i in range(1, n))
S_tskit = float(ts.segregating_sites(mode="site", span_normalise=False))
th_tskit = float(ts.segregating_sites(mode="site")) / a_n
th_allel = float(allel.watterson_theta(pos, ac, start=1, stop=L))
# scikit-allel's span is (stop - start + 1), so start=1/stop=L is the interval of
# length exactly L. Getting that wrong by one made pi differ by 1/L = 1.000e-06 -- which
# the 1e-12 bound above caught; a 1e-3 tolerance would have shipped it silently. So the
# convention is pinned here by recomputing allel's own estimator from the site count.
th_allel_byhand = ts.num_sites / (a_n * L)
rec("theta_allel_recomputed_by_hand", "%.12e" % th_allel_byhand)
rec("a_n", "%.6f" % a_n)
rec("S_tskit_count", "%.1f" % S_tskit)
rec("S_allel_sites", "%d" % ts.num_sites)
rec("theta_tskit", "%.12e" % th_tskit)
rec("theta_scikit_allel", "%.12e" % th_allel)
span_err = abs(th_allel - th_allel_byhand) / th_allel_byhand
rec("allel_span_convention", "stop-start+1; recomputed vs reported rel %.3e" % span_err)
if span_err > 1e-12:
    die("scikit-allel's watterson_theta is %.12e but S_sites/(a_n*L) is %.12e (rel %.3e) "
        "-- the start/stop interval is not spanning exactly L sites, so every per-site "
        "comparison below is off by that factor" % (th_allel, th_allel_byhand, span_err))

ratio = th_tskit / th_allel
predicted = ts.num_mutations / ts.num_sites
rec("theta_ratio", "%.12f" % ratio)
rec("mutations_over_sites", "%.12f" % predicted)
rec("ratio_minus_prediction", "%.3e" % abs(ratio - predicted))
if abs(ratio - predicted) > 1e-9:
    die("the tskit/allel Watterson ratio is %.12f but num_mutations/num_sites is %.12f; "
        "the gap is then NOT just the mutation-vs-site counting convention and needs a "
        "different explanation" % (ratio, predicted))
rec("accounting_watterson",
    "the whole tskit-vs-allel gap is num_mutations/num_sites = %.6f" % predicted)
if S_tskit != float(ts.num_mutations):
    die("tskit's segregating_sites count is %.1f, not the mutation count %d, so the "
        "explanation above does not hold" % (S_tskit, ts.num_mutations))

# ---------------- 7. the closed form, with a bound from the sampling distribution
# E[pi] = 4*Ne*mu per site. One replicate is far too variable to assert (measured
# 0.81-1.04), so the claim is about the MEAN of NREP replicates, bounded by its own
# standard error. A biased estimator would sit many SE away; coalescent variance would not.
expected = 4 * NE * MU
ratios = np.array([sim(s).diversity(mode="site") / expected for s in range(1, NREP + 1)])
if not np.all(np.isfinite(ratios)):
    die("a non-finite diversity among the replicates")
mean, sd = float(ratios.mean()), float(ratios.std(ddof=1))
se = sd / np.sqrt(NREP)
dev = abs(mean - 1.0) / se
rec("closed_form_4Ne_mu", "%.6e" % expected)
rec("replicates", "%d" % NREP)
rec("pi_over_4Nemu_mean", "%.4f" % mean)
rec("pi_over_4Nemu_sd", "%.4f" % sd)
rec("pi_over_4Nemu_se", "%.4f" % se)
rec("pi_single_replicate_range", "%.4f .. %.4f" % (ratios.min(), ratios.max()))
rec("deviation_in_se", "%.2f" % dev)
if dev > 4.0:
    die("the mean of %d replicates is %.4f of 4*Ne*mu, %.2f standard errors from 1.0; "
        "coalescent variance does not explain that" % (NREP, mean, dev))
rec("closed_form_check", "mean within %.2f SE of 4*Ne*mu (bound: 4 SE)" % dev)

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("POPGEN OK")
