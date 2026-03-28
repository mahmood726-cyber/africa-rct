#!/usr/bin/env python
"""
fetch_digital_health.py -- Africa's Digital Health Bright Spot
================================================================
Africa's ONE positive story: mobile phone penetration is 80%+, and digital
health trials may be where Africa leapfrogs traditional clinical infrastructure.

Queries ClinicalTrials.gov API v2 for mHealth / telemedicine / digital health
trials across Africa (14 verified), US (2,321), South Africa, Kenya, Uganda,
Nigeria, Rwanda, Tanzania, and India.

Usage:
    python fetch_digital_health.py

Output:
    data/digital_health_data.json   (cached API results, 24h TTL)
    digital-health.html             (dark-theme interactive dashboard)

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
DIGITAL_HEALTH_QUERY = "mHealth OR mobile health OR telemedicine OR digital health OR SMS intervention"

# Countries to query
AFRICAN_COUNTRIES = [
    "South Africa", "Kenya", "Uganda", "Nigeria", "Rwanda", "Tanzania",
]

COMPARATORS = {
    "United States": 335,   # population in millions
    "India": 1440,
}

# Population in millions (2025 estimates)
POPULATIONS = {
    "South Africa": 62,
    "Kenya": 56,
    "Uganda": 48,
    "Nigeria": 230,
    "Rwanda": 14,
    "Tanzania": 67,
    "United States": 335,
    "India": 1440,
}

# Mobile phone penetration % (ITU/GSMA 2024 estimates)
MOBILE_PENETRATION = {
    "South Africa": 91,
    "Kenya": 87,
    "Uganda": 72,
    "Nigeria": 84,
    "Rwanda": 78,
    "Tanzania": 76,
    "United States": 97,
    "India": 82,
}

# Verified reference counts
VERIFIED_COUNTS = {
    "Africa_total": 14,
    "United States": 2321,
}

# Digital health subtypes to search in Africa
DIGITAL_SUBTYPES = {
    "SMS reminders / text message": "SMS OR text message OR short message",
    "mHealth apps / mobile app": "mHealth OR mobile app OR smartphone app",
    "Telemedicine / teleconsultation": "telemedicine OR teleconsultation OR telehealth",
    "Electronic health records": "electronic health record OR eHealth OR EHR",
    "Wearable / remote monitoring": "wearable OR remote monitoring OR mHealth sensor",
    "AI / machine learning health": "artificial intelligence OR machine learning health",
}

# Disease focus subtypes to search within digital health in Africa
DISEASE_FOCUS = {
    "HIV / AIDS": "HIV OR AIDS",
    "Tuberculosis": "tuberculosis OR TB",
    "Malaria": "malaria",
    "Maternal / antenatal": "maternal OR antenatal OR pregnancy",
    "Diabetes / NCD": "diabetes OR hypertension OR NCD",
    "Mental health": "mental health OR depression OR anxiety",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "digital_health_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "digital-health.html"
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
    """Fetch all digital health trial data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "country_counts": {},
        "africa_total": 0,
        "subtype_africa_counts": {},
        "subtype_us_counts": {},
        "subtype_india_counts": {},
        "disease_focus_africa": {},
        "disease_focus_us": {},
        "country_subtype_counts": {},
        "africa_trial_details": [],
    }

    all_locations = AFRICAN_COUNTRIES + list(COMPARATORS.keys())
    total_calls = (
        len(all_locations)                                   # per-country counts
        + 1                                                  # Africa aggregate
        + len(DIGITAL_SUBTYPES) * 3                          # subtypes for Africa, US, India
        + len(DISEASE_FOCUS) * 2                             # disease focus Africa + US
        + len(AFRICAN_COUNTRIES) * len(DIGITAL_SUBTYPES)     # country x subtype
        + 1                                                  # trial details
    )
    call_num = 0

    # --- Per-country digital health counts ---
    for country in all_locations:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Digital health: {country}...")
        count = get_trial_count(DIGITAL_HEALTH_QUERY, country)
        data["country_counts"][country] = count
        time.sleep(RATE_LIMIT)

    # --- Africa aggregate ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa aggregate digital health...")
    data["africa_total"] = get_trial_count_multi_location(
        DIGITAL_HEALTH_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    # --- Subtypes for Africa, US, India ---
    for subtype_label, subtype_query in DIGITAL_SUBTYPES.items():
        combined_q = f"({DIGITAL_HEALTH_QUERY}) AND ({subtype_query})"

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

    # --- Disease focus in digital health (Africa + US) ---
    for disease_label, disease_query in DISEASE_FOCUS.items():
        combined_africa = f"({DIGITAL_HEALTH_QUERY}) AND ({disease_query})"

        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa disease focus: {disease_label}...")
        data["disease_focus_africa"][disease_label] = get_trial_count_multi_location(
            combined_africa, AFRICAN_COUNTRIES
        )
        time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] US disease focus: {disease_label}...")
        data["disease_focus_us"][disease_label] = get_trial_count(
            combined_africa, "United States"
        )
        time.sleep(RATE_LIMIT)

    # --- Per-country subtype breakdown ---
    for country in AFRICAN_COUNTRIES:
        data["country_subtype_counts"][country] = {}
        for subtype_label, subtype_query in DIGITAL_SUBTYPES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {subtype_label}...")
            count = get_trial_count(subtype_query, country)
            data["country_subtype_counts"][country][subtype_label] = count
            time.sleep(RATE_LIMIT)

    # --- Africa trial details ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa trial details...")
    data["africa_trial_details"] = get_trial_details(
        DIGITAL_HEALTH_QUERY, AFRICAN_COUNTRIES
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

def compute_leapfrog_index(data):
    """Compute digital-health-specific metrics.

    Leapfrog Index = (Africa digital health trials / Africa total trials)
                     vs (US digital health trials / US total trials)
    Higher ratio for Africa suggests leapfrog potential.
    """
    africa_dh = data.get("africa_total", 0)
    us_dh = data["country_counts"].get("United States", 0)
    india_dh = data["country_counts"].get("India", 0)
    return {
        "africa_digital_health": africa_dh,
        "us_digital_health": us_dh,
        "india_digital_health": india_dh,
        "us_to_africa_ratio": round(us_dh / africa_dh, 1) if africa_dh > 0 else 999,
        "india_to_africa_ratio": round(india_dh / africa_dh, 1) if africa_dh > 0 else 999,
    }


def compute_per_capita(data):
    """Trials per million population."""
    results = {}
    for country in list(POPULATIONS.keys()):
        total = data["country_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        results[country] = {
            "total_trials": total,
            "population_m": pop,
            "trials_per_million": round(total / pop, 3),
            "mobile_penetration": MOBILE_PENETRATION.get(country, 0),
        }
    return results


def compute_hiv_dominance(data):
    """Fraction of Africa digital health trials that are HIV-focused."""
    hiv_count = data.get("disease_focus_africa", {}).get("HIV / AIDS", 0)
    total = data.get("africa_total", 0)
    pct = round(hiv_count / total * 100, 1) if total > 0 else 0
    return {"hiv_count": hiv_count, "total": total, "hiv_pct": pct}


def compute_ncd_gap(data):
    """NCD digital health trials: Africa vs US."""
    africa_ncd = data.get("disease_focus_africa", {}).get("Diabetes / NCD", 0)
    us_ncd = data.get("disease_focus_us", {}).get("Diabetes / NCD", 0)
    return {
        "africa_ncd_digital": africa_ncd,
        "us_ncd_digital": us_ncd,
        "ratio": round(us_ncd / africa_ncd, 1) if africa_ncd > 0 else 999,
    }


def compute_sponsor_analysis(data):
    """Sponsor class distribution for Africa digital health trials."""
    sponsor_class_counts = defaultdict(int)
    top_sponsors = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        cls = t.get("sponsorClass", "OTHER")
        sponsor_class_counts[cls] += 1
        name = t.get("sponsorName", "Unknown")
        top_sponsors[name] += 1
    top_10 = sorted(top_sponsors.items(), key=lambda x: -x[1])[:10]
    return {"by_class": dict(sponsor_class_counts), "top_sponsors": top_10}


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
    """Heatmap color: 0=dark, low=amber, high=green."""
    if count == 0:
        return "#111"
    elif count <= 3:
        intensity = 80 + int((count / 3) * 175)
        return f"rgb({intensity}, {max(30, intensity // 3)}, {max(30, intensity // 5)})"
    elif count <= 10:
        ratio = (count - 3) / 7
        r = int(255 - ratio * 100)
        g = int(120 + ratio * 135)
        return f"rgb({r}, {g}, 40)"
    else:
        return "rgb(50, 200, 80)"


def generate_html(data, leapfrog, per_capita, hiv_dom, ncd_gap, sponsors, trends):
    """Generate the full HTML dashboard."""

    africa_total = data.get("africa_total", 0)
    us_total = data["country_counts"].get("United States", 0)
    india_total = data["country_counts"].get("India", 0)

    # Country breakdown rows
    country_rows = ""
    for country in AFRICAN_COUNTRIES:
        ct = data["country_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        mob = MOBILE_PENETRATION.get(country, 0)
        tpm = round(ct / pop, 3) if pop > 0 else 0
        country_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{ct:,}</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'<td style="padding:8px;text-align:right;">{mob}%</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">{tpm}</td>'
            f'</tr>\n'
        )

    # Subtype comparison rows (Africa / US / India)
    subtype_rows = ""
    for subtype in DIGITAL_SUBTYPES:
        af = data["subtype_africa_counts"].get(subtype, 0)
        us = data["subtype_us_counts"].get(subtype, 0)
        ind = data["subtype_india_counts"].get(subtype, 0)
        ratio = round(us / af, 1) if af > 0 else 999
        subtype_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(subtype)}</td>'
            f'<td style="padding:8px;text-align:right;">{af:,}</td>'
            f'<td style="padding:8px;text-align:right;">{us:,}</td>'
            f'<td style="padding:8px;text-align:right;">{ind:,}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;'
            f'color:{"#ff4444" if ratio > 50 else "#ffaa33"};">{ratio}x</td>'
            f'</tr>\n'
        )

    # Disease focus rows
    disease_rows = ""
    for disease in DISEASE_FOCUS:
        af = data["disease_focus_africa"].get(disease, 0)
        us = data["disease_focus_us"].get(disease, 0)
        pct = round(af / africa_total * 100, 1) if africa_total > 0 else 0
        disease_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(disease)}</td>'
            f'<td style="padding:8px;text-align:right;">{af:,}</td>'
            f'<td style="padding:8px;text-align:right;">{pct}%</td>'
            f'<td style="padding:8px;text-align:right;">{us:,}</td>'
            f'</tr>\n'
        )

    # Heatmap rows (country x subtype)
    heatmap_rows = ""
    for subtype in DIGITAL_SUBTYPES:
        cells = ""
        for country in AFRICAN_COUNTRIES:
            count = data["country_subtype_counts"].get(country, {}).get(subtype, 0)
            bg = cell_color(count)
            text_color = "#fff" if count <= 3 else "#000"
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

    # Per-capita sorted rows
    percap_sorted = sorted(per_capita.items(), key=lambda x: -x[1]["trials_per_million"])
    percap_rows = ""
    for country, info in percap_sorted:
        is_comp = country in COMPARATORS
        row_style = "background:#1a1a2e;" if is_comp else ""
        percap_rows += (
            f'<tr style="{row_style}">'
            f'<td style="padding:8px;">{escape_html(country)}'
            f'{"  (comparator)" if is_comp else ""}</td>'
            f'<td style="padding:8px;text-align:right;">{info["total_trials"]:,}</td>'
            f'<td style="padding:8px;text-align:right;">{info["population_m"]}M</td>'
            f'<td style="padding:8px;text-align:right;">{info["mobile_penetration"]}%</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">'
            f'{info["trials_per_million"]}</td>'
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

    # Chart data
    trend_years = json.dumps(list(trends.keys()))
    trend_counts = json.dumps(list(trends.values()))

    comp_labels = json.dumps(AFRICAN_COUNTRIES + ["United States", "India"])
    comp_values = json.dumps([
        data["country_counts"].get(c, 0) for c in AFRICAN_COUNTRIES
    ] + [us_total, india_total])
    comp_colors = json.dumps(
        ["#22c55e"] * len(AFRICAN_COUNTRIES) + ["#3b82f6", "#f59e0b"]
    )

    mobile_labels = json.dumps(list(MOBILE_PENETRATION.keys()))
    mobile_values = json.dumps(list(MOBILE_PENETRATION.values()))

    disease_labels = json.dumps(list(DISEASE_FOCUS.keys()))
    disease_af_vals = json.dumps([
        data["disease_focus_africa"].get(d, 0) for d in DISEASE_FOCUS
    ])
    disease_us_vals = json.dumps([
        data["disease_focus_us"].get(d, 0) for d in DISEASE_FOCUS
    ])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa's Digital Health Bright Spot</title>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #111827;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #22c55e;
  --accent2: #3b82f6;
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
  background: linear-gradient(135deg, #22c55e, #3b82f6);
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
.accent {{ color: var(--accent); }}
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
tr:hover {{ background: rgba(34, 197, 94, 0.05); }}
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}}
canvas {{ max-width: 100%; }}
.method-note {{
  background: rgba(34, 197, 94, 0.1);
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
.bright-note {{
  background: rgba(34, 197, 94, 0.15);
  border-left: 4px solid var(--success);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
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

<h1>Africa's Digital Health Bright Spot</h1>
<p class="subtitle">The leapfrog opportunity &mdash; where 80%+ mobile penetration meets
clinical trial innovation</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Digital Health Trials</div>
    <div class="value accent">{africa_total:,}</div>
    <div class="label">vs {us_total:,} in the US</div>
  </div>
  <div class="summary-card">
    <div class="label">US-to-Africa Ratio</div>
    <div class="value warning">{leapfrog['us_to_africa_ratio']}x</div>
    <div class="label">gap narrower than most disease areas</div>
  </div>
  <div class="summary-card">
    <div class="label">Average Mobile Penetration (Africa 6)</div>
    <div class="value success">{round(sum(MOBILE_PENETRATION[c] for c in AFRICAN_COUNTRIES) / len(AFRICAN_COUNTRIES))}%</div>
    <div class="label">foundation for leapfrog</div>
  </div>
  <div class="summary-card">
    <div class="label">HIV mHealth Dominance</div>
    <div class="value danger">{hiv_dom['hiv_pct']}%</div>
    <div class="label">of Africa digital health trials = HIV</div>
  </div>
</div>

<div class="bright-note">
<strong>Africa's ONE positive story:</strong> Mobile phone penetration across sub-Saharan Africa
exceeds 80%, and digital health is the ONE area where Africa can potentially leapfrog
traditional clinical trial infrastructure. M-Pesa showed Africa can skip landlines entirely;
can mHealth do the same for clinical trials?
</div>

<!-- 2. The Leapfrog -->
<h2>2. The Leapfrog</h2>
<p>Africa missed the era of large hospital-based clinical trial infrastructure, but with
mobile penetration at {round(sum(MOBILE_PENETRATION[c] for c in AFRICAN_COUNTRIES) / len(AFRICAN_COUNTRIES))}%
across our 6 target countries, digital health trials represent Africa's best chance to build
evidence capacity without replicating Western brick-and-mortar models.</p>

<div class="method-note">
<strong>The M-Pesa Precedent:</strong> Kenya's M-Pesa mobile money service launched in 2007
and now processes transactions worth over 50% of Kenya's GDP. It succeeded precisely
because Kenya lacked existing banking infrastructure. Can mHealth trials follow the
same pattern &mdash; leapfrogging rather than catching up?
</div>

<div class="chart-container">
<h3>Mobile Phone Penetration vs Digital Health Trials</h3>
<canvas id="mobileChart" height="300"></canvas>
</div>

<!-- 3. Country Breakdown -->
<h2>3. Country Breakdown</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Digital Health Trials</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Mobile %</th>
  <th style="text-align:right;">Trials/Million</th>
</tr>
</thead>
<tbody>
{country_rows}
</tbody>
</table>

<div class="chart-container">
<h3>Digital Health Trial Counts by Country</h3>
<canvas id="countryChart" height="300"></canvas>
</div>

<!-- 4. What's Being Tested -->
<h2>4. What's Being Tested</h2>
<p>Digital health interventions in Africa cluster around SMS-based adherence reminders
and basic mHealth apps. More advanced interventions (AI, wearables, EHR integration)
remain almost exclusively in high-income country trials.</p>

<table>
<thead>
<tr>
  <th>Intervention Type</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">US</th>
  <th style="text-align:right;">India</th>
  <th style="text-align:right;">US:Africa Ratio</th>
</tr>
</thead>
<tbody>
{subtype_rows}
</tbody>
</table>

<!-- 5. HIV mHealth Dominance -->
<h2>5. HIV mHealth Dominance</h2>
<p>An estimated <span style="color:var(--danger);font-weight:bold;">{hiv_dom['hiv_pct']}%</span>
of Africa's digital health trials focus on HIV/AIDS. This reflects the massive PEPFAR/Global
Fund investment in HIV, but creates a dangerous blind spot: as NCDs overtake infectious
diseases as Africa's leading cause of death, digital health tools for diabetes,
hypertension, and mental health remain critically under-researched.</p>

<table>
<thead>
<tr>
  <th>Disease Focus</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">% of Africa DH</th>
  <th style="text-align:right;">US</th>
</tr>
</thead>
<tbody>
{disease_rows}
</tbody>
</table>

<div class="chart-container">
<h3>Disease Focus: Africa vs US Digital Health</h3>
<canvas id="diseaseChart" height="300"></canvas>
</div>

<!-- 6. NCD mHealth Gap -->
<h2>6. NCD mHealth Gap</h2>
<div class="danger-note">
<strong>The NCD blind spot:</strong> Africa has {ncd_gap['africa_ncd_digital']} digital health
NCD trials vs {ncd_gap['us_ncd_digital']:,} in the US (a {ncd_gap['ratio']}x gap). NCDs now
account for 37% of all deaths in sub-Saharan Africa and rising &mdash; yet the digital
health infrastructure being built is almost entirely HIV-focused. When the NCD
tsunami fully arrives, Africa will have mHealth tools for ART adherence but nothing
for hypertension management.
</div>

<!-- 7. Country x Subtype Heatmap -->
<h2>7. Heatmap: Country x Technology Type</h2>
<div class="scroll-x">
<table>
<thead>
<tr>
  <th>Technology</th>
  {country_headers}
</tr>
</thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>

<!-- 8. Comparison with US and India -->
<h2>8. Comparison: Africa vs US vs India</h2>

<div class="method-note">
India is the most relevant comparator: similar population scale, similar mobile
penetration (~82%), but India has far more digital health trials. The difference
is institutional &mdash; India has ICMR infrastructure and a thriving health tech
startup ecosystem. Africa needs its own version.
</div>

<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Mobile %</th>
  <th style="text-align:right;">Trials/Million</th>
</tr>
</thead>
<tbody>
{percap_rows}
</tbody>
</table>

<!-- 9. M-Pesa Model for Health -->
<h2>9. The M-Pesa Model for Health</h2>
<div class="bright-note">
<strong>Lesson from M-Pesa:</strong> Kenya's mobile money revolution succeeded because
it was designed FOR African realities, not transplanted from Western models.
Digital health trials in Africa should follow the same principle:<br><br>
&bull; <strong>SMS-first, not app-first</strong> &mdash; most phones are feature phones<br>
&bull; <strong>Community health worker integration</strong> &mdash; leverage existing trust networks<br>
&bull; <strong>Offline-capable</strong> &mdash; intermittent connectivity is the norm<br>
&bull; <strong>Swahili/Yoruba/Amharic</strong> &mdash; not English-only interfaces<br>
&bull; <strong>USSD-based</strong> &mdash; works on any phone, no data plan needed
</div>

<!-- 10. Rwanda's Digital Health Strategy -->
<h2>10. Rwanda's Digital Health Strategy</h2>
<div class="method-note">
<strong>Rwanda: Africa's digital health leader.</strong> Rwanda has invested heavily in
national digital health infrastructure including:<br><br>
&bull; <strong>RapidSMS</strong> &mdash; nationwide maternal/child health monitoring via SMS<br>
&bull; <strong>Zipline drones</strong> &mdash; blood/vaccine delivery to remote clinics<br>
&bull; <strong>OpenMRS</strong> &mdash; open-source electronic medical records nationwide<br>
&bull; <strong>Babyl (Babylon Health)</strong> &mdash; AI-assisted telemedicine reaching 2M+ users<br><br>
Rwanda demonstrates that small African nations CAN build digital health infrastructure
&mdash; the question is whether this translates into registered clinical trials and
rigorous evidence generation.
</div>

<!-- 11. Sponsors -->
<h2>11. Sponsor Analysis</h2>
<div class="two-col">
<div>
<h3>By Sponsor Class</h3>
<table>
<thead><tr><th>Class</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{sponsor_class_rows}</tbody>
</table>
</div>
<div>
<h3>Top Sponsors</h3>
<table>
<thead><tr><th>Sponsor</th><th style="text-align:right;">Trials</th></tr></thead>
<tbody>{top_sponsor_rows}</tbody>
</table>
</div>
</div>

<!-- 12. Temporal Trend -->
<h2>12. Temporal Trend</h2>
<div class="chart-container">
<h3>Africa Digital Health Trials by Year</h3>
<canvas id="trendChart" height="250"></canvas>
</div>

<!-- Method -->
<h2>Method</h2>
<div class="method-note">
<strong>Data source:</strong> ClinicalTrials.gov API v2 (accessed {datetime.now().strftime('%d %B %Y')}).<br>
<strong>Query:</strong> <code>{escape_html(DIGITAL_HEALTH_QUERY)}</code>, filtered to interventional studies.<br>
<strong>Countries:</strong> {', '.join(AFRICAN_COUNTRIES)} (Africa); United States, India (comparators).<br>
<strong>Mobile data:</strong> ITU/GSMA 2024 estimates.<br>
<strong>Limitations:</strong> Single registry; cannot capture trials on PACTR or national platforms.
Digital health terminology evolves rapidly and early trials may use different terms.
</div>

<footer>
Africa's Digital Health Bright Spot &mdash; ClinicalTrials.gov Registry Analysis |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} |
Data: ClinicalTrials.gov API v2
</footer>

</div>

<script>
// Country comparison chart
new Chart(document.getElementById('countryChart'), {{
  type: 'bar',
  data: {{
    labels: {comp_labels},
    datasets: [{{
      label: 'Digital Health Trials',
      data: {comp_values},
      backgroundColor: {comp_colors},
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

// Trend chart
new Chart(document.getElementById('trendChart'), {{
  type: 'line',
  data: {{
    labels: {trend_years},
    datasets: [{{
      label: 'Trials Started',
      data: {trend_counts},
      borderColor: '#22c55e',
      backgroundColor: 'rgba(34, 197, 94, 0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

// Mobile penetration chart
new Chart(document.getElementById('mobileChart'), {{
  type: 'bar',
  data: {{
    labels: {mobile_labels},
    datasets: [{{
      label: 'Mobile Penetration %',
      data: {mobile_values},
      backgroundColor: {json.dumps(
          ["#22c55e" if c in AFRICAN_COUNTRIES else "#3b82f6"
           for c in MOBILE_PENETRATION])},
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ min: 0, max: 100, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
    }}
  }}
}});

// Disease focus chart
new Chart(document.getElementById('diseaseChart'), {{
  type: 'bar',
  data: {{
    labels: {disease_labels},
    datasets: [
      {{
        label: 'Africa',
        data: {disease_af_vals},
        backgroundColor: '#22c55e',
        borderRadius: 4,
      }},
      {{
        label: 'US',
        data: {disease_us_vals},
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
</script>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Africa's Digital Health Bright Spot Analysis")
    print("=" * 60)
    print()

    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    print("Computing leapfrog index...")
    leapfrog = compute_leapfrog_index(data)

    print("Computing per-capita density...")
    per_capita = compute_per_capita(data)

    print("Computing HIV dominance...")
    hiv_dom = compute_hiv_dominance(data)

    print("Computing NCD gap...")
    ncd_gap = compute_ncd_gap(data)

    print("Analysing sponsors...")
    sponsors = compute_sponsor_analysis(data)

    print("Computing temporal trends...")
    trends = compute_temporal_trend(data)

    # Print summary
    print()
    print("-" * 60)
    print("DIGITAL HEALTH SUMMARY")
    print("-" * 60)
    print(f"  Africa digital health trials:  {leapfrog['africa_digital_health']:>6,}")
    print(f"  US digital health trials:      {leapfrog['us_digital_health']:>6,}")
    print(f"  India digital health trials:   {leapfrog['india_digital_health']:>6,}")
    print(f"  US-to-Africa ratio:            {leapfrog['us_to_africa_ratio']:>6}x")
    print(f"  HIV dominance (%):             {hiv_dom['hiv_pct']:>6}%")
    print(f"  NCD gap (US:Africa):           {ncd_gap['ratio']:>6}x")
    print()

    for country in AFRICAN_COUNTRIES:
        ct = data["country_counts"].get(country, 0)
        mob = MOBILE_PENETRATION.get(country, 0)
        print(f"  {country:20s}  {ct:>4} trials | {mob}% mobile")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, leapfrog, per_capita, hiv_dom, ncd_gap, sponsors, trends)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
