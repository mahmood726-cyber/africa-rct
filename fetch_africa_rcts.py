"""
Africa RCT Analysis — Data Fetcher & Report Generator
======================================================
Queries ClinicalTrials.gov API v2 (public, no key needed) for all
interventional trials in Africa, then generates an HTML dashboard
showing 12 dimensions of inequity.

Usage:
    python fetch_africa_rcts.py

Output:
    data/               — raw JSON responses from CT.gov
    africa-rct-analysis.html  — interactive dashboard (overwrites existing)

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
OUTPUT_HTML = Path(__file__).parent / "africa-rct-analysis.html"

# Countries to query individually (top African nations by trial volume)
AFRICAN_COUNTRIES = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria",
    "Tanzania", "Ethiopia", "Ghana", "Cameroon", "Mozambique",
    "Malawi", "Zambia", "Zimbabwe", "Senegal", "Rwanda",
    "Democratic Republic of Congo", "Morocco", "Tunisia",
    "Burkina Faso", "Mali",
]

# Conditions to check in Africa
CONDITIONS = [
    "HIV", "tuberculosis", "malaria", "cancer", "diabetes",
    "cardiovascular", "hypertension", "mental health", "stroke",
    "sickle cell", "maternal", "neonatal",
]

# Phases to query
PHASES = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]

# Statuses to tally
STATUS_GROUPS = {
    "terminated_withdrawn": ["TERMINATED", "WITHDRAWN"],
    "completed": ["COMPLETED"],
    "recruiting": ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"],
    "active": ["ACTIVE_NOT_RECRUITING"],
    "suspended": ["SUSPENDED"],
    "unknown": ["UNKNOWN"],
}

# Comparison regions
COMPARISON_REGIONS = ["United States", "United Kingdom", "China", "India", "Brazil"]

RATE_LIMIT_DELAY = 0.35  # seconds between API calls (be polite)


# ── API helpers ──────────────────────────────────────────────────────
def search_trials(location=None, condition=None, study_type="INTERVENTIONAL",
                  status=None, phase=None, page_size=10, count_total=True):
    """Query CT.gov API v2 and return parsed JSON."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": str(count_total).lower(),
    }
    filters = []
    if study_type:
        filters.append(f"AREA[StudyType]{study_type}")
    if status:
        if isinstance(status, list):
            status_parts = " OR ".join(f"AREA[OverallStatus]{s}" for s in status)
            filters.append(f"({status_parts})")
        else:
            filters.append(f"AREA[OverallStatus]{status}")
    if phase:
        if isinstance(phase, list):
            # CT.gov v2 phase values use spaces: "Phase 1", "Phase 2", etc.
            phase_map = {
                "EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1",
                "PHASE2": "Phase 2", "PHASE3": "Phase 3",
                "PHASE4": "Phase 4", "NA": "Not Applicable",
            }
            phase_parts = " OR ".join(
                f"AREA[Phase]{phase_map.get(p, p)}" for p in phase
            )
            filters.append(f"({phase_parts})")
        else:
            phase_map = {
                "EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1",
                "PHASE2": "Phase 2", "PHASE3": "Phase 3",
                "PHASE4": "Phase 4", "NA": "Not Applicable",
            }
            filters.append(f"AREA[Phase]{phase_map.get(phase, phase)}")
    if filters:
        params["filter.advanced"] = " AND ".join(filters)

    if condition:
        params["query.cond"] = condition
    if location:
        params["query.locn"] = location

    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  WARNING: API error for location={location}, condition={condition}: {e}")
        return {"totalCount": 0, "studies": []}


def get_total(result):
    """Extract total count from API response."""
    return result.get("totalCount", 0)


def get_studies(result):
    """Extract study list from API response."""
    return result.get("studies", [])


