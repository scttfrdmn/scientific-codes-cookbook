#!/usr/bin/env python3
"""The one success flag that differs across chips: pixel 10084, row 257, H-gamma.

Rebuilds that spectrum exactly as real.py does and runs DESI's emlines_gaussfit on it, wrapping
curve_fit to print what the flag is decided from (popt, pcov and its diagonal), plus the
OpenBLAS kernel this host selected.
    python3 probe_flip.py <in_dir> <sfd_dir>
"""
import ctypes
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.environ.get("DESI_SRC", "/tmp/desi"))
from astropy.io import fits                                   # noqa: E402
import desispec_iron_emlinefit as E                            # noqa: E402
from desiutil_dust import ext_odonnell, ebv as dust_ebv       # noqa: E402

IN, SFD = sys.argv[1], sys.argv[2]
PIX, ROW, LINE = "10084", 257, "HGAMMA"
for p in sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*"))[:1]:
    f = ctypes.CDLL(p).openblas_get_corename
    f.restype = ctypes.c_char_p
    print("openblas corename", f().decode(), flush=True)
em = fits.getdata(os.path.join(IN, "emline-main-dark-%s.fits" % PIX), "EMLINEFIT")
with fits.open(os.path.join(IN, "coadd-main-dark-%s.fits" % PIX), memmap=False) as h:
    fmap = np.array(h["FIBERMAP"].data)
    i = int(np.where(fmap["TARGETID"] == em["TARGETID"][ROW])[0][0])
    ebv = float(np.asarray(dust_ebv(np.asarray(fmap["TARGET_RA"][i:i + 1], "f8"), np.asarray(fmap["TARGET_DEC"][i:i + 1], "f8"), mapdir=SFD))[0])
    waves, fl, iv = np.zeros(0), np.zeros(0), np.zeros(0)
    for b in ("B", "R", "Z"):
        tw = h[b + "_WAVELENGTH"].data.astype("f8")
        tf, ti = np.array(h[b + "_FLUX"].data[i]), np.array(h[b + "_IVAR"].data[i])
        tr = 10 ** (-0.4 * ebv * 3.1 * ext_odonnell(tw, Rv=3.1))
        tf /= tr
        ti *= tr ** 2
        waves, fl, iv = np.append(waves, tw), np.append(fl, tf), np.append(iv, ti)
real_cf = E.curve_fit


def spy(*a, **k):
    popt, pcov = real_cf(*a, **k)
    d = np.diag(pcov)
    print("popt", repr(popt), "\npcov", repr(pcov), "\ndiag.sum() > 0:", d.sum() > 0, " diag[1] > 0:", d[1] > 0, flush=True)
    return popt, pcov


E.curve_fit = spy
print("TARGETID", em["TARGETID"][ROW], "z", em["Z"][ROW], "catalogue NDOF", em[LINE + "_NDOF"][ROW], "FLUX", em[LINE + "_FLUX"][ROW])
d, ok = E.emlines_gaussfit(LINE, float(em["Z"][ROW]), waves, fl, iv)
print("this host: succeed", ok, "NDOF", d["NDOF"], "FLUX", d["FLUX"], "SIGMA", d["SIGMA"])
print("PROBE DONE")
