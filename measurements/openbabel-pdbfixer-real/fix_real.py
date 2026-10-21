"""openbabel-pdbfixer-real, part 2: PDBFixer prepares a cohort of deposited PDB entries for MD.

    python3 -u fix_real.py <mmCIF dir> <out dir> <workers> [ids file]
    python3 -u fix_real.py --one <entry.cif.gz> <out dir>          (one entry; what each worker runs)

Each entry runs in its own process under a fixed address-space limit (MEM_GIB, 16 GiB), the way a
cluster job gets a memory limit. An entry whose preparation needs more fails with a memory error
that does not depend on what else the box is running; a process that dies is classified by its
signal. Two declared limits classify an entry instead of preparing it, the same on every chip: more
than GAP_MAX (200) internal missing residues (rebuilding measured ~5-10 s per residue), or more than
MISSING_MAX (5,000) missing atoms to build (the one such entry not already over GAP_MAX, 7abh, needs
9,705 atoms added to 17,922 deposited and ran for 1 h 50 min without finishing; the next largest
need is 2,341, prepared in 335 s). The largest entries (over BIG bytes gzipped) run at most BIG_SLOTS at a time.

Every *.cif.gz in the directory (or those named in the ids file) goes through PDBFixer's documented
preparation sequence: findMissingResidues (minus the chain-terminal ones, as PDBFixer's manual does),
findNonstandardResidues, replaceNonstandardResidues,
removeHeterogens(keepWater=True), findMissingAtoms, addMissingAtoms, addMissingHydrogens(7.0).
Then, per prepared entry:

  * deposited atoms kept: every heavy atom PDBFixer did not remove is still there, same residue,
    same name, and has not moved (beyond float32 storage of the coordinate);
  * bookkeeping: heavy atoms out == deposited heavy atoms kept + the heavy atoms PDBFixer said it
    would add (missing atoms, missing terminals, missing residues' template atoms);
  * fixed point: PDBFixer on its own output finds no missing atoms and adds no hydrogens;
  * the prepared structure parametrises: Amber14 + TIP3P-FB templates match every residue, and the
    force field's total charge is an integer equal to the charge counted from the protonation
    states PDBFixer chose (an independent rule over atom names, see charge_from_names);
  * a second code reads it: Open Babel, reading PDBFixer's PDB output, counts the same atoms,
    the same elements and the same hydrogens;
  * format round trip: the written PDBx/mmCIF reads back with the same atoms.
Entries PDBFixer or the force field cannot handle are classified with their reason, not dropped.
"""
import collections
import gzip
import hashlib
import inspect
import io
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import resource
import shutil
import sys
import time
import traceback

ONE = sys.argv[1] == "--one"
if ONE:
    SRC, OUT, WORKERS, IDS = sys.argv[2], sys.argv[3], 1, ""
else:
    SRC, OUT, WORKERS = sys.argv[1], sys.argv[2], int(sys.argv[3])
    IDS = sys.argv[4] if len(sys.argv) > 4 else ""
MEM_GIB = int(os.environ.get("MEM_GIB", "16"))
GAP_MAX = int(os.environ.get("GAP_MAX", "200"))
MISSING_MAX = int(os.environ.get("MISSING_MAX", "5000"))
BIG = int(os.environ.get("BIG", str(1 << 20)))
BIG_SLOTS = int(os.environ.get("BIG_SLOTS", "2"))
WORK = os.environ.get("WORKDIR", "/var/tmp/fix")
AA = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET",
      "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL"}
NA = {"DA", "DC", "DG", "DT", "A", "C", "G", "U"}
COLS = ["id", "status", "reason", "chains", "residues_in", "heavy_in", "heavy_removed", "heavy_kept",
        "nonstandard_replaced", "terminal_residues_dropped", "gaps", "longest_gap", "missing_residues", "missing_atoms", "missing_terminals",
        "heavy_expected", "heavy_out", "h_out", "atoms_out", "kept_missing", "kept_max_shift_A",
        "refix_missing", "refix_h_added", "ff_charge", "names_charge", "ff_charge_dev",
        "ob_atoms", "ob_h", "ob_elements_eq", "cif_rt_atoms", "waters", "t_atoms_s", "t_h_s", "t_fix_s", "t_ff_s", "t_total_s",
        "maxrss_kib", "out_sha256"]


def heavy_key(a):
    r = a.residue
    return (r.chain.id, r.id, r.insertionCode, r.name, a.name)


