"""Contract tests for platform selection and WinRT discovery."""

from types import SimpleNamespace

from muse_hrc.backends import resolve_backend
from muse_hrc.windows_discovery import WindowsDeviceSupervisor


class FakeScanner:
    devices = {}

    @classmethod
    async def discover(cls, timeout, return_adv):
        assert timeout > 0
        assert return_adv is True
        return cls.devices


def _advertisement(name, uuids=()):
    device = SimpleNamespace(address='00:55:DA:00:00:01', name=name)
    advertisement = SimpleNamespace(local_name=name, service_uuids=uuids)
    return device, advertisement


def test_auto_backend_uses_brainflow_only_on_windows():
    assert resolve_backend('auto', system='Windows') == 'brainflow'
    assert resolve_backend('auto', system='Linux') == 'athena-linux'


def test_windows_discovery_preserves_identity_across_hot_scans():
    FakeScanner.devices = {'first': _advertisement('MuseS-1234')}
    supervisor = WindowsDeviceSupervisor(scanner=FakeScanner, scan_seconds=0.01)

    assert supervisor.discover() == [
        ('operador_a', '00:55:da:00:00:01', 'windows')
    ]
    assert supervisor.discover() == []
    assert supervisor.is_visible(
        '00:55:da:00:00:01', 'windows', duration=0.01
    ) is True


def test_windows_discovery_ignores_unrelated_devices():
    device = SimpleNamespace(address='11:22:33:44:55:66', name='Keyboard')
    advertisement = SimpleNamespace(local_name='Keyboard', service_uuids=[])
    FakeScanner.devices = {'keyboard': (device, advertisement)}

    assert WindowsDeviceSupervisor(scanner=FakeScanner).discover() == []
