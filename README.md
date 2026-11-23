# Hyper-Localised Agricultural Weather Forecasting
**A Case Study of Busia County, Kenya**

Imarika is a farm-level weather forecasting system for smallholder farmers in Busia County, Western Kenya. It combines real-time data from 26 IoT weather stations with satellite reanalysis datasets to deliver 7-day forecasts, planting advisories, flood alerts, and SMS notifications.

---

## Dataset
| Source | Records | Period |
|---|---|---|
| IoT Stations (Anga) | 764,309 | Aug 2025 – present |
| Open-Meteo (ERA5) | 255,840 | Aug 2025 – present |
| NASA POWER | 63,674 | Jan 2020 – present |
| CHIRPS | 62,504 | Jan 2020 – present |

## Stack
**Data:** Python, MongoDB, PostgreSQL  
**Models:** SARIMA → LSTM → Microsoft Aurora (QLoRA)  
**Delivery:** FastAPI, React PWA, Africa's Talking SMS

## Presentation dashboard

Install the dashboard dependencies, then launch Streamlit from the project root:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Using the existing Windows virtual environment:

```bash
venv/Scripts/python.exe -m pip install -r requirements.txt
venv/Scripts/python.exe -m streamlit run app.py
```

Open `http://localhost:8501` and use the sidebar to navigate **Project Overview**,
**Data Collection**, **Model Results**, **Live Demo**, and **About**. The dashboard
reads `data/busia_stations.json`, `data/unified_dataset.csv`, the audit under
`reports/data_quality/`, and `reports/sarima_results.csv` / `sarima_plots/`.
Missing files produce on-page warnings. It performs no live model inference.

After dependency installation, the dashboard works offline. Its station map is
a local coordinate plot rather than `st.map`, whose background tiles need a
network connection. No external data APIs or map tiles are requested. The demo's
dotted 24-hour **Persistence baseline forecast** repeats the previous day's
hourly readings and preserves missing values. The audit is presented accurately:
94.93% is the zero-error-flag share, and two small rain-counter decreases are
reported as candidates, not confirmed full resets. SARIMA station coverage is
shown explicitly alongside the saved results.

## LSTM baseline

Install the TensorFlow/Keras dependencies and run both experiments:

```bash
venv/Scripts/python.exe -m pip install -r requirements-lstm.txt
venv/Scripts/python.exe lstm_model.py
```

The script first trains station `101990961244800083`, then trains a fresh model
on all 26 Busia stations. It uses 24-hour input windows, ten features, and five
next-hour outputs. Defaults are 50 epochs, batch size 64, and early stopping with
patience 10. Use `--runs single` or `--runs all` to select one experiment, and
`--output-dir PATH` to select another destination.

All-station outputs are `reports/lstm_model.keras`, `lstm_scaler.json`,
`lstm_history.csv`, `lstm_results.csv`, `lstm_predictions.csv`,
`lstm_training_curve.png`, and `lstm_plots/`. Single-station outputs have the same
names inside `reports/lstm_single_station/`. Split/missing-value tables, run
metadata and `reports/lstm.log` record progress and failures. Existing experiment
outputs require `--overwrite` to replace.

Splits use shared chronological 70/15/15 UTC timeline boundaries, so stations
with different coverage can have different row proportions. Only input features
are forward-filled, separately per station. Missing targets are not used as
training labels or evaluation ground truth. The scaler is fitted on training
rows only; validation/test windows may use earlier historical input rows.
Evaluation compares LSTM and persistence on common observed timestamps. SARIMA
skill uses the supplied fixed RMSE baselines rather than rescoring SARIMA on
these windows. `POOLED` metrics combine test observations; per-station rows are
also included. MAPE is a percentage and excludes zero actual values.

```bash
venv/Scripts/python.exe -m unittest discover -s tests -p test_lstm_model.py -v
```

## Progress
| Sprint | Focus | Status |
|---|---|---|
| 1 | Data Collection | Complete |
| 2 | Preprocessing | Complete |
| 3 | SARIMA & LSTM | Complete |
| 4 | Aurora Fine-tuning | Pending |
| 5 | API & Delivery | Pending |

---

**Student:** Philip Tait (166384) · Strathmore University · ICS 4E  
**Supervisor:** Ms. Julliet Kirui · **Industry:** @iLabAfrica Research and Innnovation Center Strathmore University
