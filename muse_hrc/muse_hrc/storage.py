"""SQLite persistence for Muse telemetry without ROS dependencies."""

import os
import sqlite3
import threading
import time
from typing import Callable, Optional

from muse_hrc.models import EegSample, ImuSample, PpgSample


BATCH_MAX_ROWS = 1024


class TelemetryStore:
    """Batch telemetry into the existing local SQLite schema.

    The object is safe to call from acquisition threads.  Recording can be
    paused while devices remain connected, which preserves the GUI workflow.
    """

    def __init__(
        self,
        db_path='~/muse_telemetry.db',
        recording_enabled=True,
        connection=None,
        error_callback: Optional[Callable[[str], None]] = None,
    ):
        self.db_path = os.path.expanduser(db_path)
        self.conn = connection or sqlite3.connect(
            self.db_path,
            check_same_thread=False,
        )
        self.cursor = self.conn.cursor()
        self._lock = threading.RLock()
        self._error_callback = error_callback
        self._owns_connection = connection is None
        self._closed = False
        self.recording_enabled = bool(recording_enabled)
        self._recording_period_id = None
        self._pending_eeg = []
        self._pending_imu = []
        self._pending_ppg = []
        self._saved_eeg = 0
        self._saved_imu = 0
        self._saved_ppg = 0
        self.init_database()
        if self.recording_enabled:
            self._open_recording_period()

    def init_database(self):
        with self._lock:
            self.cursor.execute('PRAGMA journal_mode=WAL')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS eeg_logs (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    operador  TEXT,
                    timestamp REAL,
                    channel_1 REAL,
                    channel_2 REAL,
                    channel_3 REAL,
                    channel_4 REAL,
                    channel_5 REAL
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS imu_logs (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    operador  TEXT,
                    timestamp REAL,
                    gyro_x    REAL,
                    gyro_y    REAL,
                    gyro_z    REAL
                )
            ''')
            self._ensure_column('imu_logs', 'accel_x', 'REAL')
            self._ensure_column('imu_logs', 'accel_y', 'REAL')
            self._ensure_column('imu_logs', 'accel_z', 'REAL')
            channels = ',\n'.join(
                f'channel_{index} REAL' for index in range(1, 17)
            )
            self.cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS ppg_logs (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    operador   TEXT,
                    timestamp  REAL,
                    {channels}
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS recording_periods (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    ended_at   REAL
                )
            ''')
            self.conn.commit()

    def _ensure_column(self, table, column, declaration):
        columns = {
            row[1]
            for row in self.cursor.execute(f'PRAGMA table_info({table})')
        }
        if column not in columns:
            self.cursor.execute(
                f'ALTER TABLE {table} ADD COLUMN {column} {declaration}'
            )

    def set_recording(self, enabled):
        """Set persistence state and return True only when it changed."""
        with self._lock:
            requested = bool(enabled)
            if requested == self.recording_enabled:
                return False
            if requested:
                self.recording_enabled = True
                self._open_recording_period()
            else:
                self.recording_enabled = False
                self.flush()
                self._close_recording_period()
            return True

    def _open_recording_period(self):
        if self._recording_period_id is not None:
            return
        self.cursor.execute(
            'INSERT INTO recording_periods (started_at) VALUES (?)',
            (time.time(),),
        )
        self._recording_period_id = self.cursor.lastrowid
        self.conn.commit()

    def _close_recording_period(self):
        if self._recording_period_id is None:
            return
        self.cursor.execute(
            'UPDATE recording_periods SET ended_at=? WHERE id=?',
            (time.time(), self._recording_period_id),
        )
        self.conn.commit()
        self._recording_period_id = None

    def add(self, sample):
        if isinstance(sample, EegSample):
            return self.add_eeg(sample)
        if isinstance(sample, ImuSample):
            return self.add_imu(sample)
        if isinstance(sample, PpgSample):
            return self.add_ppg(sample)
        raise TypeError(f'Unsupported telemetry type: {type(sample).__name__}')

    def add_eeg(self, sample: EegSample):
        with self._lock:
            if not self.recording_enabled:
                return False
            data = list(sample.data) + [0.0]
            self._pending_eeg.append(
                (sample.operator_id, sample.timestamp, *data[:5])
            )
            self._flush_if_full()
            return True

    def add_imu(self, sample: ImuSample):
        with self._lock:
            if not self.recording_enabled:
                return False
            self._pending_imu.append((
                sample.operator_id,
                sample.timestamp,
                *sample.angular_velocity,
                *sample.linear_acceleration,
            ))
            self._flush_if_full()
            return True

    def add_ppg(self, sample: PpgSample):
        with self._lock:
            if not self.recording_enabled:
                return False
            self._pending_ppg.append((
                sample.operator_id,
                sample.timestamp,
                *sample.data,
            ))
            self._flush_if_full()
            return True

    def _flush_if_full(self):
        if self.pending_count >= BATCH_MAX_ROWS:
            self.flush()

    @property
    def pending_count(self):
        return (
            len(self._pending_eeg)
            + len(self._pending_imu)
            + len(self._pending_ppg)
        )

    def flush(self):
        """Persist accumulated sensor rows in one SQLite transaction."""
        with self._lock:
            if not self.pending_count:
                return True
            eeg_batch = self._pending_eeg
            imu_batch = self._pending_imu
            ppg_batch = self._pending_ppg
            self._pending_eeg = []
            self._pending_imu = []
            self._pending_ppg = []
            try:
                if eeg_batch:
                    self.cursor.executemany(
                        'INSERT INTO eeg_logs '
                        '(operador, timestamp, channel_1, channel_2, '
                        'channel_3, '
                        'channel_4, channel_5) VALUES (?, ?, ?, ?, ?, ?, ?)',
                        eeg_batch,
                    )
                if imu_batch:
                    self.cursor.executemany(
                        'INSERT INTO imu_logs '
                        '(operador, timestamp, gyro_x, gyro_y, gyro_z, '
                        'accel_x, accel_y, accel_z) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        imu_batch,
                    )
                if ppg_batch:
                    placeholders = ', '.join('?' for _ in range(18))
                    self.cursor.executemany(
                        'INSERT INTO ppg_logs '
                        '(operador, timestamp, '
                        + ', '.join(
                            f'channel_{index}' for index in range(1, 17)
                        )
                        + f') VALUES ({placeholders})',
                        ppg_batch,
                    )
                self.conn.commit()
                self._saved_eeg += len(eeg_batch)
                self._saved_imu += len(imu_batch)
                self._saved_ppg += len(ppg_batch)
                return True
            except sqlite3.Error as error:
                self.conn.rollback()
                self._pending_eeg = eeg_batch + self._pending_eeg
                self._pending_imu = imu_batch + self._pending_imu
                self._pending_ppg = ppg_batch + self._pending_ppg
                if self._error_callback is not None:
                    self._error_callback(f'Error en lote SQLite: {error}')
                return False

    def metrics(self):
        with self._lock:
            return {
                'saved': {
                    'eeg': self._saved_eeg,
                    'imu': self._saved_imu,
                    'ppg': self._saved_ppg,
                },
                'pending': self.pending_count,
                'recording': self.recording_enabled,
            }

    def close(self):
        with self._lock:
            if self._closed:
                return
            self.flush()
            self._close_recording_period()
            if self._owns_connection:
                self.conn.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.close()
