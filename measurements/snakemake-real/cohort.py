"""rule cohort (Snakefile): the join over every file's stats row.

Asserts, and exits 1 on any failure:
  - exactly the eleven files of cohort-truth.tsv were joined, each once;
  - each file's num_seqs and sum_len == its published read and base counts;
  - each paired run's two mates hold the same number of reads;
  - the fan-out's total reads and bases == the published totals (the conservation identity a
    lost, duplicated or mis-joined shard cannot satisfy).

Runs as a Snakemake `script:` (the `snakemake` object) or directly, for the run without Snakemake:
    python3 cohort.py <cohort-truth.tsv> <out.tsv> <stats/*.tsv ...>
Python >= 3.9 (the direct run uses the host's)."""
import os
import re
import sys
import time
import urllib.request

if "snakemake" in globals():
    truth_path, out_path, stat_paths = snakemake.input.truth, snakemake.output[0], list(snakemake.input.stats)  # noqa: F821
    log_path = snakemake.log[0]  # noqa: F821
else:
    truth_path, out_path, stat_paths, log_path = sys.argv[1], sys.argv[2], sys.argv[3:], None


def imds(path):
    try:
        tok = urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=2).read().decode()
        return urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/" + path,
            headers={"X-aws-ec2-metadata-token": tok}), timeout=2).read().decode()
    except Exception:
        return "unknown"


def table(path):
    rows = [line.rstrip("\n").split("\t") for line in open(path) if line.strip()]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


truth = dict()
for r in table(truth_path):
    if r["file"] in truth:
        sys.exit("FAIL: %s appears twice in %s" % (r["file"], truth_path))
    truth[r["file"]] = (int(r["reads"]), int(r["bases"]))

fails = []
got = dict()
for p in stat_paths:
    rows = table(p)
    if len(rows) != 1:
        fails.append("%s: %d rows, want 1" % (p, len(rows)))
        continue
    f = rows[0]["file"]
    if f in got:
        fails.append("%s joined twice" % f)
    got[f] = (int(rows[0]["num_seqs"]), int(rows[0]["sum_len"]))

if sorted(got) != sorted(truth):
    fails.append("joined %s, want %s" % (sorted(got), sorted(truth)))

lines = ["file\tnum_seqs\tpublished_reads\tsum_len\tpublished_bases\tmatch"]
for f in sorted(got):
    n, ln = got[f]
    wn, wl = truth.get(f, (-1, -1))
    ok = n == wn and ln == wl
    if not ok:
        fails.append("%s: %d reads / %d bases, published %d / %d" % (f, n, ln, wn, wl))
    lines.append("%s\t%d\t%d\t%d\t%d\t%s" % (f, n, wn, ln, wl, "OK" if ok else "MISMATCH"))

for f in sorted(got):
    m = re.match(r"^(.*)_1(\..*)$", f)
    if m:
        mate = m.group(1) + "_2" + m.group(2)
        if mate in got and got[mate][0] != got[f][0]:
            fails.append("%s and %s differ in read count" % (f, mate))

tn, tl = sum(v[0] for v in got.values()), sum(v[1] for v in got.values())
wn, wl = sum(v[0] for v in truth.values()), sum(v[1] for v in truth.values())
if (tn, tl) != (wn, wl):
    fails.append("total %d reads / %d bases, published %d / %d" % (tn, tl, wn, wl))
lines.append("TOTAL\t%d\t%d\t%d\t%d\t%s" % (tn, wn, tl, wl, "OK" if (tn, tl) == (wn, wl) else "MISMATCH"))

with open(out_path, "w") as fh:
    fh.write("\n".join(lines) + "\n")
sys.stdout.write("\n".join(lines) + "\n")
if log_path:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "w") as fh:
        for k, v in (("instance_id", imds("instance-id")), ("instance_type", imds("instance-type")),
                     ("end", "%.3f" % time.time()), ("files_joined", str(len(got))), ("fails", str(len(fails)))):
            fh.write("%s\t%s\n" % (k, v))
        for x in fails:
            fh.write("FAIL\t%s\n" % x)
if fails:
    sys.exit("FAIL: " + "; ".join(fails))
print("COHORT OK: %d files, every count == published, total %d reads / %d bases" % (len(got), tn, tl))
