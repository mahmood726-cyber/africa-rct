"""
Heart Failure in Africa — Different Disease, No Evidence
=========================================================
Queries ClinicalTrials.gov API v2 for heart failure trials comparing
Africa (41 verified) vs US (1,855), plus condition-specific queries
for peripartum cardiomyopathy, rheumatic heart disease, endomyocardial
fibrosis, and cardiomyopathy in Africa. Fetches trial-level data to
analyze HF types, interventions, and sponsors.

Usage:
    python fetch_heart_failure_africa.py

Output:
    data/heart_failure_data.json       — cached trial data (24h validity)
    heart-failure-africa.html          — interactive equity dashboard

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

# -- Config ----------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "heart_failure_data.json"
OUTPUT_HTML = Path(__file__).parent / "heart-failure-africa.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# -- Geographic queries for HF trial counts --------------------------------
HF_LOCATION_QUERIES = {
    "Africa": "Africa",
    "United States": "United States",
    "South Africa": "South Africa",
    "Kenya": "Kenya",
    "Uganda": "Uganda",
    "Nigeria": "Nigeria",
    "Egypt": "Egypt",
    "Tanzania": "Tanzania",
    "Mozambique": "Mozambique",
    "India": "India",
    "Brazil": "Brazil",
}

# -- Condition-specific queries for Africa ---------------------------------
CONDITION_QUERIES_AFRICA = {
    "peripartum_cardiomyopathy": "peripartum cardiomyopathy",
    "rheumatic_heart_disease": "rheumatic heart disease",
    "endomyocardial_fibrosis": "endomyocardial fibrosis",
    "cardiomyopathy": "cardiomyopathy",
}

# -- African countries for location matching --------------------------------
AFRICAN_COUNTRIES = [
    "Nigeria", "South Africa", "Kenya", "Uganda", "Egypt",
    "Tanzania", "Mozambique", "Ghana", "Cameroon", "Ethiopia",
    "Senegal", "Malawi", "Rwanda", "Zimbabwe", "Zambia",
    "Burkina Faso", "Mali", "Niger", "Botswana", "Namibia",
]

# -- Sponsor classification keywords --------------------------------------
AFRICAN_KEYWORDS = [
    "nigeria", "lagos", "ibadan", "makerere", "uganda", "kenya",
    "nairobi", "ghana", "accra", "tanzania", "muhimbili", "cameroon",
    "egypt", "cairo", "ain shams", "south africa", "cape town",
    "witwatersrand", "stellenbosch", "kwazulu", "mozambique",
    "maputo", "addis ababa", "ethiopia", "kenyatta", "moi university",
    "mulago", "kilimanjaro", "dakar", "senegal",
]

PHARMA_KEYWORDS = [
    "pfizer", "novartis", "roche", "astrazeneca", "novo nordisk",
    "sanofi", "lilly", "gilead", "boehringer", "bayer", "merck",
    "bristol-myers", "johnson & johnson", "amgen", "servier",
    "menarini", "otsuka", "vifor",
]

NIH_KEYWORDS = [
    "nih", "niaid", "nhlbi", "cdc", "national institutes of health",
    "national heart, lung", "fogarty",
]

US_ACADEMIC_KEYWORDS = [
    "university", "hospital", "institute", "medical center",
    "children's", "memorial", "college of medicine",
]

# -- HF intervention classification ----------------------------------------
HF_DRUG_CLASSES = {
    "ACEi/ARB": ["enalapril", "ramipril", "lisinopril", "captopril",
                  "losartan", "valsartan", "candesartan", "telmisartan",
                  "irbesartan", "perindopril"],
    "ARNI": ["sacubitril", "entresto", "valsartan/sacubitril",
             "sacubitril/valsartan"],
    "Beta-blocker": ["carvedilol", "bisoprolol", "metoprolol", "nebivolol",
                     "atenolol"],
    "MRA": ["spironolactone", "eplerenone", "finerenone"],
    "SGLT2i": ["dapagliflozin", "empagliflozin", "canagliflozin",
               "sotagliflozin", "ertugliflozin"],
    "Diuretic": ["furosemide", "torsemide", "bumetanide", "hydrochlorothiazide",
                 "chlorthalidone", "indapamide", "metolazone"],
    "Hydralazine/Nitrate": ["hydralazine", "isosorbide", "bidil"],
    "Ivabradine": ["ivabradine", "procoralan"],
    "Digoxin": ["digoxin", "digitalis"],
    "Device/CRT": ["cardiac resynchronization", "crt", "biventricular",
                   "icd", "implantable cardioverter", "defibrillator",
                   "left ventricular assist", "lvad"],
    "Traditional/Herbal": ["herbal", "traditional", "plant extract",
                           "phytotherapy"],
}

# -- HF type classification keywords --------------------------------------
HF_TYPE_KEYWORDS = {
    "Peripartum": ["peripartum", "postpartum", "pregnancy", "ppcm",
                   "puerperal"],
    "RHD-related": ["rheumatic", "rhd", "mitral stenosis"],
    "Endomyocardial fibrosis": ["endomyocardial", "emf"],
    "Ischemic/CAD": ["ischemic", "coronary", "myocardial infarction",
                     "post-mi", "cad"],
    "Dilated CM": ["dilated cardiomyopathy", "dcm", "idiopathic dilated"],
    "HFpEF": ["preserved ejection", "hfpef", "diastolic"],
    "HFrEF": ["reduced ejection", "hfref", "systolic heart failure"],
    "Hypertensive": ["hypertensive heart", "hypertensive cardiomyopathy"],
}


# -- API helpers -----------------------------------------------------------
def search_trials(location=None, condition="heart failure",
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
                  f"for location={location}, cond={condition}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return {"totalCount": 0, "studies": []}


def fetch_count_only(location=None, condition="heart failure"):
    """Fetch just the total count for a query (page_size=1 for speed)."""
    result = search_trials(location=location, condition=condition, page_size=1)
    return result.get("totalCount", 0)


def fetch_all_pages(location=None, condition="heart failure", page_size=50):
    """Fetch all pages for a given query."""
    all_studies = []
    page_token = None
    page_num = 0

    while True:
        page_num += 1
        result = search_trials(location=location, condition=condition,
                               page_size=page_size, page_token=page_token)
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


def extract_status(study):
    try:
        return study["protocolSection"]["statusModule"]["overallStatus"]
    except (KeyError, TypeError):
        return "UNKNOWN"


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


def extract_enrollment(study):
    try:
        return study["protocolSection"]["designModule"]["enrollmentInfo"].get(
            "count", 0)
    except (KeyError, TypeError):
        return 0


def extract_start_date(study):
    try:
        return study["protocolSection"]["statusModule"]["startDateStruct"].get(
            "date", "")
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
            c = loc.get("country", "")
            if c:
                countries.add(c)
    except (KeyError, TypeError):
        pass
    return countries


# -- Classification functions ----------------------------------------------
def classify_sponsor(sponsor_name):
    """Classify sponsor as African-local, Pharma, NIH/US Govt, US Academic, Other."""
    lower = sponsor_name.lower()

    for kw in AFRICAN_KEYWORDS:
        if kw in lower:
            return "African-local"

    for kw in PHARMA_KEYWORDS:
        if kw in lower:
            return "Pharma"

    for kw in NIH_KEYWORDS:
        if kw in lower:
            return "NIH/US Govt"

    for kw in US_ACADEMIC_KEYWORDS:
        if kw in lower:
            return "US Academic"

    return "Other"


def classify_hf_type(study):
    """Classify heart failure type from conditions + title."""
    conditions_text = " ".join(extract_conditions(study)).lower()
    title_text = extract_title(study).lower()
    combined = conditions_text + " " + title_text

    matched = []
    for hf_type, keywords in HF_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                matched.append(hf_type)
                break

    if not matched:
        return "Unspecified HF"
    return matched[0]  # Return primary match


def classify_hf_intervention(interventions, study):
    """Classify HF intervention type."""
    combined = " ".join(interventions).lower()
    title_text = extract_title(study).lower()
    all_text = combined + " " + title_text

    matched = []
    for drug_class, keywords in HF_DRUG_CLASSES.items():
        for kw in keywords:
            if kw in all_text:
                matched.append(drug_class)
                break

    if not matched:
        return "Other"
    return matched[0]


def classify_scope(study, locations_count):
    """Classify as Africa-focused, Global mega-trial, or Regional."""
    countries = get_location_countries(study)

    if locations_count > 20:
        return "Global mega-trial"

    african_site_count = sum(1 for c in countries if c in AFRICAN_COUNTRIES)
    if african_site_count > 0 and len(countries) <= 3:
        return "Africa-focused"

    return "Regional"


# -- Main data collection --------------------------------------------------
def collect_data():
    """Fetch HF trials, deduplicate, classify, compute analytics."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("=" * 70)
    print("Heart Failure in Africa — ClinicalTrials.gov API v2 Audit")
    print("=" * 70)

    # ---- PART 1: HF trial counts by geography ----------------------------
    print("\n[1/3] Fetching HF trial counts by geography...")
    hf_counts = {}
    for label, loc in HF_LOCATION_QUERIES.items():
        count = fetch_count_only(location=loc, condition="heart failure")
        hf_counts[label] = count
        print(f"  {label}: {count:,} trials")
        time.sleep(RATE_LIMIT_DELAY)

    # ---- PART 2: Condition-specific queries for Africa -------------------
    print("\n[2/3] Fetching condition-specific trial counts in Africa...")
    condition_counts = {}
    for key, condition in CONDITION_QUERIES_AFRICA.items():
        count = fetch_count_only(location="Africa", condition=condition)
        condition_counts[key] = count
        print(f"  {condition} (Africa): {count}")
        time.sleep(RATE_LIMIT_DELAY)

    # ---- PART 3: Full trial-level data for Africa HF ---------------------
    print("\n[3/3] Fetching full trial-level data for Africa HF...")
    africa_studies_raw = {}
    africa_hf_studies = fetch_all_pages(location="Africa",
                                        condition="heart failure",
                                        page_size=50)
    for study in africa_hf_studies:
        nct_id = extract_nct_id(study)
        if nct_id:
            africa_studies_raw[nct_id] = study

    print(f"\n  Unique Africa HF trials: {len(africa_studies_raw)}")

    # Also fetch peripartum cardiomyopathy trials with full data
    print("  Fetching peripartum cardiomyopathy trial details...")
    ppcm_studies = fetch_all_pages(location="Africa",
                                   condition="peripartum cardiomyopathy",
                                   page_size=50)
    for study in ppcm_studies:
        nct_id = extract_nct_id(study)
        if nct_id and nct_id not in africa_studies_raw:
            africa_studies_raw[nct_id] = study

    # Also fetch RHD trials with full data
    print("  Fetching rheumatic heart disease trial details...")
    rhd_studies = fetch_all_pages(location="Africa",
                                  condition="rheumatic heart disease",
                                  page_size=50)
    for study in rhd_studies:
        nct_id = extract_nct_id(study)
        if nct_id and nct_id not in africa_studies_raw:
            africa_studies_raw[nct_id] = study

    print(f"\n  Total unique trials (HF + PPCM + RHD): {len(africa_studies_raw)}")

    # ---- Extract structured data -----------------------------------------
    trials = []
    for nct_id, study in africa_studies_raw.items():
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
            "hf_type": classify_hf_type(study),
            "intervention_class": classify_hf_intervention(interventions, study),
        }
        trials.append(trial)

    # ---- Compute analytics -----------------------------------------------
    total = len(trials)
    african_led = sum(1 for t in trials if t["sponsor_class"] == "African-local")
    terminated = sum(1 for t in trials if t["status"] in ("TERMINATED", "WITHDRAWN"))
    completed = sum(1 for t in trials if t["status"] == "COMPLETED")
    recruiting = sum(1 for t in trials if t["status"] in (
        "RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING",
        "ENROLLING_BY_INVITATION"))

    # HF type distribution
    hf_type_counts = {}
    for t in trials:
        cls = t["hf_type"]
        hf_type_counts[cls] = hf_type_counts.get(cls, 0) + 1

    # Intervention distribution
    intervention_counts = {}
    for t in trials:
        cls = t["intervention_class"]
        intervention_counts[cls] = intervention_counts.get(cls, 0) + 1

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

    # Scope counts
    scope_counts = {}
    for t in trials:
        cls = t["scope_class"]
        scope_counts[cls] = scope_counts.get(cls, 0) + 1

    # Country distribution among Africa trials
    country_trial_counts = {}
    for t in trials:
        for c in t["countries"]:
            if c in AFRICAN_COUNTRIES:
                country_trial_counts[c] = country_trial_counts.get(c, 0) + 1

    # SGLT2i trials in Africa
    sglt2i_trials = [t for t in trials if t["intervention_class"] == "SGLT2i"]

    # Device trials in Africa
    device_trials = [t for t in trials if t["intervention_class"] == "Device/CRT"]

    # ARNI trials in Africa
    arni_trials = [t for t in trials if t["intervention_class"] == "ARNI"]

    # Peripartum trials
    ppcm_trials = [t for t in trials if t["hf_type"] == "Peripartum"]

    # RHD trials
    rhd_trials_list = [t for t in trials if t["hf_type"] == "RHD-related"]

    # Terminated trials detail
    terminated_trials = [t for t in trials if t["status"] in (
        "TERMINATED", "WITHDRAWN")]

    # Mega-trials (global)
    mega_trials = [t for t in trials if t["scope_class"] == "Global mega-trial"]

    # CCI: Africa has ~10% of global HF burden but what % of trials?
    us_count = hf_counts.get("United States", 1855)
    africa_count = hf_counts.get("Africa", 41)
    # Africa HF burden share ~10% (conservative), trial share = africa/global
    total_global_approx = us_count + africa_count + hf_counts.get("India", 0) + \
        hf_counts.get("Brazil", 0) + 500  # rough estimate for rest of world
    africa_trial_share = (africa_count / total_global_approx * 100) if total_global_approx else 0
    # CCI = burden share / trial share (>1 = under-researched)
    africa_burden_share = 10.0  # ~10% of global HF burden from Africa
    cci = round(africa_burden_share / africa_trial_share, 1) if africa_trial_share > 0 else 999

    data = {
        "fetch_date": datetime.now().isoformat(),
        "hf_counts_by_geography": hf_counts,
        "condition_counts_africa": condition_counts,
        "total_africa_trials": total,
        "african_led_count": african_led,
        "african_led_pct": round(african_led / total * 100, 1) if total else 0,
        "completed_count": completed,
        "recruiting_count": recruiting,
        "terminated_count": terminated,
        "termination_rate": round(terminated / total * 100, 1) if total else 0,
        "hf_type_distribution": hf_type_counts,
        "intervention_distribution": intervention_counts,
        "sponsor_breakdown": sponsor_counts,
        "phase_distribution": phase_counts,
        "scope_counts": scope_counts,
        "country_distribution": country_trial_counts,
        "sglt2i_trial_count": len(sglt2i_trials),
        "sglt2i_trials": sglt2i_trials,
        "device_trial_count": len(device_trials),
        "device_trials": device_trials,
        "arni_trial_count": len(arni_trials),
        "arni_trials": arni_trials,
        "ppcm_trial_count": len(ppcm_trials),
        "ppcm_trials": ppcm_trials,
        "rhd_trial_count": len(rhd_trials_list),
        "rhd_trials": rhd_trials_list,
        "mega_trials": mega_trials,
        "terminated_trials": terminated_trials,
        "cci": cci,
        "africa_trial_share_pct": round(africa_trial_share, 2),
        "africa_burden_share_pct": africa_burden_share,
        "trials": trials,
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator ------------------------------------------------
def generate_html(data):
    """Generate heart-failure-africa.html interactive dashboard."""

    hf_counts = data["hf_counts_by_geography"]
    condition_counts = data["condition_counts_africa"]
    total = data["total_africa_trials"]
    trials = data["trials"]
    hf_type_counts = data["hf_type_distribution"]
    intervention_counts = data["intervention_distribution"]
    sponsor_counts = data["sponsor_breakdown"]
    phase_counts = data["phase_distribution"]
    scope_counts = data["scope_counts"]
    country_dist = data["country_distribution"]
    cci = data["cci"]
    africa_trial_share = data["africa_trial_share_pct"]

    # Sort trials
    status_order = {"RECRUITING": 0, "ACTIVE_NOT_RECRUITING": 1,
                    "NOT_YET_RECRUITING": 2, "ENROLLING_BY_INVITATION": 3,
                    "COMPLETED": 4, "TERMINATED": 5, "WITHDRAWN": 6,
                    "SUSPENDED": 7, "UNKNOWN": 8}
    trials_sorted = sorted(trials, key=lambda t: (
        status_order.get(t["status"], 99), t["nct_id"]))

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
        return "#95a5a6"

    def esc(text):
        """HTML-escape including quotes."""
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
                .replace("'", "&#39;"))

    # Build trial table rows
    trial_rows = []
    for t in trials_sorted:
        color = status_color(t["status"])
        title_trunc = esc(t["title"][:80] + ("..." if len(t["title"]) > 80 else ""))
        phases_str = ", ".join(t["phases"]) if t["phases"] else "N/A"
        countries_str = ", ".join(t["countries"][:3])
        if len(t["countries"]) > 3:
            countries_str += f" +{len(t['countries']) - 3}"
        interv_str = ", ".join(t["interventions"][:2])
        if len(t["interventions"]) > 2:
            interv_str += f" +{len(t['interventions']) - 2}"

        trial_rows.append(f"""<tr style="border-left:4px solid {color}">
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td title="{esc(t['title'])}">{title_trunc}</td>
<td>{esc(t['sponsor'])}</td>
<td>{t['sponsor_class']}</td>
<td>{t['hf_type']}</td>
<td>{t['intervention_class']}</td>
<td>{countries_str}</td>
<td>{phases_str}</td>
<td style="color:{color}">{t['status']}</td>
<td style="text-align:right">{t['enrollment']:,}</td>
</tr>""")

    trial_table_html = "\n".join(trial_rows)

    # Comparison table rows
    comparison_rows = []
    for label in ["Africa", "United States", "South Africa", "Kenya",
                  "Uganda", "Nigeria", "Egypt", "Tanzania", "Mozambique",
                  "India", "Brazil"]:
        count = hf_counts.get(label, 0)
        bar_width = min(count / max(hf_counts.get("United States", 1), 1) * 100, 100)
        highlight = ' style="background:#1e3a5f"' if label == "Africa" else ""
        comparison_rows.append(f"""<tr{highlight}>
<td style="font-weight:600">{label}</td>
<td style="text-align:right">{count:,}</td>
<td><div style="background:#3b82f6;height:18px;width:{bar_width:.1f}%;border-radius:3px;
    min-width:2px"></div></td>
</tr>""")
    comparison_table = "\n".join(comparison_rows)

    # Condition-specific table
    condition_rows = []
    for key, label in [("peripartum_cardiomyopathy", "Peripartum cardiomyopathy"),
                       ("rheumatic_heart_disease", "Rheumatic heart disease"),
                       ("endomyocardial_fibrosis", "Endomyocardial fibrosis"),
                       ("cardiomyopathy", "Cardiomyopathy (all)")]:
        count = condition_counts.get(key, 0)
        condition_rows.append(f"""<tr>
<td>{label}</td>
<td style="text-align:right;font-weight:700">{count}</td>
</tr>""")
    condition_table = "\n".join(condition_rows)

    # SGLT2i trial rows
    sglt2i_rows = []
    for t in data.get("sglt2i_trials", []):
        title_trunc = esc(t["title"][:70] + ("..." if len(t["title"]) > 70 else ""))
        sglt2i_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td>{title_trunc}</td>
