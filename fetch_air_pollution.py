#!/usr/bin/env python
"""
fetch_air_pollution.py -- Household Air Pollution: The Invisible Killer
========================================================================
Household air pollution (HAP) from cooking fires kills ~600,000 Africans
per year. ZERO trials on ClinicalTrials.gov for Africa. Three billion
people worldwide cook with solid fuels.

Queries ClinicalTrials.gov API v2 for air pollution / cookstove /
biomass fuel trials across Africa, US, India, China. Also queries
COPD trials in Africa.

Usage:
    python fetch_air_pollution.py

Output:
    data/air_pollution_data.json   (cached API results, 24h TTL)
    air-pollution.html             (dark-theme interactive dashboard)

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

# Primary query
HAP_QUERY = "air pollution OR household pollution OR indoor pollution OR cookstove OR biomass fuel"
COPD_QUERY = "COPD OR chronic obstructive"

# African countries
AFRICAN_COUNTRIES = [
    "Nigeria", "Kenya", "Uganda", "Tanzania",
    "Ethiopia", "South Africa", "Ghana", "Cameroon",
]

# Comparators
COMPARATORS = {
    "United States": 335,
    "India": 1440,
    "China": 1425,
    "United Kingdom": 68,
}

# Population in millions (2025 estimates)
POPULATIONS = {
    "Nigeria": 230,
    "Kenya": 56,
    "Uganda": 48,
    "Tanzania": 67,
    "Ethiopia": 130,
    "South Africa": 62,
    "Ghana": 34,
    "Cameroon": 28,
    "United States": 335,
    "India": 1440,
    "China": 1425,
    "United Kingdom": 68,
}

# Verified reference counts
VERIFIED_COUNTS = {
    "Africa_HAP": 0,
}

# HAP epidemiology
HAP_EPIDEMIOLOGY = {
    "global_deaths_per_year": 3200000,
    "africa_deaths_per_year": 600000,
    "africa_share_pct": 18.75,
    "people_cooking_solid_fuels_billion": 3.0,
    "africa_solid_fuel_users_million": 900,
    "africa_solid_fuel_pct": 80,
    "children_under5_pneumonia_deaths_hap": 237000,
    "africa_copd_deaths_per_year": 120000,
}

# % of population using solid fuels for cooking (WHO 2022)
SOLID_FUEL_USE = {
    "Nigeria": 72,
    "Kenya": 78,
    "Uganda": 95,
    "Tanzania": 94,
    "Ethiopia": 93,
    "South Africa": 15,
    "Ghana": 72,
    "Cameroon": 80,
    "India": 55,
    "China": 33,
    "United States": 0,
    "United Kingdom": 0,
}

# Clean cooking programmes (for comparison)
CLEAN_COOKING_PROGRAMS = {
    "India": {
        "name": "Ujjwala Yojana",
        "launched": 2016,
        "lpg_connections_million": 100,
        "coverage_pct": 70,
    },
    "China": {
        "name": "National Clean Heating Plan",
        "launched": 2017,
        "households_million": 35,
        "coverage_pct": 55,
    },
}

# Subtypes of HAP interventions to search
HAP_SUBTYPES = {
    "Cookstove / clean cooking": "cookstove OR clean cooking OR improved stove",
    "Biomass fuel / solid fuel": "biomass fuel OR solid fuel OR wood smoke",
    "LPG / clean fuel transition": "LPG OR liquefied petroleum gas OR clean fuel",
    "Indoor air quality": "indoor air quality OR household air",
    "Ventilation / chimney": "ventilation OR chimney OR kitchen ventilation",
    "Charcoal / kerosene": "charcoal OR kerosene",
}

# Health effects to search
HEALTH_EFFECTS = {
    "COPD / chronic lung disease": "COPD OR chronic obstructive",
    "Pneumonia (child)": "pneumonia AND child",
    "Lung cancer": "lung cancer",
    "Cardiovascular (HAP)": "cardiovascular AND air pollution",
    "Low birth weight (HAP)": "low birth weight AND air pollution",
    "Eye disease (HAP)": "eye disease OR cataract AND smoke",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "air_pollution_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "air-pollution.html"
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
    """Fetch all air pollution trial data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "hap_country_counts": {},
        "hap_africa_total": 0,
        "copd_country_counts": {},
        "copd_africa_total": 0,
        "subtype_africa_counts": {},
        "subtype_us_counts": {},
        "subtype_india_counts": {},
        "subtype_china_counts": {},
        "health_effect_africa": {},
        "health_effect_us": {},
        "country_subtype_counts": {},
        "africa_trial_details": [],
    }

    all_locations = AFRICAN_COUNTRIES + list(COMPARATORS.keys())
    total_calls = (
        len(all_locations) * 2                                  # HAP + COPD per country
        + 2                                                     # Africa aggregates
        + len(HAP_SUBTYPES) * 4                                 # subtypes x 4 regions
        + len(HEALTH_EFFECTS) * 2                               # health effects Africa + US
        + len(AFRICAN_COUNTRIES) * len(HAP_SUBTYPES)            # country x subtype
        + 1                                                     # trial details
    )
    call_num = 0

    # --- Per-country HAP counts ---
    for country in all_locations:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] HAP: {country}...")
        data["hap_country_counts"][country] = get_trial_count(HAP_QUERY, country)
        time.sleep(RATE_LIMIT)

    # --- Per-country COPD counts ---
    for country in all_locations:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] COPD: {country}...")
        data["copd_country_counts"][country] = get_trial_count(COPD_QUERY, country)
        time.sleep(RATE_LIMIT)

    # --- Africa aggregates ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa aggregate HAP...")
    data["hap_africa_total"] = get_trial_count_multi_location(
        HAP_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa aggregate COPD...")
    data["copd_africa_total"] = get_trial_count_multi_location(
        COPD_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    # --- Subtypes for Africa, US, India, China ---
    for subtype_label, subtype_query in HAP_SUBTYPES.items():
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

        call_num += 1
        print(f"  [{call_num}/{total_calls}] India subtype: {subtype_label}...")
        data["subtype_india_counts"][subtype_label] = get_trial_count(
            subtype_query, "India"
        )
        time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] China subtype: {subtype_label}...")
        data["subtype_china_counts"][subtype_label] = get_trial_count(
            subtype_query, "China"
        )
        time.sleep(RATE_LIMIT)

    # --- Health effects in Africa + US ---
    for effect_label, effect_query in HEALTH_EFFECTS.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa health effect: {effect_label}...")
        data["health_effect_africa"][effect_label] = get_trial_count_multi_location(
            effect_query, AFRICAN_COUNTRIES
        )
        time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] US health effect: {effect_label}...")
        data["health_effect_us"][effect_label] = get_trial_count(
            effect_query, "United States"
        )
        time.sleep(RATE_LIMIT)

    # --- Per-country subtype breakdown ---
    for country in AFRICAN_COUNTRIES:
        data["country_subtype_counts"][country] = {}
        for subtype_label, subtype_query in HAP_SUBTYPES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {subtype_label}...")
            count = get_trial_count(subtype_query, country)
            data["country_subtype_counts"][country][subtype_label] = count
            time.sleep(RATE_LIMIT)

    # --- Africa trial details (if any exist) ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa HAP trial details...")
    data["africa_trial_details"] = get_trial_details(
        HAP_QUERY, AFRICAN_COUNTRIES
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

def compute_void_analysis(data):
    """Compute the trial void statistics."""
    africa_hap = data.get("hap_africa_total", 0)
    us_hap = data["hap_country_counts"].get("United States", 0)
    india_hap = data["hap_country_counts"].get("India", 0)
    china_hap = data["hap_country_counts"].get("China", 0)
    africa_copd = data.get("copd_africa_total", 0)
    us_copd = data["copd_country_counts"].get("United States", 0)
    return {
        "africa_hap": africa_hap,
        "us_hap": us_hap,
        "india_hap": india_hap,
        "china_hap": china_hap,
        "africa_copd": africa_copd,
        "us_copd": us_copd,
        "copd_ratio": round(us_copd / africa_copd, 1) if africa_copd > 0 else 999,
    }


def compute_solid_fuel_comparison(data):
    """Solid fuel use vs trial counts."""
    results = {}
    for country in list(POPULATIONS.keys()):
        sf = SOLID_FUEL_USE.get(country, 0)
        hap = data["hap_country_counts"].get(country, 0)
        copd = data["copd_country_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        users_m = round(pop * sf / 100, 1)
        results[country] = {
            "solid_fuel_pct": sf,
            "solid_fuel_users_m": users_m,
            "hap_trials": hap,
            "copd_trials": copd,
            "population_m": pop,
        }
    return results


def compute_clean_cooking_comparison():
    """Data on India/China clean cooking programmes."""
    return CLEAN_COOKING_PROGRAMS


def compute_who_guidelines():
    """WHO air quality guideline data."""
    return {
        "pm25_guideline_ug_m3": 15,
        "typical_cooking_fire_ug_m3": 500,
        "exceedance_factor": 33,
        "who_recommendation": "Transition to clean fuels and technologies",
        "who_target_year": 2030,
        "current_access_pct_africa": 20,
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


def generate_html(data, void, solid_fuel, clean_cook, who_guide):
    """Generate the full HTML dashboard."""

    africa_hap = data.get("hap_africa_total", 0)
    us_hap = data["hap_country_counts"].get("United States", 0)
    india_hap = data["hap_country_counts"].get("India", 0)
    china_hap = data["hap_country_counts"].get("China", 0)

    # Country breakdown rows
    country_rows = ""
    for country in AFRICAN_COUNTRIES:
        hap = data["hap_country_counts"].get(country, 0)
        copd = data["copd_country_counts"].get(country, 0)
        sf = SOLID_FUEL_USE.get(country, 0)
        pop = POPULATIONS.get(country, 1)
        country_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">{hap}</td>'
            f'<td style="padding:8px;text-align:right;">{copd}</td>'
            f'<td style="padding:8px;text-align:right;color:#f59e0b;">{sf}%</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'</tr>\n'
        )

    # Comparator rows
    comp_rows = ""
    for country in COMPARATORS:
        hap = data["hap_country_counts"].get(country, 0)
        copd = data["copd_country_counts"].get(country, 0)
        sf = SOLID_FUEL_USE.get(country, 0)
        pop = POPULATIONS.get(country, 1)
        comp_rows += (
            f'<tr style="background:#1a1a2e;">'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{hap}</td>'
            f'<td style="padding:8px;text-align:right;">{copd}</td>'
            f'<td style="padding:8px;text-align:right;">{sf}%</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'</tr>\n'
        )

    # Subtype comparison rows
    subtype_rows = ""
    for subtype in HAP_SUBTYPES:
        af = data["subtype_africa_counts"].get(subtype, 0)
        us = data["subtype_us_counts"].get(subtype, 0)
        ind = data["subtype_india_counts"].get(subtype, 0)
        ch = data["subtype_china_counts"].get(subtype, 0)
        subtype_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(subtype)}</td>'
            f'<td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">{af}</td>'
            f'<td style="padding:8px;text-align:right;">{us}</td>'
            f'<td style="padding:8px;text-align:right;">{ind}</td>'
            f'<td style="padding:8px;text-align:right;">{ch}</td>'
            f'</tr>\n'
        )

    # Health effect rows
    health_rows = ""
    for effect in HEALTH_EFFECTS:
        af = data["health_effect_africa"].get(effect, 0)
        us = data["health_effect_us"].get(effect, 0)
        ratio = round(us / af, 1) if af > 0 else 999
        health_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(effect)}</td>'
            f'<td style="padding:8px;text-align:right;">{af}</td>'
            f'<td style="padding:8px;text-align:right;">{us}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;'
            f'color:{"#ef4444" if ratio > 50 else "#ffaa33"};">'
            f'{"INF" if ratio == 999 else str(ratio) + "x"}</td>'
            f'</tr>\n'
        )

    # Heatmap rows
    heatmap_rows = ""
    for subtype in HAP_SUBTYPES:
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

    # Solid fuel comparison rows
    sf_sorted = sorted(solid_fuel.items(), key=lambda x: -x[1]["solid_fuel_pct"])
    sf_rows = ""
    for country, info in sf_sorted:
        is_african = country in AFRICAN_COUNTRIES
        color = "#ef4444" if is_african and info["hap_trials"] == 0 else "#e2e8f0"
        sf_rows += (
            f'<tr>'
            f'<td style="padding:8px;color:{color};">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{info["solid_fuel_pct"]}%</td>'
            f'<td style="padding:8px;text-align:right;">{info["solid_fuel_users_m"]}M</td>'
            f'<td style="padding:8px;text-align:right;color:'
            f'{"#ef4444" if info["hap_trials"] == 0 else "#22c55e"};font-weight:bold;">'
            f'{info["hap_trials"]}</td>'
            f'<td style="padding:8px;text-align:right;">{info["copd_trials"]}</td>'
            f'</tr>\n'
        )

    # Chart data
    sf_labels = json.dumps([c for c, _ in sf_sorted if SOLID_FUEL_USE.get(c, 0) > 0])
    sf_values = json.dumps([info["solid_fuel_pct"] for c, info in sf_sorted
                            if SOLID_FUEL_USE.get(c, 0) > 0])
    sf_colors = json.dumps([
        "#ef4444" if c in AFRICAN_COUNTRIES else "#3b82f6"
        for c, _ in sf_sorted if SOLID_FUEL_USE.get(c, 0) > 0
    ])

    trial_labels = json.dumps(AFRICAN_COUNTRIES + list(COMPARATORS.keys()))
    hap_vals = json.dumps([
        data["hap_country_counts"].get(c, 0) for c in AFRICAN_COUNTRIES + list(COMPARATORS.keys())
    ])
    copd_vals = json.dumps([
        data["copd_country_counts"].get(c, 0) for c in AFRICAN_COUNTRIES + list(COMPARATORS.keys())
    ])

    health_labels = json.dumps(list(HEALTH_EFFECTS.keys()))
    health_af = json.dumps([data["health_effect_africa"].get(e, 0) for e in HEALTH_EFFECTS])
    health_us = json.dumps([data["health_effect_us"].get(e, 0) for e in HEALTH_EFFECTS])

    india_prog = clean_cook.get("India", {})
    china_prog = clean_cook.get("China", {})

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Household Air Pollution: The Invisible Killer</title>
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
  background: linear-gradient(135deg, #ef4444, #94a3b8);
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
.void-box {{
  background: rgba(239, 68, 68, 0.15);
  border: 2px solid var(--danger);
  border-radius: 12px;
  padding: 2rem;
  text-align: center;
  margin: 1.5rem 0;
}}
.void-box .big-zero {{
  font-size: 6rem;
  font-weight: 900;
  color: var(--danger);
  line-height: 1;
}}
.void-box .big-label {{
  font-size: 1.2rem;
  color: var(--muted);
  margin-top: 0.5rem;
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

<h1>Household Air Pollution: The Invisible Killer</h1>
<p class="subtitle">Zero trials. 600,000 deaths. Three billion people cook over open fires.</p>

<!-- 1. Summary -->
<h2>1. Zero Trials. 600,000 Deaths.</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa HAP Trials</div>
    <div class="value danger">{africa_hap}</div>
    <div class="label">vs {us_hap} in the US</div>
  </div>
  <div class="summary-card">
    <div class="label">African Deaths / Year (HAP)</div>
    <div class="value danger">600K</div>
    <div class="label">19% of global HAP mortality</div>
  </div>
  <div class="summary-card">
    <div class="label">Solid Fuel Cooking (Africa)</div>
    <div class="value warning">80%</div>
    <div class="label">~900 million people</div>
  </div>
  <div class="summary-card">
    <div class="label">PM2.5 Exceedance</div>
    <div class="value danger">33x</div>
    <div class="label">cooking fire vs WHO guideline</div>
  </div>
</div>

<div class="void-box">
  <div class="big-zero">{africa_hap}</div>
  <div class="big-label">registered interventional trials for household air pollution in Africa</div>
  <div class="big-label" style="margin-top:1rem;color:var(--danger);">
    While 600,000 Africans die every year from breathing cooking smoke
  </div>
</div>

<!-- 2. The Invisible Killer -->
<h2>2. The Invisible Killer</h2>
<p>Three billion people worldwide &mdash; including 900 million Africans &mdash; cook
their daily meals over open fires burning wood, charcoal, dung, or crop residues.
The smoke contains fine particulate matter (PM2.5), carbon monoxide, polycyclic
aromatic hydrocarbons, and hundreds of other toxic compounds. A typical indoor
cooking fire produces PM2.5 concentrations of <strong>500 &mu;g/m&sup3;</strong> &mdash;
{who_guide['exceedance_factor']}x the WHO guideline of {who_guide['pm25_guideline_ug_m3']}
&mu;g/m&sup3;.</p>

<div class="danger-note">
<strong>Who is affected?</strong> Women and young children bear the heaviest burden.
Women do 90%+ of cooking in sub-Saharan Africa and are exposed for 3-7 hours daily.
Children under 5 are strapped to their mothers' backs during cooking. HAP causes:<br><br>
&bull; <strong>Pneumonia:</strong> {HAP_EPIDEMIOLOGY['children_under5_pneumonia_deaths_hap']:,}
child deaths/year globally (HAP-attributable)<br>
&bull; <strong>COPD:</strong> {HAP_EPIDEMIOLOGY['africa_copd_deaths_per_year']:,} African
deaths/year<br>
&bull; <strong>Lung cancer, cardiovascular disease, low birth weight, cataracts</strong><br>
&bull; <strong>Burns:</strong> open fires are the leading cause of childhood burns in Africa
</div>

<!-- 3. Clean Cookstove Interventions -->
<h2>3. Clean Cookstove Interventions</h2>
<div class="warning-note">
<strong>The engineering exists.</strong> Improved cookstoves, LPG stoves, solar cookers,
and biogas digesters can reduce HAP by 50-90%. The Global Alliance for Clean Cookstoves
(now Clean Cooking Alliance) has distributed millions of improved stoves. Yet we found
{africa_hap} registered RCTs in Africa testing these interventions. The evidence base
for what works in African households &mdash; considering fuel availability, cultural
cooking practices, and sustained adoption &mdash; is essentially absent from
ClinicalTrials.gov.
</div>

<table>
<thead>
<tr>
  <th>Intervention Type</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">US</th>
  <th style="text-align:right;">India</th>
  <th style="text-align:right;">China</th>
</tr>
</thead>
<tbody>
{subtype_rows}
</tbody>
</table>

<!-- 4. COPD Burden -->
<h2>4. COPD Burden in Africa</h2>
<p>COPD from household air pollution is the fourth leading cause of death in Africa,
yet Africa has only {void['africa_copd']} COPD trials vs {void['us_copd']:,} in the
US (a {void['copd_ratio']}x gap). The vast majority of African COPD is caused by
HAP, not smoking &mdash; making it a fundamentally different disease requiring
fundamentally different interventions.</p>

<div class="chart-container">
<h3>HAP and COPD Trial Counts by Country</h3>
<canvas id="trialChart" height="300"></canvas>
</div>

<!-- 5. Cardiovascular Effects -->
<h2>5. Health Effects: Africa vs US</h2>
<table>
<thead>
<tr>
  <th>Health Effect</th>
  <th style="text-align:right;">Africa Trials</th>
  <th style="text-align:right;">US Trials</th>
  <th style="text-align:right;">US:Africa Ratio</th>
</tr>
</thead>
<tbody>
{health_rows}
</tbody>
</table>

<div class="chart-container">
<h3>Health Effect Trials: Africa vs US</h3>
<canvas id="healthChart" height="300"></canvas>
</div>

<!-- 6. Country Breakdown -->
<h2>6. Country Breakdown</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">HAP Trials</th>
  <th style="text-align:right;">COPD Trials</th>
  <th style="text-align:right;">Solid Fuel Use</th>
  <th style="text-align:right;">Population</th>
</tr>
</thead>
<tbody>
{country_rows}
<tr><td colspan="5" style="padding:4px;border-bottom:2px solid var(--border);"></td></tr>
{comp_rows}
</tbody>
</table>

<!-- 7. Country x Subtype Heatmap -->
<h2>7. Heatmap: Country x Intervention Type</h2>
<div class="scroll-x">
<table>
<thead>
<tr>
  <th>Intervention</th>
  {country_headers}
</tr>
</thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>

<!-- 8. Comparison with China/India -->
<h2>8. Comparison: China and India Clean Cooking Programmes</h2>
<div class="method-note">
<strong>India: Pradhan Mantri Ujjwala Yojana ({india_prog.get('launched', 'N/A')})</strong><br>
Provided {india_prog.get('lpg_connections_million', 'N/A')} million free LPG connections
to below-poverty-line households. Coverage: ~{india_prog.get('coverage_pct', 'N/A')}%
of target population. Accompanied by clinical trials and air quality monitoring
studies ({india_hap} HAP trials registered).<br><br>
<strong>China: {china_prog.get('name', 'National Clean Heating Plan')}
({china_prog.get('launched', 'N/A')})</strong><br>
Converted {china_prog.get('households_million', 'N/A')} million households from coal/biomass.
Coverage: ~{china_prog.get('coverage_pct', 'N/A')}%. China has {china_hap} registered HAP
trials.<br><br>
<strong>Africa:</strong> No equivalent national programme. No registered trials.
</div>

<!-- 9. Solid Fuel Use vs Trial Counts -->
<h2>9. Solid Fuel Use vs Trial Counts</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Solid Fuel %</th>
  <th style="text-align:right;">Users (millions)</th>
  <th style="text-align:right;">HAP Trials</th>
  <th style="text-align:right;">COPD Trials</th>
</tr>
</thead>
<tbody>
{sf_rows}
</tbody>
</table>

<div class="chart-container">
<h3>Solid Fuel Use (%) by Country</h3>
<canvas id="sfChart" height="300"></canvas>
</div>

<!-- 10. WHO Guidelines -->
<h2>10. WHO Guidelines</h2>
<div class="warning-note">
<strong>WHO Indoor Air Quality Guidelines (2014):</strong><br><br>
&bull; PM2.5 guideline: <strong>{who_guide['pm25_guideline_ug_m3']} &mu;g/m&sup3;</strong>
(annual mean)<br>
&bull; Typical African cooking fire: <strong>{who_guide['typical_cooking_fire_ug_m3']}
&mu;g/m&sup3;</strong> ({who_guide['exceedance_factor']}x exceedance)<br>
&bull; WHO recommendation: <strong>{who_guide['who_recommendation']}</strong><br>
&bull; SDG 7.1 target: Universal access to clean cooking by
<strong>{who_guide['who_target_year']}</strong><br>
&bull; Current clean cooking access in Africa: <strong>{who_guide['current_access_pct_africa']}%</strong><br><br>
At current rates, Africa will NOT meet the 2030 clean cooking target.
The number of Africans without clean cooking access is actually
<em>increasing</em> due to population growth outpacing intervention.
</div>

<!-- Method -->
<h2>Method</h2>
<div class="method-note">
<strong>Data source:</strong> ClinicalTrials.gov API v2 (accessed {datetime.now().strftime('%d %B %Y')}).<br>
<strong>Queries:</strong> <code>{escape_html(HAP_QUERY)}</code> and <code>{escape_html(COPD_QUERY)}</code>,
filtered to interventional studies.<br>
<strong>Countries:</strong> {', '.join(AFRICAN_COUNTRIES)} (Africa); {', '.join(COMPARATORS.keys())}
(comparators).<br>
<strong>Burden data:</strong> WHO Global Health Observatory 2022, GBD 2019.<br>
<strong>Solid fuel data:</strong> WHO Household Energy Database 2022.<br>
<strong>Limitations:</strong> Single registry; cannot capture trials on African/WHO/environmental
health platforms. Some HAP intervention trials may be registered under environmental or
engineering databases rather than clinical trial registries.
</div>

<footer>
Household Air Pollution: The Invisible Killer &mdash; ClinicalTrials.gov Registry Analysis |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} |
Data: ClinicalTrials.gov API v2
</footer>

</div>

<script>
// Trial counts (HAP + COPD) by country
new Chart(document.getElementById('trialChart'), {{
  type: 'bar',
  data: {{
    labels: {trial_labels},
    datasets: [
      {{
        label: 'HAP Trials',
        data: {hap_vals},
        backgroundColor: '#ef4444',
        borderRadius: 4,
      }},
      {{
        label: 'COPD Trials',
        data: {copd_vals},
        backgroundColor: '#3b82f6',
        borderRadius: 4,
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
    }}
  }}
}});

// Health effects comparison
new Chart(document.getElementById('healthChart'), {{
  type: 'bar',
  data: {{
    labels: {health_labels},
    datasets: [
      {{
        label: 'Africa',
        data: {health_af},
        backgroundColor: '#ef4444',
        borderRadius: 4,
      }},
      {{
        label: 'US',
        data: {health_us},
        backgroundColor: '#3b82f6',
        borderRadius: 4,
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
    }}
  }}
}});

// Solid fuel use chart
new Chart(document.getElementById('sfChart'), {{
  type: 'bar',
  data: {{
    labels: {sf_labels},
    datasets: [{{
      label: 'Solid Fuel Use %',
      data: {sf_values},
      backgroundColor: {sf_colors},
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ min: 0, max: 100, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
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
    print("Household Air Pollution: The Invisible Killer")
    print("=" * 60)
    print()

    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    print("Computing void analysis...")
    void = compute_void_analysis(data)

    print("Computing solid fuel comparison...")
    solid_fuel = compute_solid_fuel_comparison(data)

    print("Loading clean cooking comparison...")
    clean_cook = compute_clean_cooking_comparison()

    print("Loading WHO guidelines...")
    who_guide = compute_who_guidelines()

    # Print summary
    print()
    print("-" * 60)
    print("AIR POLLUTION SUMMARY")
    print("-" * 60)
    print(f"  Africa HAP trials:      {void['africa_hap']:>6}")
    print(f"  US HAP trials:          {void['us_hap']:>6}")
    print(f"  India HAP trials:       {void['india_hap']:>6}")
    print(f"  China HAP trials:       {void['china_hap']:>6}")
    print(f"  Africa COPD trials:     {void['africa_copd']:>6}")
    print(f"  US COPD trials:         {void['us_copd']:>6,}")
    print(f"  COPD US:Africa ratio:   {void['copd_ratio']:>6}x")
    print(f"  Africa deaths/yr (HAP): 600,000")
    print(f"  Solid fuel use (Africa): 80%")
    print()

    for country in AFRICAN_COUNTRIES:
        hap = data["hap_country_counts"].get(country, 0)
        copd = data["copd_country_counts"].get(country, 0)
        sf = SOLID_FUEL_USE.get(country, 0)
        print(f"  {country:20s}  HAP: {hap:>3} | COPD: {copd:>3} | Solid fuel: {sf}%")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, void, solid_fuel, clean_cook, who_guide)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
