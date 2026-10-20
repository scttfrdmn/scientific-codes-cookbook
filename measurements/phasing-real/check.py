#!/usr/bin/env python3
"""phasing-real's structural checks, read straight from the VCF text (same code on both pysam builds).

    check.py flip  <phased.vcf.gz> <out.vcf>       every phased genotype a|b -> b|a, nothing else
    check.py check <outdir> <input.vcf>            append results.tsv / checks.tsv lines in <outdir>

`check` expects, in /tmp: bench.chr20.vcf.gz, hifiasm.chr20.vcf.gz, sst.chr20.vcf.gz, and in <outdir>:
hifi.vcf.gz, ilmn.vcf.gz, compare.tsv, compare.flip.tsv, blocks.{hifi,ilmn}.tsv, stats.{hifi,ilmn}.tsv.
"""
import collections
import gzip
import hashlib
import os
import re
import sys

SPLIT = re.compile(r"[/|]")


def records(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            keys = f[8].split(":")
            vals = f[9].split(":")
            s = dict(zip(keys, vals))
            yield (f[0], f[1], f[3], f[4]), s


def flip(src, dst):
    n = 0
    op = gzip.open if src.endswith(".gz") else open
    with op(src, "rt") as fh, open(dst, "w") as out:
        for line in fh:
            if not line.startswith("#"):
                f = line.rstrip("\n").split("\t")
                v = f[9].split(":")
                if "|" in v[0]:
                    a = v[0].split("|")
                    if a[0] != a[1]:
                        v[0] = "|".join(reversed(a))
                        n += 1
                    f[9] = ":".join(v)
                line = "\t".join(f) + "\n"
            out.write(line)
    print("flipped\t%d" % n)


def alleles(gt):
    return tuple(sorted(SPLIT.split(gt)))


def is_het(gt):
    a = SPLIT.split(gt)
    return len(a) == 2 and "." not in a and a[0] != a[1]


def tsv(path):
    with open(path) as fh:
        head = fh.readline().lstrip("#").rstrip("\n").split("\t")
        return [dict(zip(head, l.rstrip("\n").split("\t"))) for l in fh if l.strip()]


def main_check(o, inputvcf):
    res = open(os.path.join(o, "results.tsv"), "a")
    chks = open(os.path.join(o, "checks.tsv"), "a")

    def rec(k, v):
        res.write("%s\t%s\n" % (k, v))
        print("%s = %s" % (k, v))

    def chk(claim, lhs, rhs, ok=None):
        # ok is the claim's verdict when it is not plain equality; bool() so a numpy-ish value cannot slip by
        good = bool(ok) if ok is not None else (str(lhs) == str(rhs) and str(lhs) != "")
        s = "ok" if good else "FAIL"
        chks.write("%s\t%s\t%s\t%s\n" % (claim, lhs, rhs, s))
        print("CHECK [%s] %s: %s vs %s" % (s, claim, lhs, rhs))

    bench = list(records("/tmp/bench.chr20.vcf.gz"))
    keys = [k for k, _ in bench]
    gts = [alleles(s["GT"]) for _, s in bench]
    rec("input_records", len(bench))
    rec("input_het", sum(is_het(s["GT"]) for _, s in bench))
    inp = list(records(inputvcf))
    chk("unphased input: records == benchmark's, in order", sum(a != b for a, (b, _) in zip(keys, inp)) + abs(len(inp) - len(keys)), 0)
    chk("unphased input: no genotype phased, no PS", sum("|" in s["GT"] or s.get("PS", ".") != "." for _, s in inp), 0)

    # The two GIAB phasings were transferred onto the benchmark's own calls, so they must be the
    # same records with the same genotypes, differing only in phase.
    for t in ("hifiasm", "sst"):
        tr = list(records("/tmp/%s.chr20.vcf.gz" % t))
        chk("%s truth: records == benchmark's, in order" % t, sum(a != b for a, (b, _) in zip(keys, tr)) + abs(len(tr) - len(keys)), 0)
        chk("%s truth: genotypes == benchmark's, ignoring phase" % t,
            sum(a != alleles(s["GT"]) for a, (_, s) in zip(gts, tr)), 0)
        rec("%s_phased_het" % t, sum(is_het(s["GT"]) and "|" in s["GT"] for _, s in tr))
        if t == "hifiasm":
            truth = {k: s["GT"] for k, s in tr}

    med = {}
    for name in ("hifi", "ilmn"):
        out = list(records(os.path.join(o, name + ".vcf.gz")))
        chk("%s: records == input's, in order" % name, sum(a != b for a, (b, _) in zip(keys, out)) + abs(len(out) - len(keys)), 0)
        chk("%s: genotypes unchanged, ignoring phase" % name, sum(a != alleles(s["GT"]) for a, (_, s) in zip(gts, out)), 0)
        chk("%s: no homozygous or missing call phased or given a PS" % name,
            sum((not is_het(s["GT"])) and ("|" in s["GT"] or s.get("PS", ".") != ".") for _, s in out), 0)
        phased = [(k, s) for k, s in out if is_het(s["GT"]) and "|" in s["GT"]]
        out_gt = {k: s["GT"] for k, s in phased}
        chk("%s: every phased het carries an integer PS" % name,
            sum(not s.get("PS", "").isdigit() for _, s in phased), 0)
        first, last = {}, {}
        for k, s in phased:
            ps, pos = s.get("PS"), int(k[1])
            first[ps] = min(first.get(ps, pos), pos)
            last[ps] = max(last.get(ps, pos), pos)
        # whatshap names a block after its connected component's first variant, and that variant can
        # itself end up unphased -- so the PS is an input het's position at or before the block, not
        # always one of the block's own variants
        het_pos = {k[1] for k, s in bench if is_het(s["GT"])}
        chk("%s: PS <= position of its block's first phased variant" % name, sum(int(p) > v for p, v in first.items() if p.isdigit()), 0)
        chk("%s: PS is the position of an input het call" % name, sum(p not in het_pos for p in first), 0)
        rec("%s_ps_equals_first_phased" % name, sum(int(p) == v for p, v in first.items() if p.isdigit()))
        rec("%s_phased_het" % name, len(phased))
        rec("%s_phase_sets" % name, len(first))
        blocks = tsv(os.path.join(o, "blocks.%s.tsv" % name))
        chk("%s: whatshap stats block list == PS sets in the VCF" % name, len(blocks), len(first))
        st = tsv(os.path.join(o, "stats.%s.tsv" % name))[0]
        chk("%s: phased hets == whatshap stats phased + singletons" % name, len(phased), int(st["phased"]) + int(st["singletons"]))
        spans = [last[p] - first[p] for p in first]
        rec("%s_ps_span_over_1mb" % name, sum(x > 1000000 for x in spans))
        rec("%s_ps_span_max_bp" % name, max(spans) if spans else 0)
        # A phase set spanning >1 Mb is pieces joined by one long link (a read pair, or a split read,
        # mapped far apart). Is the join right? Orient each piece (gap >100 kb) against hifiasm by
        # majority, and count joins whose two sides agree. Observation, not assertion.
        bykey = collections.defaultdict(list)
        for k, s in phased:
            bykey[s["PS"]].append(k)
        joins = right = 0
        for p, ks in bykey.items():
            if last[p] - first[p] <= 1000000:
                continue
            ks.sort(key=lambda k: int(k[1]))
            pieces, prev = [[]], None
            for k in ks:
                if prev is not None and int(k[1]) - prev > 100000:
                    pieces.append([])
                pieces[-1].append(k)
                prev = int(k[1])
            orient = []
            for pc in pieces:
                c = collections.Counter(out_gt[k] == truth[k] for k in pc if "|" in truth[k])
                orient.append(c.most_common(1)[0][0] if c else None)
            for a, b in zip(orient, orient[1:]):
                if a is not None and b is not None:
                    joins += 1
                    right += a == b
        rec("%s_long_joins" % name, joins)
        rec("%s_long_joins_consistent_with_hifiasm" % name, right)
        for k in ("blocks", "singletons", "variant_per_block_median", "variant_per_block_max", "bp_per_block_median",
                  "bp_per_block_max", "bp_per_block_sum", "block_n50", "phased_fraction"):
            rec("%s_stats_%s" % (name, k), st.get(k))
        med[name] = float(st["bp_per_block_median"])
        h = hashlib.md5()
        with gzip.open(os.path.join(o, name + ".vcf.gz"), "rb") as fh:
            for line in fh:
                if not line.startswith(b"##"):
                    h.update(line)
        rec("%s_body_md5" % name, h.hexdigest())

    chk("read length sets reach: HiFi median block (bp, whatshap stats) > Illumina's", med["hifi"], med["ilmn"], med["hifi"] > med["ilmn"])

    rows = tsv(os.path.join(o, "compare.tsv"))
    flip_rows = tsv(os.path.join(o, "compare.flip.tsv"))
    drop = ("file_name0", "file_name1")
    strip = lambda rs: [tuple(v for k, v in sorted(r.items()) if k not in drop) for r in rs]
    chk("every pairwise score is invariant under flipping every phased genotype", strip(rows) == strip(flip_rows), True,
        strip(rows) == strip(flip_rows) and len(rows) == 6)
    for r in rows:
        pair = "%s_vs_%s" % (r["dataset_name0"], r["dataset_name1"])
        for k in ("covered_variants", "all_assessed_pairs", "all_switches", "all_switch_rate", "all_switchflips",
                  "blockwise_hamming", "blockwise_hamming_rate", "blockwise_diff_genotypes", "largestblock_assessed_pairs",
                  "largestblock_switches", "largestblock_hamming"):
            rec("%s_%s" % (pair, k), r[k])
        if r["dataset_name0"] in ("hifiasm", "trio_strandseq") and r["dataset_name1"] in ("hifi", "illumina"):
            chk("%s: different genotypes" % pair, r["blockwise_diff_genotypes"], "0")
            rate = float(r["all_switch_rate"])
            # a phasing that carries no information scores 0.5; 0.05 is ten times below that
            chk("%s: switch error rate <= 0.05" % pair, r["all_switch_rate"], "0.05", rate == rate and rate <= 0.05)
            chk("%s: pairs assessed > 0" % pair, r["all_assessed_pairs"], ">0", int(r["all_assessed_pairs"]) > 0)


if __name__ == "__main__":
    if sys.argv[1] == "flip":
        flip(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "check":
        main_check(sys.argv[2], sys.argv[3])
    else:
        sys.exit("usage: check.py flip|check ...")
