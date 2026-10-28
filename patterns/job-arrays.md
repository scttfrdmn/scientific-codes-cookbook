# Job arrays — run many small things, not one big thing

> **Ten instances for one hour finish in one, at roughly the same compute cost as one instance for ten hours, plus the per-instance boot and image pull you now pay ten times over.** That dial does not exist on a machine you own.

You have 500 samples to align. The cluster instinct is one big node, kept busy, because on a shared
queue each request was expensive. Drop it. Here each sample is its own instance: all 500 at once,
each sized for *one* sample, each turning itself off when its sample is done. The core-hours of the
science are the same either way; the win is wall-clock, from "one machine, 500 times in series" to
"500 machines, once." The overhead is the catch: boot, Docker and the image pull repeat per
instance ([itemised](layout-and-effective-cost.md#the-provisioning-tax-itemised--and-the-ami-question)).
**This is safe to try:** a wrong guess costs cents and self-terminates. Launch 5, then 500.

## The shape

One task per sample. `spawn launch --count N` starts N instances tagged as one array; each reads
its slot from `$JOB_ARRAY_INDEX`:

```bash
# samples.txt — one sample per line (S3 prefixes, accession IDs, whatever your script keys on)
spawn launch \
  --count $(wc -l < samples.txt) \
  --job-array-name align-cohort \
  --instance-type c8g.2xlarge \
  --command ./align-one.sh \
  --on-complete terminate \
  --ttl 1h \
  --cost-limit 50.00
```

```bash
# align-one.sh — index → sample, then read from S3 and write results back to S3
SAMPLE=$(sed -n "$((JOB_ARRAY_INDEX + 1))p" samples.txt)
# pull $SAMPLE's reads from s3://…, align, write the BAM back to s3://…
```

Then treat the array as one object:

```bash
spawn array status  align-cohort            # requested vs launched; which indexes are missing
spawn array collect align-cohort ./results  # where each member's output landed
spawn array retry   align-cohort --failed   # relaunch only the indexes that didn't finish
```

No member depends on another's disk, which is why one failed index reruns alone.

## Size one sample, not the batch

The expensive mistake is a 96-vCPU instance, times 500. **Size each task for one sample** at its
[knee](sizing.md); the array costs that × N and takes about one task's wall-clock.

**Wider is sooner, not free, and it has a floor.** Cost rises gently with N because every task pays
the provisioning tax again, and wall-clock floors at one task's time once N reaches the number of
pieces the work splits into. So fan out to about your number of samples (or shards), not further.
When many small jobs each finish in seconds, the tax dominates and packing one box beats fanning
out ([layout and effective cost](layout-and-effective-cost.md)). Over shared read-only data, mount
rather than copy ([Copy, mount, or share?](data-movement.md)); lith reports a 64-node mount
fan-out both faster and cheaper than staging ([lith](https://scttfrdmn.github.io/lith/deadline/)).

<details>
<summary>Choosing N, concurrency, and partial success</summary>

- **N** is how many pieces the work splits into. Large N is fine; the launch *rate* is the limit,
  and `--max-concurrent-auto` derives a safe ceiling from your account's quota headroom.
- **`--min-viable K`** proceeds once K members launch instead of failing on a few without capacity.
- **Spot** (`--spot`) fits: tasks are independent and cheap to replay with `retry --failed`.
</details>

## Verify terminations — this is the one that bites at scale

Each task ends itself with a [computation sentinel](https://docs.spore.host/tools/spored); TTL is
the backstop and `--cost-limit` the hard cap. At N=1 you'd notice a stuck instance. **At N=500 you
won't**: one task that fails to self-terminate bills to its TTL, hidden in the crowd. After the
array drains, confirm every index closed:

```bash
spawn array status align-cohort     # requested vs launched vs finished; nothing still running
```

The platform accounting is the check (the spore.host MCP server answers "what's still running?").
If tasks hydrate scratch from S3, verify that's torn down too ([Data movement](data-movement.md)).

## Where this shows up

Any "N inputs, one tool each" workload: [bwa](../recipes/bwa-samtools/README.md),
[salmon](../recipes/salmon/README.md), [kallisto](../recipes/kallisto/README.md),
[fastp](../recipes/fastp/README.md), one task per sample. Every flag is in
[`spawn`](https://docs.spore.host/tools/spawn) and the [guides index](https://docs.spore.host/guides/);
you don't need them before your first run.
