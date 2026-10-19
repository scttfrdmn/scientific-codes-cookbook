# sf + terra on a real workload: every US census tract, and Colorado's 30 m DEM summarised by tract.
#
#   vector  all 85k TIGER 2024 tracts (56 state-equivalents): read with sf, measured with terra's
#           ellipsoidal expanse() against Census's own ALAND + AWATER, aggregated to counties and
#           checked against the county file's polygons, and spatially joined back to their counties
#   raster  the 28 Copernicus GLO-30 tiles over Colorado (362.88 M px, staged by geospatial-real),
#           zonal statistics by Colorado tract and by county, two routes
#
# Exact identities are integers or partitions; everything else is reported with the reason for its bound.
options(warn = 1)
out <- list()
rec <- function(k, v) {
  v <- paste(as.character(v), collapse = " ")
  out[[k]] <<- v; cat(sprintf("%s\t%s\n", k, v)); flush(stdout())
}
dump <- function() {
  writeLines(c("observable\tvalue",
               vapply(names(out), function(k) sprintf("%s\t%s", k, out[[k]]), "")),
             "/tmp/out/score.tsv")
}
# Every identity is checked and the run continues, so one failure cannot hide another; any failure
# fails the run at the end, and the completion sentinel is never written.
fails <- character()
ok <- function(cond, msg) if (!isTRUE(as.logical(cond))) {
  fails <<- c(fails, msg); cat(sprintf("FAIL: %s\n", msg)); flush(stdout())
}
clock <- function(k, expr) {
  t0 <- proc.time()[["elapsed"]]
  v <- force(expr)
  rec(paste0("t_", k, "_s"), sprintf("%.2f", proc.time()[["elapsed"]] - t0))
  v
}

suppressPackageStartupMessages({library(sf); library(terra)})
terra::terraOptions(progress = 0, tempdir = "/var/tmp/work")
rec("r_version", paste0(R.version$major, ".", R.version$minor))
rec("sf", packageVersion("sf")); rec("terra", packageVersion("terra"))
rec("s2", packageVersion("s2"))
sv <- sf::sf_extSoftVersion()
rec("geos", sv[["GEOS"]]); rec("gdal", sv[["GDAL"]]); rec("proj", sv[["PROJ"]])
rec("longdouble_digits", .Machine$longdouble.digits)
rec("blas", sessionInfo()$BLAS)

# =============================================================================================
# VECTOR: every census tract in the US
# =============================================================================================
zips <- sort(list.files("/tmp/tract", pattern = "^tl_2024_[0-9]{2}_tract\\.zip$"))
rec("tract_files", length(zips))
ok(length(zips) == 56L, "expected 56 tract files")
tr <- clock("read_tracts", {
  parts <- lapply(zips, function(z) {
    x <- sf::st_read(paste0("/vsizip//tmp/tract/", z), quiet = TRUE)
    x[, c("GEOID", "ALAND", "AWATER")]
  })
  epsg <- vapply(parts, function(x) as.character(sf::st_crs(x)$epsg), "")
  ok(all(epsg == "4269"), "a tract file is not EPSG:4269")
  do.call(rbind, parts)
})
rec("tracts", nrow(tr))
ok(!anyDuplicated(tr$GEOID), "duplicate tract GEOIDs")
ok(all(nchar(tr$GEOID) == 11L), "a tract GEOID is not 11 digits")
census <- as.numeric(tr$ALAND) + as.numeric(tr$AWATER)
rec("census_tract_total_m2", sprintf("%.0f", sum(census)))

co <- sf::st_read("/vsizip//tmp/tiger.zip", quiet = TRUE)
rec("counties", nrow(co))
ok(nrow(co) == 3235L, "expected 3235 counties")
ok(identical(sf::st_crs(co)$epsg, 4269L), "counties should be EPSG:4269")
co_census <- as.numeric(co$ALAND) + as.numeric(co$AWATER)

