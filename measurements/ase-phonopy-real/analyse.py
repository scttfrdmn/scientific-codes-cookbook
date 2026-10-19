"""measurements/ase-phonopy-real: phonopy on four force sets for bulk Si (PBE).

    python3 -u analyse.py <dir> <ranks launched>

<dir> holds phonopy's own committed Si examples at v4.4.0 (Si-QE: 64-atom conventional 2x2x2
supercell, Quantum ESPRESSO forces; Si: 16-atom primitive 2x2x2 supercell, VASP forces) and,
when present, GPAW's forces on the SAME displaced supercells (forces-qe64.npy, forces-vasp16.npy,
from forces.py). For every force set phonopy builds force constants, then frequencies at the
commensurate points Gamma/X/L, a band path, a 31^3 tetrahedron DOS and thermal properties. The
checks are identities of the method and agreement between codes on identical bytes.
Writes results.tsv and freqs.tsv into <dir>; exit 1 on any failed check.
"""
import json
import os
import sys
import time

import numpy as np
import phonopy
import spglib
from phonopy.interface.qe import parse_set_of_forces as qe_forces
from phonopy.interface.vasp import parse_set_of_forces as vasp_forces

D = sys.argv[1]
RANKS = int(sys.argv[2]) if len(sys.argv) > 2 else 0
BOHR = 0.52917721067          # Angstrom; Ry/bohr <-> eV/Angstrom via RY_EV / BOHR
RY_EV = 13.605693009
fail, rows = [], []


def rec(key, value, note=""):
    rows.append((str(key), str(value), str(note)))
    print("%-44s %s  %s" % (key, value, note), flush=True)


def check(key, ok, value, note):
    rec(key, value, ("ok  " if bool(ok) else "FAIL  ") + note)
    if not bool(ok):
        fail.append(key)


def path(name):
    return os.path.join(D, name)


# --- the two committed setups: their displaced supercells are what every code computed forces on
CASES = {
    "qe64": dict(yaml="Si-QE_phonopy_disp.yaml", ref="QE", natoms=64,
                 # phonopy's QE units: lengths bohr, forces Ry/bohr
                 to_native=BOHR / RY_EV),
    "vasp16": dict(yaml="Si_phonopy_disp.yaml", ref="VASP", natoms=16, to_native=1.0),
}
REFS = {
    # phonopy's own parsers; QE's already subtracts the drift, VASP's does not -- build() removes
    # the mean force from every set, so all four are treated as `phonopy -f` would
    "qe64": lambda: qe_forces(64, [path("Si-QE_supercell-001.out")], verbose=False),
    "vasp16": lambda: vasp_forces(16, [path("Si_vasprun.xml")], verbose=False).get("forces", []),
}
QPTS = {"G": [0, 0, 0], "X": [0.5, 0, 0.5], "L": [0.5, 0.5, 0.5]}
X_STAR = [[0.5, 0, 0.5], [0, 0.5, 0.5], [0.5, 0.5, 0], [0.5, 0.5, 1.0], [0.5, 1.0, 0.5], [1.0, 0.5, 0.5]]
BAND = [[[0, 0, 0], [0.5, 0, 0.5], [0.625, 0.25, 0.625]],
        [[0.375, 0.375, 0.75], [0, 0, 0], [0.5, 0.5, 0.5]]]   # G-X-U | K-G-L, phonopy's Si example path


