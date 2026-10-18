"""measurements/lammps-real: the assertions, from LAMMPS' own logs.
   check.py <ranks> <x> <y> <z> <steps>       (run in /tmp after run.sh's four LAMMPS runs)
Writes /tmp/out/results.tsv (key, value) for the cross-chip comparison."""
import re
import sys

NP, X, Y, Z, STEPS = (int(a) for a in sys.argv[1:6])
N = X * Y * Z
OUT = "/tmp/out/"
fails = []
res = []


def rec(key, value, note=""):
    res.append((key, str(value)))
    print("%-34s %s%s" % (key, value, ("   " + note) if note else ""))


def check(name, ok, detail):
    status = "ok  " if bool(ok) else "FAIL"
    print("%s %-44s %s" % (status, name, detail))
    if not bool(ok):
        fails.append(name)


def text(f):
    try:
        return open(OUT + f, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


# Upstream's committed reference: bench/log.15Jul25.rhodo.fixed.g++.1 at patch_22Jul2025,
# step 0 (identical in its 4-processor log). Printed to 4 decimals.
REF = {"KinEng": 21444.8313, "PotEng": -46801.0370, "E_bond": 2537.9940, "E_angle": 10921.3742,
       "E_dihed": 5211.7865, "E_impro": 213.5116, "E_vdwl": -2307.8634, "E_coul": 207025.8934,
       "E_long": -270403.7333, "Press": -149.3300, "Volume": 307995.0335}
REF_TOT = -25356.2057
REF_GRID, REF_G = "25 32 32", "0.24883488"
REF_TOPO = {"bonds": 27723, "angles": 40467, "dihedrals": 56829, "impropers": 1034}
# Extensive under replication of a periodic cell: every energy term and the volume.
EXT = ["KinEng", "PotEng", "E_bond", "E_angle", "E_dihed", "E_impro", "E_vdwl", "E_coul", "E_long", "Volume"]
# Terms that do not depend on the Ewald split, so they are extensive even with PPPM's automatic choice.
EWALD_FREE = ["KinEng", "E_bond", "E_angle", "E_dihed", "E_impro", "E_vdwl", "Volume"]


def custom_step0(log):
    """Step-0 row of a `thermo_style custom` run 0, as {column: float}."""
    lines = text(log).splitlines()
    for i, ln in enumerate(lines):
        t = ln.split()
        if t and t[0] == "Step" and i + 1 < len(lines):
            v = lines[i + 1].split()
            try:
                return dict(zip(t, (float(x) for x in v)))
            except ValueError:
                return {}
    return {}


def multi_blocks(log):
    """{step: ({term: value}, cpu_seconds)} from `thermo_style multi` output."""
    out, cur = {}, None
    for ln in text(log).splitlines():
        m = re.match(r"-+ Step\s+(\d+) -+ CPU =\s+([0-9.eE+-]+) \(sec\)", ln)
        if m:
            cur = int(m.group(1))
            out[cur] = ({}, float(m.group(2)))
            continue
        if cur is not None and "=" in ln:
            for k, v in re.findall(r"(\w+)\s+=\s+([-0-9.eE+]+)", ln):
                out[cur][0][k] = float(v)
        elif cur is not None and ln.strip() and "=" not in ln:
            cur = None
    return out


print("== 1. upstream's reference: the 32,000-atom system, step 0, automatic PPPM ==")
base_auto = custom_step0("base-auto.log")
la = text("base-auto.log")
g = re.search(r"G vector \(1/distance\) = (\S+)", la)
grid = re.search(r"^  grid = (\d+ \d+ \d+)", la, re.M)
check("PPPM grid == upstream's", grid and grid.group(1) == REF_GRID, grid.group(1) if grid else "missing")
check("PPPM g_ewald == upstream's", g and g.group(1) == REF_G, g.group(1) if g else "missing")
for k, ref in REF.items():
    v = base_auto.get(k)
    rec("ref_step0_" + k, "%.10f" % v if v is not None else "missing")
    check("step-0 %s == upstream reference" % k, v is not None and abs(v - ref) <= 1e-4,
          "%.6f vs %.4f (diff %.2e)" % (v, ref, v - ref) if v is not None else "missing")

print("== 2. extensivity: replicated %dx%dx%d at the same g_ewald and a mesh scaled with it ==" % (X, Y, Z))
bf, rf = custom_step0("base-fixed.log"), custom_step0("rep-fixed.log")
gr = re.search(r"^  grid = (\d+ \d+ \d+)", text("rep-fixed.log"), re.M)
rec("rep_fixed_grid", gr.group(1) if gr else "missing")
worst = 0.0
for k in EXT:
    if k in bf and k in rf and bf[k] != 0:
        rel = abs(rf[k] / N - bf[k]) / abs(bf[k])
        worst = max(worst, rel)
        rec("ext_rel_" + k, "%.3e" % rel, "(%.12f per cell vs %.12f)" % (rf[k] / N, bf[k]))
    else:
        check("extensivity %s" % k, False, "missing")
rec("ext_rel_worst", "%.3e" % worst)
check("every term, replicated == %d x base" % N, bool(bf) and bool(rf) and worst <= 1e-10, "worst relative %.3e (bound 1e-10)" % worst)
if "Press" in bf and "Press" in rf:
    rec("press_base_vs_rep", "%.6f vs %.6f" % (bf["Press"], rf["Press"]), "(intensive; reported)")

print("== 3. the production run: %d atoms, %d steps, %d ranks ==" % (32000 * N, STEPS, NP))
lp = text("lmp-real.log")
ver = lp.splitlines()[0] if lp else ""
rec("lammps_version", ver)
check("LAMMPS is the pinned 22 Jul 2025 release", "22 Jul 2025" in ver, ver)
m = re.search(r"with (\d+) MPI tasks", lp)
check("MPI ranks used == launched", m and int(m.group(1)) == NP, "%s want %d" % (m.group(1) if m else "missing", NP))
loop = re.search(r"Loop time of (\S+) on (\d+) procs for (\d+) steps with (\d+) atoms", lp)
check("atoms == 32,000 x %d" % N, loop and int(loop.group(4)) == 32000 * N, loop.group(4) if loop else "missing")
check("steps == %d" % STEPS, loop and int(loop.group(3)) == STEPS, loop.group(3) if loop else "missing")
for kind, base in REF_TOPO.items():
    got = re.findall(r"^\s+(\d+) %s$" % kind, lp, re.M)
    check("%s == %d x %d" % (kind, N, base), got and int(got[-1]) == N * base, got[-1] if got else "missing")
check("completed (Total wall time printed)", "Total wall time:" in lp, "")
blocks = multi_blocks("lmp-real.log")
s0 = blocks.get(0, ({}, 0))[0]
for k in EWALD_FREE:
    v = s0.get(k)
    check("step-0 %s / %d == upstream reference" % (k, N), v is not None and abs(v / N - REF[k]) <= 1e-4,
          "%.6f vs %.4f" % (v / N, REF[k]) if v is not None else "missing")
for k in ("E_coul", "E_long", "PotEng", "TotEng"):
    v = s0.get(k)
    ref = REF_TOT if k == "TotEng" else REF[k]
    if v is not None:
        rec("prod_step0_%s_per_cell_minus_ref" % k, "%.4f" % (v / N - ref), "(Ewald split differs with the grid; reported)")
for k in ("TotEng", "PotEng", "E_coul", "E_long", "KinEng", "Temp", "Press", "Volume"):
    if k in s0:
        rec("prod_step0_" + k, "%.4f" % s0[k])
last = max(blocks) if blocks else None
if last is not None:
    for k in ("TotEng", "PotEng", "Temp", "Press", "Volume"):
        rec("prod_final_" + k, "%.4f" % blocks[last][0].get(k, float("nan")), "(path; reported)")
    T = blocks[last][0].get("Temp")
    check("final temperature 280..330 K (NPT at 300 K)", T is not None and 280 < T < 330, "%s" % T)
    half = STEPS // 2
    if half in blocks and blocks[half][1] > 0 and blocks[last][1] > blocks[half][1]:
        r1 = half / blocks[half][1]
        r2 = (last - half) / (blocks[last][1] - blocks[half][1])
        rec("steps_per_s_first_half", "%.4f" % r1)
        rec("steps_per_s_second_half", "%.4f" % r2, "(ratio %.3f; homogeneity of the step cost, reported)" % (r2 / r1))
perf = re.search(r"Performance: (\S+) ns/day, (\S+) hours/ns, (\S+) timesteps/s, (\S+) (\S+)", lp)
if perf:
    rec("ns_per_day", perf.group(1))
    rec("timesteps_per_s", perf.group(3))
    rec("atom_step_rate", "%s %s" % (perf.group(4), perf.group(5)))
check("ns/day > 0", perf and float(perf.group(1)) > 0, perf.group(1) if perf else "missing")
if loop:
    rec("loop_time_s", loop.group(1))
for pat, key in ((r"Neighbor list builds = (\d+)", "neighbor_builds"), (r"Dangerous builds = (\d+)", "dangerous_builds"),
                 (r"Per MPI rank memory allocation \(min/avg/max\) = (.*)", "mem_per_rank_mbytes"),
                 (r"Total wall time: (\S+)", "lmp_total_wall"), (r"([0-9.]+)% CPU use", "cpu_use_pct")):
    mm = re.findall(pat, lp)
    if mm:
        rec(key, mm[-1].strip())
for ln in text("footprint.txt").splitlines():
    k, _, v = ln.partition("\t")
    rec(k, v)

with open(OUT + "results.tsv", "w") as fh:
    for k, v in res:
        fh.write("%s\t%s\n" % (k, v))
print("CHECKS: %d failed" % len(fails) + ("" if not fails else ": " + "; ".join(fails)))
sys.exit(1 if fails else 0)