# --- identity 1: ellipsoidal area against Census, every tract -------------------------------
tv <- terra::vect(tr)
ok(nrow(tv) == nrow(tr), "sf -> terra lost tract geometries")
g <- terra::geom(tv)
rec("tract_vertices", nrow(g))
# Reported: the published vertices are NOT on a decimal grid (max distance to the nearest 1e-6 and
# 1e-7 degree step, in steps), so the shapefile carries full doubles, not a rounded copy.
offgrid <- function(q) max(abs(g[, "x"] * 10^q - round(g[, "x"] * 10^q)),
                           abs(g[, "y"] * 10^q - round(g[, "y"] * 10^q)))
rec("coord_offgrid_1e-6_steps", sprintf("%.3e", offgrid(6)))
rec("coord_offgrid_1e-7_steps", sprintf("%.3e", offgrid(7)))
rm(g); invisible(gc())
geod <- clock("expanse_tracts", terra::expanse(tv, unit = "m", transform = TRUE))
dif <- abs(geod - census)
rel <- dif / census
rec("geod_max_rel", sprintf("%.6e", max(rel)))
rec("geod_median_rel", sprintf("%.6e", median(rel)))
rec("geod_max_abs_m2", sprintf("%.3f", max(dif)))
w <- which.max(rel)
rec("geod_worst_tract", sprintf("%s census=%.0f m2 terra=%.3f m2", tr$GEOID[w], census[w], geod[w]))
rec("geod_tracts_rel_gt_1e-6", sum(rel > 1e-6))
# A per-tract bound with the shape of the two error sources it must tell apart. ALAND and AWATER
# are integer m2 (1 m2 between them). Anything else Census did differently -- its own coordinates,
# its own area routine -- moves a boundary, so it scales with PERIMETER: the bound allows a mean
# boundary offset of 0.0557 m (half a 1e-6 degree step of latitude) all the way round. A model error
# (the sphere) scales with AREA, and fails this bound on most tracts -- the control below.
per <- clock("perim_tracts", terra::perim(tv))
bound <- 1 + per * 0.0557
rec("geod_resid_over_bound_max", sprintf("%.4f", max(dif / bound)))
rec("geod_resid_over_bound_median", sprintf("%.4f", median(dif / bound)))
bad <- sum(dif > bound)
rec("geod_tracts_outside_bound", bad)
ok(bad == 0L, sprintf("%d tracts miss Census by more than 1 m2 + perimeter x 0.0557 m", bad))
rec("geod_tracts_rel_gt_1e-5", sum(rel > 1e-5))
tot <- abs(sum(geod) - sum(census)) / sum(census)
rec("geod_total_rel", sprintf("%.6e", tot))
ok(tot < 1e-6, sprintf("total tract area off Census by %.3e", tot))

# --- control: the spherical model is clearly worse, per tract --------------------------------
sf::sf_use_s2(TRUE)
spher <- clock("st_area_s2_tracts", as.numeric(sf::st_area(tr)))
srel <- abs(spher - census) / census
rec("spher_max_rel", sprintf("%.6e", max(srel)))
rec("spher_median_rel", sprintf("%.6e", median(srel)))
ratio <- median(srel) / median(rel)
rec("spher_vs_geod_median_ratio", sprintf("%.3e", ratio))
ok(ratio >= 1e3, sprintf("spherical only %.1fx worse at the median", ratio))
rec("spher_closer_than_geod_tracts", sum(srel < rel))
rec("spher_tracts_outside_bound", sum(abs(spher - census) > bound))

