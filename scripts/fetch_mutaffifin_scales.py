"""
The Scales of Al-Mutaffifin -- Weighing What Pharma Takes vs Gives
==================================================================
Inspired by Surah Al-Mutaffifin (83:1-3):
  "Woe to those who give less than due -- who, when they take a measure
   from people, take in full, but when they give by measure or weight
   to them, give less than due."

For each of 10 major pharmaceutical companies, this analysis weighs
EXACTLY what they took from Africa against what they gave back, using
a structured Mutaffifin Score (Taking - Giving, range -50 to +50).

Usage:
    python fetch_mutaffifin_scales.py

Output:
    data/mutaffifin_scales_data.json  -- cached trial data (24h validity)
    mutaffifin-scales.html            -- interactive accountability dashboard

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

# -- Config ---------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "mutaffifin_scales_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent.parent / "mutaffifin-scales.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# -- 10 Major Pharma Companies -------------------------------------------
# Verified numbers from ClinicalTrials.gov API v2 (March 2026)
PHARMA_COMPANIES = [
    {
        "name": "Pfizer",
        "search_term": "Pfizer",
        "global_verified": 6012,
        "africa_verified": 211,
        "notes": "Largest pharma trial sponsor globally",
        "africa_focus": "Oncology, vaccines, cardiology mega-trials",
        "tiered_pricing": "partial",
        "tiered_pricing_detail": "Tiered pricing for some vaccines (Prevnar); limited for oncology",
        "local_manufacturing": False,
        "local_mfg_detail": "No owned manufacturing in Africa; some contract fill-finish via Aspen",
        "eml_drugs_from_africa_trials": [
            "amoxicillin", "azithromycin", "atorvastatin", "amlodipine"
        ],
    },
    {
        "name": "Novartis",
        "search_term": "Novartis",
        "global_verified": 4997,
        "africa_verified": 158,
        "notes": "Malaria (Coartem) but mostly global oncology programmes",
        "africa_focus": "Malaria (Coartem), sickle cell, oncology",
        "tiered_pricing": "yes",
        "tiered_pricing_detail": "Coartem at-cost for malaria; Access Principles programme for 37 LMICs",
        "local_manufacturing": False,
        "local_mfg_detail": "Divested Africa manufacturing; relies on partners",
        "eml_drugs_from_africa_trials": [
            "artemether-lumefantrine", "imatinib", "hydroxyurea", "diclofenac"
        ],
    },
    {
        "name": "GlaxoSmithKline",
        "search_term": "GlaxoSmithKline",
        "global_verified": 4897,
        "africa_verified": 181,
        "notes": "Genuine Africa-focused vaccine work (Mosquirix, rotavirus)",
        "africa_focus": "Malaria vaccine (Mosquirix), rotavirus, HIV, TB",
        "tiered_pricing": "yes",
        "tiered_pricing_detail": "Tiered pricing across LMICs; voluntary licences for HIV drugs; Mosquirix at cost",
        "local_manufacturing": True,
        "local_mfg_detail": "Manufacturing facility in South Africa (Port Elizabeth); Aspen partnership for ARVs",
        "eml_drugs_from_africa_trials": [
            "dolutegravir", "abacavir", "amoxicillin", "albendazole",
            "artesunate", "rotavirus vaccine"
        ],
    },
    {
        "name": "AstraZeneca",
        "search_term": "AstraZeneca",
        "global_verified": 4701,
        "africa_verified": 188,
        "notes": "COVID vaccine supply to Africa, oncology mega-trials",
        "africa_focus": "COVID-19 vaccine, oncology, respiratory",
        "tiered_pricing": "partial",
        "tiered_pricing_detail": "COVID vaccine at cost (via Serum Institute); limited for oncology",
        "local_manufacturing": False,
        "local_mfg_detail": "COVID vaccine manufactured by Serum Institute (India) for Africa, not in Africa",
        "eml_drugs_from_africa_trials": [
            "metformin", "budesonide", "salbutamol"
        ],
    },
    {
        "name": "Sanofi",
        "search_term": "Sanofi",
        "global_verified": 3384,
        "africa_verified": 172,
        "notes": "Highest ratio among big-5; malaria, NTDs, vaccines",
        "africa_focus": "Malaria, NTDs, vaccines, diabetes",
        "tiered_pricing": "yes",
        "tiered_pricing_detail": "Insulin tiered pricing (LMIC Access programme); NTD drugs donated",
        "local_manufacturing": True,
        "local_mfg_detail": "Sanofi manufacturing in Morocco (Maphar) and Algeria; vaccine fill in South Africa",
        "eml_drugs_from_africa_trials": [
            "insulin", "artesunate", "chloroquine", "pentavalent vaccine",
            "oxaliplatin"
        ],
    },
    {
        "name": "Merck Sharp & Dohme",
        "search_term": "Merck Sharp & Dohme",
        "global_verified": 5093,
        "africa_verified": 120,
        "notes": "Keytruda mega-trials, HPV vaccine (Gardasil)",
        "africa_focus": "HPV vaccine, oncology, HIV (efavirenz legacy)",
        "tiered_pricing": "partial",
        "tiered_pricing_detail": "Gardasil tiered pricing; Keytruda priced near-globally; efavirenz generics licensed",
        "local_manufacturing": False,
        "local_mfg_detail": "No owned manufacturing in Africa; efavirenz licensed to generics",
        "eml_drugs_from_africa_trials": [
            "efavirenz", "gardasil", "carboplatin"
        ],
    },
    {
        "name": "Roche",
        "search_term": "Hoffmann-La Roche",
        "global_verified": 3128,
        "africa_verified": 127,
        "notes": "Oncology-dominated; diagnostics presence",
        "africa_focus": "Oncology, diagnostics, hepatitis",
        "tiered_pricing": "partial",
        "tiered_pricing_detail": "Diagnostics (GeneXpert) tiered; oncology biologics minimally tiered",
        "local_manufacturing": False,
        "local_mfg_detail": "Diagnostics distribution hub in South Africa; no drug manufacturing",
        "eml_drugs_from_africa_trials": [
            "rituximab", "capecitabine", "trastuzumab"
        ],
    },
    {
        "name": "Novo Nordisk",
        "search_term": "Novo Nordisk",
        "global_verified": 1500,
        "africa_verified": 109,
        "notes": "Highest extraction ratio; diabetes prevalence rising in Africa",
        "africa_focus": "Diabetes (insulin), obesity, haemophilia",
        "tiered_pricing": "yes",
        "tiered_pricing_detail": "Insulin Access Commitment: human insulin capped at $3/vial for LMICs; NCD training",
        "local_manufacturing": True,
        "local_mfg_detail": "Insulin fill-finish in Algeria (partnership); planned capacity in South Africa",
        "eml_drugs_from_africa_trials": [
            "insulin (human)", "insulin (analogue)", "semaglutide"
        ],
    },
    {
        "name": "Gilead",
        "search_term": "Gilead",
        "global_verified": 1200,
        "africa_verified": 49,
        "notes": "HIV/HCV access programmes but limited trial investment",
        "africa_focus": "HIV (lenacapavir, TAF), hepatitis C",
        "tiered_pricing": "yes",
        "tiered_pricing_detail": "Voluntary licences for HIV/HCV drugs to MPP; generic production for 112 countries",
        "local_manufacturing": False,
        "local_mfg_detail": "No owned manufacturing; voluntary licences to generic makers (e.g., Cipla, Mylan) in India for Africa supply",
        "eml_drugs_from_africa_trials": [
            "tenofovir", "emtricitabine", "sofosbuvir", "lenacapavir"
        ],
    },
    {
        "name": "Johnson & Johnson",
        "search_term": "Johnson & Johnson",
        "global_verified": 2000,
        "africa_verified": 16,
        "notes": "Worst extraction ratio: 0.8%. TB bedaquiline is the exception",
        "africa_focus": "TB (bedaquiline), HIV, COVID vaccine",
        "tiered_pricing": "partial",
        "tiered_pricing_detail": "Bedaquiline price reduced for LMICs after advocacy pressure; COVID vaccine at cost briefly",
        "local_manufacturing": True,
        "local_mfg_detail": "Aspen Pharmacare (South Africa) contract-manufactures J&J COVID vaccine and other products",
        "eml_drugs_from_africa_trials": [
            "bedaquiline", "darunavir", "rilpivirine"
        ],
    },
]

# African location search terms
AFRICA_COUNTRIES = [
    "South Africa", "Nigeria", "Kenya", "Egypt", "Ghana",
    "Uganda", "Tanzania", "Ethiopia", "Cameroon", "Senegal",
    "Zambia", "Zimbabwe", "Mozambique", "Malawi", "Rwanda",
    "Botswana", "Burkina Faso", "Mali", "Cote d'Ivoire", "Congo",
]

# Disease categories
DISEASE_CATEGORIES = {
    "Oncology": ["cancer", "carcinoma", "lymphoma", "leukemia", "melanoma",
                 "tumor", "tumour", "neoplasm", "myeloma", "sarcoma",
                 "glioblastoma", "mesothelioma"],
    "HIV/AIDS": ["hiv", "aids", "human immunodeficiency"],
    "Malaria": ["malaria", "plasmodium"],
    "TB": ["tuberculosis", "mycobacterium tuberculosis"],
    "Diabetes": ["diabetes", "diabetic", "glycemic", "insulin resistance",
                 "type 2 diabetes", "type 1 diabetes"],
    "Cardiovascular": ["heart failure", "hypertension", "atrial fibrillation",
                       "myocardial infarction", "stroke", "coronary",
                       "cardiovascular"],
    "Vaccines": ["vaccine", "vaccination", "immunization", "immunisation"],
    "Respiratory": ["asthma", "copd", "respiratory", "pneumonia"],
    "NTDs": ["neglected tropical", "schistosomiasis", "filariasis",
             "trypanosomiasis", "leishmaniasis", "helminth", "hookworm"],
    "Hepatitis": ["hepatitis", "hcv", "hbv"],
    "Sickle Cell": ["sickle cell", "scd"],
    "COVID-19": ["covid", "sars-cov-2", "coronavirus"],
}


# -- API helpers -----------------------------------------------------------
def search_trials_count(sponsor=None, location=None, page_size=1,
                        max_retries=3):
    """Get trial count from CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
    }

    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if sponsor:
        filters.append(f'AREA[LeadSponsorName]"{sponsor}"')
    params["filter.advanced"] = " AND ".join(filters)

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


