#!/usr/bin/env python3
"""cctbx-real: recompute R-work / R-free for a cohort of PDB entries from the archive's own files.

For each entry with deposited structure factors: mmtbx.model_vs_data (as shipped in cctbx-base,
called through its own run()) against the deposited model and the FIRST data block of the
deposited sf.cif, which is the block wwPDB defines as the data used for the final refinement.
Then, per entry:

  published truth  recomputed R-work / R-free vs the values in the entry's own _refine category
  control          the same fmodel against Fobs permuted among reflections (seeded): it must be
                   far worse, or "R ~ 0.2" was not a statement about the model
  invariance       F_calc in the deposited space group vs the structure expanded to P1, on the
                   P1-expanded indices, direct summation: the symmetry machinery, exactly
  cross-code       gemmi's direct-summation F_calc (IT92) vs cctbx's (it1992) on the same
                   reflections and the same atoms, < 1e-6 relative (gemmi's table is float32)
  scored atoms     the structure model_vs_data scored equals the file's own atoms, and R at
                   fixed scales is unchanged when F_calc is recomputed from the file's atoms

Writes /tmp/out/results.tsv (one row per cohort entry, in id order) and prints 'CCTBX REAL OK'
only if every assertion holds.
"""
import gzip
import io
import math
import multiprocessing as mp
import os
import resource
import shutil
import sys
import time
import traceback

O = "/tmp/out"
W = "/var/tmp/w"
PDB = "/tmp/pdb"
N_P1 = 40        # reflections for the P1 and gemmi checks (direct summation, P1 can be huge)
SEED = 20260101
PREFER_FLAGS = ["_refln.status", "_refln.pdbx_r_free_flag"]
PREFER = ["_refln.F_meas_au", "_refln.intensity_meas", "_refln.pdbx_F_plus", "_refln.pdbx_I_plus"]


def ver(mod):
    try:
        m = __import__(mod)
        return str(getattr(m, "__version__", "unknown"))
    except Exception as e:  # a version print must never kill the run
        return "unavailable (%s)" % type(e).__name__


def gunzip(src, dst):
    with gzip.open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b)


def published(model_cif):
    """_refine R values exactly as deposited, read with gemmi (not the code under test)."""
    import gemmi
    b = gemmi.cif.read(model_cif).sole_block()

    def num(tag):
        v = b.find_value(tag)
        if v is None or v in ("?", "."):
            return None
        try:
            return float(gemmi.cif.as_number(v))
        except Exception:
            return None
    method = (b.find_value("_exptl.method") or "").strip("'\"")
    rw = num("_refine.ls_R_factor_R_work")
    if rw is None:
        rw = num("_refine.ls_R_factor_obs")
    nmeth = len(b.find_values("_exptl.method"))
    return method, nmeth, rw, num("_refine.ls_R_factor_R_free"), num("_refine.ls_d_res_high")


def first_block(sf_cif, dst):
    import gemmi
    doc = gemmi.cif.read(sf_cif)
    out = gemmi.cif.Document()
    out.add_copied_block(doc[0])
    out.write_file(dst)
    return len(doc)


