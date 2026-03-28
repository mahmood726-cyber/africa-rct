#!/usr/bin/env python
"""
fetch_ncd_gap.py — Query ClinicalTrials.gov API v2 for NCD trials in Africa
and compute the Condition Colonialism Index (CCI).

Outputs:
  - data/ncd_gap_data.json  (cached API results, 24h TTL)
  - ncd-gap-analysis.html   (dark-theme interactive dashboard)
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

AFRICAN_COUNTRIES = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria",
    "Tanzania", "Ethiopia", "Ghana", "Malawi", "Zambia",
]

COMPARATOR = "United States"

# Population in millions (2025 estimates)
POPULATIONS = {
    "South Africa": 62,
    "Egypt": 110,
    "Kenya": 56,
    "Uganda": 48,
    "Nigeria": 230,
    "Tanzania": 67,
    "Ethiopia": 130,
    "Ghana": 34,
    "Malawi": 21,
    "Zambia": 21,
    "United States": 335,
}

NCD_CONDITIONS = {
    "CVD":            "cardiovascular OR heart failure",
    "Hypertension":   "hypertension",
    "Diabetes":       "diabetes",
    "Stroke":         "stroke",
    "Cancer":         "cancer",
    "CKD":            "chronic kidney",
    "COPD/Asthma":    "COPD OR asthma",
    "Mental health":  "mental health OR depression",
    "Epilepsy":       "epilepsy",
    "Sickle cell":    "sickle cell",
}

# WHO burden estimates: Africa's share of global disease burden (%)
WHO_BURDEN_AFRICA = {
    "CVD":           24,
    "Hypertension":  38,
    "Diabetes":      25,
    "Stroke":        32,
    "Cancer":        10,
    "Mental health": 20,
    "Sickle cell":   75,
    "CKD":           15,
    "COPD/Asthma":   12,
    "Epilepsy":      30,
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "ncd_gap_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "ncd-gap-analysis.html"
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


def get_trial_count(condition_query, location):
    """Return total count of interventional NCD trials for a condition+location."""
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


def get_trial_details(condition_query, locations, page_size=100):
    """Fetch trial-level data for Africa-wide queries."""
    location_str = " OR ".join(locations)
    params = {
        "format": "json",
        "query.cond": condition_query,
        "query.locn": location_str,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": page_size,
        "countTotal": "true",
        "fields": (
            "NCTId,BriefTitle,Phase,OverallStatus,"
            "LeadSponsorName,LeadSponsorClass,StartDate,EnrollmentCount"
        ),
    }
    data = api_get(params)
    if data is None:
        return []
    studies = data.get("studies", [])
    results = []
    for study in studies:
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        enroll_mod = design.get("enrollmentInfo", {})

        # Phase
        phases_list = design.get("phases", [])
        phase_str = ", ".join(phases_list) if phases_list else "Not specified"

        # Sponsor
        lead_sponsor = sponsor_mod.get("leadSponsor", {})

        # Start date
        start_info = status_mod.get("startDateStruct", {})
        start_date = start_info.get("date", "")

        results.append({
            "nctId": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "phase": phase_str,
            "status": status_mod.get("overallStatus", ""),
            "sponsorName": lead_sponsor.get("name", ""),
            "sponsorClass": lead_sponsor.get("class", ""),
            "startDate": start_date,
            "enrollment": enroll_mod.get("count", 0),
        })
    return results


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
    """Fetch counts per country-condition and trial details for Africa."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "country_condition_counts": {},
        "us_condition_counts": {},
        "africa_trial_details": {},
    }

    # --- Per-country counts for each NCD condition ---
    all_countries = AFRICAN_COUNTRIES + [COMPARATOR]
    total_calls = len(all_countries) * len(NCD_CONDITIONS) + len(NCD_CONDITIONS)
    call_num = 0

    for country in AFRICAN_COUNTRIES:
        data["country_condition_counts"][country] = {}
        for cond_label, cond_query in NCD_CONDITIONS.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {cond_label}...")
            count = get_trial_count(cond_query, country)
            data["country_condition_counts"][country][cond_label] = count
            time.sleep(RATE_LIMIT)

    # --- US comparator counts ---
    for cond_label, cond_query in NCD_CONDITIONS.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {COMPARATOR} / {cond_label}...")
        count = get_trial_count(cond_query, COMPARATOR)
        data["us_condition_counts"][cond_label] = count
        time.sleep(RATE_LIMIT)

    # --- Africa-wide trial details per condition ---
    for cond_label, cond_query in NCD_CONDITIONS.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa details / {cond_label}...")
        details = get_trial_details(cond_query, AFRICAN_COUNTRIES)
        data["africa_trial_details"][cond_label] = details
        time.sleep(RATE_LIMIT)

    # --- Save cache ---
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cached to {CACHE_FILE}")

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_cci(data):
    """Compute Condition Colonialism Index per condition.

    CCI = (Africa burden %) / (Africa trial share %)
    Africa trial share = Africa trials / (Africa + US trials) * 100
    """
    cci_results = {}
    for cond_label in NCD_CONDITIONS:
        africa_total = sum(
            data["country_condition_counts"].get(c, {}).get(cond_label, 0)
            for c in AFRICAN_COUNTRIES
        )
        us_total = data["us_condition_counts"].get(cond_label, 0)
        total = africa_total + us_total
        if total == 0:
            trial_share = 0
        else:
            trial_share = (africa_total / total) * 100

        burden = WHO_BURDEN_AFRICA.get(cond_label, 0)
        if trial_share > 0:
            cci = burden / trial_share
        else:
            cci = float("inf")

        cci_results[cond_label] = {
            "africa_trials": africa_total,
            "us_trials": us_total,
            "trial_share_pct": round(trial_share, 2),
            "burden_pct": burden,
            "cci": round(cci, 2) if cci != float("inf") else 999.0,
        }
    return cci_results


