# r-sf and r-terra against two published references, on bytes two other recipes already staged.
#
# Neither leg is a tool-vs-tool comparison, deliberately. terra and rasterio both decode through
# GDAL, so their agreeing would say little. What both legs do is reproduce a number a THIRD PARTY
# published and shipped inside the data:
#
#   vector  TIGER's .dbf carries Census's own ALAND + AWATER in m2 beside each polygon
#   raster  the STAC item carries ESA's own scene-classification percentages for this scene
#
# and in each case a Python recipe in this catalog already reproduces it from the same object, so
# the R result is an independent stack against a common reference.
#
# The discrimination control is the sphere/ellipsoid distinction, measured rather than asserted:
# this env has r-s2 (spherical) and NO r-lwgeom, so the geodesic route cannot be assumed to exist.
# Three routes are computed and only the ellipsoidal one reproduces Census.
options(warn = 1)
out <- list()
rec <- function(k, v) { out[[k]] <<- v; cat(sprintf("%s\t%s\n", k, v)) }
dump <- function() {
  writeLines(c("observable\tvalue",
               vapply(names(out), function(k) sprintf("%s\t%s", k, out[[k]]), "")),
             "/tmp/score.tsv")
}
die <- function(msg) { dump(); cat(sprintf("FAIL: %s\n", msg)); quit(status = 1) }

suppressPackageStartupMessages({library(sf); library(terra); library(jsonlite)})
# terra draws a progress bar to stdout, which interleaves into the task log and can mangle
# the line next to it -- a diagnostic has to stay readable to be worth staging out.
terra::terraOptions(progress = 0)
rec("r_version", paste0(R.version$major, ".", R.version$minor))
rec("sf", as.character(packageVersion("sf")))
rec("terra", as.character(packageVersion("terra")))
rec("s2", as.character(packageVersion("s2")))
sv <- sf::sf_extSoftVersion()
rec("geos", sv[["GEOS"]]); rec("gdal", sv[["GDAL"]]); rec("proj", sv[["PROJ"]])

# ---------------------------------------------------------------------------------------------
# VECTOR: r-sf reads it, r-terra measures it, Census published the answer
# ---------------------------------------------------------------------------------------------
v <- sf::st_read("/vsizip//tmp/tiger.zip", quiet = TRUE)
rec("counties", nrow(v))
if (nrow(v) != 3235L) die(sprintf("expected 3235 TIGER counties, got %d", nrow(v)))
rec("crs_epsg", sf::st_crs(v)$epsg)
if (!identical(sf::st_crs(v)$epsg, 4269L)) die("TIGER should be EPSG:4269")
if (!all(c("ALAND", "AWATER") %in% names(v))) die("no ALAND/AWATER -- the reference is missing")
census <- as.numeric(v$ALAND) + as.numeric(v$AWATER)
rec("census_total_m2", sprintf("%.0f", sum(census)))

# sf -> terra geometry handoff. An exact invariance: if the two packages disagree about the
# geometry, every number below is about a different polygon set than the one sf validated.
tv <- terra::vect(v)
rec("terra_geometries", nrow(tv))
if (nrow(tv) != nrow(v)) die("sf -> terra lost geometries")

geod   <- terra::expanse(tv, unit = "m", transform = TRUE)   # ellipsoidal
sf::sf_use_s2(TRUE)
spher  <- as.numeric(sf::st_area(v))                          # spherical (s2)
planar <- as.numeric(sf::st_area(sf::st_transform(v, 5070)))  # equal-area projection

relerr <- function(a) { r <- abs(a - census) / census; r[is.finite(r) & census > 0] }
for (nm in c("geod", "spher", "planar")) {
  r <- relerr(get(nm))
  rec(sprintf("%s_max_rel", nm), sprintf("%.6e", max(r)))
  rec(sprintf("%s_median_rel", nm), sprintf("%.6e", median(r)))
}
g <- relerr(geod)
if (max(g) >= 1e-5) die(sprintf("ellipsoidal area misses Census by %.3e", max(g)))
rec("identity_census_area",
    sprintf("3235 county areas recovered from geometry to %.3e relative (median %.3e)",
            max(g), median(g)))

tot <- abs(sum(geod) - sum(census)) / sum(census)
rec("total_area_rel", sprintf("%.6e", tot))
if (tot >= 1e-6) die(sprintf("total area off by %.3e", tot))
rec("identity_total_area", sprintf("summed area matches Census total to %.3e", tot))

# The control: a spherical model is the obvious thing to reach for and it is wrong here. Stating
# how MUCH worse is what makes the agreement above a result rather than a coincidence.
ratio <- max(relerr(spher)) / max(g)
rec("spherical_vs_ellipsoidal_ratio", sprintf("%.3e", ratio))
if (ratio < 1e3) die(sprintf("spherical is only %.1fx worse -- the check cannot discriminate",
                             ratio))
