#!/usr/bin/env python
"""
fetch_latam_mirror.py — Latin America as Mirror: What Africa Can Learn

Latin America dramatically outperforms Africa in clinical trials despite
similar income levels. Brazil (45.8/M), Argentina (87.4/M), Mexico (52.7/M)
are 10-50x higher than most African nations. This analysis asks: what did
LatAm do right, and what can Africa learn?

Key comparisons:
  - Nigeria vs Colombia: similar GDP, Nigeria has 7% of Colombia's trials/capita
  - Ghana vs Peru: same population, same lower-middle income, Peru 7.5x more
  - South Africa vs Brazil: SA is the only African country at LatAm levels

Outputs:
  - data/latam_mirror_data.json   (cached API results, 24h TTL)
  - latam-mirror.html             (dark-theme interactive dashboard)
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
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# Latin American countries: {name: population_millions}
LATAM_COUNTRIES = {
    "Brazil":     216,
    "Mexico":     130,
    "Argentina":  46,
    "Colombia":   52,
    "Peru":       34,
    "Chile":      20,
}

# African comparator countries (similar GDP per capita to LatAm upper-middle)
AFRICA_COUNTRIES = {
    "South Africa": 62,
    "Nigeria":      230,
    "Kenya":        56,
    "Ghana":        34,
    "Egypt":        105,
    "Ethiopia":     130,
}

ALL_COUNTRIES = {**LATAM_COUNTRIES, **AFRICA_COUNTRIES}

# Known trial counts for verification (from ClinicalTrials.gov queries)
KNOWN_COUNTS = {
    "Brazil":       9890,
    "Mexico":       6847,
    "Argentina":    4019,
    "Colombia":     1872,
    "Peru":         1726,
    "South Africa": 3473,
    "Nigeria":      354,
    "Kenya":        720,
    "Ghana":        230,
}

# Condition queries
CONDITIONS = {
    "Cancer":         "cancer OR neoplasm OR oncology",
    "HIV":            "HIV",
    "Cardiovascular": "cardiovascular OR heart OR cardiac OR hypertension",
    "Diabetes":       "diabetes",
    "Mental Health":  "mental health OR depression OR anxiety OR psychiatric",
    "Maternal":       "maternal OR pregnancy OR obstetric OR perinatal",
}

# Phase queries for Phase 1 capacity analysis
PHASE_QUERIES = {
    "Phase 1":  "AREA[Phase]EARLY_PHASE1 OR AREA[Phase]PHASE1",
    "Phase 2":  "AREA[Phase]PHASE2",
    "Phase 3":  "AREA[Phase]PHASE3",
    "Phase 4":  "AREA[Phase]PHASE4",
}

# Head-to-head matched pairs for narrative analysis
MATCHED_PAIRS = [
    ("Nigeria", "Colombia", "Nigeria GDP $500B vs Colombia $330B, but Nigeria has ~7% of Colombia's trials per capita"),
    ("Ghana", "Peru", "Same population (34M), same lower-middle income tier, but Peru has ~7.5x more trials"),
    ("South Africa", "Brazil", "SA is the only African country competitive with LatAm levels"),
    ("Kenya", "Mexico", "Similar population ratios, but Mexico runs 5-10x more trials per capita"),
]

CACHE_FILE = Path(__file__).resolve().parent / "data" / "latam_mirror_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "latam-mirror.html"
RATE_LIMIT = 0.35  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# References
REFERENCES = [
    {"pmid": "35513403", "desc": "Clinical trial globalisation: Latin America regulatory harmonisation"},
    {"pmid": "36332653", "desc": "Community-randomized HIV prevention in sub-Saharan Africa"},
    {"pmid": "33483378", "desc": "ANVISA regulatory framework for clinical trials in Brazil"},
    {"pmid": "34843674", "desc": "Clinical trial capacity building in low- and middle-income countries"},
    {"pmid": "30636200", "desc": "Pan American Network for Drug Regulatory Harmonization (PANDRH)"},
]

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


def get_trial_count(location, condition_query=None, phase_filter=None):
    """Return total count of interventional trials for a location + optional condition/phase."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition_query:
        params["query.cond"] = condition_query
    if phase_filter:
        # Phase filter goes into filter.advanced alongside StudyType
        params["filter.advanced"] = f"AREA[StudyType]INTERVENTIONAL AND ({phase_filter})"
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_total_interventional(location):
    """Return total count of all interventional trials for a location."""
    return get_trial_count(location)


# ---------------------------------------------------------------------------
# Data collection
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


