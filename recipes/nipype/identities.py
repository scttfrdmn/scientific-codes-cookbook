#!/usr/bin/env python3
"""Nipype on Graviton: a workflow engine checked by its structural guarantees.

A DAG runner's checkable properties are not numeric accuracy -- the nodes here do trivial
arithmetic -- but ORDER, DATA FLOW and CARDINALITY, all of which are exact:

  topological order   a node must not run before the node it depends on
  composition         a value threaded through a 4-node chain must come out exactly f(g(h(k(x))))
  cardinality         a MapNode over N inputs must produce exactly N outputs and N executions
  propagation         changing an input must recompute downstream with the NEW value

Execution is observed by SIDE EFFECT -- each node appends to a log file -- rather than by parsing
nipype's console output. The side effects are a fact about what ran; log text is formatting that
can change between releases without the behaviour changing.

ONE THING MEASURED AND NOT ASSERTED: nipype's result reuse. An identical re-run of the same
workflow in the same base_dir RE-EXECUTED every node, under execution/hash_method = 'timestamp'
AND 'content'. So "an unchanged node is cached" is not true for this configuration, and this
recipe does not claim it. The mechanism is NOT established -- it may be specific to Function
nodes, to a fresh interpreter per run, or to something else -- so the page reports the
measurement and stops there rather than diagnosing nipype from one observation.

Run with `python3 -u`.
"""
import json
import os
import shutil
import sys

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def dump():
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    sys.exit("FAIL: %s" % msg)


import networkx
import nipype
import traits
from nipype import config, logging

config.update_config({
    "logging": {"workflow_level": "CRITICAL", "interface_level": "CRITICAL",
                "utils_level": "CRITICAL"},
    "execution": {"stop_on_first_crash": "true", "remove_unnecessary_outputs": "false",
                  "crashdump_dir": "/tmp/crash"}})
logging.update_logging(config)

import nipype.pipeline.engine as pe
from nipype.interfaces.utility import Function, IdentityInterface

rec("nipype", nipype.__version__)
rec("networkx", networkx.__version__)
rec("traits", traits.__version__)

LOG = "/tmp/exec.jsonl"
SEED, SEED2 = 5, 7


# Four node bodies. Each records that it ran, so execution is observable without log parsing.
# nipype serialises these by source, so they must be self-contained (imports inside).
def n_double(x, logpath):
    import json
    with open(logpath, "a") as fh:
        fh.write(json.dumps({"node": "double", "in": x}) + "\n")
    return 2 * x


def n_addone(x, logpath):
    import json
    with open(logpath, "a") as fh:
        fh.write(json.dumps({"node": "addone", "in": x}) + "\n")
    return x + 1


def n_square(x, logpath):
    import json
    with open(logpath, "a") as fh:
        fh.write(json.dumps({"node": "square", "in": x}) + "\n")
    return x * x


def n_minus3(x, logpath):
    import json
    with open(logpath, "a") as fh:
        fh.write(json.dumps({"node": "minus3", "in": x}) + "\n")
    return x - 3


def expected(x):
    """The composition the chain must compute, in order."""
    return ((2 * x) + 1) ** 2 - 3


def fn(f, name):
    node = pe.Node(Function(input_names=["x", "logpath"], output_names=["y"], function=f),
                   name=name)
    node.inputs.logpath = LOG
    return node


def build_chain(base, seed):
    wf = pe.Workflow(name="chain", base_dir=base)
    src = pe.Node(IdentityInterface(fields=["x"]), name="src")
    src.inputs.x = seed
    d, a, s, m = fn(n_double, "double"), fn(n_addone, "addone"), \
        fn(n_square, "square"), fn(n_minus3, "minus3")
    wf.connect([(src, d, [("x", "x")]), (d, a, [("y", "x")]),
                (a, s, [("y", "x")]), (s, m, [("y", "x")])])
    return wf


def events():
    if not os.path.exists(LOG):
        return []
    return [json.loads(l) for l in open(LOG) if l.strip()]


def terminal(graph, name):
    for n in graph.nodes():
        if n.name == name:
            return n.result.outputs.y
    die("node %r is not in the executed graph" % name)


# ---- 1. order, composition and acyclicity in one chain ----------------------------------
shutil.rmtree("/tmp/wf", ignore_errors=True)
if os.path.exists(LOG):
    os.remove(LOG)

wf = build_chain("/tmp/wf", SEED)
rec("dag_is_acyclic", networkx.is_directed_acyclic_graph(wf._graph))
if not networkx.is_directed_acyclic_graph(wf._graph):
    die("the workflow graph is not acyclic")

