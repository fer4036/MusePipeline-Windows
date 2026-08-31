from pathlib import Path

import pandas as pd

from muse_ml.experiments import run_experiments
from muse_ml.test_train import _write_minimal_dataset


def test_run_experiments_writes_summary_tables(tmp_path):
    input_dir = tmp_path / 'dataset'
    output_dir = tmp_path / 'experiments'
    _write_minimal_dataset(input_dir)

    summary = run_experiments(input_dir, output_dir)

    assert summary['n_experiments'] > 0
    results = pd.read_csv(output_dir / 'experiment_results.csv')
    assert {
        'feature_set',
        'target_type',
        'model',
        'smoothing',
        'calibration',
        'MAE',
        'RMSE',
        'R2',
        'Spearman',
        'balanced_accuracy',
        'macro_f1',
        'beats_baseline',
    }.issubset(results.columns)
    assert (output_dir / 'nested_loso_selections.csv').is_file()
