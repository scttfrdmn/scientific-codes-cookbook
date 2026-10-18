"""Wall time of the weight-gradient conv alone, oneDNN on vs off (off = PyTorch's own im2col + BLAS).
Usage: python3 -u convtime.py <label>"""
import os, sys, time
import torch

label = sys.argv[1]
torch.set_num_threads(int(os.environ.get("THREADS", "16")))
torch.manual_seed(0)


def med(f, n=15):
    for _ in range(3):
        f()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); f(); ts.append(time.perf_counter() - t0)
    ts.sort()
    return ts[n // 2] * 1e3


for C in (16, 64):
    x = torch.randn(128, C, 32, 32)
    w = torch.randn(C, C, 3, 3)
    go = torch.randn(128, C, 32, 32)
    ref = None
    for on in (True, False):
        with torch.backends.mkldnn.flags(enabled=on):
            fwd = med(lambda: torch.nn.functional.conv2d(x, w, padding=1))
            bww = med(lambda: torch.nn.grad.conv2d_weight(x, w.shape, go, padding=1))
            bwd = med(lambda: torch.nn.grad.conv2d_input(x.shape, w, go, padding=1))
            gw = torch.nn.grad.conv2d_weight(x, w.shape, go, padding=1)
        if ref is None:
            ref = gw
        print("TIME %s C=%d mkldnn=%s fwd %.3f ms  bwd_data %.3f ms  bwd_weights %.3f ms  |dW-dW_onednn|max %.2e"
              % (label, C, on, fwd, bwd, bww, (gw - ref).abs().max().item()), flush=True)
print("TIME OK", label, flush=True)
