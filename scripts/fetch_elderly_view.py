#!/usr/bin/env python
"""
fetch_elderly_view.py -- The Elderly View: Aging Without a Research Agenda
==========================================================================
Africa's 60+ population will triple to 230M by 2050. Currently near-ZERO
geriatric trials exist on the continent.

Key metrics:
- Geriatric trials: Africa vs US vs Japan
- Age-related condition gaps: dementia, osteoporosis, falls, etc.
- Geriatric Evidence Void per condition
- Country-level geriatric trial access

Queries ClinicalTrials.gov API v2 for geriatric/elderly-related interventional
trials across 10 African countries and comparators.

Usage:
    python fetch_elderly_view.py

Output:
    data/elderly_view_data.json   (cached API results, 24h TTL)
    elderly-view.html             (dark-theme interactive dashboard)

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

# Population 60+ (millions, UN 2024 estimates)
ELDERLY_POPULATION = {
    "Nigeria": 9.5,
    "South Africa": 5.2,
    "Kenya": 2.8,
    "Ethiopia": 5.6,
    "Egypt": 8.5,
    "Ghana": 1.8,
    "Uganda": 1.5,
    "Tanzania": 2.5,
    "Rwanda": 0.6,
    "Cameroon": 1.3,
}
AFRICA_10_ELDERLY = sum(ELDERLY_POPULATION.values())  # ~39.3M

COMPARATOR_ELDERLY = {
    "United States": 78,
    "Japan": 44,
    "United Kingdom": 16,
}

# Broad geriatric query
GERIATRIC_BROAD = "elderly OR geriatric OR aged OR older adults"

# Age-related conditions
GERIATRIC_CONDITIONS = {
    "Dementia/Alzheimer": "dementia OR Alzheimer OR Alzheimer's disease",
    "Osteoporosis": "osteoporosis OR bone density",
    "Falls prevention": "falls OR fall prevention OR accidental falls",
    "Urinary incontinence": "urinary incontinence OR overactive bladder",
    "Polypharmacy": "polypharmacy OR multiple medications OR drug interactions elderly",
    "Frailty": "frailty OR frail elderly OR sarcopenia",
    "Cataracts": "cataract OR cataracts OR lens opacity",
}

COMPARATORS = ["United States", "Japan", "United Kingdom"]

CACHE_FILE = Path(__file__).resolve().parent / "data" / "elderly_view_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "elderly-view.html"
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
    """Fetch all elderly view data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "broad_geriatric": {},
        "condition_africa": {},
        "condition_comparators": {},
        "condition_by_country": {},
        "geriatric_by_country": {},
    }

    # --- Broad geriatric: Africa vs comparators ---
    print("\n--- Broad geriatric trials ---")
    print("  Africa (10 countries)...")
    data["broad_geriatric"]["Africa"] = get_trial_count_multi(GERIATRIC_BROAD, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)

    for comp in COMPARATORS:
        print(f"  {comp}...")
        data["broad_geriatric"][comp] = get_trial_count(GERIATRIC_BROAD, comp, use_cond=False)
        time.sleep(RATE_LIMIT)

    # --- Condition-specific: Africa ---
    print("\n--- Geriatric conditions: Africa ---")
    for cond_name, cond_query in GERIATRIC_CONDITIONS.items():
        print(f"  Africa: {cond_name}...")
        data["condition_africa"][cond_name] = get_trial_count_multi(cond_query, AFRICAN_COUNTRIES, use_cond=True)
        time.sleep(RATE_LIMIT)

    # --- Condition-specific: Comparators ---
    print("\n--- Geriatric conditions: Comparators ---")
    for cond_name, cond_query in GERIATRIC_CONDITIONS.items():
        data["condition_comparators"][cond_name] = {}
        for comp in COMPARATORS:
            print(f"  {comp}: {cond_name}...")
            data["condition_comparators"][cond_name][comp] = get_trial_count(cond_query, comp, use_cond=True)
            time.sleep(RATE_LIMIT)

    # --- Condition by African country ---
    print("\n--- Geriatric conditions by African country ---")
    for cond_name, cond_query in GERIATRIC_CONDITIONS.items():
        data["condition_by_country"][cond_name] = {}
        for country in AFRICAN_COUNTRIES:
            print(f"  {country}: {cond_name}...")
            count = get_trial_count(cond_query, country, use_cond=True)
            data["condition_by_country"][cond_name][country] = count
            time.sleep(RATE_LIMIT)

    # --- Geriatric by country ---
    print("\n--- Geriatric trials by country ---")
    for country in AFRICAN_COUNTRIES:
        print(f"  {country}...")
        data["geriatric_by_country"][country] = get_trial_count(GERIATRIC_BROAD, country, use_cond=False)
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_evidence_void(data):
    """Compute Geriatric Evidence Void per condition."""
    void = {}
    for cond_name in GERIATRIC_CONDITIONS:
        africa = data["condition_africa"].get(cond_name, 0)
        us = data["condition_comparators"].get(cond_name, {}).get("United States", 0)
        japan = data["condition_comparators"].get(cond_name, {}).get("Japan", 0)
        uk = data["condition_comparators"].get(cond_name, {}).get("United Kingdom", 0)
        us_gap = round(us / africa, 1) if africa > 0 else (999 if us > 0 else 0)
        void[cond_name] = {
            "africa": africa,
            "us": us,
            "japan": japan,
            "uk": uk,
            "us_gap": us_gap,
        }
    return void


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

