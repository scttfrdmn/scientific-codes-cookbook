# rnaseq-de-real, task 2 (edgeR image: edgeR + limma). limma-voom and edgeR-QL on the SAME
# integer matrix task 1 handed DESeq2 -- same 16,139 genes after the same >=10-in->=4 filter,
# same ~ cell + dex design -- then the three are compared, and checked against the genes
# Himes et al. (2014) named from this very experiment.
t_start <- Sys.time()
rec <- function(...) { v <- vapply(list(...), function(x) paste(as.character(x), collapse = ","), ""); line <- paste(v, collapse = "\t"); cat(line, "\n"); cat(line, "\n", file = "report.tsv", append = TRUE) }
fail <- character(0)
check <- function(name, ok, detail) { ok <- isTRUE(ok); rec("CHECK", name, if (ok) "PASS" else "FAIL", detail); if (!ok) fail <<- c(fail, name) }

suppressMessages({ library(edgeR); library(limma) })
rec("version", "edgeR", as.character(packageVersion("edgeR")), "limma", as.character(packageVersion("limma")))
rec("version", "R", R.version.string)
rec("arch", Sys.info()[["machine"]])
rec("blas", tryCatch(extSoftVersion()[["BLAS"]], error = function(e) "unknown"))

K <- as.matrix(read.delim("airway_counts.tsv", row.names = 1, check.names = FALSE))
cd <- read.delim("airway_coldata.tsv")
stopifnot(identical(colnames(K), cd$sample))
cell <- factor(cd$cell); dex <- relevel(factor(cd$dex), "untrt")
design <- model.matrix(~ cell + dex)
coef <- "dextrt"
rec("matrix", nrow(K), ncol(K))

t0 <- Sys.time()
y <- calcNormFactors(DGEList(K))
v <- voom(y, design)
lt <- topTable(eBayes(lmFit(v, design)), coef = coef, number = Inf, sort.by = "none")
t_limma <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
t0 <- Sys.time()
ye <- estimateDisp(y, design)
et <- topTags(glmQLFTest(glmQLFit(ye, design), coef = coef), n = Inf, sort.by = "none")$table
t_edger <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
rec("time_s", "limma_voom", sprintf("%.2f", t_limma)); rec("time_s", "edger_ql", sprintf("%.2f", t_edger))

d <- read.delim("res_deseq2_airway.tsv")
rownames(d) <- d$gene
g <- rownames(K)
stopifnot(setequal(g, d$gene))
R <- data.frame(gene = g,
  de_lfc = d[g, "lfc"], de_p = d[g, "pvalue"], de_padj = d[g, "padj"],
  li_lfc = lt[g, "logFC"], li_p = lt[g, "P.Value"], li_padj = lt[g, "adj.P.Val"],
  ed_lfc = et[g, "logFC"], ed_p = et[g, "PValue"], ed_padj = et[g, "FDR"])
sig <- function(p) !is.na(p) & p < 0.05
rec("sig_0.05", "deseq2", sum(sig(R$de_padj)), "limma", sum(sig(R$li_padj)), "edger", sum(sig(R$ed_padj)))

# ---------------------------------------------------------------- DESeq2 vs limma-voom
# The two use different models (NB GLM + Wald vs log-CPM linear model with precision weights,
# TMM vs median-of-ratios), so p-values and log fold changes are not on a shared scale and
# no tolerance on raw values could be justified. What both must agree on is the ANSWER on the
# genes where the evidence is overwhelming: the order of evidence, and direction.
#   1. direction, EXACT: on every gene both call at FDR < 0.05, the sign of the fold change.
#   2. "agree where both are sure": each method's top 100 genes by p-value (of 16,139 tested,
#      i.e. the top 0.6%) must be called at FDR < 0.05 by the other.
# Reported, not asserted: top-100 overlap and Spearman on log2FC -- they measure model
# differences in the ambiguous middle as much as agreement.
both <- sig(R$de_padj) & sig(R$li_padj)
sgn <- sum(sign(R$de_lfc[both]) == sign(R$li_lfc[both]))
check("deseq2_limma_direction", sgn == sum(both), sprintf("%d of %d genes significant in both agree on direction", sgn, sum(both)))
ok <- !is.na(R$de_p)
top <- function(p, n = 100) R$gene[ok][order(p[ok])][seq_len(n)]
de100 <- top(R$de_p); li100 <- top(R$li_p)
a <- sum(sig(R$li_padj[match(de100, R$gene)])); b <- sum(sig(R$de_padj[match(li100, R$gene)]))
check("deseq2_top100_called_by_limma", a == 100, sprintf("%d of DESeq2's top 100 called by limma-voom", a))
check("limma_top100_called_by_deseq2", b == 100, sprintf("%d of limma-voom's top 100 called by DESeq2", b))
rec("top100_overlap", "deseq2_limma", length(intersect(de100, li100)))
rec("top100_overlap", "deseq2_edger", length(intersect(de100, top(R$ed_p))))
cc <- complete.cases(R[, c("de_lfc", "li_lfc", "ed_lfc")])
sp <- c(de_li = cor(R$de_lfc[cc], R$li_lfc[cc], method = "spearman"), de_ed = cor(R$de_lfc[cc], R$ed_lfc[cc], method = "spearman"),
        ed_li = cor(R$ed_lfc[cc], R$li_lfc[cc], method = "spearman"))
