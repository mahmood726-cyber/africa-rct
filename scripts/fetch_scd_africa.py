"""
Sickle Cell Disease in Africa — Registry Analysis
====================================================
Queries ClinicalTrials.gov API v2 for SCD trials across 8 African
countries, classifies sponsors/interventions/scope, and generates
an HTML equity-analysis dashboard.

Usage:
    python fetch_scd_africa.py

Output:
    data/scd_africa_data.json   — cached trial data (24h validity)
    scd-africa-analysis.html    — interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "scd_africa_data.json"
OUTPUT_HTML = Path(__file__).parent / "scd-africa-analysis.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# 8 target countries + "Africa" keyword
COUNTRIES = [
    "Nigeria", "Kenya", "Uganda", "Ghana",
    "Tanzania", "Egypt", "South Africa", "Cameroon",
]
LOCATION_QUERIES = COUNTRIES + ["Africa"]

# ── Sponsor classification keywords ─────────────────────────────────
AFRICAN_KEYWORDS = [
    "nigeria", "lagos", "ibadan", "makerere", "uganda", "kenya",
    "nairobi", "ghana", "accra", "tanzania", "muhimbili", "cameroon",
    "egypt", "cairo", "ain shams", "south africa", "cape town",
    "witwatersrand",
]

PHARMA_KEYWORDS = [
    "pfizer", "novartis", "roche", "astrazeneca", "novo nordisk",
    "sanofi", "lilly", "gilead", "forma", "agios", "bausch",
    "cardurion", "fulcrum", "hemaquest",
]

NIH_KEYWORDS = ["nih", "niaid", "cdc", "national institutes of health",
                 "national heart, lung", "nhlbi"]

US_ACADEMIC_KEYWORDS = ["university", "hospital", "institute", "medical center",
                        "children's", "memorial", "college of medicine"]

# ── Novel SCD agents ────────────────────────────────────────────────
NOVEL_AGENTS = [
    "crovalimab", "voxelotor", "crizanlizumab", "etavopivat",
    "mitapivat", "osivelotor", "inclacumab", "ticagrelor",
    "prasugrel", "rilzabrutinib", "pociredir", "imr-687",
]


# ── API helpers ──────────────────────────────────────────────────────
def search_trials(location=None, condition="sickle cell disease",
                  page_size=200, page_token=None, max_retries=3):
    """Query CT.gov API v2 with retry logic."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
    }

    filters = ["AREA[StudyType]INTERVENTIONAL"]
    params["filter.advanced"] = " AND ".join(filters)

    if condition:
        params["query.cond"] = condition
    if location:
        params["query.locn"] = location
    if page_token:
        params["pageToken"] = page_token

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}) "
                  f"for location={location}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return {"totalCount": 0, "studies": []}


def fetch_all_pages(location=None, condition="sickle cell disease"):
    """Fetch all pages for a given query."""
    all_studies = []
    page_token = None
    page_num = 0

    while True:
        page_num += 1
        result = search_trials(location=location, condition=condition,
                               page_token=page_token)
        studies = result.get("studies", [])
        total = result.get("totalCount", 0)
        all_studies.extend(studies)

        if page_num == 1:
            print(f"    Total for query: {total}")

        page_token = result.get("nextPageToken")
        if not page_token or not studies:
            break
        time.sleep(RATE_LIMIT_DELAY)

    return all_studies


# ── Data extraction helpers ──────────────────────────────────────────
def extract_nct_id(study):
    """Extract NCT ID from study."""
    try:
        return study["protocolSection"]["identificationModule"]["nctId"]
    except (KeyError, TypeError):
        return None


def extract_title(study):
    """Extract official or brief title."""
    try:
        id_mod = study["protocolSection"]["identificationModule"]
        return id_mod.get("officialTitle") or id_mod.get("briefTitle", "")
    except (KeyError, TypeError):
        return ""


def extract_status(study):
    """Extract overall status."""
    try:
        return study["protocolSection"]["statusModule"]["overallStatus"]
    except (KeyError, TypeError):
        return "UNKNOWN"


def extract_phases(study):
    """Extract phase list."""
    try:
        return study["protocolSection"]["designModule"].get("phases", [])
    except (KeyError, TypeError):
        return []


