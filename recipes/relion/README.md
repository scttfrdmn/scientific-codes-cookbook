# RELION — cryo-EM post-processing of a real 2.75 Å reconstruction

> **What this recipe does and does not cover.** It runs `relion_postprocess`: the
> masked gold-standard FSC, phase randomization, Rosenthal & Henderson weighting and
> automatic B-factor sharpening that turn two half-maps into a final, sharpened map
> plus a resolution estimate. It does **not** run 3D refinement or classification,
> which is where cryo-EM's compute cost actually lives and which is GPU-bound —
> Round Two, on x86. So this answers "does RELION run correctly on Graviton4," and it
> answers it unusually strongly (see the smoke check), but it is one step of a
> pipeline, not the pipeline.

One task. `relion_postprocess` reads two unfiltered half-maps and a solvent mask from
a real RELION 5 refinement and writes a sharpened map, a masked map, and a
`postprocess.star` carrying the FSC curve and every derived scalar.

## Why one task

RELION is one tool, and post-processing is one invocation of it. There is no
expensive reusable intermediate to split out the way `recipes/salmon` splits its
index: the two half-maps *are* the input, and everything downstream is written in a
single pass. A second task would buy nothing and cost another boot.

## Why this input, and what makes the check so strong

The input is a complete archived RELION 5.0.0 project: EMPIAR-10581 refined by KEK
SBRC and deposited in the RODA dataset **`cryoem-spa-workflow-records-public`**, which
archives whole RELION and cryoSPARC project directories rather than just their
deposited results. That means the bucket holds both the *inputs* of a
post-processing job (`Refine3D/job044`'s half-maps, `Import/job022`'s mask) and the
*output* the depositors themselves got (`PostProcess/job045/postprocess.star`).

So the recipe is not "RELION produced plausible numbers." It is a **reproduction**:
the same arithmetic, on the same bytes, done by RELION 5.0.0 on x86 and by RELION
5.1.0 on arm64, compared value for value. `relion_postprocess` is deterministic —
FFTs, a shell-wise correlation and a straight-line fit, with no sampling and no
thread flag to perturb the order of operations — so there is a right answer to land
on, and the check is whether Graviton4 lands on it.

It does, exactly. All **221 FSC shells × 8 columns** agree to `max|diff| = 0`, and
every scalar in the header matches: final resolution 2.747871 Å, sharpening B-factor
−50.25665, Guinier fit r 0.870246, solvent fraction 74.84736.

## `--o` must be an absolute path, and that is a bug we are working around

`relion_postprocess` writes a PDF log at the end of the run by shelling out to
Ghostscript. Bioconda's `relion` recipe lists `ghostscript` under
`requirements: host:` but **not** under `requirements: run:`, so `gs` is absent at
run time. RELION handles a missing `gs` by falling back to touching an empty PDF —
but at `dirname(--o)/logfile.pdf`, and with a bare prefix like `pp` that is a
directory which does not exist, so the touch is fatal:

```
sh: 1: gs: not found
 ERROR in executing: gs -sDEVICE=pdfwrite ... -sOutputFile=pp/logfile.pdf ...
 Filename::touch ERROR: Cannot open file: pp/logfile.pdf
```

