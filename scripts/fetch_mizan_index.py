#!/usr/bin/env python
"""
fetch_mizan_index.py — The Mizan Index: Measuring Research Justice Through Balance
===================================================================================
Computes a composite "Mizan Index" (0-100) for 20 African countries, measuring
how balanced/just the research ecosystem is across 7 dimensions inspired by
Maqasid al-Shariah (objectives of Islamic law).

Inspired by: "And the sky He raised and He set up the Mizan (Balance), that you
may not transgress the balance" (Quran 55:7-8).

The 7 Dimensions (each 0-14.3, total 0-100):
  1. Hifz al-Nafs  (Protection of Life)     — trial-burden alignment
  2. Adl           (Justice/Equity)          — per-capita trial fairness
  3. Istikhlaf     (Stewardship/Sovereignty) — local sponsorship
  4. Shura         (Consultation)            — community-engaged research
  5. La Darar      (No Harm)                 — protection from exploitation
  6. Ilm           (Knowledge/Capacity)      — Phase 1 + institutional diversity
  7. Amanah        (Trust/Accountability)     — results transparency

Usage:
    python fetch_mizan_index.py

Outputs:
    data/mizan_index_data.json   — cached API data (24h TTL)
    mizan-index.html             — gold/amber Islamic-themed dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import math
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "mizan_index_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "mizan-index.html"
RATE_LIMIT_DELAY = 0.4
CACHE_TTL_HOURS = 24
MAX_RETRIES = 3
MAX_DIM = 100 / 7  # ~14.286 per dimension

# ── 20 African countries: name -> population in millions (2025 est) ──
COUNTRIES = {
    "South Africa":               62,
    "Egypt":                      110,
    "Kenya":                      56,
    "Uganda":                     48,
    "Nigeria":                    230,
    "Tanzania":                   67,
    "Ethiopia":                   130,
    "Ghana":                      34,
    "Malawi":                     21,
    "Zambia":                     21,
    "Zimbabwe":                   16,
    "Rwanda":                     14,
    "Senegal":                    18,
    "Democratic Republic of Congo": 105,
    "Burkina Faso":               23,
    "Mozambique":                 34,
    "Morocco":                    38,
    "Tunisia":                    12,
    "Cameroon":                   29,
    "Mali":                       23,
}

# ── WHO top-3 causes of death per country (GBD 2021 / WHO GHE 2024) ──
WHO_TOP3_BURDEN = {
    "South Africa":               ["HIV", "cardiovascular", "diabetes"],
    "Egypt":                      ["cardiovascular", "cancer", "diabetes"],
    "Kenya":                      ["HIV", "malaria", "cardiovascular"],
    "Uganda":                     ["HIV", "malaria", "cardiovascular"],
    "Nigeria":                    ["malaria", "HIV", "cardiovascular"],
    "Tanzania":                   ["HIV", "malaria", "cardiovascular"],
    "Ethiopia":                   ["malaria", "HIV", "cardiovascular"],
    "Ghana":                      ["malaria", "cardiovascular", "HIV"],
    "Malawi":                     ["HIV", "malaria", "cardiovascular"],
    "Zambia":                     ["HIV", "malaria", "cardiovascular"],
    "Zimbabwe":                   ["HIV", "cardiovascular", "cancer"],
    "Rwanda":                     ["HIV", "malaria", "cardiovascular"],
    "Senegal":                    ["malaria", "cardiovascular", "HIV"],
    "Democratic Republic of Congo": ["malaria", "HIV", "cardiovascular"],
    "Burkina Faso":               ["malaria", "cardiovascular", "HIV"],
    "Mozambique":                 ["HIV", "malaria", "cardiovascular"],
    "Morocco":                    ["cardiovascular", "cancer", "diabetes"],
    "Tunisia":                    ["cardiovascular", "cancer", "diabetes"],
    "Cameroon":                   ["HIV", "malaria", "cardiovascular"],
    "Mali":                       ["malaria", "cardiovascular", "HIV"],
}

# Conditions to query for burden alignment
CONDITIONS = ["HIV", "cardiovascular", "cancer", "malaria"]

# Global median trials per million (approx from CT.gov: ~500K trials / 8B people)
GLOBAL_MEDIAN_TRIALS_PER_MILLION = 62.5

# ── Local institution keywords per country ────────────────────────────
LOCAL_KEYWORDS = {
    "South Africa": [
        "witwatersrand", "cape town", "stellenbosch", "pretoria",
        "kwazulu", "medical research council", "south african",
        "samrc", "groote schuur", "tygerberg",
    ],
    "Egypt": [
        "cairo", "ain shams", "alexandria", "mansoura", "assiut",
        "egyptian", "tanta", "zagazig",
    ],
    "Kenya": [
        "kemri", "nairobi", "aga khan", "moi university", "kenyatta",
        "kenya", "kilifi",
    ],
    "Uganda": [
        "makerere", "mulago", "mbarara", "kampala", "gulu", "uganda",
        "mrc/uvri", "busitema", "infectious diseases institute",
    ],
    "Nigeria": [
        "ibadan", "lagos", "nigeria", "obafemi awolowo", "ahmadu bello",
        "university of nigeria", "unilag", "nimr",
    ],
    "Tanzania": [
        "ifakara", "muhimbili", "kilimanjaro", "dar es salaam",
        "tanzania", "nimr",
    ],
    "Ethiopia": [
        "addis ababa", "ethiopia", "jimma", "gondar", "hawassa",
        "mekelle", "armauer hansen",
    ],
    "Ghana": [
        "ghana", "korle-bu", "kumasi", "kwame nkrumah", "noguchi",
        "navrongo", "kintampo",
    ],
    "Malawi": [
        "malawi", "kamuzu", "blantyre", "lilongwe", "zomba",
        "malawi-liverpool-wellcome",
    ],
    "Zambia": [
        "zambia", "lusaka", "university teaching hospital",
        "tropical diseases research centre",
    ],
    "Zimbabwe": [
        "zimbabwe", "harare", "biomedical research and training",
        "chinhoyi", "parirenyatwa",
    ],
    "Rwanda": [
        "rwanda", "kigali", "butaro", "partners in health",
    ],
    "Senegal": [
        "senegal", "dakar", "cheikh anta diop", "institut pasteur dakar",
        "le dantec",
    ],
    "Democratic Republic of Congo": [
        "congo", "kinshasa", "lubumbashi", "inrb",
        "university of kinshasa",
    ],
    "Burkina Faso": [
        "burkina", "ouagadougou", "bobo-dioulasso",
        "centre muraz", "irss",
    ],
    "Mozambique": [
        "mozambique", "maputo", "manhica", "beira",
        "eduardo mondlane",
    ],
    "Morocco": [
        "morocco", "rabat", "casablanca", "marrakech", "fes",
        "mohammed v", "hassan ii",
    ],
    "Tunisia": [
        "tunisia", "tunis", "sfax", "sousse", "monastir",
        "institut pasteur de tunis",
    ],
    "Cameroon": [
        "cameroon", "yaounde", "douala", "bamenda",
        "centre pasteur du cameroun",
    ],
    "Mali": [
        "mali", "bamako", "mrtc", "point g",
        "university of bamako",
    ],
}


# ── API helper ────────────────────────────────────────────────────────
def api_query(location=None, condition=None, study_type="INTERVENTIONAL",
              phase=None, status=None, page_size=10, count_total=True):
    """Query CT.gov API v2 with correct v2 syntax."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": str(count_total).lower(),
    }
    filters = []
    if study_type:
        filters.append(f"AREA[StudyType]{study_type}")
    if phase:
        phase_map = {
            "EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1",
            "PHASE2": "Phase 2", "PHASE3": "Phase 3",
            "PHASE4": "Phase 4", "NA": "Not Applicable",
        }
        filters.append(f"AREA[Phase]{phase_map.get(phase, phase)}")
    if status:
        filters.append(f"AREA[OverallStatus]{status}")
    if filters:
        params["filter.advanced"] = " AND ".join(filters)
    if condition:
        params["query.cond"] = condition
    if location:
        params["query.locn"] = location

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  WARNING: API error for {location}/{condition}: {e}")
                return {"totalCount": 0, "studies": []}