rec("identity_model_discriminates",
    sprintf("a spherical model is %.0fx further from Census, so the match is the model not luck",
            ratio))

# ---------------------------------------------------------------------------------------------
# RASTER: r-terra reads the COG, ESA published the percentages
# ---------------------------------------------------------------------------------------------
r <- terra::rast("/tmp/SCL.tif")
rec("scl_ncell", terra::ncell(r))
if (terra::ncell(r) != 30140100) die(sprintf("expected 30140100 cells, got %d", terra::ncell(r)))
rec("scl_res", paste(terra::res(r), collapse = " x "))
if (!all(terra::res(r) == 20)) die("SCL should be a 20 m grid")
rec("scl_epsg", terra::crs(r, describe = TRUE)$code)
if (terra::crs(r, describe = TRUE)$code != "32611") die("SCL should be EPSG:32611")
e <- terra::ext(r)
rec("scl_origin", sprintf("%.1f %.1f", e$xmin, e$ymax))
if (abs(e$xmin - 199980) > 1e-6 || abs(e$ymax - 4100040) > 1e-6) die("SCL origin moved")

ft <- terra::freq(r)
counts <- setNames(as.numeric(ft$count), as.character(ft$value))
rec("scl_classes_present", paste(sort(as.integer(names(counts))), collapse = ","))
# A partition identity: the classification assigns every pixel exactly once, so the counts must
# sum to the cell count with nothing left over. Exact integers, no tolerance.
rec("scl_counted", sprintf("%.0f", sum(counts)))
if (sum(counts) != terra::ncell(r)) die("class counts do not partition the grid")
rec("identity_partition", "class counts sum exactly to the 30140100 cells")

# SCL code -> the property ESA publishes for it. The reference is read from the item, not typed.
esa_key <- c("0" = "s2:nodata_pixel_percentage", "1" = "s2:saturated_defective_pixel_percentage",
             "2" = "s2:dark_features_percentage", "3" = "s2:cloud_shadow_percentage",
             "4" = "s2:vegetation_percentage",    "5" = "s2:not_vegetated_percentage",
             "6" = "s2:water_percentage",         "7" = "s2:unclassified_percentage",
             "8" = "s2:medium_proba_clouds_percentage",
             "9" = "s2:high_proba_clouds_percentage",
             "10" = "s2:thin_cirrus_percentage",  "11" = "s2:snow_ice_percentage")
props <- jsonlite::fromJSON("/tmp/item.json")$properties
miss <- setdiff(esa_key, names(props))
if (length(miss)) die(sprintf("the STAC item lacks %s", paste(miss, collapse = ", ")))
worst <- 0; nchecked <- 0
cat("  class  ESA published   terra computed      diff (pp)\n")
for (code in names(esa_key)) {
  pub <- as.numeric(props[[esa_key[[code]]]])
  got <- 100 * (if (code %in% names(counts)) counts[[code]] else 0) / terra::ncell(r)
  d <- abs(got - pub)
  worst <- max(worst, d); nchecked <- nchecked + 1
  cat(sprintf("  %5s  %13.6f  %15.6f  %+.3e\n", code, pub, got, got - pub))
}
rec("esa_classes_checked", nchecked)
rec("esa_max_abs_diff_pp", sprintf("%.3e", worst))
# ESA prints 6 decimals, so a half-ulp is 5e-7 pp; 2e-6 is that bound with room for two classes
# rounding the same way. The bound comes from their precision, not from where this run landed.
if (worst >= 2e-6) die(sprintf("ESA percentages differ by %.3e pp", worst))
rec("identity_esa_percentages",
    sprintf("all %d of ESA's published class percentages reproduced to %.2e pp",
            nchecked, worst))

# Conservation under aggregation: summing a 2x2 block cannot create or destroy pixels, so the
# vegetation count must survive a change of grid exactly. Integer identity, no tolerance.
veg <- r == 4
n_veg <- as.numeric(terra::global(veg, "sum", na.rm = TRUE)[1, 1])
agg <- terra::aggregate(veg, fact = 2, fun = "sum", na.rm = TRUE)
n_agg <- as.numeric(terra::global(agg, "sum", na.rm = TRUE)[1, 1])
rec("vegetation_px", sprintf("%.0f", n_veg))
rec("vegetation_px_after_2x_aggregate", sprintf("%.0f", n_agg))
rec("aggregate_grid", paste(dim(agg)[1:2], collapse = " x "))
if (n_veg != n_agg) die(sprintf("aggregation changed the count: %.0f -> %.0f", n_veg, n_agg))
rec("identity_aggregate_conserves",
    sprintf("%.0f vegetation pixels survive a 2x aggregate exactly", n_veg))

rec("peak_rss_mib", sprintf("%.1f", sum(gc()[, "max used"] * c(8, 56)) / 1048576))
dump()
cat("R-SPATIAL OK\n")
