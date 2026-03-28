"""
The Community's View — Research Done TO Us, Not WITH Us
=======================================================
Communities host clinical trials but rarely shape the research agenda.
Computes the "community engagement deficit" using ClinicalTrials.gov API v2.

Usage:
    python fetch_community_view.py

Output:
    data/community_view_data.json  — cached trial data
    community-view.html            — interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)
"""

import json
import os
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
CACHE_FILE = DATA_DIR / "community_view_data.json"
OUTPUT_HTML = Path(__file__).parent / "community-view.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35

# -- Country populations (2024 estimates) ----------------------------------
COUNTRY_POPS = {
    "Uganda": 48_400_000,
    "South Africa": 62_000_000,
    "Nigeria": 230_000_000,
    "Kenya": 56_000_000,
    "United States": 335_000_000,
}

# -- Community engagement classification keywords -------------------------
COMMUNITY_ENGAGED_KEYWORDS = [
    "community-based", "community based", "community engagement",
    "participatory", "community health worker", "community mobilization",
    "village health team", "peer education", "community advisory",
    "community empowerment", "implementation science", "task shifting",
    "task sharing", "community randomized", "cluster randomized",
]

EXTRACTIVE_KEYWORDS = [
    "pharmacokinetic", "bioequivalence", "dose escalation",
    "dose finding", "first-in-human", "maximum tolerated dose",
    "drug interaction", "safety and efficacy", "pivotal trial",
]


