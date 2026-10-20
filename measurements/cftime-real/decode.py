#!/usr/bin/env python3
"""Decode every time axis in the Pangeo CMIP6 catalog with cftime, and check each value.

    python3 -u decode.py <axes dir> <outdir> [workers]

<axes dir> is what fetch.py wrote: the raw zarr chunks of every store's `time` variable and
its bounds, as the stores hold them. For every variable, every value:

  decode     cftime.num2date under the variable's own units and calendar
  roundtrip  cftime.date2num of the decoded dates == the stored values, exactly
  referee    == refcal.py, an exact-integer implementation of the CF calendars that shares no
             code with cftime: (Y, M, D, h, m, s, us) elementwise
  xarray     xr.decode_cf(use_cftime=True), the path most users reach cftime by, == the above;
             and xarray's default (numpy datetime64) decode, where it takes it, == the referee
  diff       consecutive cftime datetimes subtract to the stored step (cftime's calendar
             arithmetic, a separate code path from num2date)
  control    the same values under a different calendar must decode differently

What the archive itself says (monotonic, gaps, time inside its bounds, contiguous bounds) is
REPORTED, never asserted: it is a claim about CMIP6, not about cftime.
"""
import collections
import glob
import hashlib
import json
import mmap
import os
import resource
import sys
import time
import warnings
import multiprocessing

import numpy as np
import numcodecs
import cftime
import xarray as xr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refcal  # noqa: E402

warnings.simplefilter("ignore")
# the control's calendar: the first under which the reference date itself exists
SWAP = {"standard": ("360_day", "noleap"), "proleptic_gregorian": ("360_day", "noleap"),
        "julian": ("360_day", "noleap"), "noleap": ("360_day", "all_leap"),
        "all_leap": ("360_day", "noleap"), "360_day": ("noleap", "all_leap")}
YEAR_US = 365 * refcal.DAY


class ArchiveDefect(Exception):
    """The store's bytes do not form the array its own .zarray describes."""


def read_array(shard_map, v):
    za = v["zarray"]
    shape, cshape = za["shape"], za["chunks"]
    dt = np.dtype(za["dtype"])
    fill = za.get("fill_value")
    if fill == "NaN":
        fill = np.nan
    out = np.empty(shape, dtype=dt)
    if fill is not None:
        out[...] = fill
    comp = numcodecs.get_codec(za["compressor"]) if za.get("compressor") else None
    filt = [numcodecs.get_codec(f) for f in (za.get("filters") or [])]
    sep = za.get("dimension_separator", ".")
    for c in v["chunks"]:
        if "len" not in c:
            if fill is None:               # absent, and no fill_value to stand in for it
                raise ArchiveDefect("absent-chunk")
            continue                       # an absent chunk is fill_value
        idx = [int(i) for i in c["key"].split("/", 1)[1].split(sep)]
        raw = shard_map[c["off"]:c["off"] + c["len"]]
        if hashlib.sha256(raw).hexdigest() != c["sha256"]:
            raise RuntimeError("chunk %s does not match its sha256" % c["key"])
        b = comp.decode(raw) if comp else raw
        for f in reversed(filt):
            b = f.decode(b)
        a = np.frombuffer(b, dtype=dt)
        if a.size != int(np.prod(cshape)):
            raise ArchiveDefect("chunk-size-mismatch")
        a = a.reshape(cshape, order=za.get("order", "C"))
        sl = tuple(slice(i * cs, min((i + 1) * cs, s)) for i, cs, s in zip(idx, cshape, shape))
        out[sl] = a[tuple(slice(0, s.stop - s.start) for s in sl)]
    return out


def tuples(dates):
    return np.array([(d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond)
                     for d in dates], dtype=np.int64).reshape(-1, 7)