def get_total(result):
    return result.get("totalCount", 0)


def extract_trial_info(study):
    """Extract key fields from a CT.gov v2 study object."""
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    design = proto.get("designModule", {})
    status_mod = proto.get("statusModule", {})
    enrollment_info = design.get("enrollmentInfo", {})
    cond_mod = proto.get("conditionsModule", {})
    contacts_loc = proto.get("contactsLocationsModule", {})
    locations = contacts_loc.get("locations", [])
    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
        "sponsor_class": sponsor_mod.get("leadSponsor", {}).get("class", ""),
        "status": status_mod.get("overallStatus", ""),
        "phases": design.get("phases", []),
        "enrollment": enrollment_info.get("count", 0),
        "conditions": cond_mod.get("conditions", []),
        "locations_count": len(locations),
    }


# ── Cache management ─────────────────────────────────────────────────
def is_cache_valid():
    if not CACHE_FILE.exists():
        return False
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_date = datetime.fromisoformat(data["meta"]["date"].split("+")[0])
        return (datetime.now() - cached_date) < timedelta(hours=CACHE_TTL_HOURS)
    except (json.JSONDecodeError, KeyError, ValueError):
        return False


def load_cache():
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Cache saved to {CACHE_FILE}")


# ── Sponsor classification ────────────────────────────────────────────
def classify_sponsor(sponsor_name, country):
    """Classify sponsor as local or foreign for a given country."""
    if not sponsor_name:
        return False
    name_lower = sponsor_name.lower()
    keywords = LOCAL_KEYWORDS.get(country, [])
    return any(kw in name_lower for kw in keywords)


# ── Data collection ───────────────────────────────────────────────────
def fetch_country_data(country):
    """Fetch trial data for a single country from CT.gov API v2."""
    print(f"\n  --- {country} ---")
    result = {
        "total": 0,
        "conditions": {},
        "phases": {
            "EARLY_PHASE1": 0, "PHASE1": 0, "PHASE2": 0,
            "PHASE3": 0, "PHASE4": 0, "NA": 0,
        },
        "statuses": {},
        "sample_trials": [],
    }

    # 1. Total + sample trials (up to 200 for sponsor/status analysis)
    print(f"    Fetching total + sample trials...")
    r = api_query(location=country, page_size=200)
    result["total"] = get_total(r)
    studies = r.get("studies", [])
    for s in studies:
        result["sample_trials"].append(extract_trial_info(s))
    print(f"    Total: {result['total']}, samples: {len(result['sample_trials'])}")
    time.sleep(RATE_LIMIT_DELAY)

    # 2. Condition counts (HIV, cardiovascular, cancer, malaria)
    for cond in CONDITIONS:
        r = api_query(location=country, condition=cond)
        result["conditions"][cond] = get_total(r)
        time.sleep(RATE_LIMIT_DELAY)
    print(f"    Conditions: {result['conditions']}")

    # 3. Phase counts (Phase 1, Early Phase 1, NA for community-engaged proxy)
    for phase in ["EARLY_PHASE1", "PHASE1", "NA"]:
        r = api_query(location=country, phase=phase)
        result["phases"][phase] = get_total(r)
        time.sleep(RATE_LIMIT_DELAY)
    print(f"    Phase 1: {result['phases']['EARLY_PHASE1'] + result['phases']['PHASE1']}, "
          f"NA: {result['phases']['NA']}")

    # 4. Status distribution (from sample trials)
    status_counter = Counter(t["status"] for t in result["sample_trials"])
    result["statuses"] = dict(status_counter)
    print(f"    Statuses: {result['statuses']}")

    return result


def collect_all_data():
    """Collect data for all 20 countries."""
    print("=" * 60)
    print("THE MIZAN INDEX -- Data Collection")
    print("=" * 60)

    all_data = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
            "countries": len(COUNTRIES),
            "script": "fetch_mizan_index.py",
        },
        "countries": {},
    }

    for i, country in enumerate(COUNTRIES, 1):
        print(f"\n[{i}/{len(COUNTRIES)}] {country}")
        all_data["countries"][country] = fetch_country_data(country)

    return all_data


# ── Scoring functions (7 Mizan dimensions) ────────────────────────────

def score_d1_hifz_alnafs(country_data, country):
    """D1: Hifz al-Nafs (Protection of Life) — trial-burden alignment.
    Score: alignment between trial portfolio and top-3 causes of death.
    0-14.3 scale."""
    who_top3 = WHO_TOP3_BURDEN.get(country, [])
    cond_counts = country_data.get("conditions", {})
    total = country_data.get("total", 0)
    if total == 0 or not cond_counts:
        return 0.0, {"matches": 0, "who_top3": who_top3, "research_top3": []}

    # Sort conditions by trial count
    sorted_conds = sorted(cond_counts.items(), key=lambda x: x[1], reverse=True)
    research_top3 = [c[0].lower() for c in sorted_conds[:3]]
    who_lower = [w.lower() for w in who_top3]

    matches = sum(1 for w in who_lower if w in research_top3)

    # Also weight by concentration: what % of trials target top-3 burden conditions
    burden_trials = sum(cond_counts.get(c, 0) for c in who_top3)
    all_cond_trials = sum(cond_counts.values())
    concentration = burden_trials / max(all_cond_trials, 1)

    # Score: 60% matches (0-3), 40% concentration (0-1)
    match_score = (matches / 3) * 0.6
    conc_score = min(concentration, 1.0) * 0.4
    raw = (match_score + conc_score) * MAX_DIM

    detail = {
        "matches": matches,
        "who_top3": who_top3,
        "research_top3": [c[0] for c in sorted_conds[:3]],
        "burden_trial_pct": round(concentration * 100, 1),
    }
    return round(raw, 2), detail


def score_d2_adl(country_data, country, population):
    """D2: Adl (Justice/Equity) — per-capita trial fairness.
    Score: per-capita trial rate relative to global median."""
    total = country_data.get("total", 0)
    if population <= 0:
        return 0.0, {"density": 0.0, "ratio": 0.0}

    density = total / population
    ratio = density / GLOBAL_MEDIAN_TRIALS_PER_MILLION

    # Score: logarithmic scale (most African countries far below median)
    if ratio >= 1.0:
        raw = MAX_DIM
    elif ratio >= 0.5:
        raw = MAX_DIM * 0.8
    elif ratio >= 0.2:
        raw = MAX_DIM * 0.6
    elif ratio >= 0.1:
        raw = MAX_DIM * 0.4
    elif ratio >= 0.05:
        raw = MAX_DIM * 0.2
    else:
        raw = MAX_DIM * 0.05

    detail = {
        "density": round(density, 2),
        "ratio_to_global": round(ratio, 3),
        "total_trials": total,
        "population_m": population,
    }
    return round(raw, 2), detail


