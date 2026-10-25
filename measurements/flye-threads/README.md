# Flye's thread count: speed and the assembly both move

> **On the same reads, `-t 1/2/4/8` gave 10, 12, 11 and 14 contigs.** The count wanders and is not
> monotonic. Wall falls 1733 → 955 → 537 → 360 s, which is 3.23× at 4 threads and 4.81× at 8, so
> the second four threads buy 1.49×.

Flye 2.9.6-b1802 on E. coli ONT reads DRR242223 (254,841,156 bases, read N50 9,487, `-g 4.6m`), one
run per thread count, 2026-09-09, us-east-1. This is the data behind flye's shape on
[How many cores should I ask for?](../../patterns/sizing.md).

| `-t` | wall | speedup | contigs | total length | largest | file |
|---|---|---|---|---|---|---|
| 1 | 1733 s | 1.00× | 10 | 5,478,608 | 2,384,169 | `t1/` |
| 2 | 955 s | 1.81× | 12 | 5,470,958 | 2,004,519 | `t2/` |
| 4 | 537 s | 3.23× | 11 | 5,400,307 | 2,004,513 | `t4/` |
| 8 | 360 s | 4.81× | 14 | 5,502,781 | 2,004,517 | `t8/` |

Each `timing.txt` holds the wall and the counts, and each `flye.log` agrees with it: its
`Fragments`, `Total length` and `Largest frg` lines, and the span from `Starting Flye` to
`Final assembly`.

This is one run per rung, so it shows that the answer moves with `-t`. It does not show how far
it moves at a fixed `-t`. The [flye recipe](../../recipes/flye/README.md) settles that question
separately: at a fixed `-t 8` on a different read set, seven runs on four chips gave the same
assembly.

**Not kept:** the spec, the image digest (the recipe's pin then was
`quay.io/aarchbio/flye@sha256:d87ccd4e29f2…`), the read-mode flag and the instance type. The
session record says `m8g.2xlarge`, and no $ is derived here for that reason. Artifacts copied
verbatim from `s3://scicookbook-942542972736-us-east-1/runs/flye-sweep/`. The input is
`inputs/flye-ecoli-drr242223/DRR242223.fastq.gz` in the same bucket (237,399,109 bytes), not
re-hashed. The logs do not name the file. The identification rests on the total read length, which
matches the bucket's DRR242223 run (`runs/flye-drr242223/r2/flye.log`) to the base.
