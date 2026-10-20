"""compare.py <out> <chip>=<g.vcf.gz> ...: the merged GVCFs of the three chips, against Graviton4's.

Per pair, prints `key<TAB>value` lines: records each side, records byte-identical (outside the
header), variant sites (POS REF ALT, <NON_REF> dropped) each side and shared, and over the shared
sites how many genotypes (GT) and genotype qualities (GQ) agree."""
import gzip
import hashlib
import sys


def load(path):
    lines, sites = set(), {}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            lines.add(hashlib.md5(line.encode()).digest()[:8])
            p = line.rstrip("\n").split("\t")
            alts = [a for a in p[4].split(",") if a != "<NON_REF>"]
            if alts:
                fmt, val = p[8].split(":"), p[9].split(":")
                f = dict(zip(fmt, val))
                sites[(int(p[1]), p[3], ",".join(alts))] = (f.get("GT", "."), f.get("GQ", "."))
    return lines, sites


out = open(sys.argv[1], "w")
data = {}
for a in sys.argv[2:]:
    chip, path = a.split("=", 1)
    data[chip] = load(path)


def say(k, v):
    out.write("%s\t%s\n" % (k, str(v)))
    print("%s\t%s" % (k, str(v)))


chips = list(data)
for c in chips:
    say("%s_records" % c, len(data[c][0]))
    say("%s_variant_sites" % c, len(data[c][1]))
for a, b in [(chips[1], chips[0]), (chips[2], chips[0]), (chips[2], chips[1])] if len(chips) == 3 else [(chips[1], chips[0])]:
    p = "%s_vs_%s" % (a, b)
    la, lb = data[a][0], data[b][0]
    sa, sb = data[a][1], data[b][1]
    shared = set(sa) & set(sb)
    say(p + "_identical_records", len(la & lb))
    say(p + "_records_only_" + a, len(la - lb))
    say(p + "_records_only_" + b, len(lb - la))
    say(p + "_shared_sites", len(shared))
    say(p + "_sites_only_" + a, len(set(sa) - set(sb)))
    say(p + "_sites_only_" + b, len(set(sb) - set(sa)))
    say(p + "_gt_agree", sum(sa[k][0] == sb[k][0] for k in shared))
    say(p + "_gq_agree", sum(sa[k][1] == sb[k][1] for k in shared))
out.close()
