#!/usr/bin/env python
"""
fetch_trauma_gap.py — Query ClinicalTrials.gov API v2 for trauma/emergency
trials in Africa and compute the Condition Colonialism Index for trauma.

Africa has ~76 trauma/emergency trials vs 8,881 in the US.
Africa has the world's highest road traffic fatality rate (26.6/100K vs
9.3/100K in Europe) and massive conflict-related trauma. CCI = 29.4x.

Outputs:
  - data/trauma_gap_data.json  (cached API results, 24h TTL)
  - trauma-gap.html            (dark-theme interactive dashboard)
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

# Main query for all trauma/emergency trials
TRAUMA_QUERY = "emergency OR trauma OR injury OR accident"

# Countries to query for overall trauma trial counts
QUERY_COUNTRIES = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria",
    "Tanzania", "India", "Brazil", "United Kingdom",
]

# African countries for detailed breakdown
AFRICAN_COUNTRIES = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria", "Tanzania",
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
    "India": 1440,
    "Brazil": 217,
    "United Kingdom": 68,
    "United States": 335,
}

# Verified trial counts (for reference/validation)
VERIFIED_COUNTS = {
    "Africa_total": 76,
    "United States": 8881,
}

# Specific trauma subtypes to query in Africa (all 6 African countries)
TRAUMA_SUBTYPES = {
    "Road traffic / motor vehicle": "road traffic OR motor vehicle",
    "Burns":                        "burn OR burns",
    "Violence / assault":           "violence OR assault",
    "Snakebite / envenomation":     "snakebite OR envenomation",
    "Drowning":                     "drowning",
    "Falls":                        "falls",
}

# WHO burden / epidemiology data for Africa
# Africa's share of global trauma/injury burden (%)
AFRICA_TRAUMA_BURDEN_PCT = 25  # ~25% of global trauma burden
AFRICA_TRIAL_SHARE_PCT = 0.85  # 76 / (76+8881) ~ 0.85%
TRAUMA_CCI = 29.4  # 25 / 0.85

# Road Traffic Injury fatality rates per 100K population (WHO 2023)
RTI_RATES = {
    "Africa": 26.6,
    "South-East Asia": 18.1,
    "Eastern Mediterranean": 17.7,
    "Americas": 15.6,
    "Western Pacific": 14.1,
    "Europe": 9.3,
    "Global": 16.7,
}

# Snakebite statistics
SNAKEBITE_STATS = {
    "africa_deaths_per_year": 138000,
    "global_deaths_per_year": 540000,
    "africa_share_pct": 25.6,
    "who_ntd_priority": True,
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "trauma_gap_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "trauma-gap.html"
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
    """Fetch all trauma/emergency trial data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "overall_counts": {},
        "africa_total": 0,
        "subtype_africa_counts": {},
        "subtype_us_counts": {},
        "country_subtype_counts": {},
        "africa_trial_details": [],
    }

    all_query_locations = QUERY_COUNTRIES + [COMPARATOR]
    total_calls = (
        len(all_query_locations)                          # overall counts per country
        + len(TRAUMA_SUBTYPES)                            # subtype counts for Africa
        + len(TRAUMA_SUBTYPES)                            # subtype counts for US
        + len(AFRICAN_COUNTRIES) * len(TRAUMA_SUBTYPES)   # country x subtype
        + 1                                               # Africa aggregate
        + 1                                               # Africa trial details
    )
    call_num = 0

    # --- Overall trauma/emergency counts per country ---
    for country in all_query_locations:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Overall trauma: {country}...")
        count = get_trial_count(TRAUMA_QUERY, country)
        data["overall_counts"][country] = count
        time.sleep(RATE_LIMIT)

    # --- Africa aggregate ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa aggregate trauma count...")
    africa_total = get_trial_count_multi_location(TRAUMA_QUERY, AFRICAN_COUNTRIES)
    data["africa_total"] = africa_total
    time.sleep(RATE_LIMIT)

    # --- Subtype counts for Africa (all 6 countries combined) ---
    for subtype_label, subtype_query in TRAUMA_SUBTYPES.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa subtype: {subtype_label}...")
        count = get_trial_count_multi_location(subtype_query, AFRICAN_COUNTRIES)
        data["subtype_africa_counts"][subtype_label] = count
        time.sleep(RATE_LIMIT)

    # --- Subtype counts for US ---
    for subtype_label, subtype_query in TRAUMA_SUBTYPES.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] US subtype: {subtype_label}...")
        count = get_trial_count(subtype_query, COMPARATOR)
        data["subtype_us_counts"][subtype_label] = count
        time.sleep(RATE_LIMIT)

    # --- Per-country subtype breakdown ---
    for country in AFRICAN_COUNTRIES:
        data["country_subtype_counts"][country] = {}
        for subtype_label, subtype_query in TRAUMA_SUBTYPES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {subtype_label}...")
            count = get_trial_count(subtype_query, country)
            data["country_subtype_counts"][country][subtype_label] = count
            time.sleep(RATE_LIMIT)

    # --- Africa-wide trial details ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa trial details...")
    data["africa_trial_details"] = get_trial_details(
        TRAUMA_QUERY, AFRICAN_COUNTRIES
    )
    time.sleep(RATE_LIMIT)

    # --- Save cache ---
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
    """Compute overall trauma CCI.

    CCI = Africa burden % / Africa trial share %
    Africa trial share = Africa trials / (Africa + US trials) * 100
    """
    africa_n = data.get("africa_total", 0)
    us_n = data["overall_counts"].get(COMPARATOR, 0)
    total = africa_n + us_n
    trial_share = (africa_n / total * 100) if total > 0 else 0
    burden = AFRICA_TRAUMA_BURDEN_PCT
    cci = burden / trial_share if trial_share > 0 else 999.0
    return {
        "africa_trials": africa_n,
        "us_trials": us_n,
        "trial_share_pct": round(trial_share, 2),
        "burden_pct": burden,
        "cci": round(cci, 1),
    }


