"""Smart Grid Energy Consumption Forecasting & Analysis — FastAPI application.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import List, Optional

import pandas as pd
from fastapi import (Body, FastAPI, File, HTTPException, Query, Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import engine
import location
import reports

BASE = Path(__file__).resolve().parent
db.init_db()

app = FastAPI(
    title="Smart Grid Energy Consumption Forecasting & Analysis",
    description="Location-based and dataset-based energy analytics with "
                "Linear Regression, Decision Tree and Random Forest.",
    version="2.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
INDEX_HTML = BASE / "templates" / "index.html"


def fail(code: int, msg: str):
    raise HTTPException(status_code=code, detail=msg)


# -------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


# -------------------------------------------------------------------------- datasets
@app.post("/upload-dataset/", tags=["dataset"])
async def upload_dataset(file: UploadFile = File(...)):
    name = file.filename or "dataset.csv"
    if not name.lower().endswith((".csv", ".xlsx", ".xls", ".txt", ".tab")):
        fail(400, "Unsupported file type. Upload .csv or .xlsx (also .xls/.tab).")
    content = await file.read()
    if len(content) > 60 * 1024 * 1024:
        fail(413, "File larger than 60 MB.")
    try:
        meta = engine.register_dataset(name, content)
    except Exception as exc:                                  # noqa: BLE001
        fail(422, f"Could not read the file: {exc}")
    return {"message": "Dataset uploaded.", "dataset": meta,
            "detection": meta["detection"]}


@app.get("/datasets", tags=["dataset"])
def datasets():
    return {"datasets": engine.list_datasets()}


@app.get("/dataset/info", tags=["dataset"])
def dataset_info(dataset_id: str = Query(...)):
    try:
        entry = engine.get(dataset_id)
    except KeyError as exc:
        fail(404, str(exc))
    meta = dict(entry["meta"])
    meta.pop("path", None)
    df = entry["raw"]
    ts_col = (meta.get("detection") or {}).get("timestamp_column")
    if ts_col and ts_col in df.columns:
        parsed = pd.to_datetime(df[ts_col], errors="coerce", format="mixed")
        if parsed.notna().any():
            meta["date_range"] = {"start": parsed.min().strftime("%Y-%m-%d %H:%M:%S"),
                                  "end": parsed.max().strftime("%Y-%m-%d %H:%M:%S")}
    prep = entry.get("prepared")
    if not prep and str(meta.get("status")) in ("preprocessed", "trained"):
        try:
            engine.prepare(dataset_id)
            prep = engine.get(dataset_id).get("prepared")
        except Exception:                                     # noqa: BLE001
            prep = None
    if prep:
        meta["preprocessed"] = True
        meta["grain"] = prep["grain"]
        meta["report"] = prep["report"]
    return meta


@app.get("/dataset/preview", tags=["dataset"])
def dataset_preview(dataset_id: str = Query(...),
                    timestamp_column: Optional[str] = None,
                    consumption_column: Optional[str] = None):
    try:
        return engine.preview(dataset_id, timestamp_column, consumption_column)
    except KeyError as exc:
        fail(404, str(exc))
    except Exception as exc:                                  # noqa: BLE001
        fail(400, str(exc))


class PreprocessRequest(BaseModel):
    dataset_id: str
    timestamp_column: Optional[str] = None
    consumption_column: Optional[str] = None
    cumulative_meter: bool = Field(False, description="Source holds cumulative meter "
                                                      "readings; differences are taken first.")
    duplicate_policy: str = Field("sum", description="'sum' or 'mean' for values sharing "
                                                     "the same timestamp")


@app.post("/preprocess/", tags=["dataset"])
def preprocess(req: PreprocessRequest):
    try:
        return engine.prepare(req.dataset_id, req.timestamp_column, req.consumption_column,
                              req.cumulative_meter, req.duplicate_policy)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))


class TrainRequest(BaseModel):
    dataset_id: str
    grain_override: Optional[str] = Field(None, description="hourly | daily | weekly | "
                                                            "monthly | annual")


@app.post("/train-models/", tags=["ml"])
def train_models(req: TrainRequest):
    try:
        engine.prepared(req.dataset_id)
        run = engine.train(req.dataset_id, req.grain_override)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))
    return {"message": "Three models trained on the same split.",
            "run": run}


@app.get("/model-comparison/", tags=["ml"])
def model_comparison(dataset_id: str = Query(...)):
    try:
        run = engine.get_run(dataset_id)
    except KeyError as exc:
        fail(404, str(exc))
    return {
        "dataset_id": dataset_id,
        "models": engine.MODEL_NAMES,
        "best_model": run["best_model"],
        "metrics": run["metrics"],
        "split": {"train_rows": run["train_rows"], "test_rows": run["test_rows"],
                  "train_period": run["train_period"], "test_period": run["test_period"],
                  "note": run["split_note"]},
        "feature_importance": run["feature_importance"],
        "predictions": {"dates": run["dates"], "actual": run["actual"],
                        "models": run["predictions"], "residuals": run["residuals"]},
        "trained_at": run["trained_at"],
    }


class PredictRequest(BaseModel):
    dataset_id: str
    model: str = "best"
    horizon: int = Field(7, ge=1, le=365)
    interval: str = Field("daily", description="hourly | daily | weekly | monthly | yearly")


@app.post("/predict/", tags=["ml"])
@app.post("/forecast/", tags=["ml"])
def predict(req: PredictRequest):
    try:
        result = engine.forecast(req.dataset_id, req.model, req.horizon, req.interval)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))
    if not result.get("available", True):
        return JSONResponse(status_code=200, content=result)
    return result


@app.get("/forecast/history-vs-prediction", tags=["ml"])
def history_vs_prediction(dataset_id: str = Query(...), model: str = "best",
                          horizon: int = 7, interval: str = "daily", tail: int = 60):
    try:
        return engine.history_vs_forecast(dataset_id, model, horizon, interval, tail)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))


# -------------------------------------------------------------------------- historical
def _historical(interval: str, dataset_id: str, start: Optional[str], end: Optional[str]):
    try:
        return engine.aggregate(dataset_id, interval, start, end)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))


for _iv in ("hourly", "daily", "weekly", "monthly", "yearly"):


    def _make(iv):
        def endpoint(dataset_id: str = Query(...), start: Optional[str] = None,
                     end: Optional[str] = None):
            return _historical(iv, dataset_id, start, end)
        return endpoint


    app.add_api_route(f"/historical/{_iv}", _make(_iv), methods=["GET"], tags=["historical"],
                      name=f"historical_{_iv}")


@app.get("/historical/distribution", tags=["historical"])
def historical_distribution(dataset_id: str = Query(...), bins: int = 24):
    try:
        return engine.distribution(dataset_id, bins)
    except KeyError as exc:
        fail(404, str(exc))


# -------------------------------------------------------------------------- location
@app.get("/location/search", tags=["location"])
def location_search(q: str = Query("", min_length=1)):
    if not location.available():
        fail(503, "The OWID energy dataset is not cached on this server. "
                  "Set OWID_LOCAL_FILE or allow the server to download it.")
    try:
        results = location.search(q)
    except Exception as exc:                                  # noqa: BLE001
        fail(502, f"Energy data source unavailable: {exc}")
    return {"query": q, "results": results,
            "source": "Our World in Data — Energy dataset"}


@app.get("/location/energy-data", tags=["location"])
def location_energy_data(country: str = Query(...),
                         metric: str = Query("electricity_generation"),
                         start_year: Optional[int] = None, end_year: Optional[int] = None):
    if not location.available():
        fail(503, "The OWID energy dataset is not cached on this server.")
    data = location.energy_data(country, metric, start_year, end_year)
    if not data["found"]:
        return JSONResponse(status_code=404, content=data)
    return data


class LocationImportRequest(BaseModel):
    country: str
    metric: str = "electricity_generation"
    start_year: Optional[int] = None
    end_year: Optional[int] = None


@app.post("/location/import", tags=["location"])
def location_import(req: LocationImportRequest):
    """Bring a country's OWID series in as a dataset so the ML pipeline can run on it."""
    if not location.available():
        fail(503, "The OWID energy dataset is not cached on this server.")
    data = location.energy_data(req.country, req.metric, req.start_year, req.end_year)
    if not data["found"]:
        fail(404, f"No data for '{req.country}'.")
    if not data["series"]:
        fail(404, f"No {data['metric_label']} values for {req.country}.")
    label = f"{data['location']['country']}_{req.metric}".lower().replace(" ", "_")
    dataset_id = engine.import_external_series(data["series"], label,
                                               f"OWID — {data['metric_label']}")
    prepared = engine.prepare(dataset_id)
    trainable, reason = True, None
    try:
        engine.train(dataset_id)
    except ValueError as exc:
        trainable, reason = False, str(exc)
    return {"message": "Location data imported as a dataset.",
            "dataset_id": dataset_id, "prepared": prepared,
            "trainable": trainable, "train_error": reason,
            "source": data["source"], "coverage": data["coverage"],
            "note": data["note"]}


