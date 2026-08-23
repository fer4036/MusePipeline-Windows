"""Command-line Muse collector that runs without ROS 2."""

import argparse
import json
import signal
import sys
from pathlib import Path
import queue
import threading
import time

from muse_hrc.acquisition import MuseDeviceSession
from muse_hrc.backends import (
    BACKENDS,
    discovery_supervisor,
    resolve_backend,
    session_class,
)
from muse_hrc.ble_identity import parse_manufacturer_ids
from muse_hrc.device_supervisor import retry_delay
from muse_hrc.models import DeviceStatus
from muse_hrc.storage import TelemetryStore


STREAMS = ('eeg', 'imu', 'ppg')
CONNECTION_SETTLE_SECONDS = 5.0
RETRY_PRESENCE_SCAN_SECONDS = 3.0
RETRY_PRESENCE_INTERVAL_SECONDS = 15.0


def parse_device(value):
    """Parse ``OPERATOR,MAC,HCI`` from a repeatable CLI option."""
    parts = [part.strip() for part in value.split(',')]
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            'Use --device OPERADOR,MAC,HCI; ejemplo: '
            '--device operador_a,00:55:da:bb:cc:8e,hci1'
        )
    return tuple(parts)


class StandaloneCollector:
    """Connect configured devices and persist their samples directly."""

    def __init__(
        self,
        devices,
        store,
        status_callback=None,
        discovery_supervisor=None,
        scan_interval=15.0,
        recording_control=None,
        connection_settle_seconds=CONNECTION_SETTLE_SECONDS,
        retry_presence_scan_seconds=RETRY_PRESENCE_SCAN_SECONDS,
        retry_presence_interval=RETRY_PRESENCE_INTERVAL_SECONDS,
        session_factory=MuseDeviceSession,
    ):
        self.devices = list(devices)
        self.store = store
        self.status_callback = status_callback or self._print_status
        self.discovery_supervisor = discovery_supervisor
        self.scan_interval = float(scan_interval)
        self.recording_control = recording_control
        self.connection_settle_seconds = max(
            0.0, float(connection_settle_seconds)
        )
        self.retry_presence_scan_seconds = max(
            0.5, float(retry_presence_scan_seconds)
        )
        self.retry_presence_interval = max(
            1.0, float(retry_presence_interval)
        )
        self.session_factory = session_factory
        self.sessions = {}
        self.retry_attempts = {operator_id: 0 for operator_id, _, _ in devices}
        self.next_retry = {operator_id: 0.0 for operator_id, _, _ in devices}
        self.disconnect_counts = {
            operator_id: 0 for operator_id, _, _ in devices
        }
        self.reconnect_counts = {
            operator_id: 0 for operator_id, _, _ in devices
        }
        self.running = False
        self._last_flush = time.monotonic()
        self._last_metrics = time.monotonic()
        self._last_scan = 0.0
        self._scan_thread = None
        self._connection_quiet_until = 0.0
        self._visible_retries = set()
        self._discovered_devices = queue.Queue()
        self._pending_starts = queue.Queue()

    def run(self):
        self.running = True
        for config in self.devices:
            while self.running and self._connection_is_settling():
                self.pump()
                time.sleep(0.01)
            self._start_one(config)
            self._wait_for_initial_result(config[0])
        while self.running:
            self.pump()
            self._retry_ready_sessions()
            self._accept_discovered_devices()
            self._start_pending_device()
            self._start_hot_scan_if_ready()
            time.sleep(0.01)

    def stop(self):
        self.running = False
        if self.discovery_supervisor is not None:
            self.discovery_supervisor.cancel_scan()
        self.pump()
        for session in self.sessions.values():
            session.stop()
        self.pump()
        self.store.close()

    def pump(self):
        """Transfer queued models to storage and service lifecycle events."""
        for operator_id, session in list(self.sessions.items()):
            for stream in STREAMS:
                for sample in session.drain(stream):
                    self.store.add(sample)
            for status in session.drain_status():
                self._handle_status(operator_id, status)
            if session.poll():
                self.retry_attempts[operator_id] += 1
                self.next_retry[operator_id] = (
                    time.monotonic()
                    + retry_delay(self.retry_attempts[operator_id])
                )
        now = time.monotonic()
        if self.recording_control is not None:
            self.recording_control.poll(self.store)
        if now - self._last_flush >= 0.1:
            self.store.flush()
            self._last_flush = now
        if now - self._last_metrics >= 10.0:
            for session in self.sessions.values():
                session.report_metrics()
            self._last_metrics = now

    def _start_hot_scan_if_ready(self):
        if self.discovery_supervisor is None:
            return
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        if self._connection_is_settling():
            return
        if time.monotonic() - self._last_scan < self.scan_interval:
            return
        if any(
            session.connection_pending for session in self.sessions.values()
        ):
            return
        if not self._pending_starts.empty():
            return
        self._last_scan = time.monotonic()
        self._scan_thread = threading.Thread(
            target=self._run_hot_scan,
            daemon=True,
        )
        self._scan_thread.start()

    def _run_hot_scan(self):
        try:
            for configuration in self.discovery_supervisor.discover():
                self._discovered_devices.put(configuration)
        except Exception as error:
            self._log('warning', f'Scan BLE en caliente falló: {error}')
        finally:
            self._last_scan = time.monotonic()

    def _accept_discovered_devices(self):
        while True:
            try:
                configuration = self._discovered_devices.get_nowait()
            except queue.Empty:
                return
            operator_id = configuration[0]
            self.devices = [
                item for item in self.devices if item[0] != operator_id
            ]
            self.devices.append(configuration)
            self.retry_attempts.setdefault(operator_id, 0)
            self.next_retry.setdefault(operator_id, 0.0)
            self.disconnect_counts.setdefault(operator_id, 0)
            self.reconnect_counts.setdefault(operator_id, 0)
            self._pending_starts.put(configuration)

    def _start_pending_device(self):
        if self._scan_is_active():
            return
        if self._connection_is_settling():
            return
        if any(
            session.connection_pending for session in self.sessions.values()
        ):
            return
        try:
            configuration = self._pending_starts.get_nowait()
        except queue.Empty:
            return
        self._start_one(configuration)

    def _start_one(self, config):
        operator_id, mac_address, hci_device = config
        previous = self.sessions.get(operator_id)
        if previous is not None:
            previous.stop()
        session = self.session_factory(
            operator_id=operator_id,
            mac_address=mac_address,
            hci_device=hci_device,
            log_callback=self._log,
        )
        self.sessions[operator_id] = session
        session.start()

    def _wait_for_initial_result(self, operator_id, timeout=40.0):
        deadline = time.monotonic() + timeout
        session = self.sessions[operator_id]
        while self.running and time.monotonic() < deadline:
            self.pump()
            if session.streaming or session.lifecycle_finished:
                return
            time.sleep(0.05)

    def _retry_ready_sessions(self):
        now = time.monotonic()
        if self._scan_is_active():
            return
        if self._connection_is_settling(now):
            return
        if any(
            session.connection_pending for session in self.sessions.values()
        ):
            return
        by_operator = {config[0]: config for config in self.devices}
        for operator_id, due_at in self.next_retry.items():
            if due_at and now >= due_at:
                configuration = by_operator[operator_id]
                if (
                    self.discovery_supervisor is not None and
                    operator_id not in self._visible_retries
                ):
                    self._probe_retry_presence(configuration, now)
                    return
                self._visible_retries.discard(operator_id)
                self.next_retry[operator_id] = 0.0
                self._start_one(configuration)
                return

    def _probe_retry_presence(self, configuration, now):
        operator_id, mac_address, hci_device = configuration
        self._log(
            'info',
            f'[{operator_id}] Verificando que {mac_address} vuelva a '
            'anunciarse antes de reconectar',
        )
        try:
            visible = self.discovery_supervisor.is_visible(
                mac_address,
                hci_device,
                duration=self.retry_presence_scan_seconds,
            )
        except Exception as error:
            visible = False
            self._log(
                'warning',
                f'[{operator_id}] No se pudo verificar presencia BLE: {error}',
            )
        self._connection_quiet_until = max(
            self._connection_quiet_until,
            time.monotonic() + self.connection_settle_seconds,
        )
        if visible:
            self._visible_retries.add(operator_id)
            self.next_retry[operator_id] = self._connection_quiet_until
            self._log(
                'info',
                f'[{operator_id}] MAC visible; conexión programada después '
                'de estabilizar el radio',
            )
            return
        self.next_retry[operator_id] = now + self.retry_presence_interval
        self._handle_status(
            operator_id,
            DeviceStatus(
                state='waiting_for_device',
                timestamp=time.time(),
                operator_id=operator_id,
                mac_address=mac_address,
                adapter=hci_device,
                details={
                    'next_presence_check_seconds': self.retry_presence_interval,
                },
            ),
        )
        self._log(
            'info',
            f'[{operator_id}] Diadema apagada o fuera de alcance; no se abre '
            'un intento GATT que pueda afectar otros enlaces',
        )

    def _scan_is_active(self):
        return self._scan_thread is not None and self._scan_thread.is_alive()

    def _connection_is_settling(self, now=None):
        current = time.monotonic() if now is None else now
        return current < self._connection_quiet_until

    def _handle_status(self, operator_id, status):
        payload = status.as_dict()
        state = payload.get('state')
        if state in {'disconnected', 'data_timeout'}:
            self.disconnect_counts[operator_id] = (
                self.disconnect_counts.get(operator_id, 0) + 1
            )
        elif state == 'streaming':
            previous_attempts = self.retry_attempts.get(operator_id, 0)
            if previous_attempts:
                self.reconnect_counts[operator_id] = (
                    self.reconnect_counts.get(operator_id, 0) + 1
                )
            # A recovered stream starts a fresh failure streak. Without this,
            # intermittent links wait 5, 10, 20, 40... seconds even after each
            # successful recovery.
            self.retry_attempts[operator_id] = 0
            self.next_retry[operator_id] = 0.0
            self._visible_retries.discard(operator_id)
            self._connection_quiet_until = max(
                self._connection_quiet_until,
                time.monotonic() + self.connection_settle_seconds,
            )
        payload['disconnect_count'] = self.disconnect_counts.get(
            operator_id, 0
        )
        payload['reconnect_count'] = self.reconnect_counts.get(
            operator_id, 0
        )
        payload['retry_attempt'] = self.retry_attempts.get(operator_id, 0)
        self.status_callback(payload)

    @staticmethod
    def _print_status(payload):
        compatible = dict(payload)
        compatible['operator'] = compatible.get('operator_id')
        print(
            'MUSE_STATUS_JSON='
            + json.dumps(compatible, ensure_ascii=False),
            flush=True,
        )

    @staticmethod
    def _log(level, message):
        print(f'[{level.upper()}] {message}', file=sys.stderr, flush=True)


