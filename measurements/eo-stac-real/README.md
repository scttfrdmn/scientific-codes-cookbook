# pystac-client on a real workload — a state-year of Sentinel-2, held to the bucket it indexes

> **A live `/search` for every 2024 Sentinel-2 L2A item over California returns 18,346 items. That
> is exactly the set recomputed from the `sentinel-cogs` bucket's own 53,706 item files, and every
> one of the 347,724 COG hrefs resolves to a listed object.** Every value is identical on Graviton4,
> Intel and AMD. Each run takes 3–3.5 minutes and costs $0.03–0.04 billed. The wait is on the API and S3, not on the
> chip.

Workload: the Earth Search v1 (element84) STAC API, collection `sentinel-2-l2a`,
`bbox=[-124.5, 32.5, -114.1, 42.0]` (California), `datetime="2024"`. That gives 18,346 items on
135 MGRS tiles, from three satellites. A state and a year is the area-and-period a regional study
starts from, and it is the search a researcher actually types. The independent truth is the bucket
the catalog indexes. The run lists every 2024 scene directory of MGRS zones 10–12, latitude
bands R–T (384 grid squares, 1,124,914 objects), reads each scene's own item JSON, and recomputes
the query from that. The tile year [earth-observation-real](../earth-observation-real/README.md)
pinned (11SKA, 153 scenes) is inside this search and is checked against its pins.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/earth-observation-real/stage-inputs.sh "$B"   # its scenes.txt / sizes.tsv / pins are reused
bash measurements/eo-stac-real/stage-inputs.sh "$B"             # scripts; 00-pin writes the snapshot if absent
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py eo-stac $a "$B" c1 \
    measurements/eo-stac-real/01-check.task.json)"
done
bash measurements/eo-stac-real/collect.sh "$B" c1               # results/<chip>/, results/compare.txt
```

The task runs [`real.sh`](real.sh), which runs [`real.py`](real.py). Its core:

```python
client = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")
search = client.search(collections=["sentinel-2-l2a"], bbox=BBOX, datetime="2024", limit=100)
search.matched()                                   # 18346, before a single page is read
for page in search.pages_as_dicts(): ...           # 184 pages; each item also through pystac.Item.from_dict
client.search(..., limit=1000, fields={"include": ["id", "properties.datetime"],
                                       "exclude": ["assets", "geometry", "links", "bbox"]})
