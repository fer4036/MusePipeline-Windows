"""Tests for batched telemetry persistence."""

import sqlite3
from types import SimpleNamespace

from muse_msgs.msg import EegSample, PpgSample

from muse_hrc.database_node import DatabaseNode


def test_eeg_rows_are_committed_as_a_batch():
    node = DatabaseNode.__new__(DatabaseNode)
    node.conn = sqlite3.connect(':memory:')
    node.cursor = node.conn.cursor()
    node.init_database()
    node._pending_eeg = []
    node._pending_imu = []
    node._pending_ppg = []
    node._saved_eeg = 0
    node._saved_imu = 0
    node._saved_ppg = 0
    node.recording_enabled = True
    node._recording_period_id = None
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)
    message = EegSample()
    message.header.stamp.sec = 100
    message.header.stamp.nanosec = 500_000_000
    message.data = [1.0, 2.0, 3.0, 4.0]

    node.save_eeg(message, 'operador_a')
    before_commit = node.cursor.execute(
        'SELECT COUNT(*) FROM eeg_logs'
    ).fetchone()[0]
    node._commit_batch()
    after_commit = node.cursor.execute(
        'SELECT COUNT(*) FROM eeg_logs'
    ).fetchone()[0]

    assert before_commit == 0
    assert after_commit == 1
    assert node._saved_eeg == 1


def test_schema_migration_and_ppg_batch():
    node = DatabaseNode.__new__(DatabaseNode)
    node.conn = sqlite3.connect(':memory:')
    node.cursor = node.conn.cursor()
    node.cursor.execute('''
        CREATE TABLE imu_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operador TEXT,
            timestamp REAL,
            gyro_x REAL,
            gyro_y REAL,
            gyro_z REAL
        )
    ''')
    node.init_database()
    node._pending_eeg = []
    node._pending_imu = []
    node._pending_ppg = []
    node._saved_eeg = 0
    node._saved_imu = 0
    node._saved_ppg = 0
    node.recording_enabled = True
    node._recording_period_id = None
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)
    message = PpgSample()
    message.header.stamp.sec = 200
    message.data = [float(index) for index in range(16)]

    node.save_ppg(message, 'operador_b')
    node._commit_batch()

    imu_columns = {
        row[1] for row in node.cursor.execute('PRAGMA table_info(imu_logs)')
    }
    ppg_row = node.cursor.execute(
        'SELECT operador, timestamp, channel_16 FROM ppg_logs'
    ).fetchone()
    assert {'accel_x', 'accel_y', 'accel_z'} <= imu_columns
    assert ppg_row == ('operador_b', 200.0, 15.0)


def test_samples_are_ignored_while_recording_is_paused():
    node = DatabaseNode.__new__(DatabaseNode)
    node.conn = sqlite3.connect(':memory:')
    node.cursor = node.conn.cursor()
    node.init_database()
    node._pending_eeg = []
    node._pending_imu = []
    node._pending_ppg = []
    node._saved_eeg = 0
    node._saved_imu = 0
    node._saved_ppg = 0
    node.recording_enabled = False
    node._recording_period_id = None
    message = EegSample()
    message.data = [1.0, 2.0, 3.0, 4.0]

    node.save_eeg(message, 'operador_a')
    node._commit_batch()

    assert node.cursor.execute('SELECT COUNT(*) FROM eeg_logs').fetchone()[0] == 0
