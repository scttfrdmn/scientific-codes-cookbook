---
tool: beagle-minimac4
tool_version: "Beagle 5.5 (27Feb25.75f) / Minimac4 4.1.6"
images:
  - quay.io/aarchbio/minimac4@sha256:cea08321a8ab3632633b2ba0f2b68c98d7e6fcfb1c9c624f9b790a61a0e4b3c2
  - quay.io/aarchbio/beagle@sha256:7a22203b34a80d9b832ea84c3c46d9fdf83f4f679b6e53fcf1baebeeba06ce63
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Genotype imputation — two tools, and truth we deleted ourselves

Beagle 5.5 and Minimac4 4.1.6 impute the same masked genotypes from the same reference panel on Graviton4, scored against the genotypes the fixture hid from them. For anyone imputing a cohort who wants to see what the two standard tools do and do not agree about.

> **What this covers.** 400 SNPs, a 150-sample phased reference panel, 20 target samples, 1600 genotypes masked. Haplotypes are mosaics of 8 founders, so the panel carries real LD. Not a real reference panel, population structure, or a chromosome-scale run.

## Run it

```bash
# Minimac4 wants a compressed reference and an indexed target
minimac4 --compress-reference ref.vcf -o ref.msav
bgzip -c target.vcf > target.vcf.gz && tabix -p vcf target.vcf.gz
minimac4 ref.msav target.vcf.gz -o mm4.vcf.gz -f GT

# Beagle takes plain VCFs
beagle gt=target.vcf ref=ref.vcf out=imputed
```

Two tasks: Minimac4 builds the fixture and imputes, then Beagle imputes the same bytes and both are scored against the hidden truth.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic panel, 8 founders, seed 42 | a real reference panel (1000G, TOPMed, HRC) | the mosaic construction exists to create **LD** — without it imputation has no information and any check would be vacuous. |
| 20% of genotypes masked | your real missingness | masking is what gives this recipe an answer key; on real data you can only compare the two tools to each other. |
| 400 SNPs on one contig | your chromosome | both tools scale to chromosome-size panels, and Minimac4's `.msav` compression (≈5× here) is the reason its reference format exists. |

**Leave the fixture:** deleting genotypes you already know is the only way to measure imputation accuracy directly. **Scale it** to a real panel — and read the observed-genotype finding below first, because it changes which tool you want.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m each, caps $0.05 each. Recorded windows **~1m** (fixture + Minimac4) and **1m16s** (Beagle + scoring). **These timings are not compute cost.**

<details>
<summary>As shipped: what is exact vs banded, the observed-genotype difference, the shared-error finding, pins</summary>

### The checks

| observable | assertion | Beagle | Minimac4 |
|---|---|---|---|
| masked genotypes filled | all 1600 — no holes left | **1600** | **1600** |
| concordance with hidden truth | ≥ 0.95 | **0.9900** | **0.9875** |
| agreement with each other | ≥ 0.98 | **0.9912** | |
| observed genotypes changed | Beagle: **exactly 0** | **0** | 20 (see below) |

**Why concordance is a band and filling is exact.** Whether a masked genotype is *recoverable* depends on the LD around it — a property of the data, so a threshold is the honest form. Whether a tool leaves a hole is not: an imputation tool that returns `./.` where you asked for a genotype has failed at its job, and that needs no tolerance. Note the concordance is deliberately **not** 1.0 — if it were, the fixture would be leaking the answer rather than testing recovery.

### The finding worth acting on: the tools disagree about your own data

**Minimac4 changed 20 of 6400 genotypes that were never masked. Beagle changed none.**

That is not a bug — Minimac4 re-estimates typed markers against the reference panel, so it can overwrite a call you supplied; Beagle treats observed genotypes as fixed. It is reported as an *observation*, not a failure, and asserted only to be small (<1%). But it is operationally important: **if your pipeline assumes your own genotype calls survive imputation, those two tools behave differently**, and this recipe is the cheapest place to see it.

### The shared-error finding

Of the genotypes each tool got wrong, **11 were wrong in both**. That is the majority of each tool's errors, and it is the useful kind of agreement: the residual misses are concentrated where the panel genuinely cannot resolve the genotype, rather than scattered by implementation differences. Two tools failing on the *same* sites is evidence the limit is the data — the same logic that makes [cross-code agreement](../../practices/cross-checks.md) stronger than any single-tool band.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| Minimac4 | `quay.io/aarchbio/minimac4@sha256:cea08321…` (4.1.6; also supplies bgzip/tabix/bcftools) |
| Beagle | `quay.io/aarchbio/beagle@sha256:7a22203b…` (5.5, 27Feb25.75f) |
| input | none — panel, targets, mask and truth are generated in-task by awk from `srand(42)` |

**Format note:** Minimac4 needs both a `.msav` reference (`--compress-reference`) *and* a **bgzipped, tabix-indexed** target, and fails with `Target file must be indexed` otherwise. Beagle takes plain VCFs for both. That asymmetry is why the fixture is built in the Minimac4 image — it is the one that ships `bgzip`/`tabix`.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### Run + verify

```sh
make run RECIPE=imputation
make ls  RECIPE=imputation
```

Assertions are `test`/`awk` exit checks inside the second task, so a failure fails the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with both `*_filled 1600 of 1600` and `beagle_changed 0`.

</details>
