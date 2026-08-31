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
  --stride-seconds 30 `
  --label-half-life-seconds 180
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
  --output-dir ml_output\models `
  --feature-set robust `
  --min-feature-coverage 0.6
```

Outputs:

- `metrics_loso.csv`
- `predictions_loso.csv`
- `ordinal_metrics_loso.csv`
- `ordinal_predictions_loso.csv`
- `training_summary.json`
- `*_feature_importance.csv`
- `xgboost_shap_global.csv` when `xgboost` and `shap` are installed.
- `xgboost_shap_local_top10.csv` when SHAP is available.

## Accuracy-improvement experiments

The current strategy focuses on reducing label noise and overfitting rather
than creating more correlated windows from the same participants.

Implemented changes:

- Robust feature sets: `robust`, `eeg_robust`, `ppg_robust`, and `all`.
- Temporal label weighting: windows closer to the SCEM response receive higher
  sample weight. The default half-life is 180 s, with a minimum weight of 0.25.
- Conservative models: train-mean baseline, Bayesian Ridge, stronger ElasticNet,
  Huber regression, shallow Random Forest, and regularized XGBoost.
- Ordinal comparator: balanced logistic regression for low/medium/high
  engagement.
- Honest validation: Leave-One-Subject-Out remains the main reported metric and
  every model is compared against a train-fold mean baseline.

Commands used for the main robust run:

```powershell
python -m muse_ml.dataset `
  --input-dir subject_data `
  --output-dir ml_output `
  --window-seconds 60 `
  --stride-seconds 30 `
  --label-half-life-seconds 180

python -m muse_ml.train `
  --input-dir ml_output `
  --output-dir ml_output\models `
  --feature-set robust `
  --min-feature-coverage 0.6
```

Run the full architecture comparison:

```powershell
python -m muse_ml.experiments `
  --input-dir ml_output `
  --output-dir ml_output\experiments `
  --min-feature-coverage 0.6
```

This writes:

- `experiment_results.csv`: final comparison table.
- `experiment_predictions.csv`: window-level predictions for every experiment.
- `nested_loso_selections.csv`: selected config per outer LOSO fold.
- `experiment_summary.json`: best model and methodology notes.
- `stage1_relative_scem_stage2_calibrator.joblib`: final two-stage artifact
  when the best model is the calibrated relative-SCEM architecture.

Commands used to test whether more windows help:

```powershell
python -m muse_ml.dataset `
  --input-dir subject_data `
  --output-dir ml_output_30s `
  --window-seconds 30 `
  --stride-seconds 15 `
  --label-half-life-seconds 180

python -m muse_ml.train `
  --input-dir ml_output_30s `
  --output-dir ml_output_30s\models `
  --feature-set robust `
  --min-feature-coverage 0.6
```

Commands used to compare modalities:

```powershell
python -m muse_ml.train `
  --input-dir ml_output `
  --output-dir ml_output\models_eeg_robust `
  --feature-set eeg_robust `
  --min-feature-coverage 0.6

python -m muse_ml.train `
  --input-dir ml_output `
  --output-dir ml_output\models_ppg_robust `
  --feature-set ppg_robust `
  --min-feature-coverage 0.6
```

## Current local training results

Run date: 2026-08-30.

Configuration:

```text
input_dir: ml_output
n_subjects: 11
n_windows: 690
n_features: 102
feature_set: robust
min_feature_coverage: 0.6
label_half_life_seconds: 180
target: scem_score_recomputed
validation: leave-one-subject-out
models: baseline_train_mean, bayesian_ridge, elasticnet, huber, random_forest, xgboost
ordinal_model: ordinal_logistic
riemannian_comparator: false for robust features
```

LOSO overall metrics:

| Model | MAE | RMSE | R2 | Spearman | n |
|---|---:|---:|---:|---:|---:|
| baseline_train_mean | 0.607 | 0.771 | -0.151 | -0.716 | 690 |
| bayesian_ridge | 0.609 | 0.773 | -0.159 | -0.708 | 690 |
| xgboost | 0.681 | 0.854 | -0.414 | -0.642 | 690 |
| random_forest | 0.684 | 0.871 | -0.469 | -0.573 | 690 |
| elasticnet | 0.905 | 1.092 | -1.312 | -0.428 | 690 |
| huber | 1.085 | 1.328 | -2.418 | -0.291 | 690 |

Window comparison:

| Window / stride | Features | Best non-baseline model | MAE | RMSE | R2 |
|---|---:|---|---:|---:|---:|
| 60 s / 30 s | 102 | bayesian_ridge | 0.609 | 0.773 | -0.159 |
| 30 s / 15 s | 102 | bayesian_ridge | 0.610 | 0.775 | -0.157 |

Feature-set comparison:

| Feature set | Features | Best non-baseline model | MAE | RMSE | R2 |
|---|---:|---|---:|---:|---:|
| robust EEG+PPG | 102 | bayesian_ridge | 0.609 | 0.773 | -0.159 |
| eeg_robust | 50 | bayesian_ridge | 0.608 | 0.772 | -0.153 |
| ppg_robust | 52 | bayesian_ridge | 0.611 | 0.778 | -0.172 |
| all features | 167 | bayesian_ridge | 0.610 | 0.775 | -0.163 |

Ordinal LOSO:

| Model | Accuracy | Balanced accuracy | Macro F1 | n |
|---|---:|---:|---:|---:|
| ordinal_logistic, 60 s / 30 s | 0.286 | 0.184 | 0.194 | 690 |
| ordinal_logistic, 30 s / 15 s | 0.272 | 0.168 | 0.183 | 1450 |

SCEM class balance for the current 60 s / 30 s dataset:

| Class | Windows |
|---|---:|
| high | 487 |
| medium | 196 |
| low | 7 |

## Two-stage and nested LOSO results

The practice protocol makes `paso_1` viable as a passive-task physiological
baseline because the student is mainly receiving instructions, learning the
coordinate frame, and answering a concept quiz. It is not a resting-state
baseline: the student is still processing new information. Methodologically,
reports should describe it as a task-specific passive learning baseline.

The best architecture after the new experiment runner was:

```text
Stage 1: global physiological model
  EEG robust features
  paso_1 physiological baseline subtraction
  SelectKBest f_regression, k=20
  Bayesian Ridge
  target = SCEM relative to subject mean

