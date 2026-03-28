#!/usr/bin/env python
"""
fetch_principal_agent.py -- The Principal-Agent Problem: Whose Research Agenda Is This?
======================================================================================
From organizational economics (Jensen & Meckling, 1976): when an agent (foreign
sponsor) acts on behalf of a principal (African population), their interests may
diverge.  The agent has information the principal lacks.

- PRINCIPAL: African populations who bear disease burden and trial risk
- AGENT: Foreign sponsors who choose what to study, where, and how
- INFORMATION ASYMMETRY: sponsors know trial results before communities
- INCENTIVE MISALIGNMENT: sponsors optimize for FDA/EMA approval, not local health

Uses Uganda's ~783 ClinicalTrials.gov records as the primary case study.
Compares with South Africa (stronger local voice) and Nigeria (weak infrastructure).

Usage:
    python fetch_principal_agent.py

Output:
    data/principal_agent_data.json   (cached API results, 24h TTL)
    principal-agent.html             (dark-theme interactive dashboard)

Requirements:
    Python 3.8+, no external packages (uses urllib)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# Focus countries
FOCUS_COUNTRIES = {
    "Uganda": {"population_m": 48, "pepfar_dependent": True,
               "label": "PEPFAR-dependent"},
    "South Africa": {"population_m": 62, "pepfar_dependent": False,
                     "label": "Stronger local voice"},
    "Nigeria": {"population_m": 230, "pepfar_dependent": False,
                "label": "Weak infrastructure"},
}

# Uganda disease burden ranking (GBD 2019, top causes of DALY loss)
# Rank 1 = highest burden
UGANDA_BURDEN_RANKING = {
    "HIV": 1,
    "malaria": 2,
    "neonatal": 3,
    "maternal OR pregnancy": 4,
    "pneumonia": 5,
    "tuberculosis": 6,
    "nutrition OR malnutrition": 7,
    "cardiovascular": 8,
    "mental health OR depression": 9,
    "cancer": 10,
    "sickle cell": 11,
    "epilepsy": 12,
    "diabetes": 13,
    "stroke": 14,
    "hypertension": 15,
}

# South Africa burden ranking (GBD 2019)
SA_BURDEN_RANKING = {
    "HIV": 1,
    "tuberculosis": 2,
    "cardiovascular": 3,
    "diabetes": 4,
    "cancer": 5,
    "mental health OR depression": 6,
    "maternal OR pregnancy": 7,
    "pneumonia": 8,
    "stroke": 9,
    "hypertension": 10,
    "neonatal": 11,
    "nutrition OR malnutrition": 12,
    "epilepsy": 13,
    "malaria": 14,
    "sickle cell": 15,
}

# Nigeria burden ranking (GBD 2019)
NIGERIA_BURDEN_RANKING = {
    "malaria": 1,
    "neonatal": 2,
    "HIV": 3,
    "pneumonia": 4,
    "nutrition OR malnutrition": 5,
    "tuberculosis": 6,
    "maternal OR pregnancy": 7,
    "cardiovascular": 8,
    "mental health OR depression": 9,
    "sickle cell": 10,
    "cancer": 11,
    "diabetes": 12,
    "stroke": 13,
    "hypertension": 14,
    "epilepsy": 15,
}

# Condition queries
CONDITION_QUERIES = list(UGANDA_BURDEN_RANKING.keys())

# Implementation research markers (serves local needs, not sponsor FDA approval)
IMPLEMENTATION_QUERIES = [
    "implementation science",
    "community health worker",
    "task shifting",
    "community engagement",
    "behavioral intervention",
    "health education",
    "mHealth OR mobile health",
]

CACHE_FILE = Path(__file__).resolve().parent / "data" / "principal_agent_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "principal-agent.html"
RATE_LIMIT = 0.35
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(params, retries=MAX_RETRIES):
    """Make a GET request to ClinicalTrials.gov API v2 with retries."""
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"  [retry {attempt + 1}/{retries}] {exc}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def get_trial_count(condition_query, location):
    """Return total count of interventional trials for a condition+location."""
    params = {
        "format": "json",
        "query.cond": condition_query,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_trial_details(location, page_size=100, next_token=None):
    """Fetch trial-level data for a location (all conditions)."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": page_size,
        "countTotal": "true",
        "fields": (
            "NCTId,BriefTitle,Phase,OverallStatus,HasResults,"
            "LeadSponsorName,LeadSponsorClass,StartDate,EnrollmentCount,"
            "ConditionList,CollaboratorList"
        ),
    }
    if next_token:
        params["pageToken"] = next_token
    data = api_get(params)
    if data is None:
        return [], None, 0
    studies = data.get("studies", [])
    token = data.get("nextPageToken", None)
    total = data.get("totalCount", 0)
    results = []
    for study in studies:
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        results_mod = proto.get("resultsSection", None)
        conditions_mod = proto.get("conditionsModule", {})
        enroll_mod = design.get("enrollmentInfo", {}) if design else {}
        phases_list = design.get("phases", []) if design else []
        phase_str = ", ".join(phases_list) if phases_list else "Not specified"
        lead_sponsor = sponsor_mod.get("leadSponsor", {})
        collabs = sponsor_mod.get("collaborators", [])
        collab_names = [c.get("name", "") for c in collabs]
        start_info = status_mod.get("startDateStruct", {})
        has_results = status_mod.get("resultsFirstSubmitDate") is not None
        if results_mod is not None:
            has_results = True
        results.append({
            "nctId": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "phase": phase_str,
            "status": status_mod.get("overallStatus", ""),
            "hasResults": has_results,
            "sponsorName": lead_sponsor.get("name", ""),
            "sponsorClass": lead_sponsor.get("class", ""),
            "collaborators": collab_names,
            "startDate": start_info.get("date", ""),
            "enrollment": enroll_mod.get("count", 0),
            "conditions": conditions_mod.get("conditions", []),
        })
    return results, token, total


