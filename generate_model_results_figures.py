import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

output_dir = Path("ml_results_figures")
output_dir.mkdir(exist_ok=True)

results = pd.DataFrame([
    {
        "Model": "Training Mean Baseline",
        "Features": "None",
        "MAE": 0.607,
        "RMSE": 0.771,
        "R2": -0.151,
        "Spearman": -0.716,
        "Beats Baseline": "No",
    },
    {
        "Model": "Bayesian Ridge",
        "Features": "Robust EEG+PPG",
        "MAE": 0.609,
        "RMSE": 0.773,
        "R2": -0.159,
        "Spearman": -0.708,
        "Beats Baseline": "No",
    },
    {
        "Model": "XGBoost Regressor",
        "Features": "Robust EEG+PPG",
        "MAE": 0.681,
        "RMSE": 0.854,
        "R2": -0.414,
        "Spearman": -0.642,
        "Beats Baseline": "No",
    },
    {
        "Model": "Calibrated Bayesian Ridge",
        "Features": "EEG-only",
        "MAE": 0.561,
        "RMSE": 0.714,
        "R2": -0.004,
        "Spearman": 0.525,
        "Beats Baseline": "Yes",
    },
    {
        "Model": "Calibrated Bayesian Ridge",
        "Features": "PPG-only",
        "MAE": 0.561,
        "RMSE": 0.716,
        "R2": -0.009,
        "Spearman": 0.542,
        "Beats Baseline": "Yes",
    },
    {
        "Model": "Calibrated Bayesian Ridge",
        "Features": "EEG+PPG",
        "MAE": 0.562,
        "RMSE": 0.717,
        "R2": -0.012,
        "Spearman": 0.545,
        "Beats Baseline": "Yes",
    },
])

results.to_csv(output_dir / "model_training_results.csv", index=False)

plt.figure(figsize=(10, 5))
plt.barh(results["Model"] + " (" + results["Features"] + ")", results["MAE"])
plt.axvline(0.607, linestyle="--", color="red", label="Baseline MAE")
plt.xlabel("Mean Absolute Error")
plt.title("Model Comparison by MAE")
plt.legend()
plt.tight_layout()
plt.savefig(output_dir / "mae_comparison.png", dpi=300)
plt.close()

plt.figure(figsize=(10, 5))
plt.barh(results["Model"] + " (" + results["Features"] + ")", results["RMSE"])
plt.axvline(0.771, linestyle="--", color="red", label="Baseline RMSE")
plt.xlabel("Root Mean Squared Error")
plt.title("Model Comparison by RMSE")
plt.legend()
plt.tight_layout()
plt.savefig(output_dir / "rmse_comparison.png", dpi=300)
plt.close()

plt.figure(figsize=(10, 5))
plt.barh(results["Model"] + " (" + results["Features"] + ")", results["Spearman"])
plt.axvline(0, linestyle="--", color="black")
plt.xlabel("Spearman Correlation")
plt.title("Model Comparison by Rank Correlation")
plt.tight_layout()
plt.savefig(output_dir / "spearman_comparison.png", dpi=300)
plt.close()

modality_results = results[
    (results["Model"] == "Calibrated Bayesian Ridge")
    & (results["Features"].isin(["EEG-only", "PPG-only", "EEG+PPG"]))
]

plt.figure(figsize=(7, 5))
plt.bar(modality_results["Features"], modality_results["MAE"])
plt.axhline(0.607, linestyle="--", color="red", label="Baseline MAE")
plt.ylabel("Mean Absolute Error")
plt.title("EEG-only vs PPG-only vs EEG+PPG")
plt.legend()
plt.tight_layout()
plt.savefig(output_dir / "modality_mae_comparison.png", dpi=300)
plt.close()

plt.figure(figsize=(7, 5))
plt.bar(modality_results["Features"], modality_results["Spearman"])
plt.axhline(0, linestyle="--", color="black")
plt.ylabel("Spearman Correlation")
plt.title("Correlation by Physiological Modality")
plt.tight_layout()
plt.savefig(output_dir / "modality_spearman_comparison.png", dpi=300)
plt.close()

fig, ax = plt.subplots(figsize=(12, 3))
ax.axis("off")

table_data = results.copy()
table_data[["MAE", "RMSE", "R2", "Spearman"]] = table_data[
    ["MAE", "RMSE", "R2", "Spearman"]
].round(3)

table = ax.table(
    cellText=table_data.values,
    colLabels=table_data.columns,
    loc="center",
    cellLoc="center",
)

table.auto_set_font_size(False)
table.set_fontsize(8)
table.scale(1, 1.5)

plt.title("Model Training Results Summary", pad=20)
plt.tight_layout()
plt.savefig(output_dir / "model_results_table.png", dpi=300)
plt.close()

print("Figures and result table saved in:", output_dir)