"""The independent reference for r-arrow-real: polars, its own Parquet reader and its own engine.

    python3 -u reference.py <dir of the 36 files> <out dir>

Computes analysis.R's four tables from the same bytes, file by file (so the TLC's 2023 schema drift
never meets a dataset scanner), and writes them in the same canonical text: keys then integer values,
rows sorted bytewise. Money is cents as floor(x*100 + 0.5) cast to Int64, NaN/Inf as null, which is
the same IEEE arithmetic as Acero's. Pickup hours in 2009-2010 are parsed from strings with polars'
own datetime parser; a string it cannot parse is hour -1, as in analysis.R.
"""
import glob
import os
import sys
import time

import polars as pl

IN, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
ERA = {
    2009: dict(pu="Trip_Pickup_DateTime", pax="Passenger_Count", dist="Trip_Distance", pay="Payment_Type",
               fare="Fare_Amt", tip="Tip_Amt", tot="Total_Amt"),
    2010: dict(pu="pickup_datetime", pax="passenger_count", dist="trip_distance", pay="payment_type",
               fare="fare_amount", tip="tip_amount", tot="total_amount"),
    2023: dict(pu="tpep_pickup_datetime", pax="passenger_count", dist="trip_distance", pay="payment_type",
               fare="fare_amount", tip="tip_amount", tot="total_amount"),
}


def cents(c):
    x = pl.col(c).cast(pl.Float64)
    return pl.when(x.is_finite()).then((x * 100 + 0.5).floor()).otherwise(None).cast(pl.Int64)


files = sorted(glob.glob(os.path.join(IN, "yellow_tripdata_*.parquet")))
print("polars", pl.__version__, "threads", pl.thread_pool_size(), "files", len(files), flush=True)
parts = {"files": [], "pay": [], "hour": [], "dist": []}
t0 = time.time()
for f in files:
    name = os.path.basename(f)
    year = int(name[16:20])
    c = ERA[year]
    lf = pl.scan_parquet(f).select([pl.col(v).alias(k) for k, v in c.items()])
    sch = lf.collect_schema()
    if sch["pu"] == pl.String:
        hour = pl.col("pu").str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False).dt.hour()
    else:
        hour = pl.col("pu").dt.hour()
    x = lf.with_columns(
        year=pl.lit(year, pl.Int32), file=pl.lit(name), hour=hour.cast(pl.Int32).fill_null(-1),
        pax=pl.col("pax").cast(pl.Float64), pay=pl.col("pay").cast(pl.String).fill_null("<null>"),
        fare_c=cents("fare"), tip_c=cents("tip"), tot_c=cents("tot"),
    ).collect()
    parts["files"].append(x.group_by("file").agg(
        n=pl.len().cast(pl.Int64), n_tot=pl.col("tot_c").is_not_null().sum().cast(pl.Int64),
        tot=pl.col("tot_c").sum(), tip=pl.col("tip_c").sum(), fare=pl.col("fare_c").sum(),
        pax=pl.col("pax").sum().cast(pl.Int64), max_tot=pl.col("tot_c").max(), min_tot=pl.col("tot_c").min()))
    parts["pay"].append(x.group_by("year", "pay").agg(n=pl.len().cast(pl.Int64), tot_c=pl.col("tot_c").sum()))
    parts["hour"].append(x.group_by("year", "hour").agg(n=pl.len().cast(pl.Int64), fare_c=pl.col("fare_c").sum()))
    d = x.filter((pl.col("dist") > 0) & (pl.col("dist") <= 100) & (pl.col("fare_c") > 0) & (pl.col("fare_c") <= 50000))
    parts["dist"].append(d.with_columns(bin=pl.col("dist").floor().cast(pl.Int32)).group_by("year", "bin").agg(
        n=pl.len().cast(pl.Int64), fare_c=pl.col("fare_c").sum()))
    print(name, x.height, "%.1f s" % (time.time() - t0), flush=True)

KEYS = {"files": ["file"], "pay": ["year", "pay"], "hour": ["year", "hour"], "dist": ["year", "bin"]}
for k, keys in KEYS.items():
    t = pl.concat(parts[k])
    if k != "files":
        t = t.group_by(keys).agg([pl.col(v).sum() for v in t.columns if v not in keys])
    t = t.sort(keys)
    lines = [",".join(t.columns)] + [",".join(str(v) for v in r) for r in t.iter_rows()]
    with open(os.path.join(OUT, k + ".csv"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(k, len(lines) - 1, "rows", flush=True)
print("REFERENCE OK", "%.1f s" % (time.time() - t0), flush=True)