def score_d3_istikhlaf(country_data, country):
    """D3: Istikhlaf (Stewardship/Sovereignty) — local sponsorship %.
    Score: % of trials led by local institutions."""
    trials = country_data.get("sample_trials", [])
    if not trials:
        return 0.0, {"local_pct": 0.0, "local_n": 0, "total_sampled": 0}

    local_n = sum(1 for t in trials if classify_sponsor(t["sponsor"], country))
    pct = local_n / len(trials) * 100

    # Score: linear with floor
    if pct >= 50:
        raw = MAX_DIM
    elif pct >= 30:
        raw = MAX_DIM * 0.8
    elif pct >= 20:
        raw = MAX_DIM * 0.6
    elif pct >= 10:
        raw = MAX_DIM * 0.4
    elif pct >= 5:
        raw = MAX_DIM * 0.2
    else:
        raw = MAX_DIM * 0.05

    detail = {
        "local_pct": round(pct, 1),
        "local_n": local_n,
        "total_sampled": len(trials),
    }
    return round(raw, 2), detail


def score_d4_shura(country_data, country):
    """D4: Shura (Consultation/Community Voice) — community-engaged research.
    Proxy: % of implementation/behavioral trials (Phase NA) which tend to
    involve community engagement, vs pure drug testing."""
    total = country_data.get("total", 0)
    na_count = country_data.get("phases", {}).get("NA", 0)
    if total == 0:
        return 0.0, {"na_pct": 0.0, "na_count": 0}

    na_pct = na_count / total * 100

    # Score: higher NA% suggests more implementation science / community voice
    if na_pct >= 40:
        raw = MAX_DIM
    elif na_pct >= 30:
        raw = MAX_DIM * 0.8
    elif na_pct >= 20:
        raw = MAX_DIM * 0.6
    elif na_pct >= 10:
        raw = MAX_DIM * 0.4
    elif na_pct >= 5:
        raw = MAX_DIM * 0.2
    else:
        raw = MAX_DIM * 0.05

    detail = {
        "na_pct": round(na_pct, 1),
        "na_count": na_count,
        "total": total,
    }
    return round(raw, 2), detail


def score_d5_la_darar(country_data, country):
    """D5: La Darar (No Harm) — protection from exploitation.
    Score: inverse of ghost enrollment rate (multi-site mega-trials
    that use African sites for recruitment without local benefit)."""
    trials = country_data.get("sample_trials", [])
    if not trials:
        return MAX_DIM * 0.5, {"ghost_pct": 0.0, "ghost_n": 0}

    # Ghost = trials with >50 locations (likely global mega-trial using site as body farm)
    ghost_n = sum(1 for t in trials if t.get("locations_count", 1) > 50)
    ghost_pct = ghost_n / len(trials) * 100

    # Score: inverse (fewer ghosts = higher score)
    non_ghost_pct = 100 - ghost_pct
    if non_ghost_pct >= 95:
        raw = MAX_DIM
    elif non_ghost_pct >= 85:
        raw = MAX_DIM * 0.8
    elif non_ghost_pct >= 75:
        raw = MAX_DIM * 0.6
    elif non_ghost_pct >= 60:
        raw = MAX_DIM * 0.4
    elif non_ghost_pct >= 40:
        raw = MAX_DIM * 0.2
    else:
        raw = MAX_DIM * 0.05

    detail = {
        "ghost_pct": round(ghost_pct, 1),
        "ghost_n": ghost_n,
        "total_sampled": len(trials),
        "non_ghost_pct": round(non_ghost_pct, 1),
    }
    return round(raw, 2), detail


def score_d6_ilm(country_data, country):
    """D6: Ilm (Knowledge/Capacity) — Phase 1 sovereignty + institutional diversity.
    Score: combines Phase 1 presence and unique local sponsor count."""
    phases = country_data.get("phases", {})
    trials = country_data.get("sample_trials", [])
    phase1_total = phases.get("EARLY_PHASE1", 0) + phases.get("PHASE1", 0)

    # Unique local sponsors
    local_sponsors = set()
    for t in trials:
        if classify_sponsor(t["sponsor"], country):
            local_sponsors.add(t["sponsor"])
    n_local_inst = len(local_sponsors)

    # Phase 1 sub-score (0-0.5)
    if phase1_total > 10:
        p1_sub = 0.5
    elif phase1_total >= 5:
        p1_sub = 0.4
    elif phase1_total >= 2:
        p1_sub = 0.3
    elif phase1_total >= 1:
        p1_sub = 0.2
    else:
        p1_sub = 0.0

    # Institutional diversity sub-score (0-0.5)
    if n_local_inst >= 8:
        inst_sub = 0.5
    elif n_local_inst >= 5:
        inst_sub = 0.4
    elif n_local_inst >= 3:
        inst_sub = 0.3
    elif n_local_inst >= 1:
        inst_sub = 0.15
    else:
        inst_sub = 0.0

    raw = (p1_sub + inst_sub) * MAX_DIM

    detail = {
        "phase1_total": phase1_total,
        "local_institutions": n_local_inst,
        "local_names": sorted(local_sponsors)[:5],
    }
    return round(raw, 2), detail


def score_d7_amanah(country_data, country):
    """D7: Amanah (Trust/Accountability) — results transparency.
    Score: inverse of UNKNOWN status rate among sampled trials."""
    statuses = country_data.get("statuses", {})
    trials = country_data.get("sample_trials", [])
    if not trials:
        return 0.0, {"unknown_pct": 100.0, "completed_pct": 0.0}

    total = len(trials)
    unknown_n = statuses.get("UNKNOWN", 0)
    completed_n = statuses.get("COMPLETED", 0)
    has_results_n = statuses.get("COMPLETED", 0)  # completed as proxy for results

    unknown_pct = unknown_n / total * 100
    completed_pct = completed_n / total * 100

    # Score: inverse of unknown rate, boosted by completion rate
    transparency = (100 - unknown_pct) / 100
    completion_bonus = min(completed_pct / 100, 0.3)  # up to 30% bonus weight

    raw_fraction = transparency * 0.7 + completion_bonus
    raw = min(raw_fraction * MAX_DIM, MAX_DIM)

    detail = {
        "unknown_pct": round(unknown_pct, 1),
        "completed_pct": round(completed_pct, 1),
        "unknown_n": unknown_n,
        "total_sampled": total,
        "statuses": statuses,
    }
    return round(raw, 2), detail


# ── Composite scoring ─────────────────────────────────────────────────
def compute_all_scores(all_data):
    """Compute Mizan Index for all 20 countries."""
    scores = {}
    for country, pop in COUNTRIES.items():
        cd = all_data["countries"].get(country, {})

        d1, d1_detail = score_d1_hifz_alnafs(cd, country)
        d2, d2_detail = score_d2_adl(cd, country, pop)
        d3, d3_detail = score_d3_istikhlaf(cd, country)
        d4, d4_detail = score_d4_shura(cd, country)
        d5, d5_detail = score_d5_la_darar(cd, country)
        d6, d6_detail = score_d6_ilm(cd, country)
        d7, d7_detail = score_d7_amanah(cd, country)

        total = round(d1 + d2 + d3 + d4 + d5 + d6 + d7, 1)

        # Grade
        if total >= 70:
            grade = "A"
        elif total >= 55:
            grade = "B"
        elif total >= 40:
            grade = "C"
        elif total >= 25:
            grade = "D"
        else:
            grade = "F"

        scores[country] = {
            "total_trials": cd.get("total", 0),
            "population_millions": pop,
            "d1": {"score": d1, "label": "Hifz al-Nafs", "detail": d1_detail},
            "d2": {"score": d2, "label": "Adl", "detail": d2_detail},
            "d3": {"score": d3, "label": "Istikhlaf", "detail": d3_detail},
            "d4": {"score": d4, "label": "Shura", "detail": d4_detail},
            "d5": {"score": d5, "label": "La Darar", "detail": d5_detail},
            "d6": {"score": d6, "label": "Ilm", "detail": d6_detail},
            "d7": {"score": d7, "label": "Amanah", "detail": d7_detail},
            "mizan_index": total,
            "grade": grade,
        }

    return scores


