"""Transport-independent device identity and adapter allocation policy."""

from collections import deque
from dataclasses import dataclass
import time


SCANNING = 'scanning'
CONNECTING = 'connecting'
STREAMING = 'streaming'
LOST = 'lost'
RETRY_BASE = 5.0
RETRY_MAX = 60.0


def retry_delay(retry_count):
    """Return a bounded exponential delay for a failed BLE attempt."""
    exponent = min(max(int(retry_count) - 1, 0), 4)
    return min(RETRY_BASE * (2 ** exponent), RETRY_MAX)


@dataclass
class DeviceRecord:
    """Stable identity and runtime allocation for one detected headband."""

    mac_address: str
    operator_id: str
    state: str = SCANNING
    hci_device: str = ''
    last_seen: float = 0.0
    retry_count: int = 0
    next_retry: float = 0.0


class DeviceRegistry:
    """Keep a MAC-to-operator identity stable for one process/session."""

    def __init__(self, operator_prefix='operador'):
        self.operator_prefix = operator_prefix
        self._records = {}
        self._next_index = 0

    def register(self, mac_addresses, observed_at=None):
        observed_at = time.time() if observed_at is None else observed_at
        records = []
        for mac_address in sorted({mac.lower() for mac in mac_addresses}):
            record = self._records.get(mac_address)
            if record is None:
                operator_id = (
                    f'{self.operator_prefix}_{self._operator_suffix()}'
                )
                record = DeviceRecord(mac_address, operator_id)
                self._records[mac_address] = record
            record.last_seen = float(observed_at)
            records.append(record)
        return records

    def get(self, mac_address):
        return self._records.get(mac_address.lower())

    def values(self):
        return tuple(self._records.values())

    def _operator_suffix(self):
        """Return a, b, ..., z, aa, ab ... without a hard device limit."""
        index = self._next_index
        self._next_index += 1
        result = ''
        while True:
            index, remainder = divmod(index, 26)
            result = chr(97 + remainder) + result
            if index == 0:
                return result
            index -= 1


class AdapterPool:
    """Track configured, detected, free and assigned HCI controllers."""

    def __init__(self, configured_adapters):
        self.configured = tuple(dict.fromkeys(configured_adapters))
        self.detected = {}
        self._free = deque()
        self._assigned = {}

    @property
    def free(self):
        return tuple(self._free)

    @property
    def assigned(self):
        return dict(self._assigned)

    def reconcile(self, detected):
        """Apply an HCI inventory and return removed assignment owners."""
        normalized = {
            adapter: address
            for adapter in self.configured
            if (address := detected.get(adapter))
        }
        removed_owners = []
        for owner, adapter in list(self._assigned.items()):
            if normalized.get(adapter) == self.detected.get(adapter):
                continue
            removed_owners.append(owner)
            del self._assigned[owner]
        self.detected = normalized
        used = set(self._assigned.values())
        self._free = deque(
            adapter
            for adapter in self.configured
            if adapter in normalized and adapter not in used
        )
        return removed_owners

    def reserve(self, owner):
        existing = self._assigned.get(owner)
        if existing is not None:
            return existing
        if not self._free:
            return None
        adapter = self._free.popleft()
        self._assigned[owner] = adapter
        return adapter

    def release(self, owner):
        adapter = self._assigned.pop(owner, None)
        if (
            adapter is not None
            and adapter in self.detected
            and adapter not in self._free
        ):
            self._free.append(adapter)
        return adapter
