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

## Current local training results

Run date: 2026-08-30.

Configuration:

```text
input_dir: ml_output
n_subjects: 11
n_windows: 690
n_features: 167
target: scem_score_recomputed
validation: leave-one-subject-out
models: elasticnet, random_forest, xgboost
riemannian_comparator: true
```

LOSO overall metrics:

| Model | MAE | RMSE | R2 | Spearman | n |
|---|---:|---:|---:|---:|---:|
| elasticnet | 0.827 | 0.990 | -0.899 | -0.361 | 690 |
| random_forest | 0.863 | 1.042 | -1.104 | -0.555 | 690 |
| xgboost | 0.783 | 0.959 | -0.780 | -0.574 | 690 |
| riemann_tangent_ridge | 0.761 | 0.997 | -0.927 | -0.627 | 690 |

These results confirm that the pipeline runs end to end, but the current dataset
is still too small and heterogeneous for a validated subject-independent model.
Negative LOSO R2 means that the models do not yet generalize reliably to unseen
subjects. Treat realtime predictions as experimental until more subjects and
balanced SCEM labels are collected.

## Inspect results

```powershell
Get-Content ml_output\models\training_summary.json
```

```powershell
Import-Csv ml_output\models\metrics_loso.csv |
  Where-Object { $_.fold_subject -eq "__overall__" } |
  Format-Table model,mae,rmse,r2,spearman,n -AutoSize
```

```powershell
Import-Csv ml_output\models\xgboost_shap_global.csv |
  Select-Object -First 20 |
  Format-Table feature,mean_abs_shap -AutoSize
```

```powershell
Import-Csv ml_output\models\xgboost_shap_local_top10.csv |
  Select-Object -First 30 |
  Format-Table window_id,feature,shap_value,feature_value -AutoSize
```

## Realtime cognitive state branch

The realtime integration lives in `feature/realtime-cognitive-staet`
(`staet` is the current branch spelling). It adds:

- `muse_ml.realtime`, which reads the active local SQLite session.
- 60 s EEG/PPG windows with 30 s updates by default.
- Optional publication of derived cognitive state to the cloud GUI.
- Local XAI factors per prediction when the loaded model supports SHAP or
  feature importance.

The cloud publication is opt-in because cognitive engagement predictions are
sensitive derived physiological data. To publish them to the researcher GUI:

```powershell
python -m muse_web.edge_agent `
  --cloud-url "wss://musepipeline.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions" `
  --enable-cognitive-cloud `
  --cognitive-model "ml_output\models\xgboost_final.joblib" `
  --cognitive-window-seconds 60 `
  --cognitive-update-seconds 30
```

Without `--enable-cognitive-cloud`, the agent keeps cognitive reporting disabled
and does not send predictions to Render.

## Methodological safeguards

- Use Leave-One-Subject-Out validation as the main estimate of generalization.
- Never use random window splits as the primary metric.
- Fit imputation, scaling, feature selection, and model parameters inside each
  training fold only.
- Weight windows by response so long SCEM intervals do not dominate training.
- Compare EEG-only, PPG-only, and EEG+PPG feature subsets before selecting the
  final model.
