"""Build ML-ready windows, labels, and features from Muse CSV exports."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from muse_ml.features import EEG_CHANNELS, PPG_CHANNELS, eeg_band_features, ppg_features


SIGNAL_COLUMNS = (
    'record_type', 'operador', 'timestamp',
    *PPG_CHANNELS,
)
LABEL_ITEMS = ('task_engagement', 'effort', 'persistence', 'flow')


@dataclass(frozen=True)
class GroundTruth:
    response_id: str
    subject_code: str
    session_name: str
    experiment: str
    operator: str
    section_id: str
    measurement_number: int
    section_measurement_number: int
    window_started_at: float
    window_ended_at: float
    task_engagement: int
    effort: int
    persistence: int
    flow: int
    stored_score: float
    scem_score: float


def _safe_float(value, default=math.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def read_metadata_and_ground_truth(path: Path) -> tuple[dict[str, str], list[GroundTruth]]:
    """Read metadata and SCEM rows without parsing all signal samples."""

    metadata: dict[str, str] = {}
    pending_rows: list[dict[str, str]] = []
    with path.open(newline='', encoding='utf-8-sig', errors='replace') as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            record_type = row.get('record_type', '')
            if record_type == 'metadata':
                key = row.get('metadata_key')
                if key:
                    metadata[key] = row.get('metadata_value', '')
            elif record_type == 'ground_truth_responses':
                pending_rows.append(row)
            elif record_type in {'eeg_logs', 'imu_logs', 'ppg_logs'}:
                break

    subject = metadata.get('subject_code') or path.stem
    session = metadata.get('session_name') or path.stem
    experiment = metadata.get('experiment', '')
    results: list[GroundTruth] = []
    for row in pending_rows:
        values = {
            name: _safe_int(row.get(name), default=0)
            for name in LABEL_ITEMS
        }
        if any(value < 1 or value > 5 for value in values.values()):
            continue
        scem_score = sum(values.values()) / 4.0
        measurement_number = _safe_int(row.get('measurement_number'))
        operator = row.get('operator') or row.get('operador') or 'operador_a'
        section_id = row.get('section_id', '')
        results.append(GroundTruth(
            response_id=f'{session}:{operator}:{measurement_number}',
            subject_code=subject.strip(),
            session_name=session.strip(),
            experiment=experiment.strip(),
            operator=operator,
            section_id=section_id,
            measurement_number=measurement_number,
            section_measurement_number=_safe_int(
                row.get('section_measurement_number')
            ),
            window_started_at=_safe_float(row.get('window_started_at')),
            window_ended_at=_safe_float(row.get('window_ended_at')),
            task_engagement=values['task_engagement'],
            effort=values['effort'],
            persistence=values['persistence'],
            flow=values['flow'],
            stored_score=_safe_float(row.get('engagement_score')),
            scem_score=scem_score,
        ))
    return metadata, results


def _load_signal(path: Path, record_type: str, operator: str) -> pd.DataFrame:
    chunks = []
    usecols = [column for column in SIGNAL_COLUMNS]
    for chunk in pd.read_csv(
        path,
        usecols=lambda column: column in usecols,
        chunksize=200_000,
        low_memory=False,
        encoding='utf-8-sig',
    ):
        chunk = chunk[
            (chunk['record_type'] == record_type) &
            (chunk['operador'] == operator)
        ].copy()
        if chunk.empty:
            continue
        chunk['timestamp'] = pd.to_numeric(chunk['timestamp'], errors='coerce')
        chunk = chunk.dropna(subset=['timestamp'])
        for column in PPG_CHANNELS:
            if column in chunk.columns:
                chunk[column] = pd.to_numeric(chunk[column], errors='coerce')
        chunks.append(chunk)
    if not chunks:
        return pd.DataFrame(columns=usecols)
    data = pd.concat(chunks, ignore_index=True)
    return data.sort_values('timestamp')


def _window_ranges(start: float, end: float, size: float, stride: float):
    current = float(start)
    while current + size <= end + 1e-9:
        yield current, current + size
        current += stride


def _slice_window(frame: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[(frame['timestamp'] >= start) & (frame['timestamp'] < end)]


def _feature_rows(window_id: str, features: dict[str, float]):
    for name, value in sorted(features.items()):
        if value is None or not np.isfinite(value):
            continue
        parts = name.split('_')
        modality = parts[0] if parts else ''
        channel = next((part for part in parts if part.startswith('channel')), '')
        band = next((
            part for part in parts
            if part in {'delta', 'theta', 'alpha', 'beta', 'gamma', 'low'}
        ), '')
        yield {
            'window_id': window_id,
            'feature_name': name,
            'feature_value': float(value),
            'modality': modality,
            'channel': channel,
            'band': band,
            'feature_group': '_'.join(parts[:3]) if len(parts) >= 3 else name,
        }


def build_dataset(
    input_dir: Path,
    output_dir: Path,
    window_seconds: float = 60.0,
    stride_seconds: float = 30.0,
    min_eeg_samples: int = 1024,
    min_ppg_samples: int = 600,
) -> dict[str, int]:
    """Extract ML windows/features/labels from all complete CSV files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    windows_path = output_dir / 'ml_windows.csv'
    labels_path = output_dir / 'ml_labels.csv'
    features_path = output_dir / 'ml_features.csv'
    manifest_path = output_dir / 'manifest.json'

    windows_count = labels_count = features_count = 0
    files = sorted(input_dir.glob('*.csv'))
    with (
        windows_path.open('w', newline='', encoding='utf-8') as windows_stream,
        labels_path.open('w', newline='', encoding='utf-8') as labels_stream,
        features_path.open('w', newline='', encoding='utf-8') as features_stream,
    ):
        windows_writer = csv.DictWriter(windows_stream, fieldnames=[
            'window_id', 'source_file', 'subject_code', 'session_name',
            'experiment', 'operator', 'section_id', 'response_id',
            'measurement_number', 'start_time', 'end_time', 'window_size_s',
            'stride_s', 'n_eeg_samples', 'n_ppg_samples', 'valid_eeg',
            'valid_ppg',
        ])
        labels_writer = csv.DictWriter(labels_stream, fieldnames=[
            'window_id', 'response_id', 'task_engagement', 'effort',
            'persistence', 'flow', 'scem_score_recomputed',
            'scem_score_stored', 'high_engagement',
        ])
        features_writer = csv.DictWriter(features_stream, fieldnames=[
            'window_id', 'feature_name', 'feature_value', 'modality',
            'channel', 'band', 'feature_group',
        ])
        windows_writer.writeheader()
        labels_writer.writeheader()
        features_writer.writeheader()

        for source_file in files:
            _metadata, ground_truth = read_metadata_and_ground_truth(source_file)
            if not ground_truth:
                continue
            operators = sorted({item.operator for item in ground_truth})
            for operator in operators:
                eeg = _load_signal(source_file, 'eeg_logs', operator)
                ppg = _load_signal(source_file, 'ppg_logs', operator)
                for response in [item for item in ground_truth if item.operator == operator]:
                    for index, (start, end) in enumerate(_window_ranges(
                        response.window_started_at,
                        response.window_ended_at,
                        window_seconds,
                        stride_seconds,
                    ), start=1):
                        eeg_window = _slice_window(eeg, start, end)
                        ppg_window = _slice_window(ppg, start, end)
                        valid_eeg = len(eeg_window) >= min_eeg_samples
                        valid_ppg = len(ppg_window) >= min_ppg_samples
                        if not valid_eeg and not valid_ppg:
                            continue
                        window_id = (
                            f'{response.session_name}:{response.operator}:'
                            f'{response.measurement_number}:{index}'
                        )
                        feature_values = {}
                        if valid_eeg:
                            eeg_values = {
                                channel: eeg_window[channel].to_numpy(dtype=float)
                                for channel in EEG_CHANNELS
                            }
                            feature_values.update(eeg_band_features(
                                eeg_values,
                                eeg_window['timestamp'].to_numpy(dtype=float),
                            ).features)
                        if valid_ppg:
                            ppg_values = {
                                channel: ppg_window[channel].to_numpy(dtype=float)
                                for channel in PPG_CHANNELS
                            }
                            feature_values.update(ppg_features(
                                ppg_values,
                                ppg_window['timestamp'].to_numpy(dtype=float),
                            ).features)
                        windows_writer.writerow({
                            'window_id': window_id,
                            'source_file': source_file.name,
                            'subject_code': response.subject_code,
                            'session_name': response.session_name,
                            'experiment': response.experiment,
                            'operator': response.operator,
                            'section_id': response.section_id,
                            'response_id': response.response_id,
                            'measurement_number': response.measurement_number,
                            'start_time': start,
                            'end_time': end,
                            'window_size_s': window_seconds,
                            'stride_s': stride_seconds,
                            'n_eeg_samples': len(eeg_window),
                            'n_ppg_samples': len(ppg_window),
                            'valid_eeg': int(valid_eeg),
                            'valid_ppg': int(valid_ppg),
                        })
                        labels_writer.writerow({
                            'window_id': window_id,
                            'response_id': response.response_id,
                            'task_engagement': response.task_engagement,
                            'effort': response.effort,
                            'persistence': response.persistence,
                            'flow': response.flow,
                            'scem_score_recomputed': response.scem_score,
                            'scem_score_stored': response.stored_score,
                            'high_engagement': int(response.scem_score >= 4.0),
                        })
                        for row in _feature_rows(window_id, feature_values):
                            features_writer.writerow(row)
                            features_count += 1
                        windows_count += 1
                        labels_count += 1

    manifest = {
        'input_dir': str(input_dir),
        'window_seconds': window_seconds,
        'stride_seconds': stride_seconds,
        'files': [path.name for path in files],
        'windows': windows_count,
        'labels': labels_count,
        'features': features_count,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Extract ML-ready Muse EEG/PPG features from exported CSVs.',
    )
    parser.add_argument('--input-dir', default='subject_data')
    parser.add_argument('--output-dir', default='ml_output')
    parser.add_argument('--window-seconds', type=float, default=60.0)
    parser.add_argument('--stride-seconds', type=float, default=30.0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    manifest = build_dataset(
        Path(args.input_dir),
        Path(args.output_dir),
        window_seconds=args.window_seconds,
        stride_seconds=args.stride_seconds,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