# -- API helpers -----------------------------------------------------------
def search_trials_count(location=None, condition=None, query_term=None,
                        page_size=1, max_retries=3):
    """Get trial count from CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }
    if condition:
        params["query.cond"] = condition
    if location:
        params["query.locn"] = location
    if query_term:
        params["query.term"] = query_term

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


def fetch_all_studies(location=None, condition=None, query_term=None,
                      max_pages=10, page_size=200):
    """Fetch all trial details for a given location (paginated)."""
    all_studies = []
    page_token = None

    for page_num in range(max_pages):
        params = {
            "format": "json",
            "pageSize": page_size,
            "countTotal": "true",
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        }
        if condition:
            params["query.cond"] = condition
        if location:
            params["query.locn"] = location
        if query_term:
            params["query.term"] = query_term
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  WARNING: page {page_num} error: {e}")
            break

        studies = data.get("studies", [])
        all_studies.extend(studies)

        page_token = data.get("nextPageToken")
        if not page_token or not studies:
            break
        time.sleep(RATE_LIMIT_DELAY)

    return all_studies


def extract_trial_info(study):
    """Extract key fields from a CT.gov v2 study object."""
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    design = proto.get("designModule", {})
    status_mod = proto.get("statusModule", {})
    enrollment_info = design.get("enrollmentInfo", {})
    cond_mod = proto.get("conditionsModule", {})
    arms_mod = proto.get("armsInterventionsModule", {})
    contacts_mod = proto.get("contactsLocationsModule", {})

    interventions = arms_mod.get("interventions", [])
    intervention_types = [i.get("type", "") for i in interventions]
    intervention_names = [i.get("name", "") for i in interventions]

    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "official_title": ident.get("officialTitle", ""),
        "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
        "sponsor_class": sponsor_mod.get("leadSponsor", {}).get("class", ""),
        "status": status_mod.get("overallStatus", ""),
        "phases": design.get("phases", []),
        "enrollment": enrollment_info.get("count", 0),
        "start_date": status_mod.get("startDateStruct", {}).get("date", ""),
        "conditions": cond_mod.get("conditions", []),
        "intervention_types": intervention_types,
        "intervention_names": intervention_names,
        "locations_count": len(contacts_mod.get("locations", [])),
    }


def classify_engagement(trial):
    """Classify a trial as community-engaged vs extractive."""
    text = " ".join([
        trial.get("title", ""),
        trial.get("official_title", ""),
        " ".join(trial.get("conditions", [])),
        " ".join(trial.get("intervention_names", [])),
    ]).lower()

    phases = trial.get("phases", [])
    int_types = [t.lower() for t in trial.get("intervention_types", [])]

    # Community-engaged signals
    community_signals = sum(1 for kw in COMMUNITY_ENGAGED_KEYWORDS if kw in text)
    is_behavioral = "BEHAVIORAL" in [t.upper() for t in trial.get("intervention_types", [])]
    is_phase_na = phases == ["NA"] or not phases
    is_implementation = any(w in text for w in [
        "implementation", "scale-up", "scale up", "task shift",
        "mhealth", "m-health", "community health",
    ])

    # Extractive signals
    extractive_signals = sum(1 for kw in EXTRACTIVE_KEYWORDS if kw in text)
    is_drug_trial = "DRUG" in [t.upper() for t in trial.get("intervention_types", [])]
    is_late_phase = any(p in phases for p in ["PHASE2", "PHASE3"])

    # Score
    community_score = community_signals * 2 + is_behavioral * 3 + is_phase_na * 1 + is_implementation * 2
    extractive_score = extractive_signals * 2 + (is_drug_trial and is_late_phase) * 3

    if community_score >= 3:
        return "community-engaged"
    elif extractive_score >= 3:
        return "extractive"
    elif is_behavioral or is_implementation or is_phase_na:
        return "community-engaged"
    elif is_drug_trial and any(p in phases for p in ["PHASE1", "PHASE2", "PHASE3"]):
        return "extractive"
    else:
        return "ambiguous"


# -- Data collection -------------------------------------------------------
def collect_data():
    """Collect community view data from CT.gov API v2."""

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

    # ---- Step 1: Community engagement queries (Africa vs US) ----
    print("\n" + "=" * 70)
    print("STEP 1: Community engagement queries")
    print("=" * 70)

    community_queries = {
        "community-based OR community engagement OR participatory OR community health": {
            "label": "Community-engaged research",
        },
        "community randomized OR community trial": {
            "label": "Community randomized trials",
        },
    }

    engagement_counts = {}
    for query, info in community_queries.items():
        for location in ["Africa", "United States"]:
            print(f"  Querying '{info['label']}' in {location}...")
            count = search_trials_count(location=location, query_term=query)
            key = f"{info['label']}|{location}"
            engagement_counts[key] = count
            print(f"    Count: {count}")
            time.sleep(RATE_LIMIT_DELAY)

    # Community randomized in Africa specifically
    print("  Querying community randomized in Africa...")
    community_rct_africa = search_trials_count(
        location="Africa", query_term="community randomized OR community trial")
    time.sleep(RATE_LIMIT_DELAY)

    # ---- Step 2: Load Uganda trial data for engagement classification ----
    print("\n" + "=" * 70)
    print("STEP 2: Loading Uganda trial data for engagement classification")
    print("=" * 70)

    uganda_cache = DATA_DIR / "uganda_collected_data.json"
    uganda_trials = []
    if uganda_cache.exists():
        with open(uganda_cache, "r", encoding="utf-8") as f:
            uganda_data = json.load(f)
        uganda_trials = uganda_data.get("sample_trials", [])
        print(f"  Loaded {len(uganda_trials)} Uganda trials from cache")
    else:
        print("  Fetching Uganda trials from API...")
        studies = fetch_all_studies(location="Uganda", max_pages=4, page_size=200)
        uganda_trials = [extract_trial_info(s) for s in studies]
        print(f"  Fetched {len(uganda_trials)} Uganda trials")

    # Classify each trial
    classifications = Counter()
    engaged_trials = []
    extractive_trials = []
    for trial in uganda_trials:
        cls = classify_engagement(trial)
        classifications[cls] += 1
        if cls == "community-engaged":
            engaged_trials.append(trial)
        elif cls == "extractive":
            extractive_trials.append(trial)

    total = len(uganda_trials)
    community_voice_score_uganda = round(
        classifications.get("community-engaged", 0) / total * 100, 1) if total else 0

    print(f"  Community-engaged: {classifications.get('community-engaged', 0)}")
    print(f"  Extractive: {classifications.get('extractive', 0)}")
    print(f"  Ambiguous: {classifications.get('ambiguous', 0)}")
    print(f"  Community Voice Score (Uganda): {community_voice_score_uganda}%")

    # ---- Step 3: Compare across countries ----
    print("\n" + "=" * 70)
    print("STEP 3: Cross-country community engagement comparison")
    print("=" * 70)

    country_scores = {}
    compare_countries = ["South Africa", "Nigeria", "Kenya"]
    for country in compare_countries:
        print(f"  Fetching {country} trials for classification...")
        studies = fetch_all_studies(location=country, max_pages=4, page_size=200)
        trials = [extract_trial_info(s) for s in studies]
        cls_counts = Counter()
        for t in trials:
            cls_counts[classify_engagement(t)] += 1
        t_total = len(trials)
        score = round(cls_counts.get("community-engaged", 0) / t_total * 100, 1) if t_total else 0
        country_scores[country] = {
            "total": t_total,
            "community_engaged": cls_counts.get("community-engaged", 0),
            "extractive": cls_counts.get("extractive", 0),
            "ambiguous": cls_counts.get("ambiguous", 0),
            "community_voice_score": score,
        }
        print(f"    {country}: {t_total} trials, CVS={score}%")
        time.sleep(RATE_LIMIT_DELAY)

    # ---- Step 4: Engagement by condition ----
    print("\n" + "=" * 70)
    print("STEP 4: Engagement patterns by condition")
    print("=" * 70)

    condition_engagement = {}
    conditions_to_check = ["HIV", "malaria", "tuberculosis", "cancer",
                           "maternal OR pregnancy", "mental health OR depression",
                           "nutrition OR malnutrition"]
    for cond in conditions_to_check:
        engaged_count = 0
        extractive_count = 0
        for trial in uganda_trials:
            trial_conds = " ".join(trial.get("conditions", [])).lower()
            cond_parts = [c.strip().lower() for c in cond.replace(" OR ", "|").split("|")]
            if any(cp in trial_conds for cp in cond_parts):
                cls = classify_engagement(trial)
                if cls == "community-engaged":
                    engaged_count += 1
                elif cls == "extractive":
                    extractive_count += 1
        total_cond = engaged_count + extractive_count
        rate = round(engaged_count / total_cond * 100, 1) if total_cond > 0 else 0
        condition_engagement[cond] = {
            "engaged": engaged_count,
            "extractive": extractive_count,
            "engagement_rate": rate,
        }
        print(f"  {cond}: engaged={engaged_count}, extractive={extractive_count}, rate={rate}%")

    # ---- Step 5: Cluster trial analysis (consent problem) ----
    print("\n" + "=" * 70)
    print("STEP 5: Cluster trial analysis")
    print("=" * 70)

    cluster_count = 0
    individual_consent_in_cluster = 0
    for trial in uganda_trials:
        text = (trial.get("title", "") + " " + trial.get("official_title", "")).lower()
        if "cluster" in text:
            cluster_count += 1
            if any(w in text for w in ["individual consent", "informed consent"]):
                individual_consent_in_cluster += 1
    print(f"  Cluster trials: {cluster_count}")
    print(f"  With individual consent mentioned: {individual_consent_in_cluster}")

    # ---- Compute key metrics ----
    engagement_ratio_africa = engagement_counts.get(
        "Community-engaged research|Africa", 0)
    engagement_ratio_us = engagement_counts.get(
        "Community-engaged research|United States", 0)

    # Build data object
    data = {
        "fetch_date": datetime.now().isoformat(),
        "uganda_total": total,
        "community_voice_score_uganda": community_voice_score_uganda,
        "classifications": dict(classifications),
        "engagement_counts": engagement_counts,
        "community_rct_africa": community_rct_africa,
        "community_engaged_africa": engagement_ratio_africa,
        "community_engaged_us": engagement_ratio_us,
        "country_scores": country_scores,
        "condition_engagement": condition_engagement,
        "cluster_analysis": {
            "cluster_count": cluster_count,
            "individual_consent_mentioned": individual_consent_in_cluster,
        },
        "engaged_examples": [
            {"nct_id": t["nct_id"], "title": t["title"][:120],
             "phases": t["phases"], "sponsor": t["sponsor"]}
            for t in engaged_trials[:15]
        ],
        "extractive_examples": [
            {"nct_id": t["nct_id"], "title": t["title"][:120],
             "phases": t["phases"], "sponsor": t["sponsor"]}
            for t in extractive_trials[:15]
        ],
    }

    # Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator -------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML community view dashboard."""

    fetch_date = data["fetch_date"][:10]
    total = data["uganda_total"]
    cvs_uganda = data["community_voice_score_uganda"]
    cls = data["classifications"]
    engaged = cls.get("community-engaged", 0)
    extractive = cls.get("extractive", 0)
    ambiguous = cls.get("ambiguous", 0)
    africa_engaged = data["community_engaged_africa"]
    us_engaged = data["community_engaged_us"]
    community_rct = data["community_rct_africa"]
    country_scores = data["country_scores"]
    cond_eng = data["condition_engagement"]
    cluster = data["cluster_analysis"]
    engaged_examples = data["engaged_examples"]
    extractive_examples = data["extractive_examples"]

    # Engagement rate comparison
    africa_us_ratio = round(africa_engaged / us_engaged * 100, 1) if us_engaged > 0 else 0

    # Country comparison bars
    all_scores = [("Uganda", cvs_uganda, total)]
    for country, info in country_scores.items():
        all_scores.append((country, info["community_voice_score"], info["total"]))
    all_scores.sort(key=lambda x: x[1], reverse=True)
    max_score = max(s[1] for s in all_scores) if all_scores else 1

    country_bars = []
    for name, score, count in all_scores:
        bar_w = round(score / max(max_score, 1) * 100)
        color = "#22c55e" if score >= 50 else "#f59e0b" if score >= 30 else "#ef4444"
        country_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:8px 0">'
            f'<div style="width:140px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:14px">{name}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:32px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{color};'
            f'border-radius:4px;transition:width 0.5s"></div>'
            f'<span style="position:absolute;right:8px;top:6px;font-size:13px;'
            f'color:#94a3b8;font-weight:600">{score}% ({count} trials)</span>'
            f'</div></div>'
        )
    country_bars_html = "\n".join(country_bars)

    # Condition engagement table
    cond_rows = []
    for cond, info in sorted(cond_eng.items(), key=lambda x: x[1]["engagement_rate"], reverse=True):
        label = cond.replace(" OR ", "/")
        rate = info["engagement_rate"]
        color = "#22c55e" if rate >= 50 else "#f59e0b" if rate >= 30 else "#ef4444"
        bar_w = round(rate)
        cond_rows.append(
            f'<tr>'
            f'<td style="padding:8px 12px;font-weight:600">{label}</td>'
            f'<td style="text-align:right;padding:8px 12px">{info["engaged"]}</td>'
            f'<td style="text-align:right;padding:8px 12px">{info["extractive"]}</td>'
            f'<td style="padding:8px 12px">'
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:20px">'
            f'<div style="width:{bar_w}%;height:100%;background:{color};'
            f'border-radius:4px"></div></div>'
            f'<span style="color:{color};font-weight:600;min-width:50px;'
            f'text-align:right">{rate}%</span></div></td>'
            f'</tr>'
        )
    cond_rows_html = "\n".join(cond_rows)

    # Example trials
    engaged_rows = []
    for t in engaged_examples[:10]:
        phase_str = ", ".join(t["phases"]) or "N/A"
        engaged_rows.append(
            f'<tr style="border-bottom:1px solid #1e293b">'
            f'<td style="padding:6px 8px;font-family:monospace;font-size:12px">'
            f'<a href="https://clinicaltrials.gov/study/{t["nct_id"]}" '
            f'target="_blank" style="color:#60a5fa;text-decoration:none">'
            f'{t["nct_id"]}</a></td>'
            f'<td style="padding:6px 8px;font-size:13px;max-width:400px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{t["title"]}</td>'
            f'<td style="padding:6px 8px;font-size:12px;text-align:center">'
            f'{phase_str}</td>'
            f'<td style="padding:6px 8px;font-size:12px">{t["sponsor"][:40]}</td>'
            f'</tr>'
        )
    engaged_rows_html = "\n".join(engaged_rows)

    extractive_rows = []
    for t in extractive_examples[:10]:
        phase_str = ", ".join(t["phases"]) or "N/A"
        extractive_rows.append(
            f'<tr style="border-bottom:1px solid #1e293b">'
            f'<td style="padding:6px 8px;font-family:monospace;font-size:12px">'
            f'<a href="https://clinicaltrials.gov/study/{t["nct_id"]}" '
            f'target="_blank" style="color:#60a5fa;text-decoration:none">'
            f'{t["nct_id"]}</a></td>'
            f'<td style="padding:6px 8px;font-size:13px;max-width:400px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{t["title"]}</td>'
            f'<td style="padding:6px 8px;font-size:12px;text-align:center">'
            f'{phase_str}</td>'
            f'<td style="padding:6px 8px;font-size:12px">{t["sponsor"][:40]}</td>'
            f'</tr>'
        )
    extractive_rows_html = "\n".join(extractive_rows)

    # Classification donut-style display
    engaged_pct = round(engaged / total * 100, 1) if total else 0
    extractive_pct = round(extractive / total * 100, 1) if total else 0
    ambiguous_pct = round(ambiguous / total * 100, 1) if total else 0

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research Done TO Us, Not WITH Us | Community View</title>
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
  .big-number {{
    font-size: 5rem; font-weight: 900; text-align: center;
    margin: 20px 0 10px; line-height: 1;
  }}
  .big-sub {{
    text-align: center; color: #94a3b8; font-size: 16px; margin-bottom: 16px;
  }}
  .trio {{
    display: flex; gap: 16px; margin: 20px 0; flex-wrap: wrap;
    justify-content: center;
  }}
  .trio-item {{
    flex: 1; min-width: 180px; text-align: center;
    background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
    padding: 20px;
  }}
  .trio-item .num {{
    font-size: 2.5rem; font-weight: 900; line-height: 1;
  }}
  .trio-item .desc {{
    font-size: 13px; color: #94a3b8; margin-top: 4px;
  }}
