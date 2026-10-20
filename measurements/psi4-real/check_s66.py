"""The checks for psi4_s66.py. Every bound is argued in measurements/psi4-real/README.md."""
import os

# kcal/mol. The published columns are printed to 1e-5 kcal/mol; what bounds the agreement is the
# SCF convergence DESRES ran at (Psi4's SCF-module default, 1e-6): in the probe, converging the same
# three dimers at 1e-6 instead of 1e-10 moved the columns by up to 2.35e-3 kcal/mol (tz_MP2_all).
TOL = dict.fromkeys(["sapt_es", "sapt_ex", "sapt_exs2", "sapt_ind", "sapt_exind", "sapt_disp", "sapt_exdisp_os",
                     "sapt_exdisp_ss", "sapt_delta_HF", "sapt_all", "tz_HF", "tz_MP2_os", "tz_MP2_ss", "tz_MP2_all"], 1e-2)
TOL_SUM = 1e-9      # kcal/mol: a sum of the code's own terms against its own total (double rounding)
TOL_PRINT = 6e-5    # kcal/mol: DESRES's own columns, each rounded to 1e-5, summed (up to 9 terms)
TOL_ROT = 1e-5      # kcal/mol: rigid rotation + translation; a thread-count change moved the probe by <= 8.7e-7