def generate_html(data, void):
    """Generate the full HTML dashboard."""

    # Condition comparison table
    cond_rows = ""
    sorted_conds = sorted(void.items(), key=lambda x: x[1]["us_gap"], reverse=True)
    for cond_name, v in sorted_conds:
        gap_str = f"{v['us_gap']}x" if v["us_gap"] < 999 else "INF"
        color_af = "#ef4444" if v["africa"] == 0 else ("#f59e0b" if v["africa"] < 10 else "#e2e8f0")
        cond_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cond_name)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color_af};font-weight:bold;">{v["africa"]}</td>'
            f'<td style="padding:8px;text-align:right;">{v["us"]}</td>'
            f'<td style="padding:8px;text-align:right;">{v["japan"]}</td>'
            f'<td style="padding:8px;text-align:right;">{v["uk"]}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;color:#f59e0b;">{gap_str}</td>'
            f'</tr>\n'
        )

    # Heatmap: condition x country
    heatmap_rows = ""
    for cond_name in GERIATRIC_CONDITIONS:
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

    # Per-country table
    country_rows = ""
    sorted_countries = sorted(AFRICAN_COUNTRIES, key=lambda c: data["geriatric_by_country"].get(c, 0), reverse=True)
    for country in sorted_countries:
        ger_count = data["geriatric_by_country"].get(country, 0)
        eld_pop = ELDERLY_POPULATION.get(country, 0)
        per_m = round(ger_count / eld_pop, 2) if eld_pop > 0 else 0
        color = "#ef4444" if ger_count < 5 else ("#f59e0b" if ger_count < 20 else "#e2e8f0")
        country_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{ger_count}</td>'
            f'<td style="padding:8px;text-align:right;">{eld_pop}M</td>'
            f'<td style="padding:8px;text-align:right;">{per_m}</td>'
            f'</tr>\n'
        )

    # Chart data
    cond_labels = json.dumps([c for c, _ in sorted_conds])
    cond_af_vals = json.dumps([v["africa"] for _, v in sorted_conds])
    cond_us_vals = json.dumps([v["us"] for _, v in sorted_conds])
    cond_jp_vals = json.dumps([v["japan"] for _, v in sorted_conds])

    country_labels = json.dumps(sorted_countries)
    country_ger_vals = json.dumps([data["geriatric_by_country"].get(c, 0) for c in sorted_countries])

    # Broad comparison chart data
    broad_labels = json.dumps(["Africa (10)", "United States", "Japan", "United Kingdom"])
    broad_vals = json.dumps([
        data["broad_geriatric"].get("Africa", 0),
        data["broad_geriatric"].get("United States", 0),
        data["broad_geriatric"].get("Japan", 0),
        data["broad_geriatric"].get("United Kingdom", 0),
    ])

    # Summary stats
    broad_africa = data["broad_geriatric"].get("Africa", 0)
    broad_us = data["broad_geriatric"].get("United States", 0)
    broad_japan = data["broad_geriatric"].get("Japan", 0)
    broad_uk = data["broad_geriatric"].get("United Kingdom", 0)
    broad_gap_us = round(broad_us / broad_africa, 1) if broad_africa > 0 else 999
    broad_gap_jp = round(broad_japan / broad_africa, 1) if broad_africa > 0 else 999

    zero_conds = sum(1 for v in void.values() if v["africa"] == 0)
    total_conds = len(GERIATRIC_CONDITIONS)

    dementia_africa = void.get("Dementia/Alzheimer", {}).get("africa", 0)
    dementia_us = void.get("Dementia/Alzheimer", {}).get("us", 0)
    dementia_japan = void.get("Dementia/Alzheimer", {}).get("japan", 0)
    falls_africa = void.get("Falls prevention", {}).get("africa", 0)
    falls_us = void.get("Falls prevention", {}).get("us", 0)
    polypharmacy_africa = void.get("Polypharmacy", {}).get("africa", 0)
    polypharmacy_us = void.get("Polypharmacy", {}).get("us", 0)

    africa_per_m = round(broad_africa / AFRICA_10_ELDERLY, 2) if AFRICA_10_ELDERLY > 0 else 0
    us_per_m = round(broad_us / COMPARATOR_ELDERLY["United States"], 2) if COMPARATOR_ELDERLY["United States"] > 0 else 0
    japan_per_m = round(broad_japan / COMPARATOR_ELDERLY["Japan"], 2) if COMPARATOR_ELDERLY["Japan"] > 0 else 0

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invisible Elders: Aging Without a Research Agenda in Africa</title>
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
  --teal: #14b8a6;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Segoe UI',system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
