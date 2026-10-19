# Unpack the staged SMT-LIB logics into /tmp/bench and run solve.py on every benchmark, each in its
# own single-threaded process, <workers> at a time, largest file first. One JSON record per
# benchmark goes to results.jsonl, with the process's wall time and peak RSS (ru_maxrss).
#
#   python3 -u driver.py <data dir> <out dir> <workers> <time limit s> <memory MiB> [<per-logic limit>]
import json, os, signal, subprocess, sys, tarfile, threading, time
from concurrent.futures import ThreadPoolExecutor

DATA, OUT, W, TL, MEM = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
NLIM = int(sys.argv[6]) if len(sys.argv) > 6 else 0
HERE = os.path.dirname(os.path.abspath(__file__))
LOGICS = ["QF_IDL", "QF_RDL", "QF_LRA"]
B = "/tmp/bench"
os.makedirs(OUT + "/rec", exist_ok=True)
os.makedirs(OUT + "/logs", exist_ok=True)

jobs = []
for lg in LOGICS:
    t0 = time.time()
    with tarfile.open(os.path.join(DATA, lg + ".tar.zst"), mode="r:zst") as t:
        t.extractall(os.path.join(B, lg), filter="data")   # whatever the archive's own top directory
    fs = []
    for root, _, files in os.walk(os.path.join(B, lg)):
        fs += [os.path.join(root, f) for f in files if f.endswith(".smt2")]
    fs.sort()
    print("%s: %d benchmarks unpacked in %.0f s" % (lg, len(fs), time.time() - t0), flush=True)
    if NLIM:   # a canary: a stride through each logic, plus its 3 largest files (the memory peak)
        big = sorted(fs, key=os.path.getsize)[-3:]
        fs = sorted(set(fs[::max(1, len(fs) // NLIM)][:NLIM]) | set(big))
    jobs += [(os.path.getsize(f), lg, f) for f in fs]
open(os.path.join(OUT, "jobs.txt"), "w").writelines("%s\t%s\n" % (lg, os.path.relpath(f, B)) for _, lg, f in jobs)
jobs.sort(key=lambda j: -j[0])
print("jobs: %d, %d workers, time limit %.0f s, memory %d MiB" % (len(jobs), W, TL, MEM), flush=True)

env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
lock = threading.Lock()
done = [0]
res = open(os.path.join(OUT, "results.jsonl"), "w")


def one(job):
    _, lg, p = job
    rel = os.path.relpath(p, B)
    tag = rel.replace("/", "__")
    rec = os.path.join(OUT, "rec", tag + ".json")
    t0 = time.time()
    with open(os.path.join(OUT, "logs", tag + ".log"), "w") as lgf:
        pr = subprocess.Popen([sys.executable, "-u", "-I", os.path.join(HERE, "solve.py"), p, str(TL), str(MEM), rec],
                              stdout=lgf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        # solve + core phase each get TL; the guard catches a solver that ignores its timeout
        guard = threading.Timer(2 * TL + 600, lambda: os.killpg(pr.pid, signal.SIGKILL))
        guard.start()
        _, st, ru = os.wait4(pr.pid, 0)
        guard.cancel()
    r = {"logic": lg, "bench": rel}
    if os.path.exists(rec):
        r.update(json.load(open(rec)))
    r.pop("file", None)
    if os.WIFSIGNALED(st) or os.waitstatus_to_exitcode(st) != 0:
        r["error"] = r.get("error", "") + " exit status %d%s" % (st, " (signal)" if os.WIFSIGNALED(st) else "")
    r["wall_s"] = round(time.time() - t0, 3)
    r["maxrss_mib"] = round(ru.ru_maxrss / 1024.0, 1)
    r["exit"] = os.waitstatus_to_exitcode(st)
    with lock:
        res.write(json.dumps(r) + "\n")
        res.flush()
        done[0] += 1
        if done[0] % 100 == 0 or done[0] == len(jobs):
            print("%s %d/%d done" % (time.strftime("%H:%M:%S"), done[0], len(jobs)), flush=True)


T0 = time.time()
with ThreadPoolExecutor(max_workers=W) as ex:
    list(ex.map(one, jobs))
res.close()
print("driver wall %.0f s" % (time.time() - T0), flush=True)
