# z3-real fetch: run ON AWS by 00-fetch.task.json. Downloads three whole logics of the SMT-LIB 2025
# non-incremental release (Zenodo record 16740866, DOI 10.5281/zenodo.16740866) into /tmp/out/,
# checks each archive against the md5 Zenodo publishes for it, and writes data.sha256 over what it
# keeps. Nothing passes through a laptop. It also lists each archive (count, sizes, :status labels)
# so the run is sized from the data, not a guess.
import hashlib, json, os, re, sys, tarfile, time, urllib.request

OUT = "/tmp/out"
os.makedirs(OUT, exist_ok=True)
REC = "16740866"
LOGICS = ["QF_IDL", "QF_RDL", "QF_LRA"]


def get(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=900) as r:
                return r.read()
        except Exception as e:  # a transient network error: retry, then fail loudly
            print("retry %d %s: %s" % (i, url, e), flush=True)
            time.sleep(15)
    sys.exit("FAIL: could not fetch " + url)


meta = json.loads(get("https://zenodo.org/api/records/" + REC))
files = {f["key"]: f for f in meta["files"]}
print("record %s doi %s version %s" % (REC, meta.get("doi"), meta["metadata"].get("version")), flush=True)
for lg in LOGICS + ["README.md"]:
    key = lg if lg.endswith(".md") else lg + ".tar.zst"
    f = files[key]
    b = get(f["links"]["self"])
    algo, want = f["checksum"].split(":")
    got = hashlib.new(algo, b).hexdigest()
    if got != want or len(b) != f["size"]:
        sys.exit("FAIL: %s %s %s != Zenodo's %s (%d bytes)" % (key, algo, got, want, len(b)))
    open(os.path.join(OUT, key), "wb").write(b)
    print("%s %d bytes, %s matches Zenodo" % (key, len(b), algo), flush=True)

try:
    import compression.zstd  # noqa: F401  (Python 3.14; tarfile's r:zst mode needs it)
    have = True
except Exception as e:
    have = False
    print("no compression.zstd: %s" % e, flush=True)
for lg in LOGICS if have else []:
    sizes, status = [], {}
    with tarfile.open(os.path.join(OUT, lg + ".tar.zst"), mode="r:zst") as t:
        for m in t:
            if not (m.isfile() and m.name.endswith(".smt2")):
                continue
            sizes.append(m.size)
            head = t.extractfile(m).read(65536).decode("utf-8", "replace")
            s = re.search(r"\(set-info\s+:status\s+(sat|unsat|unknown)\s*\)", head)
            k = s.group(1) if s else "absent-in-first-64KiB"
            status[k] = status.get(k, 0) + 1
    sizes.sort()
    n = len(sizes)
    print("%s: %d benchmarks, %.2f GB, median %d B, p99 %d B, max %d B, status %s" % (
        lg, n, sum(sizes) / 1e9, sizes[n // 2], sizes[int(n * 0.99)], sizes[-1], status), flush=True)
    print("  10 largest MB:", [round(s / 1e6, 1) for s in sizes[-10:]], flush=True)

lines = []
for f in sorted(os.listdir(OUT)):
    if f != "data.sha256":
        lines.append("%s  %s\n" % (hashlib.sha256(open(os.path.join(OUT, f), "rb").read()).hexdigest(), f))
open(os.path.join(OUT, "data.sha256"), "w").writelines(lines)
print("".join(lines))
print("FETCH OK")