def compute_per_capita(data):
    """Trials per million population per country."""
    results = {}
    for country in AFRICAN_COUNTRIES + [COMPARATOR]:
        if country == COMPARATOR:
            total = sum(data["us_condition_counts"].values())
        else:
            total = sum(data["country_condition_counts"].get(country, {}).values())
        pop = POPULATIONS.get(country, 1)
        results[country] = {
            "total_trials": total,
            "population_m": pop,
            "trials_per_million": round(total / pop, 2),
        }
    return results


def compute_phase_distribution(data):
    """Phase distribution across all Africa NCD trials."""
    phase_counts = defaultdict(int)
    for cond_label, trials in data.get("africa_trial_details", {}).items():
        for t in trials:
            phase_counts[t.get("phase", "Not specified")] += 1
    return dict(phase_counts)


def compute_sponsor_analysis(data):
    """Sponsor class distribution."""
    sponsor_class_counts = defaultdict(int)
    top_sponsors = defaultdict(int)
    for cond_label, trials in data.get("africa_trial_details", {}).items():
        for t in trials:
            cls = t.get("sponsorClass", "OTHER")
            sponsor_class_counts[cls] += 1
            name = t.get("sponsorName", "Unknown")
            top_sponsors[name] += 1
    top_10 = sorted(top_sponsors.items(), key=lambda x: -x[1])[:10]
    return {
        "by_class": dict(sponsor_class_counts),
        "top_sponsors": top_10,
    }


def find_missing_trials(data):
    """Country-condition pairs with zero trials."""
    missing = []
    low = []
    for country in AFRICAN_COUNTRIES:
        for cond_label in NCD_CONDITIONS:
            count = data["country_condition_counts"].get(country, {}).get(cond_label, 0)
            if count == 0:
                missing.append({"country": country, "condition": cond_label})
            elif count <= 3:
                low.append({"country": country, "condition": cond_label, "count": count})
    return {"zero": missing, "low": low}


