#!/usr/bin/env python
"""
fetch_mother_view.py -- The Mother's View: Pregnancy Without Evidence
=====================================================================
A pregnant woman in sub-Saharan Africa faces the world's highest maternal
mortality risk but the least evidence for her care.

Key metrics:
- Pregnancy/maternal trials: Africa vs US vs UK vs India
- Condition-specific gaps: gestational diabetes, preeclampsia, PPH, etc.
- Contraception/family planning trial gap
- Country-level maternal trial access
- Stillbirth invisibility: near-zero trials for a devastating outcome

Queries ClinicalTrials.gov API v2 for maternal/pregnancy-related interventional
trials across 10 African countries and comparators.

Usage:
    python fetch_mother_view.py

Output:
    data/mother_view_data.json   (cached API results, 24h TTL)
    mother-view.html             (dark-theme interactive dashboard)

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

# Maternal mortality ratio per 100,000 live births (WHO 2020 estimates)
MMR_DATA = {
    "Nigeria": 1047,
    "South Africa": 127,
    "Kenya": 530,
    "Ethiopia": 267,
    "Egypt": 17,
    "Ghana": 263,
    "Uganda": 284,
    "Tanzania": 238,
    "Rwanda": 248,
    "Cameroon": 438,
    "United States": 21,
    "United Kingdom": 10,
    "India": 103,
}

# Women of reproductive age (15-49, millions, UN estimates)
WRA_POPULATION = {
    "Africa_10": 200,  # approximate for these 10 countries
    "United States": 76,
    "United Kingdom": 16,
    "India": 370,
}

# Broad maternal query
MATERNAL_BROAD = "pregnancy OR pregnant OR antenatal OR postnatal OR breastfeeding"

# Specific maternal conditions
MATERNAL_CONDITIONS = {
    "Gestational diabetes": "gestational diabetes",
    "Preeclampsia": "preeclampsia OR pre-eclampsia OR eclampsia",
    "Postpartum hemorrhage": "postpartum hemorrhage OR postpartum haemorrhage OR PPH",
    "Maternal sepsis": "maternal sepsis OR puerperal sepsis OR puerperal fever",
    "Ectopic pregnancy": "ectopic pregnancy",
    "Miscarriage": "miscarriage OR spontaneous abortion OR early pregnancy loss",
    "Stillbirth": "stillbirth OR still birth OR intrauterine fetal death",
}

# Contraception / family planning
CONTRACEPTION_QUERY = "contraception OR family planning OR contraceptive"

# Breastfeeding
BREASTFEEDING_QUERY = "breastfeeding OR breast feeding OR lactation"

COMPARATORS = ["United States", "United Kingdom", "India"]

CACHE_FILE = Path(__file__).resolve().parent / "data" / "mother_view_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "mother-view.html"
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
    """Fetch all mother view data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "broad_maternal": {},
        "condition_africa": {},
        "condition_comparators": {},
        "condition_by_country": {},
        "contraception": {},
        "breastfeeding": {},
        "maternal_by_country": {},
    }

    # --- Broad maternal: Africa vs comparators ---
    print("\n--- Broad maternal trials ---")
    print("  Africa (10 countries)...")
    data["broad_maternal"]["Africa"] = get_trial_count_multi(MATERNAL_BROAD, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)

    for comp in COMPARATORS:
        print(f"  {comp}...")
        data["broad_maternal"][comp] = get_trial_count(MATERNAL_BROAD, comp, use_cond=False)
        time.sleep(RATE_LIMIT)

    # --- Condition-specific: Africa ---
    print("\n--- Maternal conditions: Africa ---")
    for cond_name, cond_query in MATERNAL_CONDITIONS.items():
        print(f"  Africa: {cond_name}...")
        data["condition_africa"][cond_name] = get_trial_count_multi(cond_query, AFRICAN_COUNTRIES, use_cond=True)
        time.sleep(RATE_LIMIT)

    # --- Condition-specific: Comparators ---
    print("\n--- Maternal conditions: Comparators ---")
    for cond_name, cond_query in MATERNAL_CONDITIONS.items():
        data["condition_comparators"][cond_name] = {}
        for comp in COMPARATORS:
            print(f"  {comp}: {cond_name}...")
            data["condition_comparators"][cond_name][comp] = get_trial_count(cond_query, comp, use_cond=True)
            time.sleep(RATE_LIMIT)

    # --- Condition by African country ---
    print("\n--- Maternal conditions by African country ---")
    for cond_name, cond_query in MATERNAL_CONDITIONS.items():
        data["condition_by_country"][cond_name] = {}
        for country in AFRICAN_COUNTRIES:
            print(f"  {country}: {cond_name}...")
            count = get_trial_count(cond_query, country, use_cond=True)
            data["condition_by_country"][cond_name][country] = count
            time.sleep(RATE_LIMIT)

    # --- Contraception ---
    print("\n--- Contraception / family planning ---")
    print("  Africa...")
    data["contraception"]["Africa"] = get_trial_count_multi(CONTRACEPTION_QUERY, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)
    for comp in COMPARATORS:
        print(f"  {comp}...")
        data["contraception"][comp] = get_trial_count(CONTRACEPTION_QUERY, comp, use_cond=False)
        time.sleep(RATE_LIMIT)

    # --- Breastfeeding ---
    print("\n--- Breastfeeding ---")
    print("  Africa...")
    data["breastfeeding"]["Africa"] = get_trial_count_multi(BREASTFEEDING_QUERY, AFRICAN_COUNTRIES, use_cond=False)
    time.sleep(RATE_LIMIT)
    for comp in COMPARATORS:
        print(f"  {comp}...")
        data["breastfeeding"][comp] = get_trial_count(BREASTFEEDING_QUERY, comp, use_cond=False)
        time.sleep(RATE_LIMIT)

    # --- Maternal trials by country ---
    print("\n--- Maternal trials by country ---")
    for country in AFRICAN_COUNTRIES:
        print(f"  {country}...")
        data["maternal_by_country"][country] = get_trial_count(MATERNAL_BROAD, country, use_cond=False)
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_gaps(data):
    """Compute per-condition gaps."""
    gaps = {}
    for cond_name in MATERNAL_CONDITIONS:
        africa = data["condition_africa"].get(cond_name, 0)
        us = data["condition_comparators"].get(cond_name, {}).get("United States", 0)
        uk = data["condition_comparators"].get(cond_name, {}).get("United Kingdom", 0)
        india = data["condition_comparators"].get(cond_name, {}).get("India", 0)
        us_gap = round(us / africa, 1) if africa > 0 else (999 if us > 0 else 0)
        gaps[cond_name] = {
            "africa": africa,
            "us": us,
            "uk": uk,
            "india": india,
            "us_gap": us_gap,
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
    sorted_conds = sorted(gaps.items(), key=lambda x: x[1]["us_gap"], reverse=True)
    for cond_name, g in sorted_conds:
        gap_str = f"{g['us_gap']}x" if g["us_gap"] < 999 else "INF"
        color_af = "#ef4444" if g["africa"] == 0 else ("#f59e0b" if g["africa"] < 10 else "#e2e8f0")
        cond_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cond_name)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color_af};font-weight:bold;">{g["africa"]}</td>'
            f'<td style="padding:8px;text-align:right;">{g["us"]}</td>'
            f'<td style="padding:8px;text-align:right;">{g["uk"]}</td>'
            f'<td style="padding:8px;text-align:right;">{g["india"]}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;color:#f59e0b;">{gap_str}</td>'
            f'</tr>\n'
        )

    # Heatmap: condition x country
    heatmap_rows = ""
    for cond_name in MATERNAL_CONDITIONS:
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
    sorted_countries = sorted(AFRICAN_COUNTRIES, key=lambda c: data["maternal_by_country"].get(c, 0), reverse=True)
    for country in sorted_countries:
        mat_count = data["maternal_by_country"].get(country, 0)
        mmr = MMR_DATA.get(country, 0)
        color = "#ef4444" if mat_count < 20 else ("#f59e0b" if mat_count < 100 else "#e2e8f0")
        mmr_color = "#ef4444" if mmr > 300 else ("#f59e0b" if mmr > 100 else "#22c55e")
        country_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{mat_count}</td>'
            f'<td style="padding:8px;text-align:right;color:{mmr_color};font-weight:bold;">{mmr}</td>'
            f'</tr>\n'
        )

    # Chart data
    cond_labels = json.dumps([c for c, _ in sorted_conds])
    cond_af_vals = json.dumps([g["africa"] for _, g in sorted_conds])
    cond_us_vals = json.dumps([g["us"] for _, g in sorted_conds])

    country_labels = json.dumps(sorted_countries)
    country_mat_vals = json.dumps([data["maternal_by_country"].get(c, 0) for c in sorted_countries])
    country_mmr_vals = json.dumps([MMR_DATA.get(c, 0) for c in sorted_countries])

    # Summary stats
    broad_africa = data["broad_maternal"].get("Africa", 0)
    broad_us = data["broad_maternal"].get("United States", 0)
    broad_uk = data["broad_maternal"].get("United Kingdom", 0)
    broad_india = data["broad_maternal"].get("India", 0)
    broad_gap = round(broad_us / broad_africa, 1) if broad_africa > 0 else 999

    contracep_africa = data["contraception"].get("Africa", 0)
    contracep_us = data["contraception"].get("United States", 0)
    bf_africa = data["breastfeeding"].get("Africa", 0)
    bf_us = data["breastfeeding"].get("United States", 0)

    stillbirth_africa = gaps.get("Stillbirth", {}).get("africa", 0)
    stillbirth_us = gaps.get("Stillbirth", {}).get("us", 0)
    pph_africa = gaps.get("Postpartum hemorrhage", {}).get("africa", 0)
    pph_us = gaps.get("Postpartum hemorrhage", {}).get("us", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Most Dangerous Journey: Pregnancy Without Evidence in Africa</title>
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
  --pink: #ec4899;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Segoe UI',system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
h1 {{ font-size:2rem; margin:30px 0 10px; color:#fff; }}
h2 {{ font-size:1.5rem; margin:40px 0 15px; color:var(--pink); border-bottom:1px solid var(--border); padding-bottom:8px; }}
h3 {{ font-size:1.1rem; margin:20px 0 10px; color:var(--purple); }}
.subtitle {{ color:var(--muted); font-size:1rem; margin-bottom:30px; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; margin:20px 0; }}
.stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin:20px 0; }}
.stat-box {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px; text-align:center; }}
.stat-box .number {{ font-size:2.2rem; font-weight:bold; }}
.stat-box .label {{ font-size:0.85rem; color:var(--muted); margin-top:4px; }}
.narrative {{ background:rgba(236,72,153,0.08); border-left:4px solid var(--pink); padding:20px; margin:20px 0; border-radius:0 10px 10px 0; font-style:italic; color:#f9a8d4; }}
table {{ width:100%; border-collapse:collapse; margin:10px 0; }}
th {{ background:rgba(236,72,153,0.12); padding:10px 8px; text-align:left; font-size:0.85rem; color:var(--pink); border-bottom:2px solid var(--border); }}
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

<h1>The Most Dangerous Journey</h1>
<p class="subtitle">Pregnancy Without Evidence in Africa | Project 51 of the Africa RCT Equity Series</p>

<div class="narrative">
"Every two minutes, a woman dies from complications of pregnancy or childbirth. The vast majority of these deaths occur in sub-Saharan Africa. Yet when we search for clinical trials studying the conditions that kill these women -- postpartum hemorrhage, preeclampsia, maternal sepsis -- the evidence was generated almost entirely elsewhere. The most dangerous journey in the world is navigated with a map drawn by someone who has never walked the path."
</div>

<div class="stat-grid">
  <div class="stat-box">
    <div class="number" style="color:var(--danger);">{broad_africa}</div>
    <div class="label">Maternal/Pregnancy Trials in 10 African Countries</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--accent);">{broad_us}</div>
    <div class="label">Maternal/Pregnancy Trials in the US</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--warning);">{broad_gap}x</div>
    <div class="label">US-to-Africa Maternal Trial Gap</div>
  </div>
  <div class="stat-box">
    <div class="number" style="color:var(--pink);">{contracep_africa}</div>
    <div class="label">Contraception Trials for 600M+ Women</div>
  </div>
