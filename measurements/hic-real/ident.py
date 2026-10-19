"""measurements/hic-real task 2's identities: our .mcool against 4DN's published one.
    ident.py <published.mcool> <ours.mcool> <contacts from task 1> <threads>
Exact at every resolution: same resolutions, chroms, bins and pixels (bin1_id, bin2_id, count);
every resolution's total equals the contact count task 1 produced; every pixel upper-triangle;
the genome-wide 1 Mb matrix symmetric. Then ICE balancing at three resolutions with the parameters
4DN recorded on its own weights, compared with 4DN's weights."""
import hashlib
import resource
import subprocess
import sys
import time

import h5py
import numpy as np

PUB, OURS, NC, T = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
O = "/tmp/out"
CH = 10_000_000


def rec(k, v):
    line = "%s\t%s" % (k, str(v))
    print(line, flush=True)
    with open(O + "/check.txt", "a") as fh:
        fh.write(line + "\n")


def check(ok, msg):
    if not bool(ok):
        rec("FAIL", msg)
        sys.exit(1)


def res_list(f):
    return sorted(f["resolutions"].keys(), key=int)


P, Q = h5py.File(PUB, "r"), h5py.File(OURS, "r")
RES = res_list(P)
check(res_list(Q) == RES, "resolutions differ: ours %s, 4DN %s" % (res_list(Q), RES))

digests = open(O + "/digests.tsv", "w")
for r in RES:
    p, q = P["resolutions"][r], Q["resolutions"][r]
    for ds in ("chroms/name", "chroms/length", "bins/chrom", "bins/start", "bins/end"):
        check(np.array_equal(p[ds][:], q[ds][:]), "res %s: %s differs from 4DN's" % (r, ds))
    nnz = q["pixels/count"].shape[0]
    check(p["pixels/count"].shape[0] == nnz, "res %s: nnz %d, 4DN has %d" % (r, nnz, p["pixels/count"].shape[0]))
    total, pub_total, below = 0, 0, 0
    h = {ds: hashlib.sha256() for ds in ("bin1_id", "bin2_id", "count")}
    for i in range(0, nnz, CH):
        blk = {}
        for ds in ("bin1_id", "bin2_id", "count"):
            a, b = q["pixels/" + ds][i:i + CH], p["pixels/" + ds][i:i + CH]
            check(np.array_equal(a, b), "res %s: pixels/%s differs from 4DN's in rows %d.." % (r, ds, i))
            h[ds].update(np.ascontiguousarray(a, dtype=np.int64).tobytes())
            blk[ds] = a
        total += int(blk["count"].sum(dtype=np.int64))
        pub_total += int(p["pixels/count"][i:i + CH].sum(dtype=np.int64))
        below += int(np.count_nonzero(blk["bin1_id"] > blk["bin2_id"]))
    check(total == NC, "res %s: %d contacts, task 1 produced %d" % (r, total, NC))
    check(below == 0, "res %s: %d pixels below the diagonal" % (r, below))
    rec("res_%s" % r, "bins %d  nnz %d  total %d  == 4DN's pixel for pixel" % (q["bins/start"].shape[0], nnz, total))
    digests.write("%s\t%s\n" % (r, "\t".join(h[ds].hexdigest() for ds in ("bin1_id", "bin2_id", "count"))))
digests.close()
rec("identity_published", "every pixel at all %d resolutions equals 4DN's 4DNFIFBPTHCU" % len(RES))
rec("identity_conserved", "total == %d contacts at every resolution, %s to %s bp" % (NC, RES[0], RES[-1]))
rec("identity_upper_triangle", "no pixel below the diagonal at any resolution")
Q.close()

import cooler  # noqa: E402

SYM = "1000000" if "1000000" in RES else RES[len(RES) // 2]
M = np.nan_to_num(cooler.Cooler(OURS + "::resolutions/" + SYM).matrix(balance=False)[:, :], nan=0.0)
nz, asym = int(np.count_nonzero(M)), float(np.max(np.abs(M - M.T)))
rec("symmetry_%s" % SYM, "shape %s  nonzeros %d  max|M-M.T| %.1f" % (M.shape, nz, asym))
check(nz >= 1000, "only %d nonzero cells: the symmetry check would be near-vacuous" % nz)
check(asym == 0.0, "the matrix is not symmetric")
del M

# ICE balancing with the parameters 4DN recorded on its own weights. A converged ICE is a fixed
# point; the stop rule is var(marginals) < tol, so two converged solutions may differ by order
# sqrt(tol) relative -- that, not the agreement observed, is the bound.
for r in [x for x in ("1000000", "100000", "10000") if x in RES]:
    a = dict(P["resolutions"][r]["bins/weight"].attrs)
    rec("pub_weight_attrs_%s" % r, " ".join("%s=%s" % (k, a[k]) for k in sorted(a)))
    cmd = ["cooler", "balance", "-p", str(T), "--force", "--max-iters", "1000",
           "--ignore-diags", str(int(a.get("ignore_diags", 2))), "--mad-max", str(int(a.get("mad_max", 5))),
           "--min-nnz", str(int(a.get("min_nnz", 10))), "--min-count", str(int(a.get("min_count", 0))),
           "--tol", repr(float(a.get("tol", 1e-5)))]
    if bool(a.get("cis_only", False)):
        cmd.append("--cis-only")
    if bool(a.get("divisive_weights", False)):
        cmd.append("--divisive-weights")
    cmd.append(OURS + "::resolutions/" + r)
    t0 = time.time()
    rc = subprocess.call(cmd, stdout=open(O + "/balance-%s.log" % r, "w"), stderr=subprocess.STDOUT)
    w = time.time() - t0
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    with open(O + "/timing.tsv", "a") as fh:
        fh.write("balance_%s\t%.1f\t%d\t%d\t%.1f\n" % (r, w, rc, ru.ru_maxrss, ru.ru_utime + ru.ru_stime))
    check(rc == 0, "cooler balance at %s exited %d" % (r, rc))
    with h5py.File(OURS, "r") as Q:
        wq = Q["resolutions"][r]["bins/weight"][:]
        qa = dict(Q["resolutions"][r]["bins/weight"].attrs)
    wp = P["resolutions"][r]["bins/weight"][:]
    np.save(O + "/weight-%s.npy" % r, wq)
    check(bool(qa.get("converged")), "res %s: our balance did not converge" % r)
    check(np.array_equal(np.isnan(wq), np.isnan(wp)), "res %s: masked bins differ from 4DN's" % r)
    f = ~np.isnan(wp)
    rel = float(np.max(np.abs(wq[f] - wp[f]) / np.abs(wp[f])))
    bound = 10 * float(np.sqrt(a.get("tol", 1e-5)))
    rec("balance_%s" % r, "masked %d of %d  max rel |w - w_4DN| %.3e  (bound %.1e)  var %s  converged %s"
        % (int((~f).sum()), wp.size, rel, bound, qa.get("var"), qa.get("converged")))
    check(rel < bound, "res %s: weights differ from 4DN's by %.3e relative" % (r, rel))
rec("identity_balance", "ICE weights converge to 4DN's within 10*sqrt(tol), same masked bins")
print("IDENTITIES OK")