def compute_subtype_cci(data):
    """CCI-like ratio per trauma subtype (Africa vs US)."""
    results = {}
    for subtype in TRAUMA_SUBTYPES:
        africa_n = data["subtype_africa_counts"].get(subtype, 0)
        us_n = data["subtype_us_counts"].get(subtype, 0)
        total = africa_n + us_n
        trial_share = (africa_n / total * 100) if total > 0 else 0
        ratio = round(us_n / africa_n, 1) if africa_n > 0 else float("inf")
        results[subtype] = {
            "africa": africa_n,
            "us": us_n,
            "ratio": ratio if ratio != float("inf") else 999.0,
            "trial_share_pct": round(trial_share, 2),
        }
    return results


def compute_per_capita(data):
    """Trials per million population per country."""
    results = {}
    for country in QUERY_COUNTRIES + [COMPARATOR]:
        total = data["overall_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        results[country] = {
            "total_trials": total,
            "population_m": pop,
            "trials_per_million": round(total / pop, 2),
        }
    return results


def compute_phase_distribution(data):
    """Phase distribution across Africa trauma trials."""
    phase_counts = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        phase_counts[t.get("phase", "Not specified")] += 1
    return dict(phase_counts)


def compute_sponsor_analysis(data):
    """Sponsor class distribution for Africa trauma trials."""
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
    """Trials by start year from detail data."""
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
    """Heatmap color: 0=black, 1-3=red, 4-10=yellow, 10+=green."""
    if count == 0:
        return "#111"
    elif count <= 3:
        intensity = 80 + int((count / 3) * 175)
        return f"rgb({intensity}, {max(30, intensity // 4)}, {max(30, intensity // 4)})"
    elif count <= 10:
        ratio = (count - 3) / 7
        r = int(255 - ratio * 100)
        g = int(120 + ratio * 135)
        return f"rgb({r}, {g}, 40)"
    else:
        return "rgb(50, 200, 80)"