</div>

<h2>Condition Breakdown: Where the Evidence Fails Mothers</h2>
<p style="color:var(--muted);margin-bottom:10px;">For each life-threatening maternal condition, how many trials exist in Africa?</p>

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
        <th style="text-align:right;">UK</th>
        <th style="text-align:right;">India</th>
        <th style="text-align:right;">Gap (US/Africa)</th>
      </tr>
    </thead>
    <tbody>
{cond_rows}
    </tbody>
  </table>
</div>

<div class="spotlight">
  <h3>The PPH Crisis: Africa's Leading Killer</h3>
  <p>Postpartum hemorrhage is the single largest cause of maternal death in sub-Saharan Africa, responsible for approximately 34% of maternal deaths. Yet:</p>
  <div class="stat-grid" style="margin-top:12px;">
    <div class="stat-box">
      <div class="number" style="color:var(--danger);">{pph_africa}</div>
      <div class="label">PPH Trials in Africa</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--accent);">{pph_us}</div>
      <div class="label">PPH Trials in the US</div>
    </div>
  </div>
  <p style="color:var(--muted);margin-top:12px;">Women in rural Africa deliver at home without access to uterotonics, and the clinical trial evidence for alternatives comes from settings with operating theatres and blood banks.</p>
</div>

<div class="spotlight">
  <h3>Stillbirth Invisibility</h3>
  <p>Africa has the world's highest stillbirth rate. Yet stillbirth is an invisible outcome in clinical research:</p>
  <div class="stat-grid" style="margin-top:12px;">
    <div class="stat-box">
      <div class="number" style="color:var(--danger);">{stillbirth_africa}</div>
      <div class="label">Stillbirth Trials in Africa</div>
    </div>
    <div class="stat-box">
      <div class="number" style="color:var(--accent);">{stillbirth_us}</div>
      <div class="label">Stillbirth Trials in the US</div>
    </div>
  </div>
  <p style="color:var(--muted);margin-top:12px;">An estimated 2 million babies are stillborn each year globally, nearly half in sub-Saharan Africa. The research response is near-zero.</p>
