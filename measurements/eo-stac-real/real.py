"""eo-stac-real: one state-year of Sentinel-2 L2A through pystac-client, held to the bucket it indexes.

    python3 -u real.py pin   <work> <out> <threads>     # search + listing, write the snapshot
    python3 -u real.py check <work> <out> <threads>     # the same, and hold it to the pinned snapshot

Search: Earth Search v1 `sentinel-2-l2a`, bbox = California, datetime = "2024".
Independent truth: the `sentinel-cogs` bucket itself -- every 2024 scene directory of MGRS zones
10-12, latitude bands R-T, listed with ListObjectsV2, and every scene's own item JSON read from it.

Writes <out>/score.tsv (key<TAB>value, fixed values then wall_s_* timings), <out>/checks.tsv,
<out>/tiles.tsv, and in pin mode <out>/snapshot/{items.jsonl.gz,listing.tsv.gz,snapshot.sha256}.
Exits 1 if any assertion fails; prints REAL OK otherwise.
"""
import gzip
import hashlib
import json
import os
import resource
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import pystac
import pystac_client
import requests
import shapely
from pystac_client.conformance import ConformanceClasses
from requests.adapters import HTTPAdapter
from shapely.geometry import box, shape
from urllib3.util.retry import Retry

MODE, WORK, OUT, NT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
API = "https://earth-search.aws.element84.com/v1"
COLL = "sentinel-2-l2a"
BBOX = [-124.5, 32.5, -114.1, 42.0]
YEAR = "2024"
HOST = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
ROOT = "sentinel-s2-l2a-cogs/"
ZONES, BANDS = (10, 11, 12), "RST"
# properties.datetime must survive `fields`: Earth Search builds its next-page token from it, and
# without it page 2 fails server-side with "cannot parse empty date" (page 1 succeeds).
IDS_ONLY = {"include": ["id", "properties.datetime"], "exclude": ["assets", "geometry", "links", "bbox"]}
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
os.makedirs(OUT, exist_ok=True)

score, checks, T = [], [], {}
FAILED = []


def rec(k, v):
    score.append((k, str(v)))
    print("  %-46s %s" % (k, v), flush=True)


def check(name, ok, detail=""):
    ok = bool(ok)  # numpy booleans included
    checks.append((name, "PASS" if ok else "FAIL", str(detail)))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail), flush=True)
    if not ok:
        FAILED.append(name)


