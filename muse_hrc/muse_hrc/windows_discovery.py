"""Muse discovery through the Windows Runtime backend exposed by Bleak."""

import asyncio
import re

from muse_hrc.ble_identity import (
    MUSE_BLE_IDENTIFIERS,
    MUSE_NAME_PATTERN,
    MUSE_OUI_PREFIXES,
)
from muse_hrc.device_supervisor import DeviceRegistry


def _looks_like_muse(address, device, advertisement):
    """Identify Athena advertisements without depending on a typed Bleak API."""
    name = (
        getattr(advertisement, 'local_name', None)
        or getattr(device, 'name', None)
        or ''
    )
    if MUSE_NAME_PATTERN.search(name):
        return True
    service_uuids = getattr(advertisement, 'service_uuids', ()) or ()
    normalized = ' '.join(str(item).lower() for item in service_uuids)
    if any(identifier in normalized for identifier in MUSE_BLE_IDENTIFIERS):
        return True
    return str(address).lower().startswith(MUSE_OUI_PREFIXES)


class WindowsDeviceSupervisor:
    """Discover Muse devices on Windows without exposing Linux HCI concepts."""

    def __init__(
        self,
        max_devices=4,
        scan_seconds=12.0,
        scanner=None,
        log_callback=None,
    ):
        self.max_devices = max(1, int(max_devices))
        self.scan_seconds = float(scan_seconds)
        self._scanner = scanner
        self.log_callback = log_callback
        self.registry = DeviceRegistry()
        self._configured = set()

    def discover(self):
        observed = asyncio.run(self._scan(self.scan_seconds))
        configurations = []
        for record in self.registry.register(observed):
            if record.mac_address in self._configured:
                continue
            if len(self._configured) >= self.max_devices:
                self._log(
                    'warning',
                    f'Muse {record.mac_address} detectada, pero se alcanzó '
                    f'el límite de {self.max_devices} dispositivos',
                )
                continue
            self._configured.add(record.mac_address)
            record.hci_device = 'windows'
            configurations.append((
                record.operator_id,
                record.mac_address,
                'windows',
            ))
            self._log(
                'info',
                f'{record.mac_address} → {record.operator_id} → Windows BLE',
            )
        return configurations

    def is_visible(self, mac_address, _adapter, duration=3.0):
        return mac_address.lower() in asyncio.run(self._scan(duration))

    def cancel_scan(self):
        """Bleak's bounded WinRT discovery exits on its own timeout."""

    async def _scan(self, duration):
        scanner = self._scanner
        if scanner is None:
            try:
                from bleak import BleakScanner
            except ImportError as error:
                raise RuntimeError(
                    'El descubrimiento Windows requiere bleak. Instala las '
                    'dependencias con requirements-windows.txt.'
                ) from error
            scanner = BleakScanner
        results = await scanner.discover(
            timeout=float(duration),
            return_adv=True,
        )
        observed = set()
        for key, value in results.items():
            try:
                device, advertisement = value
            except (TypeError, ValueError):
                device, advertisement = value, None
            key_is_mac = re.match(
                r'(?i)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$', str(key)
            )
            address = getattr(device, 'address', None) or (
                key if key_is_mac else None
            )
            if address and _looks_like_muse(address, device, advertisement):
                observed.add(str(address).lower())
        return observed

    def _log(self, level, message):
        if self.log_callback is not None:
            self.log_callback(level, message)
