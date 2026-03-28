#!/usr/bin/env python
"""
fetch_diversity_audit.py -- The Hujurat Diversity Audit
========================================================
Inspired by Surah Al-Hujurat (49:13): "O mankind, indeed We have created you
from male and female and made you peoples and tribes that you may know one
another. Indeed, the most noble of you in the sight of Allah is the most
righteous."

Africa has 3,000+ ethnic groups. Clinical trials typically recruit from
capital cities, excluding ethnic diversity. This project audits geographic
and ethnic diversity of trial sites within countries.

For Uganda (783 trials):
  - Classify each trial by location (Kampala vs regional vs rural)
  - Track mentions of ethnic diversity, community engagement, rural populations
  - Single-site (urban hospital) vs multi-site (geographic spread)
  - "Diversity Score" = proportion with non-capital sites
  - "Ethnic Representation Proxy" = trials in ethnically-distinct regions
  - Compare with South Africa and Nigeria

Usage:
    python fetch_diversity_audit.py

Outputs:
    data/diversity_audit_data.json  -- cached API data (24h TTL)
    diversity-audit.html            -- geographic distribution, Kampala Bubble

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
from collections import Counter

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config -------------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_FILE = DATA_DIR / "diversity_audit_data.json"
OUTPUT_HTML = SCRIPT_DIR.parent / "diversity-audit.html"
RATE_LIMIT_DELAY = 0.35
CACHE_TTL_HOURS = 24
MAX_RETRIES = 3

# -- Uganda geographic classification -----------------------------------------
# Regions and their associated ethnic groups / characteristics
UGANDA_REGIONS = {
    "Kampala": {
        "type": "capital",
        "keywords": ["kampala", "mulago", "mengo", "makerere", "kibuli",
                     "nsambya", "naguru", "kololo", "butabika"],
        "ethnic_groups": ["Baganda (dominant)", "mixed urban"],
        "description": "Capital city, major research hub",
    },
    "Central (non-Kampala)": {
        "type": "peri-urban",
        "keywords": ["entebbe", "wakiso", "mukono", "jinja", "masaka",
                     "mpigi", "mityana", "luwero", "kayunga"],
        "ethnic_groups": ["Baganda", "Basoga"],
        "description": "Central region outside Kampala",
    },
    "Western": {
        "type": "regional",
        "keywords": ["mbarara", "kabale", "fort portal", "kasese",
                     "bushenyi", "ishaka", "rukungiri", "hoima",
                     "kibale", "bundibugyo", "ntungamo"],
        "ethnic_groups": ["Banyankole", "Bakiga", "Batoro", "Bakonzo"],
        "description": "Western Uganda, distinct Bantu groups",
    },
    "Northern": {
        "type": "post-conflict",
        "keywords": ["gulu", "lira", "arua", "kitgum", "pader",
                     "soroti", "apac", "adjumani", "moyo", "nebbi",
                     "lacor", "st. mary"],
        "ethnic_groups": ["Acholi", "Langi", "Alur", "Madi"],
        "description": "Northern Uganda, post-conflict, Nilotic peoples",
    },
    "Eastern": {
        "type": "regional",
        "keywords": ["mbale", "tororo", "busia", "kapchorwa", "sironko",
                     "iganga", "kamuli", "bugiri", "bududa", "manafwa",
                     "busitema"],
        "ethnic_groups": ["Bagisu", "Iteso", "Sabiny"],
        "description": "Eastern Uganda, distinct ethnic populations",
    },
    "West Nile": {
        "type": "remote",
        "keywords": ["west nile", "arua", "koboko", "yumbe", "maracha",
                     "zombo", "packwach"],
        "ethnic_groups": ["Lugbara", "Kakwa", "Alur"],
        "description": "Remote northwestern region, refugee-hosting",
    },
    "Karamoja": {
        "type": "remote",
        "keywords": ["karamoja", "moroto", "kotido", "napak", "nakapiripirit",
                     "abim", "amudat", "kaabong"],
        "ethnic_groups": ["Karamojong (pastoralist)"],
        "description": "Semi-arid northeast, pastoralist communities",
    },
}

# South Africa provinces for comparison
SA_PROVINCES = {
    "Western Cape": {
        "keywords": ["cape town", "stellenbosch", "tygerberg", "groote schuur",
                     "worcester", "paarl", "george"],
        "ethnic_groups": ["Coloured", "Xhosa", "White"],
    },
    "Gauteng": {
        "keywords": ["johannesburg", "pretoria", "soweto", "witwatersrand",
                     "baragwanath", "tshwane", "sefako"],
        "ethnic_groups": ["Zulu", "Sotho", "mixed urban"],
    },
    "KwaZulu-Natal": {
        "keywords": ["durban", "kwazulu", "pietermaritzburg", "newcastle",
                     "richards bay", "empangeni"],
        "ethnic_groups": ["Zulu"],
    },
    "Eastern Cape": {
        "keywords": ["east london", "port elizabeth", "mthatha", "grahamstown",
                     "walter sisulu", "nelson mandela"],
        "ethnic_groups": ["Xhosa"],
    },
    "Limpopo": {
        "keywords": ["limpopo", "polokwane", "tzaneen", "thohoyandou",
                     "mankweng"],
        "ethnic_groups": ["Venda", "Tsonga", "Pedi"],
    },
    "Other Provinces": {
        "keywords": ["bloemfontein", "free state", "north west", "mpumalanga",
                     "nelspruit", "northern cape", "kimberley"],
        "ethnic_groups": ["Sotho", "Tswana", "mixed"],
    },
}

# Nigeria regions for comparison
NIGERIA_REGIONS = {
    "Lagos/South-West": {
        "keywords": ["lagos", "ibadan", "abeokuta", "ogun", "obafemi awolowo",
                     "ile-ife", "osun", "ondo"],
        "ethnic_groups": ["Yoruba"],
    },
    "South-East/South-South": {
        "keywords": ["enugu", "nnewdi", "nsukka", "port harcourt", "calabar",
                     "uyo", "benin city", "asaba", "owerri", "abia",
                     "university of nigeria"],
        "ethnic_groups": ["Igbo", "Ijaw", "Efik"],
    },
    "Abuja/North-Central": {
        "keywords": ["abuja", "jos", "ilorin", "benue", "nasarawa", "kwara",
                     "plateau"],
        "ethnic_groups": ["Tiv", "Nupe", "mixed"],
    },
    "North-West/North-East": {
        "keywords": ["kano", "kaduna", "zaria", "ahmadu bello", "sokoto",
                     "maiduguri", "bauchi", "gombe", "yola", "kebbi"],
        "ethnic_groups": ["Hausa", "Fulani", "Kanuri"],
    },
}

# Diversity/engagement keywords to search in trial text
DIVERSITY_KEYWORDS = [
    "ethnic", "ethnicity", "tribe", "tribal", "indigenous",
    "community engagement", "community-based", "community based",
    "community participation", "community health worker",
    "rural", "village", "remote", "hard-to-reach", "underserved",
    "minority", "marginalized", "marginalised", "vulnerable population",
    "cultural", "culturally appropriate", "traditional",
    "decentralized", "decentralised", "mobile clinic",
    "door-to-door", "household", "outreach",
]


# -- API helper ---------------------------------------------------------------
def api_query(location=None, condition=None, query_term=None,
              study_type="INTERVENTIONAL", page_size=10, count_total=True):
    """Query CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": str(count_total).lower(),
    }
    filters = []
    if study_type:
        filters.append(f"AREA[StudyType]{study_type}")
    if filters:
        params["filter.advanced"] = " AND ".join(filters)
    if condition:
        params["query.cond"] = condition
    if location:
        params["query.locn"] = location
    if query_term:
        params["query.term"] = query_term

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  WARNING: API error: {e}")
                return {"totalCount": 0, "studies": []}


