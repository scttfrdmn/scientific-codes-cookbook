#!/usr/bin/env bash
# Runs ON A SPAWN HOST (00-stage, no container): fetch the Ensembl GRCh37 release-87 gene
# annotation for chromosomes 20-22 plus the release's CDS and peptide FASTA, check each file
# against Ensembl's own CHECKSUMS (BSD `sum`), and write their sha256 to ensembl.sha256.
# Nothing passes through the operator's machine; spawn stages /tmp/out/ to the bucket.
set +e
set -uo pipefail
E=https://ftp.ensembl.org/pub/grch37/release-87
O=/tmp/out; mkdir -p "$O"; cd "$O" || exit 1
FAIL=0
get(){ # dir file
  curl -fsSL --retry 3 -o "$2" "$E/$1/$2" || { echo "FAIL: download $1/$2"; FAIL=1; return; }
  curl -fsSL --retry 3 -o /tmp/CHECKSUMS "$E/$1/CHECKSUMS" || { echo "FAIL: CHECKSUMS for $1"; FAIL=1; return; }
  want=$(awk -v f="$2" '$3==f{print ($1+0)" "($2+0)}' /tmp/CHECKSUMS)
  got=$(sum "$2" | awk '{print ($1+0)" "($2+0)}')
  if [ -n "$want" ] && [ "$want" = "$got" ]; then echo "ensembl_sum_ok $2 $got"; else echo "FAIL: $2 sum $got, CHECKSUMS says '$want'"; FAIL=1; fi
}
for C in 20 21 22; do get gff3/homo_sapiens Homo_sapiens.GRCh37.87.chromosome.$C.gff3.gz; done
get fasta/homo_sapiens/cds Homo_sapiens.GRCh37.cds.all.fa.gz
get fasta/homo_sapiens/pep Homo_sapiens.GRCh37.pep.all.fa.gz
sha256sum Homo_sapiens.GRCh37.* | tee ensembl.sha256
ls -l
[ "$FAIL" = 0 ] && echo "STAGE OK" || { echo "STAGE FAILED"; exit 1; }
