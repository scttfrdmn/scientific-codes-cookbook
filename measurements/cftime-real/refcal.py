"""An independent CF calendar decoder in exact int64 numpy arithmetic -- the referee for cftime.

Shares no code with cftime: day counts come from the calendar definitions (CF conventions
section 4.4.1) and, for the real-world calendars, from Julian Day Numbers (Fliegel & Van
Flandern 1968 / Richards 2013 integer forms).
"""
import re
import numpy as np

US = {"microseconds": 1, "microsecond": 1, "us": 1,
      "milliseconds": 1000, "millisecond": 1000, "ms": 1000,
      "seconds": 10**6, "second": 10**6, "secs": 10**6, "sec": 10**6, "s": 10**6,
      "minutes": 60 * 10**6, "minute": 60 * 10**6, "mins": 60 * 10**6, "min": 60 * 10**6,
      "hours": 3600 * 10**6, "hour": 3600 * 10**6, "hrs": 3600 * 10**6, "hr": 3600 * 10**6, "h": 3600 * 10**6,
      "days": 86400 * 10**6, "day": 86400 * 10**6, "d": 86400 * 10**6}
DAY = 86400 * 10**6
CAL = {"standard": "standard", "gregorian": "standard", "proleptic_gregorian": "proleptic_gregorian",
       "noleap": "noleap", "365_day": "noleap", "all_leap": "all_leap", "366_day": "all_leap",
       "360_day": "360_day", "julian": "julian"}
_REF = re.compile(r"^\s*(\w+)\s+since\s+(-?\d{1,4})-(\d{1,2})-(\d{1,2})"
                  r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:\.(\d{1,6}))?)?)?\s*(?:Z|UTC|\+0+:?0*)?\s*$")
ML = {"noleap": np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]),
      "all_leap": np.array([31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])}
CUM = {k: np.concatenate([[0], np.cumsum(v)]) for k, v in ML.items()}


def parse_units(units):
    """-> (us per unit, (y, m, d, us of day)) or None if the referee does not handle it."""
    m = _REF.match(units)
    if not m or m.group(1).lower() not in US:
        return None
    y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
    hh, mi, ss = (int(m.group(i) or 0) for i in (5, 6, 7))
    frac = m.group(8) or ""
    us = ((hh * 60 + mi) * 60 + ss) * 10**6 + (int(frac.ljust(6, "0")) if frac else 0)
    return US[m.group(1).lower()], (y, mo, d, us)


def _jdn_greg(y, m, d):
    a = (14 - m) // 12; yy = y + 4800 - a; mm = m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


def _jdn_jul(y, m, d):
    a = (14 - m) // 12; yy = y + 4800 - a; mm = m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - 32083


def _from_jdn_greg(j):
    a = j + 32044; b = (4 * a + 3) // 146097; c = a - 146097 * b // 4
    d = (4 * c + 3) // 1461; e = c - 1461 * d // 4; m = (5 * e + 2) // 153
    return 100 * b + d - 4800 + m // 10, m + 3 - 12 * (m // 10), e - (153 * m + 2) // 5 + 1


def _from_jdn_jul(j):
    c = j + 32082; d = (4 * c + 3) // 1461; e = c - 1461 * d // 4; m = (5 * e + 2) // 153
    return d - 4800 + m // 10, m + 3 - 12 * (m // 10), e - (153 * m + 2) // 5 + 1


REFORM = 2299161          # JDN of 1582-10-15 Gregorian == the day after 1582-10-04 Julian


def day_number(cal, y, m, d):
    """Absolute day count in `cal` (any fixed origin). Years are astronomical (year 0 exists)
    for the day-count calendars; the real-world ones are only used with y >= 1 here."""
    if cal == "360_day":
        return y * 360 + (m - 1) * 30 + (d - 1)
    if cal in CUM:
        return y * CUM[cal][12] + CUM[cal][m - 1] + (d - 1)
    if cal == "julian":
        return _jdn_jul(y, m, d)
    if cal == "proleptic_gregorian":
        return _jdn_greg(y, m, d)
    if cal == "standard":
        j = _jdn_jul(y, m, d)
        return j if j < REFORM else _jdn_greg(y, m, d)
    raise ValueError(cal)


