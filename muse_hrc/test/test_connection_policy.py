"""Regression tests for coordinated Muse BLE retries."""

from collections import deque
import threading
from unittest.mock import Mock, patch

from muse_hrc.discovery_node import (
    BLUEZ_RELEASE_TIMEOUT,
    DiscoveryNode,
    DiademaEntry,
    LOST,
    STREAMING,
    parse_hci_controllers,
    retry_delay,
)
from muse_hrc.device_supervisor import RETRY_MAX
from muse_hrc.acquisition import MuseDeviceSession


def test_retry_delay_is_exponential_and_bounded():
    assert [retry_delay(attempt) for attempt in range(1, 7)] == [
        5.0, 10.0, 20.0, 40.0, 60.0, 60.0,
    ]
    assert retry_delay(99) == RETRY_MAX


def test_parses_hci_names_and_controller_addresses():
    output = '''
hci1:   Type: Primary  Bus: USB
        BD Address: 00:1A:7D:DA:71:11  ACL MTU: 310:10
hci2:   Type: Primary  Bus: USB
        BD Address: 00:1A:7D:DA:71:22  ACL MTU: 310:10
'''

    assert parse_hci_controllers(output) == {
        'hci1': '00:1A:7D:DA:71:11',
        'hci2': '00:1A:7D:DA:71:22',
    }


def test_bluetoothctl_command_selects_requested_controller():
    completed = Mock(returncode=0, stdout='Connection successful\n', stderr='')

    with patch(
        'muse_hrc.discovery_node.subprocess.run', return_value=completed
    ) as run:
        result = DiscoveryNode._controller_command(
            '00:1A:7D:DA:71:22', 'connect 00:55:DA:00:00:01', 10
        )

    assert result is completed
    assert run.call_args.kwargs['input'] == (
        'select 00:1A:7D:DA:71:22\nconnect 00:55:DA:00:00:01\n'
    )


def test_bluez_release_waits_until_disconnected():
    connected = Mock(stdout='Connected: yes\n', stderr='')
    disconnected = Mock(stdout='Connected: no\n', stderr='')

    with patch(
        'muse_hrc.discovery_node.subprocess.run',
        side_effect=[connected, disconnected],
    ) as run, patch('muse_hrc.discovery_node.time.sleep'):
        released = DiscoveryNode._wait_for_bluez_release(
            '00:55:DA:00:00:01', timeout=BLUEZ_RELEASE_TIMEOUT
        )

    assert released is True
    assert run.call_count == 2


def test_bluez_release_times_out_if_connection_stays_active():
    connected = Mock(stdout='Connected: yes\n', stderr='')

    with patch(
        'muse_hrc.discovery_node.subprocess.run', return_value=connected
    ), patch('muse_hrc.discovery_node.time.sleep'), patch(
        'muse_hrc.discovery_node.time.monotonic',
        side_effect=[0.0, 0.1, 1.1],
    ):
        released = DiscoveryNode._wait_for_bluez_release(
            '00:55:DA:00:00:01', timeout=1.0
        )

    assert released is False


def test_known_lost_device_retries_without_fresh_advertisement():
    discovery = object.__new__(DiscoveryNode)
    entry = DiademaEntry('00:55:DA:00:00:01', 'operador_a')
    entry.estado = LOST
    entry.next_retry = 10.0
    entry.last_seen = 1.0
    discovery._diademas = {entry.mac: entry}
    discovery._conectando_ahora = None
    discovery._scanning = False
    discovery._cola_conexion = deque()
    discovery._adaptadores_libres = deque(['hci1'])
    discovery._lanzar_muse_node = Mock()
    discovery.get_logger = Mock()

    discovery._avanzar_cola(100.0)
    discovery._lanzar_muse_node.assert_called_once_with(entry, 100.0)


def test_scan_yields_to_ready_reconnection():
    discovery = object.__new__(DiscoveryNode)
    entry = DiademaEntry('00:55:DA:00:00:01', 'operador_a')
    entry.estado = LOST
    entry.next_retry = 10.0
    discovery._lock = threading.Lock()
    discovery._diademas = {entry.mac: entry}
    discovery._scanning = False
    discovery._conectando_ahora = None
    discovery._cola_conexion = deque()
    discovery._adaptadores_libres = deque(['hci1'])
    discovery._iniciar_scan_bloqueado = Mock()
    discovery.get_logger = Mock(return_value=Mock())

    with patch('muse_hrc.discovery_node.time.time', return_value=100.0):
        discovery._scan_callback()

    discovery._iniciar_scan_bloqueado.assert_not_called()


