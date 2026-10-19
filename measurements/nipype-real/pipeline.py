#!/usr/bin/env python3
r"""measurements/nipype-real: a DTI pipeline over a cohort, as a nipype workflow. Called by run.sh.

    pipeline.py <base_dir> <plugin> <n_procs> <ids_file> <tag> <phase>

Per subject (the infosource's iterables expand the graph once per id):

    select (SelectFiles) --> tstat (afni.TStat, 3dTstat -mean)  ----------------\
                       \--> mask  (Function: median-Otsu on mean b0 & S>0) --> dti (dipy.DTI) --> stats
                                                                                                     |
    join (JoinNode over infosource) <-- one row per subject -----------------------------------------/

Records, for check.py:
  /var/tmp/log/<phase>.cb.jsonl    nipype's status_callback: every node start / end / exception
  /var/tmp/log/exec.jsonl          side effects written by the Function nodes themselves
  /var/tmp/log/<phase>.run.json    wall, the executed graph's nodes and edges, worker peak RSS
"""
import collections
import json
import os
import resource
import sys
import time

def brainmask(dwi, bval, bvec, log):
    """median-Otsu on the mean b=0 image, intersected with S > 0 in every volume -- the mask
    measurements/neuroimaging-real computes, line for line, so the two can be compared exactly."""
    import json, os, resource, time
    import numpy as np
    import nibabel as nib
    from dipy.core.gradients import gradient_table
    from dipy.io.gradients import read_bvals_bvecs
    from dipy.segment.mask import median_otsu
    t0 = time.time()
    img = nib.load(dwi)
    raw = np.asarray(img.dataobj)
    bvals, bvecs = read_bvals_bvecs(bval, bvec)
    gtab = gradient_table(bvals, bvecs=bvecs, b0_threshold=50)
    b0 = raw[..., gtab.b0s_mask].astype(np.float64).mean(axis=-1)
    _, brain = median_otsu(b0, median_radius=4, numpass=4)
    m = brain & (raw.min(axis=-1) > 0)
    out = os.path.abspath("mask.nii.gz")
    nib.Nifti1Image(m.astype(np.uint8), img.affine).to_filename(out)
    with open(os.path.join(log, "exec.jsonl"), "a") as fh:
        fh.write(json.dumps({"node": "mask", "dwi": os.path.basename(dwi), "t0": t0, "t1": time.time(),
                             "pid": os.getpid(), "maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}) + "\n")
    return out


def stats(subject_id, dwi, fa_file, md_file, mask_file, mean_file, tag, log):
    """One row per subject: hashes and medians of the maps the pipeline wrote, and AFNI's mean
    against numpy's over the same file. JSON, so the JoinNode can carry it."""
    import hashlib, json, os, resource, time
    import numpy as np
    import nibabel as nib
    t0 = time.time()
    m = np.asarray(nib.load(mask_file).dataobj) > 0
    fa = np.asarray(nib.load(fa_file).dataobj)
    md = np.asarray(nib.load(md_file).dataobj)
    raw = np.asarray(nib.load(dwi).dataobj)
    a = np.asarray(nib.load(mean_file).dataobj, dtype=np.float64).squeeze()
    n = raw.astype(np.float64).mean(axis=-1)
    big = n > 1
    h = lambda x: hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()[:16]
    row = {"id": subject_id, "tag": tag, "dwi": os.path.basename(dwi), "shape": list(raw.shape),
           "fa_dtype": str(fa.dtype), "n_brain": int(m.sum()), "mask_sha16": h(np.packbits(m)),
           "fa_sha16": h(fa[m]), "md_sha16": h(md[m]), "fa_med": float(np.median(fa[m])),
           "md_med": float(np.median(md[m])), "fa_outside_max": float(np.abs(fa[~m]).max()),
           "afni_rel": float(np.max(np.abs(a[big] - n[big]) / n[big]))}
    with open(os.path.join(log, "exec.jsonl"), "a") as fh:
        fh.write(json.dumps({"node": "stats", "id": subject_id, "tag": tag, "t0": t0, "t1": time.time(),
                             "pid": os.getpid(), "maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}) + "\n")
    return json.dumps(row)


def collect(rows, tag, log):
    import json, os, time
    out = os.path.abspath("cohort.jsonl")
    with open(out, "w") as fh:
        for r in rows:
            fh.write(r + "\n")
    with open(os.path.join(log, "exec.jsonl"), "a") as fh:
        fh.write(json.dumps({"node": "join", "n": len(rows), "tag": tag, "t0": time.time(), "t1": time.time(),
                             "pid": os.getpid()}) + "\n")
    return out



def main():
    global cbfh
    base, plugin, nprocs, ids_file, tag, phase = sys.argv[1:7]
    nprocs = int(nprocs)
    LOG = "/var/tmp/log"
    os.makedirs(LOG, exist_ok=True)
    EXEC = LOG   # a DIRECTORY: a string naming an existing file is hashed as that file (hashprobe.py)

    from nipype import config, logging

    config.update_config({
        "logging": {"workflow_level": "INFO", "interface_level": "WARNING", "utils_level": "WARNING",
                    "log_directory": LOG, "log_to_file": "true"},
        "execution": {"stop_on_first_crash": "false", "remove_unnecessary_outputs": "false",
                      "crashdump_dir": LOG + "/crash", "hash_method": "timestamp"}})
    logging.update_logging(config)

    import nipype.pipeline.engine as pe
    from nipype.interfaces import afni
    from nipype.interfaces.dipy import DTI
    from nipype.interfaces.io import SelectFiles
    from nipype.interfaces.utility import Function, IdentityInterface


    ids = [l.strip() for l in open(ids_file) if l.strip()]
    wf = pe.Workflow(name="dti", base_dir=base)
    info = pe.Node(IdentityInterface(fields=["subject_id"]), name="infosource")
    info.iterables = ("subject_id", ids)
    sel = pe.Node(SelectFiles({"dwi": "sub-{subject_id}_ses-BAS1_dwi.nii.gz",
                               "bval": "sub-{subject_id}_ses-BAS1_dwi.bval",
                               "bvec": "sub-{subject_id}_ses-BAS1_dwi.bvec"},
                              base_directory="/tmp/dwi", sort_filelist=True), name="select")
    tstat = pe.Node(afni.TStat(args="-mean", out_file="mean.nii.gz", outputtype="NIFTI_GZ"), name="tstat", mem_gb=0.5)
    mask = pe.Node(Function(input_names=["dwi", "bval", "bvec", "log"], output_names=["mask_file"],
                            function=brainmask), name="mask", mem_gb=1.5)
    mask.inputs.log = EXEC
    dti = pe.Node(DTI(), name="dti", mem_gb=3.0)
    st = pe.Node(Function(input_names=["subject_id", "dwi", "fa_file", "md_file", "mask_file", "mean_file", "tag", "log"],
                          output_names=["row"], function=stats), name="stats", mem_gb=1.0)
    st.inputs.tag = tag
    st.inputs.log = EXEC
    join = pe.JoinNode(Function(input_names=["rows", "tag", "log"], output_names=["cohort"], function=collect),
                       joinsource="infosource", joinfield=["rows"], name="join")
    join.inputs.tag = tag
    join.inputs.log = EXEC
    wf.connect([
        (info, sel, [("subject_id", "subject_id")]),
        (sel, tstat, [("dwi", "in_file")]),
        (sel, mask, [("dwi", "dwi"), ("bval", "bval"), ("bvec", "bvec")]),
        (sel, dti, [("dwi", "in_file"), ("bval", "in_bval"), ("bvec", "in_bvec")]),
        (mask, dti, [("mask_file", "mask_file")]),
        (info, st, [("subject_id", "subject_id")]),
        (sel, st, [("dwi", "dwi")]),
        (dti, st, [("fa_file", "fa_file"), ("md_file", "md_file")]),
        (mask, st, [("mask_file", "mask_file")]),
        (tstat, st, [("out_file", "mean_file")]),
        (st, join, [("row", "rows")]),
    ])

    cbfh = open("%s/%s.cb.jsonl" % (LOG, phase), "a")


    def callback(node, status, *rest):
        cbfh.write(json.dumps({"t": time.time(), "status": str(status), "name": str(node.name),
                               "dir": str(node.output_dir())}) + "\n")
        cbfh.flush()


    def snapshot():
        """mtime of every file the nodes wrote: a re-executed node rewrites its outputs, a reused one does not."""
        out = {}
        for root, _, files in os.walk(base):
            for f in files:
                if f.endswith((".nii.gz", ".jsonl")):
                    q = os.path.join(root, f)
                    out[q] = os.stat(q).st_mtime_ns
        return out


    pre = snapshot()
    args = {"status_callback": callback}
    if plugin == "MultiProc":
        args.update({"n_procs": nprocs, "memory_gb": 52, "raise_insufficient": True})
    t0 = time.time()
    g = wf.run(plugin=plugin, plugin_args=args)
    t1 = time.time()
    cbfh.close()
    post = snapshot()
    rewritten = collections.Counter(os.path.basename(os.path.dirname(q)) for q in post if pre.get(q) != post[q])
    nodes = [str(n.output_dir()) for n in g.nodes()]
    edges = [[str(u.output_dir()), str(v.output_dir())] for u, v in g.edges()]
    join_out = [n for n in g.nodes() if n.name == "join"][0].result.outputs.cohort
    rec = {"phase": phase, "plugin": plugin, "n_procs": nprocs, "ids": len(ids), "tag": tag, "t0": t0, "t1": t1,
           "wall_s": t1 - t0, "rewritten": dict(rewritten), "nodes": nodes, "edges": edges, "cohort": str(join_out),
           "self_maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    # this phase's cohort, kept: the next phase rewrites the join's output in place
    with open(str(join_out)) as src, open("%s/%s.cohort.jsonl" % (LOG, phase), "w") as dst:
        dst.write(src.read())
    with open("%s/%s.run.json" % (LOG, phase), "w") as fh:
        json.dump(rec, fh)
    print("%s\t%s\twall %.1f s\t%d nodes, %d edges" % (phase, plugin, t1 - t0, len(nodes), len(edges)))


# MultiProc's pool starts workers by forkserver on Python 3.14, which re-imports this file:
# without the guard every worker would re-run the workflow.
if __name__ == "__main__":
    main()
