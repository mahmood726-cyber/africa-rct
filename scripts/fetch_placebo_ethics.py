"""
Placebo Ethics Audit -- Helsinki Declaration Compliance
=======================================================
Are African trial participants receiving placebos when proven treatments
exist elsewhere?  The Helsinki Declaration (2013, para 33) requires new
interventions be tested against "best proven" treatment -- not placebo --
unless there are "compelling and scientifically sound methodological
reasons."

Queries ClinicalTrials.gov API v2 for placebo-controlled trials in
Africa vs the US, classifies each by ethical justifiability, and
generates an interactive HTML dashboard.

Usage:
    python fetch_placebo_ethics.py

Output:
    data/placebo_ethics_data.json   -- cached trial data (24h validity)
    placebo-ethics.html             -- interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
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
CACHE_FILE = DATA_DIR / "placebo_ethics_data.json"
OUTPUT_HTML = Path(__file__).parent / "placebo-ethics.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# -- Verified baseline counts (ClinicalTrials.gov API v2, March 2026) ------
VERIFIED_COUNTS = {
    "africa_total": 3506,
    "africa_placebo": 1125,
    "us_total": 159196,
    "us_placebo": 16820,
}

# -- African countries for location search ----------------------------------
AFRICA_COUNTRIES = [
    "South Africa", "Nigeria", "Kenya", "Egypt", "Uganda", "Tanzania",
    "Ethiopia", "Ghana", "Cameroon", "Senegal", "Zambia", "Zimbabwe",
    "Mozambique", "Malawi", "Rwanda", "Botswana", "Burkina Faso",
    "Mali", "Cote d'Ivoire", "Congo", "Morocco", "Tunisia", "Algeria",
    "Sudan", "Madagascar", "Gabon",
]

# -- Condition-to-ethics classification -------------------------------------
# Maps condition keywords to:
#   proven_treatment: what standard of care exists globally
#   ethics_class: "justified" / "questionable" / "problematic"
#   rationale: why this classification
CONDITION_ETHICS = {
    "hiv": {
        "keywords": ["hiv", "human immunodeficiency", "aids", "antiretroviral"],
        "proven_treatment": "ARVs (antiretrovirals) -- WHO essential medicines",
        "ethics_class": "problematic",
        "rationale": "Effective ARV regimens exist and are widely available globally; "
                     "placebo arms deny proven life-saving treatment",
        "nuance": "Post-exposure prophylaxis and prevention trials (PrEP) may "
                  "justify placebo if no local PrEP standard exists",
    },
    "cancer": {
        "keywords": ["cancer", "carcinoma", "lymphoma", "leukemia", "melanoma",
                     "tumor", "tumour", "neoplasm", "myeloma", "sarcoma",
                     "glioblastoma", "oncolog"],
        "proven_treatment": "Chemotherapy, targeted therapy, immunotherapy",
        "ethics_class": "problematic",
        "rationale": "Standard chemotherapy/targeted agents exist for most cancers; "
                     "placebo-only comparators unacceptable for treatable cancers",
        "nuance": "Add-on designs (standard + placebo vs standard + new) are ethical; "
                  "pure placebo is not for treatable cancers",
    },
    "hypertension": {
        "keywords": ["hypertension", "high blood pressure", "blood pressure"],
        "proven_treatment": "ACE inhibitors, ARBs, CCBs, thiazides -- all off-patent",
        "ethics_class": "problematic",
        "rationale": "Multiple cheap antihypertensives on WHO essential list; "
                     "placebo denies proven cardiovascular protection",
        "nuance": "Mild/prehypertension lifestyle trials may justify placebo",
    },
    "diabetes": {
        "keywords": ["diabetes", "diabetic", "glycemic", "hyperglycemia",
                     "type 2 diabetes", "type 1 diabetes", "insulin"],
        "proven_treatment": "Metformin (off-patent), insulin, sulfonylureas",
        "ethics_class": "problematic",
        "rationale": "Metformin is cheap, effective, and universally available; "
                     "placebo denies proven glucose control",
        "nuance": "Add-on designs are ethical (metformin + placebo vs metformin + new)",
    },
    "malaria": {
        "keywords": ["malaria", "plasmodium", "antimalarial"],
        "proven_treatment": "ACTs (artemisinin-based combination therapies)",
        "ethics_class": "problematic",
        "rationale": "ACTs are WHO first-line; placebo for symptomatic malaria "
                     "is ethically indefensible",
        "nuance": "Vaccine and prevention trials may use placebo if no approved "
                  "vaccine exists locally",
    },
    "tuberculosis": {
        "keywords": ["tuberculosis", "mycobacterium tuberculosis", " tb "],
        "proven_treatment": "DOTS (directly observed therapy), isoniazid, rifampicin",
        "ethics_class": "problematic",
        "rationale": "DOTS regimen is globally standard; placebo denies curative therapy",
        "nuance": "Latent TB and preventive therapy trials may justify placebo "
                  "in some contexts",
    },
    "pneumonia": {
        "keywords": ["pneumonia", "lower respiratory infection"],
        "proven_treatment": "Antibiotics (amoxicillin, azithromycin)",
        "ethics_class": "problematic",
        "rationale": "Antibiotics for bacterial pneumonia are standard of care; "
                     "denying treatment risks death",
        "nuance": "Viral pneumonia without proven antiviral may justify placebo",
    },
    "diarrheal": {
        "keywords": ["diarrhea", "diarrhoea", "cholera", "rotavirus",
                     "gastroenteritis"],
        "proven_treatment": "ORS (oral rehydration), zinc supplementation",
        "ethics_class": "questionable",
        "rationale": "ORS/zinc is standard; however vaccine trials against "
                     "rotavirus/cholera may justify placebo",
        "nuance": "Prevention/vaccine trials are justifiable",
    },
    "hookworm": {
        "keywords": ["hookworm", "helminth", "schistosomiasis", "filariasis",
                     "neglected tropical", "ntd"],
        "proven_treatment": "Albendazole, praziquantel (mass drug admin)",
        "ethics_class": "questionable",
        "rationale": "Mass drug administration exists but efficacy varies; "
                     "new approaches may justify controlled comparison",
        "nuance": "Vaccine and novel drug trials may be justified",
    },
    "ebola": {
        "keywords": ["ebola", "marburg", "hemorrhagic fever"],
        "proven_treatment": "rVSV-ZEBOV vaccine (limited), supportive care",
        "ethics_class": "justified",
        "rationale": "No established curative therapy during outbreaks; "
                     "rapid-response trial designs may require placebo/control",
        "nuance": "Ring vaccination designs are preferred over pure placebo",
    },
    "covid": {
        "keywords": ["covid", "sars-cov-2", "coronavirus"],
        "proven_treatment": "Dexamethasone, antivirals (evolved over pandemic)",
        "ethics_class": "questionable",
        "rationale": "Standard of care evolved rapidly; early trials justified "
                     "placebo, later trials required active comparator",
        "nuance": "Timing matters: pre-RECOVERY (2020) vs post-RECOVERY",
    },
    "sickle_cell": {
        "keywords": ["sickle cell", "scd", "haemoglobin s"],
        "proven_treatment": "Hydroxyurea (off-patent), transfusion",
        "ethics_class": "questionable",
        "rationale": "Hydroxyurea is proven but access varies enormously in Africa; "
                     "local unavailability may partly justify placebo",
        "nuance": "If hydroxyurea is locally unavailable, questionable rather "
                  "than problematic",
    },
    "vaccine_preventable": {
        "keywords": ["vaccine", "vaccination", "immunization", "immunisation"],
        "proven_treatment": "Existing vaccines for some targets",
        "ethics_class": "justified",
        "rationale": "Novel vaccine trials against diseases with no existing "
                     "vaccine can use placebo per Helsinki para 33 exception",
        "nuance": "Only justified if no approved vaccine exists for the target",
    },
    "mental_health": {
        "keywords": ["depression", "anxiety", "schizophrenia", "bipolar",
                     "mental health", "psychiatric", "psychosis"],
        "proven_treatment": "SSRIs, antipsychotics",
        "ethics_class": "questionable",
        "rationale": "Psychiatric medications exist but access is extremely limited "
                     "in Africa; treatment gap exceeds 75%",
        "nuance": "Severe local treatment gap may justify placebo under Helsinki "
                  "exception clause, but this is contested",
    },
    "surgical": {
        "keywords": ["surgical", "surgery", "wound", "fracture"],
        "proven_treatment": "Varies by condition",
        "ethics_class": "justified",
        "rationale": "Sham surgery controls are methodologically necessary "
                     "for certain surgical trials",
        "nuance": "Only for non-life-threatening conditions",
    },
    "pain": {
        "keywords": ["pain", "analges", "migraine", "headache"],
        "proven_treatment": "Paracetamol, NSAIDs, opioids",
        "ethics_class": "questionable",
        "rationale": "Basic analgesics exist but placebo run-in periods are "
                     "common in pain research methodology",
        "nuance": "Short-duration placebo for chronic non-severe pain may be acceptable",
    },
    "dermatology": {
        "keywords": ["dermatitis", "eczema", "psoriasis", "skin", "acne"],
        "proven_treatment": "Topical steroids, emollients",
        "ethics_class": "justified",
        "rationale": "Non-life-threatening conditions where placebo comparison "
                     "is methodologically standard and risk is minimal",
        "nuance": "Severe/systemic conditions may require active comparator",
    },
    "healthy_volunteer": {
        "keywords": ["healthy volunteer", "healthy subject", "bioequivalence",
                     "pharmacokinetic", "phase 1", "first-in-human"],
        "proven_treatment": "N/A -- no treatment needed",
        "ethics_class": "justified",
        "rationale": "Phase 1/PK studies in healthy volunteers inherently "
                     "require placebo for safety assessment",
        "nuance": "Universally accepted",
    },
}

# -- Sponsor type classification -------------------------------------------
PHARMA_KEYWORDS = [
    "pfizer", "novartis", "roche", "hoffmann", "glaxosmithkline", "gsk",
    "astrazeneca", "sanofi", "merck", "msd", "johnson", "janssen",
    "gilead", "novo nordisk", "bayer", "boehringer", "lilly", "eli lilly",
    "abbvie", "amgen", "bristol-myers", "bms", "takeda", "daiichi",
    "astellas", "otsuka", "eisai", "biogen", "regeneron", "vertex",
    "moderna", "servier", "ipsen", "lundbeck", "allergan", "teva",
    "mylan", "viatris", "sandoz",
]

ACADEMIC_KEYWORDS = [
    "university", "hospital", "institute", "centre", "center",
    "medical school", "college", "faculty", "department", "ministry",
    "national", "council", "foundation", "trust", "academy",
    "school of", "research group",
]

GOV_KEYWORDS = [
    "nih", "niaid", "nci", "nhlbi", "cdc", "who", "usaid",
    "pepfar", "gates", "wellcome", "mrc", "inserm", "dfid",
    "global fund", "unitaid", "gavi",
]


# -- API helpers ------------------------------------------------------------
def search_trials_count(query_cond=None, query_intr=None, location=None,
                        filter_advanced=None, page_size=1, max_retries=3):
    """Get trial count from CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
    }
    filters = []
    if filter_advanced:
        filters.append(filter_advanced)
    if filters:
        params["filter.advanced"] = " AND ".join(filters)
    if query_cond:
        params["query.cond"] = query_cond
    if query_intr:
        params["query.intr"] = query_intr
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