# ── HTML generation ───────────────────────────────────────────────────
def _hex_to_rgb(hex_color):
    """Convert hex color to 'r, g, b' string."""
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"


def _build_radar_svg(countries_data, all_scores, label=""):
    """Build an SVG radar chart for a set of countries."""
    dims = ["d1", "d2", "d3", "d4", "d5", "d6", "d7"]
    dim_short = [
        "Life", "Justice", "Steward", "Voice", "No Harm", "Knowledge", "Trust"
    ]
    colors = [
        "#d4af37", "#27ae60", "#3498db", "#e67e22", "#e74c3c", "#9b59b6", "#1abc9c"
    ]

    cx, cy, r = 200, 200, 150
    n = len(dims)
    angle_step = 2 * math.pi / n

    svg_parts = []
    svg_parts.append(f'<svg viewBox="0 0 400 420" xmlns="http://www.w3.org/2000/svg" '
                     f'style="max-width:380px;width:100%;">')

    # Background circles
    for frac in [0.25, 0.5, 0.75, 1.0]:
        rr = r * frac
        svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="{rr}" '
                         f'fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="1"/>')

    # Axis lines and labels
    for i in range(n):
        angle = -math.pi / 2 + i * angle_step
        x2 = cx + r * math.cos(angle)
        y2 = cy + r * math.sin(angle)
        svg_parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" '
                         f'stroke="rgba(255,255,255,0.15)" stroke-width="1"/>')
        lx = cx + (r + 25) * math.cos(angle)
        ly = cy + (r + 25) * math.sin(angle)
        anchor = "middle"
        if lx < cx - 10:
            anchor = "end"
        elif lx > cx + 10:
            anchor = "start"
        svg_parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="rgba(255,255,255,0.7)" '
                         f'font-size="11" text-anchor="{anchor}" '
                         f'dominant-baseline="central">{dim_short[i]}</text>')

    # Country polygons
    for idx, (cname, cdata) in enumerate(countries_data):
        color = colors[idx % len(colors)]
        points = []
        for i in range(n):
            angle = -math.pi / 2 + i * angle_step
            val = cdata[dims[i]]["score"] / MAX_DIM  # 0-1
            pr = r * val
            px = cx + pr * math.cos(angle)
            py = cy + pr * math.sin(angle)
            points.append(f"{px:.1f},{py:.1f}")
        pts_str = " ".join(points)
        svg_parts.append(f'<polygon points="{pts_str}" fill="rgba({_hex_to_rgb(color)}, 0.15)" '
                         f'stroke="{color}" stroke-width="2"/>')
        # Legend entry
        ly = 395 + idx * 0  # We'll use inline legend below
        svg_parts.append(f'<!-- {cname}: {color} -->')

    # Inline legend at bottom
    legend_y = 400
    total_w = len(countries_data) * 120
    start_x = max(cx - total_w / 2, 10)
    for idx, (cname, cdata) in enumerate(countries_data):
        color = colors[idx % len(colors)]
        lx = start_x + idx * 120
        short_name = cname[:12] + ".." if len(cname) > 14 else cname
        svg_parts.append(f'<rect x="{lx:.0f}" y="{legend_y}" width="10" height="10" fill="{color}"/>')
        svg_parts.append(f'<text x="{lx + 14:.0f}" y="{legend_y + 9}" fill="rgba(255,255,255,0.8)" '
                         f'font-size="10">{short_name} ({cdata["mizan_index"]})</text>')

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


