#!/usr/bin/env python3
"""Pin a USGS 3DEP EPT project on RODA: list every object, fetch it, hash it.

    python3 -u pin.py <project> <outdir> [threads]

Runs ON AWS (00-pin.task.json), next to the bucket. Writes <outdir>/pins.tsv -- one line per
object, `key<TAB>size<TAB>sha256`, sorted by key -- and <outdir>/pin.log. The sha256 of pins.tsv
is the dataset pin committed in pins.sha256; the real task re-hashes every object it decodes
against this file. Every object whose ETag is a plain MD5 (single-part upload) is also checked
against the MD5 of the fetched bytes, so a short or corrupted read cannot become a pin.
"""
import concurrent.futures as cf
import hashlib
import http.client
import os
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

HOST = "usgs-lidar-public.s3.us-west-2.amazonaws.com"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_local = threading.local()


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = _local.c = http.client.HTTPSConnection(HOST, timeout=60)
    return c


def get(path):
    """GET over a per-thread keep-alive connection; three tries, the count of retries returned."""
    for attempt in range(3):
        try:
            c = conn()
            c.request("GET", path)
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise IOError("HTTP %d for %s" % (r.status, path))
            return body, dict(r.getheaders()), attempt
        except Exception:
            _local.c = None
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def list_all(prefix):
    out, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            q["continuation-token"] = token
        body, _, _ = get("/?" + urllib.parse.urlencode(q))
        root = ET.fromstring(body)
        for c in root.findall(NS + "Contents"):
            out.append((c.find(NS + "Key").text, int(c.find(NS + "Size").text), c.find(NS + "ETag").text.strip('"')))
        if root.find(NS + "IsTruncated").text != "true":
            return out
        token = root.find(NS + "NextContinuationToken").text


def main():
    project, outdir = sys.argv[1], sys.argv[2]
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    objs = list_all(project + "/")
    t1 = time.time()
    print("listed %d objects, %d bytes, in %.1f s" % (len(objs), sum(s for _, s, _ in objs), t1 - t0), flush=True)

    def one(o):
        key, size, etag = o
        body, _, retries = get("/" + urllib.parse.quote(key))
        bad = []
        if len(body) != size:
            bad.append("size %d != listed %d" % (len(body), size))
        md5_checked = "-" not in etag
        if md5_checked and hashlib.md5(body).hexdigest() != etag:
            bad.append("md5 != etag")
        return key, size, hashlib.sha256(body).hexdigest(), md5_checked, retries, bad

    rows, nmd5, nretry, fails = [], 0, 0, []
    with cf.ThreadPoolExecutor(threads) as ex:
        for i, (key, size, sha, m, r, bad) in enumerate(ex.map(one, objs, chunksize=64)):
            rows.append((key, size, sha))
            nmd5 += m
            nretry += r
            if bad:
                fails.append((key, bad))
            if (i + 1) % 10000 == 0:
                print("  %d / %d hashed, %.0f s" % (i + 1, len(objs), time.time() - t1), flush=True)
    t2 = time.time()
    rows.sort()
    with open(os.path.join(outdir, "pins.tsv"), "w") as f:
        for key, size, sha in rows:
            f.write("%s\t%d\t%s\n" % (key, size, sha))
    nbytes = sum(s for _, s, _ in rows)
    print("hashed %d objects, %d bytes, in %.1f s (%.0f MB/s); %d MD5-checked against their ETag; %d retries"
          % (len(rows), nbytes, t2 - t1, nbytes / (t2 - t1) / 1e6, nmd5, nretry), flush=True)
    if fails:
        for k, b in fails[:20]:
            print("FAIL %s: %s" % (k, "; ".join(b)))
        print("FAIL: %d objects did not verify" % len(fails))
        sys.exit(1)
    print("PIN OK")


if __name__ == "__main__":
    main()
