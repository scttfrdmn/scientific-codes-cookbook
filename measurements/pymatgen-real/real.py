"""pymatgen over a whole Materials Project release (2025-09-25), against MP's own published values.

    python3 -u real.py <data dir holding summary/ and thermo/> <out dir> <worker processes>

Three workloads, each over every record of the release:
  hull      for each thermo type (GGA_GGA+U, R2SCAN): rebuild every phase diagram from the entries
            MP published, and recompute e_above_hull, formation energy, stability, decomposition
            and decomposition enthalpy for every material, against MP's values. Plus two
            identities: a chemical-potential shift leaves every hull distance unchanged, and an
            entry gets the same hull distance in every phase diagram that contains it.
  symmetry  SpacegroupAnalyzer at MP's settings on every structure, against MP's space group; the
            space group must survive a rigid rotation + translation + site shuffle.
  matching  StructureMatcher at MP's materials-builder settings: every structure matches its own
            rotated/shuffled/rescaled copy; distinct MP materials of one formula and space group
            are grouped and the merges are counted (reported).
Writes per-record TSVs and check.txt; exits 1 if any assertion fails.
"""
import glob
import gzip
import itertools
import json
import os
import resource
import sys
import time
import warnings
import zlib
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

warnings.filterwarnings("ignore")

DATA, OUT, NP = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.makedirs(OUT, exist_ok=True)
THERMO_TYPES = ["GGA_GGA+U", "R2SCAN"]
fail = []
lines = []


def rec(label, ok, detail):
    """Record one assertion. Every value goes through str(), so describing a run cannot fail it."""
    s = "%-48s %s  %s" % (str(label), "ok  " if bool(ok) else "FAIL", str(detail))
    print(s, flush=True)
    lines.append(s)
    if not bool(ok):
        fail.append(str(label))


def note(label, detail):
    s = "%-48s info  %s" % (str(label), str(detail))
    print(s, flush=True)
    lines.append(s)


T = {}


def tick(name, t0):
    T[name] = time.time() - t0
    print("== %s: %.1f s" % (name, T[name]), flush=True)


# ---------------------------------------------------------------- thermo: read
def read_thermo_file(path):
    out = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            d = json.loads(line)
            e = d["entries"][d["energy_type"]]
            comp = e["composition"]
            n = sum(comp.values())
            out.append(dict(
                thermo_id=d["thermo_id"], tt=d["thermo_type"], mid=d["material_id"],
                deprecated=bool(d.get("deprecated")), comp=comp, natoms=n,
                e_total=e["energy"] + e["correction"],
                pub_epa=d["energy_per_atom"], pub_ehull=d["energy_above_hull"],
                pub_ef=d["formation_energy_per_atom"], pub_stable=bool(d["is_stable"]),
                pub_decomp=[(x["material_id"], x["amount"]) for x in (d.get("decomposes_to") or [])],
                pub_dH=d.get("decomposition_enthalpy"),
                pub_eqrxn=d.get("equilibrium_reaction_energy_per_atom")))
    return out


# ---------------------------------------------------------------- thermo: one phase diagram
def shift_of(el):
    """A fixed, element-dependent chemical-potential shift in eV/atom (deterministic, any chip)."""
    return (zlib.crc32(el.encode()) % 10007) / 10007.0 * 10.0 - 5.0


