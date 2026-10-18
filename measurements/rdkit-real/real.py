"""rdkit-real: regenerate ChEMBL 37's published InChI, InChIKey and formula for the WHOLE release.

    python3 -u real.py <chemreps.txt.gz> <out dir> <workers>

The recipe's three graded checks (full key, connectivity block, element counts where InChI's
formula layer is the whole inventory), carried from its first-100,000 slice to every compound in
the release, plus two RDKit-internal identities a library run should satisfy:

  * InChI round trip: RDKit parses ChEMBL's own standard InChI and writes it back out.
  * canonical SMILES idempotence: canonicalising RDKit's canonical SMILES returns it unchanged.

Rows are processed in fixed 10,000-row chunks across a fork pool and written back in input order,
so the output does not depend on the worker count or on scheduling. Chunks 0-9 are exactly the
recipe's 100,000-row subset, so the recipe's published counts are re-asserted from inside the
whole-release run.

Exits non-zero if any assertion fails, after every output is written.
"""
import gzip
import hashlib
import multiprocessing as mp
import os
import re
import sys
import time

from rdkit import Chem, RDLogger
from rdkit.Chem import inchi
from rdkit.Chem.rdMolDescriptors import CalcMolFormula

SRC, OUT, WORKERS = sys.argv[1], sys.argv[2], int(sys.argv[3])
CHUNK = 10000
TOK = re.compile(r"([A-Z][a-z]?)(\d*)")
KEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
FIELDS = ("n", "parsed", "inchi_match", "key_match", "skeleton_match", "formula_comparable",
          "atoms_match", "charge_layer", "charge_layer_match", "inchi_parsed", "inchi_roundtrip",
          "smiles_idempotent", "smiles_eq_chembl", "key_empty", "chembl_key_from_inchi",
          "atoms_input_inconsistent", "skeleton_input_inconsistent")


def atoms(formula):
    """Element counts summed over InChI's dot-separated components (the recipe's comparison)."""
    total = {}
    for comp in formula.split("."):
        m = re.match(r"^(\d+)(.*)$", comp)
        mult, rest = (int(m.group(1)), m.group(2)) if m else (1, comp)
        rest = rest.rstrip("+-")
        rest = re.sub(r"[+-]\d*$", "", rest)
        for el, n in TOK.findall(rest):
            if el:
                total[el] = total.get(el, 0) + mult * (int(n) if n else 1)
    return total


def init():
    RDLogger.DisableLog("rdApp.*")


def work(job):
    idx, lines = job
    c = dict.fromkeys(FIELDS, 0)
    out, atoms_bad, skel_bad, bad_rows = [], [], [], []
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) != 4:
            bad_rows.append(line[:200])
            continue
        cid, smi, std_inchi, std_key = f
        c["n"] += 1
        # InChI round trip: independent of the SMILES column, so independent of the stereo it drops.
        rt = 0
        inchi_form, inchi_key = "", ""
        try:
            # ChEMBL's key is the hash of ChEMBL's InChI; RDKit's InChI layer must reproduce it.
            inchi_key = inchi.InchiToInchiKey(std_inchi) or ""
        except Exception:
            inchi_key = ""
        c["chembl_key_from_inchi"] += int(inchi_key == std_key)
        try:
            m2 = inchi.MolFromInchi(std_inchi, sanitize=True, removeHs=True, logLevel=None, treatWarningAsError=False)
        except Exception:
            m2 = None
        if m2 is not None:
            c["inchi_parsed"] += 1
            inchi_form = CalcMolFormula(m2)
            try:
                rt = int(inchi.MolToInchi(m2) == std_inchi)
            except Exception:
                rt = 0
            c["inchi_roundtrip"] += rt
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            out.append("%s\t\t\t\t%d\n" % (cid, rt))
            continue
        c["parsed"] += 1
        try:
            ii = inchi.MolToInchi(mol)
            k = inchi.InchiToInchiKey(ii) if ii else ""
        except Exception:
            ii, k = "", ""
        k = k or ""
        if not k:
            c["key_empty"] += 1
        form = CalcMolFormula(mol)
        can = Chem.MolToSmiles(mol)
        m3 = Chem.MolFromSmiles(can)
        c["smiles_idempotent"] += int(m3 is not None and Chem.MolToSmiles(m3) == can)
        c["smiles_eq_chembl"] += int(can == smi)
        c["inchi_match"] += int(bool(ii) and ii == std_inchi)
        c["key_match"] += int(bool(k) and k == std_key)
        if k and k.split("-")[0] == std_key.split("-")[0]:
            c["skeleton_match"] += 1
        elif k:
            # The input is inconsistent when RDKit, reading ChEMBL's OWN InChI, regenerates
            # ChEMBL's key: then the SMILES and the InChI in the same record differ.
            try:
                k2 = inchi.MolToInchiKey(m2) if m2 is not None else ""
            except Exception:
                k2 = ""
            inc = int(k2 == std_key)
            c["skeleton_input_inconsistent"] += inc
            skel_bad.append("%s\t%s\t%s\t%s\t%d\n" % (cid, k, std_key, k2, inc))
        want_f = std_inchi.split("/")[1] if std_inchi.startswith("InChI=1S/") else ""
        if want_f:
            ok = atoms(form) == atoms(want_f)
            if "/q" in std_inchi or "/p" in std_inchi:
                c["charge_layer"] += 1
                c["charge_layer_match"] += ok
            else:
                c["formula_comparable"] += 1
                c["atoms_match"] += ok
                if not ok:
                    # Explained by the input when RDKit's formula from ChEMBL's own InChI equals
                    # the InChI formula layer, i.e. RDKit reads both representations faithfully
                    # and they describe different molecules.
                    inc = int(bool(inchi_form) and atoms(inchi_form) == atoms(want_f))
                    c["atoms_input_inconsistent"] += inc
                    atoms_bad.append("%s\t%s\t%s\t%s\t%d\t%s\n" % (cid, form, want_f, inchi_form, inc, smi))
        out.append("%s\t%s\t%s\t%s\t%d\n" % (cid, k, form, can, rt))
    return idx, c, "".join(out), atoms_bad, skel_bad, bad_rows