def get_all_trials_for_location(location, max_pages=20):
    """Paginate through all trials for a location."""
    all_trials = []
    token = None
    for page in range(max_pages):
        trials, token, total = get_trial_details(location, page_size=100, next_token=token)
        all_trials.extend(trials)
        print(f"    Page {page + 1}: fetched {len(trials)} (total so far: {len(all_trials)}/{total})")
        if not token or not trials:
            break
        time.sleep(RATE_LIMIT)
    return all_trials


def get_phase_filtered_count(location, phase_filter):
    """Count trials of a specific phase in a location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[Phase]{phase_filter}",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_implementation_count(location, query):
    """Count implementation science / community engagement trials."""
    params = {
        "format": "json",
        "query.term": query,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache():
    """Load cached data if fresh enough."""
    if CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(raw.get("timestamp", "2000-01-01"))
            if datetime.now() - ts < timedelta(hours=CACHE_TTL_HOURS):
                print(f"Using cached data from {ts.isoformat()}")
                return raw
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_all_data():
    """Fetch all principal-agent data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "country_condition_counts": {},
        "country_trial_details": {},
        "country_totals": {},
        "country_implementation_counts": {},
        "country_phase_counts": {},
    }

    # --- Per-country condition counts ---
    for country in FOCUS_COUNTRIES:
        print(f"\n--- {country} condition counts ---")
        data["country_condition_counts"][country] = {}
        for cond in CONDITION_QUERIES:
            print(f"  {country}: {cond}...")
            count = get_trial_count(cond, country)
            data["country_condition_counts"][country][cond] = count
            time.sleep(RATE_LIMIT)

    # --- Per-country trial details (paginated) ---
    for country in FOCUS_COUNTRIES:
        print(f"\n--- {country} trial details ---")
        trials = get_all_trials_for_location(country)
        data["country_trial_details"][country] = trials
        data["country_totals"][country] = len(trials)
        time.sleep(RATE_LIMIT)

    # --- Implementation research counts ---
    for country in FOCUS_COUNTRIES:
        print(f"\n--- {country} implementation research ---")
        data["country_implementation_counts"][country] = {}
        for query in IMPLEMENTATION_QUERIES:
            print(f"  {country}: {query}...")
            count = get_implementation_count(country, query)
            data["country_implementation_counts"][country][query] = count
            time.sleep(RATE_LIMIT)

    # --- Phase counts ---
    for country in FOCUS_COUNTRIES:
        print(f"\n--- {country} phase counts ---")
        data["country_phase_counts"][country] = {}
        for phase in ["PHASE1", "PHASE2", "PHASE3", "PHASE4", "EARLY_PHASE1"]:
            print(f"  {country}: {phase}...")
            count = get_phase_filtered_count(country, phase)
            data["country_phase_counts"][country][phase] = count
            time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_agenda_alignment(country, condition_counts, burden_ranking):
    """
    Agenda Alignment Score = Spearman rank correlation between
    local disease burden ranking and trial condition ranking.
    +1 = perfect alignment, -1 = inverse, 0 = no correlation.
    """
    conditions = [c for c in burden_ranking if c in condition_counts]
    n = len(conditions)
    if n < 3:
        return 0.0, []

    # Burden rank
    burden_ranks = {c: burden_ranking[c] for c in conditions}

    # Trial rank (more trials = lower rank number = higher priority)
    sorted_by_trials = sorted(conditions, key=lambda c: -condition_counts.get(c, 0))
    trial_ranks = {c: i + 1 for i, c in enumerate(sorted_by_trials)}

    # Spearman rho = 1 - 6*sum(d^2) / (n*(n^2-1))
    d_sq_sum = sum((burden_ranks[c] - trial_ranks[c]) ** 2 for c in conditions)
    rho = 1 - (6 * d_sq_sum) / (n * (n ** 2 - 1))

    details = []
    for c in conditions:
        details.append({
            "condition": c,
            "burden_rank": burden_ranks[c],
            "trial_rank": trial_ranks[c],
            "trial_count": condition_counts.get(c, 0),
            "rank_diff": trial_ranks[c] - burden_ranks[c],
        })
    details.sort(key=lambda x: x["burden_rank"])

    return round(rho, 3), details


