#!/usr/bin/env bash
# Runs on AWS (fetch.task.json), never on a laptop: stream the RELION 5.0 tutorial's
# precalculated results from the RELION authors' FTP server, hash the stream, and extract
# only the 40 files pinned in inputs.sha256 -- the 11.3 GB archive never touches disk.
#
# Source: relion50_tutorial_precalculated_results.tar.gz (11,326,844,944 B, 2023-10-26), the
# RELION 5.0 SPA tutorial's own results; its data is a 24-movie subset of EMPIAR-10204.
set +e
set -uo pipefail
cd /tmp
URL="ftp://ftp.mrc-lmb.cam.ac.uk/pub/scheres/relion50_tutorial_precalculated_results.tar.gz"
TARBALL_SHA256="7f799e1ad6c366722313a48a4e3f35d47311c896e92b985e78594bf6675711d3"

mkdir -p /tmp/stage
awk '{print "Tutorial5.0/" $2}' /tmp/inputs.sha256 > /tmp/members.txt
mkfifo /tmp/tap
sha256sum < /tmp/tap | cut -d' ' -f1 > /tmp/tarball.sha256 &
HPID=$!
T0=$(date +%s)
curl -fsS --retry 3 "$URL" | tee /tmp/tap | gzip -dc | tar xf - -C /tmp/stage -T /tmp/members.txt
RCS="${PIPESTATUS[*]}"
wait $HPID
echo "fetch_s	$(( $(date +%s) - T0 ))   (curl tee gzip tar exit: $RCS)"
[ "$RCS" = "0 0 0 0" ] || { echo "FAIL: stream or extraction failed"; exit 1; }

GOT=$(cat /tmp/tarball.sha256)
[ "$GOT" = "$TARBALL_SHA256" ] || { echo "FAIL: tarball sha256 $GOT != pinned $TARBALL_SHA256"; exit 1; }
echo "tarball sha256 matches its pin"

cd /tmp/stage/Tutorial5.0
sha256sum -c --quiet /tmp/inputs.sha256 || { echo "FAIL: an extracted file does not match its pin"; exit 1; }
echo "all $(wc -l < /tmp/inputs.sha256) extracted files match their pins"
N=$(awk '/^data_particles/{f=1} f&&/@Extract/{n++} END{print n+0}' Extract/job018/particles.star)
[ "$N" -eq 4452 ] || { echo "FAIL: particles.star lists $N particles, want 4452"; exit 1; }
echo "particles.star lists 4452 particles"
du -sh /tmp/stage/Tutorial5.0
echo "FETCH OK"
