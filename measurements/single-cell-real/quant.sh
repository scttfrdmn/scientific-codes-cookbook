# Task 2 of 2 -- alevin-fry: raw matrix (10x v2 permit list), knee-called cells, and the filtered
# matrix cut from the raw one. Staged as a pinned file. Writes /tmp/quant.txt.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp LC_ALL=C
cd /tmp
LBL="${LBL:-$(uname -m)}"; THREADS="${THREADS:-$(nproc)}"
WL=737K-august-2016.txt
META=tabula-muris-senis-full-metadata.csv
CHANNEL="${CHANNEL:-10X_P5_13}"
OUT=/tmp/quant.txt
: > "$OUT"
rec(){ printf '%s\t%s\n' "$1" "$2" | tee -a "$OUT"; }
die(){ rec FAIL "$1"; exit 1; }

[ -s pins-quant.sha256 ] || die "no pins-quant.sha256 staged"; {
  sha256sum -c pins-quant.sha256 > pins.log 2>&1 || { cat pins.log; die "input sha256 mismatch"; }
  rec inputs_sha256 "all $(wc -l < pins-quant.sha256 | tr -d ' ') match pins-quant.sha256"
}

CG=/sys/fs/cgroup
( while :; do
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" \
      "$(awk '/^anon /{print $2}' $CG/memory.stat 2>/dev/null || echo 0)" \
      "$(awk '/^shmem /{print $2}' $CG/memory.stat 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null || echo 0)" >> /tmp/samp.tsv
    sleep 1
  done ) & SP=$!
trap 'kill $SP 2>/dev/null' EXIT

rec label "$LBL"; rec arch "$(uname -m)"; rec nproc "$(nproc)"; rec threads "$THREADS"
rec alevin_fry_version "$(alevin-fry --version 2>&1 | tr '\n' ' ')"
test -s rad/map.rad || die "no staged rad/map.rad"

fry(){ # $1 = label, rest = generate-permit-list mode args
  local n=$1; shift
  alevin-fry generate-permit-list -d fw -i rad -o qd_$n "$@" > gpl_$n.log 2>&1 || die "gpl $n failed"
  alevin-fry collate -i qd_$n -r rad -t "$THREADS" > col_$n.log 2>&1 || die "collate $n failed"
  alevin-fry quant -i qd_$n -o res_$n -m t2g.tsv -r cr-like -t "$THREADS" > q_$n.log 2>&1 || die "quant $n failed"
}
T0=$(date +%s)
fry raw --unfiltered-pl "$WL"            # every whitelisted barcode with >= 10 reads (the default)
T1=$(date +%s)
fry knee --knee-distance                 # alevin-fry's own cell call
T2=$(date +%s)
# filtered = the knee's cells, quantified from the RAW collation: the Cell Ranger raw->filtered shape
cp res_knee/alevin/quants_mat_rows.txt cells_knee.txt
alevin-fry quant -i qd_raw -o res_filt -m t2g.tsv -r cr-like -t "$THREADS" --quant-subset cells_knee.txt \
  > q_filt.log 2>&1 || die "quant filt failed"
T3=$(date +%s)
kill $SP 2>/dev/null
rec raw_wall_s "$((T1-T0))"; rec knee_wall_s "$((T2-T1))"; rec filt_wall_s "$((T3-T2))"; rec fry_wall_s "$((T3-T0))"
awk -F'\t' '{if($2+0>a)a=$2+0; if($3+0>s)s=$3+0} NR==1{u0=$4;t0=$1} {u=$4;t=$1}
  END{printf "peak_anon_gib\t%.2f\npeak_shmem_gib\t%.2f\navg_cores\t%.2f\n", a/2^30, s/2^30, (u-u0)/1e6/(t-t0)}' /tmp/samp.tsv | tee -a "$OUT"

