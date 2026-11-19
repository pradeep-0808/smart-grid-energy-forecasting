"""Data + machine-learning engine for the Smart Grid platform.

Pipeline (mirrors the project notebook, extended for arbitrary uploads):
  read file -> detect timestamp/consumption columns -> clean & deduplicate ->
  build interval series -> resample to a continuous base frequency ->
  engineer lag/calendar/rolling features -> chronological 80/20 split ->
  Linear Regression / Decision Tree / Random Forest -> MAE, RMSE, R2 ->
  recursive multi-step forecast.

Only those three algorithms are used anywhere in this project.
"""
from __future__ import annotations

import io
import json
import math
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.tree import DecisionTreeRegressor

import db

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
MODELS = BASE / "models"
for d in (UPLOADS, MODELS):
    d.mkdir(parents=True, exist_ok=True)

MODEL_NAMES = ["Linear Regression", "Decision Tree", "Random Forest"]

# in-memory registry (source of truth for the file on disk is SQLite + uploads dir)
_DATASETS: dict[str, dict] = {}
_RUNS: dict[str, dict] = {}

TS_HINTS = ["timestamp", "datetime", "date_time", "date", "time", "ts", "reading_time"]
VAL_HINTS = ["kwh", "energy", "consumption", "usage", "load", "reading", "t_kwh",
             "units", "demand", "value", "electricity"]


# --------------------------------------------------------------------------
# file reading
# --------------------------------------------------------------------------
def read_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        last_err = None
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            for sep in (None, ",", ";", "\t", "|"):
                try:
                    df = pd.read_csv(path, sep=sep, engine="python", encoding=enc)
                    if df.shape[1] >= 1 and len(df) > 0:
                        return _tidy_columns(df)
                except Exception as e:      # noqa: BLE001
                    last_err = e
        raise ValueError(f"Could not parse file: {last_err}")
    return _tidy_columns(df)


def _tidy_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]
    # drop fully empty columns / rows that pandas creates from trailing separators
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    return df


def _score_column(col: str, values: pd.Series, hints) -> float:
    name = col.lower().replace("_", " ").strip()
    score = 0.0
    for h in hints:
        if name == h.replace("_", " "):
            score += 3
        elif h.replace("_", " ") in name:
            score += 1.5
    numeric = pd.to_numeric(values, errors="coerce")
    numeric_ratio = numeric.notna().mean()
    if pd.api.types.is_numeric_dtype(values) or numeric_ratio > 0.8:
        score += 1
    return score


def detect_columns(df: pd.DataFrame) -> dict:
    ts_scores, val_scores, parsed = {}, {}, {}
    for col in df.columns:
        s = df[col]
        parsed_ts = pd.to_datetime(s, errors="coerce", format="mixed")
        ratio = parsed_ts.notna().mean()
        parsed[col] = parsed_ts
        if ratio > 0.7 and not pd.api.types.is_numeric_dtype(s):
            ts_scores[col] = 3 + ratio
        ts_scores[col] = ts_scores.get(col, 0) + _score_column(col, s, TS_HINTS)
        if pd.to_numeric(s, errors="coerce").notna().mean() > 0.7:
            val_scores[col] = _score_column(col, s, VAL_HINTS)
    ts_col = max(ts_scores, key=ts_scores.get) if ts_scores else None
    val_candidates = {k: v for k, v in val_scores.items() if v > 0 and k != ts_col}
    val_col = max(val_candidates, key=val_candidates.get) if val_candidates else None
    return {
        "timestamp_column": ts_col,
        "consumption_column": val_col,
        "timestamp_candidates": sorted([c for c, s in ts_scores.items() if s >= 3], key=lambda c: -ts_scores[c]),
        "consumption_candidates": sorted([c for c in val_candidates], key=lambda c: -val_candidates[c]),
        "confident": bool(ts_col and val_col and ts_scores.get(ts_col, 0) >= 3
                          and val_scores.get(val_col, 0) >= 3),
    }


# --------------------------------------------------------------------------
# datasets
# --------------------------------------------------------------------------
def register_dataset(filename: str, content: bytes) -> dict:
    dataset_id = uuid.uuid4().hex[:12]
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._- ")[:80] or "dataset.csv"
    path = UPLOADS / f"{dataset_id}_{safe}"
    path.write_bytes(content)
    df = read_any(path)
    det = detect_columns(df)
    meta = {
        "id": dataset_id,
        "filename": safe,
        "path": str(path),
        "uploaded_at": db.now(),
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "status": "uploaded",
        "detection": det,
    }
    _DATASETS[dataset_id] = {"meta": meta, "raw": df}
    db.upsert_dataset(meta)
    return meta


