"""Run model-selection experiments for SCEM generalization studies."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.base import clone
from sklearn.feature_selection import SelectKBest, f_regression
from sklearn.impute import SimpleImputer
from sklearn.linear_model import BayesianRidge, LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from muse_ml.train import TARGET_COLUMN, _feature_allowed


HIGH_THRESHOLD = 4.0


@dataclass(frozen=True)
class ExperimentConfig:
    feature_set: str
    target_type: str
    model: str
    feature_k: int | None = None
    smoothing: str = 'none'
    calibration: str = 'none'
    physiological_baseline: str = 'none'

    @property
    def name(self):
        k = self.feature_k if self.feature_k is not None else 'all'
        return (
            f'{self.feature_set}|{self.target_type}|{self.model}|k={k}|'
            f'{self.smoothing}|{self.calibration}|{self.physiological_baseline}'
        )


def load_experiment_dataset(input_dir: Path, feature_set='robust', min_feature_coverage=0.6):
    features = pd.read_csv(input_dir / 'ml_features.csv')
    labels = pd.read_csv(input_dir / 'ml_labels.csv')
    windows = pd.read_csv(input_dir / 'ml_windows.csv')
    matrix = features.pivot_table(
        index='window_id',
        columns='feature_name',
        values='feature_value',
        aggfunc='mean',
    )
    frame = (
        matrix
        .join(labels.set_index('window_id'), how='inner')
        .join(windows.set_index('window_id'), how='inner', rsuffix='_window')
    )
    frame = frame.sort_values(['subject_code', 'session_name', 'start_time'])
    all_features = [column for column in matrix.columns if column in frame.columns]
    coverage = frame[all_features].notna().mean()
    feature_columns = [
        column for column in all_features
        if _feature_allowed(column, feature_set) and
        coverage[column] >= min_feature_coverage
    ]
    if not feature_columns:
        raise ValueError(f'No features selected for {feature_set}')
    response_counts = frame['response_id'].value_counts().to_dict()
    temporal_weight = frame.get(
        'temporal_label_weight',
        pd.Series(1.0, index=frame.index),
    )
    temporal_weight = pd.to_numeric(temporal_weight, errors='coerce').fillna(1.0)
    frame['sample_weight'] = frame['response_id'].map(
        lambda response_id: 1.0 / response_counts[response_id]
    ).astype(float) * temporal_weight.astype(float)
    frame['high_engagement_label'] = (
        frame[TARGET_COLUMN].astype(float) >= HIGH_THRESHOLD
    ).astype(int)
    frame['subject_mean_scem'] = frame.groupby('subject_code')[TARGET_COLUMN].transform('mean')
    frame['relative_scem'] = frame[TARGET_COLUMN] - frame['subject_mean_scem']
    return frame.reset_index(), feature_columns


def default_configs():
    return [
        ExperimentConfig('robust', 'absolute_scem', 'bayesian_ridge', 20),
        ExperimentConfig('robust', 'absolute_scem', 'bayesian_ridge', 50),
        ExperimentConfig('eeg_robust', 'absolute_scem', 'bayesian_ridge', 20),
        ExperimentConfig('ppg_robust', 'absolute_scem', 'bayesian_ridge', 20),
        ExperimentConfig('robust', 'relative_scem', 'bayesian_ridge', 20, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('robust', 'relative_scem', 'bayesian_ridge', 50, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('eeg_robust', 'relative_scem', 'bayesian_ridge', 20, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('ppg_robust', 'relative_scem', 'bayesian_ridge', 20, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('robust', 'absolute_scem', 'xgboost', 20),
        ExperimentConfig('robust', 'relative_scem', 'xgboost', 20, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('robust', 'high_binary', 'logistic', 20, 'rolling3', 'none', 'paso_1'),
        ExperimentConfig('robust', 'high_binary', 'logistic', 20, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('eeg_robust', 'rank_relative', 'ridge', 20, 'rolling3', 'initial_subject', 'paso_1'),
        ExperimentConfig('robust', 'multi_output_items', 'ridge', None, 'rolling3', 'initial_subject', 'paso_1'),
    ]


def _model(name, feature_k=None, random_state=42, classification=False):
    steps = [
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', RobustScaler()),
    ]
    if feature_k is not None:
        steps.append(('select', SelectKBest(f_regression, k=feature_k)))
    if classification:
        steps.append(('model', LogisticRegression(
            C=0.35,
            class_weight='balanced',
            max_iter=3000,
            random_state=random_state,
        )))
    elif name == 'bayesian_ridge':
        steps.append(('model', BayesianRidge()))
    elif name == 'ridge':
        steps.append(('model', Ridge(alpha=10.0)))
    elif name == 'xgboost':
        try:
            from xgboost import XGBRegressor
        except ImportError as error:
            raise RuntimeError('xgboost is required for this config') from error
        steps.append(('model', XGBRegressor(
            objective='reg:squarederror',
            n_estimators=120,
            max_depth=2,
            learning_rate=0.025,
            subsample=0.75,
            colsample_bytree=0.65,
            min_child_weight=8,
            reg_alpha=0.7,
            reg_lambda=10.0,
            random_state=random_state,
            n_jobs=1,
        )))
    else:
        raise ValueError(f'Modelo desconocido: {name}')
    return Pipeline(steps)


def _spearman(y_true, y_pred):
    if len(np.unique(y_true)) < 2 or len(np.unique(y_pred)) < 2:
        return np.nan
    value = stats.spearmanr(y_true, y_pred).correlation
    return float(value) if np.isfinite(value) else np.nan


def _regression_metrics(y_true, y_pred):
    mse = mean_squared_error(y_true, y_pred)
    return {
        'MAE': float(mean_absolute_error(y_true, y_pred)),
        'RMSE': float(np.sqrt(mse)),
        'R2': float(r2_score(y_true, y_pred)) if len(y_true) > 1 else np.nan,
        'Spearman': _spearman(y_true, y_pred),
    }


def _classification_metrics(y_true, y_pred):
    return {
        'balanced_accuracy': float(balanced_accuracy_score(y_true, y_pred)),
        'macro_f1': float(f1_score(y_true, y_pred, average='macro')),
        'accuracy': float(accuracy_score(y_true, y_pred)),
    }


def _apply_paso1_baseline(frame, feature_columns):
    x = frame[feature_columns].copy()
    baseline = (
        frame.loc[frame['section_id'] == 'paso_1']
        .groupby('subject_code')[feature_columns]
        .mean()
    )
    fallback = frame.groupby('subject_code')[feature_columns].mean()
    rows = []
    for _index, row in frame.iterrows():
        subject = row['subject_code']
        center = baseline.loc[subject] if subject in baseline.index else fallback.loc[subject]
        rows.append(row[feature_columns].astype(float) - center.astype(float))
    return pd.DataFrame(rows, columns=feature_columns, index=frame.index)


def _smooth_predictions(metadata, predictions):
    frame = metadata[['subject_code', 'response_id', 'start_time']].copy()
    frame['prediction'] = predictions
    frame = frame.sort_values(['subject_code', 'response_id', 'start_time'])
    smoothed = (
        frame.groupby(['subject_code', 'response_id'])['prediction']
        .transform(lambda values: values.rolling(3, min_periods=1).mean())
    )
    output = pd.Series(index=frame.index, data=smoothed.to_numpy())
    return output.sort_index().to_numpy()


def _calibration_mask(metadata):
    first_response = (
        metadata.sort_values(['subject_code', 'start_time'])
        .groupby('subject_code')['response_id']
        .first()
    )
    return metadata.apply(
        lambda row: row['response_id'] == first_response[row['subject_code']],
        axis=1,
    ).to_numpy(dtype=bool)


def _target_values(frame, config, train_mask):
    if config.target_type == 'absolute_scem':
        return frame[TARGET_COLUMN].to_numpy(dtype=float)
    if config.target_type == 'relative_scem':
        train_subject_means = frame.loc[train_mask].groupby('subject_code')[TARGET_COLUMN].mean()
        output = frame[TARGET_COLUMN] - frame['subject_code'].map(train_subject_means)
        fallback = float(frame.loc[train_mask, TARGET_COLUMN].mean())
        output = output.fillna(frame[TARGET_COLUMN] - fallback)
        return output.to_numpy(dtype=float)
    if config.target_type == 'rank_relative':
        ranked = frame.groupby('subject_code')[TARGET_COLUMN].rank(pct=True)
        return ranked.to_numpy(dtype=float)
    if config.target_type == 'multi_output_items':
        return frame[['task_engagement', 'effort', 'persistence', 'flow']].to_numpy(dtype=float)
    raise ValueError(f'Target desconocido: {config.target_type}')


def _reconstruct_scem(frame, config, raw_pred, train_mask, test_mask):
    if config.target_type == 'absolute_scem':
        return raw_pred
    if config.target_type == 'rank_relative':
        train_scores = frame.loc[train_mask, TARGET_COLUMN].to_numpy(dtype=float)
        reconstructed = np.quantile(train_scores, np.clip(raw_pred, 0.0, 1.0))
    elif config.target_type == 'multi_output_items':
        reconstructed = np.asarray(raw_pred, dtype=float).mean(axis=1)
    else:
        reconstructed = np.asarray(raw_pred, dtype=float)

    if config.calibration == 'initial_subject':
        calibration = frame.loc[test_mask & _calibration_mask(frame), TARGET_COLUMN]
        anchor = float(calibration.mean()) if not calibration.empty else float(frame.loc[train_mask, TARGET_COLUMN].mean())
    else:
        anchor = float(frame.loc[train_mask, TARGET_COLUMN].mean())
    if config.target_type == 'relative_scem':
        reconstructed = reconstructed + anchor
    return np.clip(reconstructed, 1.0, 5.0)


def _fit_predict_fold(frame, feature_columns, config, train_mask, test_mask, random_state=42):
    x = frame[feature_columns].copy()
    if config.physiological_baseline == 'paso_1':
        x = _apply_paso1_baseline(frame, feature_columns)
    x_train = x.loc[train_mask]
    x_test = x.loc[test_mask]
    weights = frame.loc[train_mask, 'sample_weight'].to_numpy(dtype=float)
    if config.target_type == 'high_binary':
        y = frame['high_engagement_label'].to_numpy(dtype=int)
        estimator = _model(config.model, config.feature_k, random_state, classification=True)
        if len(set(y[train_mask])) < 2:
            majority = int(round(np.average(y[train_mask], weights=weights)))
            pred = np.full(int(test_mask.sum()), majority, dtype=int)
            score = pred.astype(float)
        else:
            estimator.fit(x_train, y[train_mask], model__sample_weight=weights)
            pred = estimator.predict(x_test)
            score = estimator.predict_proba(x_test)[:, 1]
        if config.smoothing == 'rolling3':
            score = _smooth_predictions(frame.loc[test_mask], score)
            pred = (score >= 0.5).astype(int)
        return y[test_mask], pred, score

    y = _target_values(frame, config, train_mask)
    estimator = _model(config.model, config.feature_k, random_state)
    fit_y = y[train_mask]
    if fit_y.ndim == 1:
        estimator.fit(x_train, fit_y, model__sample_weight=weights)
    else:
        estimator.fit(x_train, fit_y)
    raw_pred = estimator.predict(x_test)
    pred = _reconstruct_scem(frame, config, raw_pred, train_mask, test_mask)
    if config.smoothing == 'rolling3':
        pred = _smooth_predictions(frame.loc[test_mask], pred)
    return frame.loc[test_mask, TARGET_COLUMN].to_numpy(dtype=float), pred, pred


def evaluate_config(frame, feature_columns, config, random_state=42, heldout_subjects=None):
    subjects = sorted(frame['subject_code'].astype(str).unique())
    if heldout_subjects is not None:
        subjects = [subject for subject in subjects if subject not in set(heldout_subjects)]
    rows = []
    predictions = []
    for subject in subjects:
        test_mask = (frame['subject_code'].astype(str) == subject).to_numpy()
        train_mask = ~test_mask
        if heldout_subjects is not None:
            train_mask &= ~frame['subject_code'].astype(str).isin(heldout_subjects).to_numpy()
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue
        y_true, y_pred, score = _fit_predict_fold(
            frame,
            feature_columns,
            config,
            train_mask,
            test_mask,
            random_state=random_state,
        )
        eval_mask = np.ones(len(y_true), dtype=bool)
        if config.calibration == 'initial_subject':
            eval_mask = ~_calibration_mask(frame.loc[test_mask].copy())
        if not eval_mask.any():
            continue
        if config.target_type == 'high_binary':
            metrics = _classification_metrics(y_true[eval_mask], y_pred[eval_mask])
            metrics.update({'MAE': np.nan, 'RMSE': np.nan, 'R2': np.nan, 'Spearman': np.nan})
        else:
            metrics = _regression_metrics(y_true[eval_mask], y_pred[eval_mask])
            metrics.update({'balanced_accuracy': np.nan, 'macro_f1': np.nan, 'accuracy': np.nan})
        metrics.update({
            'fold_subject': subject,
            'n': int(eval_mask.sum()),
        })
        rows.append(metrics)
        test_meta = frame.loc[test_mask].reset_index(drop=True)
        for row_index, keep in enumerate(eval_mask):
            if not keep:
                continue
            predictions.append({
                'experiment': config.name,
                'fold_subject': subject,
                'window_id': test_meta.loc[row_index, 'window_id'],
                'subject_code': test_meta.loc[row_index, 'subject_code'],
                'section_id': test_meta.loc[row_index, 'section_id'],
                'response_id': test_meta.loc[row_index, 'response_id'],
                'y_true': float(y_true[row_index]),
                'y_pred': float(y_pred[row_index]),
                'score': float(score[row_index]),
            })
    if not rows:
        return None, []
    if config.target_type == 'high_binary':
        all_true = np.asarray([row['y_true'] for row in predictions], dtype=int)
        all_pred = np.asarray([row['y_pred'] for row in predictions], dtype=int)
        overall = _classification_metrics(all_true, all_pred)
        overall.update({'MAE': np.nan, 'RMSE': np.nan, 'R2': np.nan, 'Spearman': np.nan})
    else:
        all_true = np.asarray([row['y_true'] for row in predictions], dtype=float)
        all_pred = np.asarray([row['y_pred'] for row in predictions], dtype=float)
        overall = _regression_metrics(all_true, all_pred)
        high_true = (all_true >= HIGH_THRESHOLD).astype(int)
        high_pred = (all_pred >= HIGH_THRESHOLD).astype(int)
        overall.update(_classification_metrics(high_true, high_pred))
    overall.update({
        'experiment': config.name,
        'feature_set': config.feature_set,
        'target_type': config.target_type,
        'model': config.model,
        'feature_k': config.feature_k if config.feature_k is not None else 'all',
        'smoothing': config.smoothing,
        'calibration': config.calibration,
        'physiological_baseline': config.physiological_baseline,
        'n': len(predictions),
    })
    return overall, predictions


def baseline_train_mean(frame):
    subjects = sorted(frame['subject_code'].astype(str).unique())
    predictions = []
    for subject in subjects:
        test_mask = (frame['subject_code'].astype(str) == subject).to_numpy()
        train_mask = ~test_mask
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue
        weights = frame.loc[train_mask, 'sample_weight'].to_numpy(dtype=float)
        prediction = float(np.average(frame.loc[train_mask, TARGET_COLUMN], weights=weights))
        for row in frame.loc[test_mask].to_dict('records'):
            predictions.append({
                'experiment': 'baseline_train_mean',
                'fold_subject': subject,
                'window_id': row['window_id'],
                'subject_code': row['subject_code'],
                'section_id': row['section_id'],
                'response_id': row['response_id'],
                'y_true': float(row[TARGET_COLUMN]),
                'y_pred': prediction,
                'score': prediction,
            })
    y_true = np.asarray([row['y_true'] for row in predictions], dtype=float)
    y_pred = np.asarray([row['y_pred'] for row in predictions], dtype=float)
    metrics = _regression_metrics(y_true, y_pred)
    high_true = (y_true >= HIGH_THRESHOLD).astype(int)
    high_pred = (y_pred >= HIGH_THRESHOLD).astype(int)
    metrics.update(_classification_metrics(high_true, high_pred))
    metrics.update({
        'experiment': 'baseline_train_mean',
        'feature_set': 'none',
        'target_type': 'absolute_scem',
        'model': 'baseline_train_mean',
        'feature_k': 'none',
        'smoothing': 'none',
        'calibration': 'none',
        'physiological_baseline': 'none',
        'n': len(predictions),
    })
    return metrics, predictions


def nested_loso(frame, configs, random_state=42):
    subjects = sorted(frame['subject_code'].astype(str).unique())
    rows = []
    all_predictions = []
    for outer_subject in subjects:
        candidate_rows = []
        for config in configs:
            feature_frame, feature_columns = _frame_for_config(frame, config)
            if not feature_columns:
                continue
            if config.feature_k is not None and config.feature_k > len(feature_columns):
                config = ExperimentConfig(
                    config.feature_set,
                    config.target_type,
                    config.model,
                    None,
                    config.smoothing,
                    config.calibration,
                    config.physiological_baseline,
                )
            result, _predictions = evaluate_config(
                feature_frame,
                feature_columns,
                config,
                random_state=random_state,
                heldout_subjects=[outer_subject],
            )
            if result is None:
                continue
            score = result['MAE']
            if np.isnan(score):
                score = 1.0 - result['balanced_accuracy']
            candidate_rows.append((score, config, result))
        if not candidate_rows:
            continue
        _score, best_config, inner_result = sorted(candidate_rows, key=lambda item: item[0])[0]
        feature_frame, feature_columns = _frame_for_config(frame, best_config)
        test_mask = (feature_frame['subject_code'].astype(str) == outer_subject).to_numpy()
        train_mask = ~test_mask
        y_true, y_pred, score = _fit_predict_fold(
            feature_frame,
            feature_columns,
            best_config,
            train_mask,
            test_mask,
            random_state=random_state,
        )
        eval_mask = np.ones(len(y_true), dtype=bool)
        if best_config.calibration == 'initial_subject':
            eval_mask = ~_calibration_mask(feature_frame.loc[test_mask].copy())
        if not eval_mask.any():
            continue
        metric = (
            _classification_metrics(y_true[eval_mask], y_pred[eval_mask])
            if best_config.target_type == 'high_binary'
            else _regression_metrics(y_true[eval_mask], y_pred[eval_mask])
        )
        metric.update({
            'fold_subject': outer_subject,
            'selected_experiment': best_config.name,
            'inner_selection_score': float(inner_result['MAE']) if not np.isnan(inner_result['MAE']) else float(1.0 - inner_result['balanced_accuracy']),
            'n': int(eval_mask.sum()),
        })
        rows.append(metric)
        meta = feature_frame.loc[test_mask].reset_index(drop=True)
        for row_index, keep in enumerate(eval_mask):
            if keep:
                all_predictions.append({
                    'experiment': 'nested_loso_selected',
                    'fold_subject': outer_subject,
                    'window_id': meta.loc[row_index, 'window_id'],
                    'y_true': float(y_true[row_index]),
                    'y_pred': float(y_pred[row_index]),
                    'score': float(score[row_index]),
                    'selected_experiment': best_config.name,
                })
    return rows, all_predictions


def _frame_for_config(base_frame, config):
    frame = base_frame.copy()
    all_features = [
        column for column in frame.columns
        if _feature_allowed(column, config.feature_set)
    ]
    feature_columns = [
        column for column in all_features
        if column not in {
            'window_id', 'source_file', 'subject_code', 'session_name',
            'experiment', 'operator', 'section_id', 'response_id',
        }
    ]
    return frame, feature_columns


def _fit_final_model(frame, feature_columns, config, output_dir, random_state=42):
    if config.target_type != 'relative_scem' or config.model != 'bayesian_ridge':
        return
    x = _apply_paso1_baseline(frame, feature_columns)
    y = frame['relative_scem'].to_numpy(dtype=float)
    estimator = _model(config.model, config.feature_k, random_state)
    estimator.fit(x, y, model__sample_weight=frame['sample_weight'].to_numpy(dtype=float))
    payload = {
        'stage_1_model': estimator,
        'stage_1_target': 'relative_scem',
        'stage_2_calibration': 'initial_subject_scem_anchor',
        'physiological_baseline': 'paso_1',
        'feature_columns': feature_columns,
        'feature_k': config.feature_k,
    }
    joblib.dump(payload, output_dir / 'stage1_relative_scem_stage2_calibrator.joblib')


def run_experiments(
    input_dir: Path,
    output_dir: Path,
    random_state=42,
    min_feature_coverage=0.6,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    base_frames = {}
    for feature_set in ('robust', 'eeg_robust', 'ppg_robust', 'all'):
        try:
            frame, feature_columns = load_experiment_dataset(
                input_dir,
                feature_set=feature_set,
                min_feature_coverage=min_feature_coverage,
            )
        except ValueError:
            continue
        base_frames[feature_set] = (frame, feature_columns)
    configs = default_configs()
    result_rows = []
    prediction_rows = []
    baseline_result, baseline_predictions = baseline_train_mean(base_frames['robust'][0])
    result_rows.append(baseline_result)
    prediction_rows.extend(baseline_predictions)
    for config in configs:
        if config.feature_set not in base_frames:
            continue
        frame, feature_columns = base_frames[config.feature_set]
        if config.feature_k is not None and config.feature_k > len(feature_columns):
            continue
        try:
            result, predictions = evaluate_config(
                frame,
                feature_columns,
                config,
                random_state=random_state,
            )
        except RuntimeError:
            continue
        if result is None:
            continue
        result_rows.append(result)
        prediction_rows.extend(predictions)
    baseline_mae = baseline_result['MAE']
    for row in result_rows:
        row['beats_baseline'] = (
            bool(row['MAE'] < baseline_mae)
            if not np.isnan(row['MAE']) else
            bool(row['balanced_accuracy'] > 0.5)
        )
        row['baseline_mae_reference'] = baseline_mae

    nested_configs = [
        config for config in configs
        if config.model in {'bayesian_ridge', 'ridge'} and
        config.target_type in {'absolute_scem', 'relative_scem'}
    ]
    nested_rows, nested_predictions = nested_loso(
        base_frames['robust'][0],
        nested_configs,
        random_state=random_state,
    )
    prediction_rows.extend(nested_predictions)
    pd.DataFrame(result_rows).sort_values(
        ['beats_baseline', 'MAE', 'balanced_accuracy'],
        ascending=[False, True, False],
    ).to_csv(output_dir / 'experiment_results.csv', index=False)
    pd.DataFrame(prediction_rows).to_csv(
        output_dir / 'experiment_predictions.csv',
        index=False,
    )
    pd.DataFrame(nested_rows).to_csv(
        output_dir / 'nested_loso_selections.csv',
        index=False,
    )
    best = sorted(
        [row for row in result_rows if not np.isnan(row['MAE'])],
        key=lambda row: row['MAE'],
    )[0]
    best_config = next(
        (config for config in configs if config.name == best['experiment']),
        None,
    )
    if best_config is not None:
        _fit_final_model(
            base_frames[best_config.feature_set][0],
            base_frames[best_config.feature_set][1],
            best_config,
            output_dir,
            random_state=random_state,
        )
    summary = {
        'input_dir': str(input_dir),
        'n_experiments': len(result_rows),
        'best_regression_experiment': best['experiment'],
        'best_regression_mae': best['MAE'],
        'baseline_mae_reference': baseline_mae,
        'paso_1_baseline_methodology': (
            'Paso 1 is treated as a passive task baseline, not as resting state. '
            'It is suitable for within-subject physiological normalization if '
            'the report states this limitation explicitly.'
        ),
    }
    (output_dir / 'experiment_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return summary


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run LOSO/nested LOSO SCEM modeling experiments.',
    )
    parser.add_argument('--input-dir', default='ml_output')
    parser.add_argument('--output-dir', default='ml_output/experiments')
    parser.add_argument('--random-state', type=int, default=42)
    parser.add_argument('--min-feature-coverage', type=float, default=0.6)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    summary = run_experiments(
        Path(args.input_dir),
        Path(args.output_dir),
        random_state=args.random_state,
        min_feature_coverage=args.min_feature_coverage,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
