"""measurements/warpx-real checks.

  check.py run <tsv> <cmd...>      run cmd; record wall, peak RSS (ru_maxrss) and CPU time
  check.py check <outdir> <rf periods> <run labels...>

Assertions (benchmark length, 1280 RF periods): the run completed to its final step, WarpX
reports the thread count launched, the time-averaged ion density is a finite positive 129-node
profile, upstream's analysis script passes, and an independent recomputation of its RMS error
against Turner et al. (2013) is under upstream's tolerance. Everything else is reported.
"""
import ast
import os
import re
import resource
import subprocess
import sys
import time

FAILS = []


def rec(fh, k, v):
    line = "%s\t%s" % (k, str(v))
    print(line)
    fh.write(line + "\n")


def need(fh, name, ok, detail):
    ok = bool(ok)
    rec(fh, "%s %s" % ("PASS" if ok else "FAIL", name), detail)
    if not ok:
        FAILS.append(name)


def cg(name):
    try:
        return open("/sys/fs/cgroup/" + name).read()
    except Exception:
        return ""


def cpu_usec():
    m = re.search(r"usage_usec (\d+)", cg("cpu.stat"))
    return int(m.group(1)) if m else -1


def run(tsv, cmd):
    c0, t0 = cpu_usec(), time.monotonic()
    rc = subprocess.call(cmd)
    wall = time.monotonic() - t0
    c1 = cpu_usec()
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    with open(tsv, "w") as fh:
        rec(fh, "rc", rc)
        rec(fh, "wall_s", "%.2f" % wall)
        rec(fh, "maxrss_mib", "%.1f" % (ru.ru_maxrss / 1024.0))
        rec(fh, "cpu_user_s", "%.1f" % ru.ru_utime)
        rec(fh, "avg_cores", "%.2f" % ((c1 - c0) / 1e6 / wall) if c0 >= 0 and wall > 0 else "unknown")
        rec(fh, "cgroup_memory_peak_mib", "%.1f" % (int(cg("memory.peak")) / 2**20) if cg("memory.peak").strip() else "unknown")
        rec(fh, "omp_num_threads", os.environ.get("OMP_NUM_THREADS", "unset"))
    return rc


def turner():
    """Turner's case-1 profile and upstream's tolerance, read from the pinned analysis script."""
    tree = ast.parse(open("/tmp/analysis_1d.py").read())
    ref = tol = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "ref_density":
                ref = [ast.literal_eval(e) for e in node.value.args[0].elts]
            if node.targets[0].id == "tolerance":
                tol = ast.literal_eval(node.value)
    return ref, tol


def check(out, cyc, runs):
    import numpy as np

    real = cyc == 1280
    freq = 13.56e6
    # The deck's own arithmetic for case 1, so the expected final step matches its int() truncation.
    dt = 1.0 / (np.array([400, 800, 1600, 3200]) * freq)[0]
    max_steps = int((np.array([cyc, 5120, 5120, 15360]) / freq)[0] / dt)
    ref, tol = turner()
    ref = np.array(ref)
    fh = open(os.path.join(out, "results.tsv"), "w")
    rec(fh, "rf_periods", cyc)
    rec(fh, "expected_steps", max_steps)
    rec(fh, "turner_nodes", ref.size)
    rec(fh, "upstream_tolerance", tol)
    need(fh, "upstream tolerance read from the pinned script", tol == 0.065 and ref.size == 129, "%s, %d nodes" % (tol, ref.size))
    prof = {}
    for r in runs:
        t = r.lstrip("r")
        log = open(os.path.join(out, "warpx-t%s.log" % r), errors="replace").read()
        kv = dict(l.split("\t", 1) for l in open(os.path.join(out, "run-t%s.tsv" % r)).read().splitlines() if "\t" in l)
        for k in ("wall_s", "maxrss_mib", "cgroup_memory_peak_mib", "avg_cores", "cpu_user_s"):
            rec(fh, "%s t%s" % (k, r), kv.get(k, "missing"))
        need(fh, "t%s exit 0" % r, kv.get("rc") == "0", kv.get("rc"))
        m = re.findall(r"OMP initialized with (\d+) OMP threads", log)
        need(fh, "t%s WarpX reports the threads launched" % r, m and m[-1] == t, "reported %s, launched %s" % (m[-1] if m else "nothing", t))
        g = re.findall(r"Level 0\s+(\d+) grids", log)
        rec(fh, "grids t%s" % r, g[-1] if g else "not printed")
        diags = open(os.path.join(out, "diags-t%s.txt" % r)).read().split() if os.path.exists(os.path.join(out, "diags-t%s.txt" % r)) else []
        need(fh, "t%s final-step diagnostic written" % r, ("diag1%06d" % max_steps) in diags, " ".join(diags) or "none")
        f = os.path.join(out, "ion_density_t%s.npy" % r)
        if not os.path.exists(f):
            need(fh, "t%s ion density saved" % r, False, "absent")
            continue
        n = np.load(f)
        need(fh, "t%s ion density is a finite positive 129-node profile" % r,
             n.shape == (129,) and np.all(np.isfinite(n)) and np.all(n > 0), "shape %s, min %.4e" % (n.shape, float(np.nanmin(n))))
        prof[r] = n
        z = np.linspace(0.0, 0.067, n.size)
        rg = np.interp(z, np.linspace(0.0, 0.067, ref.size), ref)
        e = np.abs(n[1:-1] - rg[1:-1]) / rg[1:-1]
        rms = float(np.sqrt(np.mean(e ** 2)))
        rec(fh, "t%s max_density" % r, "%.6e" % float(n.max()))
        rec(fh, "t%s max_rel_err_interior" % r, "%.4f" % float(e.max()))
        sym = float(np.sqrt(np.mean(((n - n[::-1]) / (0.5 * (n + n[::-1]))) [1:-1] ** 2)))
        rec(fh, "t%s symmetry_rms_rel n(z) vs n(L-z)" % r, "%.4f" % sym)
        if real:
            need(fh, "t%s RMS error vs Turner et al. 2013 < upstream's %s" % (r, tol), rms < tol, "%.4f" % rms)
            need(fh, "t%s upstream analysis_1d.py passes" % r, kv.get("upstream_analysis_rc") == "0", kv.get("upstream_analysis_rc", "not run"))
        else:
            rec(fh, "t%s rms_rel_err_vs_turner (probe, reported)" % r, "%.4f" % rms)
    labels = list(prof)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            same = prof[a].tobytes() == prof[b].tobytes()
            d = float(np.sqrt(np.mean(((prof[a] - prof[b]) / prof[b]) ** 2)))
            rec(fh, "profile t%s vs t%s" % (a, b), "byte-identical" if same else "rms_rel %.4e" % d)
    rec(fh, "fails", len(FAILS))
    fh.close()
    return 1 if FAILS else 0


if __name__ == "__main__":
    if sys.argv[1] == "run":
        sys.exit(run(sys.argv[2], sys.argv[3:]))
    sys.exit(check(sys.argv[2], int(sys.argv[3]), sys.argv[4:]))