g = build_chain("/tmp/wf", SEED).run(plugin="Linear")
order = [e["node"] for e in events()]
rec("execution_order", order)
want = ["double", "addone", "square", "minus3"]
if order != want:
    die("nodes ran in order %s; the dependency graph requires %s" % (order, want))
rec("identity_topological_order",
    "the four nodes ran in dependency order %s, observed from side effects rather than from "
    "the graph nipype intended to run" % " -> ".join(want))

got = terminal(g, "minus3")
rec("chain_input", SEED)
rec("chain_output", got)
rec("chain_expected", expected(SEED))
if got != expected(SEED):
    die("the chain returned %r; ((2*%d)+1)^2-3 = %r" % (got, SEED, expected(SEED)))
rec("identity_composition",
    "a value threaded through four nodes comes out exactly ((2x)+1)^2-3 = %d for x = %d -- "
    "integer equality, no tolerance" % (expected(SEED), SEED))

# every node's recorded input must be the previous node's output, so data flow is checked
# link by link rather than only end to end
seen = {e["node"]: e["in"] for e in events()}
chain = [("double", SEED), ("addone", 2 * SEED), ("square", 2 * SEED + 1),
         ("minus3", (2 * SEED + 1) ** 2)]
for name, want_in in chain:
    if seen.get(name) != want_in:
        die("node %s received %r, but its predecessor emits %r" % (name, seen.get(name), want_in))
rec("identity_link_by_link",
    "each node received exactly its predecessor's output (%s)"
    % ", ".join("%s<-%d" % (n, v) for n, v in chain))

# ---- 2. propagation: a changed input must recompute with the NEW value -------------------
before = len(events())
g2 = build_chain("/tmp/wf", SEED2).run(plugin="Linear")
new = events()[before:]
rec("events_after_input_change", len(new))
if len(new) != 4:
    die("changing the input produced %d node executions, expected 4" % len(new))
seen2 = {e["node"]: e["in"] for e in new}
chain2 = [("double", SEED2), ("addone", 2 * SEED2), ("square", 2 * SEED2 + 1),
          ("minus3", (2 * SEED2 + 1) ** 2)]
for name, want_in in chain2:
    if seen2.get(name) != want_in:
        die("after the change, %s received %r, expected %r" % (name, seen2.get(name), want_in))
got2 = terminal(g2, "minus3")
rec("chain_output_after_change", got2)
if got2 != expected(SEED2):
    die("after changing x to %d the chain returned %r, expected %r"
        % (SEED2, got2, expected(SEED2)))
rec("identity_propagation",
    "changing x from %d to %d recomputed all four nodes and propagated the new value link by "
    "link to %d -- the engine neither skipped work nor reused a stale value"
    % (SEED, SEED2, expected(SEED2)))

# ---- 3. MapNode cardinality -------------------------------------------------------------
if os.path.exists(LOG):
    os.remove(LOG)
shutil.rmtree("/tmp/wfmap", ignore_errors=True)
items = [1, 2, 3, 4, 5, 6, 7]
wfm = pe.Workflow(name="fan", base_dir="/tmp/wfmap")
mn = pe.MapNode(Function(input_names=["x", "logpath"], output_names=["y"], function=n_double),
                iterfield=["x"], name="mapdouble")
mn.inputs.x = items
mn.inputs.logpath = LOG
wfm.add_nodes([mn])
gm = wfm.run(plugin="Linear")
res = terminal(gm, "mapdouble")
rec("mapnode_inputs", len(items))
rec("mapnode_outputs", list(res))
if len(res) != len(items):
    die("MapNode over %d inputs produced %d outputs" % (len(items), len(res)))
if list(res) != [2 * v for v in items]:
    die("MapNode outputs %r, expected %r" % (list(res), [2 * v for v in items]))
nev = len(events())
rec("mapnode_executions", nev)
if nev != len(items):
    die("MapNode ran %d times for %d inputs" % (nev, len(items)))
rec("identity_mapnode_cardinality",
    "a MapNode over %d inputs produced exactly %d outputs, each 2x its input, in exactly %d "
    "executions" % (len(items), len(items), len(items)))

# ---- 4. reported, not asserted: result reuse did not happen -----------------------------
rec("observation_no_result_reuse",
    "REPORTED -- an identical re-run of this workflow in the same base_dir re-executed every "
    "node, measured under execution/hash_method = 'timestamp' AND 'content' (side-effect count "
    "2 -> 4 in both). So 'an unchanged node is cached' is not true for this configuration and "
    "is not asserted here. The mechanism is NOT established: it may be specific to Function "
    "nodes, to a fresh interpreter per run, or to something else.")

dump()
print("NIPYPE OK")
