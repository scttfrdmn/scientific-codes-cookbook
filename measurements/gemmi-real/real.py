#!/usr/bin/env python3
"""gemmi-real: read the whole wwPDB archive as of the 20260101 RODA snapshot with gemmi.

Every entry's mmCIF, its PDB-format file where wwPDB writes one, and its structure-factor file
where one was deposited, streamed from the public bucket and checked against the manifest's size
and S3 ETag before gemmi reads a byte. Per entry it records what gemmi reads and what the archive
itself records about the same entry, and then asserts the claims in ASSERTS at the end.

    python3 -u real.py            (env: MANIFEST, OUT, WORKERS, SAMPLE, BIG)

SAMPLE=k keeps one entry in k (by a hash of the id) plus every entry with an object over BIG bytes,
for the sizing probe; SAMPLE=1 is the whole archive.
"""
import gzip
import hashlib
import http.client
import io
import math
import os
import re
import resource
import sys
import time
import zlib
import multiprocessing

import gemmi

HOST = "pdbsnapshots.s3.us-west-2.amazonaws.com"
MANIFEST = os.environ.get("MANIFEST", "/tmp/manifest.tsv.gz")
OUT = os.environ.get("OUT", "/tmp/out")
WORKERS = int(os.environ.get("WORKERS", "0")) or 2 * os.cpu_count()
SAMPLE = int(os.environ.get("SAMPLE", "1"))
BIG = int(os.environ.get("BIG", str(50 << 20)))
BIG_WORKERS = int(os.environ.get("BIG_WORKERS", "6"))
PART = 8 << 20
CHUNK = 4 << 20

COLS = ["id",
        # mmCIF
        "cif_err", "block", "n_atom_site", "n_atoms", "n_models", "n_model_ids", "method",
        "cell", "sg_hm", "sg_num_rec", "sg_num_gemmi", "entity_nmol_bad", "n_entity",
        "poly_wt_n", "poly_wt_maxrel", "r_work", "r_free", "d_high", "n_obs_rec", "n_free_rec",
        # PDB-format file
        "pdb_err", "pdb_equal", "pdb_lines", "pdb_unit1", "pdb_diff",
        # structure factors
        "sf_err", "sf_blocks", "sf_kind", "sf_refl", "sf_refl0", "sf_dmin0", "sf_free0", "sf_obs0",
        "sf_cell_maxdiff", "sf_sg0"]


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


# ---------------------------------------------------------------- fetch, with the pin checked
_conn = None
TRIES = 4


class Transient(IOError):
    """An S3 5xx or a dropped connection: S3 asks clients to retry these, with backoff."""


def get(key):
    """Yield the object's bytes in chunks, verifying size and ETag; raise on any mismatch."""
    global _conn
    for attempt in (0, 1):   # one reconnect for a dropped keep-alive, not a retry loop
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection(HOST, timeout=120)
            _conn.request("GET", "/" + key)
            r = _conn.getresponse()
            if r.status != 200:
                body = r.read(200)
                raise (Transient if r.status >= 500 else IOError)("HTTP %d %s" % (r.status, body[:80]))
            return r
        except (http.client.HTTPException, OSError) as e:
            _conn = None
            if attempt or "HTTP " in str(e):
                raise
    raise IOError("unreachable")


def stream(key, size, etag):
    r = get(key)
    whole, part, partmd5s, n = hashlib.md5(), hashlib.md5(), [], 0
    fill = 0
    while True:
        b = r.read(CHUNK)
        if not b:
            break
        n += len(b)
        whole.update(b)
        # multipart ETag: md5 over the concatenated md5s of each 8 MiB part
        off = 0
        while off < len(b):
            take = min(PART - fill, len(b) - off)
            part.update(b[off:off + take])
            fill += take
            off += take
            if fill == PART:
                partmd5s.append(part.digest())
                part, fill = hashlib.md5(), 0
        yield b
    if fill:
        partmd5s.append(part.digest())
    if n != int(size):
        raise IOError("size %d != pinned %s" % (n, size))
    if "-" in etag:
        got = "%s-%d" % (hashlib.md5(b"".join(partmd5s)).hexdigest(), len(partmd5s))
    else:
        got = whole.hexdigest()
    if got != etag:
        raise IOError("etag %s != pinned %s" % (got, etag))


def fetch_text(key, size, etag):
    z = zlib.decompressobj(31)
    out = [z.decompress(b) for b in stream(key, size, etag)]
    out.append(z.flush())
    if not z.eof:
        raise IOError("truncated gzip")
    return b"".join(out)


