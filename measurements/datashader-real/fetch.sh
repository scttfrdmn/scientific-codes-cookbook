set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp
mkdir -p /tmp/out && cd /tmp/out
# NYC TLC yellow-taxi trips 2009-2010, the only years the TLC still publishes with coordinates,
# fetched ON AWS from the TLC's own CloudFront origin. The sha256 recorded here is the pin.
python3 -u - <<'PYEOF' || { echo "FAIL: fetch"; exit 1; }
import hashlib, urllib.request
from concurrent.futures import ThreadPoolExecutor
import pyarrow.parquet as pq
U = "https://d37ci6vzurychx.cloudfront.net/trip-data/"
names = ["yellow_tripdata_%d-%02d.parquet" % (y, m) for y in (2009, 2010) for m in range(1, 13)]
def get(name):
    for attempt in range(3):
        try:
            h = hashlib.sha256(); n = 0
            with urllib.request.urlopen(U + name, timeout=900) as r, open(name, "wb") as fh:
                want = int(r.headers["Content-Length"])
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    h.update(b); fh.write(b); n += len(b)
            assert n == want, "%s: %d bytes, Content-Length %d" % (name, n, want)
            return name, n, h.hexdigest()
        except Exception as e:
            print("retry", name, repr(e), flush=True)
    raise SystemExit("FAIL: %s did not download" % name)
with ThreadPoolExecutor(6) as ex:
    got = list(ex.map(get, names))
with open("data.sha256", "w") as out, open("rows.tsv", "w") as rows:
    for name, n, sha in got:
        out.write("%s  %s\n" % (sha, name))
        md = pq.ParquetFile(name).metadata
        rows.write("%s\t%d\t%d\t%d\n" % (name, n, md.num_rows, md.num_row_groups))
        print(name, n, md.num_rows, sha, flush=True)
PYEOF
cat data.sha256 rows.tsv
ls -l
echo DONE
