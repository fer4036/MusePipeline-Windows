"""Timing helpers for the Muse S Athena acquisition path."""

import threading
import time


class HostSampleClock:
    """Create ordered sample timestamps anchored to the computer wall clock.

    Athena's device tick is useful for packet decoding, but the evaluated
    firmware/dependency combination can expose a non-advancing tick. Research
    events and ground-truth forms use the computer's CLOCK_REALTIME clock, so
    sensor samples are placed on that same clock while retaining their nominal
    within-batch spacing.
    """

    def __init__(self, sampling_rate_hz, time_func=time.time):
        self.sampling_rate_hz = float(sampling_rate_hz)
        self.time_func = time_func
        self._last_timestamp = None
        self._lock = threading.Lock()

    def timestamps(self, sample_count, received_at=None):
        """Return `sample_count` ordered timestamps ending near receipt time."""
        sample_count = int(sample_count)
        if sample_count <= 0:
            return []
        receipt = float(
            self.time_func() if received_at is None else received_at
        )
        step = 1.0 / self.sampling_rate_hz
        host_start = receipt - (sample_count - 1) * step
        with self._lock:
            start = host_start
            if self._last_timestamp is not None:
                start = max(start, self._last_timestamp + step)
            values = [start + index * step for index in range(sample_count)]
            self._last_timestamp = values[-1]
        return values
