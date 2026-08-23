"""Tests for safe local session lifecycle management."""

import json
import sqlite3
import time

from muse_web.session_manager import SessionManager, safe_component


class FakeProcess:
    def __init__(self, command):
        self.command = command
        self.pid = 987654
        self.return_code = None

    def poll(self):
        return self.return_code


class FakePopen:
    def __init__(self):
        self.calls = []
        self.process = None

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.process = FakeProcess(command)
        return self.process


class FailingPopen:
    def __call__(self, _command, **_kwargs):
        raise FileNotFoundError('runtime inexistente')


def test_safe_component_removes_paths_and_personal_punctuation():
    assert safe_component('../../Sujeto Á-01', 'fallback') == 'sujeto-a-01'


def test_start_builds_fixed_ros_command_and_private_session(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)

    result = manager.start('S-01', 'Stroop base', 'hci1,hci2', 'sin novedad')

    command = fake_popen.calls[0][0]
    session = result['session']
    assert command[:4] == [
        'ros2', 'launch', 'muse_hrc', 'muse_system.launch.py'
    ]
    assert 'hci_devices:=hci1,hci2' in command
    assert any(item.startswith('database_path:=') for item in command)
    assert 'recording_enabled:=false' in command
    assert session['subject_code'] == 'S-01'
    assert (tmp_path / session['session_name'] / 'metadata.json').is_file()
    assert (tmp_path.stat().st_mode & 0o777) == 0o700

    fake_popen.process.return_code = 0
    stopped = manager.stop(export=False)
    assert stopped['status'] == 'completed'


