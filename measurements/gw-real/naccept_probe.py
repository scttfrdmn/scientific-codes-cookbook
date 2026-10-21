#!/usr/bin/env python3
"""Does bilby 2.8.2 + dynesty 3.1.0 honour `naccept` for sample="acceptance-walk"?
A 6-D Gaussian, run at naccept 30/60/120, serial and with a pool: if it is honoured, the likelihood
calls roughly double with each doubling. Also records the walk length bilby's sampler ended on.
    naccept_probe.py <out dir> [nlive ndim npool...]   (default: 200 6 1 4)"""
import os
import sys

import numpy as np

import bilby
from bilby.core.sampler import dynesty3_utils

OUT = sys.argv[1]
NLIVE = int(sys.argv[2]) if len(sys.argv) > 2 else 200
NDIM = int(sys.argv[3]) if len(sys.argv) > 3 else 6
NPOOLS = [int(a) for a in sys.argv[4:]] or [1, 4]
os.makedirs(OUT, exist_ok=True)
WALKS = []
_tune = dynesty3_utils.EnsembleWalkSampler.tune


def tune(self, info, update=True):
    _tune(self, info, update)
    WALKS.append((self.naccept, self.sampler_kwargs["walks"], info["accept"]))


dynesty3_utils.EnsembleWalkSampler.tune = tune


class Gauss(bilby.Likelihood):
    def __init__(self):
        super().__init__(parameters={"x%d" % i: None for i in range(NDIM)})

    def log_likelihood(self):
        x = np.array([self.parameters["x%d" % i] for i in range(NDIM)])
        return -0.5 * np.sum((x / 0.1) ** 2)


prior = bilby.core.prior.PriorDict({"x%d" % i: bilby.core.prior.Uniform(-1, 1) for i in range(NDIM)})
rows = []
for npool in NPOOLS:
    for na in ((30, 60, 120) if NLIVE <= 200 else (60, 120)):
        WALKS.clear()
        r = bilby.run_sampler(Gauss(), prior, sampler="dynesty", nlive=NLIVE, sample="acceptance-walk",
                              naccept=na, npool=npool, rstate=np.random.default_rng(1), check_point=False,
                              outdir="/tmp/work/p%d_%d" % (npool, na), label="g", save=False, print_method="interval-600")
        last = WALKS[-50:] if WALKS else []
        row = "nlive=%d ndim=%d npool=%d naccept=%d calls=%s tune_calls_in_main=%d mean_last_walks=%s mean_last_accept=%s" % (
            NLIVE, NDIM, npool, na, r.num_likelihood_evaluations, len(WALKS),
            "%.0f" % np.mean([w for _, w, _ in last]) if last else "-",
            "%.1f" % np.mean([a for _, _, a in last]) if last else "-")
        print(row, flush=True)
        rows.append(row)
with open(os.path.join(OUT, "naccept_probe.txt"), "w") as fh:
    fh.write("\n".join(rows) + "\n")
print("PROBE DONE")
