import psi4, json
psi4.core.set_output_file("/tmp/psi4.out", False)
psi4.set_memory("500 MB")
psi4.set_options({"scf_type": "pk"})   # exact integrals: Psi4 defaults to density fitting (DF), which shifts H2/STO-3G by ~2.4e-5 Ha; PK makes the PySCF cross-check like-with-like
psi4.geometry("0 1\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\nunits angstrom\n")
e = psi4.energy("scf/sto-3g")
json.dump({"scf_energy": e}, open("/tmp/energy.json", "w"))
print("SCF_ENERGY", repr(e))
print("psi4", getattr(psi4, "__version__", "unknown"))