def import_local_file(path: Path, label: str) -> dict:
    """Register a bundled/derived dataset (CEEW seed, OWID country import)."""
    content = path.read_bytes()
    meta = register_dataset(label or path.name, content)
    return meta


def get(dataset_id: str) -> dict:
    if dataset_id in _DATASETS:
        return _DATASETS[dataset_id]
    # rehydrate after a server restart: the file + metadata live in SQLite/uploads
    for row in db.list_datasets():
        if row["id"] == dataset_id:
            path = Path(row["path"])
            if not path.exists():
                break
            df = read_any(path)
            meta = dict(row)
            meta["detection"] = detect_columns(df)
            _DATASETS[dataset_id] = {"meta": meta, "raw": df}
            return _DATASETS[dataset_id]
    raise KeyError(f"Unknown dataset '{dataset_id}'. Upload a dataset first.")


def raw_frame(dataset_id: str) -> pd.DataFrame:
    return get(dataset_id)["raw"]


def preview(dataset_id: str, ts_override=None, val_override=None) -> dict:
    df = raw_frame(dataset_id)
    meta = get(dataset_id)["meta"]
    det = detect_columns(df)
    ts_col = ts_override or det["timestamp_column"]
    val_col = val_override or det["consumption_column"]

    dtypes = {c: str(t) for c, t in df.dtypes.items()}
    parsed_ts = pd.to_datetime(df[ts_col], errors="coerce", format="mixed") if ts_col else None
    date_range = None
    if parsed_ts is not None and parsed_ts.notna().any():
        date_range = {"start": parsed_ts.min().strftime("%Y-%m-%d %H:%M:%S"),
                      "end": parsed_ts.max().strftime("%Y-%m-%d %H:%M:%S")}
    num_cols = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.7]

    head = df.head(10).copy()
    for c in head.columns:
        head[c] = head[c].astype(str).str.slice(0, 60)
    missing = {c: int(v) for c, v in df.isna().sum().items() if v > 0}

    return {
        "dataset": {"id": meta["id"], "filename": meta["filename"], "rows": int(len(df)),
                    "columns": int(df.shape[1]), "uploaded_at": meta["uploaded_at"]},
        "columns": list(df.columns),
        "dtypes": dtypes,
        "numeric_columns": num_cols,
        "detection": det,
        "selected": {"timestamp_column": ts_col, "consumption_column": val_col},
        "head": json.loads(head.to_json(orient="records")),
        "missing_values": missing,
        "total_missing_cells": int(df.isna().sum().sum()),
        "duplicate_rows": int(df.duplicated().sum()),
        "date_range": date_range,
    }


def import_external_series(rows, label, source_label) -> str:
    """Turn an external API series (e.g. OWID country data) into a dataset."""
    df = pd.DataFrame(rows)
    df = df.rename(columns={"date": "timestamp", "value": "energy_kwh"})
    dataset_id = uuid.uuid4().hex[:12]
    safe = "".join(ch for ch in label if ch.isalnum() or ch in "._- ")[:60] or "external"
    path = UPLOADS / f"{dataset_id}_{safe}.csv"
    df.to_csv(path, index=False)
    meta = {
        "id": dataset_id, "filename": path.name, "path": str(path), "uploaded_at": db.now(),
        "rows": int(len(df)), "columns": int(df.shape[1]), "status": "uploaded",
        "detection": {"timestamp_column": "timestamp", "consumption_column": "energy_kwh",
                      "confident": True, "timestamp_candidates": ["timestamp"],
                      "consumption_candidates": ["energy_kwh"]},
        "source": source_label,
    }
    _DATASETS[dataset_id] = {"meta": meta, "raw": df}
    db.upsert_dataset(meta)
    return dataset_id