class FileRecordingControl:
    """Apply GUI recording commands through private atomic JSON files."""

    def __init__(self, control_path):
        self.control_path = Path(control_path)
        self.ack_path = self.control_path.with_name('control_ack.json')
        self._last_request_id = None

    def poll(self, store):
        try:
            payload = json.loads(
                self.control_path.read_text(encoding='utf-8')
            )
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return False
        request_id = payload.get('request_id')
        if (
            not isinstance(request_id, str)
            or request_id == self._last_request_id
        ):
            return False
        enabled = payload.get('recording')
        if not isinstance(enabled, bool):
            self._write_ack(request_id, False, 'recording debe ser booleano')
            self._last_request_id = request_id
            return False
        try:
            store.set_recording(enabled)
            store.flush()
            self._write_ack(request_id, True, '')
        except Exception as error:
            self._write_ack(request_id, False, str(error))
            self._last_request_id = request_id
            return False
        self._last_request_id = request_id
        return True

    def _write_ack(self, request_id, success, message):
        payload = {
            'request_id': request_id,
            'success': bool(success),
            'message': message,
            'timestamp': time.time(),
        }
        temporary = self.ack_path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding='utf-8',
        )
        temporary.chmod(0o600)
        temporary.replace(self.ack_path)


def build_parser():
    parser = argparse.ArgumentParser(
        description='Recopila Muse S Athena directamente, sin ROS 2.',
    )
    devices = parser.add_mutually_exclusive_group(required=True)
    devices.add_argument(
        '--device',
        action='append',
        type=parse_device,
        metavar='OPERADOR,MAC,HCI',
        help='Diadema a conectar; se puede repetir para varios operadores.',
    )
    devices.add_argument(
        '--hci-devices',
        metavar='HCI1,HCI2,...',
        help=(
            'Descubrir Muse automáticamente y asignar estos adaptadores.'
        ),
    )
    parser.add_argument(
        '--acquisition-backend',
        choices=BACKENDS,
        default='auto',
        help='Backend BLE; auto usa BrainFlow en Windows y Athena en Linux.',
    )
    parser.add_argument(
        '--max-devices',
        type=int,
        default=4,
        help='Máximo de diademas en descubrimiento Windows.',
    )
    parser.add_argument(
        '--manufacturer-ids',
        default='',
        help='IDs BlueZ adicionales separados por coma, por ejemplo 0x1234.',
    )
    parser.add_argument(
        '--scan-seconds',
        type=float,
        default=12.0,
        help='Duración del scan automático inicial.',
    )
    parser.add_argument(
        '--scan-interval',
        type=float,
        default=15.0,
        help='Segundos entre scans cuando queda un adaptador libre.',
    )
    parser.add_argument(
        '--connection-settle-seconds',
        type=float,
        default=CONNECTION_SETTLE_SECONDS,
        help=(
            'Pausa después de alcanzar streaming antes de otro scan o enlace.'
        ),
    )
    parser.add_argument(
        '--db',
        default='~/muse_telemetry.db',
        help='Ruta de la base SQLite local.',
    )
    parser.add_argument(
        '--paused',
        action='store_true',
        help=(
            'Conectar sin guardar; la API puede activar el almacén después.'
        ),
    )
    parser.add_argument(
        '--control-file',
        help='Archivo JSON privado usado por la GUI para controlar grabación.',
    )
    parser.add_argument('--cloud-url', help='Gateway ws:// o wss:// opcional.')
    parser.add_argument('--agent-id', default='', help='Identidad de este agente.')
    parser.add_argument('--agent-token', default='', help='Token del agente cloud.')
    parser.add_argument('--session-id', default='', help='Sesión enviada al cloud.')
    return parser


