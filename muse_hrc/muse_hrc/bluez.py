"""BlueZ discovery and link preparation with no ROS dependency."""

import re
import subprocess
import threading
import time
from pathlib import Path

from muse_hrc.ble_identity import (
    extract_current_scan_addresses,
    identify_muse,
    summarize_properties,
)


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


class BlueZBackend:
    """Query controllers and identify Muse advertisements through BlueZ."""

    def __init__(
        self,
        configured_adapters,
        manufacturer_ids=(),
        runner=None,
        popen_factory=None,
        sleep=None,
        log_callback=None,
        sysfs_root='/sys/class/bluetooth',
        btusb_autosuspend_path=(
            '/sys/module/btusb/parameters/enable_autosuspend'
        ),
    ):
        self.configured_adapters = tuple(configured_adapters)
        self.manufacturer_ids = set(manufacturer_ids)
        self._runner = runner or subprocess.run
        self._popen_factory = popen_factory or subprocess.Popen
        self._sleep = sleep or time.sleep
        self._log_callback = log_callback
        self._sysfs_root = Path(sysfs_root)
        self._btusb_autosuspend_path = Path(btusb_autosuspend_path)
        self._power_policy_warnings = set()
        self._process_lock = threading.Lock()
        self._scan_process = None

    def detect_adapters(self):
        result = self._runner(
            ['hciconfig', '-a'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f'hciconfig no pudo consultar adaptadores: {detail}'
            )
        detected = parse_hci_controllers(result.stdout)
        available = {
            adapter: detected[adapter]
            for adapter in self.configured_adapters
            if adapter in detected and detected[adapter]
        }
        for adapter in available:
            policy = self.adapter_power_policy(adapter)
            if (
                policy == 'auto' and
                self.btusb_autosuspend_enabled() is not False and
                adapter not in self._power_policy_warnings
            ):
                self._power_policy_warnings.add(adapter)
                self._log(
                    'warning',
                    f'{adapter}: autosuspensión USB activa; puede provocar '
                    'pérdidas GATT. Configura power/control=on antes de una '
                    'sesión experimental.',
                )
        return available

    def adapter_power_policy(self, adapter):
        """Return the parent USB device's runtime power policy when present."""
        device_link = self._sysfs_root / adapter / 'device'
        try:
            device_path = device_link.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return None
        for candidate in (device_path, *device_path.parents):
            if not (candidate / 'idVendor').is_file():
                continue
            try:
                return (candidate / 'power' / 'control').read_text().strip()
            except OSError:
                return None
        return None

    def btusb_autosuspend_enabled(self):
        """Return the btusb module autosuspend switch when observable."""
        try:
            value = self._btusb_autosuspend_path.read_text().strip().lower()
        except OSError:
            return None
        if value in {'y', 'yes', '1', 'on'}:
            return True
        if value in {'n', 'no', '0', 'off'}:
            return False
        return None

    def scan(self, adapter, controller_address, duration=SCAN_SECONDS):
        """Scan one controller and return positively identified Muse MACs."""
        candidates = self.visible_addresses(
            adapter, controller_address, duration=duration
        )
        return self.identify_devices(candidates)

    def visible_addresses(
        self, adapter, controller_address, duration=SCAN_SECONDS
    ):
        """Return addresses advertised during one controller-bound scan."""
        self._log('info', f'Iniciando scan BLE en {adapter}')
        process = self._popen_factory(
            ['bluetoothctl'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with self._process_lock:
            self._scan_process = process
        try:
            process.stdin.write(f'select {controller_address}\nscan on\n')
            process.stdin.flush()
            self._sleep(float(duration))
            process.stdin.write('scan off\nquit\n')
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=6)
            return extract_current_scan_addresses(stdout + stderr)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            with self._process_lock:
                if self._scan_process is process:
                    self._scan_process = None

    def is_visible(
        self,
        mac_address,
        adapter,
        controller_address,
        duration=3.0,
    ):
        """Check that one known address is advertising before GATT connect."""
        observed = self.visible_addresses(
            adapter, controller_address, duration=duration
        )
        return mac_address.lower() in observed

    def cancel_scan(self):
        """Stop an in-flight bluetoothctl child during collector shutdown."""
        with self._process_lock:
            process = self._scan_process
        if process is not None and process.poll() is None:
            process.terminate()

    def identify_devices(self, candidates):
        identified = set()
        for mac_address in sorted(candidates):
            result = self._runner(
                ['bluetoothctl', 'info', mac_address],
                capture_output=True,
                text=True,
                timeout=5,
            )
            properties = result.stdout + result.stderr
            reason = identify_muse(
                mac_address,
                properties,
                self.manufacturer_ids,
            )
            if reason:
                identified.add(mac_address.lower())
                self._log(
                    'info',
                    f'[{mac_address}] Muse identificada por {reason}',
                )
            else:
                self._log(
                    'debug',
                    f'[{mac_address}] Dispositivo descartado: '
                    f'{summarize_properties(properties)}',
                )
        return identified

    def prepare_device(self, mac_address, adapter, controller_address):
        """Initialize then release Athena's temporary BlueZ GATT link."""
        self._log(
            'info',
            f'[{mac_address}] Inicializando enlace BLE mediante {adapter}',
        )
        initialized = False
        try:
            result = self.controller_command(
                controller_address,
                f'connect {mac_address}',
                10,
            )
            output = (result.stdout + result.stderr).lower()
            initialized = result.returncode == 0 and 'successful' in output
        finally:
            try:
                self.controller_command(
                    controller_address,
                    f'disconnect {mac_address}',
                    5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        if self.wait_for_release(mac_address):
            self._sleep(BLUEZ_RELEASE_SETTLE)
        return initialized

    def controller_command(self, controller_address, command, timeout):
        return self._runner(
            ['bluetoothctl', '--timeout', str(int(timeout))],
            input=f'select {controller_address}\n{command}\n',
            capture_output=True,
            text=True,
            timeout=timeout + 3,
        )

    def wait_for_release(
        self,
        mac_address,
        timeout=BLUEZ_RELEASE_TIMEOUT,
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._runner(
                ['bluetoothctl', 'info', mac_address],
                capture_output=True,
                text=True,
                timeout=2,
            )
            properties = (result.stdout + result.stderr).lower()
            if (
                'connected: no' in properties
                or 'device not available' in properties
            ):
                return True
            self._sleep(0.2)
        return False

    def _log(self, level, message):
        if self._log_callback is not None:
            self._log_callback(level, message)