def list_datasets() -> list:
    out = []
    for row in db.list_datasets():
        run = db.latest_run(row["id"])
        out.append({
            "id": row["id"], "filename": row["filename"], "rows": row["rows"],
            "columns": row["columns"], "uploaded_at": row["uploaded_at"],
            "status": row["status"], "trained": bool(run),
            "best_model": run["best_model"] if run else None,
        })
    for did, entry in _DATASETS.items():
        if not any(o["id"] == did for o in out):
            m = entry["meta"]
            out.append({"id": did, "filename": m["filename"], "rows": m["rows"],
                        "columns": m["columns"], "uploaded_at": m["uploaded_at"],
                        "status": m["status"], "trained": False, "best_model": None})
    return out


# --------------------------------------------------------------------------
# preprocessing
# --------------------------------------------------------------------------
def build_series(df: pd.DataFrame, ts_col: str, val_col: str, cumulative=False,
                 duplicate_policy="sum") -> tuple[pd.DataFrame, dict]:
    ts = pd.to_datetime(df[ts_col], errors="coerce", format="mixed")
    val = pd.to_numeric(df[val_col], errors="coerce")
    work = pd.DataFrame({"timestamp": ts, "value": val})
    report = {
        "input_rows": int(len(work)),
        "invalid_timestamps": int(work.timestamp.isna().sum()),
        "invalid_values": int(work.value.isna().sum()),
        "negative_values": int((work.value < 0).sum()),
    }
    work = work.dropna(subset=["timestamp", "value"])
    work.loc[work.value < 0, "value"] = np.nan
    work = work.dropna(subset=["value"])
    report["negative_values_removed"] = report["negative_values"]
    report["rows_after_cleaning"] = int(len(work))

    work = work.sort_values("timestamp")
    dup_ts = int(work.timestamp.duplicated().sum())
    report["duplicate_timestamps"] = dup_ts
    if dup_ts:
        agg = "sum" if (duplicate_policy == "sum" and not cumulative) else "mean"
        work = (work.groupby("timestamp", as_index=False)["value"].agg(agg))
        report["duplicate_policy"] = f"{agg} of values sharing a timestamp"

    if cumulative:
        work["value"] = work["value"].diff()
        report["cumulative_converted"] = True
        dropped = int(work.value.isna().sum())
        work = work.dropna(subset=["value"])
        work = work[work.value >= 0]
        report["cumulative_negative_steps_dropped"] = dropped

    work = work.rename(columns={"timestamp": "dt", "value": "kwh"})
    work = work.sort_values("dt").reset_index(drop=True)
    return work, report


def infer_grain(series: pd.DataFrame) -> str:
    if len(series) < 3:
        return "sparse"
    deltas = series["dt"].diff().dropna().dt.total_seconds() / 3600.0
    med = float(deltas.median())
    if med <= 1.5:
        return "hourly"
    if med <= 26:
        return "daily"
    if med <= 24 * 10:
        return "weekly"
    if med <= 24 * 45:
        return "monthly"
    if med <= 24 * 400:
        return "annual"
    return "sparse"


BASE_FREQ = {"hourly": "h", "daily": "D", "weekly": "W", "monthly": "MS", "annual": "YS"}


def prepare(dataset_id: str, ts_col=None, val_col=None, cumulative=False,
            duplicate_policy="sum") -> dict:
    entry = get(dataset_id)
    df = entry["raw"]
    det = entry["meta"].get("detection") or detect_columns(df)
    ts_col = ts_col or det.get("timestamp_column")
    val_col = val_col or det.get("consumption_column")
    if not ts_col or not val_col:
        raise ValueError("Could not identify the timestamp and consumption columns. "
                         "Choose them explicitly.")
    if ts_col not in df.columns or val_col not in df.columns:
        raise ValueError("Selected columns are not present in the dataset.")

    series, report = build_series(df, ts_col, val_col, cumulative, duplicate_policy)
    if len(series) < 5:
        raise ValueError("Fewer than 5 valid records remain after cleaning — "
                         "the dataset is too small to analyse.")

    grain = infer_grain(series)
    report["detected_grain"] = grain

    # continuous base series at the dataset's own natural frequency
    freq = BASE_FREQ.get(grain, "D")
    base = (series.set_index("dt")["kwh"].resample(freq).sum().to_frame("kwh"))
    base = base.asfreq(pd.tseries.frequencies.to_offset(freq))
    gaps = int(base["kwh"].isna().sum())
    if gaps:
        base["kwh"] = base["kwh"].interpolate(method="time", limit_direction="both")
    base = base.reset_index().rename(columns={"index": "dt"})
    base["dt"] = pd.to_datetime(base["dt"])
    base = base.dropna().reset_index(drop=True)
    report["gaps_interpolated"] = gaps
    report["observations_after_resample"] = int(len(base))
    report["unit"] = "kWh per interval (summed)"

    entry["prepared"] = {
        "series": series, "base": base, "grain": grain, "report": report,
        "columns": {"timestamp": ts_col, "consumption": val_col},
        "cumulative": bool(cumulative),
    }
    db.set_dataset_status(dataset_id, "preprocessed", rows=int(len(series)))
    return {"dataset_id": dataset_id, "grain": grain, "report": report,
            "columns": entry["prepared"]["columns"],
            "rows": int(len(series)), "base_rows": int(len(base)),
            "date_range": {"start": series.dt.min().strftime("%Y-%m-%d %H:%M"),
                           "end": series.dt.max().strftime("%Y-%m-%d %H:%M")}}


