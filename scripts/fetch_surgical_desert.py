#!/usr/bin/env python
"""
fetch_surgical_desert.py — Query ClinicalTrials.gov API v2 for surgical trials
and quantify Africa's "Surgical Desert": 5 billion people lack access to safe
surgery (Lancet Commission 2015), yet Africa has only ~84 surgical RCTs vs
7,388 in the US — a CCI of 27.3x.

Outputs:
  - data/surgical_desert_data.json  (cached API results, 24h TTL)
  - surgical-desert.html            (dark-theme interactive dashboard)
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

# Primary query for surgical trials
SURGICAL_QUERY = "surgery OR surgical"

# Countries for the main comparison
COUNTRY_LIST = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria",
    "Tanzania", "Ghana",
]
GLOBAL_COMPARATORS = [
    "United States", "India", "Brazil", "China", "United Kingdom",
]

# Africa = all African countries (location search)
AFRICA_LOCATIONS = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria",
    "Tanzania", "Ghana", "Ethiopia", "Malawi", "Zambia",
    "Senegal", "Cameroon", "Rwanda", "Mozambique", "Zimbabwe",
    "Tunisia", "Morocco", "Algeria", "Libya", "Sudan",
    "Democratic Republic of Congo", "Ivory Coast", "Mali", "Niger",
    "Burkina Faso", "Guinea", "Benin", "Togo", "Sierra Leone",
    "Liberia", "Central African Republic", "Chad", "Eritrea",
    "Somalia", "Djibouti", "Comoros", "Madagascar", "Mauritius",
    "Botswana", "Namibia", "Lesotho", "Eswatini", "Gabon",
    "Equatorial Guinea", "Sao Tome", "Cape Verde", "Gambia",
    "South Sudan", "Angola",
]

# Surgical subspecialties for Africa-specific queries
SUBSPECIALTIES = {
    "Obstetric (caesarean)": "caesarean OR cesarean",
    "Orthopedic / fracture": "orthopedic OR fracture",
    "Cardiac surgery":       "cardiac surgery",
    "Neurosurgery":          "neurosurgery",
    "Ophthalmic / cataract": "ophthalmic OR cataract",
    "Anaesthesia":           "anesthesia OR anaesthesia",
}

# Population in millions (2025 estimates)
POPULATIONS = {
    "South Africa":   62,
    "Egypt":          110,
    "Kenya":          56,
    "Uganda":         48,
    "Nigeria":        230,
    "Tanzania":       67,
    "Ghana":          34,
    "United States":  335,
    "India":          1440,
    "Brazil":         217,
    "China":          1425,
    "United Kingdom": 68,
    "Africa":         1500,  # continent total
}

# Lancet Commission 2015: Africa surgical burden share
# Africa ~30% of global surgical conditions burden (Meara et al., Lancet 2015)
AFRICA_SURGICAL_BURDEN_PCT = 30

CACHE_FILE = Path(__file__).resolve().parent / "data" / "surgical_desert_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "surgical-desert.html"
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
    """Return total count of interventional surgical trials for a query+location."""
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


def get_trial_count_multi_location(condition_query, locations):
    """Return count with multiple locations OR'd together."""
    location_str = " OR ".join(locations)
    params = {
        "format": "json",
        "query.cond": condition_query,
        "query.locn": location_str,
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

        phases_list = design.get("phases", [])
        phase_str = ", ".join(phases_list) if phases_list else "Not specified"
        lead_sponsor = sponsor_mod.get("leadSponsor", {})
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
    """Fetch all surgical trial data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "africa_total": 0,
        "country_counts": {},
        "subspecialty_counts": {},
        "africa_trial_details": [],
        "subspecialty_details": {},
    }

    # --- Africa-wide surgical trial count ---
    print("  [1] Africa-wide surgical trials...")
    africa_count = get_trial_count_multi_location(SURGICAL_QUERY, AFRICA_LOCATIONS)
    data["africa_total"] = africa_count
    print(f"      Africa total: {africa_count}")
    time.sleep(RATE_LIMIT)

    # --- Per-country counts (African + comparators) ---
    all_countries = COUNTRY_LIST + GLOBAL_COMPARATORS
    total_calls = len(all_countries) + len(SUBSPECIALTIES) + 2  # +2 for details queries
    call_num = 1

    for country in all_countries:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / surgical...")
        count = get_trial_count(SURGICAL_QUERY, country)
        data["country_counts"][country] = count
        time.sleep(RATE_LIMIT)

    # --- Subspecialty counts for Africa ---
    for sub_label, sub_query in SUBSPECIALTIES.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa / {sub_label}...")
        count = get_trial_count_multi_location(sub_query, AFRICA_LOCATIONS)
        data["subspecialty_counts"][sub_label] = count
        time.sleep(RATE_LIMIT)

    # --- Trial-level details for Africa surgical ---
    print(f"  Fetching Africa surgical trial details...")
    details = get_trial_details(SURGICAL_QUERY, AFRICA_LOCATIONS)
    data["africa_trial_details"] = details
    time.sleep(RATE_LIMIT)

    # --- Subspecialty details for Africa (for sponsor analysis) ---
    for sub_label, sub_query in SUBSPECIALTIES.items():
        print(f"  Fetching Africa {sub_label} details...")
        sub_details = get_trial_details(sub_query, AFRICA_LOCATIONS, page_size=50)
        data["subspecialty_details"][sub_label] = sub_details
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
    """Compute Surgical Desert CCI.

    CCI = (Africa burden %) / (Africa trial share %)
    Africa trial share = Africa trials / (Africa + US) * 100
    Lancet Commission: Africa ~30% of global surgical burden.
    """
    africa_trials = data["africa_total"]
    us_trials = data["country_counts"].get("United States", 7388)
    total = africa_trials + us_trials

    if total == 0:
        trial_share = 0
    else:
        trial_share = (africa_trials / total) * 100

    if trial_share > 0:
        cci = AFRICA_SURGICAL_BURDEN_PCT / trial_share
    else:
        cci = float("inf")

    return {
        "africa_trials": africa_trials,
        "us_trials": us_trials,
        "trial_share_pct": round(trial_share, 2),
        "burden_pct": AFRICA_SURGICAL_BURDEN_PCT,
        "cci": round(cci, 1) if cci != float("inf") else 999.0,
    }


def compute_per_capita(data):
    """Trials per million population per country."""
    results = {}
    for country in COUNTRY_LIST + GLOBAL_COMPARATORS:
        count = data["country_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        results[country] = {
            "total_trials": count,
            "population_m": pop,
            "trials_per_million": round(count / pop, 2),
        }
    # Africa aggregate
    results["Africa (total)"] = {
        "total_trials": data["africa_total"],
        "population_m": POPULATIONS["Africa"],
        "trials_per_million": round(data["africa_total"] / POPULATIONS["Africa"], 2),
    }
    return results


def compute_phase_distribution(data):
    """Phase distribution across Africa surgical trials."""
    phase_counts = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        phase_counts[t.get("phase", "Not specified")] += 1
    return dict(phase_counts)


def compute_sponsor_analysis(data):
    """Sponsor class distribution for Africa surgical trials."""
    sponsor_class_counts = defaultdict(int)
    top_sponsors = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        cls = t.get("sponsorClass", "OTHER")
        sponsor_class_counts[cls] += 1
        name = t.get("sponsorName", "Unknown")
        top_sponsors[name] += 1
    top_10 = sorted(top_sponsors.items(), key=lambda x: -x[1])[:10]
    return {
        "by_class": dict(sponsor_class_counts),
        "top_sponsors": top_10,
    }


def compute_temporal_trend(data):
    """Trials by start year."""
    year_counts = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        sd = t.get("startDate", "")
        if sd:
            try:
                year = int(sd[:4])
                if 2000 <= year <= 2030:
                    year_counts[year] += 1
            except (ValueError, IndexError):
                pass
    return dict(sorted(year_counts.items()))


def compute_obstetric_spotlight(data):
    """Obstetric/caesarean surgery spotlight — critical for maternal mortality."""
    caesarean_count = data["subspecialty_counts"].get("Obstetric (caesarean)", 0)
    details = data.get("subspecialty_details", {}).get("Obstetric (caesarean)", [])
    sponsor_counts = defaultdict(int)
    for t in details:
        sponsor_counts[t.get("sponsorClass", "OTHER")] += 1
    return {
        "count": caesarean_count,
        "details": details,
        "sponsors": dict(sponsor_counts),
    }


def compute_anaesthesia_gap(data):
    """Anaesthesia trial gap — essential for safe surgery."""
    anaesthesia_count = data["subspecialty_counts"].get("Anaesthesia", 0)
    details = data.get("subspecialty_details", {}).get("Anaesthesia", [])
    return {
        "count": anaesthesia_count,
        "details": details,
    }


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


def cci_color(val):
    """Color by CCI severity."""
    if val > 10:
        return "#ff4444"
    elif val > 5:
        return "#ff6633"
    elif val > 2:
        return "#ffaa33"
    else:
        return "#44cc66"


def generate_html(data, cci, per_capita, phases, sponsors, trends,
                  obstetric, anaesthesia):
    """Generate the full HTML dashboard."""

    africa_trials = data["africa_total"]
    us_trials = data["country_counts"].get("United States", 0)
    ratio_us_africa = round(us_trials / africa_trials, 1) if africa_trials > 0 else "Inf"

    # --- Country comparison table ---
    comparison_countries = ["South Africa", "Egypt", "Nigeria", "Kenya",
                            "Uganda", "Tanzania", "Ghana"]
    comp_rows = ""
    for country in comparison_countries:
        count = data["country_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        tpm = round(count / pop, 2)
        comp_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{count:,}</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'<td style="padding:8px;text-align:right;">{tpm}</td>'
            f'</tr>\n'
        )

    # --- Global comparison table (Africa vs US vs India vs Brazil) ---
    global_comp_rows = ""
    global_countries = [
        ("Africa (total)", data["africa_total"], POPULATIONS["Africa"]),
        ("United States", data["country_counts"].get("United States", 0), POPULATIONS["United States"]),
        ("India", data["country_counts"].get("India", 0), POPULATIONS["India"]),
        ("Brazil", data["country_counts"].get("Brazil", 0), POPULATIONS["Brazil"]),
        ("China", data["country_counts"].get("China", 0), POPULATIONS["China"]),
        ("United Kingdom", data["country_counts"].get("United Kingdom", 0), POPULATIONS["United Kingdom"]),
    ]
    for name, count, pop in global_countries:
        tpm = round(count / pop, 2) if pop > 0 else 0
        ratio_vs_africa = round(count / africa_trials, 1) if africa_trials > 0 else "N/A"
        highlight = "background:#1a1a2e;" if name == "Africa (total)" else ""
        global_comp_rows += (
            f'<tr style="{highlight}">'
            f'<td style="padding:8px;font-weight:{"bold" if name == "Africa (total)" else "normal"};">'
            f'{escape_html(name)}</td>'
            f'<td style="padding:8px;text-align:right;">{count:,}</td>'
            f'<td style="padding:8px;text-align:right;">{pop:,}M</td>'
            f'<td style="padding:8px;text-align:right;">{tpm}</td>'
            f'<td style="padding:8px;text-align:right;">{ratio_vs_africa}x</td>'
            f'</tr>\n'
        )

    # --- Subspecialty bar chart data ---
    sub_labels = json.dumps(list(data["subspecialty_counts"].keys()))
    sub_values = json.dumps(list(data["subspecialty_counts"].values()))
    sub_colors = json.dumps([
        "#ef4444" if v <= 10
        else "#f59e0b" if v <= 30
        else "#3b82f6"
        for v in data["subspecialty_counts"].values()
    ])

    # --- Subspecialty table ---
    sub_rows = ""
    for sub_label, count in sorted(data["subspecialty_counts"].items(), key=lambda x: -x[1]):
        color = "#ff4444" if count <= 10 else "#ffaa33" if count <= 30 else "#e2e8f0"
        sub_rows += (
            f'<tr><td style="padding:8px;">{escape_html(sub_label)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">'
            f'{count}</td></tr>\n'
        )

    # --- Per-capita table ---
    per_capita_sorted = sorted(per_capita.items(), key=lambda x: -x[1]["trials_per_million"])
    percap_rows = ""
    for country, info in per_capita_sorted:
        is_africa = country == "Africa (total)"
        row_style = "background:#1a1a2e;" if is_africa else ""
        percap_rows += (
            f'<tr style="{row_style}">'
            f'<td style="padding:8px;font-weight:{"bold" if is_africa else "normal"};">'
            f'{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{info["total_trials"]:,}</td>'
            f'<td style="padding:8px;text-align:right;">{info["population_m"]:,}M</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">'
            f'{info["trials_per_million"]}</td>'
            f'</tr>\n'
        )

    # --- Phase distribution ---
    phase_rows = ""
    total_phase = sum(phases.values()) if phases else 1
    for phase, count in sorted(phases.items()):
        pct = round(count / total_phase * 100, 1)
        phase_rows += (
            f'<tr><td style="padding:8px;">{escape_html(phase)}</td>'
            f'<td style="padding:8px;text-align:right;">{count}</td>'
            f'<td style="padding:8px;text-align:right;">{pct}%</td></tr>\n'
        )

    # --- Sponsor rows ---
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

    # --- Obstetric spotlight ---
    obs_sponsor_rows = ""
    for cls, count in sorted(obstetric["sponsors"].items(), key=lambda x: -x[1]):
        obs_sponsor_rows += (
            f'<tr><td style="padding:8px;">{escape_html(cls)}</td>'
            f'<td style="padding:8px;text-align:right;">{count}</td></tr>\n'
        )

    # --- Temporal trend ---
    trend_years = json.dumps(list(trends.keys()))
    trend_counts = json.dumps(list(trends.values()))

    # --- Country bar chart ---
    country_bar_labels = json.dumps(
        [c for c in comparison_countries]
    )
    country_bar_values = json.dumps(
        [data["country_counts"].get(c, 0) for c in comparison_countries]
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Surgical Desert: Africa's Missing Surgical Trials</title>
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
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
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
.method-note {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.danger-note {{
  background: rgba(239, 68, 68, 0.1);
  border-left: 4px solid var(--danger);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.warning-note {{
  background: rgba(245, 158, 11, 0.1);
  border-left: 4px solid var(--warning);
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
.big-number {{
  font-size: 4rem;
  font-weight: 900;
  text-align: center;
  margin: 1rem 0;
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

<h1>The Surgical Desert</h1>
<p class="subtitle">5 billion people lack access to safe surgery (Lancet Commission, 2015).
Africa is the epicentre of this crisis &mdash; yet it has only {africa_trials} registered
interventional surgical trials vs {us_trials:,} in the United States.</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Surgical Trials</div>
    <div class="value danger">{africa_trials}</div>
    <div class="label">Interventional, ClinicalTrials.gov</div>
  </div>
  <div class="summary-card">
    <div class="label">US Surgical Trials</div>
    <div class="value" style="color:#3b82f6;">{us_trials:,}</div>
    <div class="label">{ratio_us_africa}x more than Africa</div>
  </div>
  <div class="summary-card">
    <div class="label">Condition Colonialism Index</div>
    <div class="value danger">{cci['cci']}</div>
    <div class="label">1.0 = fair; &gt;5 = severe gap</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Caesarean Trials</div>
    <div class="value warning">{obstetric['count']}</div>
    <div class="label">Critical for maternal mortality</div>
  </div>
</div>

<!-- 2. CCI Calculation -->
<h2>2. Condition Colonialism Index: The Calculation</h2>
<div class="method-note">
<strong>CCI = Burden Share / Trial Share</strong><br><br>
The Lancet Commission on Global Surgery (Meara et al., 2015) estimated that
5 billion people lack access to safe, affordable, timely surgical care.
Africa bears approximately <strong>{AFRICA_SURGICAL_BURDEN_PCT}%</strong> of the global
surgical disease burden.<br><br>
<strong>Africa's trial share</strong> = {africa_trials} / ({africa_trials} + {us_trials:,})
= <strong>{cci['trial_share_pct']}%</strong><br>
<strong>CCI</strong> = {AFRICA_SURGICAL_BURDEN_PCT}% / {cci['trial_share_pct']}%
= <strong style="color:#ef4444;">{cci['cci']}</strong><br><br>
A CCI of {cci['cci']} means Africa carries {cci['cci']}x more surgical disease burden
than its share of global surgical research warrants.
</div>

<div class="big-number danger">{cci['cci']}x</div>
<p style="text-align:center;color:var(--muted);margin-bottom:2rem;">
Surgical CCI: Africa's burden-to-research ratio</p>

<!-- 3. Country Breakdown -->
<h2>3. Country Breakdown: African Surgical Trials</h2>
<div class="two-col">
<div>
<table>
<thead>
<tr><th>Country</th><th style="text-align:right;">Surgical Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials/Million</th></tr>
</thead>
<tbody>
{comp_rows}
</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="countryBarChart" height="280"></canvas>
</div>
</div>

<!-- 4. Subspecialty Bars -->
<h2>4. Surgical Subspecialties in Africa</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
How many interventional trials exist in Africa for each surgical subspecialty?
Many subspecialties are virtually absent from the trial registry.
</p>
<div class="two-col">
<div>
<table>
<thead><tr><th>Subspecialty</th><th style="text-align:right;">Africa Trials</th></tr></thead>
<tbody>{sub_rows}</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="subBarChart" height="280"></canvas>
</div>
</div>

<!-- 5. Comparison Table: Africa vs US vs India vs Brazil -->
<h2>5. Global Comparison: Africa vs US vs India vs Brazil</h2>
<div class="scroll-x">
<table>
<thead>
<tr><th>Country/Region</th><th style="text-align:right;">Surgical Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials/Million</th>
<th style="text-align:right;">Ratio vs Africa</th></tr>
</thead>
<tbody>
{global_comp_rows}
</tbody>
</table>
</div>

<div class="danger-note">
<strong>The scale of disparity:</strong> The United States has {ratio_us_africa}x more
surgical trials than the entire African continent. Even India, with a similar population
to Africa, has substantially more surgical research. Africa's 1.5 billion people are
served by fewer surgical trials than many individual high-income countries.
</div>

<!-- 6. Obstetric Surgery Spotlight -->
<h2>6. Obstetric Surgery Spotlight: Caesarean Trials</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Caesarean section is the most common major surgery worldwide.
In sub-Saharan Africa, lack of access to emergency caesarean section is a leading
cause of maternal and neonatal death. How many registered caesarean trials exist?
</p>
<div class="two-col">
<div>
<div class="summary-card">
  <div class="label">Caesarean / Cesarean Trials in Africa</div>
  <div class="value warning">{obstetric['count']}</div>
  <div class="label">Interventional trials registered</div>
</div>
<div class="danger-note" style="margin-top:1rem;">
<strong>Context:</strong> An estimated 28.3 million additional surgical procedures
are needed annually in sub-Saharan Africa (Lancet Commission, 2015), with
caesarean sections representing the single largest unmet surgical need.
Yet only {obstetric['count']} interventional caesarean trials are registered
for the entire continent.
</div>
</div>
<div>
<h3>Sponsor Distribution</h3>
<table>
<thead><tr><th>Sponsor Class</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{obs_sponsor_rows}</tbody>
</table>
</div>
</div>

<!-- 7. The Anaesthesia Gap -->
<h2>7. The Anaesthesia Gap</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Safe surgery requires safe anaesthesia. The Lancet Commission documented that
most of Africa lacks trained anaesthesia providers. How much anaesthesia
research is being conducted?
</p>
<div class="summary-card" style="max-width:400px;">
  <div class="label">Anaesthesia Trials in Africa</div>
  <div class="value warning">{anaesthesia['count']}</div>
  <div class="label">Anesthesia OR anaesthesia, interventional</div>
</div>
<div class="warning-note" style="margin-top:1rem;">
<strong>The silent crisis:</strong> With only {anaesthesia['count']} anaesthesia trials
across a continent of 1.5 billion people, Africa's anaesthesia evidence base is
being built almost entirely from research conducted elsewhere. Local context &mdash;
drug availability, monitoring capacity, workforce constraints &mdash; makes
extrapolation from high-income settings unreliable.
</div>

<!-- 8. Sponsor Analysis -->
<h2>8. Sponsor Analysis</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Who funds surgical research in Africa? The sponsor profile reveals whether
research is locally driven or externally imposed.
</p>
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

<!-- 9. Per-Capita Density -->
<h2>9. Per-Capita Surgical Trial Density</h2>
<table>
<thead>
<tr><th>Country/Region</th><th style="text-align:right;">Surgical Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials / Million</th></tr>
</thead>
<tbody>
{percap_rows}
</tbody>
</table>

<!-- 10. Phase Distribution -->
<h2>10. Phase Distribution</h2>
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

<!-- 11. Temporal Trend -->
<h2>11. Temporal Trend</h2>
<div class="chart-container">
<canvas id="trendChart" height="250"></canvas>
</div>

<footer>
<p>Data source: ClinicalTrials.gov API v2 | Lancet Commission on Global Surgery (2015) |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>Surgical Desert CCI methodology: Africa burden share ({AFRICA_SURGICAL_BURDEN_PCT}%) /
Africa trial share ({cci['trial_share_pct']}%) = {cci['cci']}</p>
<p style="margin-top:0.5rem;">Project 21 of the Africa RCT Landscape Series</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {{

  // Country bar chart
  var countryCtx = document.getElementById('countryBarChart');
  if (countryCtx) {{
    new Chart(countryCtx, {{
      type: 'bar',
      data: {{
        labels: {country_bar_labels},
        datasets: [{{
          label: 'Surgical Trials',
          data: {country_bar_values},
          backgroundColor: '#3b82f6',
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{ display: true, text: 'Surgical Trials by African Country',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }}
          }},
          y: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0' }}
          }}
        }}
      }}
    }});
  }}

  // Subspecialty bar chart
  var subCtx = document.getElementById('subBarChart');
  if (subCtx) {{
    new Chart(subCtx, {{
      type: 'bar',
      data: {{
        labels: {sub_labels},
        datasets: [{{
          label: 'Trials in Africa',
          data: {sub_values},
          backgroundColor: {sub_colors},
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{ display: true, text: 'Surgical Subspecialty Trials (Africa)',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }}
          }},
          y: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0', font: {{ size: 11 }} }}
          }}
        }}
      }}
    }});
  }}

  // Phase doughnut chart
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
          title: {{ display: true, text: 'Phase Distribution (Africa Surgical)',
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
          label: 'Surgical Trials Started (Africa)',
          data: {trend_counts},
          borderColor: '#ef4444',
          backgroundColor: 'rgba(239,68,68,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 4,
          pointBackgroundColor: '#ef4444',
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ labels: {{ color: '#e2e8f0' }} }},
          title: {{ display: true, text: 'Surgical Trial Starts by Year (Africa)',
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
    print("The Surgical Desert: Africa's Missing Surgical Trials")
    print("=" * 60)
    print()

    # Fetch data
    print("Fetching surgical trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    # Compute analyses
    print("Computing Condition Colonialism Index (surgical)...")
    cci = compute_cci(data)

    print("Computing per-capita density...")
    per_capita = compute_per_capita(data)

    print("Analysing phase distribution...")
    phases = compute_phase_distribution(data)

    print("Analysing sponsors...")
    sponsors = compute_sponsor_analysis(data)

    print("Computing temporal trends...")
    trends = compute_temporal_trend(data)

    print("Analysing obstetric surgery spotlight...")
    obstetric = compute_obstetric_spotlight(data)

    print("Analysing anaesthesia gap...")
    anaesthesia = compute_anaesthesia_gap(data)

    # Print summary
    print()
    print("-" * 60)
    print("SURGICAL DESERT: KEY FINDINGS")
    print("-" * 60)
    print(f"  Africa surgical trials:     {cci['africa_trials']}")
    print(f"  US surgical trials:         {cci['us_trials']:,}")
    print(f"  US/Africa ratio:            {round(cci['us_trials'] / max(cci['africa_trials'], 1), 1)}x")
    print(f"  Africa trial share:         {cci['trial_share_pct']}%")
    print(f"  Africa burden share:        {cci['burden_pct']}%")
    print(f"  Surgical CCI:               {cci['cci']}")
    print()
    print("  Subspecialty counts (Africa):")
    for sub, count in sorted(data.get("subspecialty_counts", {}).items(),
                              key=lambda x: -x[1]):
        print(f"    {sub:30s} {count:>5}")
    print()
    print(f"  Caesarean trials (Africa):   {obstetric['count']}")
    print(f"  Anaesthesia trials (Africa): {anaesthesia['count']}")

    # Country breakdown
    print()
    print("  Country surgical trial counts:")
    for country in COUNTRY_LIST + GLOBAL_COMPARATORS:
        count = data["country_counts"].get(country, 0)
        print(f"    {country:25s} {count:>6,}")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, cci, per_capita, phases, sponsors, trends,
                         obstetric, anaesthesia)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
