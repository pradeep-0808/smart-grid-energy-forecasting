"""Genuine geographic energy data.

Source: Our World in Data energy dataset (github.com/owid/energy-data), built on
Energy Institute Statistical Review + Ember. Annual electricity data per country.
Nothing here is synthesised: every value returned comes from that CSV, and gaps
are reported as gaps.
"""
import io
import json
import os
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
CACHE = BASE / "data" / "owid_energy.csv"
URL = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
COLS = ["country", "year", "iso_code", "population", "gdp",
        "electricity_generation", "electricity_demand",
        "energy_per_capita", "per_capita_electricity", "renewables_share_elec"]

LOCAL_FILE = os.environ.get("OWID_LOCAL_FILE")


def ensure_cache():
    if CACHE.exists() and CACHE.stat().st_size > 1_000_000:
        return CACHE
    if LOCAL_FILE and Path(LOCAL_FILE).exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(Path(LOCAL_FILE).read_bytes())
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=120) as r:
        CACHE.write_bytes(r.read())
    return CACHE


def _load():
    path = ensure_cache()
    df = pd.read_csv(path, usecols=COLS)
    return df


_cache = {}


def frame():
    if "df" not in _cache:
        _cache["df"] = _load()
    return _cache["df"]


def available():
    p = CACHE.exists() and CACHE.stat().st_size > 1_000_000
    return p or bool(LOCAL_FILE and Path(LOCAL_FILE).exists())


def search(query, limit=12):
    df = frame()
    q = (query or "").strip().lower()
    if not q:
        return []
    names = df["country"].dropna().unique()
    starts = [n for n in names if n.lower().startswith(q)]
    contains = [n for n in names if q in n.lower() and n not in starts]
    out = []
    for name in (starts + contains)[:limit]:
        sub = df[(df.country == name) & df.electricity_generation.notna()]
        years = sorted(sub.year.dropna().astype(int).tolist())
        out.append({
            "country": name,
            "iso_code": (sub.iso_code.dropna().iloc[-1] if not sub.iso_code.dropna().empty else None),
            "region_type": "country",
            "years_available": len(years),
            "first_year": years[0] if years else None,
            "last_year": years[-1] if years else None,
        })
    return out


METRICS = {
    "electricity_generation": {
        "label": "Electricity generation", "unit": "TWh", "kind": "annual_energy"},
    "electricity_demand": {
        "label": "Electricity demand", "unit": "TWh", "kind": "annual_energy"},
    "per_capita_electricity": {
        "label": "Electricity per capita", "unit": "kWh/person", "kind": "annual_intensity"},
}


def energy_data(country, metric="electricity_generation", start_year=None, end_year=None):
    df = frame()
    name = (country or "").strip()
    match = df[df.country.str.lower() == name.lower()]
    if match.empty:
        cands = search(name, 5)
        return {"found": False, "query": country, "suggestions": cands,
                "message": "No exact country match in the OWID energy dataset."}

    if metric not in METRICS:
        metric = "electricity_generation"

    sub = match[["country", "iso_code", "year", metric]].dropna(subset=[metric]).copy()
    sub["year"] = sub.year.astype(int)
    if start_year:
        sub = sub[sub.year >= int(start_year)]
    if end_year:
        sub = sub[sub.year <= int(end_year)]
    sub = sub.sort_values("year")

    note = None
    if len(sub) < 5:
        note = ("Only {} annual observations are available for this metric/period. "
                "Three time-series models need more history — upload an interval-level "
                "dataset (daily/hourly meter data) to train them.").format(len(sub))

    meta = METRICS[metric]
    series = [{"date": f"{int(r.year)}-01-01", "year": int(r.year),
               "value": round(float(getattr(r, metric)), 3)} for r in sub.itertuples()]
    return {
        "found": True,
        "location": {
            "country": match.country.iloc[0],
            "iso_code": (match.iso_code.dropna().iloc[-1]
                         if not match.iso_code.dropna().empty else None),
            "granularity": "annual (country level)",
        },
        "metric": metric,
        "metric_label": meta["label"],
        "unit": meta["unit"],
        "source": {
            "name": "Our World in Data — Energy dataset",
            "detail": "Energy Institute Statistical Review of World Energy + Ember, "
                      "aggregated annually by OWID.",
            "url": URL,
            "licence": "CC BY 4.0",
        },
        "series": series,
        "coverage": {"first_year": series[0]["year"] if series else None,
                     "last_year": series[-1]["year"] if series else None,
                     "observations": len(series)},
        "note": note,
    }
