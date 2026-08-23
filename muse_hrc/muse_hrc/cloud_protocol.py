"""Versioned messages exchanged between local agents and the cloud gateway."""

from dataclasses import dataclass, field
import json
import time
import uuid


PROTOCOL_VERSION = 1
MESSAGE_TYPES = {
    'hello',
    'heartbeat',
    'inventory',
    'status',
    'command',
    'command_ack',
    'session_event',
    'export_manifest',
}


@dataclass(frozen=True)
class CloudEnvelope:
    """Serializable and validated WebSocket message envelope."""

    type: str
    agent_id: str
    payload: dict = field(default_factory=dict)
    session_id: str = ''
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self):
        if self.type not in MESSAGE_TYPES:
            raise ValueError(f'Tipo de mensaje desconocido: {self.type}')
        if not self.agent_id.strip():
            raise ValueError('agent_id no puede estar vacío')
        if not isinstance(self.payload, dict):
            raise TypeError('payload debe ser un objeto JSON')
        if int(self.protocol_version) != PROTOCOL_VERSION:
            raise ValueError(
                f'Versión de protocolo no compatible: {self.protocol_version}'
            )

    def as_dict(self):
        return {
            'protocol_version': int(self.protocol_version),
            'type': self.type,
            'message_id': self.message_id,
            'agent_id': self.agent_id,
            'session_id': self.session_id,
            'timestamp': float(self.timestamp),
            'payload': self.payload,
        }

    def to_json(self):
        return json.dumps(
            self.as_dict(), ensure_ascii=False, separators=(',', ':')
        )

    @classmethod
    def from_json(cls, serialized):
        data = json.loads(serialized)
        if not isinstance(data, dict):
            raise ValueError('El mensaje WebSocket debe ser un objeto JSON')
        return cls(
            protocol_version=data.get('protocol_version'),
            type=data.get('type', ''),
            message_id=data.get('message_id', ''),
            agent_id=data.get('agent_id', ''),
            session_id=data.get('session_id', ''),
            timestamp=data.get('timestamp', 0.0),
            payload=data.get('payload', {}),
        )
