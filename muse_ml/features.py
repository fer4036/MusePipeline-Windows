"""Feature extraction for EEG/PPG windows used in SCEM modeling."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy import signal


EEG_BANDS = {
    'delta': (1.0, 4.0),
    'theta': (4.0, 8.0),
    'alpha': (8.0, 13.0),
    'beta': (13.0, 30.0),
    'gamma_low': (30.0, 45.0),
}
EEG_CHANNELS = ('channel_1', 'channel_2', 'channel_3', 'channel_4')
PPG_CHANNELS = tuple(f'channel_{index}' for index in range(1, 17))
EPSILON = 1e-12


@dataclass(frozen=True)
class WindowFeatures:
    """Feature rows and quality metadata for one model window."""

    features: dict[str, float]
    quality: dict[str, float]


def robust_sampling_rate(timestamps: np.ndarray, fallback: float) -> float:
    """Estimate sampling rate from timestamps using the median interval."""

    timestamps = np.asarray(timestamps, dtype=float)
    timestamps = timestamps[np.isfinite(timestamps)]
    if timestamps.size < 3:
        return float(fallback)
    diffs = np.diff(np.sort(timestamps))
    diffs = diffs[(diffs > 0) & np.isfinite(diffs)]
    if diffs.size == 0:
        return float(fallback)
    median = float(np.median(diffs))
    if median <= 0:
        return float(fallback)
    return float(1.0 / median)


def _finite_signal(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    finite = np.isfinite(values)
    if finite.all():
        return values
    if not finite.any():
        return np.zeros_like(values, dtype=float)
    indexes = np.arange(values.size)
    return np.interp(indexes, indexes[finite], values[finite])


def _bandpass(values: np.ndarray, fs: float, low: float, high: float) -> np.ndarray:
    nyquist = fs / 2.0
    high = min(high, nyquist * 0.95)
    if low <= 0 or high <= low or fs <= 0:
        return values
    sos = signal.butter(4, [low, high], btype='bandpass', fs=fs, output='sos')
    if values.size < sos.shape[0] * 12:
        return values
    return signal.sosfiltfilt(sos, values)


def _notch(values: np.ndarray, fs: float, frequency: float = 60.0) -> np.ndarray:
    if fs <= frequency * 2.2 or values.size < int(fs):
        return values
    b, a = signal.iirnotch(frequency, 30.0, fs=fs)
    return signal.filtfilt(b, a, values)


def _integrate_band(freqs: np.ndarray, psd: np.ndarray, low: float, high: float) -> float:
    mask = (freqs >= low) & (freqs < high)
    if not mask.any():
        return 0.0
    return float(np.trapezoid(psd[mask], freqs[mask]))


def eeg_band_features(
    eeg_values: dict[str, np.ndarray],
    timestamps: np.ndarray,
    fallback_fs: float = 256.0,
) -> WindowFeatures:
    """Compute EEG power, relative power, ratios, and quality features."""

    fs = robust_sampling_rate(timestamps, fallback_fs)
    features: dict[str, float] = {'eeg_fs_estimated': fs}
    quality: dict[str, float] = {}
    channel_band_powers: dict[str, dict[str, float]] = {}

    for channel in EEG_CHANNELS:
        raw = _finite_signal(eeg_values.get(channel, np.array([], dtype=float)))
        valid_ratio = float(np.isfinite(eeg_values.get(channel, [])).mean()) if raw.size else 0.0
        quality[f'eeg_{channel}_valid_ratio'] = valid_ratio
        if raw.size < max(64, int(fs * 2)):
            continue
        clean = _notch(_bandpass(raw, fs, 1.0, 45.0), fs)
        clean = clean - float(np.mean(clean))
        nperseg = min(int(round(fs * 2.0)), clean.size)
        if nperseg < 64:
            continue
        freqs, psd = signal.welch(
            clean,
            fs=fs,
            window='hann',
            nperseg=nperseg,
            noverlap=nperseg // 2,
            detrend='constant',
            scaling='density',
        )
        powers = {
            band: _integrate_band(freqs, psd, low, high)
            for band, (low, high) in EEG_BANDS.items()
        }
        total = sum(powers.values())
        channel_band_powers[channel] = powers
        features[f'eeg_{channel}_total_power'] = math.log(total + EPSILON)
        features[f'eeg_{channel}_rms'] = float(np.sqrt(np.mean(clean ** 2)))
        features[f'eeg_{channel}_variance'] = float(np.var(clean))
        probabilities = psd / (float(np.sum(psd)) + EPSILON)
        entropy = -float(np.sum(probabilities * np.log(probabilities + EPSILON)))
        features[f'eeg_{channel}_spectral_entropy'] = entropy
        for band, power in powers.items():
            features[f'eeg_{channel}_abs_{band}'] = math.log(power + EPSILON)
            features[f'eeg_{channel}_rel_{band}'] = power / (total + EPSILON)
        features[f'eeg_{channel}_log_theta_alpha'] = math.log(
            (powers['theta'] + EPSILON) / (powers['alpha'] + EPSILON)
        )
        features[f'eeg_{channel}_log_beta_alpha'] = math.log(
            (powers['beta'] + EPSILON) / (powers['alpha'] + EPSILON)
        )
        features[f'eeg_{channel}_log_theta_beta'] = math.log(
            (powers['theta'] + EPSILON) / (powers['beta'] + EPSILON)
        )
        features[f'eeg_{channel}_log_beta_alpha_theta'] = math.log(
            (powers['beta'] + EPSILON) /
            (powers['alpha'] + powers['theta'] + EPSILON)
        )

    present_channels = [
        channel for channel in EEG_CHANNELS
        if channel in channel_band_powers
    ]
    for band in EEG_BANDS:
        values = [channel_band_powers[channel][band] for channel in present_channels]
        if values:
            features[f'eeg_mean_rel_{band}'] = float(np.mean([
                features[f'eeg_{channel}_rel_{band}']
                for channel in present_channels
            ]))
            features[f'eeg_log_mean_abs_{band}'] = math.log(float(np.mean(values)) + EPSILON)

    if len(present_channels) >= 2:
        matrix = np.vstack([
            _finite_signal(eeg_values[channel]) for channel in present_channels
        ])
        covariance = np.cov(matrix)
        corr = np.corrcoef(matrix)
        for i, channel_a in enumerate(present_channels):
            for j, channel_b in enumerate(present_channels):
                if j < i:
                    continue
                features[f'eeg_cov_{channel_a}_{channel_b}'] = float(
                    covariance[i, j]
                )
                if j > i:
                    features[f'eeg_corr_{channel_a}_{channel_b}'] = float(
                        corr[i, j]
                    )
        upper = corr[np.triu_indices_from(corr, k=1)]
        upper = upper[np.isfinite(upper)]
        if upper.size:
            features['eeg_mean_channel_correlation'] = float(np.mean(upper))

    features.update(quality)
    return WindowFeatures(features=features, quality=quality)


def ppg_features(
    ppg_values: dict[str, np.ndarray],
    timestamps: np.ndarray,
    fallback_fs: float = 64.0,
) -> WindowFeatures:
    """Compute PPG pulse and pulse-rate-variability features."""

    fs = robust_sampling_rate(timestamps, fallback_fs)
    features: dict[str, float] = {'ppg_fs_estimated': fs}
    quality: dict[str, float] = {}
    channel_results = []

    for channel in PPG_CHANNELS:
        raw_original = np.asarray(ppg_values.get(channel, np.array([], dtype=float)), dtype=float)
        raw = _finite_signal(raw_original)
        valid_ratio = float(np.isfinite(raw_original).mean()) if raw_original.size else 0.0
        nonzero_ratio = float((np.abs(raw) > EPSILON).mean()) if raw.size else 0.0
        quality[f'ppg_{channel}_valid_ratio'] = valid_ratio
        quality[f'ppg_{channel}_nonzero_ratio'] = nonzero_ratio
        if raw.size < max(64, int(fs * 10)) or nonzero_ratio < 0.5:
            continue
        clean = _bandpass(raw, fs, 0.5, 8.0)
        clean = clean - float(np.median(clean))
        amplitude = float(np.percentile(clean, 95) - np.percentile(clean, 5))
        distance = max(1, int(round(fs * 0.35)))
        prominence = max(EPSILON, amplitude * 0.08)
        peaks, _ = signal.find_peaks(clean, distance=distance, prominence=prominence)
        if peaks.size < 3:
            continue
        peak_times = np.asarray(timestamps, dtype=float)[peaks]
        ibis = np.diff(peak_times)
        ibis = ibis[(ibis >= 0.3) & (ibis <= 2.0)]
        if ibis.size < 2:
            continue
        hr = 60.0 / ibis
        rmssd = np.sqrt(np.mean(np.diff(ibis) ** 2)) if ibis.size >= 3 else np.nan
        sdnn = np.std(ibis) if ibis.size >= 2 else np.nan
        pnn50 = np.mean(np.abs(np.diff(ibis)) > 0.05) if ibis.size >= 3 else np.nan
        duration = float(np.max(timestamps) - np.min(timestamps)) if len(timestamps) else 0.0
        expected_beats = duration / float(np.median(ibis)) if duration > 0 else peaks.size
        peak_ratio = min(1.0, float(peaks.size) / (expected_beats + EPSILON))
        result = {
            'channel': channel,
            'score': nonzero_ratio * peak_ratio * max(0.0, min(amplitude, 1e9)),
            'hr_mean': float(np.mean(hr)),
            'hr_std': float(np.std(hr)),
            'ibi_mean': float(np.mean(ibis)),
            'ibi_std': float(np.std(ibis)),
            'rmssd': float(rmssd) if np.isfinite(rmssd) else np.nan,
            'sdnn': float(sdnn) if np.isfinite(sdnn) else np.nan,
            'pnn50': float(pnn50) if np.isfinite(pnn50) else np.nan,
            'amplitude': amplitude,
            'peak_count': float(peaks.size),
            'valid_peak_ratio': peak_ratio,
        }
        channel_results.append(result)

    if channel_results:
        best = max(channel_results, key=lambda item: item['score'])
        features['ppg_best_channel_index'] = float(int(best['channel'].split('_')[1]))
        for key, value in best.items():
            if key in {'channel', 'score'}:
                continue
            features[f'ppg_best_{key}'] = float(value)
        for key in ('hr_mean', 'hr_std', 'ibi_mean', 'ibi_std', 'rmssd', 'sdnn', 'pnn50', 'amplitude', 'valid_peak_ratio'):
            values = [item[key] for item in channel_results if np.isfinite(item[key])]
            if values:
                features[f'ppg_median_{key}'] = float(np.median(values))
                features[f'ppg_iqr_{key}'] = float(
                    np.percentile(values, 75) - np.percentile(values, 25)
                )
    features['ppg_valid_channel_count'] = float(len(channel_results))
    features.update(quality)
    return WindowFeatures(features=features, quality=quality)