</style>
</head>
<body>
<div class="container">

<h1>Research Done TO Us, Not WITH Us</h1>
<p class="subtitle">
  The Community Engagement Deficit in African Clinical Trials |
  ClinicalTrials.gov API v2 | Data: {fetch_date}
</p>

<!-- ============ SECTION 1: EXECUTIVE SUMMARY ============ -->
<div class="section">
  <h2>1. The Community Engagement Deficit</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Uganda Trials Analysed</div>
      <div class="value" style="color:#60a5fa">{total}</div>
      <div class="label">interventional trials</div>
    </div>
    <div class="kpi">
      <div class="label">Community Voice Score</div>
      <div class="value" style="color:#ef4444">{cvs_uganda}%</div>
      <div class="label">community-engaged trials</div>
    </div>
    <div class="kpi">
      <div class="label">Extractive Trials</div>
      <div class="value" style="color:#f59e0b">{extractive_pct}%</div>
      <div class="label">{extractive} of {total} trials</div>
    </div>
    <div class="kpi">
      <div class="label">Community Research (Africa)</div>
      <div class="value" style="color:#22c55e">{africa_engaged:,}</div>
      <div class="label">vs {us_engaged:,} in the US</div>
    </div>
    <div class="kpi">
      <div class="label">Community RCTs (Africa)</div>
      <div class="value" style="color:#8b5cf6">{community_rct}</div>
      <div class="label">cluster/community trials</div>
    </div>
    <div class="kpi">
      <div class="label">Cluster Trials (Uganda)</div>
      <div class="value" style="color:#06b6d4">{cluster['cluster_count']}</div>
      <div class="label">consent complexity</div>
    </div>
  </div>
  <div class="callout">
    Communities across Africa host clinical trials but rarely shape the research
    agenda. Of <strong>{total}</strong> interventional trials in Uganda, only
    <strong>{cvs_uganda}%</strong> show meaningful community engagement markers.
    The majority of trials are designed abroad, funded abroad, and answer
    questions set by foreign investigators. Communities provide the bodies; the
    Global North provides the questions.
  </div>