# -------------------------------------------------------------------------- bundled data
@app.post("/seed-dataset", tags=["dataset"])
def seed_dataset(kind: str = Query("mathura", pattern="^(mathura|bareilly|combined)$")):
    """Load the bundled CEEW aggregated smart-meter files as a dataset and train."""
    files = {"mathura": ["ceew_mathura.csv"], "bareilly": ["ceew_bareilly.csv"],
             "combined": ["ceew_mathura.csv", "ceew_bareilly.csv"]}[kind]
    frames = [pd.read_csv(BASE / "data" / f) for f in files]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    tmp_id = f"seed_{kind}"
    path = engine.UPLOADS / f"{tmp_id}.csv"
    df.to_csv(path, index=False)
    meta = engine.import_local_file(path, f"CEEW {kind} aggregated.csv")
    prepared = engine.prepare(meta["id"])
    run, error = None, None
    try:
        run = engine.train(meta["id"])
    except ValueError as exc:
        error = str(exc)
    return {"dataset": meta, "prepared": prepared, "run": run, "train_error": error}


# -------------------------------------------------------------------------- live API
class LiveReading(BaseModel):
    meter_id: str = Field(..., min_length=1, max_length=64)
    energy_consumption: float = Field(..., ge=0)
    status: Optional[str] = "ONLINE"
