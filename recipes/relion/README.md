---
tool: relion
tool_version: 5.1.0
image: quay.io/aarchbio/relion@sha256:74aef38176ce4b9929f1312bb977c54c06994329e63c94fc3accce44ea844119
spawn_version: 0.104.0
---
# RELION — cryo-EM post-processing of a real 2.75 Å reconstruction

`relion_postprocess` turns two unfiltered half-maps and a solvent mask from a real RELION 5 refinement into a sharpened map plus a resolution estimate — and reproduces, value for value, the output the depositors themselves got.

> **What this covers.** `relion_postprocess`: masked gold-standard FSC, phase randomization, Rosenthal–Henderson weighting, automatic B-factor sharpening. It does **not** run 3D refinement or classification — that is where cryo-EM's compute cost lives, it's GPU-bound, and it's Round Two on x86. So this is one step of a pipeline, not the pipeline — but it answers "does RELION run correctly on Graviton4" unusually strongly (below).

## Run it

```bash
relion_postprocess --i run_half1_class001_unfil.mrc --mask mask3d.mrc \
  --angpix 0.968 --auto_bfac --autob_lowres 10 --o /tmp/postprocess
# → FinalResolution 2.747871 Å, matching the depositors' postprocess.star exactly
```

One task — post-processing is a single invocation, and the two half-maps *are* the input (no expensive reusable intermediate to split out, unlike [salmon](../salmon/README.md)'s index).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| EMPIAR-10581's half-maps + mask (a complete archived RELION 5 project) | your own `Refine3D` half-maps + mask | the fixture is a *real deposited reconstruction* whose published `postprocess.star` is in the same bucket — that's what turns this from "plausible numbers" into an exact [reproduction](../../practices/reference-from-tests.md). |
| the archived job's flags (`--angpix 0.968 --auto_bfac --autob_lowres 10`) | your job's flags | kept identical because the whole point is that the numbers come out the same. |
| **`--o /tmp/postprocess`** (absolute) | keep it absolute | **load-bearing workaround** — a bioconda packaging bug (below) makes a bare relative prefix fatal at the end of an otherwise-correct run. |
| the `half1` filename | your half1 (don't rename half2) | RELION derives the half2 name by substituting `half1`→`half2`, so the pair must keep those tokens. |

Deterministic (FFTs, a shell-wise correlation, a straight-line fit — no sampling, no thread flag) — **nothing is determinism scaffolding**. **Leave the fixture:** it's a real 440³ reconstruction reproducing a published result exactly; there is no smaller or larger input that makes the check more legible. Leave-it.

## Shape, size, cost

One task, **`m8g.large`** (2 vCPU / 8 GiB), TTL 20m, cap $0.05. Measured: **25.3s** wall, peak **3.82 GiB RSS** (three 325 MiB maps + FFT workspace over a 440³ box). That measurement picks the box both ways — 3.82 GiB rules out `c8g.large`'s 4 GiB, and the absence of a thread flag rules out paying for more vCPUs, so `m8g.large`, not `m8g.xlarge`. TTL is sized by the ~975 MiB cross-region **staging**, not the 25 s of compute. Recorded command window **94s** (RELION 25 s; the rest is stage-in + Docker install + the 0.40 GiB pull). **These timings are not compute cost.**

<details>
<summary>As shipped: the reproduction, the ghostscript workaround, pins, the 15-value smoke check, run + verify</summary>

### The reproduction — the strongest check in the catalog

The RODA dataset `cryoem-spa-workflow-records-public` archives *whole* RELION project directories, so the bucket holds both the post-processing job's **inputs** (`Refine3D/job044`'s half-maps, `Import/job022`'s mask) and the **output** the depositors got (`PostProcess/job045/postprocess.star`). So this is a reproduction: the same arithmetic on the same bytes, done by RELION 5.0.0 on x86 and RELION 5.1.0 on arm64, compared value for value. It matches exactly — all **221 FSC shells × 8 columns** at `max|diff| = 0`.

### The ghostscript workaround (why `--o` must be absolute)

`relion_postprocess` writes a PDF log at the end by shelling out to Ghostscript. Bioconda's `relion` recipe lists `ghostscript` under `host:` but **not** `run:`, so `gs` is absent at run time; RELION falls back to touching an empty PDF at `dirname(--o)/logfile.pdf`, and with a bare prefix like `pp` that directory doesn't exist, so the touch is fatal — the task would die holding correct output. Passing `--o /tmp/postprocess` puts the fallback in a writable dir and the run exits 0. The two `gs`-related lines in the log are therefore *expected*, which is why the smoke check counts non-Ghostscript `ERROR` lines. (Filed as `bioconda/bioconda-recipes#68838` with the one-line fix; the absolute `--o` is the local mitigation.)

### Pins (data tier: RODA — every byte traces to `s3://cryoem-spa-workflow-records-public`)

| | |
|---|---|
| image | `quay.io/aarchbio/relion@sha256:74aef38176ce…` (tag `5.1.0--h4615e1f_0`, double precision, cosign-signed, `linux/arm64` only) |
| half-map 1 | `Refine3D/job044/run_half1_class001_unfil.mrc` — `sha256:8fe4881f…` (340,737,024 B, 440³ float32) |
| half-map 2 | `run_half2_class001_unfil.mrc` — `sha256:69bdea0c…` |
| mask | `Import/job022/mask3d_emd_0731_apix0o968_d440.mrc` — `sha256:07939784…` |
| reference | `PostProcess/job045/postprocess.star` — `sha256:b0611c8e…` (221-shell FSC table) |

All four from one prefix (KEK SBRC, CC0, `ap-northeast-1`); `stage-inputs.sh` cross-region-copies once and re-checks each MRC header (`MAP ` at byte 208, mode 2, cubic box) independently of the sums.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| **FSC table vs reference** | max\|diff\| ≤ 1e-6 over 221×8 values | **0.000e+00** |
| FSC columns / shells | 8 names / exactly 221 (= 440/2+1) | 8 / 221 |
| shell 0 FSC | exactly 1.000000 (self-correlation, algorithmic) | 1.000000 |
| `FinalResolution` | == 2.747871 and == reference | 2.747871 |
| `BfactorUsedForSharpening` | == −50.25665 and == reference | −50.25665 |
| `ParticleBoxFractionSolventMask` | == 74.84736 and == reference | 74.84736 |
| `RandomiseFrom` | == 17.746667 and == reference | 17.746667 |
| Guinier slope / intercept / r | == −12.56416 / −14.36036 / 0.870246 | all match |
| resolution vs Nyquist | > 1.936 Å (algorithmic floor at 0.968 Å/px) | 2.747871 Å |
| both output maps | 440³, mode 2, `MAP `, 340,737,024 B | all match |
| non-Ghostscript `ERROR` lines | exactly 0 | 0 |

The FSC-table comparison does the real work: the seven header scalars are functions of the curve, so a wrong map/mask/read could land one by luck — it cannot land 1,768 values. **Parsing note:** `postprocess.star` holds `data_general`, then `data_fsc`, then **`data_guinier`** — a parser that reads to EOF counts 442 rows, not 221, and silently compares the wrong tables.

### Run + verify

```sh
recipes/relion/stage-inputs.sh          # once; cross-region copy, ~1 GiB
spawn task run --spec recipes/relion/01-postprocess.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/relion/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect six objects, two of them 340,737,024 B. Re-run: bump the `-r1` suffix. (The `EPERM`-on-unlinking-a-staged-input trap that bit earlier recipes is closed as of spawn 0.103.1 — the wrapper now runs as the caller's uid and chowns staged paths — but this recipe deletes nothing regardless, at 1.6 GiB of ~6.1 GiB.)

</details>
