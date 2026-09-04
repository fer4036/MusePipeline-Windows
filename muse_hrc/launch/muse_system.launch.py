from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'hci_devices',
            default_value='hci0,hci1,hci2,hci3',
            description='Pool ordenado de adaptadores BlueZ para las diademas',
        ),
        DeclareLaunchArgument(
            'muse_manufacturer_ids',
            default_value='',
            description='IDs de fabricante Muse adicionales, separados por coma',
        ),
        DeclareLaunchArgument(
            'muse_python',
            default_value='auto',
            description='Python con rclpy y muselsl.athena; auto lo detecta',
        ),
        DeclareLaunchArgument(
            'database_path',
            default_value='~/muse_telemetry.db',
            description='Ruta del archivo SQLite de telemetría',
        ),
        DeclareLaunchArgument(
            'recording_enabled',
            default_value='true',
            description='Guardar muestras inmediatamente al iniciar',
        ),
        Node(
            package='muse_hrc',
            executable='database_node',
            name='central_database',
            output='screen',
            parameters=[{
                'db_path': LaunchConfiguration('database_path'),
                'recording_enabled': LaunchConfiguration('recording_enabled'),
            }],
        ),
        Node(
            package='muse_hrc',
            executable='discovery_node',
            name='auto_discovery',
            output='screen',
            parameters=[{
                'hci_devices': LaunchConfiguration('hci_devices'),
                'muse_manufacturer_ids': LaunchConfiguration(
                    'muse_manufacturer_ids'
                ),
                'muse_python': LaunchConfiguration('muse_python'),
            }],
        )
    ])
