"""
Africa's Forgotten Diseases -- Registry Analysis
====================================================
Queries ClinicalTrials.gov API v2 for 15 diseases that predominantly
affect Africa yet have near-zero clinical trial activity. Computes a
Neglect Index for each disease and generates an HTML equity dashboard.

Usage:
    python fetch_forgotten_diseases.py

Output:
    data/forgotten_diseases_data.json  -- cached data (24h validity)
    forgotten-diseases.html            -- interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
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
CACHE_FILE = DATA_DIR / "forgotten_diseases_data.json"
OUTPUT_HTML = Path(__file__).parent / "forgotten-diseases.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# -- Disease definitions ---------------------------------------------------
# Each disease: (display_name, search_terms, africa_burden_pct, who_ntd,
#                annual_africa_deaths, notes)
DISEASES = [
    {
        "name": "Burkitt lymphoma",
        "search_terms": ["Burkitt lymphoma", "Burkitt's lymphoma"],
        "africa_burden_pct": 85.0,
        "who_ntd": False,
        "annual_africa_deaths": 15000,
        "notes": "Endemic form ~85% in sub-Saharan Africa; linked to malaria/EBV co-infection",
    },
    {
        "name": "Kaposi sarcoma",
        "search_terms": ["Kaposi sarcoma", "Kaposi's sarcoma"],
        "africa_burden_pct": 80.0,
        "who_ntd": False,
        "annual_africa_deaths": 35000,
        "notes": "Most common cancer in parts of East/Southern Africa; HIV-associated",
    },
    {
        "name": "Human African trypanosomiasis",
        "search_terms": ["trypanosomiasis", "sleeping sickness", "African trypanosomiasis"],
        "africa_burden_pct": 100.0,
        "who_ntd": True,
        "annual_africa_deaths": 3500,
        "notes": "Exclusively African; transmitted by tsetse fly; 100% of cases in SSA",
    },
    {
        "name": "Schistosomiasis",
        "search_terms": ["schistosomiasis", "bilharzia"],
        "africa_burden_pct": 90.0,
        "who_ntd": True,
        "annual_africa_deaths": 200000,
        "notes": "90% of those requiring treatment live in Africa; 200K deaths/year",
    },
    {
        "name": "Onchocerciasis",
        "search_terms": ["onchocerciasis", "river blindness"],
        "africa_burden_pct": 99.0,
        "who_ntd": True,
        "annual_africa_deaths": 1200,
        "notes": "99% of cases in Africa; 18M infected; causes blindness",
    },
    {
        "name": "Trachoma",
        "search_terms": ["trachoma"],
        "africa_burden_pct": 85.0,
        "who_ntd": True,
        "annual_africa_deaths": 500,
        "notes": "Leading infectious cause of blindness; 85% burden in Africa",
    },
    {
        "name": "Lymphatic filariasis",
        "search_terms": ["lymphatic filariasis", "elephantiasis"],
        "africa_burden_pct": 65.0,
        "who_ntd": True,
        "annual_africa_deaths": 0,
        "notes": "859M people at risk; Africa has ~65% of global cases",
    },
    {
        "name": "Leishmaniasis",
        "search_terms": ["leishmaniasis"],
        "africa_burden_pct": 35.0,
        "who_ntd": True,
        "annual_africa_deaths": 20000,
        "notes": "Visceral form (kala-azar) kills ~20K/year in East Africa (Sudan, Ethiopia, Kenya)",
    },
    {
        "name": "Rabies",
        "search_terms": ["rabies"],
        "africa_burden_pct": 36.0,
        "who_ntd": True,
        "annual_africa_deaths": 21000,
        "notes": "Africa has ~36% of global rabies deaths; 21K/year; mostly children",
    },
    {
        "name": "Podoconiosis",
        "search_terms": ["podoconiosis"],
        "africa_burden_pct": 95.0,
        "who_ntd": True,
        "annual_africa_deaths": 0,
        "notes": "Non-filarial elephantiasis; ~95% of 4M cases in African highlands",
    },
    {
        "name": "Mycetoma",
        "search_terms": ["mycetoma"],
        "africa_burden_pct": 70.0,
        "who_ntd": True,
        "annual_africa_deaths": 1500,
        "notes": "Chronic fungal/bacterial infection; Sudan alone has ~70% of world cases",
    },
    {
        "name": "Noma",
        "search_terms": ["noma", "cancrum oris"],
        "africa_burden_pct": 90.0,
        "who_ntd": True,
        "annual_africa_deaths": 90000,
        "notes": "Gangrenous stomatitis; 90% African; 90% mortality without treatment; destroys faces of malnourished children",
    },
    {
        "name": "Rheumatic heart disease",
        "search_terms": ["rheumatic heart disease"],
        "africa_burden_pct": 60.0,
        "who_ntd": False,
        "annual_africa_deaths": 240000,
        "notes": "Kills 400K/year globally, ~60% African children; preventable with penicillin",
    },
    {
        "name": "Snakebite envenoming",
        "search_terms": ["snakebite", "snake envenoming", "snake bite envenomation"],
        "africa_burden_pct": 50.0,
        "who_ntd": True,
        "annual_africa_deaths": 138000,
        "notes": "WHO NTD since 2017; 138K deaths/year in SSA; antivenom supply crisis",
    },
    {
        "name": "Buruli ulcer",
        "search_terms": ["Buruli ulcer", "Mycobacterium ulcerans"],
        "africa_burden_pct": 95.0,
        "who_ntd": True,
        "annual_africa_deaths": 500,
        "notes": "95% of cases in West/Central Africa; causes severe skin destruction",
    },
]

# African countries for location queries
AFRICAN_COUNTRIES = [
    "Nigeria", "Kenya", "Uganda", "Ghana", "Tanzania", "Egypt",
    "South Africa", "Cameroon", "Ethiopia", "Sudan", "Senegal",
    "Democratic Republic of the Congo", "Mozambique", "Burkina Faso",
    "Mali", "Niger", "Rwanda",
]

# Sponsor classification keywords
AFRICAN_KEYWORDS = [
    "nigeria", "lagos", "ibadan", "makerere", "uganda", "kenya",
    "nairobi", "ghana", "accra", "tanzania", "muhimbili", "cameroon",
    "egypt", "cairo", "ain shams", "south africa", "cape town",
    "witwatersrand", "ethiopia", "addis ababa", "sudan", "khartoum",
    "senegal", "dakar", "mozambique", "burkina", "mali", "niger",
    "rwanda", "kigali",
]

PHARMA_KEYWORDS = [
    "pfizer", "novartis", "roche", "astrazeneca", "sanofi", "gsk",
    "glaxosmithkline", "merck", "johnson", "bayer", "takeda",
    "boehringer", "lilly", "gilead", "abbvie", "amgen", "regeneron",
]

NIH_KEYWORDS = [
    "nih", "niaid", "cdc", "national institutes of health",
    "national heart, lung", "nhlbi", "wellcome", "gates foundation",
    "bill & melinda", "unitaid",
]

US_KEYWORDS = ["United States"]


# -- API helpers -----------------------------------------------------------
def search_trials_count(condition_terms, location=None, max_retries=3):
    """Query CT.gov API v2 and return total count for a condition."""
    params = {
        "format": "json",
        "pageSize": 1,
        "countTotal": "true",
    }

    filters = ["AREA[StudyType]INTERVENTIONAL"]
    params["filter.advanced"] = " AND ".join(filters)

    # Build condition query from terms
    if len(condition_terms) == 1:
        params["query.cond"] = condition_terms[0]
    else:
        params["query.cond"] = " OR ".join(condition_terms)

    if location:
        params["query.locn"] = location

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("totalCount", 0)
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}) "
                  f"for {condition_terms[0]}, location={location}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return 0


def fetch_trials_detail(condition_terms, location=None, page_size=200,
                        max_retries=3):
    """Fetch full trial records for a condition+location query."""
    all_studies = []
    page_token = None

    while True:
        params = {
            "format": "json",
            "pageSize": page_size,
            "countTotal": "true",
        }

        filters = ["AREA[StudyType]INTERVENTIONAL"]
        params["filter.advanced"] = " AND ".join(filters)

        if len(condition_terms) == 1:
            params["query.cond"] = condition_terms[0]
        else:
            params["query.cond"] = " OR ".join(condition_terms)

        if location:
            params["query.locn"] = location
        if page_token:
            params["pageToken"] = page_token

        for attempt in range(max_retries):
            try:
                resp = requests.get(BASE_URL, params=params, timeout=30)
                resp.raise_for_status()
                result = resp.json()
                break
            except requests.RequestException as e:
                print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                result = {"totalCount": 0, "studies": []}

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


def extract_status(study):
    try:
        return study["protocolSection"]["statusModule"]["overallStatus"]
    except (KeyError, TypeError):
        return "UNKNOWN"


def extract_sponsor(study):
    try:
        return study["protocolSection"]["sponsorCollaboratorsModule"][
            "leadSponsor"]["name"]
    except (KeyError, TypeError):
        return "Unknown"


def extract_phases(study):
    try:
        return study["protocolSection"]["designModule"].get("phases", [])
    except (KeyError, TypeError):
        return []


def extract_enrollment(study):
    try:
        return study["protocolSection"]["designModule"]["enrollmentInfo"].get(
            "count", 0)
    except (KeyError, TypeError):
        return 0


def classify_sponsor(sponsor_name):
    """Classify sponsor origin."""
    lower = sponsor_name.lower()
    for kw in AFRICAN_KEYWORDS:
        if kw in lower:
            return "African-led"
    for kw in PHARMA_KEYWORDS:
        if kw in lower:
            return "Pharma"
    for kw in NIH_KEYWORDS:
        if kw in lower:
            return "Funder/NGO"
    return "Other"


# -- Main data collection --------------------------------------------------
def collect_data():
    """Fetch trial counts for each disease across Africa, globally, and US."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Fetching forgotten disease trial data from ClinicalTrials.gov API v2...")
    print(f"Querying {len(DISEASES)} diseases x 3 scopes (Africa, Global, US)...\n")

    disease_results = []

    for idx, disease in enumerate(DISEASES):
        name = disease["name"]
        terms = disease["search_terms"]
        print(f"[{idx + 1}/{len(DISEASES)}] {name}")

        # Global count (no location filter)
        global_count = search_trials_count(terms)
        print(f"  Global: {global_count}")
        time.sleep(RATE_LIMIT_DELAY)

        # Africa count (query each African country)
        africa_nct_ids = set()
        for country in AFRICAN_COUNTRIES:
            studies = fetch_trials_detail(terms, location=country)
            for s in studies:
                nct_id = extract_nct_id(s)
                if nct_id:
                    africa_nct_ids.add(nct_id)
            time.sleep(RATE_LIMIT_DELAY)

        # Also query "Africa" as a general term
        studies = fetch_trials_detail(terms, location="Africa")
        for s in studies:
            nct_id = extract_nct_id(s)
            if nct_id:
                africa_nct_ids.add(nct_id)
        time.sleep(RATE_LIMIT_DELAY)

        africa_count = len(africa_nct_ids)
        print(f"  Africa: {africa_count}")

        # US count for comparison
        us_count = search_trials_count(terms, location="United States")
        print(f"  US:     {us_count}")
        time.sleep(RATE_LIMIT_DELAY)

        # Fetch Africa trial details for sponsor analysis
        africa_trials = []
        africa_detail_ids = set()
        for country in AFRICAN_COUNTRIES[:5]:  # Top 5 countries for detail
            studies = fetch_trials_detail(terms, location=country)
            for s in studies:
                nct_id = extract_nct_id(s)
                if nct_id and nct_id not in africa_detail_ids:
                    africa_detail_ids.add(nct_id)
                    sponsor = extract_sponsor(s)
                    africa_trials.append({
                        "nct_id": nct_id,
                        "title": extract_title(s),
                        "status": extract_status(s),
                        "sponsor": sponsor,
                        "sponsor_class": classify_sponsor(sponsor),
                        "phases": extract_phases(s),
                        "enrollment": extract_enrollment(s),
                    })
            time.sleep(RATE_LIMIT_DELAY)

        # Compute Neglect Index
        africa_burden_pct = disease["africa_burden_pct"]
        if global_count > 0 and africa_count > 0:
            africa_trial_share = (africa_count / global_count) * 100
            neglect_index = round(africa_burden_pct / africa_trial_share, 1)
        elif global_count > 0 and africa_count == 0:
            neglect_index = float("inf")
            africa_trial_share = 0.0
        else:
            neglect_index = None
            africa_trial_share = 0.0

        # Sponsor breakdown for Africa trials
        sponsor_counts = {}
        for t in africa_trials:
            cls = t["sponsor_class"]
            sponsor_counts[cls] = sponsor_counts.get(cls, 0) + 1

        result = {
            "name": name,
            "search_terms": terms,
            "africa_burden_pct": africa_burden_pct,
            "who_ntd": disease["who_ntd"],
            "annual_africa_deaths": disease["annual_africa_deaths"],
            "notes": disease["notes"],
            "global_count": global_count,
            "africa_count": africa_count,
            "us_count": us_count,
            "africa_trial_share": round(africa_trial_share, 2),
            "neglect_index": neglect_index,
            "sponsor_breakdown": sponsor_counts,
            "africa_trials": africa_trials,
        }
        disease_results.append(result)
        print(f"  Neglect Index: {neglect_index}")
        print()

    # Summary statistics
    total_africa_trials = sum(d["africa_count"] for d in disease_results)
    total_global_trials = sum(d["global_count"] for d in disease_results)
    total_us_trials = sum(d["us_count"] for d in disease_results)
    zero_africa = [d for d in disease_results if d["africa_count"] == 0]
    who_ntd_count = sum(1 for d in disease_results if d["who_ntd"])

    # Sort by neglect index descending (inf first)
    disease_results_sorted = sorted(
        disease_results,
        key=lambda d: (
            0 if d["neglect_index"] == float("inf") else 1,
            -(d["neglect_index"] if d["neglect_index"] is not None
              and d["neglect_index"] != float("inf") else 0)
        )
    )

    data = {
        "fetch_date": datetime.now().isoformat(),
        "diseases": disease_results_sorted,
        "summary": {
            "total_diseases": len(DISEASES),
            "total_africa_trials": total_africa_trials,
            "total_global_trials": total_global_trials,
            "total_us_trials": total_us_trials,
            "zero_africa_count": len(zero_africa),
            "zero_africa_names": [d["name"] for d in zero_africa],
            "who_ntd_count": who_ntd_count,
            "mean_neglect_index": round(
                sum(d["neglect_index"] for d in disease_results
                    if d["neglect_index"] is not None
                    and d["neglect_index"] != float("inf"))
                / max(1, sum(1 for d in disease_results
                             if d["neglect_index"] is not None
                             and d["neglect_index"] != float("inf"))),
                1
            ),
        },
    }

    # Cache -- convert inf to string for JSON
    def sanitize_for_json(obj):
        if isinstance(obj, float) and obj == float("inf"):
            return "Infinity"
        if isinstance(obj, dict):
            return {k: sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize_for_json(v) for v in obj]
        return obj

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(data), f, indent=2, ensure_ascii=False)
    print(f"Cached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator -------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML dashboard for forgotten diseases analysis."""

    diseases = data["diseases"]
    summary = data["summary"]
    fetch_date = data["fetch_date"][:10]

    # Restore inf from "Infinity" strings
    for d in diseases:
        if d.get("neglect_index") == "Infinity":
            d["neglect_index"] = float("inf")

    # Separate zero-trial and non-zero diseases
    zero_diseases = [d for d in diseases if d["africa_count"] == 0]
    nonzero_diseases = [d for d in diseases if d["africa_count"] > 0]

    # Find RHD and snakebite for spotlights
    rhd = next((d for d in diseases if "Rheumatic" in d["name"]), None)
    snakebite = next((d for d in diseases if "Snakebite" in d["name"]), None)
    noma = next((d for d in diseases if "Noma" in d["name"]), None)

    # Build main disease table
    disease_rows = []
    for d in diseases:
        ni = d["neglect_index"]
        if ni == float("inf"):
            ni_str = "INFINITE"
            ni_color = "#ef4444"
        elif ni is not None:
            ni_str = f"{ni:.1f}x"
            if ni > 20:
                ni_color = "#ef4444"
            elif ni > 10:
                ni_color = "#f59e0b"
            elif ni > 5:
                ni_color = "#eab308"
            else:
                ni_color = "#22c55e"
        else:
            ni_str = "N/A"
            ni_color = "#6b7280"

        who_badge = ('<span style="background:#7c3aed;color:#e9d5ff;'
                     'padding:2px 8px;border-radius:4px;font-size:0.75rem">'
                     'WHO NTD</span>') if d["who_ntd"] else ""

        deaths_str = f'{d["annual_africa_deaths"]:,}' if d["annual_africa_deaths"] > 0 else "Morbidity-dominant"

        disease_rows.append(f"""<tr>
<td style="font-weight:600">{d['name']} {who_badge}</td>
<td style="text-align:right">{d['africa_burden_pct']:.0f}%</td>
<td style="text-align:right;font-weight:700;color:{'#ef4444' if d['africa_count'] == 0 else '#60a5fa'}">{d['africa_count']}</td>
<td style="text-align:right">{d['global_count']}</td>
<td style="text-align:right;color:#94a3b8">{d['us_count']}</td>
<td style="text-align:right">{d['africa_trial_share']:.1f}%</td>
<td style="text-align:right;font-weight:700;color:{ni_color}">{ni_str}</td>
<td style="text-align:right">{deaths_str}</td>
<td style="font-size:0.75rem;color:#9ca3af;max-width:200px">{d['notes']}</td>
</tr>""")

    disease_table_html = "\n".join(disease_rows)

    # Zero-trial disease cards
    zero_cards = []
    for d in zero_diseases:
        zero_cards.append(f"""<div style="background:#1c1917;border:2px solid #ef4444;
            border-radius:12px;padding:20px;text-align:center">
            <div style="font-size:3rem;font-weight:900;color:#ef4444">0</div>
            <div style="font-size:1.1rem;font-weight:700;color:white;margin:8px 0">{d['name']}</div>
            <div style="font-size:0.85rem;color:#fca5a5">{d['africa_burden_pct']:.0f}% of global burden in Africa</div>
            <div style="font-size:0.8rem;color:#9ca3af;margin-top:8px">{d['notes']}</div>
            <div style="font-size:0.85rem;color:#f59e0b;margin-top:8px">
                Global trials: {d['global_count']} | US trials: {d['us_count']}</div>
        </div>""")
    zero_cards_html = "\n".join(zero_cards) if zero_cards else ""

    # Sponsor aggregate across all diseases
    all_sponsor_counts = {}
    for d in diseases:
        for cls, count in d.get("sponsor_breakdown", {}).items():
            all_sponsor_counts[cls] = all_sponsor_counts.get(cls, 0) + count

    sponsor_labels = json.dumps(list(all_sponsor_counts.keys()))
    sponsor_values = json.dumps(list(all_sponsor_counts.values()))
    sponsor_colors = json.dumps([
        "#22c55e" if k == "African-led"
        else "#ef4444" if k == "Pharma"
        else "#3b82f6" if k == "Funder/NGO"
        else "#6b7280"
        for k in all_sponsor_counts.keys()
    ])

    # Chart data: Neglect Index bar chart
    chart_names = []
    chart_ni_values = []
    chart_ni_colors = []
    for d in diseases:
        ni = d["neglect_index"]
        if ni is not None and ni != float("inf"):
            chart_names.append(d["name"])
            chart_ni_values.append(ni)
            chart_ni_colors.append(
                "#ef4444" if ni > 20
                else "#f59e0b" if ni > 10
                else "#eab308" if ni > 5
                else "#22c55e"
            )
    ni_labels = json.dumps(chart_names)
    ni_values = json.dumps(chart_ni_values)
    ni_colors = json.dumps(chart_ni_colors)

    # Africa vs US comparison chart
    compare_names = json.dumps([d["name"] for d in diseases])
    compare_africa = json.dumps([d["africa_count"] for d in diseases])
    compare_us = json.dumps([d["us_count"] for d in diseases])

    # Build RHD spotlight
    rhd_html = ""
    if rhd:
        rhd_html = f"""
        <div style="background:linear-gradient(135deg,#1e1b4b,#312e81);border:2px solid #818cf8;
            border-radius:16px;padding:32px;margin:24px 0">
            <h3 style="color:#a5b4fc;font-size:1.4rem;margin-bottom:16px">
                Rheumatic Heart Disease: The Silent Killer of African Children</h3>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:20px">
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#fbbf24">400,000</div>
                    <div style="color:#c4b5fd">Deaths per year globally</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#ef4444">{rhd['africa_burden_pct']:.0f}%</div>
                    <div style="color:#c4b5fd">Burden in Africa</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#60a5fa">{rhd['africa_count']}</div>
                    <div style="color:#c4b5fd">Africa trials</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#94a3b8">{rhd['us_count']}</div>
                    <div style="color:#c4b5fd">US trials</div>
                </div>
            </div>
            <p style="color:#e0e7ff;line-height:1.8">
                Rheumatic heart disease is entirely preventable with penicillin prophylaxis costing
                pennies per dose. Yet it kills an estimated 240,000 Africans each year, predominantly
                children and young adults. A disease eradicated from wealthy nations decades ago
                continues to destroy lives because the clinical trial infrastructure to optimize
                African treatment protocols barely exists. The contrast is devastating: a condition
                that costs cents to prevent receives almost no research investment in the continent
                where it kills most.</p>
        </div>"""

    # Build snakebite spotlight
    snakebite_html = ""
    if snakebite:
        snakebite_html = f"""
        <div style="background:linear-gradient(135deg,#1a2e05,#365314);border:2px solid #84cc16;
            border-radius:16px;padding:32px;margin:24px 0">
            <h3 style="color:#bef264;font-size:1.4rem;margin-bottom:16px">
                Snakebite Envenoming: WHO NTD Since 2017, Still Invisible</h3>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:20px">
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#fbbf24">138,000</div>
                    <div style="color:#d9f99d">Deaths/year in Africa</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#ef4444">400,000</div>
                    <div style="color:#d9f99d">Amputations & disabilities/year</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#60a5fa">{snakebite['africa_count']}</div>
                    <div style="color:#d9f99d">Africa trials</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#94a3b8">{snakebite['us_count']}</div>
                    <div style="color:#d9f99d">US trials</div>
                </div>
            </div>
            <p style="color:#ecfccb;line-height:1.8">
                The WHO recognized snakebite envenoming as a neglected tropical disease in 2017.
                Across sub-Saharan Africa, an estimated 138,000 people die from snakebite every year,
                with another 400,000 suffering permanent disability including amputations.
                Antivenom production has collapsed: effective polyvalent antivenoms cost $100-500 per
                treatment in countries where monthly income averages $50. Meanwhile, the pipeline of
                clinical trials testing new antivenoms, dosing strategies, or adjunct treatments in
                Africa remains vanishingly thin.</p>
        </div>"""

    # Build noma spotlight
    noma_html = ""
    if noma:
        noma_html = f"""
        <div style="background:linear-gradient(135deg,#1c0a00,#431407);border:2px solid #f97316;
            border-radius:16px;padding:32px;margin:24px 0">
            <h3 style="color:#fdba74;font-size:1.4rem;margin-bottom:16px">
                Noma (Cancrum Oris): The Disease That Destroys Faces</h3>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:20px">
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#fbbf24">90%</div>
                    <div style="color:#fed7aa">Case fatality without treatment</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#ef4444">90%</div>
                    <div style="color:#fed7aa">Cases in Africa</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#60a5fa">{noma['africa_count']}</div>
                    <div style="color:#fed7aa">Africa trials</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:2.5rem;font-weight:900;color:#94a3b8">{noma['global_count']}</div>
                    <div style="color:#fed7aa">Global trials</div>
                </div>
            </div>
            <p style="color:#fff7ed;line-height:1.8">
                Noma is a gangrenous infection that destroys the face, primarily affecting malnourished
                children under age five in sub-Saharan Africa. It has a 90% mortality rate when
                untreated. Survivors face severe disfigurement requiring reconstructive surgery
                that is unavailable in most African countries. Noma was added to the WHO NTD list
                in 2023, yet the clinical trial count for this disease in Africa tells its own story
                of abandonment. An estimated 140,000 new cases occur annually, overwhelmingly in the
                Sahel region.</p>
        </div>"""

    # HIC rare disease comparison
    hic_comparison_html = f"""
    <div style="background:var(--bg2);border-radius:16px;padding:32px;margin:24px 0;
        border:1px solid var(--bg3)">
        <h3 style="color:#f472b6;font-size:1.3rem;margin-bottom:20px">
            The Comparison That Shames: HIC Rare Diseases vs Africa's Forgotten Diseases</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
            <div style="background:#1e1b4b;border-radius:12px;padding:24px;border:1px solid #4338ca">
                <h4 style="color:#a5b4fc;margin-bottom:12px">High-Income Country Rare Diseases</h4>
                <div style="color:#e0e7ff;line-height:1.8">
                    <div>Orphan Drug Act (1983): 600+ drugs approved</div>
                    <div>EU Orphan Regulation: 200+ designations</div>
                    <div>Cystic fibrosis (70K patients): <strong>200+ trials</strong></div>
                    <div>Huntington disease (30K patients): <strong>150+ trials</strong></div>
                    <div>Duchenne MD (20K patients): <strong>100+ trials</strong></div>
                    <div style="margin-top:12px;color:#fbbf24;font-weight:700">
                        Rare disease R&D: ~$8B/year globally</div>
                </div>
            </div>
            <div style="background:#1c0a00;border-radius:12px;padding:24px;border:1px solid #9a3412">
                <h4 style="color:#fdba74;margin-bottom:12px">Africa's Forgotten Diseases</h4>
                <div style="color:#fff7ed;line-height:1.8">
                    <div>Schistosomiasis (240M at risk): <strong>{next((d['africa_count'] for d in diseases if 'Schisto' in d['name']), '?')} Africa trials</strong></div>
                    <div>RHD (240K deaths/year): <strong>{rhd['africa_count'] if rhd else '?'} Africa trials</strong></div>
                    <div>Snakebite (138K deaths/year): <strong>{snakebite['africa_count'] if snakebite else '?'} Africa trials</strong></div>
                    <div>Noma (140K cases/year): <strong>{noma['africa_count'] if noma else '?'} Africa trials</strong></div>
                    <div>Mycetoma, podoconiosis, Buruli ulcer combined:
                        <strong>{sum(d['africa_count'] for d in diseases if d['name'] in ('Mycetoma','Podoconiosis','Buruli ulcer'))} trials</strong></div>
                    <div style="margin-top:12px;color:#ef4444;font-weight:700">
                        Total Africa trials across all 15 diseases: {summary['total_africa_trials']}</div>
                </div>
            </div>
        </div>
        <p style="color:#d1d5db;margin-top:20px;line-height:1.8">
            A rare disease affecting 30,000 people in wealthy countries attracts more clinical trials
            than diseases killing hundreds of thousands of Africans annually. The Orphan Drug Act
            provides tax credits, market exclusivity, and FDA fee waivers. No equivalent mechanism
            exists for diseases that are common in Africa but commercially invisible. The market
            has decided that 240,000 dead African children from RHD each year do not constitute
            a viable commercial opportunity.</p>
    </div>"""

    # Build full HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Africa's Forgotten Diseases &mdash; Clinical Trial Neglect Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></{'' + 'script'}>
<style>
:root {{
    --bg: #0a0e17;
    --bg2: #111827;
    --bg3: #1f2937;
    --text: #e5e7eb;
    --text2: #9ca3af;
    --accent: #60a5fa;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --orange: #f59e0b;
    --grey: #6b7280;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}}
.container {{ max-width:1600px; margin:0 auto; padding:24px; }}
h1 {{ font-size:2.2rem; margin-bottom:8px; color:white; }}
h2 {{ font-size:1.5rem; margin:40px 0 16px; color:var(--accent);
      border-bottom:1px solid var(--bg3); padding-bottom:8px; }}
h3 {{ font-size:1.2rem; margin:16px 0 8px; color:var(--text); }}
.subtitle {{ color:var(--text2); margin-bottom:24px; font-size:1.05rem; }}

.banner {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
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
    font-size:2.2rem;
    font-weight:800;
    color:white;
}}
.stat-card .label {{
    font-size:0.85rem;
    color:var(--text2);
    margin-top:4px;
}}

.charts-grid {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(450px,1fr));
    gap:24px;
    margin:24px 0;
}}
.chart-box {{
    background:var(--bg2);
    border-radius:12px;
    padding:20px;
    border:1px solid var(--bg3);
}}

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
    max-height:700px;
    overflow-y:auto;
    border-radius:12px;
    border:1px solid var(--bg3);
    background:var(--bg2);
}}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}