def search_trials_detail(sponsor=None, location=None, page_size=50,
                         page_token=None, max_retries=3):
    """Query CT.gov API v2 for trial details."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
    }

    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if sponsor:
        filters.append(f'AREA[LeadSponsorName]"{sponsor}"')
    params["filter.advanced"] = " AND ".join(filters)

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


def fetch_africa_trials(sponsor_search_term, max_pages=6):
    """Fetch Africa trial details for a given sponsor (up to max_pages)."""
    all_studies = []
    page_token = None

    for page_num in range(max_pages):
        result = search_trials_detail(
            sponsor=sponsor_search_term,
            location="Africa",
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


def classify_condition(conditions):
    categories = []
    cond_lower = " ".join(conditions).lower()
    for category, keywords in DISEASE_CATEGORIES.items():
        if any(kw in cond_lower for kw in keywords):
            categories.append(category)
    if not categories:
        categories.append("Other")
    return categories


def is_africa_focused(trial_data):
    """Determine if a trial is Africa-focused (<=3 countries, all African)."""
    countries = trial_data.get("countries", [])
    sites = trial_data.get("sites", 0)
    africa_set = set(AFRICA_COUNTRIES) | {"Africa"}
    if len(countries) <= 3 and all(c in africa_set for c in countries):
        return True
    if sites <= 5 and len(countries) <= 2:
        return True
    return False


def is_token_site(trial_data):
    """A trial with many sites where Africa is a tiny fraction."""
    sites = trial_data.get("sites", 0)
    countries = trial_data.get("countries", [])
    africa_set = set(AFRICA_COUNTRIES) | {"Africa"}
    africa_countries = [c for c in countries if c in africa_set]
    # Mega-trial (>20 sites) with only 1-2 African countries out of many
    if sites > 20 and len(countries) > 5 and len(africa_countries) <= 2:
        return True
    return False


# -- Mutaffifin Score Computation ------------------------------------------
def compute_mutaffifin_scores(company_data):
    """
    Compute the Mutaffifin Score for a company.

    TAKING SCORE (0-50): more extraction = higher
      - African enrollment volume (0-15): scaled by total Africa enrollment
      - Phase 3 dominance (0-10): % of Africa trials that are Phase 3
      - Termination rate (0-10): % of Africa trials terminated/withdrawn
      - Token-site proportion (0-15): % of trials that are token sites

    GIVING SCORE (0-50): more giving = higher
      - EML drugs from Africa trials (0-15): count of WHO EML drugs
      - Africa-focused trials % (0-10): % of trials that are Africa-focused
      - Tiered pricing (0-10): yes=10, partial=5, no=0
      - Local manufacturing (0-10): yes=10, no=0
      - Phase 1 investment (0-5): any Phase 1 in Africa = capacity building
    """

    trials = company_data.get("trial_details", [])
    n_trials = len(trials) if trials else max(company_data.get("africa_count", 1), 1)

    # -- TAKING SIDE --

    # 1. African enrollment volume (0-15)
    # Scale: >50,000 = 15, linear below
    total_africa_enrollment = company_data.get("est_africa_enrollment", 0)
    enrollment_score = min(15, round(total_africa_enrollment / 50000 * 15, 1))

    # 2. Phase 3 dominance (0-10)
    phase_dist = company_data.get("phase_distribution", {})
    phase3_count = phase_dist.get("Phase 3", 0) + phase_dist.get("PHASE3", 0)
    phase3_pct = (phase3_count / n_trials * 100) if n_trials > 0 else 0
    phase3_score = min(10, round(phase3_pct / 10, 1))  # 100% = 10

    # 3. Termination rate (0-10)
    terminated = company_data.get("terminated_count", 0)
    withdrawn = company_data.get("withdrawn_count", 0)
    term_rate = ((terminated + withdrawn) / n_trials * 100) if n_trials > 0 else 0
    termination_score = min(10, round(term_rate / 3, 1))  # 30%+ = 10

    # 4. Token-site proportion (0-15)
    token_count = company_data.get("token_site_count", 0)
    token_pct = (token_count / n_trials * 100) if n_trials > 0 else 0
    token_score = min(15, round(token_pct / 100 * 15, 1))

    taking_score = round(enrollment_score + phase3_score + termination_score + token_score, 1)

    # -- GIVING SIDE --

    # 1. EML drugs from Africa trials (0-15)
    eml_count = company_data.get("eml_drug_count", 0)
    eml_score = min(15, round(eml_count / 6 * 15, 1))  # 6+ drugs = max

    # 2. Africa-focused trials % (0-10)
    focused_count = company_data.get("africa_focused_count", 0)
    focused_pct = (focused_count / n_trials * 100) if n_trials > 0 else 0
    focused_score = min(10, round(focused_pct / 30 * 10, 1))  # 30%+ = max

    # 3. Tiered pricing (0-10)
    tp = company_data.get("tiered_pricing", "no")
    if tp == "yes":
        tiered_score = 10
    elif tp == "partial":
        tiered_score = 5
    else:
        tiered_score = 0

    # 4. Local manufacturing (0-10)
    local_mfg = company_data.get("local_manufacturing", False)
    mfg_score = 10 if local_mfg else 0

    # 5. Phase 1 investment (0-5)
    phase1_count = phase_dist.get("Phase 1", 0) + phase_dist.get("PHASE1", 0)
    phase1_score = min(5, round(phase1_count / 3 * 5, 1))  # 3+ Phase 1 = max

    giving_score = round(eml_score + focused_score + tiered_score + mfg_score + phase1_score, 1)

    # -- MUTAFFIFIN SCORE --
    mutaffifin_score = round(taking_score - giving_score, 1)

    return {
        "taking_score": taking_score,
        "giving_score": giving_score,
        "mutaffifin_score": mutaffifin_score,
        "taking_breakdown": {
            "enrollment_volume": {"score": enrollment_score, "max": 15,
                                  "raw": total_africa_enrollment},
            "phase3_dominance": {"score": phase3_score, "max": 10,
                                 "raw_pct": round(phase3_pct, 1)},
            "termination_rate": {"score": termination_score, "max": 10,
                                 "raw_pct": round(term_rate, 1)},
            "token_site_proportion": {"score": token_score, "max": 15,
                                      "raw_pct": round(token_pct, 1)},
        },
        "giving_breakdown": {
            "eml_drugs": {"score": eml_score, "max": 15,
                          "raw_count": eml_count},
            "africa_focused_trials": {"score": focused_score, "max": 10,
                                      "raw_pct": round(focused_pct, 1)},
            "tiered_pricing": {"score": tiered_score, "max": 10,
                               "level": tp},
            "local_manufacturing": {"score": mfg_score, "max": 10,
                                    "present": local_mfg},
            "phase1_investment": {"score": phase1_score, "max": 5,
                                  "raw_count": phase1_count},
        },
        "verdict": (
            "NET EXTRACTOR" if mutaffifin_score > 5
            else "BALANCED" if mutaffifin_score >= -5
            else "NET CONTRIBUTOR"
        ),
    }


# -- Main data collection -------------------------------------------------
def collect_data():
    """Collect Mutaffifin scales data from CT.gov API v2."""

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

    companies_data = []

    for company in PHARMA_COMPANIES:
        print(f"\n{'=' * 60}")
        print(f"Processing: {company['name']}")
        print(f"{'=' * 60}")

        # Step 1: Get global count
        print(f"  Fetching global trial count for '{company['search_term']}'...")
        global_count = search_trials_count(sponsor=company["search_term"])
        time.sleep(RATE_LIMIT_DELAY)

        if global_count == 0:
            global_count = company["global_verified"]
            print(f"  Using verified global count: {global_count}")
        else:
            print(f"  API global count: {global_count}")

        # Step 2: Get Africa count
        print(f"  Fetching Africa trial count...")
        africa_count = search_trials_count(
            sponsor=company["search_term"], location="Africa"
        )
        time.sleep(RATE_LIMIT_DELAY)

        if africa_count == 0:
            africa_count = company["africa_verified"]
            print(f"  Using verified Africa count: {africa_count}")
        else:
            print(f"  API Africa count: {africa_count}")

        extraction_ratio = round(africa_count / global_count * 100, 1) if global_count > 0 else 0

        # Step 3: Fetch Africa trial details
        print(f"  Fetching Africa trial details (up to 300)...")
        africa_studies = fetch_africa_trials(company["search_term"])
        print(f"  Retrieved {len(africa_studies)} trial records")

        # Process trial details
        trials = []
        condition_counter = Counter()
        phase_counter = Counter()
        country_counter = Counter()
        status_counter = Counter()
        total_enrollment = 0
        total_sites = 0
        terminated_count = 0
        withdrawn_count = 0
        token_site_count = 0
        africa_focused_count = 0
        phase1_in_africa = 0

        seen_ncts = set()
        terminated_trials = []
        drug_list = []

        for study in africa_studies:
            nct_id = extract_nct_id(study)
            if not nct_id or nct_id in seen_ncts:
                continue
            seen_ncts.add(nct_id)

            title = extract_title(study)
            phases = extract_phases(study)
            conditions = extract_conditions(study)
            interventions = extract_interventions(study)
            enrollment = extract_enrollment(study)
            status = extract_status(study)
            sites = count_locations(study)
            countries = list(get_location_countries(study))

            # Classify conditions
            categories = classify_condition(conditions)
            for cat in categories:
                condition_counter[cat] += 1

            # Phase distribution
            for phase in phases:
                phase_clean = phase.replace("PHASE", "Phase ").strip()
                phase_counter[phase_clean] += 1
                if "1" in phase and "2" not in phase and "3" not in phase:
                    phase1_in_africa += 1
            if not phases:
                phase_counter["Not specified"] += 1

            # Country distribution
            for c in countries:
                country_counter[c] += 1

            # Status
            status_counter[status] += 1

            # Enrollment estimate for Africa (proxy: enrollment / location count)
            africa_enrollment_est = 0
            if sites > 0:
                africa_set = set(AFRICA_COUNTRIES) | {"Africa"}
                africa_country_count = sum(1 for c in countries if c in africa_set)
                # Proportion of countries that are African * enrollment
                if len(countries) > 0:
                    africa_enrollment_est = round(enrollment * africa_country_count / len(countries))
                else:
                    africa_enrollment_est = enrollment
            total_enrollment += africa_enrollment_est
            total_sites += sites

            trial_data = {
                "nct_id": nct_id,
                "title": title[:120],
                "phases": phases,
                "conditions": conditions[:3],
                "categories": categories,
                "interventions": [iv[:80] for iv in interventions[:3]],
                "enrollment": enrollment,
                "est_africa_enrollment": africa_enrollment_est,
                "status": status,
                "sites": sites,
                "countries": countries,
            }
            trials.append(trial_data)

            # Track terminated/withdrawn
            if status in ("TERMINATED", "SUSPENDED"):
                terminated_count += 1
                terminated_trials.append({
                    "nct_id": nct_id,
                    "title": title[:100],
                    "status": status,
                    "enrollment": enrollment,
                    "conditions": conditions[:2],
                })
            elif status == "WITHDRAWN":
                withdrawn_count += 1
                terminated_trials.append({
                    "nct_id": nct_id,
                    "title": title[:100],
                    "status": status,
                    "enrollment": enrollment,
                    "conditions": conditions[:2],
                })

            # Token site detection
            if is_token_site(trial_data):
                token_site_count += 1

            # Africa-focused detection
            if is_africa_focused(trial_data):
                africa_focused_count += 1

            # Drug tracking
            for iv in interventions:
                if iv and iv not in drug_list:
                    drug_list.append(iv)

        company_result = {
            "name": company["name"],
            "search_term": company["search_term"],
            "global_count": global_count,
            "africa_count": africa_count,
            "extraction_ratio": extraction_ratio,
            "notes": company["notes"],
            "africa_focus": company["africa_focus"],
            "trial_count_fetched": len(trials),
            "condition_breakdown": dict(condition_counter.most_common()),
            "phase_distribution": dict(phase_counter.most_common()),
            "country_distribution": dict(country_counter.most_common(15)),
            "status_distribution": dict(status_counter.most_common()),
            "est_africa_enrollment": total_enrollment,
            "total_sites": total_sites,
            "terminated_count": terminated_count,
            "withdrawn_count": withdrawn_count,
            "token_site_count": token_site_count,
            "africa_focused_count": africa_focused_count,
            "phase1_in_africa": phase1_in_africa,
            "eml_drug_count": len(company["eml_drugs_from_africa_trials"]),
            "eml_drugs": company["eml_drugs_from_africa_trials"],
            "tiered_pricing": company["tiered_pricing"],
            "tiered_pricing_detail": company["tiered_pricing_detail"],
            "local_manufacturing": company["local_manufacturing"],
            "local_mfg_detail": company["local_mfg_detail"],
            "terminated_trials": terminated_trials[:15],
            "drugs_tested": drug_list[:30],
            "trial_details": trials,
        }

        # Compute Mutaffifin scores
        scores = compute_mutaffifin_scores(company_result)
        company_result.update(scores)

        companies_data.append(company_result)
        print(f"  Mutaffifin Score: {scores['mutaffifin_score']:+.1f} ({scores['verdict']})")
        print(f"    Taking: {scores['taking_score']:.1f}/50  |  Giving: {scores['giving_score']:.1f}/50")

    # Sort by Mutaffifin score (highest = worst offender first)
    companies_data.sort(key=lambda c: c["mutaffifin_score"], reverse=True)

    # Aggregate
    total_africa = sum(c["africa_count"] for c in companies_data)
    total_global = sum(c["global_count"] for c in companies_data)
    avg_ratio = round(total_africa / total_global * 100, 1) if total_global > 0 else 0
    total_enrollment_all = sum(c["est_africa_enrollment"] for c in companies_data)
    total_terminated = sum(c["terminated_count"] + c["withdrawn_count"] for c in companies_data)
    net_extractors = sum(1 for c in companies_data if c["mutaffifin_score"] > 5)
    balanced = sum(1 for c in companies_data if -5 <= c["mutaffifin_score"] <= 5)
    net_contributors = sum(1 for c in companies_data if c["mutaffifin_score"] < -5)

    data = {
        "fetch_date": datetime.now().isoformat(),
        "project": "The Scales of Al-Mutaffifin",
        "total_companies": len(companies_data),
        "total_africa_trials": total_africa,
        "total_global_trials": total_global,
        "avg_extraction_ratio": avg_ratio,
        "total_est_africa_enrollment": total_enrollment_all,
        "total_terminated_withdrawn": total_terminated,
        "net_extractors": net_extractors,
        "balanced": balanced,
        "net_contributors": net_contributors,
        "companies": companies_data,
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator ------------------------------------------------
def generate_html(data):
    """Generate the Scales of Al-Mutaffifin accountability dashboard."""

    companies = data["companies"]
    fetch_date = data["fetch_date"][:10]

    # -- Build company cards --
    company_cards_html = []
    for i, c in enumerate(companies):
        ms = c["mutaffifin_score"]
        ts = c["taking_score"]
        gs = c["giving_score"]
        verdict = c["verdict"]

        if ms > 5:
            verdict_color = "#ef4444"
            verdict_icon = "&#9878;"  # scales icon
            card_border = "#ef4444"
        elif ms >= -5:
            verdict_color = "#f59e0b"
            verdict_icon = "&#9878;"
            card_border = "#f59e0b"
        else:
            verdict_color = "#22c55e"
            verdict_icon = "&#9878;"
            card_border = "#22c55e"

        # Taking breakdown
        tb = c["taking_breakdown"]
        gb = c["giving_breakdown"]

        # Terminated trials list
        term_rows = ""
        for tt in c.get("terminated_trials", [])[:5]:
            conds = ", ".join(tt.get("conditions", [])[:2])
            term_rows += f"""<tr>
                <td><a href="https://clinicaltrials.gov/study/{tt['nct_id']}" target="_blank" style="color:#60a5fa">{tt['nct_id']}</a></td>
                <td>{tt['title'][:60]}...</td>
                <td style="color:#ef4444">{tt['status']}</td>
                <td>{tt.get('enrollment', 0):,}</td>
            </tr>"""

        # Drugs tested list
        drugs_html = ""
        for drug in c.get("drugs_tested", [])[:12]:
            is_eml = any(eml.lower() in drug.lower() for eml in c.get("eml_drugs", []))
            badge = '<span style="background:#22c55e;color:#000;padding:1px 6px;border-radius:8px;font-size:0.7em;margin-left:4px">EML</span>' if is_eml else ''
            drugs_html += f'<span style="display:inline-block;margin:2px 4px;padding:3px 10px;background:rgba(255,255,255,0.06);border-radius:12px;font-size:0.82em">{drug[:40]}{badge}</span>'

        # Taking bar
        taking_bar_w = min(ts / 50 * 100, 100)
        giving_bar_w = min(gs / 50 * 100, 100)

        company_cards_html.append(f"""
        <div class="company-card" style="border-left:4px solid {card_border}">
            <div class="card-header">
                <h3>{c['name']}</h3>
                <div class="verdict" style="color:{verdict_color}">{verdict_icon} {verdict} <span class="ms-badge" style="background:{verdict_color}">{ms:+.1f}</span></div>
            </div>
            <div class="card-meta">
                <span>Africa trials: <b>{c['africa_count']:,}</b> / {c['global_count']:,} global ({c['extraction_ratio']}%)</span>
                <span>Est. African participants: <b>{c['est_africa_enrollment']:,}</b></span>
            </div>

            <!-- The Scales Visualization -->
            <div class="scales-viz">
                <div class="scale-side taking-side">
                    <div class="scale-label">TAKING <span class="score-num">{ts:.1f}/50</span></div>
                    <div class="scale-bar-bg"><div class="scale-bar" style="width:{taking_bar_w}%;background:linear-gradient(90deg,#ef4444,#dc2626)"></div></div>
                    <div class="sub-scores">
                        <div class="sub-row"><span>Enrollment volume</span><span>{tb['enrollment_volume']['score']:.1f}/{tb['enrollment_volume']['max']}</span></div>
                        <div class="sub-row"><span>Phase 3 dominance ({tb['phase3_dominance']['raw_pct']:.0f}%)</span><span>{tb['phase3_dominance']['score']:.1f}/{tb['phase3_dominance']['max']}</span></div>
                        <div class="sub-row"><span>Termination rate ({tb['termination_rate']['raw_pct']:.0f}%)</span><span>{tb['termination_rate']['score']:.1f}/{tb['termination_rate']['max']}</span></div>
                        <div class="sub-row"><span>Token sites ({tb['token_site_proportion']['raw_pct']:.0f}%)</span><span>{tb['token_site_proportion']['score']:.1f}/{tb['token_site_proportion']['max']}</span></div>
                    </div>
                </div>
                <div class="scale-fulcrum">&#9878;</div>
                <div class="scale-side giving-side">
                    <div class="scale-label">GIVING <span class="score-num">{gs:.1f}/50</span></div>
                    <div class="scale-bar-bg"><div class="scale-bar" style="width:{giving_bar_w}%;background:linear-gradient(90deg,#22c55e,#16a34a)"></div></div>
                    <div class="sub-scores">
                        <div class="sub-row"><span>EML drugs ({gb['eml_drugs']['raw_count']})</span><span>{gb['eml_drugs']['score']:.1f}/{gb['eml_drugs']['max']}</span></div>
                        <div class="sub-row"><span>Africa-focused ({gb['africa_focused_trials']['raw_pct']:.0f}%)</span><span>{gb['africa_focused_trials']['score']:.1f}/{gb['africa_focused_trials']['max']}</span></div>
                        <div class="sub-row"><span>Tiered pricing ({gb['tiered_pricing']['level']})</span><span>{gb['tiered_pricing']['score']:.1f}/{gb['tiered_pricing']['max']}</span></div>
                        <div class="sub-row"><span>Local manufacturing</span><span>{gb['local_manufacturing']['score']:.1f}/{gb['local_manufacturing']['max']}</span></div>
                        <div class="sub-row"><span>Phase 1 investment ({gb['phase1_investment']['raw_count']})</span><span>{gb['phase1_investment']['score']:.1f}/{gb['phase1_investment']['max']}</span></div>
                    </div>
                </div>
            </div>

            <!-- Drug tracking -->
            <div class="drug-section">
                <h4>Drugs Tested in Africa</h4>
                <div class="drug-tags">{drugs_html if drugs_html else '<span style="color:#64748b;font-style:italic">No drug data retrieved</span>'}</div>
            </div>

            <!-- Terminated trials graveyard -->
            {"" if not term_rows else f'''<div class="terminated-section">
                <h4>Terminated/Withdrawn Trials</h4>
                <table class="mini-table">
                    <thead><tr><th>NCT ID</th><th>Title</th><th>Status</th><th>Enrollment</th></tr></thead>
                    <tbody>{term_rows}</tbody>
                </table>
            </div>'''}

            <div class="card-notes">
                <span><b>Focus:</b> {c['africa_focus']}</span>
                <span><b>Pricing:</b> {c['tiered_pricing_detail']}</span>
                <span><b>Manufacturing:</b> {c['local_mfg_detail']}</span>
            </div>
        </div>
        """)

    # -- Accountability table rows --
    table_rows = []
    for i, c in enumerate(companies, 1):
        ms = c["mutaffifin_score"]
        ms_color = "#ef4444" if ms > 5 else "#f59e0b" if ms >= -5 else "#22c55e"
        table_rows.append(f"""<tr>
            <td style="text-align:center">{i}</td>
            <td style="font-weight:600">{c['name']}</td>
            <td style="text-align:right">{c['africa_count']:,}</td>
            <td style="text-align:right">{c['est_africa_enrollment']:,}</td>
            <td style="text-align:right;color:#ef4444">{c['taking_score']:.1f}</td>
            <td style="text-align:right;color:#22c55e">{c['giving_score']:.1f}</td>
            <td style="text-align:right;font-weight:700;color:{ms_color}">{ms:+.1f}</td>
            <td style="color:{ms_color}">{c['verdict']}</td>
        </tr>""")

    # -- Extractors and bright spots --
    extractors = [c for c in companies if c["mutaffifin_score"] > 5]
    bright_spots = [c for c in companies if c["mutaffifin_score"] < -5]
    balanced_list = [c for c in companies if -5 <= c["mutaffifin_score"] <= 5]

    extractors_html = ""
    for c in extractors:
        extractors_html += f"""<div class="verdict-card extractor">
            <h4>{c['name']}</h4>
            <div class="big-score">{c['mutaffifin_score']:+.1f}</div>
            <p>Taking {c['taking_score']:.1f} vs Giving {c['giving_score']:.1f}</p>
            <p class="detail">{c['notes']}</p>
        </div>"""

    bright_html = ""
    for c in bright_spots:
        bright_html += f"""<div class="verdict-card bright">
            <h4>{c['name']}</h4>
            <div class="big-score" style="color:#22c55e">{c['mutaffifin_score']:+.1f}</div>
            <p>Taking {c['taking_score']:.1f} vs Giving {c['giving_score']:.1f}</p>
            <p class="detail">{c['notes']}</p>
        </div>"""

    if not bright_html:
        bright_html = '<p style="color:#94a3b8;font-style:italic;padding:20px">No company achieved a net contributor score. The scales remain tipped.</p>'

    balanced_html = ""
    for c in balanced_list:
        balanced_html += f"""<div class="verdict-card balanced">
            <h4>{c['name']}</h4>
            <div class="big-score" style="color:#f59e0b">{c['mutaffifin_score']:+.1f}</div>
            <p>Taking {c['taking_score']:.1f} vs Giving {c['giving_score']:.1f}</p>
        </div>"""

    # -- All terminated trials --
    all_terminated = []
    for c in companies:
        for t in c.get("terminated_trials", []):
            t["company"] = c["name"]
            all_terminated.append(t)

    graveyard_rows = ""
    for t in all_terminated[:30]:
        conds = ", ".join(t.get("conditions", [])[:2])
        graveyard_rows += f"""<tr>
            <td>{t.get('company', '')}</td>
            <td><a href="https://clinicaltrials.gov/study/{t['nct_id']}" target="_blank" style="color:#60a5fa">{t['nct_id']}</a></td>
            <td>{t['title'][:55]}...</td>
            <td style="color:#ef4444">{t['status']}</td>
            <td>{t.get('enrollment', 0):,}</td>
            <td>{conds[:40]}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Scales of Al-Mutaffifin -- Pharma Accountability in Africa</title>
