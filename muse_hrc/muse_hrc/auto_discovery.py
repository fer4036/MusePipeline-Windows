"""Automatic Muse discovery and HCI assignment without ROS 2."""

from muse_hrc.bluez import BlueZBackend, SCAN_SECONDS
from muse_hrc.device_supervisor import AdapterPool, DeviceRegistry


class AutomaticDeviceSupervisor:
    """Discover visible Muse devices and allocate one controller to each."""

    SCAN_OWNER = '__bluez_scan__'

    def __init__(
        self,
        hci_devices,
        manufacturer_ids=(),
        scan_seconds=SCAN_SECONDS,
        bluez_backend=None,
        log_callback=None,
    ):
        self.hci_devices = tuple(hci_devices)
        self.scan_seconds = float(scan_seconds)
        self.log_callback = log_callback
        self.registry = DeviceRegistry()
        self.adapters = AdapterPool(self.hci_devices)
        self.bluez = bluez_backend or BlueZBackend(
            self.hci_devices,
            manufacturer_ids=manufacturer_ids,
            log_callback=log_callback,
        )

    def discover(self):
        """Return configurations for new or newly reassigned devices."""
        detected = self.bluez.detect_adapters()
        removed_operators = self.adapters.reconcile(detected)
        for operator_id in removed_operators:
            for record in self.registry.values():
                if record.operator_id == operator_id:
                    record.hci_device = ''
                    break
        if not self.adapters.free:
            return []
        scan_adapter = self.adapters.reserve(self.SCAN_OWNER)
        controller_address = detected[scan_adapter]
        try:
            mac_addresses = self.bluez.scan(
                scan_adapter,
                controller_address,
                duration=self.scan_seconds,
            )
        finally:
            self.adapters.release(self.SCAN_OWNER)

        records = self.registry.register(mac_addresses)
        configurations = []
        for record in records:
            if record.hci_device:
                continue
            adapter = self.adapters.reserve(record.operator_id)
            if adapter is None:
                self._log(
                    'warning',
                    f'[{record.operator_id}] Muse {record.mac_address} '
                    'detectada, pero no queda un adaptador libre',
                )
                continue
            record.hci_device = adapter
            controller_address = detected[adapter]
            try:
                initialized = self.bluez.prepare_device(
                    record.mac_address,
                    adapter,
                    controller_address,
                )
            except Exception as error:
                initialized = False
                self._log(
                    'warning',
                    f'[{record.operator_id}] Preparación BLE incompleta: '
                    f'{error}',
                )
            if not initialized:
                self._log(
                    'warning',
                    f'[{record.operator_id}] Athena intentará la conexión '
                    'directa',
                )
            configurations.append((
                record.operator_id,
                record.mac_address,
                adapter,
            ))
            self._log(
                'info',
                f'{record.mac_address} → {record.operator_id} → {adapter}',
            )
        return configurations

    def is_visible(self, mac_address, adapter, duration=3.0):
        """Confirm a known disconnected Muse is advertising on its adapter."""
        detected = self.bluez.detect_adapters()
        controller_address = detected.get(adapter)
        if not controller_address:
            self._log(
                'warning',
                f'[{mac_address}] {adapter} no está disponible para verificar '
                'reconexión',
            )
            return False
        return self.bluez.is_visible(
            mac_address,
            adapter,
            controller_address,
            duration=duration,
        )

    def _log(self, level, message):
        if self.log_callback is not None:
            self.log_callback(level, message)
