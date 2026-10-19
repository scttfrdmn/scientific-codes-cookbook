# optimization-real fetch: run ON AWS by 00-fetch.task.json. Downloads the public sources into
# /tmp/out/ and writes data.sha256 over everything it keeps. Nothing here passes through a laptop.
#
#   MIPLIB 2017 benchmark set (240 instances), its versioned test list and solution file v37
#   Netlib LP as COIN-OR's Data-Netlib at tag releases/1.2.11 (plain MPS, gzipped)
#   SoPlex's committed optimal values for Netlib (32 digits), at tag v8.1.0
#   netlib's own readme (the published 1989 optima), for comparison
#   MIPLIB's three published solutions for binkar10_1 (see below)
import hashlib, io, os, sys, tarfile, time, urllib.request

OUT = "/tmp/out"
os.makedirs(OUT + "/netlib", exist_ok=True)
SRC = {
    "miplib/benchmark.zip": "https://miplib.zib.de/downloads/benchmark.zip",
    "miplib/benchmark-v2.test": "https://miplib.zib.de/downloads/benchmark-v2.test",
    "miplib/miplib2017-v37.solu": "https://miplib.zib.de/downloads/miplib2017-v37.solu",
    "ref/soplex-quick.solu": "https://raw.githubusercontent.com/scipopt/soplex/v8.1.0/check/testset/quick.solu",
    "ref/netlib-readme": "https://netlib.org/lp/data/readme",
}
# MIPLIB's published solutions for binkar10_1: v37 of the .solu file takes its value (6741.38) from
# solution 3, which needs integrality slack to be feasible; solutions 1 and 2 are the 6742.20 one.
for i in (1, 2, 3):
    SRC["miplib/solutions/binkar10_1/%d/binkar10_1.sol.gz" % i] = \
        "https://miplib.zib.de/downloads/solutions/binkar10_1/%d/binkar10_1.sol.gz" % i
NETLIB_TGZ = "https://codeload.github.com/coin-or-tools/Data-Netlib/tar.gz/refs/tags/releases/1.2.11"


def get(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                return r.read()
        except Exception as e:  # a transient network error: retry, then fail loudly
            print("retry %d %s: %s" % (i, url, e), flush=True)
            time.sleep(10)
    sys.exit("FAIL: could not fetch " + url)


for rel, url in SRC.items():
    os.makedirs(os.path.dirname(os.path.join(OUT, rel)), exist_ok=True)
    b = get(url)
    open(os.path.join(OUT, rel), "wb").write(b)
    print("%s %d bytes" % (rel, len(b)), flush=True)

# GitHub's archiver does not promise stable tarball bytes, so the members are kept and pinned.
tgz = get(NETLIB_TGZ)
n = 0
with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
    for m in t.getmembers():
        if m.isfile() and m.name.endswith(".mps.gz"):
            open(os.path.join(OUT, "netlib", os.path.basename(m.name)), "wb").write(t.extractfile(m).read())
            n += 1
print("netlib: %d .mps.gz files" % n, flush=True)

lines = []
for root, _, files in os.walk(OUT):
    for f in files:
        p = os.path.join(root, f)
        if f != "data.sha256":
            lines.append("%s  %s\n" % (hashlib.sha256(open(p, "rb").read()).hexdigest(), os.path.relpath(p, OUT)))
lines.sort(key=lambda s: s.split("  ", 1)[1])
open(os.path.join(OUT, "data.sha256"), "w").writelines(lines)
print("".join(lines))
print("FETCH OK")
