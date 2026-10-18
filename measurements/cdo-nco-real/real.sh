# A year of hourly ERA5 2 m temperature through CDO and NCO, the way the operators are used:
# area-weighted global means, daily / monthly / annual / hour-of-day means, monthly anomalies, and a
# conservative regrid to 1 degree. Every product is computed by one tool from the staged year; the
# checks (check.py) compare the tools with each other, with xarray's run on the same bytes
# (measurements/climate-real), with closed forms and with C3S's published numbers.
# Run by the task after its pins verify, from /tmp. Per-job wall and peak RSS: rt.py -> times.tsv.
set +e
set -uo pipefail
cd /tmp
export TMPDIR=/tmp HOME=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
command -v cdo >/dev/null || export PATH=/opt/conda/bin:$PATH    # arm64 image; the x86 twin sets PATH itself
P=$(getconf _NPROCESSORS_ONLN)
F=$(ls /tmp/era5/*.nc | sort | tr '\n' ' ')
F1=$(ls /tmp/era5/*.nc | sort | head -1)
CSH=--cnk_csh=1000000000   # NCO's chunk cache: 1 GB holds a whole 27-hour slab of these files' chunks
{
  lscpu | grep -iE 'model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)'
  echo "uname_m: $(uname -m)"; echo "nproc_online: $P"
  cdo -V 2>&1 | head -1; ncks --version 2>&1 | tail -1
  grep MemTotal /proc/meminfo; df -h /tmp | tail -1
  echo "cdo links a BLAS: $(ldd "$(command -v cdo)" | grep -ciE 'blas')"
} > host.txt 2>&1
cat host.txt
rm -f times.tsv

# wave 0, once: the cell areas of both grids and the conservative-remap weights. fldmean is handed
# the areas (-setgridarea); left to itself, CDO 2.6.1 recomputes them every timestep (wave 3).
cat > w0.tsv <<EOF
gridarea	cdo -s -b F64 gridarea $F1 area.nc
gridarea_1deg	cdo -s -f nc -b F64 gridarea -const,1,r360x180 area1.nc
gencon	cdo -s gencon,r360x180 $F1 w.nc
EOF
python3 -u rt.py w0.tsv 3 times.tsv || { echo "FAIL: wave 0"; exit 1; }

# wave 1: every product that reads the whole year -- nine full reads, side by side
cat > w1.tsv <<EOF
cdo_fldmean	cdo -s -b F64 fldmean -setgridarea,area.nc -mergetime $F g.nc
cdo_remapcon_fldmean	cdo -s -b F64 fldmean -setgridarea,area1.nc -remap,r360x180,w.nc -mergetime $F gr.nc
cdo_timmean	cdo -s -b F64 timmean -mergetime $F ann.nc
cdo_monmean	cdo -s -b F64 monmean -mergetime $F mon.nc
cdo_daymean	cdo -s -b F64 daymean -mergetime $F day.nc
cdo_dhourmean	cdo -s -b F64 dhourmean -mergetime $F dhour.nc
nco_ncra_year	ncra -O --dbl $CSH $F ann_nco.nc
nco_ncra_daily	ncra -O --dbl $CSH --mro -d time,,,24,24 $F day_nco.nc
nco_ncra_monthly	i=0; for f in $F; do i=\$((i+1)); ncra -O --dbl $CSH \$f mon_nco_\$(printf %02d \$i).nc || exit 1; done; ncrcat -O mon_nco_??.nc mon_nco.nc
EOF
T0=$(date +%s)
python3 -u rt.py w1.tsv "$P" times.tsv || { echo "FAIL: wave 1"; exit 1; }
echo "wave 1: $(( $(date +%s) - T0 )) s"
df -h /tmp | tail -1

# wave 2: products of products (small inputs)
cat > w2.tsv <<EOF
cdo_anom_monthly	cdo -s -b F64 monmean -ymonsub day.nc mon.nc anom.nc
cdo_anom_control	cdo -s -b F64 monmean -ymonsub day.nc -shifttime,1month mon.nc anom_ctrl.nc
cdo_remapbil_annual	cdo -s -b F64 fldmean -setgridarea,area1.nc -remapbil,r360x180 ann.nc annbil_g.nc
cdo_annual_fldmean	cdo -s -b F64 fldmean -setgridarea,area.nc ann.nc ann_g.nc
cdo_monthly_fldmean	cdo -s -b F64 fldmean -setgridarea,area.nc mon.nc mon_g.nc
cdo_daily_fldmean	cdo -s -b F64 fldmean -setgridarea,area.nc day.nc day_g.nc
nco_ncwa_weighted	for x in ann mon; do cp \${x}_nco.nc \${x}_nco_w.nc && ncks -A -v cell_area area.nc \${x}_nco_w.nc && ncwa -O -w cell_area -a latitude,longitude \${x}_nco_w.nc \${x}_nco_g.nc || exit 1; done
EOF
python3 -u rt.py w2.tsv "$P" times.tsv || { echo "FAIL: wave 2"; exit 1; }

# wave 3: the two default spellings that are slow on these files, on January only. Both costs are
# per timestep, so they scale with the year; their answers must match the fast spellings' January.
cat > w3.tsv <<EOF
default_cdo_fldmean_jan	cdo -s -b F64 fldmean $F1 default_g_jan.nc
default_ncra_jan	ncra -O --dbl $F1 default_ncra_jan.nc
fast_cdo_fldmean_jan	cdo -s -b F64 fldmean -setgridarea,area.nc $F1 fast_g_jan.nc
EOF
python3 -u rt.py w3.tsv 3 times.tsv || { echo "FAIL: wave 3"; exit 1; }
cat times.tsv