def one(pid):
    t0 = time.time()
    row = {"id": pid, "status": "", "reason": ""}
    d = os.path.join(W, pid)
    os.makedirs(d, exist_ok=True)
    log = io.StringIO()
    try:
        mcif = os.path.join(d, pid + ".cif")
        gunzip(os.path.join(PDB, "mmCIF", pid + ".cif.gz"), mcif)
        method, nmeth, rw_pub, rf_pub, dres = published(mcif)
        row.update(method=method, rw_pub=rw_pub, rf_pub=rf_pub, d_pub=dres)
        if nmeth != 1 or method.upper() != "X-RAY DIFFRACTION":
            row.update(status="skip", reason="not single-method X-ray (%s x%d)" % (method, nmeth))
            return row
        if rw_pub is None:
            row.update(status="skip", reason="no deposited R-work")
            return row
        sfall = os.path.join(d, pid + "-sf-all.cif")
        gunzip(os.path.join(PDB, "sf", "r%ssf.ent.gz" % pid), sfall)
        sf = os.path.join(d, pid + "-sf.cif")
        row["sf_blocks"] = first_block(sfall, sf)
        os.remove(sfall)

        from cctbx.array_family import flex
        import mmtbx.f_model
        import mmtbx.model_vs_data
        # A first block can hold more than one observation array or more than one flag array;
        # model_vs_data then stops rather than guess. Choose by a fixed order, recorded per entry.
        args = [mcif, sf]
        fmodel = None
        for attempt in range(3):
            try:
                fmodel = mmtbx.model_vs_data.run(args, log=log)
                break
            except Exception as e:
                msg = str(e)
                if "Multiple equally suitable" not in msg or attempt == 2:
                    raise
                import iotbx.reflection_file_reader
                have = set()
                for ma in iotbx.reflection_file_reader.any_reflection_file(sf).as_miller_arrays():
                    have.update(ma.info().labels)
                key, order = (("r_free_flags_label", PREFER_FLAGS) if "R-free flags" in msg
                              else ("f_obs_label", PREFER))
                pick = [l for l in order if l in have]
                if not pick:
                    raise
                row[key] = pick[0]
                log.write("\n== ambiguous: %s=%s ==\n" % (key, pick[0]))
                args = args + ["%s=%s" % (key, pick[0])]
        if fmodel is None:
            raise RuntimeError("model_vs_data returned no fmodel")
        fo = fmodel.f_obs()
        n_free = fmodel.r_free_flags().data().count(True)
        rw = fmodel.r_work()
        rf = fmodel.r_free() if 0 < n_free < fo.indices().size() else None
        xrs = fmodel.xray_structure
        row.update(rw=rw, rf=rf, n_refl=fo.indices().size(), d_min=fo.d_min(),
                   n_atoms=xrs.scatterers().size(), sg=str(fo.space_group_info()).replace(" ", ""),
                   n_free=n_free)

        # control: Fobs permuted among reflections, seeded, same everything else
        flex.set_random_seed(SEED)
        perm = flex.random_permutation(fo.data().size())
        fo_perm = fo.customized_copy(data=fo.data().select(perm),
                                     sigmas=fo.sigmas().select(perm) if fo.sigmas() is not None else None)
        fm_c = mmtbx.f_model.manager(xray_structure=xrs, f_obs=fo_perm,
                                     r_free_flags=fmodel.r_free_flags())
        fm_c.update_all_scales(update_f_part1=True)
        row["rw_control"] = fm_c.r_work()

        # invariance: deposited space group vs the same structure expanded to P1
        sub = fo.select(flex.size_t(range(min(N_P1, fo.indices().size()))))
        fc = sub.structure_factors_from_scatterers(xray_structure=xrs, algorithm="direct").f_calc()
        fc_p1 = fc.expand_to_p1()
        xrs_p1 = xrs.expand_to_p1()
        fc_p1d = fc_p1.structure_factors_from_scatterers(xray_structure=xrs_p1, algorithm="direct").f_calc()
        a, b = fc_p1.common_sets(fc_p1d)
        den = flex.max(flex.abs(a.data()))
        row["p1_rel"] = flex.max(flex.abs(a.data() - b.data())) / den
        row["n_p1"] = a.indices().size()
        row["n_ops"] = fo.space_group().order_z()

        # The atoms as deposited, straight from the file's hierarchy, against the structure
        # model_vs_data scored: same sites, ADPs and occupancies? And if not, what does the
        # difference do to R at fixed scales (same fmodel, only F_calc recomputed)?
        import iotbx.pdb
        pin = iotbx.pdb.input(file_name=mcif)
        hier = pin.construct_hierarchy()
        xu = hier.extract_xray_structure(crystal_symmetry=fo.crystal_symmetry())
        if xu.scatterers().size() == xrs.scatterers().size():
            su, sr = xu.scatterers(), xrs.scatterers()
            dxyz = (xu.sites_cart() - xrs.sites_cart()).norms()
            dadp = flex.double([abs(a.u_iso - b.u_iso) if not a.flags.use_u_aniso() else
                                max(abs(p - q) for p, q in zip(a.u_star, b.u_star)) for a, b in zip(su, sr)])
            docc = flex.abs(su.extract_occupancies() - sr.extract_occupancies())
            moved = (dxyz > 1e-9) | (dadp > 1e-12) | (docc > 1e-9)
            row["n_changed"] = moved.count(True)
            row["max_dxyz_A"] = flex.max(dxyz)
            xu.scattering_type_registry(custom_dict=xrs.scattering_type_registry().as_type_gaussian_dict())
            fm2 = fmodel.deep_copy()
            fm2.update_xray_structure(xray_structure=xu, update_f_calc=True, update_f_mask=True)
            row["rw_deposited_atoms"] = fm2.r_work()
        # cross-code: gemmi direct summation, IT92, on the same reflections, both sides reading the
        # model file's own atoms and cell (model_vs_data takes its cell from the reflection file).
        try:
            import gemmi
            from cctbx import miller
            xm = hier.extract_xray_structure(crystal_symmetry=pin.crystal_symmetry())
            row["cell_diff"] = max(abs(p - q) for p, q in zip(fo.unit_cell().parameters(),
                                                              xm.unit_cell().parameters()))
            # gemmi's IT92 ignores formal charge by default, so compare neutral atoms on both sides
            for sc in xm.scatterers():
                sc.scattering_type = sc.scattering_type.rstrip("+-0123456789")
            xm.scattering_type_registry(table="it1992", types_without_a_scattering_contribution=["?"])
            ms = miller.set(xm.crystal_symmetry(), sub.indices(), anomalous_flag=False)
            fc92 = ms.structure_factors_from_scatterers(xray_structure=xm, algorithm="direct").f_calc()
            st = gemmi.read_structure(mcif)
            st.remove_empty_chains()
            row["n_atoms_gemmi"] = st[0].count_atom_sites()
            row["n_atoms_model"] = xm.scatterers().size()
            calc = gemmi.StructureFactorCalculatorX(st.cell)
            g = flex.complex_double([calc.calculate_sf_from_model(st[0], tuple(h)) for h in fc92.indices()])
            row["gemmi_rel"] = flex.max(flex.abs(fc92.data() - g)) / flex.max(flex.abs(fc92.data()))
        except Exception as e:
            row["gemmi_rel"] = None
            row["gemmi_err"] = "%s: %s" % (type(e).__name__, str(e)[:80])
        row["status"] = "ok"
    except Exception as e:
        row.update(status="error", reason="%s: %s" % (type(e).__name__, str(e).replace("\n", " ")[:160]))
        log.write(traceback.format_exc())
    finally:
        row["wall_s"] = round(time.time() - t0, 2)
        row["rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
        with open(os.path.join(O, "logs", pid + ".log"), "w") as f:
            f.write(log.getvalue())
        shutil.rmtree(d, ignore_errors=True)
    return row


COLS = ["id", "status", "reason", "method", "sg", "n_ops", "n_atoms", "n_refl", "n_free", "sf_blocks", "f_obs_label", "r_free_flags_label",
        "d_pub", "d_min", "rw_pub", "rw", "rf_pub", "rf", "rw_control", "n_changed", "max_dxyz_A", "rw_deposited_atoms", "p1_rel", "n_p1", "cell_diff", "n_atoms_model", "n_atoms_gemmi", "gemmi_rel",
        "gemmi_err", "wall_s", "rss_mib"]


def fmt(v):
    if v is None:
        return "NA"
    if isinstance(v, float):
        return repr(v)
    return str(v).replace("\t", " ")


def main():
    os.makedirs(os.path.join(O, "logs"), exist_ok=True)
    os.makedirs(W, exist_ok=True)
    ids = sorted(f[:4] for f in os.listdir(os.path.join(PDB, "mmCIF")) if f.endswith(".cif.gz"))
    probe = os.environ.get("CCTBX_PROBE_IDS", "").split()
    if probe:
        ids = [i for i in ids if i in probe]
    print("cctbx-real: %d entries; cctbx %s, gemmi %s, numpy %s, python %s" % (
        len(ids), ver("cctbx"), ver("gemmi"), ver("numpy"), sys.version.split()[0]))
    try:
        import libtbx.version
        print("libtbx version: %s" % str(libtbx.version.get_version()))
    except Exception as e:
        print("libtbx version: unavailable (%s)" % type(e).__name__)
    nproc = int(os.environ.get("CCTBX_WORKERS", os.cpu_count()))
    t0 = time.time()
    rows = []
    with mp.get_context("fork").Pool(nproc, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(one, ids):
            rows.append(r)
            print("%-4s %-5s %6.1fs rw %s/%s %s" % (r["id"], r["status"], r["wall_s"],
                  fmt(r.get("rw_pub")), fmt(r.get("rw")), r.get("reason", "")), flush=True)
    wall = time.time() - t0
    rows.sort(key=lambda r: r["id"])
    with open(os.path.join(O, "results.tsv"), "w") as f:
        f.write("\t".join(COLS) + "\n")
        for r in rows:
            f.write("\t".join(fmt(r.get(c)) for c in COLS) + "\n")
    print("cohort wall: %.1f s on %d workers" % (wall, nproc))

    fails = []

    def check(ok, msg):
        print(("PASS " if bool(ok) else "FAIL ") + msg)
        if not bool(ok):
            fails.append(msg)

    ok = [r for r in rows if r["status"] == "ok"]
    skip = [r for r in rows if r["status"] == "skip"]
    err = [r for r in rows if r["status"] == "error"]
    check(len(rows) == len(ids), "every entry accounted for: %d of %d" % (len(rows), len(ids)))
    print("ok %d  skip %d  error %d" % (len(ok), len(skip), len(err)))
    for r in skip + err:
        print("  %s %s %s" % (r["id"], r["status"], r["reason"]))
    check(len(ok) > 0, "at least one entry compared")
    fin = [r for r in ok if all(isinstance(r.get(k), float) and math.isfinite(r[k]) and 0 < r[k] < 1
                                for k in ("rw", "rw_control"))
           and (r.get("rf") is None or (math.isfinite(r["rf"]) and 0 < r["rf"] < 1))]
    check(len(fin) == len(ok), "R-work, R-free (where flagged) and control finite and in (0,1): %d of %d" % (len(fin), len(ok)))
    worse = [r for r in fin if r["rw_control"] > r["rw"] + 0.1]
    check(len(worse) == len(fin), "control (Fobs permuted) worse by > 0.1 on every entry: %d of %d" % (len(worse), len(fin)))
    p1 = [r for r in ok if isinstance(r.get("p1_rel"), float) and r["p1_rel"] < 1e-9]
    check(len(p1) == len(ok), "P1 expansion reproduces F_calc to < 1e-9 relative: %d of %d (max %s)" % (
        len(p1), len(ok), fmt(max([r.get("p1_rel") or 0.0 for r in ok] or [0.0]))))
    if fin:
        dw = sorted(abs(r["rw"] - r["rw_pub"]) for r in fin)
        med = dw[len(dw) // 2]
        print("|R-work recomputed - deposited|: median %.4f; within 0.01 %d, 0.02 %d, 0.05 %d of %d" % (
            med, sum(x <= 0.01 for x in dw), sum(x <= 0.02 for x in dw), sum(x <= 0.05 for x in dw), len(dw)))
        check(med < 0.01, "median |R-work recomputed - deposited| < 0.01: %.4f" % med)
        ctl = sorted(r["rw_control"] for r in fin)
        print("control R-work: median %.4f, min %.4f" % (ctl[len(ctl) // 2], ctl[0]))
    g = [r["gemmi_rel"] for r in ok if isinstance(r.get("gemmi_rel"), float)]
    if g:
        g.sort()
        print("gemmi vs cctbx F_calc (IT92, direct): %d entries, median %.3e, max %.3e" % (len(g), g[len(g) // 2], g[-1]))
    check(len(g) == len(ok), "gemmi F_calc computed for every compared entry: %d of %d" % (len(g), len(ok)))
    same = [r for r in ok if r.get("n_atoms_model") is not None and r.get("n_atoms_model") == r.get("n_atoms_gemmi")]
    print("same atom count in both readers: %d of %d" % (len(same), len(ok)))
    check(len(same) == len(ok), "both readers build the same number of atoms on every compared entry")
    check(all(isinstance(r.get("gemmi_rel"), float) and r["gemmi_rel"] < 1e-6 for r in same),
          "gemmi vs cctbx F_calc < 1e-6 relative on every entry (gemmi's IT92 table is float32)")
    rd = [r for r in ok if (r.get("n_changed") or 0) > 0]
    du = [abs(r["rw_deposited_atoms"] - r["rw"]) for r in ok if isinstance(r.get("rw_deposited_atoms"), float)]
    print("entries where model_vs_data scored atoms that differ from the file: %d of %d (max site shift %s A); "
          "|R-work deposited atoms - scored| at fixed scales: max %s over %d" % (
          len(rd), len(ok), fmt(max([r["max_dxyz_A"] for r in rd] or [0.0])), fmt(max(du or [0.0])), len(du)))
    cmp_ = [r for r in ok if r.get("n_changed") is not None]
    check(len(cmp_) == len(ok) and not rd and all(x == 0 or x < 1e-12 for x in du),
          "model_vs_data scored exactly the deposited atoms: %d of %d compared, %d differ" % (len(cmp_), len(ok), len(rd)))
    cd = [r for r in ok if isinstance(r.get("cell_diff"), float) and r["cell_diff"] > 0]
    print("reflection-file cell differs from model cell: %d of %d entries (max %s)" % (
        len(cd), len(ok), fmt(max([r["cell_diff"] for r in cd] or [0.0]))))
    if fails:
        print("FAILED: %d assertion(s)" % len(fails))
        sys.exit(1)
    print("CCTBX REAL OK")


if __name__ == "__main__":
    main()
