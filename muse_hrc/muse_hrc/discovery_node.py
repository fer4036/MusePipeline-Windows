import rclpy
from rclpy.node import Node
import subprocess
import threading
import time
import re
import json
from collections import deque
from pathlib import Path

from std_msgs.msg import String

from muse_hrc.ble_identity import (
    extract_current_scan_addresses,
    identify_muse,
    parse_manufacturer_ids,
    summarize_properties,
)
from muse_hrc.python_runtime import select_muse_python

SCANNING = 'scanning'
CONNECTING = 'connecting'
STREAMING = 'streaming'
LOST = 'lost'

RETRY_BASE = 5.0
RETRY_MAX = 60.0
# Bleak uses up to 30 seconds for one GATT connection. Give the child enough
# time to report and clean up before the parent enforces its outer timeout.
CONNECT_TIMEOUT = 40.0
BLUEZ_RELEASE_TIMEOUT = 3.0
BLUEZ_RELEASE_SETTLE = 0.75
SCAN_SECONDS = 12.0


def parse_hci_controllers(output):
    """Return ``{hci_name: controller_address}`` from ``hciconfig -a``."""
    controllers = {}
    current = None
    for line in (output or '').splitlines():
        header = re.match(r'^\s*(hci\d+):', line)
        if header:
            current = header.group(1)
            controllers.setdefault(current, None)
            continue
        address = re.search(
            r'\bBD Address:\s*((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})',
            line,
        )
        if current and address:
            controllers[current] = address.group(1).upper()
    return controllers


def retry_delay(retry_count):
    """Return a bounded exponential delay for a failed BLE attempt."""
    exponent = min(max(int(retry_count) - 1, 0), 4)
    return min(RETRY_BASE * (2 ** exponent), RETRY_MAX)


class DiademaEntry:
    def __init__(self, mac: str, operador_id: str):
        self.mac = mac
        self.operador_id = operador_id
        self.estado = SCANNING
        self.proceso = None
        self.retry_count = 0
        self.next_retry = 0.0
        self.connect_start = 0.0  # epoch cuando se lanzó el muse_node
        self.last_seen = time.time()
        self.hci_device = None
        self.status_subscription = None


