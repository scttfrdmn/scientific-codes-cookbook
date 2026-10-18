# DADA2 on a real MiSeq study: the DADA2 tutorial reproduced, then the whole Kozich et al. 2013
# stability study it was cut from, both checked against the HMP mock community's known sequences.
#
#   Rscript dada2-real.R <dir with StabilityNoMetaG.tar and miseqsopdata.zip> <out dir> <threads>
#
# Part A runs the tutorial's own calls on its own 20 samples and asserts the tutorial's printed
# numbers (benjjneb.github.io/dada2/tutorial.html, built with dada2 1.37.0; this image is 1.38.0).
# Part B runs the same calls on every sample of the study. Every assertion is recorded, and the
# script stops non-zero at the end if any failed, so one run reports all of them.
suppressMessages(library(dada2))
args <- commandArgs(TRUE)
IN <- args[1]; OUT <- args[2]; NT <- as.integer(args[3])
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
W <- "/var/tmp/dada2-real"                     # container disk, not the /tmp staging tmpfs
dir.create(W, showWarnings = FALSE, recursive = TRUE)
RES <- file.path(OUT, "results.tsv"); cat("", file = RES)
FAILS <- 0L
rec <- function(k, v) {                         # str() every value: never let reporting fail a run
  v <- paste(vapply(as.list(v), function(x) as.character(x), ""), collapse = ",")
  cat(k, "\t", v, "\n", sep = "", file = RES, append = TRUE); cat(sprintf("%-44s %s\n", k, v))
}
must <- function(ok, k, detail = "") {
  ok <- isTRUE(all(as.logical(ok))) && length(ok) > 0
  if (ok) rec(paste0("assert:", k), "ok") else { rec(paste0("assert:", k), paste("FAIL", detail)); FAILS <<- FAILS + 1L }
}
tic <- function() proc.time()[["elapsed"]]
timed <- function(k, expr) { t0 <- tic(); v <- expr; rec(paste0("wall_s:", k), sprintf("%.1f", tic() - t0)); v }
gu <- function(x) sum(getUniques(x))
msgs <- character(0)
quietly <- function(expr) withCallingHandlers(expr, message = function(m) {
  msgs <<- c(msgs, conditionMessage(m)); invokeRestart("muffleMessage") })

rec("dada2_version", as.character(packageVersion("dada2")))
rec("r_version", R.version.string)
rec("threads", NT)
rec("lapack_library", La_library())   # recorded only: DADA2's inference is C++, not BLAS

# The mock's truth: the 20 reference V4 amplicons the tutorial evaluates against
unzip(file.path(IN, "miseqsopdata.zip"), exdir = W, unzip = "internal")   # the image has no unzip binary
sop <- list.files(W, pattern = "MiSeq_SOP$", recursive = TRUE, include.dirs = TRUE, full.names = TRUE)[1]
ref <- getSequences(file.path(sop, "HMP_MOCK.v35.fasta"))
rec("mock_reference_sequences", length(ref))