def extract_conditions(study):
    """Extract condition list."""
    try:
        return study["protocolSection"]["conditionsModule"].get("conditions", [])
    except (KeyError, TypeError):
        return []


def extract_interventions(study):
    """Extract intervention names."""
    try:
        interventions = study["protocolSection"]["armsInterventionsModule"].get(
            "interventions", [])
        return [i.get("name", "") for i in interventions]
    except (KeyError, TypeError):
        return []


def extract_sponsor(study):
    """Extract lead sponsor name."""
    try:
        return study["protocolSection"]["sponsorCollaboratorsModule"][
            "leadSponsor"]["name"]
    except (KeyError, TypeError):
        return "Unknown"


def extract_enrollment(study):
    """Extract enrollment count."""
    try:
        return study["protocolSection"]["designModule"]["enrollmentInfo"].get(
            "count", 0)
    except (KeyError, TypeError):
        return 0


def extract_start_date(study):
    """Extract start date string."""
    try:
        return study["protocolSection"]["statusModule"]["startDateStruct"].get(
            "date", "")
    except (KeyError, TypeError):
        return ""


def count_locations(study):
    """Count number of location sites."""
    try:
        locs = study["protocolSection"]["contactsLocationsModule"].get(
            "locations", [])
        return len(locs)
    except (KeyError, TypeError):
        return 0


def get_location_countries(study):
    """Get set of countries from locations."""
    countries = set()
    try:
        locs = study["protocolSection"]["contactsLocationsModule"].get(
            "locations", [])
        for loc in locs:
            c = loc.get("country", "")
            if c:
                countries.add(c)
    except (KeyError, TypeError):
        pass
    return countries


# ── Classification functions ─────────────────────────────────────────
def classify_sponsor(sponsor_name):
    """Classify sponsor as African-local, US Academic, Pharma, NIH/US Govt, Other."""
    lower = sponsor_name.lower()

    # Check African-local first
    for kw in AFRICAN_KEYWORDS:
        if kw in lower:
            return "African-local"

    # Check Pharma
    for kw in PHARMA_KEYWORDS:
        if kw in lower:
            return "Pharma"

    # Check NIH/US Govt
    for kw in NIH_KEYWORDS:
        if kw in lower:
            return "NIH/US Govt"

    # Check US Academic (only if not African)
    for kw in US_ACADEMIC_KEYWORDS:
        if kw in lower:
            return "US Academic"

    return "Other"


def classify_scope(study, locations_count):
    """Classify as Africa-focused, Global mega-trial, or Regional."""
    countries = get_location_countries(study)
    conditions_text = " ".join(extract_conditions(study)).lower()
    title_text = extract_title(study).lower()
    combined = conditions_text + " " + title_text

    # Global mega-trial: >20 sites
    if locations_count > 20:
        return "Global mega-trial"

    # Africa-focused: single country, <=3 sites, condition mentions Africa
    africa_terms = ["africa", "nigeria", "sub-saharan", "kenyan", "ugandan",
                    "ghanaian", "tanzanian", "egyptian", "cameroonian",
                    "south african"]
    has_africa_mention = any(term in combined for term in africa_terms)

    if len(countries) <= 1 and locations_count <= 3:
        return "Africa-focused"
    if has_africa_mention and locations_count <= 3:
        return "Africa-focused"

    return "Regional"


def classify_intervention(interventions):
    """Classify as Hydroxyurea, Novel agent, or Other."""
    combined = " ".join(interventions).lower()

    if "hydroxyurea" in combined or "hydroxycarbamide" in combined:
        return "Hydroxyurea"

    for agent in NOVEL_AGENTS:
        if agent in combined:
            return "Novel agent"

    return "Other"