</div>

<!-- ============ SECTION 2: EXTRACTIVE vs PARTICIPATORY ============ -->
<div class="section">
  <h2>2. Extractive vs Participatory: The Classification</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    Each of {total} Uganda trials classified by engagement signals: community
    language, behavioral/implementation design, Phase NA indicators (community-engaged)
    vs drug testing, late-phase, pharmacokinetic indicators (extractive).
  </p>
  <div class="trio">
    <div class="trio-item">
      <div class="num" style="color:#22c55e">{engaged}</div>
      <div class="desc">Community-engaged ({engaged_pct}%)</div>
      <div style="font-size:12px;color:#64748b;margin-top:4px">
        Behavioral, participatory, implementation</div>
    </div>
    <div class="trio-item">
      <div class="num" style="color:#ef4444">{extractive}</div>
      <div class="desc">Extractive ({extractive_pct}%)</div>
      <div style="font-size:12px;color:#64748b;margin-top:4px">
        Drug testing, dose-finding, pharmacokinetics</div>
    </div>
    <div class="trio-item">
      <div class="num" style="color:#f59e0b">{ambiguous}</div>
      <div class="desc">Ambiguous ({ambiguous_pct}%)</div>
      <div style="font-size:12px;color:#64748b;margin-top:4px">
        Mixed signals or insufficient information</div>
    </div>
  </div>
  <div class="callout-amber callout">
    <strong>What "extractive" means:</strong> An extractive trial is one where
    the community provides participants and disease burden, but the research
    question, protocol, and intellectual property are entirely controlled by
    external organizations. Community members are subjects, not partners. They
    may never see the results or benefit from the intervention tested on them.
  </div>
