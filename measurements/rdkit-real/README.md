# RDKit on the whole ChEMBL 37 release — Graviton4, Intel and AMD

> **RDKit regenerates ChEMBL's published connectivity for 2,897,783 of 2,897,804 compounds. Every
> element-count disagreement in the release is one ChEMBL's own record explains. The 2.9M-row
> output is byte-identical on all three chips.** AMD EPYC runs it 1.32× faster than Graviton4 at the
> same price per result. Intel is 1.40× slower and 54% dearer billed.

Workload: **every compound** in ChEMBL 37's `chembl_37_chemreps.txt.gz`, all 2,897,819 rows. Each
compound gets an InChI, InChIKey, formula and canonical SMILES from its SMILES, and RDKit also parses
ChEMBL's own standard InChI back. This is a whole-library registration or standardisation pass,
which is how RDKit meets a compound collection in practice. The recipe's 100,000-row slice is the
first 3.5% of this file.

**Data tier: a durable public id.** EBI's `releases/chembl_37/` directory is immutable, and EBI
publishes the file's sha256 in that directory's `checksums.txt`. `00-fetch.task.json` downloads it
on AWS and checks it against that sha256 (`ea6181ce8dc7af41…`, 292,540,141 bytes) before keeping it.
[`pins.sha256`](pins.sha256) pins it again at every stage-in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/rdkit-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if the release is not staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py rdkit $a "$B" "$(date +%s)" \
    measurements/rdkit-real/real.task.json)" --wait
done
bash measurements/rdkit-real/collect.sh "$B"            # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) is the workload and its checks. It sends fixed 10,000-row chunks to an
8-process fork pool and writes them back in input order, so the output does not depend on
scheduling. Per compound:

```python
mol = Chem.MolFromSmiles(smi); ii = inchi.MolToInchi(mol); key = inchi.InchiToInchiKey(ii)
form = CalcMolFormula(mol); can = Chem.MolToSmiles(mol); m2 = inchi.MolFromInchi(std_inchi)
```

## Results

8 workers on `c8g.2xlarge`, `c8i.2xlarge` and `c8a.2xlarge` (8 vCPU, 16 GiB). Graviton4 runs the
recipe's image and the two x86 chips run the `comp-chem` env's linux-64 twin. All three read the same
staged bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 483 s | 571 s | 0.46 GiB | 0.3190 | 0.0428 | 0.0506 |
| Xeon 6975P-C | `c8i.2xlarge` | 678 s | 750 s | 0.46 GiB | 0.3748 | 0.0706 | 0.0781 |
| **EPYC 9R45** | `c8a.2xlarge` | **367 s** | **423 s** | 0.50 GiB | 0.4311 | 0.0439 | 0.0507 |

A result is the whole verified release: 2.9M compounds and every check below. "Billed" is spawn's
completion window, from wrapper start to stage-out end. $/hr is spawn's `cost_estimate` over that
window.

- **Throughput is 6,000 compounds/s on Graviton4, 4,274 on Intel and 7,896 on AMD.** All three
  kept 7.9 of 8 vCPU busy. The work is integer and graph code, so the chip's per-core speed decides it.
- **AMD and Graviton4 cost the same per result**, 0.0506 against 0.0507 billed. AMD is 1.32×
  faster at a 35% dearer hour. Pick AMD for wall time and either chip for cost.
- **Intel's 8 vCPU are 4 cores × 2 threads.** Graviton4's and AMD's are 8 full cores, so Intel
  ran 8 workers on half the cores. It is 1.40× slower than Graviton4 and 54% dearer per result
  billed.
- **The box is sized by cores, not memory.** Peak RSS was 0.5 GiB for 8 workers. A `c8g.xlarge`
  would hold the data but halve the workers, which was not run. The release is 280 MB staged in
  tmpfs.
- **Overhead is 56–88 s per bill.** Graviton4 pulls the aarch.science image. The x86 twin stages
  544 MB of packages and installs them offline.
- n = 1 per chip. The run is deterministic: two Graviton4 runs wrote the same output sha256.

## What held at scale, on all three chips

[`real.py`](real.py) asserts every row inside the task. A failure fails the run.

| claim | why it must hold | all three chips |
|---|---|---|
| 2,897,819 rows, 4 columns each, unique `chembl_id`, one row out per row in | the release | yes |
| every key written is a well-formed 27-character InChIKey | InChI's format | yes |
| **element counts agree, or the record's SMILES and InChI describe different molecules** | **the recipe's exact identity** | **2,847,270 + 5 = 2,847,275** |
| canonical SMILES is idempotent, every parsed compound | canonicalisation | 2,897,804 / 2,897,804 |
| first 100,000 rows reproduce the recipe's counts: 99,997 / 99,914 / 99,996 / 97,665 | the recipe's published values | yes |

**The five exceptions are one ChEMBL record shape, not an RDKit disagreement.** All five are
PROTACs from one series (CHEMBL4850252, …4857695, …4858474, …4862608, …4869947). In each, RDKit's
formula from the SMILES differs from the InChI's formula layer by exactly +CH₂ −O. RDKit's
formula from parsing ChEMBL's *own InChI* equals that layer, and so does the key it regenerates from
it. So RDKit reads both representations correctly, and the record's two representations differ by
one linker atom. [`results/arm64/atoms_mismatch.tsv`](results/arm64/atoms_mismatch.tsv) lists them.
The assertion requires each exception to meet this test, so a real RDKit error still fails the run.

Reported, not asserted (identical on all three chips):

| observable | count | of |
|---|---|---|
| SMILES parsed | 2,897,804 | 2,897,819 |
| full InChIKey == ChEMBL's | 2,894,324 (99.880%) | parsed |
| connectivity block == ChEMBL's | 2,897,783 (99.9993%) | parsed |
| of the 21 connectivity misses, ChEMBL's own InChI regenerates its key | 5 (the PROTACs) | 21 |
| ChEMBL's InChI parsed by RDKit / written back unchanged | 2,897,574 / 2,894,300 | 2,897,819 |
| RDKit's canonical SMILES == ChEMBL's `canonical_smiles` | 2,895,158 (99.909%) | parsed |
| `InchiToInchiKey(ChEMBL's InChI)` == ChEMBL's key | 2,897,809 | 2,897,819 |

Of the other 16 connectivity misses, 8 differ in the protonation flag (RDKit `-N`, ChEMBL `-M`,
`-O` or `-J`) and 8 in the skeleton itself. [`results/arm64/skeleton_mismatch.tsv`](results/arm64/skeleton_mismatch.tsv)
lists them. The last row means 10 records carry a key that is not the hash of their own InChI.

## Across chips

**Nothing here is floating point, so there is no fixed point versus path to sort.** The 2.9M-row
`rdkit_real.tsv` has the same sha256 on all three chips (`9a84f0adae6bc1dd…`, uncompressed), and so
do both mismatch lists and every count in `metrics.txt`. That includes Intel and AMD against
Graviton4, and an aarch.science arm64 build against a linux-64 twin of the same versions.
[`results/compare.txt`](results/compare.txt) holds the comparison. OpenBLAS is not on this path, so
no kernel name is recorded.

## Caveats

- One release, one invocation shape. Fingerprints, substructure search, conformer embedding and
  descriptor tables were not run. Conformer embedding is the floating-point path where chips could
  differ.
- The 8-worker pool is a choice. A worker-count sweep was not run.
