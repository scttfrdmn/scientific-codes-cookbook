#!/usr/bin/env bash
# measurements/cromwell-wdl-real, on a spawn host: Broad's published haplotypecaller-gvcf-gatk4 WDL
# (gatk4-germline-snps-indels 2.3.1, unmodified) on Cromwell's local backend with Docker, over
# NA12878 chr20 at 36x. Cromwell runs in its pinned image and launches each WDL task as a sibling
# container of the pinned GATK image through the host's Docker socket -- the single-box shape the
# local backend is built for.
#
#   run1   the workflow, cold                    -> every call executes
#   run2   the same submission again             -> every call must be a cache hit
#   run3   shard 0's interval file edited (@CO)  -> exactly that shard and the merge re-run
#   direct the same 16 HaplotypeCaller commands and the merge, written by hand from the WDL and
#          run in the same image without Cromwell -> every output must match Cromwell's
#
#   run.sh <scatter dir name> <concurrent jobs>
set +e
SC=${1:?scatter dir}; J=${2:?jobs}
IN=/tmp/in; W=/tmp/cw; OUT=/tmp/out
mkdir -p $OUT $W/bin $W/home $W/db $W/direct
CHECK=$OUT/check.txt; : > $CHECK; : > $OUT/timing.txt
FAILS=0
rec() { printf '%s\t%s\n' "$1" "$2" | tee -a $CHECK; }
want() { if [ "$2" = "$3" ]; then rec "$1" "$2"; else rec "$1" "$2  FAIL: want $3"; FAILS=$((FAILS + 1)); fi; }
ph() { echo "$1 $(date +%s)" | tee -a $OUT/timing.txt; }
kv() { awk -F'\t' -v k="$1" '$1==k{print $2; exit}' "$2"; }
ph start

case "$(uname -m)" in
  aarch64) CROM=quay.io/aarchbio/cromwell@sha256:0d86efb43d8df392362fb54c0ef1ea2a6449b776cabe5089dd1b5a0cafce230e
           GATK=quay.io/aarchbio/gatk4@sha256:92065598dde922a2223eb57863606c4bf8558b11af938ada9637efb9d0d8d0fc
           DTGZ=docker-27.5.1-aarch64.tgz ;;
  x86_64)  CROM=quay.io/biocontainers/cromwell@sha256:71738c6e849f45197f746e5aeb9a80d607498ae48d87ebb923ab61faf8f5199b
           GATK=quay.io/biocontainers/gatk4@sha256:7311332e4d565d2356bbc0540cf582b505d1969a5f088a2c065302b13b10f5ee
           DTGZ=docker-27.5.1-x86_64.tgz ;;
  *) echo "unexpected arch $(uname -m)"; exit 1 ;;
esac

# ---- the host -----------------------------------------------------------------------------------
TOK=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
rec instance_type "$(curl -s -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/instance-type)"
rec host_arch "$(uname -m)"
rec vcpus "$(nproc)"
rec threads_per_core "$(lscpu | awk -F: '/Thread\(s\) per core/{gsub(/ /,"",$2); print $2}')"
rec cpu_model "$(lscpu | awk -F: '/Model name/{sub(/^ +/,"",$2); print $2; exit}')"
rec mem_total_kib "$(awk '/MemTotal/{print $2}' /proc/meminfo)"
{ lscpu; echo; df -h /tmp /var; uname -a; } > $OUT/host.txt 2>&1
grep -E "  (haplotypecaller-gvcf-gatk4\.wdl|NA12878\.chr20\.30x\.bam|$DTGZ)\$" $IN/pins.sha256 > $W/pins.list
( cd $IN && sha256sum -c --strict $W/pins.list ) > $OUT/pins.txt 2>&1
want inputs_match_pins "$?" 0