def check_var(name, arr, attrs, xr_dates):
    """All the per-value checks on one variable. -> row dict (deterministic fields only)."""
    units = attrs.get("units")
    cal_raw = attrs.get("calendar", "standard")
    cal = str(cal_raw).lower()
    row = {"var": name, "units": units, "calendar": cal_raw, "dtype": arr.dtype.str, "n": int(arr.size)}
    flat = arr.ravel()
    ok = np.isfinite(flat) if flat.dtype.kind == "f" else np.ones(flat.size, bool)
    vals = flat[ok]
    row["masked"] = int((~ok).sum())
    parsed = refcal.parse_units(units) if units else None
    if units is None or cal not in refcal.CAL or parsed is None:
        row["status"] = "no-referee"           # counted; asserted to be zero
        return row, None
    rcal = refcal.CAL[cal]
    unit = units.split()[0].lower()
    fac, ref = parsed
    # 1. decode
    try:
        dates = cftime.num2date(vals, units, calendar=cal_raw)
    except Exception as e:
        row["status"] = "cftime-refused:%s" % type(e).__name__
        # A refusal is only right if the values really cannot be represented: the exact
        # product in microseconds overflows int64. Checked in Python integers.
        if vals.size and vals.dtype.kind in "iu":
            big = max(abs(int(vals.min())), abs(int(vals.max()))) * fac
        elif vals.size:
            big = float(np.abs(vals).max()) * fac
        else:
            big = 0
        row["refusal_justified"] = int(big > 2**63 - 1)
        row["xr_refused"] = int(xr_dates["cftime"] is None)
        if vals.dtype.kind in "iu":            # what the bytes are, read as float64
            f = flat.view("<f8") if flat.dtype.str == "<i8" else None
            row["bytes_are_plausible_f8"] = int(f is not None and bool(np.isfinite(f).all())
                                                and bool((np.abs(f) < 1e8).all()))
        return row, None
    dates = np.asarray(dates, dtype=object).ravel()
    row["status"] = "ok"
    # 2. round trip, exact
    #    Integers must come back exactly. A float can carry sub-microsecond noise that
    #    cftime's microsecond resolution rounds away (706.9999999999995 h -> 707 h), so for
    #    floats the claim is: back within 1 us of the stored value (cftime rounds to the us and
    #    snaps within 1 us of a whole second), and decoding `back` gives the same dates.
    back = np.asarray(cftime.date2num(dates, units, calendar=cal_raw)).ravel()
    if vals.dtype.kind in "iu":
        row["rt_bad"] = int((back != vals).sum())
    else:
        err_us = np.abs(back.astype(np.float64) - vals) * fac
        slack = np.spacing(np.abs(back.astype(np.float64))) * fac
        row["rt_inexact"] = int((back != vals).sum())
        row["rt_max_us"] = float(err_us.max()) if err_us.size else 0.0
        row["rt_bad"] = int((err_us > 1.0 + slack).sum())
        again = np.asarray(cftime.num2date(back, units, calendar=cal_raw), dtype=object).ravel()
        row["rt_redecode_bad"] = int(sum(1 for a, b in zip(again, dates) if not (a == b)))
    # 3. referee
    vus = refcal.to_us(vals, fac, unit)
    want = refcal.decode(vus, rcal, ref)
    if rcal in ("standard", "julian", "proleptic_gregorian") and ref[0] != 0:
        want[:, 0] = np.where(want[:, 0] <= 0, want[:, 0] - 1, want[:, 0])   # no year zero
    got = tuples(dates)
    row["ref_bad"] = int((got != want).any(axis=1).sum())
    # 4. xarray, use_cftime=True: the same objects
    #    One gap is xarray's, not cftime's, and is allowed only in exactly its shape: a
    #    variable named by `climatology` with no units of its own, which CF (7.4) says inherits
    #    the coordinate's -- xarray returns it as plain numbers. Counted, never silent.
    xd = xr_dates["cftime"]
    undecoded = lambda x: x is not None and np.asarray(x).dtype.kind in "iuf"
    allowed = xr_dates.get("inherited_climatology", False)
    if xd is None:                             # xarray refused what cftime decoded
        row["xr_bad"] = int(vals.size)
    elif undecoded(xd):
        row["xr_undecoded"] = 1
        row["xr_bad"] = 0 if allowed else int(vals.size)
    else:
        xd = np.asarray(xd, dtype=object).ravel()[ok]
        row["xr_bad"] = int(sum(1 for a, b in zip(xd, dates) if not (a == b)))
    #    xarray default: numpy datetime64 where it chooses it; checked against the referee
    xdef = xr_dates["default"]
    if xdef is None and xr_dates.get("default_refused"):
        row["xrdef"] = "refused"
        row["xrdef_bad"] = int(vals.size)
    elif undecoded(xdef):
        row["xrdef"] = "undecoded"
        row["xrdef_bad"] = 0 if allowed else int(vals.size)
    elif xdef is None or rcal not in ("standard", "proleptic_gregorian"):
        row["xrdef"] = "n/a"                  # xarray hands every other calendar to cftime
        row["xrdef_bad"] = 0
    elif xdef.dtype.kind == "M":
        #    xarray's own (pandas) path, at its own resolution: each value must lie between
        #    the floor and ceiling of the EXACT time in that unit. Reported, not asserted:
        #    this is xarray's arithmetic, not cftime's.
        tu, _ = np.datetime_data(xdef.dtype)
        per_us = {"ns": 1000, "us": 1}.get(tu)
        x = xdef.ravel()[ok]
        if per_us is None:
            x, per_us = x.astype("datetime64[us]"), 1
        x = x.astype(np.int64)
        epoch = refcal.absolute_us(np.zeros(1, np.int64), "proleptic_gregorian", (1970, 1, 1, 0))[0]
        off = int(refcal.absolute_us(np.zeros(1, np.int64), "proleptic_gregorian", ref)[0] - epoch) * per_us
        big = (max(abs(float(vals.min())), abs(float(vals.max()))) * fac * per_us + abs(off)) if vals.size else 0
        if big < 2.0 ** 62:
            lo, hi = refcal.bracket(vals, fac * per_us)
            lo, hi = lo + off, hi + off
        else:                                  # the exact times leave int64: Python integers
            lo, hi = refcal.bracket(vals, fac * per_us, dtype=object)
            lo, hi = lo + off, hi + off
            x = x.astype(object)
        out = (x < lo) | (x > hi)
        row["xrdef"] = "datetime64[%s]" % tu
        row["xrdef_bad"] = 0
        row["xrdef_outside"] = int(out.sum())
        if out.any():
            err = [max(int(l) - int(v), int(v) - int(h)) for l, v, h in zip(lo[out], x[out], hi[out])]
            row["xrdef_max_err_s"] = max(err) / (per_us * 1e6)
            row["xrdef_outside_by_gt_1s"] = sum(1 for e in err if e > per_us * 10**6)
    else:
        row["xrdef"] = "cftime"
        row["xrdef_bad"] = int(sum(1 for a, b in zip(xdef.ravel()[ok], dates) if not (a == b)))
    # 5. calendar arithmetic: consecutive datetimes subtract to the stored step
    if dates.size > 1:
        steps = np.array([x - y for x, y in zip(dates[1:], dates[:-1])], dtype="timedelta64[us]").astype(np.int64)
        row["diff_bad"] = int((steps != np.diff(vus)).sum())
    else:
        row["diff_bad"] = 0
    # 6. control: the wrong calendar must give different dates
    absu = refcal.absolute_us(vus, rcal, ref)
    span = int(absu.max() - absu.min()) if absu.size else 0
    row["span_ge_1y"] = int(span >= YEAR_US)
    #    (on the axis's last 64 values, the ones furthest from the reference date)
    for other in SWAP[rcal]:
        try:
            wrong = tuples(cftime.num2date(vals[-64:], units, calendar=other))
        except ValueError:
            continue                           # the reference date does not exist there
        row["control"] = other
        row["control_differs"] = int((wrong != got[-64:]).any(axis=1).sum() > 0)
        break
    # what the archive says -- reported, not asserted
    if absu.size:
        row["first"] = "%04d-%02d-%02d" % tuple(got[np.argmin(absu)][:3])
        row["last"] = "%04d-%02d-%02d" % tuple(got[np.argmax(absu)][:3])
    row["digest"] = hashlib.sha256(got.tobytes()).hexdigest()[:16]
    return row, (rcal, absu, ok, arr.shape)