@app.post("/live-api/readings", tags=["live"])
def post_live_reading(reading: LiveReading):
    if reading.energy_consumption < 0:
        fail(400, "Energy consumption cannot be negative.")
    saved = db.add_reading(reading.meter_id.strip(), float(reading.energy_consumption),
                           (reading.status or "ONLINE").upper())
    return {"status": "success", "message": "Live electricity reading received.",
            "data": saved}

_SIMULATOR_POSITIONS = {}

@app.post("/live-api/simulator/start", tags=["live"])
def start_dataset_simulator(dataset_id: str = Query(...)):
    try:
        prep = engine.prepared(dataset_id)
        entry = engine.get(dataset_id)
    except (KeyError, ValueError) as exc:
        fail(400, str(exc))

    series = prep["series"]

    if series.empty:
        fail(400, "No valid records available for simulation.")

    _SIMULATOR_POSITIONS[dataset_id] = 0

    return {
        "status": "ready",
        "dataset_id": dataset_id,
        "filename": entry["meta"]["filename"],
        "records": len(series),
        "grain": prep["grain"],
        "message": "Dataset simulator is ready."
    }

@app.post("/live-api/simulator/next", tags=["live"])
def next_simulated_reading(dataset_id: str = Query(...)):
    if dataset_id not in _SIMULATOR_POSITIONS:
        fail(400, "Start the simulator first.")

    try:
        prep = engine.prepared(dataset_id)
        entry = engine.get(dataset_id)
    except (KeyError, ValueError) as exc:
        fail(400, str(exc))

    series = prep["series"]
    position = _SIMULATOR_POSITIONS[dataset_id]

    if position >= len(series):
        return {
            "status": "completed",
            "message": "All dataset records have been simulated."
        }

    record = series.iloc[position]

    meter_id = "SIM_" + dataset_id[:8].upper()
    energy = float(record["kwh"])
    source_timestamp = pd.Timestamp(record["dt"]).isoformat()

    saved = db.add_reading(
        meter_id,
        energy,
        "ONLINE",
        source=f"dataset_simulator:{entry['meta']['filename']}"
    )

    _SIMULATOR_POSITIONS[dataset_id] = position + 1

    return {
        "status": "success",
        "data": saved,
        "source_timestamp": source_timestamp,
        "progress": {
            "current": position + 1,
            "total": len(series)
        }
    }

