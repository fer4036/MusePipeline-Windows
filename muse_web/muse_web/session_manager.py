"""Safe lifecycle management for local Muse acquisition sessions."""

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from muse_web.csv_export import EXPORT_PROFILES, export_session_csvs
from muse_web.workshop import (
    GROUND_TRUTH_INTERVAL_SECONDS,
    PRACTICE,
    public_definition,
    section_by_id,
)


HCI_PATTERN = re.compile(r'^hci\d+(,hci\d+)*$')
SESSION_NAME_PATTERN = re.compile(r'^\d{8}_\d{6}_[a-z0-9_-]+_[a-z0-9_-]+(?:_\d+)?$')
OPERATOR_PATTERN = re.compile(r'^operador_[a-z]+$')
STATUS_PATTERN = re.compile(r'MUSE_STATUS_JSON=(\{.*\})')
SIGNALS = {'eeg', 'imu', 'ppg'}
GROUND_TRUTH_SCORES = ('task_engagement', 'effort', 'persistence', 'flow')
PIPELINE_BACKENDS = {'ros2', 'standalone'}


def safe_component(value, fallback):
    normalized = unicodedata.normalize('NFKD', value)
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii').lower()
    cleaned = re.sub(r'[^a-z0-9_-]+', '-', ascii_value).strip('-_')
    return cleaned[:48] or fallback


def _process_is_alive(pid):
    """Return whether a Linux PID exists and is not a zombie."""
    try:
        process_stat = (Path('/proc') / str(pid) / 'stat').read_text(
            encoding='utf-8'
        )
        state = process_stat.rsplit(')', 1)[1].split()[0]
        if state == 'Z':
            return False
        os.kill(pid, 0)
    except (IndexError, OSError, ValueError):
        return False
    return True


def _process_command(pid):
    """Read one process command as argv without invoking a shell."""
    try:
        raw = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
    except OSError:
        return []
    return [
        item.decode('utf-8', errors='replace')
        for item in raw.split(b'\0') if item
    ]


class AttachedProcess:
    """Small Popen-compatible handle for a collector owned by an older GUI."""

    def __init__(self, pid, alive_checker=_process_is_alive):
        self.pid = pid
        self._alive_checker = alive_checker

    def poll(self):
        return None if self._alive_checker(self.pid) else -1

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.05)
        return -1


