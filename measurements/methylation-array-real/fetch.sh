#!/usr/bin/env bash
# Runs on AWS (fetch.task.json), never on a laptop: fetch the whole GSE42861 cohort (Liu et al.
# 2013, Nat Biotechnol 31:142 -- 689 peripheral-blood 450k arrays, 354 rheumatoid arthritis and 335
# controls) from GEO, pin it, and lay it out for the minfi and SeSAMe tasks.
#
#   GSE42861_RAW.tar     GEO supplementary archive, 2015-11-04, 6,166,384,640 bytes (filelist.txt).
#                        GEO publishes no checksum, so its sha256 is recorded here and committed as
#                        the pin; every later task checks each IDAT against idat.sha256.
#   series matrix header the per-sample annotation (gender, disease state, age). GEO regenerates the
#                        series matrix file, so its bytes are not a pin -- the pin is on the derived
#                        pheno.tsv, which is content.
set +e
set -uo pipefail
cd /tmp
S=/tmp/stage
mkdir -p "$S/idat"
G=https://ftp.ncbi.nlm.nih.gov/geo/series/GSE42nnn/GSE42861
FAIL=0

T0=$(date +%s)
curl -fsS --retry 5 -o /tmp/raw.tar "$G/suppl/GSE42861_RAW.tar"; echo "curl tar exit $?"
# the annotation block only: stream the series matrix and stop at its 2.5 GB data table
curl -fsS --retry 5 "$G/matrix/GSE42861_series_matrix.txt.gz" | gzip -dc 2>/dev/null \
  | awk '/^!series_matrix_table_begin/{exit} {print}' > /tmp/header.txt
echo "series header lines $(wc -l < /tmp/header.txt)"
echo "fetch_s $(( $(date +%s) - T0 ))"
SZ=$(stat -c %s /tmp/raw.tar)
[ "$SZ" = 6166384640 ] && echo "raw.tar size $SZ == GEO filelist.txt" || { echo "FAIL: raw.tar is $SZ bytes, GEO says 6166384640"; FAIL=1; }
sha256sum /tmp/raw.tar | awk '{print $1"  GSE42861_RAW.tar"}' > "$S/raw.sha256"
cat "$S/raw.sha256"

tar -xf /tmp/raw.tar -C "$S/idat"; echo "untar exit $?"
rm -f /tmp/raw.tar            # this container downloaded it, so it may remove it
ls "$S/idat" | grep -v '\.idat\.gz$'
rm -f "$S"/idat/GPL13534_*    # the archive also carries the platform manifest; minfi ships its own
( cd "$S/idat" && sha256sum *.idat.gz ) > "$S/idat.sha256"
N=$(wc -l < "$S/idat.sha256")
[ "$N" = 1378 ] && echo "idat files $N == 689 x 2" || { echo "FAIL: $N idat files, want 1378"; FAIL=1; }

python3 -I - /tmp/header.txt "$S/pheno.tsv" "$S/idat" <<'PY'
import os, sys
hdr, out, idat = sys.argv[1:4]
rows = {}
chars = []
for line in open(hdr, encoding="utf-8"):
    f = line.rstrip("\n").split("\t")
    k, v = f[0], [x.strip('"') for x in f[1:]]
    if k == "!Sample_characteristics_ch1":
        chars.append(v)
    elif k in ("!Sample_geo_accession", "!Sample_title"):
        rows[k] = v
    elif k == "!Sample_supplementary_file":
        rows.setdefault(k, []).append(v)
gsm = rows["!Sample_geo_accession"]
n = len(gsm)
assert len(set(gsm)) == n, "duplicate GSM accessions"
cols = {}
for v in chars:
    keys = {x.split(": ", 1)[0] for x in v}
    assert len(keys) == 1, keys
    cols[keys.pop()] = [x.split(": ", 1)[1] for x in v]
base = []
for i in range(n):
    files = [os.path.basename(s[i]) for s in rows["!Sample_supplementary_file"]]
    grn = [x for x in files if x.endswith("_Grn.idat.gz")]
    assert len(grn) == 1, files
    b = grn[0][:-len("_Grn.idat.gz")]
    assert b.startswith(gsm[i] + "_"), (b, gsm[i])
    for ch in ("Grn", "Red"):
        assert os.path.exists(os.path.join(idat, "%s_%s.idat.gz" % (b, ch))), b
    base.append(b)
want = ["gender", "disease state", "age", "smoking status", "subject"]
with open(out, "w") as o:
    o.write("\t".join(["gsm", "basename"] + [w.replace(" ", "_") for w in want]) + "\n")
    for i in range(n):
        o.write("\t".join([gsm[i], base[i]] + [cols[w][i] for w in want]) + "\n")
from collections import Counter
print("samples", n, "unique", len(set(gsm)))
for w in ("gender", "disease state", "subject"):
    print(w, dict(Counter(cols[w])))
PY
[ $? = 0 ] || { echo "FAIL: series-matrix annotation did not parse"; FAIL=1; }
cp /tmp/header.txt "$S/series_matrix_header.txt"
sha256sum "$S/pheno.tsv" | awk '{print $1"  pheno.tsv"}'
du -sh "$S"
[ "$FAIL" = 0 ] || exit 1
echo "FETCH OK"
