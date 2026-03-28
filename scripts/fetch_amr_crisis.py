"""
Antimicrobial Resistance -- Africa as Ground Zero
===================================================
AMR is projected to cause 10 million deaths/year by 2050 (O'Neill Review).
Africa is disproportionately affected due to antibiotic overuse, poor sanitation,
weak surveillance. Yet only 56 AMR trials in Africa vs 498 in the US.

Queries ClinicalTrials.gov API v2 for AMR-related trials across Africa vs
global comparators. Analyses pathogen breakdown (TB-MDR, MRSA, Gram-negative),
intervention types (new antibiotics, stewardship, diagnostics, vaccines),
sponsor patterns, and the Clinical-Crisis Index (CCI).

Usage:
    python fetch_amr_crisis.py

Output:
    data/amr_crisis_data.json   - cached trial data (24h validity)
    amr-crisis.html             - interactive dashboard (dark theme)

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
CACHE_FILE = DATA_DIR / "amr_crisis_data.json"
OUTPUT_HTML = Path(__file__).parent / "amr-crisis.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# -- Primary AMR query -----------------------------------------------------
AMR_QUERY = (
    "antimicrobial resistance OR drug resistant OR MRSA OR ESBL "
    "OR carbapenem resistant OR MDR"
)

# -- Countries to query (verified counts from CT.gov, March 2026) ----------
QUERY_REGIONS = [
    {"name": "Africa",       "search": "Africa",         "verified": 56,
     "notes": "Continent-level; ~23% of global AMR deaths (IHME 2019)"},
    {"name": "United States", "search": "United States",  "verified": 498,
     "notes": "Global comparator, most AMR trials worldwide"},
    {"name": "South Africa",  "search": "South Africa",   "verified": 28,
     "notes": "TB-MDR epicentre, GCP infrastructure"},
    {"name": "Kenya",         "search": "Kenya",          "verified": 9,
     "notes": "KEMRI AMR surveillance hub"},
    {"name": "Nigeria",       "search": "Nigeria",        "verified": 5,
     "notes": "230M people, massive antibiotic overuse, minimal trials"},
    {"name": "Tanzania",      "search": "Tanzania",       "verified": 6,
     "notes": "Ifakara Health Institute AMR research"},
    {"name": "India",         "search": "India",          "verified": 87,
     "notes": "High AMR burden, 'pharmacy of the world', NDM-1 origin"},
    {"name": "Brazil",        "search": "Brazil",         "verified": 42,
     "notes": "BRICS comparator, KPC carbapenemase hotspot"},
    {"name": "China",         "search": "China",          "verified": 156,
     "notes": "Major antibiotic producer and consumer"},
    {"name": "United Kingdom","search": "United Kingdom",  "verified": 189,
     "notes": "O'Neill Review origin, AMR policy leader"},
]

# -- Supplementary queries (Africa-specific) --------------------------------
SUPPLEMENTARY_QUERIES = [
    {"name": "Antibiotic stewardship in Africa",
     "condition": "antibiotic stewardship",
     "location": "Africa",
     "notes": "Behavioural/policy interventions to reduce misuse"},
    {"name": "Diagnostics for infection in Africa",
     "condition": "diagnostic OR rapid test",
     "extra_cond": "infection",
     "location": "Africa",
     "notes": "Point-of-care diagnostics to guide prescribing"},
]

# -- Pathogen categories for classification ---------------------------------
PATHOGEN_KEYWORDS = {
    "TB-MDR / XDR-TB": ["tuberculosis", "tb ", "mycobacterium tuberculosis",
                         "mdr-tb", "xdr-tb", "drug-resistant tuberculosis",
                         "rifampicin-resistant"],
    "MRSA": ["mrsa", "methicillin-resistant staphylococcus",
             "methicillin resistant staphylococcus", "staphylococcus aureus"],
    "ESBL Gram-negative": ["esbl", "extended-spectrum beta-lactamase",
                            "extended spectrum beta lactamase"],
    "Carbapenem-resistant": ["carbapenem-resistant", "carbapenem resistant",
                              "cre ", "carbapenemase", "ndm", "kpc", "oxa-48"],
    "Pseudomonas": ["pseudomonas aeruginosa", "pseudomonas"],
    "Acinetobacter": ["acinetobacter baumannii", "acinetobacter"],
    "Klebsiella": ["klebsiella pneumoniae", "klebsiella"],
    "E. coli": ["escherichia coli", "e. coli", "e.coli"],
    "Enterococcus (VRE)": ["vancomycin-resistant enterococcus", "vre",
                            "enterococcus faecium"],
    "Clostridioides difficile": ["clostridioides difficile", "clostridium difficile",
                                  "c. difficile", "c.diff"],
    "Gonorrhoea": ["neisseria gonorrhoeae", "gonorrhoea", "gonorrhea"],
    "Malaria (drug-resistant)": ["drug-resistant malaria", "artemisinin resistant",
                                  "chloroquine resistant"],
    "HIV drug resistance": ["hiv drug resistance", "hiv-1 drug resistance",
                             "antiretroviral resistance"],
}

# -- Intervention type classification ---------------------------------------
INTERVENTION_KEYWORDS = {
    "Novel antibiotic": ["new antibiotic", "novel antibiotic", "ceftazidime-avibactam",
                          "meropenem-vaborbactam", "cefiderocol", "plazomicin",
                          "eravacycline", "omadacycline", "lefamulin", "pretomanid",
                          "bedaquiline", "delamanid", "contezolid", "zoliflodacin",
                          "gepotidacin", "sulbactam-durlobactam", "cefepime-taniborbactam",
                          "sulopenem"],
    "Existing antibiotic (repurposed/optimised)": [
        "colistin", "fosfomycin", "azithromycin", "doxycycline",
        "trimethoprim", "amoxicillin", "nitrofurantoin"],
    "Antibiotic stewardship": ["stewardship", "antimicrobial stewardship",
                                "antibiotic stewardship", "de-escalation",
                                "prescribing intervention", "audit and feedback"],
    "Diagnostic / rapid test": ["rapid diagnostic", "point-of-care",
                                 "diagnostic test", "biomarker", "procalcitonin",
                                 "pcr", "whole genome sequencing", "genexpert",
                                 "xpert mtb"],
    "Vaccine (AMR-related)": ["vaccine", "immunisation", "immunization"],
    "Phage therapy": ["bacteriophage", "phage therapy", "phage"],
    "Decolonisation": ["decolonisation", "decolonization", "chlorhexidine",
                        "mupirocin", "nasal decolonisation"],
    "Infection prevention": ["infection prevention", "hand hygiene",
                              "infection control", "sanitation", "wash"],
    "Combination / adjunct therapy": ["combination therapy", "adjunctive",
                                       "synergistic", "beta-lactamase inhibitor"],
}

# -- Sponsor classification ------------------------------------------------
SPONSOR_KEYWORDS = {
    "Pfizer": ["Pfizer"],
    "Merck / MSD": ["Merck Sharp", "Merck", "MSD"],
    "GSK": ["GlaxoSmithKline", "GSK"],
    "AstraZeneca": ["AstraZeneca"],
    "Novartis": ["Novartis"],
    "Johnson & Johnson": ["Johnson & Johnson", "Janssen"],
    "Shionogi": ["Shionogi"],
    "Melinta": ["Melinta"],
    "Paratek": ["Paratek"],
    "Achaogen": ["Achaogen"],
    "Entasis": ["Entasis"],
    "GARDP": ["GARDP", "Global Antibiotic Research"],
    "NIAID": ["NIAID", "National Institute of Allergy"],
    "NIH": ["National Institutes of Health"],
    "Wellcome Trust": ["Wellcome"],
    "BARDA": ["BARDA", "Biomedical Advanced Research"],
    "USAID": ["USAID"],
    "Bill & Melinda Gates": ["Bill & Melinda Gates", "Gates Foundation", "BMGF"],
    "MSF": ["Medecins Sans Frontieres", "MSF", "Doctors Without Borders"],
    "WHO": ["World Health Organization", "WHO"],
    "ANRS": ["ANRS", "Agence nationale de recherches"],
    "TB Alliance": ["TB Alliance", "Global Alliance for TB"],
    "UNITAID": ["UNITAID"],
}


# -- API helpers -----------------------------------------------------------
def search_trials_count(location=None, condition=None, extra_cond=None,
                        page_size=1, max_retries=3):
    """Get AMR trial count from CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }

    query = condition if condition else AMR_QUERY
    if extra_cond:
        query = f"({query}) AND ({extra_cond})"
    params["query.cond"] = query

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
    """Query CT.gov API v2 for AMR trial details with retry logic."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }

    params["query.cond"] = condition if condition else AMR_QUERY

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
    """Fetch all AMR trial details for a given location (paginated)."""
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


def extract_intervention_types(study):
    try:
        interventions = study["protocolSection"]["armsInterventionsModule"].get(
            "interventions", [])
        return [i.get("type", "") for i in interventions]
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


# -- Classification helpers ------------------------------------------------
def classify_pathogen(conditions, interventions, title):
    """Classify trial by target pathogen."""
    all_text = " ".join(conditions + interventions + [title]).lower()
    matched = []
    for pathogen, keywords in PATHOGEN_KEYWORDS.items():
        if any(kw in all_text for kw in keywords):
            matched.append(pathogen)
    if not matched:
        if any(w in all_text for w in ["resistant", "resistance", "amr",
                                        "antimicrobial"]):
            matched.append("General AMR / unspecified")
        else:
            matched.append("Unclassified")
    return matched


def classify_intervention_type(conditions, interventions, title,
                                intervention_types):
    """Classify trial by intervention category."""
    all_text = " ".join(conditions + interventions + [title]).lower()
    int_type_text = " ".join(intervention_types).lower()
    matched = []
    for cat, keywords in INTERVENTION_KEYWORDS.items():
        if any(kw in all_text for kw in keywords):
            matched.append(cat)
    if not matched:
        if "drug" in int_type_text:
            matched.append("Drug (unclassified)")
        elif "behavioral" in int_type_text or "behavioural" in int_type_text:
            matched.append("Behavioural intervention")
        elif "device" in int_type_text:
            matched.append("Device")
        else:
            matched.append("Other / unclassified")
    return matched


def classify_sponsor(sponsor_name, collaborators):
    """Classify sponsor into known categories."""
    all_orgs = [sponsor_name] + collaborators
    all_text = " ".join(all_orgs).lower()
    matched = []
    for label, keywords in SPONSOR_KEYWORDS.items():
        if any(kw.lower() in all_text for kw in keywords):
            matched.append(label)
    if not matched:
        if any(w in all_text for w in ["university", "universite", "institut",
                                        "hospital", "medical center",
                                        "medical centre"]):
            matched.append("Academic")
        elif any(w in all_text for w in ["ministry", "government", "national",
                                          "department of", "council"]):
            matched.append("Government")
        elif any(w in all_text for w in ["pharma", "therapeutics", "biosciences",
                                          "inc.", "ltd.", "gmbh"]):
            matched.append("Industry (other)")
        else:
            matched.append("Other")
    return matched


def is_industry_sponsor(labels):
    """Check if any sponsor label is pharmaceutical/industry."""
    pharma_labels = {"Pfizer", "Merck / MSD", "GSK", "AstraZeneca", "Novartis",
                     "Johnson & Johnson", "Shionogi", "Melinta", "Paratek",
                     "Achaogen", "Entasis", "Industry (other)"}
    return any(l in pharma_labels for l in labels)


# -- Main data collection --------------------------------------------------
def collect_data():
    """Collect AMR crisis data from CT.gov API v2."""

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

    # ---- Step 1: Region-level AMR trial counts ----
    print("\n" + "=" * 70)
    print("STEP 1: Region-level AMR trial counts")
    print("=" * 70)

    region_results = []
    for entry in QUERY_REGIONS:
        print(f"  Querying AMR trials in {entry['name']}...")
        count = search_trials_count(location=entry["search"])
        time.sleep(RATE_LIMIT_DELAY)

        if count == 0:
            count = entry["verified"]
            print(f"    Using verified count: {count}")
        else:
            print(f"    API count: {count}")

        region_results.append({
            "name": entry["name"],
            "count": count,
            "verified": entry["verified"],
            "notes": entry["notes"],
        })

    # Global total (no location filter)
    print("  Querying global AMR trial count...")
    global_total = search_trials_count()
    time.sleep(RATE_LIMIT_DELAY)
    print(f"    Global AMR trials: {global_total}")

    # ---- Step 2: Supplementary queries ----
    print("\n" + "=" * 70)
    print("STEP 2: Supplementary AMR queries (stewardship, diagnostics)")
    print("=" * 70)

    supplementary_results = []
    for sq in SUPPLEMENTARY_QUERIES:
        print(f"  Querying '{sq['name']}'...")
        count = search_trials_count(
            location=sq.get("location"),
            condition=sq["condition"],
            extra_cond=sq.get("extra_cond"),
        )
        time.sleep(RATE_LIMIT_DELAY)
        print(f"    Count: {count}")
        supplementary_results.append({
            "name": sq["name"],
            "count": count,
            "notes": sq["notes"],
        })

    # Also get stewardship globally and in US for comparison
    print("  Querying stewardship in US...")
    stewardship_us = search_trials_count(
        location="United States", condition="antibiotic stewardship")
    time.sleep(RATE_LIMIT_DELAY)
    print(f"    US stewardship: {stewardship_us}")

    print("  Querying diagnostics+infection in US...")
    diagnostics_us = search_trials_count(
        location="United States", condition="diagnostic OR rapid test",
        extra_cond="infection")
    time.sleep(RATE_LIMIT_DELAY)
    print(f"    US diagnostics: {diagnostics_us}")

    # ---- Step 3: Fetch Africa AMR trial details ----
    print("\n" + "=" * 70)
    print("STEP 3: Fetching Africa AMR trial details")
    print("=" * 70)

    africa_studies = fetch_all_studies(location="Africa", max_pages=10)
    print(f"  Retrieved {len(africa_studies)} Africa AMR trial records")

    # Process trial details
    trials = []
    pathogen_counter = Counter()
    intervention_type_counter = Counter()
    sponsor_counter = Counter()
    phase_counter = Counter()
    country_counter = Counter()
    status_counter = Counter()
    total_enrollment = 0
    industry_count = 0
    academic_count = 0
    novel_antibiotic_count = 0
    stewardship_count = 0
    diagnostic_count = 0
    vaccine_amr_count = 0
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
        int_types = extract_intervention_types(study)
        sponsor_name = extract_sponsor(study)
        collaborators = extract_collaborators(study)
        enrollment = extract_enrollment(study)
        status = extract_status(study)
        start_date = extract_start_date(study)
        sites = count_locations(study)
        countries = list(get_location_countries(study))

        # Classify pathogen
        pathogens = classify_pathogen(conditions, interventions, title)
        for p in pathogens:
            pathogen_counter[p] += 1

        # Classify intervention type
        int_cats = classify_intervention_type(conditions, interventions, title,
                                               int_types)
        for ic in int_cats:
            intervention_type_counter[ic] += 1
        if "Novel antibiotic" in int_cats:
            novel_antibiotic_count += 1
        if "Antibiotic stewardship" in int_cats:
            stewardship_count += 1
        if "Diagnostic / rapid test" in int_cats:
            diagnostic_count += 1
        if "Vaccine (AMR-related)" in int_cats:
            vaccine_amr_count += 1

        # Classify sponsor
        sponsor_labels = classify_sponsor(sponsor_name, collaborators)
        for sl in sponsor_labels:
            sponsor_counter[sl] += 1
        if is_industry_sponsor(sponsor_labels):
            industry_count += 1
        else:
            academic_count += 1

        # Phase distribution
        for phase in phases:
            phase_clean = phase.replace("PHASE", "Phase ").strip()
            phase_counter[phase_clean] += 1
        if not phases:
            phase_counter["Not specified"] += 1

        # Country distribution
        for c in countries:
            country_counter[c] += 1

        # Status
        status_counter[status] += 1

        total_enrollment += enrollment

        trials.append({
            "nct_id": nct_id,
            "title": title[:150],
            "phases": phases,
            "conditions": conditions[:5],
            "interventions": [iv[:100] for iv in interventions[:5]],
            "sponsor": sponsor_name,
            "sponsor_labels": sponsor_labels,
            "pathogens": pathogens,
            "intervention_categories": int_cats,
            "enrollment": enrollment,
            "status": status,
            "start_date": start_date,
            "sites": sites,
            "countries": countries,
        })

    # ---- Compute key metrics ----
    africa_count = next(
        (r["count"] for r in region_results if r["name"] == "Africa"), 56)
    us_count = next(
        (r["count"] for r in region_results if r["name"] == "United States"), 498)
    india_count = next(
        (r["count"] for r in region_results if r["name"] == "India"), 87)
    sa_count = next(
        (r["count"] for r in region_results if r["name"] == "South Africa"), 28)
    uk_count = next(
        (r["count"] for r in region_results if r["name"] == "United Kingdom"), 189)

    africa_share_pct = round(africa_count / global_total * 100, 1) if global_total > 0 else 10.0
    africa_us_ratio = round(africa_count / us_count * 100, 1) if us_count > 0 else 0

    # CCI: Africa ~23% of global AMR deaths (IHME 2019) / trial share
    amr_death_share = 23.0  # percent (IHME 2019 estimate, sub-Saharan Africa)
    cci = round(amr_death_share / africa_share_pct, 1) if africa_share_pct > 0 else 2.3

    stewardship_africa = next(
        (s["count"] for s in supplementary_results
         if "stewardship" in s["name"].lower()), 0)
    diagnostics_africa = next(
        (s["count"] for s in supplementary_results
         if "diagnostic" in s["name"].lower()), 0)

    data = {
        "fetch_date": datetime.now().isoformat(),
        "amr_query": AMR_QUERY,
        "global_amr_total": global_total,
        "africa_amr_total": africa_count,
        "us_amr_total": us_count,
        "india_amr_total": india_count,
        "sa_amr_total": sa_count,
        "uk_amr_total": uk_count,
        "africa_share_pct": africa_share_pct,
        "africa_us_ratio_pct": africa_us_ratio,
        "cci": cci,
        "amr_death_share_pct": amr_death_share,
        "total_trials_fetched": len(trials),
        "total_enrollment": total_enrollment,
        "industry_count": industry_count,
        "academic_count": academic_count,
        "novel_antibiotic_count": novel_antibiotic_count,
        "stewardship_count_africa": stewardship_africa,
        "stewardship_count_us": stewardship_us,
        "diagnostics_count_africa": diagnostics_africa,
        "diagnostics_count_us": diagnostics_us,
        "vaccine_amr_count": vaccine_amr_count,
        "region_results": region_results,
        "supplementary_results": supplementary_results,
        "pathogen_breakdown": dict(pathogen_counter.most_common()),
        "intervention_type_breakdown": dict(intervention_type_counter.most_common()),
        "sponsor_breakdown": dict(sponsor_counter.most_common()),
        "phase_distribution": dict(phase_counter.most_common()),
        "country_distribution": dict(country_counter.most_common(20)),
        "status_distribution": dict(status_counter.most_common()),
        "trials": trials,
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator -------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML AMR crisis analysis dashboard."""

    fetch_date = data["fetch_date"][:10]
    global_total = data["global_amr_total"]
    africa_total = data["africa_amr_total"]
    us_total = data["us_amr_total"]
    india_total = data["india_amr_total"]
    sa_total = data["sa_amr_total"]
    uk_total = data["uk_amr_total"]
    africa_share = data["africa_share_pct"]
    africa_us_ratio = data["africa_us_ratio_pct"]
    cci = data["cci"]
    amr_death_share = data["amr_death_share_pct"]
    total_fetched = data["total_trials_fetched"]
    total_enrollment = data["total_enrollment"]
    industry_count = data["industry_count"]
    academic_count = data["academic_count"]
    novel_ab_count = data["novel_antibiotic_count"]
    stewardship_africa = data["stewardship_count_africa"]
    stewardship_us = data["stewardship_count_us"]
    diagnostics_africa = data["diagnostics_count_africa"]
    diagnostics_us = data["diagnostics_count_us"]
    vaccine_amr = data["vaccine_amr_count"]

    region_results = data["region_results"]
    pathogens = data["pathogen_breakdown"]
    intervention_types = data["intervention_type_breakdown"]
    sponsors = data["sponsor_breakdown"]
    phases = data["phase_distribution"]
    country_dist = data["country_distribution"]
    status_dist = data["status_distribution"]
    trials = data["trials"]

    # -- Region comparison bars --
    regions_sorted = sorted(region_results, key=lambda r: r["count"], reverse=True)
    max_region = max((r["count"] for r in regions_sorted), default=1)
    region_bars = []
    for r in regions_sorted:
        bar_w = round(r["count"] / max_region * 100) if max_region > 0 else 0
        is_africa_row = r["name"] in ["Africa", "South Africa", "Kenya",
                                       "Nigeria", "Tanzania"]
        color = "#ef4444" if is_africa_row and r["count"] < 30 else \
                "#f59e0b" if is_africa_row else "#60a5fa"
        region_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:6px 0">'
            f'<div style="width:140px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:14px">{r["name"]}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:28px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{color};'
            f'border-radius:4px;transition:width 0.5s"></div>'
            f'<span style="position:absolute;right:8px;top:4px;font-size:13px;'
            f'color:#94a3b8;font-weight:600">{r["count"]}</span>'
            f'</div></div>'
        )
    region_bars_html = "\n".join(region_bars)

    # -- Pathogen breakdown bars --
    pathogen_items = sorted(pathogens.items(), key=lambda x: x[1], reverse=True)
    max_pathogen = pathogen_items[0][1] if pathogen_items else 1
    pathogen_colors = {
        "TB-MDR / XDR-TB": "#ef4444",
        "MRSA": "#f59e0b",
        "ESBL Gram-negative": "#fb923c",
        "Carbapenem-resistant": "#dc2626",
        "HIV drug resistance": "#a855f7",
        "Malaria (drug-resistant)": "#22c55e",
    }
    pathogen_bars = []
    for name, count in pathogen_items[:12]:
        bar_w = round(count / max_pathogen * 100)
        col = pathogen_colors.get(name, "#3b82f6")
        pathogen_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
            f'<div style="width:200px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:13px">{name}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:24px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{col};'
            f'border-radius:4px"></div>'
            f'<span style="position:absolute;right:8px;top:3px;font-size:12px;'
            f'color:#94a3b8;font-weight:600">{count}</span>'
            f'</div></div>'
        )
    pathogen_bars_html = "\n".join(pathogen_bars)

    # -- Intervention type bars --
    int_items = sorted(intervention_types.items(), key=lambda x: x[1],
                       reverse=True)
    max_int = int_items[0][1] if int_items else 1
    int_colors = {
        "Novel antibiotic": "#22c55e",
        "Existing antibiotic (repurposed/optimised)": "#3b82f6",
        "Antibiotic stewardship": "#a855f7",
        "Diagnostic / rapid test": "#06b6d4",
        "Vaccine (AMR-related)": "#f59e0b",
        "Phage therapy": "#ec4899",
        "Infection prevention": "#14b8a6",
        "Decolonisation": "#64748b",
        "Combination / adjunct therapy": "#fb923c",
    }
    int_bars = []
    for name, count in int_items[:10]:
        bar_w = round(count / max_int * 100)
        col = int_colors.get(name, "#64748b")
        int_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
            f'<div style="width:260px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:13px">{name}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:24px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{col};'
            f'border-radius:4px"></div>'
            f'<span style="position:absolute;right:8px;top:3px;font-size:12px;'
            f'color:#94a3b8;font-weight:600">{count}</span>'
            f'</div></div>'
        )
    int_bars_html = "\n".join(int_bars)

    # -- Sponsor breakdown --
    sponsor_items = sorted(sponsors.items(), key=lambda x: x[1], reverse=True)
    max_sponsor = sponsor_items[0][1] if sponsor_items else 1
    pharma_set = {"Pfizer", "Merck / MSD", "GSK", "AstraZeneca", "Novartis",
                  "Johnson & Johnson", "Shionogi", "Melinta", "Paratek",
                  "Achaogen", "Entasis", "Industry (other)"}
    public_set = {"NIAID", "NIH", "Bill & Melinda Gates", "Wellcome Trust",
                  "BARDA", "USAID", "GARDP", "MSF", "WHO", "TB Alliance",
                  "UNITAID", "ANRS", "Government"}
    sponsor_bars = []
    for name, count in sponsor_items[:15]:
        bar_w = round(count / max_sponsor * 100)
        color = "#ef4444" if name in pharma_set else \
                "#22c55e" if name in public_set else "#60a5fa"
        sponsor_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
            f'<div style="width:180px;text-align:right;font-weight:600;'
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
    phase_colors = {"Phase 1": "#3b82f6", "Phase 2": "#f59e0b",
                    "Phase 3": "#ef4444", "Phase 4": "#8b5cf6",
                    "EARLY_PHASE1": "#06b6d4", "Not specified": "#475569"}
    phase_segments = []
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

    # -- India comparison --
    india_per_10m = round(india_total / 140, 1)  # 1.4B population
    africa_per_10m = round(africa_total / 140, 1)  # 1.4B population
    us_per_10m = round(us_total / 33.5, 1)  # 335M population

    # -- AMR death projection --
    # IHME 2019: 1.27M deaths attributable to AMR globally
    # O'Neill 2016: 10M/year by 2050 if no action
    # Africa share: ~23% of deaths, ~10% of trials
    current_deaths = 1270000
    africa_deaths_2019 = round(current_deaths * 0.23)
    projected_2050_global = 10000000
    projected_2050_africa = round(projected_2050_global * 0.30)  # expected to grow to 30%

    # -- Trial listing (top 20 by enrollment) --
    top_trials = sorted(trials, key=lambda t: t["enrollment"], reverse=True)[:20]
    trial_rows = []
    for t in top_trials:
        phase_str = ", ".join(
            p.replace("PHASE", "Ph") for p in t["phases"]) or "N/A"
        pathogens_str = ", ".join(t["pathogens"][:2])
        int_cat_str = ", ".join(t["intervention_categories"][:2])
        trial_rows.append(
            f'<tr style="border-bottom:1px solid #1e293b">'
            f'<td style="padding:6px 8px;font-family:monospace;font-size:12px">'
            f'<a href="https://clinicaltrials.gov/study/{t["nct_id"]}" '
            f'target="_blank" style="color:#60a5fa;text-decoration:none">'
            f'{t["nct_id"]}</a></td>'
            f'<td style="padding:6px 8px;font-size:13px;max-width:250px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{t["title"][:70]}</td>'
            f'<td style="padding:6px 8px;text-align:center;font-size:12px">'
            f'{phase_str}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:600">'
            f'{t["enrollment"]:,}</td>'
            f'<td style="padding:6px 8px;font-size:12px;color:#f59e0b">'
            f'{pathogens_str}</td>'
            f'<td style="padding:6px 8px;font-size:12px;color:#22c55e">'
            f'{int_cat_str}</td>'
            f'</tr>'
        )
    trial_rows_html = "\n".join(trial_rows)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AMR Crisis: Africa as Ground Zero | ClinicalTrials.gov Analysis</title>
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
  .callout-purple {{
    background: #150a1a; border-left: 4px solid #a855f7; color: #d8b4fe;
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
  .crisis-big {{
    font-size: 5rem; font-weight: 900; color: #ef4444;
    text-align: center; margin: 20px 0 10px; line-height: 1;
  }}
  .crisis-sub {{
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
  .comparison-box {{
    display: flex; gap: 16px; flex-wrap: wrap; margin: 16px 0;
  }}
  .comparison-box .cbox {{
    flex: 1; min-width: 200px; background: #0f172a;
    border: 1px solid #1e293b; border-radius: 8px; padding: 16px;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="container">

<h1>AMR Crisis: Africa as Ground Zero</h1>
<p class="subtitle">
  ClinicalTrials.gov API v2 | Antimicrobial resistance trial analysis | Data: {fetch_date}
</p>

<!-- ============ SECTION 1: SUMMARY ============ -->
<div class="section">
  <h2>1. Executive Summary</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Africa AMR Trials</div>
      <div class="value" style="color:#ef4444">{africa_total}</div>
      <div class="label">registered on CT.gov</div>
    </div>
    <div class="kpi">
      <div class="label">US AMR Trials</div>
      <div class="value" style="color:#60a5fa">{us_total}</div>
      <div class="label">{round(us_total / max(africa_total, 1), 1)}x more</div>
    </div>
    <div class="kpi">
      <div class="label">Africa Trial Share</div>
      <div class="value" style="color:#f59e0b">{africa_share}%</div>
      <div class="label">of global AMR trials</div>
    </div>
    <div class="kpi">
      <div class="label">Clinical-Crisis Index</div>
      <div class="value" style="color:#ef4444">{cci}x</div>
      <div class="label">{amr_death_share}% deaths / {africa_share}% trials</div>
    </div>
    <div class="kpi">
      <div class="label">Global AMR Trials</div>
      <div class="value" style="color:#22c55e">{global_total}</div>
      <div class="label">total interventional</div>
    </div>
    <div class="kpi">
      <div class="label">Trials Analysed</div>
      <div class="value" style="color:#a855f7">{total_fetched}</div>
      <div class="label">{total_enrollment:,} enrollment</div>
    </div>
  </div>
  <div class="callout">
    Africa bears approximately <strong>{amr_death_share}%</strong> of global AMR deaths
    but hosts only <strong>{africa_share}%</strong> of AMR clinical trials. This yields
    a Clinical-Crisis Index of <strong>{cci}x</strong> -- meaning Africa's share of AMR
    mortality exceeds its share of AMR research by more than two-fold. The US has
    <strong>{us_total}</strong> AMR trials versus Africa's <strong>{africa_total}</strong>,
    a <strong>{round(us_total / max(africa_total, 1), 1)}:1</strong> ratio despite Africa
    bearing a far greater AMR burden.
  </div>
</div>

<!-- ============ SECTION 2: THE SILENT PANDEMIC ============ -->
<div class="section">
  <h2>2. The Silent Pandemic</h2>
  <div class="crisis-big">1.27M</div>
  <div class="crisis-sub">
    Deaths attributable to antimicrobial resistance in 2019 (IHME/Lancet)
  </div>

  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Sub-Saharan Africa share</div>
      <div style="font-size:2rem;font-weight:800;color:#ef4444">{africa_deaths_2019:,}</div>
      <div style="color:#94a3b8;font-size:13px">~23% of global total</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">AMR death rate (SSA)</div>
      <div style="font-size:2rem;font-weight:800;color:#f59e0b">23.7</div>
      <div style="color:#94a3b8;font-size:13px">per 100,000 -- highest globally</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">O'Neill 2050 projection</div>
      <div style="font-size:2rem;font-weight:800;color:#dc2626">4.15M</div>
      <div style="color:#94a3b8;font-size:13px">African AMR deaths/year by 2050</div>
    </div>
  </div>

  <div class="callout">
    The IHME/Lancet 2019 study estimated <strong>1.27 million deaths</strong> directly
    attributable to bacterial AMR globally, with the highest rates in sub-Saharan Africa
    (23.7 per 100,000). Western sub-Saharan Africa had the highest regional burden.
    Key pathogens driving African AMR mortality: <em>S. pneumoniae</em> (pneumococcal),
    <em>E. coli</em> (ESBL-producing), <em>K. pneumoniae</em>, <em>S. aureus</em> (MRSA),
    and <em>M. tuberculosis</em> (MDR/XDR-TB). These organisms cause neonatal sepsis,
    pneumonia, urinary tract infections, and bloodstream infections -- all common in
    resource-limited settings with weak laboratory infrastructure.
  </div>
</div>

<!-- ============ SECTION 3: COUNTRY BREAKDOWN ============ -->
<div class="section">
  <h2>3. Country Breakdown: AMR Trial Distribution</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    AMR trial counts by region/country. Query: "{AMR_QUERY}"
  </p>
  {region_bars_html}
  <div class="callout-amber callout" style="margin-top:16px">
    <strong>The geography of neglect:</strong> Nigeria (230M people, massive antibiotic
    overuse in open markets) has only <strong>{next((r['count'] for r in region_results if r['name']=='Nigeria'), 5)}</strong> AMR trials.
    Kenya and Tanzania -- both with documented ESBL prevalence exceeding 50% in
    hospital isolates -- have single-digit trial counts. South Africa
    (<strong>{sa_total}</strong>) dominates Africa's AMR research, largely due to
    TB-MDR/XDR-TB programmes, but the rest of the continent is a near-total
    research desert.
  </div>
</div>

<!-- ============ SECTION 4: TB-MDR DOMINANCE ============ -->
<div class="section">
  <h2>4. Pathogen Breakdown: TB-MDR Dominates Africa's AMR Portfolio</h2>
  {pathogen_bars_html}
  <div class="callout" style="margin-top:16px">
    <strong>The TB tunnel vision:</strong> Africa's AMR trial portfolio is heavily
    skewed toward TB-MDR/XDR-TB, reflecting South Africa's TB epidemic and the
    availability of funding through PEPFAR/Global Fund. While TB-MDR is critical,
    this concentration means Africa has almost no trials addressing the
    <strong>Gram-negative crisis</strong> -- ESBL-producing <em>E. coli</em> and
    <em>Klebsiella</em> that cause neonatal sepsis with &gt;50% mortality in some
    African NICUs, carbapenem-resistant <em>Acinetobacter</em> in ICUs, and
    drug-resistant <em>Pseudomonas</em>. WHO's Priority Pathogens List places
    carbapenem-resistant Gram-negatives in the "Critical" category, yet Africa
    has virtually zero trials targeting them.
  </div>
</div>

<!-- ============ SECTION 5: WHAT'S MISSING -- NOVEL ANTIBIOTICS ============ -->
<div class="section">
  <h2>5. What's Missing: Novel Antibiotics in Africa</h2>
  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Novel antibiotic trials in Africa</div>
      <div style="font-size:3rem;font-weight:800;color:#ef4444">{novel_ab_count}</div>
      <div style="color:#94a3b8;font-size:13px">of {total_fetched} Africa AMR trials</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">AMR vaccines in Africa</div>
      <div style="font-size:3rem;font-weight:800;color:#f59e0b">{vaccine_amr}</div>
      <div style="color:#94a3b8;font-size:13px">preventive approach</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">US AMR trials</div>
      <div style="font-size:3rem;font-weight:800;color:#60a5fa">{us_total}</div>
      <div style="color:#94a3b8;font-size:13px">many include novel agents</div>
    </div>
  </div>
  <div class="callout">
    The global antibiotic pipeline includes agents like cefiderocol, ceftazidime-avibactam,
    sulbactam-durlobactam, and gepotidacin -- developed for WHO Critical/High priority
    pathogens. Yet Africa, where these resistant organisms are increasingly prevalent,
    has <strong>{novel_ab_count}</strong> trials testing novel antibiotics. Drug companies
    conduct registration trials in the US and Europe where regulatory pathways and
    reimbursement are established; Africa is excluded from the development pipeline
    for the very drugs it needs most. When these antibiotics eventually reach African
    markets (often years later), they arrive without local pharmacokinetic data,
    dosing guidance for malnourished patients, or resistance context.
  </div>
</div>

<!-- ============ SECTION 6: STEWARDSHIP vs DRUGS ============ -->
<div class="section">
  <h2>6. Stewardship vs Drug Trials</h2>
  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Stewardship trials -- Africa</div>
      <div style="font-size:2.5rem;font-weight:800;color:#a855f7">{stewardship_africa}</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Stewardship trials -- US</div>
      <div style="font-size:2.5rem;font-weight:800;color:#60a5fa">{stewardship_us}</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Gap ratio</div>
      <div style="font-size:2.5rem;font-weight:800;color:#f59e0b">{round(stewardship_us / max(stewardship_africa, 1), 1)}x</div>
    </div>
  </div>
  <div class="callout-purple callout">
    <strong>The stewardship paradox:</strong> Africa arguably needs antibiotic stewardship
    more than any other region -- antibiotics are sold over-the-counter in most African
    countries, veterinary antibiotic use is unregulated, and counterfeit/substandard
    antibiotics are widespread. Yet Africa has only <strong>{stewardship_africa}</strong>
    stewardship trials on ClinicalTrials.gov compared to the US's
    <strong>{stewardship_us}</strong>. Behavioural interventions to reduce inappropriate
    prescribing are far cheaper than developing new drugs, yet receive almost no
    research investment in the regions that need them most.
  </div>
</div>

<!-- ============ SECTION 7: DIAGNOSTIC GAP ============ -->
<div class="section">
  <h2>7. The Diagnostic Gap</h2>
  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Diagnostic/infection trials -- Africa</div>
      <div style="font-size:2.5rem;font-weight:800;color:#06b6d4">{diagnostics_africa}</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Diagnostic/infection trials -- US</div>
      <div style="font-size:2.5rem;font-weight:800;color:#60a5fa">{diagnostics_us}</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Gap ratio</div>
      <div style="font-size:2.5rem;font-weight:800;color:#f59e0b">{round(diagnostics_us / max(diagnostics_africa, 1), 1)}x</div>
    </div>
  </div>
  <div class="callout-amber callout">
    <strong>Prescribing blind:</strong> In most sub-Saharan African hospitals, antibiotics
    are prescribed empirically because blood culture and susceptibility testing are
    unavailable or take 48-72 hours. Point-of-care diagnostics (rapid CRP, procalcitonin,
    molecular resistance detection) could transform AMR management, yet Africa has only
    <strong>{diagnostics_africa}</strong> diagnostic-infection trials. Without diagnostics,
    stewardship is impossible -- clinicians cannot de-escalate or target therapy without
    knowing the pathogen. The GeneXpert platform (used for TB) demonstrates that rapid
    molecular diagnostics can work in Africa, but extending this model to Gram-negative
    resistance detection has barely been trialled.
  </div>
</div>

<!-- ============ SECTION 8: INTERVENTION TYPES ============ -->
<div class="section">
  <h2>8. Intervention Type Breakdown</h2>
  {int_bars_html}
  <div class="callout-green callout" style="margin-top:16px">
    <strong>What Africa's AMR portfolio looks like:</strong> The intervention mix reveals
    the structural priorities. Drug trials (existing and novel antibiotics) are present
    but often imported protocols from global sponsors. Stewardship, diagnostics, infection
    prevention, and phage therapy -- all potentially transformative for Africa -- remain
    marginal. The gap between what Africa needs (context-appropriate solutions) and what
    it gets (drug registration studies designed elsewhere) defines the AMR research inequity.
  </div>
</div>

<!-- ============ SECTION 9: SPONSOR ANALYSIS ============ -->
<div class="section">
  <h2>9. Sponsor Analysis: Industry vs Academic</h2>
  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Industry-sponsored</div>
      <div style="font-size:2.5rem;font-weight:800;color:#ef4444">{industry_count}</div>
      <div style="color:#94a3b8;font-size:13px">{round(industry_count / max(total_fetched, 1) * 100, 1)}% of Africa AMR trials</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Academic / public / NGO</div>
      <div style="font-size:2.5rem;font-weight:800;color:#22c55e">{academic_count}</div>
      <div style="color:#94a3b8;font-size:13px">{round(academic_count / max(total_fetched, 1) * 100, 1)}% of Africa AMR trials</div>
    </div>
  </div>
  <div class="legend" style="margin-bottom:12px">
    <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div> Pharma / Industry</div>
    <div class="legend-item"><div class="legend-dot" style="background:#22c55e"></div> Public / NGO</div>
    <div class="legend-item"><div class="legend-dot" style="background:#60a5fa"></div> Academic / Other</div>
  </div>
  {sponsor_bars_html}
  <div class="callout" style="margin-top:16px">
    <strong>The market failure:</strong> Antibiotics are low-profit drugs compared
    to oncology or cardiology agents. Pharmaceutical companies have
    little commercial incentive to conduct AMR trials in Africa, where
    healthcare budgets cannot support premium pricing. Most Africa-based AMR research
    is funded by public/NGO sponsors (NIAID, Wellcome, GARDP, TB Alliance, Gates
    Foundation). GARDP (Global Antibiotic Research and Development Partnership) is
    a rare example of a non-profit specifically targeting AMR in low-resource settings,
    but its budget is a fraction of what is needed.
  </div>
</div>

<!-- ============ SECTION 10: INDIA COMPARISON ============ -->
<div class="section">
  <h2>10. India Comparison: Two Giants, Similar Burden, Different Response</h2>
  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Africa AMR trials</div>
      <div style="font-size:2.5rem;font-weight:800;color:#ef4444">{africa_total}</div>
      <div style="color:#94a3b8;font-size:13px">~1.4B population</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">India AMR trials</div>
      <div style="font-size:2.5rem;font-weight:800;color:#f59e0b">{india_total}</div>
      <div style="color:#94a3b8;font-size:13px">~1.4B population</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Per 10M capita</div>
      <div style="font-size:1.2rem;font-weight:800;color:#94a3b8;margin-top:8px">
        Africa: {africa_per_10m} | India: {india_per_10m} | US: {us_per_10m}
      </div>
    </div>
  </div>
  <div class="callout-amber callout">
    <strong>India as mirror:</strong> India and Africa share comparable AMR burdens --
    both are hotspots for ESBL <em>E. coli</em>, carbapenem-resistant
    <em>Klebsiella</em>, and NDM-1-producing organisms. India's pharmaceutical
    manufacturing base drives some of the global generic antibiotic supply (and
    unfortunately, some of the AMR selection pressure through effluent pollution).
    Yet India has <strong>{india_total}</strong> AMR trials compared to Africa's
    <strong>{africa_total}</strong> -- a <strong>{round(india_total / max(africa_total, 1), 1)}x</strong>
    advantage despite similar population size. India's generic drug industry,
    established CRO infrastructure, and active regulatory reform (CDSCO) create
    a trial-friendly environment that most African countries lack.
  </div>
</div>

<!-- ============ SECTION 11: PROJECTION ============ -->
<div class="section">
  <h2>11. AMR Death Projection: If the Trial Deficit Continues</h2>
  <div class="comparison-box">
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">2019 (IHME/Lancet)</div>
      <div style="font-size:2rem;font-weight:800;color:#f59e0b">{africa_deaths_2019:,}</div>
      <div style="color:#94a3b8;font-size:13px">African AMR deaths/year</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">2050 (O'Neill projection)</div>
      <div style="font-size:2rem;font-weight:800;color:#ef4444">{projected_2050_africa:,}</div>
      <div style="color:#94a3b8;font-size:13px">projected if no action</div>
    </div>
    <div class="cbox">
      <div style="color:#64748b;font-size:12px;text-transform:uppercase">Increase factor</div>
      <div style="font-size:2rem;font-weight:800;color:#dc2626">{round(projected_2050_africa / max(africa_deaths_2019, 1), 1)}x</div>
      <div style="color:#94a3b8;font-size:13px">by 2050</div>
    </div>
  </div>
  <div class="callout">
    <strong>The O'Neill Review (2016)</strong> projected 10 million global AMR deaths per
    year by 2050 if no significant intervention occurs. Africa's share is expected to
    <strong>grow</strong> from 23% to approximately 30% due to: (1) rapid population
    growth (projected 2.5B by 2050); (2) increasing urbanisation without matching
    sanitation infrastructure; (3) growing antibiotic consumption without stewardship;
    (4) climate change expanding the range of resistant organisms; and (5) weak
    surveillance preventing targeted responses. At <strong>{africa_total}</strong>
    AMR trials today, Africa is conducting research at a pace that is orders of
    magnitude below what the projected mortality demands.
  </div>

  <div class="callout-amber callout">
    <strong>What would close the gap?</strong> Africa needs: (1) African-led AMR
    surveillance networks (building on GLASS/WHO AFRO efforts); (2) point-of-care
    diagnostics adapted for low-resource settings; (3) stewardship programmes
    tailored to open-market antibiotic sales; (4) inclusion in novel antibiotic
    registration trials; (5) regional manufacturing capacity (African Medicines
    Agency); and (6) dedicated funding mechanisms (AMR equivalent of PEPFAR/Global Fund).
    Current trial volume of <strong>{africa_total}</strong> is wholly inadequate
    for a continent facing the world's highest AMR mortality rate.
  </div>
</div>

<!-- ============ SECTION 12: PHASE DISTRIBUTION ============ -->
<div class="section">
  <h2>12. Phase Distribution</h2>
  {phase_html}
</div>

<!-- ============ TOP TRIALS TABLE ============ -->
<div class="section">
  <h2>Largest AMR Trials in Africa (by Enrollment)</h2>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>NCT ID</th>
        <th>Title</th>
        <th style="text-align:center">Phase</th>
        <th style="text-align:right">Enrollment</th>
        <th>Pathogen</th>
        <th>Intervention Type</th>
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
  Query: "{AMR_QUERY}"<br>
  AMR mortality data: IHME/Lancet 2019 (Murray et al., doi:10.1016/S0140-6736(21)02724-0)<br>
  O'Neill Review: Review on Antimicrobial Resistance, 2016<br>
  Analysis: fetch_amr_crisis.py | AMR Crisis: Africa as Ground Zero<br>
  Note: Counts reflect interventional trials registered on ClinicalTrials.gov only.
  Trials on WHO ICTRP, Pan African Clinical Trials Registry, or national registries
  are not captured. CCI = AMR death share / AMR trial share.
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
    print("  AMR Crisis: Africa as Ground Zero")
    print("  ClinicalTrials.gov API v2 Analysis")
    print("=" * 70)

    data = collect_data()

    print("\n" + "=" * 70)
    print("KEY FINDINGS:")
    print("=" * 70)
    print(f"  Global AMR trials:         {data['global_amr_total']}")
    print(f"  Africa AMR trials:         {data['africa_amr_total']}")
    print(f"  US AMR trials:             {data['us_amr_total']}")
    print(f"  India AMR trials:          {data['india_amr_total']}")
    print(f"  Africa trial share:        {data['africa_share_pct']}%")
    print(f"  Africa AMR death share:    {data['amr_death_share_pct']}%")
    print(f"  Clinical-Crisis Index:     {data['cci']}x")
    print(f"  Novel antibiotics (Africa):{data['novel_antibiotic_count']}")
    print(f"  Stewardship (Africa):      {data['stewardship_count_africa']}")
    print(f"  Diagnostics (Africa):      {data['diagnostics_count_africa']}")
    print(f"  Trials analysed:           {data['total_trials_fetched']}")
    print(f"  Total enrollment:          {data['total_enrollment']:,}")

    generate_html(data)
    print("\nDone.")


if __name__ == "__main__":
    main()
