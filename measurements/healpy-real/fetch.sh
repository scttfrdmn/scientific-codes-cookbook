set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
mkdir -p /tmp/out && cd /tmp/out
# Planck PR3 (2018) products from the Planck Legacy Archive's IRSA mirror, fetched on AWS.
# The release tag (R3.00 / R3.01) is in every file name; the sha256 recorded here is the pin.
python3 -u - <<'PYEOF' || { echo "FAIL: fetch"; exit 1; }
import hashlib, urllib.request
B = "https://irsa.ipac.caltech.edu/data/Planck/release_3/"
F = {
 "COM_CMB_IQU-smica_2048_R3.00_full.fits": ("all-sky-maps/maps/component-maps/cmb/", 2013312960),
 "COM_Mask_CMB-common-Mask-Int_2048_R3.00.fits": ("ancillary-data/masks/", 201335040),
 "COM_Mask_CMB-common-Mask-Pol_2048_R3.00.fits": ("ancillary-data/masks/", 201335040),
 "COM_PowerSpect_CMB-TT-full_R3.01.txt": ("ancillary-data/cosmoparams/", 170547),
 "COM_PowerSpect_CMB-EE-full_R3.01.txt": ("ancillary-data/cosmoparams/", 135731),
 "COM_PowerSpect_CMB-base-plikHM-TTTEEE-lowl-lowE-lensing-minimum-theory_R3.01.txt": ("ancillary-data/cosmoparams/", 205647),
}
out = open("data.sha256", "w")
for name, (d, size) in F.items():
    h = hashlib.sha256(); n = 0
    with urllib.request.urlopen(B + d + name, timeout=900) as r, open(name, "wb") as fh:
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            h.update(b); fh.write(b); n += len(b)
    print(name, n, h.hexdigest(), flush=True)
    assert n == size, "%s: %d bytes, expected %d" % (name, n, size)
    out.write("%s  %s\n" % (h.hexdigest(), name))
out.close()
from astropy.io import fits
with open("headers.txt", "w") as fh:
    for name in F:
        if name.endswith(".fits"):
            with fits.open(name, memmap=True) as hd:
                for i, h in enumerate(hd):
                    fh.write("== %s HDU %d\n%s\n" % (name, i, h.header.tostring(sep="\n")))
    for name in F:
        if name.endswith(".txt"):
            fh.write("== %s head\n%s\n" % (name, "".join(open(name).readlines()[:5])))
PYEOF
cat data.sha256
ls -l
echo DONE