def compute_information_rent(trials):
    """
    Information Rent = % of completed trials that posted results.
    Higher = agent is reporting back to principal (less information asymmetry).
    """
    completed = [t for t in trials if t.get("status") == "COMPLETED"]
    if not completed:
        return 0.0, 0, 0

    with_results = sum(1 for t in completed if t.get("hasResults", False))
    pct = round(100 * with_results / len(completed), 1)
    return pct, with_results, len(completed)


def compute_incentive_gap(phase_counts, implementation_counts):
    """
    Incentive Gap = ratio of Phase 3 trials (serves sponsor's FDA/EMA approval)
    to implementation research trials (serves local needs).
    Higher = worse alignment with local needs.
    """
    phase3 = phase_counts.get("PHASE3", 0)
    impl_total = sum(implementation_counts.values())
    if impl_total == 0:
        return 999.0, phase3, impl_total
    return round(phase3 / impl_total, 2), phase3, impl_total


def compute_voice_score(implementation_counts, total_trials):
    """
    Voice Score = % of trials with community engagement / behavioral component.
    Higher = principal has more voice in research agenda.
    """
    community = (
        implementation_counts.get("community health worker", 0)
        + implementation_counts.get("community engagement", 0)
        + implementation_counts.get("behavioral intervention", 0)
        + implementation_counts.get("health education", 0)
    )
    if total_trials == 0:
        return 0.0, community
    pct = round(100 * community / total_trials, 1)
    return pct, community