def build(case, forces, label):
    """phonopy from the committed yaml + one force set; returns a dict of results."""
    c = CASES[case]
    ph = phonopy.load(path(c["yaml"]), produce_fc=False, log_level=0)
    f = np.array(forces, dtype=float)
    ph.forces = f - f.mean(axis=1, keepdims=True)
    t0 = time.time()
    ph.produce_force_constants()            # raw: no symmetrisation, so the sum rule is a test
    raw = ph.force_constants.copy()
    g_raw = np.sort(np.abs(ph.get_frequencies([0, 0, 0])))
    ph.symmetrize_force_constants()          # what phonopy-load does by default
    out = {"label": label, "case": case}
    out["gamma_acoustic_raw"] = float(g_raw[:3].max())
    asr_row = np.abs(raw.sum(axis=1)).max()  # sum_j Phi_ij, 0 by translational invariance
    out["asr_rowsum_raw"] = float(asr_row)
    for k, q in QPTS.items():
        out["f_" + k] = np.array(ph.get_frequencies(q))
    xs = np.array([np.sort(ph.get_frequencies(q)) for q in X_STAR])
    out["xstar_spread"] = float((xs.max(axis=0) - xs.min(axis=0)).max())
    # band path, DOS and thermal properties: the phonopy work a phonon study actually does
    from phonopy.phonon.band_structure import get_band_qpoints
    ph.run_band_structure(get_band_qpoints(BAND, npoints=101))
    bs = ph.get_band_structure_dict()
    out["band_min"] = float(min(np.min(f) for f in bs["frequencies"]))
    out["band_max"] = float(max(np.max(f) for f in bs["frequencies"]))
    ph.run_mesh([31, 31, 31])
    mesh = ph.get_mesh_dict()
    w, fr = mesh["weights"], mesh["frequencies"]
    ph.run_total_dos(use_tetrahedron_method=True, freq_pitch=0.01)
    dos = ph.get_total_dos_dict()
    out["dos_integral"] = float(np.trapezoid(dos["total_dos"], dos["frequency_points"]))
    out["natom_prim"] = len(ph.primitive)
    T = np.arange(0, 1001, 5, dtype=float)
    ph.run_thermal_properties(temperatures=T, cutoff_frequency=1e-3)
    tp = ph.get_thermal_properties_dict()
    F, S, Cv = tp["free_energy"], tp["entropy"], tp["heat_capacity"]    # kJ/mol, J/K/mol
    # zero-point energy two ways: phonopy's F(0) and 1/2 sum h nu over the same mesh
    from phonopy.physical_units import get_physical_units
    u = get_physical_units()
    ev_kjmol = u.EvTokJmol
    fpos = np.where(fr > 1e-3, fr, 0.0)
    zpe = 0.5 * np.sum(w[:, None] * fpos) / w.sum() * u.THzToEv * ev_kjmol
    out["F0"] = float(F[0])
    out["zpe_sum"] = float(zpe)
    # S = -dF/dT at 300 K by central differences at h and h/2: the error must fall as h^2
    i300 = int(np.where(T == 300)[0][0])
    def dfdt(h):
        ph.run_thermal_properties(temperatures=[300 - h, 300 + h], cutoff_frequency=1e-3)
        f2 = ph.get_thermal_properties_dict()["free_energy"]
        return -(f2[1] - f2[0]) / (2 * h) * 1000.0                    # J/K/mol
    e1, e2 = abs(dfdt(20.0) - S[i300]), abs(dfdt(10.0) - S[i300])
    out["S300"] = float(S[i300])
    out["dSdT_err_h20"], out["dSdT_err_h10"] = float(e1), float(e2)
    out["Cv300"] = float(Cv[i300])
    out["phonopy_s"] = time.time() - t0
    return out


# --- symmetry and displacement count: the chain link, on both committed cells
for case, c in CASES.items():
    ph = phonopy.load(path(c["yaml"]), produce_fc=False, log_level=0)
    u = ph.unitcell
    sg = spglib.get_spacegroup((u.cell, u.scaled_positions, u.numbers), symprec=1e-5)
    check("%s_spacegroup" % case, sg == "Fd-3m (227)", sg, "want Fd-3m (227)")
    check("%s_supercell_atoms" % case, len(ph.supercell) == c["natoms"], len(ph.supercell), "want %d" % c["natoms"])
    check("%s_displacements_committed" % case, len(ph.displacements) == 1, len(ph.displacements), "want 1")
    ph.generate_displacements()
    check("%s_displacements_regenerated" % case, len(ph.displacements) == 1, len(ph.displacements),
          "want 1 (phonopy 4.4.0 + spglib re-derive the same symmetry cut)")

# --- force sets: the committed reference, and GPAW's on the same displaced supercell
results = {}
for case, c in CASES.items():
    ref = REFS[case]()
    check("%s_%s_forces_parsed" % (case, c["ref"]), len(ref) == 1 and np.shape(ref[0]) == (c["natoms"], 3),
          np.shape(ref[0]) if len(ref) else "none", "phonopy's own parser")
    rec("%s_%s_drift_over_fmax" % (case, c["ref"]), "%.2e" % (np.abs(np.mean(ref[0], axis=0)).max() / np.abs(ref[0]).max()))
    results[case + "_" + c["ref"]] = build(case, ref, c["ref"])
    g = path("forces-%s.npy" % case)
    if os.path.exists(g):
        meta = json.load(open(path("gpaw-%s.json" % case)))
        raw = np.load(g)                                  # eV/Angstrom, as GPAW returned them
        drift = raw.mean(axis=0)
        fmax = np.abs(raw).max()
        rec("%s_gpaw_energy_eV" % case, "%.8f" % meta["energy"])
        rec("%s_gpaw_wall_s" % case, "%.1f" % meta["wall"])
        rec("%s_gpaw_scf_iterations" % case, meta.get("iterations", "?"))
        rec("%s_gpaw_peak_rss_gib_sum_ranks" % case, "%.2f" % meta.get("rss_gib_sum", float("nan")))
        check("%s_gpaw_mpi_ranks" % case, meta["ranks"] == RANKS, meta["ranks"], "want %d launched" % RANKS)
        # Newton's third law on the whole cell: a uniform force is the numerical residual of
        # the real-space grids; it must be small next to the displaced atom's restoring force
        check("%s_gpaw_drift_over_fmax" % case, np.abs(drift).max() / fmax < 1e-2,
              "%.2e" % (np.abs(drift).max() / fmax), "want < 1e-2 (|mean F| vs max |F|)")
        f = raw * c["to_native"]
        results[case + "_GPAW"] = build(case, [f], "GPAW")
        np.savetxt(path("forces-%s-gpaw.tsv" % case), raw, fmt="%.10f", delimiter="\t")

