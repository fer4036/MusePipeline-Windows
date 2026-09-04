"""Tests for local Muse S Athena timing compatibility."""

from muse_hrc.athena_protocol import HostSampleClock


def test_host_clock_tracks_wall_time_when_device_tick_is_frozen():
    clock = HostSampleClock(4.0)

    first = clock.timestamps(4, received_at=100.0)
    second = clock.timestamps(4, received_at=101.0)

    assert first == [99.25, 99.5, 99.75, 100.0]
    assert second == [100.25, 100.5, 100.75, 101.0]


def test_host_clock_never_moves_backwards_on_batched_delivery():
    clock = HostSampleClock(2.0)

    first = clock.timestamps(2, received_at=10.0)
    second = clock.timestamps(2, received_at=9.9)

    assert first == [9.5, 10.0]
    assert second == [10.5, 11.0]
