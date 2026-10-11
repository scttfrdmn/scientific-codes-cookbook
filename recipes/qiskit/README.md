---
tool: qiskit
tool_version: "2.5.2"
env: quantum
image: quay.io/aarchsci/quantum@sha256:4f22a72396c229660587b4be43bf7f832046dcf9006b3aad8f81cbfd82c6207b
spawn_version: 0.126.1
last_verified: 2026-10-11
---
# Qiskit — exact amplitudes, the DFT matrix, and a bit-identical cross-check against qutip

Runs Qiskit and Aer on Graviton4 against closed forms and against an independent quantum library. For anyone doing quantum simulation on ARM.

## Run it

```bash
make stage RECIPE=qiskit     # once: the checks only — every reference here is arithmetic
spawn task run --spec "$(make -s spec RECIPE=qiskit)" --wait
make ls RECIPE=qiskit

Statevector(bell).data            # [0.7071067811865475, 0, 0, 0.7071067811865475] — exactly
Operator(QFT(3, do_swaps=True))   # == exp(+2πi·jk/N)/√N, the DFT matrix
Clifford(qc).to_operator()        # == Operator(qc), up to a global phase of exactly 1
AerSimulator(method="stabilizer", seed_simulator=20261011).run(qc, shots=8192)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the qutip cross-check | another Aer method | **this is the only comparison that is two implementations.** Aer methods share a backend; qutip has its own gates and tensor conventions. |
| the qubit reversal | — | **don't drop it.** Qiskit is little-endian: its qubit 0 is the **rightmost** qutip tensor factor. Ignore that and you are 0.707 off, which looks like a numerics bug. |
| `do_swaps=True` | `False` | the QFT is only the DFT matrix *with* the swaps. Without them you are 1.0 away — the wrong convention is not subtly wrong. |
| `QFT(...)` | `synth_qft_full(...)` | **`QFT` is deprecated as of Qiskit 2.1 and removed in 3.0.** Both are asserted here and give the same matrix, so the migration is safe. |
| `seed_simulator=20261011` | any fixed value | keep it fixed. Sampled counts are checked against their own binomial error, and an unseeded run makes every such claim flaky ([same rule the samplers earned](../../practices/cross-checks.md)). |
| 2–4 qubits | more | statevector cost doubles per qubit. Small is the point: these sizes are hand-checkable against algebra. |

**Leave the fixture.** There isn't one — nothing is fetched, because a Bell amplitude *is* `1/√2` and the QFT *is* the DFT matrix. **Scale it** to your own circuits; the identities (unitarity, norm, tableau-vs-dense) hold at any size and cost nothing.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The analysis is **7 s** of a 1m04s window — 39 s installing Docker, 16 s pulling the 0.42 GB image ([layout](../../patterns/layout-and-effective-cost.md)). BLAS threads are pinned to 1 so the seeded sampling stays reproducible.

<details>
<summary>As shipped: two bit-identical results, a published matrix reproduced, and two controls that are O(1) away</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| qiskit / aer / qutip / numpy | read from the running install | **2.5.2** / 0.17.2 / 5.3.1 / 2.5.3 |
| Aer methods present | — | 8, incl. `stabilizer`, `extended_stabilizer`, `matrix_product_state` |
| **Bell amplitudes** | **`== ` float64 `1/√2`, exactly** | **bit-identical** |
| Bell `\|01⟩`,`\|10⟩` | **exactly 0** | `0j`, `0j` |
| norm | within 4 eps | 2.220e-16 |
| **`U†U = I`** | **< 1e-14 on 8×8** | **5.551e-16** |
| Clifford global phase | *reported* | **exactly `1.0+0.0j`** |
| **tableau vs dense unitary** | **== 0 after dephasing** | **0.000e+00** |
| **QFT vs DFT matrix, n=2,3,4** | **< 1e-13, both APIs** | 2.689e-16 / 1.415e-15 / **3.783e-15** |
| **wrong QFT conventions** | **≥ 0.1 away** | **1.000e+00** (all three) |
| **`⟨Z⟩` vs `cos θ`, 6 angles** | **< 1e-14** | **1.384e-16** |
| **Grover, 4 marked states** | **`1−P` < 1e-14** | **8.882e-16** (4 eps) |
| **qutip vs Qiskit amplitudes** | **== 0** | **0.000e+00** |
| **un-reversed control** | **≥ 0.1 away** | **7.071e-01** |
| support, exact / statevector / stabilizer | all three identical | `0000 0111 1000 1111` |
| sampled frequencies | within 5 binomial σ | **1.837 σ** |
| same seed | identical counts | yes |

### Nothing is fetched, because the answers are arithmetic

There is no dataset and no published table. A Bell state's amplitudes are `1/√2`; the QFT is
`exp(+2πi·jk/N)/√N`; `⟨Z⟩` under `RY(θ)` is `cos θ`; Grover on two qubits with one marked state
lands on it after a single iteration. `stage-inputs.sh` derives each of those **locally with numpy
alone** before an instance is paid for — confirming the DFT reference is unitary (2e-15 at n=4),
that `F` and its conjugate are far enough apart to act as a control, and that
`sin²(3·asin(½)) = 1.00000000000000000`. A wrong reference would otherwise be chased on a
running box.

**Two results are bit-identical, not merely close.** The Bell amplitudes equal the float64 nearest
value to `1/√2` under `==`, and the Clifford tableau reproduces the dense unitary at exactly
`0.000e+00`. Those are written as equalities, with no tolerance to argue about.

### The cross-library check is the only one that is two implementations

Comparing two Aer methods compares one backend twice. qutip 5.3.1 has its own gate definitions and
its own tensor-product conventions, so a 3-qubit state built independently in each is a real
cross-implementation check — the thing that is *not* available when terra meets rasterio over GDAL,
or r-arrow meets pyarrow over libarrow.

**Qiskit is little-endian, and that is measured rather than assumed.** A probe put `X` on qubit 0
alone and compared against both qutip constructions:

```text
qiskit  Statevector(X on q0)   = [0, 1, 0, 0]
qutip   tensor(I, X)|00⟩       = [0, 1, 0, 0]   <- Qiskit q0 is the RIGHTMOST factor
qutip   tensor(X, I)|00⟩       = [0, 0, 1, 0]
```

With the reversal applied, the two libraries agree **bit-identically (0.000e+00)**. Without it they
are **7.071e-01** apart — and that un-reversed comparison is kept as the control, because an
agreement that would hold under either convention would be testing nothing
([verify the metric measures agreement, not a method difference](../../practices/cross-checks.md)).

### Two more places the control is intrinsic

**The QFT.** `do_swaps=True` with the `+2πi` sign reproduces the DFT matrix to 3.783e-15 at n=4.
The other three combinations — wrong sign, no swaps, both — are each **1.000e+00** away. There is
no configuration that is *nearly* right, so this check cannot pass under a convention mix-up.

**Grover is asserted at machine precision, not at 1.** All four marked states come back with
`1−P = 8.882e-16`, which is exactly **4 eps** and identical across all four. Asserting `== 1`
would have been wrong; asserting a loose band would have discarded the information that the
departure is 4 eps of float accumulation rather than an algorithmic shortfall.

### Sampling is checked against its own error, and reproducibility comes first

`stabilizer` and `statevector` are different algorithms — a symplectic tableau versus dense
amplitude propagation — and both reproduce the exact support `{0000, 0111, 1000, 1111}`. Their
**counts** are *not* compared: each method has its own RNG stream, so the same seed gives different
samples, and comparing them would be a flaky check on two correct tools.

What is compared is each sampled frequency against the exact probability, bounded by **its own
binomial standard error** rather than a chosen number: `√(p(1−p)/8192)`, asserted at 5σ, observed
at **1.837σ**. And two runs at seed `20261011` give **identical** counts, which is what licenses
treating any sampled figure above as a measurement at all.

### Pins

| | |
|---|---|
| data | **none.** Every reference is a closed form, derived in `stage-inputs.sh` before launch |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/quantum@sha256:4f22a72396c2…` — qiskit 2.5.2, qiskit-aer 0.17.2, qutip 5.3.1, openfermion 1.7.1, python 3.14.8 |

