#!/usr/bin/env python3
"""siesta-real: SIESTA 5.4.2's own test suite against its committed references, then the suite's
1,359-atom Si987H372 quantum dot run as the full calculation the test file describes.

    python3 -u real.py <suite dir> <work dir> <out dir> <ranks per test> <tests at once> <qdot ranks>

Part 1 copies the suite's execution model from cmake/tests/SiestaTests.cmake rather than inventing
one: every siesta_subtest() in Tests/*/CMakeLists.txt whose if() guard the build satisfies runs as

    SIESTA_PS_PATH=Tests/Pseudos mpiexec -n N <exe> -out <name>_mpiN.out [EXTRA_ARGS] ../<name>.fdf

in <dir>/<name>_mpiN/, after the directory's *.fdf are copied to <dir>/ and FILES_SETUP into the
work directory, with FIXTURES_REQUIRED respected. N is SIESTA_TESTS_MPI_NUMPROCS (upstream's
default is 4). Each output is then checked by the suite's own yaml_compare.py with its own
test-cfg.yml tolerances against Reference/<name>.out, exactly as siesta_verifyout() calls it.

Part 2 is 12.Solvers/si-qdot-elpa-native.fdf, the CSIRO Si987H372 dot. Upstream's test stops it
after one SCF step ("For a quicker test. Remove if full test is desired"). Here it runs (a) as the
quick test with the build's default eigensolver and with MRRR, both against the committed one-step
reference, (b) with the quick block removed: converged SCF and forces, and (c) as (b) at the
production DZP basis (14,691 orbitals), once with the build's default eigensolver and once with
ELPA through ELSI -- two eigensolver libraries that must reach one fixed point.

A test SIESTA stops at MaxSCFIterations before convergence (it prints SCF_NOT_CONV) is a point on
the SCF path, not the fixed point. Those are reported against their references, not asserted.
"""
import ctypes
import glob
import os
import re
import resource
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

SUITE, WORK, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
NP, CONC, QNP = int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
TESTS = os.path.join(SUITE, "tests")
PSEUDOS = os.path.join(TESTS, "Pseudos")
DIRS = ["00.BasisSets", "01.PseudoPotentials", "02.SpinPolarization", "03.SpinOrbit",
        "04.SCFMixing", "05.Bands", "06.DensityOfStates", "07.ForceConstants",
        "08.GeometryOptimization", "09.MolecularDynamics", "10.Functionals",
        "11.ElectronicProperties", "12.Solvers", "13.MiscOptions", "14.FileIO", "15.TDDFT",
        "16.TranSiesta", "17.Wannier90", "Dependency_Tests"]    # Tests/CMakeLists.txt's order
LOG = open(os.path.join(OUT, "real.log"), "w")
FAILS = []