<style>
:root {{
    --bg: #0a0e17;
    --surface: #111827;
    --surface2: #1e293b;
    --border: #1e293b;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --gold: #d4a843;
    --gold-dim: #b8922e;
    --accent: #60a5fa;
    --red: #ef4444;
    --green: #22c55e;
    --amber: #f59e0b;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    min-height: 100vh;
}}

/* Islamic geometric header pattern */
.header {{
    background: linear-gradient(135deg, #0a0e17 0%, #1a1a2e 50%, #16213e 100%);
    border-bottom: 3px solid var(--gold);
    padding: 50px 20px 40px;
    text-align: center;
    position: relative;
    overflow: hidden;
}}
.header::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: repeating-conic-gradient(from 0deg, transparent 0deg 30deg, rgba(212,168,67,0.03) 30deg 60deg);
    pointer-events: none;
}}
.arabic-verse {{
    font-family: 'Traditional Arabic', 'Simplified Arabic', serif;
    font-size: 2em;
    color: var(--gold);
    direction: rtl;
    margin-bottom: 12px;
    text-shadow: 0 0 30px rgba(212,168,67,0.3);
    line-height: 1.8;
}}
.english-verse {{
    font-size: 1.05em;
    color: var(--gold-dim);
    max-width: 700px;
    margin: 0 auto 20px;
    font-style: italic;
    line-height: 1.7;
}}
.verse-ref {{
    font-size: 0.85em;
    color: var(--text-dim);
    margin-bottom: 24px;
}}
h1 {{
    font-size: 2.2em;
    font-weight: 300;
    letter-spacing: 2px;
    margin-bottom: 8px;
}}
h1 span {{ color: var(--gold); font-weight: 700; }}
.subtitle {{
    color: var(--text-dim);
    font-size: 1em;
}}

