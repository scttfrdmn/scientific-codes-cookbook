#!/usr/bin/env python3
"""measurements/nipype-real: what the four workflow runs must satisfy. Called by run.sh.

    check.py <ids_file> <n_procs> <linear_ids_file>

Phases (run.sh): A = the cohort under MultiProc; B = a subset under Linear in a fresh base_dir;
C = A again, unchanged, same base_dir, fresh interpreter; D = A with one parameter of the
`stats` node changed. Exit 1 if any assertion fails; every number is printed either way.
"""
import collections, glob, json, os, sys
import numpy as np
import nibabel as nib

ids = [l.strip() for l in open(sys.argv[1]) if l.strip()]
nprocs = int(sys.argv[2])
lin_ids = [l.strip() for l in open(sys.argv[3]) if l.strip()]
N = len(ids)
LOG = "/var/tmp/log"
fails = []


def rec(k, v):
    print("%s\t%s" % (k, str(v)))


def check(name, ok, detail):
    ok = bool(ok)
    rec(name, ("PASS  " if ok else "FAIL  ") + str(detail))
    if not ok:
        fails.append(name)


def load_cb(phase):
    p = "%s/%s.cb.jsonl" % (LOG, phase)
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []


def run(phase):
    return json.load(open("%s/%s.run.json" % (LOG, phase)))


execlog = [json.loads(l) for l in open(LOG + "/exec.jsonl") if l.strip()]


def side_effects(phase):
    r = run(phase)
    return collections.Counter(e["node"] for e in execlog if r["t0"] <= e["t0"] <= r["t1"])


def starts(phase):
    return collections.Counter(e["name"] for e in load_cb(phase) if e["status"] == "start")


PER_SUBJECT = ["select", "tstat", "mask", "dti", "stats"]

# ---- A: the cohort under MultiProc --------------------------------------------------------
A = run("A")
cb = load_cb("A")
rec("A_wall_s", "%.1f" % A["wall_s"])
check("A_graph_nodes", len(A["nodes"]) == len(PER_SUBJECT) * N + 1,
      "%d executed-graph nodes; %d per subject x %d + 1 join = %d" % (len(A["nodes"]), len(PER_SUBJECT), N, len(PER_SUBJECT) * N + 1))
check("A_graph_edges", len(A["edges"]) == 9 * N, "%d edges; 9 per subject x %d = %d" % (len(A["edges"]), N, 9 * N))
ev = collections.defaultdict(lambda: collections.defaultdict(list))
for e in cb:
    ev[e["dir"]][e["status"]].append(e["t"])
once = [d for d in A["nodes"] if len(ev[d]["start"]) == 1 and len(ev[d]["end"]) == 1]
exc = sum(len(v["exception"]) for v in ev.values())
check("A_each_node_once", len(once) == len(A["nodes"]) and exc == 0 and len(ev) == len(A["nodes"]),
      "%d of %d nodes started and ended exactly once; %d exceptions; %d distinct node dirs seen"
      % (len(once), len(A["nodes"]), exc, len(ev)))
crash = glob.glob(LOG + "/crash/*")
check("A_no_crash_files", not crash, "%d crash files" % len(crash))
se, st = side_effects("A"), starts("A")
check("A_side_effects_match_callbacks",
      se["mask"] == st["mask"] == N and se["stats"] == st["stats"] == N and se["join"] == st["join"] == 1,
      "Function-node side effects mask/stats/join %d/%d/%d; nipype's start callbacks %d/%d/%d"
      % (se["mask"], se["stats"], se["join"], st["mask"], st["stats"], st["join"]))
bad = [(u, v) for u, v in A["edges"] if not (ev[u]["end"] and ev[v]["start"] and ev[v]["start"][0] >= ev[u]["end"][0])]
check("A_dependency_order", not bad, "%d of %d edges: the child started before its parent ended" % (len(bad), len(A["edges"])))
pts = sorted([(ev[d]["start"][0], 1) for d in once] + [(ev[d]["end"][0], -1) for d in once], key=lambda x: (x[0], x[1]))
cur = peak = 0
for _, s in pts:
    cur += s
    peak = max(peak, cur)