# (barcode, gene, count) triples, sorted -- the matrix as data, independent of row/column order
triples(){ awk 'NR==FNR{r[FNR]=$1; next} FILENAME==ARGV[2]{g[FNR]=$1; next}
                /^%/{next} !h{h=1; next} {print r[$1]"\t"g[$2]"\t"$3}' \
             "$1/alevin/quants_mat_rows.txt" "$1/alevin/quants_mat_cols.txt" "$1/alevin/quants_mat.mtx" | sort > "$1.triples"; }
fails=0
check(){ # $1 = res dir: per-cell totals from the matrix must equal quant's own featureDump
  local r=$1
  triples "$r"
  local nrow; nrow=$(wc -l < "$r/alevin/quants_mat_rows.txt" | tr -d ' ')
  local hdr; hdr=$(awk '/^%/{next} {print $1, $2, $3; exit}' "$r/alevin/quants_mat.mtx")
  local nnz; nnz=$(wc -l < "$r.triples" | tr -d ' ')
  local fdrows; fdrows=$(awk 'NR>1' "$r/featureDump.txt" | wc -l | tr -d ' ')
  # per-cell: row sum and nnz from the matrix vs DeduplicatedReads / NumGenesExpressed in featureDump
  awk -F'\t' 'NR==FNR{s[$1]+=$3; z[$1]+=($3>0); next} FNR==1{for(i=1;i<=NF;i++)c[$i]=i; next}
       { n++; if (s[$1]+0 != $c["DeduplicatedReads"]+0) bs++; if (z[$1]+0 != $c["NumGenesExpressed"]+0) bz++;
         tu+=$c["DeduplicatedReads"]; tm+=$c["MappedReads"]; tc+=$c["CorrectedReads"]; ts+=s[$1] }
       END{ printf "%d %d %d %d %.0f %.0f %.0f %.0f\n", n, bs+0, bz+0, bm+0, tu, tm, tc, ts }' \
       "$r.triples" "$r/featureDump.txt" > "$r.pc"
  read -r N BS BZ BM TU TM TC TS < "$r.pc"
  rec "${r}_cells" "$N"
  rec "${r}_mtx_header" "$hdr"
  rec "${r}_umis_matrix" "$TS"
  rec "${r}_umis_featuredump" "$TU"
  rec "${r}_mapped_reads" "$TM"
  rec "${r}_corrected_reads" "$TC"
  rec "${r}_cells_rowsum_ne_dedup" "$BS"
  rec "${r}_cells_nnz_ne_genes" "$BZ"
  rec "${r}_triples_sha256" "$(sha256sum < "$r.triples" | cut -c1-64)"
  [ "$N" -eq "$nrow" ] && [ "$N" -eq "$fdrows" ] || { rec CHECKFAIL "$r: featureDump $fdrows / rows $nrow / matched $N"; fails=$((fails+1)); }
  [ "$(echo "$hdr" | cut -d' ' -f1)" -eq "$nrow" ] && [ "$(echo "$hdr" | cut -d' ' -f3)" -ge "$nnz" ] || { rec CHECKFAIL "$r: mtx header $hdr vs rows $nrow nnz $nnz"; fails=$((fails+1)); }
  [ "$BS" -eq 0 ] || { rec CHECKFAIL "$r: $BS cells whose matrix row sum != DeduplicatedReads"; fails=$((fails+1)); }
  [ "$BZ" -eq 0 ] || { rec CHECKFAIL "$r: $BZ cells whose nnz != NumGenesExpressed"; fails=$((fails+1)); }
  [ "$TS" = "$TU" ] || { rec CHECKFAIL "$r: matrix total $TS != featureDump total $TU"; fails=$((fails+1)); }
}
check res_raw; check res_knee; check res_filt

# ---- unmapped reads: piscem's own file, decoded here, brackets featureDump's CorrectedReads ----
# piscem 0.23.0 writes unmapped_bc_count.bin as a 3-byte header (version 1, 1 field, type 3 = u32),
# then (u32 barcode, u32 count) records, little-endian, 2 bits/base with the first base high (the
# recipe's fixture checks this decoder against planted counts). alevin-fry adds the unmapped reads
# of every raw barcode it corrects to a cell into that cell's CorrectedReads, so per cell
#   CorrectedReads - MappedReads  >=  unmapped reads whose raw barcode IS the cell   (exact matches)
# and, summed over cells, it can be no more than every unmapped read in the file. alevin-fry 0.9.0
# misread this file (as headerless (u64, u32) pairs) and fails the lower bound.
UB=rad/unmapped_bc_count.bin
UHDR=$(od -An -v -tu1 -N3 "$UB" | tr -s ' ' | sed 's/^ //; s/ $//')
rec unmapped_bin_header "$UHDR"
[ "$UHDR" = "1 1 3" ] || { rec CHECKFAIL "unmapped_bc_count.bin header is '$UHDR', not '1 1 3'"; fails=$((fails+1)); }
od -An -v -tu4 -j3 "$UB" | awk '{for(i=1;i<=NF;i++){ if(h){c[b]+=$i; h=0} else {b=$i; h=1} }}
  END{ if(h){print "ODD"; exit} for(v in c){ s=""; for(p=15;p>=0;p--) s=s substr("ACGT", int(v/2^(2*p))%4+1, 1); print s"\t"c[v] } }' > bin_unmapped.tsv
UTOT=$(awk -F'\t' '{s+=$2} END{printf "%.0f", s}' bin_unmapped.tsv)
rec unmapped_bin_barcodes "$(wc -l < bin_unmapped.tsv | tr -d ' ')"
rec unmapped_bin_reads "$UTOT"
grep -q ODD bin_unmapped.tsv && { rec CHECKFAIL "unmapped_bc_count.bin is not whole (u32, u32) records"; fails=$((fails+1)); }
# the two halves of piscem's own read count: mapped + unmapped-with-a-valid-barcode <= reads processed
NREADS_P=$(awk -F: '$1 ~ /"num_reads"/ {gsub(/[ ",]/,"",$2); print $2; exit}' rad/map_info.json)
NMAPPED_P=$(awk -F: '$1 ~ /"num_mapped"/ {gsub(/[ ",]/,"",$2); print $2; exit}' rad/map_info.json)
[ "$UTOT" -gt 0 ] && [ $((NMAPPED_P + UTOT)) -le "$NREADS_P" ] \
  || { rec CHECKFAIL "unmapped reads $UTOT + mapped $NMAPPED_P vs piscem's $NREADS_P reads"; fails=$((fails+1)); }
uncheck(){ # $1 = res dir
  awk -F'\t' 'NR==FNR{e[$1]=$2; next} FNR==1{for(i=1;i<=NF;i++)c[$i]=i; next}
    { n++; u=$c["CorrectedReads"]-$c["MappedReads"]; x=e[$1]+0
      if (u >= x) ge++; su+=u; sx+=x
      r=$c["MappedReads"]/$c["CorrectedReads"]; d=$c["MappingRate"]-r; if(d<0)d=-d; if (d<=1e-6) rok++ }
    END{ printf "%d %d %d %.0f %.0f\n", n, ge+0, rok+0, su, sx }' bin_unmapped.tsv "$1/featureDump.txt"; }
for n in raw knee filt; do
  read -r UN UGE UROK USU USX < <(uncheck res_$n)
  rec "res_${n}_unmapped_in_corrected" "$USU"
  rec "res_${n}_unmapped_exact_barcode" "$USX"
  rec "res_${n}_cells_corrected_ge_exact" "$UGE of $UN"
  rec "res_${n}_cells_mappingrate_consistent" "$UROK of $UN"
  [ "$UGE" -eq "$UN" ] || { rec CHECKFAIL "res_$n: $((UN-UGE)) cells credit fewer unmapped reads than piscem wrote for their own barcode"; fails=$((fails+1)); }
  [ "$UROK" -eq "$UN" ] || { rec CHECKFAIL "res_$n: $((UN-UROK)) cells whose MappingRate != MappedReads/CorrectedReads"; fails=$((fails+1)); }
  [ "$USX" -gt 0 ] || { rec CHECKFAIL "res_$n: no unmapped read carries a cell's own barcode -- decoder or file wrong"; fails=$((fails+1)); }
  awk -v a="$USU" -v b="$UTOT" 'BEGIN{exit !(a <= b)}' \
    || { rec CHECKFAIL "res_$n: $USU unmapped reads credited, the file holds $UTOT"; fails=$((fails+1)); }
done

# ---- read conservation across the tool boundary: piscem wrote N records, alevin-fry read N ----
NMAPPED=$(awk -F: '$1 ~ /"num_mapped"/ {gsub(/[ ",]/,"",$2); print $2; exit}' rad/map_info.json)
OBS_RAW=$(awk '/ observed [0-9,]+ reads/{for(i=1;i<NF;i++) if($i=="observed"){v=$(i+1); gsub(/,/,"",v); print v; exit}}' gpl_raw.log)
OBS_KNEE=$(awk '/ observed [0-9,]+ reads/{for(i=1;i<NF;i++) if($i=="observed"){v=$(i+1); gsub(/,/,"",v); print v; exit}}' gpl_knee.log)
PROC(){ awk '/processed [0-9,]+ total read records/{for(i=1;i<NF;i++) if($i=="processed"){v=$(i+1); gsub(/,/,"",v); print v; exit}}' "$1"; }
rec piscem_records "$NMAPPED"; rec gpl_observed_raw "$OBS_RAW"; rec gpl_observed_knee "$OBS_KNEE"
for n in raw knee filt; do
  P=$(PROC q_$n.log); C=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++)c[$i]=i; next} {s+=$c["MappedReads"]} END{printf "%.0f", s}' res_$n/featureDump.txt)
  rec "quant_${n}_records" "$P"
  [ "$P" = "$C" ] || { rec CHECKFAIL "quant $n processed $P records, featureDump MappedReads sum $C"; fails=$((fails+1)); }