def generate_html(data, cci, subtype_cci, per_capita, phases, sponsors, trends):
    """Generate the full HTML dashboard."""

    africa_total = data.get("africa_total", 0)
    us_total = data["overall_counts"].get(COMPARATOR, 0)

    # Subtype heatmap rows
    heatmap_rows = ""
    for subtype in TRAUMA_SUBTYPES:
        cells = ""
        for country in AFRICAN_COUNTRIES:
            count = data["country_subtype_counts"].get(country, {}).get(subtype, 0)
            bg = cell_color(count)
            text_color = "#fff" if count <= 3 else "#000"
            cells += (
                f'<td style="background:{bg};color:{text_color};'
                f'text-align:center;padding:8px;font-weight:bold;">{count}</td>'
            )
        sub_info = subtype_cci.get(subtype, {})
        ratio_val = sub_info.get("ratio", 0)
        ratio_color = "#ff4444" if ratio_val > 50 else "#ffaa33" if ratio_val > 10 else "#44cc66"
        cells += (
            f'<td style="background:{ratio_color};color:#000;text-align:center;'
            f'padding:8px;font-weight:bold;">{ratio_val}x</td>'
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

    # Subtype comparison table
    subtype_rows = ""
    for subtype in TRAUMA_SUBTYPES:
        info = subtype_cci.get(subtype, {})
        africa_n = info.get("africa", 0)
        us_n = info.get("us", 0)
        ratio = info.get("ratio", 0)
        subtype_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(subtype)}</td>'
            f'<td style="padding:8px;text-align:right;">{africa_n:,}</td>'
            f'<td style="padding:8px;text-align:right;">{us_n:,}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;'
            f'color:{"#ff4444" if ratio > 50 else "#ffaa33"};">{ratio}x</td>'
            f'</tr>\n'
        )

    # RTI fatality rate rows
    rti_rows = ""
    for region, rate in sorted(RTI_RATES.items(), key=lambda x: -x[1]):
        is_africa = region == "Africa"
        color = "#ff4444" if is_africa else "#e2e8f0"
        weight = "bold" if is_africa else "normal"
        rti_rows += (
            f'<tr><td style="padding:8px;color:{color};font-weight:{weight};">'
            f'{escape_html(region)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:{weight};">'
            f'{rate}</td></tr>\n'
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

    # Country-level overall counts for Africa
    country_overall_rows = ""
    for country in AFRICAN_COUNTRIES:
        total_sub = sum(
            data["country_subtype_counts"].get(country, {}).values()
        )
        overall = data["overall_counts"].get(country, 0)
        pop = POPULATIONS.get(country, 1)
        tpm = round(overall / pop, 2)
        country_overall_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{overall:,}</td>'
            f'<td style="padding:8px;text-align:right;">{pop}M</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">{tpm}</td>'
            f'</tr>\n'
        )

    # Chart data
    subtype_labels = json.dumps(list(TRAUMA_SUBTYPES.keys()))
    subtype_africa_vals = json.dumps([
        subtype_cci.get(s, {}).get("africa", 0) for s in TRAUMA_SUBTYPES
    ])
    subtype_us_vals = json.dumps([
        subtype_cci.get(s, {}).get("us", 0) for s in TRAUMA_SUBTYPES
    ])

    rti_labels = json.dumps(list(RTI_RATES.keys()))
    rti_values = json.dumps(list(RTI_RATES.values()))
    rti_colors = json.dumps([
        "#ef4444" if r == "Africa" else "#64748b" for r in RTI_RATES
    ])

    trend_years = json.dumps(list(trends.keys()))
    trend_counts = json.dumps(list(trends.values()))

    # Comparison countries bar data
    comp_countries = ["South Africa", "Egypt", "Kenya", "Nigeria", "India", "Brazil", "United Kingdom", "United States"]
    comp_labels = json.dumps(comp_countries)
    comp_values = json.dumps([data["overall_counts"].get(c, 0) for c in comp_countries])
    comp_colors = json.dumps([
        "#ef4444" if c in AFRICAN_COUNTRIES else "#3b82f6" for c in comp_countries
    ])

    snakebite_africa = data["subtype_africa_counts"].get("Snakebite / envenomation", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa Trauma &amp; Emergency Care Trial Gap</title>
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
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.scroll-x {{ overflow-x: auto; }}
.stat-highlight {{
  display: inline-block;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 6px;
  padding: 0.2rem 0.6rem;
  font-weight: bold;
  color: var(--danger);
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

<h1>Africa's Trauma &amp; Emergency Care Trial Gap</h1>
<p class="subtitle">The world's highest injury burden, the fewest trials &mdash;
Condition Colonialism Index for trauma across Africa</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Trauma Trials</div>
    <div class="value danger">{africa_total:,}</div>
    <div class="label">vs {us_total:,} in the United States</div>
  </div>
  <div class="summary-card">
    <div class="label">Condition Colonialism Index</div>
    <div class="value danger">{cci['cci']}</div>
    <div class="label">25% burden / {cci['trial_share_pct']}% trial share</div>
  </div>
  <div class="summary-card">
    <div class="label">Road Traffic Fatality Rate</div>
    <div class="value danger">26.6</div>
    <div class="label">per 100K (Africa) vs 9.3 (Europe)</div>
  </div>
  <div class="summary-card">
    <div class="label">US-to-Africa Trial Ratio</div>
    <div class="value warning">{round(us_total / africa_total, 0) if africa_total > 0 else 'Inf'}x</div>
    <div class="label">{us_total:,} vs {africa_total:,} trauma trials</div>
  </div>
</div>

<div class="method-note">
<strong>Condition Colonialism Index (CCI)</strong> = Africa's share of global trauma/injury
burden (%) divided by Africa's share of registered trials (%). Africa carries ~25% of the
global trauma burden but hosts only ~{cci['trial_share_pct']}% of trauma/emergency trials
relative to the US, yielding a CCI of <span class="stat-highlight">{cci['cci']}x</span>.
A CCI of 1.0 would mean proportional investment. Values above 5.0 represent severe
structural under-investment.
</div>

<!-- 2. CCI Computation -->
<h2>2. CCI Computation</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Trauma Burden</div>
    <div class="value warning">{AFRICA_TRAUMA_BURDEN_PCT}%</div>
    <div class="label">of global injury deaths + DALYs</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Trial Share</div>
    <div class="value danger">{cci['trial_share_pct']}%</div>
    <div class="label">{africa_total} / ({africa_total} + {us_total}) trials</div>
  </div>
  <div class="summary-card">
    <div class="label">CCI = Burden / Share</div>
    <div class="value danger">{cci['cci']}</div>
    <div class="label">{AFRICA_TRAUMA_BURDEN_PCT}% / {cci['trial_share_pct']}%</div>
  </div>
</div>

<div class="danger-note">
<strong>Interpretation:</strong> For every unit of trauma research Africa should receive
based on its burden, it gets only 1/{cci['cci']}th. This is among the highest CCI values
in the Africa RCT Gap series, reflecting that trauma &mdash; despite being a leading cause
of death in young Africans &mdash; remains profoundly under-researched on the continent.
</div>

<!-- 3. Road Traffic Spotlight -->
<h2>3. Road Traffic Injury Spotlight</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Africa has the world's highest road traffic fatality rate: <span class="stat-highlight">26.6
deaths per 100,000 population</span> (WHO Global Status Report on Road Safety 2023),
nearly triple Europe's rate of 9.3/100K. Yet road traffic injury trials in Africa are
vanishingly rare.
</p>

<div class="two-col">
<div>
<h3>RTI Fatality Rate by WHO Region (per 100K)</h3>
<table>
<thead><tr><th>Region</th><th style="text-align:right;">Rate / 100K</th></tr></thead>
<tbody>{rti_rows}</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="rtiChart" height="280"></canvas>
</div>
</div>

<div class="warning-note">
<strong>The paradox:</strong> Africa has the highest road traffic death rate of any WHO
region (26.6/100K), accounting for the fastest-growing cause of death in young adults
aged 5&ndash;29 across the continent. Yet the number of interventional trials addressing
road traffic injuries in Africa remains in the single digits or low double digits. This
disparity reflects not only under-funding but also the absence of pre-hospital care
systems, trauma registries, and research infrastructure needed to conduct such trials.
</div>

<!-- 4. Snakebite -->
<h2>4. Snakebite: The Forgotten Emergency</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Snakebite envenomation kills an estimated 138,000 people in Africa annually
(WHO Neglected Tropical Diseases). Africa accounts for ~25.6% of global snakebite deaths.
The WHO added snakebite to its NTD priority list in 2017.
</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Annual African Snakebite Deaths</div>
    <div class="value danger">138,000</div>
    <div class="label">~25.6% of global total</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Snakebite Trials</div>
    <div class="value danger">{snakebite_africa}</div>
    <div class="label">On ClinicalTrials.gov</div>
  </div>
  <div class="summary-card">
    <div class="label">US Snakebite Trials</div>
    <div class="value">{subtype_cci.get('Snakebite / envenomation', dict()).get('us', 0)}</div>
    <div class="label">For comparison</div>
  </div>
</div>

<div class="danger-note">
<strong>138,000 deaths per year, {snakebite_africa} trials.</strong> Africa's snakebite
crisis represents one of the starkest examples of the global research equity gap. The
continent lacks locally manufactured antivenoms, and most existing antivenoms are
imported at costs exceeding monthly wages. The absence of clinical trials means treatment
protocols rely on evidence generated elsewhere, often for different snake species entirely.
</div>

<!-- 5. Burns Gap -->
<h2>5. Burns: The Hidden Epidemic</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Burns are the leading cause of injury-related disability in many African settings, driven
by open cooking fires, kerosene stoves, and limited access to running water. Children under
5 are disproportionately affected.
</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Burns Trials</div>
    <div class="value danger">{subtype_cci.get('Burns', dict()).get('africa', 0)}</div>
    <div class="label">Across 6 queried countries</div>
  </div>
  <div class="summary-card">
    <div class="label">US Burns Trials</div>
    <div class="value">{subtype_cci.get('Burns', dict()).get('us', 0)}</div>
    <div class="label">For comparison</div>
  </div>
  <div class="summary-card">
    <div class="label">US/Africa Ratio</div>
    <div class="value warning">{subtype_cci.get('Burns', dict()).get('ratio', 0)}x</div>
    <div class="label">Gap magnitude</div>
  </div>
</div>

<!-- 6. Violence & Conflict Trauma -->
<h2>6. Violence &amp; Conflict Trauma</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Africa hosts 35% of the world's active armed conflicts (Uppsala Conflict Data Program).
Interpersonal violence rates in southern and eastern Africa are among the highest globally.
Yet trials addressing violence and assault injuries are almost non-existent.
</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Violence/Assault Trials</div>
    <div class="value danger">{subtype_cci.get('Violence / assault', dict()).get('africa', 0)}</div>
    <div class="label">Across 6 queried countries</div>
  </div>
  <div class="summary-card">
    <div class="label">US Violence/Assault Trials</div>
    <div class="value">{subtype_cci.get('Violence / assault', dict()).get('us', 0)}</div>
    <div class="label">For comparison</div>
  </div>
</div>

<div class="warning-note">
<strong>The Lancet Commission on Global Surgery (2015)</strong> found that 5 billion people
lack access to safe, affordable surgical and anaesthesia care. In sub-Saharan Africa,
an estimated 93% of the population lacks access to essential surgical services. Trauma
is the condition most dependent on timely surgical intervention, making this gap
life-threatening in the most literal sense.
</div>

<!-- 7. Country Breakdown -->
<h2>7. African Country Breakdown</h2>
<h3>Overall Trauma Trial Counts</h3>
<table>
<thead>
<tr><th>Country</th><th style="text-align:right;">Trauma Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials / Million</th></tr>
</thead>
<tbody>
{country_overall_rows}
</tbody>
</table>

<h3>Subtype Heatmap by Country</h3>
<p style="color:var(--muted);margin-bottom:0.5rem;">
Rows = trauma subtypes. Columns = African countries.
<span style="color:#ff4444;">0-3 trials (red)</span>,
<span style="color:#cccc44;">4-10 (yellow)</span>,
<span style="color:#44cc66;">10+ (green)</span>,
<span style="background:#111;padding:2px 6px;">0 = black</span>.
</p>
<div class="scroll-x">
<table>
<thead>
<tr>
<th>Subtype</th>
{country_headers}
<th style="padding:8px;writing-mode:vertical-rl;">US/Africa Ratio</th>
</tr>
</thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>

<!-- 8. International Comparison -->
<h2>8. International Comparison</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Trauma trial counts across comparator countries: US, India, Brazil, UK, and 6 African nations.
</p>
<div class="chart-container">
<canvas id="compChart" height="300"></canvas>
</div>

<h3>Per-Capita Trauma Trial Density</h3>
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

<!-- 9. Subtype Deep-Dive -->
<h2>9. Trauma Subtype: Africa vs United States</h2>
<div class="scroll-x">
<table>
<thead>
<tr><th>Subtype</th><th style="text-align:right;">Africa</th>
<th style="text-align:right;">US</th>
<th style="text-align:right;">US/Africa Ratio</th></tr>
</thead>
<tbody>
{subtype_rows}
</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="subtypeChart" height="300"></canvas>
</div>

<!-- 10. Sponsor Analysis -->
<h2>10. Sponsor Analysis</h2>
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
<thead><tr><th>Sponsor</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{top_sponsor_rows}</tbody>
</table>
</div>
</div>

<h3>Phase Distribution</h3>
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

<!-- 11. The Emergency Care Desert -->
<h2>11. The Emergency Care Desert</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
The trial gap is a symptom of a deeper structural absence. Africa's trauma care system
is not just under-researched &mdash; it is, for most of the continent, non-existent.
</p>

<div class="danger-note">
<strong>The Lancet Commission on Global Surgery (2015)</strong> estimated that 143 million
additional surgical procedures are needed annually in low- and middle-income countries.
Sub-Saharan Africa has the greatest unmet need, with fewer than 200 operations per
100,000 population compared to over 11,000 per 100,000 in the US.
</div>

<div class="warning-note" style="margin-top:1rem;">
<strong>Key findings from the emergency care evidence base:</strong>
<ul style="margin-top:0.5rem;padding-left:1.5rem;">
<li>Most African countries have no formal pre-hospital emergency system (no 911/999 equivalent)</li>
<li>Average time from injury to hospital exceeds 60 minutes in rural sub-Saharan Africa</li>
<li>Fewer than 1 in 10 trauma patients in Africa reach a facility with surgical capacity within the golden hour</li>
<li>Africa has 0.5 surgeons per 100,000 population vs 67 per 100,000 in the US (Lancet Commission)</li>
<li>Blood supply covers &lt;50% of national needs in most African countries (WHO Global Blood Safety)</li>
<li>Trauma registries &mdash; essential for research &mdash; exist in fewer than 10 African countries</li>
</ul>
</div>

<div class="method-note" style="margin-top:1rem;">
<strong>What this means for trials:</strong> The absence of emergency care infrastructure
creates a vicious cycle. Without pre-hospital systems, patients die before reaching
facilities. Without facilities, there are no sites for trials. Without trials, there is
no evidence to advocate for investment. Without investment, the infrastructure gap persists.
The CCI of {cci['cci']}x captures the magnitude of this cycle in a single number.
</div>

<!-- 12. Temporal Trend -->
<h2>12. Temporal Trend</h2>
<div class="chart-container">
<canvas id="trendChart" height="250"></canvas>
</div>

<footer>
<p>Data source: ClinicalTrials.gov API v2 | WHO Global Status Report on Road Safety 2023 |
WHO Global Health Estimates | Lancet Commission on Global Surgery 2015 |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>Condition Colonialism Index (CCI) methodology: Africa burden % / Africa trial share %</p>
<p>Query: "{escape_html(TRAUMA_QUERY)}" | Interventional studies only</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {{

  // RTI rate bar chart
  var rtiCtx = document.getElementById('rtiChart');
  if (rtiCtx) {{
    new Chart(rtiCtx, {{
      type: 'bar',
      data: {{
        labels: {rti_labels},
        datasets: [{{
          label: 'RTI Deaths per 100K',
          data: {rti_values},
          backgroundColor: {rti_colors},
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{ display: true, text: 'Road Traffic Fatality Rate by WHO Region',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ color: '#1e293b' }},
            ticks: {{ color: '#94a3b8' }},
            title: {{ display: true, text: 'Deaths per 100K', color: '#94a3b8' }}
          }},
          y: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0' }}
          }}
        }}
      }}
    }});
  }}

  // International comparison bar chart
  var compCtx = document.getElementById('compChart');
  if (compCtx) {{
    new Chart(compCtx, {{
      type: 'bar',
      data: {{
        labels: {comp_labels},
        datasets: [{{
          label: 'Trauma/Emergency Trials',
          data: {comp_values},
          backgroundColor: {comp_colors},
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{ display: true, text: 'Trauma/Emergency Trial Counts by Country',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0' }}
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

  // Subtype grouped bar chart
  var subtypeCtx = document.getElementById('subtypeChart');
  if (subtypeCtx) {{
    new Chart(subtypeCtx, {{
      type: 'bar',
      data: {{
        labels: {subtype_labels},
        datasets: [
          {{
            label: 'Africa',
            data: {subtype_africa_vals},
            backgroundColor: '#ef4444',
            borderWidth: 0,
            borderRadius: 4,
          }},
          {{
            label: 'United States',
            data: {subtype_us_vals},
            backgroundColor: '#3b82f6',
            borderWidth: 0,
            borderRadius: 4,
          }}
        ]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ labels: {{ color: '#e2e8f0' }} }},
          title: {{ display: true, text: 'Trauma Subtype: Africa vs US',
                    color: '#e2e8f0', font: {{ size: 14 }} }}
        }},
        scales: {{
          x: {{
            grid: {{ display: false }},
            ticks: {{ color: '#e2e8f0', maxRotation: 45 }}
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
          label: 'Trauma Trials Started (Africa)',
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
          title: {{ display: true, text: 'Trauma Trial Starts by Year (Africa)',
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
    print("Africa Trauma & Emergency Care Trial Gap Analysis")
    print("=" * 60)
    print()

    # Fetch data
    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    # Compute analyses
    print("Computing Condition Colonialism Index...")
    cci = compute_cci(data)

    print("Computing subtype CCI...")
    subtype_cci = compute_subtype_cci(data)

    print("Computing per-capita density...")
    per_capita = compute_per_capita(data)

    print("Analysing phase distribution...")
    phases = compute_phase_distribution(data)

    print("Analysing sponsors...")
    sponsors = compute_sponsor_analysis(data)

    print("Computing temporal trends...")
    trends = compute_temporal_trend(data)

    # Print summary
    print()
    print("-" * 60)
    print("TRAUMA CCI SUMMARY")
    print("-" * 60)
    print(f"  Africa trauma trials:  {cci['africa_trials']:>6,}")
    print(f"  US trauma trials:      {cci['us_trials']:>6,}")
    print(f"  Africa trial share:    {cci['trial_share_pct']:>6}%")
    print(f"  Africa burden share:   {cci['burden_pct']:>6}%")
    print(f"  CCI:                   {cci['cci']:>6}")
    print()
    print("-" * 60)
    print("SUBTYPE COMPARISON (Africa vs US)")
    print("-" * 60)
    for subtype, info in subtype_cci.items():
        print(
            f"  {subtype:30s}  Africa: {info['africa']:>5,} | "
            f"US: {info['us']:>6,} | Ratio: {info['ratio']}x"
        )

    print()
    print("-" * 60)
    print("PER-CAPITA DENSITY")
    print("-" * 60)
    for country, info in sorted(per_capita.items(), key=lambda x: -x[1]["trials_per_million"]):
        print(
            f"  {country:20s}  {info['total_trials']:>6,} trials | "
            f"{info['population_m']}M pop | {info['trials_per_million']} / million"
        )

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, cci, subtype_cci, per_capita, phases, sponsors, trends)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
