"""openbabel-pdbfixer-real, part 1: Open Babel over the WHOLE ChEMBL 37 release.

    python3 -u ob_real.py <chemreps.txt.gz> <rdkit_real.tsv.gz> <out dir> <workers> [limit]

Per compound, from ChEMBL's SMILES:
  * InChI and InChIKey, compared with ChEMBL's published key and with RDKit's key for the same row
    (rdkit-real's whole-release output, read in lockstep: same file, same row order);
  * a SMILES -> MDL molfile -> molecule round trip, the molfile written as `obabel -ismi -osdf`
    writes it: atoms, bonds, elements, isotopes, formal charges, bond orders and the hydrogen count
    must survive it; canonical SMILES and InChI (with and without its stereo layers) are compared,
    and a canonical-SMILES difference is put to RDKit to say whether the molecule changed;
  * canonical SMILES idempotence: re-reading Open Babel's canonical SMILES gives it back;
  * element and charge conservation across engines: Open Babel's element counts (hydrogens
    included) and net charge against RDKit's formula for the same SMILES.
And from ChEMBL's standard InChI: Open Babel reads it and writes it back, and keys it.

Rows go in fixed 10,000-row chunks to a fork pool and come back in input order, so the output
does not depend on the worker count. Chunks 0-9 are the recipe's 100,000-row subset, so the recipe's
published counts are re-asserted from inside the whole-release run.
"""
import collections
import gzip
import hashlib
import multiprocessing as mp
import os
import re
import resource
import sys
import threading
import time

SRC, RDK, OUT, WORKERS = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
LIMIT = int(sys.argv[5]) if len(sys.argv) > 5 else 0
CHUNK = 10000
KEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
TOK = re.compile(r"([A-Z][a-z]?)(\d*)")
SYM = {}
RT = ("write_fail", "read_fail", "v3000", "header_2d", "read_as_2d", "atoms", "bonds", "elements", "isotopes", "charges",
      "bond_orders", "lossless", "hydrogens", "canonical", "inchi", "inchi_nostereo", "key", "stereo_lost")
FIELDS = ("n", "parsed", "key_empty", "chembl_key_match", "chembl_skeleton_match",
          "rdkit_parsed", "both_parsed", "rdkit_key_match", "rdkit_skeleton_match",
          "rdkit_heavy_match", "rdkit_h_match", "rdkit_charge_match", "rdkit_formula_match",
          "can_idempotent", "inchi_parsed", "inchi_roundtrip", "inchi_key_eq_chembl") \
    + tuple("mol_%s" % f for f in RT)
# Rows of these comparisons that fail are written out in full, so every count is traceable.
LISTS = ("can_idempotent", "rdkit_formula_match", "rdkit_skeleton_match") \
    + tuple("mol_%s" % f for f in ("lossless", "hydrogens", "canonical", "inchi_nostereo"))
STEREO = re.compile(r"/[btms][^/]*")


def init():
    global ob, conv
    from openbabel import openbabel as _ob
    ob = _ob
    ob.obErrorLog.SetOutputLevel(0)
    ob.obErrorLog.StopLogging()
    conv = {}
    for name, fin, fout in (("smi", "smi", None), ("mol_in", "mol", None), ("inchi_in", "inchi", None),
                            ("key", None, "inchikey"), ("inchi", None, "inchi"), ("can", None, "can"),
                            ("mol_out", None, "mol")):
        c = ob.OBConversion()
        if fin:
            c.SetInFormat(fin)
        if fout:
            c.SetOutFormat(fout)
        conv[name] = c
    et = ob.GetSymbol if hasattr(ob, "GetSymbol") else ob.OBElements.GetSymbol
    for z in range(0, 119):
        SYM[z] = et(z)


def read(fmt, text):
    m = ob.OBMol()
    return m if conv[fmt].ReadString(m, text) else None


def write(fmt, m):
    s = conv[fmt].WriteString(m)
    return s.split()[0] if s and s.split() else ""


def sig(m):
    """Everything a molfile stores explicitly, plus the total hydrogen count, from one molecule."""
    el, iso, ch, bo = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    h = 0
    for a in ob.OBMolAtomIter(m):
        z = a.GetAtomicNum()
        el[z] += 1
        iso[(z, a.GetIsotope())] += 1
        ch[a.GetFormalCharge()] += 1
        if z == 1:
            h += 1
        h += a.GetImplicitHCount()
    for b in ob.OBMolBondIter(m):
        bo[b.GetBondOrder()] += 1
    return {"atoms": m.NumAtoms(), "bonds": m.NumBonds(), "elements": el, "isotopes": iso,
            "charges": ch, "bond_orders": bo, "h": h, "net": sum(q * k for q, k in ch.items())}