def rec(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def check(ok, what):
    if not bool(ok):
        FAILS.append(what)
        rec("FAIL:", what)


# ---------------------------------------------------------------- build facts
SIESTA = shutil.which("siesta")
TBTRANS = shutil.which("tbtrans")
rec("siesta", SIESTA, "tbtrans", TBTRANS, "mpiexec", shutil.which("mpiexec"))
os.makedirs(os.path.join(WORK, "hdr"), exist_ok=True)
open(os.path.join(WORK, "hdr", "empty.fdf"), "w").close()
try:   # the header is printed before input is read; an empty input then stops the run
    hdr = subprocess.run([SIESTA, "-out", "hdr.out", "empty.fdf"], cwd=os.path.join(WORK, "hdr"),
                         capture_output=True, text=True, timeout=120).stdout
except subprocess.TimeoutExpired:
    hdr = ""
hp = os.path.join(WORK, "hdr", "hdr.out")
htext = open(hp, errors="replace").read() if os.path.exists(hp) else hdr
head = htext.split("Runtime information")[0]
with open(os.path.join(OUT, "siesta-header.txt"), "w") as fh:
    fh.write(head)
rec("== siesta build header ==")
for line in head.splitlines():
    rec("  " + line)
FEAT = {
    "SIESTA_WITH_ELSI": "ELSI support" in head,
    "ELSI_HAS_PEXSI": "PEXSI" in "".join(re.findall(r"ELSI support.*\n((?:[ \t]+\S.*\n)*)", head)),
    "ELSI_ELPA_HAS_GPU_SUPPORT": False,
    "SIESTA_ELPA_HAS_GPU_SUPPORT": False,
    "SIESTA_WITH_PEXSI": bool(re.search(r"^\s*(Native )?PEXSI support", head, re.M)),
    "SIESTA_WITH_ELPA": "Native ELPA support" in head,
    "SIESTA_WITH_NCDF": "NetCDF support" in head,
    "SIESTA_WITH_WANNIER90": "Wannier90" in head,
    "SIESTA_WITH_DFTD3": "DFT-D3 support" in head,
    "SIESTA_WITH_FLOOK": "Lua support" in head,
}
m = re.search(r"Version\s*:\s*(\S+)", head)
rec("siesta_version", m.group(1) if m else "unknown")
check(m and m.group(1).startswith("5.4.2"), "siesta reports version 5.4.2")
for k in sorted(FEAT):
    rec("feature", k, FEAT[k])
try:
    lib = glob.glob(os.path.join(os.path.dirname(os.path.dirname(SIESTA)), "lib", "libopenblas*.so*"))[0]
    ob = ctypes.CDLL(lib)
    ob.openblas_get_corename.restype = ctypes.c_char_p
    ob.openblas_get_config.restype = ctypes.c_char_p
    rec("openblas_corename", ob.openblas_get_corename().decode())
    rec("openblas_config", ob.openblas_get_config().decode())
except Exception as e:  # noqa: BLE001 -- a description of the run must never fail it
    rec("openblas_corename unknown (%s)" % str(e))
ldd = subprocess.run(["ldd", SIESTA], capture_output=True, text=True).stdout
with open(os.path.join(OUT, "siesta-ldd.txt"), "w") as fh:
    fh.write(ldd)
rec("ldd_blas", " ".join(sorted({l.split()[0] for l in ldd.splitlines()
                                 if re.search(r"blas|lapack|scalapack|elpa|elsi|xc", l)})))


# ---------------------------------------------------------------- CMakeLists -> tests
def cmake_commands(text):
    """(name, raw args) for each top-level command, comments stripped."""
    text = re.sub(r"#[^\n]*", "", text)
    out, i = [], 0
    for m in re.finditer(r"([A-Za-z_]+)\s*\(", text):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        out.append((m.group(1).lower(), text[m.end():j - 1]))
        i = j
    return out


def cmake_args(raw, var):
    toks = re.findall(r'"[^"]*"|[^\s]+', raw)
    out = []
    for t in toks:
        quoted = t.startswith('"')
        t = t.strip('"')
        t = re.sub(r"\$\{(\w+)\}", lambda m: str(var.get(m.group(1), "")), t)
        out.append((t, quoted))
    return out


def cond(args, var):
    words = [a for a, _ in args]
    neg = words[:1] == ["NOT"]
    if neg:
        words = words[1:]
    if len(words) == 3 and words[1] == "GREATER":
        v = int(var.get(words[0], words[0])) > int(words[2])
    else:
        v = bool(FEAT.get(words[0], var.get(words[0], False)))
    return (not v) if neg else v


ONE = {"NAME", "MPI_NPROC", "MPI_MINPROCS", "PSEUDO_DIR", "NAME_ALTER", "EXEC", "TEST_NAME",
       "TEST_WORKING_DIRECTORY"}
MULTI = {"FILES_SETUP", "FILES_CLEANUP", "LABELS", "EXTRA_ARGS", "FIXTURES_SETUP",
         "FIXTURES_REQUIRED", "FIXTURES_CLEANUP"}
tests, skipped = [], []
var = {"PROJECT_NAME": "siesta", "SIESTA_TESTS_MPI_NUMPROCS": NP}
for d in DIRS:
    src = os.path.join(TESTS, d)
    stack = [True]
    for cmd, raw in cmake_commands(open(os.path.join(src, "CMakeLists.txt")).read()):
        args = cmake_args(raw, var)
        if cmd == "if":
            stack.append(stack[-1] and cond(args, var))
            continue
        if cmd == "else":
            prev = stack.pop()
            parent = stack[-1]
            stack.append(parent and not prev)    # one-level if/else is all the suite uses
            continue
        if cmd == "endif":
            stack.pop()
            continue
        if cmd == "set" and all(stack):
            var[args[0][0]] = args[1][0]
            continue
        if cmd != "siesta_subtest":
            continue
        t = {k: [] for k in MULTI}
        key = None
        for a, quoted in args:
            if not quoted and a in ONE | MULTI | {"SERIAL"}:
                key = a
                if a == "SERIAL":
                    t["SERIAL"] = True
                    key = None
                continue
            if key in ONE:
                t[key] = a
                key = None
            elif key in MULTI:
                t[key].append(a)
            else:
                t["NAME"] = a
        if not all(stack):
            skipped.append("%s/%s (build lacks the guard)" % (d, t["NAME"]))
            continue
        n = int(t.get("MPI_NPROC", NP))
        n = min(n, NP)
        if n < int(t.get("MPI_MINPROCS", 0)):
            skipped.append("%s/%s (MPI_MINPROCS)" % (d, t["NAME"]))
            continue
        if t.get("SERIAL"):
            n = 1
        alt = t.get("NAME_ALTER", t["NAME"])
        label = alt + ("_mpi%d" % n if n > 1 else "")
        t.update(dir=d, n=n, alt=alt, label=label, bindir=os.path.join(WORK, "suite", d),
                 wdir=os.path.join(WORK, "suite", d, label))
        if "TEST_WORKING_DIRECTORY" in t:
            var[t["TEST_WORKING_DIRECTORY"]] = t["wdir"]
        tests.append(t)
rec("tests selected", len(tests), "skipped", len(skipped))
for s in skipped:
    rec("  skipped:", s)


# ---------------------------------------------------------------- run
ENV = dict(os.environ, SIESTA_PS_PATH=PSEUDOS, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
# Open MPI 5 counts slots by physical core and ignores OMPI_MCA_rmaps_base_oversubscribe, so 16
# ranks on an Intel 8i box (8 cores x 2 threads) are refused without :OVERSUBSCRIBE. --bind-to none
# because 4 concurrent mpiexecs would each bind their ranks to cores 0-3.
MPI = ["mpiexec", "--bind-to", "none", "--map-by", ":OVERSUBSCRIBE", "-n"]


# the -fdf-setup step: each directory's *.fdf into its binary directory, once and before any test
# starts, so no running SIESTA can read an input another thread is rewriting
for d in DIRS:
    os.makedirs(os.path.join(WORK, "suite", d), exist_ok=True)
    for f in glob.glob(os.path.join(TESTS, d, "*.fdf")):
        shutil.copy(f, os.path.join(WORK, "suite", d))


def prepare(t):
    src = os.path.join(TESTS, t["dir"])
    os.makedirs(t["wdir"], exist_ok=True)
    for f in t["FILES_SETUP"]:
        shutil.copy(f if os.path.isabs(f) else os.path.join(src, f), t["wdir"])


def run_test(t):
    try:
        prepare(t)
    except OSError as e:   # a FILES_SETUP another test should have produced
        t["rc"], t["wall"] = "setup-error", 0.0
        rec("setup error %s/%s: %s" % (t["dir"], t["label"], e))
        return t
    exe = TBTRANS if t.get("EXEC", "").endswith("tbtrans") else SIESTA
    extra = [w for a in t["EXTRA_ARGS"] for w in shlex.split(a)]
    argv = [exe, "-out", t["label"] + ".out"] + extra + ["../%s.fdf" % t["NAME"]]
    if t["n"] > 1:
        argv = MPI + [str(t["n"])] + argv
    t0 = time.time()
    with open(os.path.join(t["wdir"], "stdout.txt"), "w") as fh:
        rc = subprocess.run(argv, cwd=t["wdir"], env=ENV, stdout=fh, stderr=subprocess.STDOUT).returncode
    t["wall"] = time.time() - t0
    t["rc"] = rc
    return t


T0 = time.time()
done, state = set(), {}
fixture_of = {}
for t in tests:
    for f in t["FIXTURES_SETUP"]:
        fixture_of[f] = t["label"] + "@" + t["dir"]
key = lambda t: t["label"] + "@" + t["dir"]   # noqa: E731
pending = list(tests)
running = {}
with ThreadPoolExecutor(CONC) as pool:
    while pending or running:
        for t in list(pending):
            if len(running) >= CONC:
                break
            req = [fixture_of[f] for f in t["FIXTURES_REQUIRED"] if f in fixture_of]
            if all(r in done for r in req):
                pending.remove(t)
                if any(state.get(r) != 0 for r in req):
                    t["rc"], t["wall"] = "dependency-failed", 0
                    done.add(key(t))
                    state[key(t)] = "dependency-failed"
                    continue
                running[key(t)] = pool.submit(run_test, t)
        for k, fut in list(running.items()):
            if fut.done():
                t = fut.result()
                done.add(k)
                state[k] = t["rc"]
                rec("ran %-42s %-26s n=%d rc=%s %.1fs" % (t["label"], t["dir"], t["n"], t["rc"], t["wall"]))
                del running[k]
        time.sleep(0.2)
SUITE_WALL = time.time() - T0
rec("suite_run_wall_s %.0f" % SUITE_WALL)


# ---------------------------------------------------------------- verify, with upstream's tools
def digest(path):
    txt = subprocess.run(["awk", "-f", os.path.join(TESTS, "out_digest_yaml.awk"), path],
                         capture_output=True, text=True).stdout
    vals, sect = {}, None
    for line in txt.splitlines():
        if re.match(r"^\w+:$", line):
            sect = line[:-1]
            continue
        m = re.match(r"^\s+([\w-]+):\s+\[(.*)\]$", line)
        if m:
            for i, v in enumerate(m.group(2).split(",")):
                vals[(sect, m.group(1) + str(i))] = num(v)
            continue
        m = re.match(r"^\s+([\w-]+):\s+(\S+)$", line)
        if m:
            vals[(sect, m.group(1))] = num(m.group(2))
    return {k: v for k, v in vals.items() if v is not None}


def num(v):
    try:
        return float(v)
    except ValueError:   # a Fortran overflow field; yaml_compare would fail on it too
        return None


TOL = {}
for line in open(os.path.join(TESTS, "test-cfg.yml")):
    m = re.match(r"^\s+(\w+):\s+([0-9.eE+-]+)", line)
    if m and line.startswith("  "):
        TOL[m.group(1)] = float(m.group(2))
rows, dig = [], []
for t in tests:
    out = os.path.join(t["wdir"], t["label"] + ".out")
    ref = os.path.join(TESTS, t["dir"], "Reference", t["alt"] + ".out")
    otext = open(out, errors="replace").read() if os.path.exists(out) else ""
    # SIESTA's two completion lines: a normal end, and the deliberate stop of s_only/save_init_rho
    ended = ">> End of run" in otext or "Requested End of Run" in otext
    v = {"rc": t["rc"], "ended": ended, "notconv": "SCF_NOT_CONV" in otext, "verify": "no-reference", "compared": 0, "nonzero": 0,
         "worst": 0.0, "worst_key": "-"}
    if os.path.exists(ref) and os.path.exists(out):
        p = subprocess.run([sys.executable, os.path.join(TESTS, "yaml_compare.py"),
                            "-c", os.path.join(TESTS, "test-cfg.yml"),
                            "-p", os.path.join(TESTS, "out_digest_yaml.awk"),
                            "-t", out, "-r", ref, "-o", os.path.join(t["wdir"], t["alt"] + ".yml")],
                           cwd=t["wdir"], capture_output=True, text=True,
                           env=dict(os.environ, SCRIPTS_DIR=TESTS))
        with open(os.path.join(t["wdir"], "verify.txt"), "w") as fh:
            fh.write(p.stdout + p.stderr)
        v["verify"] = {0: "pass", 255: "etol"}.get(p.returncode, "error%d" % p.returncode)
        do, dr = digest(out), digest(ref)
        for (sect, k), rv in dr.items():
            if sect not in ("energies", "forces", "geometry", "mulliken", "spin", "optical"):
                continue
            if (sect, k) not in do:
                continue
            base = re.sub(r"\d+$", "", k) if sect == "geometry" else k
            tol = TOL.get(base, 1e-7)
            d = abs(do[(sect, k)] - rv)
            v["compared"] += 1
            v["nonzero"] += rv != 0.0
            if d / tol > v["worst"]:
                v["worst"], v["worst_key"] = d / tol, k
            dig.append((t["dir"], t["label"], sect, k, do[(sect, k)], rv, tol))
    elif os.path.exists(out):
        for (sect, k), ov in digest(out).items():
            dig.append((t["dir"], t["label"], sect, k, ov, "", ""))
    t.update(v)
    rows.append(t)
with open(os.path.join(OUT, "suite.tsv"), "w") as fh:
    fh.write("dir\ttest\tranks\trc\tended\tscf_not_conv\twall_s\tverify\tcompared\tnonzero_ref\tworst_diff_over_tol\tworst_key\n")
    for t in rows:
        fh.write("%s\t%s\t%d\t%s\t%s\t%s\t%.1f\t%s\t%d\t%d\t%.4f\t%s\n" % (
            t["dir"], t["label"], t["n"], t["rc"], t["ended"], t["notconv"], t["wall"], t["verify"],
            t["compared"], t["nonzero"], t["worst"], t["worst_key"]))
with open(os.path.join(OUT, "digest.tsv"), "w") as fh:
    fh.write("dir\ttest\tsection\tkey\tvalue\treference\ttolerance\n")
    for r in dig:
        fh.write("\t".join(str(x) for x in r) + "\n")

ran = [t for t in rows if t["rc"] == 0 and t["ended"]]
bad = [t for t in rows if not (t["rc"] == 0 and t["ended"])]
ver = [t for t in rows if t["verify"] != "no-reference"]
rec("suite: %d selected, %d ran to '>> End of run', %d did not" % (len(rows), len(ran), len(bad)))
for t in bad:
    rec("  did not complete: %s/%s rc=%s" % (t["dir"], t["label"], t["rc"]))
rec("verify: %d with a committed reference: %d pass, %d etol, %d other" % (
    len(ver), sum(t["verify"] == "pass" for t in ver), sum(t["verify"] == "etol" for t in ver),
    sum(t["verify"] not in ("pass", "etol") for t in ver)))
for t in ver:
    if t["verify"] != "pass":
        rec("  %s %s/%s worst %s at %.2f x tol" % (t["verify"], t["dir"], t["label"], t["worst_key"], t["worst"]))
rec("verify: %d reference values compared, %d of them non-zero in the reference" % (
    sum(t["compared"] for t in ver), sum(t["nonzero"] for t in ver)))
check(not bad, "every selected test runs to SIESTA's end-of-run line")
# A test SIESTA stopped at MaxSCFIterations before convergence (it prints SCF_NOT_CONV) reports a
# point on the SCF path, not the fixed point; the suite README says many tests are deliberately
# not converged. The converged ones are asserted against their references; the rest are reported.
conv = [t for t in ver if not t["notconv"]]
nconv = [t for t in ver if t["notconv"]]
rec("verify, SCF converged: %d tests, %d pass" % (len(conv), sum(t["verify"] == "pass" for t in conv)))
rec("verify, stopped before convergence (SCF_NOT_CONV): %d tests, %d pass, %d etol (reported)" % (
    len(nconv), sum(t["verify"] == "pass" for t in nconv), sum(t["verify"] == "etol" for t in nconv)))
check(all(t["verify"] == "pass" for t in conv),
      "every SCF-converged test with a reference passes upstream's yaml_compare")
# rank count from inside the run, for every MPI test
rk = 0
for t in ran:
    if t["n"] > 1:
        m = re.search(r"Running on (\d+) nodes", open(os.path.join(t["wdir"], t["label"] + ".out"), errors="replace").read())
        rk += bool(m and int(m.group(1)) == t["n"])
check(rk == sum(t["n"] > 1 for t in ran), "every MPI test reports 'Running on %d nodes'" % NP)
rec("rank count asserted in %d MPI tests" % rk)


# ---------------------------------------------------------------- part 2: the 1,359-atom dot
def total(path):
    m = re.findall(r"^siesta:\s+Total =\s+(-?[0-9.]+)", open(path, errors="replace").read(), re.M)
    return float(m[-1]) if m else None


QD = os.path.join(WORK, "qdot")
os.makedirs(QD, exist_ok=True)
src = os.path.join(TESTS, "12.Solvers")
shutil.copy(os.path.join(src, "coords.Si987H372.fdf"), QD)
base = open(os.path.join(src, "si-qdot-elpa-native.fdf")).read()
QUICK = ("MaxSCFIterations     1\n", "scf-must-converge F\n", "compute-forces F\n")
for q in QUICK:
    check(q in base, "si-qdot-elpa-native.fdf carries the quick-test line %r" % q.strip())
REF_E = total(os.path.join(src, "Reference", "si-qdot-elpa-native.out"))
rec("qdot reference (committed, one SCF step, ELPA-2):", REF_E)
nodiag = re.sub(r"^diag-algorithm.*\n", "", base, flags=re.M)
check("SolutionMethod   diagon\n" in nodiag and "PAO.BasisSize       SZ\n" in nodiag, "qdot input has the lines the variants edit")
full = re.sub("|".join(re.escape(q) for q in QUICK), "", nodiag)       # the "full test" the file describes
dzp = full.replace("PAO.BasisSize       SZ\n", "PAO.BasisSize       DZP\n")  # the production basis
variants = [("qdot-quick-default", nodiag, "build default eigensolver"),
            ("qdot-quick-mrrr", nodiag.replace("SolutionMethod   diagon\n", "SolutionMethod   diagon\nDiag.Algorithm MRRR\n"), "MRRR"),
            ("qdot-full", full, "full: converged SCF + forces"),
            ("qdot-dzp", dzp, "full at DZP, build default eigensolver"),
            ("qdot-dzp-elsi-elpa", dzp.replace("SolutionMethod   diagon\n", "solution-method elsi\nelsi-solver elpa\n"),
             "full at DZP, ELSI-ELPA")]
qrows = []
for name, text, what in variants:
    text = re.sub(r"^SystemLabel.*$", "SystemLabel         " + name, text, count=1, flags=re.M)
    with open(os.path.join(QD, name + ".fdf"), "w") as fh:
        fh.write(text)
    t0 = time.time()
    with open(os.path.join(QD, name + ".stdout"), "w") as fh:
        extra = [] if name.startswith("qdot-quick") else ["-fdf", "Charge.Mulliken:end"]   # print Qtot; a write, not an SCF setting
        rc = subprocess.run(MPI + [str(QNP), SIESTA, "-out", name + ".out"] + extra + [name + ".fdf"], cwd=QD,
                            env=ENV, stdout=fh, stderr=subprocess.STDOUT).returncode
    wall = time.time() - t0
    o = open(os.path.join(QD, name + ".out"), errors="replace").read()
    e = total(os.path.join(QD, name + ".out"))
    nscf = len(re.findall(r"^\s+scf:\s+\d+", o, re.M))
    ranks = re.search(r"Running on (\d+) nodes", o)
    qtot = re.findall(r"mulliken: Qtot =\s+([0-9.]+)", o)
    conv = "SCF cycle converged" in o
    # the final force block: one "siesta: <atom> fx fy fz" row per atom, then "siesta: Tot"
    fb = o.rsplit("siesta: Atomic forces (eV/Ang):", 1)[-1] if "Atomic forces" in o else ""
    fb = fb.split("Stress tensor")[0]
    nf = len(re.findall(r"^siesta:\s+\d+\s+-?\d+\.\d+\s+-?\d+\.\d+\s+-?\d+\.\d+\s*$", fb, re.M))
    forces = re.search(r"^siesta:\s+Tot\s+(\S+)\s+(\S+)\s+(\S+)", fb, re.M)
    fmax = re.findall(r"^\s+Max\s+(\S+)\s*$", o, re.M)
    rec("%s (%s): rc=%d wall=%.0fs ranks=%s Etot=%s scf_steps=%d converged=%s Qtot=%s" % (
        name, what, rc, wall, ranks.group(1) if ranks else None, e, nscf, conv, qtot[-1] if qtot else None))
    check(rc == 0 and ">> End of run" in o, "%s runs to '>> End of run'" % name)
    check(ranks and int(ranks.group(1)) == QNP, "%s reports 'Running on %d nodes'" % (name, QNP))
    r = {"name": name, "wall": wall, "etot": e, "nscf": nscf, "conv": conv,
         "qtot": qtot[-1] if qtot else "", "ftot": " ".join(forces.groups()) if forces else "", "nf": nf,
         "fmax": fmax[-1] if fmax else ""}
    if name.startswith("qdot-quick"):
        check(e is not None and abs(e - REF_E) < TOL["Etot"],
              "%s Etot within upstream's Etot tolerance %.3f eV of the committed reference" % (name, TOL["Etot"]))
        r["vs_ref"] = (e - REF_E) if e is not None else ""
    else:
        check(conv, "%s: 'SCF cycle converged'" % name)
        check(forces is not None and nf == 1359, "%s: a force on each of the 1,359 atoms (%d)" % (name, nf))
        # 987 Si x 4 + 372 H x 1 valence electrons; a neutral dot conserves them exactly
        check(qtot and abs(float(qtot[-1]) - 4320.0) < 1e-3, "%s: Mulliken Qtot == 4320 valence electrons" % name)
        r["vs_ref"] = ""
    qrows.append(r)
with open(os.path.join(OUT, "qdot.tsv"), "w") as fh:
    fh.write("run\twall_s\tEtot_eV\tminus_ref_eV\tscf_steps\tconverged\tQtot\tforce_rows\tforce_sum_eV_Ang\tmax_force\n")
    for r in qrows:
        fh.write("%s\t%.1f\t%s\t%s\t%d\t%s\t%s\t%s\t%s\t%s\n" % (r["name"], r["wall"], r["etot"], r["vs_ref"],
                                                          r["nscf"], r["conv"], r["qtot"], r["nf"], r["ftot"], r["fmax"]))
# two eigensolver libraries (ScaLAPACK's, ELPA through ELSI) on one Hamiltonian reach one fixed point
ed = {r["name"]: r["etot"] for r in qrows}
if ed.get("qdot-dzp") is not None and ed.get("qdot-dzp-elsi-elpa") is not None:
    dd = ed["qdot-dzp"] - ed["qdot-dzp-elsi-elpa"]
    rec("qdot DZP: default eigensolver minus ELSI-ELPA = %.6f eV" % dd)
    check(abs(dd) < TOL["Etot"], "qdot DZP converged Etot agrees across eigensolvers within %.3f eV" % TOL["Etot"])
else:
    check(False, "both DZP runs produced a total energy")
rec("ru_maxrss_children_gib %.2f" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576))
if FAILS:
    rec("SIESTA REAL FAILED: %d check(s)" % len(FAILS))
    sys.exit(1)
rec("SIESTA REAL OK")
