# Run every solve as its own single-threaded process, <workers> at a time, and collect one JSON
# record per (solver, instance) into results.jsonl with the process's wall time and peak RSS.
#
#   python3 -u driver.py <data dir> <out dir> <workers> <LP time limit s> <MIP time limit s> [<MIP instance limit>]
#
# Netlib LP: HiGHS, SCIP and CBC on every instance. MIPLIB 2017 benchmark: HiGHS and SCIP on every
# instance. Jobs start largest-file first, so the long tail is not one big model started last.
import json, os, signal, subprocess, sys, threading, time, zipfile
from concurrent.futures import ThreadPoolExecutor

DATA, OUT, W, TL_LP, TL_MIP = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
NMIP = int(sys.argv[6]) if len(sys.argv) > 6 else 0
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT + "/rec", exist_ok=True)
os.makedirs(OUT + "/logs", exist_ok=True)

mipdir = "/tmp/mip"
os.makedirs(mipdir, exist_ok=True)
with zipfile.ZipFile(os.path.join(DATA, "miplib/benchmark.zip")) as z:
    for nm in z.namelist():
        if nm.endswith(".mps.gz"):
            with z.open(nm) as fi, open(os.path.join(mipdir, os.path.basename(nm)), "wb") as fo:
                fo.write(fi.read())
listed = [l.strip() for l in open(os.path.join(DATA, "miplib/benchmark-v2.test")) if l.strip()]
have = set(os.listdir(mipdir))
missing = [f for f in listed if os.path.basename(f) not in have]
if missing:
    sys.exit("FAIL: benchmark.zip lacks %d of the %d listed instances: %s" % (len(missing), len(listed), missing[:5]))
print("miplib: %d listed instances, all present" % len(listed), flush=True)

jobs = []
for f in sorted(os.listdir(os.path.join(DATA, "netlib"))):
    p = os.path.join(DATA, "netlib", f)
    for s in ("highs", "scip", "cbc"):
        jobs.append((os.path.getsize(p), s, "lp", p, TL_LP))
mip = sorted((os.path.join(mipdir, os.path.basename(f)) for f in listed), key=os.path.getsize, reverse=True)
if NMIP:
    mip = mip[-NMIP:]   # a canary: the smallest NMIP
for p in mip:
    for s in ("highs", "scip"):
        jobs.append((os.path.getsize(p), s, "mip", p, TL_MIP))
# MIPLIB's published solutions, where staged, go through the same checker as the solvers' own
sold = os.path.join(DATA, "miplib/solutions")
for inst in sorted(os.listdir(sold)) if os.path.isdir(sold) else []:
    for sid in sorted(os.listdir(os.path.join(sold, inst))):
        jobs.append((0, "solfile", "ref", os.path.join(mipdir, inst + ".mps.gz"), os.path.join(sold, inst, sid, inst + ".sol.gz")))
jobs.sort(key=lambda j: -j[0])
print("jobs: %d (%d LP, %d MIP), %d workers" % (len(jobs), sum(j[2] == "lp" for j in jobs),
                                                 sum(j[2] == "mip" for j in jobs), W), flush=True)

env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
lock = threading.Lock()
done = [0]
res = open(os.path.join(OUT, "results.jsonl"), "w")


def one(job):
    _, solver, kind, p, arg = job
    tl = 600.0 if solver == "solfile" else arg
    inst = os.path.basename(p)[:-len(".mps.gz")]
    tag = "%s.%s.%s" % (kind, solver, inst) + ("." + os.path.basename(os.path.dirname(arg)) if solver == "solfile" else "")
    rec = os.path.join(OUT, "rec", tag + ".json")
    t0 = time.time()
    with open(os.path.join(OUT, "logs", tag + ".log"), "w") as lg:
        pr = subprocess.Popen([sys.executable, "-u", "-I", os.path.join(HERE, "solve.py"), solver, kind, p, str(arg), rec],
                              stdout=lg, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        guard = threading.Timer(tl * 1.25 + 300, lambda: os.killpg(pr.pid, signal.SIGKILL))
        guard.start()
        _, st, ru = os.wait4(pr.pid, 0)
        guard.cancel()
    r = {"solver": solver, "kind": kind, "instance": inst}
    if solver == "solfile":
        r["solution_file"] = os.path.relpath(arg, DATA)
    if os.path.exists(rec):
        r.update(json.load(open(rec)))
    else:
        r["error"] = "no record: exit status %d%s" % (st, " (killed by the guard)" if os.WIFSIGNALED(st) else "")
    r["wall_s"] = round(time.time() - t0, 3)
    r["maxrss_mib"] = round(ru.ru_maxrss / 1024.0, 1)
    r["exit"] = os.waitstatus_to_exitcode(st)
    with lock:
        res.write(json.dumps(r) + "\n")
        res.flush()
        done[0] += 1
        if done[0] % 25 == 0 or done[0] == len(jobs):
            print("%s %d/%d done" % (time.strftime("%H:%M:%S"), done[0], len(jobs)), flush=True)


T0 = time.time()
with ThreadPoolExecutor(max_workers=W) as ex:
    list(ex.map(one, jobs))
res.close()
print("driver wall %.0f s" % (time.time() - T0), flush=True)