def generate_html(scores, all_data):
    """Generate the Mizan Index HTML dashboard."""
    date_str = datetime.now().strftime("%d %B %Y")

    # Sort by Mizan Index descending
    ranked = sorted(scores.items(), key=lambda x: x[1]["mizan_index"], reverse=True)
    total_scores = [s["mizan_index"] for _, s in ranked]
    avg_score = round(sum(total_scores) / len(total_scores), 1)
    best_country, best_data = ranked[0]
    worst_country, worst_data = ranked[-1]

    # Grade distribution
    grade_counts = Counter(s["grade"] for s in scores.values())

    # Top 3 and bottom 3
    top3 = ranked[:3]
    bottom3 = ranked[-3:]

    # Build radar SVGs
    radar_top3 = _build_radar_svg(top3, scores, "Top 3")
    radar_bottom3 = _build_radar_svg(bottom3, scores, "Bottom 3")

    # Dimension analysis — find weakest dimension across Africa
    dims = ["d1", "d2", "d3", "d4", "d5", "d6", "d7"]
    dim_names = {
        "d1": "Hifz al-Nafs (Protection of Life)",
        "d2": "Adl (Justice/Equity)",
        "d3": "Istikhlaf (Stewardship/Sovereignty)",
        "d4": "Shura (Consultation/Community Voice)",
        "d5": "La Darar (No Harm)",
        "d6": "Ilm (Knowledge/Capacity)",
        "d7": "Amanah (Trust/Accountability)",
    }
    dim_arabic = {
        "d1": "حفظ النفس",
        "d2": "العدل",
        "d3": "الاستخلاف",
        "d4": "الشورى",
        "d5": "لا ضرر",
        "d6": "العلم",
        "d7": "الأمانة",
    }
    dim_descriptions = {
        "d1": "Do trials target the top killers? Measures alignment between a country's trial portfolio and its WHO top-3 causes of death. A nation that studies what kills its people honors the sanctity of life.",
        "d2": "Is trial access per-capita fair? Compares each country's trials-per-million to the global median. Justice demands proportional access to the benefits of research.",
        "d3": "Who controls the research agenda? Measures % of trials led by local institutions. True stewardship means communities direct their own knowledge production.",
        "d4": "Are communities represented? Uses implementation/behavioral trial share as a proxy for community-engaged research. Shura requires the voices of those affected.",
        "d5": "Are participants protected from exploitation? Inverse of ghost enrollment rate (mega-trials using African sites as body farms). The Prophetic principle: no harm, no reciprocal harm.",
        "d6": "Is research building local knowledge? Combines Phase 1 trial sovereignty (capacity for first-in-human studies) with institutional diversity. Knowledge is a trust.",
        "d7": "Are results reported transparently? Inverse of UNKNOWN status rate. Trust requires that what was promised is delivered and disclosed.",
    }
    dim_quran = {
        "d1": '"Whoever saves a life, it is as if they saved all of mankind" (5:32)',
        "d2": '"O you who believe, be persistently standing firm in justice" (4:135)',
        "d3": '"It is He who made you stewards of the earth" (6:165)',
        "d4": '"And those who conduct their affairs by mutual consultation" (42:38)',
        "d5": '"There should be neither harm nor reciprocal harm" (Hadith, Ibn Majah)',
        "d6": '"Say: Are those who know equal to those who do not know?" (39:9)',
        "d7": '"Indeed, Allah commands you to render trusts to whom they are due" (4:58)',
    }

    dim_averages = {}
    for d in dims:
        avg = sum(s[d]["score"] for s in scores.values()) / len(scores)
        dim_averages[d] = round(avg, 2)
    weakest_dim = min(dim_averages, key=dim_averages.get)
    strongest_dim = max(dim_averages, key=dim_averages.get)

    # Build scorecard table rows
    table_rows = ""
    grade_color_map = {
        "A": "#d4af37", "B": "#27ae60", "C": "#3498db",
        "D": "#e67e22", "F": "#e74c3c",
    }
    for rank_idx, (country, s) in enumerate(ranked, 1):
        gc = grade_color_map.get(s["grade"], "#888")
        total_bg = f"rgba({_hex_to_rgb(gc)}, 0.12)"
        table_rows += f"""
        <tr>
          <td style="text-align:center;font-weight:600;color:#d4af37;">{rank_idx}</td>
          <td style="font-weight:600;">{country}</td>
          <td class="dim-cell">{s['d1']['score']:.1f}</td>
          <td class="dim-cell">{s['d2']['score']:.1f}</td>
          <td class="dim-cell">{s['d3']['score']:.1f}</td>
          <td class="dim-cell">{s['d4']['score']:.1f}</td>
          <td class="dim-cell">{s['d5']['score']:.1f}</td>
          <td class="dim-cell">{s['d6']['score']:.1f}</td>
          <td class="dim-cell">{s['d7']['score']:.1f}</td>
          <td style="text-align:center;font-weight:700;font-size:1.15em;
              background:{total_bg};color:{gc};">{s['mizan_index']}</td>
          <td style="text-align:center;font-weight:700;font-size:1.2em;
              color:{gc};">{s['grade']}</td>
        </tr>"""

    # Dimension deep-dive sections
    dimension_sections = ""
    for d in dims:
        dim_ranked = sorted(scores.items(), key=lambda x: x[1][d]["score"], reverse=True)
        top_in_dim = dim_ranked[:3]
        bot_in_dim = dim_ranked[-3:]
        top_str = ", ".join(f"{c} ({s[d]['score']:.1f})" for c, s in top_in_dim)
        bot_str = ", ".join(f"{c} ({s[d]['score']:.1f})" for c, s in bot_in_dim)

        dimension_sections += f"""
        <div class="card dim-card">
          <div class="dim-header">
            <span class="dim-arabic">{dim_arabic[d]}</span>
            <h3 class="dim-title">{dim_names[d]}</h3>
          </div>
          <p class="dim-quran">{dim_quran[d]}</p>
          <p class="dim-desc">{dim_descriptions[d]}</p>
          <div class="dim-avg">Continental average: <strong>{dim_averages[d]:.1f}</strong> / {MAX_DIM:.1f}</div>
          <div class="dim-leaders">
            <div class="dim-top"><span class="badge-good">Highest</span> {top_str}</div>
            <div class="dim-bottom"><span class="badge-bad">Lowest</span> {bot_str}</div>
          </div>
        </div>"""

    # Grade distribution bars
    grade_bar_html = ""
    grade_order = ["A", "B", "C", "D", "F"]
    grade_labels_map = {
        "A": "A (70-100)", "B": "B (55-69)", "C": "C (40-54)",
        "D": "D (25-39)", "F": "F (0-24)",
    }
    for g in grade_order:
        count = grade_counts.get(g, 0)
        pct = count / len(scores) * 100
        gc = grade_color_map.get(g, "#888")
        grade_bar_html += f"""
        <div class="grade-row">
          <span class="grade-label" style="color:{gc};">{grade_labels_map[g]}</span>
          <div class="grade-bar-track">
            <div class="grade-bar-fill" style="width:{pct}%;background:{gc};"></div>
          </div>
          <span class="grade-count">{count} countries</span>
        </div>"""

    # Imbalance analysis: which dimension is most consistently low?
    imbalance_rows = ""
    sorted_dims = sorted(dim_averages.items(), key=lambda x: x[1])
    for d, avg in sorted_dims:
        bar_pct = avg / MAX_DIM * 100
        color = "#e74c3c" if avg < MAX_DIM * 0.3 else "#e67e22" if avg < MAX_DIM * 0.5 else "#d4af37"
        imbalance_rows += f"""
        <div class="imbalance-row">
          <span class="imbalance-label">{dim_names[d]}</span>
          <div class="imbalance-bar-track">
            <div class="imbalance-bar-fill" style="width:{bar_pct:.0f}%;background:{color};"></div>
          </div>
          <span class="imbalance-value" style="color:{color};">{avg:.1f} / {MAX_DIM:.1f}</span>
        </div>"""

    # Country bar chart (horizontal)
    country_bars = ""
    max_score = max(s["mizan_index"] for s in scores.values()) if scores else 100
    for rank_idx, (country, s) in enumerate(ranked, 1):
        gc = grade_color_map.get(s["grade"], "#888")
        pct = s["mizan_index"] / 100 * 100
        country_bars += f"""
        <div class="country-bar-row">
          <span class="country-bar-name">{country}</span>
          <div class="country-bar-track">
            <div class="country-bar-fill" style="width:{pct:.0f}%;background:linear-gradient(90deg, {gc}, rgba({_hex_to_rgb(gc)},0.4));"></div>
          </div>
          <span class="country-bar-score" style="color:{gc};">{s['mizan_index']}</span>
        </div>"""

    # Policy implications
    policy_items = []
    if dim_averages.get(weakest_dim, 0) < MAX_DIM * 0.3:
        policy_items.append(f"<strong>{dim_names[weakest_dim]}</strong> is the weakest dimension across the continent (avg {dim_averages[weakest_dim]:.1f}/{MAX_DIM:.1f}). This represents the most urgent area for systemic intervention.")

    # Count countries below threshold
    low_equity_n = sum(1 for s in scores.values() if s["d2"]["score"] < MAX_DIM * 0.3)
    if low_equity_n > 10:
        policy_items.append(f"<strong>{low_equity_n} of 20 countries</strong> score below 30% on the Justice/Equity dimension, meaning their citizens have drastically unequal access to clinical research participation.")

    low_sovereignty_n = sum(1 for s in scores.values() if s["d3"]["score"] < MAX_DIM * 0.3)
    if low_sovereignty_n > 5:
        policy_items.append(f"<strong>{low_sovereignty_n} countries</strong> score below 30% on Stewardship/Sovereignty. In these nations, the research agenda is controlled predominantly by external institutions.")

    # Check for La Darar concerns
    high_ghost_n = sum(1 for s in scores.values() if s["d5"]["detail"].get("ghost_pct", 0) > 15)
    if high_ghost_n > 0:
        policy_items.append(f"<strong>{high_ghost_n} countries</strong> show ghost enrollment rates above 15%, suggesting participants may be enrolled in mega-trials designed elsewhere with minimal local benefit.")

    policy_html = "\n".join(f'<li class="policy-item">{item}</li>' for item in policy_items)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Mizan Index: Measuring Research Justice Through the Lens of Balance</title>
<style>
/* ===== CSS VARIABLES ===== */
:root {{
    --bg: #0a0e17;
    --surface: #111827;
    --surface2: #1a2236;
    --gold: #d4af37;
    --gold-light: #f0d060;
    --gold-dim: rgba(212, 175, 55, 0.15);
    --amber: #f59e0b;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --accent-green: #27ae60;
    --accent-red: #e74c3c;
    --accent-blue: #3498db;
    --accent-orange: #e67e22;
    --border: rgba(212, 175, 55, 0.2);
    --font-body: 'Georgia', 'Times New Roman', serif;
    --font-heading: 'Georgia', 'Times New Roman', serif;
    --font-mono: 'Consolas', 'Monaco', monospace;
    --font-arabic: 'Amiri', 'Traditional Arabic', 'Scheherazade New', serif;
}}

