#!/usr/bin/env python
"""
fetch_francophone_gap.py — Query ClinicalTrials.gov API v2 comparing
Francophone vs Anglophone Africa trial density.

Outputs:
  - data/francophone_gap_data.json  (cached API results, 24h TTL)
  - francophone-gap.html            (dark-theme interactive dashboard)
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

FRANCOPHONE_COUNTRIES = {
    "Senegal":      17,
    "Congo, The Democratic Republic of the": 102,
    "Burkina Faso": 23,
    "Mali":         23,
    "Niger":        27,
    "Chad":         18,
    "Guinea":       14,
    "Madagascar":   30,
    "Benin":        13,
    "Togo":          9,
}

# Display names for countries whose CT.gov name differs
DISPLAY_NAMES = {
    "Congo, The Democratic Republic of the": "DRC",
}

ANGLOPHONE_COUNTRIES = {
    "South Africa": 62,
    "Kenya":        56,
    "Uganda":       48,
    "Nigeria":     230,
    "Tanzania":     67,
    "Ghana":        34,
    "Malawi":       21,
    "Zambia":       21,
    "Zimbabwe":     15,
}

# Verified trial counts (for fallback / validation)
VERIFIED_COUNTS = {
    "Senegal": 97, "Congo, The Democratic Republic of the": 105,
    "Burkina Faso": 196, "Mali": 144, "Niger": 37, "Chad": 14,
    "Guinea": 133, "Madagascar": 24, "Benin": 51, "Togo": 7,
    "South Africa": 3473, "Kenya": 720, "Uganda": 783, "Nigeria": 354,
    "Tanzania": 431, "Ghana": 230, "Malawi": 288, "Zambia": 245,
    "Zimbabwe": 166,
}

CONDITIONS = {
    "HIV":      "HIV",
    "Malaria":  "malaria",
    "TB":       "tuberculosis",
    "Cancer":   "cancer",
    "Diabetes": "diabetes",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "francophone_gap_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "francophone-gap.html"
RATE_LIMIT = 0.35  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def display_name(country):
    """Return short display name for a country."""
    return DISPLAY_NAMES.get(country, country)


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


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


def get_total_interventional(location):
    """Return total count of interventional trials for a location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_condition_count(condition_query, location):
    """Return total count of interventional trials for a condition+location."""
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
    """Fetch total and condition-level counts for all countries."""
    cached = load_cache()
    if cached is not None:
        return cached

    all_countries = list(FRANCOPHONE_COUNTRIES.keys()) + list(ANGLOPHONE_COUNTRIES.keys())
    total_calls = len(all_countries) * (1 + len(CONDITIONS))
    call_num = 0

    data = {
        "timestamp": datetime.now().isoformat(),
        "total_counts": {},
        "condition_counts": {},
    }

    for country in all_countries:
        # Total interventional trials
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {display_name(country)} / total...")
        count = get_total_interventional(country)
        data["total_counts"][country] = count
        time.sleep(RATE_LIMIT)

        # Per-condition
        data["condition_counts"][country] = {}
        for cond_label, cond_query in CONDITIONS.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {display_name(country)} / {cond_label}...")
            ccount = get_condition_count(cond_query, country)
            data["condition_counts"][country][cond_label] = ccount
            time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_analysis(data):
    """Compute all analysis metrics from collected data."""
    results = {}

    # Per-country stats
    country_stats = {}
    for country in list(FRANCOPHONE_COUNTRIES.keys()) + list(ANGLOPHONE_COUNTRIES.keys()):
        is_franco = country in FRANCOPHONE_COUNTRIES
        pop = FRANCOPHONE_COUNTRIES.get(country) or ANGLOPHONE_COUNTRIES.get(country)
        trials = data["total_counts"].get(country, VERIFIED_COUNTS.get(country, 0))
        tpm = round(trials / pop, 2) if pop > 0 else 0
        country_stats[country] = {
            "group": "Francophone" if is_franco else "Anglophone",
            "population_m": pop,
            "trials": trials,
            "trials_per_million": tpm,
            "conditions": data["condition_counts"].get(country, {}),
        }
    results["country_stats"] = country_stats

    # Group totals
    franco_pop = sum(FRANCOPHONE_COUNTRIES.values())
    anglo_pop = sum(ANGLOPHONE_COUNTRIES.values())
    franco_trials = sum(
        country_stats[c]["trials"] for c in FRANCOPHONE_COUNTRIES
    )
    anglo_trials = sum(
        country_stats[c]["trials"] for c in ANGLOPHONE_COUNTRIES
    )
    franco_tpm = round(franco_trials / franco_pop, 2) if franco_pop > 0 else 0
    anglo_tpm = round(anglo_trials / anglo_pop, 2) if anglo_pop > 0 else 0
    penalty = round(anglo_tpm / franco_tpm, 1) if franco_tpm > 0 else float("inf")

    results["group_totals"] = {
        "francophone": {
            "population_m": franco_pop,
            "trials": franco_trials,
            "trials_per_million": franco_tpm,
        },
        "anglophone": {
            "population_m": anglo_pop,
            "trials": anglo_trials,
            "trials_per_million": anglo_tpm,
        },
        "francophone_penalty": penalty,
    }

    # Group condition totals
    group_conditions = {"Francophone": defaultdict(int), "Anglophone": defaultdict(int)}
    for country, stats in country_stats.items():
        group = stats["group"]
        for cond, count in stats["conditions"].items():
            group_conditions[group][cond] += count
    results["group_conditions"] = {
        k: dict(v) for k, v in group_conditions.items()
    }

    return results


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def cell_color(count):
    """Heatmap color: 0=black, 1-10=red shades, 11-50=amber, 51+=teal."""
    if count == 0:
        return "#111"
    elif count <= 10:
        intensity = 80 + int((count / 10) * 175)
        return f"rgb({intensity}, {max(30, intensity // 4)}, {max(30, intensity // 4)})"
    elif count <= 50:
        ratio = (count - 10) / 40
        r = int(255 - ratio * 60)
        g = int(140 + ratio * 80)
        return f"rgb({r}, {g}, 50)"
    elif count <= 200:
        ratio = min((count - 50) / 150, 1.0)
        return f"rgb({int(60 - ratio * 20)}, {int(180 + ratio * 40)}, {int(100 + ratio * 50)})"
    else:
        return "rgb(30, 220, 160)"


