"""An independent anti-k_t, written from the definition, to hold FastJet against.

    from brute import antikt
    jets = antikt(px, py, pz, E, R)     # list of (sorted tuple of input indices, px, py, pz, E)

Cacciari, Salam & Soyez (JHEP 0804:063): d_ij = min(kt_i^-2, kt_j^-2) dR_ij^2 / R^2, d_iB = kt_i^-2,
repeatedly take the smallest; merge i and j by adding 4-momenta (E-scheme), or retire i as a jet.
The full pairwise matrix is searched at every step -- no tiling, no nearest-neighbour geometry, none
of FastJet's machinery -- so the two agree only if both implement the definition. Rapidity and phi
follow the definitions FastJet documents for PseudoJet (rap = 0.5 ln((E+pz)/(E-pz)), phi in
[0, 2pi)), so that the same 4-vectors give the same distances.
"""
import numpy as np

TWO_PI = 2.0 * np.pi


def _rap_phi(px, py, pz, E):
    kt2 = px * px + py * py
    phi = np.arctan2(py, px) if kt2 > 0 else 0.0
    if phi < 0:
        phi += TWO_PI
    if phi >= TWO_PI:
        phi -= TWO_PI
    # rapidity through the transverse mass, as PseudoJet::_finish_init does
    m2 = (E + pz) * (E - pz) - kt2
    m2 = max(m2, 0.0)
    abs_pz = abs(pz)
    E_plus_pz = E + abs_pz
    rap = 0.5 * np.log((kt2 + m2) / (E_plus_pz * E_plus_pz)) if kt2 + m2 > 0 else 0.0
    if pz > 0:
        rap = -rap
    return kt2, rap, phi


def antikt(px, py, pz, E, R):
    n = len(px)
    P = np.stack([px, py, pz, E], axis=1).astype(np.float64)
    kt2 = np.empty(n); rap = np.empty(n); phi = np.empty(n)
    for i in range(n):
        kt2[i], rap[i], phi[i] = _rap_phi(*P[i])
    members = [[i] for i in range(n)]
    alive = np.ones(n, dtype=bool)
    invR2 = 1.0 / (R * R)
    jets = []
    while alive.any():
        idx = np.flatnonzero(alive)
        s = 1.0 / kt2[idx]                                  # kt^-2
        dy = rap[idx][:, None] - rap[idx][None, :]
        dp = np.abs(phi[idx][:, None] - phi[idx][None, :])
        dp = np.where(dp > np.pi, TWO_PI - dp, dp)
        d = np.minimum(s[:, None], s[None, :]) * (dy * dy + dp * dp) * invR2
        np.fill_diagonal(d, np.inf)
        k = int(np.argmin(d))
        a, b = divmod(k, len(idx))
        ib = int(np.argmin(s))
        if s[ib] <= d[a, b]:
            i = idx[ib]
            jets.append((tuple(sorted(members[i])),) + tuple(P[i]))
            alive[i] = False
            continue
        i, j = idx[a], idx[b]
        P[i] = P[i] + P[j]
        kt2[i], rap[i], phi[i] = _rap_phi(*P[i])
        members[i] += members[j]
        alive[j] = False
    return jets