/* ===== RESET & BASE ===== */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ scroll-behavior: smooth; }}

body {{
    font-family: var(--font-body);
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    min-height: 100vh;
}}

/* ===== LAYOUT ===== */
.container {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 24px;
}}

/* ===== HEADER ===== */
.hero {{
    text-align: center;
    padding: 60px 0 40px;
    border-bottom: 2px solid var(--gold-dim);
    background: linear-gradient(180deg, rgba(212,175,55,0.06) 0%, transparent 100%);
}}

.hero-arabic {{
    font-family: var(--font-arabic);
    font-size: 2.2em;
    color: var(--gold);
    direction: rtl;
    margin-bottom: 12px;
    line-height: 1.5;
}}

.hero-verse {{
    font-style: italic;
    color: var(--gold-light);
    font-size: 1.1em;
    max-width: 700px;
    margin: 0 auto 20px;
    line-height: 1.6;
}}

.hero-title {{
    font-family: var(--font-heading);
    font-size: 2.6em;
    color: var(--gold);
    margin-bottom: 8px;
    letter-spacing: 0.02em;
    text-shadow: 0 0 40px rgba(212,175,55,0.2);
}}

.hero-subtitle {{
    font-size: 1.15em;
    color: var(--text-dim);
    max-width: 700px;
    margin: 0 auto;
}}

.hero-meta {{
    margin-top: 20px;
    font-size: 0.9em;
    color: var(--text-dim);
}}

/* ===== SECTION ===== */
section {{
    padding: 48px 0;
    border-bottom: 1px solid rgba(212,175,55,0.1);
}}

section:last-child {{ border-bottom: none; }}

.section-title {{
    font-family: var(--font-heading);
    font-size: 1.7em;
    color: var(--gold);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
}}

.section-title .arabic-inline {{
    font-family: var(--font-arabic);
    font-size: 0.8em;
    color: var(--gold-light);
    direction: rtl;
}}

.section-desc {{
    color: var(--text-dim);
    margin-bottom: 24px;
    font-size: 1.05em;
    max-width: 800px;
}}

/* ===== KEY STATS ===== */
.key-stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin: 24px 0;
}}

.stat-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
}}

.stat-value {{
    font-size: 2.2em;
    font-weight: 700;
    color: var(--gold);
    line-height: 1.2;
}}

.stat-label {{
    font-size: 0.85em;
    color: var(--text-dim);
    margin-top: 4px;
}}

/* ===== CARDS ===== */
.card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 16px;
}}

.dim-card {{
    border-left: 4px solid var(--gold);
}}

.dim-header {{
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 8px;
}}

.dim-arabic {{
    font-family: var(--font-arabic);
    font-size: 1.3em;
    color: var(--gold-light);
    direction: rtl;
}}

.dim-title {{
    font-size: 1.2em;
    color: var(--gold);
    margin: 0;
}}

.dim-quran {{
    font-style: italic;
    color: var(--amber);
    font-size: 0.9em;
    margin-bottom: 10px;
    padding-left: 16px;
    border-left: 2px solid rgba(212,175,55,0.3);
}}

.dim-desc {{
    color: var(--text-dim);
    font-size: 0.95em;
    margin-bottom: 12px;
}}

.dim-avg {{
    color: var(--text);
    font-size: 0.95em;
    margin-bottom: 10px;
}}

.dim-leaders {{
    display: flex;
    flex-direction: column;
    gap: 6px;
}}

.dim-top, .dim-bottom {{
    font-size: 0.9em;
    color: var(--text-dim);
}}

.badge-good {{
    display: inline-block;
    background: rgba(39,174,96,0.2);
    color: #27ae60;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
    font-weight: 600;
    margin-right: 6px;
}}

.badge-bad {{
    display: inline-block;
    background: rgba(231,76,60,0.2);
    color: #e74c3c;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
    font-weight: 600;
    margin-right: 6px;
}}

/* ===== TABLE ===== */
.table-wrapper {{
    overflow-x: auto;
    margin: 20px 0;
    border-radius: 12px;
    border: 1px solid var(--border);
}}

table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9em;
}}

thead th {{
    background: var(--surface2);
    color: var(--gold);
    padding: 12px 8px;
    text-align: center;
    font-weight: 600;
    border-bottom: 2px solid var(--gold-dim);
    white-space: nowrap;
    font-size: 0.85em;
}}

thead th:nth-child(2) {{ text-align: left; }}

tbody td {{
    padding: 10px 8px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    color: var(--text);
}}

tbody tr:hover {{ background: rgba(212,175,55,0.05); }}

.dim-cell {{
    text-align: center;
    font-family: var(--font-mono);
    font-size: 0.9em;
}}

/* ===== RADAR CHARTS ===== */
.radar-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin: 20px 0;
}}

.radar-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
}}

.radar-box h3 {{
    color: var(--gold);
    margin-bottom: 12px;
    font-size: 1.1em;
}}

/* ===== GRADE DISTRIBUTION ===== */
.grade-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
}}

.grade-label {{
    width: 100px;
    font-weight: 600;
    font-size: 0.9em;
    text-align: right;
}}

.grade-bar-track {{
    flex: 1;
    height: 24px;
    background: rgba(255,255,255,0.04);
    border-radius: 4px;
    overflow: hidden;
}}

.grade-bar-fill {{
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s;
}}

.grade-count {{
    width: 100px;
    font-size: 0.85em;
    color: var(--text-dim);
}}

/* ===== IMBALANCE BARS ===== */
.imbalance-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
}}

.imbalance-label {{
    width: 280px;
    font-size: 0.9em;
    color: var(--text);
    text-align: right;
}}

.imbalance-bar-track {{
    flex: 1;
    height: 20px;
    background: rgba(255,255,255,0.04);
    border-radius: 4px;
    overflow: hidden;
}}

.imbalance-bar-fill {{
    height: 100%;
    border-radius: 4px;
}}

.imbalance-value {{
    width: 120px;
    font-size: 0.85em;
    font-family: var(--font-mono);
}}

/* ===== COUNTRY BARS ===== */
.country-bar-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 6px;
}}

.country-bar-name {{
    width: 200px;
    font-size: 0.9em;
    color: var(--text);
    text-align: right;
}}

.country-bar-track {{
    flex: 1;
    height: 22px;
    background: rgba(255,255,255,0.04);
    border-radius: 4px;
    overflow: hidden;
}}

.country-bar-fill {{
    height: 100%;
    border-radius: 4px;
}}

.country-bar-score {{
    width: 50px;
    font-weight: 700;
    font-family: var(--font-mono);
    font-size: 0.95em;
}}

/* ===== POLICY ===== */
.policy-list {{
    list-style: none;
    padding: 0;
}}

.policy-item {{
    padding: 12px 16px;
    margin-bottom: 10px;
    background: var(--surface);
    border-left: 4px solid var(--gold);
    border-radius: 0 8px 8px 0;
    font-size: 0.95em;
    line-height: 1.6;
}}

/* ===== METHODOLOGY ===== */
.method-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88em;
    margin: 16px 0;
}}

.method-table th {{
    background: var(--surface2);
    color: var(--gold);
    padding: 10px;
    text-align: left;
    border-bottom: 2px solid var(--gold-dim);
}}

.method-table td {{
    padding: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    vertical-align: top;
}}

