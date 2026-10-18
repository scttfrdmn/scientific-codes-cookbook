#!/usr/bin/env python3
"""Run a wave of shell jobs, P at a time, and record each one's wall time, peak RSS and exit code.

    python3 -u rt.py <jobs.tsv> <P> <out.tsv>

jobs.tsv: one `name<TAB>shell command` per line. Each job's stdout+stderr goes to logs/<name>.log.
Peak RSS comes from os.wait4, i.e. the kernel's own ru_maxrss for that child (the largest
process in its tree that was waited on -- the cdo or ncra process itself, under `sh -c exec`).
Exits 1 if any job exits non-zero; the descriptive lines here never fail a run on their own.
"""
import os
import subprocess
import sys
import time

jobs = [l.rstrip("\n").split("\t", 1) for l in open(sys.argv[1]) if l.strip()]
P = int(sys.argv[2])
os.makedirs("logs", exist_ok=True)
out = open(sys.argv[3], "a")
running, bad = {}, 0
queue = list(jobs)
while queue or running:
    while queue and len(running) < P:
        name, cmd = queue.pop(0)
        log = open("logs/%s.log" % name, "w")
        p = subprocess.Popen(["bash", "-c", "set -o pipefail; " + cmd], stdout=log, stderr=subprocess.STDOUT)
        running[p.pid] = (name, time.time(), log)
        print("start\t%s" % name, flush=True)
    pid, status, ru = os.wait4(-1, 0)
    if pid not in running:
        continue
    name, t0, log = running.pop(pid)
    log.close()
    rc = os.waitstatus_to_exitcode(status)
    wall = time.time() - t0
    row = "%s\t%.1f\t%.3f\t%d" % (name, wall, ru.ru_maxrss / 1024.0 ** 2, rc)
    out.write(row + "\n")
    out.flush()
    print("done\t" + row, flush=True)
    if rc != 0:
        bad += 1
        print("---- tail of logs/%s.log" % name, flush=True)
        print("".join(open("logs/%s.log" % name).readlines()[-20:]), flush=True)
sys.exit(1 if bad else 0)