def get_total(result):
    return result.get("totalCount", 0)


def extract_trial_detail(study):
    """Extract detailed fields from a CT.gov v2 study for diversity analysis."""
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    design = proto.get("designModule", {})
    status_mod = proto.get("statusModule", {})
    desc = proto.get("descriptionModule", {})
    cond_mod = proto.get("conditionsModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    contacts_loc = proto.get("contactsLocationsModule", {})
    locations = contacts_loc.get("locations", [])
    eligibility = proto.get("eligibilityModule", {})

    # Combine all text fields for keyword searching
    text_blob = " ".join([
        ident.get("briefTitle", ""),
        ident.get("officialTitle", ""),
        desc.get("briefSummary", ""),
        desc.get("detailedDescription", ""),
        eligibility.get("eligibilityCriteria", ""),
    ]).lower()

    # Extract location details
    loc_details = []
    for loc in locations:
        loc_details.append({
            "facility": loc.get("facility", ""),
            "city": loc.get("city", ""),
            "state": loc.get("state", ""),
            "country": loc.get("country", ""),
        })

    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
        "sponsor_class": sponsor_mod.get("leadSponsor", {}).get("class", ""),
        "status": status_mod.get("overallStatus", ""),
        "phases": design.get("phases", []),
        "enrollment": design.get("enrollmentInfo", {}).get("count", 0),
        "conditions": cond_mod.get("conditions", []),
        "locations": loc_details,
        "location_count": len(locations),
        "text_blob": text_blob,
    }


# -- Cache management ---------------------------------------------------------
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