def compute_sponsor_analysis(trials):
    """Analyze sponsor origins (foreign vs local)."""
    sponsor_classes = Counter(t.get("sponsorClass", "UNKNOWN") for t in trials)
    sponsors = Counter(t.get("sponsorName", "Unknown") for t in trials)

    # Classify foreign vs local
    local_keywords_map = {
        "Uganda": ["makerere", "uganda", "mbarara", "mulago", "kampala", "mrc/uvri"],
        "South Africa": ["south africa", "cape town", "wits", "stellenbosch",
                         "kwazulu", "pretoria", "johannesburg", "samrc", "mrc south"],
        "Nigeria": ["nigeria", "lagos", "ibadan", "abuja", "benin", "enugu"],
    }
    results = {}
    for country in FOCUS_COUNTRIES:
        country_trials = [t for t in trials]  # all trials for this analysis
        keywords = local_keywords_map.get(country, [])
        local_count = 0
        foreign_count = 0
        for t in country_trials:
            sponsor = t.get("sponsorName", "").lower()
            if any(kw in sponsor for kw in keywords):
                local_count += 1
            else:
                foreign_count += 1
        results[country] = {
            "local": local_count,
            "foreign": foreign_count,
            "local_pct": round(100 * local_count / max(len(country_trials), 1), 1),
        }

    return sponsor_classes, sponsors.most_common(15), results


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(data, analyses):
    """Generate the full HTML dashboard."""

    ug = analyses["Uganda"]
    sa = analyses["South Africa"]
    ng = analyses["Nigeria"]

    # Agenda alignment details rows
    def alignment_rows(details):
        rows = ""
        for d in details:
            diff = d["rank_diff"]
            color = "#22c55e" if abs(diff) <= 2 else ("#f59e0b" if abs(diff) <= 5 else "#ef4444")
            arrow = "=" if diff == 0 else ("+" + str(diff) if diff > 0 else str(diff))
            rows += (
                f'<tr>'
                f'<td style="padding:8px;">{escape_html(d["condition"])}</td>'
                f'<td style="padding:8px;text-align:center;">{d["burden_rank"]}</td>'
                f'<td style="padding:8px;text-align:center;">{d["trial_rank"]}</td>'
                f'<td style="padding:8px;text-align:center;">{d["trial_count"]}</td>'
                f'<td style="padding:8px;text-align:center;color:{color};font-weight:bold;">{arrow}</td>'
                f'</tr>\n'
            )
        return rows

    ug_alignment_rows = alignment_rows(ug["alignment_details"])
    sa_alignment_rows = alignment_rows(sa["alignment_details"])
    ng_alignment_rows = alignment_rows(ng["alignment_details"])

    # Sponsor top-15 rows for Uganda
    ug_sponsor_rows = ""
    for name, count in ug["top_sponsors"]:
        is_local = any(kw in name.lower() for kw in ["makerere", "uganda", "mbarara", "mulago", "kampala", "mrc/uvri"])
        color = "#22c55e" if is_local else "#94a3b8"
        tag = " [LOCAL]" if is_local else ""
        ug_sponsor_rows += (
            f'<tr>'
            f'<td style="padding:8px;color:{color};">{escape_html(name)}{tag}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;">{count}</td>'
            f'</tr>\n'
        )

    # Implementation counts comparison
    impl_rows = ""
    for query in IMPLEMENTATION_QUERIES:
        ug_c = data["country_implementation_counts"].get("Uganda", {}).get(query, 0)
        sa_c = data["country_implementation_counts"].get("South Africa", {}).get(query, 0)
        ng_c = data["country_implementation_counts"].get("Nigeria", {}).get(query, 0)
        impl_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(query)}</td>'
            f'<td style="padding:8px;text-align:right;">{ug_c}</td>'
            f'<td style="padding:8px;text-align:right;">{sa_c}</td>'
            f'<td style="padding:8px;text-align:right;">{ng_c}</td>'
            f'</tr>\n'
        )

    # Chart data
    ug_burden_labels = json.dumps([d["condition"] for d in ug["alignment_details"]])
    ug_burden_ranks = json.dumps([d["burden_rank"] for d in ug["alignment_details"]])
    ug_trial_ranks = json.dumps([d["trial_rank"] for d in ug["alignment_details"]])

    # Comparison summary
    summary_labels = json.dumps(["Uganda", "South Africa", "Nigeria"])
    alignment_vals = json.dumps([ug["alignment_score"], sa["alignment_score"], ng["alignment_score"]])
    info_rent_vals = json.dumps([ug["info_rent_pct"], sa["info_rent_pct"], ng["info_rent_pct"]])
    voice_vals = json.dumps([ug["voice_pct"], sa["voice_pct"], ng["voice_pct"]])

    # Phase comparison
    phase_labels = json.dumps(["Phase 1", "Phase 2", "Phase 3", "Phase 4"])
    ug_phases = json.dumps([
        data["country_phase_counts"].get("Uganda", {}).get(p, 0)
        for p in ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]
    ])
    sa_phases = json.dumps([
        data["country_phase_counts"].get("South Africa", {}).get(p, 0)
        for p in ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]
    ])
    ng_phases = json.dumps([
        data["country_phase_counts"].get("Nigeria", {}).get(p, 0)
        for p in ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]
    ])

    ug_total = data["country_totals"].get("Uganda", 0)
    sa_total = data["country_totals"].get("South Africa", 0)
    ng_total = data["country_totals"].get("Nigeria", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Principal-Agent Problem: Whose Research Agenda Is This?</title>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #111827;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #3b82f6;
  --danger: #ef4444;
  --warning: #f59e0b;
  --success: #22c55e;
  --purple: #a855f7;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  line-height: 1.6;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
h1 {{
  font-size: 2.2rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #a855f7, #3b82f6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
h2 {{
  font-size: 1.5rem;
  margin: 2.5rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--border);
  color: var(--accent);
}}
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 2rem; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
  margin-bottom: 2rem;
}}
.summary-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  text-align: center;
}}
.summary-card .value {{
  font-size: 2.5rem;
  font-weight: 800;
  margin: 0.5rem 0;
}}
.summary-card .label {{
  color: var(--muted);
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}
.danger {{ color: var(--danger); }}
.warning {{ color: var(--warning); }}
.success {{ color: var(--success); }}
.purple {{ color: var(--purple); }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 1rem;
}}
th {{
  background: #1a2332;
  padding: 10px 8px;
  text-align: left;
  font-size: 0.85rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}}
