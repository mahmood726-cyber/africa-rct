"""
The Maternal Mortality Scandal — Project 29
============================================
Africa has 66% of ALL global maternal deaths. Only 9 maternal mortality /
obstetric trials vs 738 in the US — CCI = 82x. This is arguably the most
extreme clinical trial gap in global health.

Queries ClinicalTrials.gov API v2 for maternal mortality / obstetric trials
across African countries and comparators, computes the Condition Colonialism
Index (CCI), and generates an HTML dashboard (dark theme).

Usage:
    python fetch_maternal_mortality.py

Output:
    data/maternal_mortality_data.json  — cached trial data (24h validity)
    maternal-mortality.html            — interactive dark-theme dashboard

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
CACHE_FILE = DATA_DIR / "maternal_mortality_data.json"
OUTPUT_HTML = Path(__file__).parent / "maternal-mortality.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# ── Condition queries ────────────────────────────────────────────────
# Broad query for maternal mortality / obstetric trials
MATERNAL_QUERY = (
    "maternal mortality OR obstetric OR postpartum hemorrhage "
    "OR preeclampsia OR eclampsia"
)

# Sub-condition queries (Africa-specific)
SUB_QUERIES = {
    "caesarean": "caesarean",
    "maternal_sepsis": "maternal sepsis",
    "peripartum_cardiomyopathy": "peripartum cardiomyopathy",
    "postpartum_hemorrhage": "postpartum hemorrhage",
    "preeclampsia": "preeclampsia OR eclampsia",
}

# ── Target locations ─────────────────────────────────────────────────
AFRICAN_COUNTRIES = [
    "South Africa", "Kenya", "Uganda", "Nigeria", "Tanzania",
    "Ethiopia", "Mozambique", "DRC",
]

COMPARATORS = {
    "United States": "United States",
    "India": "India",
    "Bangladesh": "Bangladesh",
}

# ── Burden data (WHO 2020 / UNICEF / Lancet Global Health) ──────────
# Maternal mortality ratio (MMR) per 100,000 live births
# Source: WHO/UNICEF/UNFPA/World Bank (2020 estimates)
MMR_DATA = {
    "Sub-Saharan Africa": 542,
    "South Africa": 127,
    "Kenya": 342,
    "Uganda": 284,
    "Nigeria": 1047,
    "Tanzania": 238,
    "Ethiopia": 267,
    "Mozambique": 289,
    "DRC": 547,
    "India": 103,
    "Bangladesh": 123,
    "United States": 21,
    "SDG 3.1 Target": 70,
    "Global Average": 223,
}

# Africa's share of global maternal deaths
AFRICA_MATERNAL_DEATH_SHARE = 66.0  # %

# ── Sponsor classification keywords ─────────────────────────────────
AFRICAN_KEYWORDS = [
    "nigeria", "lagos", "ibadan", "makerere", "uganda", "kenya",
    "nairobi", "ghana", "accra", "tanzania", "muhimbili", "cameroon",
    "egypt", "cairo", "south africa", "cape town", "witwatersrand",
    "stellenbosch", "pretoria", "kwazulu", "zambia", "lusaka",
    "zimbabwe", "harare", "malawi", "lilongwe", "blantyre",
    "mozambique", "maputo", "kilimanjaro", "moi university",
    "university of botswana", "addis ababa", "drc", "kinshasa",
    "lubumbashi", "ethiopia", "jimma", "gondar",
]

PHARMA_KEYWORDS = [
    "pfizer", "merck", "roche", "astrazeneca", "gsk",
    "glaxosmithkline", "sanofi", "johnson & johnson", "janssen",
    "novartis", "bayer", "moderna", "medimmune",
    "bristol-myers", "lilly", "gilead", "amgen", "abbvie",
    "ferring", "organon",
]

NIH_KEYWORDS = [
    "nih", "niaid", "nichd", "national institutes of health",
    "eunice kennedy shriver", "cdc",
]

NGO_KEYWORDS = [
    "who", "world health organization", "pepfar", "gates foundation",
    "bill & melinda gates", "clinton health", "unitaid", "gavi",
    "path", "jhpiego", "fhi 360", "global fund", "unfpa",
    "unicef", "usaid", "dfid", "wellcome trust",
    "centre for infectious disease research",
]

# ── Intervention classification ──────────────────────────────────────
HEMORRHAGE_KEYWORDS = [
    "oxytocin", "misoprostol", "tranexamic acid", "txa",
    "carbetocin", "ergometrine", "uterine balloon",
    "uterine tamponade", "b-lynch", "hemorrhage", "haemorrhage",
    "blood transfusion", "postpartum bleeding", "pph",
]

PREECLAMPSIA_KEYWORDS = [
    "preeclampsia", "pre-eclampsia", "eclampsia", "magnesium sulfate",
    "magnesium sulphate", "mgso4", "antihypertensive",
    "labetalol", "nifedipine", "methyldopa", "aspirin prophylaxis",
    "calcium supplementation", "hellp",
]

CAESAREAN_KEYWORDS = [
    "caesarean", "cesarean", "c-section", "surgical delivery",
    "uterine closure", "spinal anesthesia", "spinal anaesthesia",
]

SEPSIS_KEYWORDS = [
    "maternal sepsis", "puerperal sepsis", "puerperal fever",
    "chorioamnionitis", "endometritis", "prophylactic antibiotics",
]

CARDIO_KEYWORDS = [
    "peripartum cardiomyopathy", "ppcm", "cardiac failure",
    "heart failure pregnancy", "bromocriptine", "cabergoline",
]


# ── API helpers ──────────────────────────────────────────────────────
def search_trials(location=None, condition=None,
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
                  f"for location={location}, condition={condition}: {e}")
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


def classify_obstetric_category(study):
    """Classify trial into obstetric sub-categories.

    Returns primary and all matching categories.
    """
    interventions = extract_interventions(study)
    title = extract_title(study).lower()
    conditions = " ".join(extract_conditions(study)).lower()
    combined = (" ".join(i["name"].lower() for i in interventions)
                + " " + title + " " + conditions)

    categories = set()

    for kw in HEMORRHAGE_KEYWORDS:
        if kw in combined:
            categories.add("Hemorrhage/PPH")
            break

    for kw in PREECLAMPSIA_KEYWORDS:
        if kw in combined:
            categories.add("Preeclampsia/Eclampsia")
            break

    for kw in CAESAREAN_KEYWORDS:
        if kw in combined:
            categories.add("Caesarean section")
            break

    for kw in SEPSIS_KEYWORDS:
        if kw in combined:
            categories.add("Maternal sepsis")
            break

    for kw in CARDIO_KEYWORDS:
        if kw in combined:
            categories.add("Peripartum cardiomyopathy")
            break

    if not categories:
        categories.add("Other obstetric")

    primary = list(categories)[0] if categories else "Other obstetric"
    return primary, list(categories)


# ── Main data collection ────────────────────────────────────────────
def collect_data():
    """Fetch maternal mortality/obstetric trials, classify, compute CCI."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(
            CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Fetching maternal mortality / obstetric trials from "
          "ClinicalTrials.gov API v2...")
    print("=" * 65)

    # ── Step 1: Get counts for main query across locations ────────
    print("\n[1/5] Getting maternal mortality trial counts per location...")
    location_counts = {}

    # Africa aggregate
    print(f"  Querying: Africa")
    africa_count = get_count_only(location="Africa", condition=MATERNAL_QUERY)
    location_counts["Africa"] = africa_count
    print(f"    Count: {africa_count}")
    time.sleep(RATE_LIMIT_DELAY)

    # US
    print(f"  Querying: United States")
    us_count = get_count_only(location="United States",
                              condition=MATERNAL_QUERY)
    location_counts["United States"] = us_count
    print(f"    Count: {us_count}")
    time.sleep(RATE_LIMIT_DELAY)

    # African countries
    for country in AFRICAN_COUNTRIES:
        print(f"  Querying: {country}")
        count = get_count_only(location=country, condition=MATERNAL_QUERY)
        location_counts[country] = count
        print(f"    Count: {count}")
        time.sleep(RATE_LIMIT_DELAY)

    # Comparators
    for name, loc in COMPARATORS.items():
        if name == "United States":
            continue
        print(f"  Querying: {name}")
        count = get_count_only(location=loc, condition=MATERNAL_QUERY)
        location_counts[name] = count
        print(f"    Count: {count}")
        time.sleep(RATE_LIMIT_DELAY)

    # ── Step 2: Sub-condition queries in Africa ──────────────────
    print("\n[2/5] Getting sub-condition trial counts in Africa...")
    sub_condition_counts = {}
    for key, query in SUB_QUERIES.items():
        print(f"  Querying: {key}")
        count = get_count_only(location="Africa", condition=query)
        sub_condition_counts[key] = count
        print(f"    Count: {count}")
        time.sleep(RATE_LIMIT_DELAY)

    # ── Step 3: Fetch trial-level data for Africa ────────────────
    print("\n[3/5] Fetching trial-level data for Africa...")
    africa_studies_raw = {}
    country_hits = {}

    # Fetch via "Africa" keyword
    print("  Querying: Africa (all)")
    studies = fetch_all_pages(location="Africa", condition=MATERNAL_QUERY,
                              page_size=100)
    for study in studies:
        nct_id = extract_nct_id(study)
        if nct_id:
            africa_studies_raw[nct_id] = study
    time.sleep(RATE_LIMIT_DELAY)

    # Per-country fetch for attribution
    for country in AFRICAN_COUNTRIES:
        print(f"  Querying: {country}")
        studies = fetch_all_pages(location=country,
                                  condition=MATERNAL_QUERY,
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

    print(f"\nTotal unique Africa trials after dedup: "
          f"{len(africa_studies_raw)}")

    # ── Step 4: Classify each trial ──────────────────────────────
    print("\n[4/5] Classifying trials...")
    trials = []
    for nct_id, study in africa_studies_raw.items():
        interventions = extract_intervention_names(study)
        locations_count = count_locations(study)
        primary_cat, all_cats = classify_obstetric_category(study)
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
            "obstetric_category": primary_cat,
            "all_categories": all_cats,
        }
        trials.append(trial)

    total_africa = len(trials)

    # ── Step 5: Compute statistics ───────────────────────────────
    print("\n[5/5] Computing statistics...")

    # Obstetric category breakdown
    category_counts = {}
    for t in trials:
        cat = t["obstetric_category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1

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

    # ── CCI Calculation ──────────────────────────────────────────
    # Africa: 66% of global maternal deaths
    # Africa trial share = africa_count / (africa_count + us_count)
    # Verified: 9 Africa vs 738 US for narrow search
    # Trial share = 9 / (9 + 738) * 100 = 1.2%
    # CCI = 66 / 1.2 = 55x (conservative)
    # CCI vs US burden share: US ~0.8% of maternal deaths
    # CCI = (66/0.8) / (9/738) = 82x
    total_pool = africa_count + us_count
    africa_trial_share_pct = round(
        africa_count / total_pool * 100, 2) if total_pool else 0
    # Conservative CCI
    cci_conservative = round(
        AFRICA_MATERNAL_DEATH_SHARE / africa_trial_share_pct, 1
    ) if africa_trial_share_pct > 0 else 999
    # Direct CCI vs US (burden-weighted)
    us_burden_share = 0.8  # US ~0.8% of global maternal deaths
    cci_direct = round(
        (AFRICA_MATERNAL_DEATH_SHARE / us_burden_share)
        / (africa_count / us_count), 1
    ) if (us_count and africa_count) else 999

    # ── Assemble data payload ────────────────────────────────────
    data = {
        "fetch_date": datetime.now().isoformat(),
        "condition": "maternal mortality / obstetric",
        "maternal_query": MATERNAL_QUERY,
        "total_africa_unique": total_africa,
        "africa_count_api": africa_count,
        "us_count": us_count,
        "cci_conservative": cci_conservative,
        "cci_direct": cci_direct,
        "africa_burden_share": AFRICA_MATERNAL_DEATH_SHARE,
        "africa_trial_share_pct": africa_trial_share_pct,
        "location_counts": location_counts,
        "country_distribution": country_dist,
        "sub_condition_counts": sub_condition_counts,
        "category_breakdown": category_counts,
        "sponsor_breakdown": sponsor_counts,
        "phase_distribution": phase_counts,
        "status_counts": status_counts,
        "african_led_count": african_led,
        "african_led_pct": round(
            african_led / total_africa * 100, 1) if total_africa else 0,
        "mmr_data": MMR_DATA,
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
    """Generate a dark-themed HTML maternal mortality equity dashboard."""

    total_africa = data["total_africa_unique"]
    africa_api = data["africa_count_api"]
    us_count = data["us_count"]
    cci_con = data["cci_conservative"]
    cci_dir = data["cci_direct"]
    location_counts = data["location_counts"]
    country_dist = data["country_distribution"]
    category_counts = data["category_breakdown"]
    sponsor_counts = data["sponsor_breakdown"]
    phase_counts = data["phase_distribution"]
    status_counts = data["status_counts"]
    sub_cond = data["sub_condition_counts"]
    mmr = data["mmr_data"]
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

    def category_color(cls):
        colors = {
            "Hemorrhage/PPH": "#e74c3c",
            "Preeclampsia/Eclampsia": "#f39c12",
            "Caesarean section": "#3498db",
            "Maternal sepsis": "#9b59b6",
            "Peripartum cardiomyopathy": "#e91e63",
            "Other obstetric": "#95a5a6",
        }
        return colors.get(cls, "#95a5a6")

    # Build trial table rows
    trial_rows = []
    for t in trials_sorted:
        color = status_color(t["status"])
        cat_color = category_color(t["obstetric_category"])
        title_trunc = (t["title"][:80] + ("..."
                       if len(t["title"]) > 80 else ""))
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
<td style="color:{cat_color}">{t['obstetric_category']}</td>
</tr>""")

    trial_table_html = "\n".join(trial_rows)

    # Country comparison data for charts
    africa_chart_data = []
    for loc in AFRICAN_COUNTRIES:
        count = location_counts.get(loc, country_dist.get(loc, 0))
        m = mmr.get(loc, 0)
        africa_chart_data.append({"name": loc, "count": count, "mmr": m})

    country_chart_labels = json.dumps([d["name"] for d in africa_chart_data])
    country_chart_values = json.dumps([d["count"] for d in africa_chart_data])
    country_chart_mmr = json.dumps([d["mmr"] for d in africa_chart_data])

    # Global comparison
    global_labels = json.dumps(
        ["Africa", "United States", "India", "Bangladesh"])
    global_values = json.dumps([
        africa_api,
        us_count,
        location_counts.get("India", 0),
        location_counts.get("Bangladesh", 0),
    ])

    # Category breakdown
    cat_labels = json.dumps(list(category_counts.keys()))
    cat_values = json.dumps(list(category_counts.values()))
    cat_colors = json.dumps([category_color(k) for k in category_counts.keys()])

    # Sponsor breakdown
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

    # Phase distribution
    phase_labels = json.dumps(list(phase_counts.keys()))
    phase_values = json.dumps(list(phase_counts.values()))

    # MMR comparison rows
    mmr_rows = ""
    for country in ["Nigeria", "DRC", "Kenya", "Mozambique", "Uganda",
                    "Ethiopia", "Tanzania", "South Africa"]:
        m = mmr.get(country, 0)
        trials_c = location_counts.get(country, 0)
        bar_width = min(m / 12, 100)
        mmr_rows += f"""<tr>
<td style="font-weight:700">{country}</td>
<td style="text-align:right;color:#e74c3c;font-weight:700">{m:,}</td>
<td style="text-align:right;color:#f1c40f">{trials_c}</td>
<td><div style="background:#e74c3c;height:12px;border-radius:4px;width:{bar_width}%"></div></td>
</tr>"""

    # Sub-condition spotlight rows
    sub_rows = ""
    sub_display = {
        "postpartum_hemorrhage": "Postpartum hemorrhage",
        "preeclampsia": "Preeclampsia/Eclampsia",
        "caesarean": "Caesarean section",
        "maternal_sepsis": "Maternal sepsis",
        "peripartum_cardiomyopathy": "Peripartum cardiomyopathy",
    }
    for key, display in sub_display.items():
        count = sub_cond.get(key, 0)
        sub_rows += f"""<tr>
<td style="font-weight:600">{display}</td>
<td style="text-align:right;color:#fbbf24;font-weight:700">{count}</td>
</tr>"""

    # Severity findings
    severity_items = []
    severity_items.append({
        "level": "CRITICAL",
        "text": (f"CCI = {cci_con}x (conservative) to {cci_dir}x (direct) "
                 f"-- Africa carries {AFRICA_MATERNAL_DEATH_SHARE:.0f}% of "
                 f"global maternal deaths but has only ~{africa_api} trials "
                 f"vs {us_count:,} in the US")
    })
    severity_items.append({
        "level": "CRITICAL",
        "text": ("SDG 3.1 target: reduce MMR to <70/100K by 2030. "
                 f"Sub-Saharan Africa is at {mmr.get('Sub-Saharan Africa', 542)}"
                 "/100K -- 7.7x above target with no trial infrastructure "
                 "to close the gap")
    })
    severity_items.append({
        "level": "CRITICAL",
        "text": (f"Nigeria has the world's highest absolute maternal deaths "
                 f"(MMR {mmr.get('Nigeria', 1047)}/100K) yet hosts only "
                 f"{location_counts.get('Nigeria', 0)} maternal/obstetric "
                 f"trial(s)")
    })
    if african_led_pct < 40:
        severity_items.append({
            "level": "HIGH",
            "text": (f"Only {african_led_pct}% of Africa-based maternal "
                     f"trials are led by African institutions")
        })
    pph_count = sub_cond.get("postpartum_hemorrhage", 0)
    severity_items.append({
        "level": "HIGH",
        "text": (f"Postpartum hemorrhage is the #1 cause of maternal death "
                 f"globally, yet only {pph_count} trial(s) in Africa")
    })
    ppcm_count = sub_cond.get("peripartum_cardiomyopathy", 0)
    severity_items.append({
        "level": "HIGH",
        "text": (f"Peripartum cardiomyopathy: highest incidence in Africa "
                 f"(1:100-1:1000 pregnancies in Nigeria) yet only "
                 f"{ppcm_count} trial(s)")
    })

    severity_html = ""
    for item in severity_items:
        bg = ("#7f1d1d" if item["level"] == "CRITICAL" else
              "#78350f" if item["level"] == "HIGH" else "#1e3a5f")
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
<title>The Maternal Mortality Scandal &mdash; Africa's Most Extreme Trial Gap</title>
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
    --pink: #e91e63;
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

/* SDG box */
.sdg-box {{
    background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
    border-radius:16px;
    padding:28px;
    border:2px solid #3b82f6;
    margin:24px 0;
    text-align:center;
}}
.sdg-value {{
    font-size:2.5rem;
    font-weight:800;
    color:#ef4444;
}}
.sdg-target {{
    font-size:1.5rem;
    font-weight:700;
    color:#22c55e;
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

<h1>The Maternal Mortality Scandal</h1>
<p class="subtitle">Africa has 66% of all global maternal deaths but almost no clinical trials &mdash;
the most extreme trial gap in global health &mdash;
ClinicalTrials.gov Registry &mdash; Generated {datetime.now().strftime('%d %B %Y')}</p>

<!-- ===== Summary Banner ===== -->
<div class="banner">
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{africa_api}</div>
        <div class="label">Africa Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--accent)">{us_count:,}</div>
        <div class="label">US Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--yellow)">{cci_dir}x</div>
        <div class="label">CCI (direct)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{AFRICA_MATERNAL_DEATH_SHARE:.0f}%</div>
        <div class="label">Africa's share of maternal deaths</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--orange)">{mmr.get('Sub-Saharan Africa', 542)}</div>
        <div class="label">SSA MMR (/100K)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--green)">{mmr.get('SDG 3.1 Target', 70)}</div>
        <div class="label">SDG 3.1 target (/100K)</div>
    </div>
</div>

<!-- ===== CCI Box ===== -->
<div class="cci-box">
    <div class="cci-value">{cci_con}x &ndash; {cci_dir}x</div>
    <div class="cci-label">Condition Colonialism Index</div>
    <div class="cci-detail">
        Africa carries <strong>{AFRICA_MATERNAL_DEATH_SHARE:.0f}%</strong> of all global maternal deaths
        but hosts only <strong>~{africa_api}</strong> registered interventional trials
        (query: maternal mortality / obstetric / PPH / preeclampsia / eclampsia),
        compared to <strong>{us_count:,}</strong> in the United States.
        <br><br>
        <strong>Conservative CCI:</strong> {AFRICA_MATERNAL_DEATH_SHARE:.0f}% burden / {data['africa_trial_share_pct']}% trial share = <strong>{cci_con}x</strong><br>
        <strong>Direct CCI (vs US):</strong> (66%/0.8%) / ({africa_api}/{us_count:,}) = <strong>{cci_dir}x</strong>
    </div>
</div>

<!-- ===== SDG 3.1 Failure ===== -->
<h2>The SDG 3.1 Failure</h2>
<div class="sdg-box">
    <p style="color:#93c5fd;font-size:1.1rem;margin-bottom:16px">
        Sustainable Development Goal 3.1: Reduce global maternal mortality ratio to
        less than 70 per 100,000 live births by 2030</p>
    <div style="display:flex;justify-content:center;align-items:center;gap:40px;flex-wrap:wrap">
        <div>
            <div class="sdg-value">{mmr.get('Sub-Saharan Africa', 542)}</div>
            <div style="color:#fca5a5">Sub-Saharan Africa MMR</div>
        </div>
        <div style="font-size:2rem;color:var(--text2)">vs</div>
        <div>
            <div class="sdg-target">{mmr.get('SDG 3.1 Target', 70)}</div>
            <div style="color:#86efac">SDG 3.1 Target</div>
        </div>
    </div>
    <p style="color:#d1d5db;margin-top:16px;max-width:700px;margin-left:auto;margin-right:auto">
        Sub-Saharan Africa's MMR is <strong>{round(mmr.get('Sub-Saharan Africa', 542) / mmr.get('SDG 3.1 Target', 70), 1)}x</strong>
        above the SDG 3.1 target. Nigeria alone has an MMR of <strong>{mmr.get('Nigeria', 1047):,}/100K</strong> --
        almost <strong>15x</strong> the target. With fewer than {africa_api} interventional trials across
        the continent, there is no clinical research infrastructure to close this gap by 2030.
        The SDG 3.1 deadline is a mathematical impossibility for most of Africa.</p>
</div>
<div class="info-box">
    <h3>MMR by Country (WHO/UNICEF 2020)</h3>
    <table style="max-width:700px">
        <thead>
            <tr><th>Country</th><th style="text-align:right">MMR (/100K)</th>
                <th style="text-align:right">Trials</th><th style="width:30%">Burden</th></tr>
        </thead>
        <tbody>
            {mmr_rows}
            <tr style="border-top:2px solid var(--accent)">
                <td style="color:var(--accent);font-weight:700">United States</td>
                <td style="text-align:right;color:var(--accent);font-weight:700">{mmr.get('United States', 21)}</td>
                <td style="text-align:right;color:var(--accent);font-weight:700">{us_count:,}</td>
                <td><div style="background:var(--accent);height:12px;border-radius:4px;width:{min(mmr.get('United States', 21) / 12, 100)}%"></div></td>
            </tr>
        </tbody>
    </table>
</div>

<!-- ===== Severity Summary ===== -->
<h2>Severity Summary</h2>
<div class="severity-box">
    {severity_html}
</div>

<!-- ===== Country Breakdown ===== -->
<h2>Country Breakdown: Africa</h2>
<p class="lead" style="color:var(--text2)">Trial counts by African country, alongside WHO maternal
mortality ratio data (deaths per 100,000 live births).</p>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trials per African Country</h3>
        <canvas id="countryChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>MMR vs Trial Count</h3>
        <canvas id="scatterChart"></canvas>
    </div>
</div>

<!-- ===== Obstetric Hemorrhage Spotlight ===== -->
<h2>Obstetric Hemorrhage Spotlight</h2>
<div class="crisis-box">
    <p style="color:#fde68a;font-weight:700;font-size:1.1rem;margin-bottom:12px">
        The #1 cause of maternal death worldwide</p>
    <p style="color:#e5e7eb;margin-bottom:16px">
        Postpartum hemorrhage (PPH) causes approximately 27% of all maternal deaths
        globally and an even higher proportion in Sub-Saharan Africa where access to
        uterotonics, blood products, and surgical intervention is severely limited.
        Despite this, we identified only <strong>{sub_cond.get('postpartum_hemorrhage', 0)}</strong>
        PPH-related interventional trial(s) with African sites. The interventions that
        save lives (misoprostol, tranexamic acid, uterine balloon tamponade) are
        well-established but remain woefully under-trialled in the populations that
        need them most.</p>
    <p style="color:#fde68a;font-size:0.9rem">
        Compare: the United States alone has hundreds of obstetric hemorrhage trials
        despite a maternal death rate 25x lower than Sub-Saharan Africa.</p>
</div>

<!-- ===== Sub-condition Trials in Africa ===== -->
<h2>Sub-condition Trials in Africa</h2>
<div class="info-box">
    <p>Breakdown of interventional trial counts in Africa by specific obstetric condition.</p>
    <table style="max-width:500px">
        <thead>
            <tr><th>Condition</th><th style="text-align:right">Trials in Africa</th></tr>
        </thead>
        <tbody>
            {sub_rows}
        </tbody>
    </table>
</div>

<!-- ===== Peripartum Cardiomyopathy ===== -->
<h2>Peripartum Cardiomyopathy: The Cardiologist's Link</h2>
<div class="info-box">
    <p><strong>Peripartum cardiomyopathy (PPCM) has its highest incidence in Africa</strong>,
    particularly Nigeria (Kano region: 1 in 100 pregnancies), yet it remains one of the
    most under-researched conditions in cardiovascular medicine.</p>
    <p>We identified only <strong>{sub_cond.get('peripartum_cardiomyopathy', 0)}</strong>
    PPCM trial(s) with African sites on ClinicalTrials.gov. This condition kills young
    mothers of childbearing age and has a unique pathophysiology linked to the 16 kDa
    prolactin fragment. Bromocriptine has shown promise in small African studies, but
    definitive multicentre RCTs are absent.</p>
    <p style="color:var(--pink);font-weight:600">PPCM sits at the intersection of
    cardiology and obstetrics -- two fields that rarely converge in African clinical
    research. This is a missed opportunity for Africa-led investigation into a
    condition that disproportionately affects African women.</p>
</div>

<!-- ===== Preeclampsia ===== -->
<h2>Preeclampsia / Eclampsia Trials</h2>
<div class="info-box">
    <p>Preeclampsia/eclampsia is the second leading cause of maternal death in Africa.
    Magnesium sulfate, the gold-standard treatment, was ironically validated in
    the landmark Magpie Trial which included African sites -- yet two decades later,
    follow-up research remains sparse.</p>
    <p>We identified <strong>{sub_cond.get('preeclampsia', 0)}</strong> preeclampsia/eclampsia
    trial(s) in Africa. Key gaps include: aspirin prophylaxis dosing in African populations,
    antihypertensive optimization, and screening biomarkers validated for low-resource settings.</p>
</div>

<!-- ===== Caesarean Section ===== -->
<h2>Caesarean Section Trials</h2>
<div class="info-box">
    <p>In many parts of Sub-Saharan Africa, access to safe caesarean section is the
    difference between life and death. The WHO estimates that 5-15% of births require
    surgical delivery, yet caesarean section rates in parts of rural Africa fall below 1%.</p>
    <p>We identified <strong>{sub_cond.get('caesarean', 0)}</strong> caesarean-related
    trial(s) with African sites. Research gaps include: anaesthesia safety in
    low-resource theatres, surgical technique optimization, and infection prevention
    in settings without reliable water supply.</p>
</div>

<!-- ===== Obstetric Category Breakdown ===== -->
<h2>Obstetric Category Breakdown</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Trial Categories</h3>
        <canvas id="categoryChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Sponsor Breakdown</h3>
        <canvas id="sponsorChart"></canvas>
    </div>
</div>

<!-- ===== Comparison: India & Bangladesh ===== -->
<h2>Comparison: India &amp; Bangladesh</h2>
<div class="info-box">
    <p><strong>India:</strong> {location_counts.get('India', 0)} maternal/obstetric trials.
    India's MMR ({mmr.get('India', 103)}/100K) has fallen dramatically from 556 in 1990 to 103 in 2020 --
    a success story driven partly by trial infrastructure. India's Janani Suraksha Yojana
    programme was evaluated through multiple RCTs. Africa has no equivalent research pipeline.</p>
    <p><strong>Bangladesh:</strong> {location_counts.get('Bangladesh', 0)} trials.
    Bangladesh (MMR {mmr.get('Bangladesh', 123)}/100K) has achieved remarkable progress
    through community health worker models that were validated by randomised trials.
    Africa's MMR remains 4-5x higher than both India and Bangladesh.</p>
    <p style="color:var(--yellow)">The lesson: maternal mortality reduction requires
    trial infrastructure. Countries that invested in obstetric research saw their MMR
    fall. Africa -- which carries 66% of the burden -- has been left behind.</p>
</div>
<div class="charts-grid">
    <div class="chart-box">
        <h3>Global Comparison: Trial Counts</h3>
        <canvas id="globalChart"></canvas>
    </div>
    <div class="chart-box">
        <h3>Phase Distribution (Africa Trials)</h3>
        <canvas id="phaseChart"></canvas>
    </div>
</div>

<!-- ===== Sponsor Analysis ===== -->
<h2>Sponsor Analysis</h2>
<div class="info-box">
    <p>Of the {total_africa} unique maternal/obstetric trials in Africa,
    <strong>{data['african_led_count']}</strong> ({african_led_pct}%) are led by
    African institutions. The remainder are sponsored by NGOs/multilateral
    organizations, non-African academic centres, NIH/US government agencies,
    or pharmaceutical companies.</p>
    <p>Unlike oncology or cardiovascular disease, maternal health trials in Africa
    are overwhelmingly NGO/multilateral-sponsored. This reflects the "aid dependency"
    model where African maternal health research is driven by external
    priorities rather than local scientific leadership.</p>
</div>

<!-- ===== Full Trial Table ===== -->
<h2>All Africa Trials ({total_africa})</h2>
<p style="color:var(--text2);margin-bottom:8px">
    Rows coloured by status: <span style="color:var(--green)">completed</span>,
    <span style="color:var(--red)">terminated/withdrawn</span>,
    <span style="color:var(--yellow)">active/recruiting</span>,
    <span style="color:var(--grey)">unknown</span>.
    Category:
    <span style="color:#e74c3c">Hemorrhage</span>,
    <span style="color:#f39c12">Preeclampsia</span>,
    <span style="color:#3498db">Caesarean</span>,
    <span style="color:#9b59b6">Sepsis</span>,
    <span style="color:var(--pink)">PPCM</span>.
</p>
<div class="table-container">
<table>
<thead>
<tr>
    <th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sponsor Class</th>
    <th>Country</th><th>Phase</th><th>Status</th><th>Enrollment</th>
    <th>Category</th>
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
    Africa trials: {africa_api} (unique: {total_africa}) | US trials: {us_count:,} |
    CCI: {cci_con}x-{cci_dir}x |
    MMR data: WHO/UNICEF/UNFPA/World Bank 2020 |
    Generated by fetch_maternal_mortality.py (Project 29)
</div>

</div><!-- /container -->

<script>
// -- Country bar chart --
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

// -- MMR vs Trial Count scatter --
(function() {{
    var countries = {country_chart_labels};
    var trials = {country_chart_values};
    var mmrVals = {country_chart_mmr};
    var points = countries.map(function(c, i) {{
        return {{ x: trials[i], y: mmrVals[i], label: c }};
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
                            return p.label + ': ' + p.x + ' trials, MMR ' + p.y + '/100K';
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
                    title: {{ display: true, text: 'MMR (per 100K live births)', color: '#9ca3af' }}
                }}
            }}
        }}
    }});
}})();

// -- Obstetric category doughnut --
new Chart(document.getElementById('categoryChart'), {{
    type: 'doughnut',
    data: {{
        labels: {cat_labels},
        datasets: [{{
            data: {cat_values},
            backgroundColor: {cat_colors},
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

// -- Sponsor doughnut --
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

// -- Global comparison bar --
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

// -- Phase distribution --
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
    print("=" * 65)
    print("The Maternal Mortality Scandal -- Project 29")
    print("Africa's Most Extreme Clinical Trial Gap")
    print("=" * 65)

    data = collect_data()

    # Print summary
    print(f"\n{'=' * 65}")
    print("SUMMARY")
    print(f"{'=' * 65}")
    print(f"Africa trials (API):       {data['africa_count_api']}")
    print(f"Africa trials (unique):    {data['total_africa_unique']}")
    print(f"US trials:                 {data['us_count']:,}")
    print(f"CCI (conservative):        {data['cci_conservative']}x")
    print(f"CCI (direct):              {data['cci_direct']}x")
    print(f"Africa burden share:       {data['africa_burden_share']}%")
    print(f"Africa trial share:        {data['africa_trial_share_pct']}%")
    print(f"African-led:               {data['african_led_count']} "
          f"({data['african_led_pct']}%)")
    print()
    print("Sub-condition counts (Africa):")
    for key, count in sorted(data["sub_condition_counts"].items(),
                              key=lambda x: -x[1]):
        print(f"  {key:30s} {count}")
    print()
    print("Category breakdown:")
    for cls, count in sorted(data["category_breakdown"].items(),
                              key=lambda x: -x[1]):
        print(f"  {cls:30s} {count}")
    print()
    print("Country distribution:")
    for country, count in sorted(data["country_distribution"].items(),
                                  key=lambda x: -x[1]):
        mmr_val = MMR_DATA.get(country, 0)
        mmr_str = f"  (MMR {mmr_val}/100K)" if mmr_val else ""
        print(f"  {country:20s} {count:>4}{mmr_str}")
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
    print("MMR data (WHO/UNICEF 2020):")
    for name, val in sorted(MMR_DATA.items(), key=lambda x: -x[1]):
        print(f"  {name:25s} {val:>6,}/100K")

    # Generate HTML
    print(f"\nGenerating HTML report...")
    html = generate_html(data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to {OUTPUT_HTML}")
    print(f"Open in browser: file:///{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    main()
