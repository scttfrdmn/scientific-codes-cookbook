#!/usr/bin/env bash
# Runs on AWS (sesame-cache.task.json), once: the recipe's pinned ExperimentHub cache plus the one
# resource SeSAMe's inferSex needs that openSesame does not -- `probeIDSignature`, which
# sesameData_check_platform reads to infer the platform from probe IDs (inside mLiftOver, so passing
# platform= does not avoid it). Measured: the first real run died in inferSex with "File
# probeIDSignature either not found or needs to be cached".
#
# Output: one tar of the combined cache, and the sha256 of every non-sqlite blob in it. The pin is on
# that set (the recipe's rule: BiocFileCache names and sqlite timestamps are not reproducible).
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp LC_ALL=C
mkdir -p /tmp/h /tmp/stage
tar -xmf /tmp/sesame-cache.tar -C /tmp/h --no-same-owner --no-same-permissions
C=/tmp/h/.cache/R/ExperimentHub
printf '%s\n' 73578ce17c439acc8e1443f9a6c55c3f02fdcc18cdff287b7b93f74cb86c835e 7f361fdce9de733ab7b59461d89d950bcca3bf363cea999176f281ab7df2d982 99300ba1d45aec5d5fdcdb12b61923f7313b1600c4ec9c8b9ca573d09bfc1655 b4eb0da4ef492655c2fe18b45fa65e969967c9f4a0c2149a5fae9241b510c7b2 > /tmp/want.txt
( cd "$C" && for x in $(ls | grep -vE 'sqlite|LOCK'); do sha256sum "$x" | cut -d' ' -f1; done ) | sort > /tmp/got.txt
diff /tmp/want.txt /tmp/got.txt || { echo "FAIL: the recipe's cache is not its pinned content"; exit 1; }
HOME=/tmp/h Rscript -e 'suppressMessages(library(sesame)); sesameDataCache("probeIDSignature"); x <- sesameDataGet("probeIDSignature"); cat("probeIDSignature", class(x)[1], length(x), "\n")'
[ $? = 0 ] || { echo "FAIL: could not cache probeIDSignature"; exit 1; }
( cd "$C" && for x in $(ls | grep -vE 'sqlite|LOCK'); do echo "$(sha256sum "$x" | cut -d' ' -f1)  $x"; done ) | sort > /tmp/stage/sesame-cache-real.blobs
cat /tmp/stage/sesame-cache-real.blobs
tar -cf /tmp/stage/sesame-cache-real.tar -C /tmp/h .
ls -l /tmp/stage
echo "CACHE OK"