.method-table tr:hover {{ background: rgba(212,175,55,0.05); }}

/* ===== FOOTER ===== */
footer {{
    text-align: center;
    padding: 40px 0;
    color: var(--text-dim);
    font-size: 0.85em;
    border-top: 1px solid var(--gold-dim);
}}

footer .bismillah {{
    font-family: var(--font-arabic);
    font-size: 1.4em;
    color: var(--gold);
    margin-bottom: 12px;
    direction: rtl;
}}

/* ===== RESPONSIVE ===== */
@media (max-width: 768px) {{
    .hero-title {{ font-size: 1.8em; }}
    .hero-arabic {{ font-size: 1.6em; }}
    .radar-grid {{ grid-template-columns: 1fr; }}
    .key-stats {{ grid-template-columns: 1fr 1fr; }}
    .imbalance-label {{ width: 180px; }}
    .country-bar-name {{ width: 120px; }}
    thead th {{ font-size: 0.75em; padding: 8px 4px; }}
    tbody td {{ padding: 8px 4px; font-size: 0.8em; }}
}}
</style>
</head>
<body>
<div class="container">

<!-- ═══════════════════════════════════════════ HERO ═══════════════════ -->
<header class="hero">
  <div class="hero-arabic">
    وَالسَّمَاءَ رَفَعَهَا وَوَضَعَ الْمِيزَانَ &#x200F;* أَلَّا تَطْغَوْا فِي الْمِيزَانِ
  </div>
  <p class="hero-verse">
    "And the sky He raised, and He set up the Balance &mdash;
    that you may not transgress the Balance."<br>
    <span style="color:var(--text-dim);font-size:0.9em;">— Quran 55:7-8 (Surah al-Rahman, "The Most Merciful")</span>
  </p>
  <h1 class="hero-title">The Mizan Index</h1>
  <p class="hero-subtitle">
    Measuring Research Justice Through the Lens of Balance &mdash;
    a 7-dimension composite score for 20 African nations, inspired by
    the Quranic imperative that justice requires measurement.
  </p>
  <div class="hero-meta">
    ClinicalTrials.gov API v2 &middot; {len(COUNTRIES)} countries &middot; {date_str}
  </div>
</header>

<!-- ═════════════════════════════════ CONCEPT ═════════════════════════ -->
<section>
  <h2 class="section-title">
    The Concept
    <span class="arabic-inline">المفهوم</span>
  </h2>
  <p class="section-desc">
    The Quran repeatedly emphasizes that justice requires measurement &mdash;
    scales must be fair, weights must be honest. "Woe to those who give
    short measure, who exact full measure when they receive from others but
    give less when they measure or weigh for them" (83:1-3). This project
    applies that principle to clinical research: <strong>you cannot fix
    injustice you have not measured</strong>.
  </p>
  <p class="section-desc">
    The Mizan Index operationalizes 7 dimensions of research justice
    drawn from the Maqasid al-Shariah (objectives of Islamic law) and
    maps them to measurable indicators from ClinicalTrials.gov data.
    Each dimension scores 0 to {MAX_DIM:.1f}, yielding a composite 0-100
    index. Higher scores indicate a more <em>balanced</em> &mdash; more
    <em>just</em> &mdash; research ecosystem.
  </p>
  <p class="section-desc" style="color:var(--amber);">
    "Whoever saves a life, it is as if they saved all of mankind" (5:32)
    &mdash; every missing trial represents lives that could have been saved.
  </p>
</section>

<!-- ═════════════════════════════ KEY STATS ═══════════════════════════ -->
<section>
  <h2 class="section-title">Continental Overview</h2>
  <div class="key-stats">
    <div class="stat-box">
      <div class="stat-value">{avg_score}</div>
      <div class="stat-label">Average Mizan Index (of 100)</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" style="color:#27ae60;">{best_data['mizan_index']}</div>
      <div class="stat-label">Highest: {best_country}</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" style="color:#e74c3c;">{worst_data['mizan_index']}</div>
      <div class="stat-label">Lowest: {worst_country}</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" style="font-size:1.6em;">{dim_names[weakest_dim].split('(')[0].strip()}</div>
      <div class="stat-label">Weakest Dimension (avg {dim_averages[weakest_dim]:.1f})</div>
    </div>
  </div>
</section>

<!-- ══════════════════════════ 7 DIMENSIONS ═══════════════════════════ -->
<section>
  <h2 class="section-title">
    The 7 Dimensions of Research Justice
    <span class="arabic-inline">سبعة أبعاد</span>
  </h2>
  <p class="section-desc">
    Each dimension is inspired by a foundational principle of justice in Islamic
    ethics and mapped to a measurable indicator from clinical trial registry data.
    Each scores 0 to {MAX_DIM:.1f}, totalling 0-100.
  </p>
  {dimension_sections}
</section>

<!-- ═════════════════════════ SCORECARD TABLE ═════════════════════════ -->
<section>
  <h2 class="section-title">
    The Scorecard
    <span class="arabic-inline">بطاقة الأداء</span>
  </h2>
  <p class="section-desc">
    20 African countries ranked by their Mizan Index, with 7-dimension breakdown.
    Each dimension scored 0-{MAX_DIM:.1f}; total 0-100.
  </p>
  <div class="table-wrapper">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th style="text-align:left;">Country</th>
          <th title="Hifz al-Nafs: Protection of Life">D1<br>Life</th>
          <th title="Adl: Justice/Equity">D2<br>Justice</th>
          <th title="Istikhlaf: Stewardship">D3<br>Steward</th>
          <th title="Shura: Consultation">D4<br>Voice</th>
          <th title="La Darar: No Harm">D5<br>No Harm</th>
          <th title="Ilm: Knowledge">D6<br>Knowledge</th>
          <th title="Amanah: Trust">D7<br>Trust</th>
          <th>Mizan<br>Index</th>
          <th>Grade</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>
</section>

<!-- ═══════════════════════ VISUAL RANKING ════════════════════════════ -->
<section>
  <h2 class="section-title">Visual Ranking</h2>
  {country_bars}
</section>

<!-- ═══════════════════════ RADAR CHARTS ══════════════════════════════ -->
<section>
  <h2 class="section-title">
    Balance Profiles
    <span class="arabic-inline">ملامح التوازن</span>
  </h2>
  <p class="section-desc">
    Radar charts reveal where each country's research ecosystem is
    balanced and where it tilts. A perfectly just system would fill
    the entire heptagon.
  </p>
  <div class="radar-grid">
    <div class="radar-box">
      <h3>Top 3 Countries</h3>
      {radar_top3}
    </div>
    <div class="radar-box">
      <h3>Bottom 3 Countries</h3>
      {radar_bottom3}
    </div>
  </div>
</section>

<!-- ═══════════════════ MOST UNJUST IMBALANCES ═══════════════════════ -->
<section>
  <h2 class="section-title">
    The Most Unjust Imbalances
    <span class="arabic-inline">أشد الاختلالات</span>
  </h2>
  <p class="section-desc">
    Which dimension of research justice scores lowest across Africa?
    The bars below rank all 7 dimensions by their continental average,
    revealing where the scales of justice are most tilted.
  </p>
  {imbalance_rows}
  <div class="card" style="margin-top:20px;border-left:4px solid var(--accent-red);">
    <p style="color:var(--text);font-size:1.05em;">
      <strong style="color:var(--accent-red);">The deepest imbalance:</strong>
      {dim_names[weakest_dim]} averages just {dim_averages[weakest_dim]:.1f} out of {MAX_DIM:.1f}
      &mdash; meaning the continental research ecosystem is most deficient in
      <em>{dim_names[weakest_dim].split('(')[1].rstrip(')')}</em>.
    </p>
  </div>
