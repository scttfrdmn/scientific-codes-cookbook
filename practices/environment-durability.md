# An image you can run is not an environment you can rebuild

> **A digest pins the env inside an image. An explicit lock pins one you can rebuild anywhere.**
> Every recipe here pins its image by `@sha256:`, and that image keeps running. But "the same
> versions" is not a recipe for getting that env back. conda-forge rewrites package metadata after
> the fact, so the versions an image was built from can stop being installable, on every chip.

## What actually rots

conda-forge never deletes a package file. It **patches repodata**: it adds a dependency cap to an
old build when someone finds an incompatibility. Measured here: the aarch.science `md` image was
built with mdtraj 1.11.1 and numpy 2.5.2, and it passes its recipes. Later, conda-forge capped that
mdtraj at `numpy <2.5`. From then on, solving those versions fails on **linux-64 and linux-aarch64
alike**. So 10 recipes could not get an x86 twin by re-solving, and nobody could rebuild their env
outside the image.

The image is unaffected, because nothing inside it is re-solved. What broke is every path back
to that env that goes through a solver.

## Lock exact files, and never re-solve

An `@EXPLICIT` lock lists exact package files by URL and sha256. Installing it consults no solver
and no repodata, so a later patch cannot touch it. And because staging copies every package into
your bucket, a file withdrawn upstream still installs from that copy.

The rule that follows:

1. **Lock at verification time.** When a recipe on an aarch.science env is verified, its locks are
   committed in the same change:
   - the arm64 one, exported from the pinned image's own installed packages (`envs-arm64/`);
   - the x86 twin, solved for linux-64 (`envs-x86_64/`, via `scripts/lock_env_x86.py`).
2. **Never re-solve a committed lock.** `lock_env_x86.py` refuses to overwrite one. `make check`
   fails if a lock changed after the run that verified it.
3. **Re-solve only to repin, and repinning is a pin swap.** It needs a new image digest and
   re-verification on both chips. You don't reconstruct a combination conda-forge has since
   disowned: the patch is upstream saying it's incompatible.
4. **Say so on the page** when a recipe's env can no longer be re-solved: the date, and the patch
   that did it.

## Rebuild an env yourself, outside the image

```bash
micromamba create -p ./env -f envs-arm64/<env>-<hash>.linux-aarch64.lock     # Graviton
micromamba create -p ./env -f envs-x86_64/<env>-<hash>.linux-64.lock         # x86_64
```

No solve happens and nothing is resolved at install time. You get the same files the verified run
used, from their conda-forge URLs, or `--offline` from a staged copy. This is how a recipe's env
outlives the registry that published its image: aarch.science keeps digests on a best-effort basis.

Related: [input-provenance](input-provenance.md) for the *data* side of the same question, and
CLAUDE.md's pin-swap rule for what a repin obliges.