def prepared(dataset_id: str) -> dict:
    entry = get(dataset_id)
    if "prepared" not in entry:
        prepare(dataset_id)
    return entry["prepared"]


# --------------------------------------------------------------------------
# aggregation for historical analysis
# --------------------------------------------------------------------------
RULES = {"hourly": "h", "daily": "D", "weekly": "W-MON", "monthly": "MS", "yearly": "YS"}
GRAIN_RANK = {"hourly": 0, "daily": 1, "weekly": 2, "monthly": 3,
              "yearly": 4, "annual": 4, "sparse": 5}


def aggregate(dataset_id: str, interval: str, start=None, end=None) -> dict:
    prep = prepared(dataset_id)
    grain = prep["grain"]
    series = prep["series"].copy()

    if interval == "annual":
        interval = "yearly"
    if interval not in RULES:
        raise ValueError(f"interval must be one of {list(RULES)}")

    if GRAIN_RANK.get(grain, 5) > GRAIN_RANK.get(interval, 0):
        return {
            "available": False,
            "interval": interval,
            "grain": grain,
            "message": (f"The source data is {grain}-level, so {interval} totals cannot be "
                        f"derived without inventing detail. Choose an interval at or above "
                        f"{grain}, or upload a finer-grained dataset."),
            "buckets": [],
        }

    if start:
        series = series[series.dt >= pd.Timestamp(start)]
    if end:
        series = series[series.dt <= pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)]
    if series.empty:
        raise ValueError("No records fall inside the selected date range.")

    rule = RULES[interval]
    g = (series.set_index("dt")["kwh"].resample(rule)
         .agg(["sum", "mean", "count"]).reset_index())
    g = g[g["count"] > 0]

    if interval == "hourly":
        labels = [d.strftime("%Y-%m-%d %H:%M") for d in g["dt"]]
    elif interval == "daily":
        labels = [d.strftime("%Y-%m-%d") for d in g["dt"]]
    elif interval == "weekly":
        labels = ["W/%s" % d.strftime("%d %b %Y") for d in g["dt"]]
    elif interval == "monthly":
        labels = [d.strftime("%b %Y") for d in g["dt"]]
    else:
        labels = [str(d.year) for d in g["dt"]]

    buckets = [{"label": lab, "date": d.strftime("%Y-%m-%d %H:%M"),
                "total_kwh": round(float(s), 2), "avg_kwh": round(float(m), 2),
                "records": int(c)}
               for lab, d, s, m, c in zip(labels, g["dt"], g["sum"], g["mean"], g["count"])]
    totals = [b["total_kwh"] for b in buckets]
    peak_i = int(np.argmax(totals)) if totals else None
    low_i = int(np.argmin(totals)) if totals else None
    return {
        "available": True,
        "interval": interval,
        "grain": grain,
        "unit": "kWh",
        "buckets": buckets,
        "summary": {
            "buckets": len(buckets),
            "total_kwh": round(float(sum(totals)), 2),
            "average_kwh": round(float(np.mean(totals)), 2) if totals else None,
            "peak": buckets[peak_i] if peak_i is not None else None,
            "lowest": buckets[low_i] if low_i is not None else None,
            "period": {"start": series.dt.min().strftime("%Y-%m-%d"),
                       "end": series.dt.max().strftime("%Y-%m-%d")},
        },
        "note": ("Totals are sums of interval energy values (kWh). Average power for a bucket "
                 "is total / hours in the bucket." if interval == "hourly" else
                 "Totals are sums of recorded energy values in that bucket."),
    }


