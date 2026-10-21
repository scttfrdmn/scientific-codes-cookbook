import json, sys
fail = []
def check(label, ok, detail):
    print(f"{label:24s} {detail}")
    if not ok: fail.append(label)

e = json.load(open("/tmp/energy.json"))["scf_energy"]
# H2 at 0.74 A in a minimal basis: the textbook RHF/STO-3G total energy is
# -1.1167 Hartree -- a well-known literature value, so this is a reference identity,
# not a self-consistent band. psi4 computes it through its own native integral/SCF
# stack (an independent kernel from gpaw's in the same env).
check("scf_energy", -1.1180 < e < -1.1150, f"{e:.6f} Ha  want -1.1180..-1.1150 (textbook RHF/STO-3G H2 = -1.1167)")
# SCF must have converged -- psi4 raises on non-convergence, but confirm the output says so
out = open("/tmp/psi4.out").read()
conv = ("converged" in out.lower()) or ("Energy and wave function converged" in out)
check("scf_converged", conv, f"{'yes' if conv else 'NO'}  want a converged-SCF line")
check("energy_is_float", isinstance(e, float), f"{type(e).__name__}")
if fail: print("FAILED: " + ", ".join(fail)); sys.exit(1)
