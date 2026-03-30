#!/usr/bin/env python
"""
fetch_conflict_zones.py — Conflict Zone Exclusion Analysis

Quantifies how populations in African conflict/fragile states are
systematically excluded from clinical trial access compared to
stable African comparators.

Outputs:
  - data/conflict_zones_data.json  (cached API results, 24h TTL)
  - conflict-zone-exclusion.html   (dark-theme interactive dashboard)
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

# Conflict / fragile states with populations (millions, 2025 estimates)
CONFLICT_COUNTRIES = {
    "Somalia":                    18,
    "South Sudan":                11,
    "Central African Republic":   5.5,
    "Chad":                       18,
    "Congo, The Democratic Republic of the": 102,
    "Sudan":                      48,
    "Eritrea":                    3.5,
    "Libya":                      7,
    "Burundi":                    13,
}

# Display names for countries whose API name differs
DISPLAY_NAMES = {
    "Congo, The Democratic Republic of the": "DRC",
}

# Stable comparators
STABLE_COUNTRIES = {
    "Rwanda":    14,
    "Botswana":  2.5,
    "Mauritius": 1.3,
    "Ghana":     34,
    "Senegal":   17,
}

# Known verified trial counts (for reference/validation)
VERIFIED_COUNTS = {
    "Somalia": 8,
    "South Sudan": 6,
    "Central African Republic": 5,
    "Chad": 14,
    "Congo, The Democratic Republic of the": 105,
    "Rwanda": 121,
    "Ghana": 230,
    "Senegal": 97,
}

# Conditions of special interest in conflict zones
CONFLICT_CONDITIONS = {
    "HIV":            "HIV",
    "Malaria":        "malaria",
    "TB":             "tuberculosis",
    "Maternal":       "maternal OR pregnancy OR obstetric",
    "Nutrition":      "nutrition OR malnutrition OR stunting",
    "Mental health":  "mental health OR PTSD OR trauma OR depression",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "conflict_zones_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "conflict-zone-exclusion.html"
RATE_LIMIT = 0.35  # seconds between API calls
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


def get_trial_count(location, condition_query=None):
    """Return total count of interventional trials for a location (+ optional condition)."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition_query:
        params["query.cond"] = condition_query
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


# ---------------------------------------------------------------------------
# Data collection
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


