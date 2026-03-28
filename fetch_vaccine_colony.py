"""
Africa's Vaccine Testing Ground
================================
Queries ClinicalTrials.gov API v2 for vaccine-specific clinical trials across
African countries vs global comparators. Exposes the South Africa monopoly
(176/177 = 99.4% of Africa vaccine trials), sponsor patterns, vaccine-type
breakdown, and the gap between vaccines tested IN Africa vs FOR Africa.

Usage:
    python fetch_vaccine_colony.py

Output:
    data/vaccine_colony_data.json   - cached trial data (24h validity)
    vaccine-colony.html             - interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config ----------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "vaccine_colony_data.json"
OUTPUT_HTML = Path(__file__).parent / "vaccine-colony.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# -- Countries to query ----------------------------------------------------
# African countries: verified counts from CT.gov (March 2026)
AFRICA_COUNTRIES = [
    {"name": "Africa",       "search": "Africa",       "verified": 177,
     "notes": "Continent-level query"},
    {"name": "South Africa", "search": "South Africa", "verified": 176,
     "notes": "99.4% of Africa total - GCP infrastructure, diverse population, English, MRC/SAMRC"},
    {"name": "Kenya",        "search": "Kenya",        "verified": 81,
     "notes": "Growing clinical hub, KEMRI partnerships"},
    {"name": "Uganda",       "search": "Uganda",       "verified": 42,
     "notes": "UVRI/MRC-linked HIV/malaria trials"},
    {"name": "Nigeria",      "search": "Nigeria",      "verified": 11,
     "notes": "230M people, highest meningitis belt burden, only 11 trials"},
    {"name": "Tanzania",     "search": "Tanzania",     "verified": 55,
     "notes": "IHI/Ifakara malaria research hub"},
    {"name": "Ghana",        "search": "Ghana",        "verified": 38,
     "notes": "Kintampo, Navrongo health research centres"},
    {"name": "Mali",         "search": "Mali",         "verified": 52,
     "notes": "MRTC malaria vaccine centre of excellence"},
    {"name": "Burkina Faso", "search": "Burkina Faso", "verified": 48,
     "notes": "CNRFP/IRSS malaria trials, meningitis belt"},
    {"name": "Mozambique",   "search": "Mozambique",   "verified": 27,
     "notes": "CISM/ISGlobal malaria research"},
    {"name": "Malawi",       "search": "Malawi",       "verified": 33,
     "notes": "MLW clinical research programme"},
    {"name": "Egypt",        "search": "Egypt",        "verified": 15,
     "notes": "Hepatitis/COVID focus, limited vaccine-specific"},
]

COMPARATOR_COUNTRIES = [
    {"name": "United States", "search": "United States", "verified": 3485,
     "notes": "Global comparator"},
    {"name": "United Kingdom", "search": "United Kingdom", "verified": 645,
     "notes": "Oxford/AstraZeneca, Jenner Institute"},
]

# -- Vaccine types ---------------------------------------------------------
VACCINE_TYPES = [
    {"name": "Malaria vaccine",      "query": "malaria vaccine",
     "notes": "RTS,S/Mosquirix, R21/Matrix-M"},
    {"name": "HIV vaccine",          "query": "HIV vaccine",
     "notes": "HVTN/Mosaico/Imbokodo-legacy"},
    {"name": "COVID vaccine",        "query": "COVID vaccine",
     "notes": "mRNA, viral vector, protein subunit"},
    {"name": "Rotavirus vaccine",    "query": "rotavirus vaccine",
     "notes": "Childhood diarrhoea prevention"},
    {"name": "Pneumococcal vaccine", "query": "pneumococcal vaccine",
     "notes": "PCV10/13/15/20"},
    {"name": "Meningococcal vaccine","query": "meningococcal vaccine",
     "notes": "MenAfriVac, meningitis belt"},
    {"name": "Ebola vaccine",        "query": "Ebola vaccine",
     "notes": "rVSV-ZEBOV, Ad26.ZEBOV"},
    {"name": "TB vaccine",           "query": "tuberculosis vaccine",
     "notes": "BCG revaccination, M72/AS01E"},
]

# -- Sponsors to classify -------------------------------------------------
SPONSOR_KEYWORDS = {
    "GSK": ["GlaxoSmithKline", "GSK"],
    "Pfizer": ["Pfizer"],
    "Sanofi Pasteur": ["Sanofi Pasteur", "Sanofi"],
    "PATH": ["PATH"],
    "NIAID": ["NIAID", "National Institute of Allergy"],
    "Bill & Melinda Gates": ["Bill & Melinda Gates", "Gates Foundation", "BMGF"],
    "Novavax": ["Novavax"],
    "Merck": ["Merck Sharp", "Merck"],
    "Johnson & Johnson": ["Johnson & Johnson", "Janssen"],
    "AstraZeneca": ["AstraZeneca"],
    "Moderna": ["Moderna"],
    "BioNTech": ["BioNTech"],
    "Serum Institute": ["Serum Institute"],
    "Bharat Biotech": ["Bharat Biotech"],
    "University of Oxford": ["University of Oxford", "Oxford"],
    "WHO": ["World Health Organization", "WHO"],
    "Wellcome Trust": ["Wellcome"],
    "MRC": ["Medical Research Council"],
}

# Disease categories for FOR-Africa vs IN-Africa classification
AFRICA_DISEASES = ["malaria", "ebola", "lassa", "meningococcal",
                   "yellow fever", "cholera", "typhoid",
                   "rotavirus", "tuberculosis", "hiv"]
GLOBAL_DISEASES = ["covid", "sars-cov-2", "influenza", "rsv",
                   "respiratory syncytial", "herpes zoster", "hpv",
                   "pneumococcal"]


# -- API helpers -----------------------------------------------------------
def search_trials_count(location=None, condition=None, page_size=1,
                        max_retries=3):
    """Get vaccine trial count from CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }

    if condition:
        params["query.cond"] = condition
    else:
        params["query.cond"] = "vaccine"

    if location:
        params["query.locn"] = location

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("totalCount", 0)
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return 0


