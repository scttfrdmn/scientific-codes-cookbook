B="cookbook-942542972736-us-west-2"
W=/tmp/w; mkdir -p "$W"; R="$W/s.txt"; : > "$R"
# FIRST LINE OF REAL WORK: what shell options did spawn hand us, BEFORE we touch them?
INHERITED="$-"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/shellopt.txt" --only-show-errors 2>/dev/null || true; }
say inherited_dollar_dash "$INHERITED"
case "$INHERITED" in *e*) say errexit_inherited "YES -- a failing command kills the script" ;;
                     *)   say errexit_inherited "no" ;; esac
say shell "$(ps -o comm= -p $$) / BASH_VERSION=${BASH_VERSION:-none}"
# now reproduce the exact failure both ways
set -uo pipefail
say with_pipefail_only_dollar_dash "$-"
seq 1 200000 | gzip > "$W/t.gz"
zcat "$W/t.gz" | sed -n '1,1000p;1001q' > "$W/a" 2>/dev/null
say A_survived_sigpipe_with_inherited_flags "rc=$? (if you can read this line, it did NOT kill us)"
set +e
zcat "$W/t.gz" | sed -n '1,1000p;1001q' > "$W/b" 2>/dev/null
say B_survived_with_set_plus_e "rc=$?"
say DONE yes
