#!/usr/bin/env python3
"""Fetch the time axis of every store in the Pangeo CMIP6 catalog on AWS Open Data, once.

    python3 -u fetch.py <catalog.csv.gz> <outdir> [workers] [threads]

For each of the catalog's zarr stores this reads `.zmetadata` and keeps only what describes
time: the `time` variable, the variable its `bounds` (or `climatology`) attribute names, and
the store's `frequency`/`table_id`/`source_id`. Then it fetches those variables' raw chunks,
still compressed exactly as the store holds them, and appends them to a per-worker shard:

    <outdir>/NN.bin     the raw chunk bytes, concatenated
    <outdir>/NN.jsonl   one record per store: the metadata kept, and for every chunk its
                        key, offset and length in NN.bin, S3 VersionId and sha256

The bucket is versioned, so (key, VersionId) names immutable bytes, and the sha256 lets a
reader prove they hold the same ones. Nothing is decoded here: decoding is the measurement.

Reads are anonymous HTTPS (the bucket is public, Payer: BucketOwner), in-region.
"""
import csv
import gzip
import hashlib
import itertools
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool

HOST = "https://cmip6-pds.s3.us-west-2.amazonaws.com/"
KEEP_ROOT = ("frequency", "table_id", "source_id", "experiment_id")


def get(url):
    """-> (status, bytes or None, version id). 404 is an answer; 5xx/timeouts retry, then raise."""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return 200, r.read(), r.headers.get("x-amz-version-id")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return e.code, None, None
            err = e
        except Exception as e:  # timeouts, resets
            err = e
        time.sleep(0.5 * 2 ** attempt)
    raise RuntimeError("%s: %s" % (url, err))


def one(zstore):
    try:
        return _one(zstore)
    except Exception as e:      # recorded and counted, never silently dropped
        return {"z": zstore, "status": "error", "error": repr(e)[:500]}, []


def _one(zstore):
    base = HOST + zstore[len("s3://cmip6-pds/"):]
    st, body, ver = get(base + ".zmetadata")
    rec = {"z": zstore, "status": st}
    if st != 200:
        return rec, []
    rec["zmeta"] = {"version": ver, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
    m = json.loads(body)["metadata"]
    root = m.get(".zattrs", {})
    rec["root"] = {k: root.get(k) for k in KEEP_ROOT if k in root}
    if "time/.zarray" not in m:
        return rec, []
    tattrs = m.get("time/.zattrs", {})
    names = ["time"] + [tattrs[a] for a in ("bounds", "climatology") if a in tattrs]
    rec["vars"], blobs = {}, []
    for v in names:
        za, zt = m.get(v + "/.zarray"), m.get(v + "/.zattrs", {})
        if za is None:
            rec["vars"][v] = {"missing": True}
            continue
        sep = za.get("dimension_separator", ".")
        grid = [range(-(-s // c)) if s else range(0) for s, c in zip(za["shape"], za["chunks"])]
        chunks = []
        for idx in itertools.product(*grid):
            key = v + "/" + sep.join(str(i) for i in idx)
            cst, cb, cver = get(base + key)
            if cst != 200:
                chunks.append({"key": key, "status": cst})   # an absent zarr chunk is fill_value
                continue
            chunks.append({"key": key, "len": len(cb), "version": cver,
                           "sha256": hashlib.sha256(cb).hexdigest()})
            blobs.append(cb)
        rec["vars"][v] = {"zarray": za, "zattrs": zt, "chunks": chunks}
    return rec, blobs


def work(args):
    wid, stores, outdir, threads = args
    t0 = time.time()
    n = 0
    with open(os.path.join(outdir, "%02d.bin" % wid), "wb") as fb, \
         open(os.path.join(outdir, "%02d.jsonl" % wid), "w") as fj, \
         ThreadPoolExecutor(threads) as ex:
        off = 0
        for rec, blobs in ex.map(one, stores):
            it = iter(blobs)
            for v in rec.get("vars", {}).values():
                for c in v.get("chunks", []):
                    if "len" in c:
                        b = next(it)
                        c["off"] = off
                        fb.write(b)
                        off += len(b)
            fj.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
    return wid, n, time.time() - t0


def main():
    cat, outdir = sys.argv[1], sys.argv[2]
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 32
    os.makedirs(outdir, exist_ok=True)
    rows = list(csv.DictReader(gzip.open(cat, "rt")))
    stores = sorted(r["zstore"] for r in rows)
    if len(set(stores)) != len(stores):
        sys.exit("FAIL: the catalog repeats a zstore")
    print("catalog rows %d, distinct stores %d" % (len(rows), len(stores)), flush=True)
    # contiguous slices of the sorted list, so a shard's order is the catalog's sorted order
    k = -(-len(stores) // workers)
    jobs = [(w, stores[w * k:(w + 1) * k], outdir, threads) for w in range(workers)]
    t0 = time.time()
    with Pool(workers) as p:
        for wid, n, dt in p.imap_unordered(work, jobs):
            print("shard %02d: %d stores in %.0f s" % (wid, n, dt), flush=True)
    print("fetched %d stores in %.0f s" % (len(stores), time.time() - t0), flush=True)
    st, nobj, nbytes = {}, 0, 0
    for w in range(workers):
        for line in open(os.path.join(outdir, "%02d.jsonl" % w)):
            r = json.loads(line)
            st[str(r["status"])] = st.get(str(r["status"]), 0) + 1
            for v in r.get("vars", {}).values():
                for c in v.get("chunks", []):
                    if "len" in c:
                        nobj += 1
                        nbytes += c["len"]
                    else:
                        st["chunk_%s" % c["status"]] = st.get("chunk_%s" % c["status"], 0) + 1
    print("store status: %s" % json.dumps(st, sort_keys=True), flush=True)
    print("chunks fetched: %d, %.3f GB" % (nobj, nbytes / 1e9), flush=True)
    if st.get("error"):
        sys.exit("FAIL: %d stores could not be fetched" % st["error"])


if __name__ == "__main__":
    main()