</div>

<h2>The Condition-Country Heatmap</h2>
<p style="color:var(--muted);margin-bottom:10px;">Maternal trial counts by condition and country -- red = zero, amber = 1-4, green = 5+</p>

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

<h2>The Contraception Gap</h2>
<p style="color:var(--muted);margin-bottom:10px;">Family planning trials for a continent of 600 million women of reproductive age</p>

<div class="card">
  <div class="stat-grid">
    <div class="stat-box" style="border-left:4px solid var(--pink);">
      <div class="number" style="color:var(--pink);">{contracep_africa}</div>
      <div class="label">Africa: Contraception/FP Trials</div>
    </div>
    <div class="stat-box" style="border-left:4px solid var(--accent);">
      <div class="number" style="color:var(--accent);">{contracep_us}</div>
      <div class="label">US: Contraception/FP Trials</div>
    </div>
    <div class="stat-box" style="border-left:4px solid var(--purple);">
      <div class="number" style="color:var(--purple);">{bf_africa}</div>
      <div class="label">Africa: Breastfeeding Trials</div>
    </div>
    <div class="stat-box" style="border-left:4px solid var(--success);">
      <div class="number" style="color:var(--success);">{bf_us}</div>
      <div class="label">US: Breastfeeding Trials</div>
    </div>
  </div>
