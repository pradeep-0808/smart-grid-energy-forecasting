# Demo walkthrough (viva / reviewer script)

Everything below is clickable in the running app. Nothing is pre-baked: metrics are
computed when you press the button.

## 0. Start

```bash
cd smartgrid
pip install -r requirements.txt
python3 serve.py          # → http://localhost:8000   (API docs: /docs)
```

## 1. Dataset-based path (the main demo, ~2 minutes)

1. **Home → "Load CEEW Mathura data"** — imports the bundled CEEW aggregated file
   (16,491 meter-day rows → 653 daily records) and trains all three models.
   Expect: `Linear Regression` chosen as best with MAE ≈ 11.98 kWh, R² ≈ 0.684.
2. **Model Comparison** — the table shows all three models with MAE / RMSE / R² / MAPE
   on the same 127-row test period; the best row is highlighted and matches the
   automatic selection. Toggle *MAE / RMSE / R²* to re-render the bar chart.
3. **ML Predictions** — actual vs predicted over the hold-out; switch between
   Linear Regression, Decision Tree and Random Forest from the tabs. Show the
   residual histogram and download the predictions CSV.
4. **Historical Analysis** — click Hourly: the app refuses (source is daily) and
   tells you why. Daily/Weekly/Monthly/Yearly aggregate correctly; weekly total ≈
   sum of the 7 daily totals (verify in the bucket table).
5. **Future Forecast** — model = best, interval Daily, horizon 7 → recursive
   forecast + history-vs-future chart + peak period; CSV and PDF both work.
6. **Reports** — every card downloads a real file generated server-side.

## 2. Upload path (shows preprocessing honestly)

1. **Dataset Upload** → drop a CSV/XLSX (e.g. an hourly meter export).
2. The profile shows rows, columns, dtypes, first 10 records, missing cells,
   duplicate rows and the date range, plus the auto-detected timestamp and
   consumption columns. If detection is uncertain the app asks you to choose.
3. **Preprocess & validate** → reports grain, duplicates collapsed, gaps
   interpolated, invalid rows dropped. Try *Cumulative reading* on a meter file to
   see differences being taken first.
4. **Train 3 models** → metrics appear; a too-short series is refused with the row
   count and the minimum required (20 feature rows).

## 3. Location path

1. **Location Explorer** → search `India` → the OWID series loads (1985–2025,
   electricity generation TWh) with its source, licence and coverage.
2. **Import as dataset** → the series becomes a dataset. Training is then
   **refused** ("only 11 feature rows remain… at least 20 needed") — this is the
   key honesty point: annual country data cannot support three time-series models,
   and the app says so instead of inventing a score.

## 4. Live monitoring

```python
import requests
requests.post("http://localhost:8000/live-api/readings",
              json={"meter_id": "SMART_METER_001", "energy_consumption": 148.25})
```
Or use the **Live Monitoring** page form. Readings persist in `smartgrid.db`
(SQLite) and the chart auto-refreshes every 5 s. `GET /live-api/readings`,
`/live-api/history`, `/live-api/meters` all return the stored rows.

## 5. Regression tests

```bash
python3 tests.py      # 24 checks: column detection, split integrity, metric
                      # selection, aggregation equality, honest refusals,
                      # fast-predict equivalence, cumulative differencing, SQLite
```

## Verified numbers (bundled CEEW Mathura, daily grain)

| Model | MAE | RMSE | R² | MAPE |
|---|---|---|---|---|
| Linear Regression ★ | 11.983 | 14.754 | 0.6838 | 16.51 % |
| Decision Tree | 18.343 | 23.920 | 0.1689 | 23.10 % |
| Random Forest | 14.228 | 17.608 | 0.5496 | 19.50 % |

505 train rows / 127 test rows · chronological split · 17 features.
