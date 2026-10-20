#!/usr/bin/env bash
# Task 00: choose the genomes (choose.py) and stage what it wrote, logs included.
#   choose.sh <N> <SUB> <seed>
set +e
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
cd /tmp
mkdir -p out
: > out/choose.log
python3 -u choose.py "$@" > out/choose.log 2>&1
RC=$?
cat out/choose.log
exit $RC