</div>

<h2>Where Pregnant Women Have the Most/Least Evidence</h2>
<p style="color:var(--muted);margin-bottom:10px;">Maternal trials by African country, alongside maternal mortality ratio (deaths per 100,000 live births)</p>

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
        <th style="text-align:right;">Maternal Trials</th>
        <th style="text-align:right;">MMR (per 100K)</th>
      </tr>
    </thead>
    <tbody>
{country_rows}
    </tbody>
  </table>
  <p style="color:var(--muted);font-size:0.85rem;margin-top:8px;">MMR = Maternal Mortality Ratio (WHO 2020). The inverse relationship -- highest mortality, fewest trials -- is the defining paradox.</p>
</div>

<div class="narrative">
"The contraception gap tells its own story: {contracep_africa} trials for 600 million women of reproductive age across 10 African countries. In the US, with 76 million women, there are {contracep_us}. A pregnant woman in Nigeria faces 50 times the mortality risk of a woman in the US, but has access to a fraction of the evidence. This is not a failure of biology. It is a failure of investment."
</div>

<div class="methodology">
  <h3>Methodology</h3>
  <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (queried {data["timestamp"][:10]})</p>
  <p><strong>Filter:</strong> <code>AREA[StudyType]INTERVENTIONAL</code> with maternal/pregnancy condition queries</p>
  <p><strong>Countries:</strong> 10 African nations + United States, United Kingdom, India as comparators</p>
  <p><strong>Conditions:</strong> 7 specific maternal conditions + contraception + breastfeeding</p>
  <p><strong>MMR data:</strong> WHO 2020 estimates (latest available)</p>
  <p><strong>Limitation:</strong> Single registry; maternal trials may use varied terminology. Some trials may span multiple conditions. MMR data reflects 2020 estimates and may have changed.</p>
  <p><strong>AI transparency:</strong> LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.</p>