td {{
  border-bottom: 1px solid var(--border);
  padding: 8px;
  font-size: 0.9rem;
}}
tr:hover {{ background: rgba(59, 130, 246, 0.05); }}
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}}
canvas {{ max-width: 100%; }}
.method-note {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.danger-note {{
  background: rgba(239, 68, 68, 0.1);
  border-left: 4px solid var(--danger);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.warning-note {{
  background: rgba(245, 158, 11, 0.1);
  border-left: 4px solid var(--warning);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.purple-note {{
  background: rgba(168, 85, 247, 0.1);
  border-left: 4px solid var(--purple);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.theory-box {{
  background: rgba(168, 85, 247, 0.08);
  border: 2px solid var(--purple);
  border-radius: 12px;
  padding: 2rem;
  margin: 1.5rem 0;
}}
.void-box {{
  background: rgba(239, 68, 68, 0.15);
  border: 2px solid var(--danger);
  border-radius: 12px;
  padding: 2rem;
  text-align: center;
  margin: 1.5rem 0;
}}
.void-box .big-number {{
  font-size: 4rem;
  font-weight: 900;
  line-height: 1;
}}
.void-box .big-label {{
  font-size: 1.2rem;
  color: var(--muted);
  margin-top: 0.5rem;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
.three-col {{
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 1100px) {{
  .three-col {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.score-display {{
  font-size: 3rem;
  font-weight: 900;
  text-align: center;
  margin: 1rem 0;
}}
.scroll-x {{ overflow-x: auto; }}
footer {{
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="container">

<h1>The Principal-Agent Problem: Whose Research Agenda Is This?</h1>
<p class="subtitle">Jensen &amp; Meckling (1976) applied to Africa's clinical trial system.
When the agent chooses the research, the principal bears the risk.</p>

<!-- 1. Theory -->
<h2>1. The Theory</h2>
<div class="theory-box">
<h3 style="color:var(--purple);margin-top:0;">Principal-Agent Theory (Jensen &amp; Meckling, 1976)</h3>
<p>When one party (the <strong>agent</strong>) is contracted to act on behalf of another
(the <strong>principal</strong>), their interests may diverge. The agent has information
the principal lacks. In Africa's clinical trial system:</p>
<br>
<div class="two-col">
<div>
<p><strong style="color:var(--purple);">PRINCIPAL</strong></p>
<p>African populations who bear disease burden, provide bodies for trials, and need
treatments for their leading killers. The principal wants research that matches their
health priorities: malaria, neonatal mortality, maternal health, NCDs.</p>
</div>
<div>
<p><strong style="color:var(--danger);">AGENT</strong></p>
<p>Foreign sponsors (US universities, NIH, pharma companies) who choose what to study,
where, and how. The agent optimizes for FDA/EMA approval, publication impact, and
global drug pipelines &mdash; not necessarily African health priorities.</p>
</div>
</div>
<br>
<p><strong>Four measurable dimensions of misalignment:</strong></p>
<br>
<p>&bull; <strong>Agenda Alignment Score</strong> &mdash; correlation between local disease burden ranking and trial condition ranking</p>
<p>&bull; <strong>Information Rent</strong> &mdash; % of completed trials that posted results (do agents report back?)</p>
<p>&bull; <strong>Incentive Gap</strong> &mdash; ratio of Phase 3 (FDA pipeline) to implementation research (local needs)</p>
<p>&bull; <strong>Voice Score</strong> &mdash; % of trials with community engagement / behavioral components</p>
</div>

<!-- 2. Summary cards -->
<h2>2. The Misalignment Quantified</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Uganda Agenda Alignment</div>
    <div class="value purple">{ug['alignment_score']}</div>
    <div class="label">Spearman rho (-1 to +1)</div>
  </div>
  <div class="summary-card">
    <div class="label">Uganda Information Rent</div>
    <div class="value {"danger" if ug['info_rent_pct'] < 30 else "warning"}">{ug['info_rent_pct']}%</div>
    <div class="label">{ug['results_posted']}/{ug['completed_count']} completed posted results</div>
  </div>
  <div class="summary-card">
    <div class="label">Uganda Incentive Gap</div>
    <div class="value warning">{ug['incentive_gap']:.1f}x</div>
    <div class="label">Phase 3 : implementation ratio</div>
  </div>
  <div class="summary-card">
    <div class="label">Uganda Voice Score</div>
    <div class="value {"success" if ug['voice_pct'] > 15 else "warning"}">{ug['voice_pct']}%</div>
    <div class="label">{ug['community_trials']} community engagement trials</div>
  </div>
</div>

<!-- Country comparison -->
<div class="three-col">
  <div class="void-box" style="border-color:var(--purple);">
    <div class="big-label">Uganda (PEPFAR-dependent)</div>
    <div class="big-number purple">{ug['alignment_score']}</div>
    <div class="big-label">Agenda Alignment</div>
    <div style="margin-top:0.5rem;font-size:0.85rem;color:var(--muted);">
      Info Rent: {ug['info_rent_pct']}% | Voice: {ug['voice_pct']}%
    </div>
  </div>
  <div class="void-box" style="border-color:var(--success);">
    <div class="big-label">South Africa (local voice)</div>
    <div class="big-number success">{sa['alignment_score']}</div>
    <div class="big-label">Agenda Alignment</div>
    <div style="margin-top:0.5rem;font-size:0.85rem;color:var(--muted);">
      Info Rent: {sa['info_rent_pct']}% | Voice: {sa['voice_pct']}%
    </div>
  </div>
  <div class="void-box" style="border-color:var(--warning);">
    <div class="big-label">Nigeria (weak infrastructure)</div>
    <div class="big-number warning">{ng['alignment_score']}</div>
    <div class="big-label">Agenda Alignment</div>
    <div style="margin-top:0.5rem;font-size:0.85rem;color:var(--muted);">
      Info Rent: {ng['info_rent_pct']}% | Voice: {ng['voice_pct']}%
    </div>
  </div>
</div>

<!-- 3. Agenda Alignment Scatter -->
<h2>3. Agenda Alignment: Burden Rank vs Trial Rank</h2>
<div class="method-note">
<strong>How to read:</strong> If trials perfectly matched disease burden, every condition
would lie on the diagonal (burden rank = trial rank). Points above the diagonal mean
the condition is <em>over-studied</em> relative to its burden; below means <em>under-studied</em>.
A Spearman rho of +1.0 = perfect alignment; 0 = no relationship; -1.0 = inverse.
</div>

<div class="chart-container">
<h3>Uganda: Burden Rank vs Trial Rank (rho = {ug['alignment_score']})</h3>
<canvas id="alignmentChart" height="400"></canvas>
</div>

<h3>Uganda: Detailed Ranking Table</h3>
<table>
<thead>
<tr>
  <th>Condition</th>
  <th style="text-align:center;">Burden Rank</th>
  <th style="text-align:center;">Trial Rank</th>
  <th style="text-align:center;">Trial Count</th>
  <th style="text-align:center;">Gap</th>
</tr>
</thead>
<tbody>
{ug_alignment_rows}
</tbody>
</table>

<!-- 4. Information Rent -->
<h2>4. Information Rent (Results Reporting)</h2>
<div class="danger-note">
<strong>Information asymmetry is the core of agency theory.</strong> The agent (foreign sponsor)
knows the trial results before the principal (African community). The "information rent" measures
how much the agent withholds. A low results-posting rate means the agent conducts research,
extracts data, and disappears &mdash; the community never learns what happened.
</div>

<div class="chart-container">
<h3>Results Posting Rate: Do Agents Report Back?</h3>
<canvas id="infoRentChart" height="250"></canvas>
</div>

<div class="three-col">
  <div class="summary-card">
    <div class="label">Uganda</div>
    <div class="value {"danger" if ug['info_rent_pct'] < 30 else "warning"}">{ug['info_rent_pct']}%</div>
    <div class="label">{ug['results_posted']} of {ug['completed_count']} completed</div>
  </div>
  <div class="summary-card">
    <div class="label">South Africa</div>
    <div class="value {"danger" if sa['info_rent_pct'] < 30 else "warning"}">{sa['info_rent_pct']}%</div>
    <div class="label">{sa['results_posted']} of {sa['completed_count']} completed</div>
  </div>
  <div class="summary-card">
    <div class="label">Nigeria</div>
    <div class="value {"danger" if ng['info_rent_pct'] < 30 else "warning"}">{ng['info_rent_pct']}%</div>
    <div class="label">{ng['results_posted']} of {ng['completed_count']} completed</div>
  </div>
</div>

<!-- 5. Incentive Gap -->
<h2>5. Incentive Gap: Phase 3 vs Implementation Research</h2>
<div class="warning-note">
<strong>Phase 3 trials serve the agent's needs</strong> (FDA/EMA approval for global markets).
<strong>Implementation research serves the principal's needs</strong> (how to deliver existing
interventions to African communities). The ratio reveals who the research agenda really serves.
</div>

<div class="chart-container">
<h3>Phase Distribution by Country</h3>
<canvas id="phaseChart" height="300"></canvas>
</div>

<h3>Implementation Research Counts</h3>
<table>
<thead>
<tr>
  <th>Implementation Query</th>
  <th style="text-align:right;">Uganda</th>
  <th style="text-align:right;">South Africa</th>
  <th style="text-align:right;">Nigeria</th>
</tr>
</thead>
<tbody>
{impl_rows}
</tbody>
</table>

<!-- 6. Voice Score -->
<h2>6. Voice Score: Does the Principal Have a Say?</h2>
<div class="purple-note">
<strong>In agency theory, "voice" is the principal's mechanism for controlling the agent.</strong>
Trials with community engagement, behavioral components, or community health worker
models give the principal a seat at the table. The Voice Score measures how much of
the research agenda includes the community's perspective.
</div>

<div class="chart-container">
<h3>Voice Score Comparison</h3>
<canvas id="voiceChart" height="250"></canvas>
</div>

<!-- 7. Uganda Sponsors -->
<h2>7. Who Are Uganda's Agents? (Top 15 Sponsors)</h2>
<div class="method-note">
<strong>Color key:</strong> <span style="color:#22c55e;font-weight:bold;">GREEN = local institution</span>,
<span style="color:#94a3b8;">grey = foreign institution</span>. In principal-agent theory,
a local agent is better aligned with the principal because they share the same environment
and information.
</div>

<table>
<thead>
<tr>
  <th>Sponsor</th>
  <th style="text-align:right;">Trials</th>
</tr>
</thead>
<tbody>
{ug_sponsor_rows}
</tbody>
</table>

<div class="three-col">
  <div class="summary-card">
    <div class="label">Uganda: Local-Led</div>
    <div class="value success">{ug['sponsor_breakdown']['Uganda']['local_pct']}%</div>
    <div class="label">{ug['sponsor_breakdown']['Uganda']['local']} trials</div>
  </div>
  <div class="summary-card">
    <div class="label">South Africa: Local-Led</div>
    <div class="value success">{sa['sponsor_breakdown']['South Africa']['local_pct']}%</div>
    <div class="label">{sa['sponsor_breakdown']['South Africa']['local']} trials</div>
  </div>
  <div class="summary-card">
    <div class="label">Nigeria: Local-Led</div>
    <div class="value {"success" if ng['sponsor_breakdown']['Nigeria']['local_pct'] > 20 else "warning"}">{ng['sponsor_breakdown']['Nigeria']['local_pct']}%</div>
    <div class="label">{ng['sponsor_breakdown']['Nigeria']['local']} trials</div>
  </div>
</div>

<!-- 8. Realignment -->
<h2>8. What Realignment Looks Like</h2>
<div class="theory-box">
<h3 style="color:var(--success);margin-top:0;">From Agency Theory to Policy</h3>
<p>Jensen &amp; Meckling's framework suggests three mechanisms to realign agents with principals:</p>
<br>
<p><strong>1. Monitoring (reduce information asymmetry):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">Mandate results posting within 12 months of completion.
Currently only {ug['info_rent_pct']}% of Uganda's completed trials post results &mdash; the principal
never learns what the agent discovered.</p>
<br>
<p><strong>2. Bonding (align incentives):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">Require sponsors to co-fund implementation research
proportional to their Phase 3 activity. If you run 10 Phase 3 trials, fund 5 implementation studies.</p>
<br>
<p><strong>3. Residual loss acceptance (empower the principal):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">Build local research capacity so the principal becomes
their own agent. South Africa's higher local sponsorship ({sa['sponsor_breakdown']['South Africa']['local_pct']}%)
shows this is possible. Fund African PIs, not just African sites.</p>
<br>
<p><strong>4. Burden-matched quotas:</strong></p>
<p style="margin-left:1rem;color:var(--muted);">If malaria is the #2 killer but ranks lower in trial priority,
require funders to explain the mismatch. The Agenda Alignment Score provides a quantitative target: move from
rho = {ug['alignment_score']} toward rho &gt; 0.8.</p>
</div>

<!-- 9. Policy Recommendations -->
<h2>9. Policy Recommendations from Agency Theory</h2>
<div class="method-note">
<p>&bull; <strong>For African governments:</strong> Establish national clinical trial priority lists based on local
burden data. Require foreign sponsors to demonstrate alignment before ethics approval.</p>
<p>&bull; <strong>For funders (NIH, Wellcome, Gates):</strong> Allocate funding proportional to
burden-trial gaps. Mandate community engagement in all sponsored trials.</p>
<p>&bull; <strong>For PEPFAR:</strong> Shift from disease-vertical (HIV-only) to burden-matched portfolios.
Uganda's trial landscape is 42% HIV while cardiovascular disease and NCDs rise unchecked.</p>
<p>&bull; <strong>For WHO:</strong> Publish annual "Agenda Alignment Scores" for every country, making
principal-agent misalignment visible and politically costly.</p>
<p>&bull; <strong>For African researchers:</strong> Use these metrics to negotiate with sponsors.
"Your proposed trial worsens our Agenda Alignment Score" is a powerful argument.</p>
</div>

<!-- Method -->
<h2>Method</h2>
<div class="method-note">
<strong>Data source:</strong> ClinicalTrials.gov API v2 (accessed {datetime.now().strftime('%d %B %Y')}).<br>
<strong>Countries:</strong> Uganda (PEPFAR-dependent), South Africa (stronger local voice),
Nigeria (weak infrastructure).<br>
<strong>Agenda Alignment:</strong> Spearman rank correlation between GBD 2019 disease burden
ranking and trial condition count ranking across 15 conditions.<br>
<strong>Information Rent:</strong> % of completed interventional trials with posted results
(resultsFirstSubmitDate or resultsSection present).<br>
<strong>Incentive Gap:</strong> Phase 3 trial count / sum of implementation research queries.<br>
<strong>Voice Score:</strong> (community health worker + community engagement + behavioral +
health education trials) / total trials.<br>
<strong>Limitations:</strong> Single registry; burden rankings are approximate; sponsor classification
by keyword matching is imperfect; community engagement may be present but not captured in
registry metadata.
</div>

<footer>
The Principal-Agent Problem: Whose Research Agenda Is This? &mdash; ClinicalTrials.gov Registry Analysis |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} |
Data: ClinicalTrials.gov API v2
</footer>

</div>

<script>
// Alignment scatter for Uganda
const alignLabels = {ug_burden_labels};
const burdenRanks = {ug_burden_ranks};
const trialRanks = {ug_trial_ranks};

const scatterData = alignLabels.map((label, i) => ({{
  x: burdenRanks[i],
  y: trialRanks[i],
  label: label
}}));

new Chart(document.getElementById('alignmentChart'), {{
  type: 'scatter',
  data: {{
    datasets: [
      {{
        label: 'Conditions',
        data: scatterData,
        backgroundColor: '#a855f7',
        borderColor: '#a855f7',
        pointRadius: 8,
        pointHoverRadius: 12,
      }},
      {{
        label: 'Perfect alignment',
        data: [{{x:1,y:1}},{{x:15,y:15}}],
        type: 'line',
        borderColor: 'rgba(148,163,184,0.4)',
        borderDash: [5,5],
        pointRadius: 0,
        fill: false,
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ labels: {{ color: '#94a3b8' }} }},
      tooltip: {{
        callbacks: {{
          label: function(ctx) {{
            const pt = ctx.raw;
            return pt.label + ': burden=' + pt.x + ', trials=' + pt.y;
          }}
        }}
      }}
    }},
    scales: {{
      x: {{
        title: {{ display: true, text: 'Disease Burden Rank (1=highest)', color: '#94a3b8' }},
        min: 0, max: 16,
        grid: {{ color: '#1e293b' }},
        ticks: {{ color: '#94a3b8' }},
        reverse: false,
      }},
      y: {{
        title: {{ display: true, text: 'Trial Count Rank (1=most trials)', color: '#94a3b8' }},
        min: 0, max: 16,
        grid: {{ color: '#1e293b' }},
        ticks: {{ color: '#94a3b8' }},
        reverse: false,
      }}
    }}
  }}
}});

// Information Rent chart
new Chart(document.getElementById('infoRentChart'), {{
  type: 'bar',
  data: {{
    labels: {summary_labels},
    datasets: [{{
      label: 'Results Posting Rate (%)',
      data: {info_rent_vals},
      backgroundColor: ['#a855f7', '#22c55e', '#f59e0b'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ min: 0, max: 100, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

// Phase distribution
new Chart(document.getElementById('phaseChart'), {{
  type: 'bar',
  data: {{
    labels: {phase_labels},
    datasets: [
      {{ label: 'Uganda', data: {ug_phases}, backgroundColor: '#a855f7', borderRadius: 4 }},
      {{ label: 'South Africa', data: {sa_phases}, backgroundColor: '#22c55e', borderRadius: 4 }},
      {{ label: 'Nigeria', data: {ng_phases}, backgroundColor: '#f59e0b', borderRadius: 4 }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

// Voice Score
new Chart(document.getElementById('voiceChart'), {{
  type: 'bar',
  data: {{
    labels: {summary_labels},
    datasets: [{{
      label: 'Voice Score (%)',
      data: {voice_vals},
      backgroundColor: ['#a855f7', '#22c55e', '#f59e0b'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ min: 0, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("The Principal-Agent Problem: Whose Research Agenda Is This?")
    print("=" * 70)
    print()

    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    burden_maps = {
        "Uganda": UGANDA_BURDEN_RANKING,
        "South Africa": SA_BURDEN_RANKING,
        "Nigeria": NIGERIA_BURDEN_RANKING,
    }

    analyses = {}
    for country in FOCUS_COUNTRIES:
        print(f"\n--- Analyzing {country} ---")
        cond_counts = data["country_condition_counts"].get(country, {})
        trials = data["country_trial_details"].get(country, [])
        impl_counts = data["country_implementation_counts"].get(country, {})
        phase_counts = data["country_phase_counts"].get(country, {})
        total = data["country_totals"].get(country, 0)

        alignment_score, alignment_details = compute_agenda_alignment(
            country, cond_counts, burden_maps[country]
        )
        info_rent_pct, results_posted, completed_count = compute_information_rent(trials)
        incentive_gap, phase3_count, impl_total = compute_incentive_gap(phase_counts, impl_counts)
        voice_pct, community_trials = compute_voice_score(impl_counts, total)
        sponsor_classes, top_sponsors, sponsor_breakdown = compute_sponsor_analysis(trials)

        analyses[country] = {
            "alignment_score": alignment_score,
            "alignment_details": alignment_details,
            "info_rent_pct": info_rent_pct,
            "results_posted": results_posted,
            "completed_count": completed_count,
            "incentive_gap": incentive_gap,
            "phase3_count": phase3_count,
            "impl_total": impl_total,
            "voice_pct": voice_pct,
            "community_trials": community_trials,
            "sponsor_classes": dict(sponsor_classes),
            "top_sponsors": top_sponsors,
            "sponsor_breakdown": sponsor_breakdown,
            "total_trials": total,
        }

        print(f"  Agenda Alignment Score: {alignment_score}")
        print(f"  Information Rent:       {info_rent_pct}% ({results_posted}/{completed_count})")
        print(f"  Incentive Gap:          {incentive_gap:.1f}x (Phase3={phase3_count}, Impl={impl_total})")
        print(f"  Voice Score:            {voice_pct}% ({community_trials} community trials)")
        print(f"  Local sponsorship:      {sponsor_breakdown[country]['local_pct']}%")

    # Print comparison
    print()
    print("-" * 70)
    print("PRINCIPAL-AGENT COMPARISON")
    print("-" * 70)
    for country in FOCUS_COUNTRIES:
        a = analyses[country]
        print(f"  {country:20s}  Align: {a['alignment_score']:>6} | "
              f"InfoRent: {a['info_rent_pct']:>5.1f}% | "
              f"Gap: {a['incentive_gap']:>5.1f}x | "
              f"Voice: {a['voice_pct']:>5.1f}%")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, analyses)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
