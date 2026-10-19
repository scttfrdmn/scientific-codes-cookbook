#!/usr/bin/env python3
"""Where do the chips differ? For the named outputs: yt's per-particle mass against the reader's,
grid by grid (with the bits of every dds and its product); yt's per-grid gas mass the same way;
and the 800x800 projection images saved, so the chips' images can be differenced.
    python3 -u diag.py <Enzo_64 dir> <out dir> <output names...>"""
import os
import sys

import numpy as np

ROOT, OUT, NAMES = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = [sys.argv[0], ROOT, OUT, "1"]      # real.py reads its argv at import
import real  # noqa: E402  (the same Independent reader the run uses)
os.makedirs(OUT, exist_ok=True)


def main():
    import glob
    import yt
    yt.funcs.mylog.setLevel(40)
    # every output's unit conversions, to the bit: yt derives them from the redshift and the
    # cosmology, so they are libm results
    for hier in sorted(glob.glob(os.path.join(ROOT, "[DR]D*", "*.hierarchy"))):
        ds = yt.load(hier[:-len(".hierarchy")])
        reg = ds.unit_registry
        print("units %s z=%s %s" % (os.path.basename(os.path.dirname(hier)), float.hex(float(ds.current_redshift)),
              " ".join("%s=%s" % (u, float.hex(float(reg.lut[u][0]))) for u in
                       ("code_length", "code_mass", "code_time", "code_density", "code_velocity")
                       if u in reg.lut)), flush=True)
    for name in NAMES:
        pf = glob.glob(os.path.join(ROOT, name, "*.hierarchy"))[0][:-len(".hierarchy")]
        ind = real.Independent(pf)
        ds = yt.load(pf)
        byid = {g["id"]: g for g in ind.grids}
        npm = ndds = ngas = 0
        for yg in ds.index.grids:
            g = byid[yg.id]
            dds_y = np.asarray(yg.dds.to("code_length").d, dtype=np.float64)
            prod_y = dds_y.prod(dtype="f8")
            prod_i = float(np.prod(g["dx"]))
            if not np.array_equal(dds_y, g["dx"]) or prod_y != prod_i:
                ndds += 1
                if ndds <= 10:
                    print("%s grid %d L%d dds yt %s prod %s | reader %s prod %s" % (
                        name, yg.id, g["level"], [float.hex(float(x)) for x in dds_y], float.hex(float(prod_y)),
                        [float.hex(float(x)) for x in g["dx"]], float.hex(prod_i)), flush=True)
            if g.get("NumberOfParticles", 0):
                my = np.asarray(yg["all", "particle_mass"].to("code_mass").d)
                iy = np.asarray(yg["all", "particle_index"].d).astype(np.int64)
                mi = g["pmass_file"] * prod_i
                oy, oi = np.argsort(iy), np.argsort(g["pindex"].astype(np.int64))
                if not np.array_equal(iy[oy], g["pindex"].astype(np.int64)[oi]):
                    print("%s grid %d: particle index sets differ" % (name, yg.id), flush=True)
                d = my[oy] != mi[oi]
                if d.any():
                    npm += int(d.sum())
                    k = np.nonzero(d)[0][0]
                    print("%s grid %d L%d: %d particle masses differ, e.g. yt %s reader %s file %s" % (
                        name, yg.id, g["level"], int(d.sum()), float.hex(float(my[oy][k])),
                        float.hex(float(mi[oi][k])), float.hex(float(g["pmass_file"][oi][k]))), flush=True)
            cm = np.asarray(yg["gas", "cell_mass"].to("code_mass").d)
            ci = g["rho"].astype(np.float64) * prod_i
            if not np.array_equal(cm, ci):
                ngas += 1
                if ngas <= 10:
                    k = np.unravel_index(np.argmax(cm != ci), cm.shape)
                    print("%s grid %d L%d: cell masses differ in %d cells, e.g. yt %s reader %s" % (
                        name, yg.id, g["level"], int((cm != ci).sum()), float.hex(float(cm[k])),
                        float.hex(float(ci[k]))), flush=True)
        print("%s: grids %d, dds differing %d, particle masses differing %d, grids with cell masses "
              "differing %d" % (name, len(ds.index.grids), ndds, npm, ngas), flush=True)
        p = ds.proj(("gas", "density"), "z")
        pt = ds.proj(("gas", "temperature"), "z", weight_field=("gas", "mass"))
        np.savez_compressed(os.path.join(OUT, "%s-images.npz" % name),
                            density=np.asarray(p.to_frb((1.0, "unitary"), 800)[("gas", "density")].d),
                            temperature=np.asarray(pt.to_frb((1.0, "unitary"), 800)[("gas", "temperature")].d))


if __name__ == "__main__":
    main()
