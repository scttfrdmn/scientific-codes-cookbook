#!/usr/bin/env python3
"""cutadapt-real: check one sample's runs against its input, read by read.

    python3 check.py <acc> <ENA pairs> <ENA bases> <AD1> <AD2> <workdir> <outdir> [--full]

One pass walks the input, the unfiltered run (`raw`) and the recipe's run (`main`) in lockstep
(cutadapt keeps input order at any -j). What it asserts, all exact:

  1. the input is the archived run: pairs and bases == ENA's read_count and base_count
  2. trimming only removes a 3' suffix: every raw read is a prefix (sequence AND quality) of its input
  3. the report describes the files: per mate, the histogram of removed lengths taken from the files
     == the report's trimmed_lengths, the number of reads with something removed == readN_with_adapter,
     and bases in/out == the files' -- for both the raw and the main report
  4. cutadapt's guarantee, by an independent search: no untouched read contains its adapter exactly,
     or ends in an exact adapter prefix of >= 3 bp (-O 3; zero errors is always within -e)
  5. filtering is the documented rule applied after trimming: the main output is exactly the raw
     pairs with both mates >= 10 bp (-m 10) and no N in either (--max-n 0), too_short first, in order
  6 (--full). -j 1 writes the same records as -j T; -q 15,20 equals the BWA rule written from the docs
Observations (reported, not asserted): how many trimmed reads' removed suffix is an exact adapter
prefix, and whether both mates of a read-through pair agree on the insert (R1 == revcomp R2).
"""
import hashlib
import json
import sys
from collections import Counter

import dnaio

acc, want_pairs, want_bases, AD1, AD2, W, S = sys.argv[1:8]
want_pairs, want_bases = int(want_pairs), int(want_bases)
FULL = "--full" in sys.argv[8:]
ok = True
out = open("%s/checks.txt" % S, "w")


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    out.write(line + "\n")
    out.flush()


def check(cond, what, detail):
    global ok
    good = bool(cond)
    ok = ok and good
    say("%-4s %-62s %s" % ("ok" if good else "FAIL", what, detail))


def rep(name):
    return json.load(open("%s/%s.json" % (S, name)))


def hist(J, side):
    a = J[side][0]["three_prime_end"]
    return Counter({t["len"]: sum(t["counts"]) for t in a["trimmed_lengths"] if sum(t["counts"])})


def guarantee_violated(s, ad):
    """True if s holds a zero-error adapter match cutadapt (-O 3) must have trimmed."""
    if ad in s:
        return True
    n, k = len(s), len(ad)
    p = s.find(ad[:3], max(0, n - k + 1))
    while p != -1 and p <= n - 3:
        if ad.startswith(s[p:]):
            return True
        p = s.find(ad[:3], p + 1)
    return False


RC = str.maketrans("ACGTN", "TGCAN")
inp = dnaio.open("%s_1.fastq.gz" % acc, file2="%s_2.fastq.gz" % acc)
raw = dnaio.open("%s/raw_R1.fq" % W, file2="%s/raw_R2.fq" % W)
main = iter(dnaio.open("%s/main_R1.fq.gz" % W, file2="%s/main_R2.fq.gz" % W))
H = [Counter(), Counter()]
pairs = bases = 0
not_prefix, viol, with_ad, exact_suffix = [0, 0], [0, 0], [0, 0], [0, 0]
viol_ex = []
too_short = too_many_n = kept = main_mismatch = 0
main_bases = 0
both = both_len = both_rc = one_only = 0
sha_main = hashlib.sha256()
nxt = next(main, None)
for (i1, i2), (r1, r2) in zip(inp, raw):
    pairs += 1
    bases += len(i1.sequence) + len(i2.sequence)
    removed = []
    for m, (i, r, ad) in enumerate(((i1, r1, AD1), (i2, r2, AD2))):
        n, L = len(i.sequence), len(r.sequence)
        if r.name != i.name or not i.sequence.startswith(r.sequence) or not i.qualities.startswith(r.qualities):
            not_prefix[m] += 1
        d = n - L
        removed.append(d)
        if d:
            H[m][d] += 1
            with_ad[m] += 1
            cut = i.sequence[L:L + len(ad)]
            exact_suffix[m] += ad.startswith(cut)
        elif guarantee_violated(i.sequence, ad):
            viol[m] += 1
            if len(viol_ex) < 5:
                viol_ex.append((m + 1, i.name, i.sequence))
    if removed[0] and removed[1]:
        both += 1
        both_len += len(r1.sequence) == len(r2.sequence)
        both_rc += r1.sequence == r2.sequence.translate(RC)[::-1]
    elif removed[0] or removed[1]:
        one_only += 1
    # 5: the filter rule, in cutadapt's order
    if len(r1.sequence) < 10 or len(r2.sequence) < 10:
        too_short += 1
    elif "N" in r1.sequence or "N" in r2.sequence:
        too_many_n += 1
    else:
        kept += 1
        if nxt is None or nxt[0].fastq_bytes() != r1.fastq_bytes() or nxt[1].fastq_bytes() != r2.fastq_bytes():
            main_mismatch += 1
        if nxt is not None:
            sha_main.update(nxt[0].fastq_bytes() + nxt[1].fastq_bytes())
            main_bases += len(nxt[0].sequence) + len(nxt[1].sequence)
            nxt = next(main, None)
