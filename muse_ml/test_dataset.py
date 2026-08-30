import csv
from pathlib import Path

from muse_ml.dataset import build_dataset


HEADER = [
    'record_type', 'metadata_key', 'metadata_value', 'id', 'started_at',
    'ended_at', 'practice_id', 'operator', 'section_id', 'section_number',
    'section_title', 'target_minutes', 'started_at_iso', 'ended_at_iso',
    'closed_reason', 'eeg_start_ids', 'eeg_end_ids', 'section_interval_id',
    'measurement_number', 'section_measurement_number', 'due_at',
    'window_started_at', 'window_ended_at', 'section_started_at',
    'section_ended_at', 'submitted_at', 'submitted_at_iso',
    'task_engagement', 'effort', 'persistence', 'flow', 'engagement_score',
    'effort_persistence_score', 'clock_source', 'instrument_doi',
    'operador', 'timestamp', 'channel_1', 'channel_2', 'channel_3',
    'channel_4', 'channel_5', 'gyro_x', 'gyro_y', 'gyro_z', 'accel_x',
    'accel_y', 'accel_z', 'channel_6', 'channel_7', 'channel_8',
    'channel_9', 'channel_10', 'channel_11', 'channel_12', 'channel_13',
    'channel_14', 'channel_15', 'channel_16',
]


def _blank_row(record_type):
    row = {column: '' for column in HEADER}
    row['record_type'] = record_type
    return row


def test_build_dataset_from_exported_csv(tmp_path):
    input_dir = tmp_path / 'raw'
    output_dir = tmp_path / 'ml'
    input_dir.mkdir()
    path = input_dir / 'subject_operador_a_complete.csv'
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADER)
        writer.writeheader()
        for key, value in {
            'session_name': 'synthetic_session',
            'subject_code': 'synthetic_subject',
            'experiment': 'synthetic',
        }.items():
            row = _blank_row('metadata')
            row['metadata_key'] = key
            row['metadata_value'] = value
            writer.writerow(row)
        label = _blank_row('ground_truth_responses')
        label.update({
            'operator': 'operador_a',
            'section_id': 'paso_1',
            'measurement_number': '1',
            'section_measurement_number': '1',
            'window_started_at': '0',
            'window_ended_at': '12',
            'task_engagement': '4',
            'effort': '4',
            'persistence': '5',
            'flow': '3',
            'engagement_score': '4.0',
        })
        writer.writerow(label)
        for index in range(256 * 12):
            timestamp = index / 256
            row = _blank_row('eeg_logs')
            row['operador'] = 'operador_a'
            row['timestamp'] = timestamp
            for channel in ('channel_1', 'channel_2', 'channel_3', 'channel_4'):
                row[channel] = 1.0
            writer.writerow(row)
        for index in range(64 * 12):
            timestamp = index / 64
            row = _blank_row('ppg_logs')
            row['operador'] = 'operador_a'
            row['timestamp'] = timestamp
            for channel in [f'channel_{number}' for number in range(1, 17)]:
                row[channel] = 1.0
            writer.writerow(row)

    manifest = build_dataset(
        input_dir,
        output_dir,
        window_seconds=10,
        stride_seconds=5,
        min_eeg_samples=100,
        min_ppg_samples=100,
    )

    assert manifest['windows'] == 1
    assert (output_dir / 'ml_windows.csv').is_file()
    assert (output_dir / 'ml_labels.csv').is_file()
    assert (output_dir / 'ml_features.csv').is_file()