def blocks(key, size, etag):
    """Decompress a structure-factor file and yield its CIF data blocks one at a time, so a 3 GB
    group deposition (hundreds of datasets) never sits in memory whole."""
    z = zlib.decompressobj(31)
    buf = bytearray()
    pos = 1   # where the next search for a block start begins (never rescan: a block can be 1 GB)

    def cut(final):
        nonlocal buf, pos
        while True:
            i = buf.find(b"\ndata_", pos)
            if i < 0:
                pos = max(1, len(buf) - 6)
                break
            yield bytes(buf[:i + 1])
            del buf[:i + 1]
            pos = 1
        if final and buf.strip():
            yield bytes(buf)
            buf = bytearray()

    for b in stream(key, size, etag):
        buf += z.decompress(b)
        yield from cut(False)
    buf += z.flush()
    if not z.eof:
        raise IOError("truncated gzip")
    yield from cut(True)


# ---------------------------------------------------------------- per-entry reads
def val(block, tag):
    v = block.find_value(tag)
    if v is None:
        return "-"
    v = gemmi.cif.as_string(v)
    return v if v not in ("", "?", ".") else "-"


def err(e):
    return ("%s:%s" % (type(e).__name__, str(e)[:60])).replace("\t", " ").replace("\n", " ")


def cell6(c):
    return (c.a, c.b, c.c, c.alpha, c.beta, c.gamma)


def fmt_cell(c):
    return ",".join(repr(x) for x in cell6(c))


def read_cif(row, r):
    text = fetch_text(row["cif_key"], row["cif_size"], row["cif_md5"])
    doc = gemmi.cif.read_string(text)
    del text
    b = doc.sole_block()
    r["block"] = b.name
    r["n_atom_site"] = len(b.find_values("_atom_site.id"))
    st = gemmi.make_structure_from_block(b)
    r["n_atoms"] = sum(m.count_atom_sites() for m in st)
    r["n_models"] = len(st)
    r["n_model_ids"] = len(set(b.find_values("_atom_site.pdbx_PDB_model_num")))
    meth = sorted(gemmi.cif.as_string(x) for x in b.find_values("_exptl.method"))
    r["method"] = "|".join(meth).replace(" ", "_") or "-"
    r["cell"] = fmt_cell(st.cell)
    r["sg_hm"] = st.spacegroup_hm.replace(" ", "_") or "-"
    r["sg_num_rec"] = val(b, "_symmetry.Int_Tables_number")
    sg = gemmi.find_spacegroup_by_name(st.spacegroup_hm) if st.spacegroup_hm else None
    r["sg_num_gemmi"] = sg.number if sg else "-"
    # wwPDB derives _entity.pdbx_number_of_molecules from the asym units it assigns
    asym = {}
    for e in b.find_values("_struct_asym.entity_id"):
        e = gemmi.cif.as_string(e)
        asym[e] = asym.get(e, 0) + 1
    t = b.find("_entity.", ["id", "pdbx_number_of_molecules", "?formula_weight", "?type"])
    bad, wt = 0, {}
    for row_ in t:
        eid = gemmi.cif.as_string(row_[0])
        nm = gemmi.cif.as_string(row_[1])
        # water is one asym unit holding many molecules, so it is the one type this cannot hold for
        water = row_.has(3) and gemmi.cif.as_string(row_[3]) == "water"
        if not water and nm.isdigit() and int(nm) != asym.get(eid, 0):
            bad += 1
        if row_.has(2):
            try:
                wt[eid] = float(gemmi.cif.as_string(row_[2]))
            except ValueError:
                pass
    r["entity_nmol_bad"] = bad
    r["n_entity"] = len(t)
    # polymer weight from the sequence (gemmi's tabulated residues) vs the archive's formula_weight
    seqs = {}
    for row_ in b.find("_entity_poly_seq.", ["entity_id", "num", "mon_id"]):
        eid, num, mon = (gemmi.cif.as_string(row_[i]) for i in range(3))
        s = seqs.setdefault(eid, {})
        s.setdefault(num, mon)   # microheterogeneity: the first listed monomer at each position
    n_wt, maxrel = 0, 0.0
    for eid, s in seqs.items():
        names = list(s.values())
        if eid not in wt or wt[eid] <= 0 or len(names) < 2:
            continue
        if any(gemmi.find_tabulated_residue(m) is None for m in names):
            continue
        w = gemmi.calculate_sequence_weight(names)
        n_wt += 1
        maxrel = max(maxrel, abs(w - wt[eid]) / wt[eid])
    r["poly_wt_n"] = n_wt
    r["poly_wt_maxrel"] = "%.3e" % maxrel if n_wt else "-"
    r["r_work"] = val(b, "_refine.ls_R_factor_R_work")
    r["r_free"] = val(b, "_refine.ls_R_factor_R_free")
    r["d_high"] = val(b, "_refine.ls_d_res_high")
    r["n_obs_rec"] = val(b, "_refine.ls_number_reflns_obs")
    r["n_free_rec"] = val(b, "_refine.ls_number_reflns_R_free")
    return st


