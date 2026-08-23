"""Tests for Python telemetry contracts and sample decoding."""

import time

import numpy as np
import pytest

from muse_hrc.acquisition import MuseDeviceSession
from muse_hrc.models import EegSample, ImuSample, PpgSample


def test_models_validate_channel_counts():
    with pytest.raises(ValueError, match='4 values'):
        EegSample(0.0, 'operador_a', (1.0, 2.0))
    with pytest.raises(ValueError, match='16 values'):
        PpgSample(0.0, 'operador_a', (1.0,) * 15)


def test_callbacks_produce_python_models_without_ros():
    session = MuseDeviceSession(
        'operador_a',
        '00:55:da:bb:cc:8e',
        'hci1',
    )
    with session._lock:
        session.streaming = True
    session._eeg_callback(np.ones((4, 2)), None)
    session._acc_callback(np.ones((3, 1)), None)
    session._imu_callback(np.full((3, 1), 180.0), None)
    session._optics_callback(np.ones((16, 1)), None)

    eeg = session.drain('eeg')
    imu = session.drain('imu')
    ppg = session.drain('ppg')

    assert len(eeg) == 2 and all(isinstance(row, EegSample) for row in eeg)
    assert len(imu) == 1 and isinstance(imu[0], ImuSample)
    assert imu[0].angular_velocity == pytest.approx((3.14159265,) * 3)
    assert imu[0].linear_acceleration == pytest.approx((9.80665,) * 3)
    assert len(ppg) == 1 and isinstance(ppg[0], PpgSample)
    assert session.drain_status()[0].state == 'streaming'


def test_poll_requests_one_coordinated_retry_after_lifecycle_finishes():
    session = MuseDeviceSession('operador_a', '00:55:da:bb:cc:8e')
    with session._lock:
        session._lifecycle_finished = True
        session._connecting = False

    assert session.poll() is True
    assert session.poll() is False
    assert session.drain_status()[0].state == 'retry_required'


def test_poll_marks_stream_without_data_as_timed_out():
    session = MuseDeviceSession(
        'operador_a',
        '00:55:da:bb:cc:8e',
        data_timeout=1.0,
    )
    with session._lock:
        session.streaming = True
        session._last_data_time = time.monotonic() - 2.0
        session.muse = object()

    assert session.poll() is False
    assert session.streaming is False
    assert session.drain_status()[0].state == 'data_timeout'


def test_streaming_lifecycle_does_not_count_as_pending_connection():
    session = MuseDeviceSession('operador_a', '00:55:da:bb:cc:8e')
    with session._lock:
        session._connecting = True
        session.streaming = True

    assert session.connecting is True
    assert session.connection_pending is False

    with session._lock:
        session.streaming = False

    assert session.connection_pending is True
