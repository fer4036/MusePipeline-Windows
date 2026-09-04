"""Tests for ROS-independent Muse discovery and adapter supervision."""

from types import SimpleNamespace
import json
import sqlite3
import time
from unittest.mock import Mock

from muse_hrc.auto_discovery import AutomaticDeviceSupervisor
from muse_hrc.bluez import BlueZBackend, parse_hci_controllers
from muse_hrc.device_supervisor import AdapterPool, DeviceRegistry
from muse_hrc.models import DeviceStatus
from muse_hrc.standalone import (
    FileRecordingControl,
    StandaloneCollector,
    build_parser,
)
from muse_hrc.storage import TelemetryStore


class FakeBlueZ:
    def __init__(self, detected, muse_addresses):
        self.detected = detected
        self.muse_addresses = set(muse_addresses)
        self.scans = []
        self.prepared = []
        self.presence_checks = []

    def detect_adapters(self):
        return dict(self.detected)

    def scan(self, adapter, address, duration):
        self.scans.append((adapter, address, duration))
        return set(self.muse_addresses)

    def prepare_device(self, mac_address, adapter, address):
        self.prepared.append((mac_address, adapter, address))
        return True

    def is_visible(self, mac_address, adapter, address, duration):
        self.presence_checks.append(
            (mac_address, adapter, address, duration)
        )
        return mac_address.lower() in self.muse_addresses


def test_registry_keeps_mac_operator_identity_for_the_session():
    registry = DeviceRegistry()

    first = registry.register({'00:55:da:00:00:02', '00:55:da:00:00:01'})
    again = registry.register({'00:55:da:00:00:01'})

    assert [record.operator_id for record in first] == [
        'operador_a',
        'operador_b',
    ]
    assert again[0] is first[0]


def test_adapter_pool_preserves_valid_assignments_across_hotplug_snapshot():
    pool = AdapterPool(('hci1', 'hci2'))
    pool.reconcile({'hci1': 'AA', 'hci2': 'BB'})
    assert pool.reserve('operador_a') == 'hci1'
    assert pool.reserve('operador_b') == 'hci2'

    removed = pool.reconcile({'hci2': 'BB'})

    assert removed == ['operador_a']
    assert pool.assigned == {'operador_b': 'hci2'}
    assert pool.free == ()


def test_automatic_supervisor_discovers_and_assigns_one_hci_per_muse():
    backend = FakeBlueZ(
        {'hci1': 'AA', 'hci2': 'BB'},
        {'00:55:da:00:00:02', '00:55:da:00:00:01'},
    )
    supervisor = AutomaticDeviceSupervisor(
        ('hci1', 'hci2'),
        scan_seconds=0.1,
        bluez_backend=backend,
    )

    configurations = supervisor.discover()

    assert configurations == [
        ('operador_a', '00:55:da:00:00:01', 'hci2'),
        ('operador_b', '00:55:da:00:00:02', 'hci1'),
    ]
    assert backend.scans == [('hci1', 'AA', 0.1)]
    assert len(backend.prepared) == 2


def test_automatic_supervisor_limits_devices_to_available_adapters():
    backend = FakeBlueZ(
        {'hci1': 'AA'},
        {'00:55:da:00:00:02', '00:55:da:00:00:01'},
    )
    supervisor = AutomaticDeviceSupervisor(
        ('hci1',),
        bluez_backend=backend,
    )

    assert len(supervisor.discover()) == 1


def test_repeated_scan_only_returns_headbands_added_mid_session():
    backend = FakeBlueZ(
        {'hci1': 'AA', 'hci2': 'BB'},
        {'00:55:da:00:00:01'},
    )
    supervisor = AutomaticDeviceSupervisor(
        ('hci1', 'hci2'),
        bluez_backend=backend,
    )

    assert supervisor.discover() == [
        ('operador_a', '00:55:da:00:00:01', 'hci2')
    ]
    backend.muse_addresses.add('00:55:da:00:00:02')

    assert supervisor.discover() == [
        ('operador_b', '00:55:da:00:00:02', 'hci1')
    ]
    assert supervisor.registry.get(
        '00:55:da:00:00:01'
    ).operator_id == 'operador_a'


def test_collector_queues_new_devices_found_by_background_scan():
    backend = FakeBlueZ(
        {'hci1': 'AA'},
        {'00:55:da:00:00:01'},
    )
    supervisor = AutomaticDeviceSupervisor(
        ('hci1',),
        bluez_backend=backend,
    )
    store = TelemetryStore(
        connection=sqlite3.connect(':memory:'),
        recording_enabled=False,
    )
    collector = StandaloneCollector([], store, discovery_supervisor=supervisor)

    collector._run_hot_scan()
    collector._accept_discovered_devices()

    assert collector.devices == [
        ('operador_a', '00:55:da:00:00:01', 'hci1')
    ]
    assert collector._pending_starts.qsize() == 1