class DiscoveryNode(Node):
    def __init__(self):
        super().__init__('discovery_node')

        self._diademas: dict[str, DiademaEntry] = {}
        self._lock = threading.Lock()
        self._operador_counter = 0
        self._scanning = False
        self._scan_adapter = None

        self.declare_parameter('hci_devices', 'hci0,hci1,hci2,hci3')
        configured_hci = self.get_parameter(
            'hci_devices'
        ).get_parameter_value().string_value
        self._adaptadores_configurados = tuple(
            item.strip() for item in configured_hci.split(',') if item.strip()
        )
        self._adaptadores_detectados = self._detectar_adaptadores() or {}
        self._adaptadores_libres = deque(self._adaptadores_detectados)
        missing = set(self._adaptadores_configurados) - set(
            self._adaptadores_detectados
        )
        if missing:
            self.get_logger().warn(
                f"Adaptadores configurados no disponibles: {sorted(missing)}"
            )

        self.declare_parameter('muse_python', 'auto')
        configured_python = self.get_parameter(
            'muse_python'
        ).get_parameter_value().string_value
        module_path = Path(__file__).resolve()
        workspace_root = next(
            (
                parent for parent in module_path.parents
                if (parent / 'muse_env' / 'bin' / 'python').is_file()
            ),
            Path.cwd(),
        )
        self._muse_python = select_muse_python(
            configured_python,
            workspace_root,
        )

        self.declare_parameter('muse_manufacturer_ids', '')
        manufacturer_ids = self.get_parameter(
            'muse_manufacturer_ids'
        ).get_parameter_value().string_value
        self._muse_manufacturer_ids = parse_manufacturer_ids(manufacturer_ids)

        # Cola FIFO: MACs esperando su turno para conectar
        self._cola_conexion: deque[str] = deque()

        # Solo una diadema conecta a la vez
        self._conectando_ahora: str | None = None

        # Scan BLE cada 15 s
        self.create_timer(15.0, self._scan_callback)

        # Watchdog cada 3 s: avanza la cola y detecta caídas
        self.create_timer(3.0, self._watchdog_callback)

        self.get_logger().info(
            "DiscoveryNode iniciado — adaptadores disponibles: "
            f"{list(self._adaptadores_libres)}"
        )
        self.get_logger().info(
            f"Runtime de Muse Athena: {self._muse_python}"
        )

        # Primer scan inmediato, reservado sobre un controlador libre concreto.
        with self._lock:
            self._iniciar_scan_bloqueado()

    def _detectar_adaptadores(self, warn=True):
        """Return configured adapters currently exposed, including addresses."""
        try:
            result = subprocess.run(
                ['hciconfig', '-a'],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            if warn:
                self.get_logger().warn(
                    f"No se pudo ejecutar hciconfig ({error}); "
                    "se conservará el último inventario válido."
                )
            return None

        if result.returncode != 0:
            if warn:
                detail = (result.stderr or result.stdout).strip()
                self.get_logger().warn(
                    f"hciconfig no pudo consultar adaptadores: {detail}"
                )
            return None

        detected = parse_hci_controllers(result.stdout)
        return {
            adapter: detected[adapter]
            for adapter in self._adaptadores_configurados
            if adapter in detected and detected[adapter]
        }

    def _adaptadores_en_uso(self):
        """Return HCI names currently assigned to child processes."""
        return {
            entry.hci_device
            for entry in self._diademas.values()
            if entry.hci_device is not None
        }

    def _reconciliar_adaptadores(self, detected, now):
        """Apply one HCI hotplug snapshot while preserving valid assignments."""
        previous = self._adaptadores_detectados
        removed = {
            adapter for adapter, address in previous.items()
            if detected.get(adapter) != address
        }
        added = {
            adapter for adapter, address in detected.items()
            if previous.get(adapter) != address
        }

        if removed:
            self._adaptadores_libres = deque(
                adapter for adapter in self._adaptadores_libres
                if adapter not in removed
            )
            for entry in self._diademas.values():
                if entry.hci_device not in removed:
                    continue
                missing_adapter = entry.hci_device
                self._terminar_proceso(entry)
                entry.hci_device = None
                entry.estado = LOST
                entry.retry_count += 1
                entry.next_retry = now + retry_delay(entry.retry_count)
                if self._conectando_ahora == entry.mac:
                    self._conectando_ahora = None
                status_payload = {
                    'state': 'error',
                    'timestamp': now,
                    'message': (
                        f'Adaptador {missing_adapter} desconectado del bus USB'
                    ),
                    'operator': entry.operador_id,
                    'mac': entry.mac,
                    'adapter': missing_adapter,
                }
                self.get_logger().info(
                    'MUSE_STATUS_JSON=' + json.dumps(
                        status_payload,
                        ensure_ascii=True,
                        separators=(',', ':'),
                    )
                )
                self.get_logger().error(
                    f"[{entry.operador_id}] Adaptador {missing_adapter} "
                    "desapareció del sistema; esperando reenumeración USB."
                )

        self._adaptadores_detectados = dict(detected)
        used = self._adaptadores_en_uso()
        for adapter in self._adaptadores_configurados:
            if (
                adapter in added and
                adapter not in used and
                adapter != self._scan_adapter and
                adapter not in self._adaptadores_libres
            ):
                self._adaptadores_libres.append(adapter)

        if removed:
            self.get_logger().warn(
                f"Adaptadores BLE removidos: {sorted(removed)}"
            )
        if added:
            self.get_logger().info(
                f"Adaptadores BLE disponibles nuevamente: {sorted(added)}"
            )

    # ──────────────────────────────────────────────────────────────
    #  SCAN BLE
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _controller_command(controller_address, command, timeout):
        """Run one bluetoothctl command after selecting a concrete controller."""
        return subprocess.run(
            ['bluetoothctl', '--timeout', str(int(timeout))],
            input=f'select {controller_address}\n{command}\n',
            capture_output=True,
            text=True,
            timeout=timeout + 3,
        )

    def _emparejar_diadema(
        self, mac: str, adapter: str, controller_address: str
    ) -> bool:
        """
        Initialize the Athena BLE link.

        Muse S Athena only needs connect and disconnect through bluetoothctl.
        A successful pairing is not required.
        """
        self.get_logger().info(
            f"[{mac}] Inicializando enlace BLE mediante {adapter}..."
        )
        try:
            result = self._controller_command(
                controller_address, f'connect {mac}', 10
            )
            output = (result.stdout + result.stderr).lower()
            initialized = result.returncode == 0 and 'successful' in output
        except (OSError, subprocess.SubprocessError) as error:
            self.get_logger().warn(f"[{mac}] Preparación BLE incompleta: {error}")
            initialized = False
        finally:
            # Pairing is intentionally omitted: Athena does not require a bond.
            # Always release the temporary bluetoothctl connection before Bleak.
            try:
                self._controller_command(
                    controller_address, f'disconnect {mac}', 5
                )
            except (OSError, subprocess.SubprocessError):
                pass

        released = self._wait_for_bluez_release(mac)
        if released:
            # BlueZ may report Connected=no slightly before it has released
            # the GATT transport. Avoid racing the following Bleak client.
            time.sleep(BLUEZ_RELEASE_SETTLE)
        else:
            self.get_logger().warn(
                f"[{mac}] BlueZ no confirmó la liberación del enlace temporal."
            )

        if initialized:
            self.get_logger().info(f"[{mac}] ✅ Enlace BLE inicializado.")
        else:
            self.get_logger().warn(
                f"[{mac}] bluetoothctl no confirmó la conexión temporal."
            )
        return initialized

    @staticmethod
    def _wait_for_bluez_release(mac, timeout=BLUEZ_RELEASE_TIMEOUT):
        """Wait until BlueZ reports that the temporary link is disconnected."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'info', mac],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            properties = (result.stdout + result.stderr).lower()
            if 'connected: no' in properties or 'device not available' in properties:
                return True
            time.sleep(0.2)
        return False

    def _scan_callback(self):
        with self._lock:
            if self._scanning:
                self.get_logger().debug("Scan BLE anterior todavía en curso")
                return
            if self._conectando_ahora is not None or self._cola_conexion:
                self.get_logger().debug(
                    "Scan aplazado — hay una conexión BLE en curso"
                )
                return
            if self._perdidas_para_reintento(time.time()):
                self.get_logger().debug(
                    "Scan aplazado — una diadema conocida requiere reconexión"
                )
                return
            if not self._adaptadores_libres:
                self.get_logger().debug(
                    "Scan aplazado — no hay adaptadores libres"
                )
                return
            self._iniciar_scan_bloqueado()

    def _iniciar_scan_bloqueado(self):
        """Reserve one free HCI and start a scan; caller must hold the lock."""
        if self._scanning or not self._adaptadores_libres:
            return False
        adapter = self._adaptadores_libres.popleft()
        controller_address = self._adaptadores_detectados.get(adapter)
        if not controller_address:
            return False
        self._scanning = True
        self._scan_adapter = adapter
        threading.Thread(
            target=self._run_ble_scan,
            args=(adapter, controller_address),
            daemon=True,
        ).start()
        return True

    def _run_ble_scan(self, adapter, controller_address):
        self.get_logger().info(f"Iniciando scan BLE en {adapter}...")

        proc = None
        try:
            proc = subprocess.Popen(
                ['bluetoothctl'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            proc.stdin.write(
                f'select {controller_address}\nscan on\n'
            )
            proc.stdin.flush()
            time.sleep(SCAN_SECONDS)
            proc.stdin.write('scan off\nquit\n')
            proc.stdin.flush()
            stdout, stderr = proc.communicate(timeout=6)
            output = stdout + stderr

            # Only classify advertisements observed during this scan. BlueZ's
            # persistent device cache can contain powered-off Muse headbands.
            candidates = extract_current_scan_addresses(output)
            self.get_logger().info(
                f"Clasificando {len(candidates)} dispositivos BLE por propiedades..."
            )

            identified = self._identificar_diademas(candidates)
            self.get_logger().info(f"Diademas Muse detectadas: {sorted(identified)}")
            self._registrar_nuevas(
                identified, adapter, controller_address
            )

        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
                proc.communicate()
            self.get_logger().warn(f"Scan BLE en {adapter} expiró.")
        except (BrokenPipeError, OSError, subprocess.SubprocessError) as error:
            self.get_logger().warn(f"Scan BLE en {adapter} falló: {error}")
        except Exception as error:
            self.get_logger().error(f"Error en scan BLE: {error}")
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
            with self._lock:
                self._scanning = False
                self._scan_adapter = None
                if (
                    adapter in self._adaptadores_detectados and
                    adapter not in self._adaptadores_en_uso() and
                    adapter not in self._adaptadores_libres
                ):
                    self._adaptadores_libres.append(adapter)

    def _identificar_diademas(self, candidates):
        """Query BlueZ properties and return only positively identified Muses."""
        identified = set()
        for mac in sorted(candidates):
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'info', mac],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.SubprocessError) as error:
                self.get_logger().debug(f"[{mac}] No se pudo consultar: {error}")
                continue

            properties = result.stdout + result.stderr
            reason = identify_muse(
                mac,
                properties,
                self._muse_manufacturer_ids,
            )
            if reason:
                identified.add(mac)
                self.get_logger().info(f"[{mac}] Muse identificada por {reason}")
            else:
                self.get_logger().debug(
                    f"[{mac}] Dispositivo descartado: "
                    f"{summarize_properties(properties)}"
                )
        return identified

    def _registrar_nuevas(
        self, macs: set, scan_adapter: str, controller_address: str
    ):
        nuevas = []
        now = time.time()
        with self._lock:
            for mac in sorted(macs):
                if mac in self._diademas:
                    self._diademas[mac].last_seen = now
                else:
                    operador_id = f"operador_{chr(97 + self._operador_counter)}"
                    self._operador_counter += 1
                    entry = DiademaEntry(mac, operador_id)
                    entry.last_seen = now
                    self._diademas[mac] = entry
                    self.get_logger().info(
                        f"Nueva diadema detectada: {mac} → {operador_id}"
                    )
                    nuevas.append(mac)

        # BlueZ serializes several controller operations internally. Prepare
        # one headband at a time while the scan remains marked as active.
        preparadas = [
            (
                mac,
                self._emparejar_diadema(
                    mac, scan_adapter, controller_address
                ),
            )
            for mac in nuevas
        ]

        with self._lock:
            for mac, enlace_inicializado in preparadas:
                entry = self._diademas.get(mac)
                if entry is None or mac in self._cola_conexion:
                    continue
                self._cola_conexion.append(mac)
                if not enlace_inicializado:
                    self.get_logger().warn(
                        f"[{entry.operador_id}] La preparación bluetoothctl falló; "
                        "Athena intentará la conexión directa sin cambiar su ID."
                    )
                self.get_logger().info(
                    f"[{entry.operador_id}] En cola "
                    f"(posición {len(self._cola_conexion)})"
                )

    # ──────────────────────────────────────────────────────────────
    #  WATCHDOG + COLA SECUENCIAL
    # ──────────────────────────────────────────────────────────────
    def _watchdog_callback(self):
        now = time.time()
        detected = self._detectar_adaptadores(warn=False)
        with self._lock:
            if detected is not None:
                self._reconciliar_adaptadores(detected, now)
            self._revisar_streaming(now)
            self._revisar_timeout_conexion(now)
            self._revisar_caidas(now)
            self._avanzar_cola(now)

    def _revisar_streaming(self, now):
        """
        Wait for the child's first-sample status event.

        Topic existence and old log text cannot prove that samples are still
        flowing because ROS publishers remain present during a BLE outage.
        The status callback changes the entry to STREAMING only after the
        child receives a new EEG sample.
        """
        if self._conectando_ahora is None:
            return

        mac = self._conectando_ahora
        entry = self._diademas.get(mac)
        if entry is None or entry.estado != CONNECTING:
            return

    def _revisar_timeout_conexion(self, now):
        """Si una diadema lleva demasiado en CONNECTING, la manda al final de la cola."""
        if self._conectando_ahora is None:
            return

        mac = self._conectando_ahora
        entry = self._diademas.get(mac)
        if entry is None:
            return

        if entry.proceso is None or entry.proceso.poll() is not None:
            self.get_logger().warn(
                f"[{entry.operador_id}] muse_node terminó durante la conexión."
            )
            self._liberar_adaptador(entry)
            entry.estado = LOST
            entry.retry_count += 1
            entry.next_retry = now + retry_delay(entry.retry_count)
            self._conectando_ahora = None
            return

        elapsed = now - entry.connect_start
        if elapsed > CONNECT_TIMEOUT:
            self.get_logger().warn(
                f"[{entry.operador_id}] Timeout de conexión ({CONNECT_TIMEOUT}s). "
                f"Reencolar al final."
            )
            # Terminar proceso actual
            self._terminar_proceso(entry)
            self._liberar_adaptador(entry)
            entry.estado = LOST
            entry.retry_count += 1
            delay = retry_delay(entry.retry_count)
            entry.next_retry = now + delay
            self._conectando_ahora = None  # liberar turno para la siguiente

    def _revisar_caidas(self, now):
        """Detecta procesos que murieron estando en STREAMING."""
        for mac, entry in self._diademas.items():
            if entry.estado != STREAMING:
                continue

            proceso_vivo = (
                entry.proceso is not None and
                entry.proceso.poll() is None
            )
            if not proceso_vivo:
                self.get_logger().warn(
                    f"[{entry.operador_id}] Stream caído. Reencolar."
                )
                entry.estado = LOST
                self._liberar_adaptador(entry)
                entry.retry_count += 1
                delay = retry_delay(entry.retry_count)
                entry.next_retry = now + delay

    def _avanzar_cola(self, now):
        """
        Advance the connection queue when no device is connecting.

        Ready LOST devices take priority; otherwise the next new device is
        selected from the FIFO queue.
        """
        if self._scanning or self._conectando_ahora is not None:
            return  # el controlador está escaneando o ya hay una conexión

        # Prioridad: diademas LOST con reintento listo
        # (ordenadas por next_retry para atender primero la más antigua)
        perdidas_listas = self._perdidas_para_reintento(now)
        if perdidas_listas:
            entry = perdidas_listas[0]
            self.get_logger().info(
                f"[{entry.operador_id}] Reintentando (intento #{entry.retry_count})..."
            )
            self._lanzar_muse_node(entry, now)
            return

        # Cola normal de nuevas diademas
        if self._cola_conexion:
            mac = self._cola_conexion.popleft()
            entry = self._diademas.get(mac)
            if entry and self._adaptadores_libres:
                self.get_logger().info(
                    f"[{entry.operador_id}] Turno de conectar. "
                    f"Quedan {len(self._cola_conexion)} en cola."
                )
                self._lanzar_muse_node(entry, now)
            elif entry:
                self._cola_conexion.appendleft(mac)
                self.get_logger().debug("Todas las interfaces hci están ocupadas")

    def _perdidas_para_reintento(self, now):
        """Return known lost devices whose coordinated backoff has elapsed.

        A connected Athena normally stops advertising and may not resume
        advertisements immediately after an unexpected GATT loss. Its known
        address is therefore sufficient for a direct Bleak retry; requiring a
        fresh scan here made recovery impossible after longer streams.
        """
        return sorted(
            (
                entry for entry in self._diademas.values()
                if entry.estado == LOST and now >= entry.next_retry
            ),
            key=lambda entry: entry.next_retry,
        )

    # ──────────────────────────────────────────────────────────────
    #  LANZAR muse_node
    # ──────────────────────────────────────────────────────────────
    def _lanzar_muse_node(self, entry: DiademaEntry, now: float):
        self._terminar_proceso(entry)
        self._liberar_adaptador(entry)
        while (
            self._adaptadores_libres and
            self._adaptadores_libres[0] not in self._adaptadores_detectados
        ):
            stale = self._adaptadores_libres.popleft()
            self.get_logger().warn(
                f"Adaptador obsoleto {stale} retirado antes de conectar."
            )
        if not self._adaptadores_libres:
            return False
        entry.hci_device = self._adaptadores_libres.popleft()
        if entry.status_subscription is None:
            entry.status_subscription = self.create_subscription(
                String,
                f'/{entry.operador_id}/status',
                lambda msg, mac=entry.mac: self._status_callback(mac, msg),
                20,
            )

        comando = [
            self._muse_python, '-m', 'muse_hrc.muse_node',
            '--ros-args',
            '-p', f'operador_id:={entry.operador_id}',
            '-p', f'mac_address:={entry.mac}',
            '-p', f'hci_device:={entry.hci_device}',
        ]

        log_path = f'/tmp/muse_{entry.operador_id}.log'
        try:
            with open(log_path, 'w') as log_file:
                entry.proceso = subprocess.Popen(
                    comando, stdout=log_file, stderr=log_file
                )
        except (OSError, subprocess.SubprocessError) as error:
            self.get_logger().error(
                f"[{entry.operador_id}] No se pudo lanzar muse_node: {error}"
            )
            self._liberar_adaptador(entry)
            entry.estado = LOST
            entry.retry_count += 1
            entry.next_retry = now + retry_delay(entry.retry_count)
            return False
        entry.estado = CONNECTING
        entry.connect_start = now
        self._conectando_ahora = entry.mac

        self.get_logger().info(
            f"[{entry.operador_id}] muse_node lanzado en {entry.hci_device} "
            f"(PID {entry.proceso.pid})"
        )
        return True

    def _status_callback(self, mac: str, msg: String):
        """Relay child connection state and metrics to the launch terminal."""
        try:
            payload = json.loads(msg.data)
            state = payload.get('state', 'unknown')
        except (TypeError, ValueError, json.JSONDecodeError):
            self.get_logger().warn(f"[{mac}] Estado de muse_node inválido")
            return

        with self._lock:
            entry = self._diademas.get(mac)
            if entry is None:
                return

            if state == 'streaming':
                entry.estado = STREAMING
                entry.retry_count = 0
                if self._conectando_ahora == mac:
                    self._conectando_ahora = None
            elif state == 'connecting':
                entry.estado = CONNECTING
                entry.connect_start = time.time()
                if self._conectando_ahora is None:
                    self._conectando_ahora = mac
            elif state in {
                'disconnected', 'data_timeout', 'retry_required', 'error'
            }:
                if entry.estado == STREAMING:
                    entry.connect_start = time.time()
                entry.estado = CONNECTING
                if self._conectando_ahora is None:
                    self._conectando_ahora = mac

            operador = entry.operador_id
            status_payload = {
                **payload,
                'operator': operador,
                'mac': mac,
                'adapter': entry.hci_device,
            }

        # Machine-readable line consumed by the local GUI. It deliberately
        # remains in the normal pipeline log for auditing and troubleshooting.
        self.get_logger().info(
            'MUSE_STATUS_JSON=' + json.dumps(
                status_payload,
                ensure_ascii=True,
                separators=(',', ':'),
            )
        )

        if state == 'metrics':
            rates = payload.get('received_hz', {})
            published_rates = payload.get('published_hz', {})
            dropped = payload.get('dropped', {})
            queued = payload.get('queued', {})
            self.get_logger().info(
                f"[{operador}] EEG recibido={rates.get('eeg', 0)} Hz, "
                f"publicado={published_rates.get('eeg', 0)} Hz, "
                f"descartadas={dropped.get('eeg', 0)}, "
                f"en_cola={queued.get('eeg', 0)}"
            )
        elif state == 'streaming':
            self.get_logger().info(
                f"[{operador}] ✅ CONEXIÓN EXITOSA, STREAMING INICIADO"
            )
        elif state == 'connected':
            self.get_logger().info(
                f"[{operador}] Enlace GATT establecido; esperando EEG..."
            )
        elif state == 'connecting':
            self.get_logger().info(
                f"[{operador}] Conectando mediante {entry.hci_device}..."
            )
        elif state in {'disconnected', 'data_timeout', 'retry_required'}:
            self.get_logger().warn(
                f"[{operador}] Estado BLE: {state}; "
                "el coordinador programará el reintento"
            )
        elif state == 'error':
            self.get_logger().error(
                f"[{operador}] Error BLE: {payload.get('message', 'desconocido')}"
            )

    def _liberar_adaptador(self, entry: DiademaEntry):
        if entry.hci_device is None:
            return
        if (
            entry.hci_device in self._adaptadores_detectados and
            entry.hci_device != self._scan_adapter and
            entry.hci_device not in self._adaptadores_libres
        ):
            self._adaptadores_libres.append(entry.hci_device)
            self.get_logger().info(
                f"[{entry.operador_id}] Adaptador {entry.hci_device} liberado"
            )
        entry.hci_device = None

    def _terminar_proceso(self, entry: DiademaEntry):
        if entry.proceso and entry.proceso.poll() is None:
            entry.proceso.terminate()
            try:
                entry.proceso.wait(timeout=3)
            except subprocess.TimeoutExpired:
                entry.proceso.kill()
                try:
                    entry.proceso.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.get_logger().error(
                        f"[{entry.operador_id}] El proceso no terminó tras SIGKILL"
                    )
        entry.proceso = None

    # ──────────────────────────────────────────────────────────────
    #  CLEANUP
    # ──────────────────────────────────────────────────────────────
    def destroy_node(self):
        self.get_logger().info("Cerrando — terminando procesos hijos...")
        with self._lock:
            for entry in self._diademas.values():
                self._terminar_proceso(entry)
                self._liberar_adaptador(entry)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = DiscoveryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