def ob_formula(s):
    """Element counts with every hydrogen counted, keyed by symbol (RDKit's formula convention)."""
    f = collections.Counter()
    for z, k in s["elements"].items():
        if z != 1:
            f[SYM[z]] += k
    f["H"] += s["h"]
    return {k: v for k, v in f.items() if v}


def rd_formula(form):
    """RDKit CalcMolFormula string -> (element counts, net charge). D and T count as H."""
    q = 0
    m = re.search(r"([+-])(\d*)$", form)
    if m:
        q = (1 if m.group(1) == "+" else -1) * (int(m.group(2)) if m.group(2) else 1)
        form = form[:m.start()]
    f = collections.Counter()
    for el, n in TOK.findall(form):
        if el in ("D", "T"):
            el = "H"
        f[el] += int(n) if n else 1
    return {k: v for k, v in f.items() if v}, q


def roundtrip(t, m, s1, can, ii, k, c, lists, cid, smi):
    """Write m as a molfile, read it back, and compare with the molecule read from the SMILES."""
    mb = conv["mol_out"].WriteString(m)
    if not mb:
        c[t + "_write_fail"] += 1
        return
    c[t + "_v3000"] += int("V3000" in mb[:2000])
    # the molfile's header line 2 carries a dimension code in columns 21-22
    hdr = mb.split("\n")
    c[t + "_header_2d"] += int(len(hdr) > 1 and hdr[1][20:22] == "2D")
    m2 = read("mol_in", mb)
    if m2 is None:
        c[t + "_read_fail"] += 1
        return
    c[t + "_read_as_2d"] += int(m2.GetDimension() == 2)
    s2 = sig(m2)
    eq = {f: s1[f] == s2[f] for f in ("atoms", "bonds", "elements", "isotopes", "charges", "bond_orders")}
    for f, v in eq.items():
        c[t + "_" + f] += int(v)
    lossless = all(eq.values())
    c[t + "_lossless"] += int(lossless)
    if not lossless:
        lists[t + "_lossless"].append("%s\t%s\t%s\n" % (cid, ",".join(f for f, v in eq.items() if not v), smi))
    okh = s1["h"] == s2["h"]
    c[t + "_hydrogens"] += int(okh)
    if not okh:
        lists[t + "_hydrogens"].append("%s\t%d\t%d\t%s\n" % (cid, s1["h"], s2["h"], smi))
    can2 = write("can", m2)
    c[t + "_canonical"] += int(can2 == can)
    if can2 != can:
        lists[t + "_canonical"].append("%s\t%s\t%s\n" % (cid, can, can2))
    ii2 = conv["inchi"].WriteString(m2).strip()
    c[t + "_inchi"] += int(ii2 == ii)
    ns = STEREO.sub("", ii2) == STEREO.sub("", ii)
    c[t + "_inchi_nostereo"] += int(ns)
    if not ns:
        lists[t + "_inchi_nostereo"].append("%s\t%s\t%s\n" % (cid, ii, ii2))
    c[t + "_stereo_lost"] += int(bool(STEREO.search(ii)) and not STEREO.search(ii2))
    c[t + "_key"] += int(write("key", m2) == k)