# -- Classification functions -------------------------------------------------
def classify_uganda_location(trial):
    """Classify a trial's Uganda locations into regions."""
    regions_found = set()
    loc_texts = []

    for loc in trial.get("locations", []):
        if loc.get("country", "").lower().strip() in ("uganda", ""):
            loc_text = f"{loc.get('facility', '')} {loc.get('city', '')} {loc.get('state', '')}".lower()
            loc_texts.append(loc_text)

    combined_text = " ".join(loc_texts) + " " + trial.get("title", "").lower()

    for region_name, region_info in UGANDA_REGIONS.items():
        for kw in region_info["keywords"]:
            if kw in combined_text:
                regions_found.add(region_name)
                break

    # Default: if Uganda trial has no recognized location, classify as "Unknown"
    if not regions_found:
        regions_found.add("Unknown")

    return list(regions_found)


def classify_sa_location(trial):
    """Classify a South Africa trial into provinces."""
    provinces_found = set()
    loc_texts = []

    for loc in trial.get("locations", []):
        if "south africa" in loc.get("country", "").lower() or loc.get("country", "") == "":
            loc_text = f"{loc.get('facility', '')} {loc.get('city', '')} {loc.get('state', '')}".lower()
            loc_texts.append(loc_text)

    combined_text = " ".join(loc_texts) + " " + trial.get("title", "").lower()

    for province_name, province_info in SA_PROVINCES.items():
        for kw in province_info["keywords"]:
            if kw in combined_text:
                provinces_found.add(province_name)
                break

    if not provinces_found:
        provinces_found.add("Unknown")

    return list(provinces_found)


def classify_nigeria_location(trial):
    """Classify a Nigeria trial into regions."""
    regions_found = set()
    loc_texts = []

    for loc in trial.get("locations", []):
        if "nigeria" in loc.get("country", "").lower() or loc.get("country", "") == "":
            loc_text = f"{loc.get('facility', '')} {loc.get('city', '')} {loc.get('state', '')}".lower()
            loc_texts.append(loc_text)

    combined_text = " ".join(loc_texts) + " " + trial.get("title", "").lower()

    for region_name, region_info in NIGERIA_REGIONS.items():
        for kw in region_info["keywords"]:
            if kw in combined_text:
                regions_found.add(region_name)
                break

    if not regions_found:
        regions_found.add("Unknown")

    return list(regions_found)


def check_diversity_keywords(trial):
    """Check how many diversity/engagement keywords appear in trial text."""
    text = trial.get("text_blob", "")
    found = [kw for kw in DIVERSITY_KEYWORDS if kw in text]
    return found


# -- Data collection -----------------------------------------------------------
def fetch_country_trials(country, max_trials=500):
    """Fetch up to max_trials from a country for detailed analysis."""
    print(f"\n  Fetching trials for {country}...")

    all_trials = []
    page_token = None
    page = 0

    while len(all_trials) < max_trials:
        params = {
            "format": "json",
            "pageSize": min(100, max_trials - len(all_trials)),
            "countTotal": "true",
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
            "query.locn": country,
        }
        if page_token:
            params["pageToken"] = page_token

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(BASE_URL, params=params, timeout=30)
                resp.raise_for_status()
                result = resp.json()
                break
            except requests.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                else:
                    print(f"    WARNING: API error on page {page}: {e}")
                    result = {"studies": []}

        studies = result.get("studies", [])
        if not studies:
            break

        total_count = result.get("totalCount", 0)
        if page == 0:
            print(f"    Total available: {total_count:,}")

        for s in studies:
            all_trials.append(extract_trial_detail(s))

        page_token = result.get("nextPageToken")
        if not page_token:
            break

        page += 1
        print(f"    Fetched {len(all_trials)} trials so far...")
        time.sleep(RATE_LIMIT_DELAY)

    print(f"    Retrieved {len(all_trials)} trials for analysis")
    return all_trials, total_count if page == 0 else result.get("totalCount", len(all_trials))


def collect_all_data():
    """Collect trial data for Uganda, South Africa, and Nigeria."""
    print("=" * 60)
    print("THE HUJURAT DIVERSITY AUDIT -- Data Collection")
    print("=" * 60)

    all_data = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
            "script": "fetch_diversity_audit.py",
        },
        "uganda": {"trials": [], "total": 0},
        "south_africa": {"trials": [], "total": 0},
        "nigeria": {"trials": [], "total": 0},
    }

    # Fetch Uganda trials (up to 500)
    ug_trials, ug_total = fetch_country_trials("Uganda", max_trials=500)
    all_data["uganda"]["trials"] = ug_trials
    all_data["uganda"]["total"] = ug_total

    # Fetch South Africa trials (up to 500)
    sa_trials, sa_total = fetch_country_trials("South Africa", max_trials=500)
    all_data["south_africa"]["trials"] = sa_trials
    all_data["south_africa"]["total"] = sa_total

    # Fetch Nigeria trials (up to 500)
    ng_trials, ng_total = fetch_country_trials("Nigeria", max_trials=500)
    all_data["nigeria"]["trials"] = ng_trials
    all_data["nigeria"]["total"] = ng_total

    return all_data


