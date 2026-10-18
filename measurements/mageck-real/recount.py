#!/usr/bin/env python3
"""Recount every FASTQ without MAGeCK's code, under each `mageck count` run's 5' offsets.

    recount.py <library> <run>=<mageck count log> [...] -- <label>:<fastq.gz> [...]

MAGeCK's counting rule (mageckCountIO.py, 0.5.9.5): for each read, try the 5' offsets in the
order the log lists them (auto-determined, or given with --trim-5), and count the first exact
20-mer that is a library sequence. Library rules: first record per ID and per sequence wins; a
non-ACGT first line is a header. This reimplements that rule, one process per sample (gzip -dc
in its own process), for every run in one pass over the reads. It also counts the reads that
hold a library guide at ANY offset, and at which offset the ones a run left uncounted sit.
Writes recount.<run>.<label>.tsv and recount.<run>.<label>.stats.
"""
import multiprocessing as mp
import re
import subprocess
import sys


def library(path):
    ids, seqs, n = {}, {}, 0
    for line in open(path):
        f = line.strip().split("\t")
        n += 1
        if f[0] in ids or len(f) < 3:
            continue
        s = f[1].upper()
        if n == 1 and re.search("[^ATCG]", s):
            continue
        if s in seqs:
            continue
        ids[f[0]] = s
        seqs[s] = f[0]
    return seqs


def trims(log):
    """{fastq path: [offsets]} from a mageck count log, in the order MAGeCK tries them."""
    out, cur = {}, None
    for line in open(log):
        m = re.search(r"Parsing FASTQ file (.+?)\.\.\.\s*$", line)
        if m:
            cur = m.group(1)
        m = re.search(r"(?:Auto determination of trim5 results: |Specified trimming length:)(\S+)", line)
        if m and cur:
            out[cur] = [int(x) for x in m.group(1).split(",")]
            cur = None
    return out


def one(job):
    label, fq, runs, seqs = job          # runs: [(run, [offsets])]
    lens = sorted({len(s) for s in seqs}, reverse=True)
    assert lens == [20], lens
    keys = {s.encode(): sg for s, sg in seqs.items()}
    counts = {r: dict.fromkeys(keys, 0) for r, _ in runs}
    mapped = {r: 0 for r, _ in runs}
    left = {r: {} for r, _ in runs}      # offset of the first guide in reads this run did not count
    reads = anyoff = 0
    p = subprocess.Popen(["gzip", "-dc", fq], stdout=subprocess.PIPE, bufsize=1 << 20)
    n = 0
    for line in p.stdout:
        n += 1
        if n % 4 != 2:
            continue
        reads += 1
        s = line.strip()
        missed = []
        for r, offs in runs:
            hit = None
            for t in offs:
                k = s[t:t + 20]
                if len(k) == 20 and k in keys:
                    hit = k
                    break
            if hit is None:
                missed.append(r)
            else:
                counts[r][hit] += 1
                mapped[r] += 1
        if missed:          # only reads some run did not count need the full offset scan
            first = None
            for t in range(0, len(s) - 19):
                if s[t:t + 20] in keys:
                    first = t
                    break
            if first is None:
                continue
            for r in missed:
                left[r][first] = left[r].get(first, 0) + 1
        anyoff += 1
    rc = p.wait()
    for r, offs in runs:
        with open("recount.%s.%s.tsv" % (r, label), "w") as o:
            for k, c in counts[r].items():
                o.write("%s\t%d\n" % (keys[k], c))
        with open("recount.%s.%s.stats" % (r, label), "w") as o:
            o.write("gzip_exit\t%d\nlines\t%d\nreads\t%d\nmapped\t%d\nmapped_any_offset\t%d\noffsets\t%s\n"
                    % (rc, n, reads, mapped[r], anyoff, ",".join(map(str, offs))))
            o.write("uncounted_offsets\t%s\n" % " ".join("%d:%d" % kv for kv in sorted(left[r].items())))
    return label, rc, n, reads, anyoff, mapped


def main():
    a = sys.argv[1:]
    cut = a.index("--")
    seqs = library(a[0])
    runs = [x.split("=", 1) for x in a[1:cut]]
    tr = {r: trims(log) for r, log in runs}
    print("library\t%d unique sequences; offsets %s" % (len(seqs), tr))
    jobs = []
    for x in a[cut + 1:]:
        label, fq = x.split(":", 1)
        for r, _ in runs:
            if fq not in tr[r]:
                print("FAIL: run %s has no 5' offsets for %s" % (r, fq))
                sys.exit(1)
        jobs.append((label, fq, [(r, tr[r][fq]) for r, _ in runs], seqs))
    bad = False
    with mp.Pool(len(jobs)) as pool:
        for label, rc, n, reads, anyoff, mapped in pool.map(one, jobs):
            print("%s\tgzip %d lines %d reads %d any-offset %d mapped %s" % (label, rc, n, reads, anyoff, mapped))
            if rc != 0 or n % 4 != 0:
                bad = True
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