ph docker_install
sudo dnf install -y -q docker > /dev/null 2>&1
sudo systemctl start docker
for _ in $(seq 1 30); do sudo docker info > /dev/null 2>&1 && break; sleep 2; done
tar xzf $IN/$DTGZ -C $W/bin --strip-components=1 docker/docker
rec docker_cli "$($W/bin/docker --version 2>&1)"
SOCKGID=$(stat -c %g /var/run/docker.sock)
U="$(id -u):$(id -g)"
CGD=$(sudo docker info --format '{{.CgroupDriver}}' 2>/dev/null)
rec docker_cgroup_driver "$CGD"
# Every WDL task container goes under one parent cgroup, held open by a sleeper, so its
# memory.peak is the aggregate peak of everything Cromwell ran at once (page cache included).
if [ "$CGD" = systemd ]; then
  TASKCG=cwtask.slice; CGP=/sys/fs/cgroup/cwtask.slice
  sudo systemd-run -q --unit=cwhold --slice=cwtask.slice sleep infinity
else
  TASKCG=/cwtask; CGP=/sys/fs/cgroup/cwtask
  sudo mkdir -p $CGP
fi

ph image_pull
sudo docker pull -q $CROM > /dev/null; sudo docker pull -q $GATK > /dev/null
want cromwell_image "$(sudo docker inspect --format '{{index .RepoDigests 0}}' $CROM)" "$CROM"
want gatk_image "$(sudo docker inspect --format '{{index .RepoDigests 0}}' $GATK)" "$GATK"
CP=/opt/conda/bin:/usr/local/bin:/usr/bin:/bin
sudo docker run --rm --entrypoint /bin/bash $CROM -c "export PATH=$CP; command -v cromwell java python3; find / -xdev \( -name 'cromwell*.jar' -o -name 'womtool*.jar' \) 2>/dev/null" > $OUT/cromwell-image.txt 2>&1
sudo docker run --rm --entrypoint /bin/bash $GATK -c "export PATH=$CP; command -v gatk java python3; gatk --version" > $OUT/gatk-image.txt 2>&1
JAR=$(awk '/\/cromwell[^/]*\.jar$/{print; exit}' $OUT/cromwell-image.txt)
WOM=$(awk '/\/womtool[^/]*\.jar$/{print; exit}' $OUT/cromwell-image.txt)
rec cromwell_jar "$JAR"
rec gatk_version "$(awk '/HTSJDK Version|The Genome Analysis Toolkit/{print; exit}' $OUT/gatk-image.txt)"

# Cromwell in its container, as the instance user, with the Docker socket and /tmp at the same path.
crom() {
  sudo docker run --rm --user "$U" --group-add "$SOCKGID" -e HOME=$W/home -e PATH=$CP \
    -v /var/run/docker.sock:/var/run/docker.sock -v /tmp:/tmp -w $W --entrypoint /bin/bash $CROM -c "$1"
}
JAVA="java -Xmx4g -Duser.home=$W/home -Dconfig.file=$W/cromwell.conf"
rec cromwell_version "$(crom "java -jar $JAR --version" 2>&1 | tail -n 1)"

# ---- the backend: Cromwell's own local backend, with call caching on a file database ------------
cat > $W/cromwell.conf <<EOF
include required(classpath("application"))
call-caching { enabled = true, invalidate-bad-cache-results = true }
database {
  profile = "slick.jdbc.HsqldbProfile\$"
  db {
    driver = "org.hsqldb.jdbcDriver"
    url = "jdbc:hsqldb:file:$W/db/cromwell;shutdown=false;hsqldb.tx=mvcc"
    connectionTimeout = 120000
    numThreads = 1
  }
}
backend {
  default = "Local"
  providers {
    Local {
      actor-factory = "cromwell.backend.impl.sfs.config.ConfigBackendLifecycleActorFactory"
      config {
        concurrent-job-limit = $J
        run-in-background = true
        root = "$W/cromwell-executions"
        runtime-attributes = """
          String? docker
          String? docker_user
        """
        submit = "/usr/bin/env bash \${script}"
        submit-docker = """
          $W/bin/docker run --rm -i --user $U --cgroup-parent=$TASKCG -e PATH=$CP --entrypoint /bin/bash -v \${cwd}:\${docker_cwd} \${docker} \${docker_script}
        """
        filesystems {
          local {
            localization: ["hard-link", "copy"]
            caching { duplication-strategy: ["hard-link", "copy"], hashing-strategy: "file" }
          }
        }
      }
    }
  }
}
EOF
cp $W/cromwell.conf $OUT/

