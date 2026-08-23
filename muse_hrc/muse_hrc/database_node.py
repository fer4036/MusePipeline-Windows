import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
from muse_msgs.msg import EegSample, PpgSample
from std_srvs.srv import SetBool
import sqlite3
import os
import time

BATCH_INTERVAL_SECONDS = 0.1
BATCH_MAX_ROWS = 1024


class DatabaseNode(Node):
    def __init__(self):
        super().__init__('database_node')
        self.declare_parameter('db_path', '~/muse_telemetry.db')
        self.declare_parameter('recording_enabled', True)
        configured_path = self.get_parameter(
            'db_path'
        ).get_parameter_value().string_value
        self.db_path = os.path.expanduser(configured_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_database()
        self.recording_enabled = self.get_parameter(
            'recording_enabled'
        ).get_parameter_value().bool_value
        self._recording_period_id = None
        self._pending_eeg = []
        self._pending_imu = []
        self._pending_ppg = []
        self._saved_eeg = 0
        self._saved_imu = 0
        self._saved_ppg = 0
        self.suscripciones_activas = {}
        self.discovery_timer = self.create_timer(3.0, self.discover_operators_callback)
        self.commit_timer = self.create_timer(
            BATCH_INTERVAL_SECONDS, self._commit_batch
        )
        self.metrics_timer = self.create_timer(10.0, self._report_metrics)
        self.recording_service = self.create_service(
            SetBool,
            '~/set_recording',
            self._set_recording_callback,
        )
        if self.recording_enabled:
            self._open_recording_period()
        self.get_logger().info(f"Nodo de base de datos listo. Guardado en: {self.db_path}")
        self.get_logger().info(
            'Grabación inicial: '
            f"{'ACTIVA' if self.recording_enabled else 'EN ESPERA'}"
        )

    def init_database(self):
        self.cursor.execute("PRAGMA journal_mode=WAL")
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
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS ppg_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                operador   TEXT,
                timestamp  REAL,
                channel_1  REAL,
                channel_2  REAL,
                channel_3  REAL,
                channel_4  REAL,
                channel_5  REAL,
                channel_6  REAL,
                channel_7  REAL,
                channel_8  REAL,
                channel_9  REAL,
                channel_10 REAL,
                channel_11 REAL,
                channel_12 REAL,
                channel_13 REAL,
                channel_14 REAL,
                channel_15 REAL,
                channel_16 REAL
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

    def _set_recording_callback(self, request, response):
        """Enable or pause persistence without disconnecting the headbands."""
        requested = bool(request.data)
        if requested == self.recording_enabled:
            state = 'activa' if requested else 'detenida'
            response.success = True
            response.message = f'La grabación ya estaba {state}'
            return response

        if requested:
            self.recording_enabled = True
            self._open_recording_period()
            self.get_logger().info('GRABACIÓN INICIADA por solicitud de usuario')
            response.message = 'Grabación iniciada'
        else:
            self.recording_enabled = False
            self._commit_batch()
            self._close_recording_period()
            self.get_logger().info('GRABACIÓN DETENIDA por solicitud de usuario')
            response.message = 'Grabación detenida'
        response.success = True
        return response

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

    def _ensure_column(self, table, column, declaration):
        columns = {
            row[1] for row in self.cursor.execute(f'PRAGMA table_info({table})')
        }
        if column not in columns:
            self.cursor.execute(
                f'ALTER TABLE {table} ADD COLUMN {column} {declaration}'
            )

    def discover_operators_callback(self):
        topic_names_and_types = self.get_topic_names_and_types()
        for topic_name, topic_types in topic_names_and_types:
            if topic_name.endswith('/eeg'):
                operador = topic_name.split('/')[1]
                if operador not in self.suscripciones_activas:
                    self.get_logger().info(f"NUEVO OPERADOR: {operador}, CREANDO SUSCRIPCIONES")
                    sub_eeg = self.create_subscription(
                        EegSample,
                        f'/{operador}/eeg',
                        lambda msg, op=operador: self.save_eeg(msg, op),
                        1000
                    )
                    sub_imu = self.create_subscription(
                        Imu,
                        f'/{operador}/imu',
                        lambda msg, op=operador: self.save_imu(msg, op),
                        1000
                    )
                    sub_ppg = self.create_subscription(
                        PpgSample,
                        f'/{operador}/ppg',
                        lambda msg, op=operador: self.save_ppg(msg, op),
                        1000
                    )
                    self.suscripciones_activas[operador] = [
                        sub_eeg, sub_imu, sub_ppg
                    ]

    def save_eeg(self, msg, operador):
        if not self.recording_enabled:
            return
        try:
            t = self._timestamp(msg.header.stamp)
            d = [float(value) for value in msg.data]
            d = d + [0.0] * (5 - len(d))
            self._pending_eeg.append(
                (operador, t, d[0], d[1], d[2], d[3], d[4])
            )
            self._flush_if_full()
        except Exception as e:
            self.get_logger().error(f"Error al guardar EEG de {operador}: {e}")

    def save_imu(self, msg, operador):
        if not self.recording_enabled:
            return
        try:
            t = self._timestamp(msg.header.stamp)
            self._pending_imu.append((
                operador,
                t,
                msg.angular_velocity.x,
                msg.angular_velocity.y,
                msg.angular_velocity.z,
                msg.linear_acceleration.x,
                msg.linear_acceleration.y,
                msg.linear_acceleration.z,
            ))
            self._flush_if_full()
        except Exception as e:
            self.get_logger().error(f"Error al guardar IMU de {operador}: {e}")

    def save_ppg(self, msg, operador):
        if not self.recording_enabled:
            return
        try:
            t = self._timestamp(msg.header.stamp)
            data = [float(value) for value in msg.data]
            data = data + [0.0] * (16 - len(data))
            self._pending_ppg.append((operador, t, *data[:16]))
            self._flush_if_full()
        except Exception as error:
            self.get_logger().error(f"Error al guardar PPG de {operador}: {error}")

    @staticmethod
    def _timestamp(stamp):
        return float(stamp.sec) + float(stamp.nanosec) / 1_000_000_000.0

    def _flush_if_full(self):
        pending = (
            len(self._pending_eeg) +
            len(self._pending_imu) +
            len(self._pending_ppg)
        )
        if pending >= BATCH_MAX_ROWS:
            self._commit_batch()

    def _commit_batch(self):
        """Persist accumulated sensor rows in one SQLite transaction."""
        if (
            not self._pending_eeg and
            not self._pending_imu and
            not self._pending_ppg
        ):
            return

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
                    '(operador, timestamp, channel_1, channel_2, channel_3, '
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
                self.cursor.executemany(
                    'INSERT INTO ppg_logs '
                    '(operador, timestamp, channel_1, channel_2, channel_3, '
                    'channel_4, channel_5, channel_6, channel_7, channel_8, '
                    'channel_9, channel_10, channel_11, channel_12, '
                    'channel_13, channel_14, channel_15, channel_16) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
                    '?, ?, ?)',
                    ppg_batch,
                )
            self.conn.commit()
            self._saved_eeg += len(eeg_batch)
            self._saved_imu += len(imu_batch)
            self._saved_ppg += len(ppg_batch)
        except sqlite3.Error as error:
            self.conn.rollback()
            self._pending_eeg = eeg_batch + self._pending_eeg
            self._pending_imu = imu_batch + self._pending_imu
            self._pending_ppg = ppg_batch + self._pending_ppg
            self.get_logger().error(f"Error en lote SQLite: {error}")

    def _report_metrics(self):
        self.get_logger().info(
            "Persistencia: "
            f"EEG={self._saved_eeg}, IMU={self._saved_imu}, "
            f"PPG={self._saved_ppg}, "
            f"grabando={self.recording_enabled}, "
            "pendientes="
            f"{len(self._pending_eeg) + len(self._pending_imu) + len(self._pending_ppg)}"
        )

    def destroy_node(self):
        self._commit_batch()
        self._close_recording_period()
        self.conn.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = DatabaseNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
