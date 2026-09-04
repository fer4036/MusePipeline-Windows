"""Tests for the Muse CSV exporter."""

import csv
import sqlite3

from muse_web.csv_export import export_session_csvs, export_session_to_csv


def test_exports_excel_friendly_rectangular_csv(tmp_path):
    database = tmp_path / 'raw.sqlite'
    output = tmp_path / 'export.csv'
    with sqlite3.connect(database) as connection:
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
        connection.execute(
            'INSERT INTO eeg_logs VALUES (2, ?, ?, ?)',
            ('operador_b', 100.6, 99.0),
        )
        connection.execute('''
            CREATE TABLE ground_truth_responses (
                id INTEGER PRIMARY KEY,
                operator TEXT,
                section_id TEXT,
                submitted_at REAL,
                engagement_score REAL
            )
        ''')
        connection.execute(
            'INSERT INTO ground_truth_responses VALUES (1, ?, ?, ?, ?)',
            ('operador_a', 'paso_1', 101.0, 4.25),
        )

    export_session_to_csv(
        database,
        output,
        {'subject_code': 'S-01', 'notes': 'línea\tnueva'},
        'operador_a',
    )

    assert output.read_bytes().startswith(b'\xef\xbb\xbf')
    with output.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert {row['record_type'] for row in rows} == {
        'metadata', 'eeg_logs', 'ground_truth_responses'
    }
    notes = next(row for row in rows if row['metadata_key'] == 'notes')
    assert notes['metadata_value'] == 'línea\tnueva'
    eeg = next(row for row in rows if row['record_type'] == 'eeg_logs')
    assert eeg['operador'] == 'operador_a'
    assert eeg['channel_1'] == '42.0'
    assert not any(row.get('operador') == 'operador_b' for row in rows)
    response = next(
        row for row in rows if row['record_type'] == 'ground_truth_responses'
    )
    assert response['operator'] == 'operador_a'
    assert response['engagement_score'] == '4.25'


def test_missing_database_is_rejected(tmp_path):
    try:
        export_session_to_csv(
            tmp_path / 'missing.sqlite', tmp_path / 'out.csv', {}, 'operador_a'
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('A missing session database must be rejected')


def test_creates_both_csv_profiles_per_operator(tmp_path):
    database = tmp_path / 'raw.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE eeg_logs '
            '(id INTEGER PRIMARY KEY, operador TEXT, timestamp REAL)'
        )
        connection.executemany(
            'INSERT INTO eeg_logs VALUES (?, ?, ?)',
            [(1, 'operador_a', 1.0), (2, 'operador_b', 2.0)],
        )
    paths = export_session_csvs(database, tmp_path, {'subject_code': 'S-01'})
    assert [path.name for path in paths] == [
        'export_operador_a_muse.csv',
        'export_operador_a_completo.csv',
        'export_operador_b_muse.csv',
        'export_operador_b_completo.csv',
    ]


def test_muse_profile_contains_only_signal_rows(tmp_path):
    database = tmp_path / 'raw.sqlite'
    output = tmp_path / 'muse.csv'
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE eeg_logs '
            '(id INTEGER PRIMARY KEY, operador TEXT, channel_1 REAL)'
        )
        connection.execute(
            'INSERT INTO eeg_logs VALUES (1, ?, ?)', ('operador_a', 10.0)
        )
        connection.execute(
            'CREATE TABLE ground_truth_responses '
            '(id INTEGER PRIMARY KEY, operator TEXT, engagement_score REAL)'
        )
        connection.execute(
            'INSERT INTO ground_truth_responses VALUES (1, ?, ?)',
            ('operador_a', 4.5),
        )

    export_session_to_csv(
        database, output, {'subject_code': 'S-01'}, 'operador_a', 'muse'
    )

    with output.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert 'metadata_key' not in reader.fieldnames
        assert 'engagement_score' not in reader.fieldnames
    assert {row['record_type'] for row in rows} == {'eeg_logs'}
