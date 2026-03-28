#!/usr/bin/env python
"""
fetch_palliative_desert.py — Query ClinicalTrials.gov API v2 for palliative
care, hospice, and end-of-life trials and quantify Africa's "Palliative Care
Desert": millions die in pain from cancer, HIV, and other conditions without
evidence-based symptom management. Only ~57 palliative/hospice/end-of-life
trials in Africa vs 1,286 in the US. 83% of people needing palliative care in
low-income countries receive none (Lancet Commission on Palliative Care, 2018).

Outputs:
  - data/palliative_desert_data.json  (cached API results, 24h TTL)
  - palliative-desert.html            (dark-theme interactive dashboard)
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

# Primary query for palliative care trials
PALLIATIVE_QUERY = "palliative OR hospice OR end of life OR pain management OR symptom management"

# Opioid access query (restricted in most African countries)
OPIOID_QUERY = "opioid OR morphine"

# Countries for the main comparison
COUNTRY_LIST = [
    "South Africa", "Kenya", "Uganda", "Nigeria",
]
GLOBAL_COMPARATORS = [
    "United States", "India", "Brazil", "United Kingdom",
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

# Condition-specific sub-queries for Africa palliative trials
CONDITION_SUBTYPES = {
    "HIV palliative": "HIV AND (palliative OR hospice OR end of life OR symptom management)",
    "Cancer palliative": "cancer AND (palliative OR hospice OR end of life OR symptom management)",
    "Pediatric palliative": "pediatric AND (palliative OR hospice OR end of life OR pain management)",
}

# Population in millions (2025 estimates)
POPULATIONS = {
    "South Africa":   62,
    "Kenya":          56,
    "Uganda":         48,
    "Nigeria":        230,
    "United States":  335,
    "India":          1440,
    "Brazil":         217,
    "United Kingdom": 68,
    "Africa":         1500,  # continent total
}

# Lancet Commission on Palliative Care (2018):
# Africa ~30% of global palliative care need (based on serious health-related suffering)
# Africa trial share ~4.2% => CCI ~7.1x
AFRICA_PALLIATIVE_BURDEN_PCT = 30

CACHE_FILE = Path(__file__).resolve().parent / "data" / "palliative_desert_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "palliative-desert.html"
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
    """Return total count of interventional trials for a query+location."""
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
            "LeadSponsorName,LeadSponsorClass,StartDate,EnrollmentCount,"
            "Condition,InterventionName"
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
        cond_mod = proto.get("conditionsModule", {})
        interv_mod = proto.get("armsInterventionsModule", {})

        phases_list = design.get("phases", [])
        phase_str = ", ".join(phases_list) if phases_list else "Not specified"
        lead_sponsor = sponsor_mod.get("leadSponsor", {})
        start_info = status_mod.get("startDateStruct", {})
        start_date = start_info.get("date", "")

        conditions = cond_mod.get("conditions", [])
        interventions = interv_mod.get("interventions", [])
        interv_names = [i.get("name", "") for i in interventions] if interventions else []

        results.append({
            "nctId": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "phase": phase_str,
            "status": status_mod.get("overallStatus", ""),
            "sponsorName": lead_sponsor.get("name", ""),
            "sponsorClass": lead_sponsor.get("class", ""),
            "startDate": start_date,
            "enrollment": enroll_mod.get("count", 0),
            "conditions": conditions,
            "interventions": interv_names,
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
    """Fetch all palliative care trial data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "africa_total": 0,
        "country_counts": {},
        "condition_subtype_counts": {},
        "opioid_africa_count": 0,
        "africa_trial_details": [],
        "condition_subtype_details": {},
    }

    # --- Africa-wide palliative trial count ---
    print("  [1] Africa-wide palliative/hospice/EOL trials...")
    africa_count = get_trial_count_multi_location(PALLIATIVE_QUERY, AFRICA_LOCATIONS)
    data["africa_total"] = africa_count
    print(f"      Africa total: {africa_count}")
    time.sleep(RATE_LIMIT)

    # --- Per-country counts (African + comparators) ---
    all_countries = COUNTRY_LIST + GLOBAL_COMPARATORS
    total_calls = len(all_countries) + len(CONDITION_SUBTYPES) + 3  # +1 opioid, +2 details
    call_num = 1

    for country in all_countries:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / palliative...")
        count = get_trial_count(PALLIATIVE_QUERY, country)
        data["country_counts"][country] = count
        time.sleep(RATE_LIMIT)

    # --- Condition subtype counts for Africa ---
    for sub_label, sub_query in CONDITION_SUBTYPES.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa / {sub_label}...")
        count = get_trial_count_multi_location(sub_query, AFRICA_LOCATIONS)
        data["condition_subtype_counts"][sub_label] = count
        time.sleep(RATE_LIMIT)

    # --- Opioid/morphine access trials in Africa ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Africa / opioid+morphine access...")
    opioid_count = get_trial_count_multi_location(OPIOID_QUERY, AFRICA_LOCATIONS)
    data["opioid_africa_count"] = opioid_count
    print(f"      Opioid/morphine Africa trials: {opioid_count}")
    time.sleep(RATE_LIMIT)

    # --- Trial-level details for Africa palliative ---
    print("  Fetching Africa palliative trial details...")
    details = get_trial_details(PALLIATIVE_QUERY, AFRICA_LOCATIONS)
    data["africa_trial_details"] = details
    time.sleep(RATE_LIMIT)

    # --- Condition subtype details for Africa ---
    for sub_label, sub_query in CONDITION_SUBTYPES.items():
        print(f"  Fetching Africa {sub_label} details...")
        sub_details = get_trial_details(sub_query, AFRICA_LOCATIONS, page_size=50)
        data["condition_subtype_details"][sub_label] = sub_details
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
    """Compute Palliative Care Desert CCI.

    CCI = (Africa burden %) / (Africa trial share %)
    Africa trial share = Africa trials / (Africa + US) * 100
    Lancet Commission on Palliative Care (2018): Africa ~30% of global
    serious health-related suffering requiring palliative care.
    """
    africa_trials = data["africa_total"]
    us_trials = data["country_counts"].get("United States", 1286)
    total = africa_trials + us_trials

    if total == 0:
        trial_share = 0
    else:
        trial_share = (africa_trials / total) * 100

    if trial_share > 0:
        cci = AFRICA_PALLIATIVE_BURDEN_PCT / trial_share
    else:
        cci = float("inf")

    return {
        "africa_trials": africa_trials,
        "us_trials": us_trials,
        "trial_share_pct": round(trial_share, 2),
        "burden_pct": AFRICA_PALLIATIVE_BURDEN_PCT,
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
    """Phase distribution across Africa palliative trials."""
    phase_counts = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        phase_counts[t.get("phase", "Not specified")] += 1
    return dict(phase_counts)


def compute_sponsor_analysis(data):
    """Sponsor class distribution for Africa palliative trials."""
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


def compute_intervention_analysis(data):
    """Categorize interventions in Africa palliative trials."""
    interv_cats = defaultdict(int)
    for t in data.get("africa_trial_details", []):
        title_lower = t.get("title", "").lower()
        interv_lower = " ".join(t.get("interventions", [])).lower()
        combined = title_lower + " " + interv_lower

        if any(k in combined for k in ["morphine", "opioid", "tramadol", "codeine", "fentanyl"]):
            interv_cats["Opioid/analgesic"] += 1
        elif any(k in combined for k in ["psychosocial", "counseling", "counselling", "support group"]):
            interv_cats["Psychosocial support"] += 1
        elif any(k in combined for k in ["palliative care", "hospice", "integrated care"]):
            interv_cats["Palliative care model"] += 1
        elif any(k in combined for k in ["nutrition", "feeding", "diet"]):
            interv_cats["Nutrition support"] += 1
        elif any(k in combined for k in ["exercise", "physiotherapy", "rehabilitation"]):
            interv_cats["Rehabilitation/exercise"] += 1
        else:
            interv_cats["Other"] += 1
    return dict(sorted(interv_cats.items(), key=lambda x: -x[1]))


def compute_uganda_spotlight(data):
    """Uganda spotlight: one of few African countries with palliative care policy."""
    uganda_count = data["country_counts"].get("Uganda", 0)
    uganda_details = []
    for t in data.get("africa_trial_details", []):
        title_lower = t.get("title", "").lower()
        conds = [c.lower() for c in t.get("conditions", [])]
        # Uganda is identified by sponsor or title references
        if "uganda" in t.get("sponsorName", "").lower() or "uganda" in title_lower:
            uganda_details.append(t)
    return {
        "count": uganda_count,
        "details": uganda_details,
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
                  interventions, uganda):
    """Generate the full HTML dashboard."""

    africa_trials = data["africa_total"]
    us_trials = data["country_counts"].get("United States", 0)
    uk_trials = data["country_counts"].get("United Kingdom", 0)
    ratio_us_africa = round(us_trials / africa_trials, 1) if africa_trials > 0 else "Inf"
    opioid_count = data.get("opioid_africa_count", 0)

    # HIV vs cancer vs pediatric
    hiv_count = data["condition_subtype_counts"].get("HIV palliative", 0)
    cancer_count = data["condition_subtype_counts"].get("Cancer palliative", 0)
    pediatric_count = data["condition_subtype_counts"].get("Pediatric palliative", 0)

    # --- Country comparison table ---
    comparison_countries = ["South Africa", "Kenya", "Uganda", "Nigeria"]
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

    # --- Global comparison table ---
    global_comp_rows = ""
    global_countries = [
        ("Africa (total)", data["africa_total"], POPULATIONS["Africa"]),
        ("United States", data["country_counts"].get("United States", 0), POPULATIONS["United States"]),
        ("India", data["country_counts"].get("India", 0), POPULATIONS["India"]),
        ("Brazil", data["country_counts"].get("Brazil", 0), POPULATIONS["Brazil"]),
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

    # --- Condition subtype bar chart data ---
    sub_labels = json.dumps(list(data["condition_subtype_counts"].keys()))
    sub_values = json.dumps(list(data["condition_subtype_counts"].values()))
    sub_colors = json.dumps([
        "#ef4444" if v <= 5
        else "#f59e0b" if v <= 15
        else "#3b82f6"
        for v in data["condition_subtype_counts"].values()
    ])

    # --- Condition subtype table ---
    sub_rows = ""
    for sub_label, count in sorted(data["condition_subtype_counts"].items(), key=lambda x: -x[1]):
        color = "#ff4444" if count <= 5 else "#ffaa33" if count <= 15 else "#e2e8f0"
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

    # --- Intervention analysis rows ---
    interv_rows = ""
    for cat, count in interventions.items():
        interv_rows += (
            f'<tr><td style="padding:8px;">{escape_html(cat)}</td>'
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
<title>The Palliative Care Desert: Dying in Pain Without Evidence</title>
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
  background: linear-gradient(135deg, #ef4444, #a855f7);
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
.purple {{ color: #a855f7; }}
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
.purple-note {{
  background: rgba(168, 85, 247, 0.1);
  border-left: 4px solid #a855f7;
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.success-note {{
  background: rgba(34, 197, 94, 0.1);
  border-left: 4px solid var(--success);
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

<h1>The Palliative Care Desert</h1>
<p class="subtitle">Millions of Africans die in pain from cancer, HIV, and other conditions
without evidence-based symptom management. Only {africa_trials} palliative/hospice/end-of-life
trials in Africa vs {us_trials:,} in the United States. The Lancet Commission on Palliative Care
(2018) estimated that 83% of people needing palliative care in low-income countries receive none.</p>

<!-- 1. Summary -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Palliative Trials</div>
    <div class="value danger">{africa_trials}</div>
    <div class="label">Interventional, ClinicalTrials.gov</div>
  </div>
  <div class="summary-card">
    <div class="label">US Palliative Trials</div>
    <div class="value" style="color:#3b82f6;">{us_trials:,}</div>
    <div class="label">{ratio_us_africa}x more than Africa</div>
  </div>
  <div class="summary-card">
    <div class="label">Condition Colonialism Index</div>
    <div class="value danger">{cci['cci']}</div>
    <div class="label">1.0 = fair; &gt;5 = severe gap</div>
  </div>
  <div class="summary-card">
    <div class="label">Opioid/Morphine Trials (Africa)</div>
    <div class="value purple">{opioid_count}</div>
    <div class="label">Access crisis: most countries restrict opioids</div>
  </div>
</div>

<!-- 2. Dying in Pain -->
<h2>2. Dying in Pain</h2>
<div class="danger-note">
<strong>The Lancet Commission on Palliative Care (Knaul et al., 2018)</strong> reported that
an estimated <strong>61 million people</strong> experience serious health-related suffering
globally each year, with the vast majority in low- and middle-income countries.
<strong>83%</strong> of those needing palliative care in low-income countries receive none.<br><br>
In sub-Saharan Africa, fewer than <strong>5%</strong> of people who need palliative care
receive it. Cancer patients routinely die in agony because morphine is unavailable.
HIV patients endure chronic pain without evidence-based symptom management.
Children with life-limiting conditions have almost no access to pediatric palliative care.<br><br>
<strong>The research response?</strong> Just {africa_trials} interventional trials
across a continent of 1.5 billion people &mdash; while the US has {us_trials:,}.
</div>

<div class="big-number danger">{cci['cci']}x</div>
<p style="text-align:center;color:var(--muted);margin-bottom:2rem;">
Palliative Care CCI: Africa's burden-to-research ratio</p>

<!-- 3. CCI Calculation -->
<h2>3. Condition Colonialism Index: The Calculation</h2>
<div class="method-note">
<strong>CCI = Burden Share / Trial Share</strong><br><br>
The Lancet Commission on Palliative Care (Knaul et al., 2018) estimated that
Africa accounts for approximately <strong>{AFRICA_PALLIATIVE_BURDEN_PCT}%</strong> of
global serious health-related suffering requiring palliative care, driven by
the HIV/AIDS epidemic, rising cancer burden, and lack of health system capacity.<br><br>
<strong>Africa's trial share</strong> = {africa_trials} / ({africa_trials} + {us_trials:,})
= <strong>{cci['trial_share_pct']}%</strong><br>
<strong>CCI</strong> = {AFRICA_PALLIATIVE_BURDEN_PCT}% / {cci['trial_share_pct']}%
= <strong style="color:#ef4444;">{cci['cci']}</strong><br><br>
A CCI of {cci['cci']} means Africa carries {cci['cci']}x more palliative care need
than its share of palliative research warrants.
</div>

<!-- 4. Country Breakdown -->
<h2>4. Country Breakdown: Palliative Trials in Africa</h2>
<div class="two-col">
<div>
<table>
<thead>
<tr><th>Country</th><th style="text-align:right;">Palliative Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials/Million</th></tr>
</thead>
<tbody>
{comp_rows}
</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="countryBarChart" height="250"></canvas>
</div>
</div>

<!-- 5. HIV vs Cancer vs Pediatric Palliative Trials -->
<h2>5. HIV vs Cancer vs Pediatric Palliative Trials</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
How does Africa's palliative trial portfolio break down by the conditions
driving the greatest suffering?
</p>
<div class="two-col">
<div>
<table>
<thead><tr><th>Condition Subtype</th><th style="text-align:right;">Africa Trials</th></tr></thead>
<tbody>{sub_rows}</tbody>
</table>
</div>
<div class="chart-container">
<canvas id="subBarChart" height="250"></canvas>
</div>
</div>
<div class="warning-note">
<strong>Context:</strong> Sub-Saharan Africa carries 67% of the global HIV burden and
has the fastest-rising cancer incidence of any world region. Yet palliative care
research for both conditions combined barely registers in the trial registry.
Pediatric palliative care &mdash; for children with HIV, cancer, sickle cell disease,
and other life-limiting conditions &mdash; has only {pediatric_count} registered
interventional trials across the entire continent.
</div>

<!-- 6. The Opioid Access Crisis -->
<h2>6. The Opioid Access Crisis</h2>
<div class="purple-note">
<strong>Pain relief is a human right, yet most African countries severely restrict
opioid access.</strong><br><br>
The International Narcotics Control Board reports that Africa accounts for less than
<strong>1%</strong> of global medical opioid consumption, despite carrying a huge burden
of serious health-related suffering. In many African countries, morphine is simply
unavailable: regulatory barriers, fear of addiction, lack of trained prescribers,
and broken supply chains combine to ensure that millions die in untreated pain.<br><br>
Only <strong>{opioid_count}</strong> interventional trials for opioids or morphine
are registered across all of Africa. Without research into context-appropriate pain
management strategies, the evidence gap perpetuates the suffering.
</div>
<div class="summary-card" style="max-width:400px;margin:1rem auto;">
  <div class="label">Opioid/Morphine Trials in Africa</div>
  <div class="value purple">{opioid_count}</div>
  <div class="label">Interventional, ClinicalTrials.gov</div>
</div>

<!-- 7. Pediatric Palliative Gap -->
<h2>7. The Pediatric Palliative Gap</h2>
<div class="danger-note">
<strong>Children are the invisible victims.</strong><br><br>
Africa has the world's youngest population &mdash; nearly half of its 1.5 billion people
are under 18. Children with cancer, HIV, sickle cell disease, and congenital conditions
face life-limiting illness without any palliative care infrastructure in most countries.<br><br>
Only <strong>{pediatric_count}</strong> interventional pediatric palliative trials are
registered across the entire continent. The global pediatric palliative care community
has called this an ethical emergency, yet the research pipeline remains virtually empty.<br><br>
Most evidence for pediatric symptom management comes from high-income settings where
drug availability, monitoring, and family support systems bear no resemblance to the
African context.
</div>

<!-- 8. Sponsor Analysis -->
<h2>8. Sponsor Analysis</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
Who funds palliative care research in Africa? The sponsor profile reveals whether
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

<!-- 9. Uganda Bright Spot -->
<h2>9. Uganda: The Bright Spot</h2>
<div class="success-note">
<strong>Uganda is one of the few African countries with a national palliative care policy.</strong><br><br>
Uganda integrated palliative care into its national health plan, trained clinical officers
to prescribe morphine, and established Hospice Africa Uganda as a model for the continent.
The African Palliative Care Association (APCA) is headquartered in Kampala.<br><br>
Uganda hosts <strong>{uganda['count']}</strong> palliative/hospice/end-of-life interventional
trials, making it a relative leader on the continent. While this is still a tiny number
by global standards, Uganda demonstrates that policy commitment can translate into
research activity even in low-resource settings.
</div>

<!-- 10. Intervention Types -->
<h2>10. Intervention Types in Africa's Palliative Trials</h2>
<div class="two-col">
<div>
<table>
<thead><tr><th>Intervention Category</th><th style="text-align:right;">Count</th></tr></thead>
<tbody>{interv_rows}</tbody>
</table>
</div>
<div class="method-note" style="align-self:start;">
<strong>Note:</strong> Categorization is based on keyword matching in trial titles and
intervention descriptions. A single trial may address multiple categories. The
dominance of non-pharmacological interventions partly reflects the opioid access
crisis: researchers cannot easily study opioids they cannot obtain.
</div>
</div>

<!-- 11. Comparison with the UK: Hospice Movement Origin -->
<h2>11. Comparison with the UK: Where the Hospice Movement Began</h2>
<div class="method-note">
<strong>The modern hospice movement was born in the United Kingdom</strong> &mdash;
Dame Cicely Saunders founded St Christopher's Hospice in London in 1967,
establishing the principles of holistic end-of-life care that now define
palliative medicine worldwide.<br><br>
The UK has <strong>{uk_trials:,}</strong> registered palliative/hospice/end-of-life
interventional trials for a population of 68 million. Africa, with
<strong>1.5 billion people and vastly greater suffering</strong>, has just
{africa_trials}.<br><br>
The UK has approximately <strong>{round(uk_trials / 68, 2)}</strong> palliative
trials per million people. Africa has <strong>{round(africa_trials / 1500, 3)}</strong>.
That is a <strong>{round((uk_trials / 68) / max(africa_trials / 1500, 0.001), 1)}x</strong>
per-capita gap between the birthplace of hospice care and the continent that needs
it most.
</div>

<!-- 12. Global Comparison -->
<h2>12. Global Comparison</h2>
<div class="scroll-x">
<table>
<thead>
<tr><th>Country/Region</th><th style="text-align:right;">Palliative Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials/Million</th>
<th style="text-align:right;">Ratio vs Africa</th></tr>
</thead>
<tbody>
{global_comp_rows}
</tbody>
</table>
</div>

<!-- 13. Per-Capita Density -->
<h2>13. Per-Capita Palliative Trial Density</h2>
<table>
<thead>
<tr><th>Country/Region</th><th style="text-align:right;">Palliative Trials</th>
<th style="text-align:right;">Population</th>
<th style="text-align:right;">Trials / Million</th></tr>
</thead>
<tbody>
{percap_rows}
</tbody>
</table>

<!-- 14. Phase Distribution -->
<h2>14. Phase Distribution</h2>
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

<!-- 15. Temporal Trend -->
<h2>15. Temporal Trend</h2>
<div class="chart-container">
<canvas id="trendChart" height="250"></canvas>
</div>

<footer>
<p>Data source: ClinicalTrials.gov API v2 | Lancet Commission on Palliative Care (Knaul et al., 2018) |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>Palliative Care CCI methodology: Africa burden share ({AFRICA_PALLIATIVE_BURDEN_PCT}%) /
Africa trial share ({cci['trial_share_pct']}%) = {cci['cci']}</p>
<p style="margin-top:0.5rem;">Project 27 of the Africa RCT Landscape Series</p>
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
          label: 'Palliative Trials',
          data: {country_bar_values},
          backgroundColor: '#a855f7',
          borderWidth: 0,
          borderRadius: 4,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          title: {{ display: true, text: 'Palliative Trials by African Country',
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

  // Condition subtype bar chart
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
          title: {{ display: true, text: 'Palliative Trials by Condition (Africa)',
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
          title: {{ display: true, text: 'Phase Distribution (Africa Palliative)',
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
          label: 'Palliative Trials Started (Africa)',
          data: {trend_counts},
          borderColor: '#a855f7',
          backgroundColor: 'rgba(168,85,247,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 4,
          pointBackgroundColor: '#a855f7',
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ labels: {{ color: '#e2e8f0' }} }},
          title: {{ display: true, text: 'Palliative Trial Starts by Year (Africa)',
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
    print("The Palliative Care Desert: Dying in Pain Without Evidence")
    print("=" * 60)
    print()

    # Fetch data
    print("Fetching palliative care trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    # Compute analyses
    print("Computing Condition Colonialism Index (palliative care)...")
    cci = compute_cci(data)

    print("Computing per-capita density...")
    per_capita = compute_per_capita(data)

    print("Analysing phase distribution...")
    phases = compute_phase_distribution(data)

    print("Analysing sponsors...")
    sponsors = compute_sponsor_analysis(data)

    print("Computing temporal trends...")
    trends = compute_temporal_trend(data)

    print("Analysing intervention types...")
    interventions = compute_intervention_analysis(data)

    print("Analysing Uganda bright spot...")
    uganda = compute_uganda_spotlight(data)

    # Print summary
    print()
    print("-" * 60)
    print("PALLIATIVE CARE DESERT: KEY FINDINGS")
    print("-" * 60)
    print(f"  Africa palliative trials:    {cci['africa_trials']}")
    print(f"  US palliative trials:        {cci['us_trials']:,}")
    print(f"  US/Africa ratio:             {round(cci['us_trials'] / max(cci['africa_trials'], 1), 1)}x")
    print(f"  Africa trial share:          {cci['trial_share_pct']}%")
    print(f"  Africa burden share:         {cci['burden_pct']}%")
    print(f"  Palliative CCI:              {cci['cci']}")
    print()
    print("  Condition subtype counts (Africa):")
    for sub, count in sorted(data.get("condition_subtype_counts", {}).items(),
                              key=lambda x: -x[1]):
        print(f"    {sub:30s} {count:>5}")
    print()
    print(f"  Opioid/morphine trials (Africa): {data.get('opioid_africa_count', 0)}")
    print(f"  Uganda palliative trials:        {uganda['count']}")

    # Country breakdown
    print()
    print("  Country palliative trial counts:")
    for country in COUNTRY_LIST + GLOBAL_COMPARATORS:
        count = data["country_counts"].get(country, 0)
        print(f"    {country:25s} {count:>6,}")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, cci, per_capita, phases, sponsors, trends,
                         interventions, uganda)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