PDBOPT = gemmi.PdbWriteOptions(minimal=True)
PDBOPT.ter_records = False


NUM = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?)")
# PDB's fixed columns for the records whose numbers abut (`1.00133.38`, `-27.405-141.718`)
COLUMNS = {
    "atom": [(30, 38, "xyz"), (38, 46, "xyz"), (46, 54, "xyz"), (54, 60, "occ"), (60, 66, "b")],
    "hetatm": [(30, 38, "xyz"), (38, 46, "xyz"), (46, 54, "xyz"), (54, 60, "occ"), (60, 66, "b")],
    "anisou": [(28 + 7 * k, 35 + 7 * k, "u") for k in range(6)],
    "cryst1": [(6, 15, "abc"), (15, 24, "abc"), (24, 33, "abc"), (33, 40, "ang"), (40, 47, "ang"), (47, 54, "ang")],
}


def unit_diff(u, v, integer_ok):
    """0 if u and v are the same number printed differently (a signed zero), 1 if one unit apart
    in the last printed place, None otherwise. Integers are ids or counts unless integer_ok."""
    u, v = u.strip(), v.strip()
    if ("." in u) != ("." in v) or ("." not in u and not integer_ok):
        return None
    if "." in u and len(u.split(".")[1]) != len(v.split(".")[1]):
        return None
    try:
        d = abs(int(u.replace(".", "")) - int(v.replace(".", "")))
    except ValueError:
        return None
    return d if d <= 1 else None


def hy36(t):
    """Decode a 4-character hybrid-36 residue number (A000 = 10000), or None."""
    t = t.strip()
    if len(t) != 4 or not t[0].isalpha() or not t.isalnum():
        return None
    try:
        if t[0].isupper():
            return int(t, 36) - int("A000", 36) + 10000
        return int(t, 36) - int("a000", 36) + 10000 + 26 * 36 ** 3
    except ValueError:
        return None


def one_unit(x, y):
    """The record classes if x and y differ only by one unit in the last printed place of decimal
    numbers (ANISOU's integers count as decimals in 1e-4 A^2), or by the sign of a zero; else None.
    Every non-numeric byte, and every integer that is a count or an id, must be identical."""
    if x[:6] != y[:6] or len(x) != len(y):
        return None
    rec = x[:6].strip().lower()
    seen = []
    if rec in ("atom", "hetatm", "anisou") and x[22:26] != y[22:26]:
        # A residue number over 9999: gemmi writes it in hybrid-36 ("A001" is 10001), and the
        # archive's own PDB-format file carries it modulo 10000 ("0001"). Accepted only if that
        # is exactly the relation, and then counted, because it is the archive's file losing a digit.
        n = hy36(x[22:26])
        try:
            m = int(y[22:26])
        except ValueError:
            return None
        if n is None or n < 10000 or n % 10000 != m:
            return None
        seen.append(rec + "_resseq_mod10000")
        y = y[:22] + x[22:26] + y[26:]
        if x == y:
            return seen[0]
    if rec == "cryst1":
        if x[66:] != y[66:]:
            seen.append("cryst1_z")
        x, y = x[:66], y[:66]
    cols = COLUMNS.get(rec)
    if cols:
        lo, hi = cols[0][0], cols[-1][1]
        if x[:lo] != y[:lo] or x[hi:] != y[hi:]:
            return None
        pairs = [(x[i:j], y[i:j], f) for i, j, f in cols if x[i:j] != y[i:j]]
    else:
        xs, ys = NUM.split(x), NUM.split(y)
        if len(xs) != len(ys) or any(xs[k].strip() != ys[k].strip() for k in range(0, len(xs), 2)):
            return None
        pairs = [(xs[k], ys[k], "num") for k in range(1, len(xs), 2) if xs[k] != ys[k]]
    for u, v, f in pairs:
        d = unit_diff(u, v, rec == "anisou")
        if d is None:
            return None
        seen.append("%s_%s%s" % (rec, f, "_negzero" if d == 0 else ""))
    return "+".join(sorted(set(seen))) or None