# ---- inputs: the published WDL, unmodified; the scatter Broad's tool made -------------------------
WDL=$IN/haplotypecaller-gvcf-gatk4.wdl
ls $IN/$SC/*/scattered.interval_list | sort > $W/intervals.txt
NSH=$(wc -l < $W/intervals.txt | tr -d ' ')
rec shards_planned "$NSH"
inputs() {   # <interval list file> -> the workflow's inputs JSON
  cat <<EOF
{
  "HaplotypeCallerGvcf_GATK4.input_bam": "$IN/NA12878.chr20.30x.bam",
  "HaplotypeCallerGvcf_GATK4.input_bam_index": "$IN/NA12878.chr20.30x.bam.bai",
  "HaplotypeCallerGvcf_GATK4.ref_fasta": "$IN/chr20.fa",
  "HaplotypeCallerGvcf_GATK4.ref_fasta_index": "$IN/chr20.fa.fai",
  "HaplotypeCallerGvcf_GATK4.ref_dict": "$IN/chr20.dict",
  "HaplotypeCallerGvcf_GATK4.scattered_calling_intervals_list": "$1",
  "HaplotypeCallerGvcf_GATK4.gatk_docker": "$GATK",
  "HaplotypeCallerGvcf_GATK4.gatk_path": "gatk"
}
EOF
}
inputs $W/intervals.txt > $W/inputs.json
cp $W/inputs.json $OUT/

ph womtool
crom "java -jar $WOM validate $WDL" > $OUT/womtool.txt 2>&1
want womtool_validate "$(awk 'NR==1{print $1}' $OUT/womtool.txt)" "Success!"

body() { zcat "$1" | grep -v '^##GATKCommandLine' | md5sum | cut -c1-32; }

cromrun() {   # <n> <inputs.json>
  ph cromwell_run$1_start
  crom "python3 $IN/rss.py $OUT/engine-run$1.rss $JAVA -jar $JAR run $WDL --inputs $2 --metadata-output $W/meta$1.json" \
    > $OUT/cromwell-run$1.log 2>&1
  rec cromwell_run$1_rc "$?"
  ph cromwell_run$1_end
  cp $W/meta$1.json $OUT/ 2>/dev/null
  python3 $IN/meta.py $W/meta$1.json $OUT/calls-run$1.tsv > $OUT/meta-run$1.txt 2>&1
  sed "s/^/run$1_/" $OUT/meta-run$1.txt | tee -a $CHECK
}

# ---- run 1: cold --------------------------------------------------------------------------------
cromrun 1 $W/inputs.json
want run1_succeeded "$(kv status $OUT/meta-run1.txt)" Succeeded
want run1_hc_calls "$(kv HaplotypeCaller_done $OUT/meta-run1.txt)" "$NSH"
want run1_hc_cache_hits "$(kv HaplotypeCaller_cache_hits $OUT/meta-run1.txt)" 0
want run1_merge_done "$(kv MergeGVCFs_done $OUT/meta-run1.txt)" 1
want run1_shards_materialised "$(ls -d $W/cromwell-executions/HaplotypeCallerGvcf_GATK4/*/call-HaplotypeCaller/shard-* 2>/dev/null | wc -l | tr -d ' ')" "$NSH"
rec task_cgroup_memory_peak_kib "$(( $(cat $CGP/memory.peak 2>/dev/null || echo 0) / 1024 ))"
rec engine_maxrss_kib "$(kv maxrss_kib $OUT/engine-run1.rss)"
V1=$(kv output_vcf $OUT/meta-run1.txt)
B1=$(body "$V1")
rec run1_body_md5 "$B1"
# Cromwell's shard i must have been given interval file i (scatter order is the input order).
awk -F'\t' '$1=="HaplotypeCaller"{print $6}' $OUT/calls-run1.tsv | xargs -n1 cat | md5sum | cut -c1-32 > $W/a
xargs -n1 cat < $W/intervals.txt | md5sum | cut -c1-32 > $W/b
want run1_shard_i_got_interval_i "$(cat $W/a)" "$(cat $W/b)"

