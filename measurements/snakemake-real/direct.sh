#!/usr/bin/env bash
# measurements/snakemake-real, without Snakemake: the same seqkit command on the same eleven
# files in the same image, written by hand from the rule rather than copied from what Snakemake
# ran, on the same instance size as a stats job. Then cohort.py over these rows. Every output
# must be byte-identical to the workflow's (staged in at /tmp/in/smk/).
#
#   direct.sh <bucket>
set +e
B=${1:?bucket}
IN=/tmp/in; OUT=/tmp/out; D=/var/tmp/direct
mkdir -p $OUT/stats $D
CHECK=$OUT/check.txt; : > $CHECK
FAILS=0
rec() { printf '%s\t%s\n' "$1" "$2" | tee -a $CHECK; }
want() { if [ "$2" = "$3" ]; then rec "$1" "$2"; else rec "$1" "$2  FAIL: want $3"; FAILS=$((FAILS + 1)); fi; }
TOK=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
rec instance_type "$(curl -s -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/instance-type)"
rec threads_per_core "$(lscpu | awk -F: '/Thread\(s\) per core/{gsub(/ /,"",$2); print $2}')"
rec cpu_model "$(lscpu | awk -F: '/Model name/{sub(/^ +/,"",$2); print $2; exit}')"
{ lscpu; echo; df -h /tmp /var/tmp; uname -a; } > $OUT/host.txt 2>&1

case "$(uname -m)" in
  aarch64) IMG=quay.io/aarchbio/seqkit@sha256:5478aaad4dd7bf7d7f02eee168ee3ad90d17b6729ab5e889a9385b4458cde7c5 ;;
  x86_64)  IMG=quay.io/biocontainers/seqkit@sha256:0e14f53b486c6b6e199e525f3f1e7494b59b580f835f7835e497b46f99267b6a ;;
esac
sudo dnf install -y -q docker > /dev/null 2>&1
sudo systemctl start docker
sudo docker pull -q "$IMG" > /dev/null
want image "$(sudo docker inspect --format '{{index .RepoDigests 0}}' "$IMG")" "$IMG"

printf 'file\tseqkit_s\n' > $OUT/timing.tsv
SAME=0; N=0
while IFS=$'\t' read -r f sha reads bases; do
  [ "$f" = file ] && continue
  N=$((N + 1))
  key=$(awk -F'\t' -v f="$f" '$1==f{print $2}' $IN/keys.tsv)
  aws s3 cp --only-show-errors "s3://$B/$key" "$D/$f"
  want "sha256 $f" "$(sha256sum "$D/$f" | awk '{print $1}')" "$sha"
  t0=$(date +%s.%N)
  sudo docker run --rm -v $D:/w:ro "$IMG" bash -c "export PATH=/opt/conda/bin:/usr/local/bin:\$PATH; seqkit stats -T -a -j 2 /w/$f" > $D/raw.tsv
  t1=$(date +%s.%N)
  awk -v f="$f" 'BEGIN{FS=OFS="\t"} NR==1{print; next} {$1=f; print}' $D/raw.tsv > $OUT/stats/$f.tsv
  awk -v f="$f" -v a="$t0" -v b="$t1" 'BEGIN{printf "%s\t%.2f\n", f, b - a}' >> $OUT/timing.tsv
  if cmp -s $OUT/stats/$f.tsv "$IN/smk/stats/$f.tsv"; then SAME=$((SAME + 1)); rec "stats $f" "identical to the workflow's"
  else rec "stats $f" "DIFFERS from the workflow's  FAIL"; FAILS=$((FAILS + 1)); diff $OUT/stats/$f.tsv "$IN/smk/stats/$f.tsv" >> $CHECK; fi
  rm -f "$D/$f"   # this task's own download, not a staged input
done < $IN/cohort-truth.tsv
want stats_identical "$SAME" 11

python3 -I $IN/cohort.py $IN/cohort-truth.tsv $OUT/cohort.tsv $(ls $OUT/stats/*.tsv | sort) > $OUT/cohort.out 2>&1
want direct_cohort_exit "$?" 0
cmp -s $OUT/cohort.tsv $IN/smk/cohort.tsv
want cohort_identical_to_workflow "$?" 0
rec fails "$FAILS"
[ "$FAILS" = 0 ] && echo "DIRECT OK"
exit $FAILS