check("A_concurrency_le_nprocs", peak <= nprocs, "at most %d nodes running at once; n_procs = %d" % (peak, nprocs))
dur = collections.defaultdict(float)
for d in once:
    dur[os.path.basename(d)] += ev[d]["end"][0] - ev[d]["start"][0]
busy = sum(dur.values())
rec("A_node_seconds", ", ".join("%s %.0f" % (k, dur[k]) for k in PER_SUBJECT + ["join"]))
rec("A_pool_occupancy", "%.3f (sum of node durations / (wall x n_procs))" % (busy / (A["wall_s"] * nprocs)))
mx = collections.defaultdict(int)
for e in execlog:
    if A["t0"] <= e["t0"] <= A["t1"] and "maxrss_kib" in e:
        mx[e["node"]] = max(mx[e["node"]], e["maxrss_kib"])
rec("A_pool_worker_peak_rss_mib", "%.0f (ru_maxrss read inside each Function node: a pool worker's lifetime peak, "
    "so it covers every node that worker ran before, dti included)" % (max(mx.values()) / 1024))

rows = [json.loads(l) for l in open("%s/A.cohort.jsonl" % LOG) if l.strip()]
rid = [r["id"] for r in rows]
check("A_join_rows", len(rows) == N and len(set(rid)) == N and set(rid) == set(ids),
      "%d rows, %d distinct ids, set == subjects: %s" % (len(rows), len(set(rid)), set(rid) == set(ids)))
wired = [r for r in rows if r["dwi"] == "sub-%s_ses-BAS1_dwi.nii.gz" % r["id"]]
check("A_rows_wired_to_own_subject", len(wired) == N, "%d of %d rows computed from their own subject's scan" % (len(wired), N))
check("A_fa_float32_zero_outside_mask",
      all(r["fa_dtype"] == "float32" for r in rows) and max(r["fa_outside_max"] for r in rows) == 0.0,
      "FA float32 in %d rows; max |FA| outside the mask %g" % (sum(r["fa_dtype"] == "float32" for r in rows),
                                                              max(r["fa_outside_max"] for r in rows)))
afni = max(r["afni_rel"] for r in rows)
check("A_afni_tstat_vs_numpy", afni < 1e-6, "3dTstat -mean vs numpy, max relative %.3e (bound 1e-6)" % afni)
fa_med = np.array([r["fa_med"] for r in rows])
rec("A_fa_median_cohort", "%.4f (range %.4f - %.4f)" % (np.median(fa_med), fa_med.min(), fa_med.max()))

# ---- A vs measurements/neuroimaging-real: the same computation, driven by xargs there ------
n_mask = n_fa_eq = n_md_eq = 0
fa_ulp = md_ulp = 0.0
nvox = 0
for r in rows:
    sid = r["id"]
    d = "/var/tmp/wf/dti/_subject_id_%s" % sid
    ref = np.load("/var/tmp/ref/%s.npz" % sid)
    shp = tuple(ref["shape"])
    rm = np.unpackbits(ref["mask"])[:int(np.prod(shp))].reshape(shp).astype(bool)
    m = np.asarray(nib.load(d + "/mask/mask.nii.gz").dataobj) > 0
    n_mask += int(m.shape == rm.shape and np.array_equal(m, rm))
    fa = np.asarray(nib.load(glob.glob(d + "/dti/*_fa.nii.gz")[0]).dataobj)[rm]
    md = np.asarray(nib.load(glob.glob(d + "/dti/*_md.nii.gz")[0]).dataobj)[rm]
    rf, rd = ref["fa_wls"].astype(np.float32), ref["md_wls"].astype(np.float32)
    n_fa_eq += int(np.array_equal(fa, rf))
    n_md_eq += int(np.array_equal(md, rd))
    fa_ulp = max(fa_ulp, float(np.max(np.abs(fa.astype(np.float64) - rf) / np.spacing(np.abs(rf)))))
    md_ulp = max(md_ulp, float(np.max(np.abs(md.astype(np.float64) - rd) / np.spacing(np.abs(rd)))))
    nvox += int(rm.sum())
