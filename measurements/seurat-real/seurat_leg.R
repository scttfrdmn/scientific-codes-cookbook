# Seurat leg of seurat-real: Seurat's standard workflow on exactly Scanpy's retained cells, genes
# and HVGs (recipes/seurat's matched design), then FindAllMarkers.
#   Rscript seurat_leg.R <contract dir> <out dir> <markers: 1|0>
suppressPackageStartupMessages({library(Seurat); library(Matrix)})
args <- commandArgs(trailingOnly = TRUE)
CON <- args[1]; OUT <- args[2]; DO_MARKERS <- args[3] == "1"
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
t0 <- Sys.time()
cat(sprintf("version\tSeurat %s\n", as.character(packageVersion("Seurat"))))
cat(sprintf("version\tR %s.%s\n", R.version$major, R.version$minor))

counts <- Read10X(data.dir = "filtered_feature_bc_matrix", gene.column = 1)
cat(sprintf("raw\t%d genes x %d cells\n", nrow(counts), ncol(counts)))
if (anyDuplicated(rownames(counts))) stop("gene IDs are not unique; the join key is unsound")
keep_cells <- readLines(file.path(CON, "keep_cells.txt"))
keep_genes <- readLines(file.path(CON, "keep_genes.txt"))
hvg <- readLines(file.path(CON, "hvg.txt"))
missing_g <- setdiff(keep_genes, rownames(counts)); missing_c <- setdiff(keep_cells, colnames(counts))
if (length(missing_g) || length(missing_c))
  stop(sprintf("%d genes and %d cells from Scanpy are absent from Seurat's matrix",
               length(missing_g), length(missing_c)))
counts <- counts[keep_genes, keep_cells]
cat(sprintf("subset\t%d genes x %d cells\n", nrow(counts), ncol(counts)))

# EXACT cross-tool identity: every cell's integer library size equals Scanpy's.
lib <- read.csv(file.path(CON, "libsize.csv"), colClasses = c("character", "numeric"))
mine <- colSums(counts)[lib$barcode]
nbad <- sum(mine != lib$lib)
cat(sprintf("libsize_identity\t%d of %d cells differ from Scanpy (total %.0f)\n",
            nbad, length(mine), sum(mine)))
if (nbad) stop("library sizes differ between Seurat and Scanpy")

so <- CreateSeuratObject(counts = counts, min.cells = 0, min.features = 0)
so <- NormalizeData(so, normalization.method = "LogNormalize", scale.factor = 1e4, verbose = FALSE)
# Normalisation identity: expm1(data) sums to scale.factor in every cell. Double precision, so the
# bound is round-trip error of log1p/expm1, not a band.
d <- LayerData(so, layer = "data"); d@x <- expm1(d@x)
dev <- max(abs(colSums(d) - 1e4)) / 1e4; rm(d)
cat(sprintf("norm_identity\tmax |sum-1e4|/1e4 = %.3e over %d cells\n", dev, ncol(so)))
if (!(dev < 1e-10)) stop("LogNormalize does not sum to 1e4 per cell")

so <- FindVariableFeatures(so, selection.method = "vst", nfeatures = 2000, verbose = FALSE)
cat(sprintf("hvg_overlap_vst_vs_scanpy\t%d of 2000\n", length(intersect(VariableFeatures(so), hvg))))
VariableFeatures(so) <- hvg
so <- ScaleData(so, features = hvg, verbose = FALSE)
so <- RunPCA(so, features = hvg, npcs = 40, verbose = FALSE, seed.use = 0)
so <- FindNeighbors(so, dims = 1:40, k.param = 10, verbose = FALSE)
so <- FindClusters(so, resolution = 1.0, verbose = FALSE, random.seed = 0)
cl <- Idents(so)
cat(sprintf("seurat_clusters\t%d\n", length(unique(cl))))
write.csv(data.frame(barcode = names(cl), seurat = as.character(cl)),
          file.path(OUT, "seurat_labels.csv"), row.names = FALSE)
cat(sprintf("wall_seurat_cluster_s\t%.1f\n", as.numeric(difftime(Sys.time(), t0, units = "secs"))))

if (DO_MARKERS) {
  t1 <- Sys.time()
  mk <- FindAllMarkers(so, only.pos = TRUE, verbose = FALSE)
  cat(sprintf("wall_findallmarkers_s\t%.1f\n", as.numeric(difftime(Sys.time(), t1, units = "secs"))))
  feats <- read.delim(gzfile("filtered_feature_bc_matrix/features.tsv.gz"), header = FALSE)
  sym <- setNames(feats$V2, feats$V1)
  mk$symbol <- sym[mk$gene]
  sig <- mk[mk$p_val_adj < 0.05, ]
  cat(sprintf("markers\t%d positive markers, %d at p_adj<0.05, %d of %d clusters have one\n",
              nrow(mk), nrow(sig), length(unique(sig$cluster)), nlevels(cl)))
  write.table(mk, file.path(OUT, "markers_all.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
  # Categorical biology: each lineage marker is a significant positive marker of the cluster in
  # which it peaks, and CD3D/LYZ/MS4A1/NKG7 peak in four distinct clusters.
  dat <- LayerData(so, layer = "data")
  peaks <- c()
  for (g in c("CD3D", "LYZ", "MS4A1", "NKG7")) {
    id <- names(sym)[sym == g & names(sym) %in% rownames(so)]
    if (length(id) != 1) stop(sprintf("marker %s maps to %d retained IDs", g, length(id)))
    m <- tapply(as.numeric(dat[id, ]), cl, mean)
    pk <- names(which.max(m)); peaks[g] <- pk
    ok <- any(sig$gene == id & as.character(sig$cluster) == pk)
    cat(sprintf("marker_peak\t%s peaks in cluster %s; significant marker there: %s\n", g, pk, ok))
    if (!ok) stop(sprintf("%s is not a significant marker of its peak cluster %s", g, pk))
  }
  if (length(unique(peaks)) != 4) stop("CD3D/LYZ/MS4A1/NKG7 do not peak in four distinct clusters")
  cat("biology\tT, monocyte, B and NK markers each mark their own cluster\n")
}
cat(sprintf("wall_seurat_s\t%.1f\n", as.numeric(difftime(Sys.time(), t0, units = "secs"))))
hwm <- grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)
cat(sprintf("peak_rss_seurat\t%s\n", gsub("\\s+", " ", sub("VmHWM:", "", hwm))))
cat("SEURAT OK\n")