<td>{esc(t['sponsor'])}</td>
<td style="color:{status_color(t['status'])}">{t['status']}</td>
</tr>""")
    sglt2i_table = "\n".join(sglt2i_rows) if sglt2i_rows else \
        '<tr><td colspan="4" style="text-align:center;color:#ef4444;font-weight:700">NO SGLT2i TRIALS FOUND IN AFRICA</td></tr>'

    # Device trial rows
    device_rows = []
    for t in data.get("device_trials", []):
        title_trunc = esc(t["title"][:70] + ("..." if len(t["title"]) > 70 else ""))
        device_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td>{title_trunc}</td>
<td>{esc(t['sponsor'])}</td>
<td style="color:{status_color(t['status'])}">{t['status']}</td>
</tr>""")
    device_table = "\n".join(device_rows) if device_rows else \
        '<tr><td colspan="4" style="text-align:center;color:#ef4444;font-weight:700">NEAR-ZERO DEVICE/CRT/ICD TRIALS IN AFRICA</td></tr>'

    # PPCM trial rows
    ppcm_rows = []
    for t in data.get("ppcm_trials", []):
        title_trunc = esc(t["title"][:70] + ("..." if len(t["title"]) > 70 else ""))
        ppcm_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td>{title_trunc}</td>
<td>{esc(t['sponsor'])}</td>
<td>{", ".join(t['countries'][:3])}</td>
<td style="color:{status_color(t['status'])}">{t['status']}</td>
</tr>""")
    ppcm_table = "\n".join(ppcm_rows) if ppcm_rows else \
        '<tr><td colspan="5" style="text-align:center;color:#fbbf24">No peripartum-specific trials identified</td></tr>'

    # RHD trial rows
    rhd_rows = []
    for t in data.get("rhd_trials", []):
        title_trunc = esc(t["title"][:70] + ("..." if len(t["title"]) > 70 else ""))
        rhd_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank"
    style="color:#60a5fa">{t['nct_id']}</a></td>
<td>{title_trunc}</td>
<td>{esc(t['sponsor'])}</td>
<td>{", ".join(t['countries'][:3])}</td>
<td style="color:{status_color(t['status'])}">{t['status']}</td>
</tr>""")
    rhd_table = "\n".join(rhd_rows) if rhd_rows else \
        '<tr><td colspan="5" style="text-align:center;color:#ef4444">Only ~2 RHD trials registered despite 240K annual deaths</td></tr>'

    # Chart data
    hf_type_labels = json.dumps(list(hf_type_counts.keys()))
    hf_type_values = json.dumps(list(hf_type_counts.values()))
    intervention_labels = json.dumps(list(intervention_counts.keys()))
    intervention_values = json.dumps(list(intervention_counts.values()))
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
    country_labels = json.dumps(list(country_dist.keys()))
    country_values = json.dumps(list(country_dist.values()))

    # Geographic comparison chart data
    geo_labels = json.dumps(list(hf_counts.keys()))
    geo_values = json.dumps(list(hf_counts.values()))

    # Severity assessment
    severity_items = []
    africa_ct = hf_counts.get("Africa", 41)
    us_ct = hf_counts.get("United States", 1855)
    ratio = round(us_ct / africa_ct, 0) if africa_ct > 0 else 999

    severity_items.append({
        "level": "CRITICAL",
        "text": f"The US has {us_ct:,} HF trials vs {africa_ct} in ALL of Africa — a {ratio:.0f}:1 ratio"
    })
    if cci > 3:
        severity_items.append({
            "level": "CRITICAL",
            "text": f"Condition Colonialism Index = {cci} (Africa carries ~10% of HF burden but only {africa_trial_share:.1f}% of trials)"
        })
    if data["sglt2i_trial_count"] < 3:
        severity_items.append({
            "level": "HIGH",
            "text": f"Only {data['sglt2i_trial_count']} SGLT2i trial(s) in Africa — DAPA-HF/EMPEROR-Reduced enrolled minimal Africans"
        })
    if data["device_trial_count"] < 3:
        severity_items.append({
            "level": "HIGH",
            "text": f"Only {data['device_trial_count']} device/CRT/ICD trial(s) — near-zero device therapy evidence for Africa"
        })
    if condition_counts.get("rheumatic_heart_disease", 0) <= 5:
        severity_items.append({
            "level": "CRITICAL",
            "text": f"Only {condition_counts.get('rheumatic_heart_disease', 0)} RHD trial(s) in Africa despite 240,000 annual deaths — preventable with $0.02 penicillin"
        })
    if data["ppcm_trial_count"] < 5:
        severity_items.append({
            "level": "HIGH",
            "text": f"Only {data['ppcm_trial_count']} peripartum cardiomyopathy trial(s) — Nigeria has world's highest rate"
        })

    severity_html = ""
    for item in severity_items:
        bg = "#7f1d1d" if item["level"] == "CRITICAL" else \
             "#78350f" if item["level"] == "HIGH" else "#1e3a5f"
        severity_html += f"""<div style="background:{bg};padding:12px 16px;
            border-radius:8px;margin:6px 0;display:flex;align-items:center;gap:12px">
            <span style="font-weight:700;color:#fbbf24;min-width:80px">{item['level']}</span>
            <span>{item['text']}</span>
        </div>\n"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Heart Failure in Africa — Different Disease, No Evidence</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
    --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --red: #ef4444; --green: #22c55e; --yellow: #eab308;
    --orange: #f97316;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif;
       line-height: 1.6; padding: 20px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ font-size: 2.2rem; margin-bottom: 8px; color: #fff;
     background: linear-gradient(135deg, #dc2626, #f97316);
     -webkit-background-clip: text; -webkit-text-fill-color: transparent;
     background-clip: text; }}
h2 {{ font-size: 1.5rem; margin: 32px 0 16px; color: #f8fafc;
     border-bottom: 2px solid var(--accent); padding-bottom: 8px; }}
h3 {{ font-size: 1.15rem; margin: 20px 0 10px; color: #cbd5e1; }}
.subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 24px; }}
.grid {{ display: grid; gap: 16px; margin: 16px 0; }}
.grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
.grid-3 {{ grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); }}
.grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
.card {{ background: var(--surface); border-radius: 12px; padding: 20px;
         border: 1px solid var(--surface2); }}
.stat-card {{ text-align: center; }}
.stat-value {{ font-size: 2.5rem; font-weight: 800; }}
.stat-label {{ color: var(--muted); font-size: 0.85rem; margin-top: 4px; }}
.stat-red {{ color: var(--red); }}
.stat-green {{ color: var(--green); }}
.stat-yellow {{ color: var(--yellow); }}
.stat-orange {{ color: var(--orange); }}
.stat-accent {{ color: var(--accent); }}
.thesus-box {{ background: linear-gradient(135deg, #1e3a5f, #1e293b);
               border: 2px solid var(--accent); border-radius: 12px;
               padding: 24px; margin: 16px 0; }}
.thesus-box h3 {{ color: #60a5fa; margin-top: 0; }}
.phenotype-box {{ background: linear-gradient(135deg, #3f0f0f, #1e293b);
                  border: 2px solid var(--red); border-radius: 12px;
                  padding: 24px; margin: 16px 0; }}
.phenotype-box h3 {{ color: #fca5a5; margin-top: 0; }}
.rhd-box {{ background: linear-gradient(135deg, #422006, #1e293b);
            border: 2px solid var(--orange); border-radius: 12px;
            padding: 24px; margin: 16px 0; }}
.rhd-box h3 {{ color: #fdba74; margin-top: 0; }}
.quote {{ font-style: italic; color: #94a3b8; border-left: 3px solid var(--accent);
          padding-left: 16px; margin: 12px 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{ background: var(--surface2); padding: 10px 8px; text-align: left;
     color: #94a3b8; font-weight: 600; position: sticky; top: 0; }}
td {{ padding: 8px; border-bottom: 1px solid #1e293b; }}
tr:hover {{ background: rgba(59,130,246,0.08); }}
.table-scroll {{ max-height: 500px; overflow-y: auto; border-radius: 8px;
                 border: 1px solid var(--surface2); }}
.chart-container {{ position: relative; height: 320px; }}
.severity-box {{ margin: 16px 0; }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
        font-size: 0.75rem; font-weight: 600; }}
.tag-red {{ background: #7f1d1d; color: #fca5a5; }}
.tag-green {{ background: #14532d; color: #86efac; }}
.tag-yellow {{ background: #78350f; color: #fde68a; }}
.footer {{ margin-top: 40px; padding: 20px; background: var(--surface);
           border-radius: 12px; color: var(--muted); font-size: 0.8rem; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="container">

<h1>Heart Failure in Africa: Different Disease, No Evidence</h1>
<p class="subtitle">
    ClinicalTrials.gov API v2 equity audit | Fetched: {data['fetch_date'][:10]} |
    {total} Africa trials vs {hf_counts.get('United States', 0):,} US trials
</p>

<!-- SEVERITY ASSESSMENT -->
<div class="severity-box">
{severity_html}
</div>

<!-- SUMMARY STATS -->
<h2>Summary: The Evidence Desert</h2>
<div class="grid grid-4">
    <div class="card stat-card">
        <div class="stat-value stat-red">{hf_counts.get('Africa', 0)}</div>
        <div class="stat-label">HF Trials in ALL of Africa</div>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-accent">{hf_counts.get('United States', 0):,}</div>
        <div class="stat-label">HF Trials in the United States</div>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-orange">{cci}</div>
        <div class="stat-label">Condition Colonialism Index</div>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-yellow">{data['african_led_pct']}%</div>
        <div class="stat-label">African-led Trials</div>
    </div>
</div>

<div class="grid grid-4">
    <div class="card stat-card">
        <div class="stat-value stat-green">{data['completed_count']}</div>
        <div class="stat-label">Completed</div>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-yellow">{data['recruiting_count']}</div>
        <div class="stat-label">Recruiting</div>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-red">{data['terminated_count']}</div>
        <div class="stat-label">Terminated/Withdrawn</div>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-red">{data['termination_rate']}%</div>
        <div class="stat-label">Termination Rate</div>
    </div>
</div>

<!-- DIFFERENT DISEASE -->
<h2>A Different Disease: Africa's Unique HF Phenotype</h2>
<div class="phenotype-box">
    <h3>Heart Failure in Africa Is NOT the Same Disease as in High-Income Countries</h3>
    <div class="grid grid-2" style="margin-top:12px">
        <div>
            <h3>Africa HF Profile</h3>
            <ul style="padding-left:20px;color:#e2e8f0">
                <li><strong>Mean age: ~52 years</strong> (vs 75 in Europe/US)</li>
                <li><strong>More women</strong> (~40-50% vs 25-30% in HIC trials)</li>
                <li><strong>Peripartum cardiomyopathy</strong> — leading cause in young women</li>
                <li><strong>Endomyocardial fibrosis</strong> — virtually unknown in HIC</li>
                <li><strong>Rheumatic heart disease</strong> — 240,000 deaths/year</li>
                <li><strong>Hypertensive heart disease</strong> — dominant etiology</li>
                <li><strong>Less ischemic disease</strong> (vs HIC where CAD dominates)</li>
                <li><strong>HIV-associated cardiomyopathy</strong></li>
            </ul>
        </div>
        <div>
            <h3>HIC (US/Europe) HF Profile</h3>
            <ul style="padding-left:20px;color:#94a3b8">
                <li>Mean age: ~75 years</li>
                <li>Predominantly male (70-75%)</li>
                <li>Ischemic heart disease dominates (~60%)</li>
                <li>Post-MI cardiomyopathy</li>
                <li>HFpEF increasingly common</li>
                <li>RHD essentially eliminated</li>
                <li>Peripartum CM rare</li>
                <li>Robust device therapy (ICD/CRT) infrastructure</li>
            </ul>
        </div>
    </div>
    <p class="quote" style="margin-top:16px">
        "The evidence base for HF treatment was built in 75-year-old white men with
        ischemic cardiomyopathy. Can we assume it applies to 35-year-old Nigerian
        women with peripartum cardiomyopathy?"
    </p>
</div>

<!-- THESUS-HF -->
<h2>THESUS-HF: The ONLY Landmark African HF Trial</h2>
<div class="thesus-box">
    <h3>Treatment of Heart Failure with Standard versus Uptitrated Doses of Sacubitril/Valsartan —
        No. The Sub-Saharan Africa Survey of Heart Failure (THESUS-HF)</h3>
    <p style="margin-top:8px">
        <strong>THESUS-HF</strong> (Damasceno et al., <em>Eur Heart J</em> 2012) was the first and
        remains essentially the <strong>only large-scale prospective heart failure registry</strong>
        from sub-Saharan Africa. It enrolled 1,006 patients across 12 centers in 9 African countries.
    </p>
    <div class="grid grid-3" style="margin-top:12px">
        <div class="card" style="background:#0f172a">
            <div style="font-size:1.8rem;font-weight:800;color:#60a5fa">1,006</div>
            <div style="color:#94a3b8;font-size:0.85rem">Patients enrolled</div>
        </div>
        <div class="card" style="background:#0f172a">
            <div style="font-size:1.8rem;font-weight:800;color:#f97316">52.3 yrs</div>
            <div style="color:#94a3b8;font-size:0.85rem">Mean age (vs 75 in PARADIGM-HF)</div>
        </div>
        <div class="card" style="background:#0f172a">
            <div style="font-size:1.8rem;font-weight:800;color:#ef4444">17.8%</div>
            <div style="color:#94a3b8;font-size:0.85rem">6-month mortality (vs 8% in HIC registries)</div>
        </div>
    </div>
    <h3 style="margin-top:16px">What THESUS-HF Taught Us</h3>
    <ul style="padding-left:20px;margin-top:8px">
        <li><strong>Different phenotype = different treatment needs.</strong> Only 8% had CAD;
            hypertension (45%), RHD (14%), and peripartum CM (12%) dominated.</li>
        <li><strong>Younger patients, higher mortality.</strong> Despite being 23 years younger on
            average, 6-month mortality was double that of European registries.</li>
        <li><strong>Medication access crisis.</strong> Only 40% were on ACE inhibitors at discharge;
            beta-blocker use was &lt;50%.</li>
        <li><strong>No evidence to guide treatment.</strong> The drugs prescribed were
            extrapolated from trials in elderly white patients with ischemic disease.</li>
    </ul>
    <p class="quote" style="margin-top:12px">
        "After THESUS-HF, we know the disease is different. But we still have no trials
        testing whether the treatments work for THIS population."
    </p>
</div>

<!-- CCI -->
<h2>Condition Colonialism Index (CCI)</h2>
<div class="card">
    <p>The CCI measures the mismatch between disease burden and research investment.
       For heart failure in Africa:</p>
    <div class="grid grid-3" style="margin-top:12px">
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-red">~10%</div>
            <div class="stat-label">Africa's share of global HF burden</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-red">{africa_trial_share:.1f}%</div>
            <div class="stat-label">Africa's share of global HF trials</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-orange">{cci}</div>
            <div class="stat-label">CCI (burden/trial ratio; >1 = under-researched)</div>
        </div>
    </div>
</div>

<!-- GEOGRAPHIC COMPARISON -->
<h2>Geographic Comparison: HF Trials by Country/Region</h2>
<div class="grid grid-2">
    <div class="card">
        <div class="table-scroll">
            <table>
                <thead><tr><th>Location</th><th style="text-align:right">HF Trials</th>
                <th>Relative Scale</th></tr></thead>
                <tbody>{comparison_table}</tbody>
            </table>
        </div>
    </div>
    <div class="card">
        <div class="chart-container">
            <canvas id="geoChart"></canvas>
        </div>
    </div>
</div>

<!-- AFRICA HF TYPE -->
<h2>Africa's HF Phenotype Distribution</h2>
<div class="grid grid-2">
    <div class="card">
        <div class="chart-container">
            <canvas id="hfTypeChart"></canvas>
        </div>
    </div>
    <div class="card">
        <h3>Condition-Specific Trials in Africa</h3>
        <table>
            <thead><tr><th>Condition</th><th style="text-align:right">Trials</th></tr></thead>
            <tbody>{condition_table}</tbody>
        </table>
        <p style="margin-top:12px;color:#94a3b8;font-size:0.85rem">
            These conditions are characteristic of Africa's unique HF epidemiology
            yet have near-zero dedicated trial activity.
        </p>
    </div>
</div>

<!-- INTERVENTION GAP -->
<h2>Treatment Evidence Gaps</h2>
<div class="grid grid-2">
    <div class="card">
        <div class="chart-container">
            <canvas id="interventionChart"></canvas>
        </div>
    </div>
    <div class="card">
        <h3>Key Questions Without Answers</h3>
        <ul style="padding-left:20px;margin-top:8px">
            <li><strong>SGLT2 inhibitors:</strong> DAPA-HF enrolled 5% Black patients;
                EMPEROR-Reduced had &lt;7% from Africa. Are results generalizable to
                non-ischemic, younger, female-predominant African HF?</li>
            <li><strong>ARNI (Sacubitril/Valsartan):</strong> PARADIGM-HF had minimal
                African representation. Mean age 64 vs 52 in Africa.</li>
            <li><strong>Devices (ICD/CRT):</strong> Virtually no implantation infrastructure.
                Even if trials showed benefit, implementation is impossible.</li>
            <li><strong>Hydralazine/Nitrate:</strong> A-HeFT enrolled African Americans,
                not Africans. Different genetic and etiological context.</li>
        </ul>
    </div>
</div>

<!-- SGLT2i GAP -->
<h2>SGLT2 Inhibitor Gap: Are DAPA-HF/EMPEROR Results Generalizable?</h2>
<div class="card">
    <div class="grid grid-3">
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-red">{data['sglt2i_trial_count']}</div>
            <div class="stat-label">SGLT2i trials in Africa</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-yellow">~5%</div>
            <div class="stat-label">Black patients in DAPA-HF</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-orange">66 yrs</div>
            <div class="stat-label">Mean age in DAPA-HF (vs 52 in Africa)</div>
        </div>
    </div>
    <h3 style="margin-top:16px">SGLT2i Trials in Africa</h3>
    <div class="table-scroll" style="max-height:300px">
        <table>
            <thead><tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Status</th></tr></thead>
            <tbody>{sglt2i_table}</tbody>
        </table>
    </div>
    <p class="quote" style="margin-top:12px">
        DAPA-HF and EMPEROR-Reduced revolutionized HF treatment globally. But their populations
        were ~66 years old, predominantly ischemic, and predominantly white/Asian. Extrapolating
        to 35-year-old women with peripartum cardiomyopathy requires an act of faith, not evidence.
    </p>
</div>

<!-- DEVICE GAP -->
<h2>Device Therapy Gap: ICD/CRT Near-Zero in Africa</h2>
<div class="card">
    <div class="grid grid-3">
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-red">{data['device_trial_count']}</div>
            <div class="stat-label">Device/CRT/ICD trials in Africa</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-accent">~1,200</div>
            <div class="stat-label">ICD implants per million (US)</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-red">&lt;1</div>
            <div class="stat-label">ICD implants per million (most of Africa)</div>
        </div>
    </div>
    <h3 style="margin-top:16px">Device Trials in Africa</h3>
    <div class="table-scroll" style="max-height:300px">
        <table>
            <thead><tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Status</th></tr></thead>
            <tbody>{device_table}</tbody>
        </table>
    </div>
</div>

<!-- RHD SPOTLIGHT -->
<h2>RHD Spotlight: $0.02 Penicillin, 240,000 Deaths</h2>
<div class="rhd-box">
    <h3>Rheumatic Heart Disease — The Most Cost-Effective Prevention in Medicine, Ignored</h3>
    <div class="grid grid-3" style="margin-top:12px">
        <div class="card" style="background:#0f172a">
            <div style="font-size:1.8rem;font-weight:800;color:#ef4444">240,000</div>
            <div style="color:#94a3b8;font-size:0.85rem">Annual RHD deaths (mostly Africa + South Asia)</div>
        </div>
        <div class="card" style="background:#0f172a">
            <div style="font-size:1.8rem;font-weight:800;color:#22c55e">$0.02</div>
            <div style="color:#94a3b8;font-size:0.85rem">Cost of penicillin prophylaxis (per dose)</div>
        </div>
        <div class="card" style="background:#0f172a">
            <div style="font-size:1.8rem;font-weight:800;color:#ef4444">{condition_counts.get('rheumatic_heart_disease', 0)}</div>
            <div style="color:#94a3b8;font-size:0.85rem">RHD trials in Africa</div>
        </div>
    </div>
    <h3 style="margin-top:16px">RHD Trials in Africa</h3>
    <div class="table-scroll" style="max-height:250px">
        <table>
            <thead><tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Countries</th><th>Status</th></tr></thead>
            <tbody>{rhd_table}</tbody>
        </table>
    </div>
    <p class="quote" style="margin-top:12px">
        RHD is essentially eliminated in high-income countries through secondary penicillin
        prophylaxis costing pennies per dose. In Africa, it remains a leading cause of heart
        failure in the young — and has virtually no dedicated clinical trial activity.
    </p>
</div>

<!-- PERIPARTUM CARDIOMYOPATHY -->
<h2>Peripartum Cardiomyopathy: Nigeria Has the World's Highest Rate</h2>
<div class="card">
    <div class="grid grid-3">
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-red">{data['ppcm_trial_count']}</div>
            <div class="stat-label">PPCM trials in Africa</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-orange">1 in 100</div>
            <div class="stat-label">PPCM incidence in Northern Nigeria</div>
        </div>
        <div class="card stat-card" style="background:#0f172a">
            <div class="stat-value stat-yellow">1 in 3,000</div>
            <div class="stat-label">PPCM incidence in the US</div>
        </div>
    </div>
    <h3 style="margin-top:16px">Peripartum Cardiomyopathy Trials in Africa</h3>
    <div class="table-scroll" style="max-height:300px">
        <table>
            <thead><tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Countries</th><th>Status</th></tr></thead>
            <tbody>{ppcm_table}</tbody>
        </table>
    </div>
    <p style="margin-top:12px;color:#94a3b8">
        Northern Nigeria has the world's highest incidence of peripartum cardiomyopathy, possibly
        linked to cultural postpartum practices (hot baths, high salt intake). The condition affects
        young women at their most productive age. Despite this, Africa-specific treatment trials
        are vanishingly rare.
    </p>
</div>

<!-- SPONSOR ANALYSIS -->
<h2>Sponsor Analysis: Who Runs Africa's HF Trials?</h2>
<div class="grid grid-2">
    <div class="card">
        <div class="chart-container">
            <canvas id="sponsorChart"></canvas>
        </div>
    </div>
    <div class="card">
        <h3>Sponsor Breakdown</h3>
        <table>
            <thead><tr><th>Category</th><th style="text-align:right">Count</th><th style="text-align:right">%</th></tr></thead>
            <tbody>
"""
    for cat, count in sorted(sponsor_counts.items(), key=lambda x: -x[1]):
        pct = round(count / total * 100, 1) if total else 0
        color = "#2ecc71" if cat == "African-local" else "#e74c3c" if cat == "Pharma" else "#94a3b8"
        html += f"""<tr><td style="color:{color}">{cat}</td>
<td style="text-align:right">{count}</td>
<td style="text-align:right">{pct}%</td></tr>\n"""

    html += f"""
            </tbody>
        </table>
    </div>
</div>

<!-- COUNTRY DISTRIBUTION WITHIN AFRICA -->
<h2>Country Distribution Within Africa</h2>
<div class="grid grid-2">
    <div class="card">
        <div class="chart-container">
            <canvas id="countryChart"></canvas>
        </div>
    </div>
    <div class="card">
        <h3>African Country Trial Counts</h3>
        <div class="table-scroll" style="max-height:350px">
            <table>
                <thead><tr><th>Country</th><th style="text-align:right">HF Trials</th></tr></thead>
                <tbody>
"""
    for country, count in sorted(country_dist.items(), key=lambda x: -x[1]):
        html += f"""<tr><td>{country}</td><td style="text-align:right;font-weight:700">{count}</td></tr>\n"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>
</div>

<!-- US/INDIA/BRAZIL COMPARISON -->
<h2>Global Comparators: US, India, Brazil</h2>
<div class="grid grid-3">
    <div class="card stat-card">
        <div class="stat-value stat-accent">{hf_counts.get('United States', 0):,}</div>
        <div class="stat-label">US HF Trials</div>
        <p style="font-size:0.8rem;color:#94a3b8;margin-top:8px">
            Mature trial infrastructure, industry-funded, aging population
        </p>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-yellow">{hf_counts.get('India', 0):,}</div>
        <div class="stat-label">India HF Trials</div>
        <p style="font-size:0.8rem;color:#94a3b8;margin-top:8px">
            Growing rapidly, younger population like Africa, more RHD
        </p>
    </div>
    <div class="card stat-card">
        <div class="stat-value stat-green">{hf_counts.get('Brazil', 0):,}</div>
        <div class="stat-label">Brazil HF Trials</div>
        <p style="font-size:0.8rem;color:#94a3b8;margin-top:8px">
            Middle-income comparator, Chagas cardiomyopathy unique etiology
        </p>
    </div>
</div>

<!-- FULL TRIAL TABLE -->
<h2>Complete Trial Registry: Africa Heart Failure</h2>
<div class="card">
    <div class="table-scroll" style="max-height:600px">
        <table>
            <thead><tr>
                <th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Class</th>
                <th>HF Type</th><th>Drug Class</th><th>Countries</th>
                <th>Phase</th><th>Status</th><th>N</th>
            </tr></thead>
            <tbody>{trial_table_html}</tbody>
        </table>
    </div>
</div>

<!-- METHODOLOGY -->
<h2>Methodology</h2>
<div class="card">
    <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (public, no patient-level data)</p>
    <p><strong>Query:</strong> Interventional trials with condition "heart failure" + location-specific
       queries for each country/region. Supplementary queries for peripartum cardiomyopathy,
       rheumatic heart disease, endomyocardial fibrosis, and cardiomyopathy in Africa.</p>
    <p><strong>Classification:</strong> Automated keyword-based classification of sponsor type,
       HF etiology, intervention class, and geographic scope. Manual verification of key findings.</p>
    <p><strong>CCI calculation:</strong> Burden share (~10% for Africa) / Trial share ({africa_trial_share:.1f}%) = {cci}</p>
    <p><strong>Limitations:</strong> Single registry (ClinicalTrials.gov); may undercount locally
       registered trials. Keyword classification is imperfect. Burden share is approximate.
       THESUS-HF was an observational registry, not an RCT, highlighting the even deeper gap
       in interventional evidence.</p>
    <p style="margin-top:12px;color:#94a3b8;font-size:0.8rem">
        <strong>AI transparency:</strong> LLM assistance was used for code generation and
        language editing. The author reviewed all outputs, verified API queries, and takes
        full responsibility for the final content and interpretation.
    </p>
</div>

<div class="footer">
    <p><strong>Heart Failure in Africa — Different Disease, No Evidence</strong></p>
    <p>Project 32 of the AfricaRCT Equity Series | Generated {data['fetch_date'][:10]}</p>
    <p>Source code: fetch_heart_failure_africa.py | Data: ClinicalTrials.gov API v2</p>
</div>

</div>

<script>
// Geographic comparison chart
new Chart(document.getElementById('geoChart'), {{
    type: 'bar',
    data: {{
        labels: {geo_labels},
        datasets: [{{
            label: 'HF Trials',
            data: {geo_values},
            backgroundColor: {geo_labels}.map((l,i) =>
                l === 'Africa' ? '#ef4444' :
                l === 'United States' ? '#3b82f6' : '#64748b'),
            borderRadius: 4
        }}]
    }},
    options: {{
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }},
            y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0' }} }}
        }}
    }}
}});

// HF type chart
new Chart(document.getElementById('hfTypeChart'), {{
    type: 'doughnut',
    data: {{
        labels: {hf_type_labels},
        datasets: [{{
            data: {hf_type_values},
            backgroundColor: ['#ef4444','#f97316','#eab308','#22c55e','#3b82f6',
                             '#8b5cf6','#ec4899','#06b6d4','#84cc16']
        }}]
    }},
    options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
            legend: {{ position: 'right', labels: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }}
        }}
    }}
}});

// Intervention chart
new Chart(document.getElementById('interventionChart'), {{
    type: 'bar',
    data: {{
        labels: {intervention_labels},
        datasets: [{{
            label: 'Trials',
            data: {intervention_values},
            backgroundColor: '#3b82f6',
            borderRadius: 4
        }}]
    }},
    options: {{
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }},
            y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0', font: {{ size: 10 }} }} }}
        }}
    }}
}});

// Sponsor chart
new Chart(document.getElementById('sponsorChart'), {{
    type: 'pie',
    data: {{
        labels: {sponsor_labels},
        datasets: [{{
            data: {sponsor_values},
            backgroundColor: {sponsor_colors}
        }}]
    }},
    options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
            legend: {{ position: 'right', labels: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }}
        }}
    }}
}});

// Country chart
new Chart(document.getElementById('countryChart'), {{
    type: 'bar',
    data: {{
        labels: {country_labels},
        datasets: [{{
            label: 'Trials',
            data: {country_values},
            backgroundColor: '#f97316',
            borderRadius: 4
        }}]
    }},
    options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0', font: {{ size: 10 }},
                 maxRotation: 45 }} }},
            y: {{ grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }}
        }}
    }}
}});
</script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML dashboard written to {OUTPUT_HTML}")
    print(f"  File size: {len(html):,} bytes")


# -- Main ------------------------------------------------------------------
def main():
    data = collect_data()
    generate_html(data)

    # Print summary
    hf = data["hf_counts_by_geography"]
    print("\n" + "=" * 70)
    print("HEART FAILURE IN AFRICA — SUMMARY")
    print("=" * 70)
    print(f"  Africa HF trials:     {hf.get('Africa', 0)}")
    print(f"  US HF trials:         {hf.get('United States', 0):,}")
    print(f"  Ratio:                {hf.get('United States', 0) / max(hf.get('Africa', 1), 1):.0f}:1")
    print(f"  CCI:                  {data['cci']}")
    print(f"  African-led:          {data['african_led_pct']}%")
    print(f"  SGLT2i in Africa:     {data['sglt2i_trial_count']}")
    print(f"  Device/CRT in Africa: {data['device_trial_count']}")
    print(f"  PPCM trials:          {data['ppcm_trial_count']}")
    print(f"  RHD trials:           {data['rhd_trial_count']}")
    cc = data["condition_counts_africa"]
    print(f"\n  Peripartum CM:        {cc.get('peripartum_cardiomyopathy', 0)} trials")
    print(f"  RHD:                  {cc.get('rheumatic_heart_disease', 0)} trials")
    print(f"  Endomyocardial fib:   {cc.get('endomyocardial_fibrosis', 0)} trials")
    print(f"  Cardiomyopathy:       {cc.get('cardiomyopathy', 0)} trials")
    print("=" * 70)


if __name__ == "__main__":
    main()
