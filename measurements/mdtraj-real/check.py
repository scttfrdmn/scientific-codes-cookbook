"""Assertions on real.py's results.json; prints check.txt and exits 1 on any failure.
   python3 -u check.py <out dir> [first N files]
"""
import json
import os
import sys

OUT = sys.argv[1]
NF = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else 0
r = json.load(open(os.path.join(OUT, "results.json")))
F = r["files"]
fail = []

# The dataset: the FAST run of SARS-CoV-2 Mpro in s3://fah-public-data-covid19-cryptic-pockets,
# 6 generations x 10 kids. The topology is the dataset's own protein-only prot_masses.pdb.
WANT_FILES = NF or 60
# Bounds, each from the arithmetic, not from a run:
# - two decoders of the same XTC integers: float32 at |x| <= ~10 nm carries ~1e-6 nm, so 1e-5 nm
#   says "the same number", against a stored quantum of 1e-3 nm.
TOL_DECODE_NM = 1e-5
# - a squared RMSD from MDTraj's float32 QCP: G_a + G_b - 2 lambda cancels terms of ~N|x|^2
#   (~300 CA x ~4 nm^2) down to N*rmsd^2, so float32 (6e-8 relative) leaves ~1e-6 nm^2. Compared
#   as squares, because sqrt amplifies that floor without bound as the RMSD goes to zero.
TOL_MSD_F32 = 1e-5
# - two float64 implementations (MDAnalysis's QCP, numpy SVD): QCP's Newton solve stops at 1e-11.
TOL_MSD_F64 = 1e-9
TOL_REL_F32 = 1e-5          # Rg / SASA sums in float32
TOL_DIHEDRAL_RAD = 1e-3     # float32 cross products on ~0.15 nm bonds; 1e-3 rad = 0.06 degrees
TOL_GRID = 0.01             # decoded x*1000 within 1% of a quantum of an integer
# openmm-mdanalysis-real's own MDAnalysis result on this DCD (results/graviton4/check.txt), as printed.
JAC_PUBLISHED = "1.54e-05"


def check(label, ok, detail):
    ok = bool(ok)                  # a numpy bool must fail too; NaN comparisons are already False
    print("%-34s %s  %s" % (label, "ok  " if ok else "FAIL", detail), flush=True)
    if not ok:
        fail.append(label)


def obs(label, detail):
    print("%-34s obs   %s" % (label, detail), flush=True)


def worst(key):
    v = [f.get(key, float("nan")) for f in F]
    return max(v) if all(x == x for x in v) else float("nan")      # any NaN/missing fails the bound


def every(key):
    return all(bool(f.get(key)) for f in F)


obs("versions", "mdtraj %s, MDAnalysis %s, numpy %s, %d workers" % (
    r["mdtraj_version"], r["mdanalysis_version"], r["numpy_version"], r["workers"]))
nfr = sum(f["n_frames"] for f in F)
nval = sum(f["n_values"] for f in F)
gb = sum(f["bytes"] for f in F) / 1e9
check("files", len(F) == WANT_FILES == len({f["file"] for f in F}),
      "%d distinct XTCs, %.3f GB  want %d" % (len({f["file"] for f in F}), gb, WANT_FILES))
atoms = {f["n_atoms"] for f in F} | {f["top_atoms"] for f in F} | {f["mda_atoms"] for f in F}
check("atoms", len(atoms) == 1, "%s in every XTC, the topology and MDAnalysis" % sorted(atoms))
check("frames_two_libraries", all(f["n_frames"] == f["mda_frames"] == f["rt_frames"] for f in F),
      "%s frames in total, identical per file in MDTraj, MDAnalysis and the round trip" % format(nfr, ","))
obs("frames_per_file", "%d..%d" % (min(f["n_frames"] for f in F), max(f["n_frames"] for f in F)))
obs("time_step_ps", "%g..%g; time %g..%g ps" % (min(f["dt_min"] for f in F), max(f["dt_max"] for f in F),
                                              min(f["time_first"] for f in F), max(f["time_last"] for f in F)))
check("dt_constant", all(f["dt_min"] > 0 and f["dt_max"] - f["dt_min"] < 1e-3 for f in F),
      "every file's frame times strictly increase by one step (spread < 1e-3 ps)")
