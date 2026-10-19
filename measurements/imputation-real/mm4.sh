#!/usr/bin/env bash
# Task 03 (minimac4 image): Minimac4 4.1.6 imputes the same cohort from the same panel. Minimac4
# wants phased targets, so it is given Beagle's phase at the array sites -- the shape of an
# imputation server's phase-then-impute pipeline. Then both tools are scored against the truth.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
machine
rec minimac4_version "$(minimac4 --version 2>&1 | head -1)"
rec python_version "$(python3 -c 'import sys; print(sys.version.split()[0])')"
rec threads "$T"
hwm_start minimac4 python3 bcftools

echo "== Beagle's phase at the array sites =="
tabix -p vcf beagle.vcf.gz
tabix -p vcf truth.vcf.gz
bcftools view -T typed.pos -Ou beagle.vcf.gz | bcftools annotate -x INFO,^FORMAT/GT -Oz -o "$W/phased.vcf.gz"
tabix -p vcf "$W/phased.vcf.gz"
NTYPED=$(wc -l < typed.pos | tr -d ' ')
chk "phased target sites == array sites" "$(bcftools index -n "$W/phased.vcf.gz")" "$NTYPED"

echo "== minimac4 =="
tic
minimac4 ref.msav "$W/phased.vcf.gz" -o "$O/mm4.vcf.gz" -O vcf.gz -f GT,DS -t "$T" > "$O/mm4.log" 2>&1
RC=$?
toc minimac4
chk "minimac4 exit status" "$RC" 0
tail -5 "$O/mm4.log"
tabix -p vcf "$O/mm4.vcf.gz" || { bcftools view -Oz -o "$W/mm4.bgz.vcf.gz" "$O/mm4.vcf.gz" && mv "$W/mm4.bgz.vcf.gz" "$O/mm4.vcf.gz" && tabix -p vcf "$O/mm4.vcf.gz"; }
chk "minimac4 TYPED records == array sites" "$(bcftools view -H -i 'INFO/TYPED=1' "$O/mm4.vcf.gz" | wc -l | tr -d ' ')" "$NTYPED"
rec mm4_body_md5 "$(body_md5 "$O/mm4.vcf.gz")"

echo "== score both against the held-out truth =="
tic
python3 -u -I score.py truth.vcf.gz beagle.vcf.gz "$O/mm4.vcf.gz" ref.sites.tsv typed.pos "$T" "$O" > "$O/score.log" 2>&1
RC=$?
toc score
cat "$O/score.log"
chk "score.py exit status (it fails on any of its own checks)" "$RC" 0
[ -s "$O/score.checks.tsv" ] && cat "$O/score.checks.tsv" >> "$O/checks.tsv"
rec scores_md5 "$(md5 < "$O/scores.tsv")"
hwm_stop
finish "MINIMAC4+SCORE"