def distribution(dataset_id: str, bins=24) -> dict:
    prep = prepared(dataset_id)
    values = prep["series"]["kwh"].astype(float)
    counts, edges = np.histogram(values, bins=bins)
    return {"bins": [round(float(e), 1) for e in edges],
            "counts": [int(c) for c in counts],
            "min": round(float(values.min()), 2), "max": round(float(values.max()), 2),
            "mean": round(float(values.mean()), 2),
            "median": round(float(values.median()), 2)}


# --------------------------------------------------------------------------
# feature engineering + training
# --------------------------------------------------------------------------
def feature_spec(grain: str) -> dict:
    if grain == "hourly":
        return {"lags": [1, 2, 3, 24, 48, 168], "rolling": [3, 24], "std": [24],
                "calendar": ["hour", "day_of_week", "day", "month", "is_weekend"]}
    return {"lags": [1, 2, 3, 7, 14, 21, 30], "rolling": [3, 7, 14], "std": [7],
            "calendar": ["day", "day_of_week", "month", "day_of_year", "is_weekend"]}


def feature_names(grain: str) -> list:
    spec = feature_spec(grain)
    names = list(spec["calendar"])
    names += [f"lag_{l}" for l in spec["lags"]]
    names += [f"rolling_mean_{w}" for w in spec["rolling"]]
    names += [f"rolling_std_{w}" for w in spec["std"]]
    names += [f"rolling_max_{w}" for w in spec["std"]]
    return names


def add_features(df: pd.DataFrame, grain: str) -> pd.DataFrame:
    spec = feature_spec(grain)
    d = df.copy().sort_values("dt").reset_index(drop=True)
    d["day"] = d.dt.dt.day
    d["day_of_week"] = d.dt.dt.dayofweek
    d["month"] = d.dt.dt.month
    d["day_of_year"] = d.dt.dt.dayofyear
    d["is_weekend"] = (d.day_of_week >= 5).astype(int)
    d["hour"] = d.dt.dt.hour
    for lag in spec["lags"]:
        d[f"lag_{lag}"] = d["kwh"].shift(lag)
    for w in spec["rolling"]:
        d[f"rolling_mean_{w}"] = d["kwh"].shift(1).rolling(w).mean()
    for w in spec["std"]:
        d[f"rolling_std_{w}"] = d["kwh"].shift(1).rolling(w).std()
        d[f"rolling_max_{w}"] = d["kwh"].shift(1).rolling(w).max()
    return d.dropna().reset_index(drop=True)


def make_models():
    return {
        "Linear Regression": LinearRegression(),
        "Decision Tree": DecisionTreeRegressor(random_state=42, max_depth=None,
                                               min_samples_leaf=2),
        "Random Forest": RandomForestRegressor(n_estimators=300, max_depth=12,
                                               min_samples_leaf=2, random_state=42, n_jobs=-1),
    }



def _fast_predict(model, matrix: np.ndarray) -> np.ndarray:
    """Predict rows in one pass without scikit-learn's per-call validation overhead.

    For tree ensembles we call the compiled C predict on each fitted tree directly
    and average — identical values to ``model.predict`` (asserted in the tests) but
    ~10x faster, which matters for recursive forecasting where one call per step
    would otherwise take minutes.
    """
    trees = getattr(model, "estimators_", None)
    if trees is not None and len(trees):
        mat = np.ascontiguousarray(matrix, dtype=np.float32)
        total = None
        for tree in trees:
            p = tree.tree_.predict(mat)
            total = p if total is None else total + p
        return (total / len(trees)).ravel()
    return np.asarray(model.predict(matrix)).ravel()


