# Proving which bytes you ran on

> **An image pins itself; data doesn't.** Every recipe here pins its container with
> `@sha256:` and cosign-verifies it — and then stages its *inputs* with a hand-rolled shell
> script. This page is about closing that asymmetry, and about the two different questions
> that get mistaken for one.

A reader who re-runs a recipe gets the same binary by construction. Whether they get the same
*input* is currently a matter of reading the staging script and trusting it. 29 of the recipes
here ship a `stage-inputs.sh`; 26 pin their input bytes by sha256, and the three that don't
(bwa-mem2, kallisto, minimap2) derive theirs from an object another recipe already pinned. So
the pins exist — but as **26 bespoke implementations of one idea**, with no single manifest to
check a staged bucket against.

## Two questions that look like one

The confusion worth avoiding: content-addressing and mounting are not competing answers.

| | the question | the answer shape | the tell |
|---|---|---|---|
| **Content-addressing** (DVC, ORAS, our sha256 scripts) | *Did I stage the same bytes you did?* | a digest you can recompute and compare | there **are** bytes you hold |
| **Mount in place** ([lith](https://github.com/scttfrdmn/lith)) | *Can I read what I can't copy?* | a filesystem over data you never move | there are **no** bytes to address |

Scale is what separates them, and the gap is not subtle. The RODA kraken2 RefSeq-Complete
database is **1.206 TB**; `hash.k2d` alone reports **1,189,091,671,800 bytes** through the
mount. `lith index build` covered all of it in about **a second**, producing a **728-byte**
index. Nothing about that is a content-addressing problem — you cannot hash what you declined
to download, and the entire point is that nobody copies it.

So: content-addressing for the MB–GB inputs a recipe **produces**; mounting for the TB
references it **reads**. [data-movement](../patterns/data-movement.md) covers choosing the
*transport*; this page is only about identity.

## What content-addressing has to get right here: the set, not the envelope

The sharpest case in this catalog is `recipes/methylation-array`, and it argues against the
obvious design. SeSAMe needs three ExperimentHub resources cached offline. That cache **cannot
be pinned as an artifact**: BiocFileCache gives every blob a random filename prefix and the
sqlite files carry timestamps, so neither the filenames nor a tar of the directory is
reproducible. The downloaded resources themselves are immutable.

So the pin is on **content, not container** — hash every non-sqlite blob and require the *set*
to match four known sha256s, ignoring the envelope entirely:

```sh
( cd "$CACHE" && for f in $(ls | grep -vE "sqlite|LOCK"); do
    shasum -a 256 "$f" | cut -d' ' -f1
  done | sort ) > got.txt        # compared against the pinned set, not against a tar's sum
```

A tool that tracks the artifact *as produced* — DVC hashes the file or directory it is given —
would pin the irreproducible tar and report a spurious change on every re-stage. The mode
scientific caches actually need is **"this set of blobs, ignore the container"**, which is a
real gap rather than a configuration detail. Same shape as
[sourcing a reference from a code's own tests](reference-from-tests.md): the thing worth
pinning is rarely the thing the tool hands you.

## Why putting data in the registry is tempting, and the one measured caution

The appeal of ORAS is precise. This project currently runs **two trust roots**: images are
digest-pinned and cosign-verified, while input data is a sha256 inside a shell script with no
signature at all. Pushing data as an OCI artifact collapses those into one verification path.
That is a genuine simplification, not a reshuffle.

The caution is measured rather than hypothetical. Container registries are **not archives**:
retention on the registry this project pulls from is best-effort, four digests were garbage-
collected out from under us, and a vanished pull is easy to misread — a `401` is not a `404`,
which cost one wrong diagnosis before the distinction was recorded. Keeping ~40 pins alive took
an explicit arrangement, not a default.

So data-in-a-registry needs a mirror story — S3, Zenodo, a DOI — stated in the design, not added
after the first eviction. Otherwise it trades a scattered-but-durable layer for a
unified-but-evictable one, which is a worse deal than it looks.

## The rule

Pin the input, not just the image — and pin the **content set** rather than whatever file the
tool happened to emit, because the envelope is often the irreproducible part. Reach for a
content-addressed layer for inputs you produce and could hand someone; reach for a mount for
references too large to hand anyone. And whatever holds the digest, keep the bytes somewhere
that promises to still have them.
