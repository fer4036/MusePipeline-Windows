"""Transport-independent telemetry contracts for the Muse pipeline.

These classes are the canonical representation inside the application.  ROS
messages, CSV rows, SQLite rows and future network payloads are adapters around
these models rather than the application's source of truth.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Tuple, Union


FloatTuple = Tuple[float, ...]


def _float_tuple(values: Sequence[float], size: int, field_name: str):
    converted = tuple(float(value) for value in values)
    if len(converted) != size:
        raise ValueError(
            f'{field_name} must contain {size} values; got {len(converted)}'
        )
    return converted


@dataclass(frozen=True)
class EegSample:
    """One timestamped sample from the four Muse EEG electrodes."""

    timestamp: float
    operator_id: str
    data: FloatTuple
    sampling_rate_hz: float = 256.0

    def __post_init__(self):
        object.__setattr__(self, 'timestamp', float(self.timestamp))
        object.__setattr__(
            self, 'sampling_rate_hz', float(self.sampling_rate_hz)
        )
        object.__setattr__(
            self, 'data', _float_tuple(self.data, 4, 'EEG data')
        )


@dataclass(frozen=True)
class ImuSample:
    """One IMU sample in SI units: rad/s and m/s²."""

    timestamp: float
    operator_id: str
    angular_velocity: FloatTuple
    linear_acceleration: FloatTuple = (0.0, 0.0, 0.0)
    acceleration_available: bool = True
    sampling_rate_hz: float = 52.0

    def __post_init__(self):
        object.__setattr__(self, 'timestamp', float(self.timestamp))
        object.__setattr__(
            self, 'sampling_rate_hz', float(self.sampling_rate_hz)
        )
        object.__setattr__(
            self,
            'angular_velocity',
            _float_tuple(self.angular_velocity, 3, 'angular velocity'),
        )
        object.__setattr__(
            self,
            'linear_acceleration',
            _float_tuple(self.linear_acceleration, 3, 'linear acceleration'),
        )


@dataclass(frozen=True)
class PpgSample:
    """One raw optical/PPG sample from the 16 Athena channels."""

    timestamp: float
    operator_id: str
    data: FloatTuple
    sampling_rate_hz: float = 64.0

    def __post_init__(self):
        object.__setattr__(self, 'timestamp', float(self.timestamp))
        object.__setattr__(
            self, 'sampling_rate_hz', float(self.sampling_rate_hz)
        )
        object.__setattr__(
            self, 'data', _float_tuple(self.data, 16, 'PPG data')
        )


@dataclass(frozen=True)
class DeviceStatus:
    """Machine-readable device lifecycle event, independent of ROS."""

    state: str
    timestamp: float
    operator_id: str
    mac_address: str = ''
    adapter: str = ''
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self):
        payload = {
            'state': self.state,
            'timestamp': float(self.timestamp),
            'operator_id': self.operator_id,
        }
        if self.mac_address:
            payload['mac'] = self.mac_address
        if self.adapter:
            payload['adapter'] = self.adapter
        payload.update(dict(self.details))
        return payload


TelemetrySample = Union[EegSample, ImuSample, PpgSample]