check("ref_mask_identical", n_mask == N, "%d of %d masks bit-identical to neuroimaging-real's" % (n_mask, N))
check("ref_fa_within_1ulp", fa_ulp <= 1.0,
      "WLS FA vs neuroimaging-real's (rounded to float32): max %.2f float32 ulp over %d voxels; %d of %d subjects bit-identical"
      % (fa_ulp, nvox, n_fa_eq, N))
check("ref_md_within_1ulp", md_ulp <= 1.0, "WLS MD likewise: max %.2f ulp; %d of %d bit-identical" % (md_ulp, n_md_eq, N))

# ---- B: Linear, fresh base_dir, a subset -- outputs must be the same bytes -----------------
B = run("B")
rec("B_wall_s", "%.1f (%d subjects, Linear)" % (B["wall_s"], len(lin_ids)))
same = 0
for sid in lin_ids:
    a, b = "/var/tmp/wf/dti/_subject_id_%s" % sid, "/var/tmp/wfl/dti/_subject_id_%s" % sid
    files = ["mask/mask.nii.gz", "tstat/mean.nii.gz"] + ["dti/" + os.path.basename(p) for p in sorted(glob.glob(a + "/dti/*.nii.gz"))]
    same += int(all(np.array_equal(np.asarray(nib.load(a + "/" + f).dataobj), np.asarray(nib.load(b + "/" + f).dataobj)) for f in files)
                and len(files) == 8)
lrows = {json.loads(l)["id"]: json.loads(l) for l in open("%s/B.cohort.jsonl" % LOG) if l.strip()}
arow = {r["id"]: r for r in rows}
check("B_linear_equals_multiproc", same == len(lin_ids) and all(lrows[s] == arow[s] for s in lin_ids) and len(lrows) == len(lin_ids),
      "%d of %d subjects: all 8 maps array-identical and the stats row equal" % (same, len(lin_ids)))

# ---- C: unchanged re-run -- reused, not recomputed ------------------------------------------
C = run("C")
sc, ec = starts("C"), side_effects("C")
rec("C_wall_s", "%.1f" % C["wall_s"])
check("C_unchanged_rerun_reuses_all", not C["rewritten"] and sum(ec.values()) == 0,
      "outputs rewritten: %s; Function side effects %s; nodes submitted (start callbacks) %s"
      % (C["rewritten"] or "none", dict(ec) or "none", dict(sc) or "none"))
crow = [json.loads(l) for l in open("%s/C.cohort.jsonl" % LOG) if l.strip()]
check("C_cohort_unchanged", crow == rows, "the join's rows equal phase A's: %s" % (crow == rows))

# ---- D: a parameter of `stats` changed -- exactly the downstream nodes re-run -------------
D = run("D")
sd, ed = starts("D"), side_effects("D")
rec("D_wall_s", "%.1f" % D["wall_s"])
check("D_change_reruns_only_downstream", D["rewritten"] == {"join": 1} and ed == {"stats": N, "join": 1},
      "Function side effects %s, outputs rewritten %s (expected stats %d + join 1, no map rewritten); start callbacks %s"
      % (dict(ed), D["rewritten"], N, dict(sd)))
drow = [json.loads(l) for l in open("%s/D.cohort.jsonl" % LOG) if l.strip()]
strip = lambda rs: [{k: v for k, v in r.items() if k != "tag"} for r in rs]
check("D_new_value_propagated", all(r["tag"] == "t2" for r in drow) and strip(drow) == strip(rows),
      "%d rows carry the new tag; every other field equals phase A's: %s" % (sum(r["tag"] == "t2" for r in drow), strip(drow) == strip(rows)))

# ---- E: the mechanism behind a Function node that will not cache -------------------------
hp = {m: sum(1 for _ in open("/var/tmp/hp-%s/exec.jsonl" % m)) for m in ("file", "dir")}
check("E_file_path_input_defeats_reuse", hp["file"] == 2 and hp["dir"] == 1,
      "two identical runs, fresh interpreters: %d executions when the node's input names the log FILE it appends to, "
      "%d when it names the log's directory" % (hp["file"], hp["dir"]))

rec("assertions_failed", len(fails))
if fails:
    rec("failed", ", ".join(fails))
    sys.exit(1)
print("NIPYPE-REAL OK")
