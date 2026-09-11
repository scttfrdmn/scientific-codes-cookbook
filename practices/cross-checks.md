# Cross-checking two codes honestly

Many recipes here check one tool against another — bowtie2 against bwa, PySCF against Psi4, RAxML-NG against IQ-TREE. Run on the same bytes, two independent codebases agreeing is far stronger evidence than either alone. But a cross-check is only as good as the *comparison*, and there are three ways it quietly goes wrong. Each recipe states its own specific check; this page is the discipline behind them, so a recipe links here instead of re-arguing why the check is shaped the way it is.

## Compare like with like — the metric must measure agreement, not a difference in method

**The symptom:** you compare two correct tools' raw outputs and get a low number — 0.4, 0.6 — and conclude one is broken. It usually means the *comparison* was wrong, not the tool.

**Why:** two tools can be individually correct and still not produce the same *quantity*. A naive metric then measures the method difference, not disagreement about the science — and a green check on the wrong metric is worse than no check.

**Do this — ask the question both tools can answer.** Three worked failures from this cookbook, each fixed by changing the metric, not the tool:

- **Different models → raw values aren't comparable; use rank.** kallisto vs salmon TPM: raw log-TPM Pearson was **0.61** because the two use different EM / effective-length models — Spearman *rank* correlation is **0.912** once you ask the question they both answer (does transcript A rank above B).
- **Repeat-heavy references → all-mapped concordance is meaningless; restrict to confident calls.** minimap2 vs bwa: naive all-mapped agreement was **0.43** (two correct aligners break repeat ties differently), **0.9921** once gated on MAPQ ≥ 30 — "agree where both are sure."
- **Different modes reject different reads → match the modes.** bowtie2 vs bwa: default end-to-end against bwa's soft-clipping gave **82%**; `--local` on the mapped set gave **0.9462**.

## Justify the tolerance by the shared problem, not by the noise

**The symptom:** you pick a tolerance that makes the numbers pass (a band 3% above what you observed), and it later fails on noise — or you demand more precision than the problem defines, and it fails for a reason unrelated to correctness.

**Why:** the right tolerance is set by the *precision the shared problem is defined to*, not by how closely the two runs happened to land. State *why* the number is what it is, and the check can't be a fudge.

**Do this — two worked tolerances, both justified, both different:**

- **RAxML-NG vs IQ-TREE agree to `1e-8`** on the log-likelihood — because the ML optimum *is* defined to that precision, so a tighter band is meaningful and a looser one throws away signal.
- **PySCF vs Psi4 (H₂, RHF/STO-3G), both with exact integrals, agree to `3e-7` Ha** — but only after matching the method. Psi4 *defaults* to density fitting (DF); comparing that DF energy against PySCF's exact one made two correct codes look `2.4e-5` Ha apart, and the recipe first mis-blamed "unstandardized STO-3G contraction coefficients." It's DF, not the basis (measured DF−PK = `2.401e-5`) — exactly the *match the modes* failure below, one env over. Set `SCF_TYPE PK` so both run exact integrals, and the check asserts `< 1e-5` (tight enough to be a real cross-validation, loose enough to survive SCF-convergence noise). The integral treatment sets the tolerance, not the basis.

## Pin threads and a seed before an exact identity means anything

**The symptom:** a cross-check on an exact value passes one run and fails the next, with no code change — a correct tool, a valid result, a flaky check.

**Why:** many correct tools don't produce the *same* output run to run. Thread count changes the order of updates and therefore which local optimum, tree, or assembly the search lands on. Each result is valid; an exact assertion on it is flaky *unless the search is pinned*.

**Do this — pin the thread count and the seed, verify byte-identical across two runs, then assert the exact number** (otherwise assert a band, or report it as an observation). IQ-TREE's ML search lands on a different tree at a different thread count, so it fixes `-T <n>` (not `AUTO`) and `-seed`; the assemblers (SPAdes, Flye, MEGAHIT) fix `-t`; RAxML-NG fixes `--threads` and `--seed`, because the thread count even feeds the difficulty prediction that decides how many starting trees it generates. Pin first, then the exact identity is real.

---

Three disciplines, one rule underneath: **assert the claim you mean.** A recipe links here for the *why*; it keeps its own specific check — kallisto's rank correlation, Psi4's chemical-accuracy band, IQ-TREE's pinned seed — inline, where the number lives.