# --- identity 2: tracts partition counties, in geometry ---------------------------------------
key <- substr(tr$GEOID, 1, 5)
ok(setequal(unique(key), co$GEOID), "tract county keys != county GEOIDs")
geodC <- clock("expanse_counties", terra::expanse(terra::vect(co), unit = "m", transform = TRUE))
sumT <- tapply(geod, key, sum)[co$GEOID]
crel <- abs(sumT - geodC) / geodC
rec("additivity_max_rel", sprintf("%.6e", max(crel)))
rec("additivity_median_rel", sprintf("%.6e", median(crel)))
rec("additivity_max_abs_m2", sprintf("%.3f", max(abs(sumT - geodC))))
wc <- which.max(crel)
rec("additivity_worst_county", sprintf("%s %s tracts=%.1f county=%.1f", co$GEOID[wc], co$NAME[wc],
                                       sumT[wc], geodC[wc]))
rec("additivity_counties_rel_gt_1e-9", sum(crel > 1e-9))
# TIGER is one topology: a county's boundary is its tracts' outer edges, vertex for vertex, so the
# interior edges cancel and the only residual is the order of a floating-point sum (85k terms)
ok(max(crel) < 1e-12, sprintf("tract areas do not add up to their county's: %.3e", max(crel)))
# Census's own integers, tracts summed to counties: reported, it is the reference's property
csum <- tapply(as.numeric(tr$ALAND) + as.numeric(tr$AWATER), key, sum)[co$GEOID]
rec("census_additivity_counties_exact", sum(csum == co_census))
rec("census_additivity_max_abs_m2", sprintf("%.0f", max(abs(csum - co_census))))
ok(all(csum == co_census), "Census's tract ALAND+AWATER do not sum to its county figures")

# --- identity 3: a spatial join recovers the GEOID hierarchy ---------------------------------
# A point inside each tract (GEOS, planar in lon/lat -- the frame TIGER's edges are drawn in) must
# fall in exactly one county, and it must be the county the tract's GEOID names.
sf::sf_use_s2(FALSE)
pts <- clock("point_on_surface", suppressMessages(suppressWarnings(
  sf::st_point_on_surface(sf::st_geometry(tr)))))
hits <- clock("st_intersects_join", suppressMessages(sf::st_intersects(pts, sf::st_geometry(co))))
nh <- lengths(hits)
rec("join_points_in_zero_counties", sum(nh == 0L))
rec("join_points_in_multiple_counties", sum(nh > 1L))
first <- vapply(hits, function(h) if (length(h)) h[[1]] else NA_integer_, 1L)
match_ok <- !is.na(first) & co$GEOID[first] == key
rec("join_matches_geoid", sum(match_ok))
ok(all(nh == 1L), "a tract point is in zero or several counties")
ok(all(match_ok), sprintf("%d tracts joined to the wrong county", sum(!match_ok)))
sf::sf_use_s2(TRUE)

# =============================================================================================
# RASTER: Colorado's DEM, summarised by tract and by county
# =============================================================================================
tifs <- sort(list.files("/tmp/dem", pattern = "_DEM\\.tif$", full.names = TRUE))
rec("dem_tiles", length(tifs))
ok(length(tifs) == 28L, "expected 28 DEM tiles")
dem <- clock("vrt", terra::vrt(tifs))
rec("dem_dim", paste(dim(dem)[1:2], collapse = " x "))
rec("dem_ncell", terra::ncell(dem))
ok(terra::ncell(dem) == 362880000, "mosaic is not 14400 x 25200")
rec("dem_ext", paste(sprintf("%.6f", as.vector(terra::ext(dem))), collapse = " "))

cot <- tr[substr(tr$GEOID, 1, 2) == "08", ]
coc <- co[co$STATEFP == "08", ]
rec("co_tracts", nrow(cot)); rec("co_counties", nrow(coc))
cot$zid <- seq_len(nrow(cot))
coc$zid <- seq_len(nrow(coc))
# TIGER is NAD83 and the DEM WGS84. Put the polygons in the raster's CRS once, explicitly, so every
# route below sees the same coordinates (extract() would otherwise transform on its own).
cotv <- terra::vect(cot); cocv <- terra::vect(coc)
g0 <- terra::geom(cotv)[, c("x", "y")]
cotv <- terra::project(cotv, terra::crs(dem)); cocv <- terra::project(cocv, terra::crs(dem))
rec("nad83_to_wgs84_max_shift_deg", sprintf("%.3e", max(abs(terra::geom(cotv)[, c("x", "y")] - g0))))
rm(g0)

