"""Tests for ROS-independent batched telemetry persistence."""

import sqlite3

from muse_hrc.models import EegSample, PpgSample
from muse_hrc.storage import TelemetryStore


def memory_store(recording_enabled=True, connection=None):
    return TelemetryStore(
        connection=connection or sqlite3.connect(':memory:'),
        recording_enabled=recording_enabled,
    )


def test_eeg_rows_are_committed_as_a_batch():
    store = memory_store()
    sample = EegSample(
        timestamp=100.5,
        operator_id='operador_a',
        data=(1.0, 2.0, 3.0, 4.0),
    )

    store.add_eeg(sample)
    before_commit = store.cursor.execute(
        'SELECT COUNT(*) FROM eeg_logs'
    ).fetchone()[0]
    store.flush()
    after_commit = store.cursor.execute(
        'SELECT COUNT(*) FROM eeg_logs'
    ).fetchone()[0]

    assert before_commit == 0
    assert after_commit == 1
    assert store.metrics()['saved']['eeg'] == 1


def test_schema_migration_and_ppg_batch():
    connection = sqlite3.connect(':memory:')
    connection.execute('''
        CREATE TABLE imu_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operador TEXT,
            timestamp REAL,
            gyro_x REAL,
            gyro_y REAL,
            gyro_z REAL
        )
    ''')
    store = memory_store(connection=connection)
    sample = PpgSample(
        timestamp=200.0,
        operator_id='operador_b',
        data=tuple(float(index) for index in range(16)),
    )

    store.add_ppg(sample)
    store.flush()

    imu_columns = {
        row[1] for row in store.cursor.execute('PRAGMA table_info(imu_logs)')
    }
    ppg_row = store.cursor.execute(
        'SELECT operador, timestamp, channel_16 FROM ppg_logs'
    ).fetchone()
    assert {'accel_x', 'accel_y', 'accel_z'} <= imu_columns
    assert ppg_row == ('operador_b', 200.0, 15.0)


def test_samples_are_ignored_while_recording_is_paused():
    store = memory_store(recording_enabled=False)
    sample = EegSample(
        timestamp=0.0,
        operator_id='operador_a',
        data=(1.0, 2.0, 3.0, 4.0),
    )

    assert store.add_eeg(sample) is False
    store.flush()

    count = store.cursor.execute('SELECT COUNT(*) FROM eeg_logs').fetchone()[0]
    assert count == 0


def test_recording_periods_follow_explicit_start_and_stop():
    store = memory_store(recording_enabled=False)

    assert store.set_recording(True) is True
    assert store.set_recording(True) is False
    assert store.set_recording(False) is True

    started_at, ended_at = store.cursor.execute(
        'SELECT started_at, ended_at FROM recording_periods'
    ).fetchone()
    assert started_at > 0
    assert ended_at >= started_at