h1 {{ font-size:2rem; margin:30px 0 10px; color:#fff; }}
h2 {{ font-size:1.5rem; margin:40px 0 15px; color:var(--teal); border-bottom:1px solid var(--border); padding-bottom:8px; }}
h3 {{ font-size:1.1rem; margin:20px 0 10px; color:var(--purple); }}
.subtitle {{ color:var(--muted); font-size:1rem; margin-bottom:30px; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; margin:20px 0; }}
.stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin:20px 0; }}
.stat-box {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px; text-align:center; }}
.stat-box .number {{ font-size:2.2rem; font-weight:bold; }}
.stat-box .label {{ font-size:0.85rem; color:var(--muted); margin-top:4px; }}
.narrative {{ background:rgba(20,184,166,0.08); border-left:4px solid var(--teal); padding:20px; margin:20px 0; border-radius:0 10px 10px 0; font-style:italic; color:#5eead4; }}
table {{ width:100%; border-collapse:collapse; margin:10px 0; }}
th {{ background:rgba(20,184,166,0.12); padding:10px 8px; text-align:left; font-size:0.85rem; color:var(--teal); border-bottom:2px solid var(--border); }}
td {{ border-bottom:1px solid var(--border); }}
tr:hover {{ background:rgba(255,255,255,0.02); }}
.chart-container {{ position:relative; height:380px; margin:20px 0; }}
canvas {{ max-width:100%; }}
.methodology {{ background:rgba(59,130,246,0.05); border:1px solid var(--border); border-radius:10px; padding:20px; margin:30px 0; font-size:0.9rem; color:var(--muted); }}
.methodology h3 {{ color:var(--accent); }}
footer {{ text-align:center; padding:40px 20px; color:var(--muted); font-size:0.8rem; border-top:1px solid var(--border); margin-top:40px; }}
.spotlight {{ background:rgba(239,68,68,0.08); border:2px solid var(--danger); border-radius:12px; padding:24px; margin:20px 0; }}
.spotlight h3 {{ color:var(--danger); margin-top:0; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="container">

<h1>Invisible Elders</h1>
<p class="subtitle">Africa's Fastest-Growing Demographic with Zero Research Agenda | Project 52 of the Africa RCT Equity Series</p>

<div class="narrative">
"Africa's population over 60 will triple to 230 million by 2050. They will face dementia, osteoporosis, falls, polypharmacy, and frailty -- conditions for which near-zero clinical trials have been conducted on the continent. A grandmother in Accra who develops Alzheimer's disease will be treated based on evidence generated entirely in Boston, Tokyo, and London. Her genetics, her diet, her social context, her co-morbidities are different. But the research agenda treats her as if she does not exist."
</div>

<div class="stat-grid">
  <div class="stat-box">
    <div class="number" style="color:var(--danger);">{broad_africa}</div>
    <div class="label">Geriatric Trials in 10 African Countries</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--accent);">{broad_us}</div>
    <div class="label">Geriatric Trials in the US</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--teal);">{broad_japan}</div>
    <div class="label">Geriatric Trials in Japan</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--warning);">{broad_gap_us}x</div>
    <div class="label">US-to-Africa Geriatric Gap</div>
  </div>
</div>

<div class="card" style="text-align:center;">
  <div style="font-size:1.5rem;color:var(--muted);margin-bottom:12px;">Geriatric Trials per Million Elderly</div>
  <div class="stat-grid">
    <div class="stat-box">
      <div class="number" style="color:var(--danger);">{africa_per_m}</div>
      <div class="label">Africa ({AFRICA_10_ELDERLY}M elderly)</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--accent);">{us_per_m}</div>
      <div class="label">US ({COMPARATOR_ELDERLY["United States"]}M elderly)</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--teal);">{japan_per_m}</div>
      <div class="label">Japan ({COMPARATOR_ELDERLY["Japan"]}M elderly)</div>
    </div>
  </div>
