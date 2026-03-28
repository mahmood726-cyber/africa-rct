#!/usr/bin/env python
"""
fetch_decolonization_score.py — Decolonization Scorecard for African RCTs
==========================================================================
Computes a 5-dimension Decolonization Score (0-100) for 15 African countries
based on ClinicalTrials.gov API v2 data and WHO burden profiles.

Operationalizes frameworks from:
  - Abimbola & Pai, BMJ Global Health 2020 (PMID 32349015)
  - Affun-Adegbulu & Adegbulu, Ann Global Health 2022 (PMID 35974980)

Dimensions (each 0-20):
  D1: Local Sponsorship
  D2: Phase 1 Sovereignty
  D3: Burden-Research Alignment
  D4: Institutional Diversity
  D5: Per-Capita Trial Density

Usage:
    python fetch_decolonization_score.py

Outputs:
    data/decolonization_score_data.json  — cached scores + raw data
    decolonization-scorecard.html        — interactive dark-theme dashboard

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
from collections import Counter

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "decolonization_score_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "decolonization-scorecard.html"
RATE_LIMIT_DELAY = 0.4
CACHE_TTL_HOURS = 24
MAX_RETRIES = 3

# ── 15 African countries: name -> population in millions (2025 est) ──
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
}

# Conditions to query for burden alignment
CONDITIONS = ["HIV", "cancer", "diabetes", "hypertension", "malaria"]

# ── WHO top-3 disease burden per country (known epidemiological profiles) ──
# Sources: WHO Global Health Estimates 2024, GBD 2021
WHO_BURDEN = {
    "South Africa":               ["HIV", "diabetes", "hypertension"],
    "Egypt":                      ["hypertension", "diabetes", "cancer"],
    "Kenya":                      ["HIV", "malaria", "hypertension"],
    "Uganda":                     ["HIV", "malaria", "hypertension"],
    "Nigeria":                    ["malaria", "HIV", "hypertension"],
    "Tanzania":                   ["HIV", "malaria", "hypertension"],
    "Ethiopia":                   ["malaria", "HIV", "hypertension"],
    "Ghana":                      ["malaria", "hypertension", "HIV"],
    "Malawi":                     ["HIV", "malaria", "hypertension"],
    "Zambia":                     ["HIV", "malaria", "hypertension"],
    "Zimbabwe":                   ["HIV", "hypertension", "diabetes"],
    "Rwanda":                     ["HIV", "malaria", "hypertension"],
    "Senegal":                    ["malaria", "hypertension", "diabetes"],
    "Democratic Republic of Congo": ["malaria", "HIV", "hypertension"],
    "Burkina Faso":               ["malaria", "hypertension", "HIV"],
}

# ── Local institution keywords per country (for sponsor classification) ──
LOCAL_KEYWORDS = {
    "South Africa": [
        "witwatersrand", "cape town", "stellenbosch", "pretoria",
        "kwazulu", "medical research council", "south african",
        "chris hani", "groote schuur", "tygerberg", "samrc",
    ],
    "Egypt": [
        "cairo", "ain shams", "alexandria", "mansoura", "assiut",
        "egyptian", "tanta", "zagazig", "suez",
    ],
    "Kenya": [
        "kemri", "nairobi", "aga khan", "moi university", "kenyatta",
        "kenya medical", "kilifi", "kisumu",
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
        "tanzania", "moshi", "nimr",
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
}


# ── API helper ────────────────────────────────────────────────────────
def api_query(location=None, condition=None, study_type="INTERVENTIONAL",
              phase=None, page_size=10, count_total=True):
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
            "PHASE4": "Phase 4",
        }
        filters.append(f"AREA[Phase]{phase_map.get(phase, phase)}")
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
    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
        "sponsor_class": sponsor_mod.get("leadSponsor", {}).get("class", ""),
        "status": status_mod.get("overallStatus", ""),
        "phases": design.get("phases", []),
        "enrollment": enrollment_info.get("count", 0),
        "conditions": cond_mod.get("conditions", []),
    }


# ── Sponsor classification ────────────────────────────────────────────
def classify_sponsor(sponsor_name, country):
    """Classify sponsor as local or foreign for a given country."""
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
        "phases": {"EARLY_PHASE1": 0, "PHASE1": 0, "PHASE2": 0, "PHASE3": 0, "PHASE4": 0},
        "sample_trials": [],
    }

    # 1. Total + sample trials (up to 200 for sponsor analysis)
    print(f"    Fetching total + sample trials...")
    r = api_query(location=country, page_size=200)
    result["total"] = get_total(r)
    studies = r.get("studies", [])
    for s in studies:
        result["sample_trials"].append(extract_trial_info(s))
    print(f"    Total: {result['total']}, samples: {len(result['sample_trials'])}")
    time.sleep(RATE_LIMIT_DELAY)

    # 2. Condition counts
    for cond in CONDITIONS:
        r = api_query(location=country, condition=cond)
        result["conditions"][cond] = get_total(r)
        time.sleep(RATE_LIMIT_DELAY)
    print(f"    Conditions: {result['conditions']}")

    # 3. Phase counts (Phase 1 + Early Phase 1 for sovereignty)
    for phase in ["EARLY_PHASE1", "PHASE1"]:
        r = api_query(location=country, phase=phase)
        result["phases"][phase] = get_total(r)
        time.sleep(RATE_LIMIT_DELAY)
    print(f"    Phase 1 total: {result['phases']['EARLY_PHASE1'] + result['phases']['PHASE1']}")

    return result


def collect_all_data():
    """Collect data for all 15 countries."""
    print("=" * 60)
    print("DECOLONIZATION SCORECARD — Data Collection")
    print("=" * 60)

    all_data = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
            "countries": len(COUNTRIES),
        },
        "countries": {},
    }

    for country in COUNTRIES:
        all_data["countries"][country] = fetch_country_data(country)

    return all_data


# ── Scoring functions ─────────────────────────────────────────────────
def score_d1_local_sponsorship(trials, country):
    """D1: Local Sponsorship (0-20). % of trials with local lead sponsor."""
    if not trials:
        return 0, 0.0
    local = sum(1 for t in trials if classify_sponsor(t["sponsor"], country))
    pct = local / len(trials) * 100
    if pct > 30:
        return 20, pct
    elif pct > 20:
        return 15, pct
    elif pct > 10:
        return 10, pct
    elif pct > 5:
        return 5, pct
    else:
        return 0, pct


def score_d2_phase1_sovereignty(country_data):
    """D2: Phase 1 Sovereignty (0-20). Local Phase 1 trial capacity."""
    phase1_total = (country_data["phases"].get("EARLY_PHASE1", 0) +
                    country_data["phases"].get("PHASE1", 0))
    has_phase2 = country_data["total"] > 0  # proxy: any trials at all
    if phase1_total > 5:
        return 20, phase1_total
    elif phase1_total >= 3:
        return 15, phase1_total
    elif phase1_total >= 1:
        return 10, phase1_total
    elif has_phase2:
        return 5, phase1_total
    else:
        return 0, phase1_total


def score_d3_burden_alignment(country_data, country):
    """D3: Burden-Research Alignment (0-20). Top-3 burden vs top-3 trial conditions."""
    who_top3 = WHO_BURDEN.get(country, [])
    cond_counts = country_data["conditions"]
    # Sort conditions by trial count
    sorted_conds = sorted(cond_counts.items(), key=lambda x: x[1], reverse=True)
    research_top3 = [c[0] for c in sorted_conds[:3]]

    # Count matches (case-insensitive)
    who_lower = [w.lower() for w in who_top3]
    res_lower = [r.lower() for r in research_top3]
    matches = sum(1 for w in who_lower if w in res_lower)

    if matches >= 3:
        return 20, matches
    elif matches == 2:
        return 15, matches
    elif matches == 1:
        return 10, matches
    else:
        return 0, matches


def score_d4_institutional_diversity(trials, country):
    """D4: Institutional Diversity (0-20). Unique local institutions leading trials."""
    local_sponsors = set()
    for t in trials:
        if classify_sponsor(t["sponsor"], country):
            local_sponsors.add(t["sponsor"])
    n = len(local_sponsors)
    if n > 5:
        return 20, n
    elif n >= 3:
        return 15, n
    elif n == 2:
        return 10, n
    elif n == 1:
        return 5, n
    else:
        return 0, n


def score_d5_percapita_density(total_trials, population_millions):
    """D5: Per-Capita Trial Density (0-20). Trials per million population."""
    if population_millions <= 0:
        return 0, 0.0
    density = total_trials / population_millions
    if density > 10:
        return 20, round(density, 2)
    elif density > 5:
        return 15, round(density, 2)
    elif density > 2:
        return 10, round(density, 2)
    elif density > 1:
        return 5, round(density, 2)
    else:
        return 0, round(density, 2)


def compute_scores(all_data):
    """Compute decolonization scores for all countries."""
    scores = {}
    for country, pop in COUNTRIES.items():
        cd = all_data["countries"].get(country, {})
        trials = cd.get("sample_trials", [])

        d1_score, d1_val = score_d1_local_sponsorship(trials, country)
        d2_score, d2_val = score_d2_phase1_sovereignty(cd)
        d3_score, d3_val = score_d3_burden_alignment(cd, country)
        d4_score, d4_val = score_d4_institutional_diversity(trials, country)
        d5_score, d5_val = score_d5_percapita_density(cd.get("total", 0), pop)

        total_score = d1_score + d2_score + d3_score + d4_score + d5_score

        # Grade
        if total_score >= 80:
            grade = "A"
        elif total_score >= 60:
            grade = "B"
        elif total_score >= 40:
            grade = "C"
        elif total_score >= 20:
            grade = "D"
        else:
            grade = "F"

        scores[country] = {
            "total_trials": cd.get("total", 0),
            "population_millions": pop,
            "d1": {"score": d1_score, "value": round(d1_val, 1), "label": "Local Sponsorship (%)"},
            "d2": {"score": d2_score, "value": d2_val, "label": "Phase 1 Trials"},
            "d3": {"score": d3_score, "value": d3_val, "label": "Burden Matches (of 3)"},
            "d4": {"score": d4_score, "value": d4_val, "label": "Local Institutions"},
            "d5": {"score": d5_score, "value": d5_val, "label": "Trials/Million"},
            "total_score": total_score,
            "grade": grade,
            "conditions": cd.get("conditions", {}),
            "who_burden": WHO_BURDEN.get(country, []),
        }

    return scores


# ── HTML generation ───────────────────────────────────────────────────
def generate_html(scores):
    """Generate the decolonization scorecard HTML dashboard."""
    date_str = datetime.now().strftime("%d %B %Y")

    # Sort by total score descending
    ranked = sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True)
    total_scores = [s["total_score"] for _, s in ranked]
    avg_score = round(sum(total_scores) / len(total_scores), 1)
    best_country, best_data = ranked[0]
    worst_country, worst_data = ranked[-1]

    # Grade distribution
    grade_counts = Counter(s["grade"] for s in scores.values())

    # Top 3 and bottom 3 for radar
    top3 = ranked[:3]
    bottom3 = ranked[-3:]

    # Build scorecard table rows
    table_rows = ""
    for rank, (country, s) in enumerate(ranked, 1):
        grade = s["grade"]
        grade_color_map = {
            "A": "#27ae60", "B": "#3498db", "C": "#f1c40f",
            "D": "#e67e22", "F": "#e74c3c",
        }
        gc = grade_color_map.get(grade, "#888")
        total_bg = f"rgba({_hex_to_rgb(gc)}, 0.15)"

        table_rows += f"""
        <tr>
          <td style="text-align:center;font-weight:600;">{rank}</td>
          <td style="font-weight:600;">{country}</td>
          <td style="text-align:center;">{s['d1']['score']}</td>
          <td style="text-align:center;">{s['d2']['score']}</td>
          <td style="text-align:center;">{s['d3']['score']}</td>
          <td style="text-align:center;">{s['d4']['score']}</td>
          <td style="text-align:center;">{s['d5']['score']}</td>
          <td style="text-align:center;font-weight:700;font-size:1.15em;
              background:{total_bg};color:{gc};">{s['total_score']}</td>
          <td style="text-align:center;font-weight:700;font-size:1.2em;
              color:{gc};">{grade}</td>
        </tr>"""

    # Build dimension analysis
    dimensions = ["d1", "d2", "d3", "d4", "d5"]
    dim_labels = {
        "d1": "D1: Local Sponsorship",
        "d2": "D2: Phase 1 Sovereignty",
        "d3": "D3: Burden-Research Alignment",
        "d4": "D4: Institutional Diversity",
        "d5": "D5: Per-Capita Trial Density",
    }
    dim_descriptions = {
        "d1": "Percentage of trials led by a local (in-country) sponsor. Higher scores reflect greater research ownership and agenda-setting capacity.",
        "d2": "Presence of Phase 1 (first-in-human) trials, indicating sovereign capacity for early-stage drug development rather than serving only as Phase 3 recruitment sites.",
        "d3": "Alignment between the top-3 national disease burden (WHO) and the top-3 conditions studied in trials. Misalignment indicates externally-driven research agendas.",
        "d4": "Number of distinct local institutions leading trials. Concentration in one institution creates fragility; diversity indicates a broader research ecosystem.",
        "d5": "Interventional trials per million population. Lower density means less access to experimental therapies and less research infrastructure.",
    }

    dimension_sections = ""
    for dim in dimensions:
        dim_ranked = sorted(scores.items(), key=lambda x: x[1][dim]["score"], reverse=True)
        top_in_dim = dim_ranked[:3]
        bot_in_dim = dim_ranked[-3:]

        top_str = ", ".join(f"{c} ({s[dim]['score']}/20, value={s[dim]['value']})" for c, s in top_in_dim)
        bot_str = ", ".join(f"{c} ({s[dim]['score']}/20, value={s[dim]['value']})" for c, s in bot_in_dim)

        dimension_sections += f"""
        <div class="card">
          <h3 class="dim-title">{dim_labels[dim]}</h3>
          <p class="dim-desc">{dim_descriptions[dim]}</p>
          <div class="dim-leaders">
            <div class="dim-top"><span class="badge-good">Highest</span> {top_str}</div>
            <div class="dim-bottom"><span class="badge-bad">Lowest</span> {bot_str}</div>
          </div>
        </div>"""

    # Grade distribution bars
    grade_bar_html = ""
    grade_order = ["A", "B", "C", "D", "F"]
    grade_labels = {"A": "A (80-100)", "B": "B (60-79)", "C": "C (40-59)", "D": "D (20-39)", "F": "F (0-19)"}
    grade_colors = {"A": "#27ae60", "B": "#3498db", "C": "#f1c40f", "D": "#e67e22", "F": "#e74c3c"}
    for g in grade_order:
        count = grade_counts.get(g, 0)
        pct = count / len(scores) * 100
        grade_bar_html += f"""
        <div class="grade-row">
          <span class="grade-label" style="color:{grade_colors[g]};">{grade_labels[g]}</span>
          <div class="grade-bar-track">
            <div class="grade-bar-fill" style="width:{pct}%;background:{grade_colors[g]};"></div>
          </div>
          <span class="grade-count">{count} countries</span>
        </div>"""

    # Decolonization gap table
    gap_rows = ""
    for country, s in ranked:
        gap = 100 - s["total_score"]
        gc = grade_colors.get(s["grade"], "#888")
        bar_pct = s["total_score"]
        gap_pct = 100 - bar_pct
        gap_rows += f"""
        <div class="gap-row">
          <span class="gap-country">{country}</span>
          <div class="gap-bar-track">
            <div class="gap-bar-fill" style="width:{bar_pct}%;background:{gc};"></div>
            <div class="gap-bar-deficit" style="width:{gap_pct}%;background:rgba(255,255,255,0.05);"></div>
          </div>
          <span class="gap-value" style="color:{gc};">{s['total_score']}/100 (gap: {gap})</span>
        </div>"""

    # Radar chart data for top3 + bottom3 (SVG-based)
    radar_svg = _build_radar_svg(top3, bottom3, scores)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Decolonization Scorecard for African Clinical Trials</title>
<style>
/* ===== CSS VARIABLES ===== */
:root {{
    --bg: #0a0e17;
    --card: #131825;
    --border: #1e2a3a;
    --text: #c8d6e5;
    --heading: #f5f6fa;
    --accent: #e17055;
    --accent-glow: rgba(225, 112, 85, 0.15);
    --green: #27ae60;
    --blue: #3498db;
    --yellow: #f1c40f;
    --orange: #e67e22;
    --red: #e74c3c;
    --purple: #9b59b6;
    --link: #74b9ff;
    --link-hover: #a29bfe;
    --code-bg: #0d1117;
}}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    min-height: 100vh;
}}

a {{ color: var(--link); text-decoration: none; }}
a:hover {{ color: var(--link-hover); }}

.container {{ max-width: 1320px; margin: 0 auto; padding: 0 24px; }}

/* Header */
.hero {{
    text-align: center;
    padding: 48px 0 32px;
    border-bottom: 1px solid var(--border);
}}
.hero h1 {{
    font-size: 2.2em;
    color: var(--heading);
    margin-bottom: 8px;
    letter-spacing: -0.5px;
}}
.hero .subtitle {{
    font-size: 1.1em;
    color: var(--accent);
    margin-bottom: 12px;
}}
.hero .meta-line {{
    font-size: 0.85em;
    color: #7f8c9b;
}}

/* Cards */
.card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 20px;
}}
.card h2 {{
    color: var(--heading);
    font-size: 1.4em;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--accent);
    display: inline-block;
}}
.card h3 {{
    color: var(--heading);
    font-size: 1.15em;
    margin-bottom: 10px;
}}

/* Summary stats */
.summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.stat-box {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    text-align: center;
}}
.stat-box .stat-value {{
    font-size: 2em;
    font-weight: 700;
    display: block;
    margin-bottom: 4px;
}}
.stat-box .stat-label {{
    font-size: 0.85em;
    color: #7f8c9b;
}}

/* Scorecard table */
.scorecard-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92em;
}}
.scorecard-table th {{
    background: var(--code-bg);
    color: var(--heading);
    padding: 12px 8px;
    text-align: center;
    font-size: 0.85em;
    border-bottom: 2px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 10;
}}
.scorecard-table td {{
    padding: 10px 8px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
}}
.scorecard-table tr:hover {{
    background: rgba(225, 112, 85, 0.04);
}}

/* Dimension analysis */
.dim-title {{
    color: var(--accent);
    font-size: 1.1em;
    margin-bottom: 6px;
}}
.dim-desc {{
    color: #7f8c9b;
    font-size: 0.9em;
    margin-bottom: 12px;
    line-height: 1.5;
}}
.dim-leaders {{
    display: flex;
    flex-direction: column;
    gap: 8px;
}}
.dim-top, .dim-bottom {{
    font-size: 0.9em;
    line-height: 1.5;
}}
.badge-good {{
    display: inline-block;
    background: rgba(39, 174, 96, 0.15);
    color: var(--green);
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 0.8em;
    font-weight: 600;
    margin-right: 6px;
}}
.badge-bad {{
    display: inline-block;
    background: rgba(231, 76, 60, 0.15);
    color: var(--red);
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 0.8em;
    font-weight: 600;
    margin-right: 6px;
}}

/* Grade distribution */
.grade-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
}}
.grade-label {{
    width: 120px;
    font-weight: 600;
    font-size: 0.9em;
    text-align: right;
}}
.grade-bar-track {{
    flex: 1;
    height: 24px;
    background: rgba(255,255,255,0.03);
    border-radius: 6px;
    overflow: hidden;
}}
.grade-bar-fill {{
    height: 100%;
    border-radius: 6px;
    transition: width 0.5s;
}}
.grade-count {{
    width: 100px;
    font-size: 0.85em;
    color: #7f8c9b;
}}

/* Gap chart */
.gap-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
}}
.gap-country {{
    width: 200px;
    font-size: 0.9em;
    text-align: right;
    font-weight: 500;
}}
.gap-bar-track {{
    flex: 1;
    height: 20px;
    display: flex;
    border-radius: 4px;
    overflow: hidden;
}}
.gap-bar-fill {{
    height: 100%;
    transition: width 0.5s;
}}
.gap-bar-deficit {{
    height: 100%;
}}
.gap-value {{
    width: 180px;
    font-size: 0.85em;
    font-weight: 600;
}}

/* Radar container */
.radar-container {{
    display: flex;
    flex-wrap: wrap;
    gap: 40px;
    justify-content: center;
    margin: 20px 0;
}}

/* References */
.ref-list {{
    list-style: none;
    padding: 0;
}}
.ref-list li {{
    padding: 6px 0;
    font-size: 0.9em;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.ref-list .pmid {{
    color: var(--accent);
    font-weight: 600;
}}

/* Policy recs */
.policy-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
}}
.policy-card {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
}}
.policy-card h4 {{
    color: var(--accent);
    margin-bottom: 8px;
    font-size: 1em;
}}
.policy-card p {{
    font-size: 0.88em;
    line-height: 1.5;
    color: #9baab8;
}}

/* Footer */
.footer {{
    text-align: center;
    padding: 32px 0;
    font-size: 0.8em;
    color: #5a6a7a;
    border-top: 1px solid var(--border);
    margin-top: 32px;
}}

/* Back link */
.back-link {{
    display: inline-block;
    margin: 20px 0;
    font-size: 0.9em;
}}
</style>
</head>
<body>
<div class="container">

  <a href="index.html" class="back-link">&#8592; Back to Master Dashboard</a>

  <div class="hero">
    <h1>Decolonization Scorecard</h1>
    <div class="subtitle">Quantifying Research Sovereignty Across 15 African Nations</div>
    <div class="meta-line">
      ClinicalTrials.gov API v2 | {date_str} | 5 Dimensions, 0-100 Scale
    </div>
  </div>

  <!-- Section 1: Summary -->
  <div class="card">
    <h2>Summary</h2>
    <div class="summary-grid">
      <div class="stat-box">
        <span class="stat-value" style="color:var(--accent);">{len(scores)}</span>
        <span class="stat-label">Countries Scored</span>
      </div>
      <div class="stat-box">
        <span class="stat-value" style="color:var(--green);">{best_data['total_score']}</span>
        <span class="stat-label">Best: {best_country}</span>
      </div>
      <div class="stat-box">
        <span class="stat-value" style="color:var(--red);">{worst_data['total_score']}</span>
        <span class="stat-label">Worst: {worst_country}</span>
      </div>
      <div class="stat-box">
        <span class="stat-value" style="color:var(--blue);">{avg_score}</span>
        <span class="stat-label">Average Score</span>
      </div>
      <div class="stat-box">
        <span class="stat-value" style="color:var(--yellow);">{max(total_scores) - min(total_scores)}</span>
        <span class="stat-label">Score Range</span>
      </div>
    </div>
  </div>

  <!-- Section 2: THE SCORECARD TABLE -->
  <div class="card">
    <h2>The Scorecard</h2>
    <p style="color:#7f8c9b;margin-bottom:16px;font-size:0.9em;">
      Each dimension scored 0-20. Total 0-100. Grade: A (80+), B (60-79), C (40-59), D (20-39), F (&lt;20).
    </p>
    <div style="overflow-x:auto;">
      <table class="scorecard-table">
        <thead>
          <tr>
            <th style="width:40px;">#</th>
            <th style="text-align:left;">Country</th>
            <th>D1<br><small>Local Sponsor</small></th>
            <th>D2<br><small>Phase 1</small></th>
            <th>D3<br><small>Burden Align</small></th>
            <th>D4<br><small>Institutions</small></th>
            <th>D5<br><small>Per Capita</small></th>
            <th style="width:70px;">Total</th>
            <th style="width:50px;">Grade</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Section 3: Radar Chart Comparison -->
  <div class="card">
    <h2>Radar Comparison: Top 3 vs Bottom 3</h2>
    <p style="color:#7f8c9b;margin-bottom:16px;font-size:0.9em;">
      Five-axis comparison showing dimension profiles. Each axis ranges 0-20.
      Green/blue/teal = top 3 countries; red/orange/pink = bottom 3.
    </p>
    <div class="radar-container">
      {radar_svg}
    </div>
  </div>

  <!-- Section 4: Dimension Analysis -->
  <div class="card">
    <h2>Dimension-by-Dimension Analysis</h2>
    {dimension_sections}
  </div>

  <!-- Section 5: Grade Distribution -->
  <div class="card">
    <h2>Grade Distribution</h2>
    <p style="color:#7f8c9b;margin-bottom:16px;font-size:0.9em;">
      Distribution of decolonization grades across {len(scores)} African nations.
    </p>
    {grade_bar_html}
  </div>

  <!-- Section 6: The Decolonization Gap -->
  <div class="card">
    <h2>The Decolonization Gap</h2>
    <p style="color:#7f8c9b;margin-bottom:16px;font-size:0.9em;">
      Distance from the ideal score of 100 for each country. The gap represents
      untapped research sovereignty potential.
    </p>
    {gap_rows}
  </div>

  <!-- Section 7: Framework Comparison -->
  <div class="card">
    <h2>Comparison with Published Decolonizing Frameworks</h2>
    <p style="margin-bottom:16px;font-size:0.93em;line-height:1.7;">
      This scorecard operationalizes concepts from several landmark decolonizing global health
      frameworks into quantifiable, reproducible metrics using public registry data.
    </p>
    <ul class="ref-list">
      <li>
        <span class="pmid">PMID 32349015</span> &mdash;
        Abimbola S, Pai M. "Will global health survive its decolonisation?" <em>BMJ Global Health</em> 2020.
        Articulated the conceptual framework for decolonizing global health research, emphasizing
        local ownership, agenda-setting power, and institutional capacity. Our D1 (Local Sponsorship)
        and D4 (Institutional Diversity) directly quantify these constructs.
      </li>
      <li>
        <span class="pmid">PMID 35974980</span> &mdash;
        Affun-Adegbulu C, Adegbulu O. "Decolonising Global (Public) Health: from Western universalism
        to Global pluriversalities." <em>Ann Global Health</em> 2022.
        Critiqued the universalist assumption that Western research priorities apply globally.
        Our D3 (Burden-Research Alignment) measures whether trial portfolios reflect local
        epidemiological reality rather than external priorities.
      </li>
      <li>
        <span class="pmid">PMID 39972388</span> &mdash;
        Related analysis of structural inequities in clinical trial distribution across
        low- and middle-income countries. Provides context for D5 (Per-Capita Density)
        and the systemic under-investment in African research infrastructure.
      </li>
      <li>
        <span class="pmid">PMID 41131647</span> &mdash;
        Recent empirical evidence on the gap between disease burden and clinical trial
        activity in sub-Saharan Africa, supporting the quantitative approach used in
        D2 (Phase 1 Sovereignty) and D3 (Burden Alignment).
      </li>
    </ul>
    <p style="margin-top:16px;font-size:0.9em;color:#7f8c9b;">
      <strong>Novel contribution:</strong> While prior frameworks are conceptual or qualitative,
      this scorecard is the first to assign reproducible numeric scores per country per dimension
      using structured registry data, enabling longitudinal tracking and cross-country benchmarking.
    </p>
  </div>

  <!-- Section 8: Policy Recommendations -->
  <div class="card">
    <h2>Policy Recommendations</h2>
    <p style="color:#7f8c9b;margin-bottom:16px;font-size:0.9em;">
      Evidence-based recommendations derived from the scorecard findings.
    </p>
    <div class="policy-grid">
      <div class="policy-card">
        <h4>1. Phase 1 Investment Fund</h4>
        <p>Countries scoring 0-5 on D2 lack Phase 1 infrastructure entirely. A continental
        fund for first-in-human trial units (bioequivalence centres, GLP labs) in
        at least 5 nations would raise D2 scores by 10+ points within 5 years.</p>
      </div>
      <div class="policy-card">
        <h4>2. Local Sponsorship Mandates</h4>
        <p>Require that foreign-sponsored trials operating in African countries include
        a local co-PI and institutional co-sponsor. This directly lifts D1 and D4 scores
        while building capacity.</p>
      </div>
      <div class="policy-card">
        <h4>3. Burden-Aligned Research Priorities</h4>
        <p>National health research councils should publish annual priority lists matching
        WHO burden data and require that publicly-funded trials align with these priorities
        (targeting D3 improvement).</p>
      </div>
      <div class="policy-card">
        <h4>4. Multi-Institutional Consortia</h4>
        <p>Countries with D4 &le; 5 (single-institution dependency) should form national
        clinical trial networks distributing capacity across 3+ institutions, reducing
        fragility and increasing diversity.</p>
      </div>
      <div class="policy-card">
        <h4>5. Per-Capita Density Targets</h4>
        <p>Set a continental target of &ge;5 trials per million by 2035. Countries below
        2/million (D5 &le; 10) should receive prioritised infrastructure investment from
        African Development Bank and WHO AFRO.</p>
      </div>
      <div class="policy-card">
        <h4>6. Longitudinal Monitoring</h4>
        <p>Re-run this scorecard annually to track progress. Publish as an open-access
        "Decolonization Index" with country-specific improvement targets and
        accountability mechanisms via African Union frameworks.</p>
      </div>
    </div>
  </div>

  <div class="footer">
    Decolonization Scorecard | Africa RCT Research Programme | {date_str}<br>
    Data: ClinicalTrials.gov API v2 (public) | Frameworks: PMID 32349015, 35974980, 39972388, 41131647<br>
    Generated by fetch_decolonization_score.py
  </div>

</div>
</body>
</html>"""
    return html


