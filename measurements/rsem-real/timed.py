#!/usr/bin/env python3
"""Run one command; append `<label> rc <n> wall_s <s> maxrss_kb <kb>` to a timing file.
maxrss is the kernel's ru_maxrss over waited-for descendants (RUSAGE_CHILDREN), so a wrapper
script (hisat2 is one) reports the aligner's peak, not its own. stdout/stderr go to <log>.
    timed.py <timing file> <label> <log> -- cmd ...
"""
import resource
import subprocess
import sys
import time

timing, label, log = sys.argv[1:4]
cmd = sys.argv[sys.argv.index("--") + 1:]
t0 = time.time()
with open(log, "w") as fh:
    rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
wall = time.time() - t0
kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
line = "%s\trc\t%d\twall_s\t%.1f\tmaxrss_kb\t%d\n" % (label, rc, wall, kb)
open(timing, "a").write(line)
sys.stdout.write(line)
sys.exit(rc)