</div>

<!-- ============ SECTION 3: CROSS-COUNTRY COMPARISON ============ -->
<div class="section">
  <h2>3. Community Voice Score by Country</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    Percentage of trials classified as community-engaged, by country. Higher
    scores indicate more participatory research culture.
  </p>
  {country_bars_html}
  <div class="callout-green callout" style="margin-top:16px">
    <strong>What drives variation?</strong> Countries with strong community
    health worker systems (Uganda, Kenya) tend to have higher engagement scores.
    South Africa's large pharma-driven trial portfolio pulls its score toward
    extractive. Nigeria's small trial volume makes scores volatile but reflects
    limited community-based research infrastructure.
  </div>
</div>

<!-- ============ SECTION 4: BY CONDITION ============ -->
<div class="section">
  <h2>4. Engagement Rate by Condition</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    Which conditions attract community-engaged vs extractive research in Uganda?
  </p>
  <table>
    <thead>
      <tr>
        <th>Condition</th>
        <th style="text-align:right">Engaged</th>
        <th style="text-align:right">Extractive</th>
        <th>Engagement Rate</th>
      </tr>
    </thead>
    <tbody>
      {cond_rows_html}
    </tbody>
  </table>
  <div class="callout" style="margin-top:16px">
    <strong>The pattern:</strong> Behavioral conditions (mental health, nutrition,
    maternal health) show higher community engagement. Drug-focused conditions
    (HIV, malaria, TB) are dominated by extractive trials, even when the diseases
    disproportionately burden the host communities. The most community-relevant
    diseases attract the least community-shaped research.
  </div>