def read_pdb(row, r, st_cif):
    text = fetch_text(row["pdb_key"], row["pdb_size"], row["pdb_md5"])
    st = gemmi.read_pdb_string(text)
    del text
    if st_cif is None:
        r["pdb_equal"] = "-"
        return
    # The same entry in wwPDB's two formats, through gemmi's two parsers, written back by one
    # writer: every atom (name, altloc, residue, chain, model, xyz, occupancy, B, element, charge,
    # ANISOU) plus CRYST1 must come out byte-identical.
    a = st_cif.make_pdb_string(PDBOPT).splitlines()
    p = st.make_pdb_string(PDBOPT).splitlines()
    r["pdb_lines"] = len(p)
    r1 = {}
    if a == p:
        r["pdb_equal"] = 1
        return
    # The differences allowed are the formats', each bounded by a field width, and counted:
    #  - CRYST1's Z (cols 67-70) is written from _cell.Z_PDB, which many mmCIFs leave out;
    #  - a numeric field may sit one unit apart in its last printed place (0.001 A in x/y/z, 0.01
    #    in occupancy and B, 1e-4 A^2 in ANISOU) where the mmCIF carries more digits than the PDB
    #    column holds, so wwPDB's rounding and gemmi's rounding of the same decimal can disagree
    #    by one unit, never two. Every other byte, and every count, must be identical.
    first = None
    if len(a) == len(p):
        for i, (x, y) in enumerate(zip(a, p)):
            if x == y:
                continue
            kind = one_unit(x, y)
            if kind is None:
                first = i
                break
            r1[kind] = r1.get(kind, 0) + 1
    else:
        first = next((i for i, (x, y) in enumerate(zip(a, p)) if x != y), min(len(a), len(p)))
    r["pdb_unit1"] = ",".join("%s:%d" % kv for kv in sorted(r1.items())) or "-"
    if first is None:
        r["pdb_equal"] = 1
        return
    # The two formats can list the same atoms in a different order (8x0q: a branched sugar before,
    # or after, a ligand). Fall back to the atom records as a multiset per model, serial numbers
    # aside: sorted, they must pair up under the same one-unit rule, every record accounted for.
    def records(lines):
        out, model = [], 0
        for ln in lines:
            if ln.startswith("MODEL"):
                model += 1
            elif ln[:6] in ("ATOM  ", "HETATM", "ANISOU"):
                out.append((model, ln[:6] + "      " + ln[11:]))
        out.sort()
        return out
    ra, rp = records(a), records(p)
    r["pdb_equal"] = 0
    if len(ra) == len(rp) and all(m == n for (m, _), (n, _) in zip(ra, rp)):
        r2 = {}
        for (_, x), (_, y) in zip(ra, rp):
            if x == y:
                continue
            kind = one_unit(x, y)
            if kind is None:
                r2 = None
                break
            r2[kind] = r2.get(kind, 0) + 1
        if r2 is not None:
            r["pdb_equal"] = 2          # same atoms, reordered
            r["pdb_unit1"] = ",".join("%s:%d" % kv for kv in sorted(r2.items())) or "-"
    x = a[first] if first < len(a) else "<eof>"
    y = p[first] if first < len(p) else "<eof>"
    r["pdb_diff"] = ("L%d/%d/%d|%s|%s" % (first, len(a), len(p), x[:80].rstrip(), y[:80].rstrip())).replace("\t", " ")


def read_sf(row, r, st_cif):
    nb, nrefl, kinds, celldiff = 0, 0, set(), 0.0
    for text in blocks(row["sf_key"], row["sf_size"], row["sf_md5"]):
        doc = gemmi.cif.read_string(text)
        del text
        for rb in gemmi.as_refln_blocks(doc):
            loop = rb.default_loop
            if loop is None:
                kinds.add("noloop")
                nb += 1
                continue
            kind = "merged" if rb.is_merged() else "unmerged"
            kinds.add(kind)
            n = loop.length()
            nrefl += n
            if st_cif is not None and rb.cell.is_crystal() and st_cif.cell.is_crystal():
                celldiff = max(celldiff, max(abs(x - y) for x, y in zip(cell6(rb.cell), cell6(st_cif.cell))))
            if nb == 0:
                r["sf_refl0"] = n
                r["sf_sg0"] = (rb.spacegroup.hm.replace(" ", "_") if rb.spacegroup else "-")
                if n and rb.cell.is_crystal():
                    d = rb.make_d_array()
                    r["sf_dmin0"] = repr(float(d.min()))
                pre = "_refln." if kind == "merged" else "_diffrn_refln."
                st_col = rb.block.find_values(pre + "status")
                if len(st_col):
                    v = [gemmi.cif.as_string(x) for x in st_col]
                    r["sf_free0"] = v.count("f")
                    r["sf_obs0"] = v.count("o") + v.count("f")
            nb += 1
        del doc
    r["sf_blocks"] = nb
    r["sf_kind"] = "|".join(sorted(kinds)) or "-"
    r["sf_refl"] = nrefl
    r["sf_cell_maxdiff"] = "%.4g" % celldiff