</div>

<h2>The Global Comparison</h2>

<div class="card">
  <div class="chart-container" style="height:300px;">
    <canvas id="broadChart"></canvas>
  </div>
</div>

<h2>Geriatric Evidence Void by Condition</h2>
<p style="color:var(--muted);margin-bottom:10px;">For each age-related condition, how many trials exist in Africa vs the aging-research leaders?</p>

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
        <th style="text-align:right;">Africa</th>
        <th style="text-align:right;">US</th>
        <th style="text-align:right;">Japan</th>
        <th style="text-align:right;">UK</th>
        <th style="text-align:right;">Gap (US/Africa)</th>
      </tr>
    </thead>
    <tbody>
{cond_rows}
    </tbody>
  </table>
</div>

<div class="spotlight">
  <h3>Dementia Spotlight: Rising Fast, Near-Zero Trials</h3>
  <p>Dementia prevalence in Africa is projected to increase by 235% between 2019 and 2050 -- the highest increase of any world region. Yet the trial landscape is:</p>
  <div class="stat-grid" style="margin-top:12px;">
    <div class="stat-box">
      <div class="number" style="color:var(--danger);">{dementia_africa}</div>
      <div class="label">Dementia Trials in Africa</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--accent);">{dementia_us}</div>
      <div class="label">Dementia Trials in the US</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--teal);">{dementia_japan}</div>
      <div class="label">Dementia Trials in Japan</div>
    </div>
  </div>
  <p style="color:var(--muted);margin-top:12px;">New Alzheimer's drugs like lecanemab and donanemab were developed entirely without African participants. Their efficacy, safety, and dosing in African populations is unknown. The APOE e4 allele -- the strongest genetic risk factor -- has different frequencies and possibly different effects in African populations.</p>
</div>

<div class="spotlight">
  <h3>The Polypharmacy Danger</h3>
  <p>As Africa's elderly population grows, polypharmacy (taking 5+ medications simultaneously) becomes a major safety concern. Most drug-drug interaction data comes from European/American populations:</p>
  <div class="stat-grid" style="margin-top:12px;">
    <div class="stat-box">
      <div class="number" style="color:var(--danger);">{polypharmacy_africa}</div>
      <div class="label">Polypharmacy Trials in Africa</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--accent);">{polypharmacy_us}</div>
      <div class="label">Polypharmacy Trials in the US</div>
    </div>
  </div>
</div>

<div class="spotlight">
  <h3>The Falls Prevention Gap</h3>
  <p>Falls are a leading cause of disability and death in the elderly worldwide. Africa's infrastructure, housing, and healthcare access create unique fall risk profiles -- yet:</p>
  <div class="stat-grid" style="margin-top:12px;">
    <div class="stat-box">
      <div class="number" style="color:var(--danger);">{falls_africa}</div>
      <div class="label">Falls Prevention Trials in Africa</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--accent);">{falls_us}</div>
      <div class="label">Falls Prevention Trials in the US</div>
    </div>
  </div>
</div>

<h2>The Condition-Country Heatmap</h2>
<p style="color:var(--muted);margin-bottom:10px;">Geriatric trial counts by condition and country -- red = zero, amber = 1-4, green = 5+</p>

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

<h2>Geriatric Trials by African Country</h2>
<p style="color:var(--muted);margin-bottom:10px;">Which countries have any geriatric research at all?</p>

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
        <th style="text-align:right;">Geriatric Trials</th>
        <th style="text-align:right;">Population 60+</th>
        <th style="text-align:right;">Trials per Million Elderly</th>
      </tr>
    </thead>
    <tbody>
{country_rows}
    </tbody>
  </table>
