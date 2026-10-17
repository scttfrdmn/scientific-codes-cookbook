# PyTorch (CPU) on a real workload: ResNet-20 on CIFAR-10, Graviton4 vs x86

> **x86 trains 2.1× faster and Graviton4 infers 2.2× faster, from the same PyTorch build.** The
> reason is one oneDNN kernel: on aarch64 the weight-gradient convolution falls back to oneDNN's
> reference GEMM (`gemm:ref`), while the forward pass runs a vectorised `brgconv:sve_128` that
> beats x86's `jit:avx512_core`. Pick the chip by what you are doing with the model.

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
spawn task run --spec "$spec" --wait          # then x86_64 in place of arm64, serially
```

`00-probe.task.json` is the sizing probe: one epoch on 2,560 images at 8 and 16 threads, then the
oneDNN kernel each convolution pass dispatches to.

## Results

Same image digest (the recipe's arm64 image, and its env's linux-64 twin on x86), same input bytes,
16 threads, one `4xlarge` per chip, us-west-2. **A result is one 15-epoch training run.**

| chip | instance | train (15 ep) | samples/s train | samples/s infer | peak RSS | $/hr | compute $/result | $/epoch | billed (task, 2 runs) |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 791 s | 1,004 | **7,071** | 3.0 GiB | 0.6381 | 0.140 | 0.0088 | 1726 s, $0.306 |
| Xeon 6 (Granite Rapids) | `c8i.4xlarge` | **411 s** | **2,099** | 3,280 | 3.1 GiB | 0.7497 | **0.086** | **0.0050** | 946 s, $0.197 |

The train column is the task's command window halved, so it includes data loading and per-epoch
evaluation. `samples/s` and `$/epoch` come from the training loop alone. `$/hr` is the on-demand
Linux price from the AWS Price List; spawn printed none. The billed window runs from launch to
terminate and covers both trainings plus boot, Docker and the pull (x86 also installs the env twin,
in 10 s).

- **Training: x86 is 2.09× faster and 1.78× cheaper per epoch**, even after its 17% higher hourly price.
- **Inference: Graviton4 is 2.16× faster and 2.53× cheaper per image** (batch 1000, the 10,000 test
  images). That is 10,000 images, about 1.4 s of compute, so read it as a kernel ratio rather than a
  serving benchmark.
- **The probe shows where the time goes.** For one 16→16-channel 3×3 conv layer at batch 128:

  | pass | Graviton4 | x86 |
  |---|---|---|
  | forward | `brgconv:sve_128` 0.78 ms | `jit:avx512_core` 0.96 ms |
  | backward-data | `brg_conv_bwd:sve_128` 0.63 ms | `jit:avx512_core` 0.87 ms |
  | **backward-weights** | **`gemm:ref` 8.65 ms** | `jit:avx512_core` 0.59 ms |

  One pass of three runs about 15× slower on aarch64, and that pass alone accounts for the training gap.
- **Threads.** At 8 → 16 threads Graviton4 goes 544 → 1,007 samples/s (1.85×), and x86 goes
  2,162 → 2,192, which is flat: 16 vCPU on `c8i` is 8 cores with SMT, and on `c8g` it is 16 cores.
  An 8-thread x86 job would cost the same on a box half the size.
- **Peak RSS is ~3 GiB on both chips.** The dataset fits as uint8 tensors (180 MB), so the 32 GiB
  of a `c8g`/`c8i` 4xlarge is far more than this needs. `m`/`r` would only add idle RAM; the knee
  is cores.
- n = 2 trainings per chip, and the table quotes the first. The repeat gave the same numbers bit
  for bit (below). Its throughput came within 0.01% (arm64) and 3% (x86) for training, and within
  2% and 9% for inference. So the inference ratio is **1.95–2.16×** across the four runs, and the
  training ratio 2.09–2.15×.

## What was asserted at scale, on both chips

| identity | Graviton4 | x86 |
|---|---|---|
| archive sha256 pinned; decoded pixels+labels sha256 | `12412dd2…` | **same** |
| class counts: 5,000 train / 1,000 test per class, all RGB 32×32 | exact | exact |
| ResNet-20 parameter count = 269,722 (counted by hand, He et al.'s "0.27M") | exact | exact |
| **repeat in a fresh process: every parameter, all 5,865 per-step losses, final accuracy** | **bit-identical** | **bit-identical** |
| confusion rows each sum to 1,000, total 10,000, trace = correct count | exact | exact |
| softmax rows sum to 1 (float64) | 5.6e-16 | 6.7e-16 |
| **test accuracy > 0.80** (floor) and **< 0.9225** (ceiling) | **0.8871** | **0.8880** |
| eval batch 100 vs 1000: max \|Δlogit\|, prediction flips (reported) | **0**, 0 | 7.6e-06, 0 |

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

Per-step loss traces (float hex), the per-epoch JSON, both training logs, the repeat comparison and
the probe logs are in [`results/`](results/).
