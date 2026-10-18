---
tool: ants
tool_version: "2.6.5"
gap_arm64: no arm64 build exists — conda-forge ants 2.6.5 is linux-64 only and the project's antsx/ants image is amd64-only (checked 2026-10-16)
images_x86_64:
  - docker.io/antsx/ants@sha256:59c45f54a1f1dc69134f63bec91a726e41c71c64a16cc21cda0b54526910a3c3
last_verified_x86_64: 2026-10-16
spawn_version: 0.127.0
---
# ANTs — registration that undoes a planted motion, on x86

Moves a synthetic volume by a known translation and a known rotation, then has `antsRegistration` undo them on same-generation Intel — plus two exact interpolation identities. The catalog's first x86-only recipe. For anyone registering brain images on AWS.

## Run it

```bash
make stage RECIPE=ants            # once: a synthetic volume and its exact 2-voxel shift
spawn task run --spec "$(make -s spec RECIPE=ants)" --wait      # runs on c8i (x86_64)
make ls RECIPE=ants ARCH=x86_64

antsRegistration -d 3 -m MeanSquares[fixed.nii,moving.nii.gz,1] -t Rigid[0.1] \
  -c [300x200x100,1e-10,10] -s 2x1x0vox -f 2x1x1 -o [rr_,warped.nii.gz]
ConvertTransformFile 3 rr_0GenericAffine.mat rr.txt     # matrix + offset as text
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| **x86 only** | Graviton | **there is no arm64 build of ANTs**: conda-forge ships `ants` 2.6.5 for linux-64 only, and the project's `antsx/ants` image is amd64-only. This recipe is the cookbook's first x86-only one. |
| `T` applied to make `moving` | your own pair | registration recovers **`T⁻¹`**, not `T`: `antsApplyTransforms` resamples `moving(x) = fixed(T(x))`. A sign that looks wrong is usually this. |
| transforms in ITK world space | NIfTI voxel thinking | ANTs works in **LPS**, so `x = −(i − c)`: a +2 mm ITK x-translation moves array index *i* by **−2**. The exact shift check is what confirms it. |
| `MeanSquares` | `MI` / `CC` | right for same-modality images like these; use Mattes MI across modalities and `CC` for deformable (SyN) work. |
| synthetic volume | your scans | nothing is fetched. The ANTs image ships no templates or python, so the fixture is built in staging. |

**Leave the fixture.** A planted motion is what turns "registration converged" into "registration found the answer": the transform is known before ANTs runs. **Scale it** to real brains; the identities hold, and recovered-vs-planted is how to test any pipeline change.

## Which box — [measured](../../measurements/ants-real/README.md) (125 real T1s, `antsRegistrationSyNQuick.sh` to MNI152)

| chip | instance | cohort wall | $/hr | **billed $/subject** |
|---|---|---|---|---|
| Graviton4 | — | gap: no arm64 build | | |
| Intel | `c8i.4xlarge` | 35 min | 0.7497 | 0.0036 |
| **AMD** | `c8a.4xlarge` | **22 min** | 0.8622 | **0.0026** |

**Take AMD: 1.58× faster, 26% cheaper per subject.** Both chips give byte-identical warps for all 125 subjects, and every identity above holds on the real scans.

## Shape, size, cost

One task on `c8i.large` (2 vCPU / 4 GiB, x86_64), TTL 40m as a **backstop** with `cost_limit` $0.12 as the real guard. Three registrations on a 64³ volume take seconds of a ~1-minute window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: two exact interpolation identities, a planted translation recovered to 0.001 mm and a rotation to 9e-5 rad</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged fixture | match pins | 2 of 2 |
| ANTs | read from the binary | **`2.6.5.dev1-gfdce4d2`** (the image tag says `v2.6.5`) |
| **identity transform, linear interpolation** | **`sum|out − in| == 0`** | **0** |
| **2 mm ITK-x shift, nearest-neighbour, vs numpy's shift** | **`== 0`** | **0** |
| **planted translation (3.3, −2.1, 1.7) mm** | **recovered as its negative, max err `< 0.1` mm** | **(−3.29895, 2.09878, −1.70123)**, err **0.00123** mm |
| **planted rotation +0.09 rad about z** | **recovered as −0.09, `< 0.002` rad** | **−0.089912**, err 8.8e-5 |
| recovered rotation axis | off-axis terms `< 0.002` | 1.88e-4 |
| **image registered to itself** | **`max|T − I| < 1e-6`** | **0** |
| Pearson to the fixed image | must rise | 0.8916 → 0.99997 (translation), 0.9957 → 0.99998 (rotation) |

### A planted answer, so convergence is not the claim

Registration that "converged" can converge to the wrong place; registration that returns a
transform known before it ran has found the answer. So the task builds `moving` from `fixed`
with a transform it writes itself, and asks ANTs to undo it:

```text
planted   translation (3.3, -2.1, 1.7) mm       ->  recovered (-3.29895, 2.09878, -1.70123)
planted   rotation +0.09 rad about z            ->  recovered -0.089912 rad, off-axis 1.9e-4
```

The bounds are set between two regimes, not near the observed values: a failed or sign-flipped
registration misses by the planted motion itself — at least 1.7 mm, or 0.18 rad for a flipped
rotation, 0.09 for a wrong axis — while a converged one on a smooth volume is sub-voxel. `0.1` mm
is a tenth of a voxel, and `0.002` rad is about 2% of the planted angle. The rotation is compared by
angle and axis, which do not depend on the centre ANTs chooses; the translation is checked on a
pure translation, where there is no centre to choose.

The fixture is deliberately asymmetric — four Gaussian blobs of different size, weight and
elongation, off-centre — so no symmetry of the volume can satisfy a wrong answer. Staging measures
it: its correlation with its own 90° rotation is 0.727.

### Two exact identities, and the coordinate convention they pin down

An identity transform with linear interpolation samples every voxel at its own grid point, so the
output must equal the input exactly — and does, to **0**. A 2 mm translation with nearest-neighbour
interpolation must equal an exact 2-voxel shift computed independently in numpy by array indexing —
and does, to **0**. That second one is also the check on the convention: ITK's world frame is LPS,
so with this volume's voxel-to-world mapping `x = −(i − 31.5)`, and a +2 mm x-translation samples
`i − 2`. Get the frame wrong and the shift goes the other way and the sum is far from zero.

### Pins

| | |
|---|---|
| fixture | a 64³ float32 volume at 1 mm and its exact 2-voxel shift, built in staging with numpy and written as NIfTI-1 by hand (sha256-pinned) |
| image | `docker.io/antsx/ants@sha256:59c45f54…` — **the ANTs project's own image**, tag `v2.6.5`, `linux/amd64`. Not cosign-signed, so the digest is the pin (the same provenance tier as [openfoam](../openfoam/README.md)'s `opencfd` image). The binary reports `2.6.5.dev1-gfdce4d2`, built 2026-01-14 |
| host | `c8i` (Intel, AVX-512) |

**Un-defers for arm64 when** an aarch64 build exists: conda-forge's `ants-feedstock` adding
`linux_aarch64` — requested as [ants-feedstock#35](https://github.com/conda-forge/ants-feedstock/issues/35);
the feedstock already cross-compiles `osx-arm64`, so ANTs builds for ARM — or the project
publishing an arm64 image.

### Run + verify

```sh
make stage RECIPE=ants
spawn task run --spec "$(make -s spec RECIPE=ants)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs-x86_64/ants/r1/smoke-check.txt" -
```

Fails on a fixture pin mismatch, an identity transform that changes the image, a nearest-neighbour
shift that differs from numpy's, a translation recovered worse than 0.1 mm, a rotation worse than
0.002 rad or off the z axis, a self-registration that is not the identity, or a registration that
does not raise similarity. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

A planted *warp*, as opposed to a rigid motion, as the check on deformable registration. SyN on real
brains is in the [measurement](../../measurements/ants-real/README.md) (Jacobian > 0, inverse
consistency, Dice gain over affine), but no MNI/OASIS template ships in the image, so the recipe
itself stays synthetic. **N4 bias correction**, **atlas-based
segmentation** (`antsAtroposN4.sh`) and **cortical thickness**. **Multi-modal metrics** (Mattes MI)
and **`CC`**. **Graviton**, until an aarch64 build exists — and with it the cross-architecture
identity every bioconda recipe now carries.

</details>