# ---- run 2: identical resubmission, every call from the cache -------------------------------------
cromrun 2 $W/inputs.json
want run2_succeeded "$(kv status $OUT/meta-run2.txt)" Succeeded
want run2_hc_cache_hits "$(kv HaplotypeCaller_cache_hits $OUT/meta-run2.txt)" "$NSH"
want run2_merge_cache_hits "$(kv MergeGVCFs_cache_hits $OUT/meta-run2.txt)" 1
V2=$(kv output_vcf $OUT/meta-run2.txt)
want run2_output_bytes_eq_run1 "$(md5sum < "$V2" | cut -c1-32)" "$(md5sum < "$V1" | cut -c1-32)"

# ---- run 3: one input changed; only what depends on it may re-run --------------------------------
mkdir -p $W/mod
I0=$(sed -n 1p $W/intervals.txt)
{ grep '^@' "$I0"; echo "@CO	cromwell-wdl-real: same intervals, different bytes"; grep -v '^@' "$I0"; } > $W/mod/scattered.interval_list
{ echo $W/mod/scattered.interval_list; sed 1d $W/intervals.txt; } > $W/intervals-mod.txt
inputs $W/intervals-mod.txt > $W/inputs3.json
cromrun 3 $W/inputs3.json
want run3_succeeded "$(kv status $OUT/meta-run3.txt)" Succeeded
want run3_hc_cache_hits "$(kv HaplotypeCaller_cache_hits $OUT/meta-run3.txt)" "$((NSH - 1))"
want run3_hc_rerun_shard "$(awk -F'\t' '$1=="HaplotypeCaller" && $4=="false"{printf "%s ", $2}' $OUT/calls-run3.tsv)" "0 "
want run3_merge_cache_hits "$(kv MergeGVCFs_cache_hits $OUT/meta-run3.txt)" 0
want run3_body_eq_run1 "$(body "$(kv output_vcf $OUT/meta-run3.txt)")" "$B1"

# ---- direct: the same steps, no engine --------------------------------------------------------------
ph direct_start
GQB=$(for g in 10 20 30 40 50 60 70 80 90; do printf -- '-GQB %s ' $g; done)
i=0
while read -r iv; do
  d=$W/direct/shard-$i; mkdir -p $d
  cat > $d/run.sh <<EOF
cd $d
python3 $IN/rss.py $d/rss.txt gatk --java-options "-Xmx6G -XX:GCTimeLimit=50 -XX:GCHeapFreeLimit=10" HaplotypeCaller \
  -R $IN/chr20.fa -I $IN/NA12878.chr20.30x.bam -L $iv -O NA12878.chr20.30x.g.vcf.gz -contamination 0 \
  -G StandardAnnotation -G StandardHCAnnotation -G AS_StandardAnnotation $GQB -ERC GVCF > hc.log 2>&1
EOF
  echo $d
  i=$((i + 1))
done < $W/intervals.txt > $W/direct/jobs.txt
xargs -P "$J" -I{} sudo docker run --rm --user "$U" -e PATH=$CP -e HOME=/tmp -v /tmp:/tmp --entrypoint /bin/bash $GATK {}/run.sh \
  < $W/direct/jobs.txt
ph direct_hc_end
M=$W/direct/merge; mkdir -p $M
sudo docker run --rm --user "$U" -e PATH=$CP -e HOME=/tmp -v /tmp:/tmp -w $M --entrypoint /bin/bash $GATK -c \
  "gatk --java-options -Xmx2G MergeVcfs $(sed 's|$|/NA12878.chr20.30x.g.vcf.gz|; s|^|--INPUT |' $W/direct/jobs.txt | tr '\n' ' ') --OUTPUT NA12878.chr20.30x.g.vcf.gz" \
  > $M/merge.log 2>&1
