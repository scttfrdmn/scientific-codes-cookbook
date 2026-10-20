#!/usr/bin/env python3
"""Describe the GWTC-2.1 release file: groups, config, versions, sample counts. Read-only.
    inspect_release.py <release.h5>"""
import sys

import h5py
import numpy as np

f = h5py.File(sys.argv[1], "r")


def show(name, obj):
    if isinstance(obj, h5py.Dataset):
        kind = obj.dtype if obj.dtype.names is None else "fields=%d" % len(obj.dtype.names)
        print("D", name, obj.shape, kind)
    else:
        print("G", name)


f.visititems(show)


def dump(n, o):
    if isinstance(o, h5py.Dataset):
        v = o[()]
        try:
            v = [x.decode() if isinstance(x, bytes) else x for x in np.atleast_1d(v)]
        except Exception:
            pass
        print("   %s = %s" % (n, str(v)[:3000]))


for run in f:
    if not isinstance(f[run], h5py.Group) or "posterior_samples" not in f[run]:
        continue
    g = f[run]
    ps = g["posterior_samples"]
    print("\n==== %s: %d samples, fields:" % (run, ps.shape[0]), ", ".join(ps.dtype.names))
    for k in ("chirp_mass", "mass_ratio", "chi_eff", "luminosity_distance", "theta_jn",
              "log_likelihood", "network_matched_filter_snr", "H1_matched_filter_snr"):
        if k in ps.dtype.names:
            x = ps[k]
            if np.iscomplexobj(x):
                x = np.abs(x)
            print("  %s q05/50/95 = %s" % (k, np.percentile(x, [5, 50, 95])))
    for sec in ("config_file", "meta_data", "priors", "approximant", "version", "psds",
                "calibration_envelope"):
        if sec in g:
            print("---- %s/%s" % (run, sec))
            if sec in ("psds", "calibration_envelope"):
                g[sec].visititems(lambda n, o: print("   ", n, getattr(o, "shape", ""),
                                                     o[()][:2] if isinstance(o, h5py.Dataset) else ""))
            else:
                g[sec].visititems(dump)
print("INSPECT OK")