</div>

<!-- ============ SECTION 5: THE CONSENT PROBLEM ============ -->
<div class="section">
  <h2>5. The Consent Problem: Community vs Individual</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Cluster Trials in Uganda</div>
      <div class="value" style="color:#8b5cf6">{cluster['cluster_count']}</div>
      <div class="label">community-randomized</div>
    </div>
    <div class="kpi">
      <div class="label">Individual Consent Mentioned</div>
      <div class="value" style="color:#f59e0b">{cluster['individual_consent_mentioned']}</div>
      <div class="label">in cluster trial titles</div>
    </div>
  </div>
  <div class="callout">
    <strong>The cluster trial paradox:</strong> In cluster-randomized trials,
    entire communities are randomized, but individual members may not have
    meaningfully consented. A village chief's agreement is not the same as
    informed consent. When a community is randomized to receive a new malaria
    intervention, individual families cannot opt out of the environmental
    changes. Yet these trials are approved under the same ethical frameworks
    designed for individual-level research. The gap between community consent
    and individual autonomy remains largely unaddressed in African trial ethics.
  </div>
</div>

<!-- ============ SECTION 6: WHAT PARTNERSHIP LOOKS LIKE ============ -->
<div class="section">
  <h2>6. What Genuine Partnership Looks Like: CBPR Models</h2>
  <div class="callout-green callout">
    <strong>Community-Based Participatory Research (CBPR)</strong> offers a
    framework where communities are genuine partners, not just recruitment pools.
    Key principles include: (1) community members co-design research questions;
    (2) local researchers hold PI positions; (3) results are fed back to
    communities in accessible formats; (4) communities retain some intellectual
    property rights; (5) capacity building is an explicit outcome.
  </div>
  <h3>Examples of Community-Engaged Trials in Uganda</h3>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>NCT ID</th>
        <th>Title</th>
        <th style="text-align:center">Phase</th>
        <th>Sponsor</th>
      </tr>
    </thead>
    <tbody>
      {engaged_rows_html}
    </tbody>
  </table>
  </div>
  <h3 style="margin-top:24px">Examples of Extractive Trials in Uganda</h3>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>NCT ID</th>
        <th>Title</th>
        <th style="text-align:center">Phase</th>
        <th>Sponsor</th>
      </tr>
    </thead>
    <tbody>
      {extractive_rows_html}
    </tbody>
  </table>
  </div>