def from_day_number(cal, n):
    n = np.asarray(n, dtype=np.int64)
    if cal == "360_day":
        y, r = np.divmod(n, 360); mo, d = np.divmod(r, 30)
        return y, mo + 1, d + 1
    if cal in CUM:
        y, r = np.divmod(n, CUM[cal][12])
        mo = np.searchsorted(CUM[cal], r, side="right")      # 1..12
        return y, mo, r - CUM[cal][mo - 1] + 1
    if cal == "julian":
        return _from_jdn_jul(n)
    if cal == "proleptic_gregorian":
        return _from_jdn_greg(n)
    if cal == "standard":
        yj, mj, dj = _from_jdn_jul(n); yg, mg, dg = _from_jdn_greg(n)
        g = n >= REFORM
        return np.where(g, yg, yj), np.where(g, mg, mj), np.where(g, dg, dj)
    raise ValueError(cal)


def decode(values_us, cal, ref):
    """values_us: int64 microseconds since ref. -> int64 array (N, 7): Y M D h m s us."""
    y, m, d, tod = ref
    t = np.asarray(values_us, dtype=np.int64) + (day_number(cal, y, m, d) * DAY + tod)
    days, us = np.divmod(t, DAY)
    Y, M, D = from_day_number(cal, days)
    s, us = np.divmod(us, 10**6); mi, s = np.divmod(s, 60); h, mi = np.divmod(mi, 60)
    return np.stack([Y, M, D, h, mi, s, us], axis=-1).astype(np.int64)


def absolute_us(values_us, cal, ref):
    """Microseconds on the calendar's own absolute line (comparable within one calendar)."""
    y, m, d, tod = ref
    return np.asarray(values_us, dtype=np.int64) + (day_number(cal, y, m, d) * DAY + tod)


FINE = ("microseconds", "microsecond", "microsec", "microsecs", "us",
        "milliseconds", "millisecond", "millisec", "millisecs", "msec", "msecs", "ms")


def to_us(values, factor, unit):
    """Values in `unit` -> int64 microseconds, EXACTLY.

    Integers multiply. A float64 is an exact binary rational, so its product with the
    factor is computed in Python integers and rounded once -- no longdouble, no float
    product. The rounding is cftime's stated contract: to the nearest microsecond (ties to
    even, as np.rint), then snapped to the whole second when that lands 1 us either side of
    one, for units coarser than milliseconds (cftime issue #187)."""
    v = np.asarray(values)
    if v.dtype.kind in "iu":
        return v.astype(np.int64) * factor
    m, e = np.frexp(v.astype(np.float64))
    M = (m * 2.0 ** 53).astype(np.int64)                 # exact: v == M * 2**(e-53)
    s = e.astype(np.int64) - 53
    out = np.empty(v.shape, dtype=np.int64)
    snap = unit not in FINE
    for i, (Mi, si) in enumerate(zip(M.ravel().tolist(), s.ravel().tolist())):
        P = Mi * factor
        if si >= 0:
            q = fl = ce = P << si
        else:
            dv = 1 << -si
            q, r = divmod(P, dv)                           # floor division, 0 <= r < dv
            fl, ce = q, q + (1 if r else 0)
            if 2 * r > dv or (2 * r == dv and q % 2):
                q += 1
        if snap:
            if q % 10**6 == 1:
                q = fl
            elif q % 10**6 == 999999:
                q = ce
        out.flat[i] = q
    return out


def bracket(values, factor, dtype=np.int64):
    """Values times an integer factor -> (floor, ceil) as int64, EXACTLY (no rounding rule):
    the two integers either side of the true product, equal when it is whole."""
    v = np.asarray(values)
    if v.dtype.kind in "iu":
        x = v.astype(np.int64).astype(dtype) * factor
        return x, x
    m, e = np.frexp(v.astype(np.float64))
    M = (m * 2.0 ** 53).astype(np.int64)
    s = e.astype(np.int64) - 53
    lo, hi = np.empty(v.shape, dtype), np.empty(v.shape, dtype)
    for i, (Mi, si) in enumerate(zip(M.ravel().tolist(), s.ravel().tolist())):
        P = Mi * factor
        if si >= 0:
            lo.flat[i] = hi.flat[i] = P << si
        else:
            q, r = divmod(P, 1 << -si)
            lo.flat[i], hi.flat[i] = q, q + (1 if r else 0)
    return lo, hi
