# Job arrays — run many small things, not one big thing

> **Ten instances for one hour cost the same as one instance for ten hours — and finish in one hour.** That dial does not exist on a machine you own.

You have 500 samples to align. The instinct you brought from the cluster is to ask for one
big node and push the samples through it — because on a shared queue each request was
expensive and you got one shot, so you asked for everything at once and kept it busy.

Drop that instinct. Here, each sample is its own instance: all 500 at once, each sized for
*one* sample, each turning itself off the moment its sample is done. You pay the same
core-hours either way — 500 alignments take the compute they take — but the wall-clock
collapses from "one machine, 500 times in series" to "500 machines, once."

**This is safe to try.** A wrong guess costs cents and self-terminates. Launch 5, look at what
one costs, then launch 500 — nothing in this pattern can run up a bill you didn't cap.

## The shape

One task per sample. `spawn launch --count N` starts N instances tagged as one array; each
reads its slot from `$JOB_ARRAY_INDEX` and picks its own work:

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

Then treat the whole array as one object:

```bash
spawn array status  align-cohort            # requested vs launched; which indexes are missing
spawn array collect align-cohort ./results  # where each member's output landed
spawn array retry   align-cohort --failed   # relaunch only the indexes that didn't finish
```

Each member reads its inputs from S3 and writes results back to S3 — nothing depends on
another member's disk, which is exactly why one failed index reruns alone.

## Size one sample, not the batch

The expensive mistake is importing the big-machine habit into the fan-out — asking for a
96-vCPU instance, times 500. **Size each task for one sample.** Find the instance where a
single alignment stops getting faster (its knee — see [Sizing and the scaling knee](sizing.md))
and that's your `--instance-type`. The array's cost is that per-task cost × N; its wall-clock is
about *one* task's. Ten small right-sized instances beat one oversized one on both axes — and
"right-sized" is a dial that doesn't exist on a node you bought.

**Wider is sooner, not free — and it has a floor.** Two honest caveats keep this from being a
slogan. First, cost is *not* flat across width: the core-hours of the science are conserved, but
every task pays the boot-and-image-pull overhead ([the container path](../practices/container-path.md))
*again*, so total cost rises gently with N. Second, wall-clock **floors** at roughly one task's
time — once N reaches the number of pieces the work splits into, more instances buy nothing, and
past that you're paying overhead for idle boxes. So the question isn't *whether* to go wide, it's
*how* wide: fan out to about your number of samples (or shards), not further. Done right it is both
sooner and cheaper than one big node — a mount-based fan-out over shared read-only data has been
demonstrated at 64 nodes coming out both faster and cheaper than staging (see [Copy, mount, or
share?](data-movement.md) and [lith](https://scttfrdmn.github.io/lith/deadline/)).

<details>
<summary>Choosing N, concurrency, and partial success</summary>

- **N** is just how many pieces the work splits into — usually one per sample/shard/parameter.
  Large N is fine; the launch *rate*, not N, is the only limit, and `--max-concurrent-auto`
  derives a safe ceiling from your account's real quota headroom.
- **`--min-viable K`** lets the array proceed once K members launch instead of failing when a
  few can't get capacity — lower parallelism, not failure.
- **Spot** (`--spot`) fits naturally: the tasks are independent and each is cheap to lose and
  replay with `spawn array retry --failed`.
</details>

## Verify terminations — this is the one that bites at scale

Each task ends itself with a [computation sentinel](https://docs.spore.host/tools/spored)
(`spored`, watching the *work* finish) rather than a timer; TTL is the backstop; `--cost-limit`
is the hard cap. Three layers — and at N=1 you'd notice a stuck instance. **At N=500 you won't.**
One task that fails to self-terminate hides in the crowd and bills to its TTL, or past it if
something is truly wrong. So after the array drains, confirm every index actually closed:

```bash
spawn array status align-cohort     # requested vs launched vs finished; nothing still running
```

The platform accounting *is* the check — you don't need a raw `aws ec2` query for it. And if you'd
rather not think in CLI at all, the spore.host **MCP server** lets you ask your AI assistant
"what's still running?" and "stop the one that hung" in plain language — the same verify-terminations
move, for the audience that won't write a filter query.

Same discipline covers storage: if your tasks hydrate
scratch (EFS/Lustre) from S3, verify the *scratch* is torn down too — a stranded filesystem
bills quietly and never looks as obviously wasteful as an idle instance. See
[Data movement](data-movement.md).

## Where this shows up

Any "N inputs, one tool each" workload is this shape — the everyday bioinformatics case (N
samples × one aligner or quantifier) most of all. Recipes that fan out this way:

- [bwa](../recipes/bwa-samtools/README.md) — one alignment is one task; a cohort is this pattern.
- salmon, kallisto, fastp — same shape, one task per sample.

The canonical flag reference lives in docs.spore.host —
[`spawn`](https://docs.spore.host/tools/spawn) documents `--count` and the `array` subcommands,
and the [guides index](https://docs.spore.host/guides/) covers the run-many-jobs workflow end to
end. This page is the *why* and the shape; the docs are every flag. You don't need to read them
before your first run.