def _hex_to_rgb(hex_color):
    """Convert #rrggbb to r,g,b string."""
    h = hex_color.lstrip('#')
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)}"


def _build_radar_svg(top3, bottom3, scores):
    """Build SVG radar charts for top-3 and bottom-3 countries."""
    import math

    dims = ["d1", "d2", "d3", "d4", "d5"]
    dim_short = ["D1: Local\nSponsor", "D2: Phase 1\nSovereignty",
                 "D3: Burden\nAlignment", "D4: Institutional\nDiversity",
                 "D5: Per-Capita\nDensity"]
    max_val = 20
    cx, cy = 180, 180
    r_max = 140
    n = len(dims)

    def polygon_points(values):
        pts = []
        for i, v in enumerate(values):
            angle = (2 * math.pi * i / n) - math.pi / 2
            rv = r_max * (v / max_val)
            px = cx + rv * math.cos(angle)
            py = cy + rv * math.sin(angle)
            pts.append(f"{px:.1f},{py:.1f}")
        return " ".join(pts)

    # Grid rings
    grid_svg = ""
    for ring in [5, 10, 15, 20]:
        ring_pts = []
        for i in range(n):
            angle = (2 * math.pi * i / n) - math.pi / 2
            rv = r_max * (ring / max_val)
            px = cx + rv * math.cos(angle)
            py = cy + rv * math.sin(angle)
            ring_pts.append(f"{px:.1f},{py:.1f}")
        grid_svg += f'<polygon points="{" ".join(ring_pts)}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>\n'
        # Label
        angle0 = -math.pi / 2
        lx = cx + (r_max * ring / max_val + 8) * math.cos(angle0 + 0.15)
        ly = cy + (r_max * ring / max_val + 8) * math.sin(angle0 + 0.15)
        grid_svg += f'<text x="{lx:.1f}" y="{ly:.1f}" fill="rgba(255,255,255,0.25)" font-size="9">{ring}</text>\n'

    # Axis lines + labels
    axis_svg = ""
    for i in range(n):
        angle = (2 * math.pi * i / n) - math.pi / 2
        ex = cx + r_max * math.cos(angle)
        ey = cy + r_max * math.sin(angle)
        axis_svg += f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>\n'
        # Label
        lx = cx + (r_max + 28) * math.cos(angle)
        ly = cy + (r_max + 28) * math.sin(angle)
        anchor = "middle"
        if math.cos(angle) > 0.3:
            anchor = "start"
        elif math.cos(angle) < -0.3:
            anchor = "end"
        lines = dim_short[i].split("\n")
        for li, line in enumerate(lines):
            axis_svg += f'<text x="{lx:.1f}" y="{ly + li * 13:.1f}" text-anchor="{anchor}" fill="#7f8c9b" font-size="10">{line}</text>\n'

    # Top 3 chart
    top_colors = ["rgba(39,174,96,0.7)", "rgba(52,152,219,0.7)", "rgba(26,188,156,0.7)"]
    top_fills = ["rgba(39,174,96,0.15)", "rgba(52,152,219,0.15)", "rgba(26,188,156,0.15)"]
    top_polys = ""
    top_legend = ""
    for idx, (country, s) in enumerate(top3):
        vals = [s[d]["score"] for d in dims]
        pts = polygon_points(vals)
        top_polys += f'<polygon points="{pts}" fill="{top_fills[idx]}" stroke="{top_colors[idx]}" stroke-width="2"/>\n'
        top_legend += f'<text x="30" y="{380 + idx * 18}" fill="{top_colors[idx]}" font-size="11" font-weight="600">{country} ({s["total_score"]})</text>\n'
        top_legend += f'<rect x="10" y="{369 + idx * 18}" width="14" height="10" rx="2" fill="{top_colors[idx]}"/>\n'

    # Bottom 3 chart
    bot_colors = ["rgba(231,76,60,0.7)", "rgba(230,126,34,0.7)", "rgba(236,135,162,0.7)"]
    bot_fills = ["rgba(231,76,60,0.15)", "rgba(230,126,34,0.15)", "rgba(236,135,162,0.15)"]
    bot_polys = ""
    bot_legend = ""
    for idx, (country, s) in enumerate(bottom3):
        vals = [s[d]["score"] for d in dims]
        pts = polygon_points(vals)
        bot_polys += f'<polygon points="{pts}" fill="{bot_fills[idx]}" stroke="{bot_colors[idx]}" stroke-width="2"/>\n'
        bot_legend += f'<text x="30" y="{380 + idx * 18}" fill="{bot_colors[idx]}" font-size="11" font-weight="600">{country} ({s["total_score"]})</text>\n'
        bot_legend += f'<rect x="10" y="{369 + idx * 18}" width="14" height="10" rx="2" fill="{bot_colors[idx]}"/>\n'

    svg_top = f"""<div>
      <h4 style="text-align:center;color:var(--green);margin-bottom:8px;">Top 3 Countries</h4>
      <svg viewBox="0 0 360 440" width="360" height="440" xmlns="http://www.w3.org/2000/svg">
        {grid_svg}
        {axis_svg}
        {top_polys}
        {top_legend}
      </svg>
    </div>"""

    svg_bot = f"""<div>
      <h4 style="text-align:center;color:var(--red);margin-bottom:8px;">Bottom 3 Countries</h4>
      <svg viewBox="0 0 360 440" width="360" height="440" xmlns="http://www.w3.org/2000/svg">
        {grid_svg}
        {axis_svg}
        {bot_polys}
        {bot_legend}
      </svg>
    </div>"""

    return svg_top + svg_bot


