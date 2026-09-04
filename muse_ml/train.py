"""Train SCEM cognitive engagement models with LOSO validation and XAI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler


TARGET_COLUMN = 'scem_score_recomputed'


def load_ml_dataset(input_dir: Path):
    """Return feature matrix, labels, groups, sample weights, and metadata."""

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
    feature_columns = [
        column for column in matrix.columns
        if column in frame.columns
    ]
    response_counts = frame['response_id'].value_counts().to_dict()
    sample_weight = frame['response_id'].map(
        lambda response_id: 1.0 / response_counts[response_id]
    ).to_numpy(dtype=float)
    return (
        frame[feature_columns],
        frame[TARGET_COLUMN].to_numpy(dtype=float),
        frame['subject_code'].astype(str).to_numpy(),
        sample_weight,
        frame.reset_index(),
        feature_columns,
    )


def _spearman(y_true, y_pred):
    if len(np.unique(y_true)) < 2 or len(np.unique(y_pred)) < 2:
        return float('nan')
    value = stats.spearmanr(y_true, y_pred).correlation
    return float(value) if np.isfinite(value) else float('nan')


def _metrics(y_true, y_pred):
    mse = mean_squared_error(y_true, y_pred)
    return {
        'mae': float(mean_absolute_error(y_true, y_pred)),
        'rmse': float(np.sqrt(mse)),
        'r2': float(r2_score(y_true, y_pred)) if len(y_true) > 1 else float('nan'),
        'spearman': _spearman(y_true, y_pred),
        'n': int(len(y_true)),
    }


def _models(random_state=42):
    models = {
        'elasticnet': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('scaler', RobustScaler()),
            ('model', ElasticNet(
                alpha=0.02,
                l1_ratio=0.25,
                random_state=random_state,
                max_iter=20_000,
            )),
        ]),
        'random_forest': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('model', RandomForestRegressor(
                n_estimators=300,
                max_depth=5,
                min_samples_leaf=5,
                random_state=random_state,
                n_jobs=-1,
            )),
        ]),
    }
    try:
        from xgboost import XGBRegressor
        models['xgboost'] = Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('model', XGBRegressor(
                objective='reg:squarederror',
                n_estimators=250,
                max_depth=2,
                learning_rate=0.03,
                subsample=0.85,
                colsample_bytree=0.75,
                reg_alpha=0.1,
                reg_lambda=3.0,
                random_state=random_state,
                n_jobs=1,
            )),
        ])
    except ImportError:
        pass
    return models


def loso_train(input_dir: Path, output_dir: Path, random_state=42):
    """Train each model with leave-one-subject-out validation."""

    output_dir.mkdir(parents=True, exist_ok=True)
    x, y, groups, sample_weight, metadata, feature_columns = load_ml_dataset(input_dir)
    unique_subjects = sorted(set(groups))
    models = _models(random_state=random_state)
    metrics_rows = []
    prediction_rows = []

    for model_name, estimator in models.items():
        fold_predictions = []
        fold_truth = []
        for subject in unique_subjects:
            test_mask = groups == subject
            train_mask = ~test_mask
            if train_mask.sum() == 0 or test_mask.sum() == 0:
                continue
            fitted = clone(estimator)
            fit_kwargs = {}
            if model_name in {'xgboost', 'random_forest'}:
                fit_kwargs['model__sample_weight'] = sample_weight[train_mask]
            else:
                fit_kwargs['model__sample_weight'] = sample_weight[train_mask]
            fitted.fit(x.iloc[train_mask], y[train_mask], **fit_kwargs)
            y_pred = fitted.predict(x.iloc[test_mask])
            fold_metric = _metrics(y[test_mask], y_pred)
            fold_metric.update({'model': model_name, 'fold_subject': subject})
            metrics_rows.append(fold_metric)
            fold_predictions.extend(y_pred.tolist())
            fold_truth.extend(y[test_mask].tolist())
            for row, truth, pred in zip(
                metadata.loc[test_mask].to_dict('records'),
                y[test_mask],
                y_pred,
            ):
                prediction_rows.append({
                    'model': model_name,
                    'fold_subject': subject,
                    'window_id': row['window_id'],
                    'subject_code': row['subject_code'],
                    'session_name': row['session_name'],
                    'section_id': row['section_id'],
                    'response_id': row['response_id'],
                    'start_time': row['start_time'],
                    'end_time': row['end_time'],
                    'y_true': float(truth),
                    'y_pred': float(pred),
                    'abs_error': float(abs(truth - pred)),
                })
        if fold_truth:
            overall = _metrics(np.asarray(fold_truth), np.asarray(fold_predictions))
            overall.update({'model': model_name, 'fold_subject': '__overall__'})
            metrics_rows.append(overall)

        final_model = clone(estimator)
        fit_kwargs = {'model__sample_weight': sample_weight}
        final_model.fit(x, y, **fit_kwargs)
        joblib.dump(final_model, output_dir / f'{model_name}_final.joblib')
        _write_feature_importance(
            final_model, model_name, feature_columns, output_dir
        )
        if model_name == 'xgboost':
            _write_shap(final_model, x, metadata, feature_columns, output_dir)

    riemann_summary = _train_riemannian_if_available(
        x, y, groups, sample_weight, metadata, output_dir
    )
    if riemann_summary is not None:
        metrics_rows.extend(riemann_summary['metrics'])
        prediction_rows.extend(riemann_summary['predictions'])

    pd.DataFrame(metrics_rows).to_csv(output_dir / 'metrics_loso.csv', index=False)
    pd.DataFrame(prediction_rows).to_csv(
        output_dir / 'predictions_loso.csv',
        index=False,
    )
    summary = {
        'input_dir': str(input_dir),
        'subjects': unique_subjects,
        'n_subjects': len(unique_subjects),
        'n_windows': int(len(x)),
        'n_features': int(len(feature_columns)),
        'models': sorted(models),
        'riemannian_comparator': riemann_summary is not None,
        'target': TARGET_COLUMN,
        'validation': 'leave-one-subject-out',
    }
    (output_dir / 'training_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return summary


def _covariance_matrices(x: pd.DataFrame):
    channels = ('channel_1', 'channel_2', 'channel_3', 'channel_4')
    required = [
        f'eeg_cov_{channel_a}_{channel_b}'
        for i, channel_a in enumerate(channels)
        for channel_b in channels[i:]
    ]
    if any(column not in x.columns for column in required):
        return None
    matrices = []
    values = x[required].to_numpy(dtype=float)
    for row in values:
        matrix = np.zeros((len(channels), len(channels)), dtype=float)
        index = 0
        for i in range(len(channels)):
            for j in range(i, len(channels)):
                value = row[index]
                if not np.isfinite(value):
                    value = 0.0
                matrix[i, j] = value
                matrix[j, i] = value
                index += 1
        matrix += np.eye(len(channels)) * 1e-6
        matrices.append(matrix)
    return np.asarray(matrices)


def _train_riemannian_if_available(
    x, y, groups, sample_weight, metadata, output_dir
):
    matrices = _covariance_matrices(x)
    if matrices is None:
        return None
    try:
        from pyriemann.tangentspace import TangentSpace
    except ImportError:
        return None

    metrics_rows = []
    prediction_rows = []
    unique_subjects = sorted(set(groups))
    for subject in unique_subjects:
        test_mask = groups == subject
        train_mask = ~test_mask
        tangent = TangentSpace(metric='riemann')
        x_train = tangent.fit_transform(matrices[train_mask])
        x_test = tangent.transform(matrices[test_mask])
        imputer = SimpleImputer(strategy='median')
        scaler = RobustScaler()
        regressor = Ridge(alpha=5.0)
        x_train = scaler.fit_transform(imputer.fit_transform(x_train))
        x_test = scaler.transform(imputer.transform(x_test))
        regressor.fit(x_train, y[train_mask], sample_weight=sample_weight[train_mask])
        y_pred = regressor.predict(x_test)
        fold_metric = _metrics(y[test_mask], y_pred)
        fold_metric.update({
            'model': 'riemann_tangent_ridge',
            'fold_subject': subject,
        })
        metrics_rows.append(fold_metric)
        for row, truth, pred in zip(
            metadata.loc[test_mask].to_dict('records'),
            y[test_mask],
            y_pred,
        ):
            prediction_rows.append({
                'model': 'riemann_tangent_ridge',
                'fold_subject': subject,
                'window_id': row['window_id'],
                'subject_code': row['subject_code'],
                'session_name': row['session_name'],
                'section_id': row['section_id'],
                'response_id': row['response_id'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'y_true': float(truth),
                'y_pred': float(pred),
                'abs_error': float(abs(truth - pred)),
            })
    all_predictions = [row['y_pred'] for row in prediction_rows]
    all_truth = [row['y_true'] for row in prediction_rows]
    if all_truth:
        overall = _metrics(np.asarray(all_truth), np.asarray(all_predictions))
        overall.update({
            'model': 'riemann_tangent_ridge',
            'fold_subject': '__overall__',
        })
        metrics_rows.append(overall)

    tangent = TangentSpace(metric='riemann')
    transformed = tangent.fit_transform(matrices)
    imputer = SimpleImputer(strategy='median')
    scaler = RobustScaler()
    regressor = Ridge(alpha=5.0)
    transformed = scaler.fit_transform(imputer.fit_transform(transformed))
    regressor.fit(transformed, y, sample_weight=sample_weight)
    joblib.dump(
        {'tangent': tangent, 'imputer': imputer, 'scaler': scaler, 'model': regressor},
        output_dir / 'riemann_tangent_ridge_final.joblib',
    )
    return {'metrics': metrics_rows, 'predictions': prediction_rows}


def _write_feature_importance(model, model_name, feature_columns, output_dir):
    estimator = model.named_steps['model']
    if hasattr(estimator, 'feature_importances_'):
        values = estimator.feature_importances_
    elif hasattr(estimator, 'coef_'):
        values = np.abs(estimator.coef_)
    else:
        return
    rows = pd.DataFrame({
        'model': model_name,
        'feature_name': feature_columns,
        'importance': values,
    }).sort_values('importance', ascending=False)
    rows.to_csv(output_dir / f'{model_name}_feature_importance.csv', index=False)


def _write_shap(model, x, metadata, feature_columns, output_dir):
    try:
        import shap
    except ImportError:
        return
    sample_size = min(500, len(x))
    sample = x.sample(n=sample_size, random_state=42) if len(x) > sample_size else x
    transformed = model.named_steps['imputer'].transform(sample)
    estimator = model.named_steps['model']
    explainer = shap.TreeExplainer(estimator)
    shap_values = explainer.shap_values(transformed)
    if isinstance(shap_values, list):
        shap_values = shap_values[0]
    shap_values = np.asarray(shap_values)
    global_rows = pd.DataFrame({
        'feature_name': feature_columns,
        'mean_abs_shap': np.mean(np.abs(shap_values), axis=0),
        'mean_shap': np.mean(shap_values, axis=0),
    }).sort_values('mean_abs_shap', ascending=False)
    global_rows.to_csv(output_dir / 'xgboost_shap_global.csv', index=False)

    top_indexes = np.argsort(np.abs(shap_values), axis=1)[:, -10:][:, ::-1]
    local_rows = []
    sample_metadata = metadata.set_index('window_id').loc[sample.index].reset_index()
    for row_index, feature_indexes in enumerate(top_indexes):
        window_id = sample_metadata.iloc[row_index]['window_id']
        for rank, feature_index in enumerate(feature_indexes, start=1):
            local_rows.append({
                'window_id': window_id,
                'rank': rank,
                'feature_name': feature_columns[feature_index],
                'feature_value': float(transformed[row_index, feature_index]),
                'shap_value': float(shap_values[row_index, feature_index]),
            })
    pd.DataFrame(local_rows).to_csv(
        output_dir / 'xgboost_shap_local_top10.csv',
        index=False,
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description='Train SCEM engagement models with LOSO validation.',
    )
    parser.add_argument('--input-dir', default='ml_output')
    parser.add_argument('--output-dir', default='ml_output/models')
    parser.add_argument('--random-state', type=int, default=42)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    summary = loso_train(
        Path(args.input_dir),
        Path(args.output_dir),
        random_state=args.random_state,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
