#!/usr/bin/env bash
# Does sorting + coalescing pay at the density a REAL sample implies?
#
# My first attempt at this measured nothing and the test was the reason: 10,000 probes spread
# over 1.189 TB sit ~119 MB apart, so a 1 MiB coalescing window merged 97 of 10,000 and
# doubled bytes for no gain. Undersized by ~100x to detect its own mechanism.
#
# DENSITY is the variable. A 1M-read sample is ~30M minimizer lookups over 1.189 TB, i.e. a
# mean spacing of ~39.6 KB. I can reproduce that spacing WITHOUT issuing 30M requests by
# confining N probes to a region of N x 39,636 bytes: locally identical structure, 3 orders
# of magnitude cheaper. That is the whole trick, and it is why this probe is honest rather
# than a shortcut.
#
#   RUNG A  the same probes at REALISTIC density (~39.6 KB apart), sweeping the coalescing
#           window. Reports requests, bytes and effective lookups/s so the knee is visible.
#   RUNG B  the identical sweep at SPARSE density (~119 MB apart) -- the configuration that
#           measured nothing last time. If A moves and B doesn't, density is proven to be
#           the variable rather than asserted.
#   RUNG C  a plain sequential scan of the region, as the limit the sweep should converge to.
#
# If the knee lands at a window where "coalesce" has become "read the whole region", then at
# real density the optimal strategy IS a scan -- which is the merge-join argument, measured.
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"
R="$W/result.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/density.txt" --only-show-errors 2>/dev/null || true; }
trap 'say trap_exit_line "$LINENO"; push' EXIT

say instance_type "$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')" http://169.254.169.254/latest/meta-data/instance-type || echo '?')"
say nproc "$(nproc --all)"; push
sudo dnf install -y -q python3-pip >/dev/null 2>&1
pip3 install --quiet --disable-pip-version-check boto3 >/dev/null 2>&1

# Heredoc binds to python3 (before the pipe), NOT to tee -- see concurrency.sh.
python3 -u - <<'PY' 2>&1 | tee -a "$R"
import boto3, botocore, random, time
from concurrent.futures import ThreadPoolExecutor

BUCKET = "kraken2-ncbi-refseq-complete-v205"
KEY    = "Kraken2_RefSeqCompleteV205/hash.k2d"
cfg = botocore.config.Config(signature_version=botocore.UNSIGNED,
                             max_pool_connections=2048,
                             retries={"max_attempts": 3, "mode": "adaptive"})
s3 = boto3.client("s3", config=cfg)
SZ = s3.head_object(Bucket=BUCKET, Key=KEY)["ContentLength"]

N      = 10000          # lookups per configuration
DEPTH  = 256            # from the queue-depth sweep: past the linear region, below the plateau
PROBE  = 4096
DENSE  = 39636          # bytes: mean spacing of ~30M probes over this table (a 1M-read sample)
SPARSE = SZ // N        # ~119 MB: the spacing my FIRST attempt accidentally tested

print("hash_k2d_bytes\t%d" % SZ)
print("probes_per_config\t%d\tdepth\t%d" % (N, DEPTH))
print("dense_spacing_bytes\t%d\t(a 1M-read sample)" % DENSE)
print("sparse_spacing_bytes\t%d\t(what the failed test measured)" % SPARSE)

def get(off, n):
    r = s3.get_object(Bucket=BUCKET, Key=KEY, Range="bytes=%d-%d" % (off, off + n - 1))
    return len(r["Body"].read())

def offsets(spacing, seed):
    """N probes with the given MEAN spacing, i.e. confined to a region N*spacing wide."""
    random.seed(seed)
    span = N * spacing
    base = random.randrange(0, max(1, SZ - span - PROBE))
    return [base + random.randrange(0, span) for _ in range(N)]

def spans(offs, window):
    """Sort, then merge probes lying within `window` of a span's start into one range GET."""
    out, cur = [], None
    for o in sorted(offs):
        if cur is not None and o - cur[0] < window:
            cur[1] = max(cur[1], o + PROBE)
        else:
            if cur: out.append(cur)
            cur = [o, o + PROBE]
    if cur: out.append(cur)
    return out

def run(label, offs, window):
    reqs = [(o, PROBE) for o in offs] if window is None else \
           [(a, b - a) for a, b in spans(offs, window)]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=DEPTH) as ex:
        got = sum(ex.map(lambda r: get(*r), reqs))
    dt = time.time() - t0
    print("%-18s %7d reqs %9.1f MiB %7.2f s  %10.1f lookups/s" %
          (label, len(reqs), got / 2**20, dt, N / dt))
    return N / dt

WINDOWS = [(None, "individual"), (64*1024, "64 KiB"), (256*1024, "256 KiB"),
           (1<<20, "1 MiB"), (4<<20, "4 MiB"), (16<<20, "16 MiB")]

print("\n== RUNG A: REALISTIC density (~39.6 KB apart) -- sweep the coalescing window ==")
dense = offsets(DENSE, 11)
print("region_width_mib\t%.1f" % (N * DENSE / 2**20))
a = {}
for win, name in WINDOWS:
    try: a[name] = run(name, dense, win)
    except Exception as e: print("%-18s FAILED %s" % (name, type(e).__name__))

print("\n== RUNG B: SPARSE density (~119 MB apart) -- the test that measured nothing ==")
sparse = offsets(SPARSE, 11)
b = {}
for win, name in [WINDOWS[0], WINDOWS[3]]:
    try: b[name] = run(name, sparse, win)
    except Exception as e: print("%-18s FAILED %s" % (name, type(e).__name__))

print("\n== RUNG C: plain sequential scan of the dense region (the limit case) ==")
lo, hi = min(dense), max(dense) + PROBE
CH = 8 << 20
chunks = [(o, min(CH, hi - o)) for o in range(lo, hi, CH)]
t0 = time.time()
with ThreadPoolExecutor(max_workers=DEPTH) as ex:
    got = sum(ex.map(lambda r: get(*r), chunks))
dt = time.time() - t0
print("%-18s %7d reqs %9.1f MiB %7.2f s  %10.1f lookups/s  (%.0f MB/s)" %
      ("scan 8 MiB", len(chunks), got/2**20, dt, N/dt, got/1e6/dt))

print("\n== VERDICT ==")
if a and b:
    ai = a.get("individual"); am = max(a.values())
    bi = b.get("individual"); bm = max(b.values())
    best = max(a, key=a.get)
    print("dense:  individual %.1f -> best %.1f lookups/s  (%.1fx, window=%s)" % (ai, am, am/ai, best))
    print("sparse: individual %.1f -> best %.1f lookups/s  (%.1fx)" % (bi, bm, bm/bi))
    print("If dense gains and sparse does not, DENSITY was the variable -- and my earlier")
    print("null result was an undersized test, not evidence against sorting.")
PY
push
say DONE yes; push
