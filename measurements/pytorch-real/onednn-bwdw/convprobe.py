"""One Conv2d, forward + backward, under ONEDNN_VERBOSE: which oneDNN implementation each pass
dispatches to and its exec time. Usage: ONEDNN_VERBOSE=1 python3 -u convprobe.py <label>"""
import re, sys, os, io, contextlib
import torch

label = sys.argv[1]
print("BUILD", label, "torch", torch.__version__, "file", torch.__file__)
cfg = torch.__config__.show()
for line in cfg.splitlines():
    if re.search(r"DNN|ACL|BLAS|capability", line, re.I):
        print("CFG", line.strip()[:160])
for k in ("USE_MKLDNN_ACL", "USE_ACL", "ACL"):
    print("CFG has %s: %s" % (k, k in cfg))
print("mkldnn available", torch.backends.mkldnn.is_available())
torch.set_num_threads(int(os.environ.get("THREADS", "16")))
torch.manual_seed(0)
for C in (16, 64):
    c = torch.nn.Conv2d(C, C, 3, 1, 1, bias=False)
    x = torch.randn(128, C, 32, 32, requires_grad=True)
    for it in range(6):
        sys.stdout.flush()
        print("ITER C=%d it=%d" % (C, it), flush=True)
        c.zero_grad(); x.grad = None
        c(x).sum().backward()
print("PROBE OK", label, flush=True)
