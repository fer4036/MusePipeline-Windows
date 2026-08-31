import math
import sqlite3

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import BayesianRidge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from muse_ml.realtime import RealtimeCognitiveMonitor, RealtimeConfig


def test_realtime_monitor_reads_recent_sqlite_window_without_model(tmp_path):
    database = tmp_path / 'raw.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE eeg_logs ('
            'id INTEGER PRIMARY KEY, operador TEXT, timestamp REAL, '
            'channel_1 REAL, channel_2 REAL, channel_3 REAL, channel_4 REAL)'
        )
        connection.execute(
            'CREATE TABLE ppg_logs ('
            'id INTEGER PRIMARY KEY, operador TEXT, timestamp REAL, '
            + ', '.join(f'channel_{index} REAL' for index in range(1, 17))
            + ')'
        )
        for index in range(600):
            timestamp = index / 10.0
            eeg_value = math.sin(2 * math.pi * 10 * timestamp)
            connection.execute(
                'INSERT INTO eeg_logs '
                '(operador, timestamp, channel_1, channel_2, channel_3, channel_4) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                ('operador_a', timestamp, eeg_value, eeg_value, eeg_value, eeg_value),
            )
            ppg_channels = [math.sin(2 * math.pi * 1.2 * timestamp)] * 16
            connection.execute(
                'INSERT INTO ppg_logs VALUES ('
                + ', '.join(['NULL', '?', '?', *(['?'] * 16)])
                + ')',
                ('operador_a', timestamp, *ppg_channels),
            )

    monitor = RealtimeCognitiveMonitor(RealtimeConfig(
        window_seconds=60,
        min_eeg_samples=128,
        min_ppg_samples=128,
    ))

    snapshot = monitor.snapshot(database, ['operador_a'])

    assert snapshot['state'] == 'active'
    assert snapshot['operators'][0]['state'] == 'features_ready'
    assert snapshot['operators'][0]['method'] == 'model_not_configured'
    assert snapshot['operators'][0]['features_used'] > 0


def test_realtime_monitor_uses_two_stage_calibrated_artifact(tmp_path):
    database = tmp_path / 'raw.sqlite'
    model_path = tmp_path / 'stage.joblib'
    feature_columns = [
        'eeg_channel_1_rel_alpha',
        'eeg_channel_1_rel_theta',
        'eeg_channel_2_rel_alpha',
        'eeg_channel_2_rel_theta',
    ]
    model = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', RobustScaler()),
        ('model', BayesianRidge()),
    ])
    model.fit(
        pd.DataFrame([
            {name: 0.0 for name in feature_columns},
            {
                'eeg_channel_1_rel_alpha': -0.2,
                'eeg_channel_1_rel_theta': 0.2,
                'eeg_channel_2_rel_alpha': -0.2,
                'eeg_channel_2_rel_theta': 0.2,
            },
        ]),
        [0.0, 0.4],
    )
    joblib.dump({
        'stage_1_model': model,
        'stage_1_target': 'relative_scem',
        'stage_2_calibration': 'initial_subject_scem_anchor',
        'physiological_baseline': 'paso_1',
        'feature_columns': feature_columns,
    }, model_path)

    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE eeg_logs ('
            'id INTEGER PRIMARY KEY, operador TEXT, timestamp REAL, '
            'channel_1 REAL, channel_2 REAL, channel_3 REAL, channel_4 REAL)'
        )
        connection.execute(
            'CREATE TABLE ppg_logs ('
            'id INTEGER PRIMARY KEY, operador TEXT, timestamp REAL, '
            + ', '.join(f'channel_{index} REAL' for index in range(1, 17))
            + ')'
        )
        connection.execute(
            'CREATE TABLE workshop_sections ('
            'id INTEGER PRIMARY KEY, operator TEXT, section_id TEXT, '
            'started_at REAL, ended_at REAL)'
        )
        connection.execute(
            'CREATE TABLE ground_truth_responses ('
            'id INTEGER PRIMARY KEY, operator TEXT, submitted_at REAL, '
            'task_engagement INTEGER, effort INTEGER, persistence INTEGER, '
            'flow INTEGER, engagement_score REAL)'
        )
        connection.execute(
            'INSERT INTO workshop_sections '
            '(operator, section_id, started_at, ended_at) VALUES (?, ?, ?, ?)',
            ('operador_a', 'paso_1', 0.0, 60.0),
        )
        connection.execute(
            'INSERT INTO ground_truth_responses '
            '(operator, submitted_at, task_engagement, effort, persistence, flow, engagement_score) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('operador_a', 65.0, 4, 4, 4, 4, 4.0),
        )
        for index in range(2800):
            timestamp = index / 20.0
            frequency = 10.0 if timestamp < 60.0 else 6.0
            value = math.sin(2 * math.pi * frequency * timestamp)
            connection.execute(
                'INSERT INTO eeg_logs '
                '(operador, timestamp, channel_1, channel_2, channel_3, channel_4) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                ('operador_a', timestamp, value, value, value, value),
            )

    monitor = RealtimeCognitiveMonitor(RealtimeConfig(
        model_path=str(model_path),
        window_seconds=60,
        min_eeg_samples=128,
        min_ppg_samples=128,
    ))

    snapshot = monitor.snapshot(database, ['operador_a'])
    operator = snapshot['operators'][0]

    assert snapshot['model_kind'] == 'stage1_relative_scem_stage2_calibrator'
    assert operator['state'] == 'estimated'
    assert operator['stage_1_target'] == 'relative_scem'
    assert operator['physiological_baseline'] == 'paso_1'
    assert operator['stage_2_calibration'] == 'initial_subject_scem_anchor'
    assert operator['score'] is not None