check("on_the_xtc_grid", worst("grid_dev_max") < TOL_GRID,
      "max |1000x - round(1000x)| = %.2e quanta  want < %g (precision 1000, decoded exactly)" % (worst("grid_dev_max"), TOL_GRID))
check("coords_two_libraries", worst("coord_diff_nm") < TOL_DECODE_NM,
      "max|MDTraj - MDAnalysis| = %.2e nm over %s values  want < %g" % (worst("coord_diff_nm"), format(nval, ","), TOL_DECODE_NM))
check("box_two_libraries", worst("box_diff_nm") < TOL_DECODE_NM and worst("angle_diff_deg") < 1e-4,
      "max|diff| = %.2e nm, angles %.2e deg (box angles %s)" % (
          worst("box_diff_nm"), worst("angle_diff_deg"), " ".join("%.2f" % a for a in F[0]["box_angles"])))
check("time_two_libraries", worst("time_diff_ps") < 1e-3, "max|diff| = %.2e ps" % worst("time_diff_ps"))
check("xtc_round_trip_mdtraj", worst("rt_diff_nm") == 0.0,
      "MDTraj writes every file back to XTC and reads it: max|diff| = %.2e nm  want exactly 0" % worst("rt_diff_nm"))
check("xtc_round_trip_mdanalysis", worst("rt_mda_diff_A") == 0.0,
      "MDAnalysis reads MDTraj's XTC: max|diff| vs the original = %.2e A  want exactly 0" % worst("rt_mda_diff_A"))
# The coordinates survive exactly; the file bytes are not the claim (the header and bitstream may differ).
obs("xtc_rewrite_bytes", "same size as the original %d of %d, byte-identical %d of %d" % (
    sum(f["rt_bytes"] == f["bytes"] for f in F), len(F), sum(bool(f["rt_file_identical"]) for f in F), len(F)))
check("ca_selection_same", every("ca_same"), "%s CA atoms, the same indices in both libraries" % sorted({f["n_ca"] for f in F}))
check("rmsd_mdtraj_vs_mdanalysis", worst("msd_mdtraj_mda") < TOL_MSD_F32,
      "max|rmsd^2 diff| = %.2e nm^2 over %s frames  want < %g (float32 QCP vs float64 QCP)" % (worst("msd_mdtraj_mda"), format(nfr, ","), TOL_MSD_F32))
check("rmsd_mdanalysis_vs_svd", worst("msd_mda_svd") < TOL_MSD_F64,
      "max|rmsd^2 diff| = %.2e nm^2  want < %g (QCP vs an independent float64 Kabsch SVD)" % (worst("msd_mda_svd"), TOL_MSD_F64))
check("rmsd_rigid_motion_invariant", worst("msd_rigid_motion") < TOL_MSD_F32,
      "random rotation+translation of every frame: max|rmsd^2 diff| = %.2e nm^2  want < %g" % (worst("msd_rigid_motion"), TOL_MSD_F32))
check("rg_mdtraj_vs_mdanalysis_coords", worst("rg_rel_mdtraj_mda") < TOL_REL_F32,
      "unweighted Rg, max rel diff %.2e  want < %g (no mass table on either side)" % (worst("rg_rel_mdtraj_mda"), TOL_REL_F32))
check("rg_rigid_motion_invariant", worst("rg_rel_rigid_motion") < TOL_REL_F32, "max rel diff %.2e" % worst("rg_rel_rigid_motion"))
check("dihedral_definitions_same", every("dihedral_quads_same"),
      "phi %s / psi %s quadruplets: MDTraj's == MDAnalysis's own phi_selection/psi_selection" % (
          sorted({f["n_phi"] for f in F}), sorted({f["n_psi"] for f in F})))
check("dihedrals_mdtraj_vs_mdanalysis", worst("dihedral_diff_rad") < TOL_DIHEDRAL_RAD,
      "max wrapped |diff| = %.2e rad  want < %g" % (worst("dihedral_diff_rad"), TOL_DIHEDRAL_RAD))
