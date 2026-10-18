"""astropy on a real survey field: Gaia DR3 x Pan-STARRS1 over one whole HEALPix order-4 pixel.

Every staged byte is checked against pins.sha256 by the task before this runs. Each claim is a
published reference, an identity or a cross-code agreement -- no band on an observed value; every
bound below is derived from the mechanism and says why in its note.
Writes real.log (stdout), real-results.tsv and matches.fits; prints ASTROPY REAL OK last.
"""
import glob, hashlib, resource, sys, time, traceback, warnings
import numpy as np
import astropy, astropy.units as u
from astropy.table import Table, vstack, join
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.time import Time
from astropy.coordinates import (SkyCoord, ICRS, FK4, FK5, Galactic, Supergalactic, BarycentricMeanEcliptic,
                                 BaseCoordinateFrame, SphericalRepresentation, RepresentationMapping,
                                 StaticMatrixTransform, frame_transform_graph, search_around_sky,
                                 match_coordinates_sky)
from astropy.coordinates.matrix_utilities import rotation_matrix
from astropy.utils import iers

warnings.simplefilter("ignore", FITSFixedWarning)
warnings.filterwarnings("ignore", module="erfa")
iers.conf.auto_download = False            # offline: nothing here depends on Earth orientation
IN = sys.argv[1] if len(sys.argv) > 1 else "/tmp/in"
UAS_PER_RAD = 206264.80624709636e6
C_KMS = 299792.458
AU_KM_YR = 4.740470463533348               # km/s per (mas/yr / mas)
FAIL, ROWS = [], []


def rec(key, value, ok=None, note=""):
    """The one reporting primitive: str() every value so a describing line can never kill the run.
    ok is coerced with bool(): numpy comparisons return np.bool_, and `np.False_ is False` is False."""
    ok = None if ok is None else bool(ok)
    flag = "" if ok is None else ("ok" if ok else "FAIL")
    line = "\t".join(str(x) for x in (key, value, flag, note))
    print(line, flush=True)
    ROWS.append(line)
    if ok is False:
        FAIL.append(str(key))


class section:
    """Time a block; an exception inside it is recorded as a failure and the next block still runs."""
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0 = time.time()

    def __exit__(self, et, ev, tb):
        rec("time_" + self.name + "_s", "%.2f" % (time.time() - self.t0))
        if et is not None:
            traceback.print_exception(et, ev, tb)
            rec("exception_in_" + self.name, repr(ev), False)
        return True


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def read_parquet(f, cols):
    """pyarrow -> astropy Table, nulls as masks. astropy 8.0.1's own parquet reader raised
    KeyError('_healpix_29') on these HATS files (see astropy_parquet_reader), so read via pyarrow."""
    import pyarrow.parquet as pq
    at = pq.read_table(f, columns=cols)
    out = Table()
    for name in cols:
        a = at[name]
        v = a.to_numpy(zero_copy_only=False)
        out[name] = np.ma.MaskedArray(v, mask=a.is_null().to_numpy(zero_copy_only=False)) if a.null_count else v
    return out


def astropy_parquet_reader(f, cols):
    """Diagnostic only: does astropy's reader take this file, and what does the file's metadata carry."""
    import pyarrow.parquet as pq
    md = pq.read_schema(f).metadata or {}
    rec("parquet_metadata_keys", sorted(k.decode() for k in md))
    y = md.get(b"table_meta_yaml")
    if y is not None:
        txt = y.decode()
        rec("parquet_yaml_mentions_healpix_29", "_healpix_29" in txt, None, "yaml is %d bytes" % len(txt))
    try:
        Table.read(f, include_names=cols)
        rec("astropy_parquet_reader", "ok")
    except Exception as exc:
        rec("astropy_parquet_reader", "raises " + repr(exc), None, "reported; data read via pyarrow")


def col(t, name):
    """A column as float64 with nulls as NaN (parquet nulls arrive masked, with arbitrary data under the mask)."""
    return np.ma.filled(np.ma.asarray(t[name], dtype=float), np.nan)


def uvec(lon_deg, lat_deg):
    lo, la = np.radians(np.asarray(lon_deg, float)), np.radians(np.asarray(lat_deg, float))
    return np.vstack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def sep_uas(a, b):
    """Angle between two 3xN unit-vector arrays, in microarcsec (atan2 form: exact at tiny angles)."""
    cr = np.linalg.norm(np.cross(a.T, b.T), axis=1)
    return np.arctan2(cr, np.einsum("ij,ij->j", a, b)) * UAS_PER_RAD