def test_collector_does_not_retry_or_connect_while_scan_is_active():
    store = TelemetryStore(
        connection=sqlite3.connect(':memory:'),
        recording_enabled=False,
    )
    configuration = ('operador_a', '00:55:da:00:00:01', 'hci1')
    collector = StandaloneCollector([configuration], store)
    collector._scan_thread = SimpleNamespace(is_alive=lambda: True)
    collector.next_retry['operador_a'] = time.monotonic() - 1
    collector._pending_starts.put(configuration)
    started = []
    collector._start_one = started.append

    collector._retry_ready_sessions()
    collector._start_pending_device()

    assert started == []
    assert collector.next_retry['operador_a'] != 0.0
    assert collector._pending_starts.qsize() == 1


def test_successful_reconnection_resets_backoff_and_starts_settle_window():
    store = TelemetryStore(
        connection=sqlite3.connect(':memory:'),
        recording_enabled=False,
    )
    payloads = []
    configuration = ('operador_a', '00:55:da:00:00:01', 'hci1')
    collector = StandaloneCollector(
        [configuration],
        store,
        status_callback=payloads.append,
        connection_settle_seconds=5.0,
    )
    collector.retry_attempts['operador_a'] = 3
    collector.next_retry['operador_a'] = time.monotonic() + 20

    collector._handle_status(
        'operador_a',
        DeviceStatus(
            'streaming', time.time(), 'operador_a',
            '00:55:da:00:00:01', 'hci1',
        ),
    )

    assert collector.retry_attempts['operador_a'] == 0
    assert collector.next_retry['operador_a'] == 0.0
    assert collector.reconnect_counts['operador_a'] == 1
    assert collector._connection_is_settling()
    assert payloads[-1]['reconnect_count'] == 1


def test_disconnect_counter_is_included_in_subsequent_metrics():
    store = TelemetryStore(
        connection=sqlite3.connect(':memory:'),
        recording_enabled=False,
    )
    payloads = []
    configuration = ('operador_a', '00:55:da:00:00:01', 'hci1')
    collector = StandaloneCollector(
        [configuration], store, status_callback=payloads.append
    )
    collector._handle_status(
        'operador_a',
        DeviceStatus('disconnected', time.time(), 'operador_a'),
    )
    collector._handle_status(
        'operador_a',
        DeviceStatus('metrics', time.time(), 'operador_a'),
    )

    assert collector.disconnect_counts['operador_a'] == 1
    assert payloads[-1]['disconnect_count'] == 1


def test_retry_waits_for_same_mac_to_advertise_before_gatt_connection():
    mac_address = '00:55:da:00:00:01'
    backend = FakeBlueZ({'hci1': 'AA'}, set())
    supervisor = AutomaticDeviceSupervisor(
        ('hci1',), bluez_backend=backend
    )
    store = TelemetryStore(
        connection=sqlite3.connect(':memory:'),
        recording_enabled=False,
    )
    payloads = []
    configuration = ('operador_a', mac_address, 'hci1')
    collector = StandaloneCollector(
        [configuration],
        store,
        status_callback=payloads.append,
        discovery_supervisor=supervisor,
        connection_settle_seconds=0.0,
        retry_presence_scan_seconds=1.0,
    )
    collector.next_retry['operador_a'] = time.monotonic() - 1
    started = []
    collector._start_one = started.append

    collector._retry_ready_sessions()

    assert started == []
    assert payloads[-1]['state'] == 'waiting_for_device'
    assert backend.presence_checks[-1] == (
        mac_address, 'hci1', 'AA', 1.0
    )

    backend.muse_addresses.add(mac_address)
    collector.next_retry['operador_a'] = time.monotonic() - 1
    collector._retry_ready_sessions()
    assert started == []
    assert 'operador_a' in collector._visible_retries

    collector.next_retry['operador_a'] = time.monotonic() - 1
    collector._retry_ready_sessions()
    assert started == [configuration]


def test_supervisor_presence_check_uses_assigned_controller():
    backend = FakeBlueZ(
        {'hci2': 'BB'}, {'00:55:da:00:00:01'}
    )
    supervisor = AutomaticDeviceSupervisor(
        ('hci2',), bluez_backend=backend
    )

    assert supervisor.is_visible(
        '00:55:da:00:00:01', 'hci2', duration=2.0
    ) is True
    assert backend.presence_checks == [
        ('00:55:da:00:00:01', 'hci2', 'BB', 2.0)
    ]


