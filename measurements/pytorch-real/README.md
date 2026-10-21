# PyTorch (CPU) on a real workload: ResNet-20 on CIFAR-10, Graviton4, Intel and AMD

> **x86 trains 2.1× (Intel) to 3.2× (AMD) faster and Graviton4 infers 1.6–2.2× faster, from the
> same PyTorch build.** The reason is one oneDNN kernel: on aarch64 the weight-gradient convolution
> falls back to oneDNN's reference GEMM (`gemm:ref`), while the forward pass runs a vectorised
> `brgconv:sve_128` that beats x86's `jit:avx512_core`. Pick the chip by what you are doing with the model.

Workload: **ResNet-20** (He et al. 2016, CIFAR variant, 269,722 parameters) trained from scratch on
the **complete CIFAR-10**, 50,000 training and 10,000 test images, for 15 epochs. That is the
standard small-vision training job, the one people actually run on a CPU box. The schedule is
batch 128, Nesterov SGD, a one-cycle LR peaking at 0.2, weight decay 5e-4, and pad-4 crop + flip
augmentation. Each chip trains it **twice**, in fresh processes, with 16 threads, a fixed seed and
`use_deterministic_algorithms(True)`.

## Run it

```sh
measurements/pytorch-real/stage-inputs.sh "$(make -s print-bucket)"     # CIFAR-10 from AWS Open Data, pinned
spec=$(python3 scripts/spec_arch.py pytorch arm64 "$(make -s print-bucket)" r1 measurements/pytorch-real/01-train.task.json)
spawn task run --spec "$spec" --wait          # then x86_64 (Intel), then amd, in place of arm64, serially
```

`00-probe.task.json` is the sizing probe: one epoch on 2,560 images at 8 and 16 threads, then the
oneDNN kernel each convolution pass dispatches to.

## Results

Same image digest (the recipe's arm64 image, and its env's linux-64 twin on both x86 chips), same
input bytes, 16 threads, one `4xlarge` per chip, us-west-2. **A result is one 15-epoch training run.**

| chip | instance | train (15 ep) | samples/s train | samples/s infer | peak RSS | $/hr | compute $/result | $/epoch | billed (task, 2 runs) |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 791 s | 1,004 | **7,071** | 3.0 GiB | 0.6381 | 0.140 | 0.0088 | 1726 s, $0.306 |
| Xeon 6 (Granite Rapids) | `c8i.4xlarge` | 411 s | 2,099 | 3,280 | 3.1 GiB | 0.7497 | 0.086 | 0.0050 | 946 s, $0.197 |
| EPYC 9R45 (Zen 5) | `c8a.4xlarge` | **283 s** | **3,243** | 4,546 | 3.1 GiB | 0.8622 | **0.068** | **0.0037** | 641 s, $0.154 |

The train column is the task's command window halved, so it includes data loading and per-epoch
evaluation. `samples/s` and `$/epoch` come from the training loop alone. `$/hr` is the on-demand
Linux price from the AWS Price List; spawn printed none. The billed window runs from launch to
terminate and covers both trainings plus boot, Docker and the pull (x86 also installs the env twin,
in 10 s).

- **Training: Intel is 2.09× faster than Graviton4 and 1.78× cheaper per epoch**, even after its 17%
  higher hourly price. **AMD is 3.23× faster than Graviton4 and 1.54× faster than Intel**, and 2.4×
  and 1.34× cheaper per epoch, after an hour 35% dearer than Graviton4's.
- **Inference: Graviton4 is 2.16× faster than Intel and 1.56× faster than AMD**, and 2.53× and 2.11×
  cheaper per image (batch 1000, the 10,000 test images). That is 10,000 images, about 1.4 s of
  compute, so read it as a kernel ratio rather than a
  serving benchmark.
