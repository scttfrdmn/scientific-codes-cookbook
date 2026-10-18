#!/usr/bin/env python3
"""Write measurements/neuroimaging-real's arm64 TaskSpecs; the x86/AMD twins come from scripts/spec_arch.py
(`fsl` for the dtifit task, `neuroimaging` for the DIPY task -- each recipe's own x86 pins).

    python3 mkspec.py pin
    python3 mkspec.py run <name> <family> <cpu> <memory_gib> <parallel> <subjects|0=all> <ttl> <fsl_cap> <dipy_cap>
    python3 mkspec.py compare

The scripts are staged files, pinned here by sha256 (EC2 user data is 16 KiB, spawn#1063).
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
B = "s3://${COOKBOOK_BUCKET}"
I = B + "/inputs/neuroimaging-real"


def image(recipe):
    return re.search(r"^image:\s*(\S+)", open(os.path.join(ROOT, "recipes", recipe, "README.md")).read(), re.M).group(1)


def sha(*names):
    return " ".join("%s  %s" % (hashlib.sha256(open(os.path.join(HERE, n), "rb").read()).hexdigest(), n) for n in names)


def pins(*names):
    lines = "\\n".join(sha(n) for n in names)
    return "printf '%s\\n' | sha256sum -c --quiet || { echo \"FAIL: a script does not match its pin\"; exit 1; }" % lines


def cmd(body):
    return ["bash", "-c", "set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n" + body]


def write(name, spec):
    p = os.path.join(HERE, name + ".task.json")
    with open(p, "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(p)


DWI = [{"source": I + "/dwi/", "destination": "/tmp/dwi/"}, {"source": I + "/subjects.txt", "destination": "/tmp/subjects.txt"}]
what = sys.argv[1]

if what == "pin":
    # Computes the pin on AWS, from the staged copies: the dataset never touches a laptop.
    write("pin", {
        "task_id": "neuroreal-pin", "container": image("fsl"),
        "command": cmd("cd /tmp/dwi && sha256sum * > /tmp/dwi.sha256; echo \"files	$(wc -l < /tmp/dwi.sha256)\"; du -sb /tmp/dwi\n"),
        "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"]},
        "inputs": DWI,
        "outputs": [{"source": "/tmp/dwi.sha256", "destination": B + "/runs/neuroimaging-real/pin/dwi.sha256"}],
        "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.15}})

elif what == "run":
    name, fam, cpu, mem, par, n, ttl, fcap, dcap = sys.argv[2:11]
    O = B + "/runs/neuroimaging-real/" + name
    res = {"cpu": int(cpu), "memory_gib": int(mem), "architecture": "arm64", "families": [fam]}
    common = DWI + [{"source": I + "/dwi.sha256", "destination": "/tmp/dwi.sha256"}]
    write("fsl-" + name, {
        "task_id": "neuroreal-fsl-" + name, "container": image("fsl"),
        "command": cmd("cd /tmp\n%s\nbash /tmp/fsl.sh %s %s\n" % (pins("fsl.sh").replace("fsl.sh", "/tmp/fsl.sh"), par, n)),
        "resources": res,
        "inputs": common + [
            {"source": B + "/inputs/fsl/pkgs/", "destination": "/tmp/pkgs/"},
            {"source": I + "/pkgs/", "destination": "/tmp/xpkgs/"},
            {"source": I + "/fsl-dtifit-linux-aarch64.lock", "destination": "/tmp/locks/fsl-dtifit-linux-aarch64.lock"},
            {"source": I + "/fsl-dtifit-linux-64.lock", "destination": "/tmp/locks/fsl-dtifit-linux-64.lock"},
            {"source": I + "/fsl.sh", "destination": "/tmp/fsl.sh"}],
        "outputs": [{"source": "/tmp/" + f, "destination": "%s/fsl/%s" % (O, f)} for f in
                    ("summary.txt", "results.tsv", "samples.tsv", "subjects.err", "install.log", "dtifit.tar")],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": float(fcap)}})
    write("dipy-" + name, {
        "task_id": "neuroreal-dipy-" + name, "container": image("neuroimaging"),
        "command": cmd("cd /tmp\n%s\nbash /tmp/dipy.sh %s %s\n" % (pins("dipy.sh", "subject.py").replace("dipy.sh", "/tmp/dipy.sh").replace("subject.py", "/tmp/subject.py"), par, n)),
        "resources": res,
        "inputs": common + [
            {"source": "%s/fsl/dtifit.tar" % O, "destination": "/tmp/dtifit.tar"},
            {"source": I + "/dipy.sh", "destination": "/tmp/dipy.sh"},
            {"source": I + "/subject.py", "destination": "/tmp/subject.py"}],
        "outputs": [{"source": "/tmp/" + f, "destination": "%s/dipy/%s" % (O, f)} for f in
                    ("summary.txt", "results.tsv", "samples.tsv", "subjects.err", "checks.txt", "dipy-maps.tar")],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": float(dcap)}})

elif what == "compare":
    # Reads all three chips' outputs, so it is written directly, never through spec_arch.py.
    ins = [{"source": I + "/compare.py", "destination": "/tmp/compare.py"}]
    for a, pre in (("arm64", "runs"), ("x86_64", "runs-x86_64"), ("amd", "runs-amd")):
        for t, f in (("dipy", "dipy-maps.tar"), ("dipy", "results.tsv"), ("fsl", "dtifit.tar")):
            ins.append({"source": "%s/%s/neuroimaging-real/cohort/%s/%s" % (B, pre, t, f), "destination": "/tmp/%s/%s-%s" % (a, t, f)})
    write("compare", {
        "task_id": "neuroreal-compare", "container": image("neuroimaging"),
        "command": cmd("cd /tmp\n%s\nexport OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1\npython3 -u /tmp/compare.py > /tmp/compare.txt 2>&1\nRC=$?\ncat /tmp/compare.txt\nexit $RC\n"
                       % pins("compare.py").replace("compare.py", "/tmp/compare.py")),
        "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"]},
        "inputs": ins,
        "outputs": [{"source": "/tmp/compare.txt", "destination": B + "/runs/neuroimaging-real/compare/compare.txt"}],
        "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.10}})