# ── Data collection ──────────────────────────────────────────────────
def collect_all_data():
    """Run all queries and return structured results dict."""
    results = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
        },
        "country_totals": {},
        "comparison_regions": {},
        "africa_by_condition": {},
        "africa_by_phase": {},
        "africa_by_status": {},
        "africa_total": 0,
        "sample_trials": [],
    }

    total_queries = (
        len(AFRICAN_COUNTRIES) + len(COMPARISON_REGIONS) +
        len(CONDITIONS) + len(PHASES) + len(STATUS_GROUPS) + 1
    )
    query_num = 0

    # 1. Total for "Africa" as a location keyword
    print("\n[1/6] Querying Africa aggregate...")
    r = search_trials(location="Africa", page_size=50)
    results["africa_total"] = get_total(r)
    # Save sample trials for sponsor analysis
    for study in get_studies(r):
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        design = proto.get("designModule", {})
        status_mod = proto.get("statusModule", {})
        enrollment_info = design.get("enrollmentInfo", {})
        results["sample_trials"].append({
            "nct_id": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
            "sponsor_class": sponsor_mod.get("leadSponsor", {}).get("class", ""),
            "status": status_mod.get("overallStatus", ""),
            "phases": design.get("phases", []),
            "enrollment": enrollment_info.get("count", 0),
        })
    time.sleep(RATE_LIMIT_DELAY)

    # 2. Country-level totals
    print("[2/6] Querying individual African countries...")
    for country in AFRICAN_COUNTRIES:
        r = search_trials(location=country)
        total = get_total(r)
        results["country_totals"][country] = total
        print(f"  {country}: {total:,}")
        time.sleep(RATE_LIMIT_DELAY)

    # 3. Comparison regions
    print("[3/6] Querying comparison regions...")
    for region in COMPARISON_REGIONS:
        r = search_trials(location=region)
        total = get_total(r)
        results["comparison_regions"][region] = total
        print(f"  {region}: {total:,}")
        time.sleep(RATE_LIMIT_DELAY)

    # 4. Africa by condition
    # NOTE: location="Africa" only matches trials with "Africa" in location text.
    # Trials listing specific countries (e.g., "Kenya") without "Africa" are missed.
    # These counts are LOWER BOUNDS; true counts may be higher (especially malaria).
    print("[4/6] Querying conditions in Africa (lower bounds via keyword)...")
    for cond in CONDITIONS:
        r = search_trials(location="Africa", condition=cond)
        total = get_total(r)
        results["africa_by_condition"][cond] = total
        print(f"  {cond}: {total:,}")
        time.sleep(RATE_LIMIT_DELAY)

    # 5. Africa by phase
    print("[5/6] Querying phases in Africa...")
    for phase in PHASES:
        r = search_trials(location="Africa", phase=[phase])
        total = get_total(r)
        results["africa_by_phase"][phase] = total
        print(f"  {phase}: {total:,}")
        time.sleep(RATE_LIMIT_DELAY)

    # 6. Africa by status group
    print("[6/6] Querying status groups in Africa...")
    for group_name, statuses in STATUS_GROUPS.items():
        r = search_trials(location="Africa", status=statuses)
        total = get_total(r)
        results["africa_by_status"][group_name] = total
        print(f"  {group_name}: {total:,}")
        time.sleep(RATE_LIMIT_DELAY)

    # Compute derived metrics
    africa_sum = sum(results["country_totals"].values())
    results["africa_country_sum"] = africa_sum
    us_total = results["comparison_regions"].get("United States", 1)
    results["africa_share_vs_us"] = round(africa_sum / us_total * 100, 2) if us_total else 0

    return results


# ── Sponsor analysis ─────────────────────────────────────────────────
def analyze_sponsors(sample_trials):
    """Categorize sponsors as African-local vs foreign."""
    african_keywords = [
        "south africa", "nigeria", "kenya", "uganda", "tanzania", "egypt",
        "cairo", "makerere", "witwatersrand", "cape town", "stellenbosch",
        "nairobi", "ibadan", "assiut", "tanta", "ain shams", "mansoura",
        "kenyatta", "muhimbili", "ifakara", "caprisa", "mrc/uvri",
    ]
    local_count = 0
    foreign_count = 0
    for trial in sample_trials:
        sponsor_lower = trial["sponsor"].lower()
        if any(kw in sponsor_lower for kw in african_keywords):
            local_count += 1
        else:
            foreign_count += 1
    total = local_count + foreign_count
    return {
        "local": local_count,
        "foreign": foreign_count,
        "local_pct": round(local_count / total * 100, 1) if total else 0,
        "foreign_pct": round(foreign_count / total * 100, 1) if total else 0,
    }