def main(argv=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)
    devices = arguments.device or []
    supervisor = None
    selected_backend = resolve_backend(arguments.acquisition_backend)
    if arguments.hci_devices:
        hci_devices = tuple(
            item.strip()
            for item in arguments.hci_devices.split(',')
            if item.strip()
        )
        if not hci_devices:
            parser.error('--hci-devices no contiene adaptadores válidos')
        supervisor = discovery_supervisor(
            selected_backend,
            hci_devices=hci_devices,
            max_devices=min(arguments.max_devices, len(hci_devices)),
            manufacturer_ids=parse_manufacturer_ids(
                arguments.manufacturer_ids
            ),
            scan_seconds=arguments.scan_seconds,
            log_callback=StandaloneCollector._log,
        )
    store = TelemetryStore(
        db_path=arguments.db,
        recording_enabled=not arguments.paused,
    )
    recording_control = (
        FileRecordingControl(arguments.control_file)
        if arguments.control_file else None
    )
    cloud_transport = None

    def handle_cloud_command(payload):
        action = payload.get('action')
        if action == 'start_recording':
            store.set_recording(True)
        elif action == 'stop_recording':
            store.set_recording(False)
            store.flush()
        else:
            raise ValueError(f'Comando cloud no permitido: {action}')
        return {'recording': store.recording_enabled}

    status_callback = None
    if arguments.cloud_url:
        if not arguments.agent_id or not arguments.agent_token:
            parser.error('--cloud-url requiere --agent-id y --agent-token')
        from muse_hrc.cloud_transport import AgentWebSocketTransport
        cloud_transport = AgentWebSocketTransport(
            arguments.cloud_url,
            arguments.agent_id,
            arguments.agent_token,
            session_id=arguments.session_id,
            command_handler=handle_cloud_command,
            log_callback=StandaloneCollector._log,
        )

        def status_callback(payload):
            StandaloneCollector._print_status(payload)
            cloud_transport.publish('status', payload)

    collector = StandaloneCollector(
        devices,
        store,
        discovery_supervisor=supervisor,
        scan_interval=arguments.scan_interval,
        recording_control=recording_control,
        connection_settle_seconds=arguments.connection_settle_seconds,
        session_factory=session_class(selected_backend),
        status_callback=status_callback,
    )

    def request_stop(_signum, _frame):
        collector.running = False

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, request_stop)
    try:
        if cloud_transport is not None:
            cloud_transport.start()
        collector.run()
    finally:
        collector.stop()
        if cloud_transport is not None:
            cloud_transport.stop()


if __name__ == '__main__':
    main()