```

Live catalogs change, so the reference is a **snapshot**. `00-pin.task.json` ran the same search and
listing on AWS (2026-10-20). It wrote every returned item as canonical JSON, plus the
(key, size, ETag) of every object in those items' scene directories. Both files went S3-to-S3 into
`inputs/eo-stac-real/snapshot/` and are pinned by sha256 in [`pins.sha256`](pins.sha256). Two pin
runs 10 minutes apart produced byte-identical snapshot files.

## Results

8 vCPU, one `m8?.2xlarge` (32 GiB) per chip, 32 HTTP threads, pystac-client 0.9.0 / pystac 1.15.2 /
shapely 2.2.0 (the aarch.science arm64 image; on x86 its linux-64 twin, the same versions):

| chip | instance | real.py wall | task window | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 208.1 s | 295 s | 346 s | 0.3590 | **0.0208** | **0.0345** | 1.52 GiB |
| Xeon 6975P-C | `m8i.2xlarge` | 206.7 s | 264 s | 347 s | 0.4234 | 0.0243 | 0.0408 | 1.52 GiB |
| EPYC 9R45 | `m8a.2xlarge` | **187.8 s** | 234 s | 282 s | 0.4869 | 0.0254 | 0.0381 | 1.52 GiB |

| phase (s) | search, 184 pages | 15 invariance searches + aggregate | list 1.12 M objects | GET 53,706 bucket items |
|---|---|---|---|---|
| Graviton4 | 98.3 | 8.1 | 10.8 | 90.8 |
| Intel | 95.8 | 7.9 | 9.2 | 93.8 |
| AMD | 84.5 | 8.0 | 7.2 | 88.1 |

A *result* is the whole search plus every check below. $/hr is spawn's `cost_estimate` over the
task window. *Billed* is EC2 launch to terminate, priced at that rate. Peak RSS is real.py's `ru_maxrss`.
The cgroup's `memory.peak` was 1.5 GiB on Graviton4 and 3.5 GiB on x86, where it also counts the
offline env install. Threads per core: 1 on Graviton4 and AMD, 2 on Intel (8 vCPU = 4 cores). OpenBLAS
selects `neoversev2` and `Cooperlake`, but nothing here calls BLAS.

- **The chip barely matters.** The search is 184 sequential pages at ~0.5 s each, which is
  server time. The bucket phase is 53,706 HTTPS GETs. AMD is 10% faster end to end and Graviton4
  is cheapest per result, by 14–18% on compute and 9–15% billed.
- **A page cannot be larger than ~6 MB.** `limit=1000` full items fails with
  `APIError: {"message": "Internal server error"}`. Measured with plain POSTs, `limit=260` (5.57 MB)
  returns 200 and `limit=300` returns 502. The error does not say "page too big". Keep full-item
  searches at pystac-client's default of 100. Ids-only searches page fine at 1000.
- **`fields` must keep `properties.datetime`.** Excluding `properties` gives a good first page.
  Page 2 then fails server-side with `parse_exception: cannot parse empty date`, because Earth
  Search builds its next-page token from each item's datetime.
- **Boot is a third of the bill.** It is 94–140 s of a 282–347 s billed window. These timings
  are not compute cost ([layout](../../patterns/layout-and-effective-cost.md)).
- n = 1 per chip. Every value is deterministic: two pin runs and three chip runs agree on all of them.

## What held at scale, on all three chips

| claim | why it must hold | all three chips |
|---|---|---|
| `matched()` == items paginated, no id served twice | pagination is complete and exact | 18,346 / 18,346 |
| limit 1000 (ids only) == limit 100, the same **sequence** | page size must not change the answer | 18,346 ids, same order |
| 12 monthly searches: disjoint, union == the year, each only its month | datetime range handling at every boundary | 1,593 … 1,945, sum 18,346 |
| `eo:cloud_cover` < 20 ∪ ≥ 20 == the year, disjoint, predicate holds | the `query` extension partitions the set | 9,886 + 8,460 + 0 missing |
| server `/aggregate` total and per-tile counts == the items | a second server code path | 18,346; 135 tiles; overflow 0 |
| every item: collection, year, geometry ∩ bbox (shapely) | the result satisfies its own query | 0 violate |
| **search == the query recomputed from the bucket's own items** | nothing missed, nothing invented | 18,346 of 53,706 match; 0 / 0 |
| `earthsearch:s3_path` == the item's bucket directory | the catalog points at the right place | 0 differ |
| **API item == bucket item, value for value**, except links and created/updated | the catalog serves what the bucket holds | 0 of 18,346 differ |
| every `sentinel-cogs` asset href is a listed key | no dangling hrefs | 347,724 checked, 0 dangling |
| pystac renames `proj:epsg` → `proj:code` on parse | the recipe's premise, on every item | 18,346 of 18,346 |
| 11SKA == earth-observation-real's 153 scenes + its nodata item; their item files still match its sha256 pins; their 612 sizes match | reuse of a pinned tile year | 154; 0 differ; 0 differ |
| live items == the snapshot, byte for byte; live listing == pinned (key, size, ETag) | the catalog and bucket did not move between runs | 18,346; 384,386 rows |

**Value for value, not byte for byte.** Every API item differs from its bucket file when compared
as raw JSON (18,346 of 18,346). The API serialises the bucket's integral floats as integers
(`proj:transform` `20.0` → `20`, `s2:snow_ice_percentage` `0.0` → `0`). With that one
normalisation, nothing else differs. A pipeline that hashes items to detect change has to compare
API to API or bucket to bucket, never across.

**Reported, not asserted: what the bucket holds that the catalog does not say.**

- `S2B_10TEM_20240829_0_L2A`, a 0%-nodata scene, has an item listing only 5 of its COG assets
  (`coastal`, `nir09`, metadata, thumbnail). Fourteen data COGs sit in its directory, among them
  B02–B08, SCL, TCI and AOT, and **no asset references them**. The item JSON is 6.5 KB against the usual ~22 KB.
  `item.assets["red"]` raises `KeyError` on a scene whose red band is in the bucket.
- `L2A_PVI.tif` is in 18,302 of the 18,346 scene directories and is never an asset. The other 44
  directories have none.
- 3 of the 53,709 listed scene directories have no item JSON: `S2C_10TFT_20241202_1_L2A`,
  `S2C_10TGS_20241119_1_L2A`, `S2A_11RPK_20240201_0_L2A`. None falls in the query.
- Some items point their data assets at the requester-pays JP2 bucket, not at COGs. One example
  is the 100%-nodata `S2B_11SKA_20241025_0_L2A`, whose `blue` is
  `s3://sentinel-s2-l2a/tiles/…/B02.jp2`. 296,291 hrefs point at `sentinel-s2-l2a`. That count was
  not split by item.

## Across chips: fixed point or path

**Nothing differed.** All 33 fixed values in `score.tsv` are identical on Intel and on AMD against
Graviton4. So are `checks.tsv` and `tiles.tsv`, the per-tile counts. The fixed values include the
sha256 of the item set and of the 384,386-row listing. Nothing here is floating-point arithmetic
that a chip could move. The work is JSON parsing, set algebra and shapely's exact predicate on the
same coordinates. Only the timings changed, and by ≤ 10%.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/earth-observation@sha256:e5e8125b307e…` (arm64), the recipe's; x86: the `earth-observation-6a5be4ba03fa` linux-64 twin |
| data (tier: RODA) | Earth Search v1 `sentinel-2-l2a` over `s3://sentinel-cogs/sentinel-s2-l2a-cogs/`, live |
| reference | `snapshot/items.jsonl.gz` (18,346 items, `0d2d6e10…`) and `snapshot/listing.tsv.gz` (384,386 rows, `5d487970…`), from `00-pin`, 2026-10-20 |
| reused | earth-observation-real's `scenes.txt`, `sizes.tsv`, `pins.sha256` (11SKA 2024) |
| scripts | `real.py`, `real.sh`, pinned in `pins.sha256` and checked in-task |

## Caveats

- The snapshot only pins what the catalog returned **on 2026-10-20**. If Earth Search reprocesses or
  re-ingests any of these items, the snapshot checks fail loudly, by design. Re-pin with `00-pin`; that is a pin swap,
  so re-run all three chips.
- ETag is not a content hash for multipart uploads. The listing pin proves that no object was
  rewritten, not what its bytes are. The bytes of the 11SKA bands are pinned by sha256 in
  earth-observation-real.
- The bbox is a rectangle around California, not the state outline. Its corners include parts of
  Oregon, Nevada, Arizona and Mexico.
