"""Print the per-chip results table from downloaded run outputs.
   python3 collect.py <label>=<dir>,<instance>,<$/hr>,<billed s> ...
Each <dir> holds one run's results.json and host.txt (runs*/openmm-mdanalysis-real/r1/). $/hr is
spawn's printed on-demand price; billed is EC2 LaunchTime to the terminate time in
StateTransitionReason.
"""
import json
import os
import sys

print("| chip | instance | JAC 1 ns | JAC ns/day | STMV 1,000 steps | STMV ns/day | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for arg in sys.argv[1:]:
    label, rest = arg.split("=", 1)
    d, inst, price, billed = rest.split(",")
    price, billed = float(price), float(billed)
    r = json.load(open(os.path.join(d, "results.json")))
    host = {}
    for line in open(os.path.join(d, "host.txt")):
        k, _, v = line.strip().rpartition(" ")
        host[k.rstrip(":")] = v
    j, s = r["systems"]["JAC"], r["systems"]["STMV"]
    wall = float(host["tool_wall_s"])
    print("| %s | `%s` | %.0f s | %.1f | %.0f s | %.2f | %.0f s | %.0f s | %s GiB | %.4f | %.3f | %.3f |" % (
        label, inst, j["md_s"], j["ns_per_day"], s["md_s"], s["ns_per_day"], wall, billed,
        host.get("cgroup_memory_peak_gib"), price, wall / 3600 * price, billed / 3600 * price))
    print("  %s: threads/core %s, maxrss %.2f GiB, Reference single point JAC %.1f s / STMV %.1f s" % (
        label, host.get("Thread(s) per core"), r["maxrss_gib"], j["e0_ref_s"], s["e0_ref_s"]), file=sys.stderr)
