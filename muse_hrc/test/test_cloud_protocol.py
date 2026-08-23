"""Cloud protocol validation tests independent of a live server."""

import pytest

from muse_hrc.cloud_protocol import CloudEnvelope


def test_cloud_envelope_round_trip_preserves_command_identity():
    message = CloudEnvelope(
        type='command',
        agent_id='laboratorio-01',
        session_id='sesion-01',
        payload={'action': 'start_recording'},
    )

    restored = CloudEnvelope.from_json(message.to_json())

    assert restored == message


def test_cloud_envelope_rejects_unknown_messages_and_versions():
    with pytest.raises(ValueError, match='desconocido'):
        CloudEnvelope(type='raw_eeg', agent_id='lab', payload={})
    with pytest.raises(ValueError, match='Versión'):
        CloudEnvelope(
            type='hello', agent_id='lab', payload={}, protocol_version=2
        )
