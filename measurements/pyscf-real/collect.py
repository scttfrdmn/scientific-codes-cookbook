"""Compare the three chips' PySCF outputs molecule by molecule, and each chip's PySCF energies with
Graviton4's Psi4 (Psi4's linux-64 twin does not import; see the README).
    python3 collect.py <dir>   (holding arm64/, x86_64/, amd/ with pyscf.jsonl; arm64/ also psi4.jsonl)"""
import json
import os
import sys

D = sys.argv[1]
CHIPS = ["arm64", "x86_64", "amd"]
FIELDS = ["b3lyp_e", "b3lyp_homo", "b3lyp_lumo", "b3lyp_mu", "rhf", "mp2_corr", "ccsd_corr", "t_corr",
          "rhf_rotated", "lib_e"]
WFN = ["rhf", "mp2_corr", "ccsd_corr", "t_corr"]


def load(chip, tool):
    out = {}
    for line in open(os.path.join(D, chip, tool + ".jsonl")):
        r = json.loads(line)
        assert r["idx"] not in out, (chip, tool, r["idx"])
        out[r["idx"]] = r
    return out


py = {c: load(c, "pyscf") for c in CHIPS}
ids = set(py["arm64"])
assert all(set(py[c]) == ids for c in CHIPS), "different molecule sets"
print("== PySCF, %d molecules on each chip ==" % len(ids))
for a, b in [("x86_64", "arm64"), ("amd", "arm64"), ("x86_64", "amd")]:
    for f in FIELDS:
        pairs = [(py[a][i][f], py[b][i][f]) for i in ids if f in py[a][i] and f in py[b][i]]
        d = [abs(x - y) for x, y in pairs]
        rel = [abs(x - y) / abs(y) for x, y in pairs if y]
        same = sum(1 for x, y in pairs if x == y)
        print("%-6s vs %-6s %-11s n=%4d  max|d| %.2e  max rel %.1e  bit-identical %4d/%d"
              % (a, b, f, len(pairs), max(d), max(rel), same, len(pairs)))
ps = load("arm64", "psi4")
assert set(ps) == ids
print("\n== each chip's PySCF against Graviton4's Psi4 ==")
for c in CHIPS:
    for f in WFN:
        d = [abs(py[c][i][f] - ps[i][f]) for i in ids if f in py[c][i] and f in ps[i]]
        print("PySCF %-6s vs Psi4 arm64  %-9s n=%4d  max|d| %.2e" % (c, f, len(d), max(d)))