.zero-grid {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
    gap:20px;
    margin:20px 0;
}}

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

<h1>Africa's Forgotten Diseases</h1>
<p class="subtitle">Clinical Trial Neglect Analysis &mdash; 15 diseases that disproportionately
affect Africa yet receive near-zero trial investment &mdash; ClinicalTrials.gov &mdash;
Generated {datetime.now().strftime('%d %B %Y')}</p>

<!-- Summary Banner -->
<div class="banner">
    <div class="stat-card">
        <div class="value">{summary['total_diseases']}</div>
        <div class="label">Diseases Analyzed</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{summary['total_africa_trials']}</div>
        <div class="label">Total Africa Trials (all 15 diseases)</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--accent)">{summary['total_global_trials']}</div>
        <div class="label">Total Global Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:#94a3b8">{summary['total_us_trials']}</div>
        <div class="label">Total US Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--red)">{summary['zero_africa_count']}</div>
        <div class="label">Diseases with ZERO Africa Trials</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:#a78bfa">{summary['who_ntd_count']}</div>
        <div class="label">WHO NTDs in This List</div>
    </div>
    <div class="stat-card">
        <div class="value" style="color:var(--orange)">{summary['mean_neglect_index']}x</div>
        <div class="label">Mean Neglect Index (finite only)</div>
    </div>