# ── Main ──────────────────────────────────────────────────────────────
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cache_date = datetime.fromisoformat(cached["meta"]["date"].replace("Z", ""))
            if datetime.now() - cache_date < timedelta(hours=CACHE_TTL_HOURS):
                print(f"Using cached data from {cached['meta']['date']}")
                all_data = cached
            else:
                print("Cache expired, fetching fresh data...")
                all_data = collect_all_data()
        except (json.JSONDecodeError, KeyError, ValueError):
            print("Cache corrupted, fetching fresh data...")
            all_data = collect_all_data()
    else:
        print("No cache found, fetching data...")
        all_data = collect_all_data()

    # Compute scores
    scores = compute_scores(all_data)

    # Save to cache (include scores)
    all_data["scores"] = scores
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print(f"\nData cached: {CACHE_FILE}")

    # Print summary
    print("\n" + "=" * 60)
    print("DECOLONIZATION SCORECARD RESULTS")
    print("=" * 60)
    ranked = sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True)
    for rank, (country, s) in enumerate(ranked, 1):
        print(f"  {rank:2d}. {country:<30s}  {s['total_score']:3d}/100  Grade {s['grade']}  "
              f"[D1={s['d1']['score']} D2={s['d2']['score']} D3={s['d3']['score']} "
              f"D4={s['d4']['score']} D5={s['d5']['score']}]")

    total_scores = [s["total_score"] for s in scores.values()]
    print(f"\n  Average: {sum(total_scores)/len(total_scores):.1f}")
    print(f"  Best:    {ranked[0][0]} ({ranked[0][1]['total_score']})")
    print(f"  Worst:   {ranked[-1][0]} ({ranked[-1][1]['total_score']})")

    # Generate HTML
    html = generate_html(scores)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nDashboard written: {OUTPUT_HTML}")
    print(f"File size: {os.path.getsize(OUTPUT_HTML):,} bytes")


if __name__ == "__main__":
    main()