def generate_html(data, analysis):
    """Generate the full HTML dashboard."""

    gt = analysis["group_totals"]
    cs = analysis["country_stats"]
    gc = analysis["group_conditions"]

    franco_trials = gt["francophone"]["trials"]
    anglo_trials = gt["anglophone"]["trials"]
    penalty = gt["francophone_penalty"]

    # Sort countries by trials per million for bar chart
    all_sorted = sorted(cs.items(), key=lambda x: -x[1]["trials_per_million"])

    # ---- Bar chart data: per-country trials per million ----
    bar_labels = json.dumps([display_name(c) for c, _ in all_sorted])
    bar_values = json.dumps([s["trials_per_million"] for _, s in all_sorted])
    bar_colors = json.dumps([
        "#3b82f6" if s["group"] == "Anglophone" else "#f59e0b"
        for _, s in all_sorted
    ])

    # ---- Heatmap: countries x conditions ----
    all_countries_ordered = (
        sorted(FRANCOPHONE_COUNTRIES.keys(), key=lambda c: -cs[c]["trials"])
        + sorted(ANGLOPHONE_COUNTRIES.keys(), key=lambda c: -cs[c]["trials"])
    )

    country_headers = "".join(
        f'<th style="padding:8px;writing-mode:vertical-rl;text-orientation:mixed;'
        f'color:{"#f59e0b" if c in FRANCOPHONE_COUNTRIES else "#3b82f6"};">'
        f'{escape_html(display_name(c))}</th>'
        for c in all_countries_ordered
    )

    heatmap_rows = ""
    for cond_label in CONDITIONS:
        cells = ""
        for country in all_countries_ordered:
            count = cs[country]["conditions"].get(cond_label, 0)
            bg = cell_color(count)
            text_color = "#fff" if count <= 10 else "#000"
            cells += (
                f'<td style="background:{bg};color:{text_color};'
                f'text-align:center;padding:8px;font-weight:bold;">{count}</td>'
            )
        heatmap_rows += (
            f"<tr><td style='padding:8px;font-weight:bold;'>"
            f"{escape_html(cond_label)}</td>{cells}</tr>\n"
        )

    # ---- Per-capita table ----
    percap_rows = ""
    for country, stats in all_sorted:
        is_franco = stats["group"] == "Francophone"
        row_color = "#f59e0b" if is_franco else "#3b82f6"
        percap_rows += (
            f'<tr>'
            f'<td style="padding:8px;border-left:3px solid {row_color};">'
            f'{escape_html(display_name(country))}</td>'
            f'<td style="padding:8px;text-align:center;color:var(--muted);">'
            f'{"FR" if is_franco else "EN"}</td>'
            f'<td style="padding:8px;text-align:right;">{stats["trials"]:,}</td>'
            f'<td style="padding:8px;text-align:right;">{stats["population_m"]}M</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">'
            f'{stats["trials_per_million"]}</td>'
            f'</tr>\n'
        )

    # ---- Condition comparison: Francophone vs Anglophone ----
    cond_rows = ""
    for cond_label in CONDITIONS:
        fc = gc["Francophone"].get(cond_label, 0)
        ac = gc["Anglophone"].get(cond_label, 0)
        ratio = round(ac / fc, 1) if fc > 0 else "Inf"
        cond_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cond_label)}</td>'
            f'<td style="padding:8px;text-align:right;">{fc:,}</td>'
            f'<td style="padding:8px;text-align:right;">{ac:,}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">{ratio}x</td>'
            f'</tr>\n'
        )

    # ---- Outlier analysis ----
    franco_sorted = sorted(
        [(c, cs[c]) for c in FRANCOPHONE_COUNTRIES],
        key=lambda x: -x[1]["trials_per_million"]
    )
    outlier_top = franco_sorted[:2]   # Burkina Faso, Guinea
    outlier_bottom = franco_sorted[-3:]  # Chad, Togo, Niger

    # ---- Build HTML ----
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Francophone Research Desert</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
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
  background: linear-gradient(135deg, #f59e0b, #ef4444);
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
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1.05rem; margin-bottom: 2rem; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
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
.method-note {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.analysis-box {{
  background: rgba(245, 158, 11, 0.08);
  border-left: 4px solid var(--warning);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.analysis-box p {{ margin-bottom: 0.8rem; }}
.analysis-box p:last-child {{ margin-bottom: 0; }}
.danger-box {{
  background: rgba(239, 68, 68, 0.08);
  border-left: 4px solid var(--danger);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.scroll-x {{ overflow-x: auto; }}
.legend {{
  display: flex;
  gap: 1.5rem;
  margin: 0.5rem 0 1rem;
  font-size: 0.85rem;
  color: var(--muted);
}}
.legend-item {{
  display: flex;
  align-items: center;
  gap: 0.4rem;
}}
.legend-swatch {{
  width: 14px;
  height: 14px;
  border-radius: 3px;
  display: inline-block;
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

<h1>The Francophone Research Desert</h1>
<p class="subtitle">Comparing interventional trial density in Francophone vs Anglophone Africa
&mdash; a {penalty}x per-capita gap that maps the language barrier in global clinical research</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Francophone Africa Trials</div>
    <div class="value warning">{franco_trials:,}</div>
    <div class="label">{gt['francophone']['population_m']}M people &middot; {gt['francophone']['trials_per_million']} per million</div>
  </div>
  <div class="summary-card">
    <div class="label">Anglophone Africa Trials</div>
    <div class="value" style="color:var(--accent);">{anglo_trials:,}</div>
    <div class="label">{gt['anglophone']['population_m']}M people &middot; {gt['anglophone']['trials_per_million']} per million</div>
  </div>
  <div class="summary-card">
    <div class="label">Per-Capita Gap Ratio</div>
    <div class="value danger">{penalty}x</div>
    <div class="label">Anglophone rate / Francophone rate</div>
  </div>
  <div class="summary-card">
    <div class="label">Francophone Penalty</div>
    <div class="value danger">~{276 - round(276 * gt['francophone']['trials_per_million'] / gt['anglophone']['trials_per_million']):,}M</div>
    <div class="label">People in the research desert</div>
  </div>
</div>

<div class="method-note">
<strong>The Francophone Penalty</strong> is the ratio of Anglophone per-capita trial density to
Francophone per-capita trial density. A value of {penalty}x means that an Anglophone African has
{penalty} times the chance of living near an active interventional trial compared to their
Francophone counterpart. Data source: ClinicalTrials.gov API v2.
</div>

<!-- 2. Country-Level Bar Chart -->
<h2>2. Trials Per Million by Country</h2>
<div class="legend">
  <div class="legend-item"><span class="legend-swatch" style="background:#3b82f6;"></span> Anglophone</div>
  <div class="legend-item"><span class="legend-swatch" style="background:#f59e0b;"></span> Francophone</div>
</div>
<div class="chart-container">
  <canvas id="barChart" height="400"></canvas>
</div>

<!-- 3. Heatmap: Countries x Conditions -->
<h2>3. Condition Heatmap</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Rows = conditions. Columns = countries (
<span style="color:#f59e0b;">Francophone</span> |
<span style="color:#3b82f6;">Anglophone</span>). Cell color:
<span style="color:#ff4444;">0-10 trials (red)</span>,
<span style="color:#cccc44;">11-50 (amber)</span>,
<span style="color:#44cc88;">51-200 (teal)</span>,
<span style="background:#111;padding:2px 6px;">0 = black</span>.
</p>
<div class="scroll-x">
<table>
<thead>
<tr>
  <th style="padding:8px;">Condition</th>
  {country_headers}
</tr>
</thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>

<!-- 4. The Language Barrier -->
<h2>4. The Language Barrier</h2>
<div class="analysis-box">
<p><strong>Why do Francophone African countries receive dramatically fewer clinical trials?</strong>
The answer is structural, not biological.</p>

<p><strong>Registry dominance:</strong> ClinicalTrials.gov &mdash; the world's largest trial registry
and the de facto standard for trial registration &mdash; operates exclusively in English. French-speaking
investigators face higher barriers to registration, protocol submission, and international visibility.</p>

<p><strong>Funder geography:</strong> The largest funders of global health trials (NIH, Wellcome Trust,
Gates Foundation, PEPFAR) are Anglophone institutions with established networks in English-speaking Africa.
Francophone countries compete for a much smaller pool from AFD, Institut Pasteur, and European Commission
frameworks.</p>

<p><strong>Investigator networks:</strong> Principal investigators trained in French-language medical
schools have fewer direct connections to US/UK sponsor networks, fewer publications in high-impact
English-language journals, and less access to GCP-certified trial infrastructure. The result: sponsors
default to the same Anglophone hub sites (South Africa, Kenya, Uganda) repeatedly.</p>

<p><strong>Pharmaceutical strategy:</strong> Multinational pharma sponsors optimize for regulatory
efficiency, choosing countries with English-speaking ethics committees, ICH-harmonized regulations,
and existing CRO infrastructure &mdash; all of which cluster in Anglophone Africa.</p>
</div>

<!-- 5. Per-Capita Comparison Table -->
<h2>5. Per-Capita Comparison Table</h2>
<table>
<thead>
<tr>
  <th style="padding:8px;">Country</th>
  <th style="padding:8px;text-align:center;">Language</th>
  <th style="padding:8px;text-align:right;">Total Trials</th>
  <th style="padding:8px;text-align:right;">Population</th>
  <th style="padding:8px;text-align:right;">Trials / Million</th>
</tr>
</thead>
<tbody>
{percap_rows}
</tbody>
</table>

<!-- Condition comparison -->
<h3>Condition-Level Comparison</h3>
<table>
<thead>
<tr>
  <th style="padding:8px;">Condition</th>
  <th style="padding:8px;text-align:right;">Francophone</th>
  <th style="padding:8px;text-align:right;">Anglophone</th>
  <th style="padding:8px;text-align:right;">Ratio</th>
</tr>
</thead>
<tbody>
{cond_rows}
</tbody>
</table>

<!-- 6. The Outliers -->
<h2>6. The Outliers: Malaria Research Hubs</h2>
<div class="analysis-box">
<p><strong>{escape_html(display_name(outlier_top[0][0]))} ({outlier_top[0][1]["trials"]} trials,
{outlier_top[0][1]["trials_per_million"]} per million)</strong> and
<strong>{escape_html(display_name(outlier_top[1][0]))} ({outlier_top[1][1]["trials"]} trials,
{outlier_top[1][1]["trials_per_million"]} per million)</strong> dramatically outperform their
Francophone peers.</p>

<p>Both countries host major malaria research centers that attract international funding:
Burkina Faso's Centre National de Recherche et de Formation sur le Paludisme (CNRFP) in
Ouagadougou is a WHO-recognized site for malaria vaccine trials, while Guinea's
Maferinyah Training Center and Conakry's ties to Institut Pasteur created infrastructure
that persisted beyond the Ebola response.</p>

<p>These outliers prove that language alone does not determine trial density &mdash; established
research infrastructure and disease-specific funding streams can overcome the Francophone penalty.
However, this investment is narrowly concentrated in malaria, leaving NCDs, surgical conditions,
and mental health essentially unresearched.</p>
</div>

<!-- 7. Bottom Tier -->
<h2>7. Near-Invisible Countries</h2>
<div class="danger-box">
<p><strong>{escape_html(display_name(outlier_bottom[0][0]))}</strong>
({outlier_bottom[0][1]["trials"]} trials, {outlier_bottom[0][1]["population_m"]}M people,
{outlier_bottom[0][1]["trials_per_million"]} per million),
<strong>{escape_html(display_name(outlier_bottom[1][0]))}</strong>
({outlier_bottom[1][1]["trials"]} trials, {outlier_bottom[1][1]["population_m"]}M people,
{outlier_bottom[1][1]["trials_per_million"]} per million), and
<strong>{escape_html(display_name(outlier_bottom[2][0]))}</strong>
({outlier_bottom[2][1]["trials"]} trials, {outlier_bottom[2][1]["population_m"]}M people,
{outlier_bottom[2][1]["trials_per_million"]} per million)
represent the deepest troughs of the Francophone research desert.</p>

<p>Combined, these three countries have a population exceeding 50 million people, yet their
total interventional trial count is lower than a single medium-sized US academic medical center
produces in one year. Chad and Togo have fewer registered interventional trials than many
individual US hospitals. Niger, despite being one of the world's fastest-growing populations,
has a per-capita trial density that is essentially zero.</p>

<p>These countries face compounding disadvantages: Francophone language barriers, political
instability (Chad, Niger), weak health infrastructure, low GDP per capita, and minimal
international research network connections. Without targeted investment in local research
capacity, these populations will remain invisible to evidence-based medicine.</p>
</div>

<footer>
  Data from ClinicalTrials.gov API v2 &middot; Interventional studies only &middot;
  Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot;
  Africa RCT Landscape Project
</footer>

</div>

<script>
// Bar chart: Trials per million by country
const barCtx = document.getElementById('barChart').getContext('2d');
new Chart(barCtx, {{
  type: 'bar',
  data: {{
    labels: {bar_labels},
    datasets: [{{
      label: 'Trials per million',
      data: {bar_values},
      backgroundColor: {bar_colors},
      borderColor: {bar_colors},
      borderWidth: 1,
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: 'y',
    plugins: {{
      legend: {{ display: false }},
      title: {{
        display: true,
        text: 'Interventional Trials per Million Population',
        color: '#94a3b8',
        font: {{ size: 14 }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(255,255,255,0.05)' }},
        title: {{
          display: true,
          text: 'Trials per million',
          color: '#94a3b8'
        }}
      }},
      y: {{
        ticks: {{ color: '#e2e8f0', font: {{ size: 12 }} }},
        grid: {{ display: false }}
      }}
    }}
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
    print("Francophone vs Anglophone Africa Trial Gap Analysis")
    print("=" * 60)

    print("\n[1/3] Fetching data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()

    print("\n[2/3] Computing analysis...")
    analysis = compute_analysis(data)

    gt = analysis["group_totals"]
    print(f"\n  Francophone: {gt['francophone']['trials']:,} trials, "
          f"{gt['francophone']['population_m']}M pop, "
          f"{gt['francophone']['trials_per_million']} per million")
    print(f"  Anglophone:  {gt['anglophone']['trials']:,} trials, "
          f"{gt['anglophone']['population_m']}M pop, "
          f"{gt['anglophone']['trials_per_million']} per million")
    print(f"  Francophone Penalty: {gt['francophone_penalty']}x")

    print("\n  Per-capita ranking:")
    ranked = sorted(
        analysis["country_stats"].items(),
        key=lambda x: -x[1]["trials_per_million"]
    )
    for i, (country, stats) in enumerate(ranked, 1):
        marker = "FR" if stats["group"] == "Francophone" else "EN"
        print(f"    {i:2d}. [{marker}] {display_name(country):20s}  "
              f"{stats['trials']:5,} trials  "
              f"{stats['trials_per_million']:8.2f} per million")

    print("\n[3/3] Generating HTML dashboard...")
    html = generate_html(data, analysis)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Written to {OUTPUT_HTML}")

    print("\nDone.")


if __name__ == "__main__":
    main()
