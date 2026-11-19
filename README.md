# Smart Grid Energy Consumption Forecasting & Analysis

End-to-end ML platform: ingest real energy data (location or upload), clean and
resample it, engineer time-series features, train **Linear Regression**,
**Decision Tree** and **Random Forest** on one chronological split, compare them
with MAE / RMSE / R², forecast the future, monitor live meters and export reports.

Nothing on the page is hard-coded: every figure comes from a trained model or a
stored reading.

---

## 1. Quick start

```bash
cd smartgrid
pip install -r requirements.txt
python3 serve.py            # or: uvicorn main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000> for the dashboard and
<http://localhost:8000/docs> for the interactive API docs.

On Home, click **Load CEEW Mathura data** to get a trained, working system in one
click (real CEEW smart-meter data bundled in `data/`).

## 2. What each page does

| Page | Purpose |
|---|---|
| Home | Landing page, location search, dataset drop zone, bundled-data loader |
| Dashboard | 8 summary cards + consumption history (daily/weekly/monthly/yearly), historical-vs-predicted, live chart |
| Location Explorer | Real country electricity data from OWID; imports it as a dataset and tries to train |
| Dataset Upload | Profile (rows, columns, dtypes, missing, duplicates, date range), column mapping, preprocessing, training |
| ML Predictions | Actual vs predicted on the hold-out, error by model, residual histogram, prediction table + CSV |
| Model Comparison | MAE / RMSE / R² / MAPE table, metric bar chart, Random Forest feature importance, evaluation setup |
| Historical Analysis | Hourly / daily / weekly / monthly / yearly aggregation, distribution histogram, peak-vs-average, bucket table, CSV + PDF |
| Live Monitoring | Post readings to `/live-api/readings`, meter table, reading history, auto-refresh every 5 s, SQLite storage |
| Future Forecast | Model + interval + horizon selection, recursive forecast, history-vs-future chart, forecast table, CSV + PDF |
| Reports | Dataset profile, preprocessed series, model comparison, historical, forecast, live readings (CSV / PDF) |
| About Project | Pipeline, sources, limitations, live API contract |

## 3. Machine learning

* **Algorithms (only these three):** `LinearRegression`, `DecisionTreeRegressor`,
  `RandomForestRegressor` — `engine.make_models()`.
* **Features** (17 at daily grain, 19 at hourly): calendar terms plus lags
  (1,2,3,7,14,21,30 or 1,2,3,24,48,168), rolling mean (3/7/14 or 3/24), rolling
  std and max. Every rolling window is `shift(1)`, so the target is never an input.
* **Split:** chronological 80/20 — no shuffling, the test period is strictly later.
* **Metrics:** MAE, RMSE, R² (+ MAPE) computed on the unseen test period only.
* **Best model:** lowest test MAE, selected automatically.
* **Forecast:** recursive one-step-ahead; predictions are appended to history and
  features are rebuilt from predicted values only.

### Verified results on the bundled CEEW Mathura file

| Model | MAE | RMSE | R² | MAPE |
|---|---|---|---|---|
| Linear Regression ★ | 11.983 | 14.754 | 0.6838 | 16.51 % |
| Decision Tree | 18.343 | 23.920 | 0.1689 | 23.10 % |
| Random Forest | 14.228 | 17.609 | 0.5496 | 19.50 % |

505 train rows / 127 test rows, daily grain. Reproduce any time with
`POST /seed-dataset?kind=mathura`.

## 4. Honest-data rules (enforced in code)

| Situation | Behaviour |
|---|---|
| Location with no published series | `404` + "no data for this country", never a fabricated series |
| Annual country data (41 rows) with 3-model training requested | Training **refused**: "Only 11 rows remain after feature engineering… at least 20 are needed" |
| Hourly analysis requested from daily source data | Refused: "cannot be derived without inventing detail" |
| Hourly forecast requested from a daily model | Refused, with the reason and the fix |
| Cumulative meter file | Differences taken first (`cumulative_meter=true`), negative steps dropped and counted |
| Live monitoring | Only readings actually POSTed are stored; there is no simulator in the platform |

## 5. Data sources

* **CEEW Smart Meter Data** (bundled: `data/ceew_mathura.csv`,
  `data/ceew_bareilly.csv`) — Harvard Dataverse
  [doi:10.7910/DVN/GOCHJH](https://doi.org/10.7910/DVN/GOCHJH), **CC0 1.0**.
* **Our World in Data — Energy** — `https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv`,
  built on Energy Institute Statistical Review + Ember, **CC BY 4.0**.
  Cached to `data/owid_energy.csv` (≈9 MB). Set `OWID_LOCAL_FILE=/path/to/owid-energy-data.csv`
  to use a local copy instead of downloading.

## 6. API

All endpoints are documented at `/docs`. Summary:

```
GET    /health
POST   /upload-dataset/                GET /datasets           GET /dataset/info
GET    /dataset/preview                POST /preprocess/       POST /train-models/
GET    /model-comparison/              POST /predict/  POST /forecast/
GET    /forecast/history-vs-prediction
GET    /historical/{hourly|daily|weekly|monthly|yearly}        GET /historical/distribution
GET    /location/search                GET /location/energy-data   POST /location/import
GET    /live-api/readings              POST /live-api/readings      DELETE /live-api/readings
GET    /live-api/history               GET  /live-api/meters
GET    /dashboard/summary
GET    /reports/{dataset|preprocessed|comparison|predictions|historical|live}?fmt=csv|pdf
POST   /seed-dataset?kind=mathura|bareilly|combined
```

Live contract (matches your notebook):

```python
import requests
requests.post("http://localhost:8000/live-api/readings",
              json={"meter_id": "SMART_METER_001", "energy_consumption": 148.25})
