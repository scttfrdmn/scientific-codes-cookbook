#!/usr/bin/env python3
"""Write the specs for lith-vs-copy-real.

    python3 mkspec.py canary <instance_type> <out.json>
    python3 mkspec.py arm <workload> <copy|lith> <instance_type> <nonce> <out.json>

An arm spec is a spawn HOST-path task (no `container`): spawn stages the original measurement's
small inputs exactly where its own spec put them, and arm.sh, on the host, either copies the big
inputs at tuned concurrency (copy) or mounts them through lith and symlinks them into place
(lith). It then runs the original spec's container and command, unchanged, read from the original
spec file itself (staged, sha256-checked). Outputs go where the original sent them, under
runs/lith-vs-copy-real/<workload>-<arm>-<nonce>/.

Written with ${COOKBOOK_BUCKET} left in place; set COOKBOOK_BUCKET to substitute it.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = os.path.dirname(HERE)
PFX = "s3://${COOKBOOK_BUCKET}/inputs/lith-vs-copy-real"

# workload -> (original spec, the prefix whose objects are "the data", lith knobs)
W = {
    "tomography": ("tomography-real/real.task.json", "inputs/tomography-real/data/"),
    "gfs": ("gfs-thickness-real/gfs-cycle.task.json", "inputs/gfs-thickness-real/cycle/"),
    "manta": ("structural-variants-real/01-manta.task.json", "inputs/structural-variants-real/derived/"),
    "delly": ("structural-variants-real/02-delly.task.json", "inputs/structural-variants-real/derived/"),
    "gsw": ("gsw-real/argo.task.json", "inputs/gsw-real/argo/"),
}
# NIC baselines (DescribeInstanceTypes, us-west-2) so lith never falls back to an estimate
NIC = {"4xlarge": "7.5", "8xlarge": "15", "16xlarge": "30", "large": "0.937"}
# aws CLI transfer settings for the tuned copy, picked from the canary (see README)
# (the CLI's CRT client beat 64- and 128-way classic settings on both shapes), aimed at the box's
# peak NIC figure
PEAK = {"4xlarge": "15", "8xlarge": "15", "16xlarge": "30", "large": "12.5"}
TUNED = "preferred_transfer_client=crt target_bandwidth=%sGb/s multipart_chunksize=16MB"


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def sub(s):
    b = os.environ.get("COOKBOOK_BUCKET")
    return s.replace("${COOKBOOK_BUCKET}", b) if b else s


def write(spec, out):
    with open(out, "w") as f:
        f.write(sub(json.dumps(spec, indent=2)) + "\n")
    print(out)


def canary(itype, out):
    spec = {
        "task_id": "cookbook-lith-vs-copy-real-canary",
        "command": ["bash", "-c", "set +e\ncd /tmp\necho '%s  canary.sh' | sha256sum -c - || exit 1\nbash /tmp/canary.sh\n"
                    % sha(os.path.join(HERE, "canary.sh"))],
        "resources": {"instance_type": itype, "architecture": "arm64"},
        "env": {"ARM_BUCKET": "${COOKBOOK_BUCKET}"},
        "inputs": [{"source": PFX + "/canary.sh", "destination": "/tmp/canary.sh"}],
        "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/lith-vs-copy-real/canary/"}],
        "lifecycle": {"ttl": "45m", "on_complete": "terminate", "cost_limit": 0.6},
    }
    write(spec, out)


def arm(w, mode, itype, nonce, out):
    orig_rel, root = W[w]
    orig_path = os.path.join(M, orig_rel)
    orig = json.load(open(orig_path))
    rid = "%s-%s-%s" % (w, mode, nonce)
    keep, data = [], []
    for i in orig["inputs"]:
        rel = i["source"].split("${COOKBOOK_BUCKET}/", 1)[1]
        (data if rel.startswith(root) else keep).append(i)
    assert data, "no input under " + root
    outs = []
    for o in orig["outputs"]:
        d = o["destination"].replace("/runs/", "/runs/lith-vs-copy-real/%s/" % rid, 1)
        outs.append({"source": o["source"], "destination": d})
    outs.append({"source": "/tmp/arm/", "destination": "s3://${COOKBOOK_BUCKET}/runs/lith-vs-copy-real/%s/arm/" % rid})
    origname = orig_rel.replace("/", "--")
    lc = dict(orig["lifecycle"])
    res = {"instance_type": itype, "architecture": "arm64"}
    if orig["resources"].get("disk_gib"):
        res["disk_gib"] = orig["resources"]["disk_gib"]
    spec = {
        "task_id": "cookbook-lith-vs-copy-real-%s" % rid,
        "command": ["bash", "-c", "set +e\ncd /tmp\necho '%s  arm.sh' | sha256sum -c - || exit 1\nbash /tmp/arm.sh\n"
                    % sha(os.path.join(HERE, "arm.sh"))],
        "resources": res,
        "env": {
            "ARM_MODE": mode, "ARM_ID": rid, "ARM_BUCKET": "${COOKBOOK_BUCKET}",
            "ARM_ORIG": "/tmp/arm-orig.task.json", "ARM_ORIG_SHA": sha(orig_path),
            "ARM_DATA": " ".join("%s|%s" % (i["source"], i["destination"]) for i in data),
            "ARM_ROOT": "s3://${COOKBOOK_BUCKET}/" + root,
            "ARM_NIC": NIC[itype.split(".")[1]], "ARM_MEMCACHE": os.environ.get("ARM_MEMCACHE", "8GiB"),
            "ARM_S3CFG": TUNED % PEAK[itype.split(".")[1]],
        },
        "inputs": keep + [
            {"source": PFX + "/arm.sh", "destination": "/tmp/arm.sh"},
            {"source": PFX + "/orig/" + origname, "destination": "/tmp/arm-orig.task.json"},
        ],
        "outputs": outs,
        "lifecycle": {"ttl": lc["ttl"], "on_complete": "terminate", "cost_limit": float(os.environ.get("ARM_COST", lc["cost_limit"]))},
    }
    write(spec, out)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "canary":
        canary(a[1], a[2])
    else:
        arm(a[1], a[2], a[3], a[4], a[5])