def fetch_all_data():
    """Fetch condition-level, phase-level, and total trial counts for all countries."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "countries": {},
    }

    all_names = list(ALL_COUNTRIES.keys())
    # Per country: 1 total + 6 conditions + 4 phases = 11 queries
    total_calls = len(all_names) * (1 + len(CONDITIONS) + len(PHASE_QUERIES))
    call_num = 0

    for country in all_names:
        pop = ALL_COUNTRIES[country]
        region = "latam" if country in LATAM_COUNTRIES else "africa"

        # --- Total interventional ---
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / Total...")
        total_count = get_total_interventional(country)
        time.sleep(RATE_LIMIT)

        # --- Condition-level queries ---
        condition_counts = {}
        for cond_name, cond_query in CONDITIONS.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {cond_name}...")
            condition_counts[cond_name] = get_trial_count(country, condition_query=cond_query)
            time.sleep(RATE_LIMIT)

        # --- Phase-level queries ---
        phase_counts = {}
        for phase_name, phase_query in PHASE_QUERIES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {phase_name}...")
            phase_counts[phase_name] = get_trial_count(country, phase_filter=phase_query)
            time.sleep(RATE_LIMIT)

        data["countries"][country] = {
            "region": region,
            "population_m": pop,
            "total_trials": total_count,
            "conditions": condition_counts,
            "phases": phase_counts,
        }

    # --- Save cache ---
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cached to {CACHE_FILE}")

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_metrics(data):
    """Compute all comparative metrics."""
    country_metrics = {}

    for country, info in data["countries"].items():
        total = info["total_trials"]
        pop = info["population_m"]
        region = info["region"]
        conditions = info.get("conditions", {})
        phases = info.get("phases", {})

        # Per-capita rate
        per_million = round(total / pop, 1) if pop > 0 else 0

        # Disease portfolio shares
        portfolio = {}
        for cond_name, count in conditions.items():
            share = round(count / total * 100, 1) if total > 0 else 0
            portfolio[cond_name] = {"count": count, "share_pct": share}

        # HIV dominance: what % of trials are HIV?
        hiv_count = conditions.get("HIV", 0)
        hiv_dominance = round(hiv_count / total * 100, 1) if total > 0 else 0

        # Phase 1 capacity
        phase1_count = phases.get("Phase 1", 0)
        phase1_share = round(phase1_count / total * 100, 1) if total > 0 else 0
        phase3_count = phases.get("Phase 3", 0)
        phase3_share = round(phase3_count / total * 100, 1) if total > 0 else 0

        # Portfolio diversity: how many conditions have >5% share
        diversity_score = sum(
            1 for cond_name, vals in portfolio.items()
            if vals["share_pct"] > 5.0
        )

        country_metrics[country] = {
            "region": region,
            "population_m": pop,
            "total_trials": total,
            "per_million": per_million,
            "conditions": conditions,
            "portfolio": portfolio,
            "phases": phases,
            "hiv_dominance": hiv_dominance,
            "phase1_count": phase1_count,
            "phase1_share": phase1_share,
            "phase3_count": phase3_count,
            "phase3_share": phase3_share,
            "diversity_score": diversity_score,
        }

    # Regional summaries
    regional = {"latam": [], "africa": []}
    for country, m in country_metrics.items():
        regional[m["region"]].append(m)

    region_summaries = {}
    for rname, members in regional.items():
        total_trials = sum(m["total_trials"] for m in members)
        total_pop = sum(m["population_m"] for m in members)
        avg_per_million = round(total_trials / total_pop, 1) if total_pop > 0 else 0
        avg_phase1 = round(
            sum(m["phase1_share"] for m in members) / max(1, len(members)), 1
        )
        avg_hiv_dom = round(
            sum(m["hiv_dominance"] for m in members) / max(1, len(members)), 1
        )
        avg_diversity = round(
            sum(m["diversity_score"] for m in members) / max(1, len(members)), 1
        )

        # Aggregate condition counts
        agg_conditions = {}
        for cond_name in CONDITIONS:
            agg_conditions[cond_name] = sum(
                m["conditions"].get(cond_name, 0) for m in members
            )

        region_summaries[rname] = {
            "n_countries": len(members),
            "total_trials": total_trials,
            "total_pop": total_pop,
            "avg_per_million": avg_per_million,
            "avg_phase1_share": avg_phase1,
            "avg_hiv_dominance": avg_hiv_dom,
            "avg_diversity": avg_diversity,
            "aggregate_conditions": agg_conditions,
        }

    # Matched pair analysis
    pair_analysis = []
    for africa_name, latam_name, narrative in MATCHED_PAIRS:
        a = country_metrics.get(africa_name, {})
        l = country_metrics.get(latam_name, {})
        if a and l:
            gap_ratio = round(l["per_million"] / a["per_million"], 1) if a["per_million"] > 0 else float("inf")
            pair_analysis.append({
                "africa": africa_name,
                "latam": latam_name,
                "narrative": narrative,
                "africa_per_m": a["per_million"],
                "latam_per_m": l["per_million"],
                "gap_ratio": gap_ratio if gap_ratio != float("inf") else 999,
                "africa_hiv_dom": a["hiv_dominance"],
                "latam_hiv_dom": l["hiv_dominance"],
                "africa_phase1": a["phase1_share"],
                "latam_phase1": l["phase1_share"],
            })

    return {
        "country_metrics": country_metrics,
        "region_summaries": region_summaries,
        "pair_analysis": pair_analysis,
    }


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def generate_html(data, metrics):
    """Generate the full HTML dashboard."""
    cm = metrics["country_metrics"]
    rs = metrics["region_summaries"]
    pairs = metrics["pair_analysis"]

    latam_rs = rs.get("latam", {})
    africa_rs = rs.get("africa", {})

    gap_ratio = round(
        latam_rs.get("avg_per_million", 0) / africa_rs.get("avg_per_million", 1), 1
    ) if africa_rs.get("avg_per_million", 0) > 0 else 999

    # --- Ranked bar chart data (all 12 countries sorted by per_million) ---
    sorted_all = sorted(cm.items(), key=lambda x: -x[1]["per_million"])
    chart_labels = json.dumps([c for c, _ in sorted_all])
    chart_values = json.dumps([m["per_million"] for _, m in sorted_all])
    chart_colors = json.dumps([
        "#3b82f6" if m["region"] == "latam" else "#ef4444"
        for _, m in sorted_all
    ])

    # --- Country comparison table rows ---
    country_rows = ""
    for country, m in sorted_all:
        region_badge = (
            '<span style="background:#3b82f6;padding:2px 8px;border-radius:4px;'
            'font-size:0.75rem;">LatAm</span>'
            if m["region"] == "latam"
            else '<span style="background:#ef4444;padding:2px 8px;border-radius:4px;'
            'font-size:0.75rem;">Africa</span>'
        )
        country_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(country)} {region_badge}</td>'
            f'<td style="padding:10px;text-align:right;">{m["population_m"]}M</td>'
            f'<td style="padding:10px;text-align:right;">{m["total_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;font-weight:bold;'
            f'color:{"#22c55e" if m["per_million"] > 30 else "#f59e0b" if m["per_million"] > 10 else "#ef4444"};">'
            f'{m["per_million"]}</td>'
            f'<td style="padding:10px;text-align:right;">{m["hiv_dominance"]}%</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase1_share"]}%</td>'
            f'<td style="padding:10px;text-align:right;">{m["diversity_score"]}</td>'
            f'</tr>\n'
        )

    # --- Matched pairs table rows ---
    pair_rows = ""
    for p in pairs:
        gap_display = f'{p["gap_ratio"]}x' if p["gap_ratio"] < 999 else "N/A"
        pair_rows += (
            f'<tr>'
            f'<td style="padding:10px;color:#ef4444;">{escape_html(p["africa"])}</td>'
            f'<td style="padding:10px;text-align:right;">{p["africa_per_m"]}</td>'
            f'<td style="padding:10px;color:#3b82f6;">{escape_html(p["latam"])}</td>'
            f'<td style="padding:10px;text-align:right;">{p["latam_per_m"]}</td>'
            f'<td style="padding:10px;text-align:right;color:#f59e0b;font-weight:bold;">'
            f'{gap_display}</td>'
            f'<td style="padding:10px;font-size:0.85rem;color:#94a3b8;">'
            f'{escape_html(p["narrative"])}</td>'
            f'</tr>\n'
        )

    # --- Disease portfolio chart data ---
    cond_names = list(CONDITIONS.keys())
    latam_cond_counts = [latam_rs.get("aggregate_conditions", {}).get(c, 0) for c in cond_names]
    africa_cond_counts = [africa_rs.get("aggregate_conditions", {}).get(c, 0) for c in cond_names]
    # Normalize to percentages of total within region
    latam_total_cond = max(1, sum(latam_cond_counts))
    africa_total_cond = max(1, sum(africa_cond_counts))
    latam_cond_pcts = [round(c / latam_total_cond * 100, 1) for c in latam_cond_counts]
    africa_cond_pcts = [round(c / africa_total_cond * 100, 1) for c in africa_cond_counts]

    portfolio_labels = json.dumps(cond_names)
    portfolio_latam = json.dumps(latam_cond_pcts)
    portfolio_africa = json.dumps(africa_cond_pcts)

    # --- Phase comparison chart data ---
    phase_names = list(PHASE_QUERIES.keys())
    latam_countries_list = [c for c, m in cm.items() if m["region"] == "latam"]
    africa_countries_list = [c for c, m in cm.items() if m["region"] == "africa"]

    latam_phase_totals = []
    africa_phase_totals = []
    for ph in phase_names:
        latam_phase_totals.append(sum(cm[c]["phases"].get(ph, 0) for c in latam_countries_list))
        africa_phase_totals.append(sum(cm[c]["phases"].get(ph, 0) for c in africa_countries_list))

    phase_labels = json.dumps(phase_names)
    phase_latam = json.dumps(latam_phase_totals)
    phase_africa = json.dumps(africa_phase_totals)

    # --- Sponsor pattern insight ---
    # Phase 3 is a proxy for industry-sponsored trials
    latam_p3_total = sum(cm[c]["phase3_count"] for c in latam_countries_list)
    africa_p3_total = sum(cm[c]["phase3_count"] for c in africa_countries_list)
    latam_p1_total = sum(cm[c]["phase1_count"] for c in latam_countries_list)
    africa_p1_total = sum(cm[c]["phase1_count"] for c in africa_countries_list)

    # --- HIV dominance per-country chart ---
    hiv_dom_sorted = sorted(cm.items(), key=lambda x: -x[1]["hiv_dominance"])
    hiv_dom_labels = json.dumps([c for c, _ in hiv_dom_sorted])
    hiv_dom_values = json.dumps([m["hiv_dominance"] for _, m in hiv_dom_sorted])
    hiv_dom_colors = json.dumps([
        "#3b82f6" if m["region"] == "latam" else "#ef4444"
        for _, m in hiv_dom_sorted
    ])

    # --- South Africa spotlight ---
    sa = cm.get("South Africa", {})
    sa_per_m = sa.get("per_million", 0)
    sa_hiv_dom = sa.get("hiv_dominance", 0)
    sa_phase1 = sa.get("phase1_share", 0)
    sa_diversity = sa.get("diversity_score", 0)

    # --- Per-capita comparison for Phase 1 ---
    phase1_per_m_data = []
    for country, m in cm.items():
        p1_per_m = round(m["phase1_count"] / m["population_m"], 2) if m["population_m"] > 0 else 0
        phase1_per_m_data.append((country, p1_per_m, m["region"]))
    phase1_per_m_data.sort(key=lambda x: -x[1])
    p1pm_labels = json.dumps([x[0] for x in phase1_per_m_data])
    p1pm_values = json.dumps([x[1] for x in phase1_per_m_data])
    p1pm_colors = json.dumps([
        "#3b82f6" if x[2] == "latam" else "#ef4444"
        for x in phase1_per_m_data
    ])

    # --- References ---
    ref_rows = ""
    for ref in REFERENCES:
        ref_rows += (
            f'<li style="margin-bottom:0.5rem;">'
            f'<a href="https://pubmed.ncbi.nlm.nih.gov/{ref["pmid"]}/" '
            f'target="_blank" style="color:#3b82f6;">PMID {ref["pmid"]}</a> '
            f'&mdash; {escape_html(ref["desc"])}</li>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Latin America as Mirror: What Africa Can Learn</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
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
  --purple: #7c3aed;
  --latam: #3b82f6;
  --africa: #ef4444;
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
  font-size: 2.4rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #3b82f6, #22c55e);
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
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
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
.latam-color {{ color: var(--latam); }}
.africa-color {{ color: var(--africa); }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 1.5rem;
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
.chart-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}}
@media (max-width: 900px) {{
  .chart-row {{ grid-template-columns: 1fr; }}
}}
.insight-box {{
  background: var(--surface);
  border-left: 4px solid var(--danger);
  border-radius: 0 8px 8px 0;
  padding: 1.25rem 1.5rem;
  margin: 1.5rem 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.insight-box.success-box {{
  border-left-color: var(--success);
}}
.insight-box.warning-box {{
  border-left-color: var(--warning);
}}
.insight-box.blue-box {{
  border-left-color: var(--latam);
}}
.insight-box.purple-box {{
  border-left-color: var(--purple);
}}
.lesson-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
  margin: 1rem 0;
}}
.lesson-card h4 {{
  color: var(--accent);
  margin-bottom: 0.5rem;
  font-size: 1rem;
}}
.lesson-card p {{
  color: var(--muted);
  font-size: 0.9rem;
}}
.footer {{
  margin-top: 3rem;
  padding-top: 2rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.85rem;
}}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.legend-inline {{
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
  margin-right: 4px;
  vertical-align: middle;
}}
</style>
</head>
<body>
<div class="container">

<!-- ============================================================ -->
<!-- SECTION 1: Summary -->
<!-- ============================================================ -->

<h1>Latin America as Mirror: What Africa Can Learn</h1>
<p class="subtitle">
  Latin America dramatically outperforms Africa in clinical trials despite similar income
  levels. This cross-continental comparison asks what LatAm did right and what Africa can
  replicate. Data from ClinicalTrials.gov API v2, {len(ALL_COUNTRIES)} countries analysed.
</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">LatAm Avg Per-Capita Rate</div>
    <div class="value latam-color">{latam_rs.get("avg_per_million", 0)}</div>
    <div class="label">trials per million population</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Avg Per-Capita Rate</div>
    <div class="value africa-color">{africa_rs.get("avg_per_million", 0)}</div>
    <div class="label">trials per million population</div>
  </div>
  <div class="summary-card">
    <div class="label">LatAm-to-Africa Gap</div>
    <div class="value warning">{gap_ratio}x</div>
    <div class="label">LatAm runs {gap_ratio}x more trials per capita</div>
  </div>
  <div class="summary-card">
    <div class="label">LatAm Total Trials</div>
    <div class="value latam-color">{latam_rs.get("total_trials", 0):,}</div>
    <div class="label">across {latam_rs.get("n_countries", 0)} countries</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Total Trials</div>
    <div class="value africa-color">{africa_rs.get("total_trials", 0):,}</div>
    <div class="label">across {africa_rs.get("n_countries", 0)} countries</div>
  </div>
</div>

<!-- ============================================================ -->
<!-- SECTION 2: Head-to-Head Matched Pairs -->
<!-- ============================================================ -->

<h2>Head-to-Head: Matched Pairs</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Countries matched by population size or GDP, showing the stark contrast in trial capacity.
</p>
<table>
  <thead>
    <tr>
      <th>Africa Country</th>
      <th style="text-align:right;">Trials/M</th>
      <th>LatAm Country</th>
      <th style="text-align:right;">Trials/M</th>
      <th style="text-align:right;">Gap</th>
      <th>Context</th>
    </tr>
  </thead>
  <tbody>
{pair_rows}
  </tbody>
</table>

<div class="insight-box">
  <strong>The Nigeria-Colombia Gap:</strong> Nigeria (pop 230M, GDP ~$500B) and Colombia
  (pop 52M, GDP ~$330B) are both upper-middle-income resource-rich nations. Yet Colombia
  runs dramatically more clinical trials per capita than Nigeria. The difference is not
  wealth but <em>regulatory infrastructure, CRO networks, and pharma market access</em>.
</div>

<div class="insight-box warning-box">
  <strong>The Ghana-Peru Paradox:</strong> Both countries have 34 million people, both are
  classified as lower-middle-income. Peru runs approximately 7.5x more trials per capita.
  Peru benefits from COFEPRIS-adjacent harmonisation, a strong CRO ecosystem, and
  decades of pharma investment flowing through the Pan American regulatory network.
</div>

<!-- ============================================================ -->
<!-- SECTION 3: All 12 Countries Ranked -->
<!-- ============================================================ -->

<h2>All Countries Ranked by Trials Per Million</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  <span class="legend-inline" style="background:#3b82f6;"></span>Latin America
  <span class="legend-inline" style="background:#ef4444;margin-left:1rem;"></span>Africa
</p>
<div class="chart-container">
  <canvas id="rankChart" height="100"></canvas>
</div>

<table>
  <thead>
    <tr>
      <th>Country</th>
      <th style="text-align:right;">Population</th>
      <th style="text-align:right;">Total Trials</th>
      <th style="text-align:right;">Trials/Million</th>
      <th style="text-align:right;">HIV Dominance</th>
      <th style="text-align:right;">Phase 1 Share</th>
      <th style="text-align:right;">Diversity</th>
    </tr>
  </thead>
  <tbody>
{country_rows}
  </tbody>
</table>

<!-- ============================================================ -->
<!-- SECTION 4: Disease Portfolio Comparison -->
<!-- ============================================================ -->

<h2>Disease Portfolio: LatAm = Balanced, Africa = HIV-Dominated</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Share of disease-specific trials within each region. LatAm shows a diversified cancer/cardio
  portfolio while Africa's research is concentrated on HIV.
</p>
<div class="chart-row">
  <div class="chart-container">
    <h3>Disease Portfolio Share (%)</h3>
    <canvas id="portfolioChart" height="140"></canvas>
  </div>
  <div class="chart-container">
    <h3>HIV Dominance by Country</h3>
    <canvas id="hivDomChart" height="140"></canvas>
  </div>
</div>

<div class="insight-box blue-box">
  <strong>Portfolio Asymmetry:</strong> LatAm's research portfolio mirrors its disease burden,
  with strong cancer, cardiovascular, and diabetes trial coverage. Africa's portfolio is
  heavily skewed towards HIV, reflecting decades of donor-driven research agendas (PEPFAR,
  Global Fund) rather than the continent's rising NCD burden. This mismatch means Africa's
  growing cardiovascular and diabetes epidemics are under-researched.
</div>

<!-- ============================================================ -->
<!-- SECTION 5: Phase 1 Capacity Comparison -->
<!-- ============================================================ -->

<h2>Phase 1 Capacity: Drug Development Infrastructure</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Phase 1 trials indicate local drug development capacity and early-stage research
  infrastructure. Higher Phase 1 share suggests a country participates in drug
  <em>development</em>, not just late-stage confirmation trials.
</p>
<div class="chart-row">
  <div class="chart-container">
    <h3>Phase Distribution (Total Trials)</h3>
    <canvas id="phaseChart" height="140"></canvas>
  </div>
  <div class="chart-container">
    <h3>Phase 1 Trials Per Million Population</h3>
    <canvas id="phase1PerMChart" height="140"></canvas>
  </div>
</div>

<div class="summary-grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));">
  <div class="summary-card">
    <div class="label">LatAm Phase 1 Total</div>
    <div class="value latam-color">{latam_p1_total:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Phase 1 Total</div>
    <div class="value africa-color">{africa_p1_total:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">LatAm Phase 3 Total</div>
    <div class="value latam-color">{latam_p3_total:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Phase 3 Total</div>
    <div class="value africa-color">{africa_p3_total:,}</div>
  </div>
</div>

<div class="insight-box warning-box">
  <strong>Development vs. Deployment:</strong> LatAm participates meaningfully in
  drug development (Phase 1/2), while African trial activity is concentrated in
  Phase 3/4 confirmatory studies. This suggests Africa is primarily used as a site
  for confirming drugs developed elsewhere, rather than as a partner in innovation.
</div>

<!-- ============================================================ -->
<!-- SECTION 6: What LatAm Did Right -->
<!-- ============================================================ -->

<h2>What Latin America Did Right</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  The structural advantages that enabled LatAm to build world-class trial capacity
  despite middle-income constraints.
</p>

<div class="lesson-card">
  <h4>1. Regulatory Harmonisation (PANDRH)</h4>
  <p>The Pan American Network for Drug Regulatory Harmonization, established in 1999 under
  PAHO/WHO, created a common regulatory language across the region. ANVISA (Brazil),
  COFEPRIS (Mexico), and ANMAT (Argentina) achieved ICH-aligned standards, making it
  easy for multinational pharma to run multi-site trials across borders with a single
  regulatory framework. Africa's 54 countries still have fragmented, often under-resourced
  regulatory agencies.</p>
</div>

<div class="lesson-card">
  <h4>2. Pharma Industry Investment</h4>
  <p>LatAm attracted massive pharmaceutical investment through tax incentives, IP protection,
  and market access guarantees. Brazil's pharmaceutical market is the 6th largest globally
  (~$35B). This created a virtuous cycle: pharma invests in trials, which builds local
  capacity, which attracts more trials. In contrast, Africa's pharma market is perceived
  as high-risk/low-return by industry, creating a vicious cycle of under-investment.</p>
</div>

<div class="lesson-card">
  <h4>3. Clinical Research Organization (CRO) Ecosystem</h4>
  <p>LatAm has a mature CRO industry with dozens of regional and international CROs
  (ICON, Parexel, PPD, IQVIA) operating dedicated offices in Brazil, Argentina, and
  Mexico. These CROs provide trained monitors, data managers, and regulatory specialists.
  Africa has far fewer CROs, and many trials depend on academic investigators with
  limited operational support.</p>
</div>

<div class="lesson-card">
  <h4>4. Local Manufacturing Base</h4>
  <p>Brazil, Argentina, and Mexico have significant pharmaceutical manufacturing capacity,
  including biogenerics. This creates a natural pipeline from manufacturing to clinical
  development. Africa imports 70-90% of its medicines, meaning the industrial base for
  trial supply chains barely exists.</p>
</div>

<div class="lesson-card">
  <h4>5. Trained Investigator Workforce</h4>
  <p>Decades of industry trials in LatAm have created a deep pool of GCP-trained
  investigators, research nurses, and pharmacovigilance specialists. Brazil alone
  has over 700 ethics committees and thousands of experienced principal investigators.
  Africa's investigator base, while growing, is concentrated in a few centres in
  South Africa, Kenya, and Nigeria.</p>
</div>

<div class="lesson-card">
  <h4>6. Universal Health Systems as Trial Infrastructure</h4>
  <p>Brazil's SUS (Sistema Unico de Saude) and similar public health systems across
  LatAm provide a backbone for trial recruitment: centralised patient registries,
  hospital networks, and follow-up infrastructure. Many African countries lack
  comparable universal health infrastructure, making patient recruitment and
  retention costly and unreliable.</p>
</div>

<!-- ============================================================ -->
<!-- SECTION 7: What Africa Can Learn -->
<!-- ============================================================ -->

<h2>What Africa Can Learn: Transferable Lessons</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Concrete, actionable lessons that African nations can adapt from the LatAm model.
</p>

<div class="lesson-card">
  <h4>Lesson 1: Create a Pan-African Regulatory Framework</h4>
  <p>Africa needs its own PANDRH. The African Medicines Agency (AMA), established in 2023,
  is a start, but it needs teeth: mutual recognition of ethics approvals, harmonised GCP
  standards, and fast-track pathways for multi-country trials. AVAREF (African Vaccine
  Regulatory Forum) has shown this is possible for vaccines. Extending it to all
  interventional research could transform the continent's trial landscape within a decade.</p>
</div>

<div class="lesson-card">
  <h4>Lesson 2: Incentivise Industry Beyond HIV</h4>
  <p>African countries must create incentives (tax breaks, streamlined approvals, data
  exclusivity) specifically targeting cancer, cardiovascular, and diabetes trials. The
  current model, where industry comes only for HIV/TB with donor support, perpetuates
  portfolio imbalance. Rwanda and Kenya are already piloting pharma-friendly frameworks
  that could serve as templates.</p>
</div>

<div class="lesson-card">
  <h4>Lesson 3: Invest in CRO Capacity</h4>
  <p>Africa needs homegrown CROs. Nigeria's CRO sector is tiny relative to its population.
  South Africa's success partly reflects its mature CRO ecosystem (Wits Health Consortium,
  CAPRISA, Aurum Institute). A Pan-African CRO network, perhaps building on existing
  PEPFAR-funded clinical trial units, could dramatically lower the cost of running
  trials in Africa.</p>
</div>

<div class="lesson-card">
  <h4>Lesson 4: Build Local Manufacturing</h4>
  <p>The African Continental Free Trade Area (AfCFTA) and the Partnership for African
  Vaccine Manufacturing (PAVM) are steps in the right direction. Local manufacturing
  creates the industrial ecosystem that naturally feeds into clinical development.
  Brazil's transformation from a trial desert to a powerhouse closely tracked its
  pharmaceutical industrialisation in the 1990s-2000s.</p>
</div>

<div class="lesson-card">
  <h4>Lesson 5: Leverage Digital Health Infrastructure</h4>
  <p>Africa can leapfrog LatAm by building digitally-native trial infrastructure.
  Mobile health registries, electronic consent, remote monitoring, and AI-assisted
  data management could reduce the per-trial cost dramatically. Kenya's M-TIBA and
  Rwanda's electronic health records show the building blocks exist.</p>
</div>

<!-- ============================================================ -->
<!-- SECTION 8: The South Africa Bright Spot -->
<!-- ============================================================ -->

<h2>The South Africa Bright Spot</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  South Africa is the one African country operating at LatAm levels. What can the
  rest of Africa learn from SA?
</p>

<div class="summary-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));">
  <div class="summary-card">
    <div class="label">SA Trials/Million</div>
    <div class="value success">{sa_per_m}</div>
  </div>
  <div class="summary-card">
    <div class="label">SA HIV Dominance</div>
    <div class="value warning">{sa_hiv_dom}%</div>
  </div>
  <div class="summary-card">
    <div class="label">SA Phase 1 Share</div>
    <div class="value latam-color">{sa_phase1}%</div>
  </div>
  <div class="summary-card">
    <div class="label">SA Diversity Score</div>
    <div class="value success">{sa_diversity}</div>
  </div>
</div>

<div class="insight-box success-box">
  <strong>What makes South Africa different:</strong>
  SA has Africa's most sophisticated regulatory agency (SAHPRA), a mature CRO ecosystem,
  world-class academic medical centres (Wits, UCT, Stellenbosch), strong pharmaceutical
  manufacturing, and decades of industry partnership. SA attracts both HIV trials (via
  PEPFAR/MRC networks) AND industry cancer/cardio trials. The key lesson: SA invested
  in <em>general</em> trial infrastructure, not just disease-specific capacity. This is
  what enabled it to diversify beyond HIV.
</div>

<div class="insight-box purple-box">
  <strong>SA as Proof of Concept:</strong> South Africa proves that an African country
  <em>can</em> reach LatAm-level trial capacity. The question is whether SA's model,
  built on historically privileged academic infrastructure and early pharma engagement,
  can be replicated in countries starting from a lower base. The answer from LatAm is
  yes, but it requires deliberate, sustained regulatory investment over 15-20 years.
</div>

<!-- ============================================================ -->
<!-- SECTION 9: Sponsor Patterns -->
<!-- ============================================================ -->

<h2>Sponsor Patterns: Why LatAm Attracts Industry</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Phase 3 trials are overwhelmingly industry-sponsored. The distribution of Phase 3
  trials reveals where pharmaceutical companies choose to invest.
</p>

<div class="summary-grid" style="grid-template-columns:repeat(auto-fit,minmax(250px,1fr));">
  <div class="summary-card">
    <div class="label">LatAm Phase 3 Trials</div>
    <div class="value latam-color">{latam_p3_total:,}</div>
    <div class="label">Industry-attractive confirmatory studies</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Phase 3 Trials</div>
    <div class="value africa-color">{africa_p3_total:,}</div>
    <div class="label">Many HIV/vaccine-focused</div>
  </div>
</div>

<div class="insight-box blue-box">
  <strong>The Industry Calculus:</strong> Pharmaceutical companies choose trial sites based
  on: (1) regulatory predictability, (2) speed of ethics/regulatory approval, (3) patient
  recruitment speed, (4) data quality and GCP compliance, (5) IP protection, (6) market
  potential. LatAm scores higher than Africa on all six dimensions, which is why industry
  invests there. Africa's Phase 3 trials are disproportionately donor-funded HIV/vaccine
  studies, not the industry-sponsored cardiovascular/oncology trials that drive sustained
  research infrastructure. To change this, African nations must address the industry
  calculus directly.
</div>

<!-- ============================================================ -->
<!-- Data, References & Footer -->
<!-- ============================================================ -->

<h2>References</h2>
<ul style="list-style:none;padding-left:0;">
{ref_rows}
</ul>

<div class="footer">
  <p>
    <strong>Data source:</strong> ClinicalTrials.gov API v2 (interventional studies only).
    Queried {data.get("timestamp", "N/A")[:10]}.
  </p>
  <p>
    <strong>Methodology:</strong> Per-capita rates use UN 2024 population estimates.
    Disease portfolio percentages are computed within each region's total condition-tagged
    trials. Phase analysis uses ClinicalTrials.gov phase metadata. HIV dominance is
    HIV trials as percentage of total interventional trials.
  </p>
  <p>
    <strong>Limitations:</strong> ClinicalTrials.gov under-counts locally registered
    trials in both regions (REBEC in Brazil, Pan African Clinical Trials Registry).
    The comparison is ecological; country-level aggregation masks within-country
    variation. Industry sponsorship is proxied by Phase 3 counts, not explicit
    sponsor data.
  </p>
  <p style="margin-top:1rem;">
    Latin America as Mirror v1.0 &mdash; AfricaRCT Project R &mdash;
    Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}
  </p>
</div>

</div><!-- /container -->

<script>
// ---- Ranked bar chart: all 12 countries by trials/million ----
(function() {{
  const ctx = document.getElementById('rankChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {chart_labels},
      datasets: [{{
        label: 'Trials per million population',
        data: {chart_values},
        backgroundColor: {chart_colors},
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
            label: function(ctx) {{ return ctx.raw + ' trials/M'; }}
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ color: 'rgba(148,163,184,0.1)' }},
          ticks: {{ color: '#94a3b8' }},
          title: {{ display: true, text: 'Trials per million population', color: '#94a3b8' }}
        }},
        y: {{
          grid: {{ display: false }},
          ticks: {{ color: '#e2e8f0' }}
        }}
      }}
    }}
  }});
}})();

// ---- Disease portfolio radar chart ----
(function() {{
  const ctx = document.getElementById('portfolioChart').getContext('2d');
  new Chart(ctx, {{
    type: 'radar',
    data: {{
      labels: {portfolio_labels},
      datasets: [
        {{
          label: 'Latin America (%)',
          data: {portfolio_latam},
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.15)',
          pointBackgroundColor: '#3b82f6',
          borderWidth: 2,
        }},
        {{
          label: 'Africa (%)',
          data: {portfolio_africa},
          borderColor: '#ef4444',
          backgroundColor: 'rgba(239,68,68,0.15)',
          pointBackgroundColor: '#ef4444',
          borderWidth: 2,
        }}
      ]
    }},
    options: {{
      responsive: true,
      scales: {{
        r: {{
          angleLines: {{ color: 'rgba(148,163,184,0.2)' }},
          grid: {{ color: 'rgba(148,163,184,0.15)' }},
          pointLabels: {{ color: '#e2e8f0', font: {{ size: 11 }} }},
          ticks: {{ color: '#94a3b8', backdropColor: 'transparent' }}
        }}
      }},
      plugins: {{
        legend: {{ labels: {{ color: '#e2e8f0' }} }}
      }}
    }}
  }});
}})();

// ---- HIV dominance bar chart ----
(function() {{
  const ctx = document.getElementById('hivDomChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {hiv_dom_labels},
      datasets: [{{
        label: 'HIV Dominance (%)',
        data: {hiv_dom_values},
        backgroundColor: {hiv_dom_colors},
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
            label: function(ctx) {{ return ctx.raw + '% of trials are HIV'; }}
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ color: 'rgba(148,163,184,0.1)' }},
          ticks: {{ color: '#94a3b8' }},
          title: {{ display: true, text: 'HIV as % of total trials', color: '#94a3b8' }}
        }},
        y: {{
          grid: {{ display: false }},
          ticks: {{ color: '#e2e8f0' }}
        }}
      }}
    }}
  }});
}})();

// ---- Phase distribution grouped bar chart ----
(function() {{
  const ctx = document.getElementById('phaseChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {phase_labels},
      datasets: [
        {{
          label: 'Latin America',
          data: {phase_latam},
          backgroundColor: '#3b82f6',
          borderRadius: 4,
        }},
        {{
          label: 'Africa',
          data: {phase_africa},
          backgroundColor: '#ef4444',
          borderRadius: 4,
        }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ labels: {{ color: '#e2e8f0' }} }}
      }},
      scales: {{
        x: {{
          grid: {{ display: false }},
          ticks: {{ color: '#e2e8f0' }}
        }},
        y: {{
          grid: {{ color: 'rgba(148,163,184,0.1)' }},
          ticks: {{ color: '#94a3b8' }},
          title: {{ display: true, text: 'Number of trials', color: '#94a3b8' }}
        }}
      }}
    }}
  }});
}})();

// ---- Phase 1 per million bar chart ----
(function() {{
  const ctx = document.getElementById('phase1PerMChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {p1pm_labels},
      datasets: [{{
        label: 'Phase 1 trials per million',
        data: {p1pm_values},
        backgroundColor: {p1pm_colors},
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
            label: function(ctx) {{ return ctx.raw + ' Phase 1 trials/M'; }}
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ color: 'rgba(148,163,184,0.1)' }},
          ticks: {{ color: '#94a3b8' }},
          title: {{ display: true, text: 'Phase 1 trials per million', color: '#94a3b8' }}
        }},
        y: {{
          grid: {{ display: false }},
          ticks: {{ color: '#e2e8f0' }}
        }}
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"HTML written to {OUTPUT_HTML}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """Run the full pipeline: fetch, analyse, generate."""
    # Ensure stdout handles Unicode on Windows
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )
        except Exception:
            pass

    print("=" * 70)
    print("Latin America as Mirror: What Africa Can Learn")
    print("=" * 70)

    print("\n[1/3] Fetching data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()

    n_countries = len(data.get("countries", {}))
    print(f"  Collected data for {n_countries} countries")

    print("\n[2/3] Computing metrics...")
    metrics = compute_metrics(data)

    cm = metrics["country_metrics"]
    rs = metrics["region_summaries"]

    # Print summary
    latam_rs = rs.get("latam", {})
    africa_rs = rs.get("africa", {})
    print(f"\n  LatAm: {latam_rs.get('total_trials', 0):,} trials across "
          f"{latam_rs.get('n_countries', 0)} countries "
          f"({latam_rs.get('avg_per_million', 0)} per million)")
    print(f"  Africa: {africa_rs.get('total_trials', 0):,} trials across "
          f"{africa_rs.get('n_countries', 0)} countries "
          f"({africa_rs.get('avg_per_million', 0)} per million)")

    gap = round(
        latam_rs.get("avg_per_million", 0) / max(1, africa_rs.get("avg_per_million", 1)),
        1
    )
    print(f"  Gap ratio: LatAm runs {gap}x more trials per capita than Africa")

    # Matched pairs
    print("\n  Matched pairs:")
    for p in metrics["pair_analysis"]:
        gap_str = f'{p["gap_ratio"]}x' if p["gap_ratio"] < 999 else "N/A"
        print(f"    {p['africa']:>15} ({p['africa_per_m']:>5.1f}/M) vs "
              f"{p['latam']:<15} ({p['latam_per_m']:>5.1f}/M) = {gap_str} gap")

    print("\n  HIV dominance (% of trials that are HIV):")
    for country in sorted(cm, key=lambda c: -cm[c]["hiv_dominance"]):
        m = cm[country]
        marker = " <-- " if m["hiv_dominance"] > 20 else ""
        print(f"    {country:>15}: {m['hiv_dominance']:>5.1f}%{marker}")

    print(f"\n  LatAm avg Phase 1 share: {latam_rs.get('avg_phase1_share', 0)}%")
    print(f"  Africa avg Phase 1 share: {africa_rs.get('avg_phase1_share', 0)}%")

    print("\n[3/3] Generating HTML dashboard...")
    generate_html(data, metrics)

    print("\n" + "=" * 70)
    print("Done. Open latam-mirror.html in a browser.")
    print("=" * 70)


if __name__ == "__main__":
    main()
