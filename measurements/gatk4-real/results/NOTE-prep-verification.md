`prep-check.txt` in this directory is from the run that BUILT the BAM, and it shows
`sort_order GO:none` — a bug in the check, not in the data. The `@HD` line is
`@HD VN:1.5 GO:none SO:coordinate`, so `SO:` is field 4, and the assertion read field 3.
That mis-parse failed the task, which is why `prep-check.txt` records a `sort_order` that
looks wrong. `00-prep-bam.task.json` now matches `SO:coordinate` by name instead of position.

Two things worth keeping from that failure:

1. **spawn stages out declared outputs even when the command exits non-zero.** The failed run
   published a 923 MB BAM to the inputs prefix with no index and no passing check. So a
   task-internal smoke check cannot, by itself, prevent a bad artifact from being published —
   the bucket-check half of the rule is what catches it. Build to a temp name and rename after
   the check if that matters to you.

2. **The artifact was verified independently rather than rebuilt**, because a 14-minute rebuild
   proves less than reading the object that will actually be consumed:

```console
$ U=$(aws s3 presign s3://$BUCKET/inputs/gatk4/NA12878.chr20.30x.bam)
$ samtools view -H "$U" | grep '^@HD'
@HD	VN:1.5	GO:none	SO:coordinate
$ samtools view -H "$U" | grep -c '^@SQ'
1
$ samtools view -H "$U" | grep -c '^@RG'
12
$ samtools view -H "$U" | grep '^@SQ'
@SQ	SN:chr20	LN:64444167	M5:b18e6c531b0bd70e949a7fc20859cb01	UR:.../GRCh38_full_analysis_set_plus_decoy_hla.fa
$ samtools view -H "$U" | grep -o 'SM:[^[:space:]]*' | sort -u | head -1
SM:NA12878
```

`SO:coordinate`, exactly one `@SQ` (chr20, M5 matching the reference the CRAM was compressed
against), 12 read groups, all `SM:NA12878`. The other measured values in `prep-check.txt` —
17,705,654 records, mean depth 36.3307×, 99.0484% covered, 966,504,303 bytes, sha256
`0ad228c1…` — are from that same run and are unaffected by the field-index bug.
