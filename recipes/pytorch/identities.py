#!/usr/bin/env python3
"""PyTorch (CPU) on Graviton4: autograd, linear algebra, optimisation and determinism held to
identities, closed forms and a second code.

The conda-forge `pytorch` package ships no test suite (only seven helper modules named
test_*.py), so these checks go at what a user relies on:

 1. Autograd. `gradcheck`/`gradgradcheck` compare the analytic Jacobian (and its derivative)
    against central finite differences in float64 -- that comparison IS the identity -- over
    matmul, conv2d, cross-entropy on log_softmax, layer_norm, the real nn.LSTMCell kernel
    (weights as inputs) and a custom composition. A deliberately wrong backward must FAIL it.
    Plus closed forms: d/dW ||XW - Y||^2 == 2 X'(XW - Y), and the Hessian of x'Ax == A + A'.
 2. Linear algebra. solve/lstsq/eigh/svd/cholesky reconstruct their inputs and recover planted
    spectra. numpy links the SAME OpenBLAS/LAPACK here, so agreement with numpy checks torch's
    wrapping (row-major tensors handed to a column-major library), not LAPACK itself; the
    reconstructions are what check the numerics.
 3. Optimisation. LBFGS in float64 reaches the logistic MLE that statsmodels (Newton) and
    sklearn (newton-cholesky) reach -- a fixed point, three codes -- and the OLS optimum of
    numpy.linalg.lstsq. A small MLP on a planted function generalises to held-out data, bounded
    by the noise floor the design sets.
 4. Determinism. Fixed seed + fixed threads + use_deterministic_algorithms: two trainings are
    bit-identical; a different seed must differ. 1 thread vs N is REPORTED, not asserted.
 5. Backend on record: torch.__config__, CPU capability, OpenBLAS corename, and float32 matmul
    throughput as an observation only.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import ctypes
import glob
import os
import platform
import sys
import time
import warnings

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    write_score()
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        a = v.detach().cpu().numpy() if hasattr(v, "detach") else v
        if not np.all(np.isfinite(np.asarray(a, dtype="float64"))):
            die("%s is not finite" % name)


def check(name, val, bound):
    finite(name, val)
    val = float(val)
    rec(name, "%.3e (bound %.0e)" % (val, bound))
    if not val < bound:
        die("%s = %.3e, bound %.0e" % (name, val, bound))


def write_score():
    with open("score.tsv", "w") as fh:
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


warnings.simplefilter("ignore", category=FutureWarning)
import torch
import torch.nn.functional as F
from torch.autograd import gradcheck, gradgradcheck

NCPU = os.cpu_count() or 1
SEED = 20261016

# ======================================= 0. what is running
ver("torch", lambda: torch.__version__)
ver("numpy", lambda: np.__version__)
ver("sklearn", lambda: __import__("sklearn").__version__)
ver("statsmodels", lambda: __import__("statsmodels").__version__)
ver("python", lambda: platform.python_version())
ver("cpu_count", lambda: NCPU)
ver("cpu_capability", lambda: torch.backends.cpu.get_cpu_capability())
ver("mkldnn_available", lambda: torch.backends.mkldnn.is_available())
ver("openmp_available", lambda: torch.backends.openmp.is_available())
ver("mkl_available", lambda: torch.backends.mkl.is_available())
ver("parallel_backend", lambda: [l.strip() for l in torch.__config__.parallel_info().splitlines()
                                  if "parallel backend" in l][0])


def corename():
    lib = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))[0]
    f = ctypes.CDLL(lib).openblas_get_corename
    f.restype = ctypes.c_char_p
    return "%s (%s)" % (f().decode(), os.path.basename(lib))


ver("openblas_corename", corename)
try:
    with open("torch-config.txt", "w") as fh:
        fh.write(torch.__config__.show())
        fh.write("\n")
        fh.write(torch.__config__.parallel_info())
except Exception as exc:
    rec("torch_config", "unwritable (%s)" % type(exc).__name__)

# A CPU build is the claim: no CUDA compiled in.
rec("torch.version.cuda", torch.version.cuda)
if torch.version.cuda is not None or torch.cuda.is_available():
    die("this is meant to be the CPU build")
# Not emulated: the interpreter's architecture is the host's.
rec("arch", platform.machine())
if platform.machine() != os.uname().machine:
    die("interpreter architecture differs from the host")

torch.set_num_threads(NCPU)
torch.manual_seed(SEED)
D = torch.float64

# ======================================= 1. autograd
g = torch.Generator().manual_seed(SEED)


def r(*shape):
    return torch.randn(*shape, generator=g, dtype=D, requires_grad=True)


A = r(4, 5); B = r(5, 3)
img = r(1, 2, 6, 6); wconv = r(3, 2, 3, 3); bconv = r(3)
logits_in = r(6, 4); wlin = r(4, 5); target = torch.tensor([0, 1, 2, 3, 4, 1])
ln_x = r(3, 8); ln_w = r(8); ln_b = r(8)
cell = torch.nn.LSTMCell(4, 3).to(D)
lx = r(2, 4); lh = r(2, 3); lc = r(2, 3)
wih = r(12, 4); whh = r(12, 3); bih = r(12); bhh = r(12)
cx = r(5); cM = r(5, 5)


def lstm(x, h, c, Wi, Wh, bi, bh):
    out = torch.func.functional_call(cell, {"weight_ih": Wi, "weight_hh": Wh,
                                            "bias_ih": bi, "bias_hh": bh}, (x, (h, c)))
    return out[0], out[1]


def composite(x, M):
    y = torch.tanh(M @ x) * torch.sigmoid(x)
    return torch.logsumexp(y, 0) + (y ** 2).sum().sqrt() + torch.cumprod(torch.cos(x), 0).sum()


CASES = [
    ("matmul", torch.matmul, (A, B)),
    ("conv2d", lambda x, w, b: F.conv2d(x, w, b, padding=1, stride=1), (img, wconv, bconv)),
    ("cross_entropy(log_softmax)", lambda x, w: F.nll_loss(F.log_softmax(x @ w, 1), target),
     (logits_in, wlin)),
    ("softmax", lambda x: F.softmax(x, 1), (logits_in,)),
    ("layer_norm", lambda x, w, b: F.layer_norm(x, (8,), w, b), (ln_x, ln_w, ln_b)),
    ("nn.LSTMCell", lstm, (lx, lh, lc, wih, whh, bih, bhh)),
    ("composite", composite, (cx, cM)),
]
n_ok = 0
for name, fn, args in CASES:
    ok1 = gradcheck(fn, args, eps=1e-6, atol=1e-8, rtol=1e-6, raise_exception=False)
    ok2 = gradgradcheck(fn, args, eps=1e-6, atol=1e-8, rtol=1e-6, raise_exception=False)
    rec("gradcheck/gradgradcheck %s" % name, "%s / %s" % (ok1, ok2))
    if not (ok1 and ok2):
        die("autograd disagrees with finite differences for %s" % name)
    n_ok += 1
rec("autograd cases passing", "%d of %d" % (n_ok, len(CASES)))


class WrongSquare(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return x * x

    @staticmethod
    def backward(ctx, go):
        (x,) = ctx.saved_tensors
        return go * 2.0 * x * 1.001       # 0.1% wrong


ctl = gradcheck(WrongSquare.apply, (r(6),), eps=1e-6, atol=1e-8, rtol=1e-6,
                raise_exception=False)
rec("control: gradcheck on a 0.1%-wrong backward", ctl)
if ctl:
    die("gradcheck accepted a wrong gradient -- it cannot discriminate")

# Closed form: d/dW ||XW - Y||_F^2 = 2 X'(XW - Y)
X = torch.randn(200, 10, generator=g, dtype=D)
Y = torch.randn(200, 3, generator=g, dtype=D)
W = torch.randn(10, 3, generator=g, dtype=D, requires_grad=True)
((X @ W - Y) ** 2).sum().backward()
G = 2 * X.T @ (X @ W.detach() - Y)
check("dL/dW vs 2X'(XW-Y), rel", (W.grad - G).abs().max() / G.abs().max(), 1e-13)

# Closed form: Hessian of x'Ax is A + A'
Aq = torch.randn(7, 7, generator=g, dtype=D)
H = torch.autograd.functional.hessian(lambda x: x @ Aq @ x, torch.randn(7, generator=g, dtype=D))
check("hessian(x'Ax) vs A+A', rel", (H - (Aq + Aq.T)).abs().max() / (Aq + Aq.T).abs().max(), 1e-14)

# nn.LSTMCell against its defining equations (independent of the fused kernel)
with torch.no_grad():
    h1, c1 = lstm(lx, lh, lc, wih, whh, bih, bhh)
    gates = lx @ wih.T + bih + lh @ whh.T + bhh
    i, f, gg, o = gates.chunk(4, 1)
    c_ref = torch.sigmoid(f) * lc + torch.sigmoid(i) * torch.tanh(gg)
    h_ref = torch.sigmoid(o) * torch.tanh(c_ref)
check("nn.LSTMCell vs its equations", max((h1 - h_ref).abs().max(), (c1 - c_ref).abs().max()), 1e-14)

# ======================================= 2. linear algebra, planted spectra
rng = np.random.default_rng(SEED)
n = 200


def orth(k):
    q, _ = np.linalg.qr(rng.normal(size=(k, k)))
    return q


s_planted = np.geomspace(1.0, 1e3, n)[::-1].copy()          # cond = 1e3 by construction
U0, V0 = orth(n), orth(n)
An = U0 @ np.diag(s_planted) @ V0.T
At = torch.from_numpy(An.copy())
EPS = np.finfo(np.float64).eps
rec("planted condition number", "%.1e" % (s_planted[0] / s_planted[-1]))

# solve: backward error is O(eps) for a stable solver; forward agreement is cond x that.
b = rng.normal(size=n); bt = torch.from_numpy(b.copy())
x = torch.linalg.solve(At, bt)
check("solve: ||Ax-b|| / (||A|| ||x||)",
      torch.linalg.norm(At @ x - bt) / (torch.linalg.norm(At, 2) * torch.linalg.norm(x)), 1e-14)
xn = np.linalg.solve(An, b)
check("solve vs numpy, rel", np.abs(x.numpy() - xn).max() / np.abs(xn).max(), 1e-10)

# svd: reconstruction, orthogonality, the planted singular values
U, S, Vh = torch.linalg.svd(At)
check("svd: ||U S V' - A|| / ||A||", torch.linalg.norm(U @ torch.diag(S) @ Vh - At) / torch.linalg.norm(At), 1e-13)
check("svd: ||U'U - I||", (U.T @ U - torch.eye(n, dtype=D)).abs().max(), 1e-13)
check("svd: singular values vs planted, rel", np.abs(S.numpy() - s_planted).max() / s_planted[0], 1e-13)
check("svd vs numpy singular values, rel",
      np.abs(S.numpy() - np.linalg.svd(An, compute_uv=False)).max() / s_planted[0], 1e-13)

# eigh: symmetric matrix with planted, signed eigenvalues
lam = np.linspace(-5.0, 7.0, n)
Q0 = orth(n)
Sym = Q0 @ np.diag(lam) @ Q0.T
Sym = (Sym + Sym.T) / 2
St = torch.from_numpy(Sym.copy())
L, V = torch.linalg.eigh(St)
check("eigh: ||V L V' - S|| / ||S||", torch.linalg.norm(V @ torch.diag(L) @ V.T - St) / torch.linalg.norm(St), 1e-13)
check("eigh: eigenvalues vs planted, abs/max|l|", np.abs(L.numpy() - lam).max() / 7.0, 1e-13)
check("eigh vs numpy eigvalsh, abs/max|l|", np.abs(L.numpy() - np.linalg.eigvalsh(Sym)).max() / 7.0, 1e-13)

# cholesky: SPD with planted spectrum
Spd = Q0 @ np.diag(np.geomspace(1.0, 1e3, n)) @ Q0.T
Spd = (Spd + Spd.T) / 2
Pt = torch.from_numpy(Spd.copy())
Lc = torch.linalg.cholesky(Pt)
check("cholesky: ||LL' - A|| / ||A||", torch.linalg.norm(Lc @ Lc.T - Pt) / torch.linalg.norm(Pt), 1e-14)
check("cholesky: upper triangle", torch.triu(Lc, 1).abs().max(), 1e-300)
if not bool((torch.diagonal(Lc) > 0).all()):
    die("cholesky diagonal not positive")
check("cholesky vs numpy, rel", np.abs(Lc.numpy() - np.linalg.cholesky(Spd)).max() / np.abs(Lc.numpy()).max(), 1e-12)

# lstsq: tall system; the residual is orthogonal to the column space
Xl = rng.normal(size=(300, 50)); yl = rng.normal(size=300)
Xt, yt = torch.from_numpy(Xl.copy()), torch.from_numpy(yl.copy())
beta = torch.linalg.lstsq(Xt, yt.unsqueeze(1), driver="gelsd").solution.squeeze(1)
res = yt - Xt @ beta
check("lstsq: ||X'r|| / (||X|| ||r||)", torch.linalg.norm(Xt.T @ res) / (torch.linalg.norm(Xt, 2) * torch.linalg.norm(res)), 1e-13)
bn = np.linalg.lstsq(Xl, yl, rcond=None)[0]
check("lstsq vs numpy, rel", np.abs(beta.numpy() - bn).max() / np.abs(bn).max(), 1e-11)
ctl = np.abs(torch.linalg.solve(At.T, bt).numpy() - xn).max() / np.abs(xn).max()
rec("control: solve(A') vs numpy solve(A), rel", "%.3e" % ctl)
if not ctl > 1e-3:
    die("a transposed solve agreed with the untransposed one -- the comparison cannot see a layout error")

# ======================================= 3. optimisation: LBFGS vs closed forms and two codes
import statsmodels.api as sm
from sklearn.linear_model import LogisticRegression

N, P = 4000, 5
Xd = rng.normal(size=(N, P))
beta_true = np.array([0.8, -1.2, 0.5, 0.0, 1.5]); b0 = -0.3
pr = 1 / (1 + np.exp(-(b0 + Xd @ beta_true)))
yd = (rng.random(N) < pr).astype(float)
Xa = np.column_stack([np.ones(N), Xd])


def lbfgs(loss_fn, w):
    opt = torch.optim.LBFGS([w], lr=1, max_iter=500, tolerance_grad=1e-12,
                            tolerance_change=1e-16, history_size=20, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad()
        l = loss_fn(w)
        l.backward()
        return l
    for _ in range(5):
        opt.step(closure)
    return w.detach()


Xat, ydt = torch.from_numpy(Xa.copy()), torch.from_numpy(yd.copy())
w_t = lbfgs(lambda w: F.binary_cross_entropy_with_logits(Xat @ w, ydt),
            torch.zeros(P + 1, dtype=D, requires_grad=True)).numpy()
sm_fit = sm.Logit(yd, Xa).fit(method="newton", tol=1e-12, maxiter=100, disp=0)
w_sm = sm_fit.params
sk = LogisticRegression(C=np.inf, solver="newton-cholesky", tol=1e-12, max_iter=200,
                        fit_intercept=False).fit(Xa, yd)
w_sk = sk.coef_.ravel()
rec("logistic beta (torch LBFGS)", np.array2string(w_t, precision=10))
rec("logistic beta (statsmodels)", np.array2string(w_sm, precision=10))
score = np.abs(Xa.T @ (yd - 1 / (1 + np.exp(-Xa @ w_t)))).max() / N
# score/N IS the gradient of the mean loss. LBFGS stops when the loss stops changing, and a
# float64 loss near 0.5 resolves changes of ~eps*0.5, so the gradient it can certify is ~sqrt(eps)
# (1.5e-8), not tolerance_grad. The bound is ~7 sqrt(eps); the coefficient bound below is that
# divided by the Hessian's smallest eigenvalue, with margin.
check("logistic: score equation max|X'(y-p)|/N at torch's optimum", score, 1e-7)
check("logistic: torch vs statsmodels, max|diff|", np.abs(w_t - w_sm).max(), 1e-6)
check("logistic: torch vs sklearn, max|diff|", np.abs(w_t - w_sk).max(), 1e-6)
z = np.abs((w_sm - np.r_[b0, beta_true]) / sm_fit.bse).max()
check("logistic: planted beta recovered, max |z|", z, 5.0)

# OLS by LBFGS == lstsq
yo = Xa @ np.r_[b0, beta_true] + rng.normal(scale=0.5, size=N)
yot = torch.from_numpy(yo.copy())
w_o = lbfgs(lambda w: ((Xat @ w - yot) ** 2).mean(), torch.zeros(P + 1, dtype=D, requires_grad=True)).numpy()
check("OLS: torch LBFGS vs numpy lstsq, max|diff|", np.abs(w_o - np.linalg.lstsq(Xa, yo, rcond=None)[0]).max(), 1e-8)

# MLP on a planted function; the noise floor is SIGMA^2 by design.
SIGMA = 0.1


def planted(Xs):
    return torch.sin(2 * Xs[:, 0]) + Xs[:, 1] * Xs[:, 2] - 0.5 * Xs[:, 0] ** 2


def make_data(seed):
    gg = torch.Generator().manual_seed(seed)
    Xtr = torch.rand(2000, 3, generator=gg) * 2 - 1
    Xte = torch.rand(2000, 3, generator=gg) * 2 - 1
    ytr = planted(Xtr) + SIGMA * torch.randn(2000, generator=gg)
    yte = planted(Xte) + SIGMA * torch.randn(2000, generator=gg)
    return Xtr, ytr, Xte, yte


def train(seed, threads, epochs=300):
    torch.set_num_threads(threads)
    torch.manual_seed(seed)
    Xtr, ytr, Xte, yte = make_data(SEED)
    net = torch.nn.Sequential(torch.nn.Linear(3, 64), torch.nn.Tanh(),
                              torch.nn.Linear(64, 64), torch.nn.Tanh(), torch.nn.Linear(64, 1))
    opt = torch.optim.Adam(net.parameters(), lr=3e-3)
    gperm = torch.Generator().manual_seed(seed)
    losses = []
    for _ in range(epochs):
        perm = torch.randperm(2000, generator=gperm)
        for k in range(0, 2000, 100):
            idx = perm[k:k + 100]
            opt.zero_grad()
            l = F.mse_loss(net(Xtr[idx]).squeeze(1), ytr[idx])
            l.backward()
            opt.step()
            losses.append(l.item())
    with torch.no_grad():
        te = F.mse_loss(net(Xte).squeeze(1), yte).item()
    torch.set_num_threads(NCPU)
    return net, te, np.array(losses), (Xtr, ytr, Xte, yte)


torch.use_deterministic_algorithms(True)
t0 = time.time()
net1, te1, loss1, (Xtr, ytr, Xte, yte) = train(SEED, NCPU)
rec("mlp train wall (s)", "%.1f" % (time.time() - t0))
floor = SIGMA ** 2
finite("mlp test MSE", te1)
rec("mlp held-out MSE / noise floor sigma^2", "%.4f" % (te1 / floor))
# Lower bound: the floor cannot be beaten (2000-point estimate has rel SD sqrt(2/2000) = 3.2%).
if not te1 > 0.8 * floor:
    die("held-out MSE below the noise floor -- test data leaked into training")
if not te1 < 1.5 * floor:
    die("held-out MSE %.4f > 1.5 sigma^2 -- the net did not learn the planted function" % te1)
Xl1 = torch.cat([torch.ones(2000, 1), Xtr], 1).double()
bl = torch.linalg.lstsq(Xl1, ytr.double().unsqueeze(1)).solution
lin_te = F.mse_loss((torch.cat([torch.ones(2000, 1), Xte], 1).double() @ bl).squeeze(1), yte.double()).item()
rec("control: linear model held-out MSE / sigma^2", "%.2f" % (lin_te / floor))
if not lin_te > 3 * floor:
    die("a linear model fits the planted function -- the MLP check cannot discriminate")

# ======================================= 4. determinism
net2, te2, loss2, _ = train(SEED, NCPU)
same = all(torch.equal(a, b) for a, b in zip(net1.state_dict().values(), net2.state_dict().values()))
rec("determinism: same seed, %d threads, params bit-identical" % NCPU, same)
rec("determinism: same seed, loss trajectory identical", bool(np.array_equal(loss1, loss2)))
if not (same and np.array_equal(loss1, loss2) and te1 == te2):
    die("two identically seeded trainings differ")
net3, te3, _, _ = train(SEED + 1, NCPU)
d3 = max((a - b).abs().max().item() for a, b in zip(net1.state_dict().values(), net3.state_dict().values()))
rec("control: different seed, max |param diff|", "%.3e" % d3)
if not d3 > 1e-3:
    die("a different seed gave the same model -- the determinism check is vacuous")
net4, te4, loss4, _ = train(SEED, 1)
d4 = max((a - b).abs().max().item() for a, b in zip(net1.state_dict().values(), net4.state_dict().values()))
rec("observed: 1 thread vs %d, max |param diff|" % NCPU, "%.3e" % d4)
rec("observed: 1 thread vs %d, held-out MSE/sigma^2" % NCPU, "%.4f vs %.4f" % (te4 / floor, te1 / floor))
torch.use_deterministic_algorithms(False)

# ======================================= 5. float32 matmul throughput (observation, not a benchmark)
for th in (1, NCPU):
    torch.set_num_threads(th)
    a = torch.randn(2048, 2048); bb = torch.randn(2048, 2048)
    a @ bb
    reps = 5
    t0 = time.perf_counter()
    for _ in range(reps):
        a @ bb
    dt = (time.perf_counter() - t0) / reps
    rec("observed: fp32 2048^3 matmul GFLOP/s, %d thread(s)" % th, "%.1f" % (2 * 2048 ** 3 / dt / 1e9))
torch.set_num_threads(NCPU)

rec("status", "PYTORCH IDENTITIES OK")
write_score()
print("PYTORCH IDENTITIES OK")