</div>

<!-- Full Disease Table -->
<h2>Disease-by-Disease Analysis</h2>
<p style="color:var(--text2);margin-bottom:12px">
    <strong>Neglect Index</strong> = (Africa burden %) / (Africa trial share %).
    Higher values indicate greater mismatch between disease burden and research investment.
    INFINITE means Africa carries substantial burden but has zero trials.</p>
<div class="table-container">
<table>
<thead>
<tr>
<th>Disease</th>
<th style="text-align:right">Africa Burden</th>
<th style="text-align:right">Africa Trials</th>
<th style="text-align:right">Global Trials</th>
<th style="text-align:right">US Trials</th>
<th style="text-align:right">Africa Trial Share</th>
<th style="text-align:right">Neglect Index</th>
<th style="text-align:right">Annual Africa Deaths</th>
<th>Notes</th>
</tr>
</thead>
<tbody>
{disease_table_html}
</tbody>
</table>
</div>

<!-- The Absolute Zeros -->
<h2>The Absolute Zeros: Diseases with 0 Africa Trials</h2>
<p style="color:var(--text2);margin-bottom:16px">
    These diseases affect millions of Africans yet have not a single interventional
    clinical trial registered on ClinicalTrials.gov with an African site.
    The global research enterprise has rendered them invisible.</p>
