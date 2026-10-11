#!/usr/bin/env python3
"""cftime on Graviton4: CF calendar arithmetic against published constants and two
independent implementations.

A wrong time axis is the canonical silent failure in climate work -- every downstream
number stays plausible. So nothing here is a band on an observed value:

 1. PUBLISHED CONSTANTS, external to cftime and to Python. J2000.0 is Julian Day
    2451545.0 and the Modified Julian Date of 2000-01-01 is 51544. Both are fixed by
    astronomical convention, so cftime either lands on them or is wrong.

 2. THREE INDEPENDENT IMPLEMENTATIONS, compared elementwise over every day from 1600 to
    2400 (292,194 of them): cftime's C extension, Python's stdlib `datetime`, and
    `numpy.datetime64`. Three unrelated codebases, so agreement is about the numerics and
    not about internal consistency.

 3. THE CALENDAR MUST MATCH, or the comparison measures a method difference. Stdlib
    datetime is PROLEPTIC Gregorian; CF's `standard` calendar is mixed Julian/Gregorian
    with a 10-day hole at the 1582 reform. Comparing cftime's `standard` against datetime
    shows a 10-day "error" that is not an error at all -- measured here, and used as the
    CONTROL that proves the agreement above is calendar-sensitive rather than vacuous.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import datetime as dt
import sys

import numpy as np
import cftime

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("cftime", getattr(cftime, "__version__", "unknown"))
rec("numpy", np.__version__)
rec("python", "%d.%d.%d" % sys.version_info[:3])

# ----------------------------------------- 1. published constants, external to both tools
# JD 0 is -4713-11-24 12:00 in the proleptic Gregorian calendar; J2000.0 is JD 2451545.0.
PG = "proleptic_gregorian"
jd = cftime.date2num(cftime.datetime(2000, 1, 1, 12, calendar=PG),
                     "days since -4713-11-24 12:00:00", calendar=PG)
rec("julian_day_J2000", "%s" % jd)
rec("julian_day_published", "2451545")
if int(jd) != 2451545 or float(jd) != 2451545.0:
    die("J2000.0 came out at JD %s; the published value is 2451545.0" % jd)

mjd = cftime.date2num(cftime.datetime(2000, 1, 1, calendar=PG),
                      "days since 1858-11-17", calendar=PG)
rec("mjd_2000_01_01", "%s" % mjd)
rec("mjd_published", "51544")
if int(mjd) != 51544:
    die("MJD of 2000-01-01 came out at %s; the published value is 51544" % mjd)

# ---------------------- 2. three independent implementations, every day 1600 -> 2400
UNITS = "days since 1600-01-01"
START = dt.date(1600, 1, 1)
NDAYS = (dt.date(2400, 1, 1) - START).days
rec("span_days", "%d" % NDAYS)
rec("span", "1600-01-01 .. 2400-01-01")

nums = np.arange(NDAYS, dtype="float64")
cf = cftime.num2date(nums, UNITS, calendar=PG)

# (a) the round trip, within cftime: exact, not merely close
back = np.asarray(cftime.date2num(cf, UNITS, calendar=PG), dtype="float64")
if not np.all(np.isfinite(back)):
    die("date2num produced a non-finite value; a one-sided bound cannot catch that")
rt = float(np.abs(back - nums).max())
rec("roundtrip_max_abs_diff", "%.17g" % rt)
if rt != 0.0:
    die("num2date -> date2num is not exact over %d days (max |diff| %.17g)" % (NDAYS, rt))

# (b) stdlib datetime -- a wholly separate implementation
py = [START + dt.timedelta(days=int(i)) for i in range(NDAYS)]
agree_py = sum(1 for a, b in zip(cf, py)
               if (a.year, a.month, a.day) == (b.year, b.month, b.day))
rec("agree_stdlib_datetime", "%d of %d" % (agree_py, NDAYS))
if agree_py != NDAYS:
    die("cftime and stdlib datetime disagree on %d of %d days" % (NDAYS - agree_py, NDAYS))

# (c) numpy.datetime64 -- a third, also separate
npd = (np.datetime64("1600-01-01", "D") + np.arange(NDAYS, dtype="int64")).astype(object)
agree_np = sum(1 for a, b in zip(cf, npd)
               if (a.year, a.month, a.day) == (b.year, b.month, b.day))
rec("agree_numpy_datetime64", "%d of %d" % (agree_np, NDAYS))
if agree_np != NDAYS:
    die("cftime and numpy.datetime64 disagree on %d of %d days" % (NDAYS - agree_np, NDAYS))

# --------- 3. THE CONTROL: the comparison must be able to fail, and `standard` makes it
# `standard` is mixed Julian/Gregorian. Against the SAME stdlib dates it must diverge
# before the 1582 reform and agree after -- otherwise the three-way agreement above would
# also hold for a calendar-blind implementation, and would prove nothing.
rec("control_calendar", "standard (mixed Julian/Gregorian) vs stdlib proleptic")
diverged, agreed = [], []
for yr in (1000, 1500, 1582, 1600, 1900, 2000, 2400):
    n_std = int(cftime.date2num(cftime.datetime(yr, 6, 15, calendar="standard"),
                                "days since 1970-01-01", calendar="standard"))
    n_py = (dt.datetime(yr, 6, 15) - dt.datetime(1970, 1, 1)).days
    off = n_std - n_py
    rec("control_%d_offset_days" % yr, "%d" % off)
    (diverged if off else agreed).append(yr)
rec("control_diverges_before_reform", ",".join(str(y) for y in diverged))
rec("control_agrees_after_reform", ",".join(str(y) for y in agreed))
if not diverged:
    die("`standard` agreed with proleptic stdlib at every year tested -- the cross-check "
        "is calendar-blind, so the 292,194-day agreement proves nothing")
if 1600 in diverged or 2000 in diverged:
    die("`standard` diverges from proleptic Gregorian after the reform, which it must not")

# ------------------------------- 4. the 1582 reform itself: a documented, exact fact
# The Gregorian reform deleted 1582-10-05 through 1582-10-14 from the `standard` calendar.
gap = (int(cftime.date2num(cftime.datetime(1582, 10, 15, calendar="standard"),
                           "days since 1582-10-01", calendar="standard"))
       - int(cftime.date2num(cftime.datetime(1582, 10, 4, calendar="standard"),
                             "days since 1582-10-01", calendar="standard")))
rec("reform_oct4_to_oct15_standard", "%d" % gap)
if gap != 1:
    die("in `standard`, 1582-10-04 to 1582-10-15 is %d days; the reform makes it 1" % gap)
for cal, want in (("proleptic_gregorian", 11), ("julian", 11)):
    g = (int(cftime.date2num(cftime.datetime(1582, 10, 15, calendar=cal),
                             "days since 1582-10-01", calendar=cal))
         - int(cftime.date2num(cftime.datetime(1582, 10, 4, calendar=cal),
                               "days since 1582-10-01", calendar=cal)))
    rec("reform_oct4_to_oct15_%s" % cal, "%d" % g)
    if g != want:
        die("in `%s`, Oct 4 to Oct 15 is %d days, expected %d (no reform in this "
            "calendar)" % (cal, g, want))

# and the deleted dates must not be constructible at all
missing = []
for day in range(5, 15):
    try:
        cftime.datetime(1582, 10, day, calendar="standard")
    except Exception:
        missing.append(day)
rec("reform_dates_rejected", "%d of 10 (Oct 5-14)" % len(missing))
if len(missing) != 10:
    die("`standard` accepted %d of the 10 dates the reform deleted"
        % (10 - len(missing)))

# ----------------------------- 5. the julian/gregorian leap divergence, also documented
# Julian makes every 4th year a leap year; Gregorian drops century years not divisible
# by 400. So 1900 and 2100 are leap in Julian and not in Gregorian; 2000 is leap in both.
for cal, expect in (("julian", {1900: True, 2000: True, 2100: True}),
                    ("standard", {1900: False, 2000: True, 2100: False}),
                    ("proleptic_gregorian", {1900: False, 2000: True, 2100: False})):
    for yr, want in sorted(expect.items()):
        try:
            cftime.datetime(yr, 2, 29, calendar=cal)
            have = True
        except Exception:
            have = False
        rec("leap_%s_%d" % (cal, yr), "Feb29 %s" % ("exists" if have else "absent"))
        if have != want:
            die("%s: Feb 29 %d %s, which contradicts the calendar's own leap rule"
                % (cal, yr, "exists" if have else "is absent"))

# ------------------------------------------ 6. exact year lengths, per CF calendar
YEAR_LEN = {"standard": (366, 365), "gregorian": (366, 365),
            "proleptic_gregorian": (366, 365), "julian": (366, 365),
            "noleap": (365, 365), "365_day": (365, 365),
            "all_leap": (366, 366), "366_day": (366, 366),
            "360_day": (360, 360)}
for cal, (leap_yr, common_yr) in YEAR_LEN.items():
    u = "days since 2000-01-01"
    y2000 = int(cftime.date2num(cftime.datetime(2000, 1, 1, calendar=cal), u, calendar=cal))
    y2001 = int(cftime.date2num(cftime.datetime(2001, 1, 1, calendar=cal), u, calendar=cal))
    y1999 = int(cftime.date2num(cftime.datetime(1999, 1, 1, calendar=cal), u, calendar=cal))
    got = (y2001 - y2000, y2000 - y1999)
    rec("year_len_%s" % cal, "2000=%d 1999=%d" % got)
    if got != (leap_yr, common_yr):
        die("%s: year lengths %s, CF defines %s" % (cal, got, (leap_yr, common_yr)))

# a 360_day year is 12 months of exactly 30 days -- structure, not just the total
starts = [int(cftime.date2num(cftime.datetime(2000, m, 1, calendar="360_day"),
                              "days since 2000-01-01", calendar="360_day"))
          for m in range(1, 13)]
rec("360day_month_starts", ",".join(str(s) for s in starts))
if starts != [30 * i for i in range(12)]:
    die("360_day months start at %s, not every 30 days" % starts)

# ----------------------------------- 7. units and resolution: round-trip must be exact
stamp = cftime.datetime(2000, 3, 1, 12, 30, 15, 250000, calendar=PG)
for u in ("days since 2000-01-01", "hours since 2000-01-01", "seconds since 2000-01-01",
          "milliseconds since 2000-01-01", "microseconds since 2000-01-01"):
    n = cftime.date2num(stamp, u, calendar=PG)
    if not np.isfinite(float(n)):
        die("date2num gave a non-finite value for %r" % u)
    r = cftime.num2date(n, u, calendar=PG)
    rec("units_%s" % u.split()[0], "%s -> %s" % (n, "exact" if r == stamp else "LOSSY"))
    if r != stamp:
        die("round-trip through %r lost information: %s != %s" % (u, r, stamp))

# ------------------------------------------------------------------- 8. determinism
a = cftime.date2num(cftime.num2date(12345.0, UNITS, calendar=PG), UNITS, calendar=PG)
b = cftime.date2num(cftime.num2date(12345.0, UNITS, calendar=PG), UNITS, calendar=PG)
rec("determinism", "identical" if a == b else "DIFFERS")
if a != b:
    die("two identical conversions gave %r and %r" % (a, b))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("CFTIME OK")
