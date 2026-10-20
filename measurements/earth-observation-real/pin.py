"""Pin every object the real workload reads, on AWS, without copying the pixels anywhere.

    python3 -u pin.py <scenes.txt> <items dir> <out dir> <threads>

For each of the 153 scenes in scenes.txt (every 2024 L2A item of MGRS tile 11SKA in the RODA
sentinel-cogs bucket that has bands): hash the staged STAC item, check that its red / nir / scl
assets point at the objects this measurement reads, then stream B04.tif, B08.tif and SCL.tif from
the bucket over HTTPS and hash them. Writes pins-data.sha256 ("<sha256>  <scene>/<file>") and
sizes.tsv. real.py holds every byte it reads to these pins.
"""
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
ASSET = {"B04.tif": "red", "B08.tif": "nir", "SCL.tif": "scl"}

scenes_txt, items, out, threads = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
os.makedirs(out, exist_ok=True)
scenes = [l.strip() for l in open(scenes_txt) if l.strip()]
assert len(scenes) == len(set(scenes)) == 153, len(scenes)


def fetch(path):
    h, n = hashlib.sha256(), 0
    for attempt in range(3):
        try:
            h, n = hashlib.sha256(), 0
            with urllib.request.urlopen(BASE + path, timeout=120) as r:
                want = int(r.headers["Content-Length"])
                while True:
                    b = r.read(8 << 20)
                    if not b:
                        break
                    h.update(b)
                    n += len(b)
            if n != want:
                raise IOError("short read %d of %d" % (n, want))
            return h.hexdigest(), n
        except Exception as e:  # a transient network error is retried; a third failure is fatal
            err = e
            time.sleep(2)
    raise SystemExit("FAIL: %s: %s" % (path, err))


def one(scene):
    sid = scene.rsplit("/", 1)[1]
    raw = open(os.path.join(items, sid + ".json"), "rb").read()
    item = json.loads(raw)
    assert item["id"] == sid, (item["id"], sid)
    rows = [(hashlib.sha256(raw).hexdigest(), len(raw), "%s/item.json" % sid)]
    for f, key in ASSET.items():
        href = item["assets"][key]["href"]
        if href != BASE + scene + "/" + f:
            raise SystemExit("FAIL: %s asset %s points at %s" % (sid, key, href))
        sha, n = fetch(scene + "/" + f)
        rows.append((sha, n, "%s/%s" % (sid, f)))
    return rows


t0 = time.time()
with ThreadPoolExecutor(threads) as ex:
    res = list(ex.map(one, scenes))
rows = [r for rs in res for r in rs]
with open(os.path.join(out, "pins-data.sha256"), "w") as fh:
    for sha, n, name in rows:
        fh.write("%s  %s\n" % (sha, name))
with open(os.path.join(out, "sizes.tsv"), "w") as fh:
    for sha, n, name in rows:
        fh.write("%s\t%d\n" % (name, n))
tot = sum(n for _, n, _ in rows)
print("pinned %d objects, %.3f GB, in %.0f s" % (len(rows), tot / 1e9, time.time() - t0))
print("PIN OK")