By then the science is already on disk. The task would die holding correct output —
a failure with nothing wrong in it. Passing `--o /tmp/postprocess` puts the fallback
in `/tmp`, which exists and is writable, and the run exits 0. The two remaining
`gs`-related lines in `postprocess.log` are therefore *expected*, which is why the
smoke check counts non-Ghostscript `ERROR` lines rather than grepping for `ERROR`.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/relion@sha256:74aef38176ce4b9929f1312bb977c54c06994329e63c94fc3accce44ea844119` |
| | tag `5.1.0--h4615e1f_0`, RELION 5.1.0 (double precision), cosign-signed, manifest is `linux/arm64` only |
| half-map 1 | `Refine3D/job044/run_half1_class001_unfil.mrc`, byte for byte |
| | `sha256:8fe4881f98b31d0acb070229afc91d53fa9b43efaf114baa7548aebfe7a6c76e` (340,737,024 B, 440³ float32) |
| half-map 2 | `Refine3D/job044/run_half2_class001_unfil.mrc`, byte for byte |
| | `sha256:69bdea0c8e1d204bc420cbc6e3d44639490f59eb5202a65fc0870e291a644617` (340,737,024 B, 440³ float32) |
| mask | `Import/job022/mask3d_emd_0731_apix0o968_d440.mrc`, byte for byte |
| | `sha256:07939784952a5f4592af1daeb1c6b6ca2ad514f252edca62fc883680b834166d` (340,737,024 B, 440³ float32) |
| reference result | `PostProcess/job045/postprocess.star`, byte for byte |
| | `sha256:b0611c8ef5a082fb350f78f1bd9cac17eaa84367b9828840d11b53df7c1b6f71` (39,500 B, 221-shell FSC table) |

All four come from one prefix:
`s3://cryoem-spa-workflow-records-public/ArXiv/EMPIAR/EMPIAR10581/260123_tmoriya_relion5o0o0_res2o75_run05_nosplit_FSx9600`
(KEK SBRC "Cryo-EM SPA Workflow Records", CC0, `ap-northeast-1`).

**Data tier: RODA.** The top tier — an AWS Open Data bucket, copied byte for byte,
so the sha256 sums above are pins on the upstream objects themselves and nothing is
derived. The source bucket is in `ap-northeast-1` and this cookbook's bucket is in
`us-east-1`, so `stage-inputs.sh` does a cross-region copy **once, at staging time**,
not on the box.

`stage-inputs.sh` re-checks the MRC headers independently of the sums: the literal
`MAP ` at byte 208, mode 2 (float32), a cubic box, and all three maps agreeing on
that box. A truncated or wrong-box map fails at staging rather than on the box.

The flags are the archived job's own — `--angpix 0.968 --auto_bfac --autob_lowres 10`
— because the point of the recipe is that the numbers come out the same. RELION
derives the half2 filename from half1 by substituting `half1` → `half2`, so those two
filenames must not be renamed.

## Smoke check

Measured in this image, on this input, before any launch. **Nothing here is a band on
an observed value except the resolution equality itself**, and that is an equality
against a published number, not a tolerance around a hunch.

| observable | assertion | observed |
|---|---|---|
| FSC table vs reference | max\|diff\| ≤ 1e-6 over 221×8 values | **0.000e+00** |
| FSC columns | same 8 names as reference | 8, identical |
| FSC shells | exactly 221 (= box 440 / 2 + 1) | 221 |
| shell 0 FSC | exactly 1.000000 | 1.000000 |
| `FinalResolution` | == 2.747871 and == reference | 2.747871 |
| `BfactorUsedForSharpening` | == −50.25665 and == reference | −50.25665 |
| `ParticleBoxFractionSolventMask` | == 74.84736 and == reference | 74.84736 |
| `RandomiseFrom` | == 17.746667 and == reference | 17.746667 |
| `FittedSlopeGuinierPlot` | == −12.56416 and == reference | −12.56416 |
| `FittedInterceptGuinierPlot` | == −14.36036 and == reference | −14.36036 |
| `CorrelationFitGuinierPlot` | == 0.870246 and == reference | 0.870246 |
| resolution vs Nyquist | > 1.936 Å | 2.747871 Å |
| `postprocess.mrc` | 440³, mode 2, `MAP `, 340737024 B | all four |
| `postprocess_masked.mrc` | 440³, mode 2, `MAP `, 340737024 B | all four |
| non-Ghostscript `ERROR` lines | exactly 0 | 0 |

Two of these earn their place for reasons worth naming.

**Shell 0 is exactly 1.** The zero-frequency term correlates with itself by
construction, so this is algorithmic — no band, no headroom, and it cannot go flaky.