def test_start_builds_standalone_command_and_private_control(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(
        tmp_path,
        popen_factory=fake_popen,
        standalone_python='/opt/muse/python',
    )

    result = manager.start(
        'S-02',
        'Baseline',
        'hci1,hci2',
        backend='standalone',
    )

    command = fake_popen.calls[0][0]
    session_dir = tmp_path / result['session']['session_name']
    assert command[:3] == [
        '/opt/muse/python', '-m', 'muse_hrc.standalone'
    ]
    assert command[command.index('--hci-devices') + 1] == 'hci1,hci2'
    assert '--paused' in command
    assert '--control-file' in command
    assert result['session']['backend'] == 'standalone'
    control = json.loads(
        (session_dir / 'control.json').read_text(encoding='utf-8')
    )
    assert control['recording'] is False

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_web_restart_reattaches_to_verified_standalone_collector(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(
        tmp_path,
        popen_factory=fake_popen,
        standalone_python='/opt/muse/python',
    )
    started = manager.start(
        'S-02', 'Baseline', 'hci1,hci2', backend='standalone'
    )
    command = fake_popen.calls[0][0]

    manager.shutdown()

    recovered = SessionManager(
        tmp_path,
        process_alive_checker=lambda pid: pid == 987654,
        process_command_reader=lambda pid: command if pid == 987654 else [],
    )
    status = recovered.status()

    assert status['running'] is True
    assert status['session']['session_name'] == started['session']['session_name']
    assert status['session']['web_reattach_count'] == 1
    assert recovered._process.pid == 987654


def test_recovery_rejects_reused_pid_with_another_command(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(
        tmp_path,
        popen_factory=fake_popen,
        standalone_python='/opt/muse/python',
    )
    manager.start('S-02', 'Baseline', 'hci1', backend='standalone')
    manager.shutdown()

    recovered = SessionManager(
        tmp_path,
        process_alive_checker=lambda _pid: True,
        process_command_reader=lambda _pid: ['/usr/bin/python', 'other.py'],
    )

    assert recovered.status()['running'] is False


def test_launch_failure_is_reported_as_a_controlled_error(tmp_path):
    manager = SessionManager(
        tmp_path,
        popen_factory=FailingPopen(),
        standalone_python='/ruta/inexistente/python',
    )

    try:
        manager.start(
            'S-04', 'Baseline', 'hci1', backend='standalone'
        )
    except RuntimeError as error:
        assert 'No se pudo iniciar el backend standalone' in str(error)
    else:
        raise AssertionError('El fallo de lanzamiento debe ser controlado')

    metadata_path = next(tmp_path.glob('*/metadata.json'))
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    assert metadata['status'] == 'launch_error'
    assert metadata['launch_error'] == 'runtime inexistente'


def test_standalone_recording_control_requires_matching_ack(
    tmp_path,
    monkeypatch,
):
    fake_popen = FakePopen()
    manager = SessionManager(
        tmp_path,
        popen_factory=fake_popen,
        standalone_python='/opt/muse/python',
    )
    result = manager.start(
        'S-03', 'Baseline', 'hci1', backend='standalone'
    )
    session_dir = tmp_path / result['session']['session_name']
    monkeypatch.setattr(time, 'time_ns', lambda: 123456)
    (session_dir / 'control_ack.json').write_text(json.dumps({
        'request_id': '123456',
        'success': True,
    }), encoding='utf-8')

    manager.start_recording()

    control = json.loads(
        (session_dir / 'control.json').read_text(encoding='utf-8')
    )
    assert control == {
        'request_id': '123456',
        'recording': True,
        'timestamp': control['timestamp'],
    }
    manager._call_recording_service = lambda _enabled: None
    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_invalid_adapter_input_never_reaches_subprocess(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)

    try:
        manager.start('S01', 'Test', 'hci1; touch /tmp/unsafe')
    except ValueError:
        pass
    else:
        raise AssertionError('Unsafe adapter input must be rejected')

    assert fake_popen.calls == []


def test_recording_intervals_do_not_stop_pipeline(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    manager.start('S-01', 'Stroop', 'hci1')
    calls = []
    manager._call_recording_service = calls.append

    started = manager.start_recording()
    stopped = manager.stop_recording()

    assert calls == [True, False]
    assert started['recording'] is True
    assert stopped['recording'] is False
    interval = stopped['session']['recording_intervals'][0]
    assert interval['started_at']
    assert interval['ended_at']
    assert fake_popen.process.poll() is None

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_preview_and_machine_readable_operator_status(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    status = manager.start('S-01', 'Stroop', 'hci1')
    session_dir = tmp_path / status['session']['session_name']
    with sqlite3.connect(session_dir / 'raw.sqlite') as connection:
        connection.execute('''
            CREATE TABLE eeg_logs (
                id INTEGER PRIMARY KEY,
                operador TEXT,
                timestamp REAL,
                channel_1 REAL
            )
        ''')
        connection.execute(
            'INSERT INTO eeg_logs VALUES (1, ?, ?, ?)',
            ('operador_a', 100.5, 42.0),
        )
    payloads = [
        {
            'state': 'streaming', 'operator': 'operador_a',
            'timestamp': 100.0, 'adapter': 'hci1',
        },
        {
            'state': 'metrics', 'operator': 'operador_a',
            'published_hz': {'eeg': 256, 'imu': 52, 'ppg': 64},
            'battery_percent': 73.5,
            'mac': '00:55:da:00:00:01',
            'disconnect_count': 2,
            'reconnect_count': 2,
        },
    ]
    with (session_dir / 'pipeline.log').open('a', encoding='utf-8') as output:
        for payload in payloads:
            output.write('MUSE_STATUS_JSON=' + json.dumps(payload) + '\n')

    current = manager.status()
    preview = manager.preview(limit=2)

    assert current['operators'][0]['state'] == 'streaming'
    assert current['operators'][0]['battery_percent'] == 73.5
    assert current['operators'][0]['rates']['ppg'] == 64.0
    assert current['operators'][0]['mac'] == '00:55:da:00:00:01'
    assert current['operators'][0]['disconnect_count'] == 2
    assert current['operators'][0]['reconnect_count'] == 2
    assert preview['tables']['eeg_logs']['count'] == 1
    assert preview['tables']['eeg_logs']['rows'][0]['channel_1'] == 42.0

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_operator_status_reports_waiting_for_powered_headband(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    status = manager.start('S-01', 'Reconnect', 'hci1')
    session_dir = tmp_path / status['session']['session_name']
    with (session_dir / 'pipeline.log').open('a', encoding='utf-8') as output:
        output.write('MUSE_STATUS_JSON=' + json.dumps({
            'state': 'waiting_for_device',
            'operator': 'operador_a',
            'timestamp': time.time(),
            'adapter': 'hci1',
            'mac': '00:55:da:00:00:01',
            'disconnect_count': 1,
        }) + '\n')

    operator = manager.status()['operators'][0]
    assert operator['state'] == 'waiting_for_device'
    assert operator['mac'] == '00:55:da:00:00:01'

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_workshop_ground_truth_uses_sensor_clock_and_fixed_order(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    status = manager.start('GRUPO-01', 'Pick and Place', 'hci1')
    session_dir = tmp_path / status['session']['session_name']
    with (session_dir / 'pipeline.log').open('a', encoding='utf-8') as output:
        output.write('MUSE_STATUS_JSON=' + json.dumps({
            'state': 'streaming',
            'operator': 'operador_a',
            'timestamp': time.time(),
            'adapter': 'hci1',
        }) + '\n')

    manager._call_recording_service = lambda _enabled: None
    manager.start_recording()
    interval = manager.start_workshop_section(
        'paso_1', 'operador_a'
    )['active_section']
    assert interval['started_at'] > 1_000_000_000

    try:
        manager.start_workshop_section('paso_2', 'operador_a')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Only one ordered workshop section may be active')

    with sqlite3.connect(session_dir / 'raw.sqlite') as connection:
        connection.execute(
            'UPDATE workshop_sections SET started_at=started_at-601 '
            'WHERE operator=? AND section_id=?',
            ('operador_a', 'paso_1'),
        )
    result = manager.submit_ground_truth(
        'operador_a',
        'paso_1',
        {'task_engagement': 5, 'effort': 4, 'persistence': 3, 'flow': 2},
    )
    assert result['engagement_score'] == 3.5
    assert result['effort_persistence_score'] == 3.5
    assert result['measurement_number'] == 1
    assert result['section_measurement_number'] == 1

    assert manager.workshop_status()['active_section'] is not None
    manager.finish_workshop_section('paso_1', 'operador_a')

    with sqlite3.connect(session_dir / 'raw.sqlite') as connection:
        row = connection.execute(
            'SELECT window_started_at, window_ended_at, submitted_at, '
            'operator, clock_source, section_measurement_number '
            'FROM ground_truth_responses'
        ).fetchone()
        closed_reason = connection.execute(
            'SELECT closed_reason FROM workshop_sections WHERE section_id=?',
            ('paso_1',),
        ).fetchone()[0]
    assert row[0] <= row[1] == row[2]
    assert row[3:] == ('operador_a', 'CLOCK_REALTIME_UNIX', 1)
    assert closed_reason == 'completed'
    assert manager.workshop_status()['active_section'] is None

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_workshop_requires_active_muse_recording(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    manager.start('GRUPO-01', 'Pick and Place', 'hci1')

    try:
        manager.start_workshop_section('paso_1', 'operador_a')
    except RuntimeError as error:
        assert 'grabación Muse' in str(error)
    else:
        raise AssertionError('Workshop intervals must overlap Muse recording')

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_ground_truth_uses_eeg_row_progress_not_broken_sensor_time(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    status = manager.start('GRUPO-01', 'Pick and Place', 'hci1')
    session_dir = tmp_path / status['session']['session_name']
    database = session_dir / 'raw.sqlite'
    with (session_dir / 'pipeline.log').open('a', encoding='utf-8') as output:
        output.write('MUSE_STATUS_JSON=' + json.dumps({
            'state': 'streaming',
            'operator': 'operador_a',
            'timestamp': time.time(),
            'adapter': 'hci1',
        }) + '\n')
    with sqlite3.connect(database) as connection:
        connection.execute('''
            CREATE TABLE eeg_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operador TEXT,
                timestamp REAL,
                channel_1 REAL
            )
        ''')
        connection.execute(
            'INSERT INTO eeg_logs (operador, timestamp, channel_1) '
            'VALUES (?, ?, ?)',
            ('operador_a', 1.0, 10.0),
        )

    manager._call_recording_service = lambda _enabled: None
    manager.start_recording()
    manager.start_workshop_section('paso_1', 'operador_a')
    with sqlite3.connect(database) as connection:
        connection.execute(
            'UPDATE workshop_sections SET started_at=started_at-601 '
            'WHERE operator=? AND section_id=?',
            ('operador_a', 'paso_1'),
        )
        connection.execute(
            'INSERT INTO eeg_logs (operador, timestamp, channel_1) '
            'VALUES (?, ?, ?)',
            ('operador_a', 1.0, 11.0),
        )

    result = manager.submit_ground_truth(
        'operador_a',
        'paso_1',
        {'task_engagement': 5, 'effort': 4, 'persistence': 3, 'flow': 2},
    )

    assert result['operator'] == 'operador_a'
    with sqlite3.connect(database) as connection:
        marks = connection.execute(
            'SELECT eeg_start_ids, eeg_end_ids FROM ground_truth_responses'
        ).fetchone()
    assert json.loads(marks[0]) == {'operador_a': 1}
    assert json.loads(marks[1]) == {'operador_a': 2}

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_operators_advance_workshop_independently(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    status = manager.start('GRUPO-02', 'Pick and Place', 'hci1,hci2')
    session_dir = tmp_path / status['session']['session_name']
    database = session_dir / 'raw.sqlite'
    with (session_dir / 'pipeline.log').open('a', encoding='utf-8') as output:
        for operator, adapter in (('operador_a', 'hci1'), ('operador_b', 'hci2')):
            output.write('MUSE_STATUS_JSON=' + json.dumps({
                'state': 'streaming', 'operator': operator,
                'timestamp': time.time(), 'adapter': adapter,
            }) + '\n')
    with sqlite3.connect(database) as connection:
        connection.execute('''
            CREATE TABLE eeg_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operador TEXT,
                timestamp REAL,
                channel_1 REAL
            )
        ''')

    manager._call_recording_service = lambda _enabled: None
    manager.start_recording()
    manager.start_workshop_section('paso_1', 'operador_a')
    manager.start_workshop_section('paso_1', 'operador_b')
    with sqlite3.connect(database) as connection:
        connection.execute(
            'UPDATE workshop_sections SET started_at=started_at-601'
        )
        connection.executemany(
            'INSERT INTO eeg_logs (operador, timestamp, channel_1) '
            'VALUES (?, ?, ?)',
            [
                ('operador_a', time.time(), 10.0),
                ('operador_b', time.time(), 20.0),
            ],
        )
    first_measurement = manager.submit_ground_truth(
        'operador_a', 'paso_1',
        {'task_engagement': 5, 'effort': 4, 'persistence': 3, 'flow': 2},
    )
    manager.finish_workshop_section('paso_1', 'operador_a')
    manager.start_workshop_section('paso_2', 'operador_a')

    intervals = manager.workshop_status()['intervals']
    active_a = next(
        row for row in intervals
        if row['operator'] == 'operador_a' and row['ended_at'] is None
    )
    assert active_a['next_assessment_due_at'] == (
        first_measurement['next_assessment_due_at']
    )
    assert any(
        row['operator'] == 'operador_a' and row['section_id'] == 'paso_2'
        and row['ended_at'] is None for row in intervals
    )
    assert any(
        row['operator'] == 'operador_b' and row['section_id'] == 'paso_1'
        and row['ended_at'] is None for row in intervals
    )

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_ground_truth_repeats_every_ten_minutes_within_same_section(tmp_path):
    fake_popen = FakePopen()
    manager = SessionManager(tmp_path, popen_factory=fake_popen)
    status = manager.start('GRUPO-03', 'Pick and Place', 'hci1')
    session_dir = tmp_path / status['session']['session_name']
    database = session_dir / 'raw.sqlite'
    with (session_dir / 'pipeline.log').open('a', encoding='utf-8') as output:
        output.write('MUSE_STATUS_JSON=' + json.dumps({
            'state': 'streaming', 'operator': 'operador_a',
            'timestamp': time.time(), 'adapter': 'hci1',
        }) + '\n')
    with sqlite3.connect(database) as connection:
        connection.execute('''
            CREATE TABLE eeg_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operador TEXT,
                timestamp REAL,
                channel_1 REAL
            )
        ''')

    manager._call_recording_service = lambda _enabled: None
    manager.start_recording()
    manager.start_workshop_section('paso_1', 'operador_a')
    scores = {
        'task_engagement': 5, 'effort': 4, 'persistence': 3, 'flow': 2,
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            'UPDATE workshop_sections SET started_at=started_at-601'
        )
        connection.execute(
            'INSERT INTO eeg_logs (operador, timestamp, channel_1) '
            'VALUES (?, ?, ?)', ('operador_a', time.time(), 10.0),
        )
    first = manager.submit_ground_truth('operador_a', 'paso_1', scores)

    with sqlite3.connect(database) as connection:
        connection.execute(
            'UPDATE ground_truth_responses SET submitted_at=submitted_at-601 '
            'WHERE id=1'
        )
        connection.execute(
            'INSERT INTO eeg_logs (operador, timestamp, channel_1) '
            'VALUES (?, ?, ?)', ('operador_a', time.time(), 11.0),
        )
    second = manager.submit_ground_truth('operador_a', 'paso_1', scores)

    assert first['measurement_number'] == 1
    assert first['section_measurement_number'] == 1
    assert second['measurement_number'] == 2
    assert second['section_measurement_number'] == 2
    status = manager.workshop_status()
    assert status['active_section']['section_id'] == 'paso_1'
    assert status['active_section']['assessment_count'] == 2
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            'SELECT section_id, section_interval_id, measurement_number, '
            'section_measurement_number FROM ground_truth_responses ORDER BY id'
        ).fetchall()
    assert rows[0][0:2] == rows[1][0:2]
    assert [row[2:] for row in rows] == [(1, 1), (2, 2)]

    fake_popen.process.return_code = 0
    manager.stop(export=False)


def test_ground_truth_schema_migrates_existing_single_response_rows(tmp_path):
    database = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('''
            CREATE TABLE ground_truth_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                practice_id TEXT NOT NULL,
                section_id TEXT NOT NULL,
                section_title TEXT NOT NULL,
                operator TEXT NOT NULL,
                section_started_at REAL NOT NULL,
                section_ended_at REAL NOT NULL,
                submitted_at REAL NOT NULL,
                submitted_at_iso TEXT NOT NULL,
                task_engagement INTEGER NOT NULL,
                effort INTEGER NOT NULL,
                persistence INTEGER NOT NULL,
                flow INTEGER NOT NULL,
                engagement_score REAL NOT NULL,
                effort_persistence_score REAL NOT NULL,
                clock_source TEXT NOT NULL,
                instrument_doi TEXT NOT NULL,
                UNIQUE(operator, section_id)
            )
        ''')
        connection.execute(
            'INSERT INTO ground_truth_responses '
            '(practice_id, section_id, section_title, operator, '
            'section_started_at, section_ended_at, submitted_at, '
            'submitted_at_iso, task_engagement, effort, persistence, flow, '
            'engagement_score, effort_persistence_score, clock_source, '
            'instrument_doi) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
            '?, ?, ?)',
            (
                'pick-and-place-physical', 'paso_1', 'Paso histórico',
                'operador_a', 100.0, 700.0, 701.0, '1970-01-01T00:11:41Z',
                5, 4, 3, 2, 3.5, 3.5, 'CLOCK_REALTIME_UNIX', 'doi',
            ),
        )

    SessionManager._initialize_ground_truth_schema(database)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute(
                'PRAGMA table_info(ground_truth_responses)'
            )
        }
        row = connection.execute(
            'SELECT measurement_number, section_measurement_number, due_at, '
            'window_started_at, window_ended_at FROM ground_truth_responses'
        ).fetchone()
    assert {
        'section_interval_id', 'measurement_number',
        'section_measurement_number', 'due_at', 'window_started_at',
        'window_ended_at', 'eeg_start_ids', 'eeg_end_ids',
    }.issubset(columns)
    assert row == (1, 1, 701.0, 100.0, 700.0)
