set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp
mkdir -p /tmp/out && cd /tmp/out
# JPL's DE440 planetary ephemeris and its GM constants, from NAIF's generic-kernel archive, fetched
# on AWS. Both files are frozen releases (DE440: Park et al. 2021, AJ 161:105); the sha256 recorded
# here is the pin.
python3 -u - <<'PYEOF' || { echo "FAIL: fetch"; exit 1; }
import hashlib, urllib.request
B = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/"
F = {"de440.bsp": "spk/planets/", "gm_de440.tpc": "pck/"}
out = open("data.sha256", "w")
for name, d in F.items():
    h = hashlib.sha256(); n = 0
    with urllib.request.urlopen(B + d + name, timeout=900) as r, open(name, "wb") as fh:
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            h.update(b); fh.write(b); n += len(b)
    print(name, n, h.hexdigest(), flush=True)
    out.write("%s  %s\n" % (h.hexdigest(), name))
out.close()
from jplephem.spk import SPK
k = SPK.open("de440.bsp")
with open("headers.txt", "w") as fh:
    fh.write(str(k) + "\n")
    fh.write("\n".join(k.daf.comments().splitlines()[:60]) + "\n")
print(open("headers.txt").read())
PYEOF
cat data.sha256
ls -l
echo DONE