def _simulate(model, features: list, spec: dict, seed: list, times: list) -> list:
    """Exact recursive one-step-ahead simulation.

    Step i's lag_1 is step i-1's prediction, so the chain is inherently sequential
    (batching it would change the numbers). We keep the loop but make each step
    cheap: a numpy buffer, features built by name in the trained order, and
    ``_fast_predict`` for the model call.
    """
    values = list(seed)
    preds = []
    for when in times:
        n = len(values)
        fmap = {
            "hour": when.hour, "day": when.day, "day_of_week": when.dayofweek,
            "month": when.month, "day_of_year": when.dayofyear,
            "is_weekend": int(when.dayofweek >= 5),
        }
        for lag in spec["lags"]:
            fmap[f"lag_{lag}"] = values[n - lag]
        for w in spec["rolling"]:
            window = values[n - w:n]
            fmap[f"rolling_mean_{w}"] = sum(window) / len(window)
        for w in spec["std"]:
            window = values[n - w:n]
            mu = sum(window) / len(window)
            var = sum((x - mu) ** 2 for x in window) / (len(window) - 1)
            fmap[f"rolling_std_{w}"] = var ** 0.5
            fmap[f"rolling_max_{w}"] = max(window)
        row = np.asarray([[fmap[f] for f in features]], dtype=float)
        pred = max(0.0, float(_fast_predict(model, row)[0]))
        values.append(pred)
        preds.append(pred)
    return preds


def metric_block(y_true, y_pred) -> dict:
    a, p = np.asarray(y_true, float), np.asarray(y_pred, float)
    mae = float(mean_absolute_error(a, p))
    rmse = float(math.sqrt(mean_squared_error(a, p)))
    r2 = float(r2_score(a, p)) if len(a) > 1 else float("nan")
    nz = np.abs(a) > 1e-9
    mape = float(np.mean(np.abs((a[nz] - p[nz]) / a[nz])) * 100) if nz.any() else None
    return {"MAE": round(mae, 4), "RMSE": round(rmse, 4), "R2": round(r2, 4),
            "MAPE": round(mape, 4) if mape is not None else None,
            "n_test": int(len(a))}


def train(dataset_id: str, grain_override=None) -> dict:
    prep = prepared(dataset_id)
    grain = grain_override or prep["grain"]
    source = prep["base"] if grain == prep["grain"] else (
        prep["base"].set_index("dt")["kwh"].resample("h").sum().reset_index()
        if grain == "hourly" else prep["base"])
    data = add_features(source, grain)

    if len(data) < 20:
        raise ValueError(
            f"Not enough history for three-model time-series training: only {len(data)} feature "
            f"rows remain at {grain} granularity, and at least 20 are needed. Upload a dataset "
            "with a longer or finer-grained series.")

    features = feature_names(grain)
    split = int(len(data) * 0.8)
    X, y = data[features], data["kwh"]
    X_tr, X_te = X.iloc[:split], X.iloc[split:]
    y_tr, y_te = y.iloc[:split], y.iloc[split:]

    models = make_models()
    table, preds = {}, {}
    for name, model in models.items():
        model.fit(X_tr, y_tr)
        pred = model.predict(X_te)
        preds[name] = pred
        table[name] = metric_block(y_te, pred)
        # single-row recursive forecasting is much faster without a thread pool
        if hasattr(model, "n_jobs"):
            model.n_jobs = 1
        joblib.dump({"model": model, "features": features, "grain": grain,
                     "dataset_id": dataset_id, "trained_at": db.now()},
                    MODELS / f"{dataset_id}__{name.replace(' ', '_').lower()}.pkl")

    best = min(table, key=lambda k: table[k]["MAE"])

    importance = {}
    for name in ("Decision Tree", "Random Forest"):
        m = models[name]
        importance[name] = sorted(
            [{"feature": f, "importance": round(float(v), 4)}
             for f, v in zip(features, m.feature_importances_)],
            key=lambda r: -r["importance"])

    residuals = {name: [round(float(a - b), 3) for a, b in zip(y_te, preds[name])]
                 for name in MODEL_NAMES}

    dates = [d.strftime("%Y-%m-%d %H:%M" if grain == "hourly" else "%Y-%m-%d")
             for d in data["dt"].iloc[split:]]

    run = {
        "dataset_id": dataset_id,
        "grain": grain,
        "features": features,
        "rows_total": int(len(data)),
        "train_rows": int(len(X_tr)),
        "test_rows": int(len(X_te)),
        "train_period": {"start": data["dt"].iloc[0].strftime("%Y-%m-%d %H:%M"),
                         "end": data["dt"].iloc[split - 1].strftime("%Y-%m-%d %H:%M")},
        "test_period": {"start": data["dt"].iloc[split].strftime("%Y-%m-%d %H:%M"),
                        "end": data["dt"].iloc[-1].strftime("%Y-%m-%d %H:%M")},
        "metrics": table,
        "best_model": best,
        "predictions": {name: [round(float(p), 3) for p in preds[name]] for name in MODEL_NAMES},
        "actual": [round(float(v), 3) for v in y_te],
        "dates": dates,
        "residuals": residuals,
        "feature_importance": importance,
        "trained_at": db.now(),
        "split_note": "Chronological 80/20 split — no shuffling, so the test period is strictly "
                      "later than the training period.",
    }
    _RUNS[dataset_id] = run
    db.save_run(dataset_id, int(len(data)), len(features), best, json.dumps(run))
    db.set_dataset_status(dataset_id, "trained", rows=int(len(data)))
    return run


