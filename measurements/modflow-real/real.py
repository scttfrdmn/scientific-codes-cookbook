#!/usr/bin/env python3
"""modflow-real: the USGS Mobile Bay MODFLOW 6 model (doi:10.5066/P1XKGCSZ), both published
simulations, solved by the recipe's mf6 6.8.1 and checked against itself and the archive.

    python3 -u real.py

Reads the staged archive in /tmp/archive, works in /var/tmp/w (root disk, not the tmpfs), writes
everything worth keeping to /tmp/out. Runs the two simulations (postcal = the calibrated model the
report presents, precal = its pre-calibration parameters) concurrently, one mf6 process each.

Asserted here, per simulation: completion; the head and budget files have one record per stress
period with finite heads on every active cell; FLOW-JA-FACE is exactly antisymmetric; the binary
budget's totals equal the listing's to the listing's printed precision; and the GWF, SFR and UZF
budgets each close within 1% on every converged step. The comparison against the archive's own
6.3.0 heads is computed and written here and asserted in compare.py, across chips.
"""
import hashlib
import os
import re
import resource
import shutil
import subprocess
import sys
import time
import zipfile

import numpy as np
import flopy

ARCH, W, O = "/tmp/archive", "/var/tmp/w", "/tmp/out"
CASES = ["postcal", "precal"]
NPER = 97
PROBE_NPER = int(os.environ.get("MF_PROBE_NPER", "0") or 0)
os.makedirs(O, exist_ok=True)
FAILS = []
SUMMARY = open(os.path.join(O, "results.tsv"), "w")


def rec(*v):
    """Report a value. str() every field, so a tuple-like value can never become format args."""
    line = "\t".join(str(x) for x in v)
    print(line)
    SUMMARY.write(line + "\n")
    SUMMARY.flush()


def check(case, name, ok, detail):
    ok = bool(ok)   # numpy booleans: never `ok is False`
    rec(case, "check", "PASS" if ok else "FAIL", name, detail)
    if not ok:
        FAILS.append("%s: %s" % (case, name))


# ------------------------------------------------------------------ unpack
t0 = time.time()
with zipfile.ZipFile(os.path.join(ARCH, "model.zip")) as z:
    for case in CASES:
        pre = "model/input_Mobile_Bay_%s/" % case
        n = 0
        for i in z.infolist():
            if i.filename.startswith(pre) and not i.is_dir():
                dst = os.path.join(W, case, i.filename[len(pre):])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(z.read(i))
                n += 1
        rec(case, "input_files", n)