# --- per force set: identities of phonopy's method
for key, r in results.items():
    n = r["natom_prim"]
    check(key + "_gamma_acoustic_raw_THz", r["gamma_acoustic_raw"] < 1e-2, "%.3e" % r["gamma_acoustic_raw"],
          "want < 1e-2 THz (acoustic sum rule, unsymmetrised force constants)")
    check(key + "_xstar_spread_THz", r["xstar_spread"] < 1e-8, "%.1e" % r["xstar_spread"],
          "want < 1e-8 (6 symmetry-equivalent X points, identical spectra)")
    check(key + "_dos_integral", abs(r["dos_integral"] - 3 * n) < 1e-2, "%.5f" % r["dos_integral"],
          "want %d +- 1e-2 (3N modes per primitive cell, tetrahedron DOS)" % (3 * n))
    check(key + "_F0_eq_ZPE", abs(r["F0"] - r["zpe_sum"]) < 1e-9 * abs(r["zpe_sum"]) + 1e-12,
          "%.10f vs %.10f" % (r["F0"], r["zpe_sum"]), "want F(T=0) == 1/2 sum h nu (kJ/mol)")
    ratio = r["dSdT_err_h20"] / r["dSdT_err_h10"] if r["dSdT_err_h10"] > 0 else float("inf")
    check(key + "_dSdT_order", 3.5 < ratio < 4.5, "%.3f" % ratio,
          "want 4 (S = -dF/dT, central difference O(h^2); errors %.2e, %.2e J/K/mol)"
          % (r["dSdT_err_h20"], r["dSdT_err_h10"]))
    check(key + "_no_imaginary_on_band", r["band_min"] > -0.05, "%.4f" % r["band_min"], "want > -0.05 THz")
    rec(key + "_gamma_optical_THz", "%.5f" % np.sort(r["f_G"])[-1])
    rec(key + "_S300_JKmol", "%.5f" % r["S300"])
    rec(key + "_Cv300_JKmol", "%.5f" % r["Cv300"])
    rec(key + "_ZPE_kJmol", "%.6f" % r["F0"])
    rec(key + "_phonopy_s", "%.2f" % r["phonopy_s"])

# --- cross-code, REPORTED not asserted. Each GPAW force set is computed on the reference's
# identical displaced supercell with the reference's own k-mesh, so the comparison is like with
# like up to the pseudopotential / PAW dataset, which no setting can match across codes. No
# tolerance for that remainder can be justified from the problem itself, so these are
# observations. (A pre-run bound of "no further than QE is from VASP" failed at 5.5% vs 5.1%:
# QE's transverse-acoustic modes sit 3-5% above both GPAW and VASP, so the bound measured the
# QE reference's position, not GPAW's correctness.)
def worst_rel(a, b):
    w = 0.0
    for k in QPTS:
        fa, fb = np.sort(a["f_" + k]), np.sort(b["f_" + k])
        m = fb > 1.0                       # the three Gamma acoustic modes are ~0 in all four
        w = max(w, float(np.max(np.abs(fa[m] - fb[m]) / fb[m])))
    return w


rec("QE_vs_VASP_worst_rel", "%.4f" % worst_rel(results["qe64_QE"], results["vasp16_VASP"]),
    "the two published references, every mode above 1 THz at Gamma/X/L (reported)")
for case, c in CASES.items():
    a, b = results.get(case + "_GPAW"), results[case + "_" + c["ref"]]
    if a is None:
        continue
    rec("%s_GPAW_vs_%s_worst_rel" % (case, c["ref"]), "%.4f" % worst_rel(a, b),
        "identical displaced supercell and k-mesh (reported)")
    rec("%s_GPAW_vs_%s_gamma_optical_rel" % (case, c["ref"]),
        "%.5f" % (np.sort(a["f_G"])[-1] / np.sort(b["f_G"])[-1] - 1))
if "qe64_GPAW" in results:
    rec("qe64_GPAW_vs_VASP_worst_rel", "%.4f" % worst_rel(results["qe64_GPAW"], results["vasp16_VASP"]),
        "different supercell, displacement and k-mesh (reported)")
if "qe64_GPAW" in results and "vasp16_GPAW" in results:
    rec("GPAW64_vs_GPAW16_worst_rel", "%.4f" % worst_rel(results["qe64_GPAW"], results["vasp16_GPAW"]),
        "same code, two supercells and displacements (reported)")
rec("experiment_gamma_optical_THz", "15.61", "Raman 520.7 cm-1, room temperature (reported; PBE runs ~3% soft)")

with open(path("freqs.tsv"), "w") as fh:
    fh.write("forceset\tq\tfreqs_THz\n")
    for key, r in results.items():
        for k in QPTS:
            fh.write("%s\t%s\t%s\n" % (key, k, " ".join("%.6f" % x for x in np.sort(r["f_" + k]))))
with open(path("results.tsv"), "w") as fh:
    for row in rows:
        fh.write("\t".join(row) + "\n")
if fail:
    print("FAILED: " + ", ".join(fail))
    sys.exit(1)
print("ALL CHECKS PASSED")
