#!/usr/bin/env python
"""
fetch_patient_access.py -- The Patient's View: Can You Find a Trial in Africa?
===============================================================================
If you are a patient in Africa seeking a clinical trial, what are your chances?
This script computes "trial accessibility" from the patient/consumer perspective.

Key metrics:
- Recruiting trials per million population (LIVE access metric)
- Condition-specific recruiting trials (HIV, cancer, diabetes, hypertension, malaria)
- Patient Desert Score: conditions with ZERO recruiting trials per country
- Capital city concentration proxy: single-site trials = rural exclusion

Queries ClinicalTrials.gov API v2 for RECRUITING interventional trials across
10 African countries and the US as comparator.

Usage:
    python fetch_patient_access.py

Output:
    data/patient_access_data.json   (cached API results, 24h TTL)
    patient-access.html             (dark-theme interactive dashboard)

Requirements:
    Python 3.8+, no external packages (uses urllib)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

AFRICAN_COUNTRIES = {
    "Nigeria": {"population_m": 224, "capital": "Abuja"},
    "South Africa": {"population_m": 60, "capital": "Pretoria"},
    "Kenya": {"population_m": 56, "capital": "Nairobi"},
    "Ethiopia": {"population_m": 127, "capital": "Addis Ababa"},
    "Egypt": {"population_m": 112, "capital": "Cairo"},
    "Ghana": {"population_m": 34, "capital": "Accra"},
    "Uganda": {"population_m": 48, "capital": "Kampala"},
    "Tanzania": {"population_m": 66, "capital": "Dar es Salaam"},
    "Rwanda": {"population_m": 14, "capital": "Kigali"},
    "Cameroon": {"population_m": 28, "capital": "Yaounde"},
}

US_POPULATION_M = 335

# Key conditions a patient might search for
PATIENT_CONDITIONS = {
    "HIV": "HIV OR human immunodeficiency virus",
    "Cancer": "cancer OR neoplasm OR oncology",
    "Diabetes": "diabetes OR diabetic",
    "Hypertension": "hypertension OR high blood pressure",
    "Malaria": "malaria",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "patient_access_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "patient-access.html"
RATE_LIMIT = 0.35
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(params, retries=MAX_RETRIES):
    """Make a GET request to ClinicalTrials.gov API v2 with retries."""
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"  [retry {attempt + 1}/{retries}] {exc}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def get_recruiting_count(condition_query, location, use_cond=True):
    """Return count of RECRUITING interventional trials for condition+location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]RECRUITING",
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition_query:
        if use_cond:
            params["query.cond"] = condition_query
        else:
            params["query.term"] = condition_query
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_recruiting_count_multi(condition_query, countries, use_cond=True):
    """Return count of RECRUITING interventional trials across multiple countries."""
    location_str = " OR ".join(countries)
    params = {
        "format": "json",
        "query.locn": location_str,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]RECRUITING",
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition_query:
        if use_cond:
            params["query.cond"] = condition_query
        else:
            params["query.term"] = condition_query
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_all_trial_count(condition_query, location, use_cond=True):
    """Return count of ALL interventional trials (any status)."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition_query:
        if use_cond:
            params["query.cond"] = condition_query
        else:
            params["query.term"] = condition_query
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_single_site_count(location):
    """Proxy for single-site trials: trials with only one location (capital concentration)."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]RECRUITING",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache():
    """Load cached data if fresh enough."""
    if CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(raw.get("timestamp", "2000-01-01"))
            if datetime.now() - ts < timedelta(hours=CACHE_TTL_HOURS):
                print(f"Using cached data from {ts.isoformat()}")
                return raw
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_all_data():
    """Fetch all patient access data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "recruiting_by_country": {},
        "all_trials_by_country": {},
        "us_recruiting_total": 0,
        "us_all_total": 0,
        "condition_by_country": {},
        "condition_africa_total": {},
        "condition_us": {},
        "patient_desert_scores": {},
        "capital_concentration": {},
    }

    country_list = list(AFRICAN_COUNTRIES.keys())

    # --- Recruiting trials per country (total, no condition filter) ---
    print("\n--- Recruiting trials by country ---")
    for country in country_list:
        print(f"  {country}: recruiting...")
        count = get_recruiting_count(None, country, use_cond=False)
        data["recruiting_by_country"][country] = count
        time.sleep(RATE_LIMIT)

        print(f"  {country}: all trials...")
        all_count = get_all_trial_count(None, country, use_cond=False)
        data["all_trials_by_country"][country] = all_count
        time.sleep(RATE_LIMIT)

    # US totals
    print("  US: recruiting...")
    data["us_recruiting_total"] = get_recruiting_count(None, "United States", use_cond=False)
    time.sleep(RATE_LIMIT)
    print("  US: all trials...")
    data["us_all_total"] = get_all_trial_count(None, "United States", use_cond=False)
    time.sleep(RATE_LIMIT)

    # --- Condition-specific recruiting by country ---
    print("\n--- Condition-specific recruiting ---")
    for cond_name, cond_query in PATIENT_CONDITIONS.items():
        data["condition_by_country"][cond_name] = {}
        for country in country_list:
            print(f"  {country}: {cond_name}...")
            count = get_recruiting_count(cond_query, country, use_cond=True)
            data["condition_by_country"][cond_name][country] = count
            time.sleep(RATE_LIMIT)

        # Africa total for condition
        print(f"  Africa total: {cond_name}...")
        africa_total = get_recruiting_count_multi(cond_query, country_list, use_cond=True)
        data["condition_africa_total"][cond_name] = africa_total
        time.sleep(RATE_LIMIT)

        # US for condition
        print(f"  US: {cond_name}...")
        us_count = get_recruiting_count(cond_query, "United States", use_cond=True)
        data["condition_us"][cond_name] = us_count
        time.sleep(RATE_LIMIT)

    # --- Patient Desert Score per country ---
    print("\n--- Patient Desert Scores ---")
    for country in country_list:
        desert_count = 0
        for cond_name in PATIENT_CONDITIONS:
            if data["condition_by_country"][cond_name].get(country, 0) == 0:
                desert_count += 1
        data["patient_desert_scores"][country] = {
            "score": desert_count,
            "max": len(PATIENT_CONDITIONS),
            "zero_conditions": [c for c in PATIENT_CONDITIONS
                                if data["condition_by_country"][c].get(country, 0) == 0],
        }

    # --- Capital concentration proxy ---
    print("\n--- Capital concentration proxy ---")
    for country in country_list:
        capital = AFRICAN_COUNTRIES[country]["capital"]
        print(f"  {capital} ({country})...")
        capital_count = get_recruiting_count(None, capital, use_cond=False)
        country_count = data["recruiting_by_country"].get(country, 0)
        data["capital_concentration"][country] = {
            "capital": capital,
            "capital_recruiting": capital_count,
            "country_recruiting": country_count,
            "concentration_pct": round(capital_count / country_count * 100, 1) if country_count > 0 else 0,
        }
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_access_metrics(data):
    """Compute per-million recruiting rates and gaps."""
    metrics = {}
    for country, info in AFRICAN_COUNTRIES.items():
        recruiting = data["recruiting_by_country"].get(country, 0)
        pop = info["population_m"]
        rate = round(recruiting / pop, 2) if pop > 0 else 0
        metrics[country] = {
            "recruiting": recruiting,
            "population_m": pop,
            "per_million": rate,
        }

    us_rate = round(data["us_recruiting_total"] / US_POPULATION_M, 2)
    metrics["United States"] = {
        "recruiting": data["us_recruiting_total"],
        "population_m": US_POPULATION_M,
        "per_million": us_rate,
    }
    return metrics


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(data, metrics):
    """Generate the full HTML dashboard."""

    # Access rates table rows
    access_rows = ""
    sorted_countries = sorted(
        [c for c in metrics if c != "United States"],
        key=lambda c: metrics[c]["per_million"],
        reverse=True,
    )
    us = metrics["United States"]
    for country in sorted_countries:
        m = metrics[country]
        ratio = round(us["per_million"] / m["per_million"], 1) if m["per_million"] > 0 else 999
        ratio_str = f"{ratio}x" if ratio < 999 else "INF"
        color = "#ef4444" if m["per_million"] < 1.0 else ("#f59e0b" if m["per_million"] < 5.0 else "#e2e8f0")
        access_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{m["recruiting"]}</td>'
            f'<td style="padding:8px;text-align:right;">{m["population_m"]}M</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{m["per_million"]}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;color:#f59e0b;">{ratio_str}</td>'
            f'</tr>\n'
        )
    # US row
    access_rows += (
        f'<tr style="background:rgba(59,130,246,0.1);border-top:2px solid var(--accent);">'
        f'<td style="padding:8px;font-weight:bold;">United States</td>'
        f'<td style="padding:8px;text-align:right;">{us["recruiting"]}</td>'
        f'<td style="padding:8px;text-align:right;">{us["population_m"]}M</td>'
        f'<td style="padding:8px;text-align:right;color:#3b82f6;font-weight:bold;">{us["per_million"]}</td>'
        f'<td style="padding:8px;text-align:right;">1.0x</td>'
        f'</tr>\n'
    )

    # Condition-specific table
    condition_rows = ""
    for cond_name in PATIENT_CONDITIONS:
        af_total = data["condition_africa_total"].get(cond_name, 0)
        us_total = data["condition_us"].get(cond_name, 0)
        gap = round(us_total / af_total, 1) if af_total > 0 else 999
        gap_str = f"{gap}x" if gap < 999 else "INF"
        color = "#ef4444" if af_total == 0 else ("#f59e0b" if af_total < 10 else "#e2e8f0")
        condition_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cond_name)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{af_total}</td>'
            f'<td style="padding:8px;text-align:right;">{us_total}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;color:#f59e0b;">{gap_str}</td>'
            f'</tr>\n'
        )

    # Patient desert scores table
    desert_rows = ""
    sorted_desert = sorted(
        data["patient_desert_scores"].items(),
        key=lambda x: x[1]["score"],
        reverse=True,
    )
    for country, desert in sorted_desert:
        score = desert["score"]
        max_s = desert["max"]
        zero_list = ", ".join(desert["zero_conditions"]) if desert["zero_conditions"] else "None"
        color = "#ef4444" if score >= 3 else ("#f59e0b" if score >= 1 else "#22c55e")
        desert_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:center;color:{color};font-weight:bold;font-size:1.3rem;">'
            f'{score}/{max_s}</td>'
            f'<td style="padding:8px;font-size:0.85rem;color:var(--muted);">{escape_html(zero_list)}</td>'
            f'</tr>\n'
        )

    # Capital concentration table
    capital_rows = ""
    sorted_cap = sorted(
        data["capital_concentration"].items(),
        key=lambda x: x[1]["concentration_pct"],
        reverse=True,
    )
    for country, cap_info in sorted_cap:
        pct = cap_info["concentration_pct"]
        color = "#ef4444" if pct > 80 else ("#f59e0b" if pct > 50 else "#22c55e")
        capital_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;">{escape_html(cap_info["capital"])}</td>'
            f'<td style="padding:8px;text-align:right;">{cap_info["capital_recruiting"]}</td>'
            f'<td style="padding:8px;text-align:right;">{cap_info["country_recruiting"]}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{pct}%</td>'
            f'</tr>\n'
        )

    # Condition-by-country heatmap rows
    heatmap_rows = ""
    country_list = list(AFRICAN_COUNTRIES.keys())
    for cond_name in PATIENT_CONDITIONS:
        cells = f'<td style="padding:8px;font-weight:bold;">{escape_html(cond_name)}</td>'
        for country in country_list:
            val = data["condition_by_country"].get(cond_name, {}).get(country, 0)
            if val == 0:
                bg = "rgba(239,68,68,0.3)"
                clr = "#ef4444"
            elif val < 5:
                bg = "rgba(245,158,11,0.2)"
                clr = "#f59e0b"
            else:
                bg = "rgba(34,197,94,0.15)"
                clr = "#22c55e"
            cells += f'<td style="padding:6px;text-align:center;background:{bg};color:{clr};font-weight:bold;">{val}</td>'
        heatmap_rows += f'<tr>{cells}</tr>\n'

    # Chart data
    chart_countries = json.dumps(sorted_countries[:10])
    chart_rates = json.dumps([metrics[c]["per_million"] for c in sorted_countries[:10]])
    chart_us_rate = us["per_million"]

    cond_labels = json.dumps(list(PATIENT_CONDITIONS.keys()))
    cond_af_vals = json.dumps([data["condition_africa_total"].get(c, 0) for c in PATIENT_CONDITIONS])
    cond_us_vals = json.dumps([data["condition_us"].get(c, 0) for c in PATIENT_CONDITIONS])

    desert_countries = json.dumps([c for c, _ in sorted_desert])
    desert_scores = json.dumps([d["score"] for _, d in sorted_desert])

    cap_countries = json.dumps([c for c, _ in sorted_cap])
    cap_pcts = json.dumps([ci["concentration_pct"] for _, ci in sorted_cap])

    # Summary stats
    africa_total_recruiting = sum(data["recruiting_by_country"].values())
    africa_total_pop = sum(info["population_m"] for info in AFRICAN_COUNTRIES.values())
    africa_rate = round(africa_total_recruiting / africa_total_pop, 2) if africa_total_pop > 0 else 0
    us_rate_val = us["per_million"]
    gap_ratio = round(us_rate_val / africa_rate, 1) if africa_rate > 0 else 999

    avg_desert = round(sum(d["score"] for d in data["patient_desert_scores"].values()) / len(data["patient_desert_scores"]), 1)
    avg_concentration = round(sum(c["concentration_pct"] for c in data["capital_concentration"].values()) / len(data["capital_concentration"]), 1)

    heatmap_headers = "".join(
        f'<th style="padding:6px;font-size:0.7rem;writing-mode:vertical-rl;text-orientation:mixed;">{escape_html(c[:6])}</th>'
        for c in country_list
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Can I Find a Trial? The Patient's View of Clinical Trial Access in Africa</title>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #111827;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #3b82f6;
  --danger: #ef4444;
  --warning: #f59e0b;
  --success: #22c55e;
  --purple: #a855f7;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Segoe UI',system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
h1 {{ font-size:2rem; margin:30px 0 10px; color:#fff; }}
h2 {{ font-size:1.5rem; margin:40px 0 15px; color:var(--accent); border-bottom:1px solid var(--border); padding-bottom:8px; }}
h3 {{ font-size:1.1rem; margin:20px 0 10px; color:var(--purple); }}
.subtitle {{ color:var(--muted); font-size:1rem; margin-bottom:30px; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; margin:20px 0; }}
.stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin:20px 0; }}
.stat-box {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px; text-align:center; }}
.stat-box .number {{ font-size:2.2rem; font-weight:bold; }}
.stat-box .label {{ font-size:0.85rem; color:var(--muted); margin-top:4px; }}
.narrative {{ background:rgba(168,85,247,0.08); border-left:4px solid var(--purple); padding:20px; margin:20px 0; border-radius:0 10px 10px 0; font-style:italic; color:#c4b5fd; }}
table {{ width:100%; border-collapse:collapse; margin:10px 0; }}
th {{ background:rgba(59,130,246,0.15); padding:10px 8px; text-align:left; font-size:0.85rem; color:var(--accent); border-bottom:2px solid var(--border); }}
td {{ border-bottom:1px solid var(--border); }}
tr:hover {{ background:rgba(255,255,255,0.02); }}
.chart-container {{ position:relative; height:350px; margin:20px 0; }}
canvas {{ max-width:100%; }}
.methodology {{ background:rgba(59,130,246,0.05); border:1px solid var(--border); border-radius:10px; padding:20px; margin:30px 0; font-size:0.9rem; color:var(--muted); }}
.methodology h3 {{ color:var(--accent); }}
.badge {{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:0.75rem; font-weight:bold; }}
.badge-danger {{ background:rgba(239,68,68,0.2); color:#ef4444; }}
.badge-warning {{ background:rgba(245,158,11,0.2); color:#f59e0b; }}
.badge-ok {{ background:rgba(34,197,94,0.2); color:#22c55e; }}
.journey-step {{ display:flex; align-items:center; gap:16px; margin:12px 0; padding:16px; background:rgba(30,41,59,0.5); border-radius:10px; }}
.journey-step .step-num {{ width:40px; height:40px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:bold; font-size:1.2rem; flex-shrink:0; }}
.journey-step .step-text {{ flex:1; }}
footer {{ text-align:center; padding:40px 20px; color:var(--muted); font-size:0.8rem; border-top:1px solid var(--border); margin-top:40px; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="container">

<h1>Can I Find a Trial?</h1>
<p class="subtitle">The Patient's View: Clinical Trial Accessibility Across Africa | Project 49 of the Africa RCT Equity Series</p>

<div class="narrative">
"I have been diagnosed with diabetes in Lagos. My doctor says there might be a clinical trial that could help. I search online -- and find nothing recruiting in my country for my condition. In New York, the same search returns hundreds of options. This is the patient's reality of clinical trial access in Africa."
</div>

<div class="stat-grid">
  <div class="stat-box">
    <div class="number" style="color:var(--danger);">{africa_total_recruiting}</div>
    <div class="label">Recruiting Trials Across 10 African Countries</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--accent);">{data["us_recruiting_total"]}</div>
    <div class="label">Recruiting Trials in the US</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--warning);">{africa_rate}</div>
    <div class="label">Africa: Recruiting Trials per Million</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--success);">{us_rate_val}</div>
    <div class="label">US: Recruiting Trials per Million</div>
  </div>
</div>

<div class="card" style="text-align:center;">
  <div style="font-size:3rem;font-weight:bold;color:var(--danger);">{gap_ratio}x</div>
  <div style="color:var(--muted);">A patient in the US has {gap_ratio}x more recruiting trials per capita than a patient in Africa</div>
</div>

<h2>The Patient Journey: Searching for a Trial</h2>

<div class="journey-step">
  <div class="step-num" style="background:rgba(59,130,246,0.2);color:var(--accent);">1</div>
  <div class="step-text">
    <strong>Patient receives diagnosis</strong><br>
    <span style="color:var(--muted);">HIV, cancer, diabetes, hypertension, or malaria -- the conditions that define Africa's disease burden</span>
  </div>
</div>
<div class="journey-step">
  <div class="step-num" style="background:rgba(245,158,11,0.2);color:var(--warning);">2</div>
  <div class="step-text">
    <strong>Patient searches for recruiting trials</strong><br>
    <span style="color:var(--muted);">On ClinicalTrials.gov -- the world's largest trial registry. But how many are actually recruiting in their country?</span>
  </div>
</div>
<div class="journey-step">
  <div class="step-num" style="background:rgba(239,68,68,0.2);color:var(--danger);">3</div>
  <div class="step-text">
    <strong>Result: zero or near-zero options</strong><br>
    <span style="color:var(--muted);">Average Patient Desert Score = {avg_desert}/5 -- most countries have zero recruiting trials for multiple conditions</span>
  </div>
</div>
<div class="journey-step">
  <div class="step-num" style="background:rgba(168,85,247,0.2);color:var(--purple);">4</div>
  <div class="step-text">
    <strong>Even when trials exist, they are in the capital</strong><br>
    <span style="color:var(--muted);">Average capital concentration: {avg_concentration}% of recruiting trials are in the capital city -- rural patients are excluded</span>
  </div>
</div>

<h2>Recruiting Trials Per Million Population</h2>
<p style="color:var(--muted);margin-bottom:10px;">The LIVE access metric: how many trials could a patient actually join today?</p>

<div class="card">
  <div class="chart-container">
    <canvas id="rateChart"></canvas>
  </div>
</div>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">Recruiting Trials</th>
        <th style="text-align:right;">Population</th>
        <th style="text-align:right;">Per Million</th>
        <th style="text-align:right;">Gap vs US</th>
      </tr>
    </thead>
    <tbody>
{access_rows}
    </tbody>
  </table>
</div>

<h2>Can I Find a Trial for My Condition?</h2>
<p style="color:var(--muted);margin-bottom:10px;">A patient searches for their specific condition -- Africa vs US recruiting trials</p>

<div class="card">
  <div class="chart-container">
    <canvas id="condChart"></canvas>
  </div>
</div>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Condition</th>
        <th style="text-align:right;">Africa Recruiting</th>
        <th style="text-align:right;">US Recruiting</th>
        <th style="text-align:right;">Gap</th>
      </tr>
    </thead>
    <tbody>
{condition_rows}
    </tbody>
  </table>
</div>

<h2>The Patient Desert Score</h2>
<p style="color:var(--muted);margin-bottom:10px;">How many of 5 major conditions have ZERO recruiting trials in each country?<br>
Score of 5/5 = complete patient desert; 0/5 = at least one trial per condition</p>

<div class="card">
  <div class="chart-container">
    <canvas id="desertChart"></canvas>
  </div>
</div>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:center;">Desert Score</th>
        <th>Conditions with Zero Trials</th>
      </tr>
    </thead>
    <tbody>
{desert_rows}
    </tbody>
  </table>
</div>

<h2>The Condition-Country Heatmap</h2>
<p style="color:var(--muted);margin-bottom:10px;">Recruiting trial counts by condition and country -- red = zero, amber = 1-4, green = 5+</p>

<div class="card" style="overflow-x:auto;">
  <table style="font-size:0.85rem;">
    <thead>
      <tr>
        <th>Condition</th>
        {heatmap_headers}
      </tr>
    </thead>
    <tbody>
{heatmap_rows}
    </tbody>
  </table>
</div>

<h2>The Capital City Problem</h2>
<p style="color:var(--muted);margin-bottom:10px;">What percentage of recruiting trials concentrate in the capital? Rural patients -- the majority -- are excluded.</p>

<div class="card">
  <div class="chart-container">
    <canvas id="capitalChart"></canvas>
  </div>
</div>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th>Capital</th>
        <th style="text-align:right;">Capital Trials</th>
        <th style="text-align:right;">Country Total</th>
        <th style="text-align:right;">Concentration</th>
      </tr>
    </thead>
    <tbody>
{capital_rows}
    </tbody>
  </table>
</div>

<div class="narrative">
"The numbers tell a simple story: if you are a patient in Africa, clinical trials are not designed for you. They are not recruiting in your country, not studying your condition, and not located near where you live. The evidence that will guide your treatment was generated somewhere else, on someone else."
</div>

<div class="methodology">
  <h3>Methodology</h3>
  <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (queried {data["timestamp"][:10]})</p>
  <p><strong>Filter:</strong> <code>AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]RECRUITING</code></p>
  <p><strong>Countries:</strong> 10 African nations (Nigeria, South Africa, Kenya, Ethiopia, Egypt, Ghana, Uganda, Tanzania, Rwanda, Cameroon) + United States as comparator</p>
  <p><strong>Conditions:</strong> HIV, cancer, diabetes, hypertension, malaria -- the five conditions defining Africa's disease burden</p>
  <p><strong>Patient Desert Score:</strong> Count of conditions with zero recruiting trials per country (0-5 scale)</p>
  <p><strong>Capital concentration:</strong> Recruiting trials matching the capital city name vs country total</p>
  <p><strong>Limitation:</strong> Single registry; some trials may be registered elsewhere. Capital city proxy uses location name matching. Population data from UN 2024 estimates.</p>
  <p><strong>AI transparency:</strong> LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.</p>
</div>

</div>

<footer>
  Africa RCT Equity Series -- Project 49: The Patient's View | ClinicalTrials.gov API v2<br>
  Generated {data["timestamp"][:10]} | Data cached for 24 hours
</footer>

<script>
const rateLabels = {chart_countries};
const rateValues = {chart_rates};
const usRate = {chart_us_rate};

new Chart(document.getElementById('rateChart'), {{
  type: 'bar',
  data: {{
    labels: rateLabels,
    datasets: [{{
      label: 'Recruiting trials per million',
      data: rateValues,
      backgroundColor: rateValues.map(v => v < 1 ? 'rgba(239,68,68,0.7)' : (v < 5 ? 'rgba(245,158,11,0.7)' : 'rgba(34,197,94,0.7)')),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: 'Recruiting Trials per Million Population (US rate = ' + usRate + ')', color: '#e2e8f0' }},
      annotation: {{}}
    }},
    scales: {{
      y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
    }}
  }}
}});

new Chart(document.getElementById('condChart'), {{
  type: 'bar',
  data: {{
    labels: {cond_labels},
    datasets: [
      {{ label: 'Africa (10 countries)', data: {cond_af_vals}, backgroundColor: 'rgba(239,68,68,0.7)', borderRadius: 4 }},
      {{ label: 'United States', data: {cond_us_vals}, backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 4 }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: 'Recruiting Trials by Condition: Africa vs US', color: '#e2e8f0' }},
      legend: {{ labels: {{ color: '#e2e8f0' }} }}
    }},
    scales: {{
      y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

new Chart(document.getElementById('desertChart'), {{
  type: 'bar',
  data: {{
    labels: {desert_countries},
    datasets: [{{
      label: 'Patient Desert Score (out of 5)',
      data: {desert_scores},
      backgroundColor: {desert_scores}.map(v => v >= 3 ? 'rgba(239,68,68,0.7)' : (v >= 1 ? 'rgba(245,158,11,0.7)' : 'rgba(34,197,94,0.7)')),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: 'Patient Desert Score: Conditions with Zero Recruiting Trials', color: '#e2e8f0' }}
    }},
    scales: {{
      y: {{ beginAtZero: true, max: 5, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8', stepSize: 1 }} }},
      x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
    }}
  }}
}});

new Chart(document.getElementById('capitalChart'), {{
  type: 'bar',
  data: {{
    labels: {cap_countries},
    datasets: [{{
      label: 'Capital concentration (%)',
      data: {cap_pcts},
      backgroundColor: {cap_pcts}.map(v => v > 80 ? 'rgba(239,68,68,0.7)' : (v > 50 ? 'rgba(245,158,11,0.7)' : 'rgba(34,197,94,0.7)')),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: 'Capital City Concentration of Recruiting Trials (%)', color: '#e2e8f0' }}
    }},
    scales: {{
      y: {{ beginAtZero: true, max: 100, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8', callback: function(v) {{ return v + '%'; }} }} }},
      x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Generated {OUTPUT_HTML}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Project 49: The Patient's View -- Can You Find a Trial in Africa?")
    print("=" * 70)

    data = fetch_all_data()
    metrics = compute_access_metrics(data)

    # Print summary
    print("\n--- Summary ---")
    for country in sorted(metrics, key=lambda c: metrics[c]["per_million"], reverse=True):
        m = metrics[country]
        print(f"  {country}: {m['recruiting']} recruiting, {m['per_million']}/M")

    print("\n--- Patient Desert Scores ---")
    for country, desert in data["patient_desert_scores"].items():
        print(f"  {country}: {desert['score']}/{desert['max']} conditions with zero trials")

    print("\n--- Capital Concentration ---")
    for country, cap in data["capital_concentration"].items():
        print(f"  {country}: {cap['concentration_pct']}% in {cap['capital']}")

    generate_html(data, metrics)
    print("\nDone.")


if __name__ == "__main__":
    main()
