from pathlib import Path

import pandas as pd

from muse_ml.train import loso_train


def _write_minimal_dataset(path: Path):
    path.mkdir()
    windows = []
    labels = []
    features = []
    for subject_index, subject in enumerate(('s1', 's2', 's3')):
        for window_index in range(4):
            window_id = f'{subject}:{window_index}'
            score = 2.5 + subject_index * 0.5 + window_index * 0.05
            windows.append({
                'window_id': window_id,
                'source_file': f'{subject}.csv',
                'subject_code': subject,
                'session_name': subject,
                'experiment': 'synthetic',
                'operator': 'operador_a',
                'section_id': 'paso_1',
                'response_id': f'{subject}:1',
                'measurement_number': 1,
                'start_time': window_index * 30,
                'end_time': window_index * 30 + 60,
                'window_size_s': 60,
                'stride_s': 30,
                'n_eeg_samples': 15360,
                'n_ppg_samples': 3840,
                'valid_eeg': 1,
                'valid_ppg': 1,
            })
            labels.append({
                'window_id': window_id,
                'response_id': f'{subject}:1',
                'task_engagement': round(score),
                'effort': round(score),
                'persistence': round(score),
                'flow': round(score),
                'scem_score_recomputed': score,
                'scem_score_stored': score,
                'high_engagement': int(score >= 4),
            })
            features.extend([
                {
                    'window_id': window_id,
                    'feature_name': 'eeg_channel_1_rel_alpha',
                    'feature_value': 1.0 / score,
                    'modality': 'eeg',
                    'channel': 'channel_1',
                    'band': 'alpha',
                    'feature_group': 'eeg_channel_1',
                },
                {
                    'window_id': window_id,
                    'feature_name': 'eeg_channel_1_rel_theta',
                    'feature_value': score,
                    'modality': 'eeg',
                    'channel': 'channel_1',
                    'band': 'theta',
                    'feature_group': 'eeg_channel_1',
                },
            ])
    pd.DataFrame(windows).to_csv(path / 'ml_windows.csv', index=False)
    pd.DataFrame(labels).to_csv(path / 'ml_labels.csv', index=False)
    pd.DataFrame(features).to_csv(path / 'ml_features.csv', index=False)


def test_loso_train_writes_metrics(tmp_path):
    input_dir = tmp_path / 'dataset'
    output_dir = tmp_path / 'models'
    _write_minimal_dataset(input_dir)

    summary = loso_train(input_dir, output_dir)

    assert summary['n_subjects'] == 3
    assert (output_dir / 'metrics_loso.csv').is_file()
    assert (output_dir / 'predictions_loso.csv').is_file()