def charge_from_names(top):
    """Net charge from the protonation state each residue was given, read off its atom names only."""
    q = 0
    for r in top.residues():
        n = {a.name for a in r.atoms()}
        if r.name in AA:
            q += {"ARG": 1}.get(r.name, 0)
            if r.name == "LYS" and {"HZ1", "HZ2", "HZ3"} <= n:
                q += 1
            if r.name == "HIS" and {"HD1", "HE2"} <= n:
                q += 1
            if r.name == "ASP" and "HD2" not in n:
                q -= 1
            if r.name == "GLU" and "HE2" not in n:
                q -= 1
            if r.name == "CYS" and "HG" not in n:
                bonded = any({a.name, b.name} == {"SG"} and a.residue is not b.residue
                             for a, b in top.bonds() if a.residue is r or b.residue is r)
                q -= 0 if bonded else 1
            if {"H2", "H3"} <= n:          # charged N terminus (NH3+), PRO's included
                q += 1
            if "OXT" in n:                 # charged C terminus (COO-)
                q -= 1
        elif r.name in NA and "P" in n:    # one phosphodiester per phosphorus
            q -= 1
    return q


def one(path):
    from openmm import app, unit, NonbondedForce
    from pdbfixer import PDBFixer
    from openbabel import openbabel as ob
    ob.obErrorLog.StopLogging()
    pid = os.path.basename(path).split(".")[0]
    row = dict.fromkeys(COLS, "")
    row["id"] = pid
    t0 = time.time()
    wd = os.path.join(WORK, pid)
    os.makedirs(wd, exist_ok=True)
    stage = "read"
    try:
        cif = os.path.join(wd, pid + ".cif")
        with gzip.open(path, "rb") as fi, open(cif, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        fx = PDBFixer(filename=cif)
        top0 = fx.topology
        row["chains"] = sum(1 for _ in top0.chains())
        row["residues_in"] = sum(1 for _ in top0.residues())
        row["heavy_in"] = sum(1 for a in top0.atoms() if a.element is not None and a.element.symbol != "H")
        stage = "residues"
        fx.findMissingResidues()
        # PDBFixer's manual: drop missing residues at a chain's ends -- building a disordered tail
        # adds atoms nobody observed, and minimising it dominates the run (measured: 2,127 s for
        # one 17,764-atom entry). Internal gaps are kept and rebuilt.
        chains = list(fx.topology.chains())
        for key in list(fx.missingResidues):
            if key[1] == 0 or key[1] == len(list(chains[key[0]].residues())):
                row["terminal_residues_dropped"] = int(row["terminal_residues_dropped"] or 0) + len(fx.missingResidues[key])
                del fx.missingResidues[key]
        row["terminal_residues_dropped"] = int(row["terminal_residues_dropped"] or 0)
        internal = sum(len(v) for v in fx.missingResidues.values())
        row["gaps"] = len(fx.missingResidues)
        row["longest_gap"] = max([len(v) for v in fx.missingResidues.values()] or [0])
        if internal > GAP_MAX:
            # Rebuilding is ~5-10 s per missing residue (measured, addMissingAtoms), so an entry
            # with thousands unmodelled is hours of loop building PDBFixer cannot validate; such
            # entries need a modelling tool first. Declared up front, the same on every chip.
            row["missing_residues"] = internal
            row["status"] = "skipped_gaps"
            row["reason"] = "%d internal missing residues > GAP_MAX %d" % (internal, GAP_MAX)
            row["t_total_s"] = "%.2f" % (time.time() - t0)
            shutil.rmtree(wd, ignore_errors=True)
            return row
        fx.findNonstandardResidues()
        row["nonstandard_replaced"] = len(fx.nonstandardResidues)
        fx.replaceNonstandardResidues()
        fx.removeHeterogens(keepWater=True)
        kept = {}
        for a, p in zip(fx.topology.atoms(), fx.positions.value_in_unit(unit.angstrom)):
            if a.element is not None and a.element.symbol != "H":
                kept[heavy_key(a)] = p
        row["heavy_kept"] = len(kept)
        row["heavy_removed"] = row["heavy_in"] - len(kept)   # heterogens (water kept)
        stage = "atoms"
        fx.findMissingAtoms()
        mres = sum(len(v) for v in fx.missingResidues.values())
        matoms = sum(len(v) for v in fx.missingAtoms.values())
        mterm = sum(len(v) for v in fx.missingTerminals.values())
        row["missing_residues"], row["missing_atoms"], row["missing_terminals"] = mres, matoms, mterm
        # heavy atoms each missing residue will bring, from PDBFixer's own templates
        mres_heavy = 0
        for names in fx.missingResidues.values():
            for nm in names:
                t = fx.templates[nm]
                mres_heavy += sum(1 for a in t.topology.atoms() if a.element is not None and a.element.symbol != "H")
        row["heavy_expected"] = len(kept) + matoms + mterm + mres_heavy
        if matoms + mterm > MISSING_MAX:
            row["status"] = "skipped_atoms"
            row["reason"] = "%d missing atoms > MISSING_MAX %d" % (matoms + mterm, MISSING_MAX)
            row["t_total_s"] = "%.2f" % (time.time() - t0)
            shutil.rmtree(wd, ignore_errors=True)
            return row
        kw = {"seed": 0} if "seed" in inspect.signature(fx.addMissingAtoms).parameters else {}
        t1 = time.time()
        fx.addMissingAtoms(**kw)
        row["t_atoms_s"] = "%.2f" % (time.time() - t1)
        stage = "hydrogens"
        t1 = time.time()
        fx.addMissingHydrogens(7.0)
        row["t_h_s"] = "%.2f" % (time.time() - t1)
        row["t_fix_s"] = "%.2f" % (time.time() - t0)
        top, pos = fx.topology, fx.positions
        heavy = [(a, p) for a, p in zip(top.atoms(), pos.value_in_unit(unit.angstrom))
                 if a.element is not None and a.element.symbol != "H"]
        row["heavy_out"] = len(heavy)
        row["atoms_out"] = top.getNumAtoms()
        row["h_out"] = row["atoms_out"] - len(heavy)
        row["waters"] = sum(1 for r in top.residues() if r.name == "HOH")
        out = {heavy_key(a): p for a, p in heavy}
        miss = [k for k in kept if k not in out]
        row["kept_missing"] = len(miss)
        row["kept_max_shift_A"] = "%.3g" % max([max(abs(out[k][i] - kept[k][i]) for i in range(3))
                                                for k in kept if k in out] or [0.0])

        stage = "write"
        pdbx = os.path.join(wd, pid + ".fixed.cif")
        with open(pdbx, "w") as f:
            app.PDBxFile.writeFile(top, pos, f, keepIds=True)
        row["out_sha256"] = hashlib.sha256(open(pdbx, "rb").read()).hexdigest()
        rt = app.PDBxFile(pdbx)
        row["cif_rt_atoms"] = rt.topology.getNumAtoms()

        stage = "refix"
        fx2 = PDBFixer(filename=pdbx)
        fx2.findMissingResidues()
        fx2.missingResidues = {}    # the written file carries no SEQRES, so residues are not re-asked
        fx2.findMissingAtoms()
        row["refix_missing"] = sum(len(v) for v in fx2.missingAtoms.values()) + \
            sum(len(v) for v in fx2.missingTerminals.values())
        n2 = fx2.topology.getNumAtoms()
        fx2.addMissingHydrogens(7.0)
        row["refix_h_added"] = fx2.topology.getNumAtoms() - n2

        stage = "openbabel"
        if row["atoms_out"] <= 99999:
            pdb = os.path.join(wd, pid + ".fixed.pdb")
            with open(pdb, "w") as f:
                app.PDBFile.writeFile(top, pos, f, keepIds=True)
            m = ob.OBMol()
            c = ob.OBConversion()
            c.SetInFormat("pdb")
            c.ReadFile(m, pdb)
            el = collections.Counter(a.GetAtomicNum() for a in ob.OBMolAtomIter(m))
            row["ob_atoms"] = m.NumAtoms()
            row["ob_h"] = el.get(1, 0)
            mine = collections.Counter(a.element.atomic_number for a in top.atoms())
            row["ob_elements_eq"] = int(el == mine)
        else:
            row["ob_atoms"] = row["ob_h"] = row["ob_elements_eq"] = "pdb_limit"

        stage = "forcefield"
        t1 = time.time()
        ff = app.ForceField("amber14-all.xml", "amber14/tip3pfb.xml")
        system = ff.createSystem(top, nonbondedMethod=app.NoCutoff, constraints=None, rigidWater=False)
        nb = [f for f in system.getForces() if isinstance(f, NonbondedForce)][0]
        qff = sum(nb.getParticleParameters(i)[0].value_in_unit(unit.elementary_charge)
                  for i in range(nb.getNumParticles()))
        row["t_ff_s"] = "%.2f" % (time.time() - t1)
        qn = charge_from_names(top)
        row["ff_charge"] = "%.6f" % qff
        row["names_charge"] = qn
        row["ff_charge_dev"] = "%.3g" % abs(qff - qn)
        row["status"] = "prepared"
    except Exception as e:
        row["status"] = "failed_" + stage
        row["reason"] = (type(e).__name__ + ": " + str(e).splitlines()[0] if str(e) else type(e).__name__)[:200].replace("\t", " ")
        with open(os.path.join(OUT, "tracebacks.txt"), "a") as f:
            f.write("== %s (%s)\n%s\n" % (pid, stage, traceback.format_exc()))
    row["t_total_s"] = "%.2f" % (time.time() - t0)
    shutil.rmtree(wd, ignore_errors=True)    # built by this task, never a staged input
    return row


def cap():
    lim = MEM_GIB << 30
    resource.setrlimit(resource.RLIMIT_AS, (lim, lim))


def run(path):
    """One entry in its own capped process; its row comes back as the last line of stdout."""
    t0 = time.time()
    p = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "--one", path, OUT],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=cap, text=True)
    out, err = p.communicate()
    lines = [l for l in out.splitlines() if l.startswith("ROW ")]
    if p.returncode == 0 and lines:
        row = json.loads(lines[-1][4:])
    else:
        row = dict.fromkeys(COLS, "")
        row["id"] = os.path.basename(path).split(".")[0]
        row["status"] = "failed_process"
        tail = (err.strip().splitlines() or [""])[-1]
        row["reason"] = ("signal %d" % -p.returncode if p.returncode < 0 else "exit %d" % p.returncode) \
            + ("; " + tail[:150] if tail else "")
        row["t_total_s"] = "%.2f" % (time.time() - t0)
    return row