</div>

<div class="narrative">
"The invisible elders of Africa face a double burden: they are aging into conditions that the global research enterprise has studied extensively -- but not on them. When a 70-year-old Nigerian man is prescribed a statin, a blood pressure medication, and a diabetes drug, the interaction data comes from populations with different genetics, different diets, and different metabolic profiles. He is, in the most literal sense, an experiment of one -- with no research agenda behind him. By 2050, there will be 230 million such experiments."
</div>

<div class="methodology">
  <h3>Methodology</h3>
  <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (queried {data["timestamp"][:10]})</p>
  <p><strong>Filter:</strong> <code>AREA[StudyType]INTERVENTIONAL</code> with geriatric/elderly condition queries</p>
  <p><strong>Countries:</strong> 10 African nations + United States, Japan, United Kingdom as comparators</p>
  <p><strong>Conditions:</strong> 7 age-related conditions reflecting the geriatric disease burden</p>
  <p><strong>Population data:</strong> UN 2024 estimates for population aged 60+</p>
  <p><strong>Geriatric Evidence Void:</strong> Ratio of US to Africa trial counts per condition</p>
  <p><strong>Limitation:</strong> Single registry; geriatric terminology varies. Some trials may include elderly participants without geriatric-specific registration. Japan comparison limited by possible lower English-language registration on ClinicalTrials.gov.</p>
  <p><strong>AI transparency:</strong> LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.</p>
</div>

</div>

<footer>
  Africa RCT Equity Series -- Project 52: The Elderly View | ClinicalTrials.gov API v2<br>
  Generated {data["timestamp"][:10]} | Data cached for 24 hours
</footer>

<script>
new Chart(document.getElementById('broadChart'), {{
  type: 'bar',
  data: {{
    labels: {broad_labels},
    datasets: [{{
      label: 'Geriatric trials',
      data: {broad_vals},
      backgroundColor: ['rgba(239,68,68,0.7)', 'rgba(59,130,246,0.7)', 'rgba(20,184,166,0.7)', 'rgba(168,85,247,0.7)'],
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: 'Total Geriatric Trials: Africa vs Aging-Focused Nations', color: '#e2e8f0' }}
    }},
    scales: {{
      y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

new Chart(document.getElementById('condChart'), {{
  type: 'bar',
  data: {{
    labels: {cond_labels},
    datasets: [
      {{ label: 'Africa (10 countries)', data: {cond_af_vals}, backgroundColor: 'rgba(239,68,68,0.7)', borderRadius: 4 }},
      {{ label: 'United States', data: {cond_us_vals}, backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 4 }},
      {{ label: 'Japan', data: {cond_jp_vals}, backgroundColor: 'rgba(20,184,166,0.7)', borderRadius: 4 }}
    ]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: 'Geriatric Evidence Void: Africa vs US vs Japan', color: '#e2e8f0' }},
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
      label: 'Geriatric trials',
      data: {country_ger_vals},
      backgroundColor: {country_ger_vals}.map(v => v < 5 ? 'rgba(239,68,68,0.7)' : (v < 20 ? 'rgba(245,158,11,0.7)' : 'rgba(34,197,94,0.7)')),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: 'Geriatric Trials by African Country', color: '#e2e8f0' }}
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
    print("Project 52: The Elderly View -- Aging Without a Research Agenda")
    print("=" * 70)

    data = fetch_all_data()
    void = compute_evidence_void(data)

    # Print summary
    print("\n--- Geriatric Evidence Void ---")
    for cond_name, v in sorted(void.items(), key=lambda x: x[1]["us_gap"], reverse=True):
        gap_str = f"{v['us_gap']}x" if v["us_gap"] < 999 else "INF"
        print(f"  {cond_name}: Africa={v['africa']}, US={v['us']}, Japan={v['japan']}, Gap={gap_str}")

    print(f"\nBroad geriatric: Africa={data['broad_geriatric'].get('Africa', 0)}, "
          f"US={data['broad_geriatric'].get('United States', 0)}, "
          f"Japan={data['broad_geriatric'].get('Japan', 0)}")

    generate_html(data, void)
    print("\nDone.")


if __name__ == "__main__":
    main()
