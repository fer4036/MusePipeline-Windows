"""Athena integration with explicit BlueZ adapter selection."""

import asyncio
import atexit
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
        devices = self._adapter.wait(
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
                self._adapter.wait(client.connect())
            except connect_errors as error:
                print(
                    f'[{self._elapsed(start):.1f}s] Failed to connect: {error}',
                    file=sys.stderr,
                )
                try:
                    self._adapter.wait(client.disconnect())
                except Exception:
                    pass
                if attempts == 1 + retries:
                    return False
                self._adapter.pump(backends.RETRY_SLEEP_TIMEOUT)
                attempts += 1
            else:
                print(f'[{self._elapsed(start):.1f}s] BLE connected.')
                self._client = client
                break

        self._adapter.connected.add(self)
        return True

    def disconnect(self):
        """Disconnect on the event loop owned by this headband."""
        client = self._client
        try:
            if client is not None:
                self._adapter.wait(client.disconnect())
        finally:
            self._client = None
            self._adapter.connected.discard(self)

    def char_write_handle(self, value_handle, value, wait_for_response=True, timeout=30):
        declaration_handle = value_handle - 1
        self._adapter.wait(
            self._client.write_gatt_char(
                declaration_handle,
                bytearray(value),
                wait_for_response,
            )
        )

    def char_write_uuid(self, uuid, value, wait_for_response=True):
        self._adapter.wait(
            self._client.write_gatt_char(
                uuid,
                bytearray(value),
                response=wait_for_response,
            )
        )

    def subscribe(self, uuid, callback=None, indication=False, wait_for_response=True):
        def wrap(gatt_characteristic, data):
            value_handle = gatt_characteristic.handle + 1
            callback(value_handle, data)

        self._adapter.wait(self._client.start_notify(uuid, wrap))


class _HciBleakBackend(backends.BleakBackend):
    def __init__(self, hci_device, disconnected_callback):
        # muselsl normally shares a module-global asyncio loop. That works while
        # every Muse lives in a separate ROS process, but it fails when the
        # standalone collector connects several headbands from worker threads.
        # Keep one loop per backend/headband and never replace muselsl's global
        # sleep function.
        self.connected = set()
        self._loop = asyncio.new_event_loop()
        atexit.register(self.stop)
        self._hci_device = hci_device
        self._disconnected_callback = disconnected_callback

    def wait(self, awaitable):
        if self._loop.is_closed():
            if hasattr(awaitable, 'close'):
                awaitable.close()
            raise RuntimeError('El event loop BLE de la diadema ya está cerrado')
        return self._loop.run_until_complete(awaitable)

    def pump(self, seconds=1):
        self.wait(asyncio.sleep(seconds))

    def stop(self):
        for device in list(self.connected):
            try:
                device.disconnect()
            except Exception:
                self.connected.discard(device)
        if not self._loop.is_closed() and not self._loop.is_running():
            self._loop.close()

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