# ── Main data collection ────────────────────────────────────────────
def collect_data():
    """Fetch SCD trials for all target locations, deduplicate, classify."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Fetching SCD trials from ClinicalTrials.gov API v2...")
    all_studies_raw = {}  # nct_id -> study
    country_hits = {}     # country -> set of nct_ids

    for loc in LOCATION_QUERIES:
        print(f"  Querying: {loc}")
        studies = fetch_all_pages(location=loc)
        nct_ids_for_loc = set()
        for study in studies:
            nct_id = extract_nct_id(study)
            if nct_id:
                all_studies_raw[nct_id] = study
                nct_ids_for_loc.add(nct_id)
        country_hits[loc] = list(nct_ids_for_loc)
        print(f"    Unique NCT IDs: {len(nct_ids_for_loc)}")
        time.sleep(RATE_LIMIT_DELAY)

    print(f"\nTotal unique trials after dedup: {len(all_studies_raw)}")

    # Extract structured data for each trial
    trials = []
    for nct_id, study in all_studies_raw.items():
        interventions = extract_interventions(study)
        locations_count = count_locations(study)
        trial = {
            "nct_id": nct_id,
            "title": extract_title(study),
            "status": extract_status(study),
            "phases": extract_phases(study),
            "conditions": extract_conditions(study),
            "interventions": interventions,
            "sponsor": extract_sponsor(study),
            "enrollment": extract_enrollment(study),
            "start_date": extract_start_date(study),
            "locations_count": locations_count,
            "countries": list(get_location_countries(study)),
            "sponsor_class": classify_sponsor(extract_sponsor(study)),
            "scope_class": classify_scope(study, locations_count),
            "drug_class": classify_intervention(interventions),
        }
        trials.append(trial)

    # Compute summary stats
    total = len(trials)
    african_led = sum(1 for t in trials if t["sponsor_class"] == "African-local")
    terminated = sum(1 for t in trials if t["status"] in ("TERMINATED", "WITHDRAWN"))
    novel_drug = sum(1 for t in trials if t["drug_class"] == "Novel agent")
    africa_focused = sum(1 for t in trials if t["scope_class"] == "Africa-focused")
    hydroxyurea = sum(1 for t in trials if t["drug_class"] == "Hydroxyurea")

    # Country distribution (from country_hits)
    country_dist = {c: len(ids) for c, ids in country_hits.items()
                    if c in COUNTRIES}

    # Sponsor breakdown
    sponsor_counts = {}
    for t in trials:
        cls = t["sponsor_class"]
        sponsor_counts[cls] = sponsor_counts.get(cls, 0) + 1

    # Phase distribution
    phase_counts = {}
    for t in trials:
        phase_str = ", ".join(t["phases"]) if t["phases"] else "Not stated"
        phase_counts[phase_str] = phase_counts.get(phase_str, 0) + 1

    # Drug class counts
    drug_counts = {}
    for t in trials:
        cls = t["drug_class"]
        drug_counts[cls] = drug_counts.get(cls, 0) + 1

    # Scope counts
    scope_counts = {}
    for t in trials:
        cls = t["scope_class"]
        scope_counts[cls] = scope_counts.get(cls, 0) + 1

    # Terminated trials detail
    terminated_trials = [t for t in trials if t["status"] in ("TERMINATED", "WITHDRAWN")]

    # Ghost enrollment: mega-trials with >20 sites
    ghost_trials = [t for t in trials if t["locations_count"] > 20]

    data = {
        "fetch_date": datetime.now().isoformat(),
        "total_unique": total,
        "african_led_count": african_led,
        "african_led_pct": round(african_led / total * 100, 1) if total else 0,
        "terminated_count": terminated,
        "termination_rate": round(terminated / total * 100, 1) if total else 0,
        "novel_drug_count": novel_drug,
        "novel_drug_pct": round(novel_drug / total * 100, 1) if total else 0,
        "hydroxyurea_count": hydroxyurea,
        "africa_focused_count": africa_focused,
        "africa_focused_pct": round(africa_focused / total * 100, 1) if total else 0,
        "country_distribution": country_dist,
        "sponsor_breakdown": sponsor_counts,
        "phase_distribution": phase_counts,
        "drug_class_counts": drug_counts,
        "scope_counts": scope_counts,
        "terminated_trials": terminated_trials,
        "ghost_trials": ghost_trials,
        "trials": trials,
        "country_hits": country_hits,
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Cached data to {CACHE_FILE}")

    return data


# ── HTML Report Generator ───────────────────────────────────────────
def generate_html(data):
    """Generate a dark-themed HTML equity analysis dashboard."""

    total = data["total_unique"]
    african_led_pct = data["african_led_pct"]
    termination_rate = data["termination_rate"]
    novel_pct = data["novel_drug_pct"]
    africa_focused_pct = data["africa_focused_pct"]
    trials = data["trials"]
    country_dist = data["country_distribution"]
    sponsor_counts = data["sponsor_breakdown"]
    drug_counts = data["drug_class_counts"]
    scope_counts = data["scope_counts"]
    terminated_trials = data["terminated_trials"]
    ghost_trials = data["ghost_trials"]

    # Sort trials by status then NCT ID
    status_order = {"RECRUITING": 0, "ACTIVE_NOT_RECRUITING": 1,
                    "NOT_YET_RECRUITING": 2, "ENROLLING_BY_INVITATION": 3,
                    "COMPLETED": 4, "TERMINATED": 5, "WITHDRAWN": 6,
                    "SUSPENDED": 7, "UNKNOWN": 8}
    trials_sorted = sorted(trials, key=lambda t: (
        status_order.get(t["status"], 99), t["nct_id"]))

    # Build trial table rows
    def status_color(s):
        if s == "COMPLETED":
            return "#2ecc71"
        elif s in ("TERMINATED", "WITHDRAWN"):
            return "#e74c3c"
        elif s in ("RECRUITING", "ACTIVE_NOT_RECRUITING",
                    "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"):
            return "#f1c40f"
        elif s == "SUSPENDED":
            return "#e67e22"
        else:
            return "#95a5a6"

    trial_rows = []
    for t in trials_sorted:
        color = status_color(t["status"])
        title_trunc = t["title"][:80] + ("..." if len(t["title"]) > 80 else "")
        phases_str = ", ".join(t["phases"]) if t["phases"] else "N/A"
        countries_str = ", ".join(t["countries"][:3])
        if len(t["countries"]) > 3:
            countries_str += f" +{len(t['countries']) - 3}"
        interventions_str = ", ".join(t["interventions"][:2])
        if len(t["interventions"]) > 2:
            interventions_str += f" +{len(t['interventions']) - 2}"

        trial_rows.append(f"""<tr style="border-left:4px solid {color}">
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td title="{t['title'].replace('"', '&quot;')}">{title_trunc}</td>
<td>{t['sponsor']}</td>
<td>{t['sponsor_class']}</td>
<td>{countries_str}</td>
<td>{phases_str}</td>
<td style="color:{color}">{t['status']}</td>
<td style="text-align:right">{t['enrollment']:,}</td>
<td style="text-align:right">{t['locations_count']}</td>
<td>{t['drug_class']}</td>
</tr>""")

    trial_table_html = "\n".join(trial_rows)

    # Terminated trials detail
    terminated_rows = []
    for t in terminated_trials:
        title_trunc = t["title"][:60] + ("..." if len(t["title"]) > 60 else "")
        terminated_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td>{title_trunc}</td>
<td>{t['sponsor']}</td>
<td>{t['sponsor_class']}</td>
<td style="text-align:right">{t['locations_count']}</td>
<td>{t['drug_class']}</td>
<td style="color:#e74c3c">{t['status']}</td>
</tr>""")
    terminated_table = "\n".join(terminated_rows) if terminated_rows else \
        '<tr><td colspan="7" style="text-align:center;color:#95a5a6">No terminated trials found</td></tr>'

    # Ghost enrollment detail
    ghost_rows = []
    for t in ghost_trials:
        title_trunc = t["title"][:60] + ("..." if len(t["title"]) > 60 else "")
        african_countries = [c for c in t["countries"]
                            if any(ac.lower() in c.lower() for ac in COUNTRIES)]
        ghost_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td>{title_trunc}</td>