rec("spearman_lfc", sprintf("%s=%.4f", names(sp), sp))
rec("spearman_p_rank", sprintf("de_li=%.4f", cor(R$de_p[ok], R$li_p[ok], method = "spearman")))
both3 <- sig(R$de_padj) & sig(R$li_padj) & sig(R$ed_padj)
s3 <- sum(sign(R$de_lfc[both3]) == sign(R$li_lfc[both3]) & sign(R$li_lfc[both3]) == sign(R$ed_lfc[both3]))
check("three_way_direction", s3 == sum(both3), sprintf("%d of %d genes significant in all three agree on direction", s3, sum(both3)))

# ---------------------------------------------------------------- the published biology
# Himes et al. 2014 (PLoS One 9:e99625, doi:10.1371/journal.pone.0099625), from this experiment:
# "316 differentially expressed genes, including both well known (DUSP1, KLF15, PER1, TSC22D3)
# and less investigated (C7, CCDC69, CRISPLD2) glucocorticoid-responsive genes", and
# "dexamethasone treatment significantly increased CRISPLD2 mRNA". The seven are asserted DE at
# FDR < 0.05 in all three methods; only CRISPLD2's direction is published, so only it is
# asserted up. (Himes used a different aligner and test; the genes, not the counts, transfer.)
himes <- c(DUSP1 = "ENSG00000120129", KLF15 = "ENSG00000163884", PER1 = "ENSG00000179094", TSC22D3 = "ENSG00000157514",
           C7 = "ENSG00000112936", CCDC69 = "ENSG00000198624", CRISPLD2 = "ENSG00000103196")
H <- R[match(himes, R$gene), ]
for (i in seq_along(himes)) rec("himes", names(himes)[i], himes[i], sprintf("de_lfc=%.3f de_padj=%.3g li_lfc=%.3f li_padj=%.3g ed_padj=%.3g",
                                 H$de_lfc[i], H$de_padj[i], H$li_lfc[i], H$li_padj[i], H$ed_padj[i]))
nh <- sum(sig(H$de_padj) & sig(H$li_padj) & sig(H$ed_padj))
check("himes_genes_de", nh == length(himes), sprintf("%d of %d named genes DE in all three", nh, length(himes)))
cr <- H[names(himes) == "CRISPLD2", ]
check("himes_crispld2_up", cr$de_lfc > 0 && cr$li_lfc > 0 && cr$ed_lfc > 0, sprintf("CRISPLD2 log2FC deseq2=%.3f limma=%.3f edger=%.3f", cr$de_lfc, cr$li_lfc, cr$ed_lfc))

out <- R; for (k in names(out)[-1]) out[[k]] <- sprintf("%.17g", out[[k]])
write.table(out, "de_three_methods.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
hwm <- tryCatch(sub("^VmHWM:\\s*", "", grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)), error = function(e) "unknown")
rec("peak_rss", hwm)
rec("time_s", "rscript_total", sprintf("%.2f", as.numeric(difftime(Sys.time(), t_start, units = "secs"))))
if (length(fail)) { rec("RESULT", "FAIL", paste(fail, collapse = ",")); quit(status = 1) }
rec("RESULT", "PASS", "limma-edger-compare")
