set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/var/tmp HOME=/var/tmp
mkdir -p /var/tmp/out && cd /var/tmp/out
# Fetched on AWS, never on a laptop: the truth and the paper's own assembly (figshare, checked
# against figshare's computed_md5) and the medaka model (checked against its sha256 at the image's
# release tag). The reads come from RODA instead (prep.sh). The sha256 printed here is the pin.
python3 -u - <<'PYEOF' || { echo "FAIL: fetch"; exit 1; }
import hashlib, tarfile, urllib.request

def get(url, name, size=None, md5=None, sha256=None):
    h5, h2, n = hashlib.md5(), hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=1800) as r, open(name, "wb") as fh:
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            h5.update(b); h2.update(b); fh.write(b); n += len(b)
    print("fetched", name, n, "md5", h5.hexdigest(), "sha256", h2.hexdigest(), flush=True)
    assert size is None or n == size, "%s: %d bytes, publisher says %d" % (name, n, size)
    assert md5 is None or h5.hexdigest() == md5, "%s: md5 %s, publisher says %s" % (name, h5.hexdigest(), md5)
    assert sha256 is None or h2.hexdigest() == sha256, "%s: sha256 differs from the pin" % name

# Sanderson et al. 2024 (Microbial Genomics), figshare 10.6084/m9.figshare.25472167.v1: the Trycycler
# reference for E. coli CFT073, built from INDEPENDENT R9.4.1 + Illumina data, and the authors' own
# Flye+Medaka assembly of the R10.4.1 Dorado 0.5.3 reads at 100x.
get("https://ndownloader.figshare.com/files/45261640", "assemblies.tar.gz", size=582874884,
    md5="a21857eac48fd238b4dcade578b12504")
want = {"upload/trcycler_references/E_coli.fasta": "truth.fa",
        "upload/flye_medaka/R10.4.1/dorado_0.5.3/100/E_coli.fasta": "paper-flye-medaka-100x.fa"}
with tarfile.open("assemblies.tar.gz") as t:
    for m in t.getmembers():
        if m.name in want:
            open(want[m.name], "wb").write(t.extractfile(m).read())

# The medaka model matching the basecaller (dna_r10.4.1_e8.2_400bps_sup@v4.3.0), from medaka's own
# repository at the image's version, v2.2.2.
get("https://github.com/nanoporetech/medaka/raw/v2.2.2/medaka/data/r1041_e82_400bps_sup_v4.3.0_model_pt.tar.gz",
    "r1041_e82_400bps_sup_v4.3.0_model_pt.tar.gz",
    sha256="7006f79fc0f49462215860030a9c34cb148619cabb70397277ff481ecf568963")
PYEOF
rm -f assemblies.tar.gz   # this task downloaded it; it is not a staged input
for f in truth.fa paper-flye-medaka-100x.fa; do
  awk -v f=$f '/^>/{if(n)print f, h, n; h=$1; n=0; next}{n+=length($0)} END{print f, h, n}' $f
done | tee replicons.txt
sha256sum truth.fa paper-flye-medaka-100x.fa r1041_e82_400bps_sup_v4.3.0_model_pt.tar.gz | tee data.sha256
echo DONE