def test_file_control_acknowledges_recording_changes(tmp_path):
    store = TelemetryStore(
        connection=sqlite3.connect(':memory:'),
        recording_enabled=False,
    )
    control_path = tmp_path / 'control.json'
    control_path.write_text(json.dumps({
        'request_id': 'request-1',
        'recording': True,
    }), encoding='utf-8')
    control = FileRecordingControl(control_path)

    assert control.poll(store) is True
    acknowledgement = json.loads(
        (tmp_path / 'control_ack.json').read_text(encoding='utf-8')
    )
    assert store.recording_enabled is True
    assert acknowledgement['request_id'] == 'request-1'
    assert acknowledgement['success'] is True


def test_bluez_backend_filters_configured_controllers():
    result = SimpleNamespace(
        returncode=0,
        stdout='''
hci1:   Type: Primary  Bus: USB
        BD Address: 00:1A:7D:DA:71:11
hci2:   Type: Primary  Bus: USB
        BD Address: 00:1A:7D:DA:71:22
''',
        stderr='',
    )
    backend = BlueZBackend(('hci2',), runner=lambda *_args, **_kwargs: result)

    assert backend.detect_adapters() == {'hci2': '00:1A:7D:DA:71:22'}
    assert parse_hci_controllers(result.stdout)['hci1'].endswith(':11')


def test_bluez_presence_check_uses_only_current_scan_observations():
    process = Mock()
    process.poll.return_value = 0
    process.communicate.return_value = (
        '[NEW] Device 00:55:DA:00:00:01 Muse\n'
        '[DEL] Device 00:55:DA:00:00:02 Muse\n',
        '',
    )
    backend = BlueZBackend(
        ('hci2',),
        popen_factory=Mock(return_value=process),
        sleep=lambda _seconds: None,
    )

    assert backend.is_visible(
        '00:55:da:00:00:01',
        'hci2',
        '00:1A:7D:DA:71:22',
        duration=1.0,
    ) is True
    assert backend.is_visible(
        '00:55:da:00:00:02',
        'hci2',
        '00:1A:7D:DA:71:22',
        duration=1.0,
    ) is False
    assert process.stdin.write.call_args_list[0].args[0] == (
        'select 00:1A:7D:DA:71:22\nscan on\n'
    )


def test_bluez_warns_once_when_usb_autosuspend_is_enabled(tmp_path):
    usb_device = tmp_path / 'devices' / '1-1'
    interface = usb_device / '1-1:1.0'
    interface.mkdir(parents=True)
    (usb_device / 'idVendor').write_text('2357\n', encoding='utf-8')
    (usb_device / 'power').mkdir()
    (usb_device / 'power' / 'control').write_text(
        'auto\n', encoding='utf-8'
    )
    controller = tmp_path / 'class' / 'bluetooth' / 'hci1'
    controller.mkdir(parents=True)
    (controller / 'device').symlink_to(interface, target_is_directory=True)
    result = SimpleNamespace(
        returncode=0,
        stdout='hci1:\n    BD Address: 00:1A:7D:DA:71:11\n',
        stderr='',
    )
    messages = []
    module_policy = tmp_path / 'enable_autosuspend'
    module_policy.write_text('Y\n', encoding='utf-8')
    backend = BlueZBackend(
        ('hci1',),
        runner=lambda *_args, **_kwargs: result,
        log_callback=lambda level, message: messages.append((level, message)),
        sysfs_root=tmp_path / 'class' / 'bluetooth',
        btusb_autosuspend_path=module_policy,
    )

    backend.detect_adapters()
    backend.detect_adapters()

    assert backend.adapter_power_policy('hci1') == 'auto'
    assert len(messages) == 1
    assert 'autosuspensión USB activa' in messages[0][1]

    module_policy.write_text('N\n', encoding='utf-8')
    backend._power_policy_warnings.clear()
    messages.clear()
    backend.detect_adapters()
    assert messages == []


def test_cli_supports_manual_or_automatic_discovery():
    parser = build_parser()

    automatic = parser.parse_args(['--hci-devices', 'hci1,hci2'])
    manual = parser.parse_args([
        '--device',
        'operador_a,00:55:da:00:00:01,hci1',
    ])

    assert automatic.hci_devices == 'hci1,hci2'
    assert automatic.connection_settle_seconds == 5.0
    assert manual.device[0][0] == 'operador_a'
