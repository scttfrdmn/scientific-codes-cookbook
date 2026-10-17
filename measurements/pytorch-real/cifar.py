#!/usr/bin/env python3
"""ResNet-20 (He et al. 2016, CIFAR variant) trained on the full CIFAR-10, CPU PyTorch.

    python3 -u cifar.py train <tag>      # one seeded training run -> <tag>.json, <tag>.trace
    python3 -u cifar.py compare A B      # assert runs A and B are bit-identical

Data: the fast.ai CIFAR-10 archive on AWS Open Data (s3://fast-ai-imageclas/cifar10.tgz), read
straight from the tar -- 50,000 train / 10,000 test PNGs, folder = class. Nothing is extracted.

Determinism: fixed threads + manual_seed + use_deterministic_algorithms, a single-process data
pipeline, and augmentation (pad-4 random crop + horizontal flip) drawn from a seeded
torch.Generator. So a repeat on one chip must be bit-identical in every parameter and every
per-step loss. Across chips the run is a PATH (SVE128 vs AVX512, a different OpenBLAS corename),
so only bounds are asserted on it and the curves are reported.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import ctypes
import glob
import hashlib
import io
import json
import os
import platform
import resource
import sys
import tarfile
import time

import numpy as np

EPOCHS = int(os.environ.get("EPOCHS", "15"))
THREADS = int(os.environ.get("THREADS", "16"))
LIMIT = int(os.environ.get("LIMIT", "50000"))   # sizing probes only; the measured run uses all
SEED = 20261017
BATCH = 128
LR_MAX, MOMENTUM, WD = 0.2, 0.9, 5e-4
ARCHIVE = os.environ.get("ARCHIVE", "cifar10.tgz")
CLASSES = ["airplane", "automobile", "bird", "cat", "deer",
           "dog", "frog", "horse", "ship", "truck"]
# ResNet-20 with option-A (zero-padding) shortcuts and a 10-way head, counted by hand: stem conv
# 432 + BN 32; stage 16: 3 x (2 x 2304 + 64) = 14016; stage 32: 13952 + 2 x 18560 = 51072;
# stage 64: 55552 + 2 x 73984 = 203520; fc 650. Total 269722 -- He et al.'s "0.27M".
N_PARAMS = 269722
# He et al. 2016 (Table 6) report 8.75% test error for ResNet-20 after ~164 epochs. A 15-epoch
# schedule cannot legitimately beat that by a point, so accuracy above it signals leakage.
PUBLISHED_ACC = 0.9125
# A linear classifier on raw CIFAR-10 pixels tops out near 40%; a small conv net without residuals
# reaches ~65-75%. 0.80 is what only a working deep conv net reaches -- see README.
FLOOR = 0.80
OUT = {}


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    OUT[k] = v


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def corename():
    lib = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))[0]
    f = ctypes.CDLL(lib).openblas_get_corename
    f.restype = ctypes.c_char_p
    return f().decode()


def load(torch):
    from PIL import Image
    t0 = time.time()
    data = {"train": [[] for _ in CLASSES], "test": [[] for _ in CLASSES]}
    with tarfile.open(ARCHIVE, "r:gz") as tf:
        for m in tf:
            if not m.isfile() or not m.name.endswith(".png"):
                continue
            parts = m.name.split("/")      # cifar10/<split>/<class>/<n>.png
            split, cls, fn = parts[-3], parts[-2], parts[-1]
            img = Image.open(io.BytesIO(tf.extractfile(m).read()))
            img.load()
            if img.mode != "RGB" or img.size != (32, 32):
                die("%s is %s %s, not RGB 32x32" % (m.name, img.mode, img.size))
            data[split][CLASSES.index(cls)].append((int(fn[:-4]), np.asarray(img)))
    out = {}
    for split, want in (("train", 5000), ("test", 1000)):
        xs, ys = [], []
        for c, items in enumerate(data[split]):
            if len(items) != want:
                die("%s/%s has %d images, want %d" % (split, CLASSES[c], len(items), want))
            items.sort(key=lambda t: t[0])   # tar order is arbitrary; sorting makes it canonical
            xs += [a for _, a in items]
            ys += [c] * want
        x = torch.from_numpy(np.stack(xs)).permute(0, 3, 1, 2).contiguous()   # uint8 N,3,32,32
        out[split] = (x, torch.tensor(ys, dtype=torch.long))
    rec("load_s", "%.1f" % (time.time() - t0))
    rec("n_train", out["train"][0].shape[0])
    rec("n_test", out["test"][0].shape[0])
    # Content pin of the decoded pixels and labels, independent of PNG encoding and tar order.
    h = hashlib.sha256()
    for split in ("train", "test"):
        h.update(out[split][0].numpy().tobytes())
        h.update(out[split][1].numpy().tobytes())
    rec("decoded_sha256", h.hexdigest())
    return out


def build(torch):
    nn, F = torch.nn, torch.nn.functional

    class Block(nn.Module):
        def __init__(self, cin, cout, stride):
            super().__init__()
            self.c1 = nn.Conv2d(cin, cout, 3, stride, 1, bias=False)
            self.b1 = nn.BatchNorm2d(cout)
            self.c2 = nn.Conv2d(cout, cout, 3, 1, 1, bias=False)
            self.b2 = nn.BatchNorm2d(cout)
            self.pad = cout - cin
            self.stride = stride

        def forward(self, x):
            o = F.relu(self.b1(self.c1(x)))
            o = self.b2(self.c2(o))
            s = x
            if self.stride != 1 or self.pad:   # option A: subsample, zero-pad the new channels
                s = F.pad(x[:, :, ::2, ::2], (0, 0, 0, 0, self.pad // 2, self.pad // 2))
            return F.relu(o + s)

    class ResNet20(nn.Module):
        def __init__(self):
            super().__init__()
            self.c = nn.Conv2d(3, 16, 3, 1, 1, bias=False)
            self.b = nn.BatchNorm2d(16)
            layers, cin = [], 16
            for cout, stride in ((16, 1), (32, 2), (64, 2)):
                for i in range(3):
                    layers.append(Block(cin, cout, stride if i == 0 else 1))
                    cin = cout
            self.layers = nn.Sequential(*layers)
            self.fc = nn.Linear(64, 10)

        def forward(self, x):
            x = F.relu(self.b(self.c(x)))
            x = self.layers(x)
            return self.fc(F.adaptive_avg_pool2d(x, 1).flatten(1))

    return ResNet20()


def state_sha(model):
    h = hashlib.sha256()
    for k, v in model.state_dict().items():
        h.update(k.encode())
        h.update(v.detach().contiguous().numpy().tobytes())
    return h.hexdigest()


def train(tag):
    import torch
    import torch.nn.functional as F
    torch.set_num_threads(THREADS)
    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)
    rec("tag", tag)
    rec("arch", platform.machine())
    if platform.machine() != os.uname().machine:
        die("interpreter arch %s != host %s" % (platform.machine(), os.uname().machine))
    ver("torch", lambda: torch.__version__)
    ver("cpu_capability", lambda: torch.backends.cpu.get_cpu_capability())
    ver("openblas_corename", corename)
    ver("mkldnn", lambda: torch.backends.mkldnn.is_available())
    ver("nproc", lambda: os.cpu_count())
    rec("threads", torch.get_num_threads())
    rec("epochs", EPOCHS)
    if torch.get_num_threads() != THREADS:
        die("asked for %d threads, got %d" % (THREADS, torch.get_num_threads()))

    d = load(torch)
    xtr, ytr = d["train"]
    xte, yte = d["test"]
    # Every class is exactly 5000 train / 1000 test: a label-mapping error would move these.
    if torch.bincount(ytr).tolist() != [5000] * 10 or torch.bincount(yte).tolist() != [1000] * 10:
        die("class counts are not 5000/1000 per class")
    if LIMIT < 50000:
        keep = torch.randperm(50000, generator=torch.Generator().manual_seed(1))[:LIMIT]
        xtr, ytr = xtr[keep], ytr[keep]
        rec("LIMIT", "%d train images (sizing probe, not a measured run)" % LIMIT)
    ntr = xtr.shape[0]
    mean = xtr.double().mean(dim=(0, 2, 3)) / 255.0
    std = (xtr.double() / 255.0).std(dim=(0, 2, 3))
    rec("channel_mean", " ".join("%.6f" % v for v in mean))
    rec("channel_std", " ".join("%.6f" % v for v in std))
    mean = mean.float().view(1, 3, 1, 1)
    std = std.float().view(1, 3, 1, 1)

    def norm(x):
        return (x.float() / 255.0 - mean) / std

    xte_n = norm(xte)
    model = build(torch)
    n_params = sum(p.numel() for p in model.parameters())
    rec("n_params", n_params)
    if n_params != N_PARAMS:
        die("ResNet-20 has %d parameters, want %d" % (n_params, N_PARAMS))

    decay = [p for p in model.parameters() if p.ndim > 1]
    no_decay = [p for p in model.parameters() if p.ndim <= 1]
    opt = torch.optim.SGD([{"params": decay, "weight_decay": WD},
                           {"params": no_decay, "weight_decay": 0.0}],
                          lr=LR_MAX, momentum=MOMENTUM, nesterov=True)
    steps_per_epoch = (ntr + BATCH - 1) // BATCH
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=LR_MAX,
                                                total_steps=EPOCHS * steps_per_epoch,
                                                pct_start=0.25, anneal_strategy="linear")
    g = torch.Generator().manual_seed(SEED)
    trace = open("%s.trace" % tag, "w")
    th = hashlib.sha256()
    epochs = []
    ar = torch.arange(32)

    def evaluate(bs):
        model.eval()
        logits = []
        with torch.no_grad():
            for i in range(0, 10000, bs):
                logits.append(model(xte_n[i:i + bs]))
        model.train()
        return torch.cat(logits)

    t_train = 0.0
    for ep in range(EPOCHS):
        t0 = time.time()
        perm = torch.randperm(ntr, generator=g)
        tot_loss, correct = 0.0, 0
        for i in range(0, ntr, BATCH):
            idx = perm[i:i + BATCH]
            n = idx.shape[0]
            # augmentation: zero-pad 4, random 32x32 crop, random horizontal flip
            xb = F.pad(xtr[idx], (4, 4, 4, 4))
            ox = torch.randint(0, 9, (n,), generator=g)
            oy = torch.randint(0, 9, (n,), generator=g)
            flip = torch.rand(n, generator=g) < 0.5
            rows = oy.view(n, 1) + ar.view(1, 32)
            cols = ox.view(n, 1) + ar.view(1, 32)
            cols = torch.where(flip.view(n, 1), cols.flip(1), cols)
            xb = xb[torch.arange(n).view(n, 1, 1, 1), torch.arange(3).view(1, 3, 1, 1),
                    rows.view(n, 1, 32, 1), cols.view(n, 1, 1, 32)]
            yb = ytr[idx]
            out = model(norm(xb))
            loss = F.cross_entropy(out, yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            lv = loss.item()
            if lv != lv:
                die("loss is NaN at epoch %d step %d" % (ep + 1, i // BATCH))
            trace.write(float(lv).hex() + "\n")
            th.update(np.float32(lv).tobytes())
            tot_loss += lv * n
            correct += (out.argmax(1) == yb).sum().item()
        dt = time.time() - t0
        t_train += dt
        te = evaluate(1000)
        acc = (te.argmax(1) == yte).float().mean().item()
        epochs.append({"epoch": ep + 1, "train_loss": tot_loss / ntr, "train_acc": correct / ntr,
                       "test_acc": acc, "epoch_s": round(dt, 2),
                       "samples_per_s": round(ntr / dt, 1)})
        print("epoch %2d  loss %.4f  train %.4f  test %.4f  %.1fs  %.0f samples/s"
              % (ep + 1, tot_loss / ntr, correct / ntr, acc, dt, ntr / dt), flush=True)
    trace.close()
    rec("train_s", "%.1f" % t_train)
    rec("train_samples_per_s", "%.1f" % (EPOCHS * ntr / t_train))

    # Inference, timed; then the identities on the final model.
    t0 = time.time()
    logits = evaluate(1000)
    dt = time.time() - t0
    rec("infer_s", "%.2f" % dt)
    rec("infer_samples_per_s", "%.0f" % (10000 / dt))
    if not torch.isfinite(logits).all():
        die("non-finite logits")
    pred = logits.argmax(1)
    conf = torch.zeros(10, 10, dtype=torch.long)
    conf.index_put_((yte, pred), torch.ones(10000, dtype=torch.long), accumulate=True)
    # Conservation: each true-class row holds exactly its 1000 test images, the total is 10000,
    # and the confusion trace equals the count of correct predictions.
    if conf.sum(1).tolist() != [1000] * 10 or int(conf.sum()) != 10000:
        die("confusion rows do not sum to 1000 per class")
    ncorrect = int((pred == yte).sum())
    if int(conf.trace()) != ncorrect:
        die("confusion trace %d != correct count %d" % (int(conf.trace()), ncorrect))
    acc = ncorrect / 10000
    rec("test_acc", "%.4f" % acc)
    rec("per_class_acc", " ".join("%.3f" % (conf[c, c].item() / 1000) for c in range(10)))
    p = torch.softmax(logits.double(), 1)
    psum = (p.sum(1) - 1).abs().max().item()
    rec("softmax_rowsum_maxdev", "%.2e" % psum)
    if not psum < 1e-12:
        die("softmax rows do not sum to 1 (%.2e)" % psum)
    # Batch independence in eval mode: a sample's logits must not depend on its batch-mates.
    # Different batch sizes may take different conv kernels, so the fp32 residue is reported.
    l100 = evaluate(100)
    rec("eval_bs100_vs_bs1000_max_abs_logit_diff", "%.2e" % (l100 - logits).abs().max().item())
    rec("eval_bs100_vs_bs1000_pred_flips", int((l100.argmax(1) != pred).sum()))
    rec("loss_trace_sha256", th.hexdigest())
    rec("state_sha256", state_sha(model))
    rec("final_train_loss_hex", float(epochs[-1]["train_loss"]).hex())
    rec("peak_rss_mib", "%.0f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
    OUT["epochs_detail"] = epochs
    with open("%s.json" % tag, "w") as fh:
        json.dump(OUT, fh, indent=1)
    if LIMIT == 50000:
        if not acc > FLOOR:
            die("test accuracy %.4f below floor %.2f" % (acc, FLOOR))
        if not acc < PUBLISHED_ACC + 0.01:
            die("test accuracy %.4f beats ResNet-20's published %.4f by a point: leakage?"
                % (acc, PUBLISHED_ACC))
    print("TRAIN %s OK" % tag, flush=True)


def compare(a, b):
    A, B = (json.load(open("%s.json" % t)) for t in (a, b))
    ta, tb = (open("%s.trace" % t).read() for t in (a, b))
    for k in ("decoded_sha256", "state_sha256", "loss_trace_sha256", "test_acc",
              "final_train_loss_hex"):
        print("%s\t%s\t%s" % (k, A[k], B[k]), flush=True)
        if A[k] != B[k]:
            die("%s differs between %s and %s" % (k, a, b))
    if ta != tb:
        die("loss traces differ")
    print("steps\t%d" % ta.count("\n"), flush=True)
    print("REPEAT BIT-IDENTICAL OK", flush=True)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "train":
        train(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "compare":
        compare(sys.argv[2], sys.argv[3])
    else:
        die("usage: cifar.py train <tag> | compare <A> <B>")