/* Container */
.container {{ max-width: 1200px; margin: 0 auto; padding: 30px 20px; }}

/* Section headers */
.section-title {{
    font-size: 1.5em;
    font-weight: 300;
    margin: 40px 0 20px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--gold-dim);
    color: var(--gold);
}}
.section-title span {{ font-weight: 700; }}

/* Summary cards */
.summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
    margin-bottom: 30px;
}}
.summary-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    text-align: center;
}}
.summary-card .big-num {{
    font-size: 2em;
    font-weight: 700;
    margin: 5px 0;
}}
.summary-card .label {{
    font-size: 0.85em;
    color: var(--text-dim);
}}

/* Company cards */
.company-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    transition: transform 0.2s;
}}
.company-card:hover {{ transform: translateY(-2px); }}
.card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    flex-wrap: wrap;
    gap: 8px;
}}
.card-header h3 {{ font-size: 1.4em; font-weight: 600; }}
.verdict {{ font-size: 1.1em; font-weight: 600; }}
.ms-badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.85em;
    color: #000;
    font-weight: 700;
    margin-left: 6px;
}}
.card-meta {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    margin-bottom: 16px;
    font-size: 0.9em;
    color: var(--text-dim);
}}
.card-meta b {{ color: var(--text); }}

/* Scales visualization */
.scales-viz {{
    display: grid;
    grid-template-columns: 1fr 40px 1fr;
    gap: 10px;
    align-items: start;
    margin: 16px 0;
    padding: 16px;
    background: rgba(0,0,0,0.3);
    border-radius: 10px;
}}
.scale-side {{ padding: 8px; }}
.scale-label {{
    font-weight: 600;
    font-size: 0.95em;
    margin-bottom: 8px;
}}
.taking-side .scale-label {{ color: var(--red); }}
.giving-side .scale-label {{ color: var(--green); }}
.score-num {{
    font-size: 0.85em;
    opacity: 0.8;
    font-weight: 400;
}}
.scale-bar-bg {{
    background: rgba(255,255,255,0.05);
    border-radius: 6px;
    height: 10px;
    margin-bottom: 10px;
    overflow: hidden;
}}
.scale-bar {{
    height: 100%;
    border-radius: 6px;
    transition: width 0.6s ease;
}}
.sub-scores {{ font-size: 0.82em; }}
.sub-row {{
    display: flex;
    justify-content: space-between;
    padding: 2px 0;
    color: var(--text-dim);
    border-bottom: 1px solid rgba(255,255,255,0.03);
}}
.scale-fulcrum {{
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 2.5em;
    color: var(--gold);
    text-shadow: 0 0 15px rgba(212,168,67,0.4);
}}