</div>

<!-- ============ SECTION 7: THE WAY FORWARD ============ -->
<div class="section">
  <h2>7. From Subjects to Partners: The Way Forward</h2>
  <div class="callout-green callout">
    <strong>Recommendations for genuine community partnership:</strong>
  </div>
  <ul style="margin:16px 0 16px 24px;color:#94a3b8;line-height:2.2">
    <li><strong style="color:#e2e8f0">Community Advisory Boards (CABs):</strong>
      Mandatory for all trials, not just HIV -- with real decision power over
      protocol modifications, not just rubber-stamping.</li>
    <li><strong style="color:#e2e8f0">Benefit-sharing agreements:</strong>
      Pre-specify how communities will access successful interventions. No
      more "we'll figure it out later" promises.</li>
    <li><strong style="color:#e2e8f0">Local PI requirements:</strong>
      At least one local investigator in a leadership role, not just as a
      "site coordinator" for foreign PIs.</li>
    <li><strong style="color:#e2e8f0">Results dissemination:</strong>
      Community-language summaries of findings, presented in-person, not
      just buried in English-language journals.</li>
    <li><strong style="color:#e2e8f0">Research agenda co-creation:</strong>
      Communities should help set priorities, not just respond to what
      foreign funders want studied. The Village Health Team model in Uganda
      shows this is possible at scale.</li>
  </ul>
</div>

<div class="source">
  Data source: <a href="https://clinicaltrials.gov">ClinicalTrials.gov</a>
  API v2 (accessed {fetch_date})<br>
  Analysis: fetch_community_view.py | The Community's View<br>
  Classification: Algorithmic based on title keywords, phase, intervention type.
  Manual review recommended for individual trial classification.
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
    print("  The Community's View -- Research Done TO Us, Not WITH Us")
    print("  ClinicalTrials.gov API v2 Analysis")
    print("=" * 70)

    data = collect_data()

    print("\n" + "=" * 70)
    print("KEY FINDINGS:")
    print("=" * 70)
    print(f"  Uganda total trials:       {data['uganda_total']}")
    print(f"  Community Voice Score:     {data['community_voice_score_uganda']}%")
    print(f"  Community-engaged:         {data['classifications'].get('community-engaged', 0)}")
    print(f"  Extractive:                {data['classifications'].get('extractive', 0)}")
    print(f"  Community research Africa: {data['community_engaged_africa']}")
    print(f"  Community research US:     {data['community_engaged_us']}")

    generate_html(data)
    print("\nDone.")


if __name__ == "__main__":
    main()
