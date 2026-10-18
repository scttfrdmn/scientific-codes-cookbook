set +e
set -uo pipefail
export PATH=$PATH:/opt/conda/bin TMPDIR=/tmp HOME=/tmp
cd /tmp
sha256sum -c pins.sha256 || { echo "FAIL: staged bytes do not match their pins"; exit 1; }
lscpu | grep -iE 'model name|^cpu\(s\)|flags' | cut -c1-300 > probe.log
grep -o 'sve[a-z0-9]*' /proc/cpuinfo | sort -u | tr '\n' ' ' >> probe.log; echo >> probe.log
echo "== conda-forge build (image) ==" >> probe.log
ONEDNN_VERBOSE=1 python3 -u convprobe.py conda-forge >> probe.log 2>&1
echo "== official PyTorch wheel ==" >> probe.log
python3 -m pip install --no-deps --no-index --target /tmp/ptw /tmp/torch-2.14.1+cpu-cp314-cp314-manylinux_2_28_aarch64.whl > pip.log 2>&1 || { tail pip.log >> probe.log; }
ls /tmp/ptw/torch.libs >> probe.log 2>&1
PYTHONPATH=/tmp/ptw ONEDNN_VERBOSE=1 python3 -s -u convprobe.py official-wheel >> probe.log 2>&1
cat probe.log
test "$(grep -c '^PROBE OK' probe.log)" = 2 || { echo "FAIL: a probe did not finish"; exit 1; }
echo DONE