check("periodic_distances_two_libraries", worst("dist_diff_nm") < TOL_DECODE_NM,
      "%s CA-CA pairs/frame, minimum image: max|diff| = %.2e nm  want < %g" % (
          format(F[0]["n_pairs"], ","), worst("dist_diff_nm"), TOL_DECODE_NM))
check("sasa_atom_sums_to_residue", worst("sasa_atom_vs_residue_rel") < TOL_REL_F32,
      "Shrake-Rupley per-atom summed == per-residue, max rel %.2e" % worst("sasa_atom_vs_residue_rel"))

w = sum(f["n_frames"] for f in F)
obs("rmsd_ca_nm", "mean %.4f, max %.4f" % (sum(f["rmsd_mean"] * f["n_frames"] for f in F) / w, max(f["rmsd_max"] for f in F)))
obs("rg_nm", "mean %.4f" % (sum(f["rg_mean"] * f["n_frames"] for f in F) / w))
obs("dssp", "H %.4f  E %.4f (MDTraj, simplified)" % (sum(f["dssp_H"] * f["n_frames"] for f in F) / w,
                                                    sum(f["dssp_E"] * f["n_frames"] for f in F) / w))
obs("dssp_mda_residues", sorted({f.get("dssp_mda_residues") for f in F}, key=str))
ag = [f["dssp_agree_mda"] for f in F if isinstance(f["dssp_agree_mda"], float) and f["dssp_agree_mda"] >= 0]
obs("dssp_agree_mdanalysis", ("%.4f of residue-frames (%d files; a different DSSP implementation, reported)" % (min(ag), len(ag)))
    if ag else str(F[0]["dssp_agree_mda"]))
obs("sasa_total_nm2", "mean %.1f" % (sum(f["sasa_total_mean_nm2"] * f["n_frames"] for f in F) / w))
obs("worker_maxrss_gib", "%.2f" % max(f["worker_maxrss_gib"] for f in F))
T = {}
for f in F:
    for k, v in f["timing_s"].items():
        T[k] = T.get(k, 0.0) + v
obs("cpu_seconds_by_step", ", ".join("%s %.0f" % kv for kv in sorted(T.items(), key=lambda kv: -kv[1])))

j = r.get("jac", {})
print("== openmm-mdanalysis-real's DHFR DCD, identical bytes ==")
if "error" in j:
    check("jac", False, j["error"])
else:
    check("jac_atoms_frames", j["n_atoms"] == j["mda_atoms"] == 23558 and j["n_frames"] == j["mda_frames"] == 100,
          "%d atoms, %d frames in MDTraj; %d / %d in MDAnalysis  want 23558 / 100" % (
              j["n_atoms"], j["n_frames"], j["mda_atoms"], j["mda_frames"]))
    check("jac_coords_two_libraries", j["coord_diff_A"] < 1e-4,
          "max|10*MDTraj - MDAnalysis| = %.2e A  want < 1e-4 (float32 A -> nm -> A)" % j["coord_diff_A"])
    # MDTraj holds nm in float32, so reading an Angstrom DCD costs one more rounding than MDAnalysis
    # pays: the per-distance bound is the same float32-format bound as the coordinates.
    check("jac_constraints_mdtraj_vs_mdanalysis", j["dist_diff_A"] < 1e-4,
          "every one of %s constrained distances: max|MDTraj - MDAnalysis| = %.2e A  want < 1e-4" % (
              format(j["n_distances"], ","), j["dist_diff_A"]))
    check("jac_mdanalysis_reproduces_sibling", "%.2e" % j["worst_dev_mda"] == JAC_PUBLISHED,
          "worst |d-d0|/d0 %.4e  == %s, recorded by openmm-mdanalysis-real on these bytes" % (j["worst_dev_mda"], JAC_PUBLISHED))
    check("jac_mdtraj_constraints_decode", j["worst_dev_mdtraj"] < 1e-4,
          "worst |d-d0|/d0 read by MDTraj %.4e  want < 1e-4 (openmm-mdanalysis-real's float32-DCD bound)" % j["worst_dev_mdtraj"])

if fail:
    print("FAILED: " + ", ".join(fail))
    sys.exit(1)
print("ALL CHECKS PASSED")
