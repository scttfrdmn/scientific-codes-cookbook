# The container path: three things true of every recipe

These aren't facts about BWA or salmon or any one code — they're how `spawn task run` runs a
container, so they're identical on all 54 recipe pages. Learn them once here; the recipes just
obey them.

## Stage everything flat in `/tmp`

**The symptom:** your task writes its output to `/data/out` (the tidy layout every example
uses) and the command dies with `Permission denied` — or worse, the file simply never appears
and the task still reports success.

**Why:** `spawn task run` bind-mounts each staged path's parent and creates it as the instance
user (uid 1000), but `docker run` is issued with no `--user`, so an aarchbio image runs as its
own `mambauser` (uid 57439). A 0755 directory owned by uid 1000 isn't writable by uid 57439.
Host `/tmp` is mode 1777 — the one location writable regardless of the image's user. And an
*output* path's parent is created by Docker as **root** (even less writable), while anything
outside `/tmp` fails at `mkdir` before uid matters at all. Confirmed by a deliberate probe, not
inferred (spore-host/spawn#555).

**Do this:** stage every input and write every output as a flat path directly in `/tmp` —
`/tmp/reads_1.fq.gz`, `/tmp/out.bam`. If a tool insists on writing a directory, `tar` it to one
flat file and untar it in the consuming task. The `/data` + `/work` layout in spawn's own
example cannot work on this path.

## The exit code does not prove the output is real

**The symptom:** the task exits 0, `spawn task status` says `completed` — and the file you
wanted isn't in the bucket.

**Why:** a task whose declared output fails to stage was once still recorded
`state: completed, exit_code: 0` — the wrapper computed the stage-out result and never read it
(spore-host/spawn#561, since fixed, but the lesson outlives the bug). An exit code reports that
the command *ran*, never that its output is real: output that's empty, truncated, or absent
still exits 0.

**Do this:** put the smoke check **inside** the task so it can fail the task, and confirm the
objects are actually in the bucket afterward. Trust the artifact, not the status — this is the
single most load-bearing habit in the catalog, and it holds on the workflow path too (a
Nextflow summary once said `completed` while the task's S3 exit code was `126` with no output).

## One tool per image — so a pipe becomes a chain through S3

**The symptom:** you reach for `bwa mem | samtools sort` and there's no image with both tools.

**Why:** `spec.container` takes a single image, and aarch.* ships **one tool per image on
purpose** — every image traces to one signed conda recipe, and mulled multi-tool images would
mean resolving a joint environment, which breaks that provenance. This is the model, not a gap
waiting to be filled.

**Do this:** make the pipe a **sequence of single-tool tasks**, with the intermediate data
round-tripping through S3 — `bwa` writes the SAM to S3, `samtools` reads it back. Two boots
instead of one pipe; the price of a pinned, single-recipe image. It also falls out for free
that every task is independently rerunnable, because each reads all its inputs from S3 and
writes its outputs there — a failed step 2 reruns alone.

---

Recipes link here instead of repeating any of it. If you're about to explain one of these three
on a recipe page, link this page instead — that's the line between a cookbook entry and an
essay with commands in it.
