"""Selection of platform-specific acquisition and discovery implementations."""

import platform

from muse_hrc.acquisition import MuseDeviceSession


BACKENDS = ('auto', 'athena-linux', 'brainflow')


def resolve_backend(requested='auto', system=None):
    """Resolve ``auto`` while keeping explicit choices reproducible."""
    if requested not in BACKENDS:
        raise ValueError(f'Backend desconocido: {requested}')
    if requested != 'auto':
        return requested
    detected = (system or platform.system()).lower()
    return 'brainflow' if detected == 'windows' else 'athena-linux'


def session_class(backend):
    selected = resolve_backend(backend)
    if selected == 'brainflow':
        from muse_hrc.brainflow_acquisition import BrainFlowDeviceSession
        return BrainFlowDeviceSession
    return MuseDeviceSession


def discovery_supervisor(
    backend,
    hci_devices=(),
    max_devices=4,
    manufacturer_ids=(),
    scan_seconds=12.0,
    log_callback=None,
):
    selected = resolve_backend(backend)
    if selected == 'brainflow':
        from muse_hrc.windows_discovery import WindowsDeviceSupervisor
        return WindowsDeviceSupervisor(
            max_devices=max_devices,
            scan_seconds=scan_seconds,
            log_callback=log_callback,
        )
    from muse_hrc.auto_discovery import AutomaticDeviceSupervisor
    return AutomaticDeviceSupervisor(
        hci_devices,
        manufacturer_ids=manufacturer_ids,
        scan_seconds=scan_seconds,
        log_callback=log_callback,
    )
