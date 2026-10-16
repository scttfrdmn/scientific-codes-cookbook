#!/usr/bin/env python3
"""Gravitational-wave inference on Graviton4: LALSuite, PyCBC and Bilby against planted truth.

The headline is a planted signal recovered by the science, not an import:

 1. PYCBC MATCHED FILTERING finds a planted BBH chirp. An IMRPhenomD signal with known
    masses is added at a known sample to Gaussian noise coloured by a known PSD (fixed seed).
    The SNR peak must land on the planted sample (within one), and its height must agree
    with the optimal SNR sqrt(<h|h>) computed from the PSD: in Gaussian noise the real part
    of the matched-filter output at the true parameters is rho_opt + N(0, 1), so a 4-sigma
    bound is derived from the noise statistics, not from the observed value. Control: the
    same filter on the noise alone, where |rho| over every sample stays below 8 (the 2-dof
    chi-square tail puts P(|rho| > 8) at e^-32 per sample), and the mean of |rho|^2 over
    noise is 2 -- the normalisation of the whole filter in one number.

 2. BILBY PARAMETER ESTIMATION on a small problem (H1+L1, 4 s, chirp mass and mass ratio
    and distance free, the rest fixed; dynesty with an explicit seeded generator, one process,
    byte-identical across runs) must put the injected chirp mass inside its 99% credible interval, and that interval must be narrow enough to mean
    something: a chirp mass 2% off must fall outside it. Control: the same run on noise
    alone must find no signal (ln Bayes factor < 5, i.e. under 150:1 -- in noise the evidence
    ratio is O(1) because the likelihood ratio -> 1 as the distance prior's far end makes any
    template vanish), while the injection's ln BF must exceed rho^2/4: the maximum
    log-likelihood ratio is ~rho^2/2 and the Occam factor of three parameters is tens, not
    hundreds.

 3. EXACT IDENTITIES: LAL's solar-mass constants are one definition (GM_sun) rounded three
    ways; chirp mass has a closed form and PyCBC/Bilby must invert it; a Bilby prior's
    cdf(rescale(u)) == u; a waveform's self-match is 1 and invariant to amplitude; and
    Bilby's log-likelihood ratio at the injection equals <d|h> - <h|h>/2 written out.

 4. CROSS-CODE on the same bytes: one IMRPhenomD waveform generated through LALSimulation
    directly, through PyCBC and through Bilby must agree (they call the same C code, so
    this tests each wrapper's units and conventions); and the optimal SNR of one waveform
    against one PSD must agree between PyCBC's sigmasq, Bilby's inner product and the
    definition 4 df sum |h|^2 / S summed in numpy.

A pinned digest does not pin the numerics, so no assertion is on a sampler's path; the
sampler checks are coverage and exclusion, which a correct sampler satisfies for any seed
with the stated probability.

Run with `python3 -u` so a killed run still has its output in command.log.
"""

import sys
import time

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    write_score()
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="complex128"))):
            die("%s is not finite" % name)