@app.get("/live-api/readings", tags=["live"])
def get_live_readings(meter_id: Optional[str] = None, limit: int = Query(50, ge=1, le=1000),
                      start: Optional[str] = None, end: Optional[str] = None):
    rows = db.list_readings(meter_id, limit, start, end)
    stats = db.reading_stats()
    return {"total_readings": stats["n"], "returned": len(rows),
            "meters": stats["m"], "readings": rows,
            "last_reading_at": stats["last"],
            "server_time": db.now()}


@app.get("/live-api/history", tags=["live"])
def get_live_history(meter_id: Optional[str] = None, limit: int = Query(200, ge=1, le=2000),
                     start: Optional[str] = None, end: Optional[str] = None):
    rows = db.list_readings(meter_id, limit, start, end)
    return {"readings": rows, "count": len(rows), "stored_in": "SQLite (smartgrid.db)",
            "server_time": db.now()}


@app.get("/live-api/meters", tags=["live"])
def live_meters():
    ms = db.meters()
    return {"meters": ms, "count": len(ms), "stats": db.reading_stats()}


@app.delete("/live-api/readings", tags=["live"])
def delete_live_readings(meter_id: Optional[str] = None):
    db.clear_readings(meter_id)
    return {"message": "Live readings cleared." + (f" Meter {meter_id}." if meter_id else "")}


# -------------------------------------------------------------------------- dashboard
@app.get("/dashboard/summary", tags=["dashboard"])
def dashboard_summary(dataset_id: Optional[str] = None):
    datasets = engine.list_datasets()
    live = db.reading_stats()
    active = db.meters()
    payload = {"datasets": datasets, "live": live, "meters": active}

    target = dataset_id or next((d["id"] for d in datasets if d["trained"]),
                                datasets[0]["id"] if datasets else None)
    if not target:
        return payload
    try:
        prep = engine.prepared(target)
        daily = engine.aggregate(target, "daily")
        weekly = engine.aggregate(target, "weekly")
        monthly = engine.aggregate(target, "monthly")
    except Exception as exc:                                  # noqa: BLE001
        payload["dataset_error"] = str(exc)
        return payload

    run = None
    try:
        run = engine.get_run(target)
    except KeyError:
        pass

    summary = {
        "dataset_id": target,
        "filename": engine.get(target)["meta"]["filename"],
        "grain": prep["grain"],
        "total_kwh": daily["summary"]["total_kwh"],
        "average_kwh": prep["report"].get("observations_after_resample") and
                       round(float(prep["base"]["kwh"].mean()), 2),
        "peak": prep["base"].loc[prep["base"]["kwh"].idxmax()].to_dict(),
        "records": prep["report"]["observations_after_resample"],
        "period": daily["summary"]["period"],
        "daily": daily["buckets"][-60:],
        "weekly": weekly["buckets"][-16:] if weekly["available"] else [],
        "monthly": monthly["buckets"][-12:] if monthly["available"] else [],
        "best_model": run["best_model"] if run else None,
        "metrics": run["metrics"] if run else None,
    }
    summary["peak"]["dt"] = pd.Timestamp(summary["peak"]["dt"]).strftime("%Y-%m-%d %H:%M")
    payload["summary"] = summary
    return payload


