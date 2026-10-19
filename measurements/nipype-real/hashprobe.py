#!/usr/bin/env python3
"""measurements/nipype-real, phase E: why an unchanged Function node can re-run. Called by run.sh.

    hashprobe.py <file|dir>

A one-node workflow whose Function body appends a line to a log -- the recipe's side-effect pattern.
In `file` mode the node's input is the log's path; in `dir` mode it is the directory holding it.
nipype hashes any string input naming an existing FILE by that file's timestamp (or content), not by
the string (BaseTraitedSpec._get_sorteddict, nipype 1.10.0), so in `file` mode the node's own side
effect changes its hash and the next run re-executes it; a directory path is hashed as a string.
run.sh calls this twice per mode, each a fresh interpreter, and check.py counts the executions.
"""
import os
import sys


def touch(log):
    import os
    p = log if not os.path.isdir(log) else os.path.join(log, "exec.jsonl")
    with open(p, "a") as fh:
        fh.write("ran\n")
    return 1


def main():
    mode = sys.argv[1]
    d = "/var/tmp/hp-" + mode
    os.makedirs(d, exist_ok=True)
    log = d + "/exec.jsonl"
    open(log, "a").close()
    import nipype.pipeline.engine as pe
    from nipype import config, logging
    from nipype.interfaces.utility import Function
    config.update_config({"execution": {"hash_method": "timestamp"}, "logging": {"workflow_level": "WARNING"}})
    logging.update_logging(config)
    wf = pe.Workflow(name="hp", base_dir=d + "/wf")
    n = pe.Node(Function(input_names=["log"], output_names=["y"], function=touch), name="touch")
    n.inputs.log = log if mode == "file" else d
    wf.add_nodes([n])
    wf.run(plugin="Linear")
    print("%s\texecutions so far %d" % (mode, sum(1 for _ in open(log))))


if __name__ == "__main__":
    main()
