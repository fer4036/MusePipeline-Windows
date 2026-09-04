"""Muse BLE acquisition core with no ROS dependency."""

import math
import queue
import threading
import time
from typing import Callable, Optional

from muse_hrc.athena_protocol import HostSampleClock
from muse_hrc.models import DeviceStatus, EegSample, ImuSample, PpgSample


DATA_TIMEOUT_SECONDS = 10.0
BLE_PUMP_SECONDS = 0.1
STANDARD_GRAVITY = 9.80665
EEG_RATE_HZ = 256
PPG_RATE_HZ = 64
IMU_RATE_HZ = 52


class MuseDeviceSession:
    """Own one headband's BLE lifecycle and produce Python telemetry models.

    Adapter allocation and retry scheduling intentionally remain outside this
    class.  This prevents several independent reconnect loops from competing
    for the same BlueZ adapter.
    """

    def __init__(
        self,
        operator_id,
        mac_address,
        hci_device='hci0',
        adapter_factory=None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        data_timeout=DATA_TIMEOUT_SECONDS,
    ):
        self.operator_id = operator_id
        self.mac_address = mac_address.lower()
        self.hci_device = hci_device
        self._adapter_factory = adapter_factory
        self._log_callback = log_callback
        self.data_timeout = float(data_timeout)

        self._queues = {
            'eeg': queue.Queue(maxsize=5000),
            'imu': queue.Queue(maxsize=5000),
            'ppg': queue.Queue(maxsize=5000),
        }
        self._status_queue = queue.Queue(maxsize=100)
        self._sample_clocks = {
            'eeg': HostSampleClock(EEG_RATE_HZ),
            'imu': HostSampleClock(IMU_RATE_HZ),
            'ppg': HostSampleClock(PPG_RATE_HZ),
        }
        self._sensor_lock = threading.Lock()
        self._latest_acc = None
        self._stats_lock = threading.Lock()
        self._received = {'eeg': 0, 'imu': 0, 'ppg': 0}
        self._delivered = {'eeg': 0, 'imu': 0, 'ppg': 0}
        self._dropped = {'eeg': 0, 'imu': 0, 'ppg': 0}
        self._metrics_previous = dict(self._received)
        self._metrics_previous_delivered = dict(self._delivered)
        self._metrics_previous_time = time.monotonic()

        self.muse = None
        self.streaming = False
        self._connecting = False
        self._lock = threading.Lock()
        self._last_data_time = 0.0
        self._stream_started_at = None
        self._stream_status_sent = False
        self._shutting_down = False
        self._lifecycle_finished = False
        self._retry_reported = False
        self._connection_thread = None

    @property
    def connecting(self):
        with self._lock:
            return self._connecting

    @property
    def connection_pending(self):
        """True only while GATT has not reached streaming yet."""
        with self._lock:
            return self._connecting and not self.streaming

    @property
    def lifecycle_finished(self):
        with self._lock:
            return self._lifecycle_finished

    def start(self):
        """Start exactly one BLE lifecycle thread."""
        with self._lock:
            if self._connecting or self._shutting_down:
                return False
            self._connecting = True
            self._lifecycle_finished = False
            self._retry_reported = False
            thread = threading.Thread(target=self._connect, daemon=True)
            self._connection_thread = thread
            thread.start()
            return True

    def stop(self, join_timeout=5.0):
        with self._lock:
            self._shutting_down = True
            self.streaming = False
            thread = self._connection_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)

    def drain(self, stream, limit=512):
        """Return up to ``limit`` samples and account for their delivery."""
        if stream not in self._queues:
            raise ValueError(f'Unknown stream: {stream}')
        result = []
        target = self._queues[stream]
        while len(result) < limit:
            try:
                result.append(target.get_nowait())
            except queue.Empty:
                break
        if result:
            with self._stats_lock:
                self._delivered[stream] += len(result)
        return result

    def drain_status(self, limit=100):
        result = []
        while len(result) < limit:
            try:
                result.append(self._status_queue.get_nowait())
            except queue.Empty:
                break
        return result

    def poll(self):
        """Check timeout/lifecycle state; return True when owner must retry."""
        now = time.monotonic()
        with self._lock:
            streaming = self.streaming
            connecting = self._connecting
            shutting_down = self._shutting_down
            lifecycle_finished = self._lifecycle_finished
            last_data_time = self._last_data_time
            muse_missing = self.muse is None
            no_data = (
                streaming and (now - last_data_time) > self.data_timeout
            )
            if no_data or (streaming and muse_missing):
                self.streaming = False
                self._stream_started_at = None

            retry_required = lifecycle_finished and not connecting
            new_retry_required = retry_required and not self._retry_reported
            if new_retry_required:
                self._retry_reported = True
                self._queue_status('retry_required')

        if shutting_down:
            return False
        if no_data:
            elapsed = round(now - last_data_time, 1)
            self._log('warning', f'Sin datos hace {elapsed}s')
            self._queue_status('data_timeout', seconds=elapsed)
        return new_retry_required

    def report_metrics(self):
        now = time.monotonic()
        elapsed = max(now - self._metrics_previous_time, 0.001)
        with self._stats_lock:
            received = dict(self._received)
            delivered = dict(self._delivered)
            dropped = dict(self._dropped)
        received_hz = {
            name: round(
                (received[name] - self._metrics_previous[name]) / elapsed,
                1,
            )
            for name in received
        }
        delivered_hz = {
            name: round(
                (
                    delivered[name]
                    - self._metrics_previous_delivered[name]
                ) / elapsed,
                1,
            )
            for name in delivered
        }
        self._metrics_previous = received
        self._metrics_previous_delivered = delivered
        self._metrics_previous_time = now
        with self._lock:
            muse = self.muse
            streaming = self.streaming
            stream_started_at = self._stream_started_at
        battery = getattr(muse, '_battery', 0.0) if muse is not None else 0.0
        details = {
            'streaming': streaming,
            'connected_since': stream_started_at,
            'battery_percent': (
                min(100.0, round(float(battery), 1)) if battery > 0 else None
            ),
            'received_hz': received_hz,
            # Keep the legacy key for the existing web/status consumer.
            'published_hz': delivered_hz,
            'received': received,
            'published': delivered,
            'dropped': dropped,
            'queued': {
                name: target.qsize() for name, target in self._queues.items()
            },
        }
        self._queue_status('metrics', **details)
        return details

    def _eeg_callback(self, data, _timestamps):
        timestamps = self._sample_clocks['eeg'].timestamps(data.shape[1])
        with self._lock:
            self._last_data_time = time.monotonic()
            announce = self.streaming and not self._stream_status_sent
            if announce:
                self._stream_status_sent = True
        if announce:
            self._queue_status('streaming')
        for index in range(data.shape[1]):
            try:
                sample = EegSample(
                    timestamp=self._timestamp_at(timestamps, index),
                    operator_id=self.operator_id,
                    sampling_rate_hz=EEG_RATE_HZ,
                    data=tuple(float(value) for value in data[:4, index]),
                )
                self._enqueue_sample('eeg', sample)
            except Exception:
                self._record_drop('eeg')

    def _acc_callback(self, data, _timestamps):
        timestamps = self._sample_clocks['imu'].timestamps(data.shape[1])
        with self._sensor_lock:
            self._latest_acc = (data.copy(), list(timestamps))

    def _imu_callback(self, data, _timestamps):
        with self._sensor_lock:
            acceleration = self._latest_acc
            self._latest_acc = None
        if (
            acceleration is not None
            and acceleration[0].shape[1] == data.shape[1]
        ):
            timestamps = acceleration[1]
        else:
            timestamps = self._sample_clocks['imu'].timestamps(data.shape[1])

        for index in range(data.shape[1]):
            try:
                has_acceleration = self._matching_acceleration(
                    acceleration,
                    timestamps,
                    index,
                )
                if has_acceleration:
                    acc_data = acceleration[0]
                    linear_acceleration = tuple(
                        float(acc_data[axis, index]) * STANDARD_GRAVITY
                        for axis in range(3)
                    )
                else:
                    linear_acceleration = (0.0, 0.0, 0.0)
                sample = ImuSample(
                    timestamp=self._timestamp_at(timestamps, index),
                    operator_id=self.operator_id,
                    angular_velocity=tuple(
                        math.radians(float(data[axis, index]))
                        for axis in range(3)
                    ),
                    linear_acceleration=linear_acceleration,
                    acceleration_available=has_acceleration,
                    sampling_rate_hz=IMU_RATE_HZ,
                )
                self._enqueue_sample('imu', sample)
            except Exception:
                self._record_drop('imu')

    def _optics_callback(self, data, _timestamps):
        timestamps = self._sample_clocks['ppg'].timestamps(data.shape[1])
        for index in range(data.shape[1]):
            try:
                sample = PpgSample(
                    timestamp=self._timestamp_at(timestamps, index),
                    operator_id=self.operator_id,
                    sampling_rate_hz=PPG_RATE_HZ,
                    data=tuple(float(value) for value in data[:16, index]),
                )
                self._enqueue_sample('ppg', sample)
            except Exception:
                self._record_drop('ppg')

    @staticmethod
    def _timestamp_at(timestamps, index):
        if index < len(timestamps):
            return float(timestamps[index])
        return time.time()

    @staticmethod
    def _matching_acceleration(acceleration, gyro_timestamps, index):
        if acceleration is None:
            return False
        acc_data, acc_timestamps = acceleration
        if index >= acc_data.shape[1] or index >= len(acc_timestamps):
            return False
        if index >= len(gyro_timestamps):
            return False
        return abs(
            float(acc_timestamps[index]) - float(gyro_timestamps[index])
        ) < 0.02

    def _enqueue_sample(self, stream, sample):
        with self._stats_lock:
            self._received[stream] += 1
        try:
            self._queues[stream].put_nowait(sample)
        except queue.Full:
            self._record_drop(stream)

    def _record_drop(self, stream):
        with self._stats_lock:
            self._dropped[stream] += 1

    def _queue_status(self, state, **details):
        status = DeviceStatus(
            state=state,
            timestamp=time.time(),
            operator_id=self.operator_id,
            mac_address=self.mac_address,
            adapter=self.hci_device,
            details=details,
        )
        try:
            self._status_queue.put_nowait(status)
        except queue.Full:
            try:
                self._status_queue.get_nowait()
                self._status_queue.put_nowait(status)
            except (queue.Empty, queue.Full):
                pass

    def _on_disconnect(self, _client):
        with self._lock:
            was_streaming = self.streaming
            self.streaming = False
            self._last_data_time = 0.0
            self._stream_started_at = None
            shutting_down = self._shutting_down
        if was_streaming and not shutting_down:
            self._log('warning', 'Desconexión GATT detectada')
            self._queue_status('disconnected')

    def _connect(self):
        muse = None
        try:
            self._queue_status('connecting')
            self._log(
                'info',
                f'Conectando a {self.mac_address} mediante {self.hci_device}',
            )
            factory = self._adapter_factory
            if factory is None:
                from muse_hrc.athena_adapter import AdapterAthena
                factory = AdapterAthena
            muse = factory(
                address=self.mac_address,
                hci_device=self.hci_device,
                disconnected_callback=self._on_disconnect,
                callback_eeg=self._eeg_callback,
                callback_acc=self._acc_callback,
                callback_gyro=self._imu_callback,
                callback_optics=self._optics_callback,
            )
            with self._lock:
                self.muse = muse
            if not muse.connect():
                raise RuntimeError('Athena no pudo establecer la conexión BLE')
            self._queue_status('connected')
            muse.start()
            with self._lock:
                self.streaming = True
                self._last_data_time = time.monotonic()
                self._stream_started_at = time.time()
                self._stream_status_sent = False
            self._log('info', 'CONEXIÓN EXITOSA, STREAMING INICIADO')
            while self._connection_is_active(muse):
                muse.adapter.pump(BLE_PUMP_SECONDS)
        except Exception as error:
            with self._lock:
                shutting_down = self._shutting_down
            if not shutting_down:
                self._log('error', str(error))
                self._queue_status('error', message=str(error))
        finally:
            with self._lock:
                self.streaming = False
                self._stream_started_at = None
            self._disconnect_muse(muse)
            with self._lock:
                if self.muse is muse:
                    self.muse = None
                self._connecting = False
                if not self._shutting_down:
                    self._lifecycle_finished = True

    def _connection_is_active(self, muse):
        with self._lock:
            return (
                not self._shutting_down
                and self.streaming
                and self.muse is muse
            )

    @staticmethod
    def _disconnect_muse(muse):
        if muse is None:
            return
        try:
            muse.stop()
        except Exception:
            pass
        try:
            muse.disconnect()
        except Exception:
            pass

    def _log(self, level, message):
        if self._log_callback is not None:
            self._log_callback(level, f'[{self.operator_id}] {message}')
