#!/usr/bin/env python3
"""yt over a whole Enzo cosmology run (Enzo_64: 64^3 root grid, AMR, star formation).

    python3 -u real.py <Enzo_64 dir> <out dir> <workers> [output names...]

Every output of the run is loaded and reduced the way a cosmology user reduces it: total
quantities, on-axis projections, 1-D and 2-D profiles, covering grids, particle reads and
particle deposition, and a radial profile around the densest point. Each reduction is held to
a reference that does not come from yt:

  * an INDEPENDENT reader (h5py + a parse of Enzo's own .hierarchy text) rebuilds the leaf
    cells, the particle lists and the covering grids without any yt code;
  * conservation identities tie yt's reductions back to the same total mass.

Conservation totals are transpose-invariant, so the covering grids are compared ELEMENTWISE,
with a transposed and a flipped control that must fail. A NaN passes every one-sided bound, so
every compared value is first required to be finite. Cross-output claims (dark-matter particles
are neither created nor destroyed) are made over the whole series once every output is done.
"""
import glob
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import sys
import time

import numpy as np

ROOT, OUT, NP = sys.argv[1], sys.argv[2], int(sys.argv[3])
ONLY = sys.argv[4:]
CG_LEVEL = 2          # global covering grid: 256^3 over the whole box
ZOOM = 64             # local covering grid at the finest level, ZOOM^3 cells round the peak


def h(a):
    a = np.ascontiguousarray(a)
    return hashlib.sha256(str(a.dtype).encode() + str(a.shape).encode() + a.tobytes()).hexdigest()[:16]


def rel(a, b):
    return abs(float(a) - float(b)) / abs(float(b))


# --------------------------------------------------------------- the independent reader
def parse_params(pf):
    p = {}
    for line in open(pf, encoding="latin-1"):
        if "=" in line:
            k, v = line.split("=", 1)
            p[k.strip()] = v.strip()
    return p


def parse_hierarchy(pf):
    """Enzo's own text description of the grid patches. Nothing from yt."""
    grids, cur = [], None
    for line in open(pf + ".hierarchy", encoding="latin-1"):
        line = line.strip()
        m = re.match(r"^Grid = (\d+)$", line)
        if m:
            cur = {"id": int(m.group(1))}
            grids.append(cur)
            continue
        if cur is None or "=" not in line or line.startswith("Pointer"):
            continue
        k, v = [s.strip() for s in line.split("=", 1)]
        if k in ("GridStartIndex", "GridEndIndex", "GridDimension"):
            cur[k] = np.array([int(x) for x in v.split()])
        elif k in ("GridLeftEdge", "GridRightEdge"):
            cur[k] = np.array([float(x) for x in v.split()])
        elif k in ("NumberOfParticles",):
            cur[k] = int(v)
        elif k in ("BaryonFileName", "ParticleFileName"):
            cur[k] = os.path.join(os.path.dirname(pf), os.path.basename(v))
    return grids