# -------------------------------------------------------------------------- reports
@app.get("/reports/comparison", tags=["reports"])
def report_comparison(dataset_id: str = Query(...), fmt: str = "csv"):
    try:
        run = engine.get_run(dataset_id)
    except KeyError as exc:
        fail(404, str(exc))
    name = engine.get(dataset_id)["meta"]["filename"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Model", "MAE (kWh)", "RMSE (kWh)", "R2", "MAPE (%)", "Test rows"])
        for m in engine.MODEL_NAMES:
            mm = run["metrics"][m]
            w.writerow([m, mm["MAE"], mm["RMSE"], mm["R2"], mm["MAPE"], mm["n_test"]])
        w.writerow([])
        w.writerow(["Best model (lowest MAE)", run["best_model"]])
        w.writerow(["Train rows", run["train_rows"]])
        w.writerow(["Test rows", run["test_rows"]])
        w.writerow(["Split", run["split_note"]])
        return _csv_response(buf.getvalue(), f"model_comparison_{name}.csv")
    return reports.comparison_pdf(run, name)


@app.get("/reports/predictions", tags=["reports"])
def report_predictions(dataset_id: str = Query(...), model: str = "best",
                       horizon: int = 7, interval: str = "daily", fmt: str = "csv"):
    try:
        run = engine.get_run(dataset_id)
        fc = engine.forecast(dataset_id, model, horizon, interval)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))
    if not fc.get("available", True):
        fail(400, fc["message"])
    name = engine.get(dataset_id)["meta"]["filename"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Forecast interval", interval])
        w.writerow(["Model", fc["model"]])
        w.writerow(["Generated at", db.now()])
        w.writerow([])
        w.writerow(["Period", "Predicted consumption (kWh)"])
        for p in fc["points"]:
            w.writerow([p["label"], p["predicted_kwh"]])
        w.writerow([])
        w.writerow(["Total predicted kWh", fc["summary"]["total_predicted_kwh"]])
        w.writerow(["Average predicted kWh", fc["summary"]["average_predicted_kwh"]])
        w.writerow(["Peak", fc["summary"]["peak"]["label"], fc["summary"]["peak"]["predicted_kwh"]])
        return _csv_response(buf.getvalue(), f"forecast_{name}.csv")
    return reports.forecast_pdf(fc, name, run["metrics"][fc["model"]])


@app.get("/reports/historical", tags=["reports"])
def report_historical(dataset_id: str = Query(...), interval: str = "monthly",
                      fmt: str = "csv", start: Optional[str] = None, end: Optional[str] = None):
    try:
        agg = engine.aggregate(dataset_id, interval, start, end)
    except KeyError as exc:
        fail(404, str(exc))
    except ValueError as exc:
        fail(400, str(exc))
    if not agg["available"]:
        fail(400, agg["message"])
    name = engine.get(dataset_id)["meta"]["filename"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([f"{interval.capitalize()} analysis", name])
        w.writerow(["Source grain", agg["grain"]])
        w.writerow(["Period", agg["summary"]["period"]["start"], agg["summary"]["period"]["end"]])
        w.writerow([])
        w.writerow(["Period", "Total kWh", "Average kWh", "Records"])
        for b in agg["buckets"]:
            w.writerow([b["label"], b["total_kwh"], b["avg_kwh"], b["records"]])
        return _csv_response(buf.getvalue(), f"historical_{interval}_{name}.csv")
    return reports.historical_pdf(agg, name)


@app.get("/reports/dataset", tags=["reports"])
def report_dataset(dataset_id: str = Query(...), fmt: str = "csv"):
    entry = engine.get(dataset_id)
    df = entry["raw"]
    name = entry["meta"]["filename"]
    if fmt == "csv":
        return _csv_response(df.to_csv(index=False), f"dataset_{name}")
    return reports.dataset_pdf(engine.preview(dataset_id), name)


@app.get("/reports/preprocessed", tags=["reports"])
def report_preprocessed(dataset_id: str = Query(...)):
    prep = engine.prepared(dataset_id)
    name = engine.get(dataset_id)["meta"]["filename"]
    return _csv_response(prep["base"].to_csv(index=False), f"preprocessed_{name}")


@app.get("/reports/live", tags=["reports"])
def report_live(fmt: str = "csv"):
    rows = db.list_readings(limit=1000)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Meter ID", "Consumption (kWh)", "Timestamp", "Status", "Source"])
        for r in rows:
            w.writerow([r["meter_id"], r["energy_consumption"], r["timestamp"],
                        r["status"], r["source"]])
        return _csv_response(buf.getvalue(), "live_readings.csv")
    return reports.live_pdf(rows)


def _csv_response(text: str, filename: str):
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "datasets": len(engine.list_datasets()),
            "live_readings": db.reading_stats()["n"], "server_time": db.now()}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):      # noqa: ANN001
    return JSONResponse(status_code=500,
                        content={"detail": f"Unexpected server error: {exc}"})