# -- Analysis ------------------------------------------------------------------
def analyze_country(trials, classify_fn, capital_region_name):
    """Analyze geographic diversity for one country."""
    region_counts = Counter()
    multi_site_count = 0
    non_capital_count = 0
    diversity_keyword_trials = 0
    all_diversity_keywords_found = Counter()
    trials_with_regions = []

    for trial in trials:
        regions = classify_fn(trial)
        for r in regions:
            region_counts[r] += 1

        # Multi-site: trial has locations in 2+ regions
        if len(regions) > 1:
            multi_site_count += 1

        # Non-capital
        has_non_capital = any(r != capital_region_name and r != "Unknown" for r in regions)
        if has_non_capital:
            non_capital_count += 1

        # Diversity keywords
        found_kws = check_diversity_keywords(trial)
        if found_kws:
            diversity_keyword_trials += 1
            for kw in found_kws:
                all_diversity_keywords_found[kw] += 1

        trials_with_regions.append({
            "nct_id": trial["nct_id"],
            "title": trial["title"],
            "regions": regions,
            "diversity_keywords": found_kws,
            "location_count": trial["location_count"],
        })

    total = len(trials) if trials else 1

    diversity_score = round(non_capital_count / total * 100, 1)
    multi_site_pct = round(multi_site_count / total * 100, 1)
    keyword_pct = round(diversity_keyword_trials / total * 100, 1)

    # Ethnic representation proxy: trials in regions with distinct ethnic populations
    # (exclude capital and Unknown)
    ethnic_regions = {r: c for r, c in region_counts.items()
                      if r not in (capital_region_name, "Unknown")}
    ethnic_region_trials = sum(ethnic_regions.values())
    ethnic_rep_proxy = round(ethnic_region_trials / total * 100, 1)

    return {
        "total_analyzed": len(trials),
        "region_counts": dict(region_counts.most_common()),
        "multi_site_count": multi_site_count,
        "multi_site_pct": multi_site_pct,
        "non_capital_count": non_capital_count,
        "diversity_score": diversity_score,
        "diversity_keyword_trials": diversity_keyword_trials,
        "diversity_keyword_pct": keyword_pct,
        "ethnic_rep_proxy": ethnic_rep_proxy,
        "top_keywords": dict(all_diversity_keywords_found.most_common(15)),
    }


def run_analysis(data):
    """Run diversity analysis for all three countries."""
    results = {}

    print("\n  Analyzing Uganda...")
    results["uganda"] = analyze_country(
        data["uganda"]["trials"], classify_uganda_location, "Kampala"
    )
    results["uganda"]["total_registered"] = data["uganda"]["total"]

    print("  Analyzing South Africa...")
    results["south_africa"] = analyze_country(
        data["south_africa"]["trials"], classify_sa_location, "Gauteng"
    )
    results["south_africa"]["total_registered"] = data["south_africa"]["total"]

    print("  Analyzing Nigeria...")
    results["nigeria"] = analyze_country(
        data["nigeria"]["trials"], classify_nigeria_location, "Lagos/South-West"
    )
    results["nigeria"]["total_registered"] = data["nigeria"]["total"]

    return results


