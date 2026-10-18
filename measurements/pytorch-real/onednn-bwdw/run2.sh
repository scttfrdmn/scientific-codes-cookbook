set +e
set -uo pipefail
export PATH=$PATH:/opt/conda/bin TMPDIR=/tmp HOME=/tmp
cd /tmp
sha256sum -c pins2.sha256 || { echo "FAIL: staged bytes do not match their pins"; exit 1; }
python3 -m pip install --no-deps --no-index --target /tmp/ptw /tmp/torch-2.14.1+cpu-cp314-cp314-manylinux_2_28_aarch64.whl > pip.log 2>&1
: > time.log
python3 -u convtime.py conda-forge >> time.log 2>&1
PYTHONPATH=/tmp/ptw python3 -s -u convtime.py official-wheel >> time.log 2>&1
ONEDNN_VERBOSE=1 python3 -u convtime.py conda-forge > verbose.log 2>&1
PYTHONPATH=/tmp/ptw ONEDNN_VERBOSE=1 python3 -s -u convtime.py official-wheel >> verbose.log 2>&1
cat time.log
echo "== implementations seen (count by build line order) =="
grep -E 'primitive,exec,cpu,convolution' verbose.log | cut -d, -f7,8 | sort | uniq -c
test "$(grep -c '^TIME OK' time.log)" = 2 || { echo "FAIL: a timing run did not finish"; exit 1; }
echo DONE