def kabsch(a, b):
    """Best rotation R with R a ~= b, and the worst residual after it, in uas."""
    U, _, Vt = np.linalg.svd(a @ b.T)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, sep_uas(R @ a, b).max()


def rot_angle_uas(R):
    """Rotation angle from the antisymmetric part (sin) and the trace (cos). arccos of the trace alone
    has a floor of sqrt(eps) ~ 3 mas at small angles -- the first run's 6 mas 'disagreement' was that."""
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / 2
    return np.arctan2(np.linalg.norm(v), (np.trace(R) - 1) / 2) * UAS_PER_RAD


def nmax(x):
    """max that reports NaN loudly instead of letting it pass a one-sided bound (nan < tol is False,
    but nan > tol is also False -- every bound here is written `max < tol`, so NaN fails)."""
    x = np.asarray(x, float)
    return np.nan if x.size == 0 or not np.all(np.isfinite(x)) else float(x.max())


# Gaia DR3's own conventions, from the gaia_source data model: Galactic per Hipparcos (ESA 1997,
# Vol. 1 sec. 1.5.3) applied directly to ICRS, and the Chapront et al. (2002) ecliptic
# (eps = 84381.4110", ICRS origin offset phi = -55.42 mas). Built as astropy frames on the
# transform graph -- the same way a user adds any survey's convention.
class HipGalactic(BaseCoordinateFrame):
    default_representation = SphericalRepresentation
    frame_specific_representation_info = {
        SphericalRepresentation: [RepresentationMapping("lon", "l"), RepresentationMapping("lat", "b")]}


class GaiaEcliptic(BaseCoordinateFrame):
    default_representation = SphericalRepresentation


@frame_transform_graph.transform(StaticMatrixTransform, ICRS, HipGalactic)
def _icrs_hipgal():
    return rotation_matrix(180 - 122.93192, "z") @ rotation_matrix(90 - 27.12825, "y") @ rotation_matrix(192.85948, "z")


@frame_transform_graph.transform(StaticMatrixTransform, ICRS, GaiaEcliptic)
def _icrs_gaiaecl():
    return rotation_matrix(84381.4110 * u.arcsec, "x") @ rotation_matrix(-55.42 * u.mas, "z")


def frame_matrix(frame):
    """ICRS -> frame as astropy itself computes it: columns are the images of the ICRS basis vectors."""
    e = SkyCoord(ra=[0, 90, 0] * u.deg, dec=[0, 0, 90] * u.deg, frame="icrs")
    s = e.transform_to(frame).represent_as("unitspherical")
    return uvec(s.lon.deg, s.lat.deg)