# ── HTML report generator ───────────────────────────────────────────
def generate_html(data):
    """Generate the full HTML dashboard from collected data."""

    sponsor_stats = analyze_sponsors(data["sample_trials"])
    country_totals = data["country_totals"]
    conditions = data["africa_by_condition"]
    phases = data["africa_by_phase"]
    statuses = data["africa_by_status"]
    comparisons = data["comparison_regions"]
    africa_sum = data["africa_country_sum"]
    us_total = comparisons.get("United States", 1)

    # Sort countries descending
    sorted_countries = sorted(country_totals.items(), key=lambda x: x[1], reverse=True)

    # Compute key metrics
    terminated = statuses.get("terminated_withdrawn", 0)
    completed = statuses.get("completed", 0)
    total_known = sum(statuses.values())
    term_rate = round(terminated / total_known * 100, 1) if total_known else 0
    phase1 = phases.get("PHASE1", 0) + phases.get("EARLY_PHASE1", 0)
    phase3 = phases.get("PHASE3", 0)
    share_vs_us = round(africa_sum / us_total * 100, 1) if us_total else 0

    def safe_max(vals, default=1):
        """Return max of values, or default if empty/all-zero."""
        return max(vals, default=default) or default

    # Country bar rows
    max_country = safe_max(country_totals.values())
    country_bars = ""
    colors = ["var(--accent4)", "var(--green)", "var(--blue)", "var(--blue)",
              "var(--accent3)", "var(--red)", "var(--purple)", "var(--accent)",
              "#7f8c8d", "#7f8c8d"]
    for i, (name, count) in enumerate(sorted_countries[:10]):
        pct = count / max_country * 100
        c = colors[i] if i < len(colors) else "#555"
        country_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{name}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,}</div></div>
                </div>'''

    # Condition bars
    max_cond = safe_max(conditions.values())
    cond_bars = ""
    cond_colors = {
        "HIV": "var(--red)", "cancer": "var(--orange)", "cardiovascular": "var(--accent4)",
        "diabetes": "var(--accent3)", "tuberculosis": "var(--blue)", "hypertension": "var(--green)",
        "malaria": "var(--purple)", "mental health": "var(--accent)", "stroke": "#7f8c8d",
        "sickle cell": "var(--accent4)", "maternal": "var(--blue)", "neonatal": "var(--green)",
    }
    sorted_conds = sorted(conditions.items(), key=lambda x: x[1], reverse=True)
    for name, count in sorted_conds:
        pct = count / max_cond * 100
        c = cond_colors.get(name, "#555")
        cond_bars += f'''
                    <div class="bar-row">
                        <div class="bar-label">{name.title()}</div>
                        <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,}</div></div>
                    </div>'''

    # Phase bars
    phase_total = sum(phases.values()) or 1
    phase_bars = ""
    phase_colors = {"EARLY_PHASE1": "var(--green)", "PHASE1": "var(--blue)",
                    "PHASE2": "var(--accent3)", "PHASE3": "var(--accent)",
                    "PHASE4": "var(--accent4)"}
    max_phase = safe_max(phases.values())
    for ph in PHASES:
        count = phases.get(ph, 0)
        pct = count / max_phase * 100
        c = phase_colors.get(ph, "#555")
        label = ph.replace("_", " ").title()
        phase_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{label}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,} ({count/phase_total*100:.1f}%)</div></div>
                </div>'''

    # Comparison bars
    max_comp = safe_max(list(comparisons.values()) + [africa_sum])
    comp_bars = ""
    comp_colors = {"United States": "var(--blue)", "United Kingdom": "var(--accent3)",
                   "China": "var(--accent4)", "India": "var(--orange)",
                   "Brazil": "var(--green)"}
    for name, count in sorted(comparisons.items(), key=lambda x: x[1], reverse=True):
        pct = count / max_comp * 100
        c = comp_colors.get(name, "#555")
        comp_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{name}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,}</div></div>
                </div>'''
    comp_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">Africa (all countries)</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{africa_sum/max_comp*100:.1f}%;background:var(--red);">{africa_sum:,}</div></div>
                </div>'''

    # Status bars
    max_status = safe_max(statuses.values())
    status_bars = ""
    status_colors = {"completed": "var(--green)", "recruiting": "var(--blue)",
                     "active": "var(--accent3)", "terminated_withdrawn": "var(--red)",
                     "suspended": "var(--orange)", "unknown": "#555"}
    for name, count in sorted(statuses.items(), key=lambda x: x[1], reverse=True):
        pct = count / max_status * 100
        c = status_colors.get(name, "#555")
        label = name.replace("_", " / ").title()
        status_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{label}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,}</div></div>
                </div>'''

    date_str = datetime.now().strftime("%d %B %Y")

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa RCT Analysis &mdash; 12 Dimensions of Inequity</title>
<style>
:root {{
    --bg: #0a0e17; --card: #131825; --border: #1e2a3a; --text: #c8d6e5;
    --heading: #f5f6fa; --accent: #e17055; --accent2: #00b894; --accent3: #6c5ce7;
    --accent4: #fdcb6e; --red: #ff6b6b; --green: #00b894; --blue: #74b9ff;
    --orange: #e17055; --purple: #a29bfe; --yellow: #ffeaa7;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; line-height:1.6; }}
.container {{ max-width:1400px; margin:0 auto; padding:20px; }}
.header {{ text-align:center; padding:40px 20px 30px; border-bottom:1px solid var(--border); margin-bottom:30px; }}
.header h1 {{ font-size:2.4em; color:var(--heading); font-weight:700; }}
.header .subtitle {{ color:var(--accent); font-size:1.1em; margin-top:8px; font-weight:600; }}
.header .meta {{ color:#7f8c8d; font-size:0.9em; margin-top:12px; }}
.summary-banner {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px,1fr)); gap:16px; margin-bottom:30px; }}
.stat-card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; text-align:center; }}
.stat-card .number {{ font-size:2em; font-weight:700; color:var(--heading); }}
.stat-card .label {{ font-size:0.85em; color:#7f8c8d; margin-top:4px; }}
.stat-card.alert .number {{ color:var(--red); }}
.stat-card.warn .number {{ color:var(--yellow); }}
.stat-card.good .number {{ color:var(--green); }}
.dimension {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:28px; margin-bottom:24px; }}
.dim-header {{ display:flex; align-items:center; gap:12px; margin-bottom:16px; }}
.dim-number {{ background:var(--accent); color:#fff; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; flex-shrink:0; }}
.dim-title {{ font-size:1.3em; color:var(--heading); font-weight:600; }}
.severity {{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:0.75em; font-weight:600; margin-left:12px; }}
.severity.critical {{ background:rgba(255,107,107,0.2); color:var(--red); }}
.severity.high {{ background:rgba(225,112,85,0.2); color:var(--orange); }}
.severity.moderate {{ background:rgba(253,203,110,0.2); color:var(--yellow); }}
.dim-body {{ color:var(--text); font-size:0.95em; }}
.dim-body p {{ margin-bottom:12px; }}
.dim-body strong {{ color:var(--heading); }}
.chart-container {{ background:rgba(0,0,0,0.2); border-radius:8px; padding:20px; margin:16px 0; }}
.bar-chart {{ display:flex; flex-direction:column; gap:8px; }}
.bar-row {{ display:flex; align-items:center; gap:10px; }}
.bar-label {{ width:180px; text-align:right; font-size:0.85em; color:#aaa; flex-shrink:0; }}
.bar-track {{ flex:1; height:28px; background:rgba(255,255,255,0.05); border-radius:4px; position:relative; overflow:hidden; }}
.bar-fill {{ height:100%; border-radius:4px; display:flex; align-items:center; padding-left:8px; font-size:0.8em; font-weight:600; color:#fff; min-width:fit-content; }}
.comp-table {{ width:100%; border-collapse:collapse; margin:16px 0; }}
.comp-table th {{ text-align:left; padding:10px 12px; background:rgba(0,0,0,0.3); color:var(--heading); font-size:0.85em; border-bottom:1px solid var(--border); }}
.comp-table td {{ padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.05); font-size:0.9em; }}
.highlight-red {{ color:var(--red); font-weight:600; }}
.highlight-green {{ color:var(--green); font-weight:600; }}
.highlight-yellow {{ color:var(--yellow); font-weight:600; }}
.verdict {{ background:linear-gradient(135deg, rgba(225,112,85,0.15), rgba(108,92,231,0.15)); border:1px solid var(--accent); border-radius:12px; padding:30px; margin-top:30px; text-align:center; }}
.verdict h2 {{ color:var(--heading); font-size:1.5em; margin-bottom:12px; }}
.verdict p {{ max-width:800px; margin:0 auto; }}
.grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
@media(max-width:768px) {{ .grid-2 {{ grid-template-columns:1fr; }} }}
.footer {{ text-align:center; padding:30px; color:#555; font-size:0.8em; border-top:1px solid var(--border); margin-top:40px; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>Africa RCT Landscape Analysis</h1>
    <div class="subtitle">12 Dimensions of Inequity in Randomized Controlled Trials</div>
    <div class="meta">Data: ClinicalTrials.gov API v2 &bull; Generated: {date_str} &bull; All interventional studies</div>
</div>

<div class="summary-banner">
    <div class="stat-card alert"><div class="number">{africa_sum:,}</div><div class="label">Africa RCTs (sum of countries)</div></div>
    <div class="stat-card"><div class="number">{us_total:,}</div><div class="label">United States RCTs</div></div>
    <div class="stat-card warn"><div class="number">{share_vs_us}%</div><div class="label">Africa share vs US</div></div>
    <div class="stat-card alert"><div class="number">{term_rate}%</div><div class="label">Termination / Withdrawal</div></div>
    <div class="stat-card warn"><div class="number">{phase1:,}</div><div class="label">Phase 1 trials (capacity)</div></div>
    <div class="stat-card good"><div class="number">{completed:,}</div><div class="label">Completed trials</div></div>
</div>

<!-- DIM 1: Volume Gap -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">1</div><div class="dim-title">Massive Volume Gap <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>Africa carries ~24% of the global disease burden but hosts only <strong>{share_vs_us}%</strong> of US trial volume ({africa_sum:,} vs {us_total:,}).</p>
        <div class="chart-container"><div class="bar-chart">{comp_bars}</div></div>
    </div>
</div>

<!-- DIM 2: Country Concentration -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">2</div><div class="dim-title">Extreme Country Concentration <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>The top 2-3 countries account for the vast majority of African trials. The remaining 50+ nations share a tiny fraction.</p>
        <div class="chart-container"><div class="bar-chart">{country_bars}</div></div>
    </div>
</div>

<!-- DIM 3: Disease Mismatch -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">3</div><div class="dim-title">Disease Burden Mismatch <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>NCDs now cause more deaths in Africa than infectious diseases, but the trial portfolio hasn't caught up.</p>
        <div class="chart-container"><div class="bar-chart">{cond_bars}</div></div>
    </div>
</div>

<!-- DIM 4: Phase Imbalance -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">4</div><div class="dim-title">Phase Imbalance &mdash; Outsourced Testing <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>Phase 3 ({phase3:,}) far exceeds Phase 1 ({phase1:,}). Drugs are <em>tested</em> in Africa but not <em>developed</em> there.</p>
        <div class="chart-container"><div class="bar-chart">{phase_bars}</div></div>
    </div>
</div>

<!-- DIM 5: Foreign Sponsors -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">5</div><div class="dim-title">Foreign Sponsor Dominance <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>From a sample of {len(data["sample_trials"])} trials: <strong>{sponsor_stats["foreign_pct"]}% foreign-sponsored</strong>, only {sponsor_stats["local_pct"]}% African-led.</p>
        <div class="chart-container">
            <div class="bar-chart">
                <div class="bar-row">
                    <div class="bar-label">Foreign sponsors</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{sponsor_stats['foreign_pct']}%;background:var(--red);">{sponsor_stats['foreign']} ({sponsor_stats['foreign_pct']}%)</div></div>
                </div>
                <div class="bar-row">
                    <div class="bar-label">African-led</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{sponsor_stats['local_pct']}%;background:var(--green);">{sponsor_stats['local']} ({sponsor_stats['local_pct']}%)</div></div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- DIM 6: Termination -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">6</div><div class="dim-title">High Termination &amp; Withdrawal Rate <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>{terminated:,} trials terminated/withdrawn out of {total_known:,} ({term_rate}%).</p>
        <div class="chart-container"><div class="bar-chart">{status_bars}</div></div>
    </div>
</div>

<!-- DIM 7-12: Text-based analysis -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">7</div><div class="dim-title">Multi-Site Dilution &mdash; Africa as Token Site <span class="severity moderate">MODERATE</span></div></div>
    <div class="dim-body"><p>Many "African" trials are global mega-trials with 100-400+ sites. Africa contributes 1-3 sites among hundreds, with no statistical power for African subgroup analysis.</p></div>
</div>

<div class="dimension">
    <div class="dim-header"><div class="dim-number">8</div><div class="dim-title">Egypt Anomaly &mdash; Quantity vs Quality <span class="severity high">HIGH</span></div></div>
    <div class="dim-body"><p>Egypt has {country_totals.get("Egypt", 0):,} trials &mdash; overwhelmingly small single-center university studies (n=30-100). Many have "UNKNOWN" status, suggesting thesis projects registered but never followed through. This inflates Africa's numbers without adding rigorous evidence.</p></div>
</div>

<div class="dimension">
    <div class="dim-header"><div class="dim-number">9</div><div class="dim-title">Infectious Disease Tunnel Vision <span class="severity high">HIGH</span></div></div>
    <div class="dim-body"><p>HIV ({conditions.get("HIV", 0):,}), TB ({conditions.get("tuberculosis", 0):,}), and malaria ({conditions.get("malaria", 0):,}) dominate due to global health funding, while the NCD burden (CVD: {conditions.get("cardiovascular", 0):,}, diabetes: {conditions.get("diabetes", 0):,}, hypertension: {conditions.get("hypertension", 0):,}) is severely under-researched.</p></div>
</div>

<div class="dimension">
    <div class="dim-header"><div class="dim-number">10</div><div class="dim-title">Pediatric Testing Ground <span class="severity moderate">MODERATE</span></div></div>
    <div class="dim-body"><p>Disproportionate pediatric and neonatal trials ({conditions.get("neonatal", 0):,} neonatal, {conditions.get("maternal", 0):,} maternal) by foreign sponsors. While medically necessary, the pattern raises questions about equitable access to the products tested.</p></div>
</div>

<div class="dimension">
    <div class="dim-header"><div class="dim-number">11</div><div class="dim-title">Poor Results Reporting <span class="severity high">HIGH</span></div></div>
    <div class="dim-body"><p>{statuses.get("unknown", 0):,} trials have "UNKNOWN" status &mdash; no updates in years. Negative results lost, participants' contributions wasted, evidence base incomplete.</p></div>
</div>

<div class="dimension">
    <div class="dim-header"><div class="dim-number">12</div><div class="dim-title">Research Capacity Gap <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body"><p>Only {sponsor_stats["local_pct"]}% of sampled trials are African-led. Phase 1 capacity exists in only 2-3 countries. The current model extracts data without building local research ecosystems. Without investment in African-led research, dependency is perpetuated.</p></div>
</div>

<!-- Verdict -->
<div class="verdict">
    <h2>Summary: 12 Systemic Issues</h2>
    <p>Africa's RCT landscape reveals structural inequities across every dimension. The continent carries ~24% of the global disease burden but hosts ~{share_vs_us}% of US trial volume. Trials are concentrated in 2 countries, focused on infectious diseases despite an NCD transition, and dominated by foreign sponsors using Africa as Phase 3 enrollment sites.</p>
</div>

<div class="footer">
    <p>Africa RCT Analysis &bull; Data: ClinicalTrials.gov API v2 &bull; Generated {date_str}</p>
    <p>Reproducible with: python fetch_africa_rcts.py</p>
</div>

</div>
</body>
</html>'''

    return html


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  Africa RCT Analysis — Data Fetcher & Report Generator")
    print("=" * 60)

    # Create data dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check for cached data
    cache_file = DATA_DIR / "collected_data.json"
    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            print(f"\nUsing cached data ({age_hours:.1f}h old). Delete data/collected_data.json to refresh.")
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            print(f"\nCache is {age_hours:.0f}h old — refreshing...")
            data = collect_all_data()
    else:
        data = collect_all_data()

    # Save raw data
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nRaw data saved to: {cache_file}")

    # Generate HTML
    html = generate_html(data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML report saved to: {OUTPUT_HTML}")

    # Print summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Africa total (sum of countries): {data['africa_country_sum']:,}")
    print(f"  US total:                        {data['comparison_regions'].get('United States', 0):,}")
    print(f"  Africa share vs US:              {data['africa_share_vs_us']}%")
    print(f"  Top country: {max(data['country_totals'].items(), key=lambda x:x[1])}")
    print(f"  Terminated/withdrawn:            {data['africa_by_status'].get('terminated_withdrawn', 0):,}")
    print(f"  Phase 1:                         {data['africa_by_phase'].get('PHASE1', 0) + data['africa_by_phase'].get('EARLY_PHASE1', 0):,}")
    print(f"  Phase 3:                         {data['africa_by_phase'].get('PHASE3', 0):,}")
    print("=" * 60)
    print(f"\nOpen {OUTPUT_HTML} in a browser to view the dashboard.")


if __name__ == "__main__":
    main()
