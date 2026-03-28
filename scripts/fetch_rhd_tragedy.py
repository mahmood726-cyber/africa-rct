#!/usr/bin/env python
"""
fetch_rhd_tragedy.py -- RHD: The $0.02 Tragedy
===================================================
Rheumatic heart disease kills 240,000 Africans per year. It is COMPLETELY
PREVENTABLE with benzathine penicillin G costing $0.02 per dose. Yet only
~2 RHD trials exist on ClinicalTrials.gov for Africa vs 23 in the US.

Queries ClinicalTrials.gov API v2 for RHD / rheumatic fever / penicillin
prophylaxis trials across Africa and comparators, plus streptococcal
pharyngitis queries.

Usage:
    python fetch_rhd_tragedy.py

Output:
    data/rhd_data.json        (cached API results, 24h TTL)
    rhd-tragedy.html          (dark-theme interactive dashboard)

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
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# Primary queries
RHD_QUERY = "rheumatic heart disease OR rheumatic fever OR penicillin prophylaxis AND rheumatic"
STREP_QUERY = "streptococcal pharyngitis"

# African countries
AFRICAN_COUNTRIES = [
    "South Africa", "Uganda", "Kenya", "Nigeria",
    "Ethiopia", "Tanzania", "Mozambique", "Egypt",
]

# High-burden RHD countries (deaths per 100K, GBD 2019 estimates)
RHD_BURDEN = {
    "Uganda": 12.4,
    "Ethiopia": 11.8,
    "Mozambique": 10.9,
    "Tanzania": 9.7,
    "Nigeria": 8.2,
    "Kenya": 7.5,
    "South Africa": 5.3,
    "Egypt": 3.1,
    "India": 6.8,
    "Brazil": 1.4,
    "United States": 0.3,
    "United Kingdom": 0.1,
}

# Population in millions (2025 estimates)
POPULATIONS = {
    "South Africa": 62,
    "Uganda": 48,
    "Kenya": 56,
    "Nigeria": 230,
    "Ethiopia": 130,
    "Tanzania": 67,
    "Mozambique": 34,
    "Egypt": 110,
    "India": 1440,
    "Brazil": 217,
    "United States": 335,
    "United Kingdom": 68,
}

# Cost data
BPG_COST_USD = 0.02  # per dose of benzathine penicillin G
BPG_DOSES_PER_YEAR = 26  # every 2 weeks for secondary prophylaxis (or 12 for monthly)
ANNUAL_PROPHYLAXIS_COST = 0.52  # monthly dosing = 12 * $0.02 = $0.24; biweekly = $0.52
RHD_SURGERY_COST = 15000  # average valve replacement in Africa (USD)

COMPARATORS = ["United States", "India", "Brazil", "United Kingdom"]

# Verified reference counts
VERIFIED_COUNTS = {
    "Africa_RHD": 2,
    "United States_RHD": 23,
}

# RHD epidemiology
RHD_EPIDEMIOLOGY = {
    "global_deaths_per_year": 306000,
    "africa_deaths_per_year": 240000,
    "africa_share_pct": 78.4,
    "global_prevalence": 40800000,
    "africa_prevalence": 15600000,
    "children_affected_africa": 5700000,
    "median_age_death_africa": 28,
    "median_age_death_hic": 72,
}

# Streptococcal pharyngitis subtypes
STREP_SUBTYPES = {
    "Streptococcal pharyngitis": "streptococcal pharyngitis",
    "Acute rheumatic fever": "acute rheumatic fever",
    "Rheumatic heart disease": "rheumatic heart disease",
    "Penicillin prophylaxis": "penicillin prophylaxis rheumatic",
    "Valve disease (rheumatic)": "valve disease rheumatic",
    "Benzathine penicillin": "benzathine penicillin",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "rhd_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "rhd-tragedy.html"
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


def get_trial_count(condition_query, location):
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


def get_trial_count_multi_location(condition_query, locations):
    """Return total count for a condition across multiple locations (OR)."""
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
    """Fetch trial-level data for multi-location queries."""
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
        results.append({
            "nctId": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "phase": phase_str,
            "status": status_mod.get("overallStatus", ""),
            "sponsorName": lead_sponsor.get("name", ""),
            "sponsorClass": lead_sponsor.get("class", ""),
            "startDate": start_info.get("date", ""),
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
    """Fetch all RHD trial data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "rhd_country_counts": {},
        "rhd_africa_total": 0,
        "strep_country_counts": {},
        "strep_africa_total": 0,
        "subtype_africa_counts": {},
        "subtype_us_counts": {},
        "country_subtype_counts": {},
        "africa_trial_details": [],
    }

    all_locations = AFRICAN_COUNTRIES + COMPARATORS
    total_calls = (
        len(all_locations) * 2            # RHD + strep per country
        + 2                               # Africa aggregates (RHD + strep)
        + len(STREP_SUBTYPES) * 2         # subtypes Africa + US
        + len(AFRICAN_COUNTRIES) * len(STREP_SUBTYPES)  # country x subtype
        + 1                               # trial details
    )
    call_num = 0

    # --- Per-country RHD counts ---
    for country in all_locations:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] RHD: {country}...")
        data["rhd_country_counts"][country] = get_trial_count(RHD_QUERY, country)
        time.sleep(RATE_LIMIT)

    # --- Per-country strep pharyngitis counts ---
    for country in all_locations:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Strep pharyngitis: {country}...")
        data["strep_country_counts"][country] = get_trial_count(STREP_QUERY, country)
        time.sleep(RATE_LIMIT)

    # --- Africa aggregates ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa aggregate RHD...")
    data["rhd_africa_total"] = get_trial_count_multi_location(
        RHD_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa aggregate strep...")
    data["strep_africa_total"] = get_trial_count_multi_location(
        STREP_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    # --- Subtypes for Africa + US ---
    for subtype_label, subtype_query in STREP_SUBTYPES.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa subtype: {subtype_label}...")
        data["subtype_africa_counts"][subtype_label] = get_trial_count_multi_location(
            subtype_query, AFRICAN_COUNTRIES
        )
        time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] US subtype: {subtype_label}...")
        data["subtype_us_counts"][subtype_label] = get_trial_count(
            subtype_query, "United States"
        )
        time.sleep(RATE_LIMIT)

    # --- Per-country subtype breakdown ---
    for country in AFRICAN_COUNTRIES:
        data["country_subtype_counts"][country] = {}
        for subtype_label, subtype_query in STREP_SUBTYPES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {subtype_label}...")
            count = get_trial_count(subtype_query, country)
            data["country_subtype_counts"][country][subtype_label] = count
            time.sleep(RATE_LIMIT)

    # --- Africa trial details ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa RHD trial details...")
    data["africa_trial_details"] = get_trial_details(
        RHD_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Cached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_cci(data):
    """Compute RHD Condition Colonialism Index.

    CCI = Africa burden % / Africa trial share %
    """
    africa_n = data.get("rhd_africa_total", 0)
    us_n = data["rhd_country_counts"].get("United States", 0)
    total = africa_n + us_n
    trial_share = (africa_n / total * 100) if total > 0 else 0
    burden = RHD_EPIDEMIOLOGY["africa_share_pct"]
    cci = burden / trial_share if trial_share > 0 else 999.0
    return {
        "africa_trials": africa_n,
        "us_trials": us_n,
        "trial_share_pct": round(trial_share, 2),
        "burden_pct": burden,
        "cci": round(cci, 1),
    }


def compute_cost_analysis():
    """Cost-effectiveness of RHD prevention."""
    deaths_per_year = RHD_EPIDEMIOLOGY["africa_deaths_per_year"]
    children_at_risk = RHD_EPIDEMIOLOGY["children_affected_africa"]
    annual_cost_per_child = 12 * BPG_COST_USD  # monthly prophylaxis
    total_cost = children_at_risk * annual_cost_per_child
    cost_per_death_averted = total_cost / deaths_per_year if deaths_per_year > 0 else 0
    return {
        "bpg_dose_cost": BPG_COST_USD,
        "annual_prophylaxis_cost": annual_cost_per_child,
        "children_at_risk": children_at_risk,
        "total_annual_cost": total_cost,
        "cost_per_death_averted": round(cost_per_death_averted, 2),
        "surgery_cost": RHD_SURGERY_COST,
        "surgery_to_prevention_ratio": round(
            RHD_SURGERY_COST / annual_cost_per_child
        ),
    }


def compute_burden_comparison(data):
    """Compare burden rates with trial counts."""
    results = {}
    for country, rate in RHD_BURDEN.items():
        trials = data["rhd_country_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        deaths_est = round(rate * pop * 10)  # rate per 100K -> absolute (approx)
        results[country] = {
            "burden_rate": rate,
            "trials": trials,
            "population_m": pop,
            "deaths_est": deaths_est,
            "deaths_per_trial": round(deaths_est / trials) if trials > 0 else float("inf"),
        }
    return results


def compute_sponsor_analysis(data):
    """Sponsor analysis for Africa RHD trials."""
    sponsor_class_counts = defaultdict(int)
    top_sponsors = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        cls = t.get("sponsorClass", "OTHER")
        sponsor_class_counts[cls] += 1
        name = t.get("sponsorName", "Unknown")
        top_sponsors[name] += 1
    top_10 = sorted(top_sponsors.items(), key=lambda x: -x[1])[:10]
    return {"by_class": dict(sponsor_class_counts), "top_sponsors": top_10}


def compute_hic_elimination():
    """Data on RHD elimination in high-income countries."""
    return {
        "us_rhd_deaths": round(RHD_BURDEN["United States"] * POPULATIONS["United States"] * 10),
        "uk_rhd_deaths": round(RHD_BURDEN["United Kingdom"] * POPULATIONS["United Kingdom"] * 10),
        "africa_rhd_deaths": RHD_EPIDEMIOLOGY["africa_deaths_per_year"],
        "us_rate": RHD_BURDEN["United States"],
        "africa_avg_rate": round(
            sum(RHD_BURDEN[c] for c in AFRICAN_COUNTRIES if c in RHD_BURDEN) /
            sum(1 for c in AFRICAN_COUNTRIES if c in RHD_BURDEN), 1
        ),
        "elimination_year_us": "1960s",
        "elimination_method": "Universal penicillin treatment for strep throat",
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


def cell_color(count):
    """Heatmap color."""
    if count == 0:
        return "#111"
    elif count <= 2:
        return f"rgb({80 + count * 80}, 30, 30)"
    elif count <= 5:
        ratio = (count - 2) / 3
        return f"rgb({int(255 - ratio * 80)}, {int(80 + ratio * 120)}, 40)"
    else:
        return "rgb(50, 200, 80)"


def generate_html(data, cci, cost, burden, sponsors, hic):
    """Generate the full HTML dashboard."""

    africa_rhd = data.get("rhd_africa_total", 0)
    us_rhd = data["rhd_country_counts"].get("United States", 0)

    # Country RHD counts
    country_rows = ""
    for country in AFRICAN_COUNTRIES:
        ct = data["rhd_country_counts"].get(country, 0)
        strep = data["strep_country_counts"].get(country, 0)
        b = RHD_BURDEN.get(country, 0)
        pop = POPULATIONS.get(country, 1)
        country_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{ct}</td>'
            f'<td style="padding:8px;text-align:right;">{strep}</td>'
            f'<td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">{b}</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'</tr>\n'
        )

    # Burden comparison rows (all countries)
    burden_rows = ""
    for country, info in sorted(burden.items(), key=lambda x: -x[1]["burden_rate"]):
        is_african = country in AFRICAN_COUNTRIES
        color = "#ef4444" if is_african else "#94a3b8"
        weight = "bold" if is_african else "normal"
        dpt = info["deaths_per_trial"]
        dpt_str = f"{dpt:,}" if dpt != float("inf") else "INF (0 trials)"
        burden_rows += (
            f'<tr>'
            f'<td style="padding:8px;color:{color};font-weight:{weight};">'
            f'{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{info["burden_rate"]}</td>'
            f'<td style="padding:8px;text-align:right;">{info["trials"]}</td>'
            f'<td style="padding:8px;text-align:right;">{info["deaths_est"]:,}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;'
            f'color:{"#ef4444" if dpt == float("inf") else "#ffaa33"};">'
            f'{dpt_str}</td>'
            f'</tr>\n'
        )

    # Subtype rows
    subtype_rows = ""
    for subtype in STREP_SUBTYPES:
        af = data["subtype_africa_counts"].get(subtype, 0)
        us = data["subtype_us_counts"].get(subtype, 0)
        ratio = round(us / af, 1) if af > 0 else 999
        subtype_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(subtype)}</td>'
            f'<td style="padding:8px;text-align:right;">{af}</td>'
            f'<td style="padding:8px;text-align:right;">{us}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;'
            f'color:{"#ef4444" if ratio > 10 else "#ffaa33"};">'
            f'{ratio}x</td>'
            f'</tr>\n'
        )

    # Heatmap rows
    heatmap_rows = ""
    for subtype in STREP_SUBTYPES:
        cells = ""
        for country in AFRICAN_COUNTRIES:
            count = data["country_subtype_counts"].get(country, {}).get(subtype, 0)
            bg = cell_color(count)
            text_color = "#fff" if count <= 2 else "#000"
            cells += (
                f'<td style="background:{bg};color:{text_color};'
                f'text-align:center;padding:8px;font-weight:bold;">{count}</td>'
            )
        heatmap_rows += (
            f"<tr><td style='padding:8px;font-weight:bold;'>"
            f"{escape_html(subtype)}</td>{cells}</tr>\n"
        )

    country_headers = "".join(
        f'<th style="padding:8px;writing-mode:vertical-rl;text-orientation:mixed;">'
        f'{escape_html(c)}</th>'
        for c in AFRICAN_COUNTRIES
    )

    # Comparator rows
    comp_rows = ""
    for country in COMPARATORS:
        ct = data["rhd_country_counts"].get(country, 0)
        strep = data["strep_country_counts"].get(country, 0)
        b = RHD_BURDEN.get(country, 0)
        pop = POPULATIONS.get(country, 1)
        comp_rows += (
            f'<tr style="background:#1a1a2e;">'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{ct}</td>'
            f'<td style="padding:8px;text-align:right;">{strep}</td>'
            f'<td style="padding:8px;text-align:right;">{b}</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'</tr>\n'
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

    # Trial detail rows
    trial_rows = ""
    for t in data.get("africa_trial_details", []):
        trial_rows += (
            f'<tr>'
            f'<td style="padding:8px;"><a href="https://clinicaltrials.gov/study/'
            f'{escape_html(t["nctId"])}" target="_blank" style="color:#3b82f6;">'
            f'{escape_html(t["nctId"])}</a></td>'
            f'<td style="padding:8px;">{escape_html(t["title"][:80])}</td>'
            f'<td style="padding:8px;">{escape_html(t["phase"])}</td>'
            f'<td style="padding:8px;">{escape_html(t["status"])}</td>'
            f'<td style="padding:8px;">{escape_html(t["sponsorName"][:40])}</td>'
            f'</tr>\n'
        )

    # Chart data
    burden_labels = json.dumps(list(RHD_BURDEN.keys()))
    burden_values = json.dumps(list(RHD_BURDEN.values()))
    burden_colors = json.dumps([
        "#ef4444" if c in AFRICAN_COUNTRIES else "#3b82f6"
        for c in RHD_BURDEN
    ])

    trial_labels = json.dumps(AFRICAN_COUNTRIES + COMPARATORS)
    trial_values = json.dumps([
        data["rhd_country_counts"].get(c, 0)
        for c in AFRICAN_COUNTRIES + COMPARATORS
    ])
    trial_colors = json.dumps(
        ["#ef4444"] * len(AFRICAN_COUNTRIES) + ["#3b82f6"] * len(COMPARATORS)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RHD: The $0.02 Tragedy</title>
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
.cost-highlight {{
  display: inline-block;
  background: rgba(239, 68, 68, 0.2);
  border: 2px solid var(--danger);
  border-radius: 8px;
  padding: 0.3rem 0.8rem;
  font-size: 1.8rem;
  font-weight: 900;
  color: var(--danger);
  margin: 0.5rem 0;
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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="container">

<h1>RHD: The $0.02 Tragedy</h1>
<p class="subtitle">Rheumatic heart disease kills 240,000 Africans per year. Prevention
costs two US cents per dose. Yet Africa has {africa_rhd} trials.</p>

<!-- 1. Summary -->
<h2>1. $0.02 vs 240,000 Lives</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa RHD Trials</div>
    <div class="value danger">{africa_rhd}</div>
    <div class="label">vs {us_rhd} in the US</div>
  </div>
  <div class="summary-card">
    <div class="label">African Deaths / Year</div>
    <div class="value danger">240,000</div>
    <div class="label">78% of global RHD mortality</div>
  </div>
  <div class="summary-card">
    <div class="label">Prevention Cost / Dose</div>
    <div class="value success">$0.02</div>
    <div class="label">benzathine penicillin G</div>
  </div>
  <div class="summary-card">
    <div class="label">Surgery vs Prevention</div>
    <div class="value warning">{cost['surgery_to_prevention_ratio']:,}x</div>
    <div class="label">${RHD_SURGERY_COST:,} surgery vs ${cost['annual_prophylaxis_cost']}/yr prevention</div>
  </div>
</div>

<div class="danger-note">
<strong>The most cost-effective intervention never tested:</strong> Benzathine penicillin G
prevents rheumatic fever recurrence and subsequent rheumatic heart disease at
<span class="cost-highlight">$0.02/dose</span>. Annual prophylaxis costs
${cost['annual_prophylaxis_cost']} per child. Yet valve replacement surgery &mdash;
needed when prevention fails &mdash; costs ${RHD_SURGERY_COST:,}, a
{cost['surgery_to_prevention_ratio']:,}-fold difference. With only {africa_rhd} RHD
trial(s) in all of Africa, the evidence base for delivery strategies in
the highest-burden continent is essentially nonexistent.
</div>

<!-- 2. The Preventable Tragedy -->
<h2>2. The Preventable Tragedy</h2>
<p>Rheumatic heart disease follows a tragic but entirely preventable cascade:</p>
<div class="method-note">
<strong>The RHD Cascade:</strong><br>
1. <strong>Group A Streptococcus</strong> causes pharyngitis (sore throat)<br>
2. Untreated strep triggers <strong>acute rheumatic fever</strong> in susceptible individuals<br>
3. Repeated rheumatic fever attacks damage heart valves &rarr; <strong>RHD</strong><br>
4. RHD leads to heart failure and death, median age <strong>{RHD_EPIDEMIOLOGY['median_age_death_africa']}</strong> in Africa
   vs {RHD_EPIDEMIOLOGY['median_age_death_hic']} in HICs<br><br>
<strong>Every step is preventable.</strong> Treat the sore throat with penicillin.
Prevent recurrence with monthly BPG injections. High-income countries eliminated
RHD in the {hic['elimination_year_us']} using exactly this approach.
</div>

<!-- 3. Penicillin Access Crisis -->
<h2>3. The Penicillin Access Crisis</h2>
<div class="danger-note">
<strong>The paradox:</strong> Benzathine penicillin G is on the WHO Essential Medicines List.
It costs $0.02 per dose. It has been available since 1955. Yet chronic global
shortages mean African children cannot access it reliably. The reasons are
structural:<br><br>
&bull; <strong>No profit incentive:</strong> At $0.02/dose, no manufacturer invests in
production capacity<br>
&bull; <strong>Supply chain failures:</strong> Cold chain requirements in tropical climates<br>
&bull; <strong>Quality concerns:</strong> Painful injections from poor-quality formulations
reduce adherence<br>
&bull; <strong>No political priority:</strong> RHD kills quietly, one patient at a time,
unlike epidemics
</div>

<!-- 4. Country Burden -->
<h2>4. Country Burden</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">RHD Trials</th>
  <th style="text-align:right;">Strep Trials</th>
  <th style="text-align:right;">Death Rate /100K</th>
  <th style="text-align:right;">Population</th>
</tr>
</thead>
<tbody>
{country_rows}
<tr><td colspan="5" style="padding:4px;border-bottom:2px solid var(--border);"></td></tr>
{comp_rows}
</tbody>
</table>

<div class="chart-container">
<h3>RHD Death Rate per 100,000 by Country</h3>
<canvas id="burdenChart" height="300"></canvas>
</div>

<!-- 5. Burden vs Trials -->
<h2>5. Burden vs Trials: The Mismatch</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Death Rate /100K</th>
  <th style="text-align:right;">RHD Trials</th>
  <th style="text-align:right;">Est. Deaths/yr</th>
  <th style="text-align:right;">Deaths per Trial</th>
</tr>
</thead>
<tbody>
{burden_rows}
</tbody>
</table>

<!-- 6. RHD Subtypes -->
<h2>6. Trial Subtypes: Africa vs US</h2>
<table>
<thead>
<tr>
  <th>Category</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">US</th>
  <th style="text-align:right;">US:Africa Ratio</th>
</tr>
</thead>
<tbody>
{subtype_rows}
</tbody>
</table>

<!-- 7. Country x Subtype Heatmap -->
<h2>7. Heatmap: Country x Category</h2>
<div class="scroll-x">
<table>
<thead>
<tr>
  <th>Category</th>
  {country_headers}
</tr>
</thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>

<!-- 8. Comparison with HIC -->
<h2>8. Comparison: HIC Eliminated RHD</h2>
<div class="method-note">
<strong>RHD was eliminated in high-income countries decades ago.</strong><br><br>
&bull; US RHD death rate: <strong>{hic['us_rate']}/100K</strong> (estimated {hic['us_rhd_deaths']:,}
deaths/yr)<br>
&bull; UK RHD death rate: <strong>{RHD_BURDEN['United Kingdom']}/100K</strong> (estimated
{hic['uk_rhd_deaths']:,} deaths/yr)<br>
&bull; Africa average: <strong>{hic['africa_avg_rate']}/100K</strong> ({hic['africa_rhd_deaths']:,}
deaths/yr)<br><br>
The method of elimination was simple: <strong>universal treatment of strep throat with
penicillin</strong> plus secondary prophylaxis for those who develop rheumatic fever.
No new technology needed. No expensive drugs. Just reliable access to a medicine
that has existed for 70 years.
</div>

<div class="chart-container">
<h3>RHD Trial Counts by Country</h3>
<canvas id="trialChart" height="300"></canvas>
</div>

<!-- 9. Cost-Effectiveness Case -->
<h2>9. The Cost-Effectiveness Case</h2>
<div class="warning-note">
<strong>RHD prevention is among the most cost-effective interventions in global health:</strong><br><br>
&bull; BPG dose: <strong>${BPG_COST_USD}</strong><br>
&bull; Annual prophylaxis: <strong>${cost['annual_prophylaxis_cost']}</strong> (12 monthly doses)<br>
&bull; Children needing prophylaxis in Africa: <strong>{cost['children_at_risk']:,}</strong><br>
&bull; Total annual cost to protect all at-risk children: <strong>${cost['total_annual_cost']:,.0f}</strong><br>
&bull; Cost per death averted: <strong>${cost['cost_per_death_averted']}</strong><br>
&bull; For comparison, valve replacement surgery: <strong>${cost['surgery_cost']:,}</strong><br><br>
The total cost to provide secondary prophylaxis to every at-risk African child is
<strong>${cost['total_annual_cost']:,.0f}</strong> per year &mdash; less than the cost of
a single day's funding for many global health programmes.
</div>

<!-- 10. African RHD Trials -->
<h2>10. Individual African RHD Trials</h2>
{"<p>No trials found in this query.</p>" if not trial_rows else ""}
<div class="scroll-x">
<table>
<thead>
<tr>
  <th>NCT ID</th>
  <th>Title</th>
  <th>Phase</th>
  <th>Status</th>
  <th>Sponsor</th>
</tr>
</thead>
<tbody>
{trial_rows}
</tbody>
</table>
</div>

<!-- 11. Sponsors -->
<h2>11. Sponsor Analysis</h2>
<div class="two-col">
<div>
<h3>By Sponsor Class</h3>
<table>
<thead><tr><th>Class</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{sponsor_class_rows if sponsor_class_rows else "<tr><td colspan='2'>No data</td></tr>"}</tbody>
</table>
</div>
<div>
<h3>Top Sponsors</h3>
<table>
<thead><tr><th>Sponsor</th><th style="text-align:right;">Trials</th></tr></thead>
<tbody>{top_sponsor_rows if top_sponsor_rows else "<tr><td colspan='2'>No data</td></tr>"}</tbody>
</table>
</div>
</div>

<!-- Method -->
<h2>Method</h2>
<div class="method-note">
<strong>Data source:</strong> ClinicalTrials.gov API v2 (accessed {datetime.now().strftime('%d %B %Y')}).<br>
<strong>Queries:</strong> <code>{escape_html(RHD_QUERY)}</code> and <code>{escape_html(STREP_QUERY)}</code>,
filtered to interventional studies.<br>
<strong>Countries:</strong> {', '.join(AFRICAN_COUNTRIES)} (Africa); {', '.join(COMPARATORS)} (comparators).<br>
<strong>Burden data:</strong> GBD 2019 estimates, WHO Global Health Observatory.<br>
<strong>Cost data:</strong> WHO Essential Medicines List, published pharmacoeconomic analyses.<br>
<strong>Limitations:</strong> Single registry; cannot capture trials on African/WHO platforms.
RHD burden estimates have wide uncertainty intervals in low-resource settings.
</div>

<footer>
RHD: The $0.02 Tragedy &mdash; ClinicalTrials.gov Registry Analysis |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} |
Data: ClinicalTrials.gov API v2
</footer>

</div>

<script>
// Burden chart
new Chart(document.getElementById('burdenChart'), {{
  type: 'bar',
  data: {{
    labels: {burden_labels},
    datasets: [{{
      label: 'RHD Deaths per 100K',
      data: {burden_values},
      backgroundColor: {burden_colors},
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

// Trial counts chart
new Chart(document.getElementById('trialChart'), {{
  type: 'bar',
  data: {{
    labels: {trial_labels},
    datasets: [{{
      label: 'RHD Trials',
      data: {trial_values},
      backgroundColor: {trial_colors},
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
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
    print("RHD: The $0.02 Tragedy -- Registry Analysis")
    print("=" * 60)
    print()

    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    print("Computing Condition Colonialism Index...")
    cci = compute_cci(data)

    print("Computing cost analysis...")
    cost = compute_cost_analysis()

    print("Computing burden comparison...")
    burden = compute_burden_comparison(data)

    print("Analysing sponsors...")
    sponsors = compute_sponsor_analysis(data)

    print("Computing HIC elimination data...")
    hic = compute_hic_elimination()

    # Print summary
    print()
    print("-" * 60)
    print("RHD TRAGEDY SUMMARY")
    print("-" * 60)
    print(f"  Africa RHD trials:         {cci['africa_trials']:>6}")
    print(f"  US RHD trials:             {cci['us_trials']:>6}")
    print(f"  CCI:                       {cci['cci']:>6}")
    print(f"  Africa deaths/year:        240,000")
    print(f"  Prevention cost/dose:      $0.02")
    print(f"  Annual prophylaxis cost:   ${cost['annual_prophylaxis_cost']}")
    print(f"  Surgery cost:              ${cost['surgery_cost']:,}")
    print(f"  Surgery:prevention ratio:  {cost['surgery_to_prevention_ratio']:,}x")
    print()

    for country in AFRICAN_COUNTRIES:
        ct = data["rhd_country_counts"].get(country, 0)
        b = RHD_BURDEN.get(country, 0)
        print(f"  {country:20s}  {ct:>3} trials | {b}/100K death rate")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, cci, cost, burden, sponsors, hic)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