pipeline <- function(tag, fnFs, fnRs, names) {
  filtFs <- file.path(W, tag, "filtered", paste0(names, "_F_filt.fastq.gz"))
  filtRs <- file.path(W, tag, "filtered", paste0(names, "_R_filt.fastq.gz"))
  names(filtFs) <- names; names(filtRs) <- names
  out <- timed(paste0(tag, ":filterAndTrim"), filterAndTrim(fnFs, filtFs, fnRs, filtRs,
            truncLen = c(240, 160), maxN = 0, maxEE = c(2, 2), truncQ = 2, rm.phix = TRUE,
            compress = TRUE, multithread = NT))
  keep <- out[, "reads.out"] > 0             # a sample filtered to nothing has no file to read
  # learnErrors reports how many bases it learned from on stdout (cat), not as a message
  loF <- character(0); loR <- character(0)
  errF <- timed(paste0(tag, ":learnErrorsF"), { loF <- capture.output(e <- learnErrors(filtFs[keep], multithread = NT)); e })
  errR <- timed(paste0(tag, ":learnErrorsR"), { loR <- capture.output(e <- learnErrors(filtRs[keep], multithread = NT)); e })
  errmsg <- grep("total bases", c(loF, loR), value = TRUE)
  dadaFs <- timed(paste0(tag, ":dadaF"), quietly(dada(filtFs[keep], err = errF, multithread = NT)))
  dadaRs <- timed(paste0(tag, ":dadaR"), quietly(dada(filtRs[keep], err = errR, multithread = NT)))
  if (sum(keep) == 1) { dadaFs <- setNames(list(dadaFs), names[keep]); dadaRs <- setNames(list(dadaRs), names[keep]) }
  mergers <- timed(paste0(tag, ":mergePairs"), quietly(mergePairs(dadaFs, filtFs[keep], dadaRs, filtRs[keep])))
  seqtab <- makeSequenceTable(mergers)
  nochim <- timed(paste0(tag, ":removeBimeraDenovo"),
                  quietly(removeBimeraDenovo(seqtab, method = "consensus", multithread = NT)))
  list(out = out, keep = keep, errF = errF, errR = errR, errmsg = errmsg, dadaFs = dadaFs,
       dadaRs = dadaRs, mergers = mergers, seqtab = seqtab, nochim = nochim, filtFs = filtFs, filtRs = filtRs)
}

mock_eval <- function(tag, nochim, sample) {
  u <- sort(nochim[sample, ][nochim[sample, ] > 0], decreasing = TRUE)
  exact <- vapply(names(u), function(x) any(grepl(x, ref, fixed = TRUE)), TRUE)
  rec(paste0(tag, ":", sample, ":asvs"), length(u))
  rec(paste0(tag, ":", sample, ":exact_reference_matches"), sum(exact))
  rec(paste0(tag, ":", sample, ":reference_records_hit"),
      sum(vapply(ref, function(r) any(vapply(names(u)[exact], function(x) grepl(x, r, fixed = TRUE), TRUE)), TRUE)))
  rec(paste0(tag, ":", sample, ":abundances"), paste0(u, ifelse(exact, "", "*")))
  for (x in names(u)[!exact]) rec(paste0(tag, ":", sample, ":nonreference_asv_min_hamming_to_reference"),
      tryCatch(min(vapply(ref, function(r) nwhamming(x, r), 0)), error = function(e) paste("n/a:", conditionMessage(e))))
  rec(paste0(tag, ":", sample, ":reads"), sum(u))
  rec(paste0(tag, ":", sample, ":reads_in_nonreference_asvs"), sum(u[!exact]))
  rec(paste0(tag, ":", sample, ":largest_nonreference_asv_reads"), if (any(!exact)) max(u[!exact]) else 0)
  list(n = length(u), exact = sum(exact), u = u, isref = exact)
}

# Conservation identities that hold for any correct run, on any data
conserve <- function(tag, r) {
  s <- names(r$dadaFs)
  dF <- vapply(r$dadaFs, gu, 0); dR <- vapply(r$dadaRs, gu, 0)
  mg <- vapply(r$mergers, function(m) sum(m$abundance), 0)
  filt <- r$out[r$keep, "reads.out"]
  must(all(r$out[, "reads.out"] <= r$out[, "reads.in"]), paste0(tag, ":filtered<=input"))
  must(all(dF <= filt) && all(dR <= filt), paste0(tag, ":denoised<=filtered"))
  must(all(mg <= pmin(dF, dR)), paste0(tag, ":merged<=min(denoisedF,denoisedR)"))
  must(identical(as.numeric(rowSums(r$seqtab)[s]), as.numeric(mg)), paste0(tag, ":seqtab_rowsums==merged"))
  must(all(rowSums(r$nochim) <= rowSums(r$seqtab)), paste0(tag, ":nonchim<=merged"))
  must(all(colnames(r$nochim) %in% colnames(r$seqtab)) &&
       identical(unname(colSums(r$nochim)), unname(colSums(r$seqtab)[colnames(r$nochim)])),
       paste0(tag, ":nonchim_columns_are_seqtab_columns_unchanged"))
}

