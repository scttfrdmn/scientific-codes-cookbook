#!/usr/bin/env python3
"""Check four StringTie assemblies of one sample (de novo / guided, at <T> threads and at 1).

Asserted, for every assembly: each transcript's span is its exons' span, exons are sorted and
disjoint, nothing runs past its chromosome, every multi-exon transcript has a strand, and TPM and
FPKM are each ONE constant times cov for every transcript, within print rounding. The FPKM
constant depends on the library alone, so it must be the same for the de novo and the guided
assembly of the same BAM. For de novo assemblies: every intron is one that some read's CIGAR
spans (StringTie cannot invent a junction no spliced read supports). Across thread counts: the
same transcripts with the same cov/FPKM/TPM -- compared as a canonical, id-free, sorted form,
because -p changes the order bundles finish in and therefore the STRG numbering and file order.

TPMs do NOT sum to 1e6 here, and that is not asserted: StringTie normalises by coverage that
includes transcripts it later drops, so the sum is reported (with the implied normaliser).

Independently of gffcompare, it also counts what gffcompare will be asked: distinct assembled
introns and how many are annotated, and multi-exon transcripts whose whole intron chain equals an
annotated transcript's. Those land in check.json for the gffcompare task to compare against.
    check_st.py <T> <junctions.tsv> <chrom.sizes> <genes.gtf> <outdir>
"""
import hashlib
import json
import os
import re
import sys

T, JUNC, SIZES, REF, O = sys.argv[1:6]
ATTR = re.compile(r'(\S+) "([^"]*)"')
ULP = 2.0 ** -24          # StringTie keeps coverage in single precision
GFF_MIN_INTRON = 4        # gclib (gff.h): exons <= 4 bp apart are one exon to gffcompare
PR = 5e-7                 # and prints cov / FPKM / TPM to 6 decimals
fails, out, res = [], [], {}


def line(s):
    out.append(s)
    print(s)


def check(ok, what):
    if not bool(ok):
        fails.append(what)
        line("FAIL: " + what)


def read_gtf(path, keep_attrs=False, merge_close=False):
    """transcript_id -> dict(chrom, strand, exons[(s, e, cov)], tline(start,end), attrs).
    merge_close applies gclib's rule, as gffcompare reads the reference: a gap of <= 4 bp is not
    an intron (Ensembl annotates the OAZ1/OAZ2 +1 ribosomal frameshift as a 1 bp gap)."""
    tx = {}
    with open(path) as fh:
        for l in fh:
            if l.startswith("#"):
                continue
            f = l.rstrip("\n").split("\t")
            if len(f) < 9 or f[2] not in ("exon", "transcript"):
                continue
            a = dict(ATTR.findall(f[8]))
            t = tx.setdefault(a["transcript_id"], {"chrom": f[0], "strand": f[6], "exons": [], "tline": None, "attrs": {}})
            if f[2] == "exon":
                t["exons"].append((int(f[3]), int(f[4]), a.get("cov", "") if keep_attrs else ""))
            else:
                t["tline"] = (int(f[3]), int(f[4]))
                if keep_attrs:
                    t["attrs"] = a
    merged = 0
    for t in tx.values():
        t["exons"].sort()
        if merge_close and t["exons"]:
            m = [t["exons"][0]]
            for e in t["exons"][1:]:
                if e[0] - m[-1][1] - 1 <= GFF_MIN_INTRON:
                    m[-1] = (m[-1][0], max(m[-1][1], e[1]), m[-1][2])
                    merged += 1
                else:
                    m.append(e)
            t["exons"] = m
    if merge_close:
        line("%s: %d exon gaps of <= %d bp merged" % (os.path.basename(path), merged, GFF_MIN_INTRON))
    return tx


def introns(t):
    e = t["exons"]
    return [(e[i][1] + 1, e[i + 1][0] - 1) for i in range(len(e) - 1)]


def canonical_sha(tx):
    """sha256 of the assembly with StringTie's own ids and record order taken out"""
    a = lambda x, k: x["attrs"].get(k, "")
    rows = sorted("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
        x["chrom"], x["strand"], ",".join("%d-%d:%s" % e for e in x["exons"]),
        a(x, "cov"), a(x, "FPKM"), a(x, "TPM"), a(x, "reference_id"), a(x, "ref_gene_id")) for x in tx.values())
    return hashlib.sha256("\n".join(rows).encode()).hexdigest(), len(rows)


def scale(tx, key):
    """the constant c in key == c * cov, from the best-resolved transcript, and the worst
    per-transcript excess over the print-rounding + float32 bound (<= 0 means it holds)"""
    v = [(float(x["attrs"]["cov"]), float(x["attrs"][key])) for x in tx.values()]
    cm, ym = max(v)
    c = ym / cm
    ec = c * (PR / cm + PR / ym + 8 * ULP)            # uncertainty in c itself
    worst = max(abs(y - cv * c) - (PR + PR * c + cv * ec + 8 * ULP * y) for cv, y in v)
    return c, worst


sizes = dict((l.split("\t")[0], int(l.split("\t")[1])) for l in open(SIZES))
junctions = set()
for l in open(JUNC):
    c, s, e = l.rstrip("\n").split("\t")
    junctions.add((c, int(s), int(e)))
line("bam_distinct_junctions\t%d" % len(junctions))