def work(job):
    idx, rows = job
    c = dict.fromkeys(FIELDS, 0)
    lists = {k: [] for k in LISTS}
    out = []
    for line, rline in rows:
        cid, smi, std_inchi, std_key = line.rstrip("\n").split("\t")
        r = rline.rstrip("\n").split("\t")
        assert r[0] == cid, "rdkit_real.tsv out of step at %s / %s" % (cid, r[0])
        rd_key, rd_form = r[1], r[2]
        c["n"] += 1
        c["rdkit_parsed"] += int(bool(rd_key))

        # --- ChEMBL's own InChI, read and written back by Open Babel (independent of the SMILES)
        mi = read("inchi_in", std_inchi)
        irt = ""
        if mi is not None and mi.NumAtoms() > 0:
            c["inchi_parsed"] += 1
            irt = conv["inchi"].WriteString(mi).strip()
            c["inchi_roundtrip"] += int(irt == std_inchi)
            c["inchi_key_eq_chembl"] += int(write("key", mi) == std_key)

        m = read("smi", smi)
        if m is None or m.NumAtoms() == 0:
            out.append("%s\t\t\t\t\n" % cid)
            continue
        c["parsed"] += 1
        s1 = sig(m)                       # before any writer touches the molecule
        ii = conv["inchi"].WriteString(m).strip()
        k = write("key", m)
        can = write("can", m)
        if not k:
            c["key_empty"] += 1
        c["chembl_key_match"] += int(bool(k) and k == std_key)
        c["chembl_skeleton_match"] += int(bool(k) and k.split("-")[0] == std_key.split("-")[0])

        # --- canonical SMILES idempotence
        m3 = read("smi", can)
        can3 = write("can", m3) if m3 is not None else ""
        ok = can3 == can
        c["can_idempotent"] += int(ok)
        if not ok:
            lists["can_idempotent"].append("%s\t%s\t%s\n" % (cid, can, can3))

        # --- SMILES -> molfile -> molecule, as `obabel -ismi -osdf` writes it (no coordinates
        # generated). A 2D layout (--gen2D) was probed and dropped: it took >14 min per 10,000
        # compounds on the release's large macrocycles, i.e. it would be the workload.
        roundtrip("mol", m, s1, can, ii, k, c, lists, cid, smi)

        # --- against RDKit, same SMILES
        obf = ob_formula(s1)
        if rd_key:
            c["both_parsed"] += 1
            c["rdkit_key_match"] += int(k == rd_key)
            sk = bool(k) and k.split("-")[0] == rd_key.split("-")[0]
            c["rdkit_skeleton_match"] += int(sk)
            if not sk:
                lists["rdkit_skeleton_match"].append("%s\t%s\t%s\t%s\n" % (cid, k, rd_key, smi))
            rf, rq = rd_formula(rd_form)
            heavy = {e: v for e, v in obf.items() if e != "H"} == {e: v for e, v in rf.items() if e != "H"}
            hh = obf.get("H", 0) == rf.get("H", 0)
            qq = s1["net"] == rq
            c["rdkit_heavy_match"] += int(heavy)
            c["rdkit_h_match"] += int(hh)
            c["rdkit_charge_match"] += int(qq)
            c["rdkit_formula_match"] += int(heavy and hh and qq)
            if not (heavy and hh and qq):
                lists["rdkit_formula_match"].append("%s\t%s\t%s\t%d\t%s\t%s\n" % (
                    cid, "".join(f for f, v in (("E", heavy), ("H", hh), ("Q", qq)) if not v),
                    "".join("%s%d" % kv for kv in sorted(obf.items())), s1["net"], rd_form, smi))
        out.append("%s\t%s\t%s\t%d\t%s\n" % (cid, k, can, s1["net"],
                                             "".join("%s%d" % kv for kv in sorted(obf.items()))))
    return idx, c, "".join(out), lists


def chunks(fh, rh, gate):
    """Pool.imap reads its iterable as fast as it can, so the gate keeps at most a few chunks in
    flight instead of the whole release in memory."""
    idx, buf, n = 0, [], 0
    for line, rline in zip(fh, rh):
        buf.append((line, rline))
        n += 1
        if len(buf) == CHUNK:
            gate.acquire()
            yield idx, buf
            idx, buf = idx + 1, []
        if LIMIT and n >= LIMIT:
            break
    if buf:
        gate.acquire()
        yield idx, buf