- **The probe shows where the time goes.** For one 16→16-channel 3×3 conv layer at batch 128:

  | pass | Graviton4 | x86 |
  |---|---|---|
  | forward | `brgconv:sve_128` 0.78 ms | `jit:avx512_core` 0.96 ms |
  | backward-data | `brg_conv_bwd:sve_128` 0.63 ms | `jit:avx512_core` 0.87 ms |
  | **backward-weights** | **`gemm:ref` 8.65 ms** | `jit:avx512_core` 0.59 ms |

  One pass of three runs about 15× slower on aarch64, and that pass alone accounts for the training gap.
  AMD dispatches the same `jit:avx512_core` for all three passes. Its single-shot probe times (0.85,
  0.55 and 2.55 ms) are one execution each, and the 2.55 ms backward-weights call does not square with
  AMD training 1.54× faster than Intel, so read AMD's probe for the kernel names only.
- **The cause is in oneDNN, not in conda-forge's build.** PyTorch's own `torch-2.14.1+cpu` aarch64
  wheel bundles the same oneDNN v3.12.0, built with the Arm Compute Library (its forward pass runs
  `indirect_gemm:acl`). Its backward-weights pass is still `gemm:ref`, at 8.84 ms against
  conda-forge's 8.67 ms. oneDNN's f32 backward-weights list has aarch64 JIT kernels for SVE-512 and
  SVE-256 only, on `main` as well as v3.12, and no ACL entry. Graviton4's 128-bit SVE therefore falls
  through to im2col plus oneDNN's portable `ref_gemm`. This is open upstream as
  [uxlfoundation/oneDNN#2165](https://github.com/uxlfoundation/oneDNN/issues/2165) (Oct 2024).
  `torch.backends.mkldnn.flags(enabled=False)` routes the pass to PyTorch's im2col + OpenBLAS, which
  is 5.8 ms. That is faster than `gemm:ref` but still 10× x86, and it makes forward and backward-data
  slower. The probes are `02-onednn-bwdw.task.json` and `03-onednn-bwdw-time.task.json`; stage them with
  `onednn-bwdw/stage.sh`. Their logs are in `results/onednn-bwdw/`.
  **Graviton3 (`c7g`, 256-bit SVE) settles it.** The same two builds pick `jit:sve_256` for
  backward-weights there and run it in **0.85 ms**, 10× faster than Graviton4 on this pass. The
  regression is set by vector length, not by any build flag.

  | backward-weights, 16→16 3×3, batch 128 | Graviton4 `c8g` (SVE-128) | Graviton3 `c7g` (SVE-256) |
  |---|---|---|
  | conda-forge `libtorch 2.14.1` | `gemm:ref` 8.67 ms | `jit:sve_256` 0.85 ms |
  | PyTorch `2.14.1+cpu` wheel (ACL) | `gemm:ref` 8.84 ms | `jit:sve_256` 0.85 ms |

  These are medians of five executions, from `ONEDNN_VERBOSE`. At 64→64 channels the gap is
  26.6 ms against 13.6 ms.
- **Threads.** At 8 → 16 threads Graviton4 goes 544 → 1,007 samples/s (1.85×), and Intel goes
  2,162 → 2,192, which is flat: 16 vCPU on `c8i` is 8 cores with SMT, and on `c8g` it is 16 cores.
  An 8-thread Intel job would cost the same on a box half the size. AMD goes 2,572 → 3,635 (1.41×):
  16 vCPU on `c8a` are 16 cores (`lscpu`: 1 thread per core, `results/amd-c8a.4xlarge/host.txt`).
- **Peak RSS is ~3 GiB on every chip.** The dataset fits as uint8 tensors (180 MB), so the 32 GiB
  of a `c8g`/`c8i` 4xlarge is far more than this needs. `m`/`r` would only add idle RAM; the knee
  is cores.
- n = 2 trainings per chip, and the table quotes the first. The repeat gave the same numbers bit
  for bit (below). Its throughput came within 0.01% (arm64), 3% (Intel) and 3% (AMD) for training,
  and within 2%, 9% and 7% for inference. So Graviton4's inference lead is **1.95–2.16×** over Intel
  and 1.43–1.56× over AMD across the runs, and Intel's training lead 2.09–2.15×.

## What was asserted at scale, on all three chips

| identity | Graviton4 | Intel | AMD |
|---|---|---|---|
| archive sha256 pinned; decoded pixels+labels sha256 | `12412dd2…` | **same** | **same** |
| class counts: 5,000 train / 1,000 test per class, all RGB 32×32 | exact | exact | exact |
| ResNet-20 parameter count = 269,722 (counted by hand, He et al.'s "0.27M") | exact | exact | exact |
| **repeat in a fresh process: every parameter, all 5,865 per-step losses, final accuracy** | **bit-identical** | **bit-identical** | **bit-identical** |
| confusion rows each sum to 1,000, total 10,000, trace = correct count | exact | exact | exact |
| softmax rows sum to 1 (float64) | 5.6e-16 | 6.7e-16 | 6.7e-16 |
| **test accuracy > 0.80** (floor) and **< 0.9225** (ceiling) | **0.8871** | **0.8880** | **0.8880** |
| eval batch 100 vs 1000: max \|Δlogit\|, prediction flips (reported) | **0**, 0 | 7.6e-06, 0 | 7.6e-06, 0 |

**The accuracy bounds come from the model class, not from this run.** A linear classifier on
CIFAR-10 pixels reaches about 40%, and a shallow conv net without residuals reaches 65–75%. 0.80
needs a deep conv net that actually trained. It also sits 8 points below what both chips reached,
against a cross-chip spread of 0.09 points. The ceiling is ResNet-20's published 91.25% after
~164 epochs, plus one point. A 15-epoch run that beats it is test-set leakage, not a good run.

## Fixed point or path: this is a path

The trained network is a **path**, so the cross-chip comparison is reported, not asserted:

- **The step-0 loss is bit-identical across chips** (2.4814796…). The seeded initialisation, the
  augmentation draws and the first forward pass are all the same bits.
- The two chips first differ at **step 1**, by 2.4e-7 (one float32 ulp), once the first SGD update
  has gone through each chip's backward kernels. The gap reaches 1e-2 by step 10 and then wanders
  between 2e-3 and 4e-2 for the rest of training.
- Mid-training test accuracy differs by up to **10 points** (epoch 1: 45.9% vs 56.4%; epoch 6:
  65.7% vs 72.7%), at the high-LR peak of the one-cycle schedule. Do not compare checkpoints across
  chips.
- **The end of the schedule comes back together**: the final train loss is 0.2906 vs 0.2892, test
  accuracy 88.71% vs 88.80%, and per-class accuracy agrees within 2 points on every class.
- Within each chip it is exact. Determinism is therefore a per-chip (per-kernel) property: a
  checkpoint hash reproduces on the same instance family, not across architectures.
- The batch-independence row shows the same thing at inference. On x86 a different batch size takes
  a different kernel and moves logits by 7.6e-6; on Graviton4 it moves them by exactly 0.
- **AMD against Intel: the same training, bit for bit.** The parameter hash (`d8e6af29…`), the
  per-step loss trace (`A.trace`, byte-identical file), the final loss and every accuracy match the
  Intel run exactly, though AMD runs it 1.54× faster. Both run oneDNN's `jit:avx512_core` and
  OpenBLAS's `Cooperlake` kernel, so the two x86 vendors take one floating-point path here. AMD
  against Graviton4 is therefore exactly the Intel-against-Graviton4 path above. This is a claim
  about this build on these two chips, not about x86 in general: a different ISA dispatch on another
  x86 core would be a different path.

Per-step loss traces (float hex), the per-epoch JSON, both training logs, the repeat comparison and
the probe logs are in [`results/`](results/).

**Spend, AMD leg:** $0.154 for the two trainings and $0.039 for the probe, both first-attempt green.
They ran the committed specs with spawn 0.131.1 built from its release tag, because the installed
0.139.1's own wrapper leaves too little of EC2's 16 KiB user data for them (spawn#1246), plus one
non-fatal line at the top of the training task writing `host.txt` (`lscpu`, `OPENBLAS_VERBOSE=2`).
