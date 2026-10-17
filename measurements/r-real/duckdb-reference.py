# The independent reference behind check.R: DuckDB 1.5.6 (pip) over the same 12 pinned Parquet files.
#   python3 -I duckdb-reference.py <dir-with-the-12-files>
# Prints row and cent totals, the two aggregate tables as sha256 of their "k[,k],n,cents" lines, and
# the model-row count under analysis.R's filters. Run once on a laptop; it is not part of the task.
import duckdb, sys, hashlib
d = sys.argv[1]
con = duckdb.connect()
src = f"(SELECT CAST(regexp_extract(filename, '2023-([0-9]+)', 1) AS INT) AS month, * FROM read_parquet('{d}/*.parquet', filename=true, union_by_name=true))"
print(con.sql(f"SELECT count(*) n, sum(round(total_amount*100)::HUGEINT) cents, count(total_amount) nn, sum(CASE WHEN total_amount IS NULL THEN 1 ELSE 0 END) AS nnull, sum(CASE WHEN payment_type IS NULL THEN 1 ELSE 0 END) AS ptnull, sum(CASE WHEN PULocationID IS NULL THEN 1 ELSE 0 END) AS punull FROM {src}"))
# exactness check: is total_amount*100 always within 1e-6 of an integer?
print(con.sql(f"SELECT max(abs(total_amount*100 - round(total_amount*100))) FROM {src}"))
for key in ["month, payment_type", "PULocationID"]:
    rows = con.sql(f"SELECT {key}, count(*) n, sum(round(total_amount*100)::HUGEINT) cents FROM {src} GROUP BY ALL ORDER BY ALL").fetchall()
    txt = "".join(",".join(str(v) for v in r) + "\n" for r in rows)
    print(key, len(rows), hashlib.sha256(txt.encode()).hexdigest()); print(txt[:200])
print(con.sql(f"""SELECT count(*) FROM {src} WHERE RatecodeID = 1 AND fare_amount > 0 AND fare_amount <= 500
  AND trip_distance > 0 AND trip_distance <= 100
  AND (epoch_us(tpep_dropoff_datetime) - epoch_us(tpep_pickup_datetime)) > 60e6
  AND (epoch_us(tpep_dropoff_datetime) - epoch_us(tpep_pickup_datetime)) <= 180*60e6"""))
print(con.sql(f"""SELECT count(*) FROM read_parquet('{d}/*2023-01.parquet') WHERE RatecodeID = 1 AND fare_amount > 0 AND fare_amount <= 500
  AND trip_distance > 0 AND trip_distance <= 100
  AND (epoch_us(tpep_dropoff_datetime) - epoch_us(tpep_pickup_datetime)) > 60e6
  AND (epoch_us(tpep_dropoff_datetime) - epoch_us(tpep_pickup_datetime)) <= 180*60e6"""))
