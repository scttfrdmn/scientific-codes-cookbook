---
tool: pytorch
tool_version: "2.14.1"
env: single-cell
image: quay.io/aarchsci/single-cell@sha256:14d4af5bbd86625e3c98eb7b2078e3da6206aa9cb1ea3a3d631e96c7bebb901a
env_x86_64: single-cell-957f8c9c38e0
spawn_version: 0.130.1
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# PyTorch (CPU) — autograd against finite differences, linear algebra against its inputs, LBFGS against two other codes

Checks PyTorch's CPU build on Graviton4: gradients, `torch.linalg`, `torch.optim`, and bit-identical seeded training. For anyone training or fitting models on ARM without a GPU.

## Run it

```bash
make stage RECIPE=pytorch     # once: the checks only; every tensor and dataset is synthesised
spawn task run --spec "$(make -s spec RECIPE=pytorch)" --wait
make ls RECIPE=pytorch

torch.set_num_threads(n); torch.manual_seed(s); torch.use_deterministic_algorithms(True)   # then runs repeat bit for bit
gradcheck(fn, inputs_float64) and gradgradcheck(fn, inputs_float64)                        # analytic == finite-difference
torch.optim.LBFGS([w], tolerance_grad=1e-12, line_search_fn="strong_wolfe")                 # == statsmodels/sklearn MLE to 4e-9
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the `single-cell` env | a dedicated PyTorch env | **PyTorch comes in as a dependency of `spatialdata`** (via squidpy). It is a full CPU build: `cpu_generic`, OpenBLAS (`BLAS_INFO=open`), oneDNN on, no MKL, no ACL. The image is large because it also carries Scanpy and R/Seurat. |
| `gradcheck` in float64 | float32 | finite differences at `eps=1e-6` need float64; in float32 gradcheck fails correct code. |
| LBFGS with `tolerance_grad=1e-12` | the defaults (`1e-7`) | LBFGS stops when the **loss** stops changing, so it can certify a gradient only to about √ε (1.5e-8), whatever `tolerance_grad` says. The defaults stop earlier still. |
| a 3→64→64→1 MLP on a planted function | your model and data | the held-out bound comes from the noise the design plants (σ² = 0.01), so a real dataset needs its own baseline. |

**Leave the fixtures.** Every check is an identity, a planted value, or a second code solving the same problem, so the data only has to be well-posed. Synthesising it keeps the run offline. **Scale it** to your model: the determinism recipe (threads + seed + deterministic algorithms) and the LBFGS cross-check carry over unchanged.

## Which box — [measured](../../measurements/pytorch-real/README.md) (ResNet-20, full CIFAR-10, 15 epochs, 16 threads)

| chip | instance | samples/s train | samples/s infer | $/epoch | test acc |
|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 1,004 | **7,071** | 0.0088 | 0.8871 |
| x86 | `c8i.4xlarge` | **2,099** | 3,280 | **0.0050** | 0.8880 |

**Train on x86, infer on Graviton4.** The cause is one kernel in this build: on aarch64 oneDNN computes the conv weight gradient with its reference GEMM (`gemm:ref`, about 15× slower than x86's), while its SVE forward pass beats AVX-512. The run is bit-identical on a repeat within each chip, and a path across them: the two chips diverge at step 1 and finish 0.09 points apart.

## Shape, size, cost

One task on `c8g.2xlarge` (8 vCPU / 16 GiB), TTL 40m as a **backstop** with `cost_limit` $0.30 as the real guard. The checks take about 25 s of a 2-minute task window, and most of that window is the image pull ([layout](../../patterns/layout-and-effective-cost.md)), so **these timings are not compute cost**.

<details>
<summary>As shipped: 7 of 7 autograd cases, exact closed-form gradients, reconstructions to 3e-15, LBFGS equal to statsmodels and sklearn to 4e-9, bit-identical seeded training</summary>

### The checks

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | 1 of 1 |
| torch / numpy / sklearn / statsmodels | read from the running install | **2.14.1** / 2.5.4 / 1.9.1 / 0.15.0 | identical |
| CPU build | `torch.version.cuda is None` | None | None |
| backend | recorded | ATen `SVE128`, OpenMP, oneDNN available, no MKL, OpenBLAS 0.3.34 `neoversev2` | ATen `AVX512`, OpenMP, oneDNN available, no MKL, OpenBLAS 0.3.34 `Cooperlake` |
| **gradcheck + gradgradcheck**: matmul, conv2d, NLL∘log_softmax, softmax, layer_norm, `nn.LSTMCell` (weights as inputs), a custom composition | **all `True`** | **7 of 7** | **7 of 7** |
| control: a backward that is 0.1% wrong | gradcheck must reject it | rejected | rejected |
| **∂‖XW−Y‖²/∂W vs 2Xᵀ(XW−Y)** | **rel `< 1e-13`** | **0** | **0** |
| **Hessian of xᵀAx vs A+Aᵀ** | **rel `< 1e-14`** | **0** | **0** |
| `nn.LSTMCell` vs its gate equations | `< 1e-14` | 2.2e-16 | 2.2e-16 |
| **solve: backward error** ‖Ax−b‖/(‖A‖‖x‖), cond 1e3 | **`< 1e-14`** | **3.2e-16** | **2.3e-16** |
| **svd: ‖USVᵀ−A‖/‖A‖, ‖UᵀU−I‖, σ vs planted** | **`< 1e-13`** | **2.8e-15, 3.3e-15, 1.1e-15** | **2.8e-15, 2.9e-15, 1.0e-15** |
| **eigh: ‖VΛVᵀ−S‖/‖S‖, λ vs planted** | **`< 1e-13`** | **2.6e-15, 8.9e-16** | **2.6e-15, 1.0e-15** |
| **cholesky: ‖LLᵀ−A‖/‖A‖**, strictly lower, positive diagonal | **`< 1e-14`** | **8.2e-17** | **1.8e-16** |
| **lstsq: residual ⟂ columns** | **`< 1e-13`** | **6.2e-16** | **4.6e-16** |
| solve / svd / eigh / cholesky / lstsq vs numpy | `< 1e-10` … `1e-13` | 0, 1.5e-15, 8.9e-16, 0, 0 | 1.8e-14, 2.5e-15, 9.5e-16, 0, 0 |
| control: `solve(Aᵀ)` vs numpy `solve(A)` | must differ by > 1e-3 | 1.47 | 1.47 |
| **logistic MLE: torch LBFGS vs statsmodels and vs sklearn** | **max \|diff\| `< 1e-6`** | **3.5e-9, 3.5e-9** | identical, β to 10 digits |
| **score equation max\|Xᵀ(y−p)\|/N at torch's optimum** | **`< 1e-7`** | **5.1e-10** | identical |
| planted β recovered | max \|z\| `< 5` | 1.60 | identical |
| **OLS by LBFGS vs `numpy.linalg.lstsq`** | **`< 1e-8`** | **3.8e-10** | identical |
| **MLP held-out MSE / σ²** | **`> 0.8` and `< 1.5`** | **1.183** | **1.183** |
| control: linear model on the same data | held-out MSE / σ² `> 3` | 17.43 | 17.43 |
| **same seed, 8 threads, deterministic algorithms: params and loss trajectory** | **bit-identical** | **identical** | **identical** |
| control: different seed | params must differ by > 1e-3 | 1.40 | 1.40 |
| 1 thread vs 8 threads, same seed | reported | **bit-identical** (max \|diff\| 0) | **bit-identical** (max \|diff\| 0) |
| fp32 2048³ matmul | reported, not a benchmark | 73.8 GFLOP/s (1 thread), 341.6 (8) | 186.6 (1 thread), 700.0 (8) |

**x86_64** runs the same spec on the env's linux-64 twin (`envs-x86_64/single-cell-957f8c9c38e0`,
535 packages installed offline in 31 s on `c8i.2xlarge`; the nine version differences, listed on
[scvelo](../scvelo/README.md), include no torch, BLAS or LAPACK package). The linux-64 PyTorch is
the same `cpu_generic` 2.14.1 build, **not** MKL-backed: `torch.__config__.show()` reports
`BLAS_INFO=open`, `MKL not found`, the same MKL-DNN v3.12.0 (oneDNN), `CPU capability usage:
AVX512` against `SVE128`, and `-march=nocona -mtune=haswell` against no `-march` on arm64. Every
assertion holds on both chips. The gradients, the LBFGS fixed points and the MLP's held-out score
agree to every printed digit; the LAPACK reconstructions move by a few ulps, because OpenBLAS picks
a different kernel per host. Seeded training is bit-identical **within** each chip, at 1 and 8
threads alike. Across chips the trained parameters are not compared: that is a path, and only its
held-out score (1.183 on both) is reported. Two runs per chip gave the same numbers apart from the
throughput line.

### Why these bounds

The autograd identity is gradcheck's own: analytic Jacobian versus central differences, in float64
with `atol=1e-8, rtol=1e-6`. The control shows it can see a 0.1% error. The closed-form gradients
come out **exactly** equal, because autograd evaluates the same matrix products the formula does.

Reconstruction bounds are backward-stability statements: a stable LAPACK routine reconstructs its
input to a small multiple of machine epsilon (2.2e-16) times n, here n = 200. The planted spectra
are known exactly, so eigenvalues and singular values are checked against the truth, not against
another code. numpy links **the same OpenBLAS and LAPACK** in this image, so torch-vs-numpy
agreement checks torch's wrapping and not LAPACK itself. Torch tensors are row-major and LAPACK is
column-major, and the transposed-solve control shows a layout error would be caught.

The logistic fit is a fixed point that three codes must share: torch's LBFGS, statsmodels' Newton
and sklearn's `newton-cholesky`. The bound is set by the LBFGS termination argument in the table
above (√ε in the gradient, divided by the Hessian's smallest eigenvalue, with margin), not by how
closely they happened to agree. Because this is a converged fixed point and not a trajectory, the
OpenBLAS kernel the host selects cannot move it past the bound. The MLP bounds come from the
design: the planted noise σ² is an MSE floor no model can beat. With 2,000 test points that MSE
estimate has a relative SD of 3.2%, so the floor is asserted at 0.8σ². The ceiling of 1.5σ² means
the net captures the planted function to within half the noise, which the linear control (17σ²)
cannot.

### Determinism

`set_num_threads` + `manual_seed` + `use_deterministic_algorithms(True)` gives two in-process
trainings that are bit-identical in every parameter and every per-step loss. The different-seed
control shows the comparison is not vacuous. **1 vs 8 threads was also bit-identical** for this
model. Reductions this small do not split across threads, so do not count on that for large
layers. Pin the thread count.

### Pins

| | |
|---|---|
| data | **none staged**. Random tensors, planted spectra (cond 1e3), a logistic design with planted β, and a planted 3-D function with σ = 0.1 are all generated from seed `20261016` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/single-cell@sha256:14d4af5bbd86…`: `pytorch 2.14.1 cpu_generic_py314_h08a2e38_0`, `libtorch 2.14.1 cpu_generic_hb401ce7_0`, `onednn 3.13.4`, `libopenblas 0.3.34 openmp`, `sleef 3.9.0`, `llvm-openmp 23.1.3`, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`. The signature covers the **manifest-list**
digest (`c422f5f8c974…`), so verify that digest and pin the arm64 entry, the same image
[squidpy](../squidpy/README.md) and [scvelo](../scvelo/README.md) pin. `torch.__config__.show()`
reports `MKL-DNN v3.12.0` (the version libtorch was compiled against) while the env ships oneDNN
3.13.4. The full config is staged out as `torch-config.txt`, and the installed builds as `packages.txt`
(read from `$CONDA_PREFIX`, so it lists the linux-64 builds on x86).

### Run + verify

```sh
make stage RECIPE=pytorch
spawn task run --spec "$(make -s spec RECIPE=pytorch)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/pytorch/r1/score.tsv" -
```

The run fails on any of these:

- a pin mismatch, or a CUDA build
- a gradcheck or gradgradcheck failure, or a wrong backward that gradcheck accepts
- a closed-form gradient or Hessian off, or a decomposition that does not reconstruct its input or recover its planted spectrum
- a solve that cannot tell A from Aᵀ
- an LBFGS optimum off statsmodels, sklearn or least squares
- an MLP that fails to beat its bound, or beats the noise floor
- a linear model that fits the planted function
- two seeded trainings that differ, or two seeds that agree
- a NaN anywhere a bound is applied

Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**PyTorch's own test suite**: the conda-forge package ships none. There is no top-level `test/`
directory, only seven helper modules named `test_*.py`, so the
[reference-from-tests](../../practices/reference-from-tests.md) shape is not available from this
image. **A dedicated PyTorch env**: PyTorch is here only because `spatialdata` depends on it. A
`pytorch` env on aarch.science, without Scanpy and R, would be the natural ask (not filed).
**`torch.compile`/Inductor**, quantisation and bfloat16. A real model and dataset (ResNet-20 on
CIFAR-10) is [measured separately](../../measurements/pytorch-real/README.md). It is bit-identical
on a repeat at 16 threads on both chips. **GPU** is Round Two.

</details>