# -- HTML generation -----------------------------------------------------------
def generate_html(results, raw_data):
    """Generate the Diversity Audit HTML dashboard."""
    date_str = datetime.now().strftime("%d %B %Y")

    ug = results["uganda"]
    sa = results["south_africa"]
    ng = results["nigeria"]

    # Build Uganda region distribution
    ug_region_rows = ""
    ug_total = ug["total_analyzed"]
    for region, count in sorted(ug["region_counts"].items(), key=lambda x: -x[1]):
        pct = round(count / max(ug_total, 1) * 100, 1)
        region_info = UGANDA_REGIONS.get(region, {})
        region_type = region_info.get("type", "unknown")
        ethnic = ", ".join(region_info.get("ethnic_groups", []))
        type_color = {
            "capital": "#e74c3c", "peri-urban": "#f39c12", "regional": "#3498db",
            "post-conflict": "#9b59b6", "remote": "#e67e22", "unknown": "#888",
        }.get(region_type, "#888")
        bar_width = min(pct, 100)
        ug_region_rows += f"""
        <div class="region-row">
          <div class="region-name">
            <span class="region-dot" style="background:{type_color};"></span>
            {region}
            <span class="region-type" style="color:{type_color};">{region_type}</span>
          </div>
          <div class="region-bar-container">
            <div class="region-bar" style="width:{bar_width}%;background:{type_color};"></div>
            <span class="region-count">{count} ({pct}%)</span>
          </div>
          <div class="region-ethnic">{ethnic}</div>
        </div>"""

    # Kampala bubble stat
    kampala_count = ug["region_counts"].get("Kampala", 0)
    kampala_pct = round(kampala_count / max(ug_total, 1) * 100, 1)

    # Build SA province distribution
    sa_region_rows = ""
    sa_total = sa["total_analyzed"]
    for region, count in sorted(sa["region_counts"].items(), key=lambda x: -x[1]):
        pct = round(count / max(sa_total, 1) * 100, 1)
        bar_width = min(pct, 100)
        sa_region_rows += f"""
        <tr>
          <td style="font-weight:600;">{region}</td>
          <td>{count}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="height:14px;width:{bar_width}%;background:var(--teal);border-radius:3px;min-width:2px;"></div>
              <span>{pct}%</span>
            </div>
          </td>
        </tr>"""

    # Build Nigeria region distribution
    ng_region_rows = ""
    ng_total = ng["total_analyzed"]
    for region, count in sorted(ng["region_counts"].items(), key=lambda x: -x[1]):
        pct = round(count / max(ng_total, 1) * 100, 1)
        bar_width = min(pct, 100)
        ng_region_rows += f"""
        <tr>
          <td style="font-weight:600;">{region}</td>
          <td>{count}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="height:14px;width:{bar_width}%;background:var(--green);border-radius:3px;min-width:2px;"></div>
              <span>{pct}%</span>
            </div>
          </td>
        </tr>"""

    # Comparison table
    comparison_rows = ""
    for label, data_dict, capital in [
        ("Uganda", ug, "Kampala"),
        ("South Africa", sa, "Gauteng"),
        ("Nigeria", ng, "Lagos/South-West"),
    ]:
        cap_count = data_dict["region_counts"].get(capital, 0)
        cap_pct = round(cap_count / max(data_dict["total_analyzed"], 1) * 100, 1)
        ds_color = "#27ae60" if data_dict["diversity_score"] > 40 else \
                   "#f39c12" if data_dict["diversity_score"] > 20 else "#e74c3c"
        comparison_rows += f"""
        <tr>
          <td style="font-weight:600;">{label}</td>
          <td>{data_dict['total_analyzed']}</td>
          <td>{cap_pct}%</td>
          <td style="color:{ds_color};font-weight:600;">{data_dict['diversity_score']}%</td>
          <td>{data_dict['multi_site_pct']}%</td>
          <td>{data_dict['ethnic_rep_proxy']}%</td>
          <td>{data_dict['diversity_keyword_pct']}%</td>
        </tr>"""

    # Uganda diversity keywords
    kw_chips = ""
    for kw, count in sorted(ug.get("top_keywords", {}).items(), key=lambda x: -x[1])[:15]:
        kw_chips += f'<span class="kw-chip">{kw} <strong>({count})</strong></span> '

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Hujurat Diversity Audit | AfricaRCT</title>
<style>
:root {{
  --bg: #0a0e14;
  --surface: #131820;
  --surface2: #1a2030;
  --gold: #d4af37;
  --gold-dim: rgba(212,175,55,0.15);
  --text: #e8e6e3;
  --muted: #8899aa;
  --green: #27ae60;
  --red: #e74c3c;
  --orange: #f39c12;
  --blue: #3498db;
  --purple: #9b59b6;
  --teal: #1abc9c;
  --radius: 10px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
       line-height:1.6; }}

.header {{
  text-align:center; padding:48px 20px 32px;
  background:linear-gradient(180deg, rgba(27,174,156,0.08) 0%, transparent 100%);
  border-bottom:1px solid rgba(27,174,156,0.2);
}}
.header h1 {{ font-size:2.2em; color:var(--teal); margin-bottom:8px; font-weight:700; }}
.header .verse {{
  font-style:italic; color:var(--muted); max-width:800px; margin:12px auto;
  font-size:1.05em; line-height:1.7; border-left:3px solid var(--teal); padding-left:16px;
  text-align:left;
}}
.header .arabic {{
  font-family:'Traditional Arabic','Scheherazade New',serif;
  font-size:1.5em; direction:rtl; color:var(--teal); margin:12px 0;
}}
.header .subtitle {{ color:var(--muted); font-size:1.05em; margin-top:8px; }}

.container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}

.summary-grid {{
  display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
  gap:16px; margin:24px 0;
}}
.summary-card {{
  background:var(--surface); border-radius:var(--radius); padding:20px; text-align:center;
  border:1px solid rgba(255,255,255,0.06);
}}
.summary-card .big {{ font-size:2.4em; font-weight:700; }}
.summary-card .label {{ color:var(--muted); font-size:0.85em; margin-top:4px; }}

.section {{ margin:36px 0; }}
.section h2 {{
  font-size:1.5em; color:var(--teal); margin-bottom:16px;
  padding-bottom:8px; border-bottom:1px solid rgba(27,174,156,0.2);
}}
.section h2 .sub {{ font-size:0.65em; color:var(--muted); font-weight:400; }}