def xr_decode(ds_vars, coder):
    """xarray's decode of each variable: jointly (bounds inherit from time, as users get it),
    else -- when one variable makes the whole Dataset raise -- one variable at a time.
    -> {name: values, or None where xarray refused}."""
    try:
        d = xr.decode_cf(xr.Dataset(ds_vars), decode_times=coder)
        return {n: d[n].values for n in ds_vars}
    except Exception:
        out = {}
        for n, (dims, a, at) in ds_vars.items():
            if n != "time":
                at = dict(at)
                for k in ("units", "calendar"):
                    if k not in at and k in ds_vars["time"][2]:
                        at[k] = ds_vars["time"][2][k]
            try:
                out[n] = xr.decode_cf(xr.Dataset({n: (dims, a, at)}), decode_times=coder)[n].values
            except Exception:
                out[n] = None
        return out


def do_store(job):
    try:
        return _do_store(job)
    except Exception as e:                     # counted, and check.py asserts there are none
        return {"z": job[2]["z"], "status": job[2]["status"], "kind": "error", "error": repr(e)[:300]}, []


def _do_store(job):
    path, line_no, rec = job
    out = {"z": rec["z"], "status": rec["status"]}
    if rec["status"] != 200 or "vars" not in rec:
        out["kind"] = "no-zmetadata" if rec["status"] != 200 else "no-time"
        return out, []
    out["kind"] = "time"
    out.update(rec.get("root", {}))
    with open(path, "rb") as f:
        sm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) if os.path.getsize(path) else b""
        arrays = {}
        for name, v in rec["vars"].items():
            if v.get("missing"):
                continue
            try:
                arrays[name] = read_array(sm, v)
            except ArchiveDefect as e:         # reported; no claim about cftime can be made
                out["kind"] = "defective"
                out["defect"] = "%s:%s" % (name, e)
                return out, []
    # xarray, the way a user meets it: one Dataset, time + its bounds, attributes as stored
    tatt = dict(rec["vars"]["time"]["zattrs"])
    ds_vars = {}
    for name, a in arrays.items():
        at = {k: x for k, x in rec["vars"][name]["zattrs"].items() if k != "_ARRAY_DIMENSIONS"}
        dims = rec["vars"][name]["zattrs"].get("_ARRAY_DIMENSIONS") or (["time"] + ["bnds"] * (a.ndim - 1))
        ds_vars[name] = (dims, a, at)
    civil = any(str(rec["vars"][n]["zattrs"].get("calendar", tatt.get("calendar", "standard"))).lower()
                in ("standard", "gregorian", "proleptic_gregorian") for n in arrays)
    dx = xr_decode(ds_vars, xr.coders.CFDatetimeCoder(use_cftime=True))
    dd = xr_decode(ds_vars, True) if civil else {}
    rows = []
    sides = {}
    for name, a in arrays.items():
        at = dict(rec["vars"][name]["zattrs"])
        inherited = name != "time" and "units" not in at
        if name != "time":                    # CF: bounds without units inherit the parent's
            for k in ("units", "calendar"):
                if k not in at and k in tatt:
                    at[k] = tatt[k]
        r, side = check_var(name, a, at, {"cftime": dx.get(name), "default": dd.get(name),
                                          "default_refused": civil and dd.get(name) is None,
                                          "inherited_climatology": inherited and tatt.get("climatology") == name})
        r["units_inherited"] = int(inherited)
        rows.append(r)
        sides[name] = side
    # the archive's own claims about itself, on the referee's absolute line
    t = sides.get("time")
    if t is not None:
        absu = t[1]
        d = np.diff(absu)
        out["monotonic"] = int(bool((d > 0).all())) if d.size else 1
        out["dups"] = int((d == 0).sum())
        bname = next((n for n in rows if n["var"] != "time"), None)
        b = sides.get(bname["var"]) if bname else None
        if b is not None and b[0] == t[0] and b[3] == (absu.size, 2) and b[2].all():
            bb = b[1].reshape(-1, 2)
            out["in_bounds"] = int(((bb[:, 0] <= absu) & (absu <= bb[:, 1])).sum())
            out["contiguous"] = int((bb[1:, 0] == bb[:-1, 1]).sum())
            out["n_bounds_pairs"] = int(bb.shape[0])
        elif b is not None:
            out["bounds_calendar"] = "differs-or-unshaped"
    return out, rows