def main():
    os.makedirs(OUT, exist_ok=True)
    files = sorted(f for f in os.listdir(SRC) if f.endswith(".cif.gz"))
    if IDS:
        want = {l.strip() for l in open(IDS) if l.strip()}
        files = [f for f in files if f.split(".")[0] in want]
    # largest first, so the long entries do not set the tail; then by id for a fixed order
    files.sort(key=lambda f: (-os.path.getsize(os.path.join(SRC, f)), f))
    print("entries %d workers %d" % (len(files), WORKERS), flush=True)
    t0 = time.time()
    rows = []
    # rows are also written as they finish, so a run that dies keeps what it did
    part = open(os.path.join(OUT, "entries.partial.tsv"), "w")
    part.write("\t".join(COLS) + "\n")
    paths = [os.path.join(SRC, f) for f in files]
    big = [x for x in paths if os.path.getsize(x) > BIG]
    small = [x for x in paths if os.path.getsize(x) <= BIG]
    print("big entries (> %d bytes) %d in %d slots; the rest in %d" % (BIG, len(big), BIG_SLOTS, WORKERS - BIG_SLOTS), flush=True)
    with ThreadPoolExecutor(BIG_SLOTS) as bp, ThreadPoolExecutor(WORKERS - BIG_SLOTS) as sp:
        futs = [bp.submit(run, x) for x in big] + [sp.submit(run, x) for x in small]
        for fu in as_completed(futs):
            row = fu.result()
            rows.append(row)
            part.write("\t".join(str(row[c]) for c in COLS) + "\n")
            part.flush()
            print("%-5s %-18s %8s s %8s atoms  %s" % (row["id"], row["status"], row["t_total_s"],
                                                     row["atoms_out"], row["reason"][:90]), flush=True)
    wall = time.time() - t0
    rows.sort(key=lambda r: r["id"])
    with open(os.path.join(OUT, "entries.tsv"), "w") as f:
        f.write("\t".join(COLS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]) for c in COLS) + "\n")

    import openmm
    import pdbfixer
    from openbabel import openbabel as ob
    prep = [r for r in rows if r["status"] == "prepared"]
    done = [r for r in rows if r["status"] in ("prepared", "failed_forcefield")]
    st = collections.Counter(r["status"] for r in rows)
    with open(os.path.join(OUT, "metrics.txt"), "w") as m:
        m.write("openmm_version %s\npdbfixer_version %s\nopenbabel_version %s\n" % (
            str(getattr(openmm, "__version__", "unknown")), str(getattr(pdbfixer, "__version__", "unknown")),
            str(ob.OBReleaseVersion())))
        m.write("missing_max %d\n" % MISSING_MAX)
        m.write("gap_max %d\nmem_gib_per_entry %d\nbig_bytes %d\nbig_slots %d\n" % (GAP_MAX, MEM_GIB, BIG, BIG_SLOTS))
        m.write("workers %d\nworkload_wall_s %.1f\nentries %d\n" % (WORKERS, wall, len(rows)))
        for k, v in sorted(st.items()):
            m.write("status_%s %d\n" % (k, v))
        m.write("sum_entry_s %.1f\n" % sum(float(r["t_total_s"]) for r in rows))
        m.write("max_entry_maxrss_kib %d\n" % max([int(r["maxrss_kib"] or 0) for r in rows] or [0]))
        for k in ("heavy_in", "heavy_kept", "heavy_out", "h_out", "atoms_out", "missing_residues",
                  "missing_atoms", "missing_terminals", "nonstandard_replaced", "terminal_residues_dropped", "waters"):
            m.write("fixed_sum_%s %d\n" % (k, sum(int(r[k] or 0) for r in done)))
        m.write("fixed_max_kept_shift_A %s\n" % max([float(r["kept_max_shift_A"]) for r in done] or [0]))
        m.write("prepared_max_ff_charge_dev %s\n" % max([float(r["ff_charge_dev"]) for r in prep] or [0]))
        m.write("entries_sha256 %s\n" % hashlib.sha256(
            "".join("%s\t%s\n" % (r["id"], r["out_sha256"]) for r in done).encode()).hexdigest())
    print(open(os.path.join(OUT, "metrics.txt")).read(), flush=True)

    fails = []

    def check(name, ok):
        ok = bool(ok)
        print("%-74s %s" % (name, "ok" if ok else "FAIL"), flush=True)
        if not ok:
            fails.append(name)

    # Structural claims cover every entry PDBFixer finished, including those the force field then
    # refused; the charge claim needs a parametrised system, so it covers the prepared ones.
    fixed = [r for r in rows if r["status"] in ("prepared", "failed_forcefield")]

    def every(name, pred, over=None):
        over = fixed if over is None else over
        bad = [r["id"] for r in over if not pred(r)]
        check("%s (%d)%s" % (name, len(over), (": " + " ".join(bad[:10])) if bad else ""), not bad)

    check("every entry classified: prepared or failed with a reason",
          len(rows) == len(files) and all(r["status"] == "prepared" or r["reason"] for r in rows))
    check("at least one entry prepared", prep)
    every("deposited heavy atoms all kept", lambda r: int(r["kept_missing"]) == 0)
    # Positions go through OpenMM as float32 in a Context; |x| < 1000 A, so half an ulp is < 3.1e-5 A.
    every("deposited heavy atoms not moved (<= 1e-4 A, float32 storage)", lambda r: float(r["kept_max_shift_A"]) <= 1e-4)
    every("heavy atoms out == kept + what PDBFixer said it would add", lambda r: int(r["heavy_out"]) == int(r["heavy_expected"]))
    every("fixed point: PDBFixer on its own output finds nothing missing", lambda r: int(r["refix_missing"]) == 0)
    every("fixed point: and adds no hydrogens", lambda r: int(r["refix_h_added"]) == 0)
    every("PDBx written and read back with the same atoms", lambda r: int(r["cif_rt_atoms"]) == int(r["atoms_out"]))
    # Amber residue templates carry 4-decimal charges summing to an integer per residue.
    every("force-field charge == charge from protonation states (|dev| < 1e-3 e)",
          lambda r: float(r["ff_charge_dev"]) < 1e-3, over=prep)
    every("Open Babel reads the same atoms, hydrogens and elements",
          lambda r: r["ob_atoms"] == "pdb_limit" or (int(r["ob_atoms"]) == int(r["atoms_out"])
                                                      and int(r["ob_h"]) == int(r["h_out"]) and r["ob_elements_eq"] == 1))
    if fails:
        print("FAILED: %s" % "; ".join(fails), flush=True)
        sys.exit(1)
    print("PDBFIXER REAL OK", flush=True)


if __name__ == "__main__":
    if ONE:
        r = one(SRC)
        r["maxrss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print("ROW " + json.dumps(r), flush=True)
    else:
        main()
