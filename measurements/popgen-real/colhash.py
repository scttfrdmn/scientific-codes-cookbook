"""Which table columns make the simulated chr22 differ across builds? Re-simulates chr22 exactly as
real.py does (same model, seed and length) and prints a sha256 per column, plus the branch AFS.
    python3 -u colhash.py"""
import hashlib
import sys

import numpy as np

sys.argv = ["real.py", "/tmp", "/tmp/out", "1", "22"]
sys.path.insert(0, "/tmp")
import real  # noqa: E402

ts, _, _ = real.simulate(51304566, real.SEED + 22)   # chr22's ##contig length, as in the run
print("digest\t%s" % real.tables_digest(ts))
t = ts.tables
for name, col in [("nodes.time", t.nodes.time), ("nodes.population", t.nodes.population),
                  ("edges.left", t.edges.left), ("edges.right", t.edges.right),
                  ("edges.parent", t.edges.parent), ("edges.child", t.edges.child),
                  ("sites.position", t.sites.position), ("mutations.site", t.mutations.site),
                  ("mutations.node", t.mutations.node), ("mutations.time", t.mutations.time)]:
    print("%s\t%s\t%d" % (name, hashlib.sha256(np.ascontiguousarray(col).tobytes()).hexdigest()[:16],
                          len(col)))
B = ts.allele_frequency_spectrum([ts.samples()], mode="branch", polarised=True, span_normalise=False)
print("branch_afs\t%s" % hashlib.sha256(B.tobytes()).hexdigest()[:16])
print("first_nodes_time\t%s" % " ".join(repr(float(x)) for x in t.nodes.time[3336:3346]))
print("first_mut_time\t%s" % " ".join(repr(float(x)) for x in t.mutations.time[:5]))
print("first_positions\t%s" % " ".join(repr(float(x)) for x in t.sites.position[:5]))