/* Kampala Bubble */
.bubble-box {{
  background:var(--surface); border-radius:var(--radius); padding:32px;
  text-align:center; border:2px solid var(--red); position:relative; margin:24px 0;
}}
.bubble-pct {{ font-size:4em; font-weight:800; color:var(--red); }}
.bubble-label {{ font-size:1.2em; color:var(--muted); }}
.bubble-sub {{ font-size:0.95em; color:var(--muted); margin-top:8px; }}

/* Region bars */
.region-row {{
  display:grid; grid-template-columns:220px 1fr 200px; gap:12px; align-items:center;
  padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.04);
}}
.region-name {{ font-weight:600; display:flex; align-items:center; gap:8px; }}
.region-dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; flex-shrink:0; }}
.region-type {{ font-size:0.75em; padding:1px 6px; border-radius:3px;
                background:rgba(255,255,255,0.05); }}
.region-bar-container {{
  height:24px; background:var(--surface2); border-radius:4px; position:relative;
  display:flex; align-items:center;
}}
.region-bar {{
  height:100%; border-radius:4px; min-width:2px; transition:width 0.3s;
}}
.region-count {{
  position:absolute; right:8px; font-size:0.85em; color:var(--text); font-weight:600;
}}
.region-ethnic {{ font-size:0.85em; color:var(--muted); }}

/* Tables */
table {{ width:100%; border-collapse:collapse; font-size:0.92em; }}
th {{ background:var(--surface2); color:var(--teal); padding:10px 8px; text-align:left;
     font-weight:600; border-bottom:2px solid rgba(27,174,156,0.2); }}
td {{ padding:8px; border-bottom:1px solid rgba(255,255,255,0.05); }}
tr:hover {{ background:rgba(27,174,156,0.04); }}

.kw-chip {{
  display:inline-block; background:var(--surface2); padding:3px 10px;
  border-radius:4px; margin:3px; font-size:0.85em;
  border:1px solid rgba(27,174,156,0.2);
}}

.findings-box {{
  background:var(--surface); border-radius:var(--radius); padding:20px;
  border-left:4px solid var(--teal); margin:16px 0;
}}
.findings-box h3 {{ color:var(--teal); margin-bottom:8px; }}
.findings-box ul {{ padding-left:20px; }}
.findings-box li {{ margin:4px 0; }}

.footer {{
  text-align:center; padding:32px; color:var(--muted); font-size:0.85em;
  border-top:1px solid rgba(255,255,255,0.06); margin-top:40px;
}}

@media (max-width: 768px) {{
  .region-row {{ grid-template-columns:1fr; gap:4px; }}
  .region-ethnic {{ display:none; }}
}}
</style>
</head>
<body>

<div class="header">
  <div class="arabic">يَا أَيُّهَا النَّاسُ إِنَّا خَلَقْنَاكُم مِّن ذَكَرٍ وَأُنثَىٰ وَجَعَلْنَاكُمْ شُعُوبًا وَقَبَائِلَ لِتَعَارَفُوا</div>
  <h1>The Hujurat Diversity Audit</h1>
  <div class="verse">
    "O mankind, indeed We have created you from male and female and made you peoples
    and tribes that you may know one another. Indeed, the most noble of you in the sight
    of Allah is the most righteous."
    <br>&mdash; Quran 49:13 (Surah Al-Hujurat)
  </div>
  <div class="subtitle">
    Tribes and Peoples in Research &mdash; Auditing geographic and ethnic diversity
    of clinical trial sites within African countries
  </div>
</div>