# ---------------- Part A: the tutorial, verbatim, against its printed numbers ----------------
fA <- sort(list.files(sop, pattern = "_R1_001.fastq$", full.names = TRUE))
rA <- sort(list.files(sop, pattern = "_R2_001.fastq$", full.names = TRUE))
nA <- sapply(strsplit(basename(fA), "_"), `[`, 1)
rec("A:samples", length(nA))
A <- timed("A:total", pipeline("A", fA, rA, nA))
pubOut <- matrix(c(7793, 7113, 5869, 5299, 5958, 5463, 3183, 2914, 3178, 2941, 4827, 4312), ncol = 2, byrow = TRUE)
must(identical(unname(A$out[1:6, ]) + 0, pubOut + 0), "A:filterAndTrim_head==tutorial",
     paste(A$out[1:6, ], collapse = ","))
rec("A:learnErrors_messages", A$errmsg)
must(identical(A$errmsg[1], "33514080 total bases in 139642 reads from 20 samples will be used for learning the error rates.") &&
     identical(A$errmsg[2], "22342720 total bases in 139642 reads from 20 samples will be used for learning the error rates."),
     "A:learnErrors_bases==tutorial")
nvF1 <- nrow(A$dadaFs[[1]]$clustering)
rec("A:dadaF_sample1_variants", nvF1)
must(nvF1 == 128, "A:dadaF_sample1_variants==128")
rec("A:dim_seqtab", dim(A$seqtab))
must(identical(dim(A$seqtab), c(20L, 293L)), "A:dim_seqtab==20x293")
lens <- table(nchar(getSequences(A$seqtab)))
rec("A:length_table", paste(names(lens), lens, sep = ":"))
must(identical(as.integer(names(lens)), 251:255) && identical(as.integer(lens), c(1L, 88L, 196L, 6L, 2L)), "A:length_table==tutorial")
rec("A:dim_nochim", dim(A$nochim))
must(identical(dim(A$nochim), c(20L, 232L)), "A:dim_nochim==20x232")
ratio <- sum(A$nochim) / sum(A$seqtab)
rec("A:nonchim_fraction", sprintf("%.7f", ratio))
must(sprintf("%.7f", ratio) == "0.9640374", "A:nonchim_fraction==0.9640374")
trackA <- cbind(A$out, vapply(A$dadaFs, gu, 0), vapply(A$dadaRs, gu, 0),
                vapply(A$mergers, function(m) sum(m$abundance), 0), rowSums(A$nochim))
pubTrack <- matrix(c(7793, 7113, 6976, 6979, 6540, 6528,  5869, 5299, 5227, 5239, 5028, 5017,
                     5958, 5463, 5331, 5357, 4986, 4863,  3183, 2914, 2799, 2830, 2595, 2521,
                     3178, 2941, 2822, 2868, 2553, 2519,  4827, 4312, 4151, 4228, 3646, 3507), ncol = 6, byrow = TRUE)
rec("A:track_head", apply(trackA[1:6, ], 1, paste, collapse = "/"))
must(identical(unname(trackA[1:6, ]) + 0, pubTrack + 0), "A:track_head==tutorial")
mA <- mock_eval("A", A$nochim, "Mock")
must(mA$n == 20 && mA$exact == 20, "A:Mock_20_ASVs_all_exact==tutorial")
conserve("A", A)
write.table(trackA, file.path(OUT, "A-track.tsv"), sep = "\t", quote = FALSE)
saveRDS(A$nochim, file.path(OUT, "A-seqtab-nochim.rds"))

# ---------------- Part B: the whole study ----------------
untar(file.path(IN, "StabilityNoMetaG.tar"), exdir = file.path(W, "stability"))
fB <- sort(list.files(file.path(W, "stability"), pattern = "_R1_001.fastq", full.names = TRUE, recursive = TRUE))
rB <- sort(list.files(file.path(W, "stability"), pattern = "_R2_001.fastq", full.names = TRUE, recursive = TRUE))
nB <- sapply(strsplit(basename(fB), "_"), `[`, 1)
must(length(fB) == length(rB) && identical(sub("_R1_001", "", basename(fB)), sub("_R2_001", "", basename(rB))),
     "B:every_R1_has_its_R2")