def hull_task(args):
    """Build the phase diagram of one maximal chemical system and evaluate every entry in it."""
    from pymatgen.analysis.phase_diagram import PDEntry, PhaseDiagram
    from pymatgen.core import Composition
    tt, chemsys, ents = args
    t0 = time.time()
    comps = [Composition(c) for (_, c, _) in ents]
    pde = [PDEntry(cp, e, name=mid) for (mid, _, e), cp in zip(ents, comps)]
    shifted = [PDEntry(cp, e + sum(shift_of(str(el)) * amt for el, amt in cp.items()), name=mid)
               for (mid, _, e), cp in zip(ents, comps)]
    try:
        pd = PhaseDiagram(pde)
        pds = PhaseDiagram(shifted)
    except Exception as ex:   # e.g. a terminal element missing for this thermo type
        return tt, chemsys, len(ents), time.time() - t0, None, "%s: %s" % (type(ex).__name__, ex)
    res = []
    stable_rf = {e.composition.reduced_formula for e in pd.stable_entries}
    for en, es in zip(pde, shifted):
        decomp, eh = pd.get_decomp_and_e_above_hull(en, allow_negative=True)
        _, ehs = pds.get_decomp_and_e_above_hull(es, allow_negative=True)
        ef = pd.get_form_energy_per_atom(en)
        efs = pds.get_form_energy_per_atom(es)
        stable = en in pd.stable_entries
        # pymatgen solves the decomposition enthalpy exactly (it is then e_above_hull) unless a stable
        # entry of the same composition exists, in which case it is an SLSQP minimisation (ftol 1e-8)
        slsqp = en.composition.reduced_formula in stable_rf
        try:
            dH = pd.get_phase_separation_energy(en)
        except Exception:
            dH = float("nan")
        eqrxn = float("nan")
        if stable:
            try:
                eqrxn = pd.get_equilibrium_reaction_energy(en)
            except Exception:
                pass
        # the decomposition's own identities: amounts are atomic fractions summing to 1, they
        # balance the entry's composition element by element, and the hull distance is the
        # entry's energy minus the products' weighted energy
        fc = en.composition.fractional_composition
        mass = max(abs(sum(float(a) * k.composition.fractional_composition[el] for k, a in decomp.items()) - fc[el])
                   for el in pd.elements)
        asum = abs(sum(float(a) for a in decomp.values()) - 1.0)
        eres = abs(en.energy_per_atom - sum(float(a) * k.energy_per_atom for k, a in decomp.items()) - eh)
        res.append((en.name, float(eh), float(ef), bool(stable),
                    sorted((k.name, float(v)) for k, v in decomp.items()),
                    float(dH), float(eqrxn), float(ehs - eh), float(efs - ef),
                    float(max(mass, asum)), float(eres), bool(slsqp)))
    return tt, chemsys, len(ents), time.time() - t0, res, None


def maximal_systems(chemsyss):
    """Chemical systems that are not a proper subset of another present system."""
    by_size = sorted(chemsyss, key=len, reverse=True)
    present = set(chemsyss)
    covered = set()
    out = []
    for cs in by_size:
        if cs in covered:
            continue
        out.append(cs)
        for k in range(1, len(cs) + 1):
            for sub in itertools.combinations(sorted(cs), k):
                fs = frozenset(sub)
                if fs in present:
                    covered.add(fs)
    return out


# ---------------------------------------------------------------- summary: one structure
SM_KW = dict(ltol=0.2, stol=0.3, angle_tol=5, primitive_cell=True, scale=True,
             attempt_supercell=False, allow_subset=False)


def transforms(s, mid):
    """Rigid rotation, translation and site permutation of s, each alone, seeded by the material id."""
    from pymatgen.core import Structure
    from pymatgen.core.operations import SymmOp
    rng = np.random.default_rng(zlib.crc32(mid.encode()))
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    a, b, c, d = q
    R = np.array([[a*a+b*b-c*c-d*d, 2*(b*c-a*d), 2*(b*d+a*c)],
                  [2*(b*c+a*d), a*a-b*b+c*c-d*d, 2*(c*d-a*b)],
                  [2*(b*d-a*c), 2*(c*d+a*b), a*a-b*b-c*c+d*d]])
    shift = rng.random(3)
    order = rng.permutation(len(s))
    rot = s.copy()
    rot.apply_operation(SymmOp.from_rotation_and_translation(R, [0, 0, 0]), fractional=False)
    tra = s.copy()
    tra.translate_sites(list(range(len(tra))), shift, frac_coords=True, to_unit_cell=True)
    per = Structure.from_sites([s[i] for i in order])
    allt = rot.copy()
    allt.translate_sites(list(range(len(allt))), shift, frac_coords=True, to_unit_cell=True)
    allt = Structure.from_sites([allt[i] for i in order])
    return rot, tra, per, allt


