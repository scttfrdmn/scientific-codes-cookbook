#!/usr/bin/env bash
# Runs on AWS (fetch.task.json), never on a laptop: fetch the Brunello A375 dropout screen of
# Sanson et al. 2018 (Nat Commun 9:5416, BioProject PRJNA508200) from ENA, check every FASTQ
# against ENA's own md5, and record its sha256 -- the pin every later run checks (inputs.sha256).
# Also fetches the Brunello library (Zenodo 10.5281/zenodo.5750854) and Hart's core-essential /
# non-essential gene sets (hart-lab/bagel at a fixed commit), each checked against a sha256 pin.
set +e
set -uo pipefail
cd /tmp
mkdir -p /tmp/stage
E=https://ftp.sra.ebi.ac.uk/vol1/fastq
BAGEL=https://raw.githubusercontent.com/hart-lab/bagel/53388adbb4fb0931e5c9dda135502be19e4555f0
# label  run  ENA fastq_md5  ENA read_count
cat > runs.tsv <<'EOF'
plasmid	SRR8297998	82271d40adda5d8d04df7826f11b9efb	60120685
repA	SRR8279891	2e21fb2ff86850cd0bc528f7b72d88de	99121522
repB	SRR8281054	767577a52b5e68f9e541fc4adc8567bc	93622486
EOF
T0=$(date +%s)
while IFS="$(printf '\t')" read -r L R M N; do
  ( curl -fsS --retry 5 -o "/tmp/stage/$L.fastq.gz" "$E/${R:0:6}/00${R: -1}/$R/$R.fastq.gz"; echo "$L curl exit $?" ) &
done < runs.tsv
curl -fsS --retry 5 -o /tmp/stage/brunello.tsv https://zenodo.org/records/5750854/files/brunello.tsv
curl -fsS --retry 5 -o /tmp/stage/CEGv2.txt "$BAGEL/CEGv2.txt"
curl -fsS --retry 5 -o /tmp/stage/NEGv1.txt "$BAGEL/NEGv1.txt"
wait
echo "fetch_s	$(( $(date +%s) - T0 ))"
ls -l /tmp/stage

cd /tmp/stage
FAIL=0
printf 'a22ea81a16ff83f238a138f409ada2ce57c40f1407531829ef2eb6677df2ab24  brunello.tsv\n1b1df46f829e2c47dc8a4c164f60f5f1dc67513bafba7d8b9609421e0c75d27e  CEGv2.txt\n18a6b37a8f182a1bf501548e6b28fe4fcef02fcd296f873ef500a21eeb433739  NEGv1.txt\n' > /tmp/small.sha256
sha256sum -c /tmp/small.sha256 || FAIL=1
while IFS="$(printf '\t')" read -r L R M N; do
  GOT=$(md5sum "$L.fastq.gz" | cut -d' ' -f1)
  [ "$GOT" = "$M" ] && echo "$L ($R) md5 matches ENA" || { echo "FAIL: $L md5 $GOT != ENA $M"; FAIL=1; }
done < /tmp/runs.tsv
[ "$FAIL" = 0 ] || exit 1
sha256sum brunello.tsv CEGv2.txt NEGv1.txt plasmid.fastq.gz repA.fastq.gz repB.fastq.gz > /tmp/stage/inputs.sha256
cat /tmp/stage/inputs.sha256
du -sh /tmp/stage
echo "FETCH OK"