# Integer decimetres: every partial sum below 2^53 is exact in any order, so the sums are identities.
di <- clock("decimetres", round(dem * 10, 0))
v1 <- clock("valid_mask", terra::ifel(is.na(di), 0, 1))
zt <- clock("rasterize_tracts", terra::rasterize(cotv, dem, field = "zid"))
zc <- clock("rasterize_counties", terra::rasterize(cocv, dem, field = "zid"))

zs_t <- clock("zonal_tracts", terra::zonal(c(v1, di), zt, fun = "sum", na.rm = TRUE))
names(zs_t) <- c("zid", "n", "dm")
zs_c <- clock("zonal_counties", terra::zonal(c(v1, di), zc, fun = "sum", na.rm = TRUE))
names(zs_c) <- c("zid", "n", "dm")
rec("tract_zones_with_cells", nrow(zs_t))
rec("county_zones_with_cells", nrow(zs_c))

# partition: tract zones add up to the cells they cover, exactly
gn <- clock("global_tracts", terra::global(terra::mask(v1, zt), "sum", na.rm = TRUE)[1, 1])
gd <- terra::global(terra::mask(di, zt), "sum", na.rm = TRUE)[1, 1]
rec("tract_cells_valid", sprintf("%.0f", sum(zs_t$n)))
rec("tract_dm_sum", sprintf("%.0f", sum(zs_t$dm)))
rec("global_cells_valid", sprintf("%.0f", gn))
rec("global_dm_sum", sprintf("%.0f", gd))
ok(sum(zs_t$n) == gn, "tract cell counts do not partition the covered cells")
ok(sum(zs_t$dm) == gd, "tract elevation sums do not add up to the covered total")

# nesting: tract zones grouped by county == the county zones, cell for cell
tc <- substr(cot$GEOID[zs_t$zid], 1, 5)
agg_n <- tapply(zs_t$n, tc, sum); agg_d <- tapply(zs_t$dm, tc, sum)
cg <- coc$GEOID[zs_c$zid]
rec("nesting_counties_compared", length(cg))
nest_n <- sum(agg_n[cg] != zs_c$n, na.rm = TRUE) + sum(is.na(agg_n[cg]))
nest_d <- sum(agg_d[cg] != zs_c$dm, na.rm = TRUE) + sum(is.na(agg_d[cg]))
rec("nesting_counties_count_differs", nest_n)
rec("nesting_counties_sum_differs", nest_d)
rec("nesting_max_abs_cells", sprintf("%.0f", max(abs(agg_n[cg] - zs_c$n), na.rm = TRUE)))
gc_n <- terra::global(terra::mask(v1, zc), "sum", na.rm = TRUE)[1, 1]
rec("county_cells_valid", sprintf("%.0f", gc_n))

# A second route: per-polygon extract() rather than rasterize-then-group. Both decide membership
# by cell centre, so on the same polygons they must agree tract for tract, cell for cell.
ex <- clock("extract_tracts", terra::extract(c(v1, di), cotv, fun = sum, na.rm = TRUE, ID = TRUE))
names(ex) <- c("zid", "n", "dm")
m <- match(zs_t$zid, ex$zid)
rec("extract_vs_zonal_count_differs", sum(ex$n[m] != zs_t$n))
rec("extract_vs_zonal_sum_differs", sum(ex$dm[m] != zs_t$dm))
rec("extract_cells_total", sprintf("%.0f", sum(ex$n)))
ok(all(ex$n[m] == zs_t$n) && all(ex$dm[m] == zs_t$dm), "extract() and zonal() disagree")
# Control, and the trap it catches: rasterize() the NAD83 polygons straight onto the WGS84 grid.
# rasterize() does not reproject (extract() does, with a warning), so the polygons land ~0.9 m off.
nw <- 0L
zt83 <- withCallingHandlers(terra::rasterize(terra::vect(cot), dem, field = "zid"),
                            warning = function(w) { nw <<- nw + 1L; invokeRestart("muffleWarning") })
