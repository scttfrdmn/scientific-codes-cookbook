# Sourced (not run) at the top of an x86_64 task whose recipe has `env_x86_64:` -- scripts/spec_arch.py
# adds the `.` line and checks this file's sha256 first. It installs the aarch.science env's linux-64
# twin OFFLINE from staged, lock-verified packages (envs-x86_64/<name>.linux-64.lock; same versions
# as arm64), then does what `conda activate` would. It lives here rather than inline because spawn
# carries the whole task script in EC2 user data, which is capped at 16 KiB (spawn#1063).
export MAMBA_ROOT_PREFIX=/var/tmp/mamba
_T0=$(date +%s)
# micromamba URL-decodes a file:// entry both to parse the package and to open the file, so the
# entry keeps the URL's own %-encoding (x264-1%21164...) and stage_env_x86.sh stores the file under
# its DECODED name (x264-1!164...). Escaping % instead breaks the version parse (measured: an empty
# log and exit 1 under -q).
awk -v d=/tmp/xenv-pkgs '/^https?:/{n=split($0,a,"/"); split(a[n],b,"#"); print "file://" d "/" b[1] "#" b[2]; next} {print}' /tmp/xenv.lock > /var/tmp/xenv.local.lock
if ! micromamba create -y -q -p /var/tmp/env --offline -f /var/tmp/xenv.local.lock > /var/tmp/xenv-install.log 2>&1; then
  echo "FAIL: the x86 env twin did not install from its staged packages"; tail -25 /var/tmp/xenv-install.log; exit 1
fi
echo "x86 env twin: $(grep -c '^https' /tmp/xenv.lock) packages installed offline in $(( $(date +%s) - _T0 ))s"
export PATH=/var/tmp/env/bin:$PATH CONDA_PREFIX=/var/tmp/env
# packages set their variables in activate.d (cmdstan's CMDSTAN, GDAL_DATA, PROJ_DATA, CC/CXX),
# which an install alone never runs
for _f in /var/tmp/env/etc/conda/activate.d/*.sh; do [ -r "$_f" ] && . "$_f"; done
