# scientific-codes-cookbook

Working, runnable recipes for the ~50 codes researchers actually use — genomics, molecular dynamics, DFT, phylogenetics, earth observation — each one running on AWS in minutes, on right-sized hardware, and turning itself off when it's done. A recipe tells you **what to run, on what box, and why**.

## Why this exists

Running one of these codes on your laptop is easy. Running it on the cloud — where the cores and memory actually are — usually is not: a VPC, subnets, security groups, IAM roles, an AMI, a launch template, maybe a batch service, all before the first job. Two weeks of setup to run a one-hour job, so mostly people don't.

This collapses that to three steps: **find your code, run the recipe, and it turns itself off.** No standing infrastructure to forget about, no bill running after the result is in.

## The loop

1. **Know what to run and why** — this cookbook. One page per code: the real invocation, a pinned container, and a smoke check that proves the output is real.
2. **`truffle`** finds the right box — a Graviton4 `c8g`/`m8g`/`r8g` sized to the job.
3. **`spawn`** runs the recipe in that container, TTL-capped, and self-terminates.
4. **`lagotto`** steps in when the capacity you asked for isn't there.

All of it is [spore.host](https://docs.spore.host) tooling — the docs cover the suite and how to install it.

## Five minutes to a first result

```sh
brew install spore-host/tap/truffle spore-host/tap/spawn
aws sso login                                              # your account, your credentials
spawn task run --spec recipes/seqkit/01-stats.task.json --wait
spawn list                                                 # → nothing running
```

That last line is the point. The box booted, ran the recipe, checked its own output, and **turned itself off** — `spawn list` shows nothing because there's nothing left to pay for, and the run reports what it cost. (A short job is mostly boot overhead; [job arrays](patterns/job-arrays.md) amortize that across a cohort.)

## Find your code

Browse [`recipes/`](recipes/) — one directory per code, each README a self-contained page: the invocation, what to change for your own work, and all the verification in one place. The catalog spans genomics, molecular dynamics, quantum chemistry, materials, phylogenetics, and geo/EO; every page carries the exact image digest and versions it was run with.

## Three ideas the recipes lean on

- **Right-size, don't max-size** — more cores stop paying past a knee. → [sizing](patterns/sizing.md)
- **Scale out, not up** — a cohort is the same task fanned out, not a bigger box. → [job arrays](patterns/job-arrays.md)
- **Pay for the bytes you touch** — copy, mount, or share by what the job reuses. → [copy, mount, or share?](patterns/data-movement.md)

## What this doesn't cover

Working examples, not benchmarks; arm64 first, GPU is Round Two; ephemeral, no standing infrastructure. The full list — and the physical limits behind it — is in [what this cookbook does not cover](practices/what-this-does-not-cover.md).

## It runs in your own AWS account

Your credentials, your buckets, your bill — nothing intermediated. spore.host is the tooling that launches the box and tears it down; the compute and the data are yours.

## Where this will live

The cookbook will be published at **https://cookbook.spore.host**. Until that site is built, this repository is the source of truth — read the recipes here.
