"""QM9 (Ramakrishnan et al., Sci. Data 1, 140022, 2014): read the published tar, choose the stratum,
and build Gaussian 09's 6-31G(2df,p) in PySCF. Shared by pyscf_run.py and psi4_run.py."""
import os
import tarfile

# Columns of the property line (QM9 readme.txt).
COLS = ["tag", "idx", "A", "B", "C", "mu", "alpha", "homo", "lumo", "gap", "r2", "zpve", "U0", "U", "H", "G", "Cv"]


def parse(text):
    L = text.splitlines()
    na = int(L[0])
    p = L[1].split()
    rec = {k: (int(v) if k == "idx" else (v if k == "tag" else float(v))) for k, v in zip(COLS, p)}
    atoms = []
    for line in L[2:2 + na]:
        f = line.replace("*^", "e").split()
        atoms.append((f[0], (float(f[1]), float(f[2]), float(f[3]))))
    rec["atoms"] = atoms
    rec["ha"] = sum(1 for s, _ in atoms if s != "H")
    rec["smiles"] = L[na + 3].split()[0] if len(L) > na + 3 else ""
    return rec


def uncharacterized(qm9_dir):
    out = set()
    for line in open(os.path.join(qm9_dir, "uncharacterized.txt")):
        f = line.split()
        if f and f[0].isdigit():
            out.add(int(f[0]))
    return out


def load(qm9_dir, max_ha):
    """Every characterized QM9 molecule with at most max_ha heavy atoms, by index. Also returns the
    per-stratum counts over the whole release so a run can show it read all 133,885 records."""
    unc = uncharacterized(qm9_dir)
    mols, seen, strata = {}, 0, {}
    with tarfile.open(os.path.join(qm9_dir, "dsgdb9nsd.xyz.tar.bz2")) as t:
        for m in t:
            if not m.isfile():
                continue
            d = parse(t.extractfile(m).read().decode())
            seen += 1
            s = strata.setdefault(d["ha"], [0, 0])
            s[0] += 1
            if d["idx"] in unc:
                s[1] += 1
                continue
            if d["ha"] <= max_ha:
                if d["idx"] in mols:
                    raise SystemExit("duplicate QM9 index %d" % d["idx"])
                mols[d["idx"]] = d
    return mols, seen, strata, len(unc)


def gaussian_6_31g_2df_p(elements):
    """Gaussian 09's 6-31G(2df,p). PySCF's (and the Basis Set Exchange's) library entry of the same
    name takes its 2d exponents from 6-311G(2df) -- C 1.252/0.313 -- while Gaussian builds them as
    2x and 1/2x the 6-31G* d exponent, 0.8 -> 1.6/0.4, for every first-row atom. The sp part, the
    f exponent and hydrogen's p are the same in both."""
    from pyscf import gto
    out = {}
    for el in set(elements):
        b = gto.basis.load("6-31g(2df,p)", el)
        if el == "H":
            out[el] = b
            continue
        out[el] = [s for s in b if s[0] < 2] + [[2, [1.6, 1.0]], [2, [0.4, 1.0]]] + [s for s in b if s[0] == 3]
    return out


def x_6d7f(mol):
    """Columns spanning Gaussian's 6D/7F space inside PySCF's all-Cartesian AO basis: identity on the
    s, p and (Cartesian) d shells, PySCF's own Cartesian->spherical block on each f shell."""
    import numpy as np
    assert mol.cart
    c = mol.cart2sph_coeff()
    lc, ls = mol.ao_loc_nr(cart=True), mol.ao_loc_nr(cart=False)
    nc = lc[-1]
    blocks = []
    for ib in range(mol.nbas):
        if mol.bas_angular(ib) == 3:
            blocks.append(c[:, ls[ib]:ls[ib + 1]])
        else:
            e = np.zeros((nc, lc[ib + 1] - lc[ib]))
            e[lc[ib]:lc[ib + 1]] = np.eye(lc[ib + 1] - lc[ib])
            blocks.append(e)
    return np.hstack(blocks)


def restrict_to(mf, X):
    """Run mf's SCF inside span(X): PySCF diagonalises the Fock matrix in, and takes its DIIS error
    vectors from, whatever orthonormal basis check_linear_dependency returns (hf.kernel's x_orth)."""
    import numpy as np

    def cld(s, verbose=None):
        e, v = np.linalg.eigh(X.T @ s @ X)
        return X @ (v / np.sqrt(e))
    mf.check_linear_dependency = cld
    return mf
