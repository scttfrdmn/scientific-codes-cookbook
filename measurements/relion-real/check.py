"""Assertions for measurements/relion-real, run inside the task from /tmp/proj.

    check.py <MPI ranks> <threads per rank>

Asserted: the launch really was NP x NT; the refinement converged and RELION marked the job
successful; no particle was lost; the seeded half-set split equals the depositors' particle for
particle; relion_postprocess reproduces the depositors' PostProcess from their half-maps; and our
own post-processed map is a real high-resolution map. Reported, not asserted: how our refinement
(a path) compares with theirs.
"""
import os
import re
import sys

NP, NT = int(sys.argv[1]), int(sys.argv[2])
fail = []


def check(label, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {label:<40} {detail}")
    if not ok:
        fail.append(label)


def report(label, detail):
    print(f"      {label:<40} {detail}")


def star(path):
    """{block: (scalars dict, column list, rows)} for a RELION star file."""
    blocks, name, cols, rows, scal, in_loop = {}, None, [], [], {}, False
    def close():
        if name is not None:
            blocks[name] = (scal, cols, rows)
    for line in open(path):
        s = line.strip()
        if s.startswith("data_"):
            close()
            name, cols, rows, scal, in_loop = s, [], [], {}, False
        elif s.startswith("loop_"):
            in_loop = True
        elif s.startswith("_rln"):
            p = s.split()
            if in_loop:
                cols.append(p[0])
            elif len(p) > 1:
                scal[p[0]] = p[1]
        elif s and not s.startswith("#") and in_loop and cols:
            rows.append(s.split())
    close()
    return blocks


def col(block, name):
    _, cols, rows = block
    j = cols.index(name)
    return [r[j] for r in rows]


# ---- the launch ----
out = open("Refine3D/job100/run.out").read()
m = re.search(r"Number of MPI processes\s*=\s*(\d+)", out)
check("mpi_ranks", m is not None and int(m.group(1)) == NP,
      f"{m.group(1) if m else 'absent'}  want {NP} (RELION's own count, from run.out)")
# RELION prints the threads line only when there is more than one thread per rank (measured: a
# --j 1 run's MPI setup block lists ranks and hosts and nothing else), so at NT = 1 its absence
# is the expected report.
m = re.search(r"Number of threads per MPI process\s*=\s*(\d+)", out)
check("threads_per_rank", (int(m.group(1)) == NT) if m else NT == 1,
      f"{m.group(1) if m else 'absent'}  want {NT if NT > 1 else 'absent (RELION omits it at 1)'}")
m = re.search(r"Running CPU instructions in (\w+) precision", out)
report("precision", m.group(1) if m else "not printed")

# ---- completion ----
check("converged", "Auto-refine: Refinement has converged, stopping now" in out,
      "'Refinement has converged, stopping now' in run.out")
check("relion_job_exit_success", os.path.exists("Refine3D/job100/RELION_JOB_EXIT_SUCCESS"),
      "RELION_JOB_EXIT_SUCCESS written by --pipeline_control")
iters = re.findall(r"Auto-refine: Iteration=\s*(\d+)", out)
report("iterations", f"{iters[-1] if iters else '?'} (depositors' GPU run: 16)")

# ---- conservation: every particle in, every particle out, each in exactly one half ----
ours = star("Refine3D/job100/run_data.star")["data_particles"]
theirs = star("Refine3D/job019/run_data.star")["data_particles"]
inp = star("Extract/job018/particles.star")["data_particles"]
n_in, n_out = len(inp[2]), len(ours[2])
check("particles_conserved", n_in == n_out == 4452 and
      set(col(ours, "_rlnImageName")) == set(col(inp, "_rlnImageName")),
      f"{n_out} out of {n_in} in, same image names  want 4452 = 4452")
sub = dict(zip(col(ours, "_rlnImageName"), col(ours, "_rlnRandomSubset")))
ref = dict(zip(col(theirs, "_rlnImageName"), col(theirs, "_rlnRandomSubset")))
halves = [sum(1 for v in sub.values() if v == h) for h in ("1", "2")]
check("each_particle_in_one_half", halves[0] + halves[1] == n_out,
      f"half 1: {halves[0]}, half 2: {halves[1]}")
same = sum(1 for k, v in sub.items() if ref.get(k) == v)
check("half_split_equals_depositors", same == len(ref) == n_out,
      f"{same} of {len(ref)} particles in the same half as the depositors' (--random_seed 1697716706)")

# ---- control: the depositors' half-maps through relion_postprocess reproduce their result ----
def fsc_compare(a_path, b_path):
    a, b = star(a_path), star(b_path)
    ah, ac, ar = a["data_fsc"]
    bh, bc, br = b["data_fsc"]
    worst = 0.0
    if ac != bc or len(ar) != len(br):
        return None, a, b
    for x, y in zip(ar, br):
        for u, v in zip(x, y):
            worst = max(worst, abs(float(u) - float(v)))
    return worst, a, b


worst, ctl, dep = fsc_compare("PostProcess/job102/postprocess.star", "PostProcess/job021/postprocess.star")
nshell = len(dep["data_fsc"][2])
check("control_fsc_table_vs_depositors", worst is not None and worst <= 1e-6,
      f"max|diff| = {worst if worst is None else f'{worst:.3e}'} over {nshell} shells x "
      f"{len(dep['data_fsc'][1])} columns  want <= 1e-6")
for key, tol in (("_rlnFinalResolution", 1e-5), ("_rlnBfactorUsedForSharpening", 1e-4)):
    c, d = float(ctl["data_general"][0][key]), float(dep["data_general"][0][key])
    check(f"control_{key[4:]}", abs(c - d) <= tol, f"{c}  want {d} (depositors' PostProcess/job021)")

# ---- our refinement, post-processed the same way ----
pp = star("PostProcess/job101/postprocess.star")
res = float(pp["data_general"][0]["_rlnFinalResolution"])
nyq = 2 * 1.244531
check("our_fsc_shell0_is_unity", abs(float(pp["data_fsc"][2][0][pp["data_fsc"][1].index("_rlnFourierShellCorrelationCorrected")]) - 1) < 1e-9,
      "FSC at zero frequency = 1 (self-correlation)")
check("our_resolution_coarser_than_nyquist", res > nyq, f"{res} A  want > {nyq:.3f} A")
check("our_resolution_gross_guard", res <= 4.0,
      f"{res} A  want <= 4.0 A (a refinement that failed to align sits far coarser)")
dres = float(dep["data_general"][0]["_rlnFinalResolution"])
report("masked_resolution_ours_vs_theirs", f"{res} A vs {dres} A")
try:  # descriptions of the run must never be able to fail it
    report("bfactor_ours_vs_theirs", f"{pp['data_general'][0]['_rlnBfactorUsedForSharpening']} vs "
           f"{dep['data_general'][0]['_rlnBfactorUsedForSharpening']}")
    mo = star("Refine3D/job100/run_model.star")["data_model_general"][0]
    mt = star("Refine3D/job019/run_model.star")["data_model_general"][0]
    report("unmasked_resolution_ours_vs_theirs", f"{mo['_rlnCurrentResolution']} A vs {mt['_rlnCurrentResolution']} A")
except Exception as e:
    report("model_report", f"unreadable: {e!r}")

# ---- map-to-map FSC against the depositors' final map (reported) ----
# relion_image_handler --fsc prints a star table; read it by column name, not position.
rows = []
try:
    for blk in star("/tmp/xfsc.txt").values():
        _, cols, rr = blk
        if "_rlnAngstromResolution" in cols and "_rlnFourierShellCorrelation" in cols:
            ia, iff = cols.index("_rlnAngstromResolution"), cols.index("_rlnFourierShellCorrelation")
            rows = [(float(r[ia]), float(r[iff])) for r in rr if len(r) == len(cols)]
except Exception as e:  # a description of the run must never fail the run
    report("cross_fsc_parse", f"unreadable: {e!r}")
def crossing(th):
    for r, f in rows:
        if r > 0 and f < th:
            return r
    return None
report("cross_fsc_vs_depositors_map", f"{len(rows)} shells; first below 0.5 at {crossing(0.5)} A, "
       f"below 0.143 at {crossing(0.143)} A")

print("CHECK " + ("FAILED: " + ", ".join(fail) if fail else "PASSED"))
sys.exit(1 if fail else 0)