done
[ "$OBS_RAW" = "$NMAPPED" ] && [ "$OBS_KNEE" = "$NMAPPED" ] \
  || { rec CHECKFAIL "piscem wrote $NMAPPED records, generate-permit-list saw $OBS_RAW / $OBS_KNEE"; fails=$((fails+1)); }
ORI=$(awk '/orientation consistent/{for(i=1;i<NF;i++) if($i ~ /^\(/){v=substr($i,2); gsub(/,/,"",v); print v; exit}}' gpl_raw.log)
rec gpl_orientation_consistent "$ORI"
# the funnel only narrows: records written >= orientation-consistent >= quantified into raw >= into filtered
QR=$(PROC q_raw.log); QF=$(PROC q_filt.log)
[ "$NMAPPED" -ge "$ORI" ] && [ "$ORI" -ge "$QR" ] && [ "$QR" -ge "$QF" ] \
  || { rec CHECKFAIL "read funnel widens: $NMAPPED / $ORI / $QR / $QF"; fails=$((fails+1)); }

# ---- raw -> filtered: every filtered row is the raw row, byte for byte ----
cut -f1 res_raw/alevin/quants_mat_rows.txt | sort -u > raw.bc
sort -u cells_knee.txt > knee.bc
KIN=$(comm -12 knee.bc raw.bc | wc -l | tr -d ' ')
comm -12 knee.bc raw.bc > kin.bc
awk -F'\t' 'NR==FNR{k[$1]=1; next} ($1 in k)' kin.bc res_raw.triples > raw_cut.triples
rec knee_cells "$(wc -l < knee.bc | tr -d ' ')"
rec knee_cells_on_whitelist_and_in_raw "$KIN"
rec filt_umis_frac_of_raw "$(awk -v f="$(awk -F'\t' '{s+=$3} END{print s+0}' res_filt.triples)" -v r="$(awk -F'\t' '{s+=$3} END{print s+0}' res_raw.triples)" 'BEGIN{printf "%.4f", f/r}')"
[ "$(cut -f1 res_filt/alevin/quants_mat_rows.txt | sort -u | wc -l | tr -d ' ')" -eq "$KIN" ] \
  || { rec CHECKFAIL "filtered has $(wc -l < res_filt/alevin/quants_mat_rows.txt) cells, expected $KIN (knee cells present in raw)"; fails=$((fails+1)); }
