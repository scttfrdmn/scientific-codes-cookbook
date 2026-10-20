#!/usr/bin/env python3
"""Write this directory's arm64 specs from run.sh, pinning the staged scripts by sha256.

    python3 measurements/specutils-real/mkspec.py
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:c09e88de976ec1184dbc4461bbb19477e9d39a991ef8aceeddb012da8d356a1c"   # recipes/specutils' pin
B = "s3://${COOKBOOK_BUCKET}"
P = B + "/inputs/specutils-real"
OWN = ("run.sh", "real.py", "check.py", "desiutil_log.py", "pins.sha256", "sfd.sha256")
SFD = ("SFD_dust_4096_ngp.fits", "SFD_dust_4096_sgp.fits")
DESI = {"desispec_emlinefit.py": "ed51e9c567fdd3061e14889a6ff6c453c0bb2e0a442310f567f5122f14556678",
        "desiutil_dust.py": "4649129279f59732bb74174bc2202369ffc9bbf10b52fa65e1c158746d1c4ddf"}


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def spec(task, pix, resources, ttl, cap, run):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in OWN) + "".join("%s  %s\\n" % (h, f) for f, h in DESI.items())
    cmd = ("set +e\nset -uo pipefail\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/run.sh %s\n") % (pins, pix)
    if pix:
        ins = [{"source": "%s/desi/%s-main-dark-%s.fits" % (P, k, p), "destination": "/work/in/%s-main-dark-%s.fits" % (k, p)}
               for p in pix.split(",") for k in ("coadd", "emline")]
    else:
        ins = [{"source": P + "/desi/", "destination": "/work/in/"}]
    ins += [{"source": "%s/sfd/%s" % (P, f), "destination": "/work/sfd/" + f} for f in SFD]
    ins += [{"source": "%s/%s" % (P, f), "destination": "/tmp/" + f} for f in OWN + tuple(DESI)]
    outs = ["check.txt", "check.log", "real.log", "host.txt", "footprint.txt", "results.json", "pix.tar",
            "staged.sha256", "progress.txt"]
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": resources,
        "inputs": ins,
        "outputs": [{"source": "/tmp/out/" + f, "destination": "%s/runs/specutils-real/%s/%s" % (B, run, f)} for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


def write(name, s):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")


write("sizing.task.json", spec("cookbook-specutils-real-sizing", "10000,10001",
                               {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"], "disk_gib": 20},
                               "60m", 0.15, "sizing"))
write("real.task.json", spec("cookbook-specutils-real-r1", "",
                             {"cpu": 16, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 60},
                             "60m", 0.5, "r1"))
ins = [{"source": "%s/%s/specutils-real/r1/%s" % (B, pre, f), "destination": "/tmp/cmp/%s/%s" % (chip, f)}
       for chip, pre in (("arm64", "runs"), ("x86_64", "runs-x86_64"), ("amd", "runs-amd"))
       for f in ("pix.tar", "results.json", "check.txt")]
ins.append({"source": P + "/compare.py", "destination": "/tmp/compare.py"})
write("stage-sfd.task.json", {
    "task_id": "cookbook-specutils-real-stage-sfd",
    "container": IMAGE,
    "command": ["bash", "-c", "set +e\nset -uo pipefail\nexport HOME=/tmp\nmkdir -p /tmp/out\n"
                "echo '%s  /tmp/stage_sfd.py' | sha256sum -c - || { echo 'FAIL: stage_sfd.py does not match its pin'; exit 1; }\n"
                "python3 -u /tmp/stage_sfd.py /tmp/out > /tmp/out/stage_sfd.log 2>&1; RC=$?\ncat /tmp/out/stage_sfd.log\n"
                "[ $RC = 0 ] && grep -q 'SFD STAGED' /tmp/out/stage_sfd.log || { echo FAIL; exit 1; }\n" % sha("stage_sfd.py")],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": P + "/stage_sfd.py", "destination": "/tmp/stage_sfd.py"}],
    "outputs": [{"source": "/tmp/out/" + f, "destination": "%s/sfd/%s" % (P, f)} for f in SFD + ("sfd.sha256", "stage_sfd.log")],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.1},
})
pins = "".join("%s  %s\\n" % (sha(f), f) for f in ("probe_flip.py", "desiutil_log.py", "sfd.sha256")) + \
    "".join("%s  %s\\n" % (h, f) for f, h in DESI.items())
write("probe.task.json", {
    "task_id": "cookbook-specutils-real-probe",
    "container": IMAGE,
    "command": ["bash", "-c", "set +e\nset -uo pipefail\nexport PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH HOME=/tmp\nmkdir -p /tmp/out /tmp/desi/desiutil\ncd /tmp\n"
                "printf '%s' > scripts.sha256\nsha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
                "( cd /work/sfd && sha256sum -c /tmp/sfd.sha256 ) || { echo 'FAIL: SFD pin'; exit 1; }\n"
                "cp desispec_emlinefit.py /tmp/desi/desispec_iron_emlinefit.py; cp desiutil_dust.py /tmp/desi/; cp desiutil_log.py /tmp/desi/desiutil/log.py; : > /tmp/desi/desiutil/__init__.py\n"
                "python3 -u /tmp/probe_flip.py /work/in /work/sfd > /tmp/out/probe.txt 2>&1; cat /tmp/out/probe.txt\n"
                "grep -q 'PROBE DONE' /tmp/out/probe.txt || { echo FAIL; exit 1; }\n" % pins],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": "%s/desi/%s-main-dark-10084.fits" % (P, k), "destination": "/work/in/%s-main-dark-10084.fits" % k} for k in ("coadd", "emline")]
              + [{"source": "%s/sfd/%s" % (P, f), "destination": "/work/sfd/" + f} for f in SFD]
              + [{"source": "%s/%s" % (P, f), "destination": "/tmp/" + f} for f in ("probe_flip.py", "desiutil_log.py", "sfd.sha256") + tuple(DESI)],
    "outputs": [{"source": "/tmp/out/probe.txt", "destination": "%s/runs/specutils-real/probe/probe.txt" % B}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.1},
})
write("compare.task.json", {
    "task_id": "cookbook-specutils-real-compare",
    "container": IMAGE,
    "command": ["bash", "-c", "set +e\nset -uo pipefail\nexport PATH=/opt/conda/bin:$PATH HOME=/tmp\nmkdir -p /tmp/out\n"
                "echo '%s  /tmp/compare.py' | sha256sum -c - || { echo 'FAIL: compare.py does not match its pin'; exit 1; }\n"
                "python3 -u /tmp/compare.py /tmp/cmp > /tmp/out/compare.log 2>&1; RC=$?\ncat /tmp/out/compare.log\n"
                "[ $RC = 0 ] && grep -q 'COMPARE DONE' /tmp/out/compare.log && test -s /tmp/out/crosschip.txt || { echo FAIL; exit 1; }\n" % sha("compare.py")],
    "resources": {"cpu": 2, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"]},
    "inputs": ins,
    "outputs": [{"source": "/tmp/out/" + f, "destination": "%s/runs/specutils-real/compare/%s" % (B, f)} for f in ("crosschip.txt", "compare.log")],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.1},
})
print("wrote specs in", D)
