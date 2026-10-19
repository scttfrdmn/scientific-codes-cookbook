"""Fetch UCI HIGGS (doi:10.24432/C5V312) inside an AWS task and record what was fetched.

Runs on the box, never on a laptop: the file is 2.8 GB. Writes HIGGS.csv.gz plus fetch.tsv
(sha256, bytes, rows, columns, label counts) so the pin is taken from bytes that were read
end to end, not from a header.
"""
import gzip, hashlib, time, urllib.request

URL = "https://archive.ics.uci.edu/ml/machine-learning-databases/00280/HIGGS.csv.gz"
t0 = time.time()
h = hashlib.sha256(); n = 0
# UCI serves this chunked with no Content-Length and drops the connection part-way (measured:
# at 1.25 GB of 2.8). It honours Range, so resume from the byte reached. Completeness is proved
# below by reading the gzip stream to its end, which checks the gzip CRC-32 and length trailer.
with open("/tmp/HIGGS.csv.gz", "wb") as f:
    for attempt in range(30):
        req = urllib.request.Request(URL, headers={"Range": "bytes=%d-" % n} if n else {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                if n and r.status != 206:
                    raise SystemExit("server ignored Range on resume (status %d)" % r.status)
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    h.update(b); f.write(b); n += len(b)
            break
        except Exception as e:  # IncompleteRead, reset, timeout: resume from n
            print("attempt %d stopped at %d bytes: %s" % (attempt, n, type(e).__name__), flush=True)
    else:
        raise SystemExit("gave up after 30 attempts")
print("fetched %d bytes in %.0f s" % (n, time.time() - t0), flush=True)

rows = 0; cols = set(); labels = {}
with gzip.open("/tmp/HIGGS.csv.gz", "rt") as f:
    for line in f:
        rows += 1
        parts = line.rstrip("\n").split(",")
        cols.add(len(parts))
        labels[parts[0]] = labels.get(parts[0], 0) + 1
print("rows %d cols %s labels %s" % (rows, sorted(cols), labels), flush=True)
with open("/tmp/fetch.tsv", "w") as o:
    o.write("url\t%s\nsha256\t%s\nbytes\t%d\nrows\t%d\ncols\t%s\nlabels\t%s\n"
            % (URL, h.hexdigest(), n, rows, sorted(cols), sorted(labels.items())))
print("sha256", h.hexdigest())
print("FETCH OK")
