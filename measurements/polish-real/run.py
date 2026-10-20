"""Run one tool command and record what it cost: wall, exit code, peak RSS, busy CPUs.

    python3 -u run.py <out.txt> <command> [args ...]

Peak RSS is ru_maxrss over the children (the largest single process the command started), read
from the kernel after the command exits -- not a sampler, which misses short peaks.
"""
import resource
import subprocess
import sys
import time

out, cmd = sys.argv[1], sys.argv[2:]
t0 = time.time()
rc = subprocess.call(cmd)
wall = time.time() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
with open(out, "w") as fh:
    fh.write("rc %d\nwall_s %.1f\nru_maxrss_children_kib %d\navg_busy_cpus %.2f\n"
             % (rc, wall, ru.ru_maxrss, (ru.ru_utime + ru.ru_stime) / wall if wall > 0 else 0))
print(open(out).read(), end="", flush=True)
sys.exit(rc)