def ver(m):
    return str(getattr(m, "__version__", "unknown"))


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def norm(x):
    """Integral floats as ints: the API serialises the bucket's 20.0 as 20 (OpenSearch's JSON)."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def core(d, normalise=True):
    """The item minus what legitimately differs between the API's copy and the bucket's: the links
    (the API rewrites self/parent/root to itself) and the ingest timestamps."""
    c = {k: v for k, v in d.items() if k != "links"}
    c["properties"] = {k: v for k, v in d["properties"].items() if k not in ("created", "updated")}
    return hashlib.sha256(canon(norm(c) if normalise else c)).hexdigest()


_tl = threading.local()


def sess():
    s = getattr(_tl, "s", None)
    if s is None:
        s = requests.Session()
        r = Retry(total=6, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
        s.mount("https://", HTTPAdapter(max_retries=r, pool_maxsize=4))
        _tl.s = s
    return s


def tick(k, t0):
    T[k] = time.time() - t0
    print("-- %s %.1f s" % (k, T[k]), flush=True)


print("pystac", ver(pystac), "pystac-client", ver(pystac_client), "shapely", ver(shapely),
      "requests", ver(requests), flush=True)
rec("pystac_version", ver(pystac))
rec("pystac_client_version", ver(pystac_client))
rec("shapely_version", ver(shapely))

# ---------------------------------------------------------------- 1. the search, through pystac-client
t0 = time.time()
client = pystac_client.Client.open(API)
for c in ("ITEM_SEARCH", "QUERY", "SORT", "FIELDS"):
    check("API conforms to %s" % c, client.conforms_to(getattr(ConformanceClasses, c)))
# limit 100 (pystac-client's default): full items are ~21 KB, and a page above ~6 MB (limit >= ~280
# here) comes back as HTTP 500/502 "Internal server error", not as a size complaint.
search = client.search(collections=[COLL], bbox=BBOX, datetime=YEAR, limit=100)
matched = search.matched()
items = {}          # id -> summary
raw = {}            # id -> canonical bytes (the snapshot line)
order = []          # the id sequence as served
pages = 0
mig = Counter()
for page in search.pages_as_dicts():
    pages += 1
    for f in page["features"]:
        i = f["id"]
        order.append(i)
        b = canon(f)
        raw[i] = b
        p = f["properties"]
        it = pystac.Item.from_dict(f)          # the parse every pystac-client user gets
        mig["raw_proj_epsg"] += "proj:epsg" in p
        mig["parsed_proj_epsg_none"] += it.properties.get("proj:epsg") is None
        mig["parsed_proj_code"] += "proj:code" in it.properties
        items[i] = dict(
            sha=hashlib.sha256(b).hexdigest(), core=core(f), core_raw=core(f, False), geom=f["geometry"], dt=p.get("datetime"),
            coll=f.get("collection"), grid=p.get("grid:code"), cc=p.get("eo:cloud_cover"),
            s3=p.get("earthsearch:s3_path"), hrefs={k: a["href"] for k, a in f["assets"].items()},
            nodata=p.get("s2:nodata_pixel_percentage"))
tick("search", t0)
N = len(items)
rec("search_matched", matched)
rec("search_items", N)
rec("search_pages_limit100", pages)
check("matched() == items paginated", matched == len(order), "%s vs %s" % (matched, len(order)))
check("no id served twice", len(order) == N, "%d served, %d distinct" % (len(order), N))
ids_sorted = sorted(items)
set_sha = hashlib.sha256("\n".join("%s %s" % (i, items[i]["sha"]) for i in ids_sorted).encode()).hexdigest()
rec("item_set_sha256", set_sha)
rec("id_list_sha256", hashlib.sha256("\n".join(ids_sorted).encode()).hexdigest())
check("pystac renames proj:epsg on parse, on every item",
      mig["raw_proj_epsg"] == N and mig["parsed_proj_epsg_none"] == N and mig["parsed_proj_code"] == N,
      dict(mig))

# every item satisfies the query it was returned for
q = box(*BBOX)
bad = [i for i, v in items.items()
       if not (v["coll"] == COLL and str(v["dt"]).startswith(YEAR) and shape(v["geom"]).intersects(q))]
check("every item: collection, year and bbox-intersection hold", not bad, "%d violate" % len(bad))

# ---------------------------------------------------------------- 2. invariances of the search itself
t0 = time.time()
seq1000 = [f["id"] for f in client.search(collections=[COLL], bbox=BBOX, datetime=YEAR, limit=1000,
                                           fields=IDS_ONLY).items_as_dicts()]
check("page size invariance: limit 1000 (ids only) == limit 100, same sequence", seq1000 == order,
      "%d vs %d ids" % (len(seq1000), len(order)))
months = {}
for m in range(1, 13):
    months[m] = [f["id"] for f in client.search(collections=[COLL], bbox=BBOX, datetime="%s-%02d" % (YEAR, m),
                                                 limit=1000, fields=IDS_ONLY).items_as_dicts()]
allm = [i for m in months for i in months[m]]
check("12 monthly searches are disjoint and their union is the year", len(allm) == len(set(allm)) and set(allm) == set(items),
      "sum %d, distinct %d, year %d" % (len(allm), len(set(allm)), N))
rec("per_month", ",".join(str(len(months[m])) for m in range(1, 13)))
mbad = [i for m in months for i in months[m] if not str(items.get(i, {}).get("dt", "")).startswith("%s-%02d" % (YEAR, m))]
check("each monthly search returns only that month's items", not mbad, "%d misplaced" % len(mbad))
lo = {f["id"] for f in client.search(collections=[COLL], bbox=BBOX, datetime=YEAR, limit=1000, fields=IDS_ONLY,
                                     query={"eo:cloud_cover": {"lt": 20}}).items_as_dicts()}
hi = {f["id"] for f in client.search(collections=[COLL], bbox=BBOX, datetime=YEAR, limit=1000, fields=IDS_ONLY,
                                     query={"eo:cloud_cover": {"gte": 20}}).items_as_dicts()}
nocc = {i for i, v in items.items() if v["cc"] is None}
check("cloud_cover < 20 and >= 20 partition the year", not (lo & hi) and (lo | hi | nocc) == set(items) and not ((lo | hi) & nocc),
      "%d + %d + %d without cloud cover = %d" % (len(lo), len(hi), len(nocc), N))
wrong = [i for i in lo if not items[i]["cc"] < 20] + [i for i in hi if not items[i]["cc"] >= 20]
check("the query predicate holds on every returned item", not wrong, "%d violate" % len(wrong))
rec("cloud_lt20", len(lo))
rec("cloud_ge20", len(hi))
rec("cloud_missing", len(nocc))
# the API's own aggregation endpoint -- a server-side count by a different code path
agg = sess().get(API + "/aggregate", params=dict(
    collections=COLL, aggregations="total_count,grid_code_frequency",
    bbox=",".join(map(str, BBOX)), datetime="2024-01-01T00:00:00Z/2024-12-31T23:59:59Z"), timeout=120).json()
A = {a["name"]: a for a in agg["aggregations"]}
gridc = Counter(v["grid"] for v in items.values())
aggc = {b["key"]: b["frequency"] for b in A["grid_code_frequency"]["buckets"]}
check("aggregate total_count == items", A["total_count"]["value"] == N, A["total_count"]["value"])
check("aggregate grid_code_frequency == per-tile counts of the items",
      aggc == dict(gridc) and A["grid_code_frequency"].get("overflow", 0) == 0,
      "%d tiles; overflow %s" % (len(aggc), A["grid_code_frequency"].get("overflow")))
rec("tiles", len(gridc))
tick("invariances", t0)

# ---------------------------------------------------------------- 3. the bucket, listed independently
t0 = time.time()


def list_prefix(prefix, delim=None):
    keys, pre, tok = [], [], None
    while True:
        prm = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if delim:
            prm["delimiter"] = delim
        if tok:
            prm["continuation-token"] = tok
        r = sess().get(HOST + "/", params=prm, timeout=120)
        r.raise_for_status()
        x = ET.fromstring(r.content)
        for c in x.iter(NS + "Contents"):
            keys.append((c.find(NS + "Key").text, int(c.find(NS + "Size").text), c.find(NS + "ETag").text.strip('"')))
        for c in x.iter(NS + "CommonPrefixes"):
            pre.append(c.find(NS + "Prefix").text)
        if x.find(NS + "IsTruncated").text != "true":
            return keys, pre
        tok = x.find(NS + "NextContinuationToken").text


with ThreadPoolExecutor(NT) as ex:
    squares = [p for _, pre in ex.map(lambda zb: list_prefix("%s%d/%s/" % (ROOT, zb[0], zb[1]), "/"),
                                      [(z, b) for z in ZONES for b in BANDS]) for p in pre]
    listed = [k for ks, _ in ex.map(lambda s: list_prefix(s + YEAR + "/"), squares) for k in ks]
L = {k: (s, e) for k, s, e in listed}
scenes = defaultdict(list)                  # scene dir -> file names
for k in L:
    d, f = k.rsplit("/", 1)
    scenes[d].append(f)
rec("bucket_squares_listed", len(squares))
rec("bucket_objects_listed", len(L))
rec("bucket_scene_dirs", len(scenes))
tick("listing", t0)

t0 = time.time()


def get_item(d):
    sid = d.rsplit("/", 1)[1]
    k = "%s/%s.json" % (d, sid)
    if k not in L:
        return d, None
    r = sess().get("%s/%s" % (HOST, k), timeout=120)
    r.raise_for_status()
    j = r.json()
    return d, dict(id=j["id"], sha_raw=hashlib.sha256(r.content).hexdigest(), core=core(j), core_raw=core(j, False),
                   dt=j["properties"].get("datetime"), coll=j.get("collection"),
                   hit=str(j["properties"].get("datetime")).startswith(YEAR) and shape(j["geometry"]).intersects(q))


with ThreadPoolExecutor(NT) as ex:
    B = dict(ex.map(get_item, sorted(scenes)))
tick("bucket_items", t0)
nojson = sorted(d for d, v in B.items() if v is None)
idmis = sorted(d for d, v in B.items() if v and v["id"] != d.rsplit("/", 1)[1])
rec("bucket_scene_dirs_without_item_json", len(nojson))
rec("bucket_scene_dirs_without_item_json_names", ";".join(d.rsplit("/", 1)[1] for d in nojson) or "none")
check("every bucket item's id == its directory name", not idmis, "%d differ" % len(idmis))
expect = {v["id"] for v in B.values() if v and v["hit"]}
by_id = {v["id"]: d for d, v in B.items() if v}
rec("bucket_items_read", sum(1 for v in B.values() if v))
rec("bucket_items_matching_query", len(expect))
missing = sorted(expect - set(items))
extra = sorted(set(items) - expect)
check("search == the query recomputed from the bucket's own items (nothing missed)", not missing,
      "%d in the bucket, not returned: %s" % (len(missing), missing[:5]))
check("search == the query recomputed from the bucket's own items (nothing invented)", not extra,
      "%d returned, not in the bucket: %s" % (len(extra), extra[:5]))
pathbad = [i for i in items if i in by_id and items[i]["s3"] != "s3://sentinel-cogs/" + by_id[i]]
check("earthsearch:s3_path == the item's bucket directory", not pathbad, "%d differ" % len(pathbad))
coremis = sorted(i for i in items if i in by_id and items[i]["core"] != B[by_id[i]]["core"])
rawmis = sum(1 for i in items if i in by_id and items[i]["core_raw"] != B[by_id[i]]["core_raw"])
rec("api_vs_bucket_differ_before_int_float_normalisation", rawmis)
check("API item == bucket item, value for value, except links and created/updated", not coremis,
      "%d of %d differ: %s" % (len(coremis), N, coremis[:5]))

# ---------------------------------------------------------------- 4. every asset href against the listing
dangling, other_hosts, n_cogs_checked, ref = [], Counter(), 0, defaultdict(set)
for i, v in items.items():
    for name, h in v["hrefs"].items():
        if h.startswith(HOST + "/"):
            k = h[len(HOST) + 1:]
        elif h.startswith("s3://sentinel-cogs/"):
            k = h[len("s3://sentinel-cogs/"):]
        else:
            other_hosts[h.split("/")[2]] += 1
            continue
        n_cogs_checked += 1
        ref[k.rsplit("/", 1)[0]].add(k.rsplit("/", 1)[1])
        if k not in L:
            dangling.append((i, name))
dang_items = sorted({i for i, _ in dangling})
rec("asset_hrefs_in_sentinel_cogs", n_cogs_checked)
rec("asset_hrefs_dangling", len(dangling))
rec("items_with_dangling_hrefs", len(dang_items))
rec("dangling_items_sha256", hashlib.sha256("\n".join(dang_items).encode()).hexdigest())
rec("asset_hrefs_elsewhere", ";".join("%s=%d" % kv for kv in sorted(other_hosts.items())))
dang_nodata = [i for i in dang_items if items[i]["nodata"] == 100]
rec("dangling_items_100pct_nodata", len(dang_nodata))
orphans, orphan_items = Counter(), Counter()
for i, v in items.items():
    d = by_id.get(i)
    if d:
        for f in scenes[d]:
            if f not in ref[d] and f != i + ".json":
                orphans[f] += 1
                if f != "L2A_PVI.tif":
                    orphan_items[i] += 1
rec("items_without_L2A_PVI", sum(1 for i in items if i in by_id and "L2A_PVI.tif" not in scenes[by_id[i]]))
rec("items_with_unreferenced_data_objects", ";".join("%s=%d" % kv for kv in sorted(orphan_items.items())) or "none")
rec("unreferenced_objects_by_name", ";".join("%s=%d" % kv for kv in sorted(orphans.items())) or "none")

# ---------------------------------------------------------------- 5. the tile year earth-observation-real pinned
eo_scenes = [l.strip().rsplit("/", 1)[1] for l in open(os.path.join(WORK, "eo-real/scenes.txt")) if l.strip()]
got = sorted(i for i, v in items.items() if v["grid"] == "MGRS-11SKA")
check("11SKA in this search == earth-observation-real's 153 scenes + the one nodata item",
      got == sorted(eo_scenes + ["S2B_11SKA_20241025_0_L2A"]), "%d returned" % len(got))
pins = {}
for l in open(os.path.join(WORK, "eo-real/pins.sha256")):
    h, p = l.split()
    pins[p] = h
sizes = dict(l.rstrip("\n").split("\t") for l in open(os.path.join(WORK, "eo-real/sizes.tsv")))
jbad = [s for s in eo_scenes if s not in by_id or B[by_id[s]]["sha_raw"] != pins[s + "/item.json"]]
check("those 153 bucket items still match earth-observation-real's sha256 pins", not jbad, "%d differ: %s" % (len(jbad), jbad[:3]))
sbad = []
for p, n in sizes.items():
    s, f = p.split("/")
    if s not in by_id:
        sbad.append(p)
        continue
    k = by_id[s] + "/" + (s + ".json" if f == "item.json" else f)
    if L.get(k, (None,))[0] != int(n):
        sbad.append(p)
check("their 612 objects' listed sizes == earth-observation-real's pinned sizes", not sbad, "%d differ" % len(sbad))

# ---------------------------------------------------------------- 6. the snapshot
mine = sorted((k, s, e) for d in (by_id[i] for i in ids_sorted if i in by_id) for k, (s, e) in
              ((d + "/" + f, L[d + "/" + f]) for f in sorted(scenes[d])))
listing_sha = hashlib.sha256("".join("%s\t%d\t%s\n" % r for r in mine).encode()).hexdigest()
rec("snapshot_listing_rows", len(mine))
rec("snapshot_listing_sha256", listing_sha)
if MODE == "pin":
    sd = os.path.join(OUT, "snapshot")
    os.makedirs(sd, exist_ok=True)
    with gzip.GzipFile(os.path.join(sd, "items.jsonl.gz"), "wb", mtime=0) as g:
        for i in ids_sorted:
            g.write(raw[i] + b"\n")
    with gzip.GzipFile(os.path.join(sd, "listing.tsv.gz"), "wb", mtime=0) as g:
        for r in mine:
            g.write(("%s\t%d\t%s\n" % r).encode())
    with open(os.path.join(sd, "snapshot.sha256"), "w") as f:
        for n in ("items.jsonl.gz", "listing.tsv.gz"):
            f.write("%s  %s\n" % (hashlib.sha256(open(os.path.join(sd, n), "rb").read()).hexdigest(), n))
    print(open(os.path.join(sd, "snapshot.sha256")).read(), flush=True)
else:
    snap = {}
    with gzip.open(os.path.join(WORK, "snapshot/items.jsonl.gz"), "rb") as g:
        for line in g:
            line = line.rstrip(b"\n")
            snap[json.loads(line)["id"]] = hashlib.sha256(line).hexdigest()
    gone, new = sorted(set(snap) - set(items)), sorted(set(items) - set(snap))
    changed = sorted(i for i in set(snap) & set(items) if snap[i] != items[i]["sha"])
    rec("snapshot_items", len(snap))
    check("live item set == the pinned snapshot, every item byte-identical",
          not gone and not new and not changed,
          "%d gone, %d new, %d changed: %s" % (len(gone), len(new), len(changed), (gone + new + changed)[:5]))
    sl = []
    with gzip.open(os.path.join(WORK, "snapshot/listing.tsv.gz"), "rt") as g:
        for line in g:
            k, s, e = line.rstrip("\n").split("\t")
            sl.append((k, int(s), e))
    check("live listing of those scenes == the pinned (key, size, ETag) rows",
          sl == mine, "%d pinned rows, %d live; %d differ" % (len(sl), len(mine), len(set(sl) ^ set(mine))))

# ---------------------------------------------------------------- per-tile table + timings
with open(os.path.join(OUT, "tiles.tsv"), "w") as f:
    f.write("grid\titems\tlt20\tdangling\n")
    for g in sorted(gridc):
        ii = [i for i, v in items.items() if v["grid"] == g]
        f.write("%s\t%d\t%d\t%d\n" % (g, len(ii), sum(1 for i in ii if i in lo), sum(1 for i in ii if i in dang_items)))
with open(os.path.join(OUT, "dangling.tsv"), "w") as f:
    for i, n in sorted(dangling):
        f.write("%s\t%s\t%s\n" % (i, n, items[i]["nodata"]))
for k in ("search", "invariances", "listing", "bucket_items"):
    rec("wall_s_" + k, "%.1f" % T[k])
rec("wall_s_total", "%.1f" % sum(T.values()))
rec("peak_rss_gib", "%.2f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20))
with open(os.path.join(OUT, "score.tsv"), "w") as f:
    f.write("key\tvalue\n")
    f.writelines("%s\t%s\n" % kv for kv in score)
with open(os.path.join(OUT, "checks.tsv"), "w") as f:
    f.write("check\tresult\tdetail\n")
    f.writelines("%s\t%s\t%s\n" % c for c in checks)
print("%d checks, %d failed: %s" % (len(checks), len(FAILED), FAILED), flush=True)
if FAILED:
    sys.exit(1)
print("REAL OK", flush=True)