def jobs(axes):
    for path in sorted(glob.glob(os.path.join(axes, "*.jsonl"))):
        binp = path[:-6] + ".bin"
        with open(path) as f:
            for i, line in enumerate(f):
                yield binp, i, json.loads(line)


def main():
    axes, outdir = sys.argv[1], sys.argv[2]
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else os.cpu_count()
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    stores, rows = [], []
    # fork, not 3.14's default forkserver: the workers must be OUR children for RUSAGE_CHILDREN
    p = multiprocessing.get_context("fork").Pool(workers, maxtasksperchild=2000)
    for k, (s, r) in enumerate(p.imap_unordered(do_store, jobs(axes), chunksize=16)):
        stores.append(s)
        for x in r:
            x["z"] = s["z"]
            rows.append(x)
        if k % 50000 == 0:
            print("  %d stores, %.0f s" % (k, time.time() - t0), flush=True)
    p.close()
    p.join()                                   # reaped, so RUSAGE_CHILDREN covers every worker
    wall = time.time() - t0
    stores.sort(key=lambda s: s["z"])
    rows.sort(key=lambda r: (r["z"], r["var"]))
    with open(os.path.join(outdir, "stores.jsonl"), "w") as f:
        for s in stores:
            f.write(json.dumps(s, sort_keys=True) + "\n")
    with open(os.path.join(outdir, "vars.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    print("decode wall %.1f s; children max RSS %.2f GiB; children cpu %.0f s" % (
        wall, ru.ru_maxrss / 2**20, ru.ru_utime + ru.ru_stime), flush=True)
    try:
        peak = open("/sys/fs/cgroup/memory.peak").read().strip()
    except OSError:
        peak = "unavailable"
    print("cgroup memory.peak %s" % peak, flush=True)
    with open(os.path.join(outdir, "timing.tsv"), "w") as f:
        f.write("cgroup_memory_peak_bytes\t%s\n" % peak)
        f.write("decode_wall_s\t%.1f\nchildren_maxrss_kib\t%d\nchildren_cpu_s\t%.0f\nworkers\t%d\n" % (
            wall, ru.ru_maxrss, ru.ru_utime + ru.ru_stime, workers))


if __name__ == "__main__":
    main()
