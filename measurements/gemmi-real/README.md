# gemmi on the whole PDB archive: 246,905 entries, three chips

> **AMD finishes first. Graviton4 is cheapest per result, and Intel is slowest and dearest.** All
> three read every mmCIF, PDB-format and structure-factor file in the wwPDB archive. They agree byte
> for byte on everything gemmi reads. The one difference is the last 1–5 ulps of a computed
> resolution, on 7% of entries. The run also found 10 defects in the archive's own files, and gemmi is
> not the cause of any of them.

Workload: **the complete wwPDB archive as of the RCSB's 2026-01-01 snapshot**, on RODA
(`s3://pdbsnapshots/20260101/`). That is all 246,905 mmCIF files, which is the snapshot's
published count, plus the 234,600 PDB-format files wwPDB writes for entries that fit the format,
and all 190,671 structure-factor files. Together they are 440.3 GB gzipped: 2,878,368,146 atom
sites and 15,791,827,832 reflections in 236,570 data blocks. A whole-archive pass is how gemmi is
used at scale: `gemmi grep` and its Python API exist for surveys like this one, and its docs time
them against the full mmCIF tree.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/gemmi-real/stage-inputs.sh "$B"     # rebuilds the manifest from the bucket listing; must match its pin
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py gemmi $a "$B" "$(date +%s)" measurements/gemmi-real/real.task.json)"
done
python3 -I measurements/gemmi-real/compare.py arm64/entries.tsv.gz x86_64/entries.tsv.gz amd/entries.tsv.gz
```

Nothing is copied into your bucket. The task streams each object over HTTPS from the public
bucket. It checks the size and S3 ETag against the manifest: MD5 for single-part objects, and
MD5-of-8-MiB-part-MD5s for the 4,394 multipart ones. Only then does gemmi read the object (`real.py`).
The x86 runs use the recipe's `md` env twin, which has the same gemmi 0.7.5.

## Results

`c8g/c8i/c8a.8xlarge` (32 vCPU, 64 GiB). 64 worker processes, plus 6 that take the 91 entries holding an
object over 50 MB. Same manifest, same bytes.

| chip | instance | cores × threads | tool wall | billed | peak RSS (worker / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.8xlarge` | 32 × 1 | 1497 s | 1667 s | 5.4 / 41.3 GiB | 1.2762 | **0.531** | **0.591** |
| Xeon 6975P-C | `c8i.8xlarge` | 16 × 2 | 1687 s | 1787 s | 5.4 / 44.8 GiB | 1.4994 | 0.703 | 0.744 |
| **EPYC 9R45** | `c8a.8xlarge` | 32 × 1 | **1395 s** | **1483 s** | 5.4 / 44.2 GiB | 1.7243 | 0.668 | 0.710 |

A *result* is the whole archive read and checked. *Tool wall* is `real.py`, from launch to exit.
*Billed* is EC2 launch to termination. *$/hr* is spawn's cost estimate divided by the task window.
*Peak RSS* is the largest single worker's `ru_maxrss` and the cgroup's `memory.peak`.

- **Graviton4 is 17% cheaper per result than AMD and 21% cheaper than Intel**, billed. AMD is
  1.07× faster than Graviton4 and Intel 0.89× as fast, but neither margin covers its higher hourly price.
- **This is a streaming job, not a compute job.** The runs average 15.5, 19.4 and 13.7 busy CPUs
  of 32: 23,129, 32,705 and 19,162 CPU-seconds, at an aggregate 260–315 MB/s from S3. The workers
  spend much of their time waiting on bytes, so the box is half idle on every chip. Intel's
  CPU-seconds are SMT threads, two to a core. More workers might raise throughput, but that
  setting was not run. [Data movement](../../patterns/data-movement.md) is the dial here, not cores.
- **The wall is set by the bulk, not the stragglers.** The largest entry, the 3.2 GB PanDDA group
  deposition 5s74, takes 170–305 s in its own pool. Both pools finish within a second of each other.
- **Peak memory is the 6 big-entry workers plus 64 ordinary ones.** Putting the big entries first
  in a single 64-wide pool peaked at 54.8 GiB on the 64 GB box in a probe, so they run in a separate,
  narrower pool.
- **n = 1 per chip.** Everything gemmi reads came out byte-identical on all three chips (below),
  so a repeat would measure only network noise. These timings are not compute cost in general
  ([layout](../../patterns/layout-and-effective-cost.md)).

## What held at scale, on all three chips

| claim | why it must hold | result (every chip) |
|---|---|---|
| 246,905 entries | the snapshot README's published count | 246,905 |
| all 672,176 objects match size and ETag | provenance | all; one S3 500 on Graviton4, retried |
| every mmCIF parses, one block, named for its entry | completeness | 246,905 / 246,905 |
| gemmi's atom count == `_atom_site` rows, its models == distinct model numbers | the reader drops nothing | every entry; 2,878,368,146 atoms |
| **every PDB-format file == its mmCIF**, read by gemmi's two parsers and written back by one writer | two serialisations of one entry | 234,574 identical, 23 identical once reordered, 3 archive defects |
| every space group used in the archive is closed under composition | a group axiom | 92 groups, 35,622 products, 0 outside |
| gemmi's number for each recorded H-M symbol == `_symmetry.Int_Tables_number` | a published table | 231,332 of 231,334; the other 2 are non-standard symbols |
| `_entity.pdbx_number_of_molecules` == the entity's asym units (water aside) | the archive's own bookkeeping | all but 1 archive defect |
| every structure-factor file parses into reflection blocks | completeness | 190,667 of 190,671; the other 4 are malformed files |

