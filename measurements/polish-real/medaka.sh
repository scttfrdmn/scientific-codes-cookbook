# medaka over the whole run on whichever chip this task landed on, then score the draft, this
# chip's racon, this chip's medaka and the paper's own Flye+Medaka against the independent truth.
#   bash medaka.sh <threads>
T=${1:?threads}
. /tmp/common.sh
O=/var/tmp/out/medaka; mkdir -p $O
host_info $O/host.txt
MODEL=/tmp/r1041_e82_400bps_sup_v4.3.0_model_pt.tar.gz
python3 -u - > $O/libs.txt 2>&1 <<'PYEOF'
# What the network runs on. Every line is guarded: describing the run must never fail it.
import ctypes, glob, sys
def rec(k, f):
    try:
        print(k, str(f()))
    except Exception as e:
        print(k, "unavailable (%s)" % type(e).__name__)
import medaka, torch
rec("medaka", lambda: getattr(medaka, "__version__", "unknown"))
rec("torch", lambda: torch.__version__)
rec("torch_threads", lambda: torch.get_num_threads())
rec("torch_mkldnn", lambda: torch.backends.mkldnn.is_available())
rec("torch_capability", lambda: torch.backends.cpu.get_cpu_capability())
for p in sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*"))[:1]:
    def core(p=p):
        lib = ctypes.CDLL(p); lib.openblas_get_corename.restype = ctypes.c_char_p
        return lib.openblas_get_corename().decode()
    rec("openblas_corename", core)
rec("torch_config", lambda: torch.__config__.show().replace("\n", " | "))
PYEOF
cat $O/libs.txt
python3 -u /tmp/run.py $O/medaka-time.txt medaka_consensus -i /tmp/ERR12813217.fastq.gz \
  -d /tmp/draft.fa -o /var/tmp/med -t "$T" -m "$MODEL" > $O/medaka.log 2>&1
cp /var/tmp/med/consensus.fasta $O/medaka.fa 2>/dev/null
# medaka_consensus prints this only after `medaka sequence` has stitched the consensus.
DONE=$(grep -c 'Polished assembly written to' $O/medaka.log)
{
  cat $O/medaka-time.txt | sed 's/^/medaka_/'
  echo "medaka_done $DONE"
  echo "model_used $(grep -o 'Using model: .*' $O/medaka.log | head -1)"
  echo "medaka_seqs $(grep -c '^>' $O/medaka.fa)"
  echo "medaka_sha256 $(sha256sum $O/medaka.fa | awk '{print $1}')"
} | tee $O/checks.txt
[ "$DONE" -ge 1 ] || { echo "FAIL: medaka did not finish"; tail -30 $O/medaka.log; exit 1; }
python3 -u /tmp/run.py $O/score-time.txt python3 -u /tmp/score.py "$T" $O \
  /tmp/truth.fa draft=/tmp/draft.fa racon=/tmp/racon.fa medaka=$O/medaka.fa paper=/tmp/paper-flye-medaka-100x.fa
RC=$?
cat $O/score.tsv
exit $RC
