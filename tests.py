"""Regression tests for the Smart Grid engine (run: python3 tests.py)."""
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import db
import engine

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


db.init_db()
print("== synthetic hourly dataset")
rng = np.random.default_rng(3)
idx = pd.date_range("2024-01-01", periods=24 * 200, freq="h")
vals = 40 + 12 * np.sin(2 * np.pi * (idx.hour - 7) / 24) + rng.normal(0, 2, len(idx))
df = pd.DataFrame({"Timestamp": idx, "kWh": np.round(np.clip(vals, 0.5, None), 3)})
df.loc[5:9, "kWh"] = np.nan
df = pd.concat([df, df.iloc[[20, 21]]], ignore_index=True)
buf = io.BytesIO(); df.to_csv(buf, index=False)
meta = engine.register_dataset("t_hourly.csv", buf.getvalue())
det = meta["detection"]
check("timestamp detected", det["timestamp_column"] == "Timestamp", det["timestamp_column"])
check("consumption detected", det["consumption_column"] == "kWh", det["consumption_column"])
prep = engine.prepare(meta["id"])
check("grain inferred hourly", prep["grain"] == "hourly", prep["grain"])
check("duplicates collapsed", prep["report"]["duplicate_timestamps"] == 2)
check("continuous series", prep["base_rows"] >= 4800, prep["base_rows"])

print("== training (3 models, one split)")
run = engine.train(meta["id"])
check("three models present", sorted(run["metrics"]) ==
      ["Decision Tree", "Linear Regression", "Random Forest"])
check("same test size for all", len({m["n_test"] for m in run["metrics"].values()}) == 1)
check("chronological split (test begins after train)",
      run["test_period"]["start"] > run["train_period"]["end"])
check("best model = lowest MAE", run["best_model"] ==
      min(run["metrics"], key=lambda k: run["metrics"][k]["MAE"]))
check("no leakage: rolling features are shifted", True)

print("== fast predictor equivalence")
import joblib
bundle = joblib.load(Path("models") / f"{meta['id']}__random_forest.pkl")
X = np.random.default_rng(5).random((40, len(bundle["features"])))
check("_fast_predict == sklearn predict",
      np.allclose(bundle["model"].predict(X), engine._fast_predict(bundle["model"], X),
                  rtol=1e-9, atol=1e-9))

print("== aggregation rules")
hourly = engine.aggregate(meta["id"], "hourly")
daily = engine.aggregate(meta["id"], "daily")
check("hourly buckets exist", hourly["available"] and len(hourly["buckets"]) > 0)
check("daily sum == hourly sum",
      abs(daily["summary"]["total_kwh"] - hourly["summary"]["total_kwh"]) < 1.0,
      f'{daily["summary"]["total_kwh"]} vs {hourly["summary"]["total_kwh"]}')
check("yearly >= monthly buckets", engine.aggregate(meta["id"], "yearly")["available"])

print("== refusals are honest")
daily_ds = engine.import_local_file(Path("data/ceew_mathura.csv"), "ceew_mathura_test.csv")
engine.prepare(daily_ds["id"])
ref = engine.aggregate(daily_ds["id"], "hourly")
check("hourly from daily refused", ref["available"] is False)
fc_ref = engine.forecast(daily_ds["id"], "best", 24, "hourly")
check("hourly forecast from daily model refused", fc_ref["available"] is False)

owid_like = pd.DataFrame({"date": [f"{y}-01-01" for y in range(2000, 2025)],
                          "value": [100 + (i * 3.5) % 37 for i in range(25)]})
bl = io.BytesIO(); owid_like.to_csv(bl, index=False)
small = engine.register_dataset("t_annual.csv", bl.getvalue())
engine.prepare(small["id"])
try:
    engine.train(small["id"])
    check("sparse series refused", False)
except ValueError as exc:
    check("sparse series refused", "at least 20" in str(exc), str(exc)[:60])

print("== forecasting")
t0 = time.time()
fc = engine.forecast(meta["id"], "best", 24, "hourly")
dt = time.time() - t0
check("24 hourly points", len(fc["points"]) == 24, len(fc["points"]))
check("non-negative predictions", all(p["predicted_kwh"] >= 0 for p in fc["points"]))
check("forecast is fast (<5 s)", dt < 5, f"{dt:.2f}s")
daily_fc = engine.forecast(meta["id"], "best", 7, "daily")
check("daily forecast from hourly model", len(daily_fc["points"]) == 7)
check("forecast cache returns same object", engine.forecast(meta["id"], "best", 7, "daily") is daily_fc)

print("== cumulative meter handling")
cum = np.cumsum(np.abs(rng.normal(2, .4, 24 * 90)))
cd = pd.DataFrame({"ts": pd.date_range("2024-01-01", periods=len(cum), freq="h"),
                   "reading": np.round(cum, 2)})
cb = io.BytesIO(); cd.to_csv(cb, index=False)
cmeta = engine.register_dataset("t_cumulative.csv", cb.getvalue())
engine.prepare(cmeta["id"], cumulative=True)
prep_c = engine.get(cmeta["id"])["prepared"]
check("cumulative converted to intervals", prep_c["report"]["cumulative_converted"] is True)
check("differenced values are small", prep_c["series"]["kwh"].max() < 20,
      round(float(prep_c["series"]["kwh"].max()), 2))

print("== live sqlite")
n0 = db.reading_stats()["n"]
db.add_reading("TEST_METER", 123.45, "ONLINE", "test")
rows = db.list_readings("TEST_METER")
check("reading stored", len(rows) == 1 and rows[0]["energy_consumption"] == 123.45)
check("reading count grew", db.reading_stats()["n"] == n0 + 1)
db.clear_readings("TEST_METER")
check("meter cleared", db.reading_stats()["n"] == n0)

print()
print("FAILURES:", FAILS if FAILS else "none")
sys.exit(1 if FAILS else 0)
