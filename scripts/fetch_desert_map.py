#!/usr/bin/env python
"""
fetch_desert_map.py -- Complete Research Desert Map for Africa
=============================================================
Queries ClinicalTrials.gov API v2 for 20 African countries x 12 conditions
(240 pairs) and builds a zero-map showing every country-condition pair
with literally zero interventional clinical trials.

Outputs:
    data/desert_map_data.json       -- cached matrix + metadata (24h TTL)
    research-desert-map.html        -- dark-theme interactive heatmap dashboard

Usage:
    python fetch_desert_map.py

Requirements:
    Python 3.8+  (no third-party packages -- uses only stdlib)

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
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "desert_map_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "research-desert-map.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# 20 African countries with populations (millions, ~2025 estimates)
COUNTRIES = {
    "South Africa": 62,
    "Egypt": 110,
    "Kenya": 56,
    "Uganda": 48,
    "Nigeria": 230,
    "Tanzania": 67,
    "Ethiopia": 130,
    "Ghana": 34,
    "Cameroon": 27,
    "Mozambique": 33,
    "Malawi": 21,
    "Zambia": 21,
    "Zimbabwe": 15,
    "Senegal": 17,
    "Rwanda": 14,
    "DRC": 102,
    "Burkina Faso": 23,
    "Mali": 23,
    "Niger": 27,
    "Chad": 18,
}

# Country list preserving insertion order
COUNTRY_LIST = list(COUNTRIES.keys())

# 12 conditions with search queries
CONDITIONS = {
    "HIV": "HIV",
    "Malaria": "malaria",
    "Tuberculosis": "tuberculosis",
    "Cancer": "cancer",
    "Diabetes": "diabetes",
    "Hypertension": "hypertension",
    "Stroke": "stroke",
    "Cardiovascular": "cardiovascular",
    "Mental health": "mental health OR depression",
    "Sickle cell": "sickle cell",
    "Maternal": "maternal OR pregnancy",
    "Neonatal": "neonatal",
}

CONDITION_LIST = list(CONDITIONS.keys())

# Known high-burden pairs (condition -> list of countries with HIGH burden)
# Used to flag "critical zeros"
HIGH_BURDEN = {
    "HIV": ["South Africa", "Nigeria", "Kenya", "Uganda", "Tanzania",
            "Mozambique", "Malawi", "Zambia", "Zimbabwe", "Ethiopia"],
    "Malaria": ["Nigeria", "DRC", "Mozambique", "Uganda", "Burkina Faso",
                "Mali", "Niger", "Tanzania", "Ghana", "Cameroon", "Chad"],
    "Tuberculosis": ["South Africa", "Nigeria", "Ethiopia", "DRC",
                     "Kenya", "Mozambique", "Tanzania"],
    "Cancer": ["Nigeria", "Egypt", "South Africa", "Ethiopia", "DRC",
               "Tanzania", "Kenya"],
    "Diabetes": ["Egypt", "Nigeria", "South Africa", "Ethiopia",
                 "Tanzania", "Kenya"],
    "Hypertension": ["Nigeria", "Egypt", "South Africa", "Ethiopia",
                     "DRC", "Tanzania", "Kenya", "Ghana"],
    "Stroke": ["Nigeria", "Egypt", "South Africa", "Ethiopia",
               "Ghana", "Tanzania"],
    "Cardiovascular": ["Egypt", "Nigeria", "South Africa", "Ethiopia"],
    "Mental health": ["Nigeria", "Ethiopia", "DRC", "South Africa",
                      "Egypt", "Tanzania", "Kenya"],
    "Sickle cell": ["Nigeria", "DRC", "Ghana", "Cameroon", "Tanzania",
                    "Uganda", "Senegal", "Mali", "Burkina Faso", "Niger"],
    "Maternal": ["Nigeria", "Ethiopia", "DRC", "Tanzania", "Kenya",
                 "Uganda", "Mozambique", "Chad", "Niger", "Mali"],
    "Neonatal": ["Nigeria", "Ethiopia", "DRC", "Tanzania", "Kenya",
                 "Uganda", "Chad", "Niger", "Mali"],
}

# US comparison — approximate trial counts for each condition (interventional)
US_TRIALS_APPROX = {
    "HIV": 3800,
    "Malaria": 120,
    "Tuberculosis": 350,
    "Cancer": 42000,
    "Diabetes": 8500,
    "Hypertension": 3200,
    "Stroke": 2800,
    "Cardiovascular": 9500,
    "Mental health": 7200,
    "Sickle cell": 280,
    "Maternal": 4500,
    "Neonatal": 1800,
}


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


def get_trial_count(condition_query, location):
    """Return total count of interventional trials for a condition + location."""
    params = {
        "format": "json",
        "query.cond": condition_query,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_desert_matrix():
    """Query all 240 country x condition pairs and return the count matrix."""
    matrix = {}  # {country: {condition: count}}
    total_queries = len(COUNTRY_LIST) * len(CONDITION_LIST)
    done = 0

    for country in COUNTRY_LIST:
        matrix[country] = {}
        for cond_label, cond_query in CONDITIONS.items():
            done += 1
            # Special handling: DRC needs full name for location search
            locn = "Congo" if country == "DRC" else country
            print(f"  [{done}/{total_queries}] {country} x {cond_label}...", end="")
            count = get_trial_count(cond_query, locn)
            matrix[country][cond_label] = count
            print(f" {count}")
            time.sleep(RATE_LIMIT)

    return matrix


def load_or_fetch():
    """Load cached data or fetch fresh from API."""
    if CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            cached_time = datetime.fromisoformat(raw["fetched_at"].replace("+00:00", ""))
            if datetime.utcnow() - cached_time < timedelta(hours=CACHE_TTL_HOURS):
                print(f"Using cached data from {raw['fetched_at']}")
                return raw
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            print(f"Cache invalid ({exc}), refetching...")

    print(f"Fetching desert map: {len(COUNTRY_LIST)} countries x "
          f"{len(CONDITION_LIST)} conditions = "
          f"{len(COUNTRY_LIST) * len(CONDITION_LIST)} queries")
    print(f"Rate limit: {RATE_LIMIT}s between queries")
    print()

    matrix = fetch_desert_matrix()

    # Also fetch US comparison
    print("\nFetching US comparison counts...")
    us_counts = {}
    for cond_label, cond_query in CONDITIONS.items():
        count = get_trial_count(cond_query, "United States")
        us_counts[cond_label] = count
        print(f"  US x {cond_label}: {count}")
        time.sleep(RATE_LIMIT)

    result = {
        "fetched_at": datetime.utcnow().isoformat(),
        "countries": COUNTRY_LIST,
        "populations": COUNTRIES,
        "conditions": CONDITION_LIST,
        "matrix": matrix,
        "us_counts": us_counts,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nCached to {CACHE_FILE}")
    return result


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(data):
    """Compute desert metrics from the count matrix."""
    matrix = data["matrix"]
    countries = data["countries"]
    conditions = data["conditions"]
    populations = data["populations"]

    total_pairs = len(countries) * len(conditions)

    # All zeros
    zeros = []
    for country in countries:
        for cond in conditions:
            if matrix[country][cond] == 0:
                zeros.append({
                    "country": country,
                    "condition": cond,
                    "population_m": populations[country],
                })

    total_zeros = len(zeros)

    # Zeros per country
    zeros_per_country = {}
    for country in countries:
        z = sum(1 for c in conditions if matrix[country][c] == 0)
        zeros_per_country[country] = z

    # Zeros per condition
    zeros_per_condition = {}
    for cond in conditions:
        z = sum(1 for c in countries if matrix[c][cond] == 0)
        zeros_per_condition[cond] = z

    # Desert score per country = zeros / 12
    desert_scores = {c: zeros_per_country[c] / len(conditions)
                     for c in countries}

    # Critical zeros: zero trials AND high burden
    critical_zeros = []
    for item in zeros:
        country = item["country"]
        cond = item["condition"]
        if country in HIGH_BURDEN.get(cond, []):
            critical_zeros.append(item)

    # Worst country / worst condition
    worst_country = max(countries, key=lambda c: zeros_per_country[c])
    worst_condition = max(conditions, key=lambda c: zeros_per_condition[c])

    # Country ranking by desert score
    desert_ranking = sorted(countries, key=lambda c: -desert_scores[c])

    # Total trials across all pairs
    total_trials = sum(matrix[c][cond] for c in countries for cond in conditions)

    # Population affected by zeros
    pop_in_zeros = sum(populations[z["country"]] for z in zeros)

    return {
        "total_pairs": total_pairs,
        "total_zeros": total_zeros,
        "total_trials": total_trials,
        "zeros": zeros,
        "zeros_per_country": zeros_per_country,
        "zeros_per_condition": zeros_per_condition,
        "desert_scores": desert_scores,
        "desert_ranking": desert_ranking,
        "critical_zeros": critical_zeros,
        "worst_country": worst_country,
        "worst_condition": worst_condition,
        "pop_in_zeros": pop_in_zeros,
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def cell_color(count):
    """Return CSS background color for a trial count."""
    if count == 0:
        return "background:#000;border:2px solid #ff0000;color:#ff4444"
    elif count <= 3:
        return "background:#4a0000;color:#ff6666"
    elif count <= 10:
        return "background:#cc6600;color:#fff"
    elif count <= 50:
        return "background:#cccc00;color:#000"
    else:
        return "background:#228B22;color:#fff"


def cell_class(count):
    """Return CSS class name for a trial count."""
    if count == 0:
        return "cell-zero"
    elif count <= 3:
        return "cell-low"
    elif count <= 10:
        return "cell-med"
    elif count <= 50:
        return "cell-high"
    else:
        return "cell-good"


def generate_html(data, analysis):
    """Generate the research desert map HTML dashboard."""
    matrix = data["matrix"]
    countries = data["countries"]
    conditions = data["conditions"]
    populations = data["populations"]
    us_counts = data.get("us_counts", US_TRIALS_APPROX)

    a = analysis  # shorthand

    # Build heatmap rows
    heatmap_rows = []
    for country in countries:
        cells = []
        for cond in conditions:
            count = matrix[country][cond]
            cls = cell_class(count)
            cells.append(f'<td class="{cls}" title="{country} x {cond}: {count} trials">{count}</td>')
        pop = populations[country]
        ds = a["desert_scores"][country]
        zc = a["zeros_per_country"][country]
        heatmap_rows.append(
            f'<tr><td class="row-label">{country} <span class="pop">({pop}M)</span></td>'
            + "".join(cells)
            + f'<td class="desert-score">{ds:.2f}</td>'
            + f'<td class="zero-count">{zc}/12</td></tr>'
        )

    # Condition header
    cond_headers = "".join(f'<th class="cond-header"><div>{c}</div></th>' for c in conditions)

    # Desert ranking
    ranking_rows = []
    for rank, country in enumerate(a["desert_ranking"], 1):
        ds = a["desert_scores"][country]
        zc = a["zeros_per_country"][country]
        pop = populations[country]
        bar_width = int(ds * 100)
        ranking_rows.append(f"""
        <tr>
            <td class="rank-num">{rank}</td>
            <td class="rank-country">{country} ({pop}M)</td>
            <td class="rank-zeros">{zc}/12</td>
            <td class="rank-score">{ds:.2f}</td>
            <td class="rank-bar"><div class="bar" style="width:{bar_width}%"></div></td>
        </tr>""")

    # Absolute zeros list
    zero_items = []
    for z in sorted(a["zeros"], key=lambda x: (-x["population_m"], x["country"])):
        is_critical = any(
            cz["country"] == z["country"] and cz["condition"] == z["condition"]
            for cz in a["critical_zeros"]
        )
        badge = ' <span class="critical-badge">CRITICAL</span>' if is_critical else ""
        zero_items.append(
            f'<div class="zero-item">'
            f'<span class="zero-country">{z["country"]}</span> '
            f'<span class="zero-x">&times;</span> '
            f'<span class="zero-cond">{z["condition"]}</span> '
            f'<span class="zero-pop">({z["population_m"]}M people)</span>'
            f'{badge}</div>'
        )

    # Critical zeros list
    critical_items = []
    for cz in sorted(a["critical_zeros"], key=lambda x: (-x["population_m"], x["country"])):
        critical_items.append(
            f'<div class="critical-item">'
            f'<span class="crit-country">{cz["country"]}</span> '
            f'<span class="zero-x">&times;</span> '
            f'<span class="crit-cond">{cz["condition"]}</span> '
            f'<span class="crit-pop">({cz["population_m"]}M)</span>'
            f'<span class="crit-label">High burden, ZERO trials</span></div>'
        )

    # Per-condition analysis
    cond_analysis_rows = []
    for cond in sorted(conditions, key=lambda c: -a["zeros_per_condition"][c]):
        zc = a["zeros_per_condition"][cond]
        us_c = us_counts.get(cond, 0)
        # Countries with zero
        zero_countries = [c for c in countries if matrix[c][cond] == 0]
        country_str = ", ".join(zero_countries[:8])
        if len(zero_countries) > 8:
            country_str += f" (+{len(zero_countries) - 8} more)"
        cond_analysis_rows.append(f"""
        <tr>
            <td class="ca-cond">{cond}</td>
            <td class="ca-zeros">{zc}/20</td>
            <td class="ca-us">{us_c:,}</td>
            <td class="ca-countries">{country_str if zero_countries else '<span class="none-text">None</span>'}</td>
        </tr>""")

    # US comparison
    us_zeros = sum(1 for c in conditions if us_counts.get(c, 0) == 0)
    us_total = sum(us_counts.get(c, 0) for c in conditions)
    africa_total = sum(matrix[c][cond] for c in countries for cond in conditions)
    africa_pop = sum(populations[c] for c in countries)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa Research Desert Map</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0e17; color:#e0e0e0; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; line-height:1.6; }}

.container {{ max-width:1400px; margin:0 auto; padding:20px 30px; }}

h1 {{ font-size:2.2em; font-weight:700; margin-bottom:8px; color:#fff;
      background:linear-gradient(90deg,#ff4444,#ff8800,#ffcc00);
      -webkit-background-clip:text; -webkit-text-fill-color:transparent;
      background-clip:text; }}
.subtitle {{ font-size:1.1em; color:#888; margin-bottom:30px; }}

/* Summary cards */
.summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin-bottom:40px; }}
.summary-card {{ background:#131a2b; border:1px solid #1e2a45; border-radius:10px; padding:20px; text-align:center; }}
.summary-card.alert {{ border-color:#ff4444; background:#1a0a0a; }}
.card-value {{ font-size:2.4em; font-weight:800; color:#ff4444; }}
.card-value.green {{ color:#44ff88; }}
.card-label {{ font-size:0.85em; color:#888; margin-top:4px; }}

/* Heatmap */
.heatmap-section {{ margin-bottom:50px; overflow-x:auto; }}
.section-title {{ font-size:1.5em; font-weight:600; color:#fff; margin-bottom:16px; padding-bottom:8px; border-bottom:2px solid #1e2a45; }}
.section-desc {{ color:#999; margin-bottom:20px; font-size:0.95em; }}

.heatmap {{ border-collapse:collapse; width:100%; min-width:900px; }}
.heatmap th {{ background:#0f1520; padding:10px 6px; font-size:0.75em; font-weight:600; color:#aaa; text-transform:uppercase; letter-spacing:0.5px; }}
.heatmap th.corner {{ background:#0f1520; }}
.cond-header {{ writing-mode:vertical-lr; text-orientation:mixed; transform:rotate(180deg); height:110px; vertical-align:bottom; }}
.cond-header div {{ white-space:nowrap; }}

.heatmap td {{ padding:8px 4px; text-align:center; font-size:0.9em; font-weight:600; transition:transform 0.15s; }}
.heatmap td:hover {{ transform:scale(1.15); z-index:10; position:relative; }}

.row-label {{ text-align:left !important; padding-left:12px !important; font-weight:600; color:#ddd; white-space:nowrap; min-width:170px; }}
.pop {{ font-weight:400; color:#666; font-size:0.8em; }}

.cell-zero {{ background:#000 !important; border:2px solid #ff0000 !important; color:#ff4444 !important; }}
.cell-low {{ background:#4a0000; color:#ff6666; }}
.cell-med {{ background:#7a4400; color:#fff; }}
.cell-high {{ background:#8a8a00; color:#000; }}
.cell-good {{ background:#1a6b1a; color:#fff; }}

.desert-score {{ color:#ff8800; font-weight:700; }}
.zero-count {{ color:#ff4444; }}

/* Legend */
.legend {{ display:flex; gap:16px; align-items:center; margin:12px 0 30px; flex-wrap:wrap; }}
.legend-item {{ display:flex; align-items:center; gap:6px; font-size:0.85em; }}
.legend-swatch {{ width:24px; height:18px; border-radius:3px; display:inline-block; }}

/* Desert ranking */
.ranking-section {{ margin-bottom:50px; }}
.ranking-table {{ border-collapse:collapse; width:100%; max-width:800px; }}
.ranking-table th {{ background:#0f1520; padding:10px 12px; text-align:left; font-size:0.8em; color:#888; text-transform:uppercase; }}
.ranking-table td {{ padding:10px 12px; border-bottom:1px solid #1a2035; }}
.rank-num {{ width:40px; color:#666; font-weight:700; }}
.rank-country {{ font-weight:600; color:#ddd; }}
.rank-zeros {{ color:#ff4444; font-weight:700; }}
.rank-score {{ color:#ff8800; font-weight:700; }}
.rank-bar td {{ padding:0; }}
.bar {{ height:20px; background:linear-gradient(90deg,#ff0000,#ff4400,#ff8800); border-radius:0 4px 4px 0; min-width:2px; }}

/* Zero list */
.zeros-section {{ margin-bottom:50px; }}
.zero-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:8px; }}
.zero-item {{ background:#0f1520; border:1px solid #1a2035; border-radius:6px; padding:10px 14px; font-size:0.9em; }}
.zero-country {{ color:#ff8800; font-weight:600; }}
.zero-x {{ color:#555; }}
.zero-cond {{ color:#ff4444; font-weight:600; }}
.zero-pop {{ color:#666; font-size:0.85em; }}
.critical-badge {{ background:#ff0000; color:#fff; font-size:0.7em; padding:2px 6px; border-radius:3px; font-weight:700; margin-left:6px; }}

/* Critical zeros */
.critical-section {{ margin-bottom:50px; }}
.critical-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(380px,1fr)); gap:10px; }}
.critical-item {{ background:#1a0505; border:2px solid #ff0000; border-radius:8px; padding:14px 18px; }}
.crit-country {{ color:#ff8800; font-weight:700; font-size:1.05em; }}
.crit-cond {{ color:#ff4444; font-weight:700; font-size:1.05em; }}
.crit-pop {{ color:#aaa; margin:0 6px; }}
.crit-label {{ display:block; margin-top:4px; color:#ff6666; font-size:0.8em; font-weight:600; text-transform:uppercase; letter-spacing:1px; }}

/* Condition analysis */
.cond-section {{ margin-bottom:50px; }}
.cond-table {{ border-collapse:collapse; width:100%; }}
.cond-table th {{ background:#0f1520; padding:10px 12px; text-align:left; font-size:0.8em; color:#888; text-transform:uppercase; }}
.cond-table td {{ padding:10px 12px; border-bottom:1px solid #1a2035; }}
.ca-cond {{ font-weight:600; color:#ddd; }}
.ca-zeros {{ color:#ff4444; font-weight:700; }}
.ca-us {{ color:#44ff88; }}
.ca-countries {{ color:#999; font-size:0.85em; }}
.none-text {{ color:#555; font-style:italic; }}

/* US comparison */
.comparison-section {{ margin-bottom:50px; }}
.comparison-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; max-width:700px; }}
.comp-card {{ background:#131a2b; border:1px solid #1e2a45; border-radius:10px; padding:24px; text-align:center; }}
.comp-card.us {{ border-color:#44ff88; }}
.comp-card.africa {{ border-color:#ff4444; }}
.comp-val {{ font-size:2em; font-weight:800; }}
.comp-val.us {{ color:#44ff88; }}
.comp-val.africa {{ color:#ff4444; }}
.comp-sub {{ color:#888; font-size:0.85em; margin-top:4px; }}

/* Footer */
.footer {{ margin-top:40px; padding-top:20px; border-top:1px solid #1a2035; color:#555; font-size:0.8em; text-align:center; }}
.footer a {{ color:#4488ff; text-decoration:none; }}

/* Responsive */
@media (max-width:768px) {{
    .container {{ padding:10px; }}
    h1 {{ font-size:1.5em; }}
    .summary-grid {{ grid-template-columns:1fr 1fr; }}
    .comparison-grid {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<div class="container">

<h1>Africa Research Desert Map</h1>
<p class="subtitle">Complete mapping of clinical trial absence: {len(countries)} countries &times; {len(conditions)} conditions &mdash; {a['total_pairs']} pairs queried from ClinicalTrials.gov</p>

<!-- ================================================================== -->
<!-- SECTION 1: Summary -->
<!-- ================================================================== -->

<div class="summary-grid">
    <div class="summary-card alert">
        <div class="card-value">{a['total_zeros']}</div>
        <div class="card-label">Country-condition pairs with ZERO trials (out of {a['total_pairs']})</div>
    </div>
    <div class="summary-card alert">
        <div class="card-value">{a['total_zeros'] * 100 // a['total_pairs']}%</div>
        <div class="card-label">of all pairs are absolute zeros</div>
    </div>
    <div class="summary-card">
        <div class="card-value" style="color:#ff8800">{a['worst_country']}</div>
        <div class="card-label">Worst country ({a['zeros_per_country'][a['worst_country']]}/12 conditions with zero trials)</div>
    </div>
    <div class="summary-card">
        <div class="card-value" style="color:#ff8800">{a['worst_condition']}</div>
        <div class="card-label">Most absent condition ({a['zeros_per_condition'][a['worst_condition']]}/20 countries with zero)</div>
    </div>
    <div class="summary-card">
        <div class="card-value" style="color:#ffcc00">{len(a['critical_zeros'])}</div>
        <div class="card-label">Critical zeros (high burden + zero trials)</div>
    </div>
    <div class="summary-card">
        <div class="card-value green">{a['total_trials']:,}</div>
        <div class="card-label">Total interventional trials across all 240 pairs</div>
    </div>
</div>

<!-- ================================================================== -->
<!-- SECTION 2: THE HEATMAP -->
<!-- ================================================================== -->

<div class="heatmap-section">
    <h2 class="section-title">The Complete Heatmap</h2>
    <p class="section-desc">Each cell shows the number of interventional clinical trials registered on ClinicalTrials.gov. Black cells with red borders = absolute zero.</p>

    <div class="legend">
        <div class="legend-item"><span class="legend-swatch" style="background:#000;border:2px solid #ff0000"></span> 0 trials</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#4a0000"></span> 1-3</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#7a4400"></span> 4-10</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#8a8a00"></span> 11-50</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#1a6b1a"></span> 50+</div>
    </div>

    <table class="heatmap">
    <thead>
        <tr>
            <th class="corner">Country</th>
            {cond_headers}
            <th>Desert Score</th>
            <th>Zeros</th>
        </tr>
    </thead>
    <tbody>
        {"".join(heatmap_rows)}
    </tbody>
    </table>
</div>

<!-- ================================================================== -->
<!-- SECTION 3: Desert Score Ranking -->
<!-- ================================================================== -->

<div class="ranking-section">
    <h2 class="section-title">Desert Score Ranking</h2>
    <p class="section-desc">Countries ranked by proportion of conditions with zero trials. Score 1.00 = no trials for ANY condition; 0.00 = at least one trial for every condition.</p>

    <table class="ranking-table">
    <thead>
        <tr><th>#</th><th>Country</th><th>Zeros</th><th>Score</th><th>Bar</th></tr>
    </thead>
    <tbody>
        {"".join(ranking_rows)}
    </tbody>
    </table>
</div>

<!-- ================================================================== -->
<!-- SECTION 4: The Absolute Zeros -->
<!-- ================================================================== -->

<div class="zeros-section">
    <h2 class="section-title">The Absolute Zeros &mdash; {a['total_zeros']} Pairs with ZERO Trials</h2>
    <p class="section-desc">Every country-condition pair where not a single interventional trial has ever been registered. Sorted by population (most affected first).</p>

    <div class="zero-grid">
        {"".join(zero_items)}
    </div>
</div>

<!-- ================================================================== -->
<!-- SECTION 5: Critical Zeros -->
<!-- ================================================================== -->

<div class="critical-section">
    <h2 class="section-title">Critical Zeros &mdash; {len(a['critical_zeros'])} High-Burden Gaps</h2>
    <p class="section-desc">These are the most egregious gaps: countries where a condition is a major health burden, yet ZERO clinical trials exist.</p>

    <div class="critical-grid">
        {"".join(critical_items) if critical_items else '<div style="color:#666;font-style:italic;">No critical zeros found (all high-burden pairs have at least one trial).</div>'}
    </div>
</div>

<!-- ================================================================== -->
<!-- SECTION 6: Per-Condition Analysis -->
<!-- ================================================================== -->

<div class="cond-section">
    <h2 class="section-title">Per-Condition Analysis</h2>
    <p class="section-desc">Which conditions are most absent across the 20 African countries? US counts shown for comparison.</p>

    <table class="cond-table">
    <thead>
        <tr><th>Condition</th><th>Countries with Zero</th><th>US Trials</th><th>Countries with Zero Trials</th></tr>
    </thead>
    <tbody>
        {"".join(cond_analysis_rows)}
    </tbody>
    </table>
</div>

<!-- ================================================================== -->
<!-- SECTION 7: US Comparison -->
<!-- ================================================================== -->

<div class="comparison-section">
    <h2 class="section-title">The Comparison That Matters</h2>
    <p class="section-desc">How many condition-zeros would the United States have across these same 12 conditions?</p>

    <div class="comparison-grid">
        <div class="comp-card us">
            <div class="comp-val us">{us_zeros}</div>
            <div class="comp-sub">US conditions with zero trials (out of 12)</div>
            <div class="comp-sub" style="margin-top:8px;">Total US trials: <strong style="color:#44ff88">{us_total:,}</strong></div>
            <div class="comp-sub">Population: 335M</div>
        </div>
        <div class="comp-card africa">
            <div class="comp-val africa">{a['total_zeros']}</div>
            <div class="comp-sub">Africa country-condition zeros (out of {a['total_pairs']})</div>
            <div class="comp-sub" style="margin-top:8px;">Total Africa trials: <strong style="color:#ff4444">{africa_total:,}</strong></div>
            <div class="comp-sub">Combined population: {africa_pop:,}M</div>
        </div>
    </div>

    <div style="margin-top:20px;padding:20px;background:#131a2b;border-radius:10px;max-width:700px;">
        <p style="color:#ccc;font-size:0.95em;">
            The United States, with <strong>{335}M</strong> people, has <strong style="color:#44ff88">{us_total:,}</strong>
            interventional trials across these 12 conditions and <strong style="color:#44ff88">{us_zeros}</strong> zeros.
        </p>
        <p style="color:#ccc;font-size:0.95em;margin-top:10px;">
            These 20 African countries, with a combined <strong>{africa_pop:,}M</strong> people, have
            <strong style="color:#ff4444">{africa_total:,}</strong> trials and
            <strong style="color:#ff4444">{a['total_zeros']}</strong> absolute zeros.
        </p>
        <p style="color:#ff8800;font-size:0.95em;margin-top:10px;font-weight:600;">
            Trials per million people &mdash; US: {us_total * 1000 // 335:,.0f} | Africa-20: {africa_total * 1000 // africa_pop if africa_pop > 0 else 0:,.0f}
        </p>
    </div>
</div>

<!-- Footer -->
<div class="footer">
    <p>Data: ClinicalTrials.gov API v2 (interventional studies only) | Fetched: {data['fetched_at'][:10]}</p>
    <p>20 countries &times; 12 conditions = {a['total_pairs']} pairs | Africa Research Desert Map &copy; 2026</p>
</div>

</div>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Africa Research Desert Map")
    print("=" * 60)
    print()

    data = load_or_fetch()

    # Save analysis
    analysis = analyze(data)

    # Store analysis alongside raw data
    enriched = {**data, "analysis_summary": {
        "total_zeros": analysis["total_zeros"],
        "total_pairs": analysis["total_pairs"],
        "total_trials": analysis["total_trials"],
        "zeros_per_country": analysis["zeros_per_country"],
        "zeros_per_condition": analysis["zeros_per_condition"],
        "desert_scores": analysis["desert_scores"],
        "desert_ranking": analysis["desert_ranking"],
        "worst_country": analysis["worst_country"],
        "worst_condition": analysis["worst_condition"],
        "critical_zeros_count": len(analysis["critical_zeros"]),
        "pop_in_zeros_m": analysis["pop_in_zeros"],
    }}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(enriched, indent=2), encoding="utf-8")

    # Generate HTML
    html = generate_html(data, analysis)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nHTML dashboard: {OUTPUT_HTML}")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"DESERT MAP SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total pairs queried:    {analysis['total_pairs']}")
    print(f"Total ZERO pairs:       {analysis['total_zeros']} "
          f"({analysis['total_zeros'] * 100 // analysis['total_pairs']}%)")
    print(f"Total trials found:     {analysis['total_trials']:,}")
    print(f"Critical zeros:         {len(analysis['critical_zeros'])}")
    print(f"Worst country:          {analysis['worst_country']} "
          f"({analysis['zeros_per_country'][analysis['worst_country']]}/12 zeros)")
    print(f"Most absent condition:  {analysis['worst_condition']} "
          f"({analysis['zeros_per_condition'][analysis['worst_condition']]}/20 zeros)")
    print(f"\nDesert Score Ranking (top 10):")
    for i, c in enumerate(analysis["desert_ranking"][:10], 1):
        ds = analysis["desert_scores"][c]
        zc = analysis["zeros_per_country"][c]
        print(f"  {i:2d}. {c:20s}  {ds:.2f}  ({zc}/12 zeros)")

    if analysis["critical_zeros"]:
        print(f"\nCritical Zeros (high burden + zero trials):")
        for cz in analysis["critical_zeros"][:15]:
            print(f"  - {cz['country']} x {cz['condition']} ({cz['population_m']}M)")

    print(f"\nDone. Open {OUTPUT_HTML} in a browser.")


if __name__ == "__main__":
    main()