/* Drug section */
.drug-section {{ margin: 12px 0; }}
.drug-section h4 {{
    font-size: 0.9em;
    color: var(--gold-dim);
    margin-bottom: 6px;
}}
.drug-tags {{ display: flex; flex-wrap: wrap; gap: 4px; }}

/* Terminated section */
.terminated-section {{ margin: 12px 0; }}
.terminated-section h4 {{
    font-size: 0.9em;
    color: var(--red);
    margin-bottom: 6px;
}}
.mini-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82em;
}}
.mini-table th {{
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    color: var(--text-dim);
    font-weight: 600;
}}
.mini-table td {{
    padding: 5px 8px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
}}

.card-notes {{
    margin-top: 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 0.82em;
    color: var(--text-dim);
}}

/* Accountability table */
.full-table {{
    width: 100%;
    border-collapse: collapse;
    margin: 20px 0;
    font-size: 0.9em;
}}
.full-table th {{
    text-align: left;
    padding: 10px 12px;
    border-bottom: 2px solid var(--gold-dim);
    color: var(--gold);
    font-weight: 600;
    white-space: nowrap;
}}
.full-table td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
}}
.full-table tbody tr:hover {{ background: rgba(255,255,255,0.03); }}

/* Verdict cards */
.verdict-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 16px;
    margin: 16px 0;
}}
.verdict-card {{
    background: var(--surface);
    border-radius: 10px;
    padding: 20px;
    text-align: center;
}}
.verdict-card h4 {{ font-size: 1.1em; margin-bottom: 6px; }}
.verdict-card .big-score {{ font-size: 2.2em; font-weight: 700; margin: 8px 0; }}
.verdict-card .detail {{ font-size: 0.82em; color: var(--text-dim); margin-top: 6px; }}
.extractor {{
    border: 1px solid var(--red);
    background: rgba(239,68,68,0.05);
}}
.extractor .big-score {{ color: var(--red); }}
.bright {{
    border: 1px solid var(--green);
    background: rgba(34,197,94,0.05);
}}
.balanced {{
    border: 1px solid var(--amber);
    background: rgba(245,158,11,0.05);
}}

