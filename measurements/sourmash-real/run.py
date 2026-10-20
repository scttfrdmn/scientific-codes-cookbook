#!/usr/bin/env python3
"""sourmash-real, task 1: sketch, gather and classify whole Zymo shotgun runs against GTDB R226.

    python3 -u run.py <outdir> <db.zip> <lineages.csv> <zymo.tsv> <acc>:<even|log> [...]

The samples run side by side, one pipeline each, which is how a researcher with an 8-vCPU box
and a handful of samples would use it: sourmash's sketch and gather are single-threaded. Per sample:

  sketch R1, sketch R2 and sketch R1+R2 (three processes at once), sig merge R1 R2,
  gather against the database, tax metagenome against its lineages, then score.py.

Every process is timed and its peak RSS read from wait4's ru_maxrss. Needs /tmp/<acc>_{1,2}.fastq.gz.
"""
import os, subprocess, sys, threading, time

OUT, DB, LIN, TRUTH = sys.argv[1:5]
SAMPLES = [a.split(":") for a in sys.argv[5:]]
HERE = os.path.dirname(os.path.abspath(__file__))
P = "k=31,scaled=1000,abund"
lock = threading.Lock()
times = open(os.path.join(OUT, "times.txt"), "a")


def rec(*vals):
    line = " ".join(str(v) for v in vals)
    with lock:
        times.write(line + "\n"); times.flush(); print(line, flush=True)


def start(cmd, log):
    return subprocess.Popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT), time.time()


def wait(acc, step, popen_t0):
    p, t0 = popen_t0
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    rec(acc, step, "rc", p.returncode, "wall_s", "%.1f" % (time.time() - t0), "maxrss_gib", "%.3f" % (ru.ru_maxrss / 1048576))
    return p.returncode


def pipeline(acc, std, result):
    d = os.path.join(OUT, acc); os.makedirs(d, exist_ok=True)
    r1, r2 = "/tmp/%s_1.fastq.gz" % acc, "/tmp/%s_2.fastq.gz" % acc
    sk = lambda files, o: ["sourmash", "sketch", "dna", "-p", P, *files, "--name", acc, "-o", o]
    t0 = time.time()
    procs = [("sketch_R1", start(sk([r1], "%s/%s_1.sig.zip" % (d, acc)), "%s/sketch_R1.log" % d)),
             ("sketch_R2", start(sk([r2], "%s/%s_2.sig.zip" % (d, acc)), "%s/sketch_R2.log" % d)),
             ("sketch_R1R2", start(sk([r1, r2], "%s/%s.sig.zip" % (d, acc)), "%s/sketch_R1R2.log" % d))]
    rcs = [wait(acc, n, p) for n, p in procs]
    rec(acc, "sketch_all3", "wall_s", "%.1f" % (time.time() - t0))
    if any(rcs):
        result[acc] = "sketch failed"; return
    steps = [
        ("merge", ["sourmash", "sig", "merge", "%s/%s_1.sig.zip" % (d, acc), "%s/%s_2.sig.zip" % (d, acc),
                   "--set-name", acc, "-o", "%s/%s.merged.sig.zip" % (d, acc)]),
        ("gather", ["sourmash", "gather", "%s/%s.sig.zip" % (d, acc), DB, "-o", "%s/gather.csv" % d,
                    "--save-matches", "%s/matches.zip" % d]),
        ("tax", ["sourmash", "tax", "metagenome", "-g", "%s/gather.csv" % d, "-t", LIN,
                 "-F", "csv_summary", "-o", acc, "--output-dir", d]),
        ("score", ["python3", "-u", os.path.join(HERE, "score.py"), d, acc, TRUTH, std]),
    ]
    for name, cmd in steps:
        if wait(acc, name, start(cmd, "%s/%s.log" % (d, name))):
            result[acc] = name + " failed"
            os.system("tail -30 %s/%s.log" % (d, name)); return
    os.system("cat %s/score.log" % d)
    result[acc] = "ok"


result = {}
th = [threading.Thread(target=pipeline, args=(a, s, result)) for a, s in SAMPLES]
T0 = time.time()
for t in th: t.start()
for t in th: t.join()
rec("all", "tool_wall_s", "%.1f" % (time.time() - T0))
for a, _ in SAMPLES:
    print("RESULT", a, result.get(a))
sys.exit(0 if all(result.get(a) == "ok" for a, _ in SAMPLES) else 1)