</section>

<!-- ══════════════════════ GRADE DISTRIBUTION ════════════════════════ -->
<section>
  <h2 class="section-title">Grade Distribution</h2>
  {grade_bar_html}
</section>

<!-- ══════════════════════ POLICY IMPLICATIONS ═══════════════════════ -->
<section>
  <h2 class="section-title">
    Policy Implications
    <span class="arabic-inline">التداعيات</span>
  </h2>
  <p class="section-desc">
    "O you who believe, be persistently standing firm in justice,
    witnesses for Allah, even if it be against yourselves" (4:135).
    The Mizan Index does not merely describe injustice &mdash; it demands response.
  </p>
  <ul class="policy-list">
    {policy_html}
    <li class="policy-item">
      <strong>The Balance Imperative:</strong> The Quran warns against short
      measure (83:1-3). When Africa bears 25% of the global disease burden but
      receives a fraction of clinical trials, the scales are broken. The Mizan
      Index provides a framework for measuring progress toward repair.
    </li>
    <li class="policy-item">
      <strong>From measurement to action:</strong> Each dimension suggests
      a specific intervention &mdash; local funding for Istikhlaf, Phase 1 unit
      investment for Ilm, community advisory boards for Shura, results-reporting
      mandates for Amanah, and burden-aligned funding for Hifz al-Nafs.
    </li>
  </ul>
</section>

<!-- ══════════════════════ METHODOLOGY ════════════════════════════════ -->
<section>
  <h2 class="section-title">
    Methodology
    <span class="arabic-inline">المنهجية</span>
  </h2>
  <p class="section-desc">
    Data sourced from ClinicalTrials.gov API v2 on {date_str}.
    Up to 200 sample trials per country were retrieved for sponsor and
    status classification. Population data from UN World Population
    Prospects 2024. WHO burden rankings from Global Health Estimates 2024.
  </p>
  <div class="table-wrapper">
    <table class="method-table">
      <thead>
        <tr>
          <th>Dimension</th>
          <th>Islamic Principle</th>
          <th>Indicator</th>
          <th>Scoring Logic</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>D1: Hifz al-Nafs</td>
          <td>Protection of Life</td>
          <td>Alignment between trial portfolio and top-3 causes of death</td>
          <td>60% match count (0-3 of top-3) + 40% trial concentration on burden diseases</td>
        </tr>
        <tr>
          <td>D2: Adl</td>
          <td>Justice / Equity</td>
          <td>Per-capita trial rate vs global median</td>
          <td>Logarithmic scale: ratio of country density to global median (62.5/million)</td>
        </tr>
        <tr>
          <td>D3: Istikhlaf</td>
          <td>Stewardship / Sovereignty</td>
          <td>% of trials led by local institutions</td>
          <td>Keyword-based sponsor classification; tiered scoring up to 50%+</td>
        </tr>
        <tr>
          <td>D4: Shura</td>
          <td>Consultation / Community Voice</td>
          <td>% of Phase NA (implementation/behavioral) trials</td>
          <td>Higher non-drug trial share indicates community-engaged research</td>
        </tr>
        <tr>
          <td>D5: La Darar</td>
          <td>No Harm</td>
          <td>Inverse of ghost enrollment rate (trials with 50+ global sites)</td>
          <td>Fewer mega-trial enrollments = higher protection from exploitation</td>
        </tr>
        <tr>
          <td>D6: Ilm</td>
          <td>Knowledge / Capacity</td>
          <td>Phase 1 trial count + unique local sponsor count</td>
          <td>50% Phase 1 sovereignty + 50% institutional diversity</td>
        </tr>
        <tr>
          <td>D7: Amanah</td>
          <td>Trust / Accountability</td>
          <td>Inverse of UNKNOWN status rate + completion rate</td>
          <td>70% transparency (non-unknown) + 30% completion bonus</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="card">
    <h3 style="color:var(--gold);margin-bottom:8px;">Limitations</h3>
    <p style="color:var(--text-dim);font-size:0.9em;">
      1) ClinicalTrials.gov captures primarily US-regulated trials; African-initiated
      studies on national registries (e.g., PACTR) may be undercounted.
      2) Sponsor classification relies on keyword matching of the lead sponsor name
      and may misclassify some institutions.
      3) Phase NA as a proxy for community engagement is imperfect; some NA-phase
      trials are device or diagnostic studies.
      4) Ghost enrollment thresholds (50+ sites) are heuristic.
      5) Sample of up to 200 trials per country may not represent the full portfolio.
      6) The 7-dimension framework is normative; alternative weightings would yield
      different rankings.
    </p>
  </div>
</section>

</div><!-- /.container -->

<!-- ═══════════════════════ FOOTER ════════════════════════════════════ -->
<footer>
  <div class="bismillah">بسم الله الرحمن الرحيم</div>
  <p>
    The Mizan Index &middot; Measuring Research Justice Through the Lens of Balance<br>
    Data: ClinicalTrials.gov API v2 &middot; {date_str}<br>
    "And weigh with an even balance" (Quran 55:9)
  </p>
</footer>

</body>
</html>"""

    return html


# ── Main ──────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("THE MIZAN INDEX")
    print("Measuring Research Justice Through the Lens of Balance")
    print("=" * 60)

    # Check cache
    if is_cache_valid():
        print("\n  Using cached data (< 24h old)")
        all_data = load_cache()
    else:
        all_data = collect_all_data()
        save_cache(all_data)

    # Compute scores
    print("\n\nComputing Mizan Index scores...")
    scores = compute_all_scores(all_data)

    # Print summary
    ranked = sorted(scores.items(), key=lambda x: x[1]["mizan_index"], reverse=True)
    print(f"\n{'='*60}")
    print(f"{'MIZAN INDEX RESULTS':^60}")
    print(f"{'='*60}")
    print(f"\n{'Rank':<6}{'Country':<30}{'Index':<8}{'Grade'}")
    print("-" * 54)
    for i, (c, s) in enumerate(ranked, 1):
        print(f"{i:<6}{c:<30}{s['mizan_index']:<8.1f}{s['grade']}")

    # Dimension averages
    dims = ["d1", "d2", "d3", "d4", "d5", "d6", "d7"]
    dim_full = {
        "d1": "Hifz al-Nafs", "d2": "Adl", "d3": "Istikhlaf",
        "d4": "Shura", "d5": "La Darar", "d6": "Ilm", "d7": "Amanah",
    }
    print(f"\n{'Dimension Averages':^60}")
    print("-" * 54)
    for d in dims:
        avg = sum(s[d]["score"] for s in scores.values()) / len(scores)
        print(f"  {dim_full[d]:<25} {avg:.1f} / {MAX_DIM:.1f}")

    # Generate HTML
    print("\n\nGenerating HTML dashboard...")
    html = generate_html(scores, all_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written to {OUTPUT_HTML}")
    print(f"  File size: {os.path.getsize(OUTPUT_HTML):,} bytes")

    # Save scores to data file too
    scores_output = CACHE_FILE.parent / "mizan_index_scores.json"
    with open(scores_output, "w", encoding="utf-8") as f:
        json.dump({"meta": {"date": datetime.now().isoformat()}, "scores": scores},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"  Scores saved to {scores_output}")

    print(f"\n{'='*60}")
    print("Done. Open mizan-index.html in a browser to view the dashboard.")


if __name__ == "__main__":
    main()