raw_left = sum(1 for _ in raw)
main_left = 0
while nxt is not None:
    main_left += 1
    nxt = next(main, None)

say("== %s: %d pairs ==" % (acc, pairs))
check(pairs == want_pairs and bases == want_bases, "1. input == ENA read_count, base_count",
      "%d pairs / %d bases (ENA %d / %d)" % (pairs, bases, want_pairs, want_bases))
check(raw_left == 0 and not_prefix == [0, 0], "2. every trimmed read is a prefix of its input (seq + qual)",
      "not a prefix R1 %d R2 %d, raw pairs left over %d" % (not_prefix[0], not_prefix[1], raw_left))
for name in ("raw", "main"):
    J = rep(name)
    rc, bp = J["read_counts"], J["basepair_counts"]
    for m, side in enumerate(("adapters_read1", "adapters_read2")):
        h = hist(J, side)
        check(h == H[m] and rc["read%d_with_adapter" % (m + 1)] == with_ad[m],
              "3. %s report, R%d: trimmed_lengths == files, with_adapter" % (name, m + 1),
              "%d reads, %d bp removed; %d lengths, %d differ" % (with_ad[m], sum(k * v for k, v in H[m].items()),
                                                                len(H[m]), len(set(h.items()) ^ set(H[m].items()))))
    check(rc["input"] == pairs and bp["input"] == bases, "3. %s report: pairs and bases in == files" % name,
          "%d / %d" % (rc["input"], bp["input"]))
adapt = sum(k * v for m in (0, 1) for k, v in H[m].items())
check(viol == [0, 0], "4. no untouched read holds an exact adapter or 3' adapter prefix >= 3",
      "violations R1 %d R2 %d %s" % (viol[0], viol[1], viol_ex[:2] if viol_ex else ""))
J = rep("main")
rc, flt, bp = J["read_counts"], J["read_counts"]["filtered"], J["basepair_counts"]
nflt = sum(v for v in flt.values() if v)
check(main_mismatch == 0 and main_left == 0 and rc["output"] == kept and flt["too_short"] == too_short
      and (flt["too_many_n"] or 0) == too_many_n and nflt == too_short + too_many_n,
      "5. main output == raw pairs passing -m 10 --max-n 0, in order",
      "kept %d (report %d), too_short %d (%s), too_many_n %d (%s), mismatched %d, extra %d"
      % (kept, rc["output"], too_short, flt["too_short"], too_many_n, flt["too_many_n"], main_mismatch, main_left))
check(bp["output"] == main_bases, "5. main report: bases out == file", "%d / %d" % (bp["output"], main_bases))
filt_bases = bases - adapt - main_bases
say("     bases: in %d = out %d + adapter-trimmed %d + left in filtered pairs %d" % (bases, main_bases, adapt, filt_bases))
say("obs  trimmed reads whose removed suffix is an exact adapter prefix: R1 %d/%d  R2 %d/%d"
    % (exact_suffix[0], with_ad[0], exact_suffix[1], with_ad[1]))