def main():
    os.makedirs(OUT, exist_ok=True)
    total = dict.fromkeys(FIELDS, 0)
    first = dict.fromkeys(FIELDS, 0)
    lists = {k: [] for k in LISTS}
    ids, dup = set(), 0
    h = hashlib.sha256()
    rh_sha = hashlib.sha256()
    t0 = time.time()

    class Hashing:
        """rdkit_real.tsv read through a sha256, so the cross-code input is pinned as it is read."""
        def __init__(self, fh):
            self.fh = fh

        def __iter__(self):
            for line in self.fh:
                rh_sha.update(line.encode())
                yield line

    with gzip.open(SRC, "rt") as fh, gzip.open(RDK, "rt") as rh, open(os.path.join(OUT, "ob_real.tsv"), "w") as out:
        header = next(fh).rstrip("\n").split("\t")
        print("header", header, flush=True)
        with mp.get_context("fork").Pool(WORKERS, initializer=init) as pool:
            gate = threading.BoundedSemaphore(3 * WORKERS)
            for idx, c, text, ls in pool.imap(work, chunks(fh, Hashing(rh), gate), chunksize=1):
                gate.release()
                for k in FIELDS:
                    total[k] += c[k]
                    if idx < 10:
                        first[k] += c[k]
                for k in LISTS:
                    lists[k] += ls[k]
                for line in text.splitlines():
                    cid = line.split("\t", 1)[0]
                    dup += cid in ids
                    ids.add(cid)
                out.write(text)
                h.update(text.encode())
                if idx % 25 == 0:
                    print("chunk %d rows %d  %.0f s" % (idx, total["n"], time.time() - t0), flush=True)
        rest = sum(1 for _ in Hashing(rh)) if not LIMIT else -1   # drain, so the sha covers the whole file
    wall = time.time() - t0
    rows_out = sum(1 for _ in open(os.path.join(OUT, "ob_real.tsv")))
    for k in LISTS:
        with open(os.path.join(OUT, "mismatch_%s.tsv" % k), "w") as f:
            f.writelines(lists[k])
    # A canonical-SMILES difference is either two spellings of one molecule (a canonicaliser
    # instability) or two molecules (information lost). RDKit, a second canonicaliser, decides:
    # it reads both strings, stereo included, and says whether they are the same molecule.
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")

    def rdcan(x):
        mm = Chem.MolFromSmiles(x)
        return Chem.MolToSmiles(mm) if mm is not None else None
    adjud = {}
    for k in ("can_idempotent", "mol_canonical"):
        same = unread = 0
        for line in lists[k]:
            _, a, b = line.rstrip("\n").split("\t")
            ra, rb = rdcan(a), rdcan(b)
            unread += int(ra is None or rb is None)
            same += int(ra is not None and ra == rb)
        adjud[k] = (len(lists[k]), same, unread)
    keys_ok = all(KEY.match(l.split("\t")[1]) for l in open(os.path.join(OUT, "ob_real.tsv")) if l.split("\t")[1])

    from openbabel import openbabel as _ob
    with open(os.path.join(OUT, "metrics.txt"), "w") as m:
        m.write("openbabel_version %s\n" % str(_ob.OBReleaseVersion()))
        m.write("workers %d\nlimit %d\nworkload_wall_s %.1f\n" % (WORKERS, LIMIT, wall))
        m.write("worker_maxrss_kib %d\n" % resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        for k in FIELDS:
            m.write("%s %d\n" % (k, total[k]))
        for k in FIELDS:
            m.write("first100k_%s %d\n" % (k, first[k]))
        for k, (nn, same, unread) in adjud.items():
            m.write("%s_differ %d\n%s_same_molecule_per_rdkit %d\n%s_rdkit_unreadable %d\n" % (k, nn, k, same, k, unread))
        m.write("duplicate_ids %d\nrows_out %d\nrdkit_rows_after %d\nrdkit_real_sha256 %s\noutput_sha256 %s\n"
                % (dup, rows_out, rest, rh_sha.hexdigest(), h.hexdigest()))
    print(open(os.path.join(OUT, "metrics.txt")).read(), flush=True)

    fails = []

    def check(name, ok):
        ok = bool(ok)
        print("%-70s %s" % (name, "ok" if ok else "FAIL"), flush=True)
        if not ok:
            fails.append(name)

    t, f = total, first
    check("chembl_id unique; one row out per row in", dup == 0 and rows_out == t["n"])
    check("every written key is a well-formed 27-char InChIKey", keys_ok)
    check("molfile round trip: no write or read failure", t["mol_write_fail"] == 0 and t["mol_read_fail"] == 0)
    check("molfile round trip keeps atoms, bonds, elements, isotopes, charges, bond orders",
          t["mol_lossless"] == t["parsed"])
    check("molfile round trip keeps every hydrogen", t["mol_hydrogens"] == t["parsed"])
    # Canonical SMILES is not always idempotent (an isotope-symmetry tie, measured), so the claim
    # asserted is the one that matters: re-canonicalising never changes the molecule.
    check("re-canonicalising never changes the molecule (RDKit reads both as one)",
          adjud["can_idempotent"][1] == adjud["can_idempotent"][0] == t["parsed"] - t["can_idempotent"])
    check("molfile round trip keeps the InChI connectivity (stereo layers aside)",
          t["mol_inchi_nostereo"] == t["parsed"])
    if not LIMIT:
        check("rdkit_real.tsv is rdkit-real's verified output (uncompressed sha256)",
              rh_sha.hexdigest() == "9a84f0adae6bc1ddd7df138277ac1b54750d7452a3ad989a89f8b0a552372244")
        check("rdkit_real.tsv has exactly one row per release row", rest == 0)
    # The recipe's published numbers, re-asserted on the same 100,000 rows inside this run.
    check("first 100k: compounds 100000, parsed 100000", f["n"] == 100000 and f["parsed"] == 100000)
    check("first 100k: vs ChEMBL key 99846, connectivity 99930",
          f["chembl_key_match"] == 99846 and f["chembl_skeleton_match"] == 99930)
    check("first 100k: both parsed 99997; vs RDKit key 99926, connectivity 99928",
          f["both_parsed"] == 99997 and f["rdkit_key_match"] == 99926 and f["rdkit_skeleton_match"] == 99928)
    if fails:
        print("FAILED: %s" % "; ".join(fails), flush=True)
        sys.exit(1)
    print("OPENBABEL REAL OK", flush=True)


if __name__ == "__main__":
    main()
