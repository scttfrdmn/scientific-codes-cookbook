"""Where do two runs' VCFs differ? Record by record, field by field.

    python3 -u -I vcfdiff.py <a.vcf.gz> <b.vcf.gz> <label>

Prints, as label<TAB>key<TAB>value lines: records compared, records identical, records whose
INFO differs, records whose genotype fields differ, genotypes whose GT differs, genotypes whose
DS differs, max |dDS|, and per INFO key the records it differs on and the max |d|.
"""
import gzip
import hashlib
import itertools
import sys

A, B, LABEL = sys.argv[1:4]


MD5 = {}


def records(path):
    """Data lines; also hashes the whole text and the non-## text (header line + records)."""
    full, body = hashlib.md5(), hashlib.md5()
    with gzip.open(path, "rt") as f:
        for line in f:
            full.update(line.encode())
            if not line.startswith("##"):
                body.update(line.encode())
            if not line.startswith("#"):
                yield line
    MD5[path] = (full.hexdigest(), body.hexdigest())


def info(s):
    out = {}
    for kv in s.split(";"):
        k, _, v = kv.partition("=")
        out[k] = v
    return out


n = same = info_diff = geno_diff = gt_diff = ds_diff = 0
ds_max = 0.0
keys = {}
unpaired = 0
for la, lb in itertools.zip_longest(records(A), records(B)):
    if la is None or lb is None:
        unpaired += 1
        continue
    n += 1
    if la == lb:
        same += 1
        continue
    fa = la.rstrip("\n").split("\t")
    fb = lb.rstrip("\n").split("\t")
    if fa[:5] != fb[:5]:
        print("%s\tKEY_MISMATCH_AT\t%d" % (LABEL, n))
        sys.exit(1)
    if fa[7] != fb[7]:
        info_diff += 1
        ia, ib = info(fa[7]), info(fb[7])
        for k in sorted(set(ia) | set(ib)):
            if ia.get(k) != ib.get(k):
                d = keys.setdefault(k, [0, 0.0])
                d[0] += 1
                try:
                    d[1] = max(d[1], abs(float(ia.get(k)) - float(ib.get(k))))
                except (TypeError, ValueError):
                    pass
    if fa[8:] != fb[8:]:
        geno_diff += 1
        fmt = fa[8].split(":")
        gi, di = fmt.index("GT"), (fmt.index("DS") if "DS" in fmt else None)
        for xa, xb in zip(fa[9:], fb[9:]):
            if xa == xb:
                continue
            pa, pb = xa.split(":"), xb.split(":")
            if pa[gi] != pb[gi]:
                gt_diff += 1
            if di is not None and pa[di] != pb[di]:
                ds_diff += 1
                ds_max = max(ds_max, abs(float(pa[di]) - float(pb[di])))
print("%s\trecords\t%d" % (LABEL, n))
print("%s\trecords_unpaired\t%d" % (LABEL, unpaired))
for tag, path in (("a", A), ("b", B)):
    print("%s\ttext_md5_%s\t%s" % (LABEL, tag, MD5[path][0]))
    print("%s\tbody_md5_%s\t%s" % (LABEL, tag, MD5[path][1]))
print("%s\trecords_identical\t%d" % (LABEL, same))
print("%s\trecords_info_differs\t%d" % (LABEL, info_diff))
print("%s\trecords_genotype_fields_differ\t%d" % (LABEL, geno_diff))
print("%s\tgenotypes_gt_differs\t%d" % (LABEL, gt_diff))
print("%s\tgenotypes_ds_differs\t%d" % (LABEL, ds_diff))
print("%s\tmax_abs_ds_diff\t%.6f" % (LABEL, ds_max))
for k in sorted(keys):
    print("%s\tinfo_%s_records_differ\t%d" % (LABEL, k, keys[k][0]))
    print("%s\tinfo_%s_max_abs_diff\t%.6g" % (LABEL, k, keys[k][1]))