requests.get("http://localhost:8000/live-api/readings").json()
```

## 7. Storage

* `smartgrid.db` (SQLite, created automatically) — `live_readings`, `datasets`, `model_runs`.
* `uploads/` — the original uploaded files, unchanged.
* `models/` — one joblib bundle per dataset × algorithm, so every model is saved separately.

## 8. Running in Google Colab

See `Smart_Grid_Platform_Colab.ipynb` (included). In short:

```python
!git clone <your-repo>            # or upload the smartgrid/ folder to Colab
%cd smartgrid
!pip -q install -r requirements.txt
!nohup uvicorn main:app --host 0.0.0.0 --port 8000 &
```

Then expose it with a tunnel and open the printed URL:

```python
!pip -q install pyngrok
from pyngrok import ngrok
print(ngrok.connect(8000, bind_tls=True).public_url)
```

Do not open `127.0.0.1:8000` in Colab — it runs on the VM, not in your browser.

## 9. Public deployment

* **Any container/PaaS** (Render, Railway, Fly.io, Azure App Service, EC2):
  start command `uvicorn main:app --host 0.0.0.0 --port $PORT`; install
  `requirements.txt`; mount a volume for `smartgrid.db`, `uploads/`, `models/`
  if you need persistence across restarts.
* **Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

* Static assets are self-hosted (`static/vendor/`), so no CDN is required at runtime.
* SQLite is single-file; for multi-worker deployments run one worker or move the
  live table to Postgres.

## 10. Project layout

```
smartgrid/
├── main.py           FastAPI app: routes, validation, reports
├── engine.py         ingestion, preprocessing, features, 3 models, aggregation, forecast
├── location.py       OWID country series (cached download)
├── db.py             SQLite: live readings, datasets, model runs
├── reports.py        PDF builders (ReportLab)
├── serve.py          launcher that caches OWID then starts uvicorn
├── templates/index.html
├── static/css/app.css, static/js/app.js, static/vendor/{bootstrap,chart.js}
├── data/             CEEW CSV files (+ cached OWID file)
└── uploads/ models/ exports/
```
