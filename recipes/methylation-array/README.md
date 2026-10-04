---
tool: minfi
tool_version: "1.56.0"
env: aarchbio
image: quay.io/aarchbio/bioconductor-minfidata@sha256:afec63a77f9e2662e44104f1d5f17aded9ad6aa9e6ff0f07b494d9e972bb300b
spawn_version: 0.116.0
last_verified: 2026-10-03
---
# minfi — Illumina 450k arrays, checked against two truths the array itself carries

Reads six real 450k IDATs, recovers each donor's sex from X/Y intensity and each sample's donor from the array's 65 identity probes. For anyone doing array methylation on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=methylation-array)" --wait   # 20 s, nothing staged
```

```r
library(minfi); library(minfiData); data(RGsetEx)       # 6 samples, 485,512 probes
getSex(mapToGenome(ratioConvert(preprocessRaw(RGsetEx))))$predictedSex   # "M" "F" "M" "F" "F" "F"
getSnpBeta(RGsetEx)                                      # 65 probes → which donor is which
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `minfiData`'s 6 IDATs | `read.metharray.exp("your/idat/dir")` | your IDATs need a matching manifest package — 450k here; EPIC/EPICv2 are separate packages, and the probe count assertion changes with them. |
| `preprocessRaw` | `preprocessNoob`, `preprocessFunnorm`, SWAN | normalisation moves beta values but not the two truths below, which is what makes them good checks. |
| the sex and donor assertions | your own sample sheet | **this is the part worth copying** — if your sheet records sex or repeated donors, you have the same free checks on your own data. |

**Leave the fixture.** Six samples is small, but it is *three donors with two samples each* — and that pairing is what turns the identity check into a combinatorial claim rather than a clustering opinion. A bigger cohort adds rows, not truth.

## Which box

`c8g.large` (2 vCPU / 4 GiB) — verified to run under a **2 GiB** cgroup limit, so RAM isn't the constraint at this size. Measured: Docker install 39 s, the R image pull **81 s**, the analysis **20 s**. Provisioning is 86% of the 140 s window, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

Nothing is staged — `minfiData` ships the IDATs (93 MB), the 450k manifest and the ilmn12.hg19 annotation inside the image, so there is no `stage-inputs.sh` and no input to pin separately.

<details>
<summary>As shipped: two truths the array carries, and why SeSAMe isn't here yet</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| samples | exactly 6 | 6 |
| array / annotation | `IlluminaHumanMethylation450k` / `ilmn12.hg19` | matches |
| donor pairing | 3 donors × 2 samples | id1=2 id2=2 id3=2 |
| probes | exactly 485,512 | 485,512 |
| beta range | within [0, 1] by construction | [0.000000, 1.000000] over 2,913,072 values |
| **predicted sex** | **== the shipped annotation, all 6** | **MFMFFF vs MFMFFF, 6/6** |
| SNP probes | exactly 65 | 65 |
| **nearest neighbour by SNP** | **== the same donor, all 6** | **6/6** |

**Both strong checks are truths the array was built to carry, not bands on observed values.**

- **Sex from intensity.** The 450k array has X and Y probes, so total intensity on each chromosome predicts sex — and `minfiData` ships a sample sheet that *says* the sex. Predicting it back is a constructed-truth check with no tolerance: a categorical answer, right or wrong, 6 times.
- **Donor from the 65 SNP probes.** Illumina puts them on the array precisely so samples can be matched to donors, and this dataset is three people who each gave a normal and a cancer sample. So every sample's nearest neighbour in SNP space must be its own donor's other sample — **3 correct pairs out of 15 possible**, which is a combinatorial claim rather than a similarity threshold. Observed separation: median within-donor distance **0.5951** against **3.2501** between donors, a 5.5× gap, but the assertion is the ranking and not the gap.

Normalisation choice doesn't touch either one, which is what makes them worth asserting: swap `preprocessRaw` for `noob` or `funnorm` and the beta values move while 6/6 and 6/6 do not.

`485512` is the 450k array's probe count after mapping to the genome, and `65` the identity-probe count — both exact properties of the platform, so they catch a wrong manifest package, which is the failure mode most likely to produce plausible-looking garbage.

### Pins (data tier: in-image)

| | |
|---|---|
| image | `quay.io/aarchbio/bioconductor-minfidata@sha256:afec63a77f9e…` — **arm64 per-arch digest**, not the manifest list |
| contents | minfi 1.56.0, minfiData 0.56.0, `IlluminaHumanMethylation450kmanifest` 0.4.0, annotation ilmn12.hg19 |

The tag is multi-arch (`linux/amd64` + `linux/arm64`), and a manifest list is not evidence of arm64 — so the pin is the arm64 entry's digest, read by walking the list's entries.

One image covers the whole stack deliberately: `bioconductor-minfidata` depends on minfi, the manifest *and* the annotation, so requesting it ([aarchbio#64](https://github.com/playgroundlogic/aarchbio/issues/64)) closed a four-package set that is only useful together — the same reason `recipes/rnaseq-de` runs three methods from two digests.

### Why there is no SeSAMe cross-check yet

The plan was two independent routes from the same IDATs — minfi and SeSAMe have genuinely different preprocessing — compared by rank, since different normalisation makes raw beta values incomparable ([the rule](../../practices/cross-checks.md)). The image was requested and built ([aarchbio#65](https://github.com/playgroundlogic/aarchbio/issues/65)), and `sesameData` 1.28.0 even comes along in its dependency closure.

**But SeSAMe cannot run offline.** Measured by running it with the network disabled, which is the only way to find this out rather than being told by a green run on a machine that has internet:

```
openSesame FAILED:
| File idatSignature either not found or needs to be cached to be used in sesame.
| > sesameDataCache("idatSignature")
```

Its manifests come from ExperimentHub at first use, and a recipe here may not fetch data at run time. The fix is the documented one — cache it once at staging time and ship the pinned cache
([reference-from-tests](../../practices/reference-from-tests.md) covers the same shape for SIESTA's
pseudopotentials) — so this is a staging job, not a blocker. Until then the recipe stands on the two
constructed truths above, which are stronger than a rank correlation anyway.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=methylation-array)" --wait
make ls RECIPE=methylation-array
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with
`predicted_sex_matches 6/6` and `snp_identifies_donor 6/6`.

</details>
