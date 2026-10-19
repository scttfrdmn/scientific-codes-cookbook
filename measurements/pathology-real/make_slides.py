#!/usr/bin/env python3
"""Build slides.tsv: the cohort and every pin, from the publisher's manifests and RODA's listing.

    python3 -I make_slides.py <checksums.md5> <reference.csv> > slides.tsv

The cohort is every slide reference.csv types `tumor` (160: 111 training + 49 test), which is
exactly the set with an annotation XML. Image, mask and XML md5s come from the publisher's
checksums.md5. The tissue masks are not in it, so they are pinned by S3's ETag, which for a
single-part object is its md5 (checked: no ETag here carries a part count). Sizes come from
head-object, so the run can budget /tmp before it fetches.
"""
import json
import subprocess
import sys

md5 = {}
for line in open(sys.argv[1], errors="replace"):
    p = line.split()
    if len(p) == 2:
        md5[p[1].lstrip("*")] = p[0]
ref = {}
for line in open(sys.argv[2]):
    f = line.strip().split(",")
    if f[0].endswith(".tif") and f[1] == "tumor":
        ref[f[0][:-4]] = (f[3], f[2])


def head(key):
    out = subprocess.run(["aws", "s3api", "head-object", "--region", "us-west-2", "--bucket",
                          "camelyon-dataset", "--key", "CAMELYON16/" + key, "--output", "json"],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return int(j["ContentLength"]), j["ETag"].strip('"')


print("slide\tcenter\tclass\timage_bytes\tmask_bytes\timage_md5\tmask_md5\txml_md5\ttissue_md5")
for s in sorted(ref):
    ib, _ = head("images/%s.tif" % s)
    mb, _ = head("masks/%s_mask.tif" % s)
    _, tet = head("background_tissue/%s_tissue.tif" % s)
    if "-" in tet:
        sys.exit("%s tissue ETag %s is multipart -- not an md5" % (s, tet))
    print("\t".join([s, ref[s][0], ref[s][1], str(ib), str(mb), md5["images/%s.tif" % s],
                     md5["masks/%s_mask.tif" % s], md5["annotations/%s.xml" % s], tet]))