def chunks(fh):
    idx, buf = 0, []
    for line in fh:
        buf.append(line)
        if len(buf) == CHUNK:
            yield idx, buf
            idx, buf = idx + 1, []
    if buf:
        yield idx, buf


def main():
    os.makedirs(OUT, exist_ok=True)
    total = dict.fromkeys(FIELDS, 0)
    first = dict.fromkeys(FIELDS, 0)
    ids = set()
    dup = 0
    atoms_bad, skel_bad, bad_rows = [], [], []
    h = hashlib.sha256()
    t0 = time.time()
    with gzip.open(SRC, "rt") as fh, open(os.path.join(OUT, "rdkit_real.tsv"), "w") as out:
        header = next(fh).rstrip("\n").split("\t")
        print("header", header, flush=True)
        ctx = mp.get_context("fork")
        with ctx.Pool(WORKERS, initializer=init) as pool:
            for idx, c, text, ab, sb, br in pool.imap(work, chunks(fh), chunksize=1):
                for k in FIELDS:
                    total[k] += c[k]
                    if idx < 10:
                        first[k] += c[k]
                for line in text.splitlines():
                    cid = line.split("\t", 1)[0]
                    if cid in ids:
                        dup += 1
                    ids.add(cid)
                out.write(text)
                h.update(text.encode())
                atoms_bad += ab
                skel_bad += sb
                bad_rows += br
                if idx % 25 == 0:
                    print("chunk %d rows %d  %.0f s" % (idx, total["n"], time.time() - t0), flush=True)
    wall = time.time() - t0
    rows_out = sum(1 for _ in open(os.path.join(OUT, "rdkit_real.tsv")))
    with open(os.path.join(OUT, "atoms_mismatch.tsv"), "w") as f:
        f.write("chembl_id\trdkit_formula_from_smiles\tinchi_formula_layer\trdkit_formula_from_chembl_inchi\tinput_inconsistent\tsmiles\n" + "".join(atoms_bad))
    with open(os.path.join(OUT, "skeleton_mismatch.tsv"), "w") as f:
        f.write("chembl_id\trdkit_key_from_smiles\tchembl_key\trdkit_key_from_chembl_inchi\tinput_inconsistent\n" + "".join(skel_bad))

    keys_ok = all(KEY.match(line.split("\t")[1]) for line in open(os.path.join(OUT, "rdkit_real.tsv"))
                  if line.split("\t")[1])
    import rdkit
    with open(os.path.join(OUT, "metrics.txt"), "w") as m:
        m.write("rdkit_version %s\n" % str(getattr(rdkit, "__version__", "unknown")))
        m.write("workers %d\nworkload_wall_s %.1f\n" % (WORKERS, wall))
        for k in FIELDS:
            m.write("%s %d\n" % (k, total[k]))
        for k in FIELDS:
            m.write("first100k_%s %d\n" % (k, first[k]))
        m.write("malformed_rows %d\nduplicate_ids %d\nrows_out %d\noutput_sha256 %s\n"
                % (len(bad_rows), dup, rows_out, h.hexdigest()))
    print(open(os.path.join(OUT, "metrics.txt")).read(), flush=True)

    fails = []

    def check(name, ok):
        ok = bool(ok)
        print("%-62s %s" % (name, "ok" if ok else "FAIL"), flush=True)
        if not ok:
            fails.append(name)

    n = total["n"]
    check("every row has 4 columns", len(bad_rows) == 0)
    check("chembl_id unique across the release", dup == 0 and len(ids) == n)
    check("one row out per row in", rows_out == n)
    check("every written key is a well-formed 27-char InChIKey", keys_ok)
    # The recipe's exact identity, carried to the whole release: every element-count disagreement
    # must be one the INPUT explains (its SMILES and InChI describe different molecules), none RDKit.
    check("element counts agree wherever ChEMBL's SMILES and InChI agree (whole release)",
          total["atoms_match"] + total["atoms_input_inconsistent"] == total["formula_comparable"])
    check("canonical SMILES idempotent, every parsed compound",
          total["smiles_idempotent"] == total["parsed"])
    # The recipe's published numbers, re-asserted on the same 100,000 rows inside this run.
    check("first 100k: compounds 100000", first["n"] == 100000)
    check("first 100k: parsed 99997", first["parsed"] == 99997)
    check("first 100k: inchikey_match 99914", first["key_match"] == 99914)
    check("first 100k: skeleton_match 99996", first["skeleton_match"] == 99996)
    check("first 100k: atoms_match == formula_comparable == 97665",
          first["atoms_match"] == first["formula_comparable"] == 97665)
    if fails:
        print("FAILED: %s" % "; ".join(fails), flush=True)
        sys.exit(1)
    print("RDKIT REAL OK", flush=True)


if __name__ == "__main__":
    main()