<div class="zero-grid">
{zero_cards_html if zero_cards_html else '<p style="color:var(--green);padding:20px">All diseases have at least one Africa trial (still likely far too few)</p>'}
</div>

<!-- Charts -->
<h2>Visualizations</h2>
<div class="charts-grid">
    <div class="chart-box">
        <h3 style="margin-bottom:12px">Neglect Index by Disease</h3>
        <canvas id="niChart"></canvas>
    </div>
    <div class="chart-box">
        <h3 style="margin-bottom:12px">Africa vs US Trial Counts</h3>
        <canvas id="compareChart"></canvas>
    </div>
    <div class="chart-box">
        <h3 style="margin-bottom:12px">Sponsor Breakdown (Africa Trials)</h3>
        <canvas id="sponsorChart"></canvas>
    </div>
</div>

<!-- Spotlights -->
<h2>Disease Spotlights</h2>
{rhd_html}
{snakebite_html}
{noma_html}

<!-- HIC Comparison -->
<h2>The Structural Comparison</h2>
{hic_comparison_html}

<!-- Sponsor Analysis -->
<h2>Who Sponsors the Few Trials That Exist?</h2>
<div style="background:var(--bg2);border-radius:16px;padding:32px;margin:24px 0;
    border:1px solid var(--bg3)">
    <p style="color:var(--text);line-height:1.8;margin-bottom:20px">
        Among the small number of Africa-based trials identified for these 15 diseases,
        the sponsor breakdown reveals the dependency structure of African clinical research.</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px">