Stage 2: subject calibrator
  first SCEM response from the held-out subject anchors the reconstructed score
  prediction smoothing = rolling mean of 3 windows
```

Main comparison:

| Feature set | Target | Model | Selection | Smoothing | Calibration | MAE | RMSE | R2 | Spearman | Beats baseline |
|---|---|---|---|---|---|---:|---:|---:|---:|---|
| eeg_robust | relative_scem | bayesian_ridge | k=20 | rolling3 | initial_subject + paso_1 | 0.561 | 0.714 | -0.004 | 0.525 | yes |
| ppg_robust | relative_scem | bayesian_ridge | k=20 | rolling3 | initial_subject + paso_1 | 0.561 | 0.716 | -0.009 | 0.542 | yes |
| robust | relative_scem | bayesian_ridge | k=50 | rolling3 | initial_subject + paso_1 | 0.562 | 0.716 | -0.010 | 0.538 | yes |
| robust | relative_scem | xgboost | k=20 | rolling3 | initial_subject + paso_1 | 0.563 | 0.716 | -0.008 | 0.532 | yes |
| eeg_robust | rank_relative | ridge | k=20 | rolling3 | initial_subject + paso_1 | 0.584 | 0.751 | -0.111 | -0.334 | yes |
| none | absolute_scem | train mean baseline | none | none | none | 0.607 | 0.771 | -0.151 | -0.716 | no |
| robust | absolute_scem | bayesian_ridge | k=50 | none | none | 0.614 | 0.784 | -0.190 | -0.692 | no |

This is the first configuration that beats the train-fold mean baseline under
LOSO. MAE improved from 0.607 to 0.561, a reduction of about 7.7%. RMSE improved
from 0.771 to 0.714, and Spearman changed from negative to clearly positive.
R2 is still slightly negative, but very close to zero, so the model is near the
threshold where it starts explaining between-subject variance better than the
mean predictor.

Nested LOSO was also implemented for model-selection auditing. In this dataset
it produced 10 evaluable outer folds with aggregate MAE 0.750 and RMSE 0.912.
That is worse than the best pre-specified two-stage hypothesis. The practical
interpretation is that nested selection is still unstable with only 11 subjects:
it is useful as an honesty check, but it should not yet be used as the final
model-selection authority.

High vs not-high engagement classification was not successful yet:

| Model | Balanced accuracy | Macro F1 | Accuracy |
|---|---:|---:|---:|
| logistic, no SCEM calibration | 0.379 | 0.363 | 0.383 |
| logistic, initial SCEM calibration | 0.364 | 0.341 | 0.347 |

Learning-to-rank was better than the old absolute models in MAE, but did not
solve ordering: Spearman remained negative. Multi-output item modeling also did
not help on this dataset. The strongest current path is therefore Bayesian Ridge
with fold-safe feature selection, paso_1 physiological baseline, relative SCEM
target, initial subject calibration, and temporal smoothing.

These results confirm that the pipeline runs end to end, but the current dataset
is not yet strong enough for a validated subject-independent physiological
model. The best non-baseline result is Bayesian Ridge with robust EEG-only
features, but it is effectively tied with the train-fold mean baseline. Negative
LOSO R2 means that the models do not yet outperform a simple baseline when the
test subject was never seen during training. More windows did not solve this:
30 s / 15 s increased samples from 690 to 1450, but performance stayed almost
unchanged.

The ordinal model is not recommended yet. Its low balanced accuracy and macro
F1 are explained by label imbalance: only 7 windows are in the low-engagement
class, while 487 are high. For now, keep regression as the primary output and
derive low/medium/high from the regressed SCEM score only as a display layer.

## XAI interpretation

For the robust 60 s / 30 s XGBoost model, the top global SHAP factors were:

| Feature | Mean abs SHAP | Interpretation |
|---|---:|---|
| `eeg_channel_3_rel_gamma_low` | 0.052 | Low-gamma relative power in channel 3 was the strongest tree-model signal. |
| `eeg_channel_1_rel_beta` | 0.045 | Beta relative power contributed strongly, consistent with attention/activation hypotheses, but not yet validated here. |
| `eeg_channel_4_rel_theta` | 0.035 | Theta relative power contributed to engagement estimates. |
| `ppg_median_hr_mean` | 0.030 | Heart-rate level affected predictions. |
| `ppg_iqr_pnn50` | 0.019 | Variability across PPG channels in pulse-rate variability affected predictions. |
| `eeg_channel_2_log_beta_alpha` | 0.013 | Beta/alpha balance contributed to the model. |
| `ppg_median_hr_std` | 0.011 | Short-window heart-rate variability also contributed. |
| `ppg_median_pnn50` | 0.011 | Pulse-rate variability was used by the model. |

SHAP magnitude says which features moved predictions most inside the fitted
XGBoost model; it does not prove causality. Because XGBoost still underperforms
the baseline under LOSO, these explanations should be read as exploratory
signals for future protocol design, not as final physiological conclusions.

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
