# Qiskit over the whole QASMBench suite: Graviton4, Intel and AMD

> **Qiskit compiles all 123 valid QASMBench circuits (2 to 433 qubits, 8.3M gates in, 130M out)
> to an IBM heavy-hex device, and every compiled circuit that a simulator can hold — 93 of the 104
> unitary ones, up to 458 active qubits — is the same computation as its source.** 88 match at
> machine precision. The other 5 are the QFT family: Qiskit's level-2 two-qubit resynthesis
> approximates them by design, and level 1 compiles them exactly. All three chips give the same
> verdicts. Graviton4 is cheapest at $0.64 per suite billed. AMD is fastest (3,998 s against 4,783) but $0.72, and Intel is $0.74.

Workload: every original circuit of [QASMBench](https://github.com/pnnl/QASMBench) at commit
`357b942` (132 files, 255 MB of OpenQASM 2, small/medium/large). QASMBench is the standard
benchmark suite for quantum compilers and simulators, and the source of the circuits in Qiskit's own
Benchpress. Each circuit goes through what a Qiskit user runs:

- **Compile**: `generate_preset_pass_manager(optimization_level=2)` (the `transpile()` default),
  seed fixed, to the smallest heavy-hex coupling map that holds it (19 to 547 qubits), basis
  `{cz, rz, sx, x}`.
- **Simulate**: Aer `statevector` up to 30 qubits (16 GiB), `matrix_product_state` at any width,
  and `stabilizer` for Clifford circuits.

## Run it

```sh
B=$(make -s print-bucket)
measurements/qiskit-real/stage-inputs.sh "$B"    # QASMBench fetched ON AWS, checked against the commit's git blob ids
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py qiskit $a $B r4 measurements/qiskit-real/real.task.json)" --wait
done
python3 measurements/qiskit-real/collect.py <dir holding each chip's circuits.jsonl as arm64/ x86_64/ amd/>
```

`real.py` runs each circuit as its own process under a wall-clock limit (compile 90 min, MPS
check 10 min), 8 at a time with one thread each, so an intractable circuit is a reported timeout,
not a hung run. The serial dense phases use all 8 threads.

## Results

8 workers on a `2xlarge` (8 vCPU, 64 GiB) of each chip, the same `quantum` env (aarch.science
arm64; its linux-64 twin on both x86 chips), the same staged bytes:

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.2xlarge` | 4,783 s | 4,887 s | 29.7 GiB | 0.4713 | **0.626** | **0.640** |
| Intel (Xeon 6975P-C) | `r8i.2xlarge` | 4,696 s | 4,759 s | 31.3 GiB | 0.5557 | 0.725 | 0.735 |
| AMD (EPYC 9R45) | `r8a.2xlarge` | **3,998 s** | 4,074 s | 31.3 GiB | 0.6390 | 0.710 | 0.723 |

A *result* is the whole suite compiled and checked. *Tool wall* is `real.py`; *billed* is EC2
launch to the task's end.

- **One circuit sets the wall.** `bwt_n177` (2.3M gates, 177 qubits) compiles to 53M gates in
  ~1,040 s of transpile on one core and peaks at 26 GiB. It is about a third of the run, and 8
  vCPUs average under 3 busy. More cores would not shorten it, which is why this is a `2xlarge`.
- **The phases** (Graviton4 / Intel / AMD): compile 2,504 / 2,393 / 2,013 s; MPS checks 1,205
  / 1,206 / 1,204 s, which is mostly the 11 circuits that hit the 10-minute limit; dense
  originals 629 / 724 / 462 s. AMD's run is 1.20x faster than Graviton4's, and its hour
  costs 36% more, so Graviton4 stays cheaper per suite.
- **Memory picks the family.** The peak is the bwt_n177 compile (26 GiB) or the 30-qubit dense
  state (16 GiB plus a copy, 25 GiB). Both need an `r` box at 8 vCPUs. A first run on
  `r8g.4xlarge` (16 vCPU) took 3,374 s at $0.94/hr, which is faster but costs more per suite.
- **Intel's 8 vCPUs are 4 cores × 2 threads** (`Thread(s) per core: 2`); AMD's and Graviton4's are
  8 full cores. Since the run is mostly one or two busy processes, the SMT split costs Intel little.
- n = 1 per chip. The verdicts are deterministic, so the timings measure one computation.

## Why this size

This is the whole suite, which is what a compiler or simulator study runs: Benchpress and the
QASMBench paper report across all of it. The size also matters for the checks. The QFT
approximation below only shows above 18 qubits, the measured-circuit diagonal-gate removal only
on 7 particular circuits, and the 9 invalid files only because every file is parsed.

## What held at scale, on all three chips

| assertion | bound | observed (Graviton4; x86 the same verdicts) |
|---|---|---|
| every file compiles, or is refused as invalid OpenQASM 2 | no other failure | 123 compiled, 9 refused (below) |
| every compiled gate in `{cz, rz, sx, x}`, descending into control flow | 0 | 0 of 129.7M |
| every compiled two-qubit gate on a heavy-hex edge | 0 | 0 of 34,566,715 `cz` |
| **compiled == original**, dense (compiled width ≤ 28) | 1−\|⟨E\|C⟩\|² ≤ 1e-6 | 51 circuits, widths 2–27, worst 3.6e-9 (`qft_n18`) |
| **compiled == original**, MPS inverse test (`C` then `E⁻¹`, P(\|0…0⟩)) | ≤ 1e-6 | 42 circuits, widths 29–458, worst 4.6e-7 (`qft_n320`) |
| ... of those, at machine precision | ≤ 16 eps per gate | 88 of 93 |
| **the 5 above machine precision compile exactly at level 1** | ≤ 16 eps per gate | all 5 (`qft_n18/29/63/160/320`) |
| compiled-state norm; original-state norm | eps × (gates + 2ⁿ) | ≤ 2.0e-13; ≤ 2.4e-11 |
| classical-reversible originals: dense amplitude at the **integer bit-simulated** outcome | 1 − P ≤ 1e-10 | 6 originals up to 28 qubits, exactly 0 |
| Clifford originals: dense support and 2⁻ᵏ probabilities == terra's `StabilizerState` | 1e-10 | 14 originals up to 30 qubits, ≤ 4.4e-16 |
| QFT of a basis state: every amplitude at 2^(−n/2) | 1e-10 | 3 originals up to 29 qubits, ≤ 8.3e-17 |
| `quantum_info.Statevector` == Aer (two implementations) | 1e-10 | 50 originals, ≤ 7.7e-15 |
| full width: Aer MPS on classical originals returns only the bit-simulated outcome | exact | 13 originals up to 433 qubits |
| full width: Aer stabilizer and MPS samples of Clifford originals all in terra's support at one 2⁻ᵏ | exact | 25 originals up to 280 qubits |

The compiled circuit `C` is compared on the physical qubits it touches against the original `E`
placed where the final layout says each virtual qubit ends. The reference side runs on
`quantum_info` (a second implementation) wherever gates × 2ⁿ ≤ 2e9 (48 of 51 dense checks), and on
Aer otherwise. The dense bound comes from the measured spread: the first Graviton4 run put every
circuit not touched by the QFT tolerance at ≤ 6.4 eps per gate, dense and MPS alike, and the QFT
family at 3,500–12,700. The 1e-6 ceiling only has to separate the documented tolerance from a
miscompile, which is O(1) away. The measured cases below were all 0.5–1.0.

**Two things Qiskit does on purpose that a naive check would call a bug:**

- **Level-2 two-qubit resynthesis is approximate even at `approximation_degree=1.0`.** The Weyl
  decomposition accepts a specialisation at trace fidelity ≥ 1 − 1e-9 per block. Measured on the
  decomposer alone: `CU1(θ)` with θ ≤ 4.8e-5 synthesises to **zero** `cz` with operator error θ/4,
  even with `approximate=False`. A QFT has many such blocks, so `qft_n320` compiles to 1 − F =
  4.6e-7. Recompiled at level 1 (no `TwoQubitPeepholeOptimization`) the same circuits are at
  ≤ 9.9e-10, within machine precision for their size. That places the deviation in the synthesis
  tolerance and not in layout, routing or translation.
- **Compiling a circuit with its final measurements drops diagonal gates before them.** This is
  correct for the measured distribution and wrong for a state comparison. `dnn_n2`,
  `error_correctiond3_n5`, `ising_n10`, `dnn_n8`, `dnn_n16`, `gcm_h6` and `variational_n4` came out
  0.5–1.0 off in state fidelity when compiled with measurements, and exact without them. So the
  semantic check compiles the measurement-free circuit, with the same settings and seed. The
  measured compile is the timed workload, and both pass the structural checks.

**9 QASMBench files are not valid OpenQASM 2, and Qiskit refuses each with a located parse
error:** the three `QAOA_3SAT` files apply `cx` to the same qubit twice (`cx qr[325],qr[325];` at
line 1400 of N1000: a 3-SAT clause with a repeated variable), and `vqe_uccsd_n4/6/8/28`,
`hhl_n10/14` measure a register (`q`, `c`) the file never declares (it declares `reg`). They are
reported here, not fixed: editing them would make them someone else's benchmark.

## What is reported, not asserted

- **11 unitary circuits have no semantic verdict**: they are too entangled for MPS within 10
  minutes (`QV_n32`, `QV_n100`, `knn_n129/341`, `swap_test_n361`, `ising_n98`, `dnn_n51`,
  `qugan_n71/111/395`, and `vqe_n24`, whose 2.3M gates at 26 qubits are also over the dense budget).
  Quantum volume circuits are random by construction, so no classical shortcut exists. `qugan_n39`
  finishes its MPS check just inside the limit on Graviton4 and AMD and just outside it on Intel.
- **19 circuits are not unitary** (mid-circuit measurement, reset, `if`), so only the structural
  checks apply: `bwt_*`, `cc_*`, `square_root_*`, `seca_n11`, `bb84_n8`, `shor_n5`, `ipea_n2`,
  `inverseqft_n4`, `qec_sm_n5`.

## Fixed point or path

Everything asserted is a fixed point, and every verdict is the same on all three chips. The parsed
circuits (qubits, gates, depth, two-qubit count) are identical on all 132. The compiled circuit
itself is a **path**:

- **The two-qubit structure follows the seed, not the chip.** The `cz` count is identical on all
  123 compiled circuits on Intel and on AMD against Graviton4. The routed layout and SWAP choices
  are the same search.
- **The single-qubit layer follows the floating point.** The exact compiled QASM is byte-identical
  on 55 of 123 (Intel vs Graviton4). On 35 more, only the last bits of angles differ. On 33, the
  gate sequence differs: 26 have a different total gate count, by up to 0.9% (`gcm_h6`;
  `bwt_n177` is 53,189,206 vs 53,369,759). A last-bit difference decides whether a rotation counts as the identity, and the
  1q optimiser takes a different branch. AMD differs from Graviton4 on the same 68 circuits, and is byte-identical to Intel on 120 of
  123 (the other 3, `bv_n30/70/280`, have the same gate count and depth). So the compiled
  circuit follows the build (both x86 chips run the same linux-64 twin), not the vendor.
- **Fidelities agree to the noise they are made of**: the largest cross-chip difference in
  1 − F is 1.4e-9 (`multiplier_n350`, 2.0M gates), against a machine-precision bound of 7e-9 for
  its size. OpenBLAS reports `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips. These
  simulations do not run through BLAS.

## Pins

| | |
|---|---|
| input | `pnnl/QASMBench` commit `357b942396d5c2b7cbc1c229c585a6ef5ccaebac`: 132 original `.qasm` + `qelib1.inc`, README, LICENSE, NOTICE (254,779,845 bytes). Fetched on AWS by `00-fetch.task.json`, each file kept only if its git blob id matches `qasmbench-357b942.gitblobs` (the commit's tree, from the GitHub API), then pinned by sha256 in `qasmbench.sha256` and re-checked in every run |
| image | arm64 `quay.io/aarchsci/quantum@sha256:4f22a723…` (the recipe's pin); x86 the env's linux-64 twin `envs-x86_64/quantum-0134d92cdcd5` |
| versions (all chips) | qiskit 2.5.2, qiskit-aer 0.17.2, numpy 2.5.3, rustworkx 0.18.1 |
| scripts | `run.sh`, `real.py` and `qasmbench.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it); `collect.py` runs locally on the three outputs |

**Data tier: a durable public id.** QASMBench has no release or DOI, so the commit is the pin and
its tree's blob ids check every byte. The per-chip `check.txt`, `host.txt`, `footprint.txt` and
`phases.txt` are in `results/`, with the cross-chip comparison in `results/crosschip.txt`. The
per-circuit `circuits.jsonl` of each run is in the bucket under `runs*/qiskit-real/r4/`.
