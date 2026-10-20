#!/usr/bin/env python3
"""Fetch the spacy-real inputs ON AWS, checking every byte against its publisher's own checksum.
    fetch.py <first PMCID number> <last PMCID number> <out dir>

1. PMC Open Access (RODA, s3://pmc-oa-opendata, anonymous): every article whose PMCID number is in
   [first, last], at its latest version. Each article's metadata/<id>.<v>.json carries the md5 of
   its .txt; the text is kept only if it matches. Written as 256 contiguous gzip shards of jsonl
   (sorted by PMCID, mtime 0, so the bytes are reproducible) plus manifest.tsv.
2. UD English-EWT at tag r2.18 (commit b7711cce), each file checked against its git blob id.
3. The CoNLL 2018 shared-task scorer (ufal/conll2018 at 1a2c3733), checked against its sha256.
"""
import concurrent.futures as cf
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

LO, HI, OUT = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
S3 = "https://pmc-oa-opendata.s3.us-east-1.amazonaws.com/"
NSHARD = 256
EWT = "https://raw.githubusercontent.com/UniversalDependencies/UD_English-EWT/b7711cce01cdd4f5fcc0a8199b8a50d951b16c0c/"
EWT_BLOBS = {  # git blob ids at r2.18, from the GitHub contents API
    "en_ewt-ud-train.conllu": "90f7d75edc3147331665c38f668401a96b49e7c8",
    "en_ewt-ud-dev.conllu": "120d04140cf4833690117bc74c08c8da861bbce6",
    "en_ewt-ud-test.conllu": "435cd8c5240754ba81a5d00ed119f63d1fae78b6",
    "LICENSE.txt": "547fb999d1fc888c7071f50a0cf6b7338eeae2fe",
}
SCORER = ("https://raw.githubusercontent.com/ufal/conll2018/1a2c3733b3ce0b2779c4aba5fe77309f8e858481/"
          "evaluation_script/conll18_ud_eval.py",
          "589506a892ccb4aa8b0164b2dcc6536ecb571441786f0618c0f6328656c1a582")


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception as e:  # a transient network error; the checksum decides what is kept
            err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("%s: %s" % (url, err))


os.makedirs(os.path.join(OUT, "pmc"), exist_ok=True)
os.makedirs(os.path.join(OUT, "ewt"), exist_ok=True)

# --- UD English-EWT and the scorer ---
for f, blob in EWT_BLOBS.items():
    data = get(EWT + f)
    got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if got != blob:
        sys.exit("FAIL: %s git blob %s, r2.18 says %s" % (f, got, blob))
    open(os.path.join(OUT, "ewt", f), "wb").write(data)
    print("ewt %-24s %9d bytes, blob id matches r2.18" % (f, len(data)), flush=True)
data = get(SCORER[0])
if hashlib.sha256(data).hexdigest() != SCORER[1]:
    sys.exit("FAIL: conll18_ud_eval.py does not match its pin")
open(os.path.join(OUT, "ewt", "conll18_ud_eval.py"), "wb").write(data)
print("conll18_ud_eval.py matches its pin", flush=True)

# --- PMC OA: list the article prefixes in range, keep the latest version of each ---
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
versions = {}
lead = sorted({str(n)[:-4] for n in range(LO, HI + 1)})   # PMC1000.. prefixes, 10,000 ids each
for p in lead:
    token = None
    while True:
        q = "?list-type=2&delimiter=/&prefix=PMC%s" % p + ("&continuation-token=" + urllib.request.quote(token, safe="") if token else "")
        root = ET.fromstring(get(S3 + q))
        for cp in root.iter(NS + "CommonPrefixes"):
            m = re.fullmatch(r"PMC(\d+)\.(\d+)/", cp.find(NS + "Prefix").text)
            if m and LO <= int(m.group(1)) <= HI:
                n, v = int(m.group(1)), int(m.group(2))
                versions[n] = max(versions.get(n, 0), v)
        if root.find(NS + "IsTruncated").text != "true":
            break
        token = root.find(NS + "NextContinuationToken").text
ids = sorted(versions)
print("PMC%d..PMC%d: %d articles listed (latest version of each)" % (LO, HI, len(ids)), flush=True)


def one(n):
    key = "PMC%d.%d" % (n, versions[n])
    meta = json.loads(get(S3 + "metadata/%s.json" % key))
    url = meta.get("text_url") or ""
    m = re.match(r"s3://pmc-oa-opendata/(.+)\?md5=([0-9a-f]{32})$", url)
    if not m:
        return key, meta, None, "no text_url"
    data = get(S3 + m.group(1))
    if hashlib.md5(data).hexdigest() != m.group(2):
        return key, meta, None, "md5 mismatch"
    return key, meta, data, None


recs, skipped = {}, []
with cf.ThreadPoolExecutor(64) as ex:
    for i, (key, meta, data, why) in enumerate(ex.map(one, ids)):
        if why:
            skipped.append((key, why))
        else:
            recs[key] = (meta, data)
        if i % 5000 == 0:
            print("  fetched %d of %d" % (i, len(ids)), flush=True)
bad = [s for s in skipped if s[1] == "md5 mismatch"]
print("kept %d; no text %d; md5 mismatch %d" % (len(recs), len(skipped) - len(bad), len(bad)), flush=True)
if bad:
    sys.exit("FAIL: %d texts do not match their publisher md5: %s" % (len(bad), bad[:5]))

keys = sorted(recs, key=lambda k: int(k[3:].split(".")[0]))
assert len({k.split(".")[0] for k in keys}) == len(keys), "duplicate PMCID"
man = open(os.path.join(OUT, "pmc", "manifest.tsv"), "w")
man.write("shard\tkey\tmd5\tsha256\tbytes\tlicense_code\tis_historical_ocr\tis_manuscript\tis_retracted\n")
per = -(-len(keys) // NSHARD)
total = 0
for s in range(NSHARD):
    chunk = keys[s * per:(s + 1) * per]
    path = os.path.join(OUT, "pmc", "shard-%03d.jsonl.gz" % s)
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        for k in chunk:
            meta, data = recs[k]
            gz.write((json.dumps({"key": k, "text": data.decode("utf-8")}, ensure_ascii=False, sort_keys=True)
                      + "\n").encode("utf-8"))
            man.write("%03d\t%s\t%s\t%s\t%d\t%s\t%s\t%s\t%s\n" % (
                s, k, hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest(), len(data),
                meta.get("license_code"), meta.get("is_historical_ocr"), meta.get("is_manuscript"),
                meta.get("is_retracted")))
            total += len(data)
man.close()
with open(os.path.join(OUT, "pmc", "shards.sha256"), "w") as fh:
    for s in range(NSHARD):
        p = os.path.join(OUT, "pmc", "shard-%03d.jsonl.gz" % s)
        fh.write("%s  shard-%03d.jsonl.gz\n" % (hashlib.sha256(open(p, "rb").read()).hexdigest(), s))
for k, why in skipped:
    print("  no text: %s (%s)" % (k, why))
print("wrote %d articles, %d bytes of text, in %d shards" % (len(keys), total, NSHARD), flush=True)