"""

    for cls, count in sorted(all_sponsor_counts.items(), key=lambda x: -x[1]):
        color = ("#22c55e" if cls == "African-led"
                 else "#ef4444" if cls == "Pharma"
                 else "#3b82f6" if cls == "Funder/NGO"
                 else "#6b7280")
        html += f"""        <div style="background:var(--bg3);border-radius:10px;padding:16px;text-align:center">
            <div style="font-size:2rem;font-weight:800;color:{color}">{count}</div>
            <div style="font-size:0.85rem;color:var(--text2)">{cls}</div>
        </div>
"""

    html += f"""    </div>
</div>

<!-- Methodology -->
<h2>Methodology</h2>
<div style="background:var(--bg2);border-radius:12px;padding:24px;border:1px solid var(--bg3)">
    <p style="color:var(--text2);line-height:1.8">
        <strong>Data source:</strong> ClinicalTrials.gov API v2, queried {datetime.now().strftime('%d %B %Y')}.<br>
        <strong>Scope:</strong> Interventional studies only. Each disease was queried individually using
        condition-specific search terms across {len(AFRICAN_COUNTRIES)} African countries plus the general
        term "Africa". Global and US counts were obtained separately.<br>
        <strong>Deduplication:</strong> Trials identified via multiple country queries were deduplicated
        by NCT ID.<br>
        <strong>Neglect Index:</strong> Computed as (Africa burden % for disease) / (Africa trial share
        of global trials %). A value of 10x means the disease burden in Africa is 10 times greater
        than would be expected from the trial investment. Diseases with zero Africa trials have an
        infinite Neglect Index.<br>
        <strong>Burden estimates:</strong> Africa burden percentages are drawn from WHO, GBD, and
        disease-specific literature. Annual death estimates are approximate and may include
        disability-adjusted figures.<br>
        <strong>Limitations:</strong> Single registry (ClinicalTrials.gov); does not capture trials
        registered only on WHO ICTRP, Pan African Clinical Trials Registry (PACTR), or national
        registries. US count used as high-income comparator. Sponsor classification is keyword-based
        and may misclassify some organizations.</p>
