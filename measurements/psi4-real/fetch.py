# DESS66 / DESS66x8 (Donchev et al., Sci. Data 8, 55 (2021); Zenodo 10.5281/zenodo.5676284 v1.0.0),
# fetched on AWS and checked against the md5 Zenodo publishes before anything is kept.
import csv
import hashlib
import urllib.request
import zipfile

F = [("DESS66.zip", "48930e4d01b0844b2c0c1fd2db7cc16b"), ("DESS66x8.zip", "9df3c70f54f7f52856ee4d53a0b532f5")]
for name, md5 in F:
    req = urllib.request.Request("https://zenodo.org/records/5676284/files/%s?download=1" % name,
                                 headers={"User-Agent": "curl/8"})
    b = urllib.request.urlopen(req, timeout=600).read()
    got = hashlib.md5(b).hexdigest()
    print(name, len(b), "md5", got, "zenodo", md5, flush=True)
    assert got == md5, name + ": md5 does not match Zenodo's"
    open(name, "wb").write(b)
    z = zipfile.ZipFile(name)
    names = z.namelist()
    print("  %d members; first: %s" % (len(names), names[:8]))
    for n in names:
        if n.endswith("_meta.csv") or n.endswith("README.md"):
            print("=====", n)
            print(z.read(n).decode())
        elif n.endswith(".csv"):
            rows = list(csv.reader(z.read(n).decode().splitlines()))
            print("=====", n, "rows", len(rows) - 1)
            for r in rows[:3]:
                print(r)