class SessionManager:
    """Own at most one pipeline process and one session directory."""

    def __init__(
        self,
        sessions_root=None,
        popen_factory=subprocess.Popen,
        standalone_python=None,
        process_alive_checker=_process_is_alive,
        process_command_reader=_process_command,
    ):
        default_root = Path.home() / 'MuseResearch' / 'sessions'
        self.sessions_root = Path(
            sessions_root or os.environ.get('MUSE_SESSIONS_DIR', default_root)
        ).expanduser().resolve()
        self.sessions_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.sessions_root, 0o700)
        self._popen_factory = popen_factory
        workspace_root = Path(__file__).resolve().parents[2]
        configured_python = os.environ.get('MUSE_STANDALONE_PYTHON')
        if standalone_python is not None:
            selected_python = Path(standalone_python).expanduser()
        elif (
            configured_python
            and Path(configured_python).expanduser().is_file()
        ):
            selected_python = Path(configured_python).expanduser()
        else:
            candidates = (
                workspace_root / 'muse_env' / 'bin' / 'python',
                workspace_root.parent / 'muse_env' / 'bin' / 'python',
                Path(sys.executable),
            )
            selected_python = next(
                (candidate for candidate in candidates if candidate.is_file()),
                Path(sys.executable),
            )
        self._standalone_python = str(selected_python)
        self._process_alive_checker = process_alive_checker
        self._process_command_reader = process_command_reader
        self._lock = threading.RLock()
        self._process = None
        self._active = None
        self._recover_standalone_session()

    def start(
        self,
        subject_code,
        experiment,
        hci_devices,
        notes='',
        backend='ros2',
    ):
        with self._lock:
            if self._active is not None:
                raise RuntimeError(
                    'La sesión anterior debe detenerse y cerrarse primero'
                )
            if not HCI_PATTERN.fullmatch(hci_devices):
                raise ValueError(
                    'Adaptadores inválidos; usa una lista como hci1,hci2,hci3,hci4'
                )
            if backend not in PIPELINE_BACKENDS:
                raise ValueError('Backend inválido; usa ros2 o standalone')

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            subject_slug = safe_component(subject_code, 'participante')
            experiment_slug = safe_component(experiment, 'experimento')
            base_name = f'{timestamp}_{subject_slug}_{experiment_slug}'
            session_dir = self._unique_session_dir(base_name)
            session_dir.mkdir(mode=0o700)

            metadata = {
                'session_name': session_dir.name,
                'subject_code': subject_code.strip(),
                'experiment': experiment.strip(),
                'notes': notes.strip(),
                'hci_devices': hci_devices,
                'backend': backend,
                'started_at': datetime.now().astimezone().isoformat(),
                'status': 'preparing',
                'recording': False,
                'recording_intervals': [],
            }
            self._write_metadata(session_dir, metadata)

            database_path = session_dir / 'raw.sqlite'
            self._initialize_ground_truth_schema(database_path)
            os.chmod(database_path, 0o600)
            log_path = session_dir / 'pipeline.log'
            control_path = session_dir / 'control.json'
            if backend == 'standalone':
                self._write_control_request(
                    control_path,
                    False,
                    'initial',
                )
                command = [
                    self._standalone_python,
                    '-m',
                    'muse_hrc.standalone',
                    '--hci-devices',
                    hci_devices,
                    '--db',
                    str(database_path),
                    '--paused',
                    '--control-file',
                    str(control_path),
                ]
            else:
                command = [
                    'ros2', 'launch', 'muse_hrc', 'muse_system.launch.py',
                    f'hci_devices:={hci_devices}',
                    f'database_path:={database_path}',
                    'recording_enabled:=false',
                ]
            log_file = log_path.open('a', encoding='utf-8')
            os.chmod(log_path, 0o600)
            try:
                process = self._popen_factory(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    text=True,
                )
            except Exception as error:
                log_file.close()
                metadata['status'] = 'launch_error'
                metadata['launch_error'] = str(error)
                self._write_metadata(session_dir, metadata)
                raise RuntimeError(
                    f'No se pudo iniciar el backend {backend}: {error}'
                ) from error
            log_file.close()

            metadata['status'] = 'ready'
            metadata['pipeline_pid'] = process.pid
            self._write_metadata(session_dir, metadata)
            self._process = process
            self._active = {
                'directory': session_dir,
                'database': database_path,
                'log': log_path,
                'metadata': metadata,
                'control': control_path,
            }
            return self.status()

    def start_recording(self):
        """Start persistence while leaving BLE connections untouched."""
        with self._lock:
            active = self._require_active()
            metadata = active['metadata']
            if metadata.get('recording'):
                raise RuntimeError('La grabación ya está activa')
            self._call_recording_service(True)
            started_at = datetime.now().astimezone().isoformat()
            metadata['recording'] = True
            metadata['status'] = 'recording'
            metadata.setdefault('recording_intervals', []).append({
                'started_at': started_at,
                'ended_at': None,
            })
            self._write_metadata(active['directory'], metadata)
            return self.status()

    def stop_recording(self):
        """Pause persistence while keeping discovery and streaming active."""
        with self._lock:
            active = self._require_active()
            metadata = active['metadata']
            if not metadata.get('recording'):
                raise RuntimeError('La grabación no está activa')
            self._initialize_ground_truth_schema(active['database'])
            with sqlite3.connect(active['database'], timeout=5.0) as connection:
                open_section = connection.execute(
                    'SELECT section_id FROM workshop_sections '
                    'WHERE ended_at IS NULL LIMIT 1'
                ).fetchone()
            if open_section:
                raise RuntimeError(
                    f'Termina {open_section[0]} antes de detener la grabación'
                )
            self._call_recording_service(False)
            self._close_metadata_interval(metadata)
            metadata['recording'] = False
            metadata['status'] = 'ready'
            self._write_metadata(active['directory'], metadata)
            return self.status()

    def stop(self, export=True):
        with self._lock:
            if self._process is None or self._active is None:
                raise RuntimeError('No hay una sesión activa')
            process = self._process
            active = self._active
            metadata = active['metadata']
            if metadata.get('recording'):
                try:
                    self._call_recording_service(False)
                except RuntimeError as error:
                    metadata['recording_stop_error'] = str(error)
                self._close_metadata_interval(metadata)
                metadata['recording'] = False
            self._close_active_workshop_interval(active, 'session_stopped')
            self._stop_process(process)

            metadata['ended_at'] = datetime.now().astimezone().isoformat()
            metadata['exit_code'] = process.poll()
            metadata['status'] = 'completed'
            self._write_metadata(active['directory'], metadata)

            export_error = None
            if export and active['database'].is_file():
                try:
                    export_session_csvs(
                        active['database'], active['directory'], metadata
                    )
                except Exception as error:
                    export_error = str(error)
                    metadata['export_error'] = export_error
                    self._write_metadata(active['directory'], metadata)

            result = self._session_summary(active['directory'])
            result['export_error'] = export_error
            self._process = None
            self._active = None
            return result

    def export(self, session_name):
        session_dir = self._resolve_session(session_name)
        database_path = session_dir / 'raw.sqlite'
        metadata = self._read_metadata(session_dir)
        self._initialize_ground_truth_schema(database_path)
        export_session_csvs(database_path, session_dir, metadata)
        return self._session_summary(session_dir)

    def status(self):
        with self._lock:
            if self._active is None or self._process is None:
                return {
                    'running': False,
                    'recording': False,
                    'session': None,
                    'operators': [],
                    'log_tail': [],
                }
            running = self._process.poll() is None
            if (
                not running and
                self._active['metadata']['status'] not in {
                    'completed', 'pipeline_exited',
                }
            ):
                metadata = self._active['metadata']
                metadata['status'] = 'pipeline_exited'
                metadata['recording'] = False
                self._close_metadata_interval(metadata)
                self._close_active_workshop_interval(
                    self._active, 'pipeline_exited'
                )
                metadata['exit_code'] = self._process.poll()
                metadata['ended_at'] = datetime.now().astimezone().isoformat()
                self._write_metadata(self._active['directory'], metadata)
            return {
                'running': running,
                'recording': bool(
                    running and self._active['metadata'].get('recording')
                ),
                'session': self._session_summary(self._active['directory']),
                'operators': self._operator_status(self._active['log']),
                'log_tail': self._tail(self._active['log'], max_lines=60),
            }

    def list_sessions(self):
        sessions = []
        for path in sorted(self.sessions_root.iterdir(), reverse=True):
            if path.is_dir() and (path / 'metadata.json').is_file():
                sessions.append(self._session_summary(path))
        return sessions

    def csv_path(self, session_name, operator, profile):
        if not OPERATOR_PATTERN.fullmatch(operator):
            raise ValueError('Operador inválido')
        if profile not in EXPORT_PROFILES:
            raise ValueError('Perfil de exportación inválido')
        label = EXPORT_PROFILES[profile]['filename_label']
        path = (
            self._resolve_session(session_name) /
            f'export_{operator}_{label}.csv'
        )
        if not path.is_file():
            raise FileNotFoundError(
                f'La sesión todavía no tiene CSV {profile} para {operator}'
            )
        return path

    def preview(self, session_name=None, limit=5):
        """Return a small, fixed-schema preview without exposing SQL."""
        limit = min(max(int(limit), 1), 20)
        if session_name:
            database = self._resolve_session(session_name) / 'raw.sqlite'
        else:
            with self._lock:
                database = self._require_active()['database']
        if not database.is_file():
            return {'database_exists': False, 'tables': {}}

        tables = {}
        uri = f'{database.as_uri()}?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
            connection.row_factory = sqlite3.Row
            for table in (
                'eeg_logs', 'imu_logs', 'ppg_logs',
                'workshop_sections', 'ground_truth_responses',
            ):
                if not self._table_exists(connection, table):
                    continue
                count = connection.execute(
                    f'SELECT COUNT(*) FROM {table}'
                ).fetchone()[0]
                rows = connection.execute(
                    f'SELECT * FROM {table} ORDER BY id DESC LIMIT ?',
                    (limit,),
                ).fetchall()
                tables[table] = {
                    'count': count,
                    'rows': [dict(row) for row in rows],
                }
        return {'database_exists': True, 'tables': tables}

    def workshop_status(self):
        """Return protocol progress and response coverage for the active session."""
        definition = public_definition()
        with self._lock:
            if self._active is None:
                return {
                    **definition,
                    'active_section': None,
                    'intervals': [],
                    'responses': [],
                }
            database = self._active['database']
            self._initialize_ground_truth_schema(database)
            with sqlite3.connect(database, timeout=5.0) as connection:
                connection.row_factory = sqlite3.Row
                intervals = [
                    dict(row) for row in connection.execute(
                        'SELECT * FROM workshop_sections ORDER BY id'
                    )
                ]
                if self._table_exists(connection, 'eeg_logs'):
                    for interval in intervals:
                        end_ids = interval.get('eeg_end_ids')
                        if end_ids is None:
                            end_ids = json.dumps(
                                self._eeg_watermarks(connection)
                            )
                        observed = self._operators_between_marks(
                            interval.get('eeg_start_ids'), end_ids
                        )
                        owner = interval.get('operator')
                        interval['operators'] = (
                            [owner] if owner and owner in observed else []
                        )
                else:
                    for interval in intervals:
                        interval['operators'] = []
                responses = [
                    dict(row) for row in connection.execute(
                        'SELECT id, operator, section_id, measurement_number, '
                        'section_measurement_number, due_at, window_started_at, '
                        'window_ended_at, submitted_at, '
                        'submitted_at_iso, task_engagement, effort, persistence, '
                        'flow, engagement_score, effort_persistence_score '
                        'FROM ground_truth_responses ORDER BY id'
                    )
                ]
                now = time.time()
                for interval in intervals:
                    matching_section = [
                        response for response in responses
                        if response['operator'] == interval.get('operator') and
                        response['section_id'] == interval['section_id']
                    ]
                    matching_operator = [
                        response for response in responses
                        if response['operator'] == interval.get('operator')
                    ]
                    interval['assessment_count'] = len(matching_section)
                    if interval['ended_at'] is None:
                        first_started = min(
                            item['started_at'] for item in intervals
                            if item.get('operator') == interval.get('operator')
                        )
                        last_submitted = (
                            matching_operator[-1]['submitted_at']
                            if matching_operator else first_started
                        )
                        due_at = (
                            last_submitted + GROUND_TRUTH_INTERVAL_SECONDS
                        )
                        interval['next_assessment_due_at'] = due_at
                        interval['assessment_due'] = now >= due_at
                        interval['assessment_seconds_remaining'] = max(
                            0, int(due_at - now)
                        )
                    else:
                        interval['next_assessment_due_at'] = None
                        interval['assessment_due'] = False
                        interval['assessment_seconds_remaining'] = None
            active = next(
                (item for item in reversed(intervals) if item['ended_at'] is None),
                None,
            )
            return {
                **definition,
                'active_section': active,
                'intervals': intervals,
                'responses': responses,
            }

    def start_workshop_section(self, section_id, operator=None):
        """Open a workshop interval on the same Unix clock used by Muse rows."""
        with self._lock:
            active = self._require_active()
            if not active['metadata'].get('recording'):
                raise RuntimeError(
                    'Comienza la grabación Muse antes de iniciar una sección'
                )
            operator_states = {
                item['operator']: item['state']
                for item in self._operator_status(active['log'])
            }
            if operator is None or not OPERATOR_PATTERN.fullmatch(operator):
                raise ValueError('Operador inválido')
            streaming = operator_states.get(operator) == 'streaming'
            if not streaming:
                raise RuntimeError(
                    'La diadema del operador seleccionado no está transmitiendo'
                )
            section = self._known_section(section_id)
            timestamp, timestamp_iso = self._wall_timestamp()
            self._initialize_ground_truth_schema(active['database'])
            try:
                with sqlite3.connect(active['database'], timeout=5.0) as connection:
                    connection.execute('PRAGMA busy_timeout=5000')
                    connection.execute('BEGIN IMMEDIATE')
                    unfinished = connection.execute(
                        'SELECT section_id FROM workshop_sections '
                        'WHERE operator=? AND ended_at IS NULL LIMIT 1',
                        (operator,),
                    ).fetchone()
                    if unfinished:
                        raise RuntimeError(
                            f'Primero termina la sección {unfinished[0]}'
                        )
                    completed_ids = {
                        row[0] for row in connection.execute(
                            'SELECT section_id FROM workshop_sections '
                            'WHERE operator=?',
                            (operator,),
                        )
                    }
                    expected = next(
                        (
                            item['id'] for item in PRACTICE['sections']
                            if item['id'] not in completed_ids
                        ),
                        None,
                    )
                    if section_id != expected:
                        raise RuntimeError(
                            f'La siguiente sección requerida es {expected}'
                        )
                    existing = connection.execute(
                        'SELECT 1 FROM workshop_sections '
                        'WHERE operator=? AND section_id=?',
                        (operator, section_id),
                    ).fetchone()
                    if existing:
                        raise RuntimeError(
                            'Esta sección ya fue registrada en la sesión actual'
                        )
                    connection.execute(
                        'INSERT INTO workshop_sections '
                        '(practice_id, operator, section_id, section_number, section_title, '
                        'target_minutes, started_at, started_at_iso, '
                        'eeg_start_ids) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (
                            PRACTICE['id'], operator, section['id'], section['number'],
                            section['title'], section['minutes'], timestamp,
                            timestamp_iso,
                            json.dumps(self._eeg_watermarks(connection)),
                        ),
                    )
            except sqlite3.Error as error:
                raise RuntimeError(
                    f'No se pudo iniciar la sección: {error}'
                ) from error
            return self.workshop_status()

    def finish_workshop_section(self, section_id, operator):
        """Close the active interval while acquisition keeps running."""
        with self._lock:
            active = self._require_active()
            self._known_section(section_id)
            if not OPERATOR_PATTERN.fullmatch(operator or ''):
                raise ValueError('Operador inválido')
            timestamp, timestamp_iso = self._wall_timestamp()
            self._initialize_ground_truth_schema(active['database'])
            with sqlite3.connect(active['database'], timeout=5.0) as connection:
                connection.execute('PRAGMA busy_timeout=5000')
                interval = connection.execute(
                    'SELECT started_at FROM workshop_sections '
                    'WHERE operator=? AND section_id=? AND ended_at IS NULL',
                    (operator, section_id),
                ).fetchone()
                if interval is None:
                    raise RuntimeError('La sección indicada no está activa')
                latest_response = connection.execute(
                    'SELECT submitted_at FROM ground_truth_responses '
                    'WHERE operator=? ORDER BY id DESC LIMIT 1',
                    (operator,),
                ).fetchone()
                first_started = connection.execute(
                    'SELECT MIN(started_at) FROM workshop_sections '
                    'WHERE operator=?', (operator,),
                ).fetchone()[0]
                cadence_start = (
                    latest_response[0] if latest_response else first_started
                )
                due_at = cadence_start + GROUND_TRUTH_INTERVAL_SECONDS
                if timestamp >= due_at:
                    raise RuntimeError(
                        'Hay una medición de engagement pendiente; '
                        'respóndela antes de terminar la sección'
                    )
                eeg_end_ids = json.dumps(self._eeg_watermarks(connection))
                cursor = connection.execute(
                    'UPDATE workshop_sections SET ended_at=?, ended_at_iso=?, '
                    'closed_reason=?, eeg_end_ids=? '
                    'WHERE operator=? AND section_id=? AND ended_at IS NULL',
                    (
                        timestamp, timestamp_iso, 'completed', eeg_end_ids,
                        operator, section_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError('La sección indicada no está activa')
            return self.workshop_status()

    def submit_ground_truth(self, operator, section_id, scores):
        """Store one recurring ten-minute assessment in the active section."""
        with self._lock:
            active = self._require_active()
            if not OPERATOR_PATTERN.fullmatch(operator):
                raise ValueError('Operador inválido')
            known_operators = {
                item['operator'] for item in self._operator_status(active['log'])
            }
            if operator not in known_operators:
                raise ValueError(
                    'El operador no pertenece a las diademas de esta sesión'
                )
            section = self._known_section(section_id)
            normalized = {}
            for field in GROUND_TRUTH_SCORES:
                value = scores.get(field)
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f'Respuesta inválida para {field}')
                if value < 1 or value > 5:
                    raise ValueError('Las respuestas Likert deben estar entre 1 y 5')
                normalized[field] = value

            timestamp, timestamp_iso = self._wall_timestamp()
            engagement_score = sum(normalized.values()) / 4.0
            effort_persistence = (
                normalized['effort'] + normalized['persistence']
            ) / 2.0
            self._initialize_ground_truth_schema(active['database'])
            try:
                with sqlite3.connect(active['database'], timeout=5.0) as connection:
                    connection.execute('PRAGMA busy_timeout=5000')
                    connection.row_factory = sqlite3.Row
                    interval = connection.execute(
                        'SELECT id, started_at, ended_at, eeg_start_ids '
                        'FROM workshop_sections '
                        'WHERE operator=? AND section_id=? AND ended_at IS NULL '
                        'ORDER BY id DESC LIMIT 1',
                        (operator, section_id),
                    ).fetchone()
                    if interval is None:
                        raise RuntimeError(
                            'Inicia y mantén activa la sección antes de responder'
                        )
                    latest = connection.execute(
                        'SELECT submitted_at, eeg_end_ids, '
                        'section_measurement_number '
                        'FROM ground_truth_responses '
                        'WHERE operator=? '
                        'ORDER BY id DESC LIMIT 1',
                        (operator,),
                    ).fetchone()
                    latest_in_section = connection.execute(
                        'SELECT section_measurement_number '
                        'FROM ground_truth_responses '
                        'WHERE operator=? AND section_id=? '
                        'ORDER BY id DESC LIMIT 1',
                        (operator, section_id),
                    ).fetchone()
                    first_interval = connection.execute(
                        'SELECT started_at, eeg_start_ids '
                        'FROM workshop_sections WHERE operator=? '
                        'ORDER BY id LIMIT 1', (operator,),
                    ).fetchone()
                    window_started_at = (
                        latest['submitted_at']
                        if latest else first_interval['started_at']
                    )
                    due_at = (
                        window_started_at + GROUND_TRUTH_INTERVAL_SECONDS
                    )
                    if timestamp < due_at:
                        remaining = max(1, int(due_at - timestamp))
                        raise RuntimeError(
                            'La siguiente medición estará disponible en '
                            f'{remaining // 60:02d}:{remaining % 60:02d}'
                        )
                    eeg_start_ids = (
                        latest['eeg_end_ids'] if latest
                        else first_interval['eeg_start_ids']
                    )
                    eeg_end_ids = json.dumps(
                        self._eeg_watermarks(connection)
                    )
                    if self._table_exists(connection, 'eeg_logs'):
                        participants = self._operators_between_marks(
                            eeg_start_ids, eeg_end_ids
                        )
                        if operator not in participants:
                            raise RuntimeError(
                                'No hay EEG de este operador dentro de la sección'
                            )
                    global_number = connection.execute(
                        'SELECT COUNT(*) + 1 FROM ground_truth_responses '
                        'WHERE operator=?',
                        (operator,),
                    ).fetchone()[0]
                    section_number = (
                        latest_in_section['section_measurement_number'] + 1
                        if latest_in_section else 1
                    )
                    connection.execute(
                        'INSERT INTO ground_truth_responses '
                        '(practice_id, section_interval_id, section_id, '
                        'section_title, operator, measurement_number, '
                        'section_measurement_number, due_at, window_started_at, '
                        'window_ended_at, eeg_start_ids, eeg_end_ids, '
                        'section_started_at, section_ended_at, submitted_at, '
                        'submitted_at_iso, task_engagement, effort, persistence, '
                        'flow, engagement_score, effort_persistence_score, '
                        'clock_source, instrument_doi) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
                        '?, ?, ?, ?, ?, ?, ?, ?)',
                        (
                            PRACTICE['id'], interval['id'], section_id,
                            section['title'], operator, global_number,
                            section_number, due_at, window_started_at, timestamp,
                            eeg_start_ids, eeg_end_ids, interval['started_at'],
                            timestamp, timestamp, timestamp_iso,
                            normalized['task_engagement'], normalized['effort'],
                            normalized['persistence'], normalized['flow'],
                            engagement_score, effort_persistence,
                            'CLOCK_REALTIME_UNIX',
                            '10.1007/s10459-011-9272-9',
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise RuntimeError(
                    'La medición ya fue registrada; actualiza la página'
                ) from error
            except sqlite3.Error as error:
                raise RuntimeError(
                    f'No se pudo guardar el ground truth: {error}'
                ) from error
            return {
                'operator': operator,
                'section_id': section_id,
                'measurement_number': global_number,
                'section_measurement_number': section_number,
                'submitted_at': timestamp,
                'next_assessment_due_at': (
                    timestamp + GROUND_TRUTH_INTERVAL_SECONDS
                ),
                'engagement_score': engagement_score,
                'effort_persistence_score': effort_persistence,
            }

    def topic_hz(self, operator, signal_name, seconds=4):
        """Measure one ROS topic using the same CLI used during local tests."""
        if not OPERATOR_PATTERN.fullmatch(operator):
            raise ValueError('Operador inválido')
        if signal_name not in SIGNALS:
            raise ValueError('Señal inválida')
        active = self._require_active()
        topic = f'/{operator}/{signal_name}'
        if active['metadata'].get('backend') == 'standalone':
            status = next(
                (
                    item for item in self._operator_status(active['log'])
                    if item['operator'] == operator
                ),
                None,
            )
            rate = status and status.get('rates', {}).get(signal_name)
            if not rate:
                raise RuntimeError(
                    f'No hay una tasa reciente para {operator}/{signal_name}'
                )
            return {
                'topic': f'{operator}/{signal_name}',
                'average_hz': float(rate),
                'output': ['Métrica calculada por el núcleo Python'],
            }
        command = ['ros2', 'topic', 'hz', topic, '--window', '200']
        process = self._popen_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
        try:
            output, _ = process.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                output, _ = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate(timeout=2)
        rates = re.findall(r'average rate:\s*([0-9.]+)', output or '')
        if not rates:
            raise RuntimeError(
                f'No se recibieron muestras de {topic} durante {seconds} s'
            )
        return {
            'topic': topic,
            'average_hz': float(rates[-1]),
            'output': (output or '').strip().splitlines()[-8:],
        }

    def ros_graph(self):
        """Return the current ROS nodes and typed topics for diagnostics."""
        active = self._require_active()
        if active['metadata'].get('backend') == 'standalone':
            operators = self._operator_status(active['log'])
            return {
                'nodes': ['standalone_collector'],
                'topics': [
                    f"/{item['operator']}/{signal_name} [Python stream]"
                    for item in operators
                    for signal_name in sorted(SIGNALS)
                ],
            }
        commands = {
            'nodes': ['ros2', 'node', 'list'],
            'topics': ['ros2', 'topic', 'list', '-t'],
        }
        result = {}
        for key, command in commands.items():
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError(f'No se pudo consultar ROS 2: {error}') from error
            if completed.returncode != 0:
                raise RuntimeError(
                    f'ROS 2 no pudo consultar {key}: '
                    + (completed.stderr or completed.stdout).strip()[-300:]
                )
            result[key] = completed.stdout.strip().splitlines()
        return result

    def shutdown(self):
        with self._lock:
            if self._active is not None and self._process is not None:
                if self._active['metadata'].get('backend') == 'standalone':
                    # The Python collector is the local edge process. A web
                    # restart must not interrupt BLE or data capture; the next
                    # SessionManager will reattach through session metadata.
                    self._process = None
                    self._active = None
                    return
                self.stop(export=True)

    def _recover_standalone_session(self):
        """Reattach to the newest verified standalone collector, if any."""
        allowed_states = {'ready', 'recording'}
        for session_dir in sorted(self.sessions_root.iterdir(), reverse=True):
            metadata_path = session_dir / 'metadata.json'
            if not session_dir.is_dir() or not metadata_path.is_file():
                continue
            try:
                metadata = self._read_metadata(session_dir)
                pid = int(metadata.get('pipeline_pid', 0))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if (
                metadata.get('backend') != 'standalone' or
                metadata.get('status') not in allowed_states or
                pid <= 1 or
                not self._standalone_process_matches(pid, session_dir)
            ):
                continue

            metadata['web_reattach_count'] = (
                int(metadata.get('web_reattach_count', 0)) + 1
            )
            metadata['web_reattached_at'] = (
                datetime.now().astimezone().isoformat()
            )
            self._write_metadata(session_dir, metadata)
            self._process = AttachedProcess(
                pid, alive_checker=self._process_alive_checker
            )
            self._active = {
                'directory': session_dir,
                'database': session_dir / 'raw.sqlite',
                'log': session_dir / 'pipeline.log',
                'metadata': metadata,
                'control': session_dir / 'control.json',
            }
            return

    def _standalone_process_matches(self, pid, session_dir):
        """Reject stale/reused PIDs before attaching or signalling a process."""
        if not self._process_alive_checker(pid):
            return False
        command = self._process_command_reader(pid)
        expected_database = str((session_dir / 'raw.sqlite').resolve())
        expected_control = str((session_dir / 'control.json').resolve())
        try:
            database_matches = (
                command[command.index('--db') + 1] == expected_database
            )
            control_matches = (
                command[command.index('--control-file') + 1] == expected_control
            )
        except (ValueError, IndexError):
            return False
        return (
            'muse_hrc.standalone' in command and
            database_matches and control_matches
        )

    def _require_active(self):
        if self._active is None or self._process is None:
            raise RuntimeError('No hay un pipeline activo')
        if self._process.poll() is not None:
            raise RuntimeError('El pipeline ya terminó')
        return self._active

    @staticmethod
    def _close_metadata_interval(metadata):
        intervals = metadata.get('recording_intervals', [])
        if intervals and intervals[-1].get('ended_at') is None:
            intervals[-1]['ended_at'] = datetime.now().astimezone().isoformat()

    @staticmethod
    def _wall_timestamp():
        """Return Unix seconds and an ISO representation from one clock read."""
        timestamp = time.time_ns() / 1_000_000_000.0
        timestamp_iso = datetime.fromtimestamp(
            timestamp
        ).astimezone().isoformat(timespec='microseconds')
        return timestamp, timestamp_iso

    @staticmethod
    def _known_section(section_id):
        section = section_by_id(section_id)
        if section is None:
            raise ValueError('Sección de taller inválida')
        return section

    @staticmethod
    def _initialize_ground_truth_schema(database):
        """Create local protocol tables before ROS begins concurrent writes."""
        with sqlite3.connect(database, timeout=5.0) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA busy_timeout=5000')
            section_sql = '''
                CREATE TABLE IF NOT EXISTS workshop_sections (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    practice_id     TEXT NOT NULL,
                    operator        TEXT,
                    section_id      TEXT NOT NULL,
                    section_number  INTEGER NOT NULL,
                    section_title   TEXT NOT NULL,
                    target_minutes  INTEGER NOT NULL,
                    started_at      REAL NOT NULL,
                    started_at_iso  TEXT NOT NULL,
                    ended_at        REAL,
                    ended_at_iso    TEXT,
                    closed_reason   TEXT,
                    eeg_start_ids   TEXT NOT NULL DEFAULT '{}',
                    eeg_end_ids     TEXT,
                    UNIQUE(operator, section_id)
                )
            '''
            connection.execute(section_sql)
            section_columns = {
                row[1] for row in connection.execute(
                    'PRAGMA table_info(workshop_sections)'
                )
            }
            if 'eeg_start_ids' not in section_columns:
                connection.execute(
                    "ALTER TABLE workshop_sections ADD COLUMN "
                    "eeg_start_ids TEXT NOT NULL DEFAULT '{}'"
                )
            if 'eeg_end_ids' not in section_columns:
                connection.execute(
                    'ALTER TABLE workshop_sections ADD COLUMN eeg_end_ids TEXT'
                )
            if 'operator' not in section_columns:
                connection.execute(
                    'ALTER TABLE workshop_sections RENAME TO '
                    'workshop_sections_legacy'
                )
                connection.execute(section_sql)
                connection.row_factory = sqlite3.Row
                current_marks = json.dumps(
                    SessionManager._eeg_watermarks(connection)
                )
                for row in connection.execute(
                    'SELECT * FROM workshop_sections_legacy ORDER BY id'
                ).fetchall():
                    end_marks = row['eeg_end_ids'] or current_marks
                    operators = set(SessionManager._operators_between_marks(
                        row['eeg_start_ids'], end_marks
                    ))
                    if SessionManager._table_exists(
                        connection, 'ground_truth_responses'
                    ):
                        operators.update(
                            item[0] for item in connection.execute(
                                'SELECT operator FROM ground_truth_responses '
                                'WHERE section_id=?',
                                (row['section_id'],),
                            )
                        )
                    for operator in sorted(operators) or [None]:
                        connection.execute(
                            'INSERT INTO workshop_sections '
                            '(practice_id, operator, section_id, section_number, '
                            'section_title, target_minutes, started_at, '
                            'started_at_iso, ended_at, ended_at_iso, '
                            'closed_reason, eeg_start_ids, eeg_end_ids) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (
                                row['practice_id'], operator, row['section_id'],
                                row['section_number'], row['section_title'],
                                row['target_minutes'], row['started_at'],
                                row['started_at_iso'], row['ended_at'],
                                row['ended_at_iso'], row['closed_reason'],
                                row['eeg_start_ids'], row['eeg_end_ids'],
                            ),
                        )
                connection.execute('DROP TABLE workshop_sections_legacy')
            response_sql = '''
                CREATE TABLE IF NOT EXISTS ground_truth_responses (
                    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                    practice_id                TEXT NOT NULL,
                    section_interval_id         INTEGER,
                    section_id                 TEXT NOT NULL,
                    section_title              TEXT NOT NULL,
                    operator                   TEXT NOT NULL,
                    measurement_number         INTEGER NOT NULL,
                    section_measurement_number INTEGER NOT NULL,
                    due_at                     REAL NOT NULL,
                    window_started_at          REAL NOT NULL,
                    window_ended_at            REAL NOT NULL,
                    eeg_start_ids              TEXT NOT NULL DEFAULT '{}',
                    eeg_end_ids                TEXT NOT NULL DEFAULT '{}',
                    section_started_at         REAL NOT NULL,
                    section_ended_at           REAL NOT NULL,
                    submitted_at               REAL NOT NULL,
                    submitted_at_iso           TEXT NOT NULL,
                    task_engagement            INTEGER NOT NULL CHECK(
                        task_engagement BETWEEN 1 AND 5
                    ),
                    effort                     INTEGER NOT NULL CHECK(
                        effort BETWEEN 1 AND 5
                    ),
                    persistence                INTEGER NOT NULL CHECK(
                        persistence BETWEEN 1 AND 5
                    ),
                    flow                       INTEGER NOT NULL CHECK(
                        flow BETWEEN 1 AND 5
                    ),
                    engagement_score           REAL NOT NULL,
                    effort_persistence_score   REAL NOT NULL,
                    clock_source               TEXT NOT NULL,
                    instrument_doi             TEXT NOT NULL,
                    UNIQUE(operator, measurement_number),
                    UNIQUE(operator, section_id, section_measurement_number)
                )
            '''
            connection.execute(response_sql)
            response_columns = {
                row[1] for row in connection.execute(
                    'PRAGMA table_info(ground_truth_responses)'
                )
            }
            if 'measurement_number' not in response_columns:
                connection.execute(
                    'ALTER TABLE ground_truth_responses RENAME TO '
                    'ground_truth_responses_legacy'
                )
                connection.execute(response_sql)
                connection.row_factory = sqlite3.Row
                global_counts = {}
                section_counts = {}
                legacy_rows = connection.execute(
                    'SELECT * FROM ground_truth_responses_legacy ORDER BY id'
                ).fetchall()
                for row in legacy_rows:
                    operator = row['operator']
                    section_id = row['section_id']
                    global_counts[operator] = global_counts.get(operator, 0) + 1
                    section_key = (operator, section_id)
                    section_counts[section_key] = (
                        section_counts.get(section_key, 0) + 1
                    )
                    interval = connection.execute(
                        'SELECT id, eeg_start_ids, eeg_end_ids '
                        'FROM workshop_sections WHERE operator=? '
                        'AND section_id=? ORDER BY id DESC LIMIT 1',
                        (operator, section_id),
                    ).fetchone()
                    eeg_start_ids = (
                        interval['eeg_start_ids'] if interval else '{}'
                    ) or '{}'
                    eeg_end_ids = (
                        interval['eeg_end_ids'] if interval else '{}'
                    ) or '{}'
                    connection.execute(
                        'INSERT INTO ground_truth_responses '
                        '(id, practice_id, section_interval_id, section_id, '
                        'section_title, operator, measurement_number, '
                        'section_measurement_number, due_at, window_started_at, '
                        'window_ended_at, eeg_start_ids, eeg_end_ids, '
                        'section_started_at, section_ended_at, submitted_at, '
                        'submitted_at_iso, task_engagement, effort, persistence, '
                        'flow, engagement_score, effort_persistence_score, '
                        'clock_source, instrument_doi) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
                        '?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (
                            row['id'], row['practice_id'],
                            interval['id'] if interval else None, section_id,
                            row['section_title'], operator,
                            global_counts[operator], section_counts[section_key],
                            row['submitted_at'], row['section_started_at'],
                            row['section_ended_at'], eeg_start_ids, eeg_end_ids,
                            row['section_started_at'], row['section_ended_at'],
                            row['submitted_at'], row['submitted_at_iso'],
                            row['task_engagement'], row['effort'],
                            row['persistence'], row['flow'],
                            row['engagement_score'],
                            row['effort_persistence_score'], row['clock_source'],
                            row['instrument_doi'],
                        ),
                    )
                connection.execute('DROP TABLE ground_truth_responses_legacy')
            connection.execute(
                'CREATE INDEX IF NOT EXISTS idx_ground_truth_alignment '
                'ON ground_truth_responses '
                '(operator, window_started_at, window_ended_at)'
            )
            connection.execute(
                'CREATE INDEX IF NOT EXISTS idx_ground_truth_section_interval '
                'ON ground_truth_responses (section_interval_id)'
            )

    @staticmethod
    def _eeg_watermarks(connection):
        """Return the newest persisted EEG row id for each operator."""
        if not SessionManager._table_exists(connection, 'eeg_logs'):
            return {}
        return {
            row[0]: int(row[1])
            for row in connection.execute(
                'SELECT operador, MAX(id) FROM eeg_logs GROUP BY operador'
            )
            if row[0] is not None and row[1] is not None
        }

    @staticmethod
    def _operators_between_marks(start_json, end_json):
        """Identify operators whose EEG row id advanced during an interval."""
        try:
            start = json.loads(start_json or '{}')
            end = json.loads(end_json or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return sorted(
            operator for operator, end_id in end.items()
            if int(end_id) > int(start.get(operator, 0))
        )

    def _close_active_workshop_interval(self, active, reason):
        if not active['database'].is_file():
            return
        timestamp, timestamp_iso = self._wall_timestamp()
        try:
            self._initialize_ground_truth_schema(active['database'])
            with sqlite3.connect(active['database'], timeout=5.0) as connection:
                connection.execute('PRAGMA busy_timeout=5000')
                eeg_end_ids = json.dumps(self._eeg_watermarks(connection))
                connection.execute(
                    'UPDATE workshop_sections SET ended_at=?, ended_at_iso=?, '
                    'closed_reason=?, eeg_end_ids=? WHERE ended_at IS NULL',
                    (timestamp, timestamp_iso, reason, eeg_end_ids),
                )
        except sqlite3.Error as error:
            active['metadata']['workshop_close_error'] = str(error)

    def _call_recording_service(self, enabled):
        active = self._require_active()
        if active['metadata'].get('backend') == 'standalone':
            self._call_standalone_recording_control(active, enabled)
            return
        self._call_ros_recording_service(enabled)

    @staticmethod
    def _write_control_request(path, enabled, request_id):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps({
                'request_id': request_id,
                'recording': bool(enabled),
                'timestamp': time.time(),
            }),
            encoding='utf-8',
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _call_standalone_recording_control(self, active, enabled):
        request_id = str(time.time_ns())
        control_path = active['control']
        ack_path = control_path.with_name('control_ack.json')
        self._write_control_request(control_path, enabled, request_id)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    'El colector standalone terminó inesperadamente'
                )
            try:
                acknowledgement = json.loads(
                    ack_path.read_text(encoding='utf-8')
                )
            except (
                FileNotFoundError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ):
                time.sleep(0.05)
                continue
            if acknowledgement.get('request_id') != request_id:
                time.sleep(0.05)
                continue
            if not acknowledgement.get('success'):
                raise RuntimeError(
                    'El colector no pudo cambiar la grabación: '
                    + str(acknowledgement.get('message', 'error desconocido'))
                )
            return
        raise RuntimeError(
            'El colector standalone no confirmó el cambio de grabación'
        )

    @staticmethod
    def _call_ros_recording_service(enabled):
        request = '{data: ' + ('true' if enabled else 'false') + '}'
        try:
            result = subprocess.run(
                [
                    'ros2', 'service', 'call',
                    '/central_database/set_recording',
                    'std_srvs/srv/SetBool',
                    request,
                ],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f'No se pudo controlar la grabación: {error}') from error
        output = result.stdout + result.stderr
        if result.returncode != 0 or 'success=True' not in output:
            raise RuntimeError(
                'El nodo de base de datos no confirmó el cambio de grabación: '
                + output.strip()[-300:]
            )

    def _unique_session_dir(self, base_name):
        candidate = self.sessions_root / base_name
        suffix = 2
        while candidate.exists():
            candidate = self.sessions_root / f'{base_name}_{suffix}'
            suffix += 1
        return candidate

    def _resolve_session(self, session_name):
        if not SESSION_NAME_PATTERN.fullmatch(session_name):
            raise ValueError('Nombre de sesión inválido')
        session_dir = (self.sessions_root / session_name).resolve()
        if session_dir.parent != self.sessions_root or not session_dir.is_dir():
            raise FileNotFoundError('Sesión no encontrada')
        return session_dir

    @staticmethod
    def _stop_process(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=15)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _write_metadata(session_dir, metadata):
        path = session_dir / 'metadata.json'
        temporary = session_dir / '.metadata.tmp'
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    @staticmethod
    def _read_metadata(session_dir):
        return json.loads(
            (session_dir / 'metadata.json').read_text(encoding='utf-8')
        )

    def _session_summary(self, session_dir):
        metadata = self._read_metadata(session_dir)
        database = session_dir / 'raw.sqlite'
        csv_exports = []
        label_to_profile = {
            definition['filename_label']: profile
            for profile, definition in EXPORT_PROFILES.items()
        }
        for path in sorted(session_dir.glob('export_operador_*.csv')):
            match = re.fullmatch(
                r'export_(operador_[a-z]+)_(muse|completo)\.csv',
                path.name,
            )
            if not path.is_file() or not match:
                continue
            csv_exports.append({
                'operator': match.group(1),
                'profile': label_to_profile[match.group(2)],
                'filename': path.name,
                'bytes': path.stat().st_size,
            })
        ground_truth_count = 0
        if database.is_file():
            try:
                uri = f'{database.as_uri()}?mode=ro'
                with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
                    if self._table_exists(connection, 'ground_truth_responses'):
                        ground_truth_count = connection.execute(
                            'SELECT COUNT(*) FROM ground_truth_responses'
                        ).fetchone()[0]
            except sqlite3.Error:
                pass
        return {
            **metadata,
            'database_exists': database.is_file(),
            'database_bytes': database.stat().st_size if database.is_file() else 0,
            'csv_exists': bool(csv_exports),
            'csv_exports': csv_exports,
            'ground_truth_responses': ground_truth_count,
        }

    @staticmethod
    def _table_exists(connection, table):
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None

    @staticmethod
    def _operator_status(path, max_bytes=2_000_000):
        if not path.is_file():
            return []
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes))
            lines = stream.read().decode('utf-8', errors='replace').splitlines()

        operators = {}
        for line in lines:
            match = STATUS_PATTERN.search(line)
            if not match:
                continue
            try:
                payload = json.loads(match.group(1))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            name = payload.get('operator')
            if not isinstance(name, str) or not OPERATOR_PATTERN.fullmatch(name):
                continue
            item = operators.setdefault(name, {
                'operator': name,
                'state': 'unknown',
                'connected_since': None,
                'battery_percent': None,
                'rates': {'eeg': 0.0, 'imu': 0.0, 'ppg': 0.0},
                'adapter': None,
                'mac': None,
                'disconnect_count': 0,
                'reconnect_count': 0,
            })
            state = payload.get('state')
            item['adapter'] = payload.get('adapter') or item['adapter']
            item['mac'] = payload.get('mac') or item['mac']
            if state == 'streaming':
                item['state'] = 'streaming'
                item['connected_since'] = payload.get('timestamp', time.time())
            elif state in {
                'connecting', 'connected', 'reconnecting',
                'waiting_for_device',
            }:
                item['state'] = state
                if state != 'connected':
                    item['connected_since'] = None
            elif state in {'disconnected', 'data_timeout', 'error'}:
                item['state'] = state
                item['connected_since'] = None
                if (
                    state in {'disconnected', 'data_timeout'} and
                    not isinstance(payload.get('disconnect_count'), int)
                ):
                    item['disconnect_count'] += 1
            elif state == 'metrics':
                if payload.get('streaming'):
                    item['state'] = 'streaming'
                    item['connected_since'] = payload.get('connected_since')
                rates = payload.get('published_hz') or payload.get('received_hz')
                if isinstance(rates, dict):
                    item['rates'] = {
                        key: float(rates.get(key, 0.0) or 0.0)
                        for key in SIGNALS
                    }
                battery = payload.get('battery_percent')
                if isinstance(battery, (int, float)) and battery > 0:
                    item['battery_percent'] = battery
            if isinstance(payload.get('disconnect_count'), int):
                item['disconnect_count'] = payload['disconnect_count']
            if isinstance(payload.get('reconnect_count'), int):
                item['reconnect_count'] = payload['reconnect_count']

        now = time.time()
        for item in operators.values():
            started = item['connected_since']
            item['connected_seconds'] = (
                max(0, int(now - started)) if started is not None else 0
            )
        return [operators[name] for name in sorted(operators)]

    @staticmethod
    def _tail(path, max_lines=24, max_bytes=32_768):
        if not path.is_file():
            return []
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max_bytes))
            text = stream.read().decode('utf-8', errors='replace')
        return text.splitlines()[-max_lines:]
