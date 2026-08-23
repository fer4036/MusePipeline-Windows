import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from std_msgs.msg import String
from sensor_msgs.msg import Imu
from muse_msgs.msg import EegSample, PpgSample
from muse_hrc.athena_protocol import HostSampleClock
import json
import math
import time
import threading
import queue
from muse_hrc.athena_adapter import AdapterAthena

DATA_TIMEOUT_SECONDS = 10.0
BLE_PUMP_SECONDS = 0.1
METRICS_SECONDS = 10.0
MAX_FLUSH_PER_STREAM = 512
STANDARD_GRAVITY = 9.80665
EEG_RATE_HZ = 256
PPG_RATE_HZ = 64
IMU_RATE_HZ = 52


class MuseNode(Node):
    def __init__(self):
        import sys
        operador_id = 'operador_generico'
        for arg in sys.argv:
            if 'operador_id:=' in arg:
                operador_id = arg.split(':=')[1]

        super().__init__(f'muse_{operador_id}')

        self.declare_parameter('operador_id', 'operador_generico')
        self.declare_parameter('mac_address', '00:00:00:00:00:00')
        self.declare_parameter('hci_device', 'hci0')
        self.operador = self.get_parameter('operador_id').get_parameter_value().string_value
        self.mac = self.get_parameter('mac_address').get_parameter_value().string_value
        self.hci = self.get_parameter('hci_device').get_parameter_value().string_value

        self.eeg_pub = self.create_publisher(
            EegSample, f'/{self.operador}/eeg', 1000
        )
        self.imu_pub = self.create_publisher(
            Imu, f'/{self.operador}/imu', 1000
        )
        self.ppg_pub = self.create_publisher(
            PpgSample, f'/{self.operador}/ppg', 1000
        )
        self.status_pub = self.create_publisher(
            String, f'/{self.operador}/status', 20
        )

        self._eeg_queue = queue.Queue(maxsize=5000)
        self._imu_queue = queue.Queue(maxsize=5000)
        self._ppg_queue = queue.Queue(maxsize=5000)
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
        self._published = {'eeg': 0, 'imu': 0, 'ppg': 0}
        self._dropped = {'eeg': 0, 'imu': 0, 'ppg': 0}
        self._metrics_previous = dict(self._received)
        self._metrics_previous_published = dict(self._published)
        self._metrics_previous_time = time.monotonic()

        self.muse = None
        self.streaming = False
        self._connecting = False
        self._lock = threading.Lock()
        self._last_data_time = 0
        self._stream_started_at = None
        self._stream_status_sent = False
        self._shutting_down = False
        self._lifecycle_finished = False
        self._shutdown_requested = False
        self._connection_thread = None

        self.create_timer(0.001, self._flush_queues)  # 1000 Hz
        self.create_timer(1.0, self._check_connection)
        self.create_timer(METRICS_SECONDS, self._report_metrics)

        self._start_connection_thread()

        self.get_logger().info(
            f"[{self.operador}] MuseNode iniciado — MAC: {self.mac}, adaptador: {self.hci}"
        )

    def _eeg_callback(self, data, timestamps):
        timestamps = self._sample_clocks['eeg'].timestamps(data.shape[1])
        with self._lock:
            self._last_data_time = time.monotonic()
            announce_stream = self.streaming and not self._stream_status_sent
            if announce_stream:
                self._stream_status_sent = True
        if announce_stream:
            self._queue_status('streaming', adapter=self.hci, mac=self.mac)
        if not hasattr(self, '_batch_sizes'):
            self._batch_sizes = []
        self._batch_sizes.append(data.shape[1])
        if len(self._batch_sizes) % 100 == 0:
            avg = sum(self._batch_sizes[-100:]) / 100
            self.get_logger().info(
                f"[{self.operador}] batch promedio: {avg:.1f} muestras, "
                f"total callbacks: {len(self._batch_sizes)}"
            )
        for i in range(data.shape[1]):
            try:
                timestamp = (
                    float(timestamps[i])
                    if i < len(timestamps) else time.time()
                )
                msg = EegSample()
                self._set_header(msg.header, timestamp, 'eeg')
                msg.operator_id = self.operador
                msg.sampling_rate_hz = EEG_RATE_HZ
                msg.data = [float(value) for value in data[:4, i]]
                self._enqueue_sample('eeg', self._eeg_queue, msg)
            except Exception:
                self._record_drop('eeg')

    def _acc_callback(self, data, timestamps):
        """Cache acceleration; Athena delivers matching gyro data next."""
        timestamps = self._sample_clocks['imu'].timestamps(data.shape[1])
        with self._sensor_lock:
            self._latest_acc = (data.copy(), list(timestamps))

    def _imu_callback(self, data, timestamps):
        with self._sensor_lock:
            acceleration = self._latest_acc
            self._latest_acc = None

        if acceleration is not None and acceleration[0].shape[1] == data.shape[1]:
            timestamps = acceleration[1]
        else:
            timestamps = self._sample_clocks['imu'].timestamps(data.shape[1])

        for i in range(data.shape[1]):
            try:
                timestamp = (
                    float(timestamps[i])
                    if i < len(timestamps) else time.time()
                )
                msg = Imu()
                self._set_header(msg.header, timestamp, 'imu')
                msg.orientation_covariance[0] = -1.0
                msg.angular_velocity.x = math.radians(float(data[0, i]))
                msg.angular_velocity.y = math.radians(float(data[1, i]))
                msg.angular_velocity.z = math.radians(float(data[2, i]))

                if self._matching_acceleration(acceleration, timestamps, i):
                    acc_data = acceleration[0]
                    msg.linear_acceleration.x = (
                        float(acc_data[0, i]) * STANDARD_GRAVITY
                    )
                    msg.linear_acceleration.y = (
                        float(acc_data[1, i]) * STANDARD_GRAVITY
                    )
                    msg.linear_acceleration.z = (
                        float(acc_data[2, i]) * STANDARD_GRAVITY
                    )
                else:
                    msg.linear_acceleration_covariance[0] = -1.0

                self._enqueue_sample('imu', self._imu_queue, msg)
            except Exception:
                self._record_drop('imu')

    def _optics_callback(self, data, timestamps):
        """
        Enqueue the latest raw optical sample.

        ``data`` contains 16 float32 optical channels by sample. These data can
        be used to derive heart rate in a downstream processing node.
        """
        timestamps = self._sample_clocks['ppg'].timestamps(data.shape[1])
        for i in range(data.shape[1]):
            try:
                timestamp = (
                    float(timestamps[i])
                    if i < len(timestamps) else time.time()
                )
                msg = PpgSample()
                self._set_header(msg.header, timestamp, 'ppg')
                msg.operator_id = self.operador
                msg.sampling_rate_hz = PPG_RATE_HZ
                msg.data = [float(value) for value in data[:16, i]]
                self._enqueue_sample('ppg', self._ppg_queue, msg)
            except Exception:
                self._record_drop('ppg')

    def _set_header(self, header, timestamp, sensor):
        seconds = int(timestamp)
        nanoseconds = int(round((timestamp - seconds) * 1_000_000_000))
        if nanoseconds >= 1_000_000_000:
            seconds += 1
            nanoseconds -= 1_000_000_000
        header.stamp.sec = seconds
        header.stamp.nanosec = nanoseconds
        header.frame_id = f'muse_{self.operador}/{sensor}'

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

    def _enqueue_sample(self, stream, target_queue, payload):
        """Queue one sensor sample and account for overload explicitly."""
        with self._stats_lock:
            self._received[stream] += 1
        try:
            target_queue.put_nowait(payload)
        except queue.Full:
            self._record_drop(stream)

    def _record_drop(self, stream):
        with self._stats_lock:
            self._dropped[stream] += 1

    def _queue_status(self, state, **details):
        payload = {'state': state, 'timestamp': time.time(), **details}
        try:
            self._status_queue.put_nowait(json.dumps(payload))
        except queue.Full:
            try:
                self._status_queue.get_nowait()
                self._status_queue.put_nowait(json.dumps(payload))
            except (queue.Empty, queue.Full):
                pass

    def _on_disconnect(self, _client):
        """Bleak callback: mark the stream down as soon as GATT is lost."""
        with self._lock:
            was_streaming = self.streaming
            self.streaming = False
            self._last_data_time = 0.0
            self._stream_started_at = None
            shutting_down = self._shutting_down
        if was_streaming and not shutting_down:
            self.get_logger().warn(
                f"[{self.operador}] Desconexión GATT detectada en {self.hci}"
            )
            self._queue_status('disconnected', adapter=self.hci)

    def _flush_queues(self):
        while not self._status_queue.empty():
            try:
                msg = String()
                msg.data = self._status_queue.get_nowait()
                self.status_pub.publish(msg)
            except queue.Empty:
                break

        for q, pub, nombre, stream in [
            (self._eeg_queue, self.eeg_pub, 'EEG', 'eeg'),
            (self._imu_queue, self.imu_pub, 'IMU', 'imu'),
            (self._ppg_queue, self.ppg_pub, 'PPG', 'ppg'),
        ]:
            flushed = 0
            while flushed < MAX_FLUSH_PER_STREAM:
                try:
                    msg = q.get_nowait()
                    pub.publish(msg)
                    flushed += 1
                    with self._stats_lock:
                        self._published[stream] += 1
                except queue.Empty:
                    break
                except Exception as e:
                    self.get_logger().error(
                        f"[{self.operador}] Error publicando {nombre}: {e}"
                    )

    def _report_metrics(self):
        now = time.monotonic()
        elapsed = max(now - self._metrics_previous_time, 0.001)
        with self._stats_lock:
            received = dict(self._received)
            published = dict(self._published)
            dropped = dict(self._dropped)
        rates = {
            stream: round(
                (received[stream] - self._metrics_previous[stream]) / elapsed,
                1,
            )
            for stream in received
        }
        published_rates = {
            stream: round(
                (
                    published[stream] -
                    self._metrics_previous_published[stream]
                ) / elapsed,
                1,
            )
            for stream in published
        }
        self._metrics_previous = received
        self._metrics_previous_published = published
        self._metrics_previous_time = now
        with self._lock:
            muse = self.muse
            streaming = self.streaming
            stream_started_at = self._stream_started_at
        battery = getattr(muse, '_battery', 0.0) if muse is not None else 0.0
        self._queue_status(
            'metrics',
            streaming=streaming,
            connected_since=stream_started_at,
            battery_percent=(
                min(100.0, round(float(battery), 1)) if battery > 0 else None
            ),
            received_hz=rates,
            published_hz=published_rates,
            received=received,
            published=published,
            dropped=dropped,
            queued={
                'eeg': self._eeg_queue.qsize(),
                'imu': self._imu_queue.qsize(),
                'ppg': self._ppg_queue.qsize(),
            },
        )

    def _start_connection_thread(self):
        """Start exactly one BLE lifecycle thread."""
        with self._lock:
            if self._connecting or self._shutting_down:
                return False
            self._connecting = True
            thread = threading.Thread(target=self._connect, daemon=True)
            self._connection_thread = thread
            thread.start()
            return True

    def _connect(self):
        """Own the Bleak event loop for one complete connection lifecycle."""
        muse = None
        try:
            self._queue_status('connecting', adapter=self.hci, mac=self.mac)
            self.get_logger().info(
                f"[{self.operador}] Conectando a {self.mac} mediante {self.hci}..."
            )

            muse = AdapterAthena(
                address=self.mac,
                hci_device=self.hci,
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
            self._queue_status('connected', adapter=self.hci, mac=self.mac)
            muse.start()
            with self._lock:
                self.streaming = True
                self._last_data_time = time.monotonic()
                self._stream_started_at = time.time()
                self._stream_status_sent = False
            self.get_logger().info(
                f"[{self.operador}] CONEXION EXITOSA, STREAMING INICIADO"
            )

            while self._connection_is_active(muse):
                # muselsl's synchronous Bleak backend only delivers notify and
                # disconnect callbacks while its asyncio loop is being pumped.
                muse.adapter.pump(BLE_PUMP_SECONDS)

        except Exception as error:
            with self._lock:
                shutting_down = self._shutting_down
            if not shutting_down:
                self.get_logger().error(f"[{self.operador}] ERROR: {error}")
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
                not self._shutting_down and
                self.streaming and
                self.muse is muse
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

    def _check_connection(self):
        now = time.monotonic()
        with self._lock:
            streaming = self.streaming
            connecting = self._connecting
            shutting_down = self._shutting_down
            lifecycle_finished = self._lifecycle_finished
            last_data_time = self._last_data_time
            muse_missing = self.muse is None
            sin_datos = (
                streaming and
                (now - last_data_time) > DATA_TIMEOUT_SECONDS
            )
            if sin_datos or (streaming and muse_missing):
                # The lifecycle thread performs cleanup on its own event loop.
                self.streaming = False
                self._stream_started_at = None

        if shutting_down:
            return
        if lifecycle_finished and not connecting:
            with self._lock:
                if self._shutdown_requested:
                    return
                self._shutdown_requested = True
            self.get_logger().warn(
                f"[{self.operador}] Ciclo BLE terminado; "
                "devolviendo el reintento a auto_discovery"
            )
            self._queue_status(
                'retry_required', adapter=self.hci, mac=self.mac
            )
            # Flush the machine-readable state before ending this child. The
            # discovery process owns retry timing and adapter allocation.
            self._flush_queues()
            rclpy.shutdown()
            return
        if sin_datos:
            self.get_logger().warn(
                f"[{self.operador}] Sin datos hace {now - last_data_time:.0f}s — "
                "conexión BLE perdida; solicitando reintento coordinado..."
            )
            self._queue_status(
                'data_timeout', seconds=round(now - last_data_time, 1)
            )
            return
        # There is deliberately no reconnect here. Running two independent
        # retry loops caused rapid GATT attempts and stale BlueZ state. The
        # parent discovery node relaunches this one-shot process with backoff.

    def destroy_node(self):
        with self._lock:
            self._shutting_down = True
            self.streaming = False
            thread = self._connection_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MuseNode()
    try:
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