</div>

<div class="footer">
    <p>Africa's Forgotten Diseases &mdash; ClinicalTrials.gov Registry Analysis</p>
    <p>Data: ClinicalTrials.gov API v2 | Generated: {datetime.now().strftime('%d %B %Y')}</p>
    <p style="margin-top:8px">15 diseases. Billions affected. Near-zero trials.</p>
</div>

</div>

<script>
// Neglect Index chart
const niCtx = document.getElementById('niChart').getContext('2d');
new Chart(niCtx, {{
    type: 'bar',
    data: {{
        labels: {ni_labels},
        datasets: [{{
            label: 'Neglect Index',
            data: {ni_values},
            backgroundColor: {ni_colors},
            borderRadius: 4,
        }}]
    }},
    options: {{
        responsive: true,
        indexAxis: 'y',
        plugins: {{
            legend: {{ display: false }},
            tooltip: {{
                callbacks: {{
                    label: function(ctx) {{
                        return 'Neglect Index: ' + ctx.raw.toFixed(1) + 'x';
                    }}
                }}
            }}
        }},
        scales: {{
            x: {{
                title: {{ display: true, text: 'Neglect Index (higher = more neglected)',
                         color: '#9ca3af' }},
                ticks: {{ color: '#9ca3af' }},
                grid: {{ color: 'rgba(255,255,255,0.05)' }}
            }},
            y: {{
                ticks: {{ color: '#e5e7eb', font: {{ size: 11 }} }},
                grid: {{ display: false }}
            }}
        }}
    }}
}});

