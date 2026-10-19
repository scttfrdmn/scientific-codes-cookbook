#!/usr/bin/env python3
"""kraken2-real, task 1: classify whole published ZymoBIOMICS runs against the recipe's standard-8
index, one after another on one box (as a cohort would), and score each with score.py.

    python3 -u classify.py <threads> <acc>:<ENA read_count>:<ENA base_count>:<even|log> [...]

Needs /tmp/k2_standard_08gb_20250402.tar.gz, /tmp/<acc>_{1,2}.fastq.gz, score.py and zymo.tsv.
Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland), so the only tools used
are python3, tar, awk and sha256sum. Writes /tmp/out/.

Peak RSS is the classify process's own ru_maxrss (os.wait4 on the child: the kraken2 wrapper execs
classify, so the child is classify), not a sampler. The per-read output goes to /var/tmp, the
container disk, because /tmp is the half-RAM tmpfs that already holds the index twice.
"""
import os
import resource
import subprocess
import sys
import time

T = int(sys.argv[1])
SAMPLES = [a.split(":") for a in sys.argv[2:]]
IDX = "k2_standard_08gb_20250402"
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
rec(H, "kraken2", sh("command -v kraken2"), "|", sh("kraken2 --version 2>&1 | head -1"))

TIMES = O + "/times.txt"
t0 = time.time()
os.makedirs("db", exist_ok=True)
rc = subprocess.call(["tar", "-xzf", IDX + ".tar.gz", "-C", "db"])
rec(TIMES, "untar_rc", rc, "untar_s", round(time.time() - t0, 1))
if rc != 0 or not os.path.getsize("db/hash.k2d"):
    rec(TIMES, "FAIL: could not unpack the index")
    sys.exit(1)
rec(TIMES, "hash_k2d_bytes", os.path.getsize("db/hash.k2d"))
rec(TIMES, "tmpfs_after_untar", sh("df -m /tmp | awk 'NR==2{print $3 \" MB used of \" $2}'"))

FAILS = 0
for acc, pairs, bases, kind in SAMPLES:
    S = "%s/%s" % (O, acc)
    os.makedirs(S, exist_ok=True)
    per_read = "/var/tmp/%s.kraken" % acc
    rec(TIMES, "==", acc, kind, "threads", T)
    cmd = ["kraken2", "--db", "db", "--paired", "--threads", str(T),
           "--report", S + "/report.txt", "--output", per_read,
           "%s_1.fastq.gz" % acc, "%s_2.fastq.gz" % acc]
    with open(S + "/kraken2.log", "w") as log:
        t1 = time.time()
        p = subprocess.Popen(cmd, stderr=log)
        _, status, ru = os.wait4(p.pid, 0)
        wall = time.time() - t1
    rc = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
    cpu = ru.ru_utime + ru.ru_stime
    rec(TIMES, acc, "kraken2_rc", rc, "wall_s", round(wall, 1), "peak_rss_gib", round(ru.ru_maxrss / 1048576, 2),
        "cpu_s", round(cpu, 1), "avg_busy_cores", round(cpu / wall, 2) if wall else 0)
    rec(TIMES, acc, "log:", open(S + "/kraken2.log").read().replace("\n", " | "))
    if rc != 0 or not os.path.exists(S + "/report.txt"):
        rec(TIMES, "FAIL:", acc, "kraken2 exit", rc)
        FAILS += 1
        continue

    # The per-read output: its sha256 for the cross-chip comparison, and a tally of reads per taxid
    # that score.py balances against the report's taxon_reads column (two output paths, one count).
    t2 = time.time()
    rec(S + "/per-read.txt", "per_read_sha256", sh("sha256sum %s | cut -d' ' -f1" % per_read),
        "bytes", os.path.getsize(per_read))
    subprocess.call("awk -F'\\t' '{n[$1]++} $1==\"C\"{t[$3]++} END{for (k in n) print \"status\", k, n[k]; "
                    "for (k in t) print \"taxid\", k, t[k]}' %s > %s/per-read-tally.txt" % (per_read, S), shell=True)
    os.remove(per_read)  # created by this container, on its own disk: not a staged input
    rec(TIMES, acc, "per_read_hash_and_tally_s", round(time.time() - t2, 1))

    rc = subprocess.call([sys.executable, "-u", "score.py", S + "/report.txt", S + "/kraken2.log",
                          S + "/per-read-tally.txt", pairs, bases, kind, "zymo.tsv", S])
    if rc != 0:
        FAILS += 1
    rec(S + "/fingerprints.txt", "report_sha256", sh("sha256sum %s/report.txt | cut -d' ' -f1" % S))
    rec(S + "/fingerprints.txt", "per_read", open(S + "/per-read.txt").read().strip())

rec(TIMES, "failures", FAILS)
sys.exit(1 if FAILS else 0)