**The PDB-format comparison asserts a format bound, not a band.** Where a value has more digits
in the mmCIF than its PDB column holds, two correct roundings of the same decimal can sit one unit
apart in the last place, and never two. Those lines are counted by field: 11,913,277 ANISOU lines,
20,266 B-factors, 286 cell lengths and angles, and 462 MTRIX lines; no coordinate and no occupancy.
There are also "-0" against "0" cases, which gemmi writes for values that round to zero from
below: 59,627 ANISOU lines, 24 coordinates and 1,070 MTRIX lines. Then come 14,379 CRYST1 Z values absent from the
mmCIF, and 1,801 residue numbers over 9999 in 9 entries. gemmi writes those in hybrid-36 (`A001` is
10001), and the archive's PDB file holds them modulo 10000 (`0001`). Every other byte must match,
and does.

**Observed, not asserted.** These are depositor-supplied values, so agreement measures the
archive, not gemmi:

- The SF file's cell equals the model's exactly in 145,825 of 190,671 entries, and to 1e-3 in 160,173.
- The resolution from the SF file's own Miller indices and cell is within 0.01 Å of `_refine.ls_d_res_high` in 166,998 of 190,478 entries.
- The free-set count matches `_refine.ls_number_reflns_R_free` in 96,913 of 189,037.
- gemmi's sequence weight matches `_entity.formula_weight` to 1e-6 at the median entry, and to 1e-3 in 221,739 of 246,879.

The R-factors are recorded per entry but not recomputed, since that needs a bulk-solvent model.
The structure-factor side is cross-checked in cctbx-real (below).

## The archive's own defects

Each of these was found by the whole-archive run and confirmed against the raw bytes. `real.py`
asserts that the set of entries breaking each claim is exactly this set, so a new defect fails the
run, and so does one of these disappearing.

| entry | file | defect |
|---|---|---|
| 7r3k | PDB-format | HOH K 6004 has y = −1076.96, which `%8.3f` cannot hold; the PDB file prints `1076.960`, sign lost |
| 4xkt | mmCIF | U23 of S SO4 J 201 is recorded as −1307 Å² (a dropped `0.`); no 7-column ANISOU field holds it, and gemmi and the PDB file truncate it differently |
| 2cbi | mmCIF | O LYS B 476 has U11+U22+U33 = 0 (U11 −0.0138, not positive definite). gemmi writes ANISOU only for a nonzero trace, and the PDB file's integers round to a trace of ~1e-9, so one copy has the record and the other does not |
| 1e42 | structure factors | a reflection row `1 1 1 ? ? ?`: Miller indices of `?` |
| 2w4q, 3zqo | structure factors | the text `END OF REFLECTIONS` inside the reflection loop |
| 2yok | structure factors | `_audit.update_record` twice in one block, which CIF forbids; gemmi refuses the file |
| 1ldm, 3ldh | mmCIF | space group `C 4 21 2`, a C-centred setting of P 4 21 2 (no. 90) that is not a standard symbol |
| 2icy | mmCIF | entity 4 (UMP) records 1 molecule but has no asym unit and no atoms |

## Across chips

- **Intel against AMD: byte-identical**, all 246,905 rows of `entries.tsv` (sha256 `44ff58bc…`).
- **Either x86 chip against Graviton4: identical in every column but one.** All counts, cells,
  space groups, recorded values, PDB comparisons, the polymer weights and the defect list match.
  The exception is the resolution computed from Miller indices and the cell, `sf_dmin0`. It differs
  in 17,787 of 190,478 entries by 1–5 ulps (13,383 by one ulp), at most 6.3e-16 relative. It is a
  floating-point evaluation of `1/sqrt(...)`, so it is a rounding path, not a parsed value, and it
  is reported, not asserted. Graviton4's `entries.tsv` sha256 is `8d7fe367…`.
- gemmi links no BLAS, so the OpenBLAS kernel rule does not apply.

## cctbx-real, on the same bytes

cctbx-real used the same snapshot. Its cohort is the 181 structure-factor entries of hash
directory `ab`, copied to `inputs/cctbx-real/pdb-20260101/ab/` and sha256-pinned in its
`cohort.sha256`. Every one of those objects is in this run's manifest and was ETag-checked here,
so the two measurements read identical bytes. cctbx-real reports that its F_calc agrees with
gemmi's direct summation to 1.5e-7 relative (IT92 form factors; gemmi's Python binding computes
in float). That number is cctbx-real's measurement, not this run's.

## Pins

| | |
|---|---|
| archive | RODA `s3://pdbsnapshots/20260101/pub/pdb/data/structures/divided/{mmCIF,pdb,structure_factors}/`, 672,176 objects. `manifest.tsv.gz` lists each one's key, size and ETag, with sha256 `6b8ecb6f…` in `pins.sha256`. `stage-inputs.sh` rebuilds it from three `list-objects-v2` calls and refuses a mismatch. Two hidden editor files in the SF tree (`.r4bwksf.ent.gz.swp`, `.r2ji8sf.ent.gz.swp`) are skipped and named. Data tier: RODA |
| scripts | `real.py` and `real.sh` are in `pins.sha256`, checked in-task |
| image | the recipe's `md@sha256:28537712…` (arm64) and its `md-f7ed8789d24e` x86 twin, both gemmi 0.7.5 |

Per-chip summaries, exception lists, space-group tables and logs are in `results/`. The per-entry
tables (15 MB gzipped each) and per-entry timings stay in the bucket under `runs*/gemmi-real/r1/`.
`probe.task.json` is the 1-in-20 sample, plus every big entry, that sized the box.

## Spend

Six launches, **$2.78** by spawn's estimates. The three verifying runs cost $1.99. The rest was two
probes and a first full Graviton4 run. That run found the archive defects listed above, plus two
transient S3 errors (a 500 and a 503) that `real.py` now retries.
