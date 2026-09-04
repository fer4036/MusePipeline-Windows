# Muse ML pipeline

This package builds a supervised dataset for cognitive engagement modeling from
the exported Muse CSV/SQLite-derived files.

## Recommended flow

```text
raw CSV/SQLite
-> ml_windows.csv
-> ml_features.csv
-> ml_labels.csv
-> LOSO split
-> XGBoost regression
-> SHAP explanations
-> evaluation
```

The canonical target is SCEM, recomputed from raw items:

```text
scem_score = mean(task_engagement, effort, persistence, flow)
```

The stored GUI score is preserved for audit but not treated as the source of
truth.

## Extract features

```powershell
python -m muse_ml.dataset `
  --input-dir subject_data `
  --output-dir ml_output `
  --window-seconds 60 `
  --stride-seconds 30
```

Outputs:

- `ml_windows.csv`: one row per physiological window.
- `ml_labels.csv`: SCEM labels joined to each window.
- `ml_features.csv`: EEG band powers/ratios and PPG pulse features.
- `manifest.json`: extraction settings and counts.

## Train and explain

```powershell
python -m muse_ml.train `
  --input-dir ml_output `
  --output-dir ml_output\models
```

Outputs:

- `metrics_loso.csv`
- `predictions_loso.csv`
- `training_summary.json`
- `*_feature_importance.csv`
- `xgboost_shap_global.csv` when `xgboost` and `shap` are installed.
- `xgboost_shap_local_top10.csv` when SHAP is available.

## Methodological safeguards

- Use Leave-One-Subject-Out validation as the main estimate of generalization.
- Never use random window splits as the primary metric.
- Fit imputation, scaling, feature selection, and model parameters inside each
  training fold only.
- Weight windows by response so long SCEM intervals do not dominate training.
- Compare EEG-only, PPG-only, and EEG+PPG feature subsets before selecting the
  final model.