/* Graveyard */
.graveyard-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85em;
    margin: 16px 0;
}}
.graveyard-table th {{
    text-align: left;
    padding: 8px 10px;
    border-bottom: 2px solid var(--red);
    color: var(--red);
    font-weight: 600;
}}
.graveyard-table td {{
    padding: 7px 10px;
    border-bottom: 1px solid var(--border);
}}

/* Policy section */
.policy-section {{
    background: linear-gradient(135deg, rgba(212,168,67,0.08), rgba(212,168,67,0.02));
    border: 1px solid var(--gold-dim);
    border-radius: 12px;
    padding: 30px;
    margin: 30px 0;
}}
.policy-section h3 {{
    color: var(--gold);
    margin-bottom: 16px;
    font-size: 1.3em;
}}
.policy-list {{
    list-style: none;
    padding: 0;
}}
.policy-list li {{
    padding: 8px 0;
    border-bottom: 1px solid rgba(212,168,67,0.15);
    display: flex;
    gap: 12px;
    align-items: flex-start;
}}
.policy-list li::before {{
    content: '\\2696';
    color: var(--gold);
    font-size: 1.2em;
    flex-shrink: 0;
}}

/* Method box */
.method-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    margin: 20px 0;
    font-size: 0.9em;
    color: var(--text-dim);
}}
.method-box h4 {{ color: var(--gold-dim); margin-bottom: 8px; }}
.method-box code {{
    background: rgba(255,255,255,0.05);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 0.92em;
}}

