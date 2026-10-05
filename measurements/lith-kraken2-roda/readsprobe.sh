set -uo pipefail
B="cookbook-942542972736-us-west-2"
W=/tmp/w; mkdir -p "$W"
R="$W/r.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/readsprobe.txt" --only-show-errors 2>/dev/null || true; }
trap 'say TRAP_EXIT "line=$LINENO rc=$?"' EXIT
# Instrument EVERY step and push after each, because three guesses at this have been wrong.
say step_00_boot "$(uname -m) $(nproc) cpu $(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo) GiB"
say step_01_df_root "$(df -h / | awk 'NR==2{print $2" size, "$4" avail, "$5" used"}')"
ROOTDEV=$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//;s|/dev/||')
DEV=/dev/$(lsblk -dno NAME,SIZE,TYPE | awk -v r="$ROOTDEV" '$3=="disk" && $1!=r {print $1" "$2}' | sort -k2 -hr | head -1 | awk '{print $1}')
say step_02_nvme_dev "$DEV"
sudo dnf install -y -q docker >/dev/null 2>&1; say step_03_docker "rc=$?"
sudo mkfs.xfs -f -q "$DEV" >/dev/null 2>&1 || sudo mkfs.ext4 -F -q "$DEV" >/dev/null 2>&1
sudo mkdir -p /mnt/nvme && sudo mount "$DEV" /mnt/nvme && sudo mkdir -p /mnt/nvme/db /mnt/nvme/out
sudo chown -R "$(whoami)" /mnt/nvme
sudo chmod 1777 /mnt/nvme /mnt/nvme/out
say step_04_nvme "$(df -h /mnt/nvme | awk 'NR==2{print $4" avail"}')"
say step_05_begin_reads ""
aws s3 cp "s3://$B/inputs/bwa-real/SRR062634_1.filt.fastq.gz" "$W/r1.fq.gz" --only-show-errors
say step_06_cp_rc "$?"
say step_07_gz "$(stat -c%s "$W/r1.fq.gz" 2>/dev/null || echo MISSING)"
say step_08_df_root_after_dl "$(df -h / | awk 'NR==2{print $4" avail, "$5" used"}')"
zcat "$W/r1.fq.gz" | sed -n '1,4000000p;4000001q' > /mnt/nvme/reads1m.fq 2>/dev/null
say step_09_zcat_sed_rc "$?"
say step_10_reads1m_lines "$(wc -l < /mnt/nvme/reads1m.fq 2>/dev/null | tr -d ' ' || echo MISSING)"
sed -n '1,400000p' /mnt/nvme/reads1m.fq > /mnt/nvme/reads100k.fq; say step_11_100k "$?"
sed -n '1,40000p'  /mnt/nvme/reads1m.fq > /mnt/nvme/reads10k.fq;  say step_12_10k  "$?"
rm -f "$W/r1.fq.gz"; say step_13_rm_rc "$?"
for f in 10k 100k 1m; do
  say "step_14_reads_$f" "$(( $(wc -l < /mnt/nvme/reads$f.fq 2>/dev/null || echo 0) / 4 ))"
done
say step_15_df_final "$(df -h / /mnt/nvme | awk 'NR>1{print $6"="$4" "}' | tr -d '\n')"
say step_16_mem "$(free -g | awk '/^Mem:/{print "avail "$7" GiB"}')"
say DONE yes