ph direct_end
rc_ok=0; peak=0
while read -r d; do
  [ "$(kv rc $d/rss.txt)" = 0 ] && rc_ok=$((rc_ok + 1))
  r=$(kv maxrss_kib $d/rss.txt); [ "${r:-0}" -gt "$peak" ] && peak=$r
done < $W/direct/jobs.txt
want direct_hc_ok "$rc_ok" "$NSH"
rec direct_hc_max_shard_rss_kib "$peak"

# every Cromwell shard against the same shard run directly
same=0; i=0
while read -r iv; do
  cv=$(awk -F'\t' -v s=$i '$1=="HaplotypeCaller" && $2==s{print $7}' $OUT/calls-run1.tsv)
  a=$(body "$cv"); b=$(body $W/direct/shard-$i/NA12878.chr20.30x.g.vcf.gz)
  printf '%s\t%s\t%s\n' $i "$a" "$b" >> $OUT/shards-vs-direct.tsv
  [ -n "$a" ] && [ "$a" = "$b" ] && same=$((same + 1))
  i=$((i + 1))
done < $W/intervals.txt
want shards_eq_direct "$same" "$NSH"
want merged_eq_direct "$(body $M/NA12878.chr20.30x.g.vcf.gz)" "$B1"
# What --GATKCommandLine leaves behind: header lines that differ between the two merged files.
diff <(zcat "$V1" | grep '^##' | grep -v '^##GATKCommandLine') <(zcat $M/NA12878.chr20.30x.g.vcf.gz | grep '^##' | grep -v '^##GATKCommandLine') > $OUT/header-diff.txt
rec header_diff_lines "$(wc -l < $OUT/header-diff.txt | tr -d ' ')"

# ---- the merged GVCF, against the intervals it was called on -----------------------------------
ph structure
python3 $IN/gvcf.py "$V1" $(cat $W/intervals.txt) > $OUT/gvcf.txt 2>&1
sed 's/^/gvcf_/' $OUT/gvcf.txt | tee -a $CHECK
SUM=0; i=0
while read -r iv; do
  n=$(zcat $W/direct/shard-$i/NA12878.chr20.30x.g.vcf.gz | grep -vc '^#'); SUM=$((SUM + n)); i=$((i + 1))
done < $W/intervals.txt
want merged_records_eq_sum_of_shards "$(kv records $OUT/gvcf.txt)" "$SUM"
want gvcf_sample "$(kv sample $OUT/gvcf.txt)" NA12878
want gvcf_unsorted "$(kv unsorted $OUT/gvcf.txt)" 0
want gvcf_every_interval_base_covered "$(kv covered_bp_in $OUT/gvcf.txt)" "$(kv interval_bp $OUT/gvcf.txt)"

# ---- evidence out ----------------------------------------------------------------------------------
ph collect
cp "$V1" $OUT/NA12878.chr20.30x.g.vcf.gz; cp "$V1.tbi" $OUT/ 2>/dev/null
mkdir -p $OUT/logs
for s in $(seq 0 $((NSH - 1))); do
  cp $W/direct/shard-$s/hc.log $OUT/logs/direct-shard-$s.hc.log 2>/dev/null
  cp $W/direct/shard-$s/rss.txt $OUT/logs/direct-shard-$s.rss 2>/dev/null
  cp $W/cromwell-executions/HaplotypeCallerGvcf_GATK4/*/call-HaplotypeCaller/shard-$s/execution/stderr $OUT/logs/cromwell-shard-$s.stderr 2>/dev/null
done
cp $M/merge.log $OUT/logs/direct-merge.log
ph end
rec fails "$FAILS"
if [ "$FAILS" = 0 ]; then echo "CHECK PASSED" | tee -a $CHECK; else echo "CHECK FAILED ($FAILS)" | tee -a $CHECK; exit 1; fi
