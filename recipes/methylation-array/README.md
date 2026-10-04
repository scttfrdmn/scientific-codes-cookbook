---
tool: minfi-sesame
tool_version: "minfi 1.56.0 / SeSAMe 1.28.1"
env: aarchbio
images:
  minfi: quay.io/aarchbio/bioconductor-minfidata@sha256:afec63a77f9e2662e44104f1d5f17aded9ad6aa9e6ff0f07b494d9e972bb300b
  sesame: quay.io/aarchbio/bioconductor-sesame@sha256:624254ed82a905cef9814b27a1e4ec417072d8bd002899498c080301dca3295b
spawn_version: 0.116.0
last_verified: 2026-10-04
---
# minfi + SeSAMe — Illumina 450k arrays, checked three ways on the same IDATs

Recovers each donor's sex from X/Y intensity, each sample's donor from the array's 65 identity probes, and then has a second, independent pipeline agree about which sample is which. For anyone doing array methylation on ARM.

## Run it

```bash
make stage RECIPE=methylation-array                                      # once: SeSAMe's 19 MB cache
for s in $(make -s spec RECIPE=methylation-array); do
  spawn task run --spec "$s" --wait                                      # minfi 20 s, then SeSAMe 53 s
done
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

Two tasks, one image each ([one tool per image](../../practices/container-path.md)): minfi on
`c8g.large` (verified under a **2 GiB** cgroup limit), then SeSAMe on `m8g.large` for the 124 MB
it stages in. Measured: minfi **20 s** of analysis in a 140 s window, SeSAMe **53 s** in 203 s —
provisioning is 74–86% of both, so **these timings are not compute cost**
([layout](../../patterns/layout-and-effective-cost.md)). Only SeSAMe's 19 MB cache is staged —
`minfiData` carries the IDATs, manifest and annotation in its image, and task 1 re-exports them
because SeSAMe's image has no minfiData, which is also what made the failed first attempt cheap.

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
| SeSAMe ran offline | betas produced with no network | 486,427 × 6, 2,246,800 non-NA |
| **minfi ↔ SeSAMe identity** | **each sample's best rank-match is itself, all 6** | **6/6** |
| identity margin | min diagonal − max off-diagonal > 0.05 | **0.11575** |

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

### The SeSAMe cross-check, and why the claim is a *matching*

Two independent routes from the same IDATs: minfi's `preprocessRaw`, and SeSAMe's
`openSesame` (pOOBAH masking + its own normalisation). They disagree about 148,000 probes
before you start — SeSAMe masks down to 337,853 of the 485,512 shared — so **raw beta
values are not comparable**, and a correlation threshold would be measuring the
normalisation difference ([the rule](../../practices/cross-checks.md)).

So the assertion is a **matching**: correlate all 6 × 6 sample pairs by Spearman rank, and
require each SeSAMe sample's best match to be *its own* minfi sample. Six correct of
thirty-six ordered pairs, with nothing to tune.

| probe set | matched | min diagonal | max off-diagonal | margin |
|---|---|---|---|---|
| all 337,853 both retain | 6/6 | 0.99076 | 0.98198 | **0.00877** |
| top 50,000 by variance | 6/6 | 0.98535 | 0.96093 | 0.02443 |
| top 20,000 | 6/6 | 0.98599 | 0.93955 | 0.04644 |
| **top 5,000 (asserted)** | **6/6** | **0.96830** | **0.85255** | **0.11575** |
| top 1,000 | 6/6 | 0.94085 | 0.70819 | 0.23266 |

**The restriction is there because the all-probes margin is 0.0088, and a check that
squeaks past by 0.009 is a flaky check wearing a strong one's clothes.** Methylation
profiles are similar across samples from the same tissue, so most probes carry no
between-sample signal and simply dilute the comparison. Ranking by variance in *minfi's*
matrix alone — never by the comparison's own outcome — and taking the top 5,000 widens the
margin 13× to 0.116. The ladder is reported in full because 6/6 holding at every size is
the stronger statement: the matching is not an artifact of where the cut was made.

### Staging SeSAMe offline, and what is actually pinned

`openSesame` fetches three resources from ExperimentHub at first use — `idatSignature`,
`HM450.address` and `KYCG.HM450.Mask.20220123` — and a recipe here may not fetch at run
time. The minimal set was found by iterating (cache, run, read which resource the error
names, repeat); `sesameDataCache()` with no argument downloads gigabytes for a **19 MB**
need. Same sourcing move as SIESTA's stripped pseudopotentials
([the practice](../../practices/reference-from-tests.md)).

**The pin is on content, not on the tar.** BiocFileCache gives each blob a random filename
prefix and the sqlite files carry timestamps, so neither the names nor the tar's sha256 are
reproducible — but the downloaded resources are immutable. Staging hashes every non-sqlite
blob and requires the *set* to match four pinned sha256s; the task re-checks the same set
after untarring, before SeSAMe runs. The box has no network path to ExperimentHub, so
`sesame_ran_offline` returning 486,427 betas is itself the proof the cache was complete.

### A tar that extracts fine and still fails the task

First attempt died with `rc=2` on `tar -xf /tmp/sesame-cache.tar -C /tmp`:

```
tar: .: Cannot utime: Operation not permitted
tar: .: Cannot change mode to rwxr-xr-t: Operation not permitted
tar: Exiting with failure status due to previous errors
```

Every file extracted correctly. The tar carries a `.` entry, so tar tried to restore the
mode and mtime of **`/tmp` itself** — sticky `1777`, owned by the *instance* user while the
container runs as the *image's* user. It is the same ownership trap as never `rm`-ing a
staged input ([container-path](../../practices/container-path.md)), arriving through `tar`
instead, and with the same shape: a non-zero exit for a reason unrelated to the work.
Extract into a directory the container creates itself, and pass `-m --no-same-owner
--no-same-permissions`.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=methylation-array)" --wait
make ls RECIPE=methylation-array
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with
`predicted_sex_matches 6/6` and `snp_identifies_donor 6/6`.

</details>
