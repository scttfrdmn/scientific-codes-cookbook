#!/usr/bin/env python3
"""Build the gemmi-real manifest from three `list-objects-v2` listings of the RODA PDB snapshot.

    python3 -I build_manifest.py etag-mmCIF.txt etag-pdb.txt etag-structure_factors.txt > manifest.tsv

Each listing is `aws s3api list-objects-v2 ... --query 'Contents[].[Key,Size,ETag]' --output text`.
One row per PDB entry, sorted by id: the mmCIF, PDB-format and structure-factor objects with their
size and S3 ETag. A single-part ETag is the object's MD5; a multipart one (4,394 of the 672,178
objects) is the MD5 of the parts' MD5s with an `-N` part count, and every one in this snapshot has
the N that 8 MiB parts give, which is what the task recomputes. Any other ETag shape is refused
rather than pinned unchecked. Output is byte-deterministic, so its sha256 is the pin.
"""
import re
import sys

PAT = {
    "cif": re.compile(r"/mmCIF/[0-9a-z]{2}/([0-9a-z]{4})\.cif\.gz$"),
    "pdb": re.compile(r"/pdb/[0-9a-z]{2}/pdb([0-9a-z]{4})\.ent\.gz$"),
    "sf": re.compile(r"/structure_factors/[0-9a-z]{2}/r([0-9a-z]{4})sf\.ent\.gz$"),
}
rows = {}
for kind, path in zip(["cif", "pdb", "sf"], sys.argv[1:4]):
    seen = 0
    for line in open(path):
        key, size, etag = line.rstrip("\n").split("\t")
        m = PAT[kind].search(key)
        if not m and key.rsplit("/", 1)[-1].startswith("."):
            # the snapshot carries stray hidden files (an editor's .swp); not an entry, but say so
            print("skipped hidden object: %s (%s bytes)" % (key, size), file=sys.stderr)
            continue
        if not m:
            sys.exit("unexpected key in %s listing: %s" % (kind, key))
        md5 = etag.strip('"')
        mp = re.fullmatch(r"[0-9a-f]{32}(?:-([0-9]+))?", md5)
        if not mp:
            sys.exit("not an MD5 ETag, cannot pin: %s %s" % (key, etag))
        if mp.group(1) and int(mp.group(1)) != -(-int(size) // (8 << 20)):
            sys.exit("multipart ETag whose part count is not 8 MiB parts: %s %s" % (key, etag))
        e = rows.setdefault(m.group(1), {})
        if kind in e:
            sys.exit("duplicate %s object for %s" % (kind, m.group(1)))
        e[kind] = (key, size, md5)
        seen += 1
    print("%s: %d objects" % (kind, seen), file=sys.stderr)

out = sys.stdout
out.write("id\tcif_key\tcif_size\tcif_md5\tpdb_key\tpdb_size\tpdb_md5\tsf_key\tsf_size\tsf_md5\n")
orphans = 0
for pid in sorted(rows):
    e = rows[pid]
    if "cif" not in e:
        orphans += 1
    cols = [pid]
    for kind in ["cif", "pdb", "sf"]:
        cols += list(e.get(kind, ("-", "0", "-")))
    out.write("\t".join(cols) + "\n")
print("entries: %d (without an mmCIF: %d)" % (len(rows), orphans), file=sys.stderr)