say("obs  pairs trimmed on both mates %d: same length %d, R1 == revcomp(R2) %d; one mate only %d"
    % (both, both_len, both_rc, one_only))
fp = {"acc": acc, "pairs": pairs, "kept": kept, "too_short": too_short, "too_many_n": too_many_n,
      "with_adapter": with_ad, "adapter_bases": adapt, "main_records_sha256": sha_main.hexdigest(),
      "hist_sha256": [hashlib.sha256(json.dumps(sorted(H[m].items())).encode()).hexdigest() for m in (0, 1)]}

if FULL:
    # 6a. -j 1 against -j T: the same records, the same counts
    h1 = hashlib.sha256()
    n1 = 0
    for a, b in dnaio.open("%s/j1_R1.fq.gz" % W, file2="%s/j1_R2.fq.gz" % W):
        h1.update(a.fastq_bytes() + b.fastq_bytes())
        n1 += 1
    J1 = rep("j1")
    check(h1.hexdigest() == sha_main.hexdigest() and n1 == kept and J1["read_counts"] == J["read_counts"]
          and J1["basepair_counts"] == J["basepair_counts"], "6. -j 1 == -j %s: records and report counts" % J["cores"],
          "%d pairs, sha256 %s" % (n1, h1.hexdigest()[:16]))

    # 6b. -q 15,20 against cutadapt's documented algorithm, written from the docs, not imported
    def bwa(q, front, back):
        n = len(q)
        start, stop = 0, n
        s = best = 0
        for i in range(n):
            s += front - q[i]
            if s < 0:
                break
            if s > best:
                best, start = s, i + 1
        s = best = 0
        for i in range(n - 1, -1, -1):
            s += back - q[i]
            if s < 0:
                break
            if s > best:
                best, stop = s, i
        return (0, 0) if start >= stop else (start, stop)
    qsha = []
    for m in (1, 2):
        bad = changed = empty = trimmed = n = 0
        hq = hashlib.sha256()
        for a, b in zip(dnaio.open("%s_%d.fastq.gz" % (acc, m)), dnaio.open("%s/qual_R%d.fq" % (W, m))):
            n += 1
            lo, hi = bwa([ord(c) - 33 for c in a.qualities], 15, 20)
            trimmed += len(a.sequence) - (hi - lo)
            changed += (hi - lo) != len(a.sequence)
            empty += hi == lo
            if (a.name, a.sequence[lo:hi], a.qualities[lo:hi]) != (b.name, b.sequence, b.qualities):
                bad += 1
            hq.update(b.fastq_bytes())
        QJ = rep("qual_R%d" % m)
        check(n == pairs and bad == 0 and QJ["basepair_counts"]["quality_trimmed"] == trimmed and changed > 0,
              "6. -q 15,20 R%d == the BWA rule, read for read" % m,
              "%d reads, %d trimmed (%d to empty), %d bp; %d differ" % (n, changed, empty, trimmed, bad))
        qsha.append(hq.hexdigest())
    fp["qual_records_sha256"] = qsha
    NJ = rep("nextera")
    nh = [hist(NJ, s) for s in ("adapters_read1", "adapters_read2")]
    say("obs  wrong kit (Nextera adapter): with adapter R1 %d R2 %d, of which removed >= 10 bp: %d / %d; "
        "TruSeq removed >= 10 bp: %d / %d" % (NJ["read_counts"]["read1_with_adapter"], NJ["read_counts"]["read2_with_adapter"],
                                             sum(v for k, v in nh[0].items() if k >= 10), sum(v for k, v in nh[1].items() if k >= 10),
                                             sum(v for k, v in H[0].items() if k >= 10), sum(v for k, v in H[1].items() if k >= 10)))
    fp["nextera_with_adapter"] = [NJ["read_counts"]["read1_with_adapter"], NJ["read_counts"]["read2_with_adapter"]]

json.dump(fp, open("%s/fingerprints.json" % S, "w"), indent=1, sort_keys=True)
say(json.dumps(fp, sort_keys=True))
say("CHECK OK %s" % acc if ok else "CHECK FAILED %s" % acc)
sys.exit(0 if ok else 1)