ref = read_gtf(REF, merge_close=True)
ref_introns, ref_chains = set(), set()      # stranded, as gffcompare compares them
for t in ref.values():
    i = introns(t)
    for s, e in i:
        ref_introns.add((t["chrom"], t["strand"], s, e))
    if i:
        ref_chains.add((t["chrom"], t["strand"], tuple(i)))
line("reference_transcripts\t%d\treference_distinct_introns\t%d\treference_distinct_chains\t%d"
     % (len(ref), len(ref_introns), len(ref_chains)))
res["reference"] = {"distinct_introns": len(ref_introns), "transcripts": len(ref)}

for mode in ("denovo", "guided"):
    for t in (T, "1"):
        name = "%s_p%s" % (mode, t)
        tx = read_gtf(os.path.join(O, name + ".gtf"), keep_attrs=True)
        close = sum(1 for x in tx.values() for i in range(len(x["exons"]) - 1)
                    if x["exons"][i + 1][0] - x["exons"][i][1] - 1 <= GFF_MIN_INTRON)
        bad_span = bad_order = past_end = no_strand = unsupported = 0
        tpm = 0.0
        q_introns, chain_match, multi, with_ref = set(), 0, 0, 0
        match_ids = []
        genes = set()
        for tid, x in tx.items():
            e = x["exons"]
            genes.add(x["attrs"].get("gene_id"))
            if "reference_id" in x["attrs"]:
                with_ref += 1
            if x["tline"] != (e[0][0], e[-1][1]):
                bad_span += 1
            if any(e[i][1] >= e[i + 1][0] for i in range(len(e) - 1)) or any(s > en for s, en, _ in e):
                bad_order += 1
            if e[-1][1] > sizes.get(x["chrom"], -1) or e[0][0] < 1:
                past_end += 1
            tpm += float(x["attrs"]["TPM"])
            i = introns(x)
            if i:
                multi += 1
                if x["strand"] not in ("+", "-"):
                    no_strand += 1
                for s, en in i:
                    q_introns.add((x["chrom"], x["strand"], s, en))
                    if (x["chrom"], s, en) not in junctions:
                        unsupported += 1
                if (x["chrom"], x["strand"], tuple(i)) in ref_chains:
                    chain_match += 1
                    match_ids.append(tid)
        n = len(tx)
        annotated = sum(1 for q in q_introns if q in ref_introns)
        ctpm, wtpm = scale(tx, "TPM")
        cfpkm, wfpkm = scale(tx, "FPKM")
        canon, nrows = canonical_sha(tx)
        with open(os.path.join(O, name + ".chainmatch.ids"), "w") as fh:   # for the gffcompare task
            fh.write("".join(sorted(m + "\n" for m in match_ids)))
        r = {"transcripts": n, "loci": len(genes), "multi_exon": multi, "with_reference_id": with_ref,
             "distinct_introns": len(q_introns), "annotated_introns": annotated,
             "novel_introns": len(q_introns) - annotated, "multi_exon_chain_matches": chain_match,
             "unsupported_intron_instances": unsupported, "tpm_sum": round(tpm, 3),
             "tpm_per_cov": ctpm, "tpm_implied_total_cov": 1e6 / ctpm, "fpkm_per_cov": cfpkm,
             "canonical_sha256": canon}
        res[name] = r
        line("%s\t%s" % (name, json.dumps(r, sort_keys=True)))
        check(n > 10000, "%s: only %d transcripts" % (name, n))
        check(close == 0, "%s: %d assembled introns of <= %d bp (gffcompare would merge them)" % (name, close, GFF_MIN_INTRON))
        check(nrows == n, "%s: canonical form lost transcripts" % name)
        check(bad_span == 0, "%s: %d transcripts whose span is not their exons' span" % (name, bad_span))
        check(bad_order == 0, "%s: %d transcripts with unsorted or overlapping exons" % (name, bad_order))
        check(past_end == 0, "%s: %d transcripts past a chromosome end" % (name, past_end))
        check(no_strand == 0, "%s: %d multi-exon transcripts without a strand" % (name, no_strand))
        check(wtpm <= 0, "%s: a TPM is off TPM = %.9g x cov by %.3g beyond rounding" % (name, ctpm, wtpm))
        check(wfpkm <= 0, "%s: an FPKM is off FPKM = %.9g x cov by %.3g beyond rounding" % (name, cfpkm, wfpkm))
        if mode == "denovo":
            check(unsupported == 0, "%s: %d assembled introns no read spans" % (name, unsupported))

a, b = res["denovo_p%s" % T]["fpkm_per_cov"], res["guided_p%s" % T]["fpkm_per_cov"]
line("fpkm_per_cov denovo %.12g guided %.12g rel %.3g" % (a, b, abs(a - b) / a))
check(abs(a - b) / a <= 1e-7, "FPKM/cov differs between de novo and guided on the same BAM")
for mode in ("denovo", "guided"):
    a, b = res["%s_p%s" % (mode, T)]["canonical_sha256"], res["%s_p1" % mode]["canonical_sha256"]
    line("%s_p%s_vs_p1\t%s %s\t%s" % (mode, T, a[:16], b[:16], "identical" if a == b else "DIFFER"))
    check(a == b, "%s: -p %s and -p 1 assemblies differ" % (mode, T))

json.dump(res, open(os.path.join(O, "check.json"), "w"), indent=1, sort_keys=True)
line("STRINGTIE IDENTITIES " + ("PASSED" if not fails else "FAILED: %d" % len(fails)))
open(os.path.join(O, "check.txt"), "a").write("\n".join(out) + "\n")
sys.exit(1 if fails else 0)
