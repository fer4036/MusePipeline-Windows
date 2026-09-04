"""Streaming SQLite-to-text export for one Muse research session."""

import json
import os
import sqlite3
from pathlib import Path


SIGNAL_TABLES = (
    ('EEG', 'eeg_logs'),
    ('IMU', 'imu_logs'),
    ('PPG', 'ppg_logs'),
)

PROTOCOL_TABLES = (
    ('WORKSHOP_SECTIONS', 'workshop_sections'),
    ('GROUND_TRUTH_COGNITIVE_ENGAGEMENT', 'ground_truth_responses'),
)


def _text_value(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value.replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n')
    return str(value)


def _table_exists(connection, table):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def export_session_to_text(database_path, output_path, metadata):
    """Write an atomic, UTF-8, tab-separated text export."""
    database_path = Path(database_path)
    output_path = Path(output_path)
    if not database_path.is_file():
        raise FileNotFoundError(f'No existe la base de sesión: {database_path}')

    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = output_path.with_suffix('.tmp.txt')
    with sqlite3.connect(database_path) as connection, temporary_path.open(
        'w', encoding='utf-8', newline='\n'
    ) as output:
        output.write('# MUSE_RESEARCH_TEXT_V1\n')
        output.write('# Codificación: UTF-8; columnas separadas por tabulador\n')
        output.write('[METADATA]\n')
        output.write('campo\tvalor\n')
        for key, value in metadata.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            output.write(f'{_text_value(key)}\t{_text_value(value)}\n')

        if _table_exists(connection, 'recording_periods'):
            output.write('\n[RECORDING_PERIODS]\n')
            output.write('id\tstarted_at\tended_at\n')
            for row in connection.execute(
                'SELECT id, started_at, ended_at FROM recording_periods ORDER BY id'
            ):
                output.write('\t'.join(_text_value(value) for value in row) + '\n')

        for label, table in PROTOCOL_TABLES:
            if not _table_exists(connection, table):
                continue
            columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info({table})')
            ]
            column_sql = ', '.join(f'"{column}"' for column in columns)
            output.write(f'\n[{label}]\n')
            output.write('\t'.join(columns) + '\n')
            for row in connection.execute(
                f'SELECT {column_sql} FROM {table} ORDER BY id'
            ):
                output.write('\t'.join(_text_value(value) for value in row) + '\n')

        for label, table in SIGNAL_TABLES:
            if not _table_exists(connection, table):
                continue
            columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info({table})')
            ]
            column_sql = ', '.join(f'"{column}"' for column in columns)
            output.write(f'\n[{label}]\n')
            output.write('\t'.join(columns) + '\n')
            for row in connection.execute(
                f'SELECT {column_sql} FROM {table} ORDER BY id'
            ):
                output.write('\t'.join(_text_value(value) for value in row) + '\n')

    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, output_path)
    return output_path
