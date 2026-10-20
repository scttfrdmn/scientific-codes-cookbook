#!/usr/bin/env python3
"""How big is a 3DEP project? Point counts of every EPT project on RODA (projects.tsv).

    aws s3 ls --no-sign-request s3://usgs-lidar-public/ > ls.txt
    python3 -I survey.py ls.txt projects.tsv

Reads only each project's ept.json (~2.5 KB), over HTTPS.
"""
import concurrent.futures as cf
import json
import sys
import urllib.request

names = [l.split("PRE ")[1].strip().rstrip("/") for l in open(sys.argv[1]) if "PRE " in l]


def get(n):
    try:
        with urllib.request.urlopen("https://s3-us-west-2.amazonaws.com/usgs-lidar-public/%s/ept.json" % n, timeout=30) as r:
            return n, json.load(r)["points"]
    except Exception:
        return n, None


with cf.ThreadPoolExecutor(64) as ex:
    res = sorted(ex.map(get, names))
with open(sys.argv[2], "w") as f:
    for n, p in res:
        f.write("%s\t%s\n" % (n, p))
ok = sorted(p for _, p in res if p)
print("projects %d, with ept.json %d, total %d points" % (len(names), len(ok), sum(ok)))
for q in (0.1, 0.25, 0.5, 0.75, 0.9):
    print("p%02d %d" % (q * 100, ok[int(q * len(ok))]))
lassen = dict(res)["CA_LassenNP_2020"]
print("CA_LassenNP_2020 %d points, larger than %.0f%% of projects" % (lassen, 100 * sum(p < lassen for p in ok) / len(ok)))
