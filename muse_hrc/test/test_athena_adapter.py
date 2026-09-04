"""Regression tests for standalone multi-headband BLE event loops."""

import threading

from muse_hrc.athena_adapter import _HciBleakBackend


def test_each_athena_backend_can_pump_concurrently_on_its_own_loop():
    barrier = threading.Barrier(2)
    loop_ids = []
    errors = []

    def run_backend(hci_device):
        backend = _HciBleakBackend(hci_device, None)
        try:
            loop_ids.append(id(backend._loop))
            barrier.wait(timeout=2)
            for _ in range(5):
                backend.pump(0.001)
        except Exception as error:  # pragma: no cover - assertion reports it
            errors.append(error)
        finally:
            backend.stop()

    threads = [
        threading.Thread(target=run_backend, args=('hci1',)),
        threading.Thread(target=run_backend, args=('hci2',)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(set(loop_ids)) == 2