def get_run(dataset_id: str) -> dict:
    if dataset_id in _RUNS:
        return _RUNS[dataset_id]
    row = db.latest_run(dataset_id)
    if not row:
        raise KeyError("No trained models for this dataset yet. Run training first.")
    run = json.loads(row["payload"])
    _RUNS[dataset_id] = run
    return run


def load_model(dataset_id: str, model_name: str):
    path = MODELS / f"{dataset_id}__{model_name.replace(' ', '_').lower()}.pkl"
    if not path.exists():
        raise KeyError(f"{model_name} has not been trained for this dataset.")
    return joblib.load(path)


# --------------------------------------------------------------------------
# forecasting
# --------------------------------------------------------------------------
def _row_for(series_values: pd.Series, when: pd.Timestamp, grain: str) -> dict:
    spec = feature_spec(grain)
    row = {
        "day": when.day, "day_of_week": when.dayofweek, "month": when.month,
        "day_of_year": when.dayofyear, "is_weekend": int(when.dayofweek >= 5),
        "hour": when.hour,
    }
    for lag in spec["lags"]:
        row[f"lag_{lag}"] = float(series_values.iloc[-lag])
    for w in spec["rolling"]:
        row[f"rolling_mean_{w}"] = float(series_values.tail(w).mean())
    for w in spec["std"]:
        row[f"rolling_std_{w}"] = float(series_values.tail(w).std())
        row[f"rolling_max_{w}"] = float(series_values.tail(w).max())
    return row


_FC_CACHE: dict = {}


def forecast(dataset_id: str, model_name: str = "best", horizon: int = 7,
             interval: str = "daily") -> dict:
    key = (dataset_id, model_name, int(horizon), interval)
    if key in _FC_CACHE:
        return _FC_CACHE[key]
    result = _forecast_uncached(dataset_id, model_name, horizon, interval)
    if len(_FC_CACHE) > 24:
        _FC_CACHE.clear()
    _FC_CACHE[key] = result
    return result