def test_removed_adapter_stops_child_and_is_not_reused_until_reenumerated():
    discovery = object.__new__(DiscoveryNode)
    entry = DiademaEntry('00:55:DA:00:00:01', 'operador_a')
    entry.estado = STREAMING
    entry.hci_device = 'hci1'
    entry.proceso = Mock()
    discovery._diademas = {entry.mac: entry}
    discovery._adaptadores_configurados = ('hci1', 'hci2')
    discovery._adaptadores_detectados = {
        'hci1': '00:1A:7D:DA:71:11',
        'hci2': '00:1A:7D:DA:71:22',
    }
    discovery._adaptadores_libres = deque(['hci2'])
    discovery._scan_adapter = None
    discovery._conectando_ahora = None
    discovery._terminar_proceso = Mock()
    discovery.get_logger = Mock(return_value=Mock())

    discovery._reconciliar_adaptadores(
        {'hci2': '00:1A:7D:DA:71:22'}, now=100.0
    )

    discovery._terminar_proceso.assert_called_once_with(entry)
    assert entry.estado == LOST
    assert entry.hci_device is None
    assert list(discovery._adaptadores_libres) == ['hci2']

    discovery._reconciliar_adaptadores(
        {
            'hci1': '00:1A:7D:DA:71:11',
            'hci2': '00:1A:7D:DA:71:22',
        },
        now=103.0,
    )
    assert list(discovery._adaptadores_libres) == ['hci2', 'hci1']


def test_scan_reserves_only_the_selected_free_adapter():
    discovery = object.__new__(DiscoveryNode)
    discovery._scanning = False
    discovery._scan_adapter = None
    discovery._adaptadores_libres = deque(['hci2', 'hci3'])
    discovery._adaptadores_detectados = {
        'hci2': '00:1A:7D:DA:71:22',
        'hci3': '00:1A:7D:DA:71:33',
    }

    with patch('muse_hrc.discovery_node.threading.Thread') as thread:
        started = discovery._iniciar_scan_bloqueado()

    assert started is True
    assert discovery._scan_adapter == 'hci2'
    assert list(discovery._adaptadores_libres) == ['hci3']
    assert thread.call_args.kwargs['args'] == (
        'hci2', '00:1A:7D:DA:71:22'
    )
    thread.return_value.start.assert_called_once_with()


def test_scan_selects_reserved_controller_before_enabling_discovery():
    discovery = object.__new__(DiscoveryNode)
    discovery._lock = threading.Lock()
    discovery._scanning = True
    discovery._scan_adapter = 'hci2'
    discovery._adaptadores_detectados = {
        'hci2': '00:1A:7D:DA:71:22',
    }
    discovery._adaptadores_libres = deque()
    discovery._diademas = {}
    discovery._identificar_diademas = Mock(return_value=set())
    discovery._registrar_nuevas = Mock()
    discovery.get_logger = Mock(return_value=Mock())
    process = Mock()
    process.communicate.return_value = ('', '')
    process.poll.return_value = 0

    with patch(
        'muse_hrc.discovery_node.subprocess.Popen', return_value=process
    ), patch('muse_hrc.discovery_node.time.sleep'):
        discovery._run_ble_scan('hci2', '00:1A:7D:DA:71:22')

    assert process.stdin.write.call_args_list[0].args[0] == (
        'select 00:1A:7D:DA:71:22\nscan on\n'
    )
    assert process.stdin.write.call_args_list[1].args[0] == 'scan off\nquit\n'
    assert list(discovery._adaptadores_libres) == ['hci2']


def test_finished_session_returns_retry_without_inner_loop():
    session = MuseDeviceSession(
        'operador_a',
        '00:55:DA:00:00:01',
        'hci1',
    )
    with session._lock:
        session._lifecycle_finished = True

    assert session.poll() is True
    assert session.poll() is False
    statuses = session.drain_status()
    assert len(statuses) == 1
    assert statuses[0].state == 'retry_required'
