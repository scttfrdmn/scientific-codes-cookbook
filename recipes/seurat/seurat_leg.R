suppressPackageStartupMessages({library(Seurat); library(Matrix)})
cat(sprintf("version\tSeurat %s\n", as.character(packageVersion("Seurat"))))
cat(sprintf("version\tR %s.%s\n", R.version$major, R.version$minor))

# gene.column = 1 makes the rownames Ensembl IDs, matching the key Scanpy wrote. The default
# (column 2, symbols) would need make.unique() and would then disagree with python's "-1"
# suffixes on the 91 duplicated symbols in this reference.
counts <- Read10X(data.dir = "filtered_gene_bc_matrices/hg19", gene.column = 1)
cat(sprintf("raw\t%d genes x %d cells\n", nrow(counts), ncol(counts)))

keep_cells <- readLines("keep_cells.txt")
keep_genes <- readLines("keep_genes.txt")
hvg <- readLines("hvg.txt")

# Subset to EXACTLY Scanpy's retained sets. Seurat's own min.cells/min.features would give a
# slightly different result because the filters are applied differently; this recipe is about
# clustering, so the upstream is made identical rather than compared.
if (anyDuplicated(rownames(counts))) stop("gene IDs are not unique; the join key is unsound")
missing_g <- setdiff(keep_genes, rownames(counts))
missing_c <- setdiff(keep_cells, colnames(counts))
if (length(missing_g) || length(missing_c))
  stop(sprintf("%d genes and %d cells from Scanpy are absent from Seurat's matrix",
               length(missing_g), length(missing_c)))
counts <- counts[keep_genes, keep_cells]
cat(sprintf("subset\t%d genes x %d cells\n", nrow(counts), ncol(counts)))

so <- CreateSeuratObject(counts = counts, min.cells = 0, min.features = 0)
so <- NormalizeData(so, normalization.method = "LogNormalize", scale.factor = 1e4, verbose = FALSE)
VariableFeatures(so) <- hvg
so <- ScaleData(so, features = hvg, verbose = FALSE)
so <- RunPCA(so, features = hvg, npcs = 40, verbose = FALSE, seed.use = 0)
so <- FindNeighbors(so, dims = 1:40, k.param = 10, verbose = FALSE)
so <- FindClusters(so, resolution = 1.0, verbose = FALSE, random.seed = 0)

cl <- Idents(so)
cat(sprintf("seurat_clusters\t%d\n", length(unique(cl))))
write.csv(data.frame(barcode = names(cl), seurat = as.character(cl)),
          "seurat_labels.csv", row.names = FALSE)
cat("SEURAT OK\n")