rec("rasterize_crs_mismatch_warnings", nw)
z83 <- terra::zonal(v1, zt83, fun = "sum", na.rm = TRUE); names(z83) <- c("zid", "n")
m83 <- match(z83$zid, ex$zid)
rec("crs_mismatch_tracts_differ", sum(z83$n != ex$n[m83]))
rec("crs_mismatch_max_abs_cells", sprintf("%.0f", max(abs(z83$n - ex$n[m83]))))
ok(sum(z83$n != ex$n[m83]) > 0, "the control did not move: a 0.9 m datum shift went undetected")
rm(zt83); invisible(gc())

# reported: the mean elevation by county, and the pixel-area estimate of each county's area
zs_c$mean_m <- zs_c$dm / zs_c$n / 10
top <- order(-zs_c$mean_m)[1:3]
rec("county_highest_mean_m", paste(sprintf("%s=%.1f", coc$NAME[zs_c$zid[top]], zs_c$mean_m[top]),
                                   collapse = ", "))
rec("co_mean_elevation_m", sprintf("%.3f", sum(zs_c$dm) / sum(zs_c$n) / 10))
csz <- clock("cellsize", terra::cellSize(dem, unit = "m"))
za <- terra::zonal(csz, zc, fun = "sum", na.rm = TRUE)
names(za) <- c("zid", "a")
ga <- terra::expanse(cocv, unit = "m", transform = TRUE)[za$zid]
arel <- abs(za$a - ga) / ga
rec("pixel_area_vs_geod_median_rel", sprintf("%.3e", median(arel)))
rec("pixel_area_vs_geod_max_rel", sprintf("%.3e", max(arel)))
write.csv(data.frame(GEOID = cot$GEOID[zs_t$zid], cells = zs_t$n, dm_sum = zs_t$dm),
          "/tmp/out/co-tract-zonal.csv", row.names = FALSE)
write.csv(data.frame(GEOID = tr$GEOID, census_m2 = census, terra_m2 = sprintf("%.6f", geod)),
          "/tmp/out/tract-areas.csv", row.names = FALSE)

# =============================================================================================
# R's long-double accumulators (patterns/r-long-double-on-arm64.md): the workload above sums in
# C++ (terra) or over <= 85k elements. This is the base-R reduction a user might reach for instead.
# =============================================================================================
x <- clock("values_to_r", terra::values(di, mat = FALSE))
rec("values_n", length(x))
s_r <- clock("base_sum", sum(x, na.rm = TRUE))
s_t <- clock("terra_global_sum", terra::global(di, "sum", na.rm = TRUE)[1, 1])
rec("base_sum_dm", sprintf("%.0f", s_r)); rec("terra_sum_dm", sprintf("%.0f", s_t))
ok(s_r == s_t, "base R and terra disagree on an integer sum")
mr <- clock("base_mean", mean(x, na.rm = TRUE))
rec("base_mean_dm", sprintf("%.6f", mr))
# control: the same sum over non-integer values (the vector above is whole decimetres)
y <- x * 1.000001
invisible(clock("base_sum_nonint", sum(y, na.rm = TRUE)))
invisible(clock("base_mean_nonint", mean(y, na.rm = TRUE)))
rm(x, y); invisible(gc())

dump()
if (length(fails)) { cat(sprintf("FAILED %d identities\n", length(fails))); quit(status = 1) }
cat("R-SPATIAL REAL OK\n")