<td>{t['sponsor']}</td>
<td style="text-align:right">{t['locations_count']}</td>
<td style="text-align:right">{t['enrollment']:,}</td>
<td>{', '.join(african_countries) or 'N/A'}</td>
<td>{t['drug_class']}</td>
</tr>""")
    ghost_table = "\n".join(ghost_rows) if ghost_rows else \
        '<tr><td colspan="7" style="text-align:center;color:#95a5a6">No mega-trials found</td></tr>'

    # Country chart data
    country_labels = json.dumps(list(country_dist.keys()))
    country_values = json.dumps(list(country_dist.values()))

    # Sponsor chart data
    sponsor_labels = json.dumps(list(sponsor_counts.keys()))
    sponsor_values = json.dumps(list(sponsor_counts.values()))
    sponsor_colors = json.dumps([
        "#2ecc71" if k == "African-local"
        else "#e74c3c" if k == "Pharma"
        else "#3498db" if k == "US Academic"
        else "#f39c12" if k == "NIH/US Govt"
        else "#95a5a6"
        for k in sponsor_counts.keys()
    ])

    # Drug class chart data
    drug_labels = json.dumps(list(drug_counts.keys()))
    drug_values = json.dumps(list(drug_counts.values()))
    drug_colors = json.dumps([
        "#2ecc71" if k == "Hydroxyurea"
        else "#e74c3c" if k == "Novel agent"
        else "#95a5a6"
        for k in drug_counts.keys()
    ])

    # Severity summary
    severity_items = []
    if african_led_pct < 30:
        severity_items.append({
            "level": "CRITICAL",
            "text": f"Only {african_led_pct}% of trials are African-led"
        })
    if termination_rate > 15:
        severity_items.append({
            "level": "HIGH",
            "text": f"Termination rate of {termination_rate}% suggests external decision-making"
        })
    if novel_pct > 40:
        severity_items.append({
            "level": "HIGH",
            "text": f"{novel_pct}% of trials test novel agents unaffordable in Africa"
        })
    if africa_focused_pct < 50:
        severity_items.append({
            "level": "MODERATE",
            "text": f"Only {africa_focused_pct}% of trials are Africa-focused"
        })
    if len(ghost_trials) > 0:
        severity_items.append({
            "level": "HIGH",
            "text": f"{len(ghost_trials)} mega-trials use African sites as token enrollment"
        })

    severity_html = ""
    for item in severity_items:
        bg = "#7f1d1d" if item["level"] == "CRITICAL" else \
             "#78350f" if item["level"] == "HIGH" else "#1e3a5f"
        severity_html += f"""<div style="background:{bg};padding:12px 16px;
            border-radius:8px;margin:6px 0;display:flex;align-items:center;gap:12px">
            <span style="font-weight:700;color:#fbbf24;min-width:80px">{item['level']}</span>
            <span>{item['text']}</span>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sickle Cell Disease in Africa &mdash; Clinical Trial Equity Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></script>