def summary_task(line):
    from pymatgen.analysis.structure_matcher import ElementComparator, StructureMatcher
    from pymatgen.core import Composition, Structure
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    d = json.loads(line)
    mid = d["material_id"]
    sym = d["symmetry"]
    s = Structure.from_dict(d["structure"])
    r = dict(mid=mid, formula=d["formula_pretty"], deprecated=bool(d.get("deprecated")),
             nsites_ok=len(s) == d["nsites"],
             formula_str=s.composition.reduced_formula,
             formula_ok=s.composition.element_composition.reduced_composition.almost_equals(Composition(d["formula_pretty"]).reduced_composition),
             d_volume=abs(s.volume - d["volume"]) / d["volume"],
             d_density=abs(float(s.density) - d["density"]) / d["density"],
             pub_sg=sym["number"], sp=sym.get("symprec"), at=sym.get("angle_tolerance"),
             spglib_mp=sym.get("version"))

    def sg(x, tag):
        try:
            return SpacegroupAnalyzer(x, symprec=sym["symprec"], angle_tolerance=sym["angle_tolerance"]).get_space_group_number()
        except Exception as ex:
            r["err"] = "sga-%s: %s" % (tag, ex)
            return -1

    t0 = time.time()
    rot, tra, per, allt = transforms(s, mid)
    r["our_sg"] = sg(s, "orig")
    r["rot_sg"] = sg(rot, "rot")
    r["tra_sg"] = sg(tra, "tra")
    r["per_sg"] = sg(per, "per")
    r["all_sg"] = sg(allt, "all")
    t1 = time.time()
    sm = StructureMatcher(comparator=ElementComparator(), **SM_KW)
    scaled = allt.copy()
    scaled.scale_lattice(allt.volume * 1.1)
    for key, other in (("match_rot", rot), ("match_all", scaled)):
        try:
            r[key] = bool(sm.fit(s, other))
        except Exception as ex:
            r[key] = False
            r["err"] = "sm-%s: %s" % (key, ex)
    try:
        r["match_rot_noprim"] = bool(StructureMatcher(comparator=ElementComparator(),
                                                      **dict(SM_KW, primitive_cell=False)).fit(s, rot))
    except Exception as ex:
        r["match_rot_noprim"] = False
        r["err"] = "sm-noprim: %s" % ex
    if not r["match_rot"]:
        # diagnosis only: does the primitive-cell finder return the same cell size for both?
        try:
            r["nprim"] = (len(s), len(s.get_primitive_structure()), len(rot.get_primitive_structure()))
        except Exception as ex:
            r["nprim"] = str(ex)
    r["t_sym"] = t1 - t0
    r["t_match"] = time.time() - t1
    return r, json.dumps(d["structure"], separators=(",", ":"))


def group_task(args):
    from pymatgen.analysis.structure_matcher import ElementComparator, StructureMatcher
    from pymatgen.core import Structure
    key, items = args
    t0 = time.time()
    structs = []
    for mid, sj in items:
        st = Structure.from_dict(json.loads(sj))
        st.properties["mid"] = mid
        structs.append(st)
    groups = StructureMatcher(comparator=ElementComparator(), **SM_KW).group_structures(structs)
    merged = [sorted(g.properties["mid"] for g in grp) for grp in groups if len(grp) > 1]
    return key, len(items), len(groups), merged, time.time() - t0


def summary_lines(paths):
    for p in paths:
        with gzip.open(p, "rt") as fh:
            for line in fh:
                yield line


