#!/usr/bin/env python
"""
fetch_child_view.py -- The Child's View: Growing Up Without Evidence
====================================================================
A child born in Africa today will face diseases for which there is almost
no pediatric evidence generated on their continent.

Key metrics:
- Pediatric trials per condition: Africa vs US
- Childhood Evidence Gap per condition
- Neonatal trial void
- Vaccination vs treatment trial ratio

Queries ClinicalTrials.gov API v2 for pediatric-relevant conditions across
10 African countries and the US as comparator.

Usage:
    python fetch_child_view.py

Output:
    data/child_view_data.json   (cached API results, 24h TTL)
    child-view.html             (dark-theme interactive dashboard)

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

AFRICAN_COUNTRIES = [
    "Nigeria", "South Africa", "Kenya", "Ethiopia", "Egypt",
    "Ghana", "Uganda", "Tanzania", "Rwanda", "Cameroon",
]

# Population under 15 (millions, UN 2024 estimates)
CHILD_POPULATION = {
    "Nigeria": 95,
    "South Africa": 16,
    "Kenya": 22,
    "Ethiopia": 53,
    "Egypt": 32,
    "Ghana": 13,
    "Uganda": 23,
    "Tanzania": 30,
    "Rwanda": 6,
    "Cameroon": 13,
}
AFRICA_10_CHILD_POP = sum(CHILD_POPULATION.values())  # ~303M
US_CHILD_POP = 73  # millions

# Childhood conditions
CHILDHOOD_CONDITIONS = {
    "Childhood pneumonia": "childhood pneumonia OR pediatric pneumonia OR pneumonia child",
    "Diarrheal disease": "diarrheal disease OR pediatric diarrhea OR childhood diarrhea OR oral rehydration",
    "Malnutrition": "malnutrition OR severe acute malnutrition OR stunting OR wasting child",
    "Neonatal sepsis": "neonatal sepsis OR neonatal infection OR newborn sepsis",
    "Childhood HIV": "pediatric HIV OR childhood HIV OR children HIV OR PMTCT",
    "Childhood malaria": "pediatric malaria OR childhood malaria OR malaria child",
    "Childhood TB": "pediatric tuberculosis OR childhood tuberculosis OR childhood TB",
    "Childhood cancer": "pediatric cancer OR childhood cancer OR pediatric oncology OR childhood leukemia",
    "Childhood epilepsy": "pediatric epilepsy OR childhood epilepsy OR childhood seizure",
    "Sickle cell disease": "sickle cell disease OR sickle cell anemia",
}

# Broad pediatric query
PEDIATRIC_BROAD = "pediatric OR child OR infant OR neonatal"

# Vaccination vs treatment
VACCINE_QUERY = "vaccine OR vaccination OR immunization"
TREATMENT_QUERY = "treatment OR therapy OR drug"

CACHE_FILE = Path(__file__).resolve().parent / "data" / "child_view_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "child-view.html"
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


def get_trial_count(query_term, location, use_cond=True):
    """Return count of interventional trials for query+location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if use_cond:
        params["query.cond"] = query_term
    else:
        params["query.term"] = query_term
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_trial_count_multi(query_term, countries, use_cond=True):
    """Return count across multiple countries."""
    location_str = " OR ".join(countries)
    params = {
        "format": "json",
        "query.locn": location_str,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if use_cond:
        params["query.cond"] = query_term
    else:
        params["query.term"] = query_term
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
    """Fetch all child view data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "condition_africa": {},
        "condition_us": {},
        "condition_by_country": {},
        "broad_pediatric_africa": 0,
        "broad_pediatric_us": 0,
        "vaccine_africa": 0,
        "vaccine_us": 0,
        "treatment_africa": 0,
        "treatment_us": 0,
        "neonatal_africa": 0,
        "neonatal_us": 0,
        "pediatric_by_country": {},
    }

    # --- Condition-specific pediatric trials: Africa vs US ---
    print("\n--- Childhood conditions: Africa vs US ---")
    for cond_name, cond_query in CHILDHOOD_CONDITIONS.items():
        print(f"  Africa: {cond_name}...")
        africa_count = get_trial_count_multi(cond_query, AFRICAN_COUNTRIES, use_cond=True)
        data["condition_africa"][cond_name] = africa_count
        time.sleep(RATE_LIMIT)

        print(f"  US: {cond_name}...")
        us_count = get_trial_count(cond_query, "United States", use_cond=True)
        data["condition_us"][cond_name] = us_count
        time.sleep(RATE_LIMIT)

    # --- Condition by individual country ---
    print("\n--- Conditions by country ---")
    for cond_name, cond_query in CHILDHOOD_CONDITIONS.items():
        data["condition_by_country"][cond_name] = {}
        for country in AFRICAN_COUNTRIES:
            print(f"  {country}: {cond_name}...")
            count = get_trial_count(cond_query, country, use_cond=True)
            data["condition_by_country"][cond_name][country] = count
            time.sleep(RATE_LIMIT)

    # --- Broad pediatric queries ---
    print("\n--- Broad pediatric ---")
    print("  Africa: broad pediatric...")
    data["broad_pediatric_africa"] = get_trial_count_multi(PEDIATRIC_BROAD, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)
    print("  US: broad pediatric...")
    data["broad_pediatric_us"] = get_trial_count(PEDIATRIC_BROAD, "United States", use_cond=False)
    time.sleep(RATE_LIMIT)

    # --- Neonatal specifically ---
    print("  Africa: neonatal...")
    data["neonatal_africa"] = get_trial_count_multi("neonatal OR newborn", AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)
    print("  US: neonatal...")
    data["neonatal_us"] = get_trial_count("neonatal OR newborn", "United States", use_cond=False)
    time.sleep(RATE_LIMIT)

    # --- Vaccination vs treatment in pediatric context ---
    print("\n--- Vaccine vs treatment (pediatric) ---")
    ped_vaccine_q = f"({PEDIATRIC_BROAD}) AND ({VACCINE_QUERY})"
    ped_treat_q = f"({PEDIATRIC_BROAD}) AND ({TREATMENT_QUERY})"

    print("  Africa: vaccine + pediatric...")
    data["vaccine_africa"] = get_trial_count_multi(ped_vaccine_q, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)
    print("  US: vaccine + pediatric...")
    data["vaccine_us"] = get_trial_count(ped_vaccine_q, "United States", use_cond=False)
    time.sleep(RATE_LIMIT)
    print("  Africa: treatment + pediatric...")
    data["treatment_africa"] = get_trial_count_multi(ped_treat_q, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)
    print("  US: treatment + pediatric...")
    data["treatment_us"] = get_trial_count(ped_treat_q, "United States", use_cond=False)
    time.sleep(RATE_LIMIT)

    # --- Pediatric by country ---
    print("\n--- Pediatric trials by country ---")
    for country in AFRICAN_COUNTRIES:
        print(f"  {country}: broad pediatric...")
        count = get_trial_count(PEDIATRIC_BROAD, country, use_cond=False)
        data["pediatric_by_country"][country] = count
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_evidence_gaps(data):
    """Compute Childhood Evidence Gap per condition."""
    gaps = {}
    for cond_name in CHILDHOOD_CONDITIONS:
        africa = data["condition_africa"].get(cond_name, 0)
        us = data["condition_us"].get(cond_name, 0)
        if africa == 0:
            gap_ratio = 999
        elif us == 0:
            gap_ratio = 0
        else:
            gap_ratio = round(us / africa, 1)
        gaps[cond_name] = {
            "africa": africa,
            "us": us,
            "gap_ratio": gap_ratio,
        }
    return gaps


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

def generate_html(data, gaps):
    """Generate the full HTML dashboard."""

    # Condition comparison table
    cond_rows = ""
    sorted_conds = sorted(gaps.items(), key=lambda x: x[1]["gap_ratio"], reverse=True)
    for cond_name, gap_info in sorted_conds:
        gap_str = f"{gap_info['gap_ratio']}x" if gap_info["gap_ratio"] < 999 else "INF"
        color_af = "#ef4444" if gap_info["africa"] == 0 else ("#f59e0b" if gap_info["africa"] < 10 else "#e2e8f0")
        cond_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cond_name)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color_af};font-weight:bold;">{gap_info["africa"]}</td>'
            f'<td style="padding:8px;text-align:right;">{gap_info["us"]}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;color:#f59e0b;">{gap_str}</td>'
            f'</tr>\n'
        )

    # Heatmap: condition x country
    heatmap_rows = ""
    for cond_name in CHILDHOOD_CONDITIONS:
        cells = f'<td style="padding:6px;font-weight:bold;font-size:0.8rem;">{escape_html(cond_name)}</td>'
        for country in AFRICAN_COUNTRIES:
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
            cells += f'<td style="padding:4px;text-align:center;background:{bg};color:{clr};font-weight:bold;font-size:0.85rem;">{val}</td>'
        heatmap_rows += f'<tr>{cells}</tr>\n'

    heatmap_headers = "".join(
        f'<th style="padding:4px;font-size:0.65rem;writing-mode:vertical-rl;text-orientation:mixed;">{escape_html(c[:6])}</th>'
        for c in AFRICAN_COUNTRIES
    )

    # Per-country pediatric totals
    country_rows = ""
    sorted_countries = sorted(AFRICAN_COUNTRIES, key=lambda c: data["pediatric_by_country"].get(c, 0), reverse=True)
    for country in sorted_countries:
        ped_count = data["pediatric_by_country"].get(country, 0)
        child_pop = CHILD_POPULATION.get(country, 1)
        per_m = round(ped_count / child_pop, 2) if child_pop > 0 else 0
        color = "#ef4444" if per_m < 0.5 else ("#f59e0b" if per_m < 2 else "#e2e8f0")
        country_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{ped_count}</td>'
            f'<td style="padding:8px;text-align:right;">{child_pop}M</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{per_m}</td>'
            f'</tr>\n'
        )

    # Chart data
    cond_labels = json.dumps([c for c, _ in sorted_conds])
    cond_af_vals = json.dumps([g["africa"] for _, g in sorted_conds])
    cond_us_vals = json.dumps([g["us"] for _, g in sorted_conds])

    country_labels = json.dumps(sorted_countries)
    country_ped_vals = json.dumps([data["pediatric_by_country"].get(c, 0) for c in sorted_countries])

    # Summary stats
    total_conds = len(CHILDHOOD_CONDITIONS)
    zero_conds = sum(1 for g in gaps.values() if g["africa"] == 0)
    broad_gap = round(data["broad_pediatric_us"] / data["broad_pediatric_africa"], 1) if data["broad_pediatric_africa"] > 0 else 999
    neonatal_gap = round(data["neonatal_us"] / data["neonatal_africa"], 1) if data["neonatal_africa"] > 0 else 999

    vax_af = data["vaccine_africa"]
    treat_af = data["treatment_africa"]
    vax_us = data["vaccine_us"]
    treat_us = data["treatment_us"]
    vax_ratio_af = round(vax_af / treat_af, 2) if treat_af > 0 else 999
    vax_ratio_us = round(vax_us / treat_us, 2) if treat_us > 0 else 0

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Growing Up Without Evidence: The Child's View of Clinical Trials in Africa</title>
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
.chart-container {{ position:relative; height:380px; margin:20px 0; }}
canvas {{ max-width:100%; }}
.methodology {{ background:rgba(59,130,246,0.05); border:1px solid var(--border); border-radius:10px; padding:20px; margin:30px 0; font-size:0.9rem; color:var(--muted); }}
.methodology h3 {{ color:var(--accent); }}
footer {{ text-align:center; padding:40px 20px; color:var(--muted); font-size:0.8rem; border-top:1px solid var(--border); margin-top:40px; }}
.vs-box {{ display:grid; grid-template-columns:1fr auto 1fr; gap:16px; align-items:center; text-align:center; }}
.vs-label {{ font-size:2rem; font-weight:bold; color:var(--muted); }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="container">

<h1>Born Into an Evidence Desert</h1>
<p class="subtitle">The Child's View: Pediatric Clinical Trials in Africa | Project 50 of the Africa RCT Equity Series</p>

<div class="narrative">
"A child born today in Lagos, Nairobi, or Addis Ababa will face childhood pneumonia, malaria, malnutrition, and sickle cell disease. For most of these conditions, there is almost no clinical trial evidence generated on the continent where these diseases kill the most children. The treatments they receive were tested on children elsewhere -- or never tested on children at all."
</div>

<div class="stat-grid">
  <div class="stat-box">
    <div class="number" style="color:var(--danger);">{data["broad_pediatric_africa"]}</div>
    <div class="label">Pediatric Trials in 10 African Countries</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--accent);">{data["broad_pediatric_us"]}</div>
    <div class="label">Pediatric Trials in the US</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--warning);">{broad_gap}x</div>
    <div class="label">US-to-Africa Pediatric Trial Gap</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--danger);">{zero_conds}/{total_conds}</div>
    <div class="label">Conditions with Zero Africa Trials</div>
  </div>
</div>

<h2>The Childhood Evidence Gap by Condition</h2>
<p style="color:var(--muted);margin-bottom:10px;">For each condition, how many trials exist in Africa vs the US?</p>

<div class="card">
  <div class="chart-container">
    <canvas id="condChart"></canvas>
  </div>
</div>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Childhood Condition</th>
        <th style="text-align:right;">Africa (10 countries)</th>
        <th style="text-align:right;">United States</th>
        <th style="text-align:right;">Evidence Gap</th>
      </tr>
    </thead>
    <tbody>
{cond_rows}
    </tbody>
  </table>
</div>

<h2>The Condition-Country Heatmap</h2>
<p style="color:var(--muted);margin-bottom:10px;">Pediatric trial counts by condition and country -- red = zero, amber = 1-4, green = 5+</p>

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

<h2>The Neonatal Gap</h2>
<p style="color:var(--muted);margin-bottom:10px;">Neonatal sepsis and newborn conditions: where the first 28 days are the most dangerous</p>

<div class="card">
  <div class="vs-box">
    <div>
      <div style="font-size:2.5rem;font-weight:bold;color:var(--danger);">{data["neonatal_africa"]}</div>
      <div style="color:var(--muted);">Neonatal trials in Africa</div>
    </div>
    <div class="vs-label">vs</div>
    <div>
      <div style="font-size:2.5rem;font-weight:bold;color:var(--accent);">{data["neonatal_us"]}</div>
      <div style="color:var(--muted);">Neonatal trials in US</div>
    </div>
  </div>
  <div style="text-align:center;margin-top:16px;color:var(--warning);font-size:1.3rem;font-weight:bold;">
    {neonatal_gap}x gap -- despite Africa having the world's highest neonatal mortality
  </div>
</div>

<h2>Vaccination vs Treatment Trials</h2>
<p style="color:var(--muted);margin-bottom:10px;">Africa's pediatric trials are disproportionately vaccination-focused. Treatment trials for sick children are rare.</p>

<div class="card">
  <div class="stat-grid">
    <div class="stat-box" style="border-left:4px solid var(--warning);">
      <div class="number" style="color:var(--warning);">{vax_af}</div>
      <div class="label">Africa: Pediatric Vaccine Trials</div>
    </div>
    <div class="stat-box" style="border-left:4px solid var(--danger);">
      <div class="number" style="color:var(--danger);">{treat_af}</div>
      <div class="label">Africa: Pediatric Treatment Trials</div>
    </div>
    <div class="stat-box" style="border-left:4px solid var(--accent);">
      <div class="number" style="color:var(--accent);">{vax_us}</div>
      <div class="label">US: Pediatric Vaccine Trials</div>
    </div>
    <div class="stat-box" style="border-left:4px solid var(--success);">
      <div class="number" style="color:var(--success);">{treat_us}</div>
      <div class="label">US: Pediatric Treatment Trials</div>
    </div>
  </div>
  <div style="text-align:center;margin-top:12px;color:var(--muted);">
    Africa vaccine-to-treatment ratio: <strong style="color:var(--warning);">{vax_ratio_af}</strong> |
    US vaccine-to-treatment ratio: <strong style="color:var(--accent);">{vax_ratio_us}</strong>
  </div>
</div>

<h2>A Child in Lagos vs London</h2>
<p style="color:var(--muted);margin-bottom:10px;">Pediatric trials per country -- per million children under 15</p>

<div class="card">
  <div class="chart-container">
    <canvas id="countryChart"></canvas>
  </div>
</div>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">Pediatric Trials</th>
        <th style="text-align:right;">Children (under 15)</th>
        <th style="text-align:right;">Trials per Million Children</th>
      </tr>
    </thead>
    <tbody>
{country_rows}
    </tbody>
  </table>
</div>

<div class="narrative">
"The consent ethics dimension is stark: children cannot consent to being in a trial, but they also cannot consent to being treated with medicines never tested on children like them. In Africa, the latter is the default. Every day, millions of children receive treatments based on evidence from populations with different genetics, different nutrition, different co-infections. Growing up without evidence is not a metaphor -- it is the lived reality of 300 million African children."
</div>

<div class="methodology">
  <h3>Methodology</h3>
  <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (queried {data["timestamp"][:10]})</p>
  <p><strong>Filter:</strong> <code>AREA[StudyType]INTERVENTIONAL</code> with condition-specific queries combining pediatric terms</p>
  <p><strong>Countries:</strong> 10 African nations + United States as comparator</p>
  <p><strong>Conditions:</strong> 10 childhood conditions reflecting Africa's pediatric disease burden</p>
  <p><strong>Childhood Evidence Gap:</strong> Ratio of US to Africa trial counts per condition</p>
  <p><strong>Child population:</strong> UN 2024 estimates for population under 15</p>
  <p><strong>Limitation:</strong> Single registry; pediatric terminology varies. Some trials may include children without pediatric-specific registration terms. Age-eligibility filtering limited to term-based search.</p>
  <p><strong>AI transparency:</strong> LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.</p>
</div>

</div>

<footer>
  Africa RCT Equity Series -- Project 50: The Child's View | ClinicalTrials.gov API v2<br>
  Generated {data["timestamp"][:10]} | Data cached for 24 hours
</footer>

<script>
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
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: 'Childhood Evidence Gap: Africa vs US by Condition', color: '#e2e8f0' }},
      legend: {{ labels: {{ color: '#e2e8f0' }} }}
    }},
    scales: {{
      x: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8' }} }},
      y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }} }}
    }}
  }}
}});

new Chart(document.getElementById('countryChart'), {{
  type: 'bar',
  data: {{
    labels: {country_labels},
    datasets: [{{
      label: 'Pediatric trials',
      data: {country_ped_vals},
      backgroundColor: {country_ped_vals}.map(v => v < 20 ? 'rgba(239,68,68,0.7)' : (v < 100 ? 'rgba(245,158,11,0.7)' : 'rgba(34,197,94,0.7)')),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: 'Pediatric Trials by African Country', color: '#e2e8f0' }}
    }},
    scales: {{
      y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8' }} }},
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
    print("Project 50: The Child's View -- Growing Up Without Evidence")
    print("=" * 70)

    data = fetch_all_data()
    gaps = compute_evidence_gaps(data)

    # Print summary
    print("\n--- Childhood Evidence Gaps ---")
    for cond_name, gap in sorted(gaps.items(), key=lambda x: x[1]["gap_ratio"], reverse=True):
        gap_str = f"{gap['gap_ratio']}x" if gap["gap_ratio"] < 999 else "INF"
        print(f"  {cond_name}: Africa={gap['africa']}, US={gap['us']}, Gap={gap_str}")

    print(f"\nBroad pediatric: Africa={data['broad_pediatric_africa']}, US={data['broad_pediatric_us']}")
    print(f"Neonatal: Africa={data['neonatal_africa']}, US={data['neonatal_us']}")
    print(f"Vaccine (ped): Africa={data['vaccine_africa']}, US={data['vaccine_us']}")
    print(f"Treatment (ped): Africa={data['treatment_africa']}, US={data['treatment_us']}")

    generate_html(data, gaps)
    print("\nDone.")


if __name__ == "__main__":
    main()