<style>
:root {{
    --bg: #0a0e17;
    --bg2: #111827;
    --bg3: #1f2937;
    --text: #e5e7eb;
    --text2: #9ca3af;
    --accent: #60a5fa;
    --green: #2ecc71;
    --red: #e74c3c;
    --yellow: #f1c40f;
    --orange: #e67e22;
    --grey: #95a5a6;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}}
.container {{ max-width:1400px; margin:0 auto; padding:24px; }}
h1 {{ font-size:2rem; margin-bottom:8px; color:white; }}
h2 {{ font-size:1.4rem; margin:32px 0 16px; color:var(--accent); border-bottom:1px solid var(--bg3); padding-bottom:8px; }}
h3 {{ font-size:1.1rem; margin:16px 0 8px; color:var(--text); }}
.subtitle {{ color:var(--text2); margin-bottom:24px; }}

/* Banner */
.banner {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
    gap:16px;
    margin:24px 0;
}}
.stat-card {{
    background:var(--bg2);
    border-radius:12px;
    padding:20px;
    text-align:center;
    border:1px solid var(--bg3);
}}
.stat-card .value {{
    font-size:2rem;
    font-weight:700;
    color:white;
}}
.stat-card .label {{
    font-size:0.85rem;
    color:var(--text2);
    margin-top:4px;
}}

/* Charts */
.charts-grid {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(400px,1fr));
    gap:24px;
    margin:24px 0;
}}
.chart-box {{
    background:var(--bg2);
    border-radius:12px;
    padding:20px;
    border:1px solid var(--bg3);
}}

