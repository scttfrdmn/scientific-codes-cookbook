# Copy vs mount: the same alignment, two data paths

> **The interesting difference was not speed. It was that the two routes produced different
> science** — and the copy route was the wrong one, because copying made me enumerate files
> and I enumerated badly. Wall time was within 1%.

One `c8g.4xlarge`, one pinned bwa image, one workload (24.1M read pairs against GRCh38).
Only the data path differs: `aws s3 cp` to local disk, versus a lith FUSE mount reading the
**public 1000genomes bucket in place**.

## What happened

| | bytes moved | setup | align | total | **SAM records** |
|---|---|---|---|---|---|
| **copy** (`aws s3 cp`) | **8.84 GiB** | 38 s (233 MB/s) | 811 s | 849 s | 48,392,167 |
| **lith mount** | **3,368 bytes** | 1 s index + 0 s mount | 855 s | 856 s | **48,817,006** |

- **Wall time is a wash** — 856 s vs 849 s, 0.8% apart. Aligning *straight off S3* through a
  FUSE mount cost 5.4% on the aligner and saved all 38 s of staging.
- **Bytes moved differ by ~2.8 million fold.** lith moved a 3,368-byte index for 19 keys;
  the copy route moved 8.84 GiB. Nothing about the compute changed.
- **The record counts differ by 424,839 (0.88%).** That is the finding.

## Why the counts differ — and why it indicts the copy route

The mount exposes the prefix **as published**: 13 entries, including
`GRCh38_full_analysis_set_plus_decoy_hla.fa.alt`. My copy took 7 files — the five BWA index
files and two FASTQs — because copying forced me to decide what was needed, and I decided to
skip the 3.0 GiB `.fa`. In doing so I also dropped the **476 KB `.alt`**.

`bwa mem` reads `.alt` to do ALT-aware mapping. Without it the aligner logs
`read 0 ALT contigs` and emits fewer records; with it, ALT contigs are handled and the count
rises. So:

- copy route → **no ALT awareness**, 48,392,167 records
- mount route → **ALT-aware**, 48,817,006 records

**Both runs exited 0. Both record counts look plausible.** Nothing flagged the difference; it
surfaced only because the same workload ran both ways on the same box. A recipe that asserted
"48,392,167 records" — as [bwa-samtools](../../recipes/bwa-samtools/README.md) currently does —
would have locked in the less correct answer as its exact identity.

To be fair to copying: this was **my** error, not a law of physics. A careful person copies
`.alt` too. But that is the point worth taking:

> **Copying requires you to enumerate the dataset, and enumeration is a place to be wrong.
> Mounting the published prefix cannot have that failure mode** — you get what the depositors
> published, including the file you did not know mattered.

## The other saving: shape, not just time

The copy route has to put 8.84 GiB somewhere. On the `spawn task run` path that somewhere is
`/tmp`, which is **tmpfs at half of RAM**, so staging 8.84 GiB needs ≥18 GiB of RAM *on top of*
bwa's own 8.7 GiB — which is why [bwa-samtools](../../recipes/bwa-samtools/README.md) is sized
at 32 GiB. Reading through a mount needs neither: the bytes stream and the page cache is
elastic. The same job should fit a **16 GiB** box, and the copy route **cannot run there at
all** — 8.84 GiB will not fit in an 8 GiB tmpfs. That is a rate difference of 2× on the
instance, from the data path alone. *(Not yet measured — the next run.)*

This is the [effective-cost](../../patterns/cost-per-result.md) lens: the copy route makes you
rent memory whose only job is to hold a copy.

## Run it

```sh
export AWS_PROFILE=aws
aws s3 cp compare.sh "s3://$COOKBOOK_BUCKET/scripts/lith-compare.sh"
spawn launch lithcmp --region us-west-2 --instance-type c8g.4xlarge \
  --iam-managed-policies arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess \
  --command "aws s3 cp s3://$COOKBOOK_BUCKET/scripts/lith-compare.sh /tmp/c.sh && bash /tmp/c.sh" \
  --ttl 75m --cost-limit 1.20 --on-complete terminate
```

`spawn launch` rather than `task run` because a FUSE mount needs the host, and because
`--instance-type` pins the box ([spawn#610](https://github.com/spore-host/spawn/issues/610)).

## Four things that cost a run each

Recorded because none of them is in any doc, and together they took four launches:

1. **`spawn launch --command` runs as a non-root user**, and an instance launched this way has
   **neither docker nor fuse** (the task-run path has Docker). Everything privileged needs
   `sudo`; `dnf install -y docker fuse fuse3 && systemctl enable --now docker` takes ~20 s.
2. **A launch instance's role could not read our own S3 bucket** — `403 Forbidden` on the first
   line of the command, while `spawn launch` had already printed `✅ Command execution started`.
   The instance then idled to TTL. Fixed with `--iam-managed-policies`.
   ([spawn#614](https://github.com/spore-host/spawn/issues/614))
3. **A FUSE mount is private to the mounting user.** The pinned image runs as `mambauser`, so
   the container got `Permission denied` on the mount until the mount used lith's
   **`--allow-other`** *and* `/etc/fuse.conf` had `user_allow_other` uncommented. Use
   `--daemon` too: backgrounding `lith mount` from a shell that then exits takes the mount
   down with it, which is how one run aligned against an empty directory.
4. **Push results after every phase.** A first attempt ran 38 minutes and uploaded nothing
   because it only wrote to S3 at the end; its route-A numbers died with the box.

## Caveats

n = 1 per route. The counts are not directly comparable *as a performance figure* precisely
because the inputs differed (ALT vs no ALT) — the timing comparison is still fair (same
aligner, same reads, same thread count, 0.88% more work on the mount side, which if anything
understates lith). The shape claim at the end is reasoned, not yet measured.
