#!/usr/bin/env python3
"""abundance-real: Bracken on the two whole-run kraken2 reports kraken2-real produced, at species and
genus rank, then score.py on each.

    python3 -u run.py <threshold> <acc>:<ENA read_count>:<read length>:<even|log> [...]

Needs, in /tmp: pins.sha256 and every file it names (<acc>.report.txt, database<L>mers.kmer_distrib,
score.py, zymo.tsv). Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland), so the
only tools used are python3 and bracken. Writes /tmp/out/.

Peak RSS is os.wait4 on the bracken wrapper (Linux reports the max over the child and the
descendants it reaped, i.e. est_abundance.py), not a sampler.
"""
import hashlib
import os
import subprocess
import sys
import time

THRESH = sys.argv[1]
SAMPLES = [a.split(":") for a in sys.argv[2:]]
O = "/tmp/out"
os.makedirs(O, exist_ok=True)
os.chdir("/tmp")


def rec(path, *vals):
    """Print and append one line. Every value goes through str(), so no value can break the line."""
    line = " ".join(str(v) for v in vals)
    print(line, flush=True)
    with open(path, "a") as f:
        f.write(line + "\n")


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    except Exception as e:  # a description of the run must never be able to fail it
        return "unavailable (%s)" % e


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


H = O + "/host.txt"
rec(H, "uname_m", os.uname().machine)
online = sum(1 for l in open("/proc/cpuinfo") if l.startswith("processor"))
sib = set()
for c in os.listdir("/sys/devices/system/cpu"):
    p = "/sys/devices/system/cpu/%s/topology/thread_siblings_list" % c
    if os.path.exists(p):
        sib.add(open(p).read().strip())
rec(H, "online_cpus", online)
rec(H, "physical_cores", len(sib))
rec(H, "threads_per_core", online // max(len(sib), 1))
rec(H, sh("awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1\": \"$2}' /proc/cpuinfo | sort | uniq -c"))
rec(H, "mem_total_kib", sh("awk '/^MemTotal/{print $2}' /proc/meminfo"))
rec(H, "python", sys.version.split()[0])
rec(H, "bracken", sh("command -v bracken"), "|", sh("bracken -v 2>&1"))
rec(H, "est_abundance_py_sha256", sha(sh("command -v est_abundance.py")) if sh("command -v est_abundance.py") else "-")

# Every staged object against its pin, before anything runs.
T = O + "/times.txt"
t0 = time.time()
bad = []
for line in open("pins.sha256"):
    want, name = line.split()
    got = sha(name) if os.path.exists(name) else "missing"
    rec(O + "/pins-check.txt", name, "OK" if got == want else "FAILED (%s)" % got)
    if got != want:
        bad.append(name)
rec(T, "pins_check_s", round(time.time() - t0, 1))
if bad:
    rec(T, "FAIL: staged inputs do not match their pins:", ",".join(bad))
    sys.exit(1)

FAILS = 0
for acc, pairs, rlen, kind in SAMPLES:
    S = "%s/%s" % (O, acc)
    os.makedirs(S, exist_ok=True)
    for lvl in ("S", "G"):
        cmd = ["bracken", "-d", "/tmp", "-i", "%s.report.txt" % acc, "-o", "%s/%s.bracken" % (S, lvl),
               "-w", "%s/%s.breport" % (S, lvl), "-r", rlen, "-l", lvl, "-t", THRESH]
        rec(T, "==", acc, kind, "level", lvl, "cmd", " ".join(cmd))
        with open("%s/%s.log" % (S, lvl), "w") as log:
            t1 = time.time()
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            _, status, ru = os.wait4(p.pid, 0)
            wall = time.time() - t1
        rc = os.waitstatus_to_exitcode(status)
        rec(T, acc, lvl, "bracken_rc", rc, "wall_s", round(wall, 2), "peak_rss_mib", round(ru.ru_maxrss / 1024, 1),
            "cpu_s", round(ru.ru_utime + ru.ru_stime, 2))
        out = "%s/%s.bracken" % (S, lvl)
        # bracken's wrapper exits 0 on several of its own errors, so the output file is the evidence
        if rc != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            rec(T, "FAIL:", acc, lvl, "bracken exit", rc, "or empty output")
            FAILS += 1
            continue
        for f in (out, "%s/%s.breport" % (S, lvl)):
            rec(S + "/fingerprints.txt", os.path.basename(f), sha(f), os.path.getsize(f))
    rc = subprocess.call([sys.executable, "-u", "score.py", "%s.report.txt" % acc, pairs, kind, THRESH,
                          "zymo.tsv", S])
    if rc != 0:
        FAILS += 1

rec(T, "failures", FAILS)
sys.exit(1 if FAILS else 0)
