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

**You need:** the AWS CLI configured with working credentials (`aws sts get-caller-identity` must succeed) and a default region, plus this repo cloned. Everything runs in **your** AWS account, in a bucket you create.

Two recipes stage their inputs by subsetting large public BAMs in a pinned container, so their `make stage` step — `RECIPE=bcftools` (the reads the assembly chain assembles) and `RECIPE=macs2` — also needs a local **Docker** with `linux/arm64` support. Every other recipe stages with the AWS CLI alone, and **no** recipe needs Docker to *run* — only those two stage steps do.

```sh
brew install spore-host/tap/truffle spore-host/tap/spawn
make bootstrap            # create your cookbook bucket (once — leaves an S3 bucket in your account)
make run RECIPE=r         # fit a linear model on a Graviton4 box — checked, self-terminating
make ls  RECIPE=r         # your result: fit.txt, smoke-check.txt
spawn list                # confirm the box turned itself off (gone within a minute or two)
```

`r` is the first run because **it builds its input inside the task** — no data staging, so it really is five minutes. `make run` sizes a Graviton4 box, runs R's OLS fit on the bundled `cars` dataset in a pinned container, fails if the output isn't real, and turns the box off (`on_complete: terminate`, with the TTL as a backstop). `make run` returns when the task is done; the box then self-terminates on a ~1–2 minute tick, so `spawn list` — which lists every instance in your account, across regions — shows your `cookbook-r-lm` box winding down and gone shortly after, with nothing left to pay for and nothing to remember to shut off. The run reports what it cost (cents). A short job is mostly boot overhead — [job arrays](patterns/job-arrays.md) amortize that across a cohort.

Every recipe runs the same way — `make run RECIPE=<name>`, with `make stage RECIPE=<name>` first for the ones that need input data (each page says which, and where the data comes from).

## Find your code

Scan [the catalog](catalog/recipes.md) for the whole inventory at a glance — what each recipe does, and what a clean account stages or reuses first — or browse [`recipes/`](recipes/) directly, one directory per code. Each README is self-contained: the invocation, what to change for your own work, and all the verification in one place. The catalog spans genomics, molecular dynamics, quantum chemistry, materials, phylogenetics, and geo/EO; every page carries the exact image digest and versions it was run with.

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
