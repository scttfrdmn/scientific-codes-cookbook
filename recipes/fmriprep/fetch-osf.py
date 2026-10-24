"""Fetch fMRIPrep's own CI inputs for its ds005 fasttrack run from OSF (project fvuh8), on AWS.

Runs inside the staging task (stage.spec.json), never on a laptop. Each archive is checked to BE
an archive before it is hashed -- OSF can answer with an HTML maintenance page and HTTP 200 -- and
then against OSF's own published sha256 for that file version. Extracted exactly as CI does
(.circleci/config.yml at 25.2.7), plus CI's one edit to the data: "PhaseEncodingDirection": "j"
injected as the sidecar's second line, the same awk-equivalent insert. Writes /var/tmp/out/.
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import time
import urllib.request
import zipfile

OSF = "https://files.osf.io/v1/resources/fvuh8/providers/osfstorage/"
# name, OSF file id, magic bytes, OSF version, size, sha256 (OSF API, attributes.extra.hashes)
FILES = [
    ("ds005_downsampled.tar.gz", "57f32a429ad5a101f977eb75", b"\x1f\x8b", 3, 7133415,
     "50826b60020f746a6ebb6557460a706ff0df3cad59eb037f7184d54d9fce7a2c"),
    ("ds005_derivatives_freesurfer.zip", "637bab2c92a98a1c0606e6a0", b"PK\x03\x04", None, 204615799,
     "76ef8d3d5a6c776a9ea6f8640e6a9887efb67f3a805972d37409893ba84418a3"),
    ("ds005_sub-01_smriprep.zip", "637cf6505ea438007b0497d3", b"PK\x03\x04", None, 398097866,
     "0bb2bf1402ea444e5f926d335f21883538b7e8b15b460f632138ae998b266565"),
]
DL, WORK, OUT = "/var/tmp/dl", "/var/tmp/x", "/var/tmp/out"


def fail(msg):
    print("FAIL: " + msg, flush=True)
    sys.exit(1)


def fetch(name, fid, magic, size, sha):
    path = os.path.join(DL, name)
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(OSF + fid, timeout=120) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            break
        except Exception as e:  # network blip: retry, then fail with the reason
            print("  %s attempt %d: %s" % (name, attempt, e), flush=True)
            time.sleep(10 * attempt)
    else:
        fail("%s: could not download" % name)
    head = open(path, "rb").read(256)
    if not head.startswith(magic):
        fail("%s is not an archive -- OSF returned: %r" % (name, head[:120]))
    got = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    print("  %s: %d bytes, sha256 %s" % (name, got, h.hexdigest()), flush=True)
    if got != size or h.hexdigest() != sha:
        fail("%s does not match OSF's published size/sha256 (%d, %s)" % (name, size, sha))
    return path


def safe(dest, member):
    p = os.path.realpath(os.path.join(dest, member))
    if not p.startswith(os.path.realpath(dest) + os.sep):
        fail("archive member escapes the extraction dir: %r" % member)


def unzip(path, dest):
    """unzip(1) semantics: restore symlinks, which zipfile would write as text files."""
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            safe(dest, i.filename)
            target = os.path.join(dest, i.filename)
            mode = i.external_attr >> 16
            if i.is_dir():
                os.makedirs(target, exist_ok=True)
            elif stat.S_ISLNK(mode):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.symlink(z.read(i).decode(), target)
            else:
                z.extract(i, dest)


def main():
    for d in (DL, WORK, OUT):
        os.makedirs(d, exist_ok=True)
    got = {f[0]: fetch(f[0], f[1], f[2], f[4], f[5]) for f in FILES}
    data, deriv = os.path.join(WORK, "data"), os.path.join(WORK, "deriv")
    os.makedirs(data)
    os.makedirs(deriv)
    with tarfile.open(got["ds005_downsampled.tar.gz"]) as t:      # CI: tar xvzf ... -C /tmp/data/
        for m in t.getmembers():
            safe(data, m.name)
        t.extractall(data)
    unzip(got["ds005_derivatives_freesurfer.zip"], deriv)          # CI: unzip -d /tmp/ds005
    unzip(got["ds005_sub-01_smriprep.zip"], deriv)
    for d in ("data/ds005", "deriv/freesurfer", "deriv/smriprep"):
        if not os.path.isdir(os.path.join(WORK, d)):
            fail("expected %s after extraction; have %s" % (d, sorted(os.listdir(os.path.dirname(os.path.join(WORK, d))))))
    # CI, "Inject pretend metadata": awk 'NR==1{print; print "    \"PhaseEncodingDirection\": \"j\","} NR!=1'
    sc = os.path.join(data, "ds005", "task-mixedgamblestask_bold.json")
    lines = open(sc).read().split("\n")
    lines.insert(1, '    "PhaseEncodingDirection": "j",')
    open(sc, "w").write("\n".join(lines))
    if json.load(open(sc)).get("PhaseEncodingDirection") != "j":
        fail("CI's PhaseEncodingDirection edit did not produce valid JSON with PE j")
    print("  CI's PhaseEncodingDirection edit applied; sidecar parses", flush=True)
    for src, dst in (("data/ds005", "ds005"), ("deriv/freesurfer", "freesurfer"), ("deriv/smriprep", "smriprep")):
        shutil.move(os.path.join(WORK, src), os.path.join(OUT, dst))
    n = sum(len(fs) for _, _, fs in os.walk(OUT))
    subs = sorted(x for x in os.listdir(os.path.join(OUT, "ds005")) if x.startswith("sub-"))
    print("  staged %d files; ds005 subjects %s; freesurfer %s" % (n, subs, sorted(os.listdir(os.path.join(OUT, "freesurfer")))), flush=True)
    with open(os.path.join(OUT, "stage.ok"), "w") as f:   # stage-inputs.sh looks for this marker
        f.writelines("%s  %s\n" % (x[5], x[0]) for x in FILES)
    print("STAGE OK", flush=True)


if __name__ == "__main__":
    main()