def fetch_all_data():
    """Fetch trial counts for all conflict and stable countries."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "conflict_counts": {},
        "stable_counts": {},
        "conflict_condition_counts": {},
    }

    all_countries = list(CONFLICT_COUNTRIES.keys()) + list(STABLE_COUNTRIES.keys())
    condition_calls = len(CONFLICT_COUNTRIES) * len(CONFLICT_CONDITIONS)
    total_calls = len(all_countries) + condition_calls
    call_num = 0

    # --- Overall trial counts per country ---
    print("\n--- Querying overall trial counts ---")
    for country in CONFLICT_COUNTRIES:
        call_num += 1
        dname = DISPLAY_NAMES.get(country, country)
        print(f"  [{call_num}/{total_calls}] {dname} (conflict)...")
        count = get_trial_count(country)
        data["conflict_counts"][country] = count
        time.sleep(RATE_LIMIT)

    for country in STABLE_COUNTRIES:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} (stable)...")
        count = get_trial_count(country)
        data["stable_counts"][country] = count
        time.sleep(RATE_LIMIT)

    # --- Condition-specific counts for conflict countries ---
    print("\n--- Querying condition-specific counts for conflict countries ---")
    for country in CONFLICT_COUNTRIES:
        data["conflict_condition_counts"][country] = {}
        dname = DISPLAY_NAMES.get(country, country)
        for cond_label, cond_query in CONFLICT_CONDITIONS.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {dname} / {cond_label}...")
            count = get_trial_count(country, cond_query)
            data["conflict_condition_counts"][country][cond_label] = count
            time.sleep(RATE_LIMIT)

    # --- Save cache ---
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nCached to {CACHE_FILE}")

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_per_capita(data):
    """Compute trials per million for each country and group averages."""
    results = {"conflict": {}, "stable": {}}

    for country, pop in CONFLICT_COUNTRIES.items():
        trials = data["conflict_counts"].get(country, 0)
        dname = DISPLAY_NAMES.get(country, country)
        results["conflict"][country] = {
            "display_name": dname,
            "population_m": pop,
            "trials": trials,
            "per_million": round(trials / pop, 2) if pop > 0 else 0,
        }

    for country, pop in STABLE_COUNTRIES.items():
        trials = data["stable_counts"].get(country, 0)
        results["stable"][country] = {
            "display_name": country,
            "population_m": pop,
            "trials": trials,
            "per_million": round(trials / pop, 2) if pop > 0 else 0,
        }

    # Group averages
    conflict_total_trials = sum(v["trials"] for v in results["conflict"].values())
    conflict_total_pop = sum(v["population_m"] for v in results["conflict"].values())
    stable_total_trials = sum(v["trials"] for v in results["stable"].values())
    stable_total_pop = sum(v["population_m"] for v in results["stable"].values())

    results["conflict_avg_per_million"] = (
        round(conflict_total_trials / conflict_total_pop, 2)
        if conflict_total_pop > 0
        else 0
    )
    results["stable_avg_per_million"] = (
        round(stable_total_trials / stable_total_pop, 2)
        if stable_total_pop > 0
        else 0
    )
    results["conflict_total_trials"] = conflict_total_trials
    results["conflict_total_pop"] = conflict_total_pop
    results["stable_total_trials"] = stable_total_trials
    results["stable_total_pop"] = stable_total_pop

    # Conflict Exclusion Score
    if results["conflict_avg_per_million"] > 0:
        results["exclusion_score"] = round(
            results["stable_avg_per_million"] / results["conflict_avg_per_million"], 1
        )
    else:
        results["exclusion_score"] = 999.0

    return results


def compute_condition_profile(data):
    """Condition-specific trial counts for conflict countries."""
    profile = {}
    for country in CONFLICT_COUNTRIES:
        dname = DISPLAY_NAMES.get(country, country)
        conds = data.get("conflict_condition_counts", {}).get(country, {})
        profile[dname] = conds
    return profile


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def rate_color(per_million):
    """Color coding for trials per million."""
    if per_million < 0.5:
        return "#ef4444"  # danger red
    elif per_million < 2:
        return "#f59e0b"  # warning amber
    elif per_million < 5:
        return "#eab308"  # yellow
    else:
        return "#22c55e"  # green


def generate_html(data, per_capita, condition_profile):
    """Generate the full HTML dashboard."""

    conflict_total = per_capita["conflict_total_trials"]
    stable_total = per_capita["stable_total_trials"]
    conflict_pop = per_capita["conflict_total_pop"]
    stable_pop = per_capita["stable_total_pop"]
    conflict_avg = per_capita["conflict_avg_per_million"]
    stable_avg = per_capita["stable_avg_per_million"]
    exclusion_score = per_capita["exclusion_score"]

    # --- Country table rows ---
    country_rows = ""
    # Conflict countries sorted by per-capita (ascending = worst first)
    conflict_sorted = sorted(
        per_capita["conflict"].items(), key=lambda x: x[1]["per_million"]
    )
    for country, info in conflict_sorted:
        color = rate_color(info["per_million"])
        pop_ratio = ""
        if info["trials"] > 0:
            ratio = info["population_m"] * 1_000_000 / info["trials"]
            if ratio >= 1_000_000:
                pop_ratio = f"1 per {ratio / 1_000_000:.1f}M people"
            else:
                pop_ratio = f"1 per {ratio / 1_000:,.0f}K people"
        else:
            pop_ratio = "No trials"
        country_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(info["display_name"])}</td>'
            f'<td style="padding:10px;text-align:right;">{info["population_m"]}M</td>'
            f'<td style="padding:10px;text-align:right;">{info["trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{color};font-weight:bold;">'
            f'{info["per_million"]}</td>'
            f'<td style="padding:10px;text-align:center;">'
            f'<span style="background:#dc2626;color:#fff;padding:2px 10px;'
            f'border-radius:12px;font-size:0.8rem;">CONFLICT</span></td>'
            f'<td style="padding:10px;color:var(--muted);font-size:0.85rem;">'
            f'{pop_ratio}</td>'
            f'</tr>\n'
        )

    # Stable countries sorted by per-capita (descending = best first)
    stable_sorted = sorted(
        per_capita["stable"].items(), key=lambda x: -x[1]["per_million"]
    )
    for country, info in stable_sorted:
        color = rate_color(info["per_million"])
        pop_ratio = ""
        if info["trials"] > 0:
            ratio = info["population_m"] * 1_000_000 / info["trials"]
            if ratio >= 1_000_000:
                pop_ratio = f"1 per {ratio / 1_000_000:.1f}M people"
            else:
                pop_ratio = f"1 per {ratio / 1_000:,.0f}K people"
        else:
            pop_ratio = "No trials"
        country_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(info["display_name"])}</td>'
            f'<td style="padding:10px;text-align:right;">{info["population_m"]}M</td>'
            f'<td style="padding:10px;text-align:right;">{info["trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{color};font-weight:bold;">'
            f'{info["per_million"]}</td>'
            f'<td style="padding:10px;text-align:center;">'
            f'<span style="background:#16a34a;color:#fff;padding:2px 10px;'
            f'border-radius:12px;font-size:0.8rem;">STABLE</span></td>'
            f'<td style="padding:10px;color:var(--muted);font-size:0.85rem;">'
            f'{pop_ratio}</td>'
            f'</tr>\n'
        )

    # --- Condition profile heatmap ---
    cond_headers = "".join(
        f'<th style="padding:8px;writing-mode:vertical-rl;text-orientation:mixed;">'
        f'{escape_html(c)}</th>'
        for c in CONFLICT_CONDITIONS
    )

    cond_rows = ""
    for country_api, pop in CONFLICT_COUNTRIES.items():
        dname = DISPLAY_NAMES.get(country_api, country_api)
        conds = condition_profile.get(dname, {})
        cells = ""
        for cond_label in CONFLICT_CONDITIONS:
            count = conds.get(cond_label, 0)
            if count == 0:
                bg = "#111"
                tc = "#ef4444"
            elif count <= 3:
                bg = "rgba(239,68,68,0.2)"
                tc = "#ef4444"
            elif count <= 10:
                bg = "rgba(245,158,11,0.2)"
                tc = "#f59e0b"
            else:
                bg = "rgba(34,197,94,0.2)"
                tc = "#22c55e"
            cells += (
                f'<td style="background:{bg};color:{tc};text-align:center;'
                f'padding:8px;font-weight:bold;">{count}</td>'
            )
        cond_rows += (
            f'<tr><td style="padding:8px;font-weight:bold;">'
            f'{escape_html(dname)}</td>{cells}</tr>\n'
        )

    # --- Bar chart data ---
    all_countries_sorted = []
    for country, info in per_capita["conflict"].items():
        all_countries_sorted.append(
            (info["display_name"], info["per_million"], "conflict")
        )
    for country, info in per_capita["stable"].items():
        all_countries_sorted.append(
            (info["display_name"], info["per_million"], "stable")
        )
    all_countries_sorted.sort(key=lambda x: x[1])

    bar_labels = json.dumps([c[0] for c in all_countries_sorted])
    bar_values = json.dumps([c[1] for c in all_countries_sorted])
    bar_colors = json.dumps(
        ["#ef4444" if c[2] == "conflict" else "#22c55e" for c in all_countries_sorted]
    )

    # --- Mental health specific counts ---
    mental_health_total = sum(
        condition_profile.get(DISPLAY_NAMES.get(c, c), {}).get("Mental health", 0)
        for c in CONFLICT_COUNTRIES
    )

    # --- Somalia stats ---
    somalia_info = per_capita["conflict"].get("Somalia", {})
    somalia_trials = somalia_info.get("trials", 8)
    somalia_pop = somalia_info.get("population_m", 18)

    # --- DRC stats ---
    drc_key = "Congo, The Democratic Republic of the"
    drc_info = per_capita["conflict"].get(drc_key, {})
    drc_trials = drc_info.get("trials", 105)
    drc_pop = drc_info.get("population_m", 102)
    drc_per_m = drc_info.get("per_million", round(drc_trials / drc_pop, 2))

    # --- Rwanda stats ---
    rwanda_info = per_capita["stable"].get("Rwanda", {})
    rwanda_trials = rwanda_info.get("trials", 121)
    rwanda_pop = rwanda_info.get("population_m", 14)
    rwanda_per_m = rwanda_info.get("per_million", round(rwanda_trials / rwanda_pop, 2))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Conflict Zone Exclusion</title>
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
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  line-height: 1.6;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
h1 {{
  font-size: 2.4rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #ef4444, #f59e0b);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
h2 {{
  font-size: 1.5rem;
  margin: 2.5rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--border);
  color: var(--accent);
}}
h3 {{ font-size: 1.15rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1.05rem; margin-bottom: 2rem; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
  margin-bottom: 2rem;
}}
.summary-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  text-align: center;
}}
.summary-card .value {{
  font-size: 2.5rem;
  font-weight: 800;
  margin: 0.5rem 0;
}}
.summary-card .label {{
  color: var(--muted);
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}
.danger {{ color: var(--danger); }}
.warning {{ color: var(--warning); }}
.success {{ color: var(--success); }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 1rem;
}}
th {{
  background: #1a2332;
  padding: 10px 8px;
  text-align: left;
  font-size: 0.85rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}}
td {{
  border-bottom: 1px solid var(--border);
  padding: 8px;
  font-size: 0.9rem;
}}
tr:hover {{ background: rgba(59, 130, 246, 0.05); }}
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}}
canvas {{ max-width: 100%; }}
.callout {{
  border-radius: 12px;
  padding: 1.5rem 2rem;
  margin: 1.5rem 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.callout-danger {{
  background: rgba(239, 68, 68, 0.08);
  border-left: 4px solid var(--danger);
}}
.callout-warning {{
  background: rgba(245, 158, 11, 0.08);
  border-left: 4px solid var(--warning);
}}
.callout-info {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
}}
.callout-success {{
  background: rgba(34, 197, 94, 0.08);
  border-left: 4px solid var(--success);
}}
.big-stat {{
  font-size: 3rem;
  font-weight: 900;
  display: block;
  margin: 0.5rem 0;
}}
.scroll-x {{ overflow-x: auto; }}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.badge-conflict {{
  background: #dc2626;
  color: #fff;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.8rem;
}}
.badge-stable {{
  background: #16a34a;
  color: #fff;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.8rem;
}}
footer {{
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}}
</style>
</head>
<body>
<div class="container">

<h1>The Conflict Zone Exclusion</h1>
<p class="subtitle">Clinical trial access in African conflict and fragile states
vs stable comparators &mdash; mapping the invisible 226 million</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Conflict Zone Trials</div>
    <div class="value danger">{conflict_total:,}</div>
    <div class="label">{conflict_pop:.0f}M people, 9 countries</div>
  </div>
  <div class="summary-card">
    <div class="label">Stable Comparator Trials</div>
    <div class="value success">{stable_total:,}</div>
    <div class="label">{stable_pop:.0f}M people, 5 countries</div>
  </div>
  <div class="summary-card">
    <div class="label">Conflict per-capita rate</div>
    <div class="value danger">{conflict_avg}</div>
    <div class="label">Trials per million population</div>
  </div>
  <div class="summary-card">
    <div class="label">Stable per-capita rate</div>
    <div class="value success">{stable_avg}</div>
    <div class="label">Trials per million population</div>
  </div>
  <div class="summary-card">
    <div class="label">Conflict Exclusion Score</div>
    <div class="value warning">{exclusion_score}x</div>
    <div class="label">Stable rate / conflict rate</div>
  </div>
  <div class="summary-card">
    <div class="label">Mental health trials in conflict zones</div>
    <div class="value danger">{mental_health_total}</div>
    <div class="label">PTSD / trauma / depression</div>
  </div>
</div>

<div class="callout callout-info">
<strong>Conflict Exclusion Score</strong> = (stable comparator per-capita trial rate) /
(conflict zone per-capita trial rate). A score of 1.0 would mean equal access.
Higher values indicate the degree to which conflict populations are excluded from
clinical research relative to stable African nations.
</div>

<!-- 2. Country Map Table -->
<h2>2. Country Comparison Table</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
All countries sorted by conflict status. Per-capita rate colour:
<span style="color:#ef4444;">below 0.5 (critical)</span>,
<span style="color:#f59e0b;">0.5-2 (low)</span>,
<span style="color:#eab308;">2-5 (moderate)</span>,
<span style="color:#22c55e;">5+ (adequate)</span>.
</p>
<div class="scroll-x">
<table>
<thead>
<tr>
<th>Country</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials</th>
<th style="text-align:right;">Per Million</th>
<th style="text-align:center;">Status</th>
<th>Trial Density</th>
</tr>
</thead>
<tbody>
{country_rows}
</tbody>
</table>
</div>

<!-- 3. Bar Chart -->
<h2>3. Trials per Million: Conflict vs Stable</h2>
<div class="chart-container">
<canvas id="barChart" height="350"></canvas>
</div>

<!-- 4. The Invisible 226 Million -->
<h2>4. The Invisible {conflict_pop:.0f} Million</h2>
<div class="callout callout-danger">
<span class="big-stat danger">{conflict_pop:.0f} million people</span>
<p>live in nine African conflict and fragile states where clinical trial access
is near zero. These populations face the highest disease burden &mdash; infectious
disease, malnutrition, maternal mortality, and trauma &mdash; yet they are
systematically excluded from the evidence base that guides treatment.</p>
<p style="margin-top:0.75rem;">At a combined rate of <strong>{conflict_avg}
trials per million</strong>, versus <strong>{stable_avg} per million</strong>
in stable comparators, conflict zone populations are <strong>{exclusion_score}x
less likely</strong> to be included in a clinical trial.</p>
</div>

<!-- 5. Somalia Spotlight -->
<h2>5. Somalia Spotlight</h2>
<div class="callout callout-danger">
<span class="big-stat danger">{somalia_trials} trials</span>
<p>for <strong>{somalia_pop} million people</strong>. That is approximately
<strong>1 trial per {somalia_pop * 1_000_000 / max(somalia_trials, 1) / 1_000_000:.2f}
million people</strong>.</p>
<p style="margin-top:0.75rem;">Somalia has been in continuous conflict for over
three decades. Its population faces among the highest maternal mortality, malnutrition,
and infectious disease burden on the continent, yet the clinical trial infrastructure
is virtually non-existent. These {somalia_pop} million people are generating
almost no evidence to guide their own care.</p>
</div>

<!-- 6. The DRC Paradox -->
<h2>6. The DRC Paradox</h2>
<div class="callout callout-warning">
<span class="big-stat warning">{drc_trials} trials for {drc_pop}M people</span>
<p>The DRC appears to have reasonable trial activity ({drc_per_m} per million),
but this masks a critical geographic concentration. The vast majority of trials
are located in <strong>Kinshasa and other stable urban centres</strong>, not in the
conflict-affected eastern provinces (North Kivu, South Kivu, Ituri) where an
estimated 25-30 million people live under active armed conflict.</p>
<p style="margin-top:0.75rem;">If eastern DRC were counted separately, its per-capita
trial rate would likely rival Somalia's. The national average conceals one of
the world's largest populations with functionally zero clinical trial access.</p>
</div>

<!-- 7. Condition Heatmap for Conflict Zones -->
<h2>7. Disease-Specific Trials in Conflict Zones</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Condition-specific interventional trial counts across conflict/fragile states.
Cell colour: <span style="color:#ef4444;">0 = black/red</span>,
<span style="color:#f59e0b;">1-3 = amber</span>,
<span style="color:#22c55e;">10+ = green</span>.
</p>
<div class="scroll-x">
<table>
<thead>
<tr>
<th>Country</th>
{cond_headers}
</tr>
</thead>
<tbody>
{cond_rows}
</tbody>
</table>
</div>

<div class="callout callout-danger">
<h3 style="color:var(--danger);margin-top:0;">The Mental Health Gap</h3>
<p>Across all nine conflict/fragile states, there are a total of
<strong>{mental_health_total}</strong> interventional trials for mental health,
PTSD, trauma, or depression. These are populations living through active warfare,
displacement, and mass violence &mdash; conditions that produce epidemic-level
psychological trauma. The near-absence of mental health research in conflict zones
represents one of the most extreme evidence deserts in global health.</p>
</div>

<!-- 8. Rwanda Bright Spot -->
<h2>8. Rwanda: The Bright Spot</h2>
<div class="callout callout-success">
<span class="big-stat success">{rwanda_trials} trials for {rwanda_pop}M people</span>
<p><strong>{rwanda_per_m} trials per million population.</strong></p>
<p style="margin-top:0.75rem;">Rwanda's post-genocide investment in research
infrastructure, health system strengthening, and international partnerships has made
it one of Africa's most trial-dense nations. Rwanda demonstrates that a nation's
conflict history need not permanently exclude it from clinical research &mdash;
but it requires sustained political commitment, institutional capacity building,
and deliberate investment in regulatory frameworks.</p>
<p style="margin-top:0.75rem;">Rwanda's success is the proof that the exclusion
of other conflict-affected states is not inevitable but rather a choice &mdash; a
structural failure of global research investment.</p>
</div>

<!-- 9. Ethical Dimension -->
<h2>9. The Ethical Dimension</h2>
<div class="callout callout-info">
<h3 style="color:var(--accent);margin-top:0;">Populations Most in Need, Systematically Excluded</h3>
<p>Clinical trials serve two purposes: they generate evidence that guides treatment,
and they provide participants with access to novel therapies. Both purposes are
systematically denied to conflict zone populations.</p>
<p style="margin-top:0.75rem;">The consequence is a compounding injustice:
conflict-affected populations bear the highest disease burden, receive the least
research investment, and are then treated using evidence generated in populations
with fundamentally different disease ecology, nutritional status, comorbidity
profiles, and healthcare access. Evidence-based medicine, in these settings,
is based on <em>someone else's evidence</em>.</p>
<p style="margin-top:0.75rem;">The Conflict Exclusion Score of
<strong>{exclusion_score}x</strong> quantifies this structural gap.
Closing it requires dedicated conflict-zone research funding, adaptive trial
designs suited to unstable settings, and partnerships with humanitarian
organisations already operating in these regions.</p>
</div>

<footer>
<p>Data source: ClinicalTrials.gov API v2 | Population estimates: UN World Population
Prospects 2025 | Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>Conflict Exclusion Score = stable per-capita rate / conflict per-capita rate</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {{

  var barCtx = document.getElementById('barChart');
  if (barCtx) {{
    new Chart(barCtx, {{
      type: 'bar',
      data: {{
        labels: {bar_labels},
        datasets: [{{
          label: 'Trials per million population',
          data: {bar_values},
          backgroundColor: {bar_colors},
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{
            display: true,
            text: 'Trials per Million: Red = Conflict, Green = Stable',
            color: '#e2e8f0',
            font: {{ size: 14 }}
          }}
        }},
        scales: {{
          x: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }},
            title: {{ display: true, text: 'Trials per million', color: '#94a3b8' }}
          }},
          y: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0' }}
          }}
        }}
      }}
    }});
  }}

}});
</script>

</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("Conflict Zone Exclusion Analysis")
    print("=" * 60)
    print()

    # Fetch data
    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()

    # Compute analyses
    print("\nComputing per-capita rates...")
    per_capita = compute_per_capita(data)

    print("Computing condition profiles...")
    condition_profile = compute_condition_profile(data)

    # Print summary
    print()
    print("-" * 60)
    print("CONFLICT ZONE TRIAL COUNTS")
    print("-" * 60)
    for country, info in sorted(
        per_capita["conflict"].items(), key=lambda x: x[1]["per_million"]
    ):
        print(
            f"  {info['display_name']:30s}  "
            f"Pop: {info['population_m']:>5.1f}M  "
            f"Trials: {info['trials']:>5,}  "
            f"Per million: {info['per_million']:>6.2f}"
        )
    print()
    print("STABLE COMPARATOR TRIAL COUNTS")
    print("-" * 60)
    for country, info in sorted(
        per_capita["stable"].items(), key=lambda x: -x[1]["per_million"]
    ):
        print(
            f"  {info['display_name']:30s}  "
            f"Pop: {info['population_m']:>5.1f}M  "
            f"Trials: {info['trials']:>5,}  "
            f"Per million: {info['per_million']:>6.2f}"
        )

    print()
    print(f"Conflict average:  {per_capita['conflict_avg_per_million']} per million")
    print(f"Stable average:    {per_capita['stable_avg_per_million']} per million")
    print(f"Exclusion Score:   {per_capita['exclusion_score']}x")

    print()
    print("CONDITION-SPECIFIC COUNTS (CONFLICT ZONES)")
    print("-" * 60)
    for country, conds in sorted(condition_profile.items()):
        counts_str = "  ".join(
            f"{c}: {v}" for c, v in sorted(conds.items())
        )
        print(f"  {country:15s}  {counts_str}")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, per_capita, condition_profile)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
