"""cp2k-real: CP2K's own 64-water benchmarks, with the checks CP2K ships for them.

    python3 -u real.py <data dir> <out dir> <jobset>       jobset: probe | full

Two workloads, both from benchmarks/ at tag v2026.2 (staged, pinned):

  ref      benchmarks/QS_reference/w64{PBE,SCAN,PBE0,GAPW,ccGRB}.inp: 64 waters, 10 MD steps at
           production settings, each with the output CP2K's developers committed beside it
           (`.ref`). The final-step energies are compared with that output. A 10-step MD is a
           path and the reference came from another build, so the deviation is REPORTED.
  release  benchmarks/QS/H2O-64.inp under five MPI x OpenMP decompositions of the same cores.
           benchmarks/QS/check-release-comparison.py is CP2K's own release check: every
           decomposition's final total energy must lie within 1e-10 Ha of their mean. That
           criterion is ASSERTED here, with upstream's regex for the energy.

Asserted on every run: exit 0, CP2K's `PROGRAM ENDED` sentinel, the MPI process count and
OpenMP thread count CP2K reports equal to what was launched, 11 total energies (step 0..10) and an
11-row .ener file. Reference values are read from the staged files at run time; none is written
here.
"""
import ctypes
import glob
import os
import re
import shutil
import subprocess
import sys
import time

DATA, OUT, JOBSET = sys.argv[1], sys.argv[2], sys.argv[3]
P = os.environ.get("CONDA_PREFIX") or "/opt/conda"
WORK = "/var/tmp/cp2k-real"
NCPU = int(os.environ.get("CP2K_REAL_CPUS", "16"))

REF = ["w64PBE", "w64SCAN", "w64PBE0", "w64GAPW", "w64ccGRB"]
DECOMP = [(NCPU, 1), (NCPU // 2, 2), (NCPU // 4, 4), (NCPU // 8, 8), (1, NCPU)]
if JOBSET == "probe":
    JOBS = [("ref", "w64PBE", NCPU, 1), ("release", "H2O-64", NCPU, 1)]
else:
    JOBS = [("ref", n, NCPU, 1) for n in REF] + [("release", "H2O-64", r, t) for r, t in DECOMP]

# check-release-comparison.py's criterion (v2026.2). Its pattern, `ENERGY\| Total FORCE_EVAL.+?:`,
# needs a colon that 2026.2's line (`ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]   -1101.03...`)
# no longer prints, so it matches nothing; this one takes the number after the unit instead.
TOTAL_FORCE_EVAL_RE = re.compile(r"^\s*ENERGY\| Total FORCE_EVAL.+?[\]:]\s*(?P<energy>\S+)\s*$", re.MULTILINE)
RELEASE_TOL = 1e-10

os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "real.log"), "a")
fails = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def check(ok, what):
    if not bool(ok):
        fails.append(what)
        say("FAIL:", what)


def grab(pat, text, cast=str, last=False):
    m = re.findall(pat, text, re.MULTILINE)
    if not m:
        return None
    return cast(m[-1] if last else m[0])


def corename():
    for lib in sorted(glob.glob(os.path.join(P, "lib", "libopenblas*.so*"))):
        try:
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            return "%s (%s)" % (f().decode(), os.path.basename(lib))
        except Exception as e:  # a description must never fail the run
            last = str(e)
    return "unknown"


say("openblas_corename", corename())
say("jobset", JOBSET, "cpus", NCPU, "jobs", len(JOBS))

