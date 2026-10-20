"""gvcf.py <g.vcf.gz> <interval_list...>: structural facts about one GVCF against the intervals it
was called on. Prints `key<TAB>value` lines:
    records, variant_records (an ALT besides <NON_REF>), snv_records, samples, sample,
    contigs, unsorted (records out of position order), interval_bp, covered_bp_in (interval
    bases some record spans), covered_bp_out (spanned bases outside every interval)
A record spans [POS, END] for a reference block and [POS, POS+len(REF)-1] otherwise."""
import gzip
import sys


def merge(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def total(sp):
    return sum(b - a + 1 for a, b in sp)


def intersect(x, y):
    i = j = n = 0
    while i < len(x) and j < len(y):
        a, b = max(x[i][0], y[j][0]), min(x[i][1], y[j][1])
        if a <= b:
            n += b - a + 1
        if x[i][1] < y[j][1]:
            i += 1
        else:
            j += 1
    return n


iv = []
for f in sys.argv[2:]:
    for line in open(f):
        if not line.startswith("@"):
            p = line.split("\t")
            iv.append((int(p[1]), int(p[2])))
iv = merge(iv)

recs = var = snv = unsorted = 0
samples, sample, contigs, spans, last = 0, "-", set(), [], 0
with gzip.open(sys.argv[1], "rt") as fh:
    for line in fh:
        if line.startswith("##"):
            continue
        if line.startswith("#CHROM"):
            p = line.rstrip("\n").split("\t")
            samples, sample = len(p) - 9, (p[9] if len(p) > 9 else "-")
            continue
        p = line.split("\t", 8)
        recs += 1
        contigs.add(p[0])
        pos = int(p[1])
        if pos < last:
            unsorted += 1
        last = pos
        end = pos + len(p[3]) - 1
        info = p[7]
        k = info.find("END=")
        if info.startswith("END=") or (k > 0 and info[k - 1] == ";"):
            end = max(end, int(info[k + 4:].split(";", 1)[0]))
        spans.append((pos, end))
        alts = [a for a in p[4].split(",") if a != "<NON_REF>"]
        if alts:
            var += 1
            if len(p[3]) == 1 and all(len(a) == 1 and a != "*" for a in alts):
                snv += 1
cov = merge(spans)
inside = intersect(cov, iv)
for k, v in [("records", recs), ("variant_records", var), ("snv_records", snv), ("samples", samples),
             ("sample", sample), ("contigs", ",".join(sorted(contigs))), ("unsorted", unsorted),
             ("interval_bp", total(iv)), ("covered_bp_in", inside), ("covered_bp_out", total(cov) - inside)]:
    print("%s\t%s" % (k, str(v)))
