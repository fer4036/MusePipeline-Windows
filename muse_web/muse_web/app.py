"""FastAPI entry point for the LAN-only Muse research interface."""

import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from muse_web.session_manager import SessionManager
from muse_web.lan_access import MDNS_HOST, MdnsPublisher, lan_ip


HOST = os.environ.get('MUSE_WEB_HOST', '0.0.0.0')
PORT = int(os.environ.get('MUSE_WEB_PORT', '8765'))
STATIC_DIR = Path(__file__).resolve().parent / 'static'
ACCESS_COOKIE = 'muse_lan_access'


def _load_access_tokens():
    """Load stable LAN credentials, creating a private file on first use."""
    configured_path = os.environ.get(
        'MUSE_ACCESS_FILE',
        str(Path.home() / '.config' / 'muse-research' / 'access.json'),
    )
    path = Path(configured_path).expanduser()
    stored = {}
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        pass

    researcher = (
        os.environ.get('MUSE_RESEARCHER_TOKEN') or
        stored.get('researcher') or secrets.token_urlsafe(24)
    )
    operator = (
        os.environ.get('MUSE_OPERATOR_TOKEN') or
        stored.get('operator') or secrets.token_urlsafe(24)
    )
    if hmac.compare_digest(researcher, operator):
        operator = secrets.token_urlsafe(24)

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(
                {'researcher': researcher, 'operator': operator}, indent=2
            ),
            encoding='utf-8',
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError:
        # Environment variables still provide persistence on read-only systems.
        pass
    return researcher, operator


RESEARCHER_TOKEN, OPERATOR_TOKEN = _load_access_tokens()
manager = SessionManager()
mdns_publisher = MdnsPublisher()


class StartSessionRequest(BaseModel):
    """Validated session fields accepted from the local UI."""

    subject_code: str = Field(min_length=1, max_length=64)
    experiment: str = Field(min_length=1, max_length=80)
    hci_devices: str = Field(default='hci1,hci2', max_length=80)
    notes: str = Field(default='', max_length=1000)


class ExportRequest(BaseModel):
    """Validated request for regenerating one session text export."""

    session_name: str = Field(min_length=1, max_length=160)


class TopicHzRequest(BaseModel):
    """Validated ROS topic frequency measurement."""

    operator: str = Field(pattern=r'^operador_[a-z]+$')
    signal: str = Field(pattern=r'^(eeg|imu|ppg)$')


class WorkshopSectionRequest(BaseModel):
    """One of the six fixed sections in the physical Pick and Place protocol."""

    section_id: str = Field(pattern=r'^paso_[1-6]$')
    operator: str | None = Field(
        default=None,
        pattern=r'^operador_[a-z]+$',
    )


class GroundTruthRequest(BaseModel):
    """Four-item situational cognitive engagement response."""

    operator: str = Field(pattern=r'^operador_[a-z]+$')
    section_id: str = Field(pattern=r'^paso_[1-6]$')
    task_engagement: int = Field(ge=1, le=5)
    effort: int = Field(ge=1, le=5)
    persistence: int = Field(ge=1, le=5)
    flow: int = Field(ge=1, le=5)


def _request_tokens(request):
    tokens = []
    cookie_token = request.cookies.get(ACCESS_COOKIE, '')
    if cookie_token:
        tokens.append(cookie_token)
    authorization = request.headers.get('Authorization', '')
    if authorization.startswith('Bearer '):
        header_token = authorization[7:]
        if header_token and header_token not in tokens:
            tokens.append(header_token)
    return tokens


def _same_token(candidate, expected):
    return bool(candidate) and hmac.compare_digest(candidate, expected)


def _validate_browser_mutation(request):
    if request.method in {'GET', 'HEAD', 'OPTIONS'}:
        return
    if request.headers.get('X-Muse-Request') != 'muse-web-ui':
        raise HTTPException(status_code=403, detail='Solicitud de interfaz inválida')
    origin = request.headers.get('Origin')
    host = request.headers.get('Host', '')
    if origin and origin not in {f'http://{host}', f'https://{host}'}:
        raise HTTPException(status_code=403, detail='Origen no permitido')


def require_researcher(request: Request):
    if not any(
        _same_token(token, RESEARCHER_TOKEN)
        for token in _request_tokens(request)
    ):
        raise HTTPException(status_code=401, detail='Clave de investigador inválida')
    _validate_browser_mutation(request)


def require_operator(request: Request):
    if not any(
        _same_token(token, OPERATOR_TOKEN) or
        _same_token(token, RESEARCHER_TOKEN)
        for token in _request_tokens(request)
    ):
        raise HTTPException(status_code=401, detail='Clave de acceso inválida')
    _validate_browser_mutation(request)


def _operator_workshop():
    status = manager.workshop_status()
    status['responses'] = [
        {
            key: item.get(key)
            for key in (
                'operator', 'section_id', 'measurement_number',
                'section_measurement_number', 'submitted_at',
            )
        }
        for item in status.get('responses', [])
    ]
    status['intervals'] = [
        {
            key: item.get(key)
            for key in (
                'operator', 'section_id', 'started_at', 'ended_at', 'operators',
                'assessment_count', 'next_assessment_due_at',
                'assessment_due', 'assessment_seconds_remaining',
            )
        }
        for item in status.get('intervals', [])
    ]
    active = next(
        (item for item in reversed(status['intervals']) if item['ended_at'] is None),
        None,
    )
    status['active_section'] = active
    return status


