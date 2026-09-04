"""BrainFlow acquisition backend for Muse S Athena on Windows."""

from types import SimpleNamespace
import math
import time

from muse_hrc.acquisition import (
    EEG_RATE_HZ,
    IMU_RATE_HZ,
    PPG_RATE_HZ,
    STANDARD_GRAVITY,
    MuseDeviceSession,
)
from muse_hrc.models import EegSample, ImuSample, PpgSample


POLL_SECONDS = 0.05


def _value(item):
    return getattr(item, 'value', item)


class BrainFlowDeviceSession(MuseDeviceSession):
    """Expose BrainFlow data through the same session contract as BlueZ."""

    def __init__(self, *args, brainflow_api=None, preset='p1041', **kwargs):
        super().__init__(*args, **kwargs)
        self._brainflow_api = brainflow_api
        self.preset = preset
        self._board = None
        self._battery_state = SimpleNamespace(_battery=0.0)

    def _load_api(self):
        if self._brainflow_api is not None:
            return self._brainflow_api
        try:
            from brainflow import board_shim
        except ImportError as error:
            raise RuntimeError(
                'El backend BrainFlow no está instalado. Ejecuta pip install '
                '-r requirements-windows.txt.'
            ) from error
        return board_shim

    def _connect(self):
        board = None
        try:
            api = self._load_api()
            board_id = _value(api.BoardIds.MUSE_S_ATHENA_BOARD)
            params = api.BrainFlowInputParams()
            params.mac_address = self.mac_address
            params.other_info = f'preset={self.preset};low_latency=true'
            board = api.BoardShim(board_id, params)
            self._board = board
            with self._lock:
                self.muse = self._battery_state
            self._queue_status('connecting', backend='brainflow')
            self._log('info', f'Conectando a {self.mac_address} con BrainFlow')
            board.prepare_session()
            self._queue_status('connected', backend='brainflow')
            board.start_stream()
            with self._lock:
                self.streaming = True
                self._last_data_time = time.monotonic()
                self._stream_started_at = time.time()
                self._stream_status_sent = False
            self._log('info', 'CONEXIÓN EXITOSA, STREAMING INICIADO')
            while self._connection_is_active(self._battery_state):
                self._drain_board(api, board_id, board)
                time.sleep(POLL_SECONDS)
        except Exception as error:
            with self._lock:
                shutting_down = self._shutting_down
            if not shutting_down:
                self._log('error', str(error))
                self._queue_status(
                    'error', message=str(error), backend='brainflow'
                )
        finally:
            with self._lock:
                self.streaming = False
                self._stream_started_at = None
            self._release_board(board)
            self._board = None
            with self._lock:
                if self.muse is self._battery_state:
                    self.muse = None
                self._connecting = False
                if not self._shutting_down:
                    self._lifecycle_finished = True

    def _drain_board(self, api, board_id, board):
        presets = api.BrainFlowPresets
        default = _value(presets.DEFAULT_PRESET)
        auxiliary = _value(presets.AUXILIARY_PRESET)
        ancillary = _value(presets.ANCILLARY_PRESET)
        eeg = board.get_board_data(preset=default)
        imu = board.get_board_data(preset=auxiliary)
        optics = board.get_board_data(preset=ancillary)
        produced = 0
        produced += self._consume_eeg(api.BoardShim, board_id, default, eeg)
        produced += self._consume_imu(api.BoardShim, board_id, auxiliary, imu)
        produced += self._consume_optics(
            api.BoardShim, board_id, ancillary, optics
        )
        if produced:
            with self._lock:
                self._last_data_time = time.monotonic()
                announce = not self._stream_status_sent
                if announce:
                    self._stream_status_sent = True
            if announce:
                self._queue_status('streaming', backend='brainflow')

    def _consume_eeg(self, shim, board_id, preset, data):
        count = self._sample_count(data)
        if not count:
            return 0
        channels = list(shim.get_eeg_channels(board_id, preset))[:4]
        timestamps = self._timestamps(shim, board_id, preset, data, count)
        for index in range(count):
            self._enqueue_sample('eeg', EegSample(
                timestamp=timestamps[index],
                operator_id=self.operator_id,
                sampling_rate_hz=EEG_RATE_HZ,
                data=tuple(float(data[channel, index]) for channel in channels),
            ))
        return count

    def _consume_imu(self, shim, board_id, preset, data):
        count = self._sample_count(data)
        if not count:
            return 0
        acc = list(shim.get_accel_channels(board_id, preset))[:3]
        gyro = list(shim.get_gyro_channels(board_id, preset))[:3]
        timestamps = self._timestamps(shim, board_id, preset, data, count)
        for index in range(count):
            self._enqueue_sample('imu', ImuSample(
                timestamp=timestamps[index],
                operator_id=self.operator_id,
                angular_velocity=tuple(
                    math.radians(float(data[channel, index]))
                    for channel in gyro
                ),
                linear_acceleration=tuple(
                    float(data[channel, index]) * STANDARD_GRAVITY
                    for channel in acc
                ),
                acceleration_available=len(acc) == 3,
                sampling_rate_hz=IMU_RATE_HZ,
            ))
        return count

    def _consume_optics(self, shim, board_id, preset, data):
        count = self._sample_count(data)
        if not count:
            return 0
        channels = list(shim.get_optical_channels(board_id, preset))[:16]
        timestamps = self._timestamps(shim, board_id, preset, data, count)
        battery_channel = self._optional_channel(
            shim, 'get_battery_channel', board_id, preset
        )
        for index in range(count):
            values = [float(data[channel, index]) for channel in channels]
            values.extend([0.0] * (16 - len(values)))
            self._enqueue_sample('ppg', PpgSample(
                timestamp=timestamps[index],
                operator_id=self.operator_id,
                sampling_rate_hz=PPG_RATE_HZ,
                data=tuple(values[:16]),
            ))
        if battery_channel is not None:
            self._battery_state._battery = float(
                data[battery_channel, count - 1]
            )
        return count

    @staticmethod
    def _sample_count(data):
        shape = getattr(data, 'shape', (0, 0))
        return int(shape[1]) if len(shape) > 1 else 0

    @staticmethod
    def _timestamps(shim, board_id, preset, data, count):
        channel = shim.get_timestamp_channel(board_id, preset)
        values = [float(data[channel, index]) for index in range(count)]
        return [value if value > 0 else time.time() for value in values]

    @staticmethod
    def _optional_channel(shim, method_name, board_id, preset):
        method = getattr(shim, method_name, None)
        if method is None:
            return None
        try:
            channel = int(method(board_id, preset))
        except Exception:
            return None
        return channel if channel >= 0 else None

    @staticmethod
    def _release_board(board):
        if board is None:
            return
        try:
            board.stop_stream()
        except Exception:
            pass
        try:
            board.release_session()
        except Exception:
            pass