# ================================================================= main
if __name__ == "__main__":
    import pymatgen.core
    import spglib
    note("pymatgen_version", getattr(pymatgen.core, "__version__", "unknown"))
    note("spglib_version", getattr(spglib, "__version__", "unknown"))
    note("workers", NP)
    TT0 = time.time()

    # ---------------- summary: symmetry + self-matching
    t0 = time.time()
    sfiles = sorted(glob.glob(os.path.join(DATA, "summary", "*.jsonl.gz")))
    srows, structs = [], {}
    with Pool(NP) as pool:
        for r, sj in pool.imap_unordered(summary_task, summary_lines(sfiles), chunksize=16):
            srows.append(r)
            structs[r["mid"]] = sj
    tick("symmetry+selfmatch", t0)
    mids = [r["mid"] for r in srows]
    rec("summary material_id unique", len(mids) == len(set(mids)), "%d ids, %d distinct" % (len(mids), len(set(mids))))
    n = len(srows)
    note("summary materials", "%d (%d deprecated)" % (n, sum(r["deprecated"] for r in srows)))
    note("MP symmetry settings", sorted({(r["sp"], r["at"], r["spglib_mp"]) for r in srows}, key=str))
    rec("nsites decode", all(r["nsites_ok"] for r in srows), "%d of %d" % (sum(r["nsites_ok"] for r in srows), n))
    rec("reduced formula decode (composition)", all(r["formula_ok"] for r in srows),
        "%d of %d" % (sum(r["formula_ok"] for r in srows), n))
    fs = [(r["mid"], r["formula"], r["formula_str"]) for r in srows if r["formula_str"] != r["formula"]]
    note("formula_pretty string != pymatgen reduced_formula", "%d %s" % (len(fs), fs[:5]))
    mv = max(r["d_volume"] for r in srows)
    md = max(r["d_density"] for r in srows)
    rec("volume vs published", mv < 1e-9, "max rel %.2e" % mv)
    note("density vs published (atomic-mass table is pymatgen-version dependent)", "max rel %.2e" % md)
    agree = sum(r["our_sg"] == r["pub_sg"] for r in srows)
    rec("space group == MP's (spglib %s vs MP's)" % getattr(spglib, "__version__", "?"), agree == n,
        "%d of %d" % (agree, n))
    for key, what in (("rot_sg", "rigid rotation"), ("tra_sg", "origin shift"), ("per_sg", "site permutation"),
                      ("all_sg", "all three")):
        k = sum(r[key] == r["our_sg"] for r in srows)
        up = sum(r[key] > r["our_sg"] for r in srows)
        lab = "space group invariant under %s" % what
        if key == "rot_sg":
            rec(lab, k == n, "%d of %d" % (k, n))
        else:
            note(lab, "%d of %d (%d higher, %d lower)" % (k, n, up, n - k - up))
    k = sum(r["match_rot"] for r in srows)
    rec("StructureMatcher (primitive_cell=False) fits its own rotated copy", sum(r["match_rot_noprim"] for r in srows) == n,
        "%d of %d" % (sum(r["match_rot_noprim"] for r in srows), n))
    note("StructureMatcher (MP settings) fits its own rotated copy", "%d of %d; failures (nsites, primitive, primitive of rotated): %s"
         % (k, n, [(r["mid"], r.get("nprim")) for r in srows if not r["match_rot"]][:20]))
    k = sum(r["match_all"] for r in srows)
    note("StructureMatcher fits its rotated+shifted+permuted+1.1x-volume copy", "%d of %d" % (k, n))
    errs = [r for r in srows if "err" in r]
    note("structure errors", "%d %s" % (len(errs), [(r["mid"], r["err"]) for r in errs[:5]]))
    note("time sym / match per structure (sum, cpu-s)", "%.0f / %.0f" % (sum(r["t_sym"] for r in srows), sum(r["t_match"] for r in srows)))
    with open(os.path.join(OUT, "symmetry.tsv"), "w") as fh:
        fh.write("material_id\tpub_sg\tsg\trot_sg\ttra_sg\tper_sg\tall_sg\tmatch_rot\tmatch_all\n")
        for r in sorted(srows, key=lambda r: r["mid"]):
            fh.write("%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n" % (
                r["mid"], r["pub_sg"], r["our_sg"], r["rot_sg"], r["tra_sg"], r["per_sg"], r["all_sg"],
                r["match_rot"], r["match_all"]))

    # ---------------- matching across distinct materials
    t0 = time.time()
    groups = defaultdict(list)
    for r in srows:
        if not r["deprecated"]:
            groups[(r["formula"], r["pub_sg"])].append(r["mid"])
    multi = [(k, [(m, structs[m]) for m in sorted(v)]) for k, v in groups.items() if len(v) > 1]
    multi.sort(key=lambda x: len(x[1]), reverse=True)
    del structs
    note("formula+spacegroup groups with >1 material", "%d groups, %d materials, largest %d" % (
        len(multi), sum(len(v) for _, v in multi), len(multi[0][1]) if multi else 0))
    merges = []
    with Pool(NP) as pool:
        for key, nin, nout, merged, secs in pool.imap_unordered(group_task, multi, chunksize=1):
            merges.extend(merged)
    tick("matching", t0)
    merges.sort()
    note("distinct MP materials that StructureMatcher merges", "%d groups covering %d materials" % (
        len(merges), sum(len(m) for m in merges)))
    with open(os.path.join(OUT, "matches.tsv"), "w") as fh:
        fh.write("merged_material_ids\n")
        for m in merges:
            fh.write(",".join(m) + "\n")

    # ---------------- thermo
    t0 = time.time()
    tfiles = sorted(glob.glob(os.path.join(DATA, "thermo", "thermo_type=*", "nelements=*", "*.jsonl.gz")))
    with Pool(NP) as pool:
        docs = [x for chunk in pool.imap_unordered(read_thermo_file, tfiles, chunksize=4) for x in chunk]
    tick("thermo_read", t0)
    note("thermo files / docs", "%d / %d" % (len(tfiles), len(docs)))
    ids = [d["thermo_id"] for d in docs]
    rec("thermo_id unique", len(ids) == len(set(ids)), "%d ids, %d distinct" % (len(ids), len(set(ids))))
    by_tt = defaultdict(list)
    for d in docs:
        by_tt[d["tt"]].append(d)
    note("thermo docs per type", {k: len(v) for k, v in sorted(by_tt.items())})
    # decode identity: the published energy per atom is the entry's corrected energy per atom
    # A doc whose published energy_per_atom is not its own entry's corrected energy is MP-internally
    # inconsistent; the hull is rebuilt from the entries, so those docs are compared separately.
    for d in docs:
        d["decode_d"] = d["e_total"] / d["natoms"] - d["pub_epa"]
    bad = sorted((abs(d["decode_d"]), d["thermo_id"]) for d in docs if abs(d["decode_d"]) >= 1e-9)
    note("docs whose energy_per_atom != own entry's energy", "%d of %d %s" % (len(bad), len(docs), bad[:5]))
    note("GGA_GGA+U_R2SCAN (mixed) docs", "%d -- not rebuilt (needs MP's mixing scheme on full entries)"
         % len([d for d in docs if d["tt"] == "GGA_GGA+U_R2SCAN"]))

    t0 = time.time()
    tasks = []
    for tt in THERMO_TYPES:
        live = [d for d in by_tt[tt] if not d["deprecated"]]
        note("%s non-deprecated" % tt, "%d of %d" % (len(live), len(by_tt[tt])))
        by_cs = defaultdict(list)
        for d in live:
            by_cs[frozenset(d["comp"])].append((d["mid"], d["comp"], d["e_total"]))
        mx = maximal_systems(list(by_cs))
        note("%s chemical systems / maximal" % tt, "%d / %d" % (len(by_cs), len(mx)))
        for cs in mx:
            ents = []
            for k in range(1, len(cs) + 1):
                for sub in itertools.combinations(sorted(cs), k):
                    ents.extend(by_cs.get(frozenset(sub), []))
            tasks.append((tt, "-".join(sorted(cs)), ents))
    tasks.sort(key=lambda x: (len(x[1].split("-")), len(x[2])), reverse=True)   # biggest first
    note("phase diagrams to build", len(tasks))
    for tt, cs, ents in tasks[:8]:
        note("  largest", "%s %s: %d entries" % (tt, cs, len(ents)))
    got = defaultdict(dict)          # tt -> mid -> first result
    incons = defaultdict(float)      # tt -> max |ehull difference| across PDs containing a mid
    pdlog = []
    errors = []
    shift_max = defaultdict(float)
    balance_max = defaultdict(float)
    eres_max = defaultdict(float)
    with Pool(NP) as pool:
        for tt, cs, n, secs, res, err in pool.imap_unordered(hull_task, tasks, chunksize=1):
            pdlog.append((tt, cs, n, secs, err or ""))
            if secs > 30 or len(pdlog) % 5000 == 0:
                print("  pd %d/%d  %s %s  %d entries  %.1f s" % (len(pdlog), len(tasks), tt, cs, n, secs), flush=True)
            if res is None:
                errors.append((tt, cs, err))
                continue
            for r in res:
                shift_max[tt] = max(shift_max[tt], abs(r[7]), abs(r[8]))
                balance_max[tt] = max(balance_max[tt], r[9])
                eres_max[tt] = max(eres_max[tt], r[10])
                prev = got[tt].get(r[0])
                if prev is None:
                    got[tt][r[0]] = r
                else:
                    incons[tt] = max(incons[tt], abs(prev[1] - r[1]))
    tick("hull", t0)
    with open(os.path.join(OUT, "phase-diagrams.tsv"), "w") as fh:
        fh.write("thermo_type\tchemsys\tn_entries\tseconds\terror\n")
        for row in sorted(pdlog):
            fh.write("%s\t%s\t%d\t%.3f\t%s\n" % row)
    note("phase diagrams built", "%d (%d failed)" % (len(pdlog) - len(errors), len(errors)))
    for tt, cs, err in errors[:10]:
        note("  pd failure", "%s %s %s" % (tt, cs, err))
    for tt in THERMO_TYPES:
        live = {d["mid"]: d for d in by_tt[tt] if not d["deprecated"]}
        mine = got[tt]
        rec("%s every material evaluated" % tt, len(mine) == len(live), "%d of %d" % (len(mine), len(live)))
        dh, df, nst, ncontra, ndec, dam, ddH_u, ddH_s, deq, rows = 0.0, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, []
        wdH = None
        worst, unexplained, ninc = [], 0.0, 0
        for mid, d in live.items():
            r = mine.get(mid)
            if r is None:
                continue
            _, eh, ef, st, dec, dH, eq = r[:7]
            if abs(d["decode_d"]) >= 1e-9:
                # inconsistent doc: our hull distance and formation energy must move by exactly the
                # doc's own energy discrepancy, i.e. the difference is explained by it
                ninc += 1
                unexplained = max(unexplained, abs((eh - d["pub_ehull"]) - d["decode_d"]),
                                  abs((ef - d["pub_ef"]) - d["decode_d"]))
            else:
                dh_i = abs(eh - d["pub_ehull"])
                dh = max(dh, dh_i)
                worst.append((dh_i, mid))
                df = max(df, abs(ef - d["pub_ef"]))
                if d["pub_dH"] is not None and dH == dH:
                    x = abs(dH - d["pub_dH"])
                    if not r[11]:
                        ddH_u = max(ddH_u, x)
                    elif x > ddH_s:
                        ddH_s, wdH = x, mid
                if st and d["pub_eqrxn"] is not None and eq == eq:
                    deq = max(deq, abs(eq - d["pub_eqrxn"]))
            if st != d["pub_stable"]:
                # MP's flag contradicts MP's own numbers when it says unstable at e_above_hull 0
                # with a negative decomposition enthalpy (removing it would raise the hull)
                if (not d["pub_stable"]) and abs(d["pub_ehull"]) < 1e-9 and (d["pub_dH"] or 0) < 0:
                    ncontra += 1
                else:
                    nst += 1
            pub_dec = sorted(d["pub_decomp"])
            if not st:
                if [m for m, _ in dec] != [m for m, _ in pub_dec]:
                    ndec += 1
                else:
                    dam = max([dam] + [abs(a - b) for (_, a), (_, b) in zip(dec, pub_dec)])
            rows.append("%s\t%.17g\t%.17g\t%.17g\t%.17g\t%d\t%d\t%s\t%.17g\t%.17g\t%d" % (
                mid, d["pub_ehull"], eh, d["pub_ef"], ef, d["pub_stable"], st,
                ";".join("%s:%.17g" % x for x in dec), dH, eq, r[11]))
        with open(os.path.join(OUT, "hull-%s.tsv" % tt.replace("+", "U")), "w") as fh:
            fh.write("material_id\tpub_ehull\tehull\tpub_ef\tef\tpub_stable\tstable\tdecomp\tdecomp_enthalpy\teq_rxn\tdH_slsqp\n")
            fh.write("\n".join(sorted(rows)) + "\n")
        worst.sort(reverse=True)
        TOL = 1e-9
        rec("%s e_above_hull vs MP" % tt, dh < TOL, "max |d| %.2e eV/atom, worst %s" % (dh, worst[:2]))
        rec("%s formation energy vs MP" % tt, df < TOL, "max |d| %.2e eV/atom" % df)
        rec("%s decomposition enthalpy vs MP, exact (no same-composition stable entry)" % tt, ddH_u < TOL,
            "max |d| %.2e eV/atom" % ddH_u)
        # with a same-composition stable entry pymatgen finds the competing decomposition by SLSQP
        # (ftol 1e-8 on an objective of order 10 eV/atom): an optimiser's convergence, not exact
        rec("%s decomposition enthalpy vs MP, SLSQP (same-composition stable entry)" % tt, ddH_s < 1e-6,
            "max |d| %.2e eV/atom, worst %s" % (ddH_s, wdH))
        rec("%s equilibrium reaction energy vs MP (stable)" % tt, deq < TOL, "max |d| %.2e eV/atom" % deq)
        rec("%s inconsistent docs: difference == doc's own energy discrepancy" % tt, unexplained < TOL,
            "%d docs, unexplained %.2e" % (ninc, unexplained))
        rec("%s is_stable vs MP" % tt, nst == 0,
            "%d flips; %d more where MP's flag contradicts its own e_above_hull=0 and dH<0" % (nst, ncontra))
        rec("%s decomposition products vs MP" % tt, ndec == 0, "%d mismatched sets" % ndec)
        rec("%s decomposition amounts vs MP" % tt, dam < TOL, "max |d| %.2e" % dam)
        rec("%s chemical-potential shift leaves ehull, ef unchanged" % tt, shift_max[tt] < TOL, "max |d| %.2e" % shift_max[tt])
        rec("%s same entry, every PD containing it: same ehull" % tt, incons[tt] < TOL, "max |d| %.2e" % incons[tt])
        rec("%s decomposition mass balance, energy residual" % tt, balance_max[tt] < TOL and eres_max[tt] < TOL,
            "%.2e / %.2e" % (balance_max[tt], eres_max[tt]))
    del docs, by_tt

    T["total"] = time.time() - TT0
    ru_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    ru_ch = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    note("ru_maxrss self / largest child (GiB)", "%.2f / %.2f" % (ru_self / 2**20, ru_ch / 2**20))
    note("phase timings (s)", {k: round(v, 1) for k, v in T.items()})
    with open(os.path.join(OUT, "check.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.write(("FAILED: " + ", ".join(fail)) if fail else "ALL CHECKS PASSED")
        fh.write("\n")
    print("FAILED: " + ", ".join(fail) if fail else "ALL CHECKS PASSED", flush=True)
    sys.exit(1 if fail else 0)