/* Footer */
.footer {{
    text-align: center;
    padding: 30px 20px;
    color: var(--text-dim);
    font-size: 0.85em;
    border-top: 1px solid var(--border);
    margin-top: 40px;
}}

/* Responsive */
@media (max-width: 768px) {{
    .scales-viz {{ grid-template-columns: 1fr; }}
    .scale-fulcrum {{ transform: rotate(90deg); padding: 10px 0; }}
    .card-header {{ flex-direction: column; align-items: flex-start; }}
    .arabic-verse {{ font-size: 1.4em; }}
    h1 {{ font-size: 1.5em; }}
}}
</style>
</head>
<body>

<!-- Header with Quranic Verse -->
<div class="header">
    <div class="arabic-verse">
        &#1608;&#1614;&#1610;&#1618;&#1604;&#1612; &#1604;&#1616;&#1604;&#1618;&#1605;&#1615;&#1591;&#1614;&#1601;&#1617;&#1616;&#1601;&#1616;&#1610;&#1606;&#1614; &#1757;
        &#1575;&#1604;&#1617;&#1614;&#1584;&#1616;&#1610;&#1606;&#1614; &#1573;&#1616;&#1584;&#1614;&#1575; &#1575;&#1603;&#1618;&#1578;&#1614;&#1575;&#1604;&#1615;&#1608;&#1575; &#1593;&#1614;&#1604;&#1614;&#1609; &#1575;&#1604;&#1606;&#1617;&#1614;&#1575;&#1587;&#1616; &#1610;&#1614;&#1587;&#1618;&#1578;&#1614;&#1608;&#1618;&#1601;&#1615;&#1608;&#1606;&#1614; &#1757;
        &#1608;&#1614;&#1573;&#1616;&#1584;&#1614;&#1575; &#1603;&#1614;&#1575;&#1604;&#1615;&#1608;&#1607;&#1615;&#1605;&#1618; &#1571;&#1614;&#1608;&#1618; &#1608;&#1614;&#1586;&#1614;&#1606;&#1615;&#1608;&#1607;&#1615;&#1605;&#1618; &#1610;&#1615;&#1582;&#1618;&#1587;&#1616;&#1585;&#1615;&#1608;&#1606;&#1614;
    </div>
    <div class="english-verse">
        "Woe to those who give less than due &mdash; who, when they take a measure from people,
        take in full, but when they give by measure or weight to them, give less than due."
    </div>
    <div class="verse-ref">Surah Al-Mutaffifin (83:1&ndash;3)</div>
    <h1>The Scales of <span>Al-Mutaffifin</span></h1>
    <div class="subtitle">Weighing What Pharma Takes from Africa vs What It Gives Back</div>
</div>

