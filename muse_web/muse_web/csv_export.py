"""Excel-friendly, lossless CSV export for one Muse session."""

import csv
import json
import os
import sqlite3
from pathlib import Path


TABLES = (
    'recording_periods',
    'workshop_sections',
    'ground_truth_responses',
    'eeg_logs',
    'imu_logs',
    'ppg_logs',
)

SIGNAL_TABLES = ('eeg_logs', 'imu_logs', 'ppg_logs')
EXPORT_PROFILES = {
    'muse': {
        'tables': SIGNAL_TABLES,
        'include_metadata': False,
        'filename_label': 'muse',
    },
    'complete': {
        'tables': TABLES,
        'include_metadata': True,
        'filename_label': 'completo',
    },
}

OPERATOR_COLUMNS = {
    'workshop_sections': 'operator',
    'ground_truth_responses': 'operator',
    'eeg_logs': 'operador',
    'imu_logs': 'operador',
    'ppg_logs': 'operador',
}


def _table_exists(connection, table):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _csv_value(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return value


def session_operators(database_path):
    """Return every operator represented by signals, intervals or responses."""
    database_path = Path(database_path)
    if not database_path.is_file():
        raise FileNotFoundError(f'No existe la base de sesión: {database_path}')
    operators = set()
    with sqlite3.connect(database_path) as connection:
        for table, column in OPERATOR_COLUMNS.items():
            if not _table_exists(connection, table):
                continue
            columns = {
                row[1] for row in connection.execute(f'PRAGMA table_info({table})')
            }
            if column not in columns:
                continue
            operators.update(
                row[0] for row in connection.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                ) if row[0]
            )
    return sorted(operators)


def export_session_to_csv(
    database_path, output_path, metadata, operator, profile='complete'
):
    """Stream one rectangular CSV with a record type and superset schema.

    Only rows belonging to ``operator`` are included. Shared metadata and
    recording periods are copied into every operator file.
    """
    database_path = Path(database_path)
    output_path = Path(output_path)
    if profile not in EXPORT_PROFILES:
        raise ValueError(f'Perfil de exportación inválido: {profile}')
    if not database_path.is_file():
        raise FileNotFoundError(f'No existe la base de sesión: {database_path}')

    profile_definition = EXPORT_PROFILES[profile]
    temporary_path = output_path.with_name(f'.{output_path.name}.tmp')
    with sqlite3.connect(database_path) as connection:
        available = [
            table for table in profile_definition['tables']
            if _table_exists(connection, table)
        ]
        table_columns = {
            table: [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]
            for table in available
        }
        columns = []
        for table in available:
            for column in table_columns[table]:
                if column not in columns:
                    columns.append(column)
        metadata_fields = (
            ['metadata_key', 'metadata_value']
            if profile_definition['include_metadata'] else []
        )
        fieldnames = ['record_type', *metadata_fields, *columns]

        with temporary_path.open('w', encoding='utf-8-sig', newline='') as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            if profile_definition['include_metadata']:
                for key, value in metadata.items():
                    writer.writerow({
                        'record_type': 'metadata',
                        'metadata_key': key,
                        'metadata_value': _csv_value(value),
                    })
                writer.writerow({
                    'record_type': 'metadata',
                    'metadata_key': 'export_operator',
                    'metadata_value': operator,
                })
            for table in available:
                selected = ','.join(f'"{column}"' for column in table_columns[table])
                order = ' ORDER BY id' if 'id' in table_columns[table] else ''
                operator_column = OPERATOR_COLUMNS.get(table)
                where = ''
                parameters = ()
                if operator_column in table_columns[table]:
                    where = f' WHERE "{operator_column}"=?'
                    parameters = (operator,)
                connection.row_factory = sqlite3.Row
                for row in connection.execute(
                    f'SELECT {selected} FROM "{table}"{where}{order}',
                    parameters,
                ):
                    values = {key: _csv_value(row[key]) for key in row.keys()}
                    writer.writerow({'record_type': table, **values})
            output.flush()
            os.fsync(output.fileno())
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, output_path)


def export_session_csvs(
    database_path, output_directory, metadata, profiles=('muse', 'complete')
):
    """Create requested CSV profiles for every operator."""
    output_directory = Path(output_directory)
    paths = []
    for operator in session_operators(database_path):
        for profile in profiles:
            if profile not in EXPORT_PROFILES:
                raise ValueError(f'Perfil de exportación inválido: {profile}')
            label = EXPORT_PROFILES[profile]['filename_label']
            path = output_directory / f'export_{operator}_{label}.csv'
            export_session_to_csv(
                database_path, path, metadata, operator, profile
            )
            paths.append(path)
    return paths
