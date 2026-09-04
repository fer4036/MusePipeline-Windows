"""Sample mapping tests for the optional BrainFlow Athena backend."""

import pytest

from muse_hrc.brainflow_acquisition import BrainFlowDeviceSession


class Matrix:
    def __init__(self, rows):
        self.rows = rows
        self.shape = (len(rows), len(rows[0]) if rows else 0)

    def __getitem__(self, key):
        row, column = key
        return self.rows[row][column]


class FakeShim:
    @staticmethod
    def get_eeg_channels(_board, _preset):
        return [0, 1, 2, 3]

    @staticmethod
    def get_accel_channels(_board, _preset):
        return [0, 1, 2]

    @staticmethod
    def get_gyro_channels(_board, _preset):
        return [3, 4, 5]

    @staticmethod
    def get_optical_channels(_board, _preset):
        return list(range(16))

    @staticmethod
    def get_timestamp_channel(_board, preset):
        return {0: 4, 1: 6, 2: 16}[preset]

    @staticmethod
    def get_battery_channel(_board, _preset):
        return 17


def test_brainflow_rows_map_to_canonical_models_and_units():
    session = BrainFlowDeviceSession(
        'operador_a', '00:55:da:00:00:01', 'windows'
    )
    eeg = Matrix([[1.0], [2.0], [3.0], [4.0], [1000.0]])
    imu = Matrix([
        [1.0], [2.0], [3.0], [180.0], [0.0], [-180.0], [1001.0]
    ])
    optics = Matrix(
        [[float(index)] for index in range(16)] + [[1002.0], [97.5]]
    )

    assert session._consume_eeg(FakeShim, 67, 0, eeg) == 1
    assert session._consume_imu(FakeShim, 67, 1, imu) == 1
    assert session._consume_optics(FakeShim, 67, 2, optics) == 1

    eeg_sample = session.drain('eeg')[0]
    imu_sample = session.drain('imu')[0]
    ppg_sample = session.drain('ppg')[0]
    assert eeg_sample.data == (1.0, 2.0, 3.0, 4.0)
    assert imu_sample.angular_velocity == pytest.approx(
        (3.14159265, 0, -3.14159265)
    )
    assert imu_sample.linear_acceleration == pytest.approx(
        (9.80665, 19.6133, 29.41995)
    )
    assert ppg_sample.data == tuple(float(index) for index in range(16))
    assert session._battery_state._battery == 97.5
