"""ROS 2 adapter for the transport-independent SQLite telemetry store."""

import rclpy
from muse_msgs.msg import EegSample as RosEegSample
from muse_msgs.msg import PpgSample as RosPpgSample
from rclpy.node import Node
from sensor_msgs.msg import Imu as RosImuSample
from std_srvs.srv import SetBool

from muse_hrc.models import EegSample, ImuSample, PpgSample
from muse_hrc.storage import TelemetryStore


BATCH_INTERVAL_SECONDS = 0.1


class DatabaseNode(Node):
    """Discover ROS sensor topics and persist their pure Python equivalents."""

    def __init__(self):
        super().__init__('database_node')
        self.declare_parameter('db_path', '~/muse_telemetry.db')
        self.declare_parameter('recording_enabled', True)
        db_path = self.get_parameter(
            'db_path'
        ).get_parameter_value().string_value
        recording_enabled = self.get_parameter(
            'recording_enabled'
        ).get_parameter_value().bool_value
        self.store = TelemetryStore(
            db_path=db_path,
            recording_enabled=recording_enabled,
            error_callback=self.get_logger().error,
        )
        self.suscripciones_activas = {}
        self.create_timer(3.0, self.discover_operators_callback)
        self.create_timer(BATCH_INTERVAL_SECONDS, self.store.flush)
        self.create_timer(10.0, self._report_metrics)
        self.create_service(
            SetBool,
            '~/set_recording',
            self._set_recording_callback,
        )
        self.get_logger().info(
            'Nodo de base de datos listo. Guardado en: '
            f'{self.store.db_path}'
        )
        self.get_logger().info(
            'Grabación inicial: '
            f"{'ACTIVA' if self.store.recording_enabled else 'EN ESPERA'}"
        )

    def discover_operators_callback(self):
        for topic_name, _topic_types in self.get_topic_names_and_types():
            if not topic_name.endswith('/eeg'):
                continue
            operator_id = topic_name.split('/')[1]
            if operator_id in self.suscripciones_activas:
                continue
            self.get_logger().info(
                f'NUEVO OPERADOR: {operator_id}, CREANDO SUSCRIPCIONES'
            )
            subscriptions = [
                self.create_subscription(
                    RosEegSample,
                    f'/{operator_id}/eeg',
                    lambda message, op=operator_id: self.save_eeg(message, op),
                    1000,
                ),
                self.create_subscription(
                    RosImuSample,
                    f'/{operator_id}/imu',
                    lambda message, op=operator_id: self.save_imu(message, op),
                    1000,
                ),
                self.create_subscription(
                    RosPpgSample,
                    f'/{operator_id}/ppg',
                    lambda message, op=operator_id: self.save_ppg(message, op),
                    1000,
                ),
            ]
            self.suscripciones_activas[operator_id] = subscriptions

    def save_eeg(self, message, operator_id):
        try:
            self.store.add_eeg(EegSample(
                timestamp=self._timestamp(message.header.stamp),
                operator_id=operator_id,
                sampling_rate_hz=message.sampling_rate_hz,
                data=tuple(message.data),
            ))
        except Exception as error:
            self.get_logger().error(
                f'Error al guardar EEG de {operator_id}: {error}'
            )

    def save_imu(self, message, operator_id):
        try:
            self.store.add_imu(ImuSample(
                timestamp=self._timestamp(message.header.stamp),
                operator_id=operator_id,
                angular_velocity=(
                    message.angular_velocity.x,
                    message.angular_velocity.y,
                    message.angular_velocity.z,
                ),
                linear_acceleration=(
                    message.linear_acceleration.x,
                    message.linear_acceleration.y,
                    message.linear_acceleration.z,
                ),
                acceleration_available=(
                    message.linear_acceleration_covariance[0] != -1.0
                ),
            ))
        except Exception as error:
            self.get_logger().error(
                f'Error al guardar IMU de {operator_id}: {error}'
            )

    def save_ppg(self, message, operator_id):
        try:
            self.store.add_ppg(PpgSample(
                timestamp=self._timestamp(message.header.stamp),
                operator_id=operator_id,
                sampling_rate_hz=message.sampling_rate_hz,
                data=tuple(message.data),
            ))
        except Exception as error:
            self.get_logger().error(
                f'Error al guardar PPG de {operator_id}: {error}'
            )

    @staticmethod
    def _timestamp(stamp):
        return float(stamp.sec) + float(stamp.nanosec) / 1_000_000_000.0

    def _set_recording_callback(self, request, response):
        requested = bool(request.data)
        changed = self.store.set_recording(requested)
        if not changed:
            state = 'activa' if requested else 'detenida'
            response.success = True
            response.message = f'La grabación ya estaba {state}'
            return response
        state = 'INICIADA' if requested else 'DETENIDA'
        self.get_logger().info(
            f'GRABACIÓN {state} por solicitud de usuario'
        )
        response.success = True
        response.message = (
            'Grabación iniciada' if requested else 'Grabación detenida'
        )
        return response

    def _report_metrics(self):
        metrics = self.store.metrics()
        saved = metrics['saved']
        self.get_logger().info(
            'Persistencia: '
            f"EEG={saved['eeg']}, IMU={saved['imu']}, PPG={saved['ppg']}, "
            f"grabando={metrics['recording']}, pendientes={metrics['pending']}"
        )

    def destroy_node(self):
        self.store.close()
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
