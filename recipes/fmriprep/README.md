---
tool: fmriprep
tool_version: "25.2.7"
gap_arm64: no arm64 build exists — nipreps/fmriprep:25.2.7 is a single linux/amd64 manifest, as is its base ghcr.io/nipreps/fmriprep-base:20251006, and its pixi environment is solved for linux-64 only (it pins mkl, and needs ANTs, which has no aarch64 build — see recipes/ants) (checked 2026-10-23)
images_x86_64:
  - docker.io/nipreps/fmriprep@sha256:bd222fba3a9f07d6ecaa680c412907945106b846a76cd4fbfd86e61667c435f8
  - docker.io/mambaorg/micromamba@sha256:9a2239d03b9464c761ac87c13f45b37ec0f774db7d1b152a5f9add146bee2e72
last_verified_x86_64: 2026-10-23
last_verified_amd: 2026-10-23
spawn_version: 0.140.0
---
# fMRIPrep — fMRIPrep's own CI run, on x86

Runs fMRIPrep's own ds005 "fasttrack" CI job (BOLD preprocessing onto `fsaverage5` and `fsnative`) on AWS, on Intel and AMD. For anyone preprocessing task or resting-state fMRI. **Bring your own FreeSurfer licence.**

## Run it

```bash
export FS_LICENSE_FILE=~/freesurfer-license.txt   # yours: free at surfer.nmr.mgh.harvard.edu/registration.html
make stage RECIPE=fmriprep                        # once: licence -> your bucket; OSF inputs fetched on AWS
make run   RECIPE=fmriprep ARCH=amd               # m8a.xlarge, ~11 min; ARCH=x86_64 for Intel m8i
make ls    RECIPE=fmriprep ARCH=amd

fmriprep /tmp/ds005 /tmp/out participant -w /tmp/work \
  --fs-license-file /tmp/license.txt --fs-subjects-dir /tmp/freesurfer \
  --derivatives anat=/tmp/smriprep --sloppy --write-graph --mem-mb 14336 \
  --skull-strip-template OASIS30ANTs:res-1 --output-spaces fsaverage5 fsnative \
  --use-plugin /tmp/legacy.yml --nthreads 4 --stop-on-first-crash --notrack -vv
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| **x86 only** | Graviton | **there is no arm64 build of fMRIPrep**: the image, its base and its environment are amd64/linux-64 only, and it needs ANTs, which has none either. |
| the licence | **your own** | FreeSurfer's licence is per registered user, so the recipe cannot ship one. `stage-inputs.sh` copies `$FS_LICENSE_FILE` to your private bucket and never prints it. |
| downsampled ds005, 1 subject | your BIDS dataset — edit `fetch-osf.py` | one subject per task; a cohort is this task [fanned out](../../patterns/job-arrays.md). |
| `--derivatives anat=` + precomputed FreeSurfer | drop both | that is a **full** run: recon-all adds hours per subject, so re-size the TTL and `cost_limit` from a real run. |
| `--sloppy` | drop it | CI's speed setting: low-resolution registration, not for science. |
| the spec's argv | — | the image's entrypoint is `fmriprep` and spawn does not override it, so the task's command **is** fmriprep's argv; input prep happens in staging. |

**Leave the fixture.** It is what fMRIPrep's maintainers run on every commit, and its committed output list is the reference this recipe is checked against. **Scale it** to your data by dropping `--sloppy` and the fasttrack derivatives. Drop the list check too, since it describes this run only; keep the identities.

## Shape, size, cost

Three tasks: a staging task (`c8g.large`, ~1 min) fetches the OSF archives on AWS. fMRIPrep runs on `m8i.xlarge` / `m8a.xlarge` (4 vCPU / 16 GiB, CI's `--nthreads 4 --mem-mb 14336`). A check task runs on `c8i.large`. TTL 2h is only a backstop; `cost_limit` is the guard. These timings are not compute cost: boot, Docker and a 2.65 GB image pull add ~1.6 min to every run ([layout](../../patterns/layout-and-effective-cost.md)).

| chip | instance | cores × threads | fMRIPrep wall | task window | peak memory | spawn's cost |
|---|---|---|---|---|---|---|
| Graviton4 | — | — | **gap: no arm64 build** | | | |
| Intel | `m8i.xlarge` | 2 × 2 | 15m23s | 17m04s | 3.01 GiB | $0.060 |
| **AMD** | `m8a.xlarge` | 4 × 1 | **11m05s** | 12m39s | 2.70 GiB | **$0.051** |

**Take AMD:** 1.39× faster for 15% more per hour, so cheaper per run. Its four vCPUs are full cores, Intel's are two cores with SMT. Peak memory is the container cgroup's `memory.peak`; the tmpfs high-water mark was 1.5 GiB on both. A first run on 32 GiB `r8*.xlarge` boxes measured those numbers, so the box came down to 16 GiB. It keeps CI's 14 GiB `--mem-mb` true.

<details>
<summary>As shipped: fMRIPrep's own CI output list on both chips, plus five exact identities</summary>

### The checks

| observable | assertion | Intel | AMD |
|---|---|---|---|
| **output tree** (CI's `find`, pruning `figures`/`log`/`sourcedata`) | **`diff` against `.circleci/ds005_fasttrack_outputs.txt` at 25.2.7 is empty** | **76 / 76, 0 diff lines** | **76 / 76, 0 diff lines** |
| fMRIPrep version | `dataset_description.json` says `25.2.7` | 25.2.7 | 25.2.7 |
| report | `sub-01.html` says `No errors to report` | yes | yes |
| **timepoints** | input BOLD `dim[4]` == confounds rows == DataArrays in every surface series, per run | 16 == 16 == 16 (both runs, 4 series each) | same |
| **fsaverage5 mesh** | exactly 10·4⁵+2 = **10242** vertices per hemisphere | 10242 | 10242 |
| **fsnative mesh** | vertex count == the subject's own white surface | L 124169, R 122745 | same |

The list is fMRIPrep's own CI check, copied: the same `find … -prune`, the same `diff` and the same committed
reference from the image's tag. It proves every expected derivative exists and nothing unexpected does. The
identities prove the listed files contain the right shapes. A BOLD series truncated in resampling, or projected
onto the wrong mesh, still has the right filename.

### What differs across chips: paths, not the check

The list and every identity agree on both chips. The bytes do not, and the motion estimate is the only
thing that matches exactly. These are reported, not asserted:

| output | Intel vs AMD | Intel, two runs (`r8i`, then `m8i`) |
|---|---|---|
| head-motion parameters (`trans_*`, `rot_*`, `framewise_displacement`) | **bit-identical** | — |
| BOLD→T1w coregistration translation | ≤ 0.049 mm | ≤ 0.025 mm |
| confound signals (`global_signal`, `csf`, `white_matter`, `std_dvars`) | ≤ 2% relative | — |
| confounds column order | differs (same 77 columns) | — |
| listed files byte-identical | 37 of 71 | — |

The coregistration moves between two Intel runs as well as between chips, so it is a path; the chip is not
shown to move it. Compare confounds by column name, never by position.

### Why it is CI's run, and what differs from CI

Inputs, flags and data edit are those of the `ds005` job's "Run full fMRIPrep on ds005 (LegacyMultiProc
plugin)" step in `.circleci/config.yml` at tag `25.2.7`. CI's `nipype.cfg` (`stop_on_first_crash`) becomes
`--stop-on-first-crash`; `FMRIPREP_DEV=1` and `MIGAS_OPTOUT=1` are set as in CI. CI's one edit to the data,
injecting `"PhaseEncodingDirection": "j"` as the sidecar's second line, is applied in staging. The staged run
then reads it from S3. Only `-w /tmp/work` stays on the box.

### Pins

| | data tier |
|---|---|
| fMRIPrep | `docker.io/nipreps/fmriprep@sha256:bd222fba…`, tag `25.2.7`, `linux/amd64` only (vcs-ref `5433029c`). Not cosign-signed, so the digest is the pin |
| check image | `docker.io/mambaorg/micromamba@sha256:9a2239d0…` (the amd64 entry recipes/fsl pins); `check-outputs.sh` is sha256-pinned in the spec |
| `ds005_downsampled.tar.gz` | OSF `fvuh8` file `57f32a42…` (version 3), sha256 `50826b60…`, 7,133,415 B |
| `ds005_derivatives_freesurfer.zip` | OSF `637bab2c…`, sha256 `76ef8d3d…`, 204,615,799 B |
| `ds005_sub-01_smriprep.zip` | OSF `637cf650…`, sha256 `0bb2bf14…`, 398,097,866 B |
| reference list, `legacy.yml` | `.circleci/` at tag `25.2.7`, committed here verbatim |

The sha256s are OSF's own published hashes for these file versions. The staging task checks each download is
an archive before hashing it, because OSF can serve an HTML maintenance page with HTTP 200. It then checks the
hash, and fetches on AWS, never on a laptop.

**Un-defers for arm64 when** nipreps publishes an arm64 image, which needs an aarch64 ANTs
([ants-feedstock#35](https://github.com/conda-forge/ants-feedstock/issues/35)), a linux-aarch64 pixi solve without
`mkl`, and FreeSurfer/AFNI arm64 builds in the base image.

### Run + verify

```sh
make run RECIPE=fmriprep ARCH=x86_64      # and ARCH=amd
aws s3 cp "s3://$(make -s print-bucket)/runs-x86_64/fmriprep/r1/smoke-check.txt" -
```

Fails on any path missing from or added to CI's list, a version other than 25.2.7, a report with errors, a
timepoint mismatch, a non-ico5 fsaverage5 mesh, or an fsnative series off the subject's mesh. `outputs.diff` is
staged so a failure shows the paths. Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

A **full** run: recon-all, no `--sloppy`, volumetric output spaces (`MNI152NLin2009cAsym`), CIFTI. CI's two
re-runs (single BOLD run with SyN-SDC; no T1w). A cohort. **Graviton**, until an arm64 build exists.

</details>