must(!anyDuplicated(nB), "B:sample_names_unique", paste(nB[duplicated(nB)], collapse = ","))
rec("B:samples", length(nB))
B <- timed("B:total", pipeline("B", fB, rB, nB))
rec("B:reads_in", sum(B$out[, "reads.in"])); rec("B:reads_filtered", sum(B$out[, "reads.out"]))
rec("B:learnErrors_messages", B$errmsg)
rec("B:dim_seqtab", dim(B$seqtab)); rec("B:dim_nochim", dim(B$nochim))
rec("B:nonchim_fraction", sprintf("%.7f", sum(B$nochim) / sum(B$seqtab)))
conserve("B", B)
# The tutorial's 20 samples are a subset of this study: where the reads are the same bytes, the
# per-read filter must give the same counts in both runs, whatever else is in the batch
shared <- intersect(nA, nB)
rec("B:samples_shared_with_tutorial", length(shared))
same <- shared[vapply(shared, function(s) {
  a <- readLines(fA[nA == s]); b <- readLines(gzfile(fB[nB == s]))
  identical(a, b) }, TRUE)]
rec("B:shared_samples_byte_identical_reads", length(same))
if (length(same)) must(identical(A$out[match(same, nA), ] + 0, B$out[match(same, nB), ] + 0) ||
                       identical(unname(A$out[match(same, nA), ]) + 0, unname(B$out[match(same, nB), ]) + 0),
                       "B:filter_counts_on_shared_samples==A")
# Sample independence (pool=FALSE): with the error model held fixed, a sample denoised alone and
# single-threaded gives exactly what it got inside the 16-thread whole-study batch
mocks <- grep("^Mock[0-9]*$", names(B$dadaFs), value = TRUE)
rec("B:mock_samples", mocks)
probe <- if (length(mocks)) mocks[1] else names(B$dadaFs)[1]
alone <- quietly(dada(B$filtFs[[probe]], err = B$errF, multithread = FALSE))
must(identical(getUniques(alone), getUniques(B$dadaFs[[probe]])), "B:sample_alone_serial==in_batch_threaded", probe)
mB <- lapply(setNames(mocks, mocks), function(m) mock_eval("B", B$nochim, m))
# the tutorial's published mock result, now under an error model learned from the whole study
must(mB$Mock$n == 20 && mB$Mock$exact == 20, "B:Mock_20_ASVs_all_exact==tutorial")
rec("B:Mock_identical_to_A", identical(mB$Mock$u, mA$u))
write.table(cbind(B$out[B$keep, ], denoisedF = vapply(B$dadaFs, gu, 0), denoisedR = vapply(B$dadaRs, gu, 0),
                  merged = vapply(B$mergers, function(m) sum(m$abundance), 0), nonchim = rowSums(B$nochim)),
            file.path(OUT, "B-track.tsv"), sep = "\t", quote = FALSE)
saveRDS(B$nochim, file.path(OUT, "B-seqtab-nochim.rds"))
saveRDS(list(errF = getErrors(B$errF), errR = getErrors(B$errR)), file.path(OUT, "B-errors.rds"))
# plain-text, full-precision copies of the fixed points, for byte comparison across chips
write.table(B$nochim, file.path(OUT, "B-seqtab-nochim.tsv"), sep = "\t", quote = FALSE)
for (k in c("errF", "errR")) write.table(format(getErrors(B[[k]]), digits = 17), file.path(OUT, paste0("B-", k, ".tsv")),
                                         sep = "\t", quote = FALSE)
# a plain-text digest of the fixed point, for cross-chip comparison without R
writeLines(paste(colnames(B$nochim), colSums(B$nochim), sep = "\t"), file.path(OUT, "B-asv-totals.tsv"))
rec("assert_failures", FAILS)
if (FAILS > 0) quit(status = 1)
cat("DADA2 REAL OK\n")