def attempt(fn, retries):
    """Run one file's read; a transport failure (S3 5xx, a reset connection) is retried up to
    TRIES times with backoff, as S3 asks of clients. A size or ETag mismatch, or anything gemmi
    raises, is not transport and is never retried."""
    global _conn
    for k in range(TRIES):
        try:
            return fn()
        except (Transient, ConnectionError, http.client.HTTPException, TimeoutError):
            _conn = None
            if k == TRIES - 1:
                raise
            retries[0] += 1
            time.sleep(2 ** k)


def entry(row):
    t0 = time.time()
    r = {c: "-" for c in COLS}
    r["id"] = row["id"]
    retries = [0]
    st = None
    try:
        st = attempt(lambda: read_cif(row, r), retries)
        r["cif_err"] = 0
    except Exception as e:  # recorded, counted and asserted on at the end -- never swallowed
        r["cif_err"] = err(e)
    if row["pdb_key"] != "-":
        try:
            attempt(lambda: read_pdb(row, r, st), retries)
            r["pdb_err"] = 0
        except Exception as e:
            r["pdb_err"] = err(e)
    if row["sf_key"] != "-":
        try:
            attempt(lambda: read_sf(row, r, st), retries)
            r["sf_err"] = 0
        except Exception as e:
            r["sf_err"] = err(e)
    gb = (int(row["cif_size"]) + int(row["pdb_size"]) + int(row["sf_size"])) / 1e9
    return "\t".join(str(r[c]) for c in COLS), time.time() - t0, gb, retries[0]


# ---------------------------------------------------------------- the archive's own defects
# Every entry here was found by a whole-archive run and confirmed by reading the archive's raw
# bytes; each is a defect in the snapshot's files, not in gemmi. The run asserts the set of entries
# breaking each claim IS this set -- a new one fails, and so does one of these going away.
KNOWN = {
    "pdb_differ": {
        "2cbi": "O LYS B 476 has U11+U22+U33 = 0 exactly (U11 -0.0138, not positive definite); gemmi "
                "writes ANISOU only for a nonzero trace, and the PDB file's integers round to a trace of ~1e-9",
        "4xkt": "the mmCIF's U23 for S SO4 J 201 is -1307 A^2 (a dropped '0.'), which no 7-column ANISOU field holds",
        "7r3k": "HOH K 6004 has y = -1076.96, which %8.3f cannot hold; the PDB file prints 1076.960, sign lost",
    },
    "sf_err": {
        "1e42": "a reflection row '1 1 1 ? ? ? ...': Miller indices of '?'",
        "2w4q": "the text 'END OF REFLECTIONS' parsed as loop values",
        "3zqo": "the text 'END OF REFLECTIONS' parsed as loop values",
        "2yok": "_audit.update_record appears twice in one block, which CIF forbids",
    },
    "sg_unknown": {
        "1ldm": "'C 4 21 2': a C-centred setting of P 4 21 2 (no. 90) that is not a standard symbol",
        "3ldh": "'C 4 21 2', as 1ldm",
    },
    "entity_nmol": {
        "2icy": "entity 4 (UMP) records 1 molecule but has no struct_asym and no atoms",
    },
}


# ---------------------------------------------------------------- the space groups themselves
def closure(sg):
    ops = list(sg.operations())
    trip = set(o.triplet() for o in ops)
    if len(trip) != len(ops):
        return len(ops), -1
    out = 0
    for a in ops:
        for b in ops:
            if (a * b).wrap().triplet() not in trip:
                out += 1
    return len(ops), out


