#!/usr/bin/env python3
"""yoda-real, part 2: read every reference file Rivet 4.1.4 ships, and check what YODA read.

    python3 -u refs.py <workers> <rivet-analyses.tar.gz> <refs.manifest> <outdir>

Rivet's reference data is the experiments' published measurements (HEPData-derived), and reading
it is what every Rivet comparison starts with. Asserted over all 1,955 .yoda.gz files:
  * the extracted files are byte-for-byte the commit's (each against refs.manifest's sha256);
  * yoda.read parses every file, and every object's every row agrees with an independent parser
    of the YODA text written here (no yoda): the central value, every error source's down and up
    error (or its absence, `---`), and each continuous axis's edges -- bit for bit, since both
    sides parse the same decimal strings;
  * write -> read: writeYODA of what was read, read back, gives every value bit-identical where
    the source carried at most 7 significant digits (what %.6e prints); where a source carried
    more, the change is counted and bounded by %.6e's half unit in the 7th digit.
"""
import glob
import gzip
import hashlib
import json
import math
import os
import re
import resource
import sys
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor

import yoda

W, TAR, MAN, OUT = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
ROWS = []


def rec(k, v):
    v = str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((str(k), v))


def write_score():
    with open(os.path.join(OUT, "refs.tsv"), "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    write_score()
    sys.exit(1)


def parse(text):
    """An independent reader of YODA estimate blocks: {path: (kind, labels, edges, rows)}."""
    out = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.startswith("BEGIN "):
            i += 1
            continue
        kind, path = ln.split()[1], ln.split()[2]
        labels, edges, rows = None, {}, []
        i += 1
        while not lines[i].startswith("---"):
            i += 1
        i += 1
        while not lines[i].startswith("END "):
            s = lines[i]
            if s.startswith("Edges(A"):
                ax = int(s[7:s.index(")")])
                edges[ax] = s[s.index("[") + 1:s.rindex("]")]
            elif s.startswith("ErrorLabels:"):
                labels = json.loads(s[s.index("["):])      # a JSON list: "\\alpha" is \alpha
            elif s.startswith("#") or s.startswith("MaskedBins:") or not s.strip():
                pass
            else:
                rows.append(s.split())
            i += 1
        out[path] = (kind, labels, edges, rows)
        i += 1
    return out


def num(tok):
    return float(tok)          # 'nan' parses to nan; strtod-equivalent correct rounding


def same(a, b):
    return (math.isnan(a) and math.isnan(b)) or a == b


def sigdigits(tok):
    m = re.match(r"^[-+]?(\d*)\.?(\d*)[eE]?", tok)
    if not m or tok.lower() in ("nan", "inf", "-inf"):
        return 0
    d = (m.group(1) + m.group(2)).lstrip("0").rstrip("0")
    return len(d)


def cells(o, kind, nrows):
    """The yoda object's rows: a list of bin-like objects in global-index order."""
    if "ESTIMATE0D" in kind:
        return [o]
    return [o.bin(j) for j in range(nrows)]


EX = []
NEX = {}


def example(kind, *a):
    if NEX.get(kind, 0) < 3:
        NEX[kind] = NEX.get(kind, 0) + 1
        EX.append((kind,) + tuple(str(x)[:160] for x in a))


def keyed(row, labels):
    """A data row as {"value": tok, (label, "dn"): tok, (label, "up"): tok}."""
    d = {"value": row[0]}
    for j, lab in enumerate(labels or []):
        d[(lab, "dn")], d[(lab, "up")] = row[1 + 2 * j], row[2 + 2 * j]
    return d


def compare(objs, parsed, st, pre):
    for path, (kind, labels, edges, rows) in parsed.items():
        if "ESTIMATE" not in kind:
            st[pre + "non_estimate_blocks"] += 1
            continue
        o = objs.get(path)
        if o is None:
            st[pre + "missing_object"] += 1
            continue
        st[pre + "objects"] += 1
        try:
            cs = cells(o, kind, len(rows))
        except Exception:
            st[pre + "bin_index_error"] += 1
            continue
        for b, row in zip(cs, rows):
            st[pre + "rows"] += 1
            if not same(b.val(), num(row[0])):
                st[pre + "value_mismatch"] += 1
            st[pre + "values"] += 1
            for j, lab in enumerate(labels or []):
                dn, up = row[1 + 2 * j], row[2 + 2 * j]
                if dn == "---":
                    if b.hasSource(lab):
                        st[pre + "absent_error_present"] += 1
                    st[pre + "absent_errors"] += 1
                    continue
                if not b.hasSource(lab):
                    st[pre + "error_source_missing"] += 1
                    example(pre + "error_source_missing", path, lab, b.sources(), dn, up)
                    continue
                if not (same(b.errDown(lab), num(dn)) and same(b.errUp(lab), num(up))):
                    st[pre + "error_mismatch"] += 1
                st[pre + "errors"] += 2
        for ax, txt in edges.items():
            toks = [t.strip() for t in txt.split(",")]
            try:
                want = [float(t) for t in toks]
            except ValueError:
                st[pre + "discrete_axes"] += 1
                continue
            got = list({1: o.xEdges, 2: getattr(o, "yEdges", None), 3: getattr(o, "zEdges", None)}[ax]())
            if len(got) != len(want) or not all(float(g) == w for g, w in zip(got, want)):
                st[pre + "edge_mismatch"] += 1
            st[pre + "edges"] += len(want)


def work(fn):
    from collections import Counter
    st = Counter()
    t0 = time.time()
    txt = gzip.open(fn, "rt", encoding="utf-8").read()
    try:
        objs = yoda.read(fn)
    except Exception as e:
        st["read_error"] += 1
        return st, [("read_error", fn, str(e)[:200])], 0.0, 0.0
    t1 = time.time()
    parsed = parse(txt)
    st["blocks"] += len(parsed)
    for kind, *_ in parsed.values():
        st["kind:" + kind] += 1
    if len(parsed) != len(objs):
        st["block_count_mismatch"] += 1
    compare(objs, parsed, st, "")
    # write what was read, read it back, compare against the SOURCE text with the same parser
    rt = "/tmp/rt/%s.yoda" % hashlib.sha1(fn.encode()).hexdigest()
    yoda.writeYODA(list(objs.values()), rt)
    back = yoda.read(rt)
    parsed_rt = parse(open(rt, encoding="utf-8").read())
    for path, (kind, labels, edges, rows) in parsed.items():
        if path not in parsed_rt:
            st["rt_missing_block"] += 1
            continue
        labels2, rows2 = parsed_rt[path][1], parsed_rt[path][3]
        if len(rows2) != len(rows):
            st["rt_row_count_mismatch"] += 1
            continue
        if list(labels or []) != list(labels2 or []):
            st["rt_sources_reordered"] += 1      # compared by label below, not by column
        for r1, r2 in zip(rows, rows2):
            d1, d2 = keyed(r1, labels), keyed(r2, labels2)
            for key in set(d1) | set(d2):
                t1_, t2_ = d1.get(key, "---"), d2.get(key, "---")
                if t1_ == "---" or t2_ == "---":
                    if t1_ != t2_:
                        st["rt_absent_mismatch"] += 1
                        example("rt_absent_mismatch", path, key, t1_, t2_)
                    continue
                a, b = num(t1_), num(t2_)
                st["rt_values"] += 1
                if same(a, b):
                    continue
                if sigdigits(t1_) <= 7:
                    st["rt_changed_le7"] += 1
                    example("rt_changed_le7", path, key, t1_, t2_)
                else:
                    st["rt_changed_gt7"] += 1
                    rel = abs(a - b) / abs(a)
                    st["rt_worst_rel_e12"] = max(st["rt_worst_rel_e12"], int(rel * 1e12))
    compare(back, parsed, st, "rtobj_")   # the read-back objects against the source text
    for k, o in objs.items():
        if back[k].annotationsDict() != o.annotationsDict():
            st["rt_annotation_mismatch"] += 1
    os.remove(rt)
    return st, list(EX), t1 - t0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 ** 2


def main():
    from collections import Counter
    os.makedirs(OUT, exist_ok=True)
    os.makedirs("/tmp/rt", exist_ok=True)
    rec("yoda", yoda.version())
    t0 = time.time()
    with tarfile.open(TAR) as tf:
        tf.extractall("/tmp/riv", filter="data")
    top = glob.glob("/tmp/riv/*/analyses")[0]
    pins = {}
    for ln in open(MAN):
        h, n = ln.split()
        pins[n] = h
    files = sorted(glob.glob(top + "/**/*.yoda.gz", recursive=True))
    rec("reference_files", len(files))
    if len(files) != len(pins):
        die("%d reference files extracted, the manifest pins %d" % (len(files), len(pins)))
    for fn in files:
        n = "analyses/" + os.path.relpath(fn, top)
        if hashlib.sha256(open(fn, "rb").read()).hexdigest() != pins.get(n):
            die("%s does not match refs.manifest" % n)
    rec("identity_manifest", "all %d files == refs.manifest (commit 74816bd4cf29)" % len(files))
    t1 = time.time()
    with ProcessPoolExecutor(W) as pool:
        out = list(pool.map(work, files, chunksize=8))
    rec("wall_s_refs", "%.1f" % (time.time() - t1))
    rec("cpu_s_sum_yoda_read", "%.1f" % sum(r[2] for r in out))
    rec("peak_rss_gib_worker", "%.2f" % max(r[3] for r in out))
    st = Counter()
    worst = 0
    errs = []
    for s, e, _, _ in out:
        worst = max(worst, s.pop("rt_worst_rel_e12", 0))
        st.update(s)
        errs += e
    for k in sorted(st):
        rec(k, st[k])
    rec("rt_worst_rel_gt7", "%.3e" % (worst / 1e12))
    seen = {}
    for e in errs:
        if seen.get(e[0], 0) < 3:
            seen[e[0]] = seen.get(e[0], 0) + 1
            rec("example", e)
    fail = [k for k in ("read_error", "block_count_mismatch", "missing_object", "bin_index_error",
                        "value_mismatch", "error_mismatch", "error_source_missing",
                        "absent_error_present", "edge_mismatch", "non_estimate_blocks",
                        "rt_missing_block", "rt_row_count_mismatch", "rt_absent_mismatch",
                        "rt_changed_le7", "rt_annotation_mismatch")
            if st.get(k, 0) or st.get("rtobj_" + k, 0)]
    if fail:
        die("reference read: %s" % ", ".join("%s=%d/%d" % (k, st.get(k, 0), st.get("rtobj_" + k, 0)) for k in fail))
    if worst / 1e12 > 5e-7 * (1 + 1e-6):
        die("a >7-digit reference value moved %.3e in the round trip" % (worst / 1e12))
    rec("identity_reference_read", "%d objects, %d rows, %d values and %d errors: yoda == an independent "
        "parser, bit for bit" % (st["objects"], st["rows"], st["values"], st["errors"]))
    rec("wall_s_refs_py", "%.1f" % (time.time() - t0))
    write_score()
    print("REFS OK")


if __name__ == "__main__":
    main()
