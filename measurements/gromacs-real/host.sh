# Host record, diagnostic only: run as its own process before the measurement, so nothing here
# can fail the run it describes. BusyBox images have no lscpu, so SMT is read from sysfs too.
set +e
echo "== host"
uname -m
grep -m1 'model name' /proc/cpuinfo
grep -m1 'CPU part' /proc/cpuinfo
echo "cpus_online $(getconf _NPROCESSORS_ONLN)"
echo "thread_siblings_cpu0 $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
lscpu 2>/dev/null | grep -E '^(Thread|Core|Socket|Model name)'
command -v python3 >/dev/null 2>&1 || { echo "openblas_corename n/a (no python3 in image)"; exit 0; }
python3 -u - <<'PY'
import ctypes, glob, os, sys
print("libblas_target", os.path.realpath(sys.prefix + "/lib/libblas.so.3"))
for p in sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*")):
    try:
        lib = ctypes.CDLL(p)
        lib.openblas_get_corename.restype = ctypes.c_char_p
        print("openblas_corename", lib.openblas_get_corename().decode(), p)
        break
    except Exception as e:
        print("openblas_err", p, str(e))
else:
    print("openblas_corename none (no libopenblas under %s)" % sys.prefix)
PY
exit 0
