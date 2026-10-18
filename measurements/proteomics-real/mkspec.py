#!/usr/bin/env python3
"""Write the two arm64 TaskSpecs (search, rescore); the x86 ones come from scripts/spec_arch.py.

    python3 mkspec.py real   # search.task.json + rescore.task.json, all six runs
    python3 mkspec.py probe  # one run, for sizing; written to /tmp, not committed
"""
import json
import os
import sys

OPENMS = "quay.io/aarchbio/openms@sha256:8e9f3d4b0a80c509dc94229a2f02bfd45b6f4375eb6ec7d9e2032e08f4c0e725"
PERC = "quay.io/aarchbio/percolator@sha256:4cc39404eeba4f8093fdd380c1bd4f615d0e97c5e0efe4fe0e44bac63fbd6df6"
RUNS = "GFP_C001_1 GFP_C001_2 GFP_C001_3 MCL1_c007_1 MCL1_c007_2 MCL1_c007_3".split()
IN = "s3://${COOKBOOK_BUCKET}/inputs/proteomics-real/"
OUT = "s3://${COOKBOOK_BUCKET}/runs/proteomics-real/"
HERE = os.path.dirname(os.path.abspath(__file__))

PIN_CHECK = r'''echo "== staged bytes must match their pins =="
# Only the pinned files this task staged are checked (BusyBox sha256sum in the x86 image has no
# --ignore-missing), and every input the run list needs must be among them, or the filter passed
# vacuously.
while read -r h f; do test -e "$f" && printf '%s  %s\n' "$h" "$f"; done < pins.sha256 > pins.staged
sha256sum -c pins.staged || { echo "FAIL: a staged file does not match its pin"; exit 1; }
for f in __NEEDED__; do grep -q "  $f\$" pins.staged || { echo "FAIL: $f was not verified"; exit 1; }; done
echo "pinned_files_verified	$(wc -l < pins.staged | tr -d ' ')"
'''


def head(needed):
    return ("set +e\nset -uo pipefail\n"
            "# spawn runs task commands under bash -e; set -uo pipefail does not clear it (spawn#707).\n"
            "export PATH=/opt/conda/bin:$PATH\nexport TMPDIR=/tmp HOME=/tmp\ncd /tmp\nmkdir -p out\n"
            + PIN_CHECK.replace("__NEEDED__", needed)
            + "grep -m1 -iE 'model name|CPU part' /proc/cpuinfo | sed 's/^/cpu\\t/'; nproc | sed 's/^/nproc\\t/'\n")


def tail(script, log, score, sentinel, what, extra=""):
    return (extra + "T0=$(date +%%s)\nbash %s > %s 2>&1\nRC=$?\n"
            "echo \"%s exit $RC in $(( $(date +%%s) - T0 ))s\"\ncp %s out/\ngrep -v '^  ' %s\n"
            "test \"$RC\" = 0 || { echo \"FAIL: the %s task failed\"; exit 1; }\n"
            "grep -q '%s' out/%s || { echo \"FAIL: no completion sentinel\"; exit 1; }\n"
            "uname -m | awk '{print \"arch\\t\"$1}'\necho DONE\n") % (
                script, log, script, log, log, what, sentinel, score)


def spec(kind, tag, image, family, cpu, mem, ttl, cap, inputs, cmd):
    return {"task_id": "cookbook-proteomics-real-%s-%s" % (kind, tag), "container": image,
            "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family]},
            "inputs": inputs,
            "outputs": [{"source": "/tmp/out/", "destination": OUT + tag + "/" + kind + "/"}],
            "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
            "command": ["bash", "-c", cmd]}


def search(tag, runs, runsfile, threads, ttl, cap):
    needed = "target.fasta search.sh $(for r in $(cat runs.txt); do printf '%s.mzML ' \"$r\"; done)"
    cmd = head(needed) + tail("search.sh", "search-run.log", "score.tsv", "SEARCH OK", "search",
                              "export THREADS=%d\n" % threads)
    inputs = [{"source": IN + f, "destination": "/tmp/" + f} for f in ["target.fasta", "search.sh", "pins.sha256"]]
    inputs.append({"source": IN + runsfile, "destination": "/tmp/runs.txt"})
    inputs += [{"source": IN + r + ".mzML", "destination": "/tmp/" + r + ".mzML"} for r in runs]
    return spec("search", tag, OPENMS, "c8g", threads, 2 * threads, ttl, cap, inputs, cmd)


def rescore(tag, runsfile, ttl, cap):
    needed = "rescore.sh $(for r in $(cat runs.txt); do printf 'ref/ref_%s.tsv ' \"$r\"; done)"
    cmd = head(needed) + tail("rescore.sh", "rescore-run.log", "score-percolator.tsv", "RESCORE OK", "rescoring")
    inputs = [{"source": IN + f, "destination": "/tmp/" + f} for f in ["rescore.sh", "pins.sha256"]]
    inputs.append({"source": IN + runsfile, "destination": "/tmp/runs.txt"})
    inputs.append({"source": IN + "ref/", "destination": "/tmp/ref/"})
    inputs.append({"source": OUT + tag + "/search/", "destination": "/tmp/pin/"})
    return spec("rescore", tag, PERC, "c8g", 2, 4, ttl, cap, inputs, cmd)


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    print(path)


if sys.argv[1:] == ["probe"]:
    dump(search("probe", RUNS[:1], "runs-probe.txt", 16, "120m", 1.30), "/tmp/pspec/search-probe.task.json")
    dump(rescore("probe", "runs-probe.txt", "60m", 0.25), "/tmp/pspec/rescore-probe.task.json")
elif sys.argv[1:] == ["real"]:
    dump(search("r1", RUNS, "runs.txt", 8, "60m", 0.40), os.path.join(HERE, "search.task.json"))
    dump(rescore("r1", "runs.txt", "45m", 0.10), os.path.join(HERE, "rescore.task.json"))
else:
    sys.exit(__doc__)
