#!/usr/bin/env python3
"""Probe: learn the WSI and mask geometry before designing any check against it.

Run with `python3 -u`. Nothing here asserts science -- it reports the numbers the real
checks have to be built from, so they are not guessed. Deliberately separate from
identities.py: sizing and geometry are unknown for a 546 MB pyramidal TIFF, and this
project's rule is to probe cheaply rather than launch a long run on a guess.
"""
import os
import resource
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
import openslide


def rec(k, v):
    print("%s\t%s" % (k, v))


rec("openslide_python", getattr(openslide, "__version__", "unknown"))
rec("openslide_library", openslide.__library_version__)
rec("numpy", np.__version__)
try:
    import skimage
    rec("scikit_image", skimage.__version__)
except Exception as e:                                             # noqa: BLE001
    rec("scikit_image", "IMPORT FAILED: %s" % e)

WSI = "/tmp/tumor_091.tif"
MASK = "/tmp/tumor_091_mask.tif"
XML = "/tmp/tumor_091.xml"

for p in (WSI, MASK, XML, "/tmp/checksums.md5", "/tmp/reference.csv"):
    rec("staged_bytes_%s" % os.path.basename(p),
        os.path.getsize(p) if os.path.exists(p) else "MISSING")

# --- the publisher ships md5s for all 963 objects; verify, don't assume -------------------
for name, path in (("images/tumor_091.tif", WSI),
                   ("masks/tumor_091_mask.tif", MASK),
                   ("annotations/tumor_091.xml", XML)):
    want = None
    for line in open("/tmp/checksums.md5", errors="replace"):
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            want = parts[0]
            break
    got = subprocess.run(["md5sum", path], capture_output=True, text=True).stdout.split()[0]
    rec("md5_%s" % os.path.basename(path), "%s (publisher %s) %s"
        % (got, want, "MATCH" if want == got else "*** MISMATCH ***"))

for label, path in (("wsi", WSI), ("mask", MASK)):
    try:
        s = openslide.OpenSlide(path)
    except Exception as e:                                         # noqa: BLE001
        rec("%s_open" % label, "FAILED: %s" % e)
        continue
    rec("%s_dimensions" % label, s.dimensions)
    rec("%s_level_count" % label, s.level_count)
    rec("%s_level_dimensions" % label, s.level_dimensions)
    rec("%s_level_downsamples" % label, tuple(round(d, 4) for d in s.level_downsamples))
    for k in ("openslide.vendor", "openslide.mpp-x", "openslide.mpp-y",
              "openslide.objective-power", "tiff.ResolutionUnit", "tiff.XResolution"):
        if k in s.properties:
            rec("%s_prop_%s" % (label, k.replace(".", "_")), s.properties[k])
    # the smallest level is what a whole-slide comparison can afford to hold
    lvl = s.level_count - 1
    a = np.asarray(s.read_region((0, 0), lvl, s.level_dimensions[lvl]).convert("L"))
    rec("%s_smallest_level" % label, "%d, shape %s" % (lvl, a.shape))
    if label == "mask":
        vals, counts = np.unique(a, return_counts=True)
        rec("mask_values_at_smallest", dict(zip(vals.tolist(), counts.tolist())))
    s.close()

# --- the annotations, which the README says the mask was rasterised from ------------------
root = ET.parse(XML).getroot()
anns = root.findall(".//Annotation")
rec("xml_annotations", len(anns))
for a in anns:
    co = [(float(c.get("X")), float(c.get("Y"))) for c in a.findall(".//Coordinate")]
    xs, ys = [p[0] for p in co], [p[1] for p in co]
    rec("xml_ann_%s" % a.get("Name"),
        "group=%s pts=%d x=[%.0f,%.0f] y=[%.0f,%.0f]"
        % (a.get("PartOfGroup"), len(co), min(xs), max(xs), min(ys), max(ys)))

for line in open("/tmp/reference.csv"):
    if line.startswith("tumor_091"):
        rec("reference_csv_row", line.strip())

rec("peak_rss_mib", "%.1f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0))
out = subprocess.run(["df", "-m", "/tmp"], capture_output=True, text=True).stdout.splitlines()
rec("tmp_filesystem", " ".join(out[-1].split()) if len(out) > 1 else "?")
print("PROBE OK")