rows = []
for kind, name, ranks, threads in JOBS:
    job = "%s-%s-%dx%d" % (kind, name, ranks, threads)
    wd = os.path.join(WORK, job)
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd)
    inp = name + ".inp"
    shutil.copy(os.path.join(DATA, inp), wd)
    if kind == "ref":
        shutil.copy(os.path.join(DATA, "geom.xyz"), wd)
    env = dict(os.environ, OMP_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads))
    cmd = ["mpiexec", "--oversubscribe", "--bind-to", "none", "-n", str(ranks),
           "cp2k.psmp", "-i", inp, "-o", "out.txt"]
    say("== %s: %s" % (job, " ".join(cmd)))
    t0 = time.monotonic()
    with open(os.path.join(wd, "stdout.txt"), "w") as so:
        p = subprocess.Popen(cmd, cwd=wd, env=env, stdout=so, stderr=subprocess.STDOUT)
        _, status, ru = os.wait4(p.pid, 0)
    wall = time.monotonic() - t0
    rc = os.waitstatus_to_exitcode(status)
    out = open(os.path.join(wd, "out.txt"), errors="replace").read() if os.path.exists(os.path.join(wd, "out.txt")) else ""
    jo = os.path.join(OUT, job)
    os.makedirs(jo, exist_ok=True)
    for f in ["out.txt", "stdout.txt"] + [x for x in os.listdir(wd) if x.endswith(".ener")]:
        if os.path.exists(os.path.join(wd, f)):
            shutil.copy(os.path.join(wd, f), jo)

    e = [float(m["energy"]) for m in TOTAL_FORCE_EVAL_RE.finditer(out)]
    ener = []
    for f in glob.glob(os.path.join(wd, "*-1.ener")):
        ener = [l.split() for l in open(f) if l.strip() and not l.startswith("#")]
    cons = [float(r[5]) for r in ener]
    r = {
        "job": job, "kind": kind, "input": name, "ranks": ranks, "threads": threads, "exit": rc,
        "wall_s": "%.1f" % wall,
        "cp2k_total_s": grab(r"^ CP2K\s+1\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)", out, str, last=True),
        "ranks_reported": grab(r"GLOBAL\| Total number of message passing processes\s+(\d+)", out, int),
        "threads_reported": grab(r"GLOBAL\| Number of threads for this process\s+(\d+)", out, int),
        "version": grab(r"CP2K\| version string:\s+CP2K version (\S+)", out),
        "cpu_model": grab(r"GLOBAL\| CPU model name\s+(.+?)\s*$", out),
        "n_energies": len(e),
        "e_step0": repr(e[0]) if e else "",
        "e_final": repr(e[-1]) if e else "",
        "md_pot_final": grab(r"MD\| Potential energy \[hartree\]\s+(\S+)", out, str, last=True),
        "md_pot_avg": grab(r"MD\| Potential energy \[hartree\]\s+\S+\s+(\S+)", out, str, last=True),
        "md_cons_final": grab(r"MD\| Conserved quantity \[hartree\]\s+(\S+)", out, str, last=True),
        "ener_rows": len(ener),
        "cons_maxdev": "%.3e" % max(abs(c - cons[0]) for c in cons) if cons else "",
        "scf_not_converged": len(re.findall(r"SCF run NOT converged|outer SCF loop FAILED", out)),
        "cp2k_peak_proc_mib": max([int(x) for x in re.findall(r"Estimated peak process memory.*?(\d+)\s*$", out, re.MULTILINE)] or [0]),
        "maxrss_gib": "%.2f" % (ru.ru_maxrss / 1048576.0),
        "ended": "PROGRAM ENDED AT" in out,
    }
    rows.append(r)
    say("  " + "  ".join("%s=%s" % (k, r[k]) for k in r if k not in ("job",)))

    check(rc == 0, "%s exit %s" % (job, rc))
    check(r["ended"], "%s: no PROGRAM ENDED sentinel" % job)
    check(r["ranks_reported"] == ranks, "%s: CP2K reports %s MPI processes, launched %d" % (job, r["ranks_reported"], ranks))
    check(r["threads_reported"] == threads, "%s: CP2K reports %s threads, launched %d" % (job, r["threads_reported"], threads))
    check(r["version"] == "2026.2", "%s: version %s" % (job, r["version"]))
    check(len(e) == 11, "%s: %d total energies, want 11 (steps 0..10)" % (job, len(e)))
    check(len(ener) == 11, "%s: %d .ener rows, want 11" % (job, len(ener)))
    # The reference inputs converge every SCF (EPS_SCF 1e-7, outer loop); that is what makes their
    # step 0 a fixed point. H2O-64 sets IGNORE_CONVERGENCE_FAILURE, so its count is only reported.
    if kind == "ref":
        check(r["scf_not_converged"] == 0, "%s: %d SCF runs not converged" % (job, r["scf_not_converged"]))
    shutil.rmtree(wd, ignore_errors=True)   # built by this task, not staged

