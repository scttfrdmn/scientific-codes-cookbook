#!/usr/bin/env bash
# Task 01 (clair3 image, every chip): Clair3 on all of HG002's chr20 from 49x PacBio HiFi Revio,
# with the bundled hifi_revio model. Writes the VCF and the evidence to /tmp/out.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
THREADS=16
machine

DIR="$(dirname "$(command -v run_clair3.sh)")"
rec clair3_version "$(run_clair3.sh --version 2>&1 | head -1)"
ls "$DIR/models" > "$O/models.txt" 2>&1
MODEL="$DIR/models/hifi_revio"
chk "hifi_revio model bundled in the image" "$([ -d "$MODEL" ] && echo yes)" yes
[ -d "$MODEL" ] || finish CALL

# not emulated: the interpreter Clair3 runs under is built for this host (aarch64 183, x86_64 62)
em(){ od -An -tu2 -j18 -N2 "$1" | tr -d ' '; }
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=unknown ;; esac
chk "python3 ELF e_machine == uname -m" "$(em "$(command -v python3)")" "$WANT"
# describe the numerics stack; a line that only describes the run must never fail it
python3 -u - > "$O/stack.txt" 2>&1 <<'PY' || true
import ctypes, glob, os, sys
for m in ("torch", "numpy", "tensorflow"):
    try:
        mod = __import__(m); print(m, str(getattr(mod, "__version__", "unknown")))
        if m == "torch":
            print("torch_threads", str(mod.get_num_threads())); print(str(mod.__config__.show()))
    except Exception as e:
        print(m, "absent:", str(e))
for lib in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))[:1]:
    try:
        f = ctypes.CDLL(lib).openblas_get_corename; f.restype = ctypes.c_char_p; print("openblas_corename", f().decode())
    except Exception as e:
        print("openblas_corename unavailable:", str(e))
PY
cat "$O/stack.txt"

tic
run_clair3.sh --bam_fn=hifi.chr20.bam --ref_fn=chr20.fa --threads=$THREADS --platform=hifi \
  --model_path="$MODEL" --ctg_name=chr20 --output="$W/clair3_out" > "$O/clair3.log" 2>&1
RC=$?
toc clair3
chk "run_clair3.sh exit" "$RC" 0
cp "$W/clair3_out/run_clair3.log" "$O/run_clair3.log" 2>/dev/null
VCF="$W/clair3_out/merge_output.vcf.gz"
[ -s "$VCF" ] || { tail -40 "$O/clair3.log"; finish CALL; }
cp "$VCF" "$O/clair3.vcf.gz"; cp "$VCF.tbi" "$O/clair3.vcf.gz.tbi"

# structure: merge_output is a decision log (PASS variants plus RefCall rows), one sample, chr20 only
gzip -dc "$O/clair3.vcf.gz" | awk -F'\t' -v o="$O/vcf.kv" '
  /^#CHROM/ {ns=NF-9} /^#/ {next}
  {n++; if ($1!="chr20") off++; f[$7]++
   split($10, s, ":"); g=s[1]; gsub(/\|/, "/", g)
   if ($7=="PASS") { if (g !~ /[1-9]/) passref++; if (length($4)==1 && $5 ~ /^[ACGT](,[ACGT])*$/) snv++; else indel++ } }
  END {printf "records\t%d\nsamples\t%d\noff_chr20\t%d\npass\t%d\nrefcall\t%d\npass_without_alt_allele\t%d\npass_snv\t%d\npass_indel\t%d\n",
       n, ns, off, f["PASS"], f["RefCall"], passref, snv, indel > o
       for (k in f) if (k!="PASS" && k!="RefCall") printf "filter_other_%s\t%d\n", k, f[k] > o }'
cat "$O/vcf.kv" >> "$O/results.tsv"; cat "$O/vcf.kv"
kv(){ awk -F'\t' -v k="$1" '$1==k{print $2}' "$O/vcf.kv"; }
chk "sample columns" "$(kv samples)" 1
chk "records off chr20" "$(kv off_chr20)" 0
chk "PASS rows that carry no ALT allele" "$(kv pass_without_alt_allele)" 0
# every row is one of Clair3's three FILTER values, and those three account for every record
chk "rows with a FILTER outside PASS/LowQual/RefCall" "$(awk -F'\t' '$1 ~ /^filter_other_/ && $1 != "filter_other_LowQual" {n+=$2} END{print n+0}' "$O/vcf.kv")" 0
chk "records == PASS + LowQual + RefCall" "$(kv records)" "$(( $(kv pass) + $(kv refcall) + $(kv filter_other_LowQual | grep . || echo 0) ))"
chk "PASS calls > 50,000 on a 49x chromosome" "$([ "$(kv pass)" -gt 50000 ] && echo yes)" yes
rec vcf_body_md5 "$(gzip -dc "$O/clair3.vcf.gz" | grep -v '^#' | md5)"
rec vcf_sites_md5 "$(gzip -dc "$O/clair3.vcf.gz" | grep -v '^#' | awk -F'\t' '$7=="PASS"{split($10,s,":"); print $1,$2,$4,$5,s[1]}' | md5)"
finish CALL