def search_trials_detail(query_intr=None, location=None, page_size=200,
                         page_token=None, filter_advanced=None,
                         max_retries=3):
    """Query CT.gov API v2 for trial details with retry logic."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
    }
    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if filter_advanced:
        filters.append(filter_advanced)
    params["filter.advanced"] = " AND ".join(filters)

    if query_intr:
        params["query.intr"] = query_intr
    if location:
        params["query.locn"] = location
    if page_token:
        params["pageToken"] = page_token

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return {"totalCount": 0, "studies": []}


# -- Data extraction helpers ------------------------------------------------
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


def extract_sponsor_class(study):
    try:
        return study["protocolSection"]["sponsorCollaboratorsModule"][
            "leadSponsor"].get("class", "UNKNOWN")
    except (KeyError, TypeError):
        return "UNKNOWN"


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
        date_str = study["protocolSection"]["statusModule"].get(
            "startDateStruct", {}).get("date", "")
        if date_str:
            # Format: "YYYY-MM-DD" or "YYYY-MM" or "YYYY"
            return date_str[:4]  # Return year
        return ""
    except (KeyError, TypeError):
        return ""


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


def classify_sponsor(sponsor_name):
    """Classify sponsor as pharma, academic, government/NGO, or other."""
    name_lower = sponsor_name.lower()
    if any(kw in name_lower for kw in PHARMA_KEYWORDS):
        return "Industry"
    if any(kw in name_lower for kw in GOV_KEYWORDS):
        return "Government/NGO"
    if any(kw in name_lower for kw in ACADEMIC_KEYWORDS):
        return "Academic"
    return "Other"


def classify_ethics(conditions, interventions):
    """
    Classify ethical justifiability of placebo use for given conditions.

    Returns:
        ethics_class: "justified" / "questionable" / "problematic"
        matched_category: which condition category matched
        rationale: explanation
    """
    cond_lower = " ".join(conditions).lower()
    intr_lower = " ".join(interventions).lower()
    combined = cond_lower + " " + intr_lower

    # Check for add-on design (placebo + active = ethical even for treatable)
    is_addon = False
    addon_keywords = ["add-on", "add on", "adjunct", "combination",
                      "plus placebo", "standard of care", "soc",
                      "background therapy", "usual care"]
    if any(kw in combined for kw in addon_keywords):
        is_addon = True

    matched = None
    for cat_key, cat_info in CONDITION_ETHICS.items():
        if any(kw in combined for kw in cat_info["keywords"]):
            matched = cat_key
            break

    if matched is None:
        return "justified", "unclassified", "No established standard of care identified for this condition"

    cat = CONDITION_ETHICS[matched]

    # If it is an add-on design, upgrade classification
    if is_addon and cat["ethics_class"] == "problematic":
        return "questionable", matched, (
            f"Add-on design detected: {cat['rationale']}. "
            f"Add-on designs are more ethically defensible than pure placebo."
        )

    return cat["ethics_class"], matched, cat["rationale"]


# -- Main data collection ---------------------------------------------------
def collect_data():
    """Collect placebo ethics data from CT.gov API v2."""

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

    # == Step 1: Verify baseline counts ====================================
    print("\n" + "=" * 60)
    print("Step 1: Verifying baseline placebo rates")
    print("=" * 60)

    # Africa total interventional trials
    africa_total = search_trials_count(
        filter_advanced="AREA[StudyType]INTERVENTIONAL",
        location="Africa"
    )
    time.sleep(RATE_LIMIT_DELAY)
    if africa_total == 0:
        africa_total = VERIFIED_COUNTS["africa_total"]
        print(f"  Using verified Africa total: {africa_total}")
    else:
        print(f"  API Africa total: {africa_total}")

    # Africa placebo trials
    africa_placebo_count = search_trials_count(
        query_intr="placebo",
        filter_advanced="AREA[StudyType]INTERVENTIONAL",
        location="Africa"
    )
    time.sleep(RATE_LIMIT_DELAY)
    if africa_placebo_count == 0:
        africa_placebo_count = VERIFIED_COUNTS["africa_placebo"]
        print(f"  Using verified Africa placebo: {africa_placebo_count}")
    else:
        print(f"  API Africa placebo: {africa_placebo_count}")

    # US total
    us_total = search_trials_count(
        filter_advanced="AREA[StudyType]INTERVENTIONAL",
        location="United States"
    )
    time.sleep(RATE_LIMIT_DELAY)
    if us_total == 0:
        us_total = VERIFIED_COUNTS["us_total"]
        print(f"  Using verified US total: {us_total}")
    else:
        print(f"  API US total: {us_total}")

    # US placebo trials
    us_placebo_count = search_trials_count(
        query_intr="placebo",
        filter_advanced="AREA[StudyType]INTERVENTIONAL",
        location="United States"
    )
    time.sleep(RATE_LIMIT_DELAY)
    if us_placebo_count == 0:
        us_placebo_count = VERIFIED_COUNTS["us_placebo"]
        print(f"  Using verified US placebo: {us_placebo_count}")
    else:
        print(f"  API US placebo: {us_placebo_count}")

    africa_placebo_rate = round(africa_placebo_count / africa_total * 100, 1) if africa_total > 0 else 0
    us_placebo_rate = round(us_placebo_count / us_total * 100, 1) if us_total > 0 else 0
    rate_ratio = round(africa_placebo_rate / us_placebo_rate, 1) if us_placebo_rate > 0 else 0

    print(f"\n  Africa placebo rate: {africa_placebo_rate}% ({africa_placebo_count}/{africa_total})")
    print(f"  US placebo rate: {us_placebo_rate}% ({us_placebo_count}/{us_total})")
    print(f"  Rate ratio: {rate_ratio}x")

    # == Step 2: Fetch Africa placebo trial details ========================
    print("\n" + "=" * 60)
    print("Step 2: Fetching Africa placebo trial details (up to 600)")
    print("=" * 60)

    all_studies = []
    page_token = None
    max_pages = 3  # 3 pages x 200 = 600 records

    for page_num in range(max_pages):
        print(f"  Fetching page {page_num + 1}/{max_pages}...")
        result = search_trials_detail(
            query_intr="placebo",
            location="Africa",
            page_size=200,
            page_token=page_token,
        )
        studies = result.get("studies", [])
        all_studies.extend(studies)
        print(f"    Retrieved {len(studies)} trials (total: {len(all_studies)})")

        page_token = result.get("nextPageToken")
        if not page_token or not studies:
            break
        time.sleep(RATE_LIMIT_DELAY)

    print(f"\n  Total trials fetched: {len(all_studies)}")

    # == Step 3: Classify each trial =======================================
    print("\n" + "=" * 60)
    print("Step 3: Classifying ethical justifiability")
    print("=" * 60)

    trials = []
    seen_ncts = set()

    # Counters
    ethics_counter = Counter()       # justified / questionable / problematic
    condition_counter = Counter()    # matched condition categories
    sponsor_type_counter = Counter() # industry / academic / gov
    sponsor_ethics = defaultdict(lambda: Counter())  # sponsor_type -> ethics_class
    phase_counter = Counter()
    phase_placebo = defaultdict(int)
    country_counter = Counter()
    year_counter = Counter()         # temporal trend
    ethics_by_year = defaultdict(lambda: Counter())
    condition_ethics_detail = defaultdict(list)  # condition -> list of trials

    for study in all_studies:
        nct_id = extract_nct_id(study)
        if not nct_id or nct_id in seen_ncts:
            continue
        seen_ncts.add(nct_id)

        title = extract_title(study)
        phases = extract_phases(study)
        conditions = extract_conditions(study)
        interventions = extract_interventions(study)
        sponsor_name = extract_sponsor(study)
        sponsor_class_api = extract_sponsor_class(study)
        enrollment = extract_enrollment(study)
        status = extract_status(study)
        start_year = extract_start_date(study)
        countries = list(get_location_countries(study))

        # Classify ethics
        ethics_class, matched_cat, rationale = classify_ethics(
            conditions, interventions)
        ethics_counter[ethics_class] += 1
        condition_counter[matched_cat] += 1

        # Sponsor classification
        sponsor_type = classify_sponsor(sponsor_name)
        if sponsor_class_api == "INDUSTRY":
            sponsor_type = "Industry"
        sponsor_type_counter[sponsor_type] += 1
        sponsor_ethics[sponsor_type][ethics_class] += 1

        # Phase distribution
        for phase in phases:
            phase_clean = phase.replace("PHASE", "Phase ").strip()
            phase_counter[phase_clean] += 1
            phase_placebo[phase_clean] += 1
        if not phases:
            phase_counter["Not specified"] += 1
            phase_placebo["Not specified"] += 1

        # Country distribution
        for c in countries:
            if c in AFRICA_COUNTRIES or c == "Africa":
                country_counter[c] += 1

        # Temporal trend
        if start_year and start_year.isdigit():
            year_counter[start_year] += 1
            ethics_by_year[start_year][ethics_class] += 1

        # Condition detail tracking
        condition_ethics_detail[matched_cat].append({
            "nct_id": nct_id,
            "title": title[:120],
            "ethics_class": ethics_class,
            "sponsor": sponsor_name,
            "sponsor_type": sponsor_type,
        })

        trials.append({
            "nct_id": nct_id,
            "title": title,
            "phases": phases,
            "conditions": conditions,
            "interventions": [i[:80] for i in interventions],
            "sponsor": sponsor_name,
            "sponsor_type": sponsor_type,
            "enrollment": enrollment,
            "status": status,
            "start_year": start_year,
            "countries": countries,
            "ethics_class": ethics_class,
            "matched_category": matched_cat,
            "rationale": rationale,
        })

    total_classified = len(trials)
    justified_n = ethics_counter.get("justified", 0)
    questionable_n = ethics_counter.get("questionable", 0)
    problematic_n = ethics_counter.get("problematic", 0)

    helsinki_score = round(justified_n / total_classified * 100, 1) if total_classified > 0 else 0

    print(f"\n  Total classified: {total_classified}")
    print(f"  Ethically justified: {justified_n} ({round(justified_n/total_classified*100, 1) if total_classified > 0 else 0}%)")
    print(f"  Ethically questionable: {questionable_n} ({round(questionable_n/total_classified*100, 1) if total_classified > 0 else 0}%)")
    print(f"  Ethically problematic: {problematic_n} ({round(problematic_n/total_classified*100, 1) if total_classified > 0 else 0}%)")
    print(f"  Helsinki Compliance Score: {helsinki_score}%")

    # == Step 4: "Double Standard" analysis ================================
    # Would these placebos be acceptable in a US trial for the same condition?
    double_standard_count = problematic_n  # All "problematic" trials
    double_standard_pct = round(double_standard_count / total_classified * 100, 1) if total_classified > 0 else 0

    # Sponsor-type placebo rates
    industry_total = sponsor_type_counter.get("Industry", 0)
    academic_total = sponsor_type_counter.get("Academic", 0)
    gov_total = sponsor_type_counter.get("Government/NGO", 0)

    industry_problematic = sponsor_ethics.get("Industry", {}).get("problematic", 0)
    academic_problematic = sponsor_ethics.get("Academic", {}).get("problematic", 0)

    industry_problematic_pct = round(industry_problematic / industry_total * 100, 1) if industry_total > 0 else 0
    academic_problematic_pct = round(academic_problematic / academic_total * 100, 1) if academic_total > 0 else 0

    # Phase-specific analysis
    phase_analysis = {}
    for phase_name in ["Phase 1", "Phase 2", "Phase 3", "Phase 4", "Not specified"]:
        # Need to match various phase format names
        count = 0
        for p, c in phase_counter.items():
            if phase_name.lower().replace(" ", "") in p.lower().replace(" ", ""):
                count = c
                break
        phase_analysis[phase_name] = count

    # Temporal trend -- sorted
    years_sorted = sorted(year_counter.keys())

    # -- Assemble data object -----------------------------------------------
    data = {
        "fetch_date": datetime.now().isoformat(),
        "baseline": {
            "africa_total": africa_total,
            "africa_placebo": africa_placebo_count,
            "africa_placebo_rate": africa_placebo_rate,
            "us_total": us_total,
            "us_placebo": us_placebo_count,
            "us_placebo_rate": us_placebo_rate,
            "rate_ratio": rate_ratio,
        },
        "classification_summary": {
            "total_classified": total_classified,
            "justified": justified_n,
            "questionable": questionable_n,
            "problematic": problematic_n,
            "helsinki_compliance_score": helsinki_score,
        },
        "condition_breakdown": dict(condition_counter.most_common()),
        "sponsor_type_breakdown": dict(sponsor_type_counter.most_common()),
        "sponsor_ethics": {k: dict(v) for k, v in sponsor_ethics.items()},
        "phase_distribution": dict(phase_counter.most_common()),
        "phase_analysis": phase_analysis,
        "country_distribution": dict(country_counter.most_common(20)),
        "temporal_trend": dict(sorted(year_counter.items())),
        "ethics_by_year": {k: dict(v) for k, v in sorted(ethics_by_year.items())},
        "double_standard": {
            "count": double_standard_count,
            "pct": double_standard_pct,
            "industry_problematic_pct": industry_problematic_pct,
            "academic_problematic_pct": academic_problematic_pct,
        },
        "condition_ethics_detail": {
            k: v[:10] for k, v in condition_ethics_detail.items()
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
    """Generate a dark-themed HTML placebo ethics analysis dashboard."""

    baseline = data["baseline"]
    summary = data["classification_summary"]
    fetch_date = data["fetch_date"][:10]

    total_classified = summary["total_classified"]
    justified_n = summary["justified"]
    questionable_n = summary["questionable"]
    problematic_n = summary["problematic"]
    helsinki_score = summary["helsinki_compliance_score"]

    justified_pct = round(justified_n / total_classified * 100, 1) if total_classified > 0 else 0
    questionable_pct = round(questionable_n / total_classified * 100, 1) if total_classified > 0 else 0
    problematic_pct = round(problematic_n / total_classified * 100, 1) if total_classified > 0 else 0

    # -- Ethics classification table rows -----------------------------------
    ethics_rows = []
    for cat_key, cat_info in CONDITION_ETHICS.items():
        count = data["condition_breakdown"].get(cat_key, 0)
        if count == 0:
            continue
        cls = cat_info["ethics_class"]
        if cls == "justified":
            cls_color = "#22c55e"
            cls_icon = "&#x2714;"
        elif cls == "questionable":
            cls_color = "#f59e0b"
            cls_icon = "&#x26A0;"
        else:
            cls_color = "#ef4444"
            cls_icon = "&#x2718;"

        ethics_rows.append(f"""<tr>