cols = list(rows[0].keys())
with open(os.path.join(OUT, "runs.tsv"), "w") as f:
    f.write("\t".join(cols) + "\n")
    for r in rows:
        f.write("\t".join(str(r[c]) for c in cols) + "\n")

# --- against the committed reference outputs: reported, not asserted -------------------------
with open(os.path.join(OUT, "vs-reference.tsv"), "w") as f:
    f.write("input\tquantity\tours\treference\tours_minus_ref\tref_build\n")
    for r in rows:
        if r["kind"] != "ref":
            continue
        ref = open(os.path.join(DATA, r["input"] + ".ref"), errors="replace").read()
        rb = "%s %sx%s" % (grab(r"CP2K version (\S+)", ref),
                           grab(r"Total number of message passing processes\s+(\d+)", ref),
                           grab(r"Number of threads for this process\s+(\d+)", ref))
        for q, pat in [("pot_final", r"MD\| Potential energy \[hartree\]\s+(\S+)"),
                       ("pot_avg", r"MD\| Potential energy \[hartree\]\s+\S+\s+(\S+)"),
                       ("cons_final", r"MD\| Conserved quantity \[hartree\]\s+(\S+)")]:
            ours = r[{"pot_final": "md_pot_final", "pot_avg": "md_pot_avg", "cons_final": "md_cons_final"}[q]]
            theirs = grab(pat, ref, str, last=True)
            d = "%.3e" % (float(ours) - float(theirs)) if ours and theirs else ""
            f.write("\t".join(str(x) for x in (r["input"], q, ours, theirs, d, rb)) + "\n")
            say("vs-ref %-9s %-10s ours %s ref %s diff %s (ref: %s)" % (r["input"], q, ours, theirs, d, rb))

# --- CP2K's release comparison, asserted ------------------------------------------------------
rel = [r for r in rows if r["kind"] == "release" and r["e_final"]]
if len(rel) > 1:
    fin = [float(r["e_final"]) for r in rel]
    s0 = [float(r["e_step0"]) for r in rel]
    mean = sum(fin) / len(fin)
    with open(os.path.join(OUT, "release-comparison.tsv"), "w") as f:
        f.write("ranks\tthreads\te_step0\te_final\tfinal_minus_mean\n")
        for r, x in zip(rel, fin):
            f.write("%d\t%d\t%s\t%s\t%.3e\n" % (r["ranks"], r["threads"], r["e_step0"], r["e_final"], x - mean))
            check(abs(x - mean) < RELEASE_TOL, "release check: %dx%d final %r is %.3e from the mean (upstream: <1e-10)" % (r["ranks"], r["threads"], x, x - mean))
    say("release: %d decompositions, final spread %.3e Ha, max |final-mean| %.3e, step-0 spread %.3e Ha"
        % (len(fin), max(fin) - min(fin), max(abs(x - mean) for x in fin), max(s0) - min(s0)))
elif JOBSET == "full":
    check(False, "release check: fewer than two decompositions produced an energy")

check(len(rows) == len(JOBS), "ran %d of %d jobs" % (len(rows), len(JOBS)))
if fails:
    say("FAILED: %d" % len(fails))
    sys.exit(1)
say("CP2K REAL OK")