def check(res, data, todo, small, wall, out, full):
    lines, fails = [], []

    def ok(label, good, detail, assert_it=True):
        good = bool(good)
        tag = ("ok" if good else "FAIL") if assert_it else "obs"
        lines.append("%-4s %-58s %s" % (tag, label, detail))
        if assert_it and not good:
            fails.append(label)

    base = [r for r in res if not r.get("rotated") and not r.get("control")]
    rot = [r for r in res if r.get("rotated")]
    ctl = [r for r in res if r.get("control")]
    err = [r for r in res if "error" in r]
    ok("every calculation finished", not err, "%d of %d failed %s" % (len(err), len(res), [(r["row"], r["error"][-300:]) for r in err[:3]]))
    ids = [r["geom_id"] for r in base]
    ok("one result per selected geometry, no duplicate geom_id", sorted(ids) == sorted(d["geom_id"] for d in todo) and len(set(ids)) == len(ids),
       "%d results, %d selected, %d distinct" % (len(ids), len(todo), len(set(ids))))
    if full:
        ok("the published set is S66: 66 rows, 66 distinct geom_id and system names", len(data) == 66 and len({d["geom_id"] for d in data}) == 66 and len({d["name"] for d in data}) == 66,
           "%d rows, %d geom_id, %d names" % (len(data), len({d["geom_id"] for d in data}), len({d["name"] for d in data})))
    good = [r for r in base if "error" not in r]
    by = {d["geom_id"]: d for d in data}
    if good:
        kc = sorted({r["kcal_psi4"] for r in good})
        ok("Psi4's hartree2kcalmol (CODATA 2014's is 627.5094740631)", True, "%s" % kc, assert_it=False)
    # DESRES's own columns add up (the published total is the sum of the published terms)
    terms = ["sapt_es", "sapt_ex", "sapt_ind", "sapt_exind", "sapt_delta_HF", "sapt_disp", "sapt_exdisp_os", "sapt_exdisp_ss"]
    dd = [abs(sum(d["ref"][t] for t in terms) - d["ref"]["sapt_all"]) for d in data]
    ok("published: sapt_all == sum of its 8 published terms, every row", max(dd) <= TOL_PRINT, "max |d| %.2e kcal/mol <= %.0e" % (max(dd), TOL_PRINT))
    dd = [abs(d["ref"]["tz_HF"] + d["ref"]["tz_MP2_os"] + d["ref"]["tz_MP2_ss"] - d["ref"]["tz_MP2_all"]) for d in data]
    ok("published: tz_MP2_all == tz_HF + os + ss, every row", max(dd) <= TOL_PRINT, "max |d| %.2e kcal/mol <= %.0e" % (max(dd), TOL_PRINT))
    if good:
        dd = [abs(sum(r[t] for t in terms) - r["sapt_all"]) for r in good]
        ok("Psi4: SAPT0 TOTAL == sum of its 8 terms, every geometry", max(dd) <= TOL_SUM, "max |d| %.2e kcal/mol <= %.0e" % (max(dd), TOL_SUM))
        dd = [abs(r["tz_HF"] + r["tz_MP2_os"] + r["tz_MP2_ss"] - r["tz_MP2_all"]) for r in good]
        ok("Psi4: MP2 interaction == HF + os + ss, every geometry", max(dd) <= TOL_SUM, "max |d| %.2e kcal/mol <= %.0e" % (max(dd), TOL_SUM))
    # the reproduction: every published SAPT0 and DF-MP2/aVTZ column
    for col in [c for c in TOL]:
        dd = sorted((abs(r[col] - by[r["geom_id"]]["ref"][col]), r["row"]) for r in good if col in r)
        if not dd:
            continue
        tol = TOL[col]
        worst = dd[-1]
        detail = "%d geometries, max |d| %.2e (row %d), median %.2e kcal/mol" % (len(dd), worst[0], worst[1], dd[len(dd) // 2][0])
        if tol is None:
            ok("== DESS66 %s" % col, True, detail, assert_it=False)
        else:
            nbad = sum(1 for v, _ in dd if not v <= tol)
            ok("== DESS66 %s, every geometry" % col, nbad == 0 and len(dd) == len(todo), detail + " <= %.0e (%d over)" % (tol, nbad))
    # the invariance control
    if rot:
        bg = {r["geom_id"]: r for r in good}
        dd = [abs(r[c] - bg[r["geom_id"]][c]) for r in rot if "error" not in r and r["geom_id"] in bg for c in TOL if c.startswith("sapt")]
        ok("SAPT0 terms invariant under rotation + translation (%d dimers)" % len(rot),
           dd and (TOL_ROT is None or max(dd) <= TOL_ROT), "max |d| %.2e kcal/mol%s" % (max(dd) if dd else float("nan"),
                                                                                 "" if TOL_ROT is None else " <= %.0e" % TOL_ROT),
           assert_it=TOL_ROT is not None)
    # the method control: Psi4's default SAPT0 (dimer-centred basis, JK-fit electrostatics) must miss DESS66
    if ctl:
        miss = []
        for r in ctl:
            if "error" in r:
                continue
            ref = by[r["geom_id"]]["ref"]
            worst = max((abs(r[c] - ref[c]), c) for c in TOL if c.startswith("sapt"))
            miss.append((worst[0], worst[1], r["row"]))
        least = min(miss) if miss else (float("nan"), "-", -1)
        ok("control: Psi4's default SAPT0 misses DESS66 by > bound (%d dimers)" % len(ctl),
           len(miss) == len(ctl) and all(m[0] > TOL[m[1]] for m in miss),
           "least worst-column miss %.3e kcal/mol (%s, row %d)" % least)
        for r in ctl:
            if "error" not in r:
                ref = by[r["geom_id"]]["ref"]
                lines.append("       control row %-3d %s" % (r["row"], "  ".join(
                    "%s %+.4f" % (c[5:], r[c] - ref[c]) for c in TOL if c.startswith("sapt"))))
    # observation, not an assertion: how far SAPT0/aVTZ and DF-MP2/aVTZ sit from the silver-standard reference
    for col in ("sapt_all", "tz_MP2_all"):
        dd = [r[col] - by[r["geom_id"]]["ref"]["reference"] for r in good if col in r]
        if dd:
            ok("%s - silver-standard reference" % col, True, "MAE %.3f, mean %.3f, max |d| %.3f kcal/mol" % (
                sum(abs(x) for x in dd) / len(dd), sum(dd) / len(dd), max(abs(x) for x in dd)), assert_it=False)
    lines.append("")
    lines.append("jobs_wall_s %.1f  sum_job_wall_s %.1f  max geometry rss %.0f MiB" % (
        wall, sum(r["wall"] for r in res), max([r["maxrss_mib"] for r in good] or [0])))
    lines.append("RESULT: %s" % ("PASS" if not fails else "FAIL " + ", ".join(fails)))
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(out, "check.txt"), "w") as fh:
        fh.write(txt + "\n")
    return 0 if not fails else 1
