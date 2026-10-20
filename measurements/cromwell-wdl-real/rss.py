"""rss.py <report> <argv...>: run argv, then write its wall time and the peak RSS of its largest
descendant (ru_maxrss of RUSAGE_CHILDREN, KiB) to <report>. Exit with argv's exit code."""
import resource
import subprocess
import sys
import time

t = time.time()
rc = subprocess.call(sys.argv[2:])
with open(sys.argv[1], "w") as fh:
    fh.write("maxrss_kib\t%s\nwall_s\t%.1f\nrc\t%s\n"
             % (str(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss), time.time() - t, str(rc)))
sys.exit(rc)