<div class="container">

  <!-- Summary -->
  <div class="summary-grid">
    <div class="summary-card">
      <div class="big" style="color:var(--teal);">{ug['total_analyzed']}</div>
      <div class="label">Uganda Trials Analyzed</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--red);">{kampala_pct}%</div>
      <div class="label">Kampala Concentration</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--green);">{ug['diversity_score']}%</div>
      <div class="label">Diversity Score</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--blue);">{ug['multi_site_pct']}%</div>
      <div class="label">Multi-Site Trials</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--purple);">{ug['ethnic_rep_proxy']}%</div>
      <div class="label">Ethnic Region Proxy</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--orange);">{ug['diversity_keyword_pct']}%</div>
      <div class="label">Mention Diversity</div>
    </div>
  </div>

  <!-- The Kampala Bubble -->
  <div class="section">
    <h2>The Kampala Bubble <span class="sub">How concentrated is Uganda's research in the capital?</span></h2>
    <div class="bubble-box">
      <div class="bubble-pct">{kampala_pct}%</div>
      <div class="bubble-label">of Uganda's clinical trials are based in Kampala</div>
      <div class="bubble-sub">
        {kampala_count} out of {ug_total} analyzed trials list Kampala-based facilities.
        Uganda has 135+ districts across 4 major regions, home to 56+ distinct ethnic groups.
        Yet research concentrates in a single city of 1.7 million in a nation of 48 million.
      </div>
    </div>
  </div>

  <!-- Uganda Regional Distribution -->
  <div class="section">
    <h2>Uganda: Regional Trial Distribution <span class="sub">
    Classified by geographic region and associated ethnic populations</span></h2>
    <p style="color:var(--muted);margin-bottom:16px;font-size:0.9em;">
      Regions colour-coded by type:
      <span style="color:#e74c3c;">capital</span> |
      <span style="color:#f39c12;">peri-urban</span> |
      <span style="color:#3498db;">regional</span> |
      <span style="color:#9b59b6;">post-conflict</span> |
      <span style="color:#e67e22;">remote</span>
    </p>
    {ug_region_rows}
  </div>

  <!-- Ethnic Representation -->
  <div class="section">
    <h2>Ethnic Representation Proxy <span class="sub">
    Trials in regions home to distinct ethnic populations</span></h2>
    <div class="findings-box">
      <p>Uganda has 56+ ethnic groups across five major linguistic families.
      The <strong>Ethnic Representation Proxy</strong> measures the proportion of trials
      conducted in regions with distinct ethnic populations outside the capital:</p>
      <ul style="margin-top:8px;">
        <li><strong>Northern Uganda</strong> (Acholi, Langi, Alur, Madi) &mdash;
          {ug['region_counts'].get('Northern', 0)} trials</li>
        <li><strong>Eastern Uganda</strong> (Bagisu, Iteso, Sabiny) &mdash;
          {ug['region_counts'].get('Eastern', 0)} trials</li>
        <li><strong>Western Uganda</strong> (Banyankole, Bakiga, Batoro, Bakonzo) &mdash;
          {ug['region_counts'].get('Western', 0)} trials</li>
        <li><strong>Karamoja</strong> (Karamojong pastoralists) &mdash;
          {ug['region_counts'].get('Karamoja', 0)} trials</li>
        <li><strong>West Nile</strong> (Lugbara, Kakwa, refugee-hosting) &mdash;
          {ug['region_counts'].get('West Nile', 0)} trials</li>
      </ul>
      <p style="margin-top:12px;">
        <strong>Ethnic Representation Proxy: {ug['ethnic_rep_proxy']}%</strong>
        of trials reach ethnically-distinct non-capital regions.
      </p>
    </div>
  </div>

  <!-- Diversity Keywords -->
  <div class="section">
    <h2>Diversity &amp; Engagement Keywords in Trial Text <span class="sub">
    Searched in titles, summaries, descriptions, and eligibility criteria</span></h2>
    <p style="color:var(--muted);margin-bottom:12px;font-size:0.9em;">
      Only <strong>{ug['diversity_keyword_pct']}%</strong> of Uganda trials
      explicitly mention diversity, community engagement, or rural populations.
    </p>
    <div style="margin:12px 0;">
      {kw_chips}
    </div>
  </div>

  <!-- South Africa Comparison -->
  <div class="section">
    <h2>South Africa: Provincial Distribution <span class="sub">
    A more geographically diverse research landscape?</span></h2>
    <div style="overflow-x:auto;">
      <table>
        <thead>
          <tr><th>Province</th><th>Trials</th><th>Distribution</th></tr>
        </thead>
        <tbody>{sa_region_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Nigeria Comparison -->
  <div class="section">
    <h2>Nigeria: Regional Distribution <span class="sub">
    The North-South divide in research</span></h2>
    <div style="overflow-x:auto;">
      <table>
        <thead>
          <tr><th>Region</th><th>Trials</th><th>Distribution</th></tr>
        </thead>
        <tbody>{ng_region_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Cross-Country Comparison -->
  <div class="section">
    <h2>Cross-Country Diversity Comparison</h2>
    <div style="overflow-x:auto;">
      <table>
        <thead>
          <tr>
            <th>Country</th>
            <th>Analyzed</th>
            <th>Capital %</th>
            <th>Diversity Score</th>
            <th>Multi-Site %</th>
            <th>Ethnic Proxy</th>
            <th>Mention Diversity</th>
          </tr>
        </thead>
        <tbody>{comparison_rows}</tbody>
      </table>
    </div>
    <div class="findings-box" style="margin-top:16px;">
      <h3>Interpretation</h3>
      <p>The <strong>Diversity Score</strong> measures the proportion of trials with
      any non-capital site. The <strong>Ethnic Representation Proxy</strong> measures
      trials in regions home to distinct ethnic populations. The
      <strong>Mention Diversity</strong> column shows how often trial protocols
      explicitly reference ethnic, community, or rural inclusion.</p>
    </div>
  </div>

  <!-- Key Findings -->
  <div class="section">
    <h2>Key Findings</h2>
    <div class="findings-box">
      <h3>The Capital Bubble Problem</h3>
      <ul>
        <li>Clinical trials across Africa concentrate in capital cities with major
        teaching hospitals, leaving most ethnic groups and rural populations invisible
        to research.</li>
        <li>Uganda's Karamoja (pastoralist Karamojong) and West Nile (refugee-hosting)
        regions are among the most medically underserved yet least represented in trials.</li>
        <li>Nigeria's North-South research divide mirrors its broader development divide:
        the Hausa/Fulani north, home to half the population, hosts a fraction of trials.</li>
        <li>South Africa's provincial distribution may be wider, but former Bantustan
        regions (rural Eastern Cape, Limpopo) remain underrepresented.</li>
      </ul>
    </div>
    <div class="findings-box" style="border-left-color:var(--purple);">
      <h3>Why It Matters</h3>
      <p>Pharmacogenomics shows that drug metabolism varies by genetic ancestry.
      Africa has the greatest human genetic diversity on Earth. Trials that recruit
      only from capital-city hospitals miss this diversity entirely, producing
      evidence that may not generalise to rural and ethnically-distinct populations
      where disease burden is often highest.</p>
    </div>
  </div>

  <!-- Methodology -->
  <div class="section">
    <h2>Methodology</h2>
    <div class="findings-box" style="border-left-color:var(--blue);">
      <ul>
        <li><strong>Data:</strong> ClinicalTrials.gov API v2, up to 500 trials per country</li>
        <li><strong>Classification:</strong> Trial locations classified by keyword matching against
        city/facility names for each region</li>
        <li><strong>Diversity Score:</strong> % of trials with at least one non-capital site</li>
        <li><strong>Ethnic Proxy:</strong> % of trials in regions with distinct ethnic populations</li>
        <li><strong>Keyword search:</strong> {len(DIVERSITY_KEYWORDS)} terms related to ethnic diversity,
        community engagement, and rural inclusion searched in trial text fields</li>
        <li><strong>Limitations:</strong> Keyword-based classification may miss or misclassify some locations.
        Only trials registered on ClinicalTrials.gov are included. Ethnic group assignment to regions
        is approximate as populations overlap and migrate.</li>
      </ul>
    </div>
  </div>

</div>

<div class="footer">
  <p>The Hujurat Diversity Audit &mdash; AfricaRCT Programme</p>
  <p>Data: ClinicalTrials.gov API v2 | Generated {date_str}</p>
  <p style="margin-top:8px;font-style:italic;">
    "We made you peoples and tribes that you may know one another" (49:13)
  </p>
</div>

</body>
</html>"""

    return html


# -- Main ----------------------------------------------------------------------
def main():
    print()
    print("=" * 60)
    print("  THE HUJURAT DIVERSITY AUDIT")
    print("  Tribes and Peoples in Research")
    print("=" * 60)

    # Check cache
    if is_cache_valid():
        print("\n  Using cached data (< 24h old)")
        raw_data = load_cache()
    else:
        print("\n  Fetching fresh data from ClinicalTrials.gov API v2...")
        raw_data = collect_all_data()
        save_cache(raw_data)

    # Run analysis
    print("\n\nRunning diversity analysis...")
    results = run_analysis(raw_data)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"{'DIVERSITY AUDIT RESULTS':^60}")
    print(f"{'=' * 60}")

    for country_key, label, capital in [
        ("uganda", "Uganda", "Kampala"),
        ("south_africa", "South Africa", "Gauteng"),
        ("nigeria", "Nigeria", "Lagos/South-West"),
    ]:
        r = results[country_key]
        cap_count = r["region_counts"].get(capital, 0)
        cap_pct = round(cap_count / max(r["total_analyzed"], 1) * 100, 1)
        print(f"\n  {label}:")
        print(f"    Trials analyzed:     {r['total_analyzed']}")
        print(f"    Capital concentration: {cap_pct}% in {capital}")
        print(f"    Diversity Score:     {r['diversity_score']}%")
        print(f"    Multi-site:          {r['multi_site_pct']}%")
        print(f"    Ethnic proxy:        {r['ethnic_rep_proxy']}%")
        print(f"    Mention diversity:   {r['diversity_keyword_pct']}%")
        print(f"    Regions: {dict(r['region_counts'])}")

    # Generate HTML
    print("\n\nGenerating HTML dashboard...")
    html = generate_html(results, raw_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written to {OUTPUT_HTML}")
    print(f"  File size: {os.path.getsize(OUTPUT_HTML):,} bytes")

    print(f"\n{'=' * 60}")
    print("Done. Open diversity-audit.html in a browser to view the dashboard.")


if __name__ == "__main__":
    main()
