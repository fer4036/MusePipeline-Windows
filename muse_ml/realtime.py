"""Realtime cognitive engagement estimation from the active SQLite session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

import numpy as np

from muse_ml.features import EEG_CHANNELS, PPG_CHANNELS, eeg_band_features, ppg_features


LOW_LABEL = 'bajo'
MEDIUM_LABEL = 'medio'
HIGH_LABEL = 'alto'


@dataclass(frozen=True)
class RealtimeConfig:
    """Runtime parameters for cognitive state estimation."""

    model_path: str | None = None
    window_seconds: float = 60.0
    update_seconds: float = 30.0
    min_eeg_samples: int = 512
    min_ppg_samples: int = 300


class RealtimeCognitiveMonitor:
    """Compute the latest EEG/PPG feature window and optional SCEM prediction."""

    def __init__(self, config: RealtimeConfig | None = None):
        self.config = config or RealtimeConfig()
        self.model = None
        self.model_error = None
        self.feature_names: list[str] = []
        self._shap_explainer = None
        if self.config.model_path:
            self._load_model(Path(self.config.model_path))

    def snapshot(self, database_path, operators=None):
        """Return one public cognitive-state snapshot for the cloud GUI."""

        database = Path(database_path) if database_path else None
        base = {
            'enabled': True,
            'model_loaded': self.model is not None,
            'model_path': self.config.model_path,
            'model_error': self.model_error,
            'window_seconds': self.config.window_seconds,
            'update_seconds': self.config.update_seconds,
            'generated_at': time.time(),
            'operators': [],
        }
        if not database or not database.is_file():
            base['state'] = 'waiting_for_session'
            return base

        try:
            with sqlite3.connect(database, timeout=2.0) as connection:
                connection.row_factory = sqlite3.Row
                operator_ids = operators or self._operators(connection)
                if not operator_ids:
                    base['state'] = 'waiting_for_devices'
                    return base
                latest = self._latest_timestamp(connection)
                if latest is None:
                    base['state'] = 'waiting_for_data'
                    return base
                started_at = latest - self.config.window_seconds
                base['state'] = 'active'
                base['operators'] = [
                    self._operator_snapshot(
                        connection,
                        operator_id,
                        started_at,
                        latest,
                    )
                    for operator_id in operator_ids
                ]
                return base
        except Exception as error:
            base['state'] = 'error'
            base['error'] = str(error)
            return base

    def _load_model(self, model_path: Path):
        try:
            import joblib

            self.model = joblib.load(model_path)
            self.feature_names = self._infer_feature_names(self.model)
        except Exception as error:
            self.model = None
            self.model_error = str(error)

    def _operator_snapshot(self, connection, operator_id, started_at, ended_at):
        eeg = self._signal_rows(
            connection,
            'eeg_logs',
            operator_id,
            EEG_CHANNELS,
            started_at,
            ended_at,
        )
        ppg = self._signal_rows(
            connection,
            'ppg_logs',
            operator_id,
            PPG_CHANNELS,
            started_at,
            ended_at,
        )
        n_eeg = len(eeg['timestamp'])
        n_ppg = len(ppg['timestamp'])
        result = {
            'operator_id': operator_id,
            'state': 'waiting_for_data',
            'window_started_at': started_at,
            'window_ended_at': ended_at,
            'n_eeg_samples': int(n_eeg),
            'n_ppg_samples': int(n_ppg),
            'score': None,
            'level': None,
            'confidence': 0.0,
            'method': 'xgboost_scem_regression'
            if self.model is not None else 'model_not_configured',
            'top_factors': [],
        }
        if n_eeg < self.config.min_eeg_samples:
            result['message'] = 'Esperando una ventana EEG suficiente'
            return result

        features = {}
        eeg_values = {
            channel: eeg[channel]
            for channel in EEG_CHANNELS
        }
        features.update(eeg_band_features(eeg_values, eeg['timestamp']).features)
        if n_ppg >= self.config.min_ppg_samples:
            ppg_values = {
                channel: ppg[channel]
                for channel in PPG_CHANNELS
            }
            features.update(ppg_features(ppg_values, ppg['timestamp']).features)

        result['state'] = 'features_ready'
        result['features_used'] = len(features)
        result['confidence'] = self._confidence(n_eeg, n_ppg)
        if self.model is None:
            result['message'] = (
                'Configura --cognitive-model con un modelo entrenado para '
                'emitir SCEM estimado.'
            )
            result['feature_summary'] = self._feature_summary(features)
            return result

        prediction, frame = self._predict(features)
        result['state'] = 'estimated'
        result['score'] = prediction
        result['level'] = self._level(prediction)
        result['top_factors'] = self._top_factors(frame, features)
        return result

    def _predict(self, features):
        try:
            import pandas as pd
        except ImportError as error:
            raise RuntimeError('pandas es requerido para inferencia ML') from error

        names = self.feature_names or sorted(features)
        frame = pd.DataFrame([{
            name: float(features.get(name, np.nan))
            for name in names
        }])
        prediction = float(self.model.predict(frame)[0])
        return float(np.clip(prediction, 1.0, 5.0)), frame

    def _top_factors(self, frame, raw_features, limit=5):
        shap_factors = self._shap_factors(frame, limit=limit)
        if shap_factors:
            return shap_factors
        return self._importance_factors(frame, raw_features, limit=limit)

    def _shap_factors(self, frame, limit=5):
        model_steps = getattr(self.model, 'named_steps', {})
        estimator = model_steps.get('model')
        imputer = model_steps.get('imputer')
        if estimator is None or imputer is None:
            return []
        try:
            import shap

            if self._shap_explainer is None:
                self._shap_explainer = shap.TreeExplainer(estimator)
            transformed = imputer.transform(frame)
            values = self._shap_explainer.shap_values(transformed)
            row = np.asarray(values)[0]
        except Exception:
            return []
        return self._ranked_factors(frame.columns, row, frame.iloc[0], limit)

    def _importance_factors(self, frame, raw_features, limit=5):
        model_steps = getattr(self.model, 'named_steps', {})
        estimator = model_steps.get('model', self.model)
        importances = getattr(estimator, 'feature_importances_', None)
        if importances is None:
            coefficients = getattr(estimator, 'coef_', None)
            importances = coefficients
        if importances is None:
            return []
        row = frame.iloc[0]
        values = np.asarray(importances, dtype=float)[:len(frame.columns)]
        scores = values * np.asarray([
            raw_features.get(column, row[column])
            for column in frame.columns
        ], dtype=float)
        return self._ranked_factors(frame.columns, scores, row, limit)

    @staticmethod
    def _ranked_factors(columns, scores, row, limit):
        factors = []
        for index in np.argsort(np.abs(scores))[::-1][:limit]:
            score = scores[index]
            if not np.isfinite(score):
                continue
            factors.append({
                'feature': str(columns[index]),
                'contribution': float(score),
                'value': float(row.iloc[index]),
            })
        return factors

    @staticmethod
    def _infer_feature_names(model):
        steps = getattr(model, 'named_steps', {})
        imputer = steps.get('imputer')
        names = getattr(imputer, 'feature_names_in_', None)
        if names is None:
            names = getattr(model, 'feature_names_in_', None)
        return [str(name) for name in names] if names is not None else []

    @staticmethod
    def _feature_summary(features):
        names = (
            'eeg_mean_rel_theta',
            'eeg_mean_rel_alpha',
            'eeg_mean_rel_beta',
            'ppg_median_hr_mean',
            'ppg_median_rmssd',
        )
        return {
            name: float(features[name])
            for name in names
            if name in features and np.isfinite(features[name])
        }

    @staticmethod
    def _level(score):
        if score < 2.5:
            return LOW_LABEL
        if score < 3.75:
            return MEDIUM_LABEL
        return HIGH_LABEL

    def _confidence(self, n_eeg, n_ppg):
        eeg_ratio = min(1.0, n_eeg / max(1, self.config.min_eeg_samples))
        ppg_ratio = min(1.0, n_ppg / max(1, self.config.min_ppg_samples))
        return float(round(0.75 * eeg_ratio + 0.25 * ppg_ratio, 3))

    @staticmethod
    def _table_exists(connection, table):
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _latest_timestamp(self, connection):
        latest = []
        for table in ('eeg_logs', 'ppg_logs'):
            if self._table_exists(connection, table):
                value = connection.execute(
                    f'SELECT MAX(timestamp) FROM {table}'
                ).fetchone()[0]
                if value is not None:
                    latest.append(float(value))
        return max(latest) if latest else None

    def _operators(self, connection):
        values = set()
        for table in ('eeg_logs', 'ppg_logs'):
            if self._table_exists(connection, table):
                rows = connection.execute(
                    f'SELECT DISTINCT operador FROM {table} '
                    'WHERE operador IS NOT NULL ORDER BY operador'
                )
                values.update(str(row[0]) for row in rows if row[0])
        return sorted(values)

    def _signal_rows(
        self,
        connection,
        table,
        operator_id,
        channels,
        started_at,
        ended_at,
    ):
        output = {
            'timestamp': np.array([], dtype=float),
            **{channel: np.array([], dtype=float) for channel in channels},
        }
        if not self._table_exists(connection, table):
            return output
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        selected_channels = [channel for channel in channels if channel in columns]
        if not selected_channels:
            return output
        selected = ', '.join(['timestamp', *selected_channels])
        rows = connection.execute(
            f'SELECT {selected} FROM {table} '
            'WHERE operador = ? AND timestamp >= ? AND timestamp <= ? '
            'ORDER BY timestamp',
            (operator_id, started_at, ended_at),
        ).fetchall()
        if not rows:
            return output
        output['timestamp'] = np.asarray([row['timestamp'] for row in rows], dtype=float)
        for channel in selected_channels:
            output[channel] = np.asarray([row[channel] for row in rows], dtype=float)
        return output
