import numpy as np

from muse_ml.features import eeg_band_features, ppg_features


def test_eeg_band_features_detect_alpha_dominance():
    fs = 256
    timestamps = np.arange(0, 60, 1 / fs)
    alpha = np.sin(2 * np.pi * 10 * timestamps)
    eeg = {
        f'channel_{index}': alpha.copy()
        for index in range(1, 5)
    }

    result = eeg_band_features(eeg, timestamps)

    assert result.features['eeg_channel_1_rel_alpha'] > 0.70
    assert result.features['eeg_channel_1_log_theta_alpha'] < 0


def test_ppg_features_extract_heart_rate():
    fs = 64
    timestamps = np.arange(0, 60, 1 / fs)
    pulse = np.sin(2 * np.pi * 1.2 * timestamps)
    ppg = {
        f'channel_{index}': pulse.copy()
        for index in range(1, 17)
    }

    result = ppg_features(ppg, timestamps)

    assert result.features['ppg_valid_channel_count'] >= 1
    assert 60 <= result.features['ppg_best_hr_mean'] <= 90