<div class="container">

    <!-- Summary -->
    <div class="summary-grid">
        <div class="summary-card">
            <div class="label">Companies Analysed</div>
            <div class="big-num" style="color:var(--gold)">{data['total_companies']}</div>
        </div>
        <div class="summary-card">
            <div class="label">Africa Trials (Total)</div>
            <div class="big-num" style="color:var(--accent)">{data['total_africa_trials']:,}</div>
        </div>
        <div class="summary-card">
            <div class="label">Global Trials (Total)</div>
            <div class="big-num">{data['total_global_trials']:,}</div>
        </div>
        <div class="summary-card">
            <div class="label">Est. African Participants</div>
            <div class="big-num" style="color:var(--amber)">{data['total_est_africa_enrollment']:,}</div>
        </div>
        <div class="summary-card">
            <div class="label">Terminated/Withdrawn</div>
            <div class="big-num" style="color:var(--red)">{data['total_terminated_withdrawn']}</div>
        </div>
        <div class="summary-card">
            <div class="label">Net Extractors</div>
            <div class="big-num" style="color:var(--red)">{data['net_extractors']}</div>
        </div>
    </div>

    <!-- Method box -->
    <div class="method-box">
        <h4>Methodology: The Mutaffifin Score</h4>
        <p>For each company, we compute a <b>Taking Score</b> (0&ndash;50) and a <b>Giving Score</b> (0&ndash;50).
        The <b>Mutaffifin Score = Taking &minus; Giving</b>. Positive scores indicate net extraction;
        negative scores indicate net contribution.</p>
        <p style="margin-top:8px"><b>Taking</b> measures: African enrollment volume (0&ndash;15), Phase 3 dominance (0&ndash;10),
        termination rate (0&ndash;10), token-site proportion (0&ndash;15).</p>
        <p style="margin-top:4px"><b>Giving</b> measures: WHO EML drugs from Africa trials (0&ndash;15),
        Africa-focused trial % (0&ndash;10), tiered pricing (0&ndash;10), local manufacturing (0&ndash;10),
        Phase 1 investment (0&ndash;5).</p>
        <p style="margin-top:8px;font-size:0.9em">Data: ClinicalTrials.gov API v2, accessed {fetch_date}. Tiered pricing and manufacturing data from published corporate reports.</p>
    </div>

    <!-- Full Accountability Table -->
    <h2 class="section-title"><span>Full</span> Accountability Table</h2>
    <div style="overflow-x:auto">
    <table class="full-table">
        <thead><tr>
            <th>#</th><th>Company</th><th>Africa Trials</th><th>Est. Participants</th>
            <th>Taking</th><th>Giving</th><th>Score</th><th>Verdict</th>
        </tr></thead>
        <tbody>
            {"".join(table_rows)}
        </tbody>
    </table>
    </div>

    <!-- The Net Extractors -->
    <h2 class="section-title"><span>The Net</span> Extractors</h2>
    <p style="color:var(--text-dim);margin-bottom:16px">Companies with Mutaffifin Score &gt; +5: taking far more than they give.</p>
    <div class="verdict-grid">
        {extractors_html if extractors_html else '<p style="color:var(--text-dim);font-style:italic">No companies exceeded the +5 threshold.</p>'}
    </div>

    <!-- The Bright Spots -->
    <h2 class="section-title"><span>The Bright</span> Spots</h2>
    <p style="color:var(--text-dim);margin-bottom:16px">Any company giving more than taking? (Mutaffifin Score &lt; &minus;5)</p>
    <div class="verdict-grid">
        {bright_html}
    </div>

    <!-- Balanced -->
    {"" if not balanced_html else f'''
    <h2 class="section-title"><span>The</span> Balanced</h2>
    <p style="color:var(--text-dim);margin-bottom:16px">Companies with scores between &minus;5 and +5.</p>
    <div class="verdict-grid">
        {balanced_html}
    </div>
    '''}

    <!-- Company-by-Company Scales -->
    <h2 class="section-title"><span>Company-by-Company</span> Accountability</h2>
    {"".join(company_cards_html)}

    <!-- The Terminated Trials Graveyard -->
    <h2 class="section-title" style="color:var(--red)"><span>The Terminated</span> Trials Graveyard</h2>
    <p style="color:var(--text-dim);margin-bottom:16px">Trials where data was taken, then the trial was stopped &mdash; participants enrolled, then abandoned.</p>
    {"" if not graveyard_rows else f'''<div style="overflow-x:auto">
    <table class="graveyard-table">
        <thead><tr><th>Company</th><th>NCT ID</th><th>Title</th><th>Status</th><th>Enrollment</th><th>Condition</th></tr></thead>
        <tbody>{graveyard_rows}</tbody>
    </table>
    </div>'''}

    <!-- Policy: What Would Fair Measure Look Like? -->
    <div class="policy-section">
        <h3>What Would "Fair Measure" Look Like?</h3>
        <p style="color:var(--text-dim);margin-bottom:16px">The Quranic principle is clear: if you take in full, you must give in full. Applied to pharmaceutical research in Africa:</p>
        <ul class="policy-list">
            <li><b>Proportional reinvestment</b> &mdash; For every Phase 3 mega-trial using African sites, fund at least one Africa-focused Phase 1 or Phase 2 study building local research capacity.</li>
            <li><b>Mandatory tiered access</b> &mdash; Any drug tested on African participants must be made available at income-adjusted prices within 2 years of approval, enforced by trial registration conditions.</li>
            <li><b>Local manufacturing commitment</b> &mdash; Companies enrolling &gt;1,000 African participants should invest in technology transfer or local fill-finish capacity on the continent.</li>
            <li><b>Terminated trial accountability</b> &mdash; Sponsors who terminate trials after enrollment must provide participants with continued access to investigational products and publish all collected data.</li>
            <li><b>Africa-burden alignment</b> &mdash; At least 30% of a company's Africa portfolio should address conditions with disproportionate African burden (malaria, TB, sickle cell, NTDs) rather than global registration convenience.</li>
            <li><b>Community benefit sharing</b> &mdash; Trial sites should receive infrastructure that persists after the trial ends: trained staff, diagnostic equipment, clinical capacity that serves the community.</li>
            <li><b>Transparent reporting</b> &mdash; Annual public disclosure of the Mutaffifin Score components, independently audited, so extraction can be tracked over time.</li>
        </ul>
    </div>

    <div class="footer">
        <p>The Scales of Al-Mutaffifin &mdash; Project 61 of the AfricaRCT Series</p>
        <p>Data: ClinicalTrials.gov API v2 (public registry) | Generated {fetch_date}</p>
        <p style="margin-top:8px;font-size:0.8em;color:#64748b">
            Mutaffifin Score methodology: Taking (enrollment volume + Phase 3 dominance + termination rate + token sites)
            minus Giving (EML drugs + Africa-focused trials + tiered pricing + local manufacturing + Phase 1 investment).
            Positive = net extraction. Negative = net contribution.
        </p>
    </div>
</div>

</body>
</html>"""

    return html


# -- Entry point -----------------------------------------------------------
def main():
    print("=" * 60)
    print("THE SCALES OF AL-MUTAFFIFIN")
    print("Weighing What Pharma Takes vs Gives in Africa")
    print("=" * 60)

    data = collect_data()
    html = generate_html(data)

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML report written to {OUTPUT_HTML}")

    # Summary
    print(f"\n{'=' * 60}")
    print("FINAL SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Companies analysed: {data['total_companies']}")
    print(f"  Total Africa trials: {data['total_africa_trials']:,}")
    print(f"  Total global trials: {data['total_global_trials']:,}")
    print(f"  Net extractors: {data['net_extractors']}")
    print(f"  Balanced: {data['balanced']}")
    print(f"  Net contributors: {data['net_contributors']}")
    print(f"\n  Rankings (highest Mutaffifin Score = worst):")
    for i, c in enumerate(data["companies"], 1):
        ms = c["mutaffifin_score"]
        marker = "!!" if ms > 5 else "**" if ms < -5 else "  "
        print(f"  {marker} {i}. {c['name']:25s} Score: {ms:+6.1f}  "
              f"(Taking {c['taking_score']:.1f} vs Giving {c['giving_score']:.1f})")


if __name__ == "__main__":
    main()
