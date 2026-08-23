"""Athena integration with explicit BlueZ adapter selection."""

import asyncio
import sys
import time

from muselsl import backends
from muselsl import athena as athena_module
Athena = athena_module.Athena


class _HciBleakDevice(backends.BleakDevice):
    """Bleak device pinned to one BlueZ controller."""

    def __init__(self, adapter, address, hci_device, disconnected_callback, name=None):
        super().__init__(adapter, address, name=name)
        self._hci_device = hci_device
        self._disconnected_callback = disconnected_callback

    def _refresh_address(self, start):
        """Refresh a changing address using the assigned controller."""
        if not self._name:
            return
        print(f'[{self._elapsed(start):.1f}s] Scanning for {self._name}...')
        devices = backends._wait(
            backends.bleak.BleakScanner.discover(
                timeout=5.0,
                adapter=self._hci_device,
            )
        )
        for device in devices:
            if device.name and self._name in device.name:
                self._address = device.address
                return
        print(f'[{self._elapsed(start):.1f}s] {self._name} not seen during scan')

    def connect(self, retries):
        start = time.monotonic()
        attempts = 1
        connect_errors = (
            backends.bleak.exc.BleakDeviceNotFoundError,
            backends.bleak.exc.BleakError,
            TimeoutError,
            asyncio.TimeoutError,
        )

        while True:
            if attempts == 1:
                print(
                    f'[{self._elapsed(start):.1f}s] Connecting to '
                    f'{self._address} through {self._hci_device}...'
                )
            else:
                self._refresh_address(start)
                print(f'[{self._elapsed(start):.1f}s] Connection attempt {attempts}...')

            client = backends.bleak.BleakClient(
                self._address,
                timeout=30.0,
                adapter=self._hci_device,
                disconnected_callback=self._disconnected_callback,
            )
            try:
                backends._wait(client.connect())
            except connect_errors as error:
                print(
                    f'[{self._elapsed(start):.1f}s] Failed to connect: {error}',
                    file=sys.stderr,
                )
                try:
                    backends._wait(client.disconnect())
                except Exception:
                    pass
                if attempts == 1 + retries:
                    return False
                backends.sleep(backends.RETRY_SLEEP_TIMEOUT)
                attempts += 1
            else:
                print(f'[{self._elapsed(start):.1f}s] BLE connected.')
                self._client = client
                break

        self._adapter.connected.add(self)
        return True


class _HciBleakBackend(backends.BleakBackend):
    def __init__(self, hci_device, disconnected_callback):
        super().__init__()
        self._hci_device = hci_device
        self._disconnected_callback = disconnected_callback

    def connect(self, address, retries, name=None):
        device = _HciBleakDevice(
            self,
            address,
            self._hci_device,
            self._disconnected_callback,
            name=name,
        )
        if not device.connect(retries):
            return None
        return device


class AdapterAthena(Athena):
    """Athena variant that binds Bleak to ``hci_device``."""

    def __init__(self, *args, hci_device='hci0', disconnected_callback=None, **kwargs):
        kwargs['backend'] = 'bleak'
        super().__init__(*args, **kwargs)
        self.hci_device = hci_device
        self.disconnected_callback = disconnected_callback

    def connect(self, interface=None, retries=0):
        """Connect using the selected controller and Athena's init sequence."""
        self.adapter = _HciBleakBackend(
            self.hci_device,
            self.disconnected_callback,
        )
        self.adapter.start()
        device = self.adapter.connect(self.address, retries, self.name)
        if device is None:
            return False
        self.device = device

        if not self._has_athena_data_char():
            self.disconnect()
            raise RuntimeError(
                'Device does not expose the Athena data characteristic; '
                'it may not be a Muse S Athena.'
            )

        self._subscribe_control()
        self._subscribe_data()
        if self.disable_light:
            self._write_cmd_str('L0')
        self._run_init_sequence()
        self.last_timestamp = self.time_func()
        return True
