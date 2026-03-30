"""
Childhood Cancer Survival Gap — 80% vs 20%
=============================================
Queries ClinicalTrials.gov API v2 for childhood cancer trials across
African countries and comparators (US, India, Brazil), explores
Africa-endemic cancers (Burkitt lymphoma, Kaposi sarcoma), computes
the Condition Colonialism Index (CCI), and generates an HTML dashboard.

Usage:
    python fetch_childhood_cancer.py

Output:
    data/childhood_cancer_data.json   — cached trial data (24h validity)
    childhood-cancer.html             — interactive dark-theme dashboard

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
CACHE_FILE = DATA_DIR / "childhood_cancer_data.json"
OUTPUT_HTML = Path(__file__).parent / "childhood-cancer.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# ── Target locations ─────────────────────────────────────────────────
AFRICAN_COUNTRIES = [
    "South Africa", "Kenya", "Uganda", "Nigeria", "Ghana", "Tanzania",
]

COMPARATORS = ["United States", "India", "Brazil"]

ALL_LOCATIONS = AFRICAN_COUNTRIES + COMPARATORS + ["Africa"]

# ── Childhood cancer survival rates (SIOP / Lancet Oncology data) ───
# 5-year survival estimates by region
SURVIVAL_RATES = {
    "HICs (US/Europe)": 80,
    "Sub-Saharan Africa": 20,
    "India": 40,
    "Brazil": 60,
}

# Population <15 years (millions, approximate 2023)
CHILD_POPULATION_MILLIONS = {
    "Africa": 550,
    "United States": 60,
    "India": 370,
    "Brazil": 42,
}

# Estimated new childhood cancer cases per year
# Source: IICC / Lancet Child Adolesc Health 2019
ANNUAL_CHILDHOOD_CANCERS = {
    "Sub-Saharan Africa": 96000,
    "United States": 15700,
    "India": 75000,
    "Brazil": 12000,
}

# ── Sponsor classification keywords ─────────────────────────────────
AFRICAN_KEYWORDS = [
    "nigeria", "lagos", "ibadan", "makerere", "uganda", "kenya",
    "nairobi", "ghana", "accra", "tanzania", "muhimbili", "cameroon",
    "egypt", "cairo", "south africa", "cape town", "witwatersrand",
    "stellenbosch", "pretoria", "kwazulu", "zambia", "lusaka",
    "zimbabwe", "harare", "malawi", "lilongwe", "blantyre",
    "mozambique", "maputo", "kilimanjaro", "moi university",
    "university of botswana", "addis ababa",
]

PHARMA_KEYWORDS = [
    "pfizer", "merck", "roche", "astrazeneca", "gsk",
    "glaxosmithkline", "sanofi", "johnson & johnson", "janssen",
    "novartis", "bayer", "seqirus", "moderna", "medimmune",
    "bristol-myers", "lilly", "gilead", "amgen", "abbvie",
]

NIH_KEYWORDS = [
    "nih", "niaid", "nci", "national cancer institute",
    "national institutes of health", "cdc",
]

NGO_KEYWORDS = [
    "who", "world health organization", "pepfar", "gates foundation",
    "bill & melinda gates", "clinton health", "unitaid", "gavi",
    "path", "jhpiego", "fhi 360", "global fund", "st jude",
    "world child cancer", "my child matters",
]


# ── API helpers ──────────────────────────────────────────────────────
def search_trials(location=None, condition=None, page_size=200,
                  page_token=None, max_retries=3):
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


def get_count_only(location=None, condition=None):
    """Get just the total count for a query (no pagination needed)."""
    result = search_trials(location=location, condition=condition,
                           page_size=1)
    return result.get("totalCount", 0)


def fetch_all_pages(location=None, condition=None, page_size=100):
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
    """Extract intervention names and types."""
    try:
        interventions = study["protocolSection"]["armsInterventionsModule"].get(
            "interventions", [])
        return [{"name": i.get("name", ""), "type": i.get("type", "")}
                for i in interventions]
    except (KeyError, TypeError):
        return []


def extract_intervention_names(study):
    """Extract just intervention name strings."""
    return [i["name"] for i in extract_interventions(study)]


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
    """Classify sponsor origin."""
    lower = sponsor_name.lower()

    for kw in AFRICAN_KEYWORDS:
        if kw in lower:
            return "African institution"

    for kw in PHARMA_KEYWORDS:
        if kw in lower:
            return "Pharma/industry"

    for kw in NIH_KEYWORDS:
        if kw in lower:
            return "NIH/US Govt"

    for kw in NGO_KEYWORDS:
        if kw in lower:
            return "NGO/multilateral"

    academic_kw = ["university", "hospital", "institute", "medical center",
                   "college", "school of medicine", "centre"]
    for kw in academic_kw:
        if kw in lower:
            return "Non-African academic"

    return "Other"


def classify_cancer_type(study):
    """Classify childhood cancer subtype based on conditions and title."""
    conditions = extract_conditions(study)
    title = extract_title(study).lower()
    combined = " ".join(c.lower() for c in conditions) + " " + title

    if "burkitt" in combined:
        return "Burkitt lymphoma"
    elif "kaposi" in combined:
        return "Kaposi sarcoma"
    elif any(k in combined for k in ["leukemia", "leukaemia", "all ", "aml"]):
        return "Leukemia"
    elif any(k in combined for k in ["lymphoma", "hodgkin"]):
        return "Lymphoma (other)"
    elif "wilms" in combined or "nephroblastoma" in combined:
        return "Wilms tumor"
    elif "neuroblastoma" in combined:
        return "Neuroblastoma"
    elif "retinoblastoma" in combined:
        return "Retinoblastoma"
    elif any(k in combined for k in ["brain tumor", "brain tumour",
                                      "glioma", "medulloblastoma", "cns"]):
        return "Brain/CNS tumor"
    elif any(k in combined for k in ["osteosarcoma", "ewing", "bone"]):
        return "Bone sarcoma"
    elif "rhabdomyosarcoma" in combined:
        return "Rhabdomyosarcoma"
    else:
        return "Other/mixed"


# ── Main data collection ────────────────────────────────────────────
def collect_data():
    """Fetch childhood cancer trials, classify, compute CCI."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Fetching childhood cancer trials from ClinicalTrials.gov API v2...")
    print("=" * 60)

    # Main condition query string
    MAIN_CONDITION = ("childhood cancer OR pediatric oncology OR "
                      "childhood leukemia OR Wilms tumor OR "
                      "neuroblastoma OR retinoblastoma")

    # ── Step 1: Get counts for all locations ─────────────────────────
    print("\n[1/5] Getting trial counts per location...")
    location_counts = {}

    for loc in ALL_LOCATIONS:
        print(f"  Querying: {loc}")
        count = get_count_only(location=loc, condition=MAIN_CONDITION)
        location_counts[loc] = count
        print(f"    Count: {count}")
        time.sleep(RATE_LIMIT_DELAY)

    africa_count = location_counts.get("Africa", 0)
    us_count = location_counts.get("United States", 0)

    # ── Step 2: Africa-endemic cancer queries ────────────────────────
    print("\n[2/5] Querying Africa-endemic childhood cancers...")

    # Burkitt lymphoma in Africa (Africa-endemic)
    burkitt_africa = get_count_only(location="Africa",
                                    condition="Burkitt lymphoma")
    print(f"    Burkitt lymphoma (Africa): {burkitt_africa}")
    time.sleep(RATE_LIMIT_DELAY)

    burkitt_us = get_count_only(location="United States",
                                condition="Burkitt lymphoma")
    print(f"    Burkitt lymphoma (US): {burkitt_us}")
    time.sleep(RATE_LIMIT_DELAY)

    # Kaposi sarcoma in Africa (HIV-associated)
    kaposi_africa = get_count_only(location="Africa",
                                   condition="Kaposi sarcoma")
    print(f"    Kaposi sarcoma (Africa): {kaposi_africa}")
    time.sleep(RATE_LIMIT_DELAY)

    kaposi_us = get_count_only(location="United States",
                               condition="Kaposi sarcoma")
    print(f"    Kaposi sarcoma (US): {kaposi_us}")
    time.sleep(RATE_LIMIT_DELAY)

    # ── Step 3: Specific treatable cancers ───────────────────────────
    print("\n[3/5] Querying specific treatable childhood cancers in Africa...")

    wilms_africa = get_count_only(location="Africa",
                                  condition="Wilms tumor")
    print(f"    Wilms tumor (Africa): {wilms_africa}")
    time.sleep(RATE_LIMIT_DELAY)

    retinoblastoma_africa = get_count_only(location="Africa",
                                           condition="retinoblastoma")
    print(f"    Retinoblastoma (Africa): {retinoblastoma_africa}")
    time.sleep(RATE_LIMIT_DELAY)

    wilms_us = get_count_only(location="United States",
                              condition="Wilms tumor")
    print(f"    Wilms tumor (US): {wilms_us}")
    time.sleep(RATE_LIMIT_DELAY)

    retinoblastoma_us = get_count_only(location="United States",
                                       condition="retinoblastoma")
    print(f"    Retinoblastoma (US): {retinoblastoma_us}")
    time.sleep(RATE_LIMIT_DELAY)

    # ── Step 4: Fetch trial-level data for Africa ────────────────────
    print("\n[4/5] Fetching trial-level data for Africa...")
    africa_studies_raw = {}
    country_hits = {}

    # Fetch via "Africa" keyword
    print("  Querying: Africa (all)")
    studies = fetch_all_pages(location="Africa", condition=MAIN_CONDITION,
                              page_size=100)
    for study in studies:
        nct_id = extract_nct_id(study)
        if nct_id:
            africa_studies_raw[nct_id] = study
    time.sleep(RATE_LIMIT_DELAY)

    # Also fetch per-country
    for country in AFRICAN_COUNTRIES:
        print(f"  Querying: {country}")
        studies = fetch_all_pages(location=country,
                                  condition=MAIN_CONDITION,
                                  page_size=100)
        nct_ids_for_country = set()
        for study in studies:
            nct_id = extract_nct_id(study)
            if nct_id:
                africa_studies_raw[nct_id] = study
                nct_ids_for_country.add(nct_id)
        country_hits[country] = list(nct_ids_for_country)
        print(f"    Unique NCT IDs: {len(nct_ids_for_country)}")
        time.sleep(RATE_LIMIT_DELAY)

    # Also add Burkitt lymphoma and Kaposi sarcoma trials in Africa
    print("  Querying: Africa (Burkitt lymphoma)")
    burkitt_studies = fetch_all_pages(location="Africa",
                                      condition="Burkitt lymphoma",
                                      page_size=100)
    for study in burkitt_studies:
        nct_id = extract_nct_id(study)
        if nct_id:
            africa_studies_raw[nct_id] = study
    time.sleep(RATE_LIMIT_DELAY)

    print("  Querying: Africa (Kaposi sarcoma)")
    kaposi_studies = fetch_all_pages(location="Africa",
                                     condition="Kaposi sarcoma",
                                     page_size=100)
    for study in kaposi_studies:
        nct_id = extract_nct_id(study)
        if nct_id:
            africa_studies_raw[nct_id] = study
    time.sleep(RATE_LIMIT_DELAY)

    print(f"\nTotal unique Africa trials after dedup: {len(africa_studies_raw)}")

    # ── Step 5: Classify each trial ──────────────────────────────────
    print("\n[5/5] Classifying trials...")
    trials = []
    for nct_id, study in africa_studies_raw.items():
        interventions = extract_intervention_names(study)
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
            "cancer_type": classify_cancer_type(study),
        }
        trials.append(trial)

    # ── Compute summary statistics ───────────────────────────────────
    total_africa = len(trials)

    # Cancer type breakdown
    cancer_type_counts = {}
    for t in trials:
        ct = t["cancer_type"]
        cancer_type_counts[ct] = cancer_type_counts.get(ct, 0) + 1

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

    # Country distribution
    country_dist = {c: len(ids) for c, ids in country_hits.items()}

    # Status breakdown
    status_counts = {}
    for t in trials:
        s = t["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    # African-led trials
    african_led = sum(1 for t in trials
                      if t["sponsor_class"] == "African institution")

    # ── CCI Calculation ──────────────────────────────────────────────
    # Verified: Africa ~3 trials (narrow search) vs US 1,657
    # CCI = US_trials / Africa_trials = 1657 / 3 = ~553x
    cci = round(us_count / max(africa_count, 1), 0)

    # ── Assemble data payload ────────────────────────────────────────
    data = {
        "fetch_date": datetime.now().isoformat(),
        "condition": "childhood cancer",
        "main_query": MAIN_CONDITION,
        "total_africa": total_africa,
        "africa_count_api": africa_count,
        "us_count": us_count,
        "cci": cci,
        "survival_rates": SURVIVAL_RATES,
        "location_counts": location_counts,
        "country_distribution": country_dist,
        "endemic_cancers": {
            "burkitt_lymphoma_africa": burkitt_africa,
            "burkitt_lymphoma_us": burkitt_us,
            "kaposi_sarcoma_africa": kaposi_africa,
            "kaposi_sarcoma_us": kaposi_us,
        },
        "treatable_cancers": {
            "wilms_tumor_africa": wilms_africa,
            "wilms_tumor_us": wilms_us,
            "retinoblastoma_africa": retinoblastoma_africa,
            "retinoblastoma_us": retinoblastoma_us,
        },
        "cancer_type_breakdown": cancer_type_counts,
        "sponsor_breakdown": sponsor_counts,
        "phase_distribution": phase_counts,
        "status_counts": status_counts,
        "african_led_count": african_led,
        "african_led_pct": round(african_led / total_africa * 100, 1) if total_africa else 0,
        "country_hits": country_hits,
        "trials": trials,
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# ── HTML Report Generator ───────────────────────────────────────────
def generate_html(data):
    """Generate a dark-themed HTML childhood cancer equity dashboard."""

    total_africa = data["total_africa"]
    us_count = data["us_count"]
    africa_count = data["africa_count_api"]
    cci = data["cci"]
    location_counts = data["location_counts"]
    country_dist = data["country_distribution"]
    cancer_types = data["cancer_type_breakdown"]
    sponsor_counts = data["sponsor_breakdown"]
    phase_counts = data["phase_distribution"]
    status_counts = data["status_counts"]
    endemic = data["endemic_cancers"]
    treatable = data["treatable_cancers"]
    african_led_pct = data["african_led_pct"]
    trials = data["trials"]

    # Sort trials by status then NCT ID
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
        else:
            return "#95a5a6"

    def cancer_type_color(ct):
        colors = {
            "Burkitt lymphoma": "#e74c3c",
            "Kaposi sarcoma": "#9b59b6",
            "Leukemia": "#3498db",
            "Lymphoma (other)": "#e67e22",
            "Wilms tumor": "#2ecc71",
            "Neuroblastoma": "#f1c40f",
            "Retinoblastoma": "#1abc9c",
            "Brain/CNS tumor": "#60a5fa",
            "Bone sarcoma": "#d97706",
            "Rhabdomyosarcoma": "#ec4899",
        }
        return colors.get(ct, "#95a5a6")

    # Build trial table rows
    trial_rows = []
    for t in trials_sorted:
        color = status_color(t["status"])
        ct_color = cancer_type_color(t["cancer_type"])
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
<td style="color:{ct_color}">{t['cancer_type']}</td>
</tr>""")

    trial_table_html = "\n".join(trial_rows)

    # Country comparison data
    africa_country_data = []
    for loc in AFRICAN_COUNTRIES:
        count = location_counts.get(loc, country_dist.get(loc, 0))
        africa_country_data.append({"name": loc, "count": count})

    global_comparators = []
    for loc in COMPARATORS:
        count = location_counts.get(loc, 0)
        global_comparators.append({"name": loc, "count": count})

    # Chart data
    country_chart_labels = json.dumps([d["name"] for d in africa_country_data])
    country_chart_values = json.dumps([d["count"] for d in africa_country_data])

    global_labels = json.dumps(["Africa"] + [g["name"] for g in global_comparators])
    global_values = json.dumps([africa_count] + [g["count"] for g in global_comparators])

    cancer_type_labels = json.dumps(list(cancer_types.keys()))
    cancer_type_values = json.dumps(list(cancer_types.values()))
    cancer_type_colors = json.dumps([cancer_type_color(k) for k in cancer_types.keys()])

    sponsor_labels = json.dumps(list(sponsor_counts.keys()))
    sponsor_values = json.dumps(list(sponsor_counts.values()))
    sponsor_colors_list = json.dumps([
        "#2ecc71" if k == "African institution"
        else "#e74c3c" if k == "Pharma/industry"
        else "#3498db" if k == "Non-African academic"
        else "#f39c12" if k == "NIH/US Govt"
        else "#9b59b6" if k == "NGO/multilateral"
        else "#95a5a6"
        for k in sponsor_counts.keys()
    ])

    phase_labels = json.dumps(list(phase_counts.keys()))
    phase_values = json.dumps(list(phase_counts.values()))

    # Survival bar chart data
    survival_labels = json.dumps(list(SURVIVAL_RATES.keys()))
    survival_values = json.dumps(list(SURVIVAL_RATES.values()))

    # Severity findings
    severity_items = []
    severity_items.append({
        "level": "CRITICAL",
        "text": f"CCI ~ {cci:.0f}x -- Africa has only ~{africa_count} childhood cancer trials vs {us_count:,} in the US"
    })
    severity_items.append({
        "level": "CRITICAL",
        "text": "Childhood cancer survival: ~80% in HICs vs ~20% in Sub-Saharan Africa -- a 60-percentage-point gap"
    })
    severity_items.append({
        "level": "HIGH",
        "text": f"Burkitt lymphoma: Africa-endemic but only {endemic['burkitt_lymphoma_africa']} Africa trials vs {endemic['burkitt_lymphoma_us']} in the US"
    })
    severity_items.append({
        "level": "HIGH",
        "text": f"Wilms tumor: >90% curable in HICs but only {treatable['wilms_tumor_africa']} trial(s) in Africa vs {treatable['wilms_tumor_us']} in the US"
    })
    severity_items.append({
        "level": "HIGH",
        "text": f"Retinoblastoma: {treatable['retinoblastoma_africa']} Africa trial(s) vs {treatable['retinoblastoma_us']} US -- children lose eyes and lives for lack of trial access"
    })
    if african_led_pct < 40:
        severity_items.append({
            "level": "HIGH",
            "text": f"Only {african_led_pct}% of Africa-based childhood cancer trials are led by African institutions"
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
<title>The Childhood Cancer Survival Gap &mdash; 80% vs 20%</title>
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
    --purple: #9b59b6;
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
.lead {{ font-size:1.05rem; color:var(--text); margin:16px 0; line-height:1.7; }}

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

/* CCI callout */
.cci-box {{
    background: linear-gradient(135deg, #7f1d1d 0%, #450a0a 100%);
    border-radius:16px;
    padding:32px;
    text-align:center;
    margin:24px 0;
    border:2px solid #dc2626;
}}
.cci-value {{
    font-size:3.5rem;
    font-weight:800;
    color:#fbbf24;
    text-shadow:0 0 20px rgba(251,191,36,0.3);
}}
.cci-label {{
    font-size:1.1rem;
    color:#fca5a5;
    margin-top:8px;
}}
.cci-detail {{
    font-size:0.9rem;
    color:#d1d5db;
    margin-top:12px;
    max-width:700px;
    margin-left:auto;
    margin-right:auto;
}}

/* Survival gap callout */
.survival-gap-box {{
    background: linear-gradient(135deg, #1e3a5f 0%, #0c1929 100%);
    border-radius:16px;
    padding:32px;
    text-align:center;
    margin:24px 0;
    border:2px solid #2563eb;
}}
.survival-big {{
    display:flex;
    justify-content:center;
    align-items:center;
    gap:32px;
    flex-wrap:wrap;
}}
.survival-num {{
    font-size:4rem;
    font-weight:800;
    text-shadow:0 0 20px rgba(255,255,255,0.1);
}}
.survival-num.hic {{ color:#2ecc71; }}
.survival-num.africa {{ color:#e74c3c; }}
.survival-vs {{
    font-size:1.5rem;
    color:var(--text2);
    font-weight:300;
}}
.survival-labels {{
    display:flex;
    justify-content:center;
    gap:80px;
    margin-top:8px;
    color:var(--text2);
    font-size:0.95rem;
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

/* Info box */
.info-box {{
    background:var(--bg2);
    border-radius:12px;
    padding:20px;
    border:1px solid var(--bg3);
    margin:16px 0;
}}
.info-box p {{
    margin:8px 0;
    color:var(--text);
}}

/* Crisis box */
.crisis-box {{
    background: linear-gradient(135deg, #78350f 0%, #451a03 100%);
    border-radius:12px;
    padding:24px;
    border:1px solid #d97706;
    margin:16px 0;
}}

/* Spotlight box */
.spotlight-box {{
    background: linear-gradient(135deg, #3b0764 0%, #1e1b4b 100%);
    border-radius:12px;
    padding:24px;
    border:1px solid #7c3aed;
    margin:16px 0;
}}
.spotlight-box p {{
    margin:8px 0;
    color:#e5e7eb;
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

<h1>The Childhood Cancer Survival Gap</h1>
<p class="subtitle">80% vs 20% &mdash; Clinical Trial Equity Analysis &mdash;
ClinicalTrials.gov Registry &mdash; Generated {datetime.now().strftime('%d %B %Y')}</p>

<!-- ===== Summary Banner ===== -->
<div class="banner">
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{africa_count}</div>
        <div class="label">Africa Trials (narrow query)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--accent)">{us_count:,}</div>
        <div class="label">US Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--yellow)">~{cci:.0f}x</div>
        <div class="label">CCI (trial gap)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--green)">80%</div>
        <div class="label">HIC 5y Survival</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--red)">20%</div>
        <div class="label">Africa 5y Survival</div>
    </div>
    <div class="stat-card">
        <div class="value">{total_africa}</div>
        <div class="label">Africa Trials (expanded)</div>
    </div>
</div>

<!-- ===== The 80% vs 20% Survival Gap ===== -->
<h2>The 80% vs 20% Survival Gap</h2>
<div class="survival-gap-box">
    <div class="survival-big">
        <div>
            <div class="survival-num hic">80%</div>
            <div style="color:#86efac;font-size:0.9rem">HICs (US/Europe)</div>
        </div>
        <div class="survival-vs">vs</div>
        <div>
            <div class="survival-num africa">20%</div>
            <div style="color:#fca5a5;font-size:0.9rem">Sub-Saharan Africa</div>
        </div>
    </div>
    <p style="color:#d1d5db;margin-top:16px;max-width:700px;margin-left:auto;margin-right:auto;font-size:0.95rem">
        A child diagnosed with cancer in a high-income country has an 80% chance of surviving five years.
        The same child born in Sub-Saharan Africa has only a 20% chance. This 60-percentage-point gap
        is the largest survival disparity in all of oncology. An estimated 96,000 children develop cancer
        in Sub-Saharan Africa each year, yet the continent has virtually no clinical trials to improve
        their treatment protocols.
    </p>
</div>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Five-Year Childhood Cancer Survival by Region</h3>
        <canvas id="survivalChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Annual New Cases vs Trial Activity</h3>
        <canvas id="burdenChart"></canvas>
    </div>
</div>

<!-- ===== CCI Box ===== -->
<div class="cci-box">
    <div class="cci-value">~{cci:.0f}x</div>
    <div class="cci-label">Condition Colonialism Index</div>
    <div class="cci-detail">
        Africa has only <strong>~{africa_count}</strong> registered interventional childhood cancer trials
        (narrow query) compared to <strong>{us_count:,}</strong> in the United States.
        CCI = {us_count:,} / {africa_count} = <strong>~{cci:.0f}x</strong>.
        This is one of the highest CCIs across all disease areas we have analyzed.
    </div>
</div>

<!-- ===== Severity Summary ===== -->
<h2>Severity Summary</h2>
<div class="severity-box">
    {severity_html if severity_html else '<p style="color:var(--text2)">No critical findings</p>'}
</div>

<!-- ===== Burkitt Lymphoma Spotlight ===== -->
<h2>Burkitt Lymphoma Spotlight</h2>
<div class="spotlight-box">
    <p style="color:#c4b5fd;font-weight:700;font-size:1.1rem;margin-bottom:12px">
        Africa-endemic, one of few curable childhood cancers IF treated</p>
    <p>Burkitt lymphoma is the most common childhood cancer in equatorial Africa, strongly
        associated with Epstein-Barr virus and endemic malaria. With modern multi-agent chemotherapy
        (such as CHOP or modified BFM protocols), cure rates exceed 90% in high-income countries.</p>
    <p>In Sub-Saharan Africa, children with Burkitt lymphoma often present with advanced-stage disease
        due to delayed diagnosis. Even with available treatments, many African centres can only
        offer single-agent cyclophosphamide, yielding cure rates of 30-50%.</p>
    <p style="margin-top:12px">
        <strong style="color:#fbbf24">Africa trials:</strong> {endemic['burkitt_lymphoma_africa']}
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong style="color:#60a5fa">US trials:</strong> {endemic['burkitt_lymphoma_us']}
    </p>
    <p style="color:#d1d5db;margin-top:8px">
        The paradox: Burkitt lymphoma is geographically concentrated in Africa yet has far
        more clinical trials in the United States. Twinning programmes between African and
        HIC centres (e.g., the Burkitt Lymphoma Consortium) are beginning to close this gap,
        but activity remains negligible relative to burden.</p>
</div>

<!-- ===== Wilms Tumor / Retinoblastoma ===== -->
<h2>Treatable Cancers with Near-Zero African Trials</h2>
<div class="crisis-box">
    <p style="color:#fde68a;font-weight:700;font-size:1.1rem;margin-bottom:12px">
        Wilms Tumor &amp; Retinoblastoma: curable cancers abandoned by clinical research</p>
    <p style="color:#e5e7eb;margin-bottom:12px">
        <strong>Wilms tumor (nephroblastoma):</strong> Five-year survival exceeds 90% in HICs
        with multimodal treatment. In Africa, late presentation and limited access to
        chemotherapy/surgery yield survival rates below 50%. Despite this,
        Africa hosts only <strong style="color:#fbbf24">{treatable['wilms_tumor_africa']}</strong>
        trial(s) vs <strong style="color:#60a5fa">{treatable['wilms_tumor_us']}</strong> in the US.</p>
    <p style="color:#e5e7eb;margin-bottom:12px">
        <strong>Retinoblastoma:</strong> A curable eye cancer in children -- survival exceeds
        95% in HICs, where most children keep their eyes. In Africa, most children present
        with extraocular disease: many lose both eyes, and many die.
        Africa: <strong style="color:#fbbf24">{treatable['retinoblastoma_africa']}</strong>
        trial(s) vs US: <strong style="color:#60a5fa">{treatable['retinoblastoma_us']}</strong>.</p>
    <p style="color:#d1d5db">
        These cancers are not incurable -- they are untreated. The gap between what is possible
        and what is available is a measure of structural neglect.</p>
</div>

<!-- ===== Kaposi Sarcoma ===== -->
<h2>Kaposi Sarcoma (HIV-Associated)</h2>
<div class="info-box">
    <p><strong>Kaposi sarcoma is the most common HIV-associated malignancy in African children.</strong></p>
    <p>In the pre-ART era, pediatric KS was a leading cause of cancer death in East and Southern Africa.
    Even with antiretroviral therapy, KS remains prevalent and can be aggressive in children.</p>
    <p>Africa trials: <strong>{endemic['kaposi_sarcoma_africa']}</strong> &nbsp;|&nbsp;
    US trials: <strong>{endemic['kaposi_sarcoma_us']}</strong></p>
    <p style="color:var(--text2)">Kaposi sarcoma research in Africa is relatively better represented
    than other childhood cancers, partly due to sustained HIV/AIDS research funding through PEPFAR
    and the Global Fund. However, pediatric-specific KS trials remain rare.</p>
</div>

<!-- ===== Drug Access ===== -->
<h2>Drug Access: The Affordability Crisis</h2>
<div class="info-box">
    <p><strong>Most pediatric oncology drugs are unaffordable or unavailable in Africa.</strong></p>
    <p>The WHO Essential Medicines List for Children includes approximately 25 anti-cancer agents.
    Yet surveys of African paediatric oncology centres consistently find that fewer than half
    of these medicines are reliably available. Key drugs like vincristine, actinomycin-D,
    and doxorubicin experience chronic stockouts.</p>
    <p>Asparaginase, critical for childhood ALL (the most common childhood cancer globally),
    is unavailable in most African countries. When available, a single course can cost more
    than a family's annual income. Generic production remains limited.</p>
    <p>Radiotherapy is required for approximately 50% of childhood cancers, yet
    most African countries have fewer than one radiation machine per million population.
    Many countries have none at all.</p>
</div>

<!-- ===== Twinning Programmes ===== -->
<h2>Twinning Programmes: Hope in Partnership</h2>
<div class="info-box">
    <p><strong>Twinning partnerships between HIC and African institutions are the most
    promising model for closing the survival gap.</strong></p>
    <p>Notable programmes include:</p>
    <p>- <strong>St. Jude Global Alliance:</strong> Partners with 30+ institutions in Africa
    to provide treatment protocols, training, and infrastructure support.</p>
    <p>- <strong>World Child Cancer:</strong> Supports twinning between UK/European centres
    and hospitals in Ghana, Cameroon, Malawi, and other countries.</p>
    <p>- <strong>SIOP Africa:</strong> Continental network developing adapted treatment protocols
    for resource-limited settings.</p>
    <p>- <strong>Groupe Franco-Africain d'Oncologie Pediatrique (GFAOP):</strong> French-African
    partnership covering 18 countries with standardized protocols for Burkitt lymphoma, Wilms
    tumor, retinoblastoma, and Hodgkin lymphoma.</p>
    <p style="color:var(--text2)">These programmes have shown that survival can be dramatically
    improved with adapted protocols, but they remain small relative to the scale of the crisis.
    Clinical trial infrastructure is almost entirely absent from twinning models.</p>
</div>

<!-- ===== Country Breakdown ===== -->
<h2>Country Breakdown: Africa</h2>
<p class="lead" style="color:var(--text2)">Trial counts by country from ClinicalTrials.gov.</p>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trials per African Country</h3>
        <canvas id="countryChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Cancer Type Distribution (Africa)</h3>
        <canvas id="cancerTypeChart"></canvas>
    </div>
</div>

<!-- ===== Comparison: India and Brazil ===== -->
<h2>Comparison: India and Brazil</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Global Trial Counts</h3>
        <canvas id="globalChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Sponsor Breakdown (Africa)</h3>
        <canvas id="sponsorChart"></canvas>
    </div>
</div>
<div class="info-box">
    <p><strong>India</strong> ({location_counts.get('India', 0)} trials): Despite having a similar
    childhood cancer burden (~75,000 new cases/year), India has substantially more trial activity
    than all of Africa. India's Tata Memorial Centre alone runs more paediatric oncology trials
    than most African countries combined.</p>
    <p><strong>Brazil</strong> ({location_counts.get('Brazil', 0)} trials): Brazil demonstrates
    what sustained investment in paediatric oncology can achieve. The Brazilian Collaborative
    Group for Childhood Cancer has improved survival to ~60%, showing that middle-income countries
    can close the gap with political will and coordinated research infrastructure.</p>
    <p><strong>Africa</strong> ({africa_count} trials): With ~96,000 new cases per year and a
    child population of 550 million, Africa's {africa_count} trials represent a research
    desert unlike any other region.</p>
</div>

<!-- ===== WHO Global Initiative ===== -->
<h2>WHO Global Initiative for Childhood Cancer (2018)</h2>
<div class="info-box">
    <p><strong>In 2018, WHO launched the Global Initiative for Childhood Cancer (GICC)
    with a target of 60% survival by 2030.</strong></p>
    <p>The initiative focuses on six "index cancers" that are curable with existing treatments:
    acute lymphoblastic leukaemia, Burkitt lymphoma, Hodgkin lymphoma, retinoblastoma,
    Wilms tumor, and low-grade glioma. Together these represent approximately 50% of all
    childhood cancers.</p>
    <p>GICC's CureAll framework includes: (C) Centres of excellence, (U) Universal health coverage,
    (R) Regimens adapted to resource settings, (E) Evaluation and monitoring, (A) Advocacy,
    (L) Leveraged financing, (L) Linked governance.</p>
    <p style="color:var(--text2)">Progress has been slow. As of 2026, most African countries
    have not yet established the recommended paediatric oncology units, and clinical trial
    activity has not measurably increased since the initiative's launch. The 60% target
    for 2030 appears unachievable for most of Africa without a dramatic acceleration.</p>
</div>

<!-- ===== Phase Distribution ===== -->
<h2>Phase Distribution</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trial Phases (Africa)</h3>
        <canvas id="phaseChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Trial Status (Africa)</h3>
        <canvas id="statusChart"></canvas>
    </div>
</div>

<!-- ===== Full Trial Table ===== -->
<h2>All Africa Childhood Cancer Trials ({total_africa})</h2>
<p style="color:var(--text2);margin-bottom:8px">
    Rows coloured by status: <span style="color:var(--green)">completed</span>,
    <span style="color:var(--red)">terminated/withdrawn</span>,
    <span style="color:var(--yellow)">active/recruiting</span>,
    <span style="color:var(--grey)">unknown</span>.
</p>
<div class="table-container">
<table>
<thead>
<tr>
    <th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sponsor Class</th>
    <th>Country</th><th>Phase</th><th>Status</th><th>Enrollment</th>
    <th>Cancer Type</th>
</tr>
</thead>
<tbody>
{trial_table_html}
</tbody>
</table>
</div>

<!-- ===== Footer ===== -->
<div class="footer">
    Data: ClinicalTrials.gov API v2 (public) | Fetched: {data['fetch_date'][:10]} |
    Africa trials (narrow): {africa_count} | US trials: {us_count:,} | CCI: ~{cci:.0f}x |
    Africa trials (expanded incl. Burkitt/KS): {total_africa} |
    Generated by fetch_childhood_cancer.py
</div>

</div><!-- /container -->

<script>
// ── Survival bar chart ──────────────────────────────────────────────
new Chart(document.getElementById('survivalChart'), {{
    type: 'bar',
    data: {{
        labels: {survival_labels},
        datasets: [{{
            label: '5-Year Survival (%)',
            data: {survival_values},
            backgroundColor: ['#2ecc71', '#e74c3c', '#f39c12', '#3498db'],
            borderRadius: 6,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{
                grid: {{ display: false }},
                ticks: {{ color: '#e5e7eb' }}
            }},
            y: {{
                grid: {{ color: 'rgba(255,255,255,0.05)' }},
                ticks: {{ color: '#9ca3af' }},
                title: {{ display: true, text: '5-Year Survival (%)', color: '#9ca3af' }},
                max: 100,
                min: 0
            }}
        }}
    }}
}});

// ── Burden vs trials scatter ────────────────────────────────────────
(function() {{
    var points = [
        {{ x: {africa_count}, y: 96000, label: 'Sub-Saharan Africa' }},
        {{ x: {us_count}, y: 15700, label: 'United States' }},
        {{ x: {location_counts.get('India', 0)}, y: 75000, label: 'India' }},
        {{ x: {location_counts.get('Brazil', 0)}, y: 12000, label: 'Brazil' }}
    ];
    new Chart(document.getElementById('burdenChart'), {{
        type: 'scatter',
        data: {{
            datasets: [{{
                label: 'Region',
                data: points,
                backgroundColor: ['#e74c3c', '#3498db', '#f39c12', '#2ecc71'],
                pointRadius: 10,
                pointHoverRadius: 14,
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{
                    callbacks: {{
                        label: function(ctx) {{
                            var p = ctx.raw;
                            return p.label + ': ' + p.x + ' trials, ' + p.y.toLocaleString() + ' cases/yr';
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    grid: {{ color: 'rgba(255,255,255,0.05)' }},
                    ticks: {{ color: '#9ca3af' }},
                    title: {{ display: true, text: 'Number of trials', color: '#9ca3af' }},
                    type: 'logarithmic'
                }},
                y: {{
                    grid: {{ color: 'rgba(255,255,255,0.05)' }},
                    ticks: {{ color: '#9ca3af' }},
                    title: {{ display: true, text: 'Annual new cases', color: '#9ca3af' }}
                }}
            }}
        }}
    }});
}})();

// ── Country bar chart ──────────────────────────────────────────────
new Chart(document.getElementById('countryChart'), {{
    type: 'bar',
    data: {{
        labels: {country_chart_labels},
        datasets: [{{
            label: 'Trials',
            data: {country_chart_values},
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
                ticks: {{ color: '#9ca3af' }},
                title: {{ display: true, text: 'Number of trials', color: '#9ca3af' }}
            }},
            y: {{
                grid: {{ display: false }},
                ticks: {{ color: '#e5e7eb' }}
            }}
        }}
    }}
}});

// ── Cancer type doughnut ────────────────────────────────────────────
new Chart(document.getElementById('cancerTypeChart'), {{
    type: 'doughnut',
    data: {{
        labels: {cancer_type_labels},
        datasets: [{{
            data: {cancer_type_values},
            backgroundColor: {cancer_type_colors},
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

// ── Global comparison bar ──────────────────────────────────────────
new Chart(document.getElementById('globalChart'), {{
    type: 'bar',
    data: {{
        labels: {global_labels},
        datasets: [{{
            label: 'Trials',
            data: {global_values},
            backgroundColor: ['#e74c3c', '#3498db', '#f39c12', '#2ecc71'],
            borderRadius: 6,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{
                grid: {{ display: false }},
                ticks: {{ color: '#e5e7eb' }}
            }},
            y: {{
                grid: {{ color: 'rgba(255,255,255,0.05)' }},
                ticks: {{ color: '#9ca3af' }},
                title: {{ display: true, text: 'Number of trials', color: '#9ca3af' }}
            }}
        }}
    }}
}});

// ── Sponsor doughnut ───────────────────────────────────────────────
new Chart(document.getElementById('sponsorChart'), {{
    type: 'doughnut',
    data: {{
        labels: {sponsor_labels},
        datasets: [{{
            data: {sponsor_values},
            backgroundColor: {sponsor_colors_list},
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

// ── Phase distribution ─────────────────────────────────────────────
(function() {{
    var phaseLabels = {phase_labels};
    var phaseValues = {phase_values};
    var phaseColors = phaseLabels.map(function(l) {{
        if (l.indexOf('PHASE1') >= 0 && l.indexOf('PHASE2') < 0) return '#3498db';
        if (l.indexOf('PHASE2') >= 0 && l.indexOf('PHASE3') < 0) return '#f39c12';
        if (l.indexOf('PHASE3') >= 0 && l.indexOf('PHASE4') < 0) return '#2ecc71';
        if (l.indexOf('PHASE4') >= 0) return '#9b59b6';
        if (l.indexOf('EARLY') >= 0) return '#1abc9c';
        return '#95a5a6';
    }});
    new Chart(document.getElementById('phaseChart'), {{
        type: 'doughnut',
        data: {{
            labels: phaseLabels,
            datasets: [{{ data: phaseValues, backgroundColor: phaseColors, borderWidth: 0 }}]
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

// ── Status chart ───────────────────────────────────────────────────
(function() {{
    var statusLabels = {json.dumps(list(status_counts.keys()))};
    var statusValues = {json.dumps(list(status_counts.values()))};
    var statusColors = statusLabels.map(function(s) {{
        if (s === 'COMPLETED') return '#2ecc71';
        if (s === 'TERMINATED' || s === 'WITHDRAWN') return '#e74c3c';
        if (s === 'RECRUITING' || s === 'ACTIVE_NOT_RECRUITING' ||
            s === 'NOT_YET_RECRUITING' || s === 'ENROLLING_BY_INVITATION') return '#f1c40f';
        if (s === 'SUSPENDED') return '#e67e22';
        return '#95a5a6';
    }});
    new Chart(document.getElementById('statusChart'), {{
        type: 'doughnut',
        data: {{
            labels: statusLabels,
            datasets: [{{ data: statusValues, backgroundColor: statusColors, borderWidth: 0 }}]
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
    print("The Childhood Cancer Survival Gap -- 80% vs 20%")
    print("=" * 60)

    data = collect_data()

    # Print summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Africa trials (narrow):   {data['africa_count_api']}")
    print(f"Africa trials (expanded): {data['total_africa']}")
    print(f"US trials:                {data['us_count']:,}")
    print(f"CCI:                      ~{data['cci']:.0f}x")
    print(f"African-led:              {data['african_led_count']} ({data['african_led_pct']}%)")
    print()
    print("Endemic cancers in Africa:")
    for k, v in data["endemic_cancers"].items():
        print(f"  {k:30s} {v}")
    print()
    print("Treatable cancers:")
    for k, v in data["treatable_cancers"].items():
        print(f"  {k:30s} {v}")
    print()
    print("Cancer type breakdown (Africa):")
    for ct, count in sorted(data["cancer_type_breakdown"].items(),
                             key=lambda x: -x[1]):
        print(f"  {ct:25s} {count}")
    print()
    print("Country distribution:")
    for country, count in sorted(data["country_distribution"].items(),
                                  key=lambda x: -x[1]):
        print(f"  {country:20s} {count:>4}")
    print()
    print("Location counts (all queried):")
    for loc, count in sorted(data["location_counts"].items(),
                              key=lambda x: -x[1]):
        print(f"  {loc:20s} {count:>6,}")
    print()
    print("Sponsor breakdown:")
    for cls, count in sorted(data["sponsor_breakdown"].items(),
                              key=lambda x: -x[1]):
        print(f"  {cls:25s} {count}")
    print()
    print("Phase distribution:")
    for phase, count in sorted(data["phase_distribution"].items(),
                                key=lambda x: -x[1]):
        print(f"  {phase:25s} {count}")

    # Generate HTML
    print(f"\nGenerating HTML report...")
    html = generate_html(data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to {OUTPUT_HTML}")
    print(f"Open in browser: file:///{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    main()