</div>

</div>

<footer>
  Africa RCT Equity Series -- Project 51: The Mother's View | ClinicalTrials.gov API v2<br>
  Generated {data["timestamp"][:10]} | Data cached for 24 hours
</footer>

<script>
new Chart(document.getElementById('condChart'), {{
  type: 'bar',
  data: {{
    labels: {cond_labels},
    datasets: [
      {{ label: 'Africa (10 countries)', data: {cond_af_vals}, backgroundColor: 'rgba(236,72,153,0.7)', borderRadius: 4 }},
      {{ label: 'United States', data: {cond_us_vals}, backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 4 }}
    ]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: 'Maternal Condition Trials: Africa vs US', color: '#e2e8f0' }},
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
    datasets: [
      {{
        label: 'Maternal Trials',
        data: {country_mat_vals},
        backgroundColor: 'rgba(236,72,153,0.7)',
        borderRadius: 4,
        yAxisID: 'y',
      }},
      {{
        label: 'MMR (per 100K)',
        data: {country_mmr_vals},
        type: 'line',
        borderColor: '#ef4444',
        backgroundColor: 'rgba(239,68,68,0.1)',
        pointBackgroundColor: '#ef4444',
        tension: 0.3,
        yAxisID: 'y1',
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: 'Maternal Trials vs Maternal Mortality by Country', color: '#e2e8f0' }},
      legend: {{ labels: {{ color: '#e2e8f0' }} }}
    }},
    scales: {{
      y: {{ beginAtZero: true, position: 'left', grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#ec4899' }}, title: {{ display: true, text: 'Trials', color: '#ec4899' }} }},
      y1: {{ beginAtZero: true, position: 'right', grid: {{ drawOnChartArea: false }}, ticks: {{ color: '#ef4444' }}, title: {{ display: true, text: 'MMR per 100K', color: '#ef4444' }} }},
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
    print("Project 51: The Mother's View -- Pregnancy Without Evidence")
    print("=" * 70)

    data = fetch_all_data()
    gaps = compute_gaps(data)

    # Print summary
    print("\n--- Maternal Evidence Gaps ---")
    for cond_name, g in sorted(gaps.items(), key=lambda x: x[1]["us_gap"], reverse=True):
        gap_str = f"{g['us_gap']}x" if g["us_gap"] < 999 else "INF"
        print(f"  {cond_name}: Africa={g['africa']}, US={g['us']}, UK={g['uk']}, Gap={gap_str}")

    print(f"\nBroad maternal: Africa={data['broad_maternal'].get('Africa', 0)}, US={data['broad_maternal'].get('United States', 0)}")
    print(f"Contraception: Africa={data['contraception'].get('Africa', 0)}, US={data['contraception'].get('United States', 0)}")
    print(f"Breastfeeding: Africa={data['breastfeeding'].get('Africa', 0)}, US={data['breastfeeding'].get('United States', 0)}")

    generate_html(data, gaps)
    print("\nDone.")


if __name__ == "__main__":
    main()
