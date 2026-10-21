#!/usr/bin/env bash
# The x86 legs (Intel m8i.8xlarge, AMD m8a.8xlarge): routes D (lith mount), A (copy -> EBS) and
# A2 (copy -> tmpfs) on one box, by the same four-data-paths.sh and tmpfs-leg.sh as Graviton4.
# EFS and FSx are not attached, so four-data-paths.sh skips routes B and C by its own mountpoint test.
#   cross-chip-driver.sh <bucket> <tag: x86_64|amd>   (run as the login user by `spawn launch --command`)
# Expects four-data-paths.sh, tmpfs-leg.sh and their pins.sha256 under s3://<bucket>/scripts/star-real/.
set +e
B=${1:?bucket}; TAG=${2:?tag}
cd /tmp
sudo dnf install -y -q docker fuse fuse3 >/dev/null 2>&1
mkdir -p sr
aws s3 cp "s3://$B/scripts/star-real/" sr/ --recursive --only-show-errors
(cd sr && sha256sum -c pins.sha256) > /tmp/pins.txt 2>&1 || { cat /tmp/pins.txt; aws s3 cp /tmp/pins.txt "s3://$B/measurements/star-real/$TAG/pins-FAILED.txt"; exit 1; }
{ echo "instance_type $(TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60'); curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-type)"
  echo "model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "nproc_online $(getconf _NPROCESSORS_ONLN)"
  echo "threads_per_core $(lscpu | awk -F: '/[Tt]hread\(s\) per core/{gsub(/ /,"",$2); print $2}')"
  echo "uname_m $(uname -m)"
  df -h /tmp / ; } > /tmp/topo.txt 2>&1
aws s3 cp /tmp/topo.txt "s3://$B/measurements/star-real/$TAG/topo.txt" --only-show-errors
T0=$(date +%s)
sudo env COOKBOOK_BUCKET=$B STAR_REAL_TAG=$TAG bash sr/four-data-paths.sh > /tmp/fdp.log 2>&1
T1=$(date +%s)
sudo env COOKBOOK_BUCKET=$B STAR_REAL_TAG=$TAG bash sr/tmpfs-leg.sh > /tmp/tl.log 2>&1
T2=$(date +%s)
echo "four_data_paths_s $((T1-T0)) tmpfs_leg_s $((T2-T1))" >> /tmp/topo.txt
sudo dmesg | grep -i -E 'oom|killed process' >> /tmp/topo.txt 2>&1
for f in topo.txt fdp.log tl.log; do aws s3 cp /tmp/$f "s3://$B/measurements/star-real/$TAG/driver-$f" --only-show-errors; done
exit 0