**Resolution cannot be finer than Nyquist.** At 0.968 Å/px that is 1.936 Å. Also
algorithmic. It is the check that survives if the reference ever has to be swapped:
whatever else changes, a `postprocess.star` claiming 1.2 Å from this data is wrong.

The FSC-table comparison is the one doing the real work, and it is strictly stronger
than any scalar. The seven header values are functions of the curve, so a wrong map,
a wrong mask, a truncated read or a broken FFT could in principle land one of them by
luck; it cannot land 1,768 values. Parsing note, because it bit the first version of
both this check and `stage-inputs.sh`: `postprocess.star` holds `data_general`, then
`data_fsc`, then **`data_guinier`**. A parser that reads to EOF counts 442 rows, not
221, and silently compares the wrong tables.

## Resources, and what the timings mean

2 vCPU / 8 GiB, `m8g` (resolves to `m8g.large`), TTL 20m, cap $0.05. Measured work:
**25.3s** wall, 16.5s user — essentially serial, because `relion_postprocess` has no
thread flag. Peak **3.82 GiB RSS** (4,010,088 KiB), for three 325 MiB maps plus the
FFT workspace over a 440³ box.

That measurement is what picks the box, both ways. 3.82 GiB rules out `c8g.large`
(2 vCPU / 4 GiB) outright, so the family is `m8g` at 4 GiB/vCPU; and the absence of a
thread flag rules out paying for more vCPUs, so it is `large` rather than `xlarge`.
Guessing `m8g.xlarge` "to be safe" would have doubled the rate for nothing.

**These timings are not compute cost.** Boot, Docker install, image pull and staging
~975 MiB of input come first, and they dominate a 25-second job completely. Read the
number above as "the science takes half a minute", never as a benchmark.

TTL is 20m against ~25s of work — a wide ratio in relative terms, but it is sized by
the staging, not the compute: three 325 MiB objects in, two out, cross-account within
`us-east-1`. `lifecycle.cost_limit: 0.05` states the same ceiling explicitly
(20m × $0.08976/hr ≈ $0.03, rounded up).

The recorded run bears that out. Its whole command window was **94s** (16:10:41 →
16:12:15 UTC), of which RELION was 25s; the instance existed for about three minutes
end to end. The other ~69s is stage-in of 975 MiB, a 62 MB / 11-package Docker install
on the stock AL2023 AMI, and the 0.40 GiB image pull — and it cannot be split further
than that, because `command.log` carries no timestamps. Worth knowing before reading
any timing in this cookbook: on a job this short, the platform's fixed setup is most
of the bill, and *which part* of it is not currently measurable.

Disk: ~1.6 GiB peak — 975 MiB of staged input, 650 MiB of output maps, and a
handful of KiB of EPS and XML — against ~6.1 GiB usable. Nothing is deleted during
the run, and in particular no staged input is deleted.

Worth being precise about *why*, because the reason changed under this recipe. The
`EPERM`-on-unlinking-a-staged-input trap that killed two earlier recipes in this
cookbook is **closed as of spawn 0.103.1**: the wrapper now runs the container as
`--user "$(id -u):$(id -g)"` rather than the image's declared user
([`wrapper.go:310`](https://github.com/spore-host/spawn/blob/main/pkg/taskproto/wrapper.go),
spawn#555) and chowns each staged path to that uid as soon as it lands (spawn#565), so
a container can now unlink its own inputs under sticky `/tmp`. This recipe still
deletes nothing — at 1.6 GiB of ~6.1 GiB there is nothing to gain, and not deleting
staged inputs stays the cheaper habit — but it is no longer *forced*, and
`recipes/salmon` and `recipes/star` describe the older behaviour.

## Running it

```sh
recipes/relion/stage-inputs.sh          # once; cross-region copy, ~1 GiB
spawn task run --spec recipes/relion/01-postprocess.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/relion/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared output
fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail the
task; the bucket listing is the second half of the same check. Expect six objects,
two of them 340,737,024 B.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records. `relion_postprocess` overwrites its own outputs,
so there is no checkpoint guard to defeat.