if cmp -s raw_cut.triples res_filt.triples; then rec filt_equals_raw_rows "yes (every (cell,gene,count) of the filtered matrix is the raw matrix's)"
else rec CHECKFAIL "filtered rows differ from the raw rows they were cut from ($(comm -3 raw_cut.triples res_filt.triples | wc -l) lines)"; fails=$((fails+1)); fi

# ---- observation: the knee run vs the same cells cut from raw (different barcode correction) ----
awk -F'\t' 'NR==FNR{a[$1]+=$3; next} {b[$1]+=$3} END{for(c in b) if(c in a){n++; if(a[c]==b[c])e++; else if(b[c]>a[c])h++; else l++; da+=a[c]; db+=b[c]}
            printf "%d %d %d %d %.0f %.0f\n", n, e, h, l, da, db}' res_filt.triples res_knee.triples > kvf.txt
read -r KN KE KH KL KDA KDB < kvf.txt
rec knee_vs_filt "cells $KN: equal $KE, knee higher $KH, knee lower $KL; UMIs filt $KDA knee $KDB"

# ---- observation: the published Tabula Muris Senis cells for this channel ----
awk -F, -v ch="${CHANNEL}_" 'NR==1{for(i=1;i<=NF;i++)c[$i]=i; next} index($c["cell"],ch)==1{print substr($c["cell"],length(ch)+1)"\t"$c["n_counts"]}' "$META" | sort > pub.tsv
awk -F'\t' '{s[$1]+=$3} END{for(c in s) print c"\t"s[c]}' res_filt.triples | sort > ours.tsv
PUB=$(wc -l < pub.tsv | tr -d ' ')
pair(){ awk -F'\t' 'NR==FNR{p[$1]=$2; next} ($1 in p){print $1"\t"p[$1]"\t"$2}' "$1" "$2"; }   # no join: BusyBox x86 images lack it
pair pub.tsv ours.tsv > both.tsv
NB=$(wc -l < both.tsv | tr -d ' ')
rec published_cells "$PUB"; rec published_cells_in_our_knee_set "$NB"
# Spearman on per-cell UMI totals (ours vs the published n_counts), ranks with ties averaged
rk(){ sort -t "$(printf '\t')" -k"$1","$1"n both.tsv | awk -F'\t' -v k="$1" '{v[NR]=$k; id[NR]=$1}
       END{i=1; while(i<=NR){j=i; while(j<NR && v[j+1]==v[i]) j++; for(m=i;m<=j;m++) print id[m]"\t"(i+j)/2; i=j+1}}' | sort; }
rk 2 > r1.tsv; rk 3 > r2.tsv
pair r1.tsv r2.tsv | awk -F'\t' '{n++; x+=$2; y+=$3; xx+=$2*$2; yy+=$3*$3; xy+=$2*$3}
  END{ if(n>2) printf "%.4f\n", (n*xy-x*y)/sqrt((n*xx-x*x)*(n*yy-y*y)); else print "na"}' > rho.txt
rec spearman_umis_vs_published "$(cat rho.txt)"

# the tool's own logs out, raw matrix kept compact as its triples
tar -czf fry-logs.tgz gpl_*.log col_*.log q_*.log qd_*/*.json res_*/quant.json res_*/featureDump.txt
gzip -c res_filt.triples > filt.triples.gz
rec checks_failed "$fails"
[ "$fails" -eq 0 ] || die "$fails identity checks failed"
rec QUANT_OK "per-cell totals, matrix totals, and raw->filtered identities all exact"