def compute_temporal_trend(data):
    """Trials by start year (from detail data)."""
    year_counts = defaultdict(int)
    for cond_label, trials in data.get("africa_trial_details", {}).items():
        for t in trials:
            sd = t.get("startDate", "")
            if sd:
                try:
                    year = int(sd[:4])
                    if 2000 <= year <= 2030:
                        year_counts[year] += 1
                except (ValueError, IndexError):
                    pass
    return dict(sorted(year_counts.items()))


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def cell_color(count):
    """Heatmap color: 0=black, 1-5=red, 6-20=yellow, 20+=green."""
    if count == 0:
        return "#111"
    elif count <= 5:
        intensity = 80 + int((count / 5) * 175)
        return f"rgb({intensity}, {max(30, intensity // 4)}, {max(30, intensity // 4)})"
    elif count <= 20:
        ratio = (count - 5) / 15
        r = int(255 - ratio * 100)
        g = int(120 + ratio * 135)
        return f"rgb({r}, {g}, 40)"
    else:
        return "rgb(50, 200, 80)"


def generate_html(data, cci, per_capita, phases, sponsors, missing, trends):
    """Generate the full HTML dashboard."""

    total_africa_trials = sum(
        sum(conds.values())
        for conds in data["country_condition_counts"].values()
    )
    cci_values = [v["cci"] for v in cci.values() if v["cci"] < 999]
    cci_min = min(cci_values) if cci_values else 0
    cci_max = max(cci_values) if cci_values else 0
    worst_conditions = sorted(cci.items(), key=lambda x: -x[1]["cci"])[:3]
    worst_names = ", ".join(c[0] for c in worst_conditions)

    # Sort CCI for bar chart
    cci_sorted = sorted(cci.items(), key=lambda x: -x[1]["cci"])

    # Heatmap rows
    heatmap_rows = ""
    for cond_label in NCD_CONDITIONS:
        cells = ""
        for country in AFRICAN_COUNTRIES:
            count = data["country_condition_counts"].get(country, {}).get(cond_label, 0)
            bg = cell_color(count)
            text_color = "#fff" if count <= 5 else "#000"
            cells += (
                f'<td style="background:{bg};color:{text_color};'
                f'text-align:center;padding:8px;font-weight:bold;">{count}</td>'
            )
        cci_val = cci.get(cond_label, {}).get("cci", 0)
        cci_color = "#ff4444" if cci_val > 5 else "#ffaa33" if cci_val > 2 else "#44cc66"
        cells += (
            f'<td style="background:{cci_color};color:#000;text-align:center;'
            f'padding:8px;font-weight:bold;">{cci_val}</td>'
        )
        heatmap_rows += f"<tr><td style='padding:8px;font-weight:bold;'>{escape_html(cond_label)}</td>{cells}</tr>\n"

    # Country headers
    country_headers = "".join(
        f'<th style="padding:8px;writing-mode:vertical-rl;text-orientation:mixed;">'
        f'{escape_html(c)}</th>'
        for c in AFRICAN_COUNTRIES
    )

    # CCI bar chart data
    cci_bar_labels = json.dumps([c[0] for c in cci_sorted])
    cci_bar_values = json.dumps([c[1]["cci"] for c in cci_sorted])
    cci_bar_colors = json.dumps([
        "#ff4444" if c[1]["cci"] > 5 else "#ffaa33" if c[1]["cci"] > 2 else "#44cc66"
        for c in cci_sorted
    ])

    # Per-capita table
    per_capita_sorted = sorted(per_capita.items(), key=lambda x: -x[1]["trials_per_million"])
    percap_rows = ""
    for country, info in per_capita_sorted:
        is_us = country == COMPARATOR
        row_style = "background:#1a1a2e;" if is_us else ""
        percap_rows += (
            f'<tr style="{row_style}">'
            f'<td style="padding:8px;">{escape_html(country)}'
            f'{"  (comparator)" if is_us else ""}</td>'
            f'<td style="padding:8px;text-align:right;">{info["total_trials"]:,}</td>'
            f'<td style="padding:8px;text-align:right;">{info["population_m"]}M</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">'
            f'{info["trials_per_million"]}</td>'
            f'</tr>\n'
        )

    # US comparison table
    us_comp_rows = ""
    for cond_label in NCD_CONDITIONS:
        africa_n = cci[cond_label]["africa_trials"]
        us_n = cci[cond_label]["us_trials"]
        ratio = round(us_n / africa_n, 1) if africa_n > 0 else "Inf"
        us_comp_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cond_label)}</td>'
            f'<td style="padding:8px;text-align:right;">{africa_n:,}</td>'
            f'<td style="padding:8px;text-align:right;">{us_n:,}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">{ratio}x</td>'
            f'<td style="padding:8px;text-align:right;">{cci[cond_label]["burden_pct"]}%</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">'
            f'{cci[cond_label]["cci"]}</td>'
            f'</tr>\n'
        )

    # Phase distribution
    phase_rows = ""
    total_phase = sum(phases.values()) if phases else 1
    for phase, count in sorted(phases.items()):
        pct = round(count / total_phase * 100, 1)
        is_p3 = "PHASE3" in phase.upper().replace(" ", "")
        highlight = "color:#ff6644;font-weight:bold;" if is_p3 else ""
        phase_rows += (
            f'<tr><td style="padding:8px;{highlight}">{escape_html(phase)}</td>'
            f'<td style="padding:8px;text-align:right;">{count}</td>'
            f'<td style="padding:8px;text-align:right;">{pct}%</td></tr>\n'
        )

    # Sponsor rows
    sponsor_class_rows = ""
    for cls, count in sorted(sponsors["by_class"].items(), key=lambda x: -x[1]):
        sponsor_class_rows += (
            f'<tr><td style="padding:8px;">{escape_html(cls)}</td>'
            f'<td style="padding:8px;text-align:right;">{count}</td></tr>\n'
        )
    top_sponsor_rows = ""
    for name, count in sponsors["top_sponsors"]:
        top_sponsor_rows += (
            f'<tr><td style="padding:8px;">{escape_html(name)}</td>'
            f'<td style="padding:8px;text-align:right;">{count}</td></tr>\n'
        )

    # Missing trials
    missing_rows = ""
    for item in missing["zero"][:30]:
        missing_rows += (
            f'<tr><td style="padding:8px;color:#ff4444;">{escape_html(item["country"])}</td>'
            f'<td style="padding:8px;color:#ff4444;">{escape_html(item["condition"])}</td>'
            f'<td style="padding:8px;text-align:center;color:#ff4444;font-weight:bold;">0</td>'
            f'</tr>\n'
        )
    for item in missing["low"][:20]:
        missing_rows += (
            f'<tr><td style="padding:8px;color:#ffaa33;">{escape_html(item["country"])}</td>'
            f'<td style="padding:8px;color:#ffaa33;">{escape_html(item["condition"])}</td>'
            f'<td style="padding:8px;text-align:center;color:#ffaa33;font-weight:bold;">'
            f'{item["count"]}</td>'
            f'</tr>\n'
        )

    # Temporal trend data
    trend_years = json.dumps(list(trends.keys()))
    trend_counts = json.dumps(list(trends.values()))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa NCD Trial Gap Analysis</title>
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
  font-size: 2.2rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
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
.subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 2rem; }}
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
.method-note {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
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

<h1>Africa's NCD Trial Gap</h1>
<p class="subtitle">Condition Colonialism Index &mdash; Mapping the mismatch between
disease burden and clinical research investment across 10 African nations</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Total NCD Trials in Africa</div>
    <div class="value">{total_africa_trials:,}</div>
    <div class="label">Across 10 countries, 10 conditions</div>
  </div>
  <div class="summary-card">
    <div class="label">CCI Range</div>
    <div class="value danger">{cci_min} &ndash; {cci_max}</div>
    <div class="label">1.0 = fair, &gt;5 = severe gap</div>
  </div>
  <div class="summary-card">
    <div class="label">Worst Conditions</div>
    <div class="value warning" style="font-size:1.3rem;">{escape_html(worst_names)}</div>
    <div class="label">Highest CCI scores</div>
  </div>
  <div class="summary-card">
    <div class="label">Zero-Trial Pairs</div>
    <div class="value danger">{len(missing['zero'])}</div>
    <div class="label">Country-condition pairs with no trials</div>
  </div>
</div>

<div class="method-note">
<strong>Condition Colonialism Index (CCI)</strong> = Africa's share of global disease burden (%)
divided by Africa's share of trials vs the US (%). A CCI of 1.0 means proportional research
investment. Values above 1.0 indicate under-researched conditions relative to burden.
Values above 5.0 represent severe research colonialism.
</div>

<!-- 2. CCI Heatmap -->
<h2>2. Trial Count Heatmap</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Rows = NCD conditions. Columns = African countries. Cell color:
<span style="color:#ff4444;">0-5 trials (red)</span>,
<span style="color:#cccc44;">6-20 (yellow)</span>,
<span style="color:#44cc66;">20+ (green)</span>,
<span style="background:#111;padding:2px 6px;">0 = black</span>.
</p>
<div class="scroll-x">
<table>
<thead>
<tr>
<th>Condition</th>
{country_headers}
<th style="padding:8px;writing-mode:vertical-rl;">CCI</th>
</tr>
</thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>

<!-- 3. CCI Bar Chart -->
<h2>3. Condition Colonialism Index by Condition</h2>
<div class="chart-container">
<canvas id="cciBarChart" height="300"></canvas>
</div>

<!-- 4. Per-Capita Density -->
<h2>4. NCD Trial Density (per million population)</h2>
<table>
<thead>
<tr><th>Country</th><th style="text-align:right;">Total Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials / Million</th></tr>
</thead>
<tbody>
{percap_rows}
</tbody>
</table>

<!-- 5. US Comparison -->
<h2>5. Africa vs United States by Condition</h2>
<div class="scroll-x">
<table>
<thead>
<tr><th>Condition</th><th style="text-align:right;">Africa</th>
<th style="text-align:right;">US</th><th style="text-align:right;">US/Africa Ratio</th>
<th style="text-align:right;">Africa Burden</th>
<th style="text-align:right;">CCI</th></tr>
</thead>
<tbody>
{us_comp_rows}
</tbody>
</table>
</div>

<!-- 6. Phase Distribution -->
<h2>6. Phase Distribution (Outsourced Testing?)</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Phase 3 trials conducted in Africa by non-African sponsors may indicate
outsourced testing rather than locally-driven research.
</p>
<div class="two-col">
<div>
<table>
<thead><tr><th>Phase</th><th style="text-align:right;">Count</th>
<th style="text-align:right;">%</th></tr></thead>
<tbody>{phase_rows}</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="phaseChart" height="250"></canvas>
</div>
</div>

<!-- 7. Sponsor Analysis -->
<h2>7. Sponsor Analysis</h2>
<div class="two-col">
<div>
<h3>By Sponsor Class</h3>
<table>
<thead><tr><th>Class</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{sponsor_class_rows}</tbody>
</table>
</div>
<div>
<h3>Top 10 Sponsors</h3>
<table>
<thead><tr><th>Sponsor</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{top_sponsor_rows}</tbody>
</table>
</div>
</div>

<!-- 8. Missing Trials -->
<h2>8. The Missing Trials</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Country-condition pairs with zero or near-zero interventional trials.
These represent complete research blind spots for major diseases.
</p>
<div class="scroll-x">
<table>
<thead><tr><th>Country</th><th>Condition</th><th style="text-align:center;">Trials</th></tr></thead>
<tbody>
{missing_rows}
</tbody>
</table>
</div>

<!-- 9. Temporal Trend -->
<h2>9. Temporal Trend: Are NCD Trials Growing?</h2>
<div class="chart-container">
<canvas id="trendChart" height="250"></canvas>
</div>

<footer>
<p>Data source: ClinicalTrials.gov API v2 | WHO Global Health Estimates |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>Condition Colonialism Index (CCI) methodology: Africa burden % / Africa trial share %</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {{

  // CCI Bar Chart
  var cciCtx = document.getElementById('cciBarChart');
  if (cciCtx) {{
    new Chart(cciCtx, {{
      type: 'bar',
      data: {{
        labels: {cci_bar_labels},
        datasets: [{{
          label: 'Condition Colonialism Index',
          data: {cci_bar_values},
          backgroundColor: {cci_bar_colors},
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{ display: true, text: 'CCI by Condition (higher = worse gap)',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }},
            title: {{ display: true, text: 'CCI (1.0 = fair)', color: '#94a3b8' }}
          }},
          y: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0' }}
          }}
        }}
      }}
    }});
  }}

  // Phase pie chart
  var phaseCtx = document.getElementById('phaseChart');
  if (phaseCtx) {{
    var phaseData = {json.dumps(phases)};
    var phaseLabels = Object.keys(phaseData);
    var phaseValues = Object.values(phaseData);
    var phaseColors = phaseLabels.map(function(l) {{
      if (l.toUpperCase().indexOf('PHASE3') >= 0 || l === 'PHASE3')
        return '#ef4444';
      if (l.toUpperCase().indexOf('PHASE2') >= 0) return '#f59e0b';
      if (l.toUpperCase().indexOf('PHASE1') >= 0) return '#3b82f6';
      if (l.toUpperCase().indexOf('PHASE4') >= 0) return '#22c55e';
      return '#6b7280';
    }});
    new Chart(phaseCtx, {{
      type: 'doughnut',
      data: {{
        labels: phaseLabels,
        datasets: [{{ data: phaseValues, backgroundColor: phaseColors, borderWidth: 0 }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'right', labels: {{ color: '#e2e8f0' }} }},
          title: {{ display: true, text: 'Phase Distribution',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }}
      }}
    }});
  }}

  // Temporal trend line chart
  var trendCtx = document.getElementById('trendChart');
  if (trendCtx) {{
    new Chart(trendCtx, {{
      type: 'line',
      data: {{
        labels: {trend_years},
        datasets: [{{
          label: 'NCD Trials Started (Africa)',
          data: {trend_counts},
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 4,
          pointBackgroundColor: '#3b82f6',
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ labels: {{ color: '#e2e8f0' }} }},
          title: {{ display: true, text: 'NCD Trial Starts by Year (Africa)',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }}
          }},
          y: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }},
            title: {{ display: true, text: 'Trial count', color: '#94a3b8' }}
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
    print("Africa NCD Trial Gap Analysis")
    print("=" * 60)
    print()

    # Fetch data
    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    # Compute analyses
    print("Computing Condition Colonialism Index...")
    cci = compute_cci(data)

    print("Computing per-capita density...")
    per_capita = compute_per_capita(data)

    print("Analysing phase distribution...")
    phases = compute_phase_distribution(data)

    print("Analysing sponsors...")
    sponsors = compute_sponsor_analysis(data)

    print("Finding missing trials...")
    missing = find_missing_trials(data)

    print("Computing temporal trends...")
    trends = compute_temporal_trend(data)

    # Print summary
    print()
    print("-" * 60)
    print("CONDITION COLONIALISM INDEX")
    print("-" * 60)
    for cond, vals in sorted(cci.items(), key=lambda x: -x[1]["cci"]):
        print(
            f"  {cond:15s}  CCI={vals['cci']:6.2f}  "
            f"(Africa: {vals['africa_trials']:>5,} | US: {vals['us_trials']:>6,} | "
            f"Burden: {vals['burden_pct']}%)"
        )

    print()
    print(f"Zero-trial pairs: {len(missing['zero'])}")
    print(f"Low-trial pairs (1-3): {len(missing['low'])}")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, cci, per_capita, phases, sponsors, missing, trends)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
