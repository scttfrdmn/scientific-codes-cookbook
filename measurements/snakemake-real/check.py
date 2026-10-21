#!/usr/bin/env python3
"""The identities over results/ (run.sh's copies), per chip and across chips. Writes results/check.txt.

    python3 check.py

Per chip: the cold run joined eleven files with every count == published and no job failing; each
re-run executed EXACTLY the jobs its change implies (from Snakemake's own log) and rewrote exactly
those jobs' objects in S3 and no others (from the S3 listings before and after); every re-created
output is byte-identical to the cold run's; the run without Snakemake (direct.sh) matched every
output. Across chips: every output byte-identical."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import kv, snakemake_jobs  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(D, "results")
F1 = "ERR188026_1.fastq.gz"
LOST = "SRR8297998.fastq.gz"
# phase -> (the phase whose S3 listing it starts from, the jobs it must run)
PHASES = {
    "arm64": [("r1", None, "all"), ("recover", "recopied", {("stats", LOST)}), ("again", "recover", {("cohort", "-")}),
              ("noop", "again", set()), ("touch1", "noop", {("stats", F1), ("cohort", "-")}),
              ("touch2", "touch1", {("cohort", "-")})],
    "x86_64": [("r1", None, "all"), ("noop", "r1", set()), ("touch1", "noop", {("stats", F1), ("cohort", "-")}),
               ("touch2", "touch1", {("cohort", "-")})],
}
PHASES["amd"] = PHASES["x86_64"]
fails = []
lines = []


def rec(k, v, ok=True):
    lines.append("%s\t%s%s" % (k, v, "" if ok else "  FAIL"))
    if not ok:
        fails.append(k)


def listing(chip, phase):
    out = {}
    for line in open(os.path.join(R, chip, phase + ".objects.tsv")):
        key, mtime, etag, _ = line.rstrip("\n").split("\t")
        if "snakemake-workflow-sources" not in key:
            out[key.split("/snakemake-real/r1/", 1)[1]] = (mtime, etag)
    return out


def objects_of(jobs):
    want = set()
    for rule, f in jobs:
        want |= {"stats/%s.tsv" % f, "logs/stats/%s.log" % f} if rule == "stats" else {"cohort.tsv", "logs/cohort.log"}
    return want


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def science(chip, phase):
    """sha256 of every science output (stats rows, the cohort table) in a phase's copy."""
    d = os.path.join(R, chip, "outputs-" + phase)
    files = ["cohort.tsv"] + ["stats/" + f for f in sorted(os.listdir(os.path.join(d, "stats")))]
    return {f: sha(os.path.join(d, f)) for f in files}


ALL = {("stats", f.split("\t")[0]) for f in open(os.path.join(D, "..", "qc-aggregate-real", "cohort.tsv")).read().splitlines()[1:]} | {("cohort", "-")}
for chip, phases in PHASES.items():
    if not os.path.exists(os.path.join(R, chip, "r1.log")):
        rec(chip, "not run", False)
        continue
    base = science(chip, "r1")
    rec("%s r1 outputs" % chip, "%d science files" % len(base), len(base) == 12)
    cohort = open(os.path.join(R, chip, "outputs-r1", "cohort.tsv")).read().splitlines()
    rec("%s r1 cohort rows OK" % chip, "%d of 11 + TOTAL" % sum(l.endswith("\tOK") for l in cohort[1:-1]),
        sum(l.endswith("\tOK") for l in cohort[1:]) == 12 and len(cohort) == 13)
    rec("%s r1 TOTAL" % chip, cohort[-1].replace("\t", " "), cohort[-1].endswith("\tOK"))
    logs = os.path.join(R, chip, "outputs-r1", "logs", "stats")
    bad = [f for f in os.listdir(logs) if kv(os.path.join(logs, f)).get("fails") != "0"]
    rec("%s r1 stats jobs failing their own check" % chip, len(bad), not bad)
    for phase, before, want in phases:
        p = os.path.join(R, chip, phase + ".log")
        w = os.path.join(R, chip, phase + ".wall")
        if not os.path.exists(w) or len(open(w).read().split()) < 3:
            rec("%s %s" % (chip, phase), "not run", False)
            continue
        ran = set(snakemake_jobs(p))
        want = ALL if want == "all" else want
        rec("%s %s jobs run" % (chip, phase), " ".join(sorted("%s:%s" % j for j in ran)) or "none", ran == want)
        rc = open(os.path.join(R, chip, phase + ".wall")).read().split()[2]
        rec("%s %s exit" % (chip, phase), rc, rc == "0")
        if before:
            a, b = listing(chip, before), listing(chip, phase)
            changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            rec("%s %s objects rewritten" % (chip, phase), " ".join(sorted(changed)) or "none", changed == objects_of(want))
        if phase != "r1":
            now = science(chip, phase)
            rec("%s %s outputs == r1, byte for byte" % (chip, phase), "%d of %d" % (sum(now[f] == base.get(f) for f in now), len(base)),
                now == base)
    dc = os.path.join(R, chip, "direct", "check.txt")
    if os.path.exists(dc):
        k = kv(dc)
        rec("%s direct: stats identical to the workflow's" % chip, k.get("stats_identical"), k.get("stats_identical") == "11")
        rec("%s direct: cohort identical to the workflow's" % chip, k.get("cohort_identical_to_workflow"),
            k.get("cohort_identical_to_workflow") == "0")
        rec("%s direct: fails" % chip, k.get("fails"), k.get("fails") == "0")
    else:
        rec("%s direct" % chip, "not run", False)

chips = [c for c in PHASES if os.path.exists(os.path.join(R, c, "outputs-r1"))]
if len(chips) > 1:
    ref = science(chips[0], "r1")
    for c in chips[1:]:
        o = science(c, "r1")
        same = sum(o.get(f) == ref[f] for f in ref)
        rec("%s r1 outputs == %s r1, byte for byte" % (c, chips[0]), "%d of %d" % (same, len(ref)), o == ref)
        if o != ref:
            for f in ref:
                if o.get(f) != ref[f]:
                    lines.append("  differs: %s" % f)
# --jobs 4, once on arm64: an observation of #19, reported, not asserted.
if os.path.exists(os.path.join(R, "arm64", "outputs-jobs4")):
    o, ref = science("arm64", "jobs4"), science("arm64", "r1")
    lines.append("observed: arm64 jobs4 outputs == r1, byte for byte\t%d of %d" % (sum(o.get(f) == ref[f] for f in ref), len(ref)))
    ran = snakemake_jobs(os.path.join(R, "arm64", "jobs4.log"))
    late = []
    for (rule, f), (_, seen) in ran.items():
        p = os.path.join(R, "arm64", "outputs-jobs4", "logs", "stats/%s.log" % f if rule == "stats" else "cohort.log")
        k = kv(p)
        late.append(seen - float(k.get("script_end", k.get("end"))))
    lines.append("observed: arm64 jobs4 completion seen after the job's script ended\t%.0f to %.0f s (negative = seen early)"
                 % (min(late), max(late)))
lines.append("fails\t%d" % len(fails))
open(os.path.join(R, "check.txt"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(1 if fails else 0)
