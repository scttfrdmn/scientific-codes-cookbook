import warnings
warnings.filterwarnings("ignore")
import numpy as np, scipy.sparse as sp, anndata as ad, scanpy as sc, squidpy as sq
from sklearn.preprocessing import normalize
from squidpy.gr._ppatterns import _score_helper
from squidpy._constants._constants import SpatialAutocorr
from scanpy.metrics import morans_i

genes = ["ENSG00000224568", "ENSG00000105383", "ENSG00000157856", "ENSG00000244219", "ENSG00000230448", "ENSG00000186648",
         "ENSG00000187258", "ENSG00000165828", "ENSG00000253901", "ENSG00000196566", "ENSG00000250436", "ENSG00000169432",
         "ENSG00000141255", "ENSG00000066923", "ENSG00000106211", "ENSG00000141458"]
a = ad.read_h5ad("/tmp/slideseq.h5ad")
counts = sp.csr_matrix(a.raw.X)
b = ad.AnnData(X=counts.astype(np.float64), obs=a.obs[[]].copy(), var=a.var.copy())
b.obsm["spatial"] = np.asarray(a.obsm["spatial"], dtype=np.float64)
sc.pp.normalize_total(b, target_sum=1e4)
sc.pp.log1p(b)
sq.gr.spatial_neighbors_knn(b, n_neighs=6, key_added="knn")
g = b.obsp["knn_connectivities"].copy()
normalize(g, norm="l1", axis=1, copy=False)
vals = b[:, genes].X.T
vals = sp.csr_matrix(vals)
score = morans_i(g, vals)
perms = list(range(100))
step = int(np.ceil(100 / 16))
chunks = [perms[i * step:(i + 1) * step] for i in range(int(np.ceil(100 / step)))]
sims = np.concatenate([_score_helper(ix, ch, SpatialAutocorr.MORAN, g, vals, seed=0) for ix, ch in enumerate(chunks)])
nnzg = np.diff(sp.csr_matrix(vals).indptr)
for k, gname in enumerate(genes):
    s = sims[:, k]
    ex = int((s == score[k]).sum())
    near = int((np.abs(s - score[k]) <= 1e-14 * abs(score[k])).sum())
    print(gname, "beads expressing", int(nnzg[k]), "I %.17g" % score[k], "distinct perm values", len(np.unique(s)),
          "perms == I exactly", ex, "within 1e-14 rel", near, ">= I", int((s >= score[k]).sum()), flush=True)
print("TIES OK")
