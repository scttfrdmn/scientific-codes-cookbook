#!/usr/bin/env python3
"""Is the acceptance-walk chain capped at maxmcmc in the GW150914 run? pe.py, byte for byte, with
dynesty stopped at <maxiter> iterations and bilby's walk tuner recorded: per tune call, the walk
length it set and the accepted steps the returned chain had. If the walk length sits at maxmcmc
(5000) with fewer accepted steps than naccept, naccept cannot lengthen the chains.
    walks_probe.py <naccept> <maxiter> <data dir> <out dir> <npool> <nlive>"""
import os
import runpy
import sys

import bilby
from bilby.core.sampler import dynesty3_utils

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
NACCEPT, MAXITER = int(sys.argv[1]), int(sys.argv[2])
OUT = sys.argv[4]
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "walks.tsv"), "w")
LOG.write("tune_call\tnaccept\twalks_set\taccept_returned\treject_returned\n")
N = [0]
_tune = dynesty3_utils.EnsembleWalkSampler.tune


def tune(self, info, update=True):
    _tune(self, info, update)
    N[0] += 1
    LOG.write("%d\t%d\t%d\t%d\t%d\n" % (N[0], self.naccept, self.sampler_kwargs["walks"], info["accept"], info["reject"]))


dynesty3_utils.EnsembleWalkSampler.tune = tune
_run = bilby.run_sampler


def run_sampler(*a, **kw):
    kw["naccept"], kw["maxiter"] = NACCEPT, MAXITER
    print("walks_probe: naccept %d, maxiter %d" % (NACCEPT, MAXITER), flush=True)
    return _run(*a, **kw)


bilby.run_sampler = run_sampler
sys.argv = [os.path.join(HERE, "pe.py")] + sys.argv[3:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as e:
    print("pe.py exited: %s (its checks on a truncated run are not the point here)" % e, flush=True)
finally:
    LOG.close()
print("WALKS PROBE DONE")