**`qiskit 2.5.2` installs on python 3.14 via abi3**, which is worth knowing because it looks
impossible: conda-forge ships only a `py310` build on every platform, and that tag is an ABI
*floor* (`_python_abi3_support`, `cpython >=3.10`, `python` unpinned) rather than a pin. The same is
true of `rustworkx`. `qiskit-aer` by contrast is a true per-python extension at `py314`.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`e5c1c0d16a91`), so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=qiskit
spawn task run --spec "$(make -s spec RECIPE=qiskit)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/qiskit/r1/score.tsv" -
```

Fails on a pin mismatch, a Bell amplitude that is not bit-identical to `1/√2`, a non-zero
off-diagonal, `U†U` off `I` by 1e-14, a tableau that disagrees with its dense form, a QFT that
misses the DFT matrix *or* a wrong convention that comes within 0.1 of passing, `⟨Z⟩` off `cos θ`,
Grover leaving more than 1e-14 off the marked state, a qutip comparison that is not exactly 0 *or*
an un-reversed control that is not clearly wrong, a support mismatch, a frequency beyond 5σ, or two
same-seed runs that differ — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

The log carries four `DeprecationWarning`s for `qiskit.circuit.library.QFT`. They are deliberate:
the recipe asserts the deprecated class **and** its replacement give the same matrix, so the
warning is the evidence the migration path is being exercised, not noise to suppress.

### Not covered

**Noise, error mitigation and real backends** — everything here is an ideal simulator, so nothing
speaks to `NoiseModel`, readout correction, or transpilation to a coupling map. **The rest of the
env**: `qiskit-algorithms` (VQE, QAOA), `qiskit-optimization`, `qiskit-machine-learning` and
`openfermion` are all installed and unexercised; a VQE ground-state energy against an exact
diagonalisation would be the natural next identity. **`extended_stabilizer` and
`matrix_product_state`**, two Aer methods that would extend the cross-method comparison. **Circuit
depth and qubit counts that matter** — 2–4 qubits exercises the algebra, not the simulator's
scaling, and no performance claim is made.

</details>