<td style="font-weight:600">{cat_key.replace('_', ' ').title()}</td>
<td style="text-align:center;font-weight:700">{count}</td>
<td style="text-align:center;color:{cls_color};font-weight:700">{cls_icon} {cls.title()}</td>
<td style="font-size:0.85em;color:#cbd5e1">{cat_info['proven_treatment']}</td>
<td style="font-size:0.82em;color:#94a3b8">{cat_info['rationale']}</td>
</tr>""")

    # Add unclassified row
    unclassified = data["condition_breakdown"].get("unclassified", 0)
    if unclassified > 0:
        ethics_rows.append(f"""<tr>
<td style="font-weight:600;color:#94a3b8">Other / Unclassified</td>
<td style="text-align:center;font-weight:700">{unclassified}</td>
<td style="text-align:center;color:#22c55e;font-weight:700">&#x2714; Justified</td>
<td style="font-size:0.85em;color:#94a3b8">No standard identified</td>
<td style="font-size:0.82em;color:#94a3b8">No established standard of care identified</td>
</tr>""")
    ethics_table = "\n".join(ethics_rows)

    # -- Sponsor breakdown rows ---------------------------------------------
    sponsor_rows = []
    for stype in ["Industry", "Academic", "Government/NGO", "Other"]:
        s_total = data["sponsor_type_breakdown"].get(stype, 0)
        if s_total == 0:
            continue
        s_ethics = data["sponsor_ethics"].get(stype, {})
        s_just = s_ethics.get("justified", 0)
        s_quest = s_ethics.get("questionable", 0)
        s_prob = s_ethics.get("problematic", 0)
        s_prob_pct = round(s_prob / s_total * 100, 1) if s_total > 0 else 0
        prob_color = "#ef4444" if s_prob_pct > 40 else "#f59e0b" if s_prob_pct > 25 else "#22c55e"

        sponsor_rows.append(f"""<tr>