def _forecast_uncached(dataset_id: str, model_name: str = "best", horizon: int = 7,
                       interval: str = "daily") -> dict:
    prep_check = prepared(dataset_id)
    if prep_check["grain"] != "hourly" and interval == "hourly":
        return {"available": False, "interval": interval, "model": model_name,
                "message": (f"The data is {prep_check['grain']}-level, so hour-level predictions "
                            "cannot be produced without inventing intra-day detail. Upload "
                            "hourly meter data to enable 24-hour forecasts.")}
    run = get_run(dataset_id)
    if model_name in (None, "", "best", "Best Performing Model"):
        model_name = run["best_model"]
    if model_name not in MODEL_NAMES:
        raise ValueError(f"model must be one of {MODEL_NAMES} or 'best'")

    bundle = load_model(dataset_id, model_name)
    model, features, grain = bundle["model"], bundle["features"], bundle["grain"]
    prep = prepared(dataset_id)
    base = prep["base"].copy()

    step = {"hourly": "h", "daily": "D", "weekly": "W", "monthly": "MS",
            "yearly": "YS", "annual": "YS"}.get(interval)
    if step is None:
        raise ValueError("interval must be hourly, daily, weekly, monthly or yearly")

    if grain == "hourly" and interval != "hourly":
        # fine-grained model, coarser output: forecast hourly then aggregate
        steps = {"daily": 24, "weekly": 24 * 7, "monthly": 24 * 30,
                 "yearly": 24 * 365}[interval] * max(1, math.ceil(horizon / (
                     1 if interval == "daily" else 7 if interval == "weekly"
                     else 30 if interval == "monthly" else 365)))
    elif grain != "hourly" and interval == "hourly":
        return {"available": False, "model": model_name, "interval": interval,
                "message": ("The trained model works on "
                            f"{grain} data, so hour-level predictions cannot be produced "
                            "without inventing intra-day detail. Upload hourly meter data "
                            "to enable 24-hour forecasts.")}

    freq_delta = {"hourly": pd.Timedelta(hours=1), "daily": pd.Timedelta(days=1)}.get(
        grain, None)

    if grain == "hourly":
        total_steps = {"hourly": horizon, "daily": horizon * 24, "weekly": horizon * 24 * 7,
                       "monthly": horizon * 24 * 30, "yearly": horizon * 24 * 365}[interval]
    else:
        total_steps = {"daily": horizon, "weekly": horizon * 7, "monthly": horizon * 30,
                       "yearly": horizon * 365}[interval]

    if total_steps > 9000:
        raise ValueError(
            f"This horizon needs {total_steps:,} recursive steps from a {grain} model, "
            "which is beyond the 9,000-step limit. Reduce the horizon or choose a coarser "
            "forecast interval.")

    # --- vectorised recursive simulation (one tree-pass per batch, not per step) ---
    spec = feature_spec(grain)
    seed = list(base["kwh"].astype(float).to_numpy())
    min_needed = max(spec["lags"]) + 1
    if len(seed) < min_needed:
        raise ValueError(
            f"Only {len(seed)} observations are available; the lag set needs at least "
            f"{min_needed} to forecast.")

    start_when = pd.Timestamp(base["dt"].iloc[-1])
    delta = freq_delta if freq_delta is not None else pd.Timedelta(days=1)
    dates = [start_when + delta * (i + 1) for i in range(total_steps)]
    preds = _simulate(model, features, spec, seed, dates)

    fc = pd.DataFrame({"dt": dates, "kwh": preds})
    agg = (fc.set_index("dt")["kwh"].resample(RULES[interval]).sum().reset_index())
    labels = []
    for d in agg["dt"]:
        if interval == "hourly":
            labels.append(d.strftime("%Y-%m-%d %H:%M"))
        elif interval == "daily":
            labels.append(d.strftime("%Y-%m-%d"))
        elif interval == "weekly":
            labels.append("W/%s" % d.strftime("%d %b %Y"))
        elif interval == "monthly":
            labels.append(d.strftime("%b %Y"))
        else:
            labels.append(str(d.year))

    points = [{"label": lab, "date": d.strftime("%Y-%m-%d %H:%M"),
               "predicted_kwh": round(float(v), 2)}
              for lab, d, v in zip(labels, agg["dt"], agg["kwh"])]

    peak = max(points, key=lambda p: p["predicted_kwh"]) if points else None
    low = min(points, key=lambda p: p["predicted_kwh"]) if points else None
    historical_mean = float(base["kwh"].tail(min(len(base), 30)).mean())

    return {
        "available": True,
        "dataset_id": dataset_id,
        "model": model_name,
        "interval": interval,
        "grain": grain,
        "points": points,
        "summary": {
            "horizon_points": len(points),
            "total_predicted_kwh": round(sum(p["predicted_kwh"] for p in points), 2),
            "average_predicted_kwh": round(float(np.mean([p["predicted_kwh"] for p in points])), 2),
            "peak": peak,
            "lowest": low,
            "recent_actual_average_kwh": round(historical_mean, 2),
            "change_vs_recent_pct": round(
                (float(np.mean([p["predicted_kwh"] for p in points])) - historical_mean)
                / historical_mean * 100, 2) if historical_mean else None,
        },
        "method": ("Recursive one-step-ahead forecasting: each prediction is appended to the "
                   "history and lag/rolling features are rebuilt from predicted values only — "
                   "no future actual values are used."),
        "test_metrics": run["metrics"][model_name],
    }


def history_vs_forecast(dataset_id: str, model_name="best", horizon=7, interval="daily",
                        tail=60) -> dict:
    fc = forecast(dataset_id, model_name, horizon, interval)
    prep = prepared(dataset_id)
    hist = aggregate(dataset_id, interval)
    hist_points = hist["buckets"][-tail:] if hist["available"] else []
    return {"forecast": fc,
            "history": [{"label": b["label"], "date": b["date"], "kwh": b["total_kwh"]}
                        for b in hist_points],
            "grain": prep["grain"]}