def main():
    import healpy, scipy, erfa
    from scipy.spatial import cKDTree
    rec("versions", "astropy %s numpy %s scipy %s healpy %s erfa %s" % (
        ver(astropy), ver(np), ver(scipy), ver(healpy), ver(erfa)))

    # ---- load: the four Gaia DR3 and four PS1 otmo HATS partitions of order-4 pixel 808 ----------
    gcols = ["_healpix_29", "source_id", "ra", "dec", "l", "b", "ecl_lon", "ecl_lat", "parallax", "pmra",
             "pmdec", "radial_velocity", "ref_epoch", "phot_g_mean_mag"]
    pcols = ["_healpix_29", "objID", "raMean", "decMean", "cx", "cy", "cz", "epochMean", "nDetections",
             "rMeanPSFMag"]
    for kind, cols in (("gaia", gcols), ("ps1", pcols)):
        try:
            astropy_parquet_reader(sorted(glob.glob(IN + "/%s/Npix=*.parquet" % kind))[0], cols)
        except Exception as exc:
            rec("astropy_parquet_reader_diagnostic", repr(exc))
    t0 = time.time()
    parts, G, P = [], [], []
    for f in sorted(glob.glob(IN + "/gaia/Npix=*.parquet")):
        npix = int(f.rsplit("=", 1)[1].split(".")[0]); parts.append(npix)
        t = read_parquet(f, gcols); t["part"] = npix; G.append(t)
    for f in sorted(glob.glob(IN + "/ps1/Npix=*.parquet")):
        t = read_parquet(f, pcols); t["part"] = int(f.rsplit("=", 1)[1].split(".")[0]); P.append(t)
    G, P = vstack(G), vstack(P)
    rec("time_load_s", "%.2f" % (time.time() - t0))
    rec("gaia_partitions", parts, len(parts) == 4 and all(p >> 2 == 808 for p in parts), "order-5 children of order-4 pixel 808")
    rec("n_gaia", len(G)); rec("n_ps1_rows", len(P))
    rec("gaia_source_id_unique", len(np.unique(G["source_id"])) == len(G), len(np.unique(G["source_id"])) == len(G))
    rec("ps1_objID_unique", len(np.unique(P["objID"])) == len(P), len(np.unique(P["objID"])) == len(P))

    # HATS partitioning, two independent implementations: every row's _healpix_29 (hats-import)
    # must sit in the partition file it was written to, and healpy must reproduce it -- where it
    # does not, the two pixels must be neighbours (a float rounding at a 0.39 mas pixel edge).
    with section("healpix"):
        nside = 2 ** 29
        for name, t, rac, dec in (("gaia", G, "ra", "dec"), ("ps1", P, "raMean", "decMean")):
            h29 = np.asarray(t["_healpix_29"], np.int64)
            inpart = (h29 >> 48) == np.asarray(t["part"])
            rec(name + "_rows_in_their_partition", int(inpart.sum()), inpart.all(), "_healpix_29 >> 2*(29-5) == Npix")
            lo, la = col(t, rac), col(t, dec)
            okp = np.abs(la) <= 90                    # PS1 writes -999 for no mean position
            rec(name + "_rows_with_position", int(okp.sum()))
            hp = healpy.ang2pix(nside, lo[okp], la[okp], nest=True, lonlat=True)
            bad = np.nonzero(hp != h29[okp])[0]
            nb = healpy.get_all_neighbours(nside, hp[bad], nest=True) if bad.size else np.zeros((8, 0), np.int64)
            adj = bool(np.all(np.any(nb == h29[okp][bad], axis=0))) if bad.size else True
            rec(name + "_healpy_order29_disagree", bad.size, adj, "every disagreement is an adjacent pixel")

    ra, de = np.asarray(G["ra"], float), np.asarray(G["dec"], float)
    cg = SkyCoord(ra=ra * u.deg, dec=de * u.deg, frame="icrs")
    vg = uvec(ra, de)

    # ---- the survey's own catalogued coordinates as the published reference -------------------
    with section("catalogue_reference"):
        cat_gal = uvec(G["l"], G["b"])
        cat_ecl = uvec(G["ecl_lon"], G["ecl_lat"])
        h = cg.transform_to(HipGalactic())
        s = nmax(sep_uas(uvec(h.l.deg, h.b.deg), cat_gal))
        rec("gaia_lb_vs_astropy_hipparcos_frame_max_uas", "%.3e" % s, s < 1.0, "Gaia's own convention, < 1 uas")
        e = cg.transform_to(GaiaEcliptic())
        s = nmax(sep_uas(uvec(e.lon.deg, e.lat.deg), cat_ecl))
        rec("gaia_ecl_vs_astropy_chapront_frame_max_uas", "%.3e" % s, s < 1.0, "Gaia's own convention, < 1 uas")

    # astropy's built-in frames: the disagreement must be exactly the documented convention
    # difference -- one rigid rotation, equal to the rotation between the two frame definitions.
    with section("builtin_frames"):
        for name, frame, conv, cat in (("galactic", Galactic(), HipGalactic(), cat_gal),
                                       ("ecliptic", BarycentricMeanEcliptic(equinox="J2000"), GaiaEcliptic(), cat_ecl)):
            q = cg.transform_to(frame).represent_as("unitspherical")
            a = uvec(q.lon.deg, q.lat.deg)
            off = sep_uas(a, cat)
            R, resid = kabsch(a, cat)
            pred = frame_matrix(conv) @ frame_matrix(frame).T
            rec("builtin_%s_vs_catalogue_offset_mas" % name, "median %.4f max %.4f" % (np.median(off) / 1e3, nmax(off) / 1e3))
            rec("builtin_%s_offset_is_rigid_rotation_resid_uas" % name, "%.3e" % resid, resid < 1.0)
            rec("builtin_%s_rotation_fitted_vs_frame_definitions" % name, "fitted %.3f mas, predicted %.3f mas, differ %.3f uas" % (
                rot_angle_uas(R) / 1e3, rot_angle_uas(pred) / 1e3, rot_angle_uas(R @ pred.T)), None,
                "a fit over a 3.6 deg field pins the line-of-sight axis only to ~resid/0.03: reported")
            # the claim itself, no fit: the frame-definition rotation carries every star onto the catalogue
            s = nmax(sep_uas(pred @ a, cat))
            rec("builtin_%s_times_definition_difference_vs_catalogue_max_uas" % name, "%.3e" % s, s < 1.0,
                "built-in frame + the rotation between the two definitions == Gaia's catalogued value")

    # ---- round trips through the transform graph, every source ---------------------------------
    # All are rotations (exact inverses) except FK4, whose E-terms astropy removes by a fixed-point
    # iteration; the E-terms are ~0.34" (1.6e-6 rad), so each step contracts the error by ~1e-6 and
    # the iteration lands on float precision. So every round trip is < 1 uas, FK4 included.
    with section("roundtrips"):
        for name, frame in (("galactic", Galactic()), ("fk5_j2000", FK5(equinox="J2000")),
                            ("supergalactic", Supergalactic()),
                            ("barycentric_mean_ecliptic", BarycentricMeanEcliptic(equinox="J2000")),
                            ("fk4_b1950", FK4(equinox="B1950", obstime="B1950"))):
            b = cg.transform_to(frame).transform_to("icrs")
            s = nmax(sep_uas(uvec(b.ra.deg, b.dec.deg), vg))
            rec("roundtrip_icrs_%s_max_uas" % name, "%.3e" % s, s < 1.0)

    plx, pmra, pmde = (col(G, c) for c in ("parallax", "pmra", "pmdec"))
    rv = np.nan_to_num(col(G, "radial_velocity"))
    ep = Time(2016.0, format="jyear", scale="tcb")

    # ---- space motion: 2016.0 -> 2000.0 -> 2016.0 for every source with pm and parallax > 0 -----
    # ERFA's starpm is linear motion in 3D plus light time; the inverse leg solves the same equations
    # backwards, so the round trip is exact to float. ERFA alters the motion only outside physics --
    # pmsafe floors parallax at 5e-4 mas and starpv caps speed at 0.5c -- so assert on stars with a
    # transverse speed below 0.01c (which keeps parallax >= 0.016 mas at |pm| > 10) and report the
    # rest, which are parallax noise, not stars moving at relativistic speed.
    with section("space_motion"):
        e16 = bool(np.all(np.asarray(G["ref_epoch"]) == 2016.0))
        rec("ref_epoch_all_2016.0", e16, e16)
        sm = np.isfinite(pmra) & np.isfinite(pmde) & (plx > 0)
        pm_tot = np.hypot(pmra[sm], pmde[sm])
        vt = AU_KM_YR * pm_tot / plx[sm]
        phys = vt < 0.01 * C_KMS
        c6 = SkyCoord(ra=ra[sm] * u.deg, dec=de[sm] * u.deg, distance=(1 / plx[sm]) * u.kpc,
                      pm_ra_cosdec=pmra[sm] * u.mas / u.yr, pm_dec=pmde[sm] * u.mas / u.yr,
                      radial_velocity=rv[sm] * u.km / u.s, obstime=ep, frame="icrs")
        there = c6.apply_space_motion(new_obstime=Time(2000.0, format="jyear", scale="tcb"))
        back = there.apply_space_motion(new_obstime=ep)
        s = sep_uas(uvec(back.ra.deg, back.dec.deg), vg[:, sm])
        rec("n_space_motion", int(sm.sum())); rec("n_space_motion_vt_ge_0.01c", int((~phys).sum()))
        rec("space_motion_roundtrip_2016_2000_2016_max_uas", "%.3e" % nmax(s[phys]), nmax(s[phys]) < 1.0,
            "starpm is its own inverse; vt < 0.01c")
        rec("space_motion_roundtrip_vt_ge_0.01c_max_uas", "%.3e" % nmax(s[~phys]) if (~phys).any() else "none", None,
            "ERFA's parallax floor / speed cap act here: reported")
        # 16 years of motion against 16 x the catalogued pm. Straight-line motion seen from the
        # barycentre falls short of the tangent-plane length by O(theta^2) (theta = 16|pm| in rad);
        # light time contributes O(theta*beta_t) and O(beta_r); radial motion changes distance by
        # vr*dt/d; relativistic aberration of the velocity is O(beta^2). Bound each term by 1x-2x its
        # size per star. And one term the first run found: Gaia's pm is per year of TCB (its
        # reference frame's time scale), ERFA's starpm propagates in TDB, and astropy converts the
        # epochs to TDB, so 16 TCB years become 16(1 - L_B) TDB years -- a fixed shortfall of
        # L_B = 1.550519768e-8 (IAU 2006 B3). Plus float: positions carry ~2e-4 uas per ulp, so
        # allow 1 nas absolute.
        # rel <= theta^2 + beta^2 + theta*beta_t + 2|beta_r| + 2|vr dt/d| + L_B + 1 nas / (16|pm|).
        L_B = 1.550519768e-8
        moved = sep_uas(uvec(there.ra.deg, there.dec.deg), vg[:, sm]) / 1e3
        big = (pm_tot > 10) & phys
        theta = 16 * pm_tot[big] / (UAS_PER_RAD / 1e3)
        bt, br = vt[big] / C_KMS, rv[sm][big] / C_KMS
        dist_km = 1 / plx[sm][big] * 3.0856775814913673e16
        bound = (theta ** 2 + bt ** 2 + br ** 2 + theta * bt + 2 * np.abs(br) + 2 * np.abs(rv[sm][big] * 16 * 3.15576e7 / dist_km)
                 + L_B + 1e-6 / (16 * pm_tot[big]))
        short = (16 * pm_tot[big] - moved[big]) / (16 * pm_tot[big])          # signed shortfall
        rel = np.abs(short)
        rec("n_pm_gt_10_physical", int(big.sum()))
        rec("space_motion_16yr_vs_16pm_maxrel", "%.3e (bound at that star %.3e)" % (nmax(rel), bound[np.argmax(rel)] if rel.size else np.nan))
        rec("space_motion_16yr_vs_16pm_within_mechanism_bound", int(np.sum(rel <= bound)), bool(rel.size and np.all(rel <= bound)),
            "theta^2 + beta^2 + theta*beta_t + 2|beta_r| + 2|vr dt/d| + L_B + 1 nas/(16|pm|), per star")
        rv0 = br == 0
        rec("n_pm_gt_10_physical_no_rv", int(rv0.sum()))
        rec("space_motion_16yr_vs_16pm_maxrel_rv0", "%.3e" % nmax(rel[rv0]), None, "stars with no Gaia RV: geometry alone")
        # the positive confirmation: with no radial velocity the shortfall IS L_B (the other rv=0
        # terms, theta^2/3 and theta*beta_t/2, are ~1e-12 and ~1e-10 at the median star).
        med = float(np.median(short[rv0])) if rv0.any() else np.nan
        rec("space_motion_rv0_median_shortfall_minus_L_B", "%.3e (median %.6e, L_B %.6e)" % (med - L_B, med, L_B),
            abs(med - L_B) < 1e-9, "TCB pm propagated in TDB: the shortfall is the TCB-TDB rate")

    # ---- cross-match Gaia DR3 x PS1 at 1 arcsec ---------------------------------------------------
    with section("crossmatch"):
        pv = (np.abs(col(P, "decMean")) <= 90) & (col(P, "nDetections") > 0)
        Pv = P[pv]
        rec("n_ps1_valid_mean_position", len(Pv))
        pra, pde = np.asarray(Pv["raMean"], float), np.asarray(Pv["decMean"], float)
        cp = SkyCoord(ra=pra * u.deg, dec=pde * u.deg, frame="icrs")
        vp = cp.cartesian.xyz.value
        d = nmax(np.abs(vp - np.vstack([col(Pv, c) for c in ("cx", "cy", "cz")])))
        rec("ps1_cxcycz_vs_astropy_cartesian_maxabs", "%.3e" % d, d < 1e-14, "PS1's catalogued unit vector, < 1e-14 (2 uas)")

        r = 1.0 * u.arcsec
        r_uas = 1e6
        t0 = time.time()
        ig, ip, sgp, _ = search_around_sky(cg, cp, r)
        rec("time_search_around_gaia_ps1_s", "%.2f" % (time.time() - t0))
        t0 = time.time()
        jp, jg, spg, _ = search_around_sky(cp, cg, r)
        rec("time_search_around_ps1_gaia_s", "%.2f" % (time.time() - t0))
        NP = np.int64(len(Pv))
        k1 = np.sort(ig.astype(np.int64) * NP + ip); k2 = np.sort(jg.astype(np.int64) * NP + jp)
        rec("n_pairs_1arcsec", len(k1))
        rec("pairs_symmetric_GxP_eq_PxG", np.array_equal(k1, k2), np.array_equal(k1, k2))
        rec("pairs_sha256", hashlib.sha256(k1.tobytes()).hexdigest()[:16])

        def boundary_only(keys, what):
            """Two codes may disagree on a pair only where its separation is 1" to within float."""
            gi_, pj_ = keys // NP, keys % NP
            e = np.abs(sep_uas(vg[:, gi_], vp[:, pj_]) - r_uas)
            rec(what + "_differ", len(keys), len(keys) == 0 or nmax(e) < 1e-3,
                "every difference within 1 nas of the 1 arcsec boundary (max %s uas)" % ("%.3e" % nmax(e) if len(keys) else "-"))

        # independent code: scipy cKDTree on unit vectors, chord radius 2 sin(r/2)
        t0 = time.time()
        tree = cKDTree(vp.T)
        K = 16
        dist, idx = tree.query(vg.T, k=K, distance_upper_bound=2 * np.sin(r.to_value(u.rad) / 2), workers=-1)
        rec("time_ckdtree_s", "%.2f" % (time.time() - t0))
        full = int(np.sum(np.isfinite(dist[:, -1])))
        rec("ckdtree_k_saturated_rows", full, full == 0, "no Gaia source has >= %d PS1 neighbours, so k is not truncating" % K)
        m = np.isfinite(dist).ravel()
        k3 = np.sort(np.repeat(np.arange(len(G), dtype=np.int64), K)[m] * NP + idx.ravel()[m])
        boundary_only(np.setxor1d(k1, k3), "ckdtree_vs_astropy_pairs")

        t0 = time.time()
        nn_g, s_g, _ = match_coordinates_sky(cg, cp)
        nn_p, s_p, _ = match_coordinates_sky(cp, cg)
        rec("time_match_coordinates_s", "%.2f" % (time.time() - t0))
        within = s_g < r
        # who has a match: match_coordinates_sky (sep < r) vs the pair list (sep <= r)
        boundary_only(np.setxor1d(np.nonzero(within)[0], np.unique(ig)) * NP + nn_g[np.setxor1d(np.nonzero(within)[0], np.unique(ig))],
                      "has_match_gaia_matchcoords_vs_pairs")
        rec("n_gaia_with_match", int(within.sum())); rec("n_ps1_with_match", int((s_p < r).sum()))
        # "nothing in the pair list is closer than match_coordinates_sky's nearest": the claim, not
        # index equality, which a tie between two PS1 rows at the same separation could break.
        o = np.lexsort((sgp.to_value(u.rad), ig)); first = np.r_[True, ig[o][1:] != ig[o][:-1]]
        best_sep = np.full(len(G), np.inf); best_sep[ig[o][first]] = sgp[o][first].to_value(u.arcsec) * 1e6
        best_idx = np.full(len(G), -1); best_idx[ig[o][first]] = ip[o][first]
        dsep = nmax(np.abs(best_sep[within] - s_g[within].to_value(u.arcsec) * 1e6)) if within.any() else 0.0
        rec("nearest_from_pairs_vs_match_coordinates_sep_max_uas", "%.3e" % dsep, dsep < 1e-3, "same nearest separation to 1 nas")
        rec("nearest_from_pairs_ne_match_coordinates_index", int(np.sum(best_idx[within] != nn_g[within])), None, "ties: reported")
        mutual = within & (nn_p[nn_g] == np.arange(len(G))) & (s_p[nn_g] < r)
        rec("n_mutual_best_matches", int(mutual.sum()))
        rec("fraction_gaia_matched", "%.4f" % mutual.mean())

        # table join on the match, the way a user builds the merged catalogue
        t0 = time.time()
        gm = Table({"pkey": nn_g[mutual], "source_id": np.asarray(G["source_id"])[mutual],
                    "phot_g_mean_mag": col(G, "phot_g_mean_mag")[mutual], "sep_arcsec": s_g[mutual].to_value(u.arcsec)})
        pt = Table({"pkey": np.arange(len(Pv)), "objID": np.asarray(Pv["objID"]), "rMeanPSFMag": col(Pv, "rMeanPSFMag"),
                    "epochMean": col(Pv, "epochMean")})
        J = join(gm, pt, keys="pkey", join_type="inner")
        rec("time_join_s", "%.2f" % (time.time() - t0))
        rec("join_rows_eq_mutual", len(J), len(J) == int(mutual.sum()))
        rec("join_objID_unique", len(np.unique(J["objID"])) == len(J), len(np.unique(J["objID"])) == len(J))

        # proper motion: carrying each matched Gaia star to its PS1 mean epoch must close the gap
        gi = np.nonzero(mutual)[0]; pj = nn_g[mutual]
        ep_ps1 = col(Pv, "epochMean")[pj]
        ok = (plx[gi] > 0) & np.isfinite(pmra[gi]) & (np.hypot(pmra[gi], pmde[gi]) > 50) & (ep_ps1 > 0)
        gi, pj, ep_ps1 = gi[ok], pj[ok], ep_ps1[ok]
        cc = SkyCoord(ra=ra[gi] * u.deg, dec=de[gi] * u.deg, distance=(1 / plx[gi]) * u.kpc,
                      pm_ra_cosdec=pmra[gi] * u.mas / u.yr, pm_dec=pmde[gi] * u.mas / u.yr,
                      radial_velocity=rv[gi] * u.km / u.s, obstime=ep, frame="icrs")
        moved = cc.apply_space_motion(new_obstime=Time(ep_ps1, format="mjd", scale="tai"))
        before = sep_uas(vg[:, gi], vp[:, pj]) / 1e3
        after = sep_uas(uvec(moved.ra.deg, moved.dec.deg), vp[:, pj]) / 1e3
        rec("n_pm_gt_50", len(gi))
        rec("pm_propagation_median_sep_mas", "before %.2f after %.2f" % (np.median(before), np.median(after)),
            len(gi) > 0 and np.median(after) < 0.5 * np.median(before), "a sign or unit error would grow the gap, not halve it")

    # ---- WCS on real headers: every PS1 stack skycell of projection cell 1749, r band -----------
    with section("wcs"):
        worst_rt = worst_g = worst_crval = 0.0
        excess = []                                    # catalogue sep minus its float-storage bound, uas
        seps, ulp_bounds = [], []
        ndet = ngrid = ng_in = 0
        origin_votes = {0: 0, 1: 0}
        files = sorted(glob.glob(IN + "/cmf/*.cmf"))
        for i, f in enumerate(files):
            with fits.open(f, memmap=True) as hd:
                wh = next((h.header for h in hd if "CTYPE1" in h.header), None)
                if wh is None:
                    rec("wcs_header_found", f.rsplit("/", 1)[1], False); continue
                w = WCS(wh)
                nx = int(wh.get("IMNAXIS1", wh.get("ZNAXIS1", wh.get("NAXIS1"))))
                ny = int(wh.get("IMNAXIS2", wh.get("ZNAXIS2", wh.get("NAXIS2"))))
                t = hd["SkyChip.psf"].data
                if i == 0:
                    rec("cmf_layout", "extensions %s; wcs in %s; %dx%d px; X_PSF %s RA_PSF %s" % (
                        [h.name for h in hd], next(h.name for h in hd if "CTYPE1" in h.header), nx, ny,
                        t["X_PSF"].dtype, t["RA_PSF"].dtype))
                xs, ys, rs, ds = (np.asarray(t[c]) for c in ("X_PSF", "Y_PSF", "RA_PSF", "DEC_PSF"))
            x, y, rr, dd = (v.astype(float) for v in (xs, ys, rs, ds))
            k = np.isfinite(x) & np.isfinite(y) & np.isfinite(rr) & np.isfinite(dd)
            # Bound = what the catalogue's own storage dtype can carry: half an ulp of X/Y times the
            # pixel scale, plus half an ulp of RA (times cos dec) and Dec, plus 0.36 uas (1e-10 deg).
            scale_uas = np.sqrt(abs(np.linalg.det(w.pixel_scale_matrix))) * 3.6e9
            tol = (0.5 * np.hypot(np.spacing(xs[k]), np.spacing(ys[k])).astype(float) * scale_uas
                   + 0.5 * np.hypot(np.spacing(rs[k]).astype(float) * np.cos(np.radians(dd[k])), np.spacing(ds[k]).astype(float)) * 3.6e9
                   + 0.36)
            best = None
            for org in (0, 1):
                a, b = w.all_pix2world(x[k], y[k], org)
                s = sep_uas(uvec(a, b), uvec(rr[k], dd[k]))
                if best is None or np.median(s) < np.median(best[1]):
                    best = (org, s)
            origin_votes[best[0]] += 1
            excess.append(nmax(best[1] - tol) if k.any() else -np.inf); ndet += int(k.sum())
            seps.append(best[1])
            # IPP's positions are float32 pixels; one float32 ulp at the image's largest coordinate,
            # times the pixel scale, is the precision of any pixel quantity IPP computed
            ulp_bounds.append(float(np.spacing(np.float32(max(nx, ny)))) * scale_uas)
            cr = w.all_pix2world([w.wcs.crpix[0]], [w.wcs.crpix[1]], 1)
            worst_crval = max(worst_crval, abs(cr[0][0] - w.wcs.crval[0]), abs(cr[1][0] - w.wcs.crval[1]))
            # every pixel of the stack, pix -> world -> pix (the reproject workload), in row blocks
            for y0 in range(1, ny + 1, 512):
                yy, xx = np.mgrid[y0:min(y0 + 512, ny + 1), 1:nx + 1]
                xx, yy = xx.ravel().astype(float), yy.ravel().astype(float)
                wa, wb = w.wcs_pix2world(xx, yy, 1)
                px, py = w.wcs_world2pix(wa, wb, 1)
                worst_rt = max(worst_rt, nmax(np.abs(px - xx)), nmax(np.abs(py - yy))); ngrid += xx.size
            # Gaia sources on this skycell: world -> pix -> world
            gx, gy = w.wcs_world2pix(ra, de, 1)
            inside = (gx >= 1) & (gx <= nx) & (gy >= 1) & (gy <= ny)
            if inside.any():
                ba, bb = w.wcs_pix2world(gx[inside], gy[inside], 1)
                worst_g = max(worst_g, nmax(sep_uas(uvec(ba, bb), vg[:, inside]))); ng_in += int(inside.sum())
        rec("n_skycell_headers", len(files), len(files) == 100)
        rec("n_ipp_detections", ndet)
        rec("ipp_pixel_origin_votes", origin_votes, origin_votes[0] == 0 or origin_votes[1] == 0, "one convention for all 100 skycells")
        ex = max(excess) if excess else np.nan
        rec("wcs_vs_ipp_catalogue_radec_worst_excess_over_half_ulp_bound_uas", "%.3e" % ex, None,
            "run 2 asserted <= 0 and measured 11.1 uas: IPP's internal pixel arithmetic is not one rounding")
        S = np.concatenate(seps)
        rec("wcs_vs_ipp_catalogue_sep_uas_p50_p99_p9999_max", "%.2f %.2f %.2f %.2f" % tuple(np.quantile(S, [0.5, 0.99, 0.9999, 1.0])))
        ub = min(ulp_bounds)
        rec("wcs_vs_ipp_catalogue_sep_max_vs_float32_pixel_ulp_uas", "%.2f vs %.2f" % (nmax(S), ub), nmax(S) < ub,
            "IPP's RA_PSF/DEC_PSF are the header WCS at X_PSF/Y_PSF to one float32 ulp of the image's pixel range")
        rec("wcs_crpix_to_crval_maxabs_deg", "%.3e" % worst_crval, worst_crval < 1e-12)
        rec("n_pixels_roundtripped", ngrid)
        rec("wcs_pixel_grid_roundtrip_max_px", "%.3e" % worst_rt, worst_rt < 1e-6)
        rec("n_gaia_on_skycells", ng_in)
        rec("wcs_gaia_world_pix_world_max_uas", "%.3e" % worst_g, ng_in > 0 and worst_g < 1.0)

    # ---- the merged catalogue to FITS and back -------------------------------------------------
    with section("fits"):
        J.write("/tmp/matches.fits", overwrite=True)
        Rt = Table.read("/tmp/matches.fits")
        same = all(np.array_equal(np.asarray(J[c]), np.asarray(Rt[c]), equal_nan=J[c].dtype.kind == "f") for c in J.colnames)
        rec("fits_merged_catalogue_roundtrip_bit_identical", same, same)
    rec("peak_rss_gib", "%.2f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20))


if __name__ == "__main__":
    t_all = time.time()
    try:
        main()
    except Exception as exc:                      # report, then fail: never a silent partial
        traceback.print_exc()
        FAIL.append("exception: " + repr(exc))
    rec("time_total_s", "%.1f" % (time.time() - t_all))
    open("/tmp/real-results.tsv", "w").write("\n".join(ROWS) + "\n")
    print("FAILED: " + ", ".join(FAIL) if FAIL else "ASTROPY REAL OK", flush=True)
    sys.exit(1 if FAIL else 0)
