"""Write 00-fetch.task.json: fetch Enzo_64 from yt's sample-data hub ON AWS, check it against the
sha256 that yt 4.4.2's own sample_data_registry.json carries, unpack it, and keep the tree in the
bucket with a per-file sha256 manifest.   python3 measurements/yt-real/mkfetch.py"""
import json, os
D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:eaa6cb334d95db68662c51646a139c0ff635e9ebf3cb603cf5856ad40c26d066"
SHA = "2b17fffb6a2e8e471ef85dbdbfa5568f09f7bdb77715c69b40dfe48be8f71bc9"   # yt-4.4.2 sample_data_registry.json
PY = r'''
import hashlib, os, sys, tarfile, time, urllib.request
URL, SHA = "https://yt-project.org/data/Enzo_64.tar.gz", "%s"
t0 = time.time(); h = hashlib.sha256(); n = 0
with urllib.request.urlopen(URL, timeout=120) as r, open("/tmp/Enzo_64.tar.gz", "wb") as f:
    print("last-modified", r.headers.get("Last-Modified"), "length", r.headers.get("Content-Length"), flush=True)
    while True:
        b = r.read(1 << 22)
        if not b: break
        h.update(b); f.write(b); n += len(b)
got = h.hexdigest()
print("fetched %%d bytes in %%.0f s, sha256 %%s" %% (n, time.time() - t0, got), flush=True)
if got != SHA: sys.exit("FAIL: sha256 %%s, yt's registry says %%s" %% (got, SHA))
print("sha256 == yt-4.4.2 sample_data_registry.json", flush=True)
with tarfile.open("/tmp/Enzo_64.tar.gz") as t:
    for m in t.getmembers():
        if m.name.startswith("/") or ".." in m.name.split("/") or not (m.isfile() or m.isdir()):
            sys.exit("FAIL: unexpected tar member %%r" %% m.name)
    t.extractall("/tmp/out", filter="data")
os.remove("/tmp/Enzo_64.tar.gz")
''' % SHA
cmd = ("set +e\nset -uo pipefail\nexport TMPDIR=/tmp HOME=/tmp\ncd /tmp\nmkdir -p /tmp/out\n"
       "python3 -u - <<'PY' 2>&1 | tee /tmp/fetch.log\n" + PY + "PY\n"
       "test \"${PIPESTATUS[0]}\" = 0 || { echo FAIL: fetch; exit 1; }\n"
       "cd /tmp/out && find . -type f | LC_ALL=C sort | sed 's|^\\./||' | xargs -d '\\n' sha256sum > /tmp/files.sha256\n"
       "echo \"files $(wc -l < /tmp/files.sha256)  bytes $(du -sb /tmp/out | cut -f1)\" | tee -a /tmp/fetch.log\n"
       "ls /tmp/out/Enzo_64 | tee -a /tmp/fetch.log\necho DONE\n")
S3 = "s3://${COOKBOOK_BUCKET}/inputs/yt-real/"
spec = {
    "task_id": "cookbook-yt-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": 4, "memory_gib": 32, "architecture": "arm64", "families": ["r8g"]},
    "outputs": [{"source": "/tmp/out/Enzo_64/", "destination": S3 + "Enzo_64/"},
                {"source": "/tmp/files.sha256", "destination": S3 + "files.sha256"},
                {"source": "/tmp/fetch.log", "destination": S3 + "fetch.log"}],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.25},
}
json.dump(spec, open(os.path.join(D, "00-fetch.task.json"), "w"), indent=2); open(os.path.join(D, "00-fetch.task.json"), "a").write("\n")
