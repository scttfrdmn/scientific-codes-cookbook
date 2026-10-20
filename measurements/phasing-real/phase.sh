#!/usr/bin/env bash
# Task 01 (whatshap image, every chip): read-backed phasing of all of HG002's chr20 benchmark calls,
# once from PacBio HiFi and once from Illumina 2x250, scored against GIAB's two phased truth sets.
# The two whatshap processes run side by side, one core each (whatshap phase is single-threaded).
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
# whatshap names supplementary alignments by str hash(); pin it so nothing varies run to run but the chip
export PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
machine
rec whatshap_version "$(whatshap --version 2>&1)"
rec python_version "$(python3 --version 2>&1)"
rec pysam_version "$(python3 -c 'import pysam; print(getattr(pysam, "__version__", "unknown"))' 2>&1)"
# not emulated: whatshap's compiled core is built for the chip it runs on (ELF e_machine 183 / 62)
chk "whatshap core .so architecture == uname -m" "$(python3 -c '
import whatshap.core as c
m = int.from_bytes(open(c.__file__, "rb").read(20)[18:20], "little")
print({183: "aarch64", 62: "x86_64"}.get(m, m))' 2>&1)" "$(uname -m)"

tic
whatshap unphase bench.chr20.vcf.gz > "$W/input.vcf" 2> "$O/unphase.log"
chk "unphase exit" "$?" 0
toc unphase

phase(){ # name bam [extra args]
  n=$1; b=$2; shift 2
  ( s=$(date +%s)
    whatshap phase "$@" --reference chr20.fa -o "$O/$n.vcf.gz" "$W/input.vcf" "$b" > "$O/$n.log" 2>&1
    echo $? > "$W/$n.rc"; echo $(( $(date +%s) - s )) > "$W/$n.wall" ) &
}
phase hifi hifi.chr20.bam
P1=$!
phase ilmn ilmn.chr20.bam --ignore-read-groups   # novoalign's BAM has no @RG; one sample, so nothing to match
P2=$!
wait "$P1" "$P2"
for n in hifi ilmn; do
  chk "$n whatshap phase exit" "$(cat "$W/$n.rc")" 0
  printf 'phase_%s\t%s\n' "$n" "$(cat "$W/$n.wall")" >> "$O/timings.tsv"
  rec "${n}_wall_s" "$(cat "$W/$n.wall")"
  # whatshap's own peak, from getrusage ru_maxrss
  rec "${n}_max_memory_gb" "$(awk '/^Maximum memory usage:/{print $4}' "$O/$n.log")"
  rec "${n}_reads_covering_variants" "$(awk '/^Found [0-9]+ reads covering/{print $2}' "$O/$n.log")"
  rec "${n}_reads_kept_2plus_variants" "$(awk '/^Kept [0-9]+ reads that cover at least two/{print $2}' "$O/$n.log")"
  rec "${n}_reads_selected" "$(awk '/^Selected [0-9]+ most phase-informative/{print $2}' "$O/$n.log")"
  rec "${n}_largest_block_line" "$(grep '^Largest block contains' "$O/$n.log")"
  grep -c 'more than two primary alignments' "$O/$n.log" | { read c; rec "${n}_warn_multi_primary" "$c"; }
  tic
  whatshap stats --tsv "$O/stats.$n.tsv" --block-list "$O/blocks.$n.tsv" "$O/$n.vcf.gz" > "$O/stats.$n.txt" 2>&1
  chk "$n whatshap stats exit" "$?" 0
  toc "stats_$n"
  python3 -u -I check.py flip "$O/$n.vcf.gz" "$W/$n.flip.vcf" > "$W/$n.flip.txt"
  chk "$n flip exit" "$?" 0
  rec "${n}_flipped_genotypes" "$(awk '{print $2}' "$W/$n.flip.txt")"
done

# GIAB's two phasings: hifiasm_v11_phasetransfer (trio-binned assembly; sample INTEGRATION, hence
# --ignore-sample-name) and StrandSeqANDTrio (conservative). Both are paternal|maternal.
cmp(){ # tsv-out hifi-vcf ilmn-vcf
  whatshap compare --ignore-sample-name --names hifiasm,trio_strandseq,hifi,illumina \
    --tsv-pairwise "$1.tsv" --tsv-multiway "$1.multiway.tsv" \
    hifiasm.chr20.vcf.gz sst.chr20.vcf.gz "$2" "$3" > "$1.txt" 2>&1
}
tic
cmp "$O/compare" "$O/hifi.vcf.gz" "$O/ilmn.vcf.gz"
chk "whatshap compare exit" "$?" 0
toc compare
cmp "$O/compare.flip" "$W/hifi.flip.vcf" "$W/ilmn.flip.vcf"
chk "whatshap compare (flipped) exit" "$?" 0

python3 -u -I check.py check "$O" "$W/input.vcf"
chk "check.py exit" "$?" 0
finish "PHASE"
