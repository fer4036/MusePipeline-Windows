import math
import sqlite3

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