/* Tables */
table {{
    width:100%;
    border-collapse:collapse;
    font-size:0.85rem;
    margin:16px 0;
}}
th {{
    background:var(--bg3);
    padding:10px 8px;
    text-align:left;
    font-weight:600;
    position:sticky;
    top:0;
    z-index:1;
}}
td {{
    padding:8px;
    border-bottom:1px solid var(--bg3);
}}
tr:hover {{
    background:rgba(96,165,250,0.05);
}}
.table-container {{
    max-height:600px;
    overflow-y:auto;
    border-radius:12px;
    border:1px solid var(--bg3);
    background:var(--bg2);
}}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}

/* Severity */
.severity-box {{
    background:var(--bg2);
    border-radius:12px;
    padding:20px;
    border:1px solid var(--bg3);
    margin:16px 0;
}}

/* Footer */
.footer {{
    margin-top:48px;
    padding:24px 0;
    border-top:1px solid var(--bg3);
    color:var(--text2);
    font-size:0.8rem;
    text-align:center;
}}
</style>
</head>
<body>
<div class="container">

<h1>Sickle Cell Disease in Africa</h1>
<p class="subtitle">Clinical Trial Equity Analysis &mdash; ClinicalTrials.gov Registry
&mdash; Generated {datetime.now().strftime('%d %B %Y')}</p>

<!-- ── Summary Banner ─────────────────────────────────── -->
<div class="banner">
    <div class="stat-card">
        <div class="value">{total}</div>
        <div class="label">Total Unique Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--green)">{african_led_pct}%</div>
        <div class="label">African-Led</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{termination_rate}%</div>
        <div class="label">Termination Rate</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--orange)">{novel_pct}%</div>
        <div class="label">Novel Drugs (unaffordable)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--yellow)">{africa_focused_pct}%</div>
        <div class="label">Africa-Focused</div>
    </div>
</div>

<!-- ── Severity Summary ───────────────────────────────── -->
<h2>Severity Summary</h2>
<div class="severity-box">
    {severity_html if severity_html else '<p style="color:var(--text2)">No critical findings</p>'}
</div>

<!-- ── Full Trial Table ───────────────────────────────── -->
<h2>All Trials ({total})</h2>
<p style="color:var(--text2);margin-bottom:8px">
    Rows coloured by status: <span style="color:var(--green)">completed</span>,
    <span style="color:var(--red)">terminated/withdrawn</span>,
    <span style="color:var(--yellow)">active/recruiting</span>,
    <span style="color:var(--grey)">unknown</span>
</p>
<div class="table-container">
<table>
<thead>
<tr>
    <th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sponsor Class</th>
    <th>Country</th><th>Phase</th><th>Status</th><th>Enrollment</th>
    <th>Sites</th><th>Drug Class</th>
</tr>
</thead>
<tbody>
{trial_table_html}
</tbody>
</table>
</div>

<!-- ── Charts ─────────────────────────────────────────── -->
<h2>Geographic Distribution</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trials per Country</h3>
        <canvas id="countryChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Sponsor Breakdown</h3>
        <canvas id="sponsorChart"></canvas>
    </div>
</div>

<h2>Drug Class Analysis</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Intervention Classification</h3>
        <canvas id="drugChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Geographic Scope</h3>
        <canvas id="scopeChart"></canvas>
    </div>
</div>

<!-- ── Termination Cascade ────────────────────────────── -->
<h2>Termination Cascade ({len(terminated_trials)} trials)</h2>
<p style="color:var(--text2);margin-bottom:8px">
    Terminated and withdrawn trials &mdash; pattern: global sponsor decisions
    that remove African sites as collateral.
</p>
<div class="table-container" style="max-height:400px">
<table>
<thead>
<tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sponsor Class</th>
    <th>Sites</th><th>Drug Class</th><th>Status</th></tr>
</thead>
<tbody>
{terminated_table}
</tbody>
</table>
</div>

<!-- ── Ghost Enrollment ───────────────────────────────── -->
<h2>Ghost Enrollment ({len(ghost_trials)} mega-trials)</h2>
<p style="color:var(--text2);margin-bottom:8px">
    Trials with &gt;20 sites where Africa is a token enrollment location in
    a global registration programme.