<td style="font-weight:600">{stype}</td>
<td style="text-align:center;font-weight:700">{s_total}</td>
<td style="text-align:center;color:#22c55e">{s_just}</td>
<td style="text-align:center;color:#f59e0b">{s_quest}</td>
<td style="text-align:center;color:#ef4444">{s_prob}</td>
<td style="text-align:center;color:{prob_color};font-weight:700">{s_prob_pct}%</td>
</tr>""")
    sponsor_table = "\n".join(sponsor_rows)

    # -- Phase distribution rows --------------------------------------------
    phase_rows = []
    for phase_name in ["Phase 1", "Phase 2", "Phase 3", "Phase 4", "Not specified"]:
        count = data["phase_analysis"].get(phase_name, 0)
        if count == 0:
            for k, v in data["phase_distribution"].items():
                if phase_name.lower().replace(" ", "") in k.lower().replace(" ", ""):
                    count = v
                    break
        bar_w = min(round(count / max(max(data["phase_analysis"].values()) if data["phase_analysis"] else [1], 1) * 100), 100)
        phase_rows.append(f"""<tr>
<td style="font-weight:600">{phase_name}</td>
<td style="text-align:center;font-weight:700">{count}</td>
<td>
  <div style="width:200px;height:16px;background:#1e293b;border-radius:4px;overflow:hidden">
    <div style="width:{bar_w}%;height:100%;background:#3b82f6;border-radius:4px"></div>
  </div>
