"""GW150914 as GWTC-2.1 analysed it, rebuilt from the published release (shared by probe.py and pe.py).

The release's own C01:IMRPhenomXPHM config is the spec: 4 s at 2048 Hz ending 2 s after the trigger,
20-896 Hz, the release's PSDs and analytic priors (40 spline-calibration parameters included), time,
distance and time-jitter marginalisation, the H1L1 sky frame, f_ref 20 Hz. The strain is GWOSC's
16 kHz O1 file (the C02 calibration GWTC-2.1 read), cropped and resampled with
lal.ResampleREAL8TimeSeries exactly as bilby_pipe 1.0.2 did.
"""
import h5py
import numpy as np

import bilby
import lal
import lalsimulation as lalsim
from gwpy.timeseries import TimeSeries

RUN = "C01:IMRPhenomXPHM"
TRIGGER = 1126259462.391
DURATION, POST, FS, FMIN, FMAX, FREF, ROLL_OFF = 4.0, 2.0, 2048.0, 20.0, 896.0, 20.0, 0.4
START = TRIGGER + POST - DURATION
# The release was made with lalsimulation 2.4.0. Three XPHM defaults have changed since; setting the
# 2.4.0 values back is what "the same waveform" means (probe.py measures both).
XPHM_2021 = {"PrecVersion": 223, "FinalSpinMod": 3, "XHMReleaseVersion": 122019}


def xphm_dict(flags):
    d = lal.CreateDict()
    if flags:
        lalsim.SimInspiralWaveformParamsInsertPhenomXPrecVersion(d, flags["PrecVersion"])
        lalsim.SimInspiralWaveformParamsInsertPhenomXPFinalSpinMod(d, flags["FinalSpinMod"])
        lalsim.SimInspiralWaveformParamsInsertPhenomXHMReleaseVersion(d, flags["XHMReleaseVersion"])
    return d


_FLAGS = {"v": XPHM_2021}


def source(frequency_array, mass_1, mass_2, luminosity_distance, a_1, tilt_1, phi_12, a_2, tilt_2,
           phi_jl, theta_jn, phase, **kwargs):
    """bilby's lal_binary_black_hole with the waveform flags set. A lal.Dict is built per call so
    nothing unpicklable lives on the waveform generator."""
    kwargs["lal_waveform_dictionary"] = xphm_dict(_FLAGS["v"])
    return bilby.gw.source.lal_binary_black_hole(
        frequency_array, mass_1, mass_2, luminosity_distance, a_1, tilt_1, phi_12, a_2, tilt_2,
        phi_jl, theta_jn, phase, **kwargs)


def release(path):
    f = h5py.File(path, "r")
    g = f[RUN]
    pri = {k: g["priors/analytic/" + k][0].decode() for k in g["priors/analytic"]}
    psds = {d: np.array(g["psds/" + d]) for d in ("H1", "L1")}
    post = g["posterior_samples"][()]
    return pri, psds, post


def priors(pri_strings):
    """The release's analytic prior strings; bilby resolves bare class names in bilby.core.prior
    only, so the gw-specific ones get their module."""
    d = {}
    for k, v in pri_strings.items():
        cls = v.split("(")[0]
        if not hasattr(bilby.core.prior, cls) and hasattr(bilby.gw.prior, cls):
            v = "bilby.gw.prior." + v
        d[k] = v
    return bilby.gw.prior.BBHPriorDict(dictionary=d)


def strain(det, path16k):
    """bilby_pipe 1.0.2 _get_data: read [START, START+DURATION), then lal-resample to FS."""
    ts = TimeSeries.read(path16k, format="hdf5.gwosc")
    ts = ts.crop(START, START + DURATION)
    if not np.all(np.isfinite(ts.value)):
        raise SystemExit("FAIL: non-finite strain in the analysis segment (%s)" % det)
    lt = ts.to_lal()
    lal.ResampleREAL8TimeSeries(lt, float(1 / FS))
    return TimeSeries(lt.data.data, epoch=lt.epoch, dt=lt.deltaT)


def interferometers(psds, files16k):
    ifos = bilby.gw.detector.InterferometerList([])
    for det in ("H1", "L1"):
        ifo = bilby.gw.detector.get_empty_interferometer(det)
        ifo.strain_data.roll_off = ROLL_OFF
        ifo.power_spectral_density = bilby.gw.detector.PowerSpectralDensity(
            frequency_array=psds[det][:, 0], psd_array=psds[det][:, 1])
        ifo.strain_data.set_from_gwpy_timeseries(strain(det, files16k[det]))
        ifo.minimum_frequency, ifo.maximum_frequency = FMIN, FMAX
        ifo.calibration_model = bilby.gw.calibration.CubicSpline(
            prefix="recalib_%s_" % det, minimum_frequency=FMIN, maximum_frequency=FMAX, n_points=10)
        ifos.append(ifo)
    return ifos


def _wfg(ifos):
    return bilby.gw.WaveformGenerator(
        duration=DURATION, sampling_frequency=FS, start_time=ifos.start_time,
        frequency_domain_source_model=source,
        parameter_conversion=bilby.gw.conversion.convert_to_lal_binary_black_hole_parameters,
        waveform_arguments=dict(waveform_approximant="IMRPhenomXPHM", reference_frequency=FREF,
                                minimum_frequency=FMIN, maximum_frequency=FMAX,
                                catch_waveform_errors=True, pn_spin_order=-1, pn_tidal_order=-1,
                                pn_phase_order=-1, pn_amplitude_order=1))


def likelihood_calmarg(ifos, prior, curves=1000, lookup="/tmp/work/TD.npz"):
    """The sampling likelihood: calibration marginalised over `curves` response curves drawn from
    the release's spline priors, distance marginalised, time sampled."""
    _FLAGS["v"] = XPHM_2021
    return bilby.gw.likelihood.GravitationalWaveTransient(
        interferometers=ifos, waveform_generator=_wfg(ifos), priors=prior,
        phase_marginalization=False, distance_marginalization=True,
        distance_marginalization_lookup_table=lookup, time_marginalization=False,
        calibration_marginalization=True, number_of_response_curves=curves,
        calibration_lookup_table={d: "/tmp/work/%s_calibration.h5" % d for d in ("H1", "L1")},
        reference_frame="H1L1", time_reference="geocent")


def likelihood(ifos, prior, flags=XPHM_2021, lookup="/tmp/work/TD.npz"):
    _FLAGS["v"] = flags
    wfg = bilby.gw.WaveformGenerator(
        duration=DURATION, sampling_frequency=FS, start_time=ifos.start_time,
        frequency_domain_source_model=source,
        parameter_conversion=bilby.gw.conversion.convert_to_lal_binary_black_hole_parameters,
        waveform_arguments=dict(waveform_approximant="IMRPhenomXPHM", reference_frequency=FREF,
                                minimum_frequency=FMIN, maximum_frequency=FMAX,
                                catch_waveform_errors=True, pn_spin_order=-1, pn_tidal_order=-1,
                                pn_phase_order=-1, pn_amplitude_order=1))
    return bilby.gw.likelihood.GravitationalWaveTransient(
        interferometers=ifos, waveform_generator=wfg, priors=prior,
        phase_marginalization=False, distance_marginalization=True,
        distance_marginalization_lookup_table=lookup, time_marginalization=True,
        jitter_time=True, reference_frame="H1L1", time_reference="geocent")
