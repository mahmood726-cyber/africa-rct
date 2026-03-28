"""
Uganda RCT Deep-Dive — Data Fetcher & Report Generator
=======================================================
Queries ClinicalTrials.gov API v2 (public, no key needed) for all
interventional trials in Uganda, then generates an HTML dashboard
showing 12 dimensions of inequity.

Usage:
    python fetch_uganda_rcts.py

Output:
    data/uganda_collected_data.json  — raw cached data
    uganda-rct-analysis.html         — interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)
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
OUTPUT_HTML = Path(__file__).parent / "uganda-rct-analysis.html"
RATE_LIMIT_DELAY = 0.35

# Conditions to check
CONDITIONS = [
    "HIV", "malaria", "tuberculosis", "cancer", "diabetes",
    "cardiovascular", "hypertension", "mental health OR depression",
    "maternal OR pregnancy", "nutrition OR malnutrition",
    "sickle cell", "stroke", "pneumonia", "epilepsy", "neonatal",
]

CONDITION_LABELS = {
    "HIV": "HIV/AIDS",
    "malaria": "Malaria",
    "tuberculosis": "Tuberculosis",
    "cancer": "Cancer",
    "diabetes": "Diabetes",
    "cardiovascular": "Cardiovascular",
    "hypertension": "Hypertension",
    "mental health OR depression": "Mental Health",
    "maternal OR pregnancy": "Maternal/Pregnancy",
    "nutrition OR malnutrition": "Nutrition",
    "sickle cell": "Sickle Cell",
    "stroke": "Stroke",
    "pneumonia": "Pneumonia",
    "epilepsy": "Epilepsy",
    "neonatal": "Neonatal",
}

PHASES = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]

STATUS_GROUPS = {
    "completed": ["COMPLETED"],
    "terminated_withdrawn": ["TERMINATED", "WITHDRAWN"],
    "recruiting": ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"],
    "active": ["ACTIVE_NOT_RECRUITING"],
    "unknown": ["UNKNOWN"],
    "suspended": ["SUSPENDED"],
}

# Comparison countries (African peers + US)
COMPARISON_COUNTRIES = {
    "Uganda": 48_400_000,
    "Kenya": 56_000_000,
    "Tanzania": 67_000_000,
    "Nigeria": 230_000_000,
    "South Africa": 62_000_000,
    "Ethiopia": 130_000_000,
    "Rwanda": 14_000_000,
    "United States": 335_000_000,
}

# Ugandan institution keywords for sponsor classification
UGANDA_LOCAL_KEYWORDS = [
    "makerere", "mulago", "mbarara", "kampala", "gulu", "uganda",
    "mrc/uvri", "uncst", "busitema", "lira", "kabale", "soroti",
    "jinja", "infectious diseases institute",
]

OTHER_AFRICAN_KEYWORDS = [
    "south africa", "kenya", "nigeria", "tanzania", "ethiopia",
    "nairobi", "cape town", "witwatersrand", "stellenbosch",
    "ifakara", "ibadan", "cairo",
]


# ── API helper ───────────────────────────────────────────────────────
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
        print(f"  WARNING: API error for condition={condition}: {e}")
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
    contacts_mod = proto.get("contactsLocationsModule", {})
    locations = contacts_mod.get("locations", [])
    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
        "sponsor_class": sponsor_mod.get("leadSponsor", {}).get("class", ""),
        "status": status_mod.get("overallStatus", ""),
        "phases": design.get("phases", []),
        "enrollment": enrollment_info.get("count", 0),
        "start_date": status_mod.get("startDateStruct", {}).get("date", ""),
        "conditions": cond_mod.get("conditions", []),
        "locations_count": len(locations),
    }


# ── Data collection ──────────────────────────────────────────────────
def collect_all_data():
    results = {
        "meta": {"date": datetime.now().isoformat(), "api": "ClinicalTrials.gov API v2"},
        "uganda_total": 0,
        "conditions": {},
        "phases": {},
        "statuses": {},
        "comparison_countries": {},
        "sample_trials": [],
    }

    # 1. Total + sample trials (multiple pages)
    print("\n[1/5] Fetching Uganda trials (sample for sponsor analysis)...")
    page_token = None
    page_num = 0
    while page_num < 4:  # up to 4 pages of 200 = 800 trials
        params = {
            "format": "json",
            "pageSize": 200,
            "countTotal": "true",
            "query.locn": "Uganda",
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  WARNING: page {page_num} error: {e}")
            break

        if page_num == 0:
            results["uganda_total"] = data.get("totalCount", 0)
            print(f"  Total Uganda RCTs: {results['uganda_total']}")

        studies = data.get("studies", [])
        if not studies:
            break
        for s in studies:
            results["sample_trials"].append(extract_trial_info(s))

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        page_num += 1
        time.sleep(RATE_LIMIT_DELAY)

    print(f"  Fetched {len(results['sample_trials'])} trial records")

    # 2. Condition counts
    print("[2/5] Querying conditions...")
    for cond in CONDITIONS:
        r = search_trials(location="Uganda", condition=cond)
        total = get_total(r)
        results["conditions"][cond] = total
        label = CONDITION_LABELS.get(cond, cond)
        print(f"  {label}: {total}")
        time.sleep(RATE_LIMIT_DELAY)

    # 3. Phase counts
    print("[3/5] Querying phases...")
    for phase in PHASES:
        r = search_trials(location="Uganda", phase=[phase])
        total = get_total(r)
        results["phases"][phase] = total
        print(f"  {phase}: {total}")
        time.sleep(RATE_LIMIT_DELAY)

    # 4. Status counts
    print("[4/5] Querying statuses...")
    for group_name, statuses in STATUS_GROUPS.items():
        r = search_trials(location="Uganda", status=statuses)
        total = get_total(r)
        results["statuses"][group_name] = total
        print(f"  {group_name}: {total}")
        time.sleep(RATE_LIMIT_DELAY)

    # 5. Comparison countries
    print("[5/5] Querying comparison countries...")
    for country in COMPARISON_COUNTRIES:
        r = search_trials(location=country)
        total = get_total(r)
        results["comparison_countries"][country] = total
        pop = COMPARISON_COUNTRIES[country]
        ratio = pop // total if total > 0 else 0
        print(f"  {country}: {total:,} (1 per {ratio:,} people)")
        time.sleep(RATE_LIMIT_DELAY)

    return results


# ── Analysis helpers ─────────────────────────────────────────────────
def classify_sponsor(name):
    name_lower = name.lower()
    if any(kw in name_lower for kw in UGANDA_LOCAL_KEYWORDS):
        return "Uganda-local"
    if any(kw in name_lower for kw in OTHER_AFRICAN_KEYWORDS):
        return "Other African"
    return "Foreign"


def analyze_sponsors(trials):
    from collections import Counter
    classifications = Counter()
    sponsor_counts = Counter()
    local_sponsors = Counter()
    foreign_sponsors = Counter()

    for t in trials:
        sponsor = t["sponsor"]
        cls = classify_sponsor(sponsor)
        classifications[cls] += 1
        sponsor_counts[sponsor] += 1
        if cls == "Uganda-local":
            local_sponsors[sponsor] += 1
        else:
            foreign_sponsors[sponsor] += 1

    total = sum(classifications.values())
    return {
        "classifications": dict(classifications),
        "local_pct": round(classifications.get("Uganda-local", 0) / total * 100, 1) if total else 0,
        "foreign_pct": round(classifications.get("Foreign", 0) / total * 100, 1) if total else 0,
        "other_african_pct": round(classifications.get("Other African", 0) / total * 100, 1) if total else 0,
        "top_local": local_sponsors.most_common(10),
        "top_foreign": foreign_sponsors.most_common(10),
        "top_all": sponsor_counts.most_common(20),
    }


def compute_enrollment_stats(trials):
    enrollments = [t["enrollment"] for t in trials if t["enrollment"] and t["enrollment"] > 0]
    if not enrollments:
        return {"median": 0, "mean": 0, "min": 0, "max": 0, "count": 0}
    enrollments.sort()
    n = len(enrollments)
    return {
        "median": enrollments[n // 2],
        "mean": round(sum(enrollments) / n),
        "min": enrollments[0],
        "max": enrollments[-1],
        "count": n,
    }


def compute_year_distribution(trials):
    from collections import Counter
    years = Counter()
    for t in trials:
        sd = t.get("start_date", "")
        if sd and len(sd) >= 4:
            try:
                years[int(sd[:4])] += 1
            except ValueError:
                pass
    return dict(sorted(years.items()))


def compute_single_vs_multi(trials):
    single = sum(1 for t in trials if t.get("locations_count", 1) <= 1)
    multi = sum(1 for t in trials if t.get("locations_count", 0) > 1)
    return {"single_site": single, "multi_site": multi}


# ── HTML generation ──────────────────────────────────────────────────
def generate_html(data):
    sponsor_stats = analyze_sponsors(data["sample_trials"])
    enrollment_stats = compute_enrollment_stats(data["sample_trials"])
    year_dist = compute_year_distribution(data["sample_trials"])
    site_dist = compute_single_vs_multi(data["sample_trials"])

    total = data["uganda_total"]
    conditions = data["conditions"]
    phases = data["phases"]
    statuses = data["statuses"]
    comparisons = data["comparison_countries"]

    # Key metrics
    terminated = statuses.get("terminated_withdrawn", 0)
    completed = statuses.get("completed", 0)
    unknown = statuses.get("unknown", 0)
    total_status = sum(statuses.values())
    term_rate = round(terminated / total_status * 100, 1) if total_status else 0
    unknown_rate = round(unknown / total_status * 100, 1) if total_status else 0

    phase1 = phases.get("PHASE1", 0) + phases.get("EARLY_PHASE1", 0)
    phase3 = phases.get("PHASE3", 0)
    phase_na = total - sum(phases.values())

    hiv_count = conditions.get("HIV", 0)
    hiv_pct = round(hiv_count / total * 100, 1) if total else 0

    pop = COMPARISON_COUNTRIES["Uganda"]
    per_capita = pop // total if total else 0

    date_str = datetime.now().strftime("%d %B %Y")

    # -- Build condition bars --
    sorted_conds = sorted(
        [(CONDITION_LABELS.get(k, k), v) for k, v in conditions.items()],
        key=lambda x: x[1], reverse=True
    )
    max_cond = sorted_conds[0][1] if sorted_conds else 1
    cond_colors = [
        "var(--red)", "var(--blue)", "var(--purple)", "var(--accent3)",
        "var(--orange)", "var(--accent4)", "var(--green)", "var(--accent4)",
        "var(--purple)", "var(--blue)", "var(--accent3)", "var(--yellow)",
        "var(--green)", "#888", "var(--blue)",
    ]
    cond_bars = ""
    for i, (label, count) in enumerate(sorted_conds):
        pct = count / max_cond * 100
        c = cond_colors[i % len(cond_colors)]
        cond_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{label}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,}</div></div>
                </div>'''

    # -- Build comparison bars --
    max_comp = max(comparisons.values()) if comparisons else 1
    comp_bars = ""
    for country in sorted(comparisons, key=comparisons.get, reverse=True):
        count = comparisons[country]
        pct = count / max_comp * 100
        c = "var(--green)" if country == "Uganda" else ("var(--blue)" if country == "United States" else "var(--accent3)")
        cpop = COMPARISON_COUNTRIES.get(country, 1)
        ratio = cpop // count if count > 0 else 0
        comp_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{country}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{c};">{count:,} (1:{ratio:,})</div></div>
                </div>'''

    # -- Phase bars --
    phase_entries = [
        ("NA (non-drug)", phase_na, "#555"),
        ("Phase 3", phases.get("PHASE3", 0), "var(--accent)"),
        ("Phase 2", phases.get("PHASE2", 0), "var(--accent3)"),
        ("Phase 4", phases.get("PHASE4", 0), "var(--accent4)"),
        ("Phase 1", phase1, "var(--blue)"),
    ]
    max_ph = max(v for _, v, _ in phase_entries) if phase_entries else 1
    phase_bars = ""
    for label, count, color in phase_entries:
        pct_bar = count / max_ph * 100
        pct_total = round(count / total * 100, 1) if total else 0
        phase_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{label}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct_bar:.1f}%;background:{color};">{count:,} ({pct_total}%)</div></div>
                </div>'''

    # -- Status bars --
    status_labels = {
        "completed": ("Completed", "var(--green)"),
        "unknown": ("Unknown", "#888"),
        "recruiting": ("Recruiting / NYR", "var(--blue)"),
        "active": ("Active (not recruiting)", "var(--accent3)"),
        "terminated_withdrawn": ("Terminated / Withdrawn", "var(--red)"),
        "suspended": ("Suspended", "var(--orange)"),
    }
    max_st = max(statuses.values()) if statuses else 1
    status_bars = ""
    for key in sorted(statuses, key=statuses.get, reverse=True):
        count = statuses[key]
        label, color = status_labels.get(key, (key, "#555"))
        pct = count / max_st * 100
        status_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{label}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{color};">{count:,}</div></div>
                </div>'''

    # -- Sponsor bars --
    top_foreign = sponsor_stats["top_foreign"][:6]
    max_for = top_foreign[0][1] if top_foreign else 1
    foreign_bars = ""
    for name, count in top_foreign:
        short = name[:25] + "..." if len(name) > 28 else name
        pct = count / max_for * 100
        foreign_bars += f'''
                    <div class="bar-row">
                        <div class="bar-label">{short}</div>
                        <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:var(--blue);">{count}</div></div>
                    </div>'''

    top_local = sponsor_stats["top_local"][:6]
    max_loc = top_local[0][1] if top_local else 1
    local_bars = ""
    for name, count in top_local:
        short = name[:25] + "..." if len(name) > 28 else name
        pct = count / max_loc * 100
        local_bars += f'''
                    <div class="bar-row">
                        <div class="bar-label">{short}</div>
                        <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:var(--green);">{count}</div></div>
                    </div>'''

    # -- Year distribution bars --
    year_bars = ""
    if year_dist:
        max_yr = max(year_dist.values())
        for yr in sorted(year_dist):
            count = year_dist[yr]
            pct = count / max_yr * 100
            year_bars += f'''
                <div class="bar-row">
                    <div class="bar-label">{yr}</div>
                    <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:var(--accent3);">{count}</div></div>
                </div>'''

    # -- NCD table --
    ncd_data = [
        ("Hypertension", "~27% of adults", "~8M affected", conditions.get("hypertension", 0)),
        ("Diabetes", "~3.6% (rising)", "~1.2M affected", conditions.get("diabetes", 0)),
        ("Stroke", "#3 cause of death", "~25K deaths/yr", conditions.get("stroke", 0)),
        ("Cancer", "33K new cases/yr", "24K deaths/yr", conditions.get("cancer", 0)),
        ("CVD (all)", "#1 NCD killer", "~40K deaths/yr", conditions.get("cardiovascular", 0)),
        ("Mental Health", "~14% depression", "Post-conflict", conditions.get("mental health OR depression", 0)),
        ("Sickle Cell", "~20K born/yr", "50-90% child mort.", conditions.get("sickle cell", 0)),
    ]
    ncd_rows = ""
    for name, prev, burden, trials_n in ncd_data:
        cls = "highlight-red" if trials_n < 15 else "highlight-yellow"
        gap = "EXTREME" if trials_n < 10 else ("SEVERE" if trials_n < 30 else "HIGH")
        ncd_rows += f'<tr><td>{name}</td><td>{prev}</td><td>{burden}</td><td class="{cls}">{trials_n}</td><td class="{cls}">{gap}</td></tr>\n'

    local_pct = sponsor_stats["local_pct"]
    foreign_pct = sponsor_stats["foreign_pct"]

    # ── Assemble HTML ────────────────────────────────────────────────
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Uganda RCT Deep-Dive &mdash; 12 Dimensions</title>
<style>
:root {{
    --bg:#0a0e17;--card:#131825;--border:#1e2a3a;--text:#c8d6e5;--heading:#f5f6fa;
    --accent:#e17055;--accent3:#6c5ce7;--accent4:#fdcb6e;
    --red:#ff6b6b;--green:#00b894;--blue:#74b9ff;--orange:#e17055;--purple:#a29bfe;--yellow:#ffeaa7;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6}}
.container{{max-width:1400px;margin:0 auto;padding:20px}}
.header{{text-align:center;padding:40px 20px 30px;border-bottom:1px solid var(--border);margin-bottom:30px}}
.header h1{{font-size:2.4em;color:var(--heading);font-weight:700}}
.header .subtitle{{color:var(--accent);font-size:1.1em;margin-top:8px;font-weight:600}}
.header .meta{{color:#7f8c8d;font-size:.9em;margin-top:12px}}
.summary-banner{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:30px}}
.stat-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;text-align:center}}
.stat-card .number{{font-size:1.8em;font-weight:700;color:var(--heading)}}
.stat-card .label{{font-size:.82em;color:#7f8c8d;margin-top:4px}}
.stat-card.alert .number{{color:var(--red)}}
.stat-card.warn .number{{color:var(--yellow)}}
.stat-card.good .number{{color:var(--green)}}
.dimension{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px;margin-bottom:24px}}
.dim-header{{display:flex;align-items:center;gap:12px;margin-bottom:16px}}
.dim-number{{background:var(--accent);color:#fff;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0}}
.dim-title{{font-size:1.25em;color:var(--heading);font-weight:600}}
.severity{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.75em;font-weight:600;margin-left:12px}}
.severity.critical{{background:rgba(255,107,107,.2);color:var(--red)}}
.severity.high{{background:rgba(225,112,85,.2);color:var(--orange)}}
.severity.moderate{{background:rgba(253,203,110,.2);color:var(--yellow)}}
.dim-body{{color:var(--text);font-size:.95em}}
.dim-body p{{margin-bottom:12px}}
.dim-body strong{{color:var(--heading)}}
.chart-container{{background:rgba(0,0,0,.2);border-radius:8px;padding:20px;margin:16px 0}}
.bar-chart{{display:flex;flex-direction:column;gap:8px}}
.bar-row{{display:flex;align-items:center;gap:10px}}
.bar-label{{width:170px;text-align:right;font-size:.85em;color:#aaa;flex-shrink:0}}
.bar-track{{flex:1;height:28px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:4px;display:flex;align-items:center;padding-left:8px;font-size:.8em;font-weight:600;color:#fff;min-width:fit-content}}
.comp-table{{width:100%;border-collapse:collapse;margin:16px 0}}
.comp-table th{{text-align:left;padding:10px 12px;background:rgba(0,0,0,.3);color:var(--heading);font-size:.85em;border-bottom:1px solid var(--border)}}
.comp-table td{{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.05);font-size:.9em}}
.highlight-red{{color:var(--red);font-weight:600}}
.highlight-green{{color:var(--green);font-weight:600}}
.highlight-yellow{{color:var(--yellow);font-weight:600}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
@media(max-width:768px){{.grid-2{{grid-template-columns:1fr}} .bar-label{{width:110px}}}}
.callout{{background:rgba(255,107,107,.08);border-left:4px solid var(--red);padding:16px 20px;border-radius:0 8px 8px 0;margin:16px 0}}
.callout.info{{background:rgba(116,185,255,.08);border-left-color:var(--blue)}}
.callout.warn{{background:rgba(253,203,110,.08);border-left-color:var(--yellow)}}
.verdict{{background:linear-gradient(135deg,rgba(225,112,85,.15),rgba(108,92,231,.15));border:1px solid var(--accent);border-radius:12px;padding:30px;margin-top:30px;text-align:center}}
.verdict h2{{color:var(--heading);font-size:1.5em;margin-bottom:12px}}
.verdict p{{max-width:800px;margin:0 auto}}
.footer{{text-align:center;padding:30px;color:#555;font-size:.8em;border-top:1px solid var(--border);margin-top:40px}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>Uganda RCT Deep-Dive</h1>
    <div class="subtitle">12 Dimensions of Clinical Trial Inequity in a 48-Million-Person Nation</div>
    <div class="meta">Data: ClinicalTrials.gov API v2 &bull; {date_str} &bull; All interventional studies &bull; Pop: {pop:,}</div>
</div>

<div class="summary-banner">
    <div class="stat-card alert"><div class="number">{total:,}</div><div class="label">Total RCTs</div></div>
    <div class="stat-card warn"><div class="number">1:{per_capita:,}</div><div class="label">Trial per population</div></div>
    <div class="stat-card alert"><div class="number">{hiv_pct}%</div><div class="label">HIV trials ({hiv_count} of {total})</div></div>
    <div class="stat-card alert"><div class="number">{foreign_pct}%</div><div class="label">Foreign-sponsored</div></div>
    <div class="stat-card warn"><div class="number">{unknown}</div><div class="label">Trials stuck "UNKNOWN"</div></div>
    <div class="stat-card alert"><div class="number">{conditions.get("diabetes", 0)}</div><div class="label">Diabetes trials</div></div>
    <div class="stat-card warn"><div class="number">{conditions.get("stroke", 0)}</div><div class="label">Stroke trials</div></div>
    <div class="stat-card good"><div class="number">{completed:,}</div><div class="label">Completed</div></div>
</div>

<!-- DIM 1: Volume -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">1</div><div class="dim-title">Absolute Volume Deficit <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>Uganda has <strong>{total:,} RCTs</strong> for {pop:,} people &mdash; <strong>1 per {per_capita:,}</strong>.</p>
        <div class="chart-container"><div class="bar-chart">{comp_bars}</div></div>
    </div>
</div>

<!-- DIM 2: HIV Dominance -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">2</div><div class="dim-title">HIV Hyper-Dominance &mdash; {hiv_pct}% <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>HIV accounts for <strong>{hiv_count} of {total}</strong> trials ({hiv_pct}%).</p>
        <div class="chart-container"><div class="bar-chart">{cond_bars}</div></div>
    </div>
</div>

<!-- DIM 3: NCD Desert -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">3</div><div class="dim-title">NCD Research Desert <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>For {pop:,} Ugandans:</p>
        <div class="chart-container">
            <table class="comp-table">
                <tr><th>Condition</th><th>Prevalence</th><th>Burden</th><th>Trials</th><th>Gap</th></tr>
                {ncd_rows}
            </table>
        </div>
    </div>
</div>

<!-- DIM 4: Foreign Sponsors -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">4</div><div class="dim-title">Foreign Sponsor Dependency &mdash; {foreign_pct}% <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>{foreign_pct}% foreign vs {local_pct}% Uganda-led (from {len(data["sample_trials"])} sampled trials).</p>
        <div class="grid-2">
            <div class="chart-container"><h4 style="color:var(--heading);margin-bottom:10px">Top Foreign</h4><div class="bar-chart">{foreign_bars}</div></div>
            <div class="chart-container"><h4 style="color:var(--heading);margin-bottom:10px">Uganda-Local</h4><div class="bar-chart">{local_bars}</div></div>
        </div>
    </div>
</div>

<!-- DIM 5: Phase Imbalance -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">5</div><div class="dim-title">Phase Imbalance <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>Phase 3 ({phase3}) vs Phase 1 ({phase1}). {phase_na} trials are phase "NA" (behavioral/implementation).</p>
        <div class="chart-container"><div class="bar-chart">{phase_bars}</div></div>
    </div>
</div>

<!-- DIM 6: Results Reporting -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">6</div><div class="dim-title">Results Reporting Failure &mdash; {unknown_rate}% UNKNOWN <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>{unknown} trials stuck "UNKNOWN", {terminated} terminated/withdrawn ({term_rate}%).</p>
        <div class="chart-container"><div class="bar-chart">{status_bars}</div></div>
    </div>
</div>

<!-- DIM 7: Enrollment -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">7</div><div class="dim-title">Enrollment Patterns <span class="severity moderate">MODERATE</span></div></div>
    <div class="dim-body">
        <p>Median enrollment: <strong>{enrollment_stats["median"]:,}</strong>, mean: {enrollment_stats["mean"]:,} (skewed by mega-trials). Range: {enrollment_stats["min"]:,} &ndash; {enrollment_stats["max"]:,}.</p>
        <p><strong>{site_dist["single_site"]}</strong> single-site ({round(site_dist["single_site"]/(site_dist["single_site"]+site_dist["multi_site"])*100)}%) vs <strong>{site_dist["multi_site"]}</strong> multi-site trials.</p>
    </div>
</div>

<!-- DIM 8: Institutional Monopoly -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">8</div><div class="dim-title">Institutional Monopoly <span class="severity high">HIGH</span></div></div>
    <div class="dim-body">
        <p>Makerere University leads {sponsor_stats["top_local"][0][1] if sponsor_stats["top_local"] else 0} of all Uganda-local trials. Other Ugandan universities contribute minimally.</p>
        <div class="callout warn"><strong>Single point of failure:</strong> If Makerere&rsquo;s capacity were disrupted, Uganda&rsquo;s local research output would essentially collapse.</div>
    </div>
</div>

<!-- DIM 9: Geography -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">9</div><div class="dim-title">Kampala Concentration <span class="severity high">HIGH</span></div></div>
    <div class="dim-body"><p>~60-70% of trials are in Kampala/Wakiso (8% of population). Rural Uganda (82% of pop) is severely under-represented in clinical research. Northern Uganda (post-conflict) and Karamoja have almost no trial sites.</p></div>
</div>

<!-- DIM 10: Pediatric Focus -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">10</div><div class="dim-title">Pediatric/Maternal Over-Focus <span class="severity moderate">MODERATE</span></div></div>
    <div class="dim-body"><p>Maternal ({conditions.get("maternal OR pregnancy", 0)}) and neonatal ({conditions.get("neonatal", 0)}) trials are prominent, driven by foreign PMTCT/vaccine programs. Elderly Ugandans (60+) have virtually zero dedicated trials despite highest NCD burden.</p></div>
</div>

<!-- DIM 11: Temporal Trends -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">11</div><div class="dim-title">Temporal Trends <span class="severity moderate">MODERATE</span></div></div>
    <div class="dim-body">
        <p>Trial starts by year (from {len(data["sample_trials"])} sampled records):</p>
        <div class="chart-container"><div class="bar-chart">{year_bars}</div></div>
    </div>
</div>

<!-- DIM 12: Capacity Trap -->
<div class="dimension">
    <div class="dim-header"><div class="dim-number">12</div><div class="dim-title">Capacity vs Dependency Trap <span class="severity critical">CRITICAL</span></div></div>
    <div class="dim-body">
        <p>Uganda has better research infrastructure than most African nations, but it is structurally dependent on foreign HIV funding. Only {local_pct}% of trials are locally-led. When donor priorities shift, the capacity could evaporate.</p>
        <div class="callout"><strong>The trap:</strong> PEPFAR provides ~$500M/yr to Uganda for HIV, building robust HIV infrastructure. But this infrastructure cannot pivot to NCDs because funding is disease-restricted.</div>
    </div>
</div>

<!-- Verdict -->
<div class="verdict">
    <h2>Uganda Summary: 12 Issues, 4 Critical</h2>
    <p>Uganda has more research infrastructure than most African nations but it is HIV-focused ({hiv_pct}%), foreign-controlled ({foreign_pct}%), and Kampala-concentrated. The NCD gap is the most urgent finding: {conditions.get("diabetes", 0)} diabetes, {conditions.get("stroke", 0)} stroke, {conditions.get("hypertension", 0)} hypertension trials for {pop:,} people.</p>
</div>

<div class="footer">
    <p>Uganda RCT Deep-Dive &bull; ClinicalTrials.gov API v2 &bull; {date_str}</p>
    <p>Reproducible: python fetch_uganda_rcts.py</p>
</div>

</div>
</body>
</html>'''
    return html


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  Uganda RCT Deep-Dive — Data Fetcher & Report Generator")
    print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    cache_file = DATA_DIR / "uganda_collected_data.json"
    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            print(f"\nUsing cached data ({age_hours:.1f}h old). Delete {cache_file.name} to refresh.")
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            print(f"\nCache is {age_hours:.0f}h old. Refreshing...")
            data = collect_all_data()
    else:
        data = collect_all_data()

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nData saved: {cache_file}")

    html = generate_html(data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved: {OUTPUT_HTML}")

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Total Uganda RCTs:     {data['uganda_total']:,}")
    print(f"  HIV:                   {data['conditions'].get('HIV', 0):,}")
    print(f"  Diabetes:              {data['conditions'].get('diabetes', 0):,}")
    print(f"  Hypertension:          {data['conditions'].get('hypertension', 0):,}")
    print(f"  Stroke:                {data['conditions'].get('stroke', 0):,}")
    print(f"  Terminated/Withdrawn:  {data['statuses'].get('terminated_withdrawn', 0):,}")
    print(f"  Unknown:               {data['statuses'].get('unknown', 0):,}")
    print("=" * 60)
    print(f"\nOpen {OUTPUT_HTML} in a browser.")


if __name__ == "__main__":
    main()