def main():
    os.makedirs(OUT, exist_ok=True)
    print("gemmi\t%s" % ver(gemmi))
    print("python\t%s" % sys.version.split()[0])
    print("workers\t%d  sample\t1/%d  big\t%d" % (WORKERS, SAMPLE, BIG))
    with gzip.open(MANIFEST, "rt") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        rows = [dict(zip(hdr, l.rstrip("\n").split("\t"))) for l in f]
    print("manifest entries\t%d" % len(rows))
    if SAMPLE > 1:
        def keep(x):
            if max(int(x["cif_size"]), int(x["pdb_size"]), int(x["sf_size"])) > BIG:
                return True
            return int(hashlib.md5(x["id"].encode()).hexdigest(), 16) % SAMPLE == 0
        rows = [x for x in rows if keep(x)]
    # biggest first, so the 3 GB group depositions start at once and are not the stragglers
    rows.sort(key=lambda x: -(int(x["cif_size"]) + int(x["pdb_size"]) + int(x["sf_size"])))
    gb_total = sum(int(x["cif_size"]) + int(x["pdb_size"]) + int(x["sf_size"]) for x in rows) / 1e9
    print("entries to read\t%d  (%.1f GB gzipped)" % (len(rows), gb_total))

    t0 = time.time()
    lines, slow = [], []
    done_gb = 0.0
    # Entries with an object over BIG (~100 of them: PanDDA group depositions up to 3.2 GB gzipped,
    # the largest cryo-EM models) go to a pool of their own, BIG_WORKERS wide, running alongside the
    # main one: all of them at once, which biggest-first in one pool does, peaked at 58.8 of 64 GiB.
    def is_big(x):
        return max(int(x["cif_size"]), int(x["pdb_size"]), int(x["sf_size"])) > BIG
    big = [x for x in rows if is_big(x)]
    small = [x for x in rows if not is_big(x)]
    print("big entries\t%d on %d workers, the rest on %d" % (len(big), BIG_WORKERS, WORKERS))
    results = []
    # fork, not 3.14's default forkserver: the workers must be this process's children for
    # RUSAGE_CHILDREN to count their CPU time and peak RSS
    ctx = multiprocessing.get_context("fork")
    bigpool = ctx.Pool(BIG_WORKERS, maxtasksperchild=1)
    bigres = bigpool.map_async(entry, big, chunksize=1)
    bigpool.close()
    pool = ctx.Pool(WORKERS, maxtasksperchild=2000)
    for i, res in enumerate(pool.imap_unordered(entry, small, chunksize=1)):
        results.append(res)
        done_gb += res[2]
        if (i + 1) % 10000 == 0 or i + 1 == len(small):
            el = time.time() - t0
            print("progress\t%d/%d\t%.0f s\t%.1f GB\t%.0f MB/s" % (i + 1, len(small), el, done_gb, done_gb * 1e3 / max(el, 1e-9)))
    pool.close()
    pool.join()
    print("small entries done\t%.0f s" % (time.time() - t0))
    results += bigres.get()
    bigpool.join()
    print("big entries done\t%.0f s" % (time.time() - t0))
    wall = time.time() - t0
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)   # both pools reaped, so this counts them
    with open(os.path.join(OUT, "timing.tsv"), "w") as f:
        f.write("id\tseconds\tgb\tretries\n")
        for line, dt, gb, nr in sorted(results):
            f.write("%s\t%.2f\t%.4f\t%d\n" % (line.split("\t", 1)[0], dt, gb, nr))
    S_retries = sum(x[3] for x in results)
    for line, dt, gb, _ in results:
        lines.append(line)
        slow.append((dt, line.split("\t", 1)[0]))
    lines.sort()
    body = "\t".join(COLS) + "\n" + "\n".join(lines) + "\n"
    with open(os.path.join(OUT, "entries.tsv"), "w") as f:
        f.write(body)
    slow.sort(reverse=True)
    print("slowest entries\t%s" % " ".join("%s:%.0fs" % (p, d) for d, p in slow[:8]))

    # ------------------------------------------------------------ tally
    R = [dict(zip(COLS, l.split("\t"))) for l in lines]
    def n(pred):
        return sum(1 for x in R if pred(x))
    S = {}
    S["entries"] = len(R)
    S["cif_ok"] = n(lambda x: x["cif_err"] == "0")
    S["cif_err"] = n(lambda x: x["cif_err"] != "0")
    S["block_eq_id"] = n(lambda x: x["block"].lower() == x["id"])
    S["atoms_eq_atom_site"] = n(lambda x: x["cif_err"] == "0" and x["n_atoms"] == x["n_atom_site"])
    S["models_eq_ids"] = n(lambda x: x["cif_err"] == "0" and x["n_models"] == x["n_model_ids"])
    S["atoms_total"] = sum(int(x["n_atoms"]) for x in R if x["n_atoms"] != "-")
    S["pdb_present"] = n(lambda x: x["pdb_err"] != "-")
    S["pdb_ok"] = n(lambda x: x["pdb_err"] == "0")
    S["pdb_equal"] = n(lambda x: x["pdb_equal"] == "1")
    S["pdb_differ"] = n(lambda x: x["pdb_equal"] == "0")
    S["pdb_reordered"] = n(lambda x: x["pdb_equal"] == "2")
    u1 = {}
    for x in R:
        if x["pdb_unit1"] != "-":
            for kv in x["pdb_unit1"].split(","):
                k, v = kv.split(":")
                u1.setdefault(k, [0, 0])
                u1[k][0] += int(v)
                u1[k][1] += 1
    for k in sorted(u1):
        S["pdb_one_unit %s lines|entries" % k] = "%d|%d" % tuple(u1[k])
    S["sf_present"] = n(lambda x: x["sf_err"] != "-")
    S["sf_ok"] = n(lambda x: x["sf_err"] == "0")
    S["sf_reflections"] = sum(int(x["sf_refl"]) for x in R if x["sf_refl"] != "-")
    S["sf_blocks"] = sum(int(x["sf_blocks"]) for x in R if x["sf_blocks"] != "-")
    S["sf_cell_eq_model"] = n(lambda x: x["sf_err"] == "0" and x["sf_cell_maxdiff"] != "-" and float(x["sf_cell_maxdiff"]) == 0.0)
    S["sf_cell_le_1e-3"] = n(lambda x: x["sf_err"] == "0" and x["sf_cell_maxdiff"] != "-" and float(x["sf_cell_maxdiff"]) <= 1e-3)
    S["sg_num_recorded"] = n(lambda x: x["sg_num_rec"] not in ("-",) and x["sg_num_rec"].isdigit())
    S["sg_num_agree"] = n(lambda x: x["sg_num_rec"].isdigit() and str(x["sg_num_gemmi"]) == x["sg_num_rec"])
    S["sg_unknown_to_gemmi"] = n(lambda x: x["sg_hm"] != "-" and x["sg_num_gemmi"] == "-")
    S["entity_nmol_entries_bad"] = n(lambda x: x["entity_nmol_bad"] not in ("-", "0"))
    S["poly_wt_entities"] = sum(int(x["poly_wt_n"]) for x in R if x["poly_wt_n"] != "-")
    wts = sorted(float(x["poly_wt_maxrel"]) for x in R if x["poly_wt_maxrel"] != "-")
    S["poly_wt_entries_within_1e-3"] = sum(1 for v in wts if v <= 1e-3)
    S["poly_wt_entries"] = len(wts)
    for q in (0.5, 0.99, 0.999):
        S["poly_wt_maxrel_p%g" % (q * 100)] = "%.3e" % wts[min(len(wts) - 1, int(q * len(wts)))] if wts else "-"
    # resolution: the SF file's own Miller indices and cell vs the archive's _refine.ls_d_res_high
    dd, nobs, nfree, cmp_n = [], 0, 0, 0
    for x in R:
        if x["sf_dmin0"] != "-" and x["d_high"] != "-":
            try:
                dd.append(abs(float(x["sf_dmin0"]) - float(x["d_high"])))
            except ValueError:
                pass
        if x["sf_obs0"] != "-" and x["n_obs_rec"].isdigit():
            cmp_n += 1
            nobs += int(x["sf_obs0"]) == int(x["n_obs_rec"])
            if x["n_free_rec"].isdigit():
                nfree += int(x["sf_free0"]) == int(x["n_free_rec"])
    dd.sort()
    S["dmin_compared"] = len(dd)
    S["dmin_within_0.01"] = sum(1 for v in dd if v <= 0.01 + 1e-9)
    S["dmin_within_0.1"] = sum(1 for v in dd if v <= 0.1 + 1e-9)
    S["nobs_compared"] = cmp_n
    S["nobs_exact"] = nobs
    S["nfree_exact"] = nfree
    S["fetch_retries"] = S_retries
    S["wall_s"] = "%.1f" % wall
    S["child_cpu_s"] = "%.1f" % (ru.ru_utime + ru.ru_stime)
    S["avg_cores"] = "%.2f" % ((ru.ru_utime + ru.ru_stime) / max(wall, 1e-9))
    S["child_maxrss_mib"] = "%.0f" % (ru.ru_maxrss / 1024)
    S["gb_read"] = "%.2f" % gb_total

    # the space groups the archive actually uses: order and closure, the recipe's axiom at scale
    names = sorted(set(x["sg_hm"] for x in R if x["sg_num_gemmi"] != "-"))
    groups, bad = 0, []
    sgl = []
    for nm in names:
        sg = gemmi.find_spacegroup_by_name(nm.replace("_", " "))
        order, out = closure(sg)
        groups += 1
        sgl.append("%s\t%d\t%d\t%d" % (nm, sg.number, order, out))
        if out != 0:
            bad.append(nm)
    S["sg_distinct"] = groups
    S["sg_closure_fail"] = len(bad)
    with open(os.path.join(OUT, "spacegroups.tsv"), "w") as f:
        f.write("hm\tnumber\torder\toutside\n" + "\n".join(sgl) + "\n")

    # error classes
    ec = {}
    for x in R:
        for k in ("cif_err", "pdb_err", "sf_err"):
            if x[k] not in ("0", "-"):
                c = "%s %s" % (k, x[k].split(":")[0])
                ec[c] = ec.get(c, 0) + 1
    for k, v in sorted(ec.items()):
        S["errclass " + k] = v

    # every entry that breaks a claim, with why, so a failed run is diagnosable from its outputs
    with open(os.path.join(OUT, "exceptions.tsv"), "w") as f:
        f.write("id\tclaim\tdetail\n")
        for x in R:
            for k in ("cif_err", "pdb_err", "sf_err"):
                if x[k] not in ("0", "-"):
                    f.write("%s\t%s\t%s\n" % (x["id"], k, x[k]))
            if x["pdb_equal"] in ("0", "2"):
                f.write("%s\t%s\t%s\n" % (x["id"], "pdb_differ" if x["pdb_equal"] == "0" else "pdb_reordered (ok)", x["pdb_diff"]))
            if x["cif_err"] == "0" and x["n_atoms"] != x["n_atom_site"]:
                f.write("%s\tatoms\t%s vs %s\n" % (x["id"], x["n_atoms"], x["n_atom_site"]))
            if x["cif_err"] == "0" and x["n_models"] != x["n_model_ids"]:
                f.write("%s\tmodels\t%s vs %s\n" % (x["id"], x["n_models"], x["n_model_ids"]))
            if x["sg_num_rec"].isdigit() and str(x["sg_num_gemmi"]) != x["sg_num_rec"]:
                f.write("%s\tsg_num\t%s %s vs %s\n" % (x["id"], x["sg_hm"], x["sg_num_gemmi"], x["sg_num_rec"]))
            if x["entity_nmol_bad"] not in ("-", "0"):
                f.write("%s\tentity_nmol\t%s\n" % (x["id"], x["entity_nmol_bad"]))
            if x["block"].lower() != x["id"] and x["cif_err"] == "0":
                f.write("%s\tblock\t%s\n" % (x["id"], x["block"]))

    with open(os.path.join(OUT, "summary.tsv"), "w") as f:
        for k, v in S.items():
            f.write("%s\t%s\n" % (k, v))
            print("%s\t%s" % (k, v))
    print("entries.tsv sha256\t%s" % hashlib.sha256(body.encode()).hexdigest())

    # ------------------------------------------------------------ asserts (finalised from the probe)
    fails = []
    def check(name, ok):
        print("%s\t%s" % ("PASS" if bool(ok) else "FAIL", name))
        if not bool(ok):
            fails.append(name)
    if SAMPLE == 1:
        check("the archive's published count: 246,905 entries", S["entries"] == 246905)
    def ids(pred):
        return set(x["id"] for x in R if pred(x))

    def known(claim, got):
        extra, gone = sorted(got - set(KNOWN[claim])), sorted(set(KNOWN[claim]) - got)
        print("%s\tbreaking %d (known %d)%s%s" % (claim, len(got), len(KNOWN[claim]),
              "  NEW: " + " ".join(extra[:20]) if extra else "", "  GONE: " + " ".join(gone) if gone else ""))
        return not extra and not gone
    full = SAMPLE == 1
    check("every mmCIF matches its pin and gemmi reads it", S["cif_err"] == 0)
    check("every block is named for its entry", S["block_eq_id"] == S["entries"])
    check("gemmi's atom count == the _atom_site rows, every entry", S["atoms_eq_atom_site"] == S["cif_ok"])
    check("gemmi's models == the distinct model numbers, every entry", S["models_eq_ids"] == S["cif_ok"])
    check("every PDB-format file matches its pin and gemmi reads it", S["pdb_ok"] == S["pdb_present"])
    sf_bad = ids(lambda x: x["sf_err"] not in ("0", "-"))
    check("every structure-factor file matches its pin; gemmi reads all but the known-malformed",
          known("sf_err", sf_bad) if full else sf_bad <= set(KNOWN["sf_err"]))
    check("...and the malformed ones fail on parsing, never on the pin or the transport",
          not any(("etag" in x["sf_err"] or "size" in x["sf_err"] or "HTTP" in x["sf_err"]) for x in R if x["id"] in sf_bad))
    check("every space group in the archive is closed under composition", S["sg_closure_fail"] == 0)
    check("gemmi's space-group number == the recorded Int_Tables_number, every symbol gemmi knows",
          S["sg_num_agree"] == S["sg_num_recorded"] - n(lambda x: x["sg_num_rec"].isdigit() and x["sg_num_gemmi"] == "-"))
    sg_unk = ids(lambda x: x["sg_hm"] != "-" and x["sg_num_gemmi"] == "-")
    check("the symbols gemmi does not know are the known non-standard ones",
          known("sg_unknown", sg_unk) if full else sg_unk <= set(KNOWN["sg_unknown"]))
    ent = ids(lambda x: x["entity_nmol_bad"] not in ("-", "0"))
    check("every entity's recorded molecule count == its asym units (water aside), but the known",
          known("entity_nmol", ent) if full else ent <= set(KNOWN["entity_nmol"]))
    pdiff = ids(lambda x: x["pdb_equal"] == "0")
    check("every PDB-format file == its mmCIF through gemmi, but for one-unit rounding and the known",
          known("pdb_differ", pdiff) if full else pdiff <= set(KNOWN["pdb_differ"]))
    if fails:
        print("GEMMI REAL FAILED: %s" % "; ".join(fails))
        sys.exit(1)
    print("GEMMI REAL OK")


if __name__ == "__main__":
    main()