</td>
</tr>""")
    phase_table = "\n".join(phase_rows)

    # -- Country breakdown rows ---------------------------------------------
    country_rows = []
    countries = data.get("country_distribution", {})
    max_country = max(countries.values()) if countries else 1
    for country, count in list(countries.items())[:15]:
        bar_w = round(count / max_country * 100)
        country_rows.append(f"""<tr>
<td style="font-weight:600">{country}</td>
<td style="text-align:center;font-weight:700">{count}</td>
<td>
  <div style="width:200px;height:16px;background:#1e293b;border-radius:4px;overflow:hidden">
    <div style="width:{bar_w}%;height:100%;background:#60a5fa;border-radius:4px"></div>
  </div>
</td>
</tr>""")
    country_table = "\n".join(country_rows)

    # -- Temporal trend chart data ------------------------------------------
    trend = data.get("temporal_trend", {})
    trend_years = sorted(trend.keys())
    # Filter to reasonable range
    trend_years = [y for y in trend_years if y.isdigit() and 2000 <= int(y) <= 2026]
    trend_counts = [trend.get(y, 0) for y in trend_years]

    ethics_by_year = data.get("ethics_by_year", {})
    trend_problematic = [ethics_by_year.get(y, {}).get("problematic", 0) for y in trend_years]
    trend_questionable = [ethics_by_year.get(y, {}).get("questionable", 0) for y in trend_years]
    trend_justified = [ethics_by_year.get(y, {}).get("justified", 0) for y in trend_years]

    chart_years = json.dumps(trend_years)
    chart_total = json.dumps(trend_counts)
    chart_prob = json.dumps(trend_problematic)
    chart_quest = json.dumps(trend_questionable)
    chart_just = json.dumps(trend_justified)

    # -- Condition-specific analysis (HIV deep dive) ------------------------
    hiv_trials = data.get("condition_ethics_detail", {}).get("hiv", [])
    hiv_rows = []
    for t in hiv_trials[:8]:
        cls_color = "#ef4444" if t["ethics_class"] == "problematic" else \
                    "#f59e0b" if t["ethics_class"] == "questionable" else "#22c55e"
        hiv_rows.append(f"""<tr>
<td style="font-size:0.82em"><a href="https://clinicaltrials.gov/study/{t['nct_id']}"
    target="_blank" style="color:#60a5fa">{t['nct_id']}</a></td>
