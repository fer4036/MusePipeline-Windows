"""Stable LAN address publication for the local Muse web interface."""

import ipaddress
import os
import shutil
import socket
import subprocess
import threading


MDNS_HOST = 'muse-research.local'
DEFAULT_LAB_NETWORK = '192.168.0.0/24'


def global_ipv4_addresses():
    """Return non-loopback IPv4 addresses currently assigned by Linux."""
    try:
        result = subprocess.run(
            ['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    addresses = []
    for line in result.stdout.splitlines():
        fields = line.split()
        try:
            address = fields[fields.index('inet') + 1].split('/', 1)[0]
            parsed = ipaddress.ip_address(address)
        except (ValueError, IndexError):
            continue
        if parsed.version == 4 and not parsed.is_loopback:
            addresses.append(address)
    return addresses


def lan_ip():
    """Return the lab IPv4, preferring its manual 192.168.0.0/24 LAN."""
    assigned = global_ipv4_addresses()
    configured = os.environ.get('MUSE_WEB_LAN_IP', '').strip()
    if configured:
        try:
            if (
                configured in assigned and
                ipaddress.ip_address(configured).version == 4
            ):
                return configured
        except ValueError:
            pass

    network_text = os.environ.get(
        'MUSE_WEB_LAN_NETWORK', DEFAULT_LAB_NETWORK
    ).strip()
    try:
        preferred_network = ipaddress.ip_network(network_text, strict=False)
    except ValueError:
        preferred_network = ipaddress.ip_network(DEFAULT_LAB_NETWORK)
    for address in assigned:
        if ipaddress.ip_address(address) in preferred_network:
            return address

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('10.255.255.255', 1))
        routed = sock.getsockname()[0]
        if routed != '0.0.0.0':
            return routed
        return assigned[0] if assigned else '127.0.0.1'
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


class MdnsPublisher:
    """Keep one mDNS alias pinned to the currently routed LAN address."""

    def __init__(
        self,
        hostname=MDNS_HOST,
        interval=5.0,
        address_factory=lan_ip,
        popen_factory=subprocess.Popen,
    ):
        self.hostname = hostname
        self.interval = interval
        self._address_factory = address_factory
        self._popen_factory = popen_factory
        self._process = None
        self._address = None
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def available(self):
        return shutil.which('avahi-publish-address') is not None

    def start(self):
        """Start monitoring without blocking web-server startup."""
        if not self.available or self._thread is not None:
            return False
        self._stop_event.clear()
        self._refresh()
        self._thread = threading.Thread(
            target=self._monitor,
            name='muse-mdns-publisher',
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self):
        """Stop monitoring and withdraw the alias from Avahi."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, self.interval + 1.0))
        self._thread = None
        self._stop_process()

    def _monitor(self):
        while not self._stop_event.wait(self.interval):
            self._refresh()

    def _refresh(self):
        address = self._address_factory()
        process_alive = (
            self._process is not None and self._process.poll() is None
        )
        if address == self._address and process_alive:
            return
        self._stop_process()
        self._address = address
        if address == '127.0.0.1':
            return
        try:
            self._process = self._popen_factory(
                [
                    'avahi-publish-address', '-a', '-R',
                    self.hostname, address,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._process = None

    def _stop_process(self):
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
