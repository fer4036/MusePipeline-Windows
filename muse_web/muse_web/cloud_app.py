"""Cloud control plane for remote Muse agents and browser clients."""

import asyncio
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from muse_hrc.cloud_protocol import CloudEnvelope


RESEARCHER_TOKEN = os.environ.get('MUSE_CLOUD_RESEARCHER_TOKEN', '')
OPERATOR_TOKEN = os.environ.get('MUSE_CLOUD_OPERATOR_TOKEN', '')
COMMAND_TIMEOUT_SECONDS = 15.0
LEGACY_CLOUD_COOKIE = 'muse_cloud_access'
RESEARCHER_COOKIE = 'muse_cloud_researcher'
OPERATOR_COOKIE = 'muse_cloud_operator'
CLOUD_STATIC = Path(__file__).resolve().parent / 'cloud_static'
LOGGER = logging.getLogger(__name__)


def _agent_tokens():
    serialized = os.environ.get('MUSE_CLOUD_AGENT_TOKENS', '{}')
    try:
        tokens = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            'MUSE_CLOUD_AGENT_TOKENS no contiene JSON válido'
        ) from error
    if not isinstance(tokens, dict):
        raise RuntimeError('MUSE_CLOUD_AGENT_TOKENS debe ser un objeto JSON')
    return {str(key): str(value) for key, value in tokens.items()}


def _agent_token_config():
    try:
        return _agent_tokens(), ''
    except RuntimeError as error:
        return {}, str(error)


def _bearer(authorization):
    prefix = 'Bearer '
    return authorization[len(prefix):] if authorization.startswith(prefix) else ''


def _request_token(request, authorization, *cookie_names):
    bearer = _bearer(authorization)
    if bearer:
        return bearer
    for cookie_name in cookie_names:
        token = request.cookies.get(cookie_name, '')
        if token:
            return token
    return ''


def _secure_cookie(request):
    forwarded_proto = request.headers.get('x-forwarded-proto', '')
    return request.url.scheme == 'https' or forwarded_proto == 'https'


def require_researcher(
    request: Request, authorization: str = Header(default='')
):
    candidate = _request_token(
        request,
        authorization,
        RESEARCHER_COOKIE,
        LEGACY_CLOUD_COOKIE,
    )
    if (
        not RESEARCHER_TOKEN
        or not candidate
        or not hmac.compare_digest(candidate, RESEARCHER_TOKEN)
    ):
        raise HTTPException(status_code=401, detail='Clave de investigador inválida')


def require_operator(
    request: Request, authorization: str = Header(default='')
):
    operator_candidate = _request_token(
        request,
        authorization,
        OPERATOR_COOKIE,
    )
    researcher_candidate = _request_token(
        request,
        authorization,
        RESEARCHER_COOKIE,
        LEGACY_CLOUD_COOKIE,
    )
    valid = (
        OPERATOR_TOKEN and hmac.compare_digest(
            operator_candidate, OPERATOR_TOKEN
        )
    ) or (
        RESEARCHER_TOKEN and hmac.compare_digest(
            researcher_candidate, RESEARCHER_TOKEN
        )
    )
    if not valid:
        raise HTTPException(status_code=401, detail='Clave de participante inválida')


class AgentCommand(BaseModel):
    """One allow-listed command routed to a local agent."""

    action: str = Field(pattern=(
        r'^(prepare_pipeline|start_recording|stop_recording|stop_session|'
        r'status|start_section|finish_section|submit_ground_truth)$'
    ))
    session_id: str = Field(default='', max_length=160)
    subject_code: str | None = Field(default=None, max_length=64)
    experiment: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=1000)
    max_devices: int | None = Field(default=None, ge=1, le=16)
    operator: str | None = Field(
        default=None, pattern=r'^operador_[a-z]+$'
    )
    section_id: str | None = Field(default=None, pattern=r'^paso_[1-6]$')
    task_engagement: int | None = Field(default=None, ge=1, le=5)
    effort: int | None = Field(default=None, ge=1, le=5)
    persistence: int | None = Field(default=None, ge=1, le=5)
    flow: int | None = Field(default=None, ge=1, le=5)


@dataclass
class AgentConnection:
    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    hello: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)


class AgentRegistry:
    """In-memory connection routing; suitable for one cloud instance."""

    def __init__(self):
        self.connections = {}
        self._lock = asyncio.Lock()

    async def attach(self, agent_id, websocket):
        connection = AgentConnection(websocket)
        async with self._lock:
            previous = self.connections.get(agent_id)
            self.connections[agent_id] = connection
        if previous is not None:
            await previous.websocket.close(code=1012)
        return connection

    async def detach(self, agent_id, connection):
        async with self._lock:
            if self.connections.get(agent_id) is connection:
                self.connections.pop(agent_id, None)
        for future in connection.pending.values():
            if not future.done():
                future.set_exception(ConnectionError('Agente desconectado'))

    async def command(self, agent_id, payload, session_id=''):
        connection = self.connections.get(agent_id)
        if connection is None:
            raise KeyError(agent_id)
        envelope = CloudEnvelope(
            type='command',
            agent_id=agent_id,
            session_id=session_id,
            payload=payload,
        )
        future = asyncio.get_running_loop().create_future()
        connection.pending[envelope.message_id] = future
        try:
            await connection.websocket.send_text(envelope.to_json())
            return await asyncio.wait_for(
                future, timeout=COMMAND_TIMEOUT_SECONDS
            )
        finally:
            connection.pending.pop(envelope.message_id, None)

    def public_status(self):
        return [
            {
                'agent_id': agent_id,
                'connected_at': connection.connected_at,
                'last_seen': connection.last_seen,
                'hello': connection.hello,
                'operators': list(connection.statuses.values()),
                'session': connection.session,
            }
            for agent_id, connection in sorted(self.connections.items())
        ]

    def operator_status(self):
        """Return only the state needed by participant evaluation pages."""
        result = []
        for agent_id, connection in sorted(self.connections.items()):
            session = connection.session
            result.append({
                'agent_id': agent_id,
                'last_seen': connection.last_seen,
                'operators': [
                    {
                        'operator_id': item.get(
                            'operator_id', item.get('operator')
                        ),
                        'state': item.get('state'),
                    }
                    for item in connection.statuses.values()
                ],
                'session': {
                    'running': session.get('running', False),
                    'recording': session.get('recording', False),
                    'workshop': session.get('workshop', {}),
                },
            })
        return result