def write_score():
    with open("score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k_, v in ROWS:
            fh.write("%s\t%s\n" % (k_, v))


EPS = np.finfo("float64").eps
T0 = time.time()

# A bare `import pycbc` succeeds even when its submodules are broken -- import each one used.
import lal
import lalsimulation as lalsim
import pycbc
import pycbc.waveform
import pycbc.filter
import pycbc.psd
import pycbc.noise
import pycbc.conversions
import pycbc.cosmology
from pycbc.types import FrequencySeries, TimeSeries
import bilby
import bilby.gw.utils as gwutils

ver("bilby", lambda: bilby.__version__)
ver("lal", lambda: lal.__version__)
ver("lalsimulation", lambda: lalsim.__version__)
ver("pycbc", lambda: pycbc.__version__)
ver("dynesty", lambda: __import__("dynesty").__version__)
ver("numpy", lambda: np.__version__)

SEED = 20261016
bilby.core.utils.random.seed(SEED)
bilby.core.utils.logger.setLevel("WARNING")

# ======================================= 3a. LAL constants: one definition, rounded three ways
# LAL defines GMSUN_SI (measured far better than G or M_sun separately); MSUN_SI = GMSUN/G,
# MTSUN_SI = GMSUN/c^3, MRSUN_SI = GMSUN/c^2. Each is a decimal literal rounded to double, so
# the relations hold to rounding -- a few ulps -- and a wrong constant misses by orders more.
rels = {
    "MTSUN_SI vs MSUN*G/c^3": (lal.MTSUN_SI, lal.MSUN_SI * lal.G_SI / lal.C_SI ** 3),
    "MTSUN_SI vs GMSUN/c^3": (lal.MTSUN_SI, lal.GMSUN_SI / lal.C_SI ** 3),
    "MRSUN_SI vs GMSUN/c^2": (lal.MRSUN_SI, lal.GMSUN_SI / lal.C_SI ** 2),
    "GMSUN_SI vs MSUN*G": (lal.GMSUN_SI, lal.MSUN_SI * lal.G_SI),
}
worst = 0.0
for name, (a, b) in rels.items():
    finite(name, a, b)
    r = abs(a - b) / abs(a) / EPS
    worst = max(worst, r)
    rec("lal_const " + name, "%.2f ulp" % r)
if worst > 4.0:
    die("a LAL solar-mass constant is inconsistent with GMSUN_SI by %.1f ulp" % worst)

# ======================================= 3b. chirp mass: closed form, both inversions
m1s = np.array([36.0, 10.0, 1.4, 80.0, 5.3])
m2s = np.array([29.0, 8.0, 1.3, 12.0, 1.1])
mc_closed = (m1s * m2s) ** 0.6 / (m1s + m2s) ** 0.2
mc_pycbc = pycbc.conversions.mchirp_from_mass1_mass2(m1s, m2s)
mc_bilby = bilby.gw.conversion.component_masses_to_chirp_mass(m1s, m2s)
q = m2s / m1s
b1, b2 = bilby.gw.conversion.chirp_mass_and_mass_ratio_to_component_masses(mc_closed, q)
p1 = pycbc.conversions.mass1_from_mchirp_q(mc_closed, 1.0 / q)
p2 = pycbc.conversions.mass2_from_mchirp_q(mc_closed, 1.0 / q)
finite("chirp masses", mc_pycbc, mc_bilby, b1, b2, p1, p2)
d_fwd = float(max(np.max(np.abs(mc_pycbc / mc_closed - 1)), np.max(np.abs(mc_bilby / mc_closed - 1))))
d_inv = float(max(np.max(np.abs(b1 / m1s - 1)), np.max(np.abs(b2 / m2s - 1)),
                  np.max(np.abs(p1 / m1s - 1)), np.max(np.abs(p2 / m2s - 1))))
rec("chirp_mass_closed_form", "PyCBC and Bilby vs (m1 m2)^(3/5)/(m1+m2)^(1/5): max rel %.3e" % d_fwd)
rec("chirp_mass_round_trip", "(Mc, q) -> (m1, m2), Bilby and PyCBC: max rel %.3e" % d_inv)
if d_fwd > 1e-13 or d_inv > 1e-12:
    die("chirp mass conversion is not its closed form (%.3e) or does not invert (%.3e)" % (d_fwd, d_inv))

# ======================================= 3b'. distance -> redshift: PyCBC, Bilby, astropy
# Both default to Planck15 and invert astropy's D_L(z): Bilby by root-finding, PyCBC through an
# interpolant on a grid, so the round trip holds to interpolation error. 1e-5 is far inside
# any real cosmology mistake (H0 = 70 instead of 67.74 is 3%).
from astropy.cosmology import Planck15
d_l = np.array([10.0, 100.0, 460.0, 1000.0, 1500.0, 5000.0])
z_p = np.asarray(pycbc.cosmology.redshift(d_l), dtype="float64")
z_b = np.asarray(bilby.gw.conversion.luminosity_distance_to_redshift(d_l), dtype="float64")
finite("redshifts", z_p, z_b)
rt_p = float(np.max(np.abs(Planck15.luminosity_distance(z_p).value / d_l - 1)))
rt_b = float(np.max(np.abs(Planck15.luminosity_distance(z_b).value / d_l - 1)))
rec("cosmology_round_trip", "D_L(z(D_L)) / D_L - 1 over 10-5000 Mpc: PyCBC %.3e, Bilby %.3e" % (rt_p, rt_b))
if rt_p > 1e-5 or rt_b > 1e-5:
    die("distance -> redshift does not invert Planck15 D_L(z): PyCBC %.3e, Bilby %.3e" % (rt_p, rt_b))

# ======================================= 3c. Bilby priors: cdf(rescale(u)) == u
u = np.linspace(0.001, 0.999, 999)
priors_rt = {
    "Uniform": bilby.core.prior.Uniform(20, 40),
    "PowerLaw(2)": bilby.core.prior.PowerLaw(alpha=2, minimum=100, maximum=2000),
    "Sine": bilby.core.prior.Sine(),
    "Gaussian": bilby.core.prior.Gaussian(mu=1.0, sigma=0.3),
    "UniformInComponentsChirpMass": bilby.gw.prior.UniformInComponentsChirpMass(20, 40),
}
worst = 0.0
for name, pr in priors_rt.items():
    back = np.asarray(pr.cdf(pr.rescale(u)), dtype="float64")
    finite("prior round trip " + name, back)
    worst = max(worst, float(np.max(np.abs(back - u))))
rec("prior_cdf_rescale_round_trip", "%d priors x 999 points: max |cdf(rescale(u)) - u| %.3e"
    % (len(priors_rt), worst))
if worst > 1e-9:
    die("a Bilby prior's cdf does not invert its rescale (%.3e)" % worst)

# ======================================= 4a. one waveform, three wrappers of LALSimulation
APX = "IMRPhenomD"
F_LOW, DURATION, FS = 20.0, 4.0, 2048.0
DF = 1.0 / DURATION
M1, M2, DL = 36.0, 29.0, 1000.0           # Msun, Msun, Mpc
INC, PHI = 0.4, 1.3
flen = int(FS / 2 / DF) + 1

lp = lal.CreateDict()
hp_l, hc_l = lalsim.SimInspiralChooseFDWaveform(
    M1 * lal.MSUN_SI, M2 * lal.MSUN_SI, 0, 0, 0, 0, 0, 0,
    DL * 1e6 * lal.PC_SI, INC, PHI, 0, 0, 0, DF, F_LOW, FS / 2, F_LOW, lp,
    lalsim.GetApproximantFromString(APX))
hp_lal = hp_l.data.data[:flen].copy()
hp_pc, hc_pc = pycbc.waveform.get_fd_waveform(
    approximant=APX, mass1=M1, mass2=M2, distance=DL, inclination=INC, coa_phase=PHI,
    delta_f=DF, f_lower=F_LOW, f_final=FS / 2)
hp_pycbc = np.asarray(hp_pc.numpy()[:flen])
wfg = bilby.gw.WaveformGenerator(
    duration=DURATION, sampling_frequency=FS,
    frequency_domain_source_model=bilby.gw.source.lal_binary_black_hole,
    parameter_conversion=bilby.gw.conversion.convert_to_lal_binary_black_hole_parameters,
    waveform_arguments=dict(waveform_approximant=APX, reference_frequency=F_LOW,
                            minimum_frequency=F_LOW))
pol = wfg.frequency_domain_strain(dict(
    mass_1=M1, mass_2=M2, a_1=0.0, a_2=0.0, tilt_1=0.0, tilt_2=0.0, phi_12=0.0, phi_jl=0.0,
    luminosity_distance=DL, theta_jn=INC, phase=PHI))
hp_bilby = np.asarray(pol["plus"])
finite("waveforms", hp_lal, hp_pycbc, hp_bilby)
band = (np.arange(flen) * DF >= F_LOW) & (np.arange(flen) * DF < FS / 2)
scale = float(np.max(np.abs(hp_lal[band])))
d_pc = float(np.max(np.abs(hp_pycbc[band] - hp_lal[band])) / scale)
d_bi = float(np.max(np.abs(hp_bilby[band] - hp_lal[band])) / scale)
rec("waveform_pycbc_vs_lalsim", "%s h+ %d bins: max |diff| / max |h| = %.3e" % (APX, band.sum(), d_pc))
rec("waveform_bilby_vs_lalsim", "%s h+ %d bins: max |diff| / max |h| = %.3e" % (APX, band.sum(), d_bi))
if not scale > 0:
    die("LALSimulation returned an empty waveform")
if d_pc > 1e-10 or d_bi > 1e-10:
    die("a wrapper of LALSimulation does not reproduce it: PyCBC %.3e, Bilby %.3e" % (d_pc, d_bi))

# ======================================= 4b. optimal SNR: PyCBC vs Bilby vs the definition
psd = pycbc.psd.aLIGOZeroDetHighPower(flen, DF, F_LOW)
psd_np = np.asarray(psd.numpy())
h_band = np.where(band, hp_lal, 0.0)       # zero outside [f_low, Nyquist): masks cannot differ
w = band & (psd_np > 0)
s2_def = float(4.0 * DF * np.sum(np.abs(h_band[w]) ** 2 / psd_np[w]))
s2_pycbc = float(pycbc.filter.sigmasq(FrequencySeries(h_band, delta_f=DF), psd,
                                      low_frequency_cutoff=F_LOW))
psd_safe = np.where(w, psd_np, np.inf)
s2_bilby = float(np.real(gwutils.noise_weighted_inner_product(h_band, h_band, psd_safe, DURATION)))
finite("sigma^2", s2_def, s2_pycbc, s2_bilby)
d_s = max(abs(s2_pycbc / s2_def - 1), abs(s2_bilby / s2_def - 1))
rec("optimal_snr", "4 df sum |h|^2/S: sqrt = %.6f; PyCBC sigmasq and Bilby inner product max rel %.3e"
    % (np.sqrt(s2_def), d_s))
if d_s > 1e-12:
    die("optimal SNR disagrees between PyCBC, Bilby and the definition (%.3e)" % d_s)

# ======================================= 3d. match normalisation and amplitude invariance
hfs = FrequencySeries(h_band, delta_f=DF)
m_self, _ = pycbc.filter.match(hfs, hfs, psd=psd, low_frequency_cutoff=F_LOW)
m_scaled, _ = pycbc.filter.match(hfs, FrequencySeries(3.7e3 * h_band, delta_f=DF), psd=psd,
                                 low_frequency_cutoff=F_LOW)
hp_o, _ = pycbc.waveform.get_fd_waveform(approximant=APX, mass1=40.0, mass2=29.0, distance=DL,
                                         inclination=INC, coa_phase=PHI, delta_f=DF,
                                         f_lower=F_LOW, f_final=FS / 2)
hp_o.resize(flen)
m_other, _ = pycbc.filter.match(hfs, hp_o, psd=psd, low_frequency_cutoff=F_LOW)
finite("matches", m_self, m_scaled, m_other)
rec("match_self", "%.17g (|1 - m| = %.3e)" % (m_self, abs(1 - m_self)))
rec("match_amplitude_invariance", "match(h, 3700 h) - match(h, h) = %.3e" % (m_scaled - m_self))
rec("match_control_m1_40", "match vs a 40+29 template: %.6f" % m_other)
# Self-match measures one ulp ABOVE 1 on correct code, so the bound is two-sided in ulps.
if abs(1 - m_self) > 1e-12 or abs(m_scaled - m_self) > 1e-12:
    die("match is not normalised (self %.17g) or not amplitude-invariant" % m_self)
if not m_other < 0.99:
    die("a 40+29 template matches a 36+29 signal at %.6f; the match cannot discriminate" % m_other)

# ======================================= 1. PyCBC matched filtering finds a planted chirp
FS_MF = 4096.0
DT = 1.0 / FS_MF
SEG = 64.0                                   # s of data
N = int(SEG * FS_MF)
DF_MF = 1.0 / SEG
psd_mf = pycbc.psd.aLIGOZeroDetHighPower(N // 2 + 1, DF_MF, F_LOW)
noise = pycbc.noise.noise_from_psd(N, DT, psd_mf, seed=SEED)
DL_MF = 1500.0
hp_t, _ = pycbc.waveform.get_td_waveform(approximant=APX, mass1=M1, mass2=M2, distance=DL_MF,
                                         inclination=0.0, coa_phase=0.0, delta_t=DT,
                                         f_lower=F_LOW)
i_merge = -int(round(float(hp_t.start_time) / DT))     # sample of t=0 within hp_t
I_PLANT = int(40.0 * FS_MF)                             # merger planted at t = 40 s
sig = np.zeros(N)
s0 = I_PLANT - i_merge
sig[s0:s0 + len(hp_t)] = hp_t.numpy()
data = TimeSeries(noise.numpy() + sig, delta_t=DT)
# Template: the same waveform, cyclically shifted so its merger is at sample 0.
tmpl = hp_t.copy()
tmpl.resize(N)
tmpl = tmpl.cyclic_time_shift(tmpl.start_time)
rho_opt = float(np.sqrt(pycbc.filter.sigmasq(TimeSeries(sig, delta_t=DT), psd_mf,
                                            low_frequency_cutoff=F_LOW)))
snr = pycbc.filter.matched_filter(tmpl, data, psd=psd_mf, low_frequency_cutoff=F_LOW)
snr_n = pycbc.filter.matched_filter(tmpl, TimeSeries(noise.numpy(), delta_t=DT), psd=psd_mf,
                                    low_frequency_cutoff=F_LOW)
crop = int(8 * FS_MF)                        # drop wrap-around edges
a = np.abs(snr.numpy())
an = np.abs(snr_n.numpy())[crop:N - crop]
finite("SNR series", a, an, rho_opt)
i_peak = int(np.argmax(a))
rho_peak = float(a[i_peak])
rec("mf_planted_sample", "%d (merger at t = %.4f s)" % (I_PLANT, I_PLANT * DT))
rec("mf_peak_sample", "%d (offset %d samples)" % (i_peak, i_peak - I_PLANT))
rec("mf_rho_opt", "%.4f (from the PSD)" % rho_opt)
rec("mf_rho_peak", "%.4f (peak - opt = %+.4f)" % (rho_peak, rho_peak - rho_opt))
rec("mf_noise_only_max", "%.4f over %d samples" % (float(np.max(an)), an.size))
rec("mf_noise_mean_rho2", "%.4f (expect 2 for a unit-normalised complex filter)" % float(np.mean(an ** 2)))
if abs(i_peak - I_PLANT) > 1:
    die("the SNR peak is at sample %d, not the planted %d" % (i_peak, I_PLANT))
if abs(rho_peak - rho_opt) > 4.0:
    die("peak SNR %.3f is not rho_opt %.3f + N(0,1) within 4 sigma" % (rho_peak, rho_opt))
if not rho_opt > 15:
    die("the planted signal is too weak (rho_opt %.2f) for the check to mean anything" % rho_opt)
if not float(np.max(an)) < 8.0:
    die("noise alone produced |rho| = %.3f >= 8" % float(np.max(an)))
if not 1.8 < float(np.mean(an ** 2)) < 2.2:
    die("mean |rho|^2 over noise is %.4f, not 2: the filter is mis-normalised" % float(np.mean(an ** 2)))
rec("t_matched_filter_s", "%.1f" % (time.time() - T0))

# ======================================= 2. Bilby parameter estimation, small problem
GPS = 1126259642.413
inj = dict(chirp_mass=float(pycbc.conversions.mchirp_from_mass1_mass2(M1, M2)),
           mass_ratio=M2 / M1, a_1=0.0, a_2=0.0, tilt_1=0.0, tilt_2=0.0, phi_12=0.0, phi_jl=0.0,
           luminosity_distance=DL, theta_jn=INC, psi=2.659, phase=PHI, geocent_time=GPS,
           ra=1.375, dec=-1.2108)


def make_ifos(with_signal):
    ifos = bilby.gw.detector.InterferometerList(["H1", "L1"])
    for ifo in ifos:
        ifo.minimum_frequency = F_LOW
    ifos.set_strain_data_from_power_spectral_densities(
        sampling_frequency=FS, duration=DURATION, start_time=GPS - DURATION + 2)
    if with_signal:
        ifos.inject_signal(waveform_generator=wfg, parameters=inj)
    return ifos


def make_priors():
    pri = bilby.gw.prior.BBHPriorDict()
    for k in ("a_1", "a_2", "tilt_1", "tilt_2", "phi_12", "phi_jl", "theta_jn", "psi", "phase", "geocent_time", "ra", "dec"):
        pri[k] = inj[k]
    for k in ("mass_1", "mass_2"):
        pri.pop(k, None)
    pri["chirp_mass"] = bilby.core.prior.Uniform(25.0, 31.0, name="chirp_mass")
    pri["mass_ratio"] = bilby.core.prior.Uniform(0.5, 1.0, name="mass_ratio")
    pri["luminosity_distance"] = bilby.core.prior.PowerLaw(alpha=2, minimum=100.0, maximum=5000.0,
                                                           name="luminosity_distance")
    return pri


ifos = make_ifos(True)
net_snr = float(np.sqrt(sum(ifo.meta_data["optimal_SNR"] ** 2 for ifo in ifos)))
rec("pe_network_optimal_snr", "%.4f (H1+L1, %g s at %g Hz)" % (net_snr, DURATION, FS))
like = bilby.gw.likelihood.GravitationalWaveTransient(interferometers=ifos, waveform_generator=wfg)

# 3e. the likelihood is the inner products it claims: ln L/L_n = sum Re<d|h> - <h|h>/2
p_inj = dict(inj)
p_inj.update(bilby.gw.conversion.generate_mass_parameters(dict(chirp_mass=inj["chirp_mass"],
                                                              mass_ratio=inj["mass_ratio"])))
llr_b = float(like.log_likelihood_ratio(parameters=dict(p_inj)))
pols = wfg.frequency_domain_strain(dict(p_inj))
llr_d = 0.0
for ifo in ifos:
    resp = ifo.get_detector_response(pols, p_inj)
    m = ifo.frequency_mask
    psd_i = ifo.power_spectral_density_array[m]
    dh = gwutils.noise_weighted_inner_product(ifo.frequency_domain_strain[m], resp[m], psd_i, DURATION)
    hh = gwutils.noise_weighted_inner_product(resp[m], resp[m], psd_i, DURATION)
    llr_d += float(np.real(dh)) - 0.5 * float(np.real(hh))
finite("log likelihood ratios", llr_b, llr_d)
d_llr = abs(llr_b - llr_d) / abs(llr_d)
rec("pe_llr_at_injection", "Bilby %.6f vs <d|h> - <h|h>/2 written out %.6f: rel %.3e"
    % (llr_b, llr_d, d_llr))
if d_llr > 1e-10:
    die("Bilby's log-likelihood ratio is not its inner products (%.3e)" % d_llr)

# Bilby's `seed=` is silently dropped when its defaults already carry an `rstate` key ("Kwargs
# contain both 'rstate' and 'seed', ignoring 'seed'"), so dynesty runs unseeded and two runs
# differ (measured). Passing the generator makes them byte-identical. Checkpointing is off
# because Bilby sizes its run chunks from a TIMED likelihood call; at this size it is one chunk
# either way, but a longer run would split differently on a different box.
SAMPLER = dict(sampler="dynesty", nlive=200, dlogz=0.1, sample="rwalk", npool=1, resume=False,
               check_point=False, plot=False, save=True)


def sampler_kwargs():
    bilby.core.utils.random.seed(SEED)
    return dict(rstate=np.random.default_rng(SEED), **SAMPLER)


res = bilby.run_sampler(likelihood=like, priors=make_priors(), outdir="outdir", label="inj",
                        injection_parameters=dict(p_inj),
                        conversion_function=bilby.gw.conversion.generate_all_bbh_parameters,
                        **sampler_kwargs())
mc = np.asarray(res.posterior["chirp_mass"], dtype="float64")
finite("chirp mass posterior", mc)
lo, med, hi = (float(x) for x in np.quantile(mc, [0.005, 0.5, 0.995]))
q_inj = float(np.mean(mc < inj["chirp_mass"]))
rec("pe_posterior_samples", "%d" % mc.size)
rec("pe_chirp_mass_99ci", "[%.4f, %.4f], median %.4f; injected %.4f at posterior quantile %.3f"
    % (lo, hi, med, inj["chirp_mass"], q_inj))
rec("pe_ln_bayes_factor_injection", "%.3f" % float(res.log_bayes_factor))
off = 1.02 * inj["chirp_mass"]
rec("pe_control_mc_plus_2pct", "%.4f must be outside the 99%% CI" % off)
if mc.size < 500:
    die("only %d posterior samples" % mc.size)
if not lo <= inj["chirp_mass"] <= hi:
    die("injected chirp mass %.4f is outside the 99%% CI [%.4f, %.4f]" % (inj["chirp_mass"], lo, hi))
if lo <= off <= hi:
    die("a chirp mass 2%% off (%.4f) is inside the 99%% CI; the posterior is too broad to test" % off)
if not float(res.log_bayes_factor) > net_snr ** 2 / 4:
    die("ln BF %.3f for an SNR-%.1f injection; expect ~rho^2/2 less an Occam factor"
        % (float(res.log_bayes_factor), net_snr))
rec("t_pe_injection_s", "%.1f" % (time.time() - T0))

ifos_n = make_ifos(False)
like_n = bilby.gw.likelihood.GravitationalWaveTransient(interferometers=ifos_n, waveform_generator=wfg)
res_n = bilby.run_sampler(likelihood=like_n, priors=make_priors(), outdir="outdir", label="noise",
                          **sampler_kwargs())
lnbf_n = float(res_n.log_bayes_factor)
finite("noise-only ln BF", lnbf_n)
rec("pe_control_noise_ln_bayes_factor", "%.3f" % lnbf_n)
if not lnbf_n < 5.0:
    die("noise alone gave ln BF %.3f >= 5: the sampler finds signals that are not there" % lnbf_n)
rec("t_total_s", "%.1f" % (time.time() - T0))
ver("peak_rss_mib", lambda: "%.0f" % (__import__("resource").getrusage(
    __import__("resource").RUSAGE_SELF).ru_maxrss / 1024.0))

write_score()
print("GW IDENTITIES OK")