class Independent:
    """Leaf cells, particles and covering grids rebuilt from the HDF5 files directly."""

    def __init__(self, pf):
        import h5py
        p = parse_params(pf)
        self.N0 = np.array([int(x) for x in p["TopGridDimensions"].split()])
        self.DLE = np.array([float(x) for x in p["DomainLeftEdge"].split()])
        self.DRE = np.array([float(x) for x in p["DomainRightEdge"].split()])
        self.redshift = float(p.get("CosmologyCurrentRedshift", "nan"))
        self.grids = parse_hierarchy(pf)
        W = self.DRE - self.DLE
        if not (np.all(self.DLE == 0) and np.all(W == 1)):
            raise RuntimeError("this reader assumes the unit box Enzo cosmology runs use")
        files = {}
        for g in self.grids:
            dims = g["GridEndIndex"] - g["GridStartIndex"] + 1
            g["dims"] = dims
            dx = (g["GridRightEdge"] - g["GridLeftEdge"]) / dims
            lev = np.log2(W / self.N0 / dx)
            L = int(round(float(lev[0])))
            if not np.allclose(lev, L, atol=1e-9):
                raise RuntimeError("grid %d is not on a factor-2 level: %s" % (g["id"], lev))
            g["level"] = L
            g["dx"] = dx
            nL = self.N0 * 2 ** L
            lo = (g["GridLeftEdge"] - self.DLE) / W * nL
            g["lo"] = np.rint(lo).astype(np.int64)
            if not np.allclose(lo, g["lo"], atol=1e-6):
                raise RuntimeError("grid %d edge is not on its level's lattice" % g["id"])
            g["hi"] = g["lo"] + dims
            fn = g.get("BaryonFileName")
            if fn:
                f = files.get(fn) or files.setdefault(fn, h5py.File(fn, "r"))
                grp = f["Grid%08d" % g["id"]]
                # HDF5 holds (z, y, x); the transpose is the one thing yt and this reader share
                g["rho"] = grp["Density"][()].T
                if g["rho"].shape != tuple(dims):
                    raise RuntimeError("grid %d Density %s, hierarchy says %s"
                                       % (g["id"], g["rho"].shape, tuple(dims)))
            np_ = g.get("NumberOfParticles", 0)
            if np_:
                f = files.get(g["ParticleFileName"]) or files.setdefault(
                    g["ParticleFileName"], h5py.File(g["ParticleFileName"], "r"))
                grp = f["Grid%08d" % g["id"]]
                g["pmass_file"] = grp["particle_mass"][()]
                g["pindex"] = grp["particle_index"][()]
                g["ptype"] = grp["particle_type"][()] if "particle_type" in grp else \
                    np.ones(np_, dtype=np.int32)
                if g["pmass_file"].size != np_:
                    raise RuntimeError("grid %d has %d particles, hierarchy says %d"
                                       % (g["id"], g["pmass_file"].size, np_))
        for f in files.values():
            f.close()
        self.maxlevel = max(g["level"] for g in self.grids)
        self._leaf_masks()

    def _leaf_masks(self):
        by = {}
        for g in self.grids:
            by.setdefault(g["level"], []).append(g)
        self.by = by
        for L, gs in by.items():
            kids = by.get(L + 1, [])
            klo = np.array([k["lo"] // 2 for k in kids]).reshape(-1, 3)
            khi = np.array([(k["hi"] + 1) // 2 for k in kids]).reshape(-1, 3)
            for g in gs:
                m = np.ones(tuple(g["dims"]), dtype=bool)
                if len(kids):
                    a = np.maximum(klo, g["lo"])
                    b = np.minimum(khi, g["hi"])
                    for i in np.nonzero(np.all(b > a, axis=1))[0]:
                        s = tuple(slice(a[i, d] - g["lo"][d], b[i, d] - g["lo"][d]) for d in range(3))
                        m[s] = False
                g["leaf"] = m

    def leaf(self):
        rho = np.concatenate([g["rho"][g["leaf"]].astype(np.float64) for g in self.grids])
        dv = np.concatenate([np.full(int(g["leaf"].sum()), float(np.prod(g["dx"])))
                             for g in self.grids])
        return rho, dv

    def level_mass(self, L):
        return math.fsum(float(g["rho"].astype(np.float64).sum()) * float(np.prod(g["dx"]))
                         for g in self.by[L])

    def paint(self, level, lo=None, n=None):
        """Covering grid at `level`: every grid of level <= `level`, upsampled by injection,
        finer overwriting coarser."""
        nL = self.N0 * 2 ** level
        lo = np.zeros(3, np.int64) if lo is None else np.asarray(lo)
        n = nL if n is None else np.asarray(n)
        out = np.full(tuple(n), np.nan)
        for L in sorted(self.by):
            if L > level:
                break
            r = 2 ** (level - L)
            for g in self.by[L]:
                glo, ghi = g["lo"] * r, g["hi"] * r
                a, b = np.maximum(glo, lo), np.minimum(ghi, lo + n)
                if np.any(b <= a):
                    continue
                src = g["rho"]
                c0, c1 = (a - glo) // r, (b - glo - 1) // r + 1
                blk = src[c0[0]:c1[0], c0[1]:c1[1], c0[2]:c1[2]].astype(np.float64)
                for d in range(3):
                    blk = np.repeat(blk, r, axis=d)
                off = a - (glo + c0 * r)
                blk = blk[off[0]:off[0] + (b - a)[0], off[1]:off[1] + (b - a)[1],
                          off[2]:off[2] + (b - a)[2]]
                out[a[0] - lo[0]:b[0] - lo[0], a[1] - lo[1]:b[1] - lo[1],
                    a[2] - lo[2]:b[2] - lo[2]] = blk
        return out

    def particles(self):
        pm = [g["pmass_file"] * float(np.prod(g["dx"])) for g in self.grids if "pmass_file" in g]
        pi = [g["pindex"] for g in self.grids if "pmass_file" in g]
        pt = [g["ptype"] for g in self.grids if "pmass_file" in g]
        if not pm:
            return np.zeros(0), np.zeros(0, np.int64), np.zeros(0, np.int32)
        return np.concatenate(pm), np.concatenate(pi), np.concatenate(pt)


# ------------------------------------------------------------------------ one output
def enzo_record(root):
    """What Enzo itself wrote while it ran: OutputLog (dataset -> cycle, time) and
    OutputLevelInformation.out (cycle -> grids and active cells on every level)."""
    log = {}
    for line in open(os.path.join(root, "OutputLog")):
        f = line.split()
        if line.startswith("DATASET WRITTEN"):
            log[os.path.normpath(f[2])] = (int(f[3]), f[4])
    oli, cyc = {}, None
    for line in open(os.path.join(root, "OutputLevelInformation.out")):
        f = line.split()
        if line.startswith("Cycle"):
            cyc = int(f[1])
            oli[cyc] = {"time": f[3], "levels": {}}
        elif f and f[0] == "Level":
            oli[cyc]["levels"][int(f[1])] = (int(f[3]), int(f[f.index("Active") + 1]))
    return log, oli


def analyse(pf, enzo, R, C):
    import yt
    yt.funcs.mylog.setLevel(40)
    name = os.path.basename(os.path.dirname(pf))
    # R: observations, C: checks -- filled in place, so a crash keeps what ran

    def rec(k, v):
        R.append((name, k, str(v)))

    def check(k, ok, v):
        C.append((name, k, "PASS" if bool(ok) else "FAIL", str(v)))

    def finite(k, *vals):
        ok = all(bool(np.all(np.isfinite(np.asarray(v, dtype=np.float64)))) for v in vals)
        if not ok:
            check(k + "_finite", False, "NaN or inf")
        return ok

    t0 = time.time()
    ind = Independent(pf)
    t_ind = time.time() - t0
    rec("redshift", "%.6f" % ind.redshift)
    rec("grids", len(ind.grids))
    rec("max_level", ind.maxlevel)
    rec("grids_per_level", ",".join(str(len(ind.by[L])) for L in sorted(ind.by)))

    t1 = time.time()
    ds = yt.load(pf)
    ad = ds.all_data()
    check("grid_count", ds.index.num_grids == len(ind.grids),
          "yt %d / hierarchy %d" % (ds.index.num_grids, len(ind.grids)))
    check("max_level", int(ds.index.max_level) == ind.maxlevel,
          "yt %d / hierarchy %d" % (ds.index.max_level, ind.maxlevel))

    # ---- 0. Enzo's own record of this output, written while the simulation ran
    check("in_enzo_output_log", enzo is not None, "cycle %s" % (enzo[0] if enzo else "-"))
    if enzo is not None:
        cycle, t_log, levels = enzo
        rec("cycle", cycle)
        # yt takes the time from the parameter file, which prints fewer digits than OutputLog:
        # yt must equal the parameter file (through its seconds round trip), and the parameter
        # file must equal OutputLog to half a unit of its own last printed digit.
        t_par = parse_params(pf)["InitialTime"]
        t_yt = float(ds.current_time.to("code_time"))
        check("time_vs_parameter_file", rel(t_yt, float(t_par)) < 1e-15, "yt %.17g / %s" % (t_yt, t_par))
        mant = t_par.lower().split("e")[0]
        exp = int(t_par.lower().split("e")[1]) if "e" in t_par.lower() else 0
        half = 0.5 * 10.0 ** (exp - (len(mant.split(".")[1]) if "." in mant else 0))
        check("time_vs_output_log", abs(float(t_par) - float(t_log)) <= half,
              "%s / OutputLog %s (+-%.0e)" % (t_par, t_log, half))
    if enzo is not None and levels is None:
        rec("level_record", "written after the last cycle OutputLevelInformation.out records")
    elif enzo is not None:
        ls = ds.index.level_stats
        for L in sorted(set(levels) | set(ind.by)):
            want = levels.get(L, (0, 0))
            gs = ind.by.get(L, [])
            got_r = (len(gs), int(sum(int(np.prod(g["dims"])) for g in gs)))
            got_y = (int(ls["numgrids"][L]), int(ls["numcells"][L])) if L < len(ls) else (0, 0)
            check("level%d_grids_cells_vs_enzo" % L, got_r == want and got_y == want,
                  "enzo %s / reader %s / yt %s" % (want, got_r, got_y))

    # ---- 1. leaf cells: yt's child masking vs the reader's, as a multiset of exact values
    rho_i, dv_i = ind.leaf()
    rho_y = np.asarray(ad["enzo", "Density"].d, dtype=np.float64)
    rec("leaf_cells", rho_y.size)
    check("leaf_cell_count", rho_y.size == rho_i.size, "yt %d / reader %d" % (rho_y.size, rho_i.size))
    same = rho_y.size == rho_i.size and np.array_equal(np.sort(rho_y), np.sort(rho_i))
    check("leaf_density_multiset_exact", same, "sha %s" % h(np.sort(rho_y)))

    m_i = math.fsum(rho_i * dv_i)
    m_y = float(ad.quantities.total_quantity(("gas", "mass")).to("code_mass"))
    finite("gas_mass", m_y, m_i)
    rec("gas_mass_code", "%.17e" % m_y)
    check("gas_mass_vs_reader", rel(m_y, m_i) < 1e-12, "%.3e" % rel(m_y, m_i))
    vol = float(ad.quantities.total_quantity(("index", "cell_volume")).to("code_length**3"))
    check("leaf_volume_tiles_box", rel(vol, 1.0) < 1e-14, "%.17e" % vol)

    # Enzo projects each child back onto its parent, so every level's patches carry the mass
    # of the leaf cells beneath them. Observed, not asserted: it is Enzo's property, not yt's.
    rec("root_vs_leaf_mass_rel", "%.3e" % rel(ind.level_mass(0), m_i))

    m_g = float(ad.quantities.total_quantity(("gas", "mass")).to("g"))
    # ---- 2. projections: the quadtree's own cells integrate back to the total
    for ax in "xyz":
        p = ds.proj(("gas", "density"), ax)
        sig = np.asarray(p["gas", "density"].to("g/cm**2").d)
        pdx = np.asarray(p["pdx"].to("cm").d)
        pdy = np.asarray(p["pdy"].to("cm").d)
        finite("proj_%s" % ax, sig, pdx, pdy)
        area = (2 * pdx) * (2 * pdy)                  # pdx, pdy are half-widths
        m_p = math.fsum(sig * area)
        rec("proj_%s_cells" % ax, sig.size)
        check("proj_%s_mass" % ax, rel(m_p, m_g) < 1e-12, "%.3e" % rel(m_p, m_g))
        order = np.lexsort((np.asarray(p["py"].d), np.asarray(p["px"].d)))
        rec("proj_%s_sha" % ax, h(sig[order]))
    # the control: a weighted projection is an average, so it must NOT come back as a column
    pw = ds.proj(("gas", "density"), "z", weight_field=("gas", "mass"))
    u = str(pw["gas", "density"].units)
    check("control_weighted_proj_is_average", u != str(p["gas", "density"].units), u)
    # the images a ProjectionPlot would draw (800x800 buffers): kept as hashes for the chips
    pt = ds.proj(("gas", "temperature"), "z", weight_field=("gas", "mass"))
    for lab, pr, fld in (("density", p, ("gas", "density")), ("temperature", pt, ("gas", "temperature"))):
        img = np.asarray(pr.to_frb((1.0, "unitary"), 800)[fld].d)
        finite("image_%s" % lab, img)
        rec("image_z_%s_sha" % lab, h(img))

    # ---- 3. profiles: every leaf cell lands in exactly one bin
    dmin, dmax = float(ad["gas", "density"].min()), float(ad["gas", "density"].max())
    prof = yt.create_profile(ad, ("gas", "density"), ("gas", "mass"), weight_field=None,
                             n_bins=128, extrema={("gas", "density"): (dmin * 0.999, dmax * 1.001)},
                             logs={("gas", "density"): True})
    m_prof = math.fsum(np.asarray(prof["gas", "mass"].to("g").d))
    check("profile_1d_mass", rel(m_prof, m_g) < 1e-12, "%.3e" % rel(m_prof, m_g))
    tmin, tmax = float(ad["gas", "temperature"].min()), float(ad["gas", "temperature"].max())
    ph = yt.create_profile(ad, [("gas", "density"), ("gas", "temperature")], ("gas", "mass"),
                           weight_field=None, n_bins=[128, 128],
                           extrema={("gas", "density"): (dmin * 0.999, dmax * 1.001),
                                    ("gas", "temperature"): (tmin * 0.999, tmax * 1.001)})
    a2 = np.asarray(ph["gas", "mass"].to("g").d)
    finite("phase", a2)
    check("phase_2d_mass", rel(math.fsum(a2.ravel()), m_g) < 1e-12,
          "%.3e" % rel(math.fsum(a2.ravel()), m_g))
    rec("phase_sha", h(a2))
    rec("temperature_range_K", "%.6e..%.6e" % (tmin, tmax))

    # ---- 4. covering grids, ELEMENTWISE against the reader's painting
    nL = ind.N0 * 2 ** CG_LEVEL
    cg = ds.covering_grid(CG_LEVEL, left_edge=ds.domain_left_edge, dims=nL)
    a = np.asarray(cg["enzo", "Density"].d)
    want = ind.paint(CG_LEVEL)
    ok_f = finite("covering_grid", a, want)
    check("covering_grid_L%d_exact" % CG_LEVEL, ok_f and np.array_equal(a, want),
          "max|diff| %.3e" % float(np.nanmax(np.abs(a - want))))
    # a transpose and a flip are both shape-preserving on a cube: the wrong answers to reject
    ctl_t = float(np.nanmax(np.abs(a - np.transpose(want, (2, 1, 0)))) / np.nanmax(want))
    ctl_f = float(np.nanmax(np.abs(a - want[::-1])) / np.nanmax(want))
    check("control_transposed_rejected", ctl_t > 1e-3, "%.3e" % ctl_t)
    check("control_flipped_rejected", ctl_f > 1e-3, "%.3e" % ctl_f)
    rec("covering_grid_sha", h(a))
    del a, want, cg

    # the finest level, round the densest point: every level's patch placement, elementwise
    v, c = ds.find_max(("gas", "density"))
    c = np.asarray(c.to("code_length").d)
    rec("max_density_code", "%.9e" % float(v.to("code_mass/code_length**3")))
    rec("max_density_at", ",".join("%.9f" % x for x in c))
    Lm = ind.maxlevel
    nLm = ind.N0 * 2 ** Lm
    lo = np.clip(np.floor(c * nLm).astype(np.int64) - ZOOM // 2, 0, nLm - ZOOM)
    le = ds.arr(lo / nLm, "code_length")
    cz = ds.covering_grid(Lm, left_edge=le, dims=[ZOOM] * 3)
    az = np.asarray(cz["enzo", "Density"].d)
    wz = ind.paint(Lm, lo=lo, n=[ZOOM] * 3)
    ok_f = finite("zoom_covering_grid", az, wz)
    check("covering_grid_zoom_L%d_exact" % Lm, ok_f and np.array_equal(az, wz),
          "max|diff| %.3e" % float(np.nanmax(np.abs(az - wz))))
    rec("zoom_levels_present", ",".join(str(L) for L in sorted(ind.by) if any(
        np.all(np.minimum(g["hi"] * 2 ** (Lm - L), lo + ZOOM) > np.maximum(g["lo"] * 2 ** (Lm - L), lo))
        for g in ind.by[L])))
    ctl_f = float(np.nanmax(np.abs(az - wz[:, ::-1])) / np.nanmax(wz))
    check("control_zoom_flipped_rejected", ctl_f > 1e-3, "%.3e" % ctl_f)
    del az, wz, cz

    # ---- 5. particles: counts, identities and mass against the reader
    pm_i, pi_i, pt_i = ind.particles()
    n_hier = sum(g.get("NumberOfParticles", 0) for g in ind.grids)
    pi_y = np.asarray(ad["all", "particle_index"].d).astype(np.int64)
    pm_y = np.asarray(ad["all", "particle_mass"].to("code_mass").d)
    rec("particles", pi_y.size)
    check("particle_count", pi_y.size == n_hier == pi_i.size,
          "yt %d / hierarchy %d / reader %d" % (pi_y.size, n_hier, pi_i.size))
    check("particle_index_unique", np.unique(pi_y).size == pi_y.size, "%d" % np.unique(pi_y).size)
    check("particle_index_set", np.array_equal(np.sort(pi_y), np.sort(pi_i.astype(np.int64))),
          "sha %s" % h(np.sort(pi_y)))
    finite("particle_mass", pm_y)
    check("particle_mass_vs_reader", rel(math.fsum(pm_y), math.fsum(pm_i)) < 1e-12,
          "%.3e" % rel(math.fsum(pm_y), math.fsum(pm_i)))
    dm = pt_i == 1
    rec("dm_particles", int(dm.sum()))
    rec("star_particles", int((pt_i == 2).sum()))
    rec("other_particles", int((~dm & (pt_i != 2)).sum()))
    rec("dm_mass_code", "%.17e" % math.fsum(pm_i[dm]))
    # Enzo's mass unit is the mean matter density times the box, so the run's own initial-
    # conditions file fixes both components in closed form: dark matter is 1 - Ob/Om in every
    # output, and the gas is Ob/Om at the initial conditions (cycle 0), before any star forms.
    ini = parse_params(os.path.join(os.path.dirname(os.path.dirname(pf)), "64.init"))
    fb = float(ini["CosmologyOmegaBaryonNow"]) / float(ini["CosmologyOmegaMatterNow"])
    m_dm_y = math.fsum(pm_y[np.isin(pi_y, pi_i[dm])])
    check("dm_mass_closed_form", rel(m_dm_y, 1.0 - fb) < 1e-12,
          "%.17e vs 1-Ob/Om %.17e" % (m_dm_y, 1.0 - fb))
    if enzo is not None and enzo[0] == 0:
        check("ic_gas_mass_closed_form", rel(m_y, fb) < 1e-12, "%.17e vs Ob/Om %.17e" % (m_y, fb))
    rec("star_mass_code", "%.17e" % math.fsum(pm_i[pt_i == 2]))
    rec("dm_index_sha", h(np.sort(pi_i[dm].astype(np.int64))))
    rec("star_index_sha", h(np.sort(pi_i[pt_i == 2].astype(np.int64))))
    rec("total_mass_code", "%.17e" % (m_y + math.fsum(pm_i)))

    # ---- 6. deposition: nearest-grid-point deposit puts every particle in one leaf cell
    cnt = math.fsum(np.asarray(ad["deposit", "all_count"].d))
    check("deposit_count", cnt == pi_y.size, "%.1f of %d" % (cnt, pi_y.size))
    dmass = math.fsum(np.asarray(ad["deposit", "all_mass"].to("code_mass").d))
    finite("deposit_mass", dmass)
    check("deposit_mass", rel(dmass, math.fsum(pm_y)) < 1e-12, "%.3e" % rel(dmass, math.fsum(pm_y)))

    # ---- 7. the analysis a user runs: a radial profile round the densest point
    sp = ds.sphere(ds.arr(c, "code_length"), (2.0, "Mpccm/h"))
    ms = float(sp.quantities.total_quantity(("gas", "mass")).to("g"))
    # linear bins from below zero: the densest cell's own centre is at r = 0
    rp = yt.create_profile(sp, ("index", "radius"), [("gas", "mass")], weight_field=None,
                           n_bins=64, logs={("index", "radius"): False},
                           extrema={("index", "radius"): (ds.quan(-1.0, "kpccm/h"),
                                                          ds.quan(2001.0, "kpccm/h"))})
    rpm = np.asarray(rp["gas", "mass"].to("g").d)
    finite("sphere", ms, rpm)
    rec("sphere_cells", int(sp["index", "radius"].size))
    check("sphere_profile_mass", rel(math.fsum(rpm), ms) < 1e-12, "%.3e" % rel(math.fsum(rpm), ms))
    rt = yt.create_profile(sp, ("index", "radius"), [("gas", "temperature")],
                           weight_field=("gas", "mass"), n_bins=32, logs={("index", "radius"): False},
                           extrema={("index", "radius"): (ds.quan(-1.0, "kpccm/h"),
                                                          ds.quan(2001.0, "kpccm/h"))})
    rec("sphere_gas_mass_Msun", "%.9e" % (ms / 1.98841e33))
    rec("radial_T_sha", h(np.asarray(rt["gas", "temperature"].d)))

    rec("t_reader_s", "%.1f" % t_ind)
    rec("t_yt_s", "%.1f" % (time.time() - t1))
    return


def run_one(arg):
    pf, enzo = arg
    t = time.time()
    name = os.path.basename(os.path.dirname(pf))
    R, C = [(name, "output", pf)], []
    try:
        analyse(pf, enzo, R, C)
        C.append((name, "completed", "PASS", "all reductions ran"))
    except Exception as e:                  # a crash in one output is a FAIL row, not a hang
        import traceback
        R.append((name, "error", traceback.format_exc().replace("\n", " | ")))
        C.append((name, "completed", "FAIL", repr(e)))
    return R, C, time.time() - t


def main():
    pfs = sorted(p[:-len(".hierarchy")] for d in ("DD*", "RD*")
                 for p in glob.glob(os.path.join(ROOT, d, "*.hierarchy")))
    log, oli = enzo_record(ROOT)
    allC = [("series", "outputs_on_disk_vs_output_log",
             "PASS" if sorted(os.path.relpath(p, ROOT) for p in pfs) == sorted(log) else "FAIL",
             "%d on disk / %d in OutputLog" % (len(pfs), len(log)))]
    if ONLY:
        pfs = [p for p in pfs if os.path.basename(os.path.dirname(p)) in ONLY]

    def enzo_of(p):
        k = os.path.relpath(p, ROOT)
        if k not in log:
            return None
        return log[k][0], log[k][1], oli[log[k][0]]["levels"] if log[k][0] in oli else None
    # largest first, so the long outputs are not left for last
    pfs.sort(key=lambda p: -sum(os.path.getsize(f) for f in glob.glob(p + "*")))
    print("outputs %d, workers %d" % (len(pfs), NP), flush=True)
    os.makedirs(OUT, exist_ok=True)
    allR = []
    with mp.get_context("spawn").Pool(NP, maxtasksperchild=1) as pool:
        for R, C, dt in pool.imap_unordered(run_one, [(p, enzo_of(p)) for p in pfs]):
            name = R[0][0]
            nf = sum(1 for c in C if c[2] != "PASS")
            print("%-8s %6.1f s  %d checks  %d failed" % (name, dt, len(C), nf), flush=True)
            for c in C:
                if c[2] != "PASS":
                    print("   FAIL %s %s" % (c[1], c[3]), flush=True)
            R.append((name, "t_total_s", "%.1f" % dt))
            allR += R
            allC += C

    # ---- across the series: the run's dark matter is the same particles in every output
    obs = {}
    for n, k, v in allR:
        obs.setdefault(n, {})[k] = v
    names = sorted(obs)
    if len(names) > 1:
        dm = {obs[n].get("dm_index_sha") for n in names}
        allC.append(("series", "dm_particle_set_constant", "PASS" if len(dm) == 1 else "FAIL",
                     "%d distinct over %d outputs" % (len(dm), len(names))))
        dmm = [float(obs[n]["dm_mass_code"]) for n in names if "dm_mass_code" in obs[n]]
        spread = (max(dmm) - min(dmm)) / max(dmm) if dmm else float("nan")
        allC.append(("series", "dm_mass_constant", "PASS" if spread < 1e-12 else "FAIL", "%.3e" % spread))
        # gas + stars + dark matter in comoving code units: Enzo's conservation, observed only
        tot = [float(obs[n]["total_mass_code"]) for n in names if "total_mass_code" in obs[n]]
        if tot:
            allR.append(("series", "total_mass_spread_rel", "%.3e" % ((max(tot) - min(tot)) / max(tot))))
        ns = [(float(obs[n]["redshift"]), int(obs[n]["star_particles"])) for n in names
              if "star_particles" in obs[n]]
        ns.sort(key=lambda t: -t[0])
        allR.append(("series", "star_particles_by_z", " ".join("%g:%d" % t for t in ns)))

    allR.sort()
    allC.sort()
    with open(os.path.join(OUT, "observations.tsv"), "w") as f:
        f.write("output\tkey\tvalue\n")
        f.writelines("\t".join(r) + "\n" for r in allR)
    with open(os.path.join(OUT, "check.tsv"), "w") as f:
        f.write("output\tcheck\tresult\tvalue\n")
        f.writelines("\t".join(c) + "\n" for c in allC)
    nfail = sum(1 for c in allC if c[2] != "PASS")
    import resource
    print("worker_peak_rss_gib\t%.2f" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2**20),
          flush=True)
    print("checks %d, failed %d" % (len(allC), nfail), flush=True)
    sys.exit(1 if nfail or not allC else 0)


if __name__ == "__main__":
    main()