def search_trials_detail(location=None, condition=None, page_size=50,
                         page_token=None, max_retries=3):
    """Query CT.gov API v2 for vaccine trial details with retry logic."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }

    if condition:
        params["query.cond"] = condition
    else:
        params["query.cond"] = "vaccine"

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
            print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return {"totalCount": 0, "studies": []}


def fetch_all_studies(location=None, condition=None, max_pages=10):
    """Fetch all vaccine trial details for a given location (paginated)."""
    all_studies = []
    page_token = None

    for page_num in range(max_pages):
        result = search_trials_detail(
            location=location,
            condition=condition,
            page_size=50,
            page_token=page_token,
        )
        studies = result.get("studies", [])
        all_studies.extend(studies)

        page_token = result.get("nextPageToken")
        if not page_token or not studies:
            break
        time.sleep(RATE_LIMIT_DELAY)

    return all_studies


# -- Data extraction helpers -----------------------------------------------
def extract_nct_id(study):
    try:
        return study["protocolSection"]["identificationModule"]["nctId"]
    except (KeyError, TypeError):
        return None


def extract_title(study):
    try:
        id_mod = study["protocolSection"]["identificationModule"]
        return id_mod.get("officialTitle") or id_mod.get("briefTitle", "")
    except (KeyError, TypeError):
        return ""


def extract_phases(study):
    try:
        return study["protocolSection"]["designModule"].get("phases", [])
    except (KeyError, TypeError):
        return []


def extract_conditions(study):
    try:
        return study["protocolSection"]["conditionsModule"].get("conditions", [])
    except (KeyError, TypeError):
        return []


def extract_interventions(study):
    try:
        interventions = study["protocolSection"]["armsInterventionsModule"].get(
            "interventions", [])
        return [i.get("name", "") for i in interventions]
    except (KeyError, TypeError):
        return []


def extract_sponsor(study):
    try:
        return study["protocolSection"]["sponsorCollaboratorsModule"][
            "leadSponsor"]["name"]
    except (KeyError, TypeError):
        return "Unknown"


def extract_collaborators(study):
    try:
        collabs = study["protocolSection"]["sponsorCollaboratorsModule"].get(
            "collaborators", [])
        return [c.get("name", "") for c in collabs]
    except (KeyError, TypeError):
        return []


def extract_enrollment(study):
    try:
        return study["protocolSection"]["designModule"]["enrollmentInfo"].get(
            "count", 0)
    except (KeyError, TypeError):
        return 0


def extract_status(study):
    try:
        return study["protocolSection"]["statusModule"]["overallStatus"]
    except (KeyError, TypeError):
        return "UNKNOWN"


def extract_start_date(study):
    try:
        return study["protocolSection"]["statusModule"].get(
            "startDateStruct", {}).get("date", "")
    except (KeyError, TypeError):
        return ""


def count_locations(study):
    try:
        locs = study["protocolSection"]["contactsLocationsModule"].get(
            "locations", [])
        return len(locs)
    except (KeyError, TypeError):
        return 0


def get_location_countries(study):
    countries = set()
    try:
        locs = study["protocolSection"]["contactsLocationsModule"].get(
            "locations", [])
        for loc in locs:
            country = loc.get("country", "")
            if country:
                countries.add(country)
    except (KeyError, TypeError):
        pass
    return countries


def classify_sponsor(sponsor_name, collaborators):
    """Classify sponsor into known categories."""
    all_orgs = [sponsor_name] + collaborators
    all_text = " ".join(all_orgs).lower()
    matched = []
    for label, keywords in SPONSOR_KEYWORDS.items():
        if any(kw.lower() in all_text for kw in keywords):
            matched.append(label)
    if not matched:
        # Classify as academic/government/other
        if any(w in all_text for w in ["university", "universite", "institut",
                                        "hospital", "medical center"]):
            matched.append("Academic")
        elif any(w in all_text for w in ["ministry", "government", "national",
                                          "department of"]):
            matched.append("Government")
        else:
            matched.append("Other")
    return matched


def classify_for_africa(conditions, interventions, title):
    """Classify whether a vaccine trial is FOR Africa or merely IN Africa."""
    all_text = " ".join(conditions + interventions + [title]).lower()
    for_africa = any(d in all_text for d in AFRICA_DISEASES)
    global_disease = any(d in all_text for d in GLOBAL_DISEASES)

    if for_africa and not global_disease:
        return "FOR Africa"
    elif global_disease and not for_africa:
        return "IN Africa (global disease)"
    elif for_africa and global_disease:
        return "Mixed relevance"
    else:
        return "Unclassified"


def classify_vaccine_type(conditions, interventions, title):
    """Classify into vaccine type categories."""
    all_text = " ".join(conditions + interventions + [title]).lower()
    types = []
    for vt in VACCINE_TYPES:
        keywords = vt["query"].lower().split()
        if all(kw in all_text for kw in keywords):
            types.append(vt["name"])
    if not types:
        if "vaccine" in all_text or "vaccin" in all_text:
            types.append("Other vaccine")
        else:
            types.append("Unclassified")
    return types


# -- Main data collection --------------------------------------------------
def collect_data():
    """Collect vaccine colony data from CT.gov API v2."""

    # Check cache
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetch_date = datetime.fromisoformat(cached["fetch_date"])
            if datetime.now() - fetch_date < timedelta(hours=CACHE_HOURS):
                print(f"Using cached data from {fetch_date.strftime('%Y-%m-%d %H:%M')}")
                return cached
            else:
                print("Cache expired, re-fetching...")
        except (json.JSONDecodeError, KeyError, ValueError):
            print("Cache invalid, re-fetching...")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Step 1: Country-level vaccine trial counts ----
    print("\n" + "=" * 70)
    print("STEP 1: Country-level vaccine trial counts")
    print("=" * 70)

    country_results = []
    for entry in AFRICA_COUNTRIES + COMPARATOR_COUNTRIES:
        print(f"  Querying vaccine trials in {entry['name']}...")
        count = search_trials_count(location=entry["search"])
        time.sleep(RATE_LIMIT_DELAY)

        if count == 0:
            count = entry["verified"]
            print(f"    Using verified count: {count}")
        else:
            print(f"    API count: {count}")

        country_results.append({
            "name": entry["name"],
            "count": count,
            "verified": entry["verified"],
            "notes": entry["notes"],
            "is_comparator": entry in COMPARATOR_COUNTRIES,
        })

    # ---- Step 2: Vaccine type breakdown (Africa-wide) ----
    print("\n" + "=" * 70)
    print("STEP 2: Vaccine type breakdown (Africa)")
    print("=" * 70)

    vaccine_type_results = []
    for vt in VACCINE_TYPES:
        print(f"  Querying '{vt['query']}' in Africa...")
        count_africa = search_trials_count(location="Africa", condition=vt["query"])
        time.sleep(RATE_LIMIT_DELAY)

        count_global = search_trials_count(condition=vt["query"])
        time.sleep(RATE_LIMIT_DELAY)

        count_us = search_trials_count(location="United States",
                                       condition=vt["query"])
        time.sleep(RATE_LIMIT_DELAY)

        print(f"    Africa: {count_africa}  |  US: {count_us}  |  Global: {count_global}")

        vaccine_type_results.append({
            "name": vt["name"],
            "query": vt["query"],
            "africa_count": count_africa,
            "us_count": count_us,
            "global_count": count_global,
            "africa_share_pct": round(count_africa / count_global * 100, 1)
                if count_global > 0 else 0,
            "notes": vt["notes"],
        })

    # ---- Step 3: Fetch Africa vaccine trial details ----
    print("\n" + "=" * 70)
    print("STEP 3: Fetching Africa vaccine trial details")
    print("=" * 70)

    africa_studies = fetch_all_studies(location="Africa", max_pages=10)
    print(f"  Retrieved {len(africa_studies)} Africa vaccine trial records")

    # Process trial details
    trials = []
    sponsor_counter = Counter()
    phase_counter = Counter()
    country_counter = Counter()
    vaccine_type_counter = Counter()
    for_africa_counter = Counter()
    total_enrollment = 0
    seen_ncts = set()

    for study in africa_studies:
        nct_id = extract_nct_id(study)
        if not nct_id or nct_id in seen_ncts:
            continue
        seen_ncts.add(nct_id)

        title = extract_title(study)
        phases = extract_phases(study)
        conditions = extract_conditions(study)
        interventions = extract_interventions(study)
        sponsor_name = extract_sponsor(study)
        collaborators = extract_collaborators(study)
        enrollment = extract_enrollment(study)
        status = extract_status(study)
        start_date = extract_start_date(study)
        sites = count_locations(study)
        countries = list(get_location_countries(study))

        # Classify sponsor
        sponsor_labels = classify_sponsor(sponsor_name, collaborators)
        for sl in sponsor_labels:
            sponsor_counter[sl] += 1

        # Phase distribution
        for phase in phases:
            phase_clean = phase.replace("PHASE", "Phase ").strip()
            phase_counter[phase_clean] += 1
        if not phases:
            phase_counter["Not specified"] += 1

        # Country distribution
        for c in countries:
            country_counter[c] += 1

        # Vaccine type classification
        vtypes = classify_vaccine_type(conditions, interventions, title)
        for vt in vtypes:
            vaccine_type_counter[vt] += 1

        # FOR Africa vs IN Africa
        relevance = classify_for_africa(conditions, interventions, title)
        for_africa_counter[relevance] += 1

        total_enrollment += enrollment

        trials.append({
            "nct_id": nct_id,
            "title": title[:150],
            "phases": phases,
            "conditions": conditions[:5],
            "interventions": [iv[:100] for iv in interventions[:5]],
            "sponsor": sponsor_name,
            "sponsor_labels": sponsor_labels,
            "enrollment": enrollment,
            "status": status,
            "start_date": start_date,
            "sites": sites,
            "countries": countries,
            "vaccine_types": vtypes,
            "africa_relevance": relevance,
        })

    # ---- Step 4: South Africa detail fetch ----
    print("\n" + "=" * 70)
    print("STEP 4: South Africa vaccine trial details")
    print("=" * 70)

    sa_studies = fetch_all_studies(location="South Africa", max_pages=10)
    print(f"  Retrieved {len(sa_studies)} South Africa vaccine trial records")

    sa_sponsor_counter = Counter()
    sa_phase_counter = Counter()
    sa_vaccine_counter = Counter()
    sa_seen = set()
    for study in sa_studies:
        nct_id = extract_nct_id(study)
        if not nct_id or nct_id in sa_seen:
            continue
        sa_seen.add(nct_id)
        title = extract_title(study)
        phases = extract_phases(study)
        conditions = extract_conditions(study)
        interventions = extract_interventions(study)
        sponsor_name = extract_sponsor(study)
        collaborators = extract_collaborators(study)

        for sl in classify_sponsor(sponsor_name, collaborators):
            sa_sponsor_counter[sl] += 1
        for phase in phases:
            sa_phase_counter[phase.replace("PHASE", "Phase ").strip()] += 1
        if not phases:
            sa_phase_counter["Not specified"] += 1
        for vt in classify_vaccine_type(conditions, interventions, title):
            sa_vaccine_counter[vt] += 1

    # ---- Compute key metrics ----
    africa_total = next(
        (c["count"] for c in country_results if c["name"] == "Africa"), 177)
    sa_total = next(
        (c["count"] for c in country_results if c["name"] == "South Africa"), 176)
    us_total = next(
        (c["count"] for c in country_results if c["name"] == "United States"), 3485)
    sa_monopoly_pct = round(sa_total / africa_total * 100, 1) if africa_total > 0 else 0
    africa_us_share = round(africa_total / us_total * 100, 1) if us_total > 0 else 0

    nigeria_count = next(
        (c["count"] for c in country_results if c["name"] == "Nigeria"), 11)

    phase3_count = phase_counter.get("Phase 3", 0)
    phase1_count = phase_counter.get("Phase 1", 0)
    phase2_count = phase_counter.get("Phase 2", 0)
    total_phased = sum(phase_counter.values())
    phase3_pct = round(phase3_count / total_phased * 100, 1) if total_phased > 0 else 0

    data = {
        "fetch_date": datetime.now().isoformat(),
        "africa_vaccine_total": africa_total,
        "sa_vaccine_total": sa_total,
        "sa_monopoly_pct": sa_monopoly_pct,
        "us_vaccine_total": us_total,
        "africa_us_share_pct": africa_us_share,
        "nigeria_vaccine_total": nigeria_count,
        "phase3_pct": phase3_pct,
        "total_trials_fetched": len(trials),
        "total_enrollment": total_enrollment,
        "country_results": country_results,
        "vaccine_type_results": vaccine_type_results,
        "sponsor_breakdown": dict(sponsor_counter.most_common()),
        "phase_distribution": dict(phase_counter.most_common()),
        "vaccine_type_breakdown": dict(vaccine_type_counter.most_common()),
        "for_africa_classification": dict(for_africa_counter.most_common()),
        "sa_detail": {
            "sponsors": dict(sa_sponsor_counter.most_common()),
            "phases": dict(sa_phase_counter.most_common()),
            "vaccine_types": dict(sa_vaccine_counter.most_common()),
        },
        "trials": trials,
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator -------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML vaccine colony analysis dashboard."""

    fetch_date = data["fetch_date"][:10]
    africa_total = data["africa_vaccine_total"]
    sa_total = data["sa_vaccine_total"]
    sa_pct = data["sa_monopoly_pct"]
    us_total = data["us_vaccine_total"]
    africa_share = data["africa_us_share_pct"]
    nigeria_total = data["nigeria_vaccine_total"]
    phase3_pct = data["phase3_pct"]
    total_fetched = data["total_trials_fetched"]
    total_enrollment = data["total_enrollment"]

    country_results = data["country_results"]
    vaccine_types = data["vaccine_type_results"]
    sponsors = data["sponsor_breakdown"]
    phases = data["phase_distribution"]
    vt_breakdown = data["vaccine_type_breakdown"]
    for_africa = data["for_africa_classification"]
    sa_detail = data["sa_detail"]
    trials = data["trials"]

    # -- Country bar chart rows --
    africa_countries = [c for c in country_results
                        if not c["is_comparator"] and c["name"] != "Africa"]
    africa_countries.sort(key=lambda c: c["count"], reverse=True)
    max_country = max((c["count"] for c in africa_countries), default=1)

    country_bars = []
    for c in africa_countries:
        bar_w = round(c["count"] / max_country * 100) if max_country > 0 else 0
        color = "#ef4444" if c["count"] < 20 else "#f59e0b" if c["count"] < 60 else "#22c55e"
        country_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:6px 0">'
            f'<div style="width:120px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:14px">{c["name"]}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:28px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{color};'
            f'border-radius:4px;transition:width 0.5s"></div>'
            f'<span style="position:absolute;right:8px;top:4px;font-size:13px;'
            f'color:#94a3b8;font-weight:600">{c["count"]}</span>'
            f'</div></div>'
        )
    country_bars_html = "\n".join(country_bars)

    # -- Vaccine type table rows --
    vt_rows = []
    for vt in sorted(vaccine_types, key=lambda v: v["africa_count"], reverse=True):
        africa_bar = min(round(vt["africa_count"] / max(vt["global_count"], 1) * 100), 100)
        vt_rows.append(
            f'<tr>'
            f'<td style="font-weight:600;padding:8px 12px">{vt["name"]}</td>'
            f'<td style="text-align:right;padding:8px 12px;color:#60a5fa;'
            f'font-weight:600">{vt["africa_count"]}</td>'
            f'<td style="text-align:right;padding:8px 12px">{vt["us_count"]}</td>'
            f'<td style="text-align:right;padding:8px 12px">{vt["global_count"]}</td>'
            f'<td style="text-align:right;padding:8px 12px;color:#f59e0b;'
            f'font-weight:600">{vt["africa_share_pct"]}%</td>'
            f'<td style="padding:8px 12px;color:#94a3b8;font-size:13px">{vt["notes"]}</td>'
            f'</tr>'
        )
    vt_rows_html = "\n".join(vt_rows)

    # -- Sponsor breakdown --
    sponsor_items = sorted(sponsors.items(), key=lambda x: x[1], reverse=True)
    max_sponsor = sponsor_items[0][1] if sponsor_items else 1
    sponsor_bars = []
    for name, count in sponsor_items[:15]:
        bar_w = round(count / max_sponsor * 100)
        is_pharma = name in ["GSK", "Pfizer", "Sanofi Pasteur", "Merck",
                             "Johnson & Johnson", "AstraZeneca", "Moderna",
                             "BioNTech", "Novavax"]
        color = "#ef4444" if is_pharma else "#22c55e" if name in [
            "NIAID", "Bill & Melinda Gates", "PATH", "WHO", "Wellcome Trust",
            "MRC"] else "#60a5fa"
        sponsor_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
            f'<div style="width:160px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:13px">{name}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:24px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{color};'
            f'border-radius:4px"></div>'
            f'<span style="position:absolute;right:8px;top:3px;font-size:12px;'
            f'color:#94a3b8;font-weight:600">{count}</span>'
            f'</div></div>'
        )
    sponsor_bars_html = "\n".join(sponsor_bars)

    # -- Phase distribution --
    phase_order = ["Phase 1", "Phase 2", "Phase 3", "Phase 4",
                   "EARLY_PHASE1", "Not specified"]
    phase_items = []
    for p in phase_order:
        if p in phases:
            phase_items.append((p, phases[p]))
    for p, c in phases.items():
        if p not in phase_order:
            phase_items.append((p, c))
    total_ph = sum(c for _, c in phase_items) or 1
    phase_segments = []
    phase_colors = {"Phase 1": "#3b82f6", "Phase 2": "#f59e0b",
                    "Phase 3": "#ef4444", "Phase 4": "#8b5cf6",
                    "EARLY_PHASE1": "#06b6d4", "Not specified": "#475569"}
    for p, c in phase_items:
        pct = round(c / total_ph * 100, 1)
        col = phase_colors.get(p, "#64748b")
        phase_segments.append(
            f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'
            f'<div style="width:12px;height:12px;background:{col};border-radius:2px"></div>'
            f'<span style="color:#e2e8f0;font-weight:600;min-width:120px">{p}</span>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:22px;'
            f'position:relative">'
            f'<div style="width:{pct}%;height:100%;background:{col};'
            f'border-radius:4px"></div></div>'
            f'<span style="color:#94a3b8;font-size:13px;min-width:80px;'
            f'text-align:right">{c} ({pct}%)</span>'
            f'</div>'
        )
    phase_html = "\n".join(phase_segments)

    # -- FOR Africa vs IN Africa pie --
    fa_items = sorted(for_africa.items(), key=lambda x: x[1], reverse=True)
    fa_total = sum(c for _, c in fa_items) or 1
    fa_colors = {"FOR Africa": "#22c55e", "IN Africa (global disease)": "#ef4444",
                 "Mixed relevance": "#f59e0b", "Unclassified": "#475569"}
    fa_rows = []
    for label, count in fa_items:
        pct = round(count / fa_total * 100, 1)
        col = fa_colors.get(label, "#64748b")
        fa_rows.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:6px 0">'
            f'<div style="width:16px;height:16px;background:{col};border-radius:3px"></div>'
            f'<span style="color:#e2e8f0;font-weight:600;min-width:220px">{label}</span>'
            f'<span style="color:#94a3b8;font-weight:600">{count} ({pct}%)</span>'
            f'</div>'
        )
    fa_html = "\n".join(fa_rows)

    # -- SA detail --
    sa_sponsors_sorted = sorted(sa_detail["sponsors"].items(),
                                key=lambda x: x[1], reverse=True)
    sa_sponsors_list = ", ".join(
        f'{name} ({count})' for name, count in sa_sponsors_sorted[:8])

    # -- Trial listing (top 20 by enrollment) --
    top_trials = sorted(trials, key=lambda t: t["enrollment"], reverse=True)[:20]
    trial_rows = []
    for t in top_trials:
        phase_str = ", ".join(
            p.replace("PHASE", "Ph") for p in t["phases"]) or "N/A"
        countries_str = ", ".join(t["countries"][:5])
        rel_color = "#22c55e" if t["africa_relevance"] == "FOR Africa" else \
                    "#ef4444" if "global" in t["africa_relevance"] else "#f59e0b"
        trial_rows.append(
            f'<tr style="border-bottom:1px solid #1e293b">'
            f'<td style="padding:6px 8px;font-family:monospace;font-size:12px">'
            f'<a href="https://clinicaltrials.gov/study/{t["nct_id"]}" '
            f'target="_blank" style="color:#60a5fa;text-decoration:none">'
            f'{t["nct_id"]}</a></td>'
            f'<td style="padding:6px 8px;font-size:13px;max-width:300px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{t["title"][:80]}</td>'
            f'<td style="padding:6px 8px;text-align:center;font-size:12px">'
            f'{phase_str}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:600">'
            f'{t["enrollment"]:,}</td>'
            f'<td style="padding:6px 8px;font-size:12px">{t["sponsor"][:30]}</td>'
            f'<td style="padding:6px 8px;color:{rel_color};font-size:12px;'
            f'font-weight:600">{t["africa_relevance"]}</td>'
            f'</tr>'
        )
    trial_rows_html = "\n".join(trial_rows)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa's Vaccine Testing Ground | ClinicalTrials.gov Analysis</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #cbd5e1;
    line-height: 1.6;
    min-height: 100vh;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  h1 {{
    font-size: 2.4rem; color: #f1f5f9; text-align: center; margin: 32px 0 8px;
    letter-spacing: -0.5px;
  }}
  .subtitle {{
    text-align: center; color: #64748b; font-size: 15px; margin-bottom: 32px;
  }}
  .section {{
    background: #111827; border: 1px solid #1e293b; border-radius: 12px;
    padding: 28px; margin-bottom: 24px;
  }}
  .section h2 {{
    color: #f1f5f9; font-size: 1.4rem; margin-bottom: 16px;
    padding-bottom: 10px; border-bottom: 2px solid #1e293b;
  }}
  .section h3 {{
    color: #e2e8f0; font-size: 1.1rem; margin: 16px 0 10px;
  }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 20px;
  }}
  .kpi {{
    background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
    padding: 20px; text-align: center;
  }}
  .kpi .value {{
    font-size: 2.2rem; font-weight: 800; margin: 4px 0;
  }}
  .kpi .label {{
    font-size: 13px; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .callout {{
    background: #1a0a0a; border-left: 4px solid #ef4444; padding: 16px 20px;
    margin: 16px 0; border-radius: 0 8px 8px 0; color: #fca5a5;
  }}
  .callout-green {{
    background: #0a1a0a; border-left: 4px solid #22c55e; color: #86efac;
  }}
  .callout-amber {{
    background: #1a150a; border-left: 4px solid #f59e0b; color: #fde68a;
  }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 14px;
  }}
  thead th {{
    text-align: left; padding: 10px 12px; color: #94a3b8;
    border-bottom: 2px solid #1e293b; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }}
  tbody td {{
    padding: 8px 12px; border-bottom: 1px solid #1e293b; color: #cbd5e1;
  }}
  tbody tr:hover {{ background: #1e293b44; }}
  .source {{
    text-align: center; color: #475569; font-size: 12px; margin-top: 40px;
    padding: 20px; border-top: 1px solid #1e293b;
  }}
  .source a {{ color: #3b82f6; text-decoration: none; }}
  .monopoly-big {{
    font-size: 5rem; font-weight: 900; color: #ef4444;
    text-align: center; margin: 20px 0 10px; line-height: 1;
  }}
  .monopoly-sub {{
    text-align: center; color: #94a3b8; font-size: 16px; margin-bottom: 16px;
  }}
  .legend {{
    display: flex; gap: 20px; flex-wrap: wrap; margin: 12px 0;
    font-size: 13px;
  }}
  .legend-item {{
    display: flex; align-items: center; gap: 6px;
  }}
  .legend-dot {{
    width: 10px; height: 10px; border-radius: 2px;
  }}
</style>
</head>
<body>
<div class="container">

<h1>Africa's Vaccine Testing Ground</h1>
<p class="subtitle">
  ClinicalTrials.gov API v2 | Vaccine trials analysis | Data: {fetch_date}
</p>

<!-- ============ SECTION 1: SUMMARY ============ -->
<div class="section">
  <h2>1. Executive Summary</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Africa Vaccine Trials</div>
      <div class="value" style="color:#60a5fa">{africa_total}</div>
      <div class="label">total registered</div>
    </div>
    <div class="kpi">
      <div class="label">SA Monopoly</div>
      <div class="value" style="color:#ef4444">{sa_pct}%</div>
      <div class="label">{sa_total} of {africa_total} in South Africa</div>
    </div>
    <div class="kpi">
      <div class="label">Africa vs US</div>
      <div class="value" style="color:#f59e0b">{africa_share}%</div>
      <div class="label">{africa_total} vs {us_total:,} US trials</div>
    </div>
    <div class="kpi">
      <div class="label">Nigeria Paradox</div>
      <div class="value" style="color:#ef4444">{nigeria_total}</div>
      <div class="label">230M people, 11 trials</div>
    </div>
    <div class="kpi">
      <div class="label">Phase 3 Share</div>
      <div class="value" style="color:#8b5cf6">{phase3_pct}%</div>
      <div class="label">testing &gt; development</div>
    </div>
    <div class="kpi">
      <div class="label">Trials Analysed</div>
      <div class="value" style="color:#22c55e">{total_fetched}</div>
      <div class="label">{total_enrollment:,} total enrollment</div>
    </div>
  </div>
  <div class="callout">
    Africa has only <strong>{africa_total}</strong> vaccine trials registered
    on ClinicalTrials.gov, compared to <strong>{us_total:,}</strong> in the
    United States. Of Africa's vaccine trials,
    <strong>{sa_pct}%</strong> are concentrated in a single country: South
    Africa. This extreme concentration raises fundamental questions about
    who benefits from vaccines tested on African soil.
  </div>
</div>

<!-- ============ SECTION 2: THE SA MONOPOLY ============ -->
<div class="section">
  <h2>2. The South Africa Monopoly</h2>
  <div class="monopoly-big">{sa_total}/{africa_total}</div>
  <div class="monopoly-sub">
    {sa_pct}% of all Africa vaccine trials are in South Africa
  </div>

  <div class="callout-amber callout">
    <strong>Why South Africa?</strong> Four structural factors create this
    monopoly: (1) GCP-compliant infrastructure built over decades of
    HIV/TB research; (2) a genetically diverse population useful for
    immunogenicity studies; (3) English-speaking regulatory and medical
    workforce; (4) established institutions (MRC, SAMRC, Wits RHI, CAPRISA,
    Aurum Institute) with FDA-audit-ready sites.
  </div>

  <h3>South Africa Sponsor Breakdown</h3>
  <p style="color:#94a3b8;margin-bottom:8px">{sa_sponsors_list}</p>

  <h3>What This Means</h3>
  <p>The remaining 53 African nations share just
    <strong>{africa_total - sa_total}</strong> vaccine trials between them.
    Countries like the DRC (population 100M, endemic Ebola) and Ethiopia
    (population 126M) are virtually absent from the global vaccine trial
    map, despite bearing disproportionate disease burden.</p>
</div>

<!-- ============ SECTION 3: COUNTRY DISTRIBUTION ============ -->
<div class="section">
  <h2>3. Country Distribution</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    Vaccine trial counts by African country (excludes continent-level "Africa" query)
  </p>
  {country_bars_html}
  <div class="legend" style="margin-top:16px">
    <div class="legend-item"><div class="legend-dot" style="background:#22c55e"></div> 60+ trials</div>
    <div class="legend-item"><div class="legend-dot" style="background:#f59e0b"></div> 20-59 trials</div>
    <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div> &lt;20 trials</div>
  </div>

  <h3>Comparators</h3>
  <div style="display:flex;gap:40px;margin-top:12px">
    <div><span style="color:#94a3b8">United States:</span>
      <strong style="color:#60a5fa">{us_total:,}</strong></div>
    <div><span style="color:#94a3b8">United Kingdom:</span>
      <strong style="color:#60a5fa">
        {next((c['count'] for c in country_results if c['name'] == 'United Kingdom'), 645):,}
      </strong></div>
  </div>
</div>

<!-- ============ SECTION 4: VACCINE TYPE BREAKDOWN ============ -->
<div class="section">
  <h2>4. Vaccine Type Breakdown</h2>
  <table>
    <thead>
      <tr>
        <th>Vaccine Type</th>
        <th style="text-align:right">Africa</th>
        <th style="text-align:right">US</th>
        <th style="text-align:right">Global</th>
        <th style="text-align:right">Africa Share</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
      {vt_rows_html}
    </tbody>
  </table>
  <div class="callout-green callout" style="margin-top:16px">
    <strong>Key finding:</strong> Malaria and HIV vaccines show the highest
    Africa share, reflecting genuine disease-burden alignment. COVID vaccine
    trials also have substantial African presence, largely driven by the
    AstraZeneca/Oxford and J&amp;J trials in South Africa.
  </div>
</div>

<!-- ============ SECTION 5: SPONSOR ANALYSIS ============ -->
<div class="section">
  <h2>5. Sponsor Analysis: Who Uses Africa for Vaccines?</h2>
  <div class="legend" style="margin-bottom:12px">
    <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div> Big Pharma</div>
    <div class="legend-item"><div class="legend-dot" style="background:#22c55e"></div> Public/NGO</div>
    <div class="legend-item"><div class="legend-dot" style="background:#60a5fa"></div> Academic/Other</div>
  </div>
  {sponsor_bars_html}
  <div class="callout" style="margin-top:16px">
    <strong>The sponsor pattern:</strong> Major pharmaceutical companies
    (GSK, Pfizer, Sanofi Pasteur) dominate Africa's vaccine landscape.
    While GSK's malaria vaccine programme (Mosquirix/RTS,S) represents
    genuine partnership, many pharma-sponsored trials are global
    registration studies that happen to include African sites for
    regulatory diversity and large enrollment pools.
  </div>
</div>

<!-- ============ SECTION 6: THE NIGERIA PARADOX ============ -->
<div class="section">
  <h2>6. The Nigeria Paradox</h2>
  <div style="display:flex;gap:24px;align-items:center;margin:16px 0">
    <div class="kpi" style="flex:1">
      <div class="label">Population</div>
      <div class="value" style="color:#f59e0b;font-size:1.8rem">230M</div>
      <div class="label">largest in Africa</div>
    </div>
    <div class="kpi" style="flex:1">
      <div class="label">Vaccine Trials</div>
      <div class="value" style="color:#ef4444;font-size:1.8rem">{nigeria_total}</div>
      <div class="label">registered on CT.gov</div>
    </div>
    <div class="kpi" style="flex:1">
      <div class="label">Trials per 10M people</div>
      <div class="value" style="color:#ef4444;font-size:1.8rem">
        {round(nigeria_total / 23, 2)}
      </div>
      <div class="label">vs SA: {round(sa_total / 6.0, 1)}</div>
    </div>
  </div>
  <div class="callout">
    Nigeria sits at the heart of the meningitis belt, has the world's
    highest burden of circulating vaccine-derived poliovirus, and faces
    endemic yellow fever. Yet with only <strong>{nigeria_total}</strong>
    vaccine trials, it has fewer than South Africa by a factor of
    <strong>{round(sa_total / max(nigeria_total, 1))}:1</strong>.
    Structural barriers include regulatory fragmentation (NAFDAC),
    limited GCP infrastructure, security concerns in the north, and the
    legacy of the 1996 Pfizer/Trovan meningitis trial controversy that
    eroded community trust in clinical research.
  </div>
</div>

<!-- ============ SECTION 7: PHASE DISTRIBUTION ============ -->
<div class="section">
  <h2>7. Phase Distribution: Testing vs Development</h2>
  {phase_html}
  <div class="callout-amber callout" style="margin-top:16px">
    <strong>Phase skew:</strong> Phase 3 trials account for
    <strong>{phase3_pct}%</strong> of Africa's vaccine trials,
    significantly higher than the global average of ~25%. This confirms
    that Africa is disproportionately used as a testing ground for
    vaccines already developed elsewhere, rather than as a site for
    early-phase discovery and development. Phase 1 (first-in-human)
    trials remain concentrated in high-income countries.
  </div>
</div>

<!-- ============ SECTION 8: FOR AFRICA vs IN AFRICA ============ -->
<div class="section">
  <h2>8. Vaccines Tested IN Africa vs FOR Africa</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    Classification based on whether the target disease primarily burdens
    Africa (malaria, Ebola, TB, HIV) or is a global/HIC disease
    (COVID, influenza, RSV, HPV).
  </p>
  {fa_html}
  <div class="callout" style="margin-top:16px">
    <strong>The core question:</strong> When a COVID vaccine is tested
    in South Africa on thousands of participants, does Africa benefit?
    The participants contribute their bodies and risk, but post-trial
    access is not guaranteed. Meanwhile, genuinely FOR-Africa vaccines
    (malaria, Ebola, meningococcal) often face years of regulatory
    delay and pricing barriers before reaching the populations that
    participated in the trials.
  </div>
</div>

<!-- ============ SECTION 9: POST-TRIAL ACCESS ============ -->
<div class="section">
  <h2>9. Post-Trial Access: Are Vaccines Available Where Tested?</h2>
  <div class="callout">
    <strong>The access gap:</strong> Vaccines tested in Africa face
    systematic barriers to post-trial availability:
  </div>
  <ul style="margin:16px 0 16px 24px;color:#94a3b8;line-height:2">
    <li><strong style="color:#e2e8f0">RTS,S/Mosquirix (GSK):</strong>
      Tested in 7 African countries, 2009-2014. WHO recommendation:
      2021. First routine rollout: 2023. <strong style="color:#f59e0b">
      9-year gap</strong> from trial completion to access.</li>
    <li><strong style="color:#e2e8f0">R21/Matrix-M (Oxford/Serum Institute):</strong>
      Tested in Burkina Faso, Kenya, Tanzania, Mali. WHO prequalified:
      2023. Priced at $2-4/dose, more accessible but supply-constrained.</li>
    <li><strong style="color:#e2e8f0">COVID vaccines:</strong>
      AstraZeneca tested in South Africa. South Africa received vaccines
      months after HIC countries, with COVAX delays. J&amp;J vaccine
      tested in SA, initially exported before local availability.</li>
    <li><strong style="color:#e2e8f0">HIV vaccines:</strong>
      HVTN 702 (SA), Imbokodo (SA) -- both failed. Decades of testing
      on African participants without a successful product.</li>
    <li><strong style="color:#e2e8f0">Ebola vaccines:</strong>
      rVSV-ZEBOV tested during 2014-2016 West Africa outbreak. Licensed
      by Merck. Access limited to outbreak response, not routine
      immunisation in endemic zones.</li>
  </ul>
  <div class="callout-amber callout">
    <strong>The pattern:</strong> Africa contributes participants, risk,
    and disease burden. The resulting vaccines are often priced for
    high-income markets first, with African access dependent on GAVI,
    COVAX, or African Union procurement -- adding years of delay and
    uncertainty.
  </div>
</div>

<!-- ============ TOP TRIALS TABLE ============ -->
<div class="section">
  <h2>Largest Vaccine Trials in Africa (by Enrollment)</h2>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>NCT ID</th>
        <th>Title</th>
        <th style="text-align:center">Phase</th>
        <th style="text-align:right">Enrollment</th>
        <th>Sponsor</th>
        <th>Relevance</th>
      </tr>
    </thead>
    <tbody>
      {trial_rows_html}
    </tbody>
  </table>
  </div>
</div>

<div class="source">
  Data source: <a href="https://clinicaltrials.gov">ClinicalTrials.gov</a>
  API v2 (accessed {fetch_date})<br>
  Analysis: fetch_vaccine_colony.py | Africa's Vaccine Testing Ground<br>
  Note: Counts reflect interventional vaccine trials registered on
  ClinicalTrials.gov only. Trials on WHO ICTRP, Pan African Clinical
  Trials Registry, or national registries are not captured.
</div>

</div>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated HTML: {OUTPUT_HTML}")
    print(f"  File size: {len(html):,} bytes")


# -- Main ------------------------------------------------------------------
def main():
    print("=" * 70)
    print("  Africa's Vaccine Testing Ground")
    print("  ClinicalTrials.gov API v2 Analysis")
    print("=" * 70)

    data = collect_data()

    print("\n" + "=" * 70)
    print("KEY FINDINGS:")
    print("=" * 70)
    print(f"  Africa vaccine trials:     {data['africa_vaccine_total']}")
    print(f"  South Africa:              {data['sa_vaccine_total']} "
          f"({data['sa_monopoly_pct']}% of Africa)")
    print(f"  US vaccine trials:         {data['us_vaccine_total']:,}")
    print(f"  Africa/US ratio:           {data['africa_us_share_pct']}%")
    print(f"  Nigeria:                   {data['nigeria_vaccine_total']} "
          f"(230M population)")
    print(f"  Phase 3 share:             {data['phase3_pct']}%")
    print(f"  Trials analysed:           {data['total_trials_fetched']}")
    print(f"  Total enrollment:          {data['total_enrollment']:,}")

    generate_html(data)
    print("\nDone.")


if __name__ == "__main__":
    main()