</p>
<div class="table-container" style="max-height:400px">
<table>
<thead>
<tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Total Sites</th>
    <th>Enrollment</th><th>African Countries</th><th>Drug Class</th></tr>
</thead>
<tbody>
{ghost_table}
</tbody>
</table>
</div>

<!-- ── Footer ─────────────────────────────────────────── -->
<div class="footer">
    Data: ClinicalTrials.gov API v2 (public) | Fetched: {data['fetch_date'][:10]} |
    Unique trials: {total} | Generated by fetch_scd_africa.py
</div>

</div><!-- /container -->

<script>
// Country bar chart
new Chart(document.getElementById('countryChart'), {{
    type: 'bar',
    data: {{
        labels: {country_labels},
        datasets: [{{
            label: 'Trials',
            data: {country_values},
            backgroundColor: '#60a5fa',
            borderRadius: 6,
        }}]
    }},
    options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{
                grid: {{ color: 'rgba(255,255,255,0.05)' }},
                ticks: {{ color: '#9ca3af' }}
            }},
            y: {{
                grid: {{ display: false }},
                ticks: {{ color: '#e5e7eb' }}
            }}
        }}
    }}
}});

// Sponsor doughnut chart
new Chart(document.getElementById('sponsorChart'), {{
    type: 'doughnut',
    data: {{
        labels: {sponsor_labels},
        datasets: [{{
            data: {sponsor_values},
            backgroundColor: {sponsor_colors},
            borderWidth: 0,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{
                position: 'right',
                labels: {{ color: '#e5e7eb', padding: 12 }}
            }}
        }}
    }}
}});

// Drug class doughnut
new Chart(document.getElementById('drugChart'), {{
    type: 'doughnut',
    data: {{
        labels: {drug_labels},
        datasets: [{{
            data: {drug_values},
            backgroundColor: {drug_colors},
            borderWidth: 0,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{
                position: 'right',
                labels: {{ color: '#e5e7eb', padding: 12 }}
            }}
        }}
    }}
}});

// Scope chart
(function() {{
    var scopeData = {json.dumps(scope_counts)};
    var labels = Object.keys(scopeData);
    var values = Object.values(scopeData);
    var colors = labels.map(function(l) {{
        if (l === 'Africa-focused') return '#2ecc71';
        if (l === 'Global mega-trial') return '#e74c3c';
        return '#f39c12';
    }});
    new Chart(document.getElementById('scopeChart'), {{
        type: 'doughnut',
        data: {{
            labels: labels,
            datasets: [{{ data: values, backgroundColor: colors, borderWidth: 0 }}]
        }},
        options: {{
            responsive: true,
            plugins: {{
                legend: {{
                    position: 'right',
                    labels: {{ color: '#e5e7eb', padding: 12 }}
                }}
            }}
        }}
    }});
}})();
</script>

</body>
</html>"""

    return html


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Sickle Cell Disease in Africa — Registry Analysis")
    print("=" * 60)

    data = collect_data()

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total unique trials:    {data['total_unique']}")
    print(f"African-led:            {data['african_led_count']} ({data['african_led_pct']}%)")
    print(f"Termination rate:       {data['terminated_count']} ({data['termination_rate']}%)")
    print(f"Novel drug trials:      {data['novel_drug_count']} ({data['novel_drug_pct']}%)")
    print(f"Africa-focused:         {data['africa_focused_count']} ({data['africa_focused_pct']}%)")
    print(f"Hydroxyurea trials:     {data['hydroxyurea_count']}")
    print()
    print("Country distribution:")
    for country, count in sorted(data["country_distribution"].items(),
                                  key=lambda x: -x[1]):
        print(f"  {country:20s} {count}")
    print()
    print("Sponsor breakdown:")
    for cls, count in sorted(data["sponsor_breakdown"].items(),
                              key=lambda x: -x[1]):
        print(f"  {cls:20s} {count}")
    print()
    print("Drug class:")
    for cls, count in sorted(data["drug_class_counts"].items(),
                              key=lambda x: -x[1]):
        print(f"  {cls:20s} {count}")

    # Generate HTML
    print(f"\nGenerating HTML report...")
    html = generate_html(data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to {OUTPUT_HTML}")
    print(f"Open in browser: file:///{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    main()