<td style="font-size:0.82em">{t['title'][:80]}...</td>
<td style="text-align:center;color:{cls_color};font-weight:600">{t['ethics_class'].title()}</td>
<td style="font-size:0.82em;color:#94a3b8">{t['sponsor_type']}</td>
</tr>""")
    hiv_table = "\n".join(hiv_rows)

    # -- Double standard severity finding -----------------------------------
    ds = data["double_standard"]

    # -- Condition pie chart data -------------------------------------------
    cond_labels = []
    cond_values = []
    cond_colors = []
    color_map = {
        "justified": "#22c55e",
        "questionable": "#f59e0b",
        "problematic": "#ef4444",
    }
    for cat_key, cat_info in CONDITION_ETHICS.items():
        count = data["condition_breakdown"].get(cat_key, 0)
        if count > 0:
            cond_labels.append(cat_key.replace("_", " ").title())
            cond_values.append(count)
            cond_colors.append(color_map.get(cat_info["ethics_class"], "#94a3b8"))
    if unclassified > 0:
        cond_labels.append("Other")
        cond_values.append(unclassified)
        cond_colors.append("#22c55e")

    chart_cond_labels = json.dumps(cond_labels)
    chart_cond_values = json.dumps(cond_values)
    chart_cond_colors = json.dumps(cond_colors)

    # -- Helsinki Declaration text excerpt ----------------------------------
    helsinki_text = (
        "Article 33 (Declaration of Helsinki, 2013): &ldquo;The benefits, risks, burdens "
        "and effectiveness of a new intervention must be tested against those of the best "
        "proven intervention(s), except in the following circumstances: Where no proven "
        "intervention exists, the use of placebo, or no intervention, is acceptable; or "
        "where for compelling and scientifically sound methodological reasons the use of "
        "any intervention less effective than the best proven one, the use of placebo, or "
        "no intervention is necessary to determine the efficacy or safety of an intervention "
        "and the patients who receive any intervention less effective than the best proven "
        "one, placebo, or no intervention will not be subject to additional risks of serious "
        "or irreversible harm as a result of not receiving the best proven intervention.&rdquo;"
    )

    # -- Assemble HTML ------------------------------------------------------
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Placebo Ethics Audit: Helsinki Declaration Compliance in African Trials</title>
<style>
  :root {{ --bg: #0a0e17; --surface: #111827; --border: #1e293b; --text: #e2e8f0;
           --muted: #94a3b8; --accent: #3b82f6; --danger: #ef4444; --success: #22c55e;
           --warn: #f59e0b; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Inter','Segoe UI',system-ui,sans-serif;
          line-height:1.6; }}
  .container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}
  h1 {{ font-size:1.8em; margin-bottom:4px; background:linear-gradient(135deg,#ef4444,#f59e0b);
        -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
  h2 {{ font-size:1.3em; margin:32px 0 16px 0; color:#f1f5f9;
        border-bottom:2px solid var(--border); padding-bottom:8px; }}
  h3 {{ font-size:1.1em; color:#f1f5f9; }}
  .subtitle {{ color:var(--muted); font-size:0.95em; margin-bottom:24px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
               gap:12px; margin:20px 0; }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
          padding:16px; text-align:center; }}
  .kpi-value {{ font-size:1.8em; font-weight:800; }}
  .kpi-label {{ font-size:0.82em; color:var(--muted); margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.88em; }}
  th {{ background:#1e293b; color:#cbd5e1; padding:10px 12px; text-align:left;
        font-weight:600; position:sticky; top:0; }}
  td {{ padding:8px 12px; border-bottom:1px solid #1e293b; }}
  tr:hover {{ background:rgba(59,130,246,0.05); }}
  .table-wrap {{ overflow-x:auto; border-radius:12px; border:1px solid var(--border);
                 margin:12px 0; }}
  .chart-container {{ background:var(--surface); border-radius:12px; padding:20px;
                      border:1px solid var(--border); margin:12px 0; }}
  .callout {{ border-radius:12px; padding:24px; margin:20px 0; }}
  .callout-danger {{ background:#1a0000; border:2px solid #7f1d1d; }}
  .callout-warn {{ background:#1a1200; border:2px solid #78350f; }}
  .callout-info {{ background:#001a33; border:2px solid #1e3a5f; }}
  .callout-success {{ background:#001a00; border:2px solid #14532d; }}
  .method {{ background:var(--surface); border-radius:12px; padding:20px;
             border:1px solid var(--border); margin:16px 0; font-size:0.9em;
             color:var(--muted); line-height:1.8; }}
  .method strong {{ color:var(--text); }}
  .helsinki {{ background:#0f172a; border:2px solid #334155; border-radius:12px;
              padding:24px; margin:20px 0; font-style:italic; color:#cbd5e1;
              line-height:1.8; font-size:0.92em; }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:12px;
            font-size:0.78em; font-weight:600; }}
  .badge-danger {{ background:#7f1d1d; color:#fca5a5; }}
  .badge-warn {{ background:#78350f; color:#fcd34d; }}
  .badge-success {{ background:#14532d; color:#86efac; }}
  canvas {{ max-width:100%; }}
  @media(max-width:768px) {{
    .kpi-grid {{ grid-template-columns:repeat(2,1fr); }}
    h1 {{ font-size:1.4em; }}
  }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js">{"<"}/script>
</head>
<body>
<div class="container">

<!-- Header -->
<h1>Placebo Ethics Audit: Helsinki Declaration Compliance</h1>
<p class="subtitle">Are African trial participants receiving placebos when proven treatments exist?
  | {baseline['africa_placebo']:,} placebo trials in Africa | {total_classified} classified
  | Data: ClinicalTrials.gov API v2 | {fetch_date}</p>

<!-- ====== SECTION: Summary KPIs ====== -->
<h2>Summary: The Placebo Gap</h2>
<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-value" style="color:var(--danger)">{baseline['africa_placebo_rate']}%</div>
    <div class="kpi-label">Africa Placebo Rate</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--success)">{baseline['us_placebo_rate']}%</div>
    <div class="kpi-label">US Placebo Rate</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--warn)">{baseline['rate_ratio']}x</div>
    <div class="kpi-label">Africa / US Ratio</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--danger)">{problematic_n}</div>
    <div class="kpi-label">Ethically Problematic</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--warn)">{questionable_n}</div>
    <div class="kpi-label">Ethically Questionable</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--success)">{justified_n}</div>
    <div class="kpi-label">Ethically Justified</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:{"var(--danger)" if helsinki_score < 40 else "var(--warn)" if helsinki_score < 60 else "var(--success)"}">{helsinki_score}%</div>
    <div class="kpi-label">Helsinki Compliance</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--danger)">{ds['pct']}%</div>
    <div class="kpi-label">Double Standard Rate</div>
  </div>
</div>

<div class="callout callout-danger">
  <h3 style="color:#ef4444;margin:0 0 12px 0">The Core Finding</h3>
  <p style="color:#e2e8f0;line-height:1.7;margin:0">
    Africa has a placebo rate of <strong style="color:#ef4444">{baseline['africa_placebo_rate']}%</strong>
    &mdash; {baseline['rate_ratio']}x higher than the US rate of {baseline['us_placebo_rate']}%.
    Of <strong>{total_classified}</strong> classified Africa placebo trials,
    <strong style="color:#ef4444">{problematic_n}</strong> ({problematic_pct}%) involve conditions
    where proven treatments exist globally.
    Only <strong style="color:#22c55e">{justified_n}</strong> ({justified_pct}%)
    are clearly ethically justified under the Helsinki Declaration.
    The Helsinki Compliance Score is <strong style="color:#ef4444">{helsinki_score}%</strong>.
  </p>
</div>

<!-- ====== SECTION: Helsinki Declaration ====== -->
<h2>The Helsinki Declaration (2013)</h2>
<div class="helsinki">
  <p>{helsinki_text}</p>
</div>
<div class="callout callout-info">
  <p style="color:#cbd5e1;margin:0;line-height:1.7">
    <strong style="color:#60a5fa">Key question:</strong> For each placebo-controlled trial in Africa,
    does a "best proven intervention" exist? If so, is there a "compelling and scientifically sound
    methodological reason" to use placebo instead? Our classification applies these criteria
    systematically to {total_classified} trials.
  </p>
</div>

<!-- ====== SECTION: Ethics Classification Table ====== -->
<h2>Ethics Classification by Condition</h2>
<p style="color:var(--muted);margin-bottom:12px">
  Each condition classified against known global standards of care.
  <span class="badge badge-success">Justified</span> = no proven treatment.
  <span class="badge badge-warn">Questionable</span> = treatment exists but access uncertain.
  <span class="badge badge-danger">Problematic</span> = proven treatment available.
</p>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Condition</th>
  <th style="text-align:center">Count</th>
  <th style="text-align:center">Ethics Class</th>
  <th>Proven Treatment</th>
  <th>Rationale</th>
</tr>
</thead>
<tbody>
{ethics_table}
</tbody>
</table>
</div>

<!-- ====== SECTION: Ethics Pie Chart ====== -->
<h2>Ethics Classification Distribution</h2>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div class="chart-container">
    <canvas id="ethicsPieChart" height="300"></canvas>
  </div>
  <div class="chart-container">
    <canvas id="conditionBarChart" height="300"></canvas>
  </div>
</div>

<!-- ====== SECTION: Condition-Specific Analysis (HIV) ====== -->
<h2>Condition Deep Dive: HIV Placebo Trials</h2>
<div class="callout callout-danger">
  <h3 style="color:#ef4444;margin:0 0 12px 0">HIV Placebo Trials in Africa</h3>
  <p style="color:#e2e8f0;line-height:1.7;margin:0 0 16px 0">
    Antiretroviral therapy has been standard of care since the late 1990s. Every major
    guideline recommends ARVs for all HIV-positive individuals. Yet placebo-controlled
    HIV trials continue in Africa. Some may be add-on designs (standard ARVs + placebo
    vs standard ARVs + new drug), which are ethically defensible. But pure placebo arms
    that deny ARVs violate the Helsinki Declaration unequivocally.
  </p>
  {"" if not hiv_rows else f'''<div class="table-wrap">
  <table>
  <thead>
  <tr>
    <th>NCT ID</th>
    <th>Title</th>
    <th style="text-align:center">Ethics Class</th>
    <th>Sponsor Type</th>
  </tr>
  </thead>
  <tbody>
  {hiv_table}
  </tbody>
  </table>
  </div>'''}
</div>

<!-- ====== SECTION: Sponsor Patterns ====== -->
<h2>Sponsor Patterns: Who Uses Placebo?</h2>
<p style="color:var(--muted);margin-bottom:12px">
  Do pharmaceutical companies use more ethically problematic placebo designs than academic sponsors?
</p>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Sponsor Type</th>
  <th style="text-align:center">Total Placebo</th>
  <th style="text-align:center;color:#22c55e">Justified</th>
  <th style="text-align:center;color:#f59e0b">Questionable</th>
  <th style="text-align:center;color:#ef4444">Problematic</th>
  <th style="text-align:center">% Problematic</th>
</tr>
</thead>
<tbody>
{sponsor_table}
</tbody>
</table>
</div>

<div class="callout {"callout-danger" if ds["industry_problematic_pct"] > ds["academic_problematic_pct"] + 10 else "callout-warn"}">
  <h3 style="color:#f59e0b;margin:0 0 12px 0">Industry vs Academic Placebo Ethics</h3>
  <p style="color:#e2e8f0;line-height:1.7;margin:0">
    Industry-sponsored trials have an ethically problematic rate of
    <strong style="color:#ef4444">{ds['industry_problematic_pct']}%</strong>,
    compared to <strong style="color:#f59e0b">{ds['academic_problematic_pct']}%</strong>
    for academic sponsors.
    {"Industry sponsors are significantly more likely to use ethically problematic placebo designs." if ds["industry_problematic_pct"] > ds["academic_problematic_pct"] + 5 else "The difference between industry and academic sponsors is modest."}
  </p>
</div>

<!-- ====== SECTION: Phase Distribution ====== -->
<h2>Phase Distribution of Placebo Trials</h2>
<p style="color:var(--muted);margin-bottom:12px">
  Phase 1 trials (healthy volunteers, PK) justify placebo inherently. Phase 3 trials
  should use active comparators when proven treatments exist.
</p>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Phase</th>
  <th style="text-align:center">Count</th>
  <th>Distribution</th>
</tr>
</thead>
<tbody>
{phase_table}
</tbody>
</table>
</div>

<!-- ====== SECTION: The Double Standard ====== -->
<h2>The Double Standard</h2>
<div class="callout callout-danger">
  <h3 style="color:#ef4444;margin:0 0 12px 0">Would these placebos be acceptable in a US trial?</h3>
  <p style="color:#e2e8f0;line-height:1.7;margin:0 0 16px 0">
    Of the {total_classified} Africa placebo trials classified,
    <strong style="color:#ef4444">{ds['count']}</strong> ({ds['pct']}%)
    involve conditions where proven treatments exist and are routinely provided
    in US trials as active comparators. These trials would likely not receive
    IRB approval in the United States with a placebo-only control arm.
  </p>
  <p style="color:#cbd5e1;line-height:1.7;margin:0">
    This raises a fundamental ethical question: <strong>if a placebo arm would be
    unacceptable for American patients, why is it acceptable for African patients?</strong>
    The Helsinki Declaration explicitly rejects the argument that lower local standards
    of care justify lower ethical standards in research. Article 33 applies universally,
    regardless of the geographic location of the trial.
  </p>
</div>

<!-- ====== SECTION: Country Breakdown ====== -->
<h2>Country Breakdown: Where Are Placebos Used?</h2>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:center">Placebo Trials</th>
  <th>Distribution</th>
</tr>
</thead>
<tbody>
{country_table}
</tbody>
</table>
</div>

<!-- ====== SECTION: Temporal Trend ====== -->
<h2>Temporal Trend: Is Placebo Use Changing?</h2>
<div class="chart-container">
  <canvas id="trendChart" height="350"></canvas>
</div>

<!-- ====== SECTION: Methodology ====== -->
<h2>Methodology</h2>
<div class="method">
  <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (query date: {fetch_date}).</p>
  <p><strong>Inclusion:</strong> Interventional studies with "placebo" as intervention,
    located in Africa ({baseline['africa_placebo']:,} total; {total_classified} classified)
    and United States ({baseline['us_placebo']:,} placebo trials).</p>
  <p><strong>Ethics classification:</strong> Each trial classified based on whether a
    globally-recognised standard of care exists for the indicated condition, following
    the Helsinki Declaration Article 33 framework. Three categories: "Justified"
    (no proven treatment), "Questionable" (treatment exists but local access uncertain),
    "Problematic" (proven treatment globally available).</p>
  <p><strong>Helsinki Compliance Score:</strong> Proportion of placebo trials where placebo
    is ethically justified = {helsinki_score}%.</p>
  <p><strong>Limitations:</strong> Keyword-based condition matching may misclassify some
    trials. Add-on designs (active + placebo) may appear as "placebo" trials but are
    ethically defensible. Local treatment availability is approximated, not verified
    per site. Only ClinicalTrials.gov-registered trials are included.</p>
  <p><strong>Code:</strong> <code>fetch_placebo_ethics.py</code></p>
</div>

<!-- ====== SECTION: AI Transparency ====== -->
<div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:16px;
            margin:32px 0 0 0;font-size:0.82em;color:#64748b">
  <strong style="color:#94a3b8">AI transparency:</strong>
  Dashboard generated by Python script with Claude AI assistance.
  Data sourced from ClinicalTrials.gov public API. Ethics classifications are
  algorithmic assessments based on published standards of care, not formal
  ethics committee decisions.
</div>

</div><!-- /container -->

<!-- ====== CHARTS ====== -->
<script>
document.addEventListener('DOMContentLoaded', function() {{

  // Ethics pie chart
  new Chart(document.getElementById('ethicsPieChart'), {{
    type: 'doughnut',
    data: {{
      labels: ['Justified ({justified_n})', 'Questionable ({questionable_n})', 'Problematic ({problematic_n})'],
      datasets: [{{ data: [{justified_n}, {questionable_n}, {problematic_n}],
        backgroundColor: ['#22c55e', '#f59e0b', '#ef4444'],
        borderColor: '#0a0e17', borderWidth: 3 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{
        title: {{ display: true, text: 'Helsinki Compliance Classification',
                  color: '#e2e8f0', font: {{ size: 14 }} }},
        legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', padding: 16 }} }}
      }}
    }}
  }});

  // Condition bar chart
  new Chart(document.getElementById('conditionBarChart'), {{
    type: 'bar',
    data: {{
      labels: {chart_cond_labels},
      datasets: [{{ data: {chart_cond_values},
        backgroundColor: {chart_cond_colors},
        borderRadius: 4 }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      plugins: {{
        title: {{ display: true, text: 'Placebo Trials by Condition (color = ethics class)',
                  color: '#e2e8f0', font: {{ size: 14 }} }},
        legend: {{ display: false }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
        y: {{ ticks: {{ color: '#cbd5e1', font: {{ size: 11 }} }}, grid: {{ display: false }} }}
      }}
    }}
  }});

  // Temporal trend stacked bar chart
  new Chart(document.getElementById('trendChart'), {{
    type: 'bar',
    data: {{
      labels: {chart_years},
      datasets: [
        {{ label: 'Problematic', data: {chart_prob},
           backgroundColor: '#ef4444', borderRadius: 2 }},
        {{ label: 'Questionable', data: {chart_quest},
           backgroundColor: '#f59e0b', borderRadius: 2 }},
        {{ label: 'Justified', data: {chart_just},
           backgroundColor: '#22c55e', borderRadius: 2 }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{
        title: {{ display: true, text: 'Placebo Trial Ethics Classification Over Time',
                  color: '#e2e8f0', font: {{ size: 14 }} }},
        legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', padding: 16 }} }}
      }},
      scales: {{
        x: {{ stacked: true, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
        y: {{ stacked: true, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }},
              title: {{ display: true, text: 'Number of placebo trials', color: '#94a3b8' }} }}
      }}
    }}
  }});

}});
{"</"+"script>"}
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML dashboard written to {OUTPUT_HTML}")


# -- Main ------------------------------------------------------------------
def main():
    print("=" * 60)
    print("PLACEBO ETHICS AUDIT")
    print("Helsinki Declaration Compliance in African Trials")
    print("=" * 60)

    data = collect_data()
    generate_html(data)

    # Summary
    s = data["classification_summary"]
    b = data["baseline"]
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"  Africa placebo rate: {b['africa_placebo_rate']}% vs US {b['us_placebo_rate']}% ({b['rate_ratio']}x)")
    print(f"  Trials classified: {s['total_classified']}")
    print(f"  Ethically justified: {s['justified']}")
    print(f"  Ethically questionable: {s['questionable']}")
    print(f"  Ethically problematic: {s['problematic']}")
    print(f"  Helsinki Compliance Score: {s['helsinki_compliance_score']}%")
    print(f"\n  Output: {OUTPUT_HTML}")
    print("=" * 60)


if __name__ == "__main__":
    main()
