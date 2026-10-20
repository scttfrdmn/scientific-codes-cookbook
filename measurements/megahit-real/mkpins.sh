#!/usr/bin/env bash
# Rewrite pins.sha256: the two scripts, metaphlan-real's staged reads (their recorded sha256) and
# Zymo's reference zip (sha256 of the version-id copy).
set -euo pipefail
cd "$(dirname "$0")"
{
  shasum -a 256 assemble.sh evaluate.sh canon.py compare.py
  echo "c80c36586eb58a5bd3b571ec39e452f4374eba4c42b6ac00fa91e8fd38e6a0ed  ERR2984773_1.fastq.gz"
  echo "c93095578fdebecbe843c61df2b39377132289805e33d59f4d7a06ff46c96188  ERR2984773_2.fastq.gz"
  echo "188c21b88b083520110d9f8dc904188873e501366a55655635d7feed58aa573e  ERR2935805_1.fastq.gz"
  echo "101521521dc86fd9ecebcde86c5cccb7253e77901f6d870d70eee38403016df1  ERR2935805_2.fastq.gz"
  echo "a66d2085982647d8891d36059a7c996c85aee4eb79b8786071f3e3b87a787566  ZymoBIOMICS.STD.refseq.v2.zip"
} > pins.sha256
cat pins.sha256