// Africa vs US comparison
const compCtx = document.getElementById('compareChart').getContext('2d');
new Chart(compCtx, {{
    type: 'bar',
    data: {{
        labels: {compare_names},
        datasets: [
            {{
                label: 'Africa trials',
                data: {compare_africa},
                backgroundColor: '#ef4444',
                borderRadius: 4,
            }},
            {{
                label: 'US trials',
                data: {compare_us},
                backgroundColor: '#3b82f6',
                borderRadius: 4,
            }}
        ]
    }},
    options: {{
        responsive: true,
        indexAxis: 'y',
        plugins: {{
            legend: {{ labels: {{ color: '#e5e7eb' }} }}
        }},
        scales: {{
            x: {{
                title: {{ display: true, text: 'Number of trials', color: '#9ca3af' }},
                ticks: {{ color: '#9ca3af' }},
                grid: {{ color: 'rgba(255,255,255,0.05)' }},
                stacked: false
            }},
            y: {{
                ticks: {{ color: '#e5e7eb', font: {{ size: 11 }} }},
                grid: {{ display: false }}
            }}
        }}
    }}
}});

// Sponsor breakdown
const spCtx = document.getElementById('sponsorChart').getContext('2d');
new Chart(spCtx, {{
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
                position: 'bottom',
                labels: {{ color: '#e5e7eb', padding: 16 }}
            }}
        }}
    }}
}});
</{'' + 'script'}>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated {OUTPUT_HTML}")


# -- Main -----------------------------------------------------------------
def main():
    data = collect_data()
    generate_html(data)

    summary = data["summary"]
    print(f"\n{'=' * 60}")
    print(f"SUMMARY: Africa's Forgotten Diseases")
    print(f"{'=' * 60}")
    print(f"Diseases analyzed:        {summary['total_diseases']}")
    print(f"Total Africa trials:      {summary['total_africa_trials']}")
    print(f"Total global trials:      {summary['total_global_trials']}")
    print(f"Total US trials:          {summary['total_us_trials']}")
    print(f"Diseases with 0 Africa:   {summary['zero_africa_count']}")
    if summary['zero_africa_names']:
        for name in summary['zero_africa_names']:
            print(f"  - {name}")
    print(f"WHO NTDs in list:         {summary['who_ntd_count']}")
    print(f"Mean Neglect Index:       {summary['mean_neglect_index']}x")
    print(f"\nOutput: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