with zipfile.ZipFile(os.path.join(ARCH, "output.zip")) as z:
    for case in CASES:
        pre = "output/output_Mobile_Bay_%s/" % case
        for name in ["mobile_bay.hds", "mobile_bay.gwf.head.obs.csv", "mobile_bay.gwf.sfr.obs.csv",
                     "mobile_bay.lst", "mfsim.lst"]:
            dst = os.path.join(W, "ref", case, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(z.read(pre + name))
rec("all", "unpack_s", "%.1f" % (time.time() - t0))

# The archive was written on Windows: its package files name their data as `external_data\x.txt`
# and `.\mobile_bay.dis`. mf6 does not translate separators on Linux (Sim.f90 looks for '\' only
# when the OS is Windows), so the separators are rewritten to '/'. That is the only edit, and every
# rewritten character must sit on a line that names a file.
for case in CASES:
    nrep, bad = 0, []
    for fn in sorted(os.listdir(os.path.join(W, case))):
        p = os.path.join(W, case, fn)
        if not os.path.isfile(p):
            continue
        lines = open(p, encoding="latin-1").read().split("\n")
        for k, line in enumerate(lines):
            if "\\" in line:
                if not re.search(r"OPEN/CLOSE|^\s*\w+6\s+\.\\", line, re.I):
                    bad.append("%s:%d" % (fn, k + 1))
                nrep += line.count("\\")
                lines[k] = line.replace("\\", "/")
        open(p, "w", encoding="latin-1").write("\n".join(lines))
    check(case, "path separators rewritten only on file-naming lines", not bad,
          "%d separators; off-path: %s" % (nrep, ",".join(bad[:5]) or "none"))
    # The second and last edit: SAVE_FLOWS in the model's name-file options, so the budget file
    # carries EVERY term. As archived, STO and WEL save nothing, and a budget missing its storage
    # cannot close. SAVE_FLOWS selects what is written, never what is solved.
    p = os.path.join(W, case, "mobile_bay.nam")
    t = open(p).read()
    t2 = re.sub(r"(BEGIN options\s*\n)", r"\1  SAVE_FLOWS\n", t, count=1, flags=re.I)
    check(case, "SAVE_FLOWS added to the name file, nothing else", t2 != t and t2.replace("  SAVE_FLOWS\n", "", 1) == t,
          "mobile_bay.nam options: " + " ".join(re.search(r"BEGIN options(.*?)END options", t2, re.S | re.I).group(1).split()))
    open(p, "w").write(t2)
    if PROBE_NPER:
        # Probe only: truncate the simulation to its first PROBE_NPER stress periods.
        p = os.path.join(W, case, "mobile_bay.tdis")
        t = open(p).read()
        head, rest = t.split("BEGIN perioddata", 1)
        rows, tail = rest.split("END perioddata", 1)
        rows = [r for r in rows.split("\n") if r.strip()][:PROBE_NPER]
        t = re.sub(r"NPER\s+\d+", "NPER  %d" % PROBE_NPER, head) + "BEGIN perioddata\n" + "\n".join(rows) + "\nEND perioddata" + tail
        open(p, "w").write(t)
        rec(case, "PROBE_truncated_to_nper", PROBE_NPER)
nper = PROBE_NPER or NPER

# ------------------------------------------------------------------ solve, both concurrently
procs = {}
for case in CASES:
    f = open(os.path.join(O, "mf6-%s.stdout" % case), "w")
    p = subprocess.Popen(["mf6"], cwd=os.path.join(W, case), stdout=f, stderr=subprocess.STDOUT)
    procs[p.pid] = (case, time.time(), f)
    rec(case, "mf6_started_pid", p.pid)
while procs:
    pid, status, ru = os.wait4(-1, 0)
    if pid not in procs:
        continue
    case, ts, f = procs.pop(pid)
    f.close()
    rc = os.waitstatus_to_exitcode(status)
    rec(case, "mf6_wall_s", "%.1f" % (time.time() - ts))
    rec(case, "mf6_exit", rc)
    rec(case, "mf6_peak_rss_mib", "%.1f" % (ru.ru_maxrss / 1024.0))
    rec(case, "mf6_user_s", "%.1f" % ru.ru_utime)
rec("all", "solve_done_s", "%.1f" % (time.time() - t0))


# ------------------------------------------------------------------ helpers
def list_budgets(path):
    """{(label, kper): (total_in, total_out)} from a listing's budget blocks, RATE column.

    label is GWF (the model's VOLUME BUDGET), SFR_0 or UZF-1. Rates are printed in F format with
    four decimals, which is the precision the binary comparison is held to.
    """
    out = {}
    txt = open(path, encoding="latin-1").read()
    for m in re.finditer(r"^\s*(\S+) BUDGET FOR ENTIRE MODEL AT END OF TIME STEP\s+(\d+), STRESS PERIOD\s+(\d+)", txt, re.M):
        label = "GWF" if m.group(1) == "VOLUME" else m.group(1)
        seg = txt[m.end():m.end() + 20000]
        ti = re.search(r"TOTAL IN =\s*(\S+)\s+TOTAL IN =\s*(\S+)", seg)
        to = re.search(r"TOTAL OUT =\s*(\S+)\s+TOTAL OUT =\s*(\S+)", seg)
        terms = {}
        side = None
        for line in seg[:to.end()].split("\n"):
            if re.match(r"^\s*IN:", line):
                side = 0
            elif re.match(r"^\s*OUT:", line):
                side = 1
            t = re.match(r"^\s*(\S+) =\s*\S+\s+\S+ =\s*(\S+)", line)
            if t and side is not None and not t.group(1).startswith("TOTAL"):
                terms.setdefault(t.group(1), [0.0, 0.0])[side] += float(t.group(2))
        out[(label, int(m.group(3)))] = (float(ti.group(2)), float(to.group(2)), terms)
    return out


def nonconverged(path):
    txt = open(path, encoding="latin-1").read()
    return sorted({int(a) for a, b in re.findall(r"Solution 1 did not converge for stress period\s+(\d+) and time step\s+(\d+)", txt)})


def transpose_index(ia, ja):
    """For each CSR entry (n, m) the index of (m, n); diagonal entries map to themselves."""
    nodes = len(ia) - 1
    rows = np.repeat(np.arange(nodes), np.diff(ia))
    key = rows.astype(np.int64) * nodes + ja
    order = np.argsort(key, kind="stable")
    tkey = ja.astype(np.int64) * nodes + rows
    pos = np.searchsorted(key[order], tkey)
    t = order[pos]
    assert np.array_equal(key[t], tkey), "connectivity is not symmetric"
    return rows, t


# ------------------------------------------------------------------ check each simulation
for case in CASES:
    ws = os.path.join(W, case)
    so = open(os.path.join(O, "mf6-%s.stdout" % case), encoding="latin-1").read()
    m = re.search(r"VERSION\s+(\S+)", so)
    rec(case, "mf6_version_running", m.group(1) if m else "unknown")
    check(case, "mf6 6.8.1 ran", m and m.group(1) == "6.8.1", m.group(1) if m else "no banner")
    check(case, "completion sentinel on stdout", "Normal termination of simulation" in so,
          "Normal termination of simulation")
    mfsim = os.path.join(ws, "mfsim.lst")
    nc, ncref = nonconverged(mfsim), nonconverged(os.path.join(W, "ref", case, "mfsim.lst"))
    rec(case, "nonconverged_periods", ",".join(map(str, nc)) or "none")
    rec(case, "nonconverged_periods_archive_6.3.0", ",".join(map(str, [p for p in ncref if p <= nper])) or "none")

    # -- the grid, from mf6's own binary grid file
    grb = flopy.mf6.utils.MfGrdFile(os.path.join(ws, "mobile_bay.dis.grb"), verbose=False)
    idomain = np.asarray(grb.idomain).reshape(-1)
    active = idomain > 0
    ia, ja = np.asarray(grb.ia), np.asarray(grb.ja)
    if ia[0] == 1:   # flopy versions differ on whether ia/ja come back 1-based
        ia, ja = ia - 1, ja - 1
    # mf6 writes a DIS grid file's IA/JA in USER node numbering (every cell, the inactive ones with
    # empty rows); FLOW-JA-FACE follows the same entry order. So everything below is in user nodes.
    nodes = len(ia) - 1
    rec(case, "grid", "nlay 1 nrow 260 ncol 232; active %d of %d; connections %d" % (active.sum(), idomain.size, len(ja)))
    has_row = np.diff(ia) > 0
    check(case, "connectivity rows are exactly the active cells", nodes == idomain.size and np.array_equal(has_row, active),
          "%d user nodes, %d with connections, %d active" % (nodes, has_row.sum(), active.sum()))
    rows, tidx = transpose_index(ia, ja)
    offd = rows != ja
    user_of_reduced = np.nonzero(active)[0]   # reduced node k is user node user_of_reduced[k]

    # -- heads: one record per period, finite on every active cell; and against the archive
    hf = flopy.utils.HeadFile(os.path.join(ws, "mobile_bay.hds"))
    hr = flopy.utils.HeadFile(os.path.join(W, "ref", case, "mobile_bay.hds"))
    kk = hf.get_kstpkper()
    check(case, "one head record per stress period", len(kk) == nper and [p for s, p in kk] == list(range(nper)),
          "%d records" % len(kk))
    hsha = open(os.path.join(O, "heads-%s.sha256" % case), "w")
    vt = open(os.path.join(O, "vs-archive-%s.tsv" % case), "w")
    vt.write("kper\tmax_abs_dh\tp99_abs_dh\tmedian_abs_dh\tcells_over_0.1m\tconverged_here\tconverged_archive\n")
    worst_bad = 0
    for s, p in kk:
        h = hf.get_data(kstpkper=(s, p)).reshape(-1)
        bad = int((~np.isfinite(h[active]) | (np.abs(h[active]) >= 1e29)).sum())
        worst_bad = max(worst_bad, bad)
        hsha.write("%d\t%s\n" % (p + 1, hashlib.sha256(h.tobytes()).hexdigest()))
        r = hr.get_data(kstpkper=(s, p)).reshape(-1)
        d = np.abs(h[active] - r[active])
        vt.write("%d\t%.6e\t%.6e\t%.6e\t%d\t%d\t%d\n" % (p + 1, d.max(), np.percentile(d, 99), np.median(d),
                                                       int((d > 0.1).sum()), (p + 1) not in nc, (p + 1) not in ncref))
    hsha.close()
    vt.close()
    check(case, "every active cell has a finite head at every period", worst_bad == 0, "worst period: %d bad cells" % worst_bad)

    # -- budgets: binary, against the listing, and closure
    lb = list_budgets(os.path.join(ws, "mobile_bay.lst"))
    cbb = flopy.utils.CellBudgetFile(os.path.join(ws, "mobile_bay.cbb"), precision="double")
    names = [n.decode().strip() if isinstance(n, bytes) else str(n).strip() for n in cbb.get_unique_record_names()]
    rec(case, "cbb_terms", ",".join(names))
    need = ["FLOW-JA-FACE", "STO-SS", "STO-SY", "WEL", "RIV", "CHD", "SFR", "UZF-GWRCH"]
    check(case, "budget file carries every flow term, storage included", all(n in names for n in need),
          "missing: %s" % (",".join(n for n in need if n not in names) or "none"))
    bt = open(os.path.join(O, "budget-%s.tsv" % case), "w")
    bt.write("kper\tgwf_in\tgwf_out\tgwf_net\tgwf_pct\tsfr_pct\tuzf_pct\tflowja_antisym_max\tcell_resid_max\n")
    tt = open(os.path.join(O, "budget-terms-%s.tsv" % case), "w")
    tt.write("kper\tterm\tcbb_in\tcbb_out\tlst_in\tlst_out\n")
    # The listing prints every rate in F format to 4 decimals, so a printed value is the binary one
    # to within 5e-5 (plus summation order over ~60k cells, n*eps ~ 1e-11 relative): a net within twice that.
    tol = lambda x: 5e-5 + 1e-11 * abs(x)
    worst = {"antisym": 0.0, "net": 0.0, "gross": 0.0, "gwf": 0.0, "sfr": 0.0, "uzf": 0.0, "chd_gross": 0.0}
    ckk = cbb.get_kstpkper()
    check(case, "one budget record set per stress period", len(ckk) == nper, "%d" % len(ckk))
    for s, p in ckk:
        lin, lout, lterms = lb[("GWF", p + 1)]
        qin = qout = 0.0
        pkg = np.zeros(nodes)
        fja = None
        for nm in names:
            for a in cbb.get_data(kstpkper=(s, p), text=nm):
                if nm == "FLOW-JA-FACE":
                    fja = np.asarray(a).reshape(-1)
                    continue
                if hasattr(a, "dtype") and a.dtype.names and "q" in a.dtype.names:
                    q = np.asarray(a["q"], dtype=float)
                    node = np.asarray(a["node"], dtype=np.int64) - 1
                else:
                    q = np.asarray(a, dtype=float).reshape(-1)
                    node = np.arange(q.size) if q.size == nodes else user_of_reduced
                if np.abs(q[~active[node]]).max(initial=0.0) > 0:
                    FAILS.append("%s: %s has flow at an inactive cell, period %d" % (case, nm, p + 1))
                np.add.at(pkg, node, q)
                tin, tout = q[q > 0].sum(), -q[q < 0].sum()
                qin += tin
                qout += tout
                lt = lterms.get(nm)
                if lt is None:
                    FAILS.append("%s: %s is in the budget file but not the listing, period %d" % (case, nm, p + 1))
                    continue
                tt.write("%d\t%s\t%.4f\t%.4f\t%.4f\t%.4f\n" % (p + 1, nm, tin, tout, lt[0], lt[1]))
                worst["net"] = max(worst["net"], abs((tin - tout) - (lt[0] - lt[1])) / (2 * tol(tin + tout)))
                if nm == "CHD":
                    worst["chd_gross"] = max(worst["chd_gross"], abs(tin - lt[0]))
                else:
                    worst["gross"] = max(worst["gross"], abs(tin - lt[0]) / tol(tin), abs(tout - lt[1]) / tol(tout))
        anti = np.abs(fja[offd] + fja[tidx][offd]).max()
        face = np.zeros(nodes)
        np.add.at(face, rows[offd], fja[offd])
        resid = face + pkg
        worst["net"] = max(worst["net"], abs((qin - qout) - (lin - lout)) / (2 * tol(qin + qout)))
        # closure in mf6's own definition, on the listing's gross totals
        gpct = 100.0 * (lin - lout) / (0.5 * (lin + lout))
        sfr, uzf = lb.get(("SFR_0", p + 1)), lb.get(("UZF-1", p + 1))
        spct = 100.0 * (sfr[0] - sfr[1]) / (0.5 * (sfr[0] + sfr[1])) if sfr else float("nan")
        upct = 100.0 * (uzf[0] - uzf[1]) / (0.5 * (uzf[0] + uzf[1])) if uzf else float("nan")
        bt.write("%d\t%.4f\t%.4f\t%.6e\t%.6e\t%.6e\t%.6e\t%.3e\t%.6e\n" % (
            p + 1, lin, lout, qin - qout, gpct, spct, upct, anti, np.abs(resid).max()))
        worst["antisym"] = max(worst["antisym"], anti)
        if (p + 1) not in nc:
            worst["gwf"] = max(worst["gwf"], abs(gpct))
            worst["sfr"] = max(worst["sfr"], abs(spct))
            worst["uzf"] = max(worst["uzf"], abs(upct))
    bt.close()
    tt.close()
    check(case, "FLOW-JA-FACE exactly antisymmetric, every period", worst["antisym"] == 0.0,
          "max |q_nm + q_mn| = %.3e" % worst["antisym"])
    check(case, "every term's net flow, and the model's, binary == listing", worst["net"] <= 1.0,
          "worst diff / printed precision = %.3f" % worst["net"])
    check(case, "every term's gross in and out, binary == listing (CHD excepted, below)", worst["gross"] <= 1.0,
          "worst diff / printed precision = %.3f" % worst["gross"])
    rec(case, "chd_gross_listing_minus_binary_max", "%.4f" % worst["chd_gross"])
    for k in ["gwf", "sfr", "uzf"]:
        v = worst[k]
        check(case, "%s budget closes within 1%% on every converged period" % k.upper(),
              np.isfinite(v) and v < 1.0, "max |discrepancy| = %.3e %%" % v)

    # -- observations, ours and the archive's, for compare.py
    for f in ["mobile_bay.gwf.head.obs.csv", "mobile_bay.gwf.sfr.obs.csv"]:
        src = os.path.join(ws, f)
        if os.path.exists(src):
            open(os.path.join(O, "%s-%s" % (case, f)), "w").write(open(src).read())
        ref = os.path.join(W, "ref", case, f)
        open(os.path.join(O, "archive-%s-%s" % (case, f)), "w").write(open(ref).read())
    for f in ["mobile_bay.hds"]:
        shutil.copyfile(os.path.join(ws, f), os.path.join(O, "%s-%s" % (case, f)))
    # the tool's own listings (gzip: the model listing is ~80 MB of iteration detail)
    for f in ["mfsim.lst", "mobile_bay.lst"]:
        subprocess.run(["gzip", "-c", os.path.join(ws, f)], stdout=open(os.path.join(O, "%s-%s.gz" % (case, f)), "wb"), check=False)

rec("all", "total_s", "%.1f" % (time.time() - t0))
if FAILS:
    print("FAILED:\n  " + "\n  ".join(FAILS))
    sys.exit(1)
print("MODFLOW REAL OK")
