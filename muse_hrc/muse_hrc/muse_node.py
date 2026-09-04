"""ROS 2 bridge for the transport-independent Muse acquisition core."""

import json

import rclpy
from muse_msgs.msg import EegSample as RosEegSample
from muse_msgs.msg import PpgSample as RosPpgSample
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Imu as RosImuSample
from std_msgs.msg import String

from muse_hrc.acquisition import MuseDeviceSession


MAX_FLUSH_PER_STREAM = 512
METRICS_SECONDS = 10.0


class MuseNode(Node):
    """Publish a :class:`MuseDeviceSession` through ROS topics."""

    def __init__(self):
        import sys

        operator_id = 'operador_generico'
        for argument in sys.argv:
            if 'operador_id:=' in argument:
                operator_id = argument.split(':=')[1]
        super().__init__(f'muse_{operator_id}')

        self.declare_parameter('operador_id', 'operador_generico')
        self.declare_parameter('mac_address', '00:00:00:00:00:00')
        self.declare_parameter('hci_device', 'hci0')
        self.operador = self.get_parameter(
            'operador_id'
        ).get_parameter_value().string_value
        self.mac = self.get_parameter(
            'mac_address'
        ).get_parameter_value().string_value
        self.hci = self.get_parameter(
            'hci_device'
        ).get_parameter_value().string_value

        self.eeg_pub = self.create_publisher(
            RosEegSample, f'/{self.operador}/eeg', 1000
        )
        self.imu_pub = self.create_publisher(
            RosImuSample, f'/{self.operador}/imu', 1000
        )
        self.ppg_pub = self.create_publisher(
            RosPpgSample, f'/{self.operador}/ppg', 1000
        )
        self.status_pub = self.create_publisher(
            String, f'/{self.operador}/status', 20
        )

        self.session = MuseDeviceSession(
            operator_id=self.operador,
            mac_address=self.mac,
            hci_device=self.hci,
            log_callback=self._log_from_core,
        )
        self.create_timer(0.001, self._flush_queues)
        self.create_timer(1.0, self._check_connection)
        self.create_timer(METRICS_SECONDS, self.session.report_metrics)
        self.session.start()
        self.get_logger().info(
            f'[{self.operador}] Puente ROS iniciado — MAC: {self.mac}, '
            f'adaptador: {self.hci}'
        )

    def _flush_queues(self):
        for status in self.session.drain_status():
            message = String()
            message.data = json.dumps(status.as_dict())
            self.status_pub.publish(message)

        conversions = (
            ('eeg', self.eeg_pub, self._to_ros_eeg),
            ('imu', self.imu_pub, self._to_ros_imu),
            ('ppg', self.ppg_pub, self._to_ros_ppg),
        )
        for stream, publisher, converter in conversions:
            for sample in self.session.drain(stream, MAX_FLUSH_PER_STREAM):
                try:
                    publisher.publish(converter(sample))
                except Exception as error:
                    self.get_logger().error(
                        f'[{self.operador}] Error publicando {stream}: {error}'
                    )

    def _to_ros_eeg(self, sample):
        message = RosEegSample()
        self._set_header(message.header, sample.timestamp, 'eeg')
        message.operator_id = sample.operator_id
        message.sampling_rate_hz = int(sample.sampling_rate_hz)
        message.data = list(sample.data)
        return message

    def _to_ros_imu(self, sample):
        message = RosImuSample()
        self._set_header(message.header, sample.timestamp, 'imu')
        message.orientation_covariance[0] = -1.0
        message.angular_velocity.x = sample.angular_velocity[0]
        message.angular_velocity.y = sample.angular_velocity[1]
        message.angular_velocity.z = sample.angular_velocity[2]
        message.linear_acceleration.x = sample.linear_acceleration[0]
        message.linear_acceleration.y = sample.linear_acceleration[1]
        message.linear_acceleration.z = sample.linear_acceleration[2]
        if not sample.acceleration_available:
            message.linear_acceleration_covariance[0] = -1.0
        return message

    def _to_ros_ppg(self, sample):
        message = RosPpgSample()
        self._set_header(message.header, sample.timestamp, 'ppg')
        message.operator_id = sample.operator_id
        message.sampling_rate_hz = int(sample.sampling_rate_hz)
        message.data = list(sample.data)
        return message

    def _set_header(self, header, timestamp, sensor):
        seconds = int(timestamp)
        nanoseconds = int(round((timestamp - seconds) * 1_000_000_000))
        if nanoseconds >= 1_000_000_000:
            seconds += 1
            nanoseconds -= 1_000_000_000
        header.stamp.sec = seconds
        header.stamp.nanosec = nanoseconds
        header.frame_id = f'muse_{self.operador}/{sensor}'

    def _check_connection(self):
        if not self.session.poll():
            return
        self.get_logger().warn(
            f'[{self.operador}] Ciclo BLE terminado; devolviendo el '
            'reintento a auto_discovery'
        )
        self._flush_queues()
        rclpy.shutdown()

    def _log_from_core(self, level, message):
        logger = self.get_logger()
        method = {
            'warning': logger.warning,
            'error': logger.error,
        }.get(level, logger.info)
        method(message)

    def destroy_node(self):
        self.session.stop()
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