@asynccontextmanager
async def lifespan(_app):
    mdns_publisher.start()
    try:
        yield
    finally:
        mdns_publisher.stop()
        manager.shutdown()


app = FastAPI(
    title='Muse Research Local',
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get('/')
def web_entry(role: str | None = None, token: str | None = None):
    """Exchange a one-time URL credential for an HttpOnly browser cookie."""
    if token is not None:
        valid = (
            role == 'pipeline' and _same_token(token, RESEARCHER_TOKEN)
        ) or (
            role == 'operator' and _same_token(token, OPERATOR_TOKEN)
        )
        if not valid:
            raise HTTPException(status_code=401, detail='Enlace de acceso inválido')
        # The short-lived query fallback supports browsers that reject cookies
        # for a LAN IP. JavaScript stores it for the tab and removes it at once.
        response = RedirectResponse(
            url=f'/?role={role}&access={quote(token)}',
            status_code=303,
        )
        response.set_cookie(
            ACCESS_COOKIE,
            token,
            max_age=12 * 60 * 60,
            httponly=True,
            samesite='strict',
            secure=False,
        )
        return response
    return FileResponse(STATIC_DIR / 'index.html')


@app.get('/api/status', dependencies=[Depends(require_researcher)])
def status():
    return manager.status()


@app.get('/api/operator/status', dependencies=[Depends(require_operator)])
def operator_status():
    status = manager.status()
    return {
        'running': status['running'],
        'recording': status['recording'],
        'operators': [
            {'operator': item['operator'], 'state': item['state']}
            for item in status.get('operators', [])
        ],
    }


@app.get('/api/sessions', dependencies=[Depends(require_researcher)])
def sessions():
    return manager.list_sessions()


@app.post('/api/session/start', dependencies=[Depends(require_researcher)])
def start_session(payload: StartSessionRequest):
    try:
        return manager.start(
            payload.subject_code,
            payload.experiment,
            payload.hci_devices,
            payload.notes,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post('/api/session/stop', dependencies=[Depends(require_researcher)])
def stop_session():
    try:
        return manager.stop(export=True)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post('/api/recording/start', dependencies=[Depends(require_researcher)])
def start_recording():
    try:
        return manager.start_recording()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post('/api/recording/stop', dependencies=[Depends(require_researcher)])
def stop_recording():
    try:
        return manager.stop_recording()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get('/api/database/preview', dependencies=[Depends(require_researcher)])
def database_preview(session_name: str | None = None, limit: int = 5):
    try:
        return manager.preview(session_name, limit)
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post('/api/topic/hz', dependencies=[Depends(require_researcher)])
def topic_hz(payload: TopicHzRequest):
    try:
        return manager.topic_hz(payload.operator, payload.signal)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get('/api/ros/graph', dependencies=[Depends(require_researcher)])
def ros_graph():
    try:
        return manager.ros_graph()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get('/api/workshop', dependencies=[Depends(require_researcher)])
def workshop_status():
    return manager.workshop_status()


@app.get('/api/operator/workshop', dependencies=[Depends(require_operator)])
def operator_workshop_status():
    return _operator_workshop()


@app.post(
    '/api/workshop/section/start',
    dependencies=[Depends(require_operator)],
)
def start_workshop_section(payload: WorkshopSectionRequest):
    try:
        return manager.start_workshop_section(
            payload.section_id, payload.operator
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post(
    '/api/workshop/section/finish',
    dependencies=[Depends(require_operator)],
)
def finish_workshop_section(payload: WorkshopSectionRequest):
    try:
        return manager.finish_workshop_section(
            payload.section_id, payload.operator
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post('/api/ground-truth', dependencies=[Depends(require_operator)])
def submit_ground_truth(payload: GroundTruthRequest):
    try:
        scores = {
            'task_engagement': payload.task_engagement,
            'effort': payload.effort,
            'persistence': payload.persistence,
            'flow': payload.flow,
        }
        return manager.submit_ground_truth(
            payload.operator, payload.section_id, scores
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post('/api/session/export', dependencies=[Depends(require_researcher)])
def export_session(payload: ExportRequest):
    try:
        return manager.export(payload.session_name)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get(
    '/api/session/{session_name}/csv/{operator}/{profile}',
    dependencies=[Depends(require_researcher)],
)
def download_csv(session_name: str, operator: str, profile: str):
    try:
        path = manager.csv_path(session_name, operator, profile)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(
        path,
        filename=f'{session_name}_{operator}_{profile}.csv',
        media_type='text/csv; charset=utf-8',
    )


app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')


def main():
    """Run the server on the trusted local network."""
    address = lan_ip()
    base = f'http://{MDNS_HOST}:{PORT}'
    fallback = f'http://{address}:{PORT}'
    print('Muse Research — acceso únicamente desde la red local')
    print(f'Investigador: {base}/?role=pipeline&token={quote(RESEARCHER_TOKEN)}')
    print(f'Participantes: {base}/?role=operator&token={quote(OPERATOR_TOKEN)}')
    print(f'Respaldo por IP actual: {fallback}')
    print('Los archivos permanecen en esta computadora; no abras el puerto en el router.')
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)


if __name__ == '__main__':
    main()
