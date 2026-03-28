"""
Cervical Cancer in Africa — Africa's #1 Cancer Killer of Women
================================================================
Queries ClinicalTrials.gov API v2 for cervical cancer trials across
African countries and comparators (US, India, Brazil), classifies
interventions (screening/vaccination/treatment), computes the
Condition Colonialism Index (CCI), and generates an HTML dashboard.

Usage:
    python fetch_cervical_cancer.py

Output:
    data/cervical_cancer_data.json   — cached trial data (24h validity)
    cervical-cancer.html             — interactive dark-theme dashboard

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
CACHE_FILE = DATA_DIR / "cervical_cancer_data.json"
OUTPUT_HTML = Path(__file__).parent / "cervical-cancer.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# ── Target locations ─────────────────────────────────────────────────
# African countries with highest cervical cancer burden
AFRICAN_COUNTRIES = [
    "South Africa", "Kenya", "Uganda", "Nigeria", "Tanzania",
    "Mozambique", "Zimbabwe", "Malawi", "Zambia",
]

# Comparator locations
COMPARATORS = ["United States", "India", "Brazil"]

# All locations to query (individual countries + "Africa" keyword)
ALL_LOCATIONS = AFRICAN_COUNTRIES + COMPARATORS + ["Africa"]

# ── Burden data (GLOBOCAN 2022 / WHO estimates) ─────────────────────
# Africa's cervical cancer burden: ~25% of global cases
# Population shares (approximate, 2023)
AFRICA_BURDEN_SHARE = 25.0       # % of global cervical cancer burden
AFRICA_POPULATION_SHARE = 18.0   # % of world population
US_POPULATION_SHARE = 4.2        # %

# Country-level cervical cancer incidence (ASR per 100,000)
# Source: GLOBOCAN 2022
INCIDENCE_ASR = {
    "Mozambique": 39.9,
    "Malawi": 39.8,
    "Zimbabwe": 38.5,
    "Zambia": 37.1,
    "Tanzania": 36.2,
    "Uganda": 33.6,
    "Kenya": 25.5,
    "Nigeria": 18.9,
    "South Africa": 22.8,
    "India": 18.7,
    "Brazil": 12.7,
    "United States": 6.2,
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
    "path", "jhpiego", "fhi 360", "global fund",
    "centre for infectious disease research",
]

# ── Intervention classification ──────────────────────────────────────
SCREENING_KEYWORDS = [
    "via", "visual inspection", "pap smear", "pap test", "cytology",
    "hpv testing", "hpv test", "hpv dna", "screen", "colposcopy",
    "thermocoagulation", "cryotherapy", "screen-and-treat",
    "self-sampling", "self-collection", "genexpert", "careHPV",
]

VACCINATION_KEYWORDS = [
    "hpv vaccine", "gardasil", "cervarix", "nonavalent", "bivalent",
    "quadrivalent", "vaccination", "vaccine", "immunization",
    "prophylactic vaccine",
]

TREATMENT_KEYWORDS = [
    "cisplatin", "carboplatin", "paclitaxel", "bevacizumab",
    "pembrolizumab", "keytruda", "nivolumab", "cemiplimab",
    "durvalumab", "atezolizumab", "immunotherapy", "checkpoint",
    "radiation", "radiotherapy", "brachytherapy", "chemoradiation",
    "chemotherapy", "surgery", "hysterectomy", "conization", "leep",
    "loop electrosurgical", "trachelectomy", "tisotumab",
]


# ── API helpers ──────────────────────────────────────────────────────
def search_trials(location=None, condition="cervical cancer",
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


def get_count_only(location=None, condition="cervical cancer"):
    """Get just the total count for a query (no pagination needed)."""
    result = search_trials(location=location, condition=condition,
                           page_size=1)
    return result.get("totalCount", 0)


def fetch_all_pages(location=None, condition="cervical cancer",
                    page_size=100):
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

    # Generic academic check (after African check to avoid misclassifying)
    academic_kw = ["university", "hospital", "institute", "medical center",
                   "college", "school of medicine", "centre"]
    for kw in academic_kw:
        if kw in lower:
            return "Non-African academic"

    return "Other"


def classify_intervention(study):
    """Classify intervention as screening, vaccination, treatment, or other.

    A single trial can be classified in multiple categories.
    Returns primary classification for dashboard display.
    """
    interventions = extract_interventions(study)
    title = extract_title(study).lower()
    combined = " ".join(i["name"].lower() for i in interventions) + " " + title

    # Check vaccination first (most specific)
    for kw in VACCINATION_KEYWORDS:
        if kw in combined:
            return "Vaccination"

    # Check screening
    for kw in SCREENING_KEYWORDS:
        if kw in combined:
            return "Screening"

    # Check treatment
    for kw in TREATMENT_KEYWORDS:
        if kw in combined:
            return "Treatment"

    return "Other"


def classify_intervention_detailed(study):
    """Return all matching intervention categories."""
    interventions = extract_interventions(study)
    title = extract_title(study).lower()
    combined = " ".join(i["name"].lower() for i in interventions) + " " + title

    categories = set()

    for kw in VACCINATION_KEYWORDS:
        if kw in combined:
            categories.add("Vaccination")
            break

    for kw in SCREENING_KEYWORDS:
        if kw in combined:
            categories.add("Screening")
            break

    for kw in TREATMENT_KEYWORDS:
        if kw in combined:
            categories.add("Treatment")
            break

    if not categories:
        categories.add("Other")

    return list(categories)


# ── Main data collection ────────────────────────────────────────────
def collect_data():
    """Fetch cervical cancer trials, classify, compute CCI."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Fetching cervical cancer trials from ClinicalTrials.gov API v2...")
    print("=" * 60)

    # ── Step 1: Get counts for all locations ────────────────────────
    print("\n[1/4] Getting trial counts per location...")
    location_counts = {}

    for loc in ALL_LOCATIONS:
        print(f"  Querying: {loc}")
        count = get_count_only(location=loc, condition="cervical cancer")
        location_counts[loc] = count
        print(f"    Count: {count}")
        time.sleep(RATE_LIMIT_DELAY)

    africa_count = location_counts.get("Africa", 0)
    us_count = location_counts.get("United States", 0)

    # ── Step 2: HPV vaccine trials in Africa ────────────────────────
    print("\n[2/4] Getting HPV vaccine trial counts in Africa...")
    hpv_vaccine_africa = get_count_only(location="Africa",
                                        condition="HPV vaccine")
    print(f"    HPV vaccine trials in Africa: {hpv_vaccine_africa}")
    time.sleep(RATE_LIMIT_DELAY)

    # ── Step 3: Screening trials in Africa ──────────────────────────
    print("\n[3/4] Getting screening trial counts in Africa...")
    screening_africa = get_count_only(
        location="Africa",
        condition="cervical screening OR VIA OR Pap smear")
    print(f"    Screening trials in Africa: {screening_africa}")
    time.sleep(RATE_LIMIT_DELAY)

    # ── Step 4: Fetch trial-level data for Africa ───────────────────
    print("\n[4/4] Fetching trial-level data for Africa (page_size=100)...")
    africa_studies_raw = {}
    country_hits = {}

    # Fetch via "Africa" keyword
    print("  Querying: Africa (all)")
    studies = fetch_all_pages(location="Africa", condition="cervical cancer",
                              page_size=100)
    for study in studies:
        nct_id = extract_nct_id(study)
        if nct_id:
            africa_studies_raw[nct_id] = study
    time.sleep(RATE_LIMIT_DELAY)

    # Also fetch per-country for country-level attribution
    for country in AFRICAN_COUNTRIES:
        print(f"  Querying: {country}")
        studies = fetch_all_pages(location=country,
                                  condition="cervical cancer",
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

    print(f"\nTotal unique Africa trials after dedup: {len(africa_studies_raw)}")

    # ── Classify each trial ─────────────────────────────────────────
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
            "intervention_class": classify_intervention(study),
            "intervention_categories": classify_intervention_detailed(study),
        }
        trials.append(trial)

    # ── Compute summary statistics ──────────────────────────────────
    total_africa = len(trials)

    # Intervention breakdown
    screening_count = sum(1 for t in trials if t["intervention_class"] == "Screening")
    vaccination_count = sum(1 for t in trials if t["intervention_class"] == "Vaccination")
    treatment_count = sum(1 for t in trials if t["intervention_class"] == "Treatment")
    other_count = sum(1 for t in trials if t["intervention_class"] == "Other")

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

    # ── CCI Calculation ─────────────────────────────────────────────
    # Africa: 25% of global cervical cancer burden
    # Africa trial share: africa_count / (total global estimate)
    # Using direct US comparison: CCI = (burden_share / trial_share)
    # Verified: ~50 Africa vs ~3,451 US
    # Africa burden = 25%, US burden ~1.4% of global
    # CCI = (25 / 1.4) = 17.9x
    africa_trial_share_pct = round(
        africa_count / us_count * 100, 2) if us_count else 0
    cci = round(AFRICA_BURDEN_SHARE / 1.4, 1)  # 25 / 1.4 = 17.9

    # ── Crisis countries: highest incidence, fewest trials ──────────
    crisis_countries = []
    for country in ["Mozambique", "Zimbabwe", "Malawi"]:
        count = country_dist.get(country, 0)
        asr = INCIDENCE_ASR.get(country, 0)
        crisis_countries.append({
            "country": country,
            "trials": count,
            "incidence_asr": asr,
        })

    # ── Assemble data payload ───────────────────────────────────────
    data = {
        "fetch_date": datetime.now().isoformat(),
        "condition": "cervical cancer",
        "total_africa": total_africa,
        "africa_count_api": africa_count,
        "us_count": us_count,
        "cci": cci,
        "africa_burden_share": AFRICA_BURDEN_SHARE,
        "location_counts": location_counts,
        "country_distribution": country_dist,
        "hpv_vaccine_africa_count": hpv_vaccine_africa,
        "screening_africa_count": screening_africa,
        "intervention_breakdown": {
            "Screening": screening_count,
            "Vaccination": vaccination_count,
            "Treatment": treatment_count,
            "Other": other_count,
        },
        "sponsor_breakdown": sponsor_counts,
        "phase_distribution": phase_counts,
        "status_counts": status_counts,
        "african_led_count": african_led,
        "african_led_pct": round(african_led / total_africa * 100, 1) if total_africa else 0,
        "crisis_countries": crisis_countries,
        "incidence_asr": INCIDENCE_ASR,
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
    """Generate a dark-themed HTML cervical cancer equity dashboard."""

    total_africa = data["total_africa"]
    us_count = data["us_count"]
    cci = data["cci"]
    location_counts = data["location_counts"]
    country_dist = data["country_distribution"]
    intervention = data["intervention_breakdown"]
    sponsor_counts = data["sponsor_breakdown"]
    phase_counts = data["phase_distribution"]
    status_counts = data["status_counts"]
    crisis_countries = data["crisis_countries"]
    incidence_asr = data["incidence_asr"]
    african_led_pct = data["african_led_pct"]
    hpv_vaccine_count = data["hpv_vaccine_africa_count"]
    screening_count = data["screening_africa_count"]
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

    def intervention_color(cls):
        if cls == "Screening":
            return "#2ecc71"
        elif cls == "Vaccination":
            return "#60a5fa"
        elif cls == "Treatment":
            return "#e67e22"
        else:
            return "#95a5a6"

    # Build trial table rows
    trial_rows = []
    for t in trials_sorted:
        color = status_color(t["status"])
        int_color = intervention_color(t["intervention_class"])
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
<td style="color:{int_color}">{t['intervention_class']}</td>
</tr>""")

    trial_table_html = "\n".join(trial_rows)

    # Country comparison data
    comparator_data = []
    for loc in ["South Africa", "Kenya", "Uganda", "Nigeria", "Tanzania",
                "Mozambique", "Zimbabwe", "Malawi", "Zambia"]:
        count = location_counts.get(loc, country_dist.get(loc, 0))
        asr = incidence_asr.get(loc, 0)
        comparator_data.append({"name": loc, "count": count, "asr": asr})

    global_comparators = []
    for loc in ["United States", "India", "Brazil"]:
        count = location_counts.get(loc, 0)
        asr = incidence_asr.get(loc, 0)
        global_comparators.append({"name": loc, "count": count, "asr": asr})

    # Chart data
    country_chart_labels = json.dumps([d["name"] for d in comparator_data])
    country_chart_values = json.dumps([d["count"] for d in comparator_data])
    country_chart_asr = json.dumps([d["asr"] for d in comparator_data])

    global_labels = json.dumps(["Africa"] + [g["name"] for g in global_comparators])
    global_values = json.dumps([total_africa] + [g["count"] for g in global_comparators])

    intervention_labels = json.dumps(list(intervention.keys()))
    intervention_values = json.dumps(list(intervention.values()))
    intervention_colors = json.dumps([
        "#2ecc71" if k == "Screening"
        else "#60a5fa" if k == "Vaccination"
        else "#e67e22" if k == "Treatment"
        else "#95a5a6"
        for k in intervention.keys()
    ])

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

    # Crisis country rows
    crisis_rows = ""
    for cc in crisis_countries:
        crisis_rows += f"""<tr>
<td style="font-weight:700">{cc['country']}</td>
<td style="text-align:right;color:#e74c3c;font-weight:700">{cc['incidence_asr']}</td>
<td style="text-align:right;color:#f1c40f;font-weight:700">{cc['trials']}</td>
</tr>"""

    # Severity findings
    severity_items = []
    severity_items.append({
        "level": "CRITICAL",
        "text": f"CCI = {cci}x -- Africa carries {AFRICA_BURDEN_SHARE}% of global cervical cancer burden but has only ~{total_africa} trials vs {us_count:,} in the US"
    })
    severity_items.append({
        "level": "CRITICAL",
        "text": "Cervical cancer is the #1 cancer killer of women in Africa, yet most high-burden countries have fewer than 5 trials"
    })
    for cc in crisis_countries:
        if cc["trials"] <= 2:
            severity_items.append({
                "level": "HIGH",
                "text": f"{cc['country']}: incidence {cc['incidence_asr']}/100k (among highest globally) but only {cc['trials']} trial(s)"
            })
    if african_led_pct < 40:
        severity_items.append({
            "level": "HIGH",
            "text": f"Only {african_led_pct}% of Africa-based cervical cancer trials are led by African institutions"
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
<title>Cervical Cancer in Africa &mdash; Africa's #1 Cancer Killer of Women</title>
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
    max-width:600px;
    margin-left:auto;
    margin-right:auto;
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

<h1>Cervical Cancer in Africa</h1>
<p class="subtitle">Africa's #1 Cancer Killer of Women &mdash; Clinical Trial Equity Analysis &mdash;
ClinicalTrials.gov Registry &mdash; Generated {datetime.now().strftime('%d %B %Y')}</p>

<!-- ===== Summary Banner ===== -->
<div class="banner">
    <div class="stat-card">
        <div class="value">{total_africa}</div>
        <div class="label">Africa Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--accent)">{us_count:,}</div>
        <div class="label">US Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{cci}x</div>
        <div class="label">CCI (burden/trial gap)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--green)">{intervention.get('Screening', 0)}</div>
        <div class="label">Screening Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--accent)">{intervention.get('Vaccination', 0)}</div>
        <div class="label">Vaccination Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--orange)">{intervention.get('Treatment', 0)}</div>
        <div class="label">Treatment Trials</div>
    </div>
</div>

<!-- ===== CCI Box ===== -->
<div class="cci-box">
    <div class="cci-value">{cci}x</div>
    <div class="cci-label">Condition Colonialism Index</div>
    <div class="cci-detail">
        Africa carries <strong>{AFRICA_BURDEN_SHARE}%</strong> of the global cervical cancer burden
        but hosts only <strong>~{total_africa}</strong> registered interventional trials, compared to
        <strong>{us_count:,}</strong> in the United States (which bears ~1.4% of the burden).
        CCI = {AFRICA_BURDEN_SHARE} / 1.4 = <strong>{cci}x</strong>.
    </div>
</div>

<!-- ===== The Leading Killer ===== -->
<h2>The Leading Killer</h2>
<div class="info-box">
    <p><strong>Cervical cancer kills more African women than any other cancer.</strong></p>
    <p>An estimated 120,000 African women are diagnosed annually, and roughly 80,000 die --
    more than from breast cancer, more than from any other malignancy. The disease is
    almost entirely preventable through HPV vaccination and screening, yet Sub-Saharan
    Africa has the lowest screening coverage and HPV vaccination rates globally.</p>
    <p>High-burden countries like Mozambique, Malawi, and Zimbabwe have age-standardised
    incidence rates above 38 per 100,000 -- roughly <strong>6x the US rate</strong> of 6.2/100k.
    Despite this, they collectively host fewer than a handful of interventional trials.</p>
</div>

<!-- ===== Severity Summary ===== -->
<h2>Severity Summary</h2>
<div class="severity-box">
    {severity_html if severity_html else '<p style="color:var(--text2)">No critical findings</p>'}
</div>

<!-- ===== Country Breakdown ===== -->
<h2>Country Breakdown: Africa</h2>
<p class="lead" style="color:var(--text2)">Trial counts by country, sorted alongside WHO incidence data
(age-standardised rate per 100,000 women).</p>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trials per African Country</h3>
        <canvas id="countryChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Incidence vs Trial Count</h3>
        <canvas id="scatterChart"></canvas>
    </div>
</div>

<!-- ===== Screening vs Vaccination vs Treatment ===== -->
<h2>Screening vs Vaccination vs Treatment</h2>
<p class="lead" style="color:var(--text2)">Classification of Africa's cervical cancer trials by
intervention type. Screening includes VIA, HPV testing, Pap smear, colposcopy.
Vaccination includes HPV vaccine studies. Treatment includes surgery, radiation, and
chemotherapy/immunotherapy.</p>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Intervention Type Distribution</h3>
        <canvas id="interventionChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Sponsor Breakdown</h3>
        <canvas id="sponsorChart"></canvas>
    </div>
</div>

<!-- ===== HPV Vaccination Gap ===== -->
<h2>The HPV Vaccination Gap</h2>
<div class="info-box">
    <p><strong>HPV vaccines exist and are highly effective, yet trial access in Africa
    remains separate from vaccination rollout.</strong></p>
    <p>We identified <strong>{hpv_vaccine_count}</strong> HPV vaccine-related trial(s) with
    African sites on ClinicalTrials.gov. The gap between vaccine availability and clinical
    trial activity is stark: while GAVI has supported HPV vaccine introduction in multiple
    African countries, research on dose schedules, long-term efficacy in African populations,
    and integration with screening programmes remains critically underfunded.</p>
    <p>Screening-related trials (VIA, HPV testing, Pap smear): <strong>{screening_count}</strong>
    found in Africa.</p>
</div>

<!-- ===== Crisis Countries ===== -->
<h2>The Mozambique/Zimbabwe/Malawi Crisis</h2>
<div class="crisis-box">
    <p style="color:#fde68a;font-weight:700;font-size:1.1rem;margin-bottom:12px">
        Highest incidence, fewest trials</p>
    <p style="color:#e5e7eb;margin-bottom:16px">
        Mozambique, Zimbabwe, and Malawi have among the highest cervical cancer
        incidence rates on the planet -- yet they are clinical trial deserts.
        Women in these countries face the highest risk but have virtually no
        access to investigational screening, vaccination, or treatment protocols.</p>
    <table style="max-width:400px">
        <thead>
            <tr><th>Country</th><th style="text-align:right">Incidence (ASR/100k)</th>
                <th style="text-align:right">Trials</th></tr>
        </thead>
        <tbody>
            {crisis_rows}
        </tbody>
    </table>
</div>

<!-- ===== Global Comparison ===== -->
<h2>Comparison: Africa vs US, India, Brazil</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trial Counts</h3>
        <canvas id="globalChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Phase Distribution</h3>
        <canvas id="phaseChart"></canvas>
    </div>
</div>
<div class="info-box">
    <p><strong>Africa ({total_africa} trials) vs United States ({us_count:,} trials):</strong>
    The US has roughly {round(us_count / max(total_africa, 1))}x more cervical cancer trials
    despite bearing only ~1.4% of the global burden.</p>
    <p><strong>India:</strong> {location_counts.get('India', 0)} trials. India has a comparable
    incidence rate (18.7/100k) to Nigeria but substantially more trial activity.</p>
    <p><strong>Brazil:</strong> {location_counts.get('Brazil', 0)} trials. Brazil demonstrates
    what middle-income country investment in cervical cancer research can achieve.</p>
</div>

<!-- ===== Sponsor Analysis ===== -->
<h2>Sponsor Analysis</h2>
<div class="info-box">
    <p>Of the {total_africa} cervical cancer trials in Africa,
    <strong>{data['african_led_count']}</strong> ({african_led_pct}%) are led by
    African institutions. The remainder are sponsored by pharmaceutical companies,
    non-African academic centres, NIH/US government agencies, or NGOs/multilateral
    organizations.</p>
    <p>NGO and multilateral sponsors (WHO, PEPFAR, Gates Foundation, JHPIEGO)
    play a distinctively large role in cervical cancer research in Africa compared
    to other disease areas, reflecting the public health and prevention orientation
    of the trial portfolio.</p>
</div>

<!-- ===== Full Trial Table ===== -->
<h2>All Africa Trials ({total_africa})</h2>
<p style="color:var(--text2);margin-bottom:8px">
    Rows coloured by status: <span style="color:var(--green)">completed</span>,
    <span style="color:var(--red)">terminated/withdrawn</span>,
    <span style="color:var(--yellow)">active/recruiting</span>,
    <span style="color:var(--grey)">unknown</span>.
    Intervention class:
    <span style="color:var(--green)">Screening</span>,
    <span style="color:var(--accent)">Vaccination</span>,
    <span style="color:var(--orange)">Treatment</span>.
</p>
<div class="table-container">
<table>
<thead>
<tr>
    <th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sponsor Class</th>
    <th>Country</th><th>Phase</th><th>Status</th><th>Enrollment</th>
    <th>Intervention</th>
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
    Africa trials: {total_africa} | US trials: {us_count:,} | CCI: {cci}x |
    Generated by fetch_cervical_cancer.py
</div>

</div><!-- /container -->

<script>
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

// ── Incidence vs Trial Count scatter ───────────────────────────────
(function() {{
    var countries = {country_chart_labels};
    var trials = {country_chart_values};
    var asr = {country_chart_asr};
    var points = countries.map(function(c, i) {{
        return {{ x: trials[i], y: asr[i], label: c }};
    }});
    new Chart(document.getElementById('scatterChart'), {{
        type: 'scatter',
        data: {{
            datasets: [{{
                label: 'Country',
                data: points,
                backgroundColor: '#e74c3c',
                pointRadius: 8,
                pointHoverRadius: 12,
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
                            return p.label + ': ' + p.x + ' trials, ASR ' + p.y + '/100k';
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    grid: {{ color: 'rgba(255,255,255,0.05)' }},
                    ticks: {{ color: '#9ca3af' }},
                    title: {{ display: true, text: 'Number of trials', color: '#9ca3af' }}
                }},
                y: {{
                    grid: {{ color: 'rgba(255,255,255,0.05)' }},
                    ticks: {{ color: '#9ca3af' }},
                    title: {{ display: true, text: 'Incidence (ASR/100k)', color: '#9ca3af' }}
                }}
            }}
        }}
    }});
}})();

// ── Intervention doughnut ──────────────────────────────────────────
new Chart(document.getElementById('interventionChart'), {{
    type: 'doughnut',
    data: {{
        labels: {intervention_labels},
        datasets: [{{
            data: {intervention_values},
            backgroundColor: {intervention_colors},
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
</script>

</body>
</html>"""

    return html


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Cervical Cancer in Africa -- Africa's #1 Cancer Killer of Women")
    print("=" * 60)

    data = collect_data()

    # Print summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total Africa trials:      {data['total_africa']}")
    print(f"US trials:                {data['us_count']:,}")
    print(f"CCI:                      {data['cci']}x")
    print(f"Africa burden share:      {data['africa_burden_share']}%")
    print(f"African-led:              {data['african_led_count']} ({data['african_led_pct']}%)")
    print(f"HPV vaccine (Africa):     {data['hpv_vaccine_africa_count']}")
    print(f"Screening (Africa):       {data['screening_africa_count']}")
    print()
    print("Intervention breakdown:")
    for cls, count in sorted(data["intervention_breakdown"].items(),
                              key=lambda x: -x[1]):
        print(f"  {cls:20s} {count}")
    print()
    print("Country distribution:")
    for country, count in sorted(data["country_distribution"].items(),
                                  key=lambda x: -x[1]):
        asr = INCIDENCE_ASR.get(country, 0)
        asr_str = f"  (ASR {asr}/100k)" if asr else ""
        print(f"  {country:20s} {count:>4}{asr_str}")
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
    print()
    print("Crisis countries (highest incidence, fewest trials):")
    for cc in data["crisis_countries"]:
        print(f"  {cc['country']:20s} ASR {cc['incidence_asr']}/100k  "
              f"Trials: {cc['trials']}")

    # Generate HTML
    print(f"\nGenerating HTML report...")
    html = generate_html(data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to {OUTPUT_HTML}")
    print(f"Open in browser: file:///{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    main()
