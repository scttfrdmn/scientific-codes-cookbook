#!/usr/bin/env bash
# Does concurrency buy back S3's latency without the latency changing at all?
#
# The assumption this tests is the one nobody checks: "random access over network storage is
# hopeless." That is treated as physics, but it is a statement about QUEUE DEPTH. kraken2
# --memory-mapping gets ~8 lookups/s over a lith mount (measured) because an mmap page fault
# is one synchronous request per thread -- not because S3 cannot serve the pattern.
#
# So: issue the SAME random 4 KiB reads at increasing depth and read the curve. Raw S3 range
# GETs, no FUSE, so this isolates the question from lith entirely -- if the curve is flat the
# assumption is right and lith is blameless; if it scales, the ceiling was the API, not the
# storage.
#
# Rung 2 separates the two design moves. Sorting queries so they walk the table in order
# should let neighbouring lookups share a fetch; concurrency should hide latency. Measuring
# them separately says which does the work.
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"
R="$W/result.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/concurrency.txt" --only-show-errors 2>/dev/null || true; }
trap 'say trap_exit_line "$LINENO"; push' EXIT

say instance_type "$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')" http://169.254.169.254/latest/meta-data/instance-type || echo '?')"
say nproc "$(nproc --all)"; push
sudo dnf install -y -q python3-pip >/dev/null 2>&1
pip3 install --quiet --disable-pip-version-check boto3 >/dev/null 2>&1
say boto3 "$(python3 -c 'import boto3;print(boto3.__version__)' 2>&1 | tail -1)"; push

# Heredoc MUST bind to python3, i.e. sit before the pipe. `python3 - 2>&1 | tee <<'PY'`
# attaches it to tee (the last command in the pipeline), so python reads an empty stdin and
# tee writes the script's own source into the results file. That is exactly what happened on
# the first run of this probe.
python3 -u - <<'PY' 2>&1 | tee -a "$R"
import boto3, botocore, random, time, statistics
from concurrent.futures import ThreadPoolExecutor

BUCKET = "kraken2-ncbi-refseq-complete-v205"
KEY    = "Kraken2_RefSeqCompleteV205/hash.k2d"
# unsigned: public RODA bucket. Big pool so depth is not throttled by the client.
cfg = botocore.config.Config(
    signature_version=botocore.UNSIGNED,
    max_pool_connections=2048,
    retries={"max_attempts": 3, "mode": "adaptive"},
)
s3 = boto3.client("s3", config=cfg)
SZ = s3.head_object(Bucket=BUCKET, Key=KEY)["ContentLength"]
print("hash_k2d_bytes\t%d" % SZ)

def get(off, n=4096):
    r = s3.get_object(Bucket=BUCKET, Key=KEY, Range="bytes=%d-%d" % (off, off + n - 1))
    return len(r["Body"].read())

def sweep(depth, n):
    random.seed(42)                      # same offsets at every depth: only depth changes
    offs = [random.randrange(0, SZ - 4096) for _ in range(n)]
    t0 = time.time()
    if depth == 1:
        for o in offs: get(o)
    else:
        with ThreadPoolExecutor(max_workers=depth) as ex:
            list(ex.map(get, offs))
    dt = time.time() - t0
    return n / dt, 1000 * dt / n * depth   # lookups/s, and per-request latency

print("\n== RUNG 1: identical random 4 KiB reads, only the queue depth changes ==")
print("depth\tn\tlookups_per_s\tapparent_req_latency_ms")
base = None
for depth, n in ((1, 40), (4, 80), (16, 320), (64, 1280), (256, 2560), (1024, 5120)):
    try:
        rate, lat = sweep(depth, n)
        if base is None: base = rate
        print("%d\t%d\t%.1f\t%.0f\t(%.0fx over depth 1)" % (depth, n, rate, lat, rate / base))
    except Exception as e:
        print("%d\tFAILED\t%s" % (depth, type(e).__name__))

print("\n== RUNG 2: does SORTING the queries help, independent of depth? ==")
# 10k lookups, depth 256, three orderings. Sorted+coalesced merges probes that fall within
# one 1 MiB span into a single GET -- design move (1) from the write-up.
N, DEPTH, COALESCE = 10000, 256, 1 << 20
random.seed(7)
offs = [random.randrange(0, SZ - 4096) for _ in range(N)]

def timed(label, work):
    t0 = time.time(); got = work(); dt = time.time() - t0
    print("%-22s %6.1f s  %8.1f lookups/s  %5d requests  %6.1f MiB"
          % (label, dt, N / dt, got[0], got[1] / 2**20))

def plain(order):
    with ThreadPoolExecutor(max_workers=DEPTH) as ex:
        tot = sum(ex.map(get, order))
    return len(order), tot

def coalesced(order):
    spans, cur = [], None
    for o in sorted(order):
        if cur and o - cur[0] < COALESCE: cur[1] = o + 4096
        else:
            if cur: spans.append(cur)
            cur = [o, o + 4096]
    if cur: spans.append(cur)
    with ThreadPoolExecutor(max_workers=DEPTH) as ex:
        tot = sum(ex.map(lambda s: get(s[0], s[1] - s[0]), spans))
    return len(spans), tot

timed("random order",   lambda: plain(offs))
timed("sorted order",   lambda: plain(sorted(offs)))
timed("sorted+coalesced", lambda: coalesced(offs))
print("\nNOTE: lookups/s is the metric; requests and MiB show what each ordering COSTS to get it.")
PY
push
say DONE yes; push