registry = AgentRegistry()
app = FastAPI(
    title='Muse Research Cloud Gateway',
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount('/assets', StaticFiles(directory=CLOUD_STATIC), name='cloud-assets')


@app.get('/')
def cloud_entry(
    request: Request,
    role: str | None = None,
    token: str | None = None,
    agent: str | None = None,
):
    if token is not None:
        valid = (
            role == 'pipeline'
            and RESEARCHER_TOKEN
            and hmac.compare_digest(token, RESEARCHER_TOKEN)
        ) or (
            role == 'operator'
            and OPERATOR_TOKEN
            and hmac.compare_digest(token, OPERATOR_TOKEN)
        )
        if not valid:
            raise HTTPException(status_code=401, detail='Clave de acceso inválida')
        query = f'?role={role}' + (f'&agent={agent}' if agent else '')
        response = RedirectResponse('/' + query, status_code=303)
        cookie_name = (
            RESEARCHER_COOKIE if role == 'pipeline' else OPERATOR_COOKIE
        )
        response.set_cookie(
            cookie_name,
            token,
            httponly=True,
            secure=_secure_cookie(request),
            samesite='strict',
            max_age=12 * 60 * 60,
        )
        response.delete_cookie(LEGACY_CLOUD_COOKIE)
        return response
    return FileResponse(CLOUD_STATIC / 'index.html')


@app.get('/health')
def health():
    tokens, configuration_error = _agent_token_config()
    return {
        'ok': not configuration_error,
        'agents': len(registry.connections),
        'configured_agent_ids': sorted(tokens),
        'configuration_error': configuration_error,
    }


@app.get('/api/cloud/agents', dependencies=[Depends(require_researcher)])
def agents():
    return {'agents': registry.public_status()}


@app.get(
    '/api/cloud/operator/agents',
    dependencies=[Depends(require_operator)],
)
def operator_agents():
    return {'agents': registry.operator_status()}


@app.post(
    '/api/cloud/agents/{agent_id}/commands',
    dependencies=[Depends(require_researcher)],
)
async def send_command(agent_id: str, command: AgentCommand):
    try:
        acknowledgement = await registry.command(
            agent_id,
            command.model_dump(
                exclude={'session_id'}, exclude_none=True
            ),
            command.session_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Agente no conectado') from error
    except asyncio.TimeoutError as error:
        raise HTTPException(
            status_code=504, detail='El agente no respondió'
        ) from error
    return acknowledgement


@app.post(
    '/api/cloud/operator/agents/{agent_id}/commands',
    dependencies=[Depends(require_operator)],
)
async def send_operator_command(agent_id: str, command: AgentCommand):
    if command.action not in {
        'start_section', 'finish_section', 'submit_ground_truth'
    }:
        raise HTTPException(status_code=403, detail='Comando no permitido')
    try:
        return await registry.command(
            agent_id,
            command.model_dump(
                exclude={'session_id'}, exclude_none=True
            ),
            command.session_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Agente no conectado') from error
    except asyncio.TimeoutError as error:
        raise HTTPException(
            status_code=504, detail='El agente no respondió'
        ) from error


@app.websocket('/ws/agent/{agent_id}')
async def agent_socket(websocket: WebSocket, agent_id: str):
    tokens, configuration_error = _agent_token_config()
    if configuration_error:
        LOGGER.error('Configuracion cloud invalida: %s', configuration_error)
        await websocket.close(code=1011)
        return
    expected = tokens.get(agent_id, '')
    supplied = _bearer(websocket.headers.get('authorization', ''))
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    connection = await registry.attach(agent_id, websocket)
    try:
        while True:
            envelope = CloudEnvelope.from_json(await websocket.receive_text())
            if envelope.agent_id != agent_id:
                await websocket.close(code=1008)
                return
            connection.last_seen = time.time()
            if envelope.type == 'hello':
                connection.hello = envelope.payload
            elif envelope.type == 'status':
                operator = envelope.payload.get('operator_id')
                if operator:
                    connection.statuses[operator] = envelope.payload
            elif envelope.type == 'session_event':
                connection.session = envelope.payload
            elif envelope.type == 'command_ack':
                command_id = envelope.payload.get('command_id')
                future = connection.pending.get(command_id)
                if future is not None and not future.done():
                    future.set_result(envelope.payload)
    except WebSocketDisconnect:
        return
    finally:
        await registry.detach(agent_id, connection)


def main():
    import uvicorn
    uvicorn.run(
        'muse_web.cloud_app:app',
        host=os.environ.get('HOST', '0.0.0.0'),
        port=int(os.environ.get('PORT', '10000')),
    )


if __name__ == '__main__':
    main()
