#!/usr/bin/env bash
# 00-stage, on a spawn host: fetch the published WDL, Broad's calling regions and Docker's static
# CLI into /tmp/out (staged to inputs/cromwell-wdl-real/), each held to pins.sha256. Nothing here
# passes through the machine that launched it.
set +e
cd /tmp && mkdir -p /tmp/out
WDL=https://raw.githubusercontent.com/gatk-workflows/gatk4-germline-snps-indels/2.3.1/haplotypecaller-gvcf-gatk4.wdl
curl -sfL "$WDL" -o /tmp/out/haplotypecaller-gvcf-gatk4.wdl
aws s3 cp --no-sign-request --only-show-errors s3://broad-references/hg38/v0/wgs_calling_regions.hg38.interval_list /tmp/out/
for a in aarch64 x86_64; do
  curl -sfL "https://download.docker.com/linux/static/stable/$a/docker-27.5.1.tgz" -o "/tmp/out/docker-27.5.1-$a.tgz"
done
( cd /tmp/out && grep -v '\.bam$' /tmp/pins.sha256 | sha256sum -c - ) | tee /tmp/out/fetch.log
test "${PIPESTATUS[0]}" = 0 && echo "FETCH OK" | tee -a /tmp/out/fetch.log || { echo "FAIL: a fetched file does not match its pin"; exit 1; }
