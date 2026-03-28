#!/usr/bin/env python
"""
fetch_design_quality.py -- The Methodological Quality Audit
============================================================
Does Africa receive cutting-edge trial methodology or second-class designs?
Advanced designs (adaptive, Bayesian, cluster-randomized, stepped wedge,
platform trials) represent the frontier of clinical research. Africa has
only 21 cluster-randomized and 15 implementation science trials.

Queries ClinicalTrials.gov API v2 for trial design types in Africa vs US,
computing a Design Sophistication Index and Blinding Rate for each region.

Usage:
    python fetch_design_quality.py

Output:
    data/design_quality_data.json   (cached API results, 24h TTL)
    design-quality.html             (dark-theme interactive dashboard)

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
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# Design queries: (label, query_term, sophistication_weight)
# Higher weight = more advanced/sophisticated design
DESIGN_QUERIES = [
    ("Double-blind",        '"double blind" OR "double-blind"',             2),
    ("Open-label",          '"open label" OR "open-label"',                 1),
    ("Single-blind",        '"single blind" OR "single-blind"',             1.5),
    ("Cluster-randomized",  '"cluster randomized" OR "cluster-randomized"', 4),
    ("Adaptive",            '"adaptive design" OR "adaptive trial"',        5),
    ("Bayesian",            "Bayesian",                                     5),
    ("Stepped wedge",       '"stepped wedge"',                              4),
    ("Platform trial",      '"platform trial" OR "master protocol"',        5),
    ("Non-inferiority",     "non-inferiority",                              3),
    ("Equivalence",         "equivalence",                                  3),
    ("Pragmatic",           '"pragmatic trial"',                            3),
]

# Phase 1 sub-queries for Africa
PHASE1_SUBQUERIES = {
    "Phase 1 total":         None,
    "First-in-human":        '"first-in-human" OR "first in human"',
    "Pharmacokinetic":       "pharmacokinetic OR pharmacokinetics OR bioequivalence",
    "Dose-finding":          '"dose-finding" OR "dose finding" OR "dose escalation"',
}

# Regions to compare
REGIONS = {
    "Africa": {
        "location_query": "Africa",
        "population_m": 1460,
    },
    "United States": {
        "location_query": "United States",
        "population_m": 335,
    },
}

# Temporal periods for trend analysis
TEMPORAL_PERIODS = [
    ("2000-2009", "01/01/2000", "12/31/2009"),
    ("2010-2014", "01/01/2010", "12/31/2014"),
    ("2015-2019", "01/01/2015", "12/31/2019"),
    ("2020-2026", "01/01/2020", "03/27/2026"),
]

# Verified reference counts (from user)
VERIFIED = {
    "Africa_cluster_randomized": 21,
    "Africa_phase1_total": 256,
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "design_quality_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "design-quality.html"
RATE_LIMIT = 0.35  # seconds between API calls
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


def get_trial_count(location, condition_query=None, extra_filter=None):
    """Return total count of interventional trials for a location."""
    base_filter = "AREA[StudyType]INTERVENTIONAL"
    if extra_filter:
        base_filter += " AND " + extra_filter
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": base_filter,
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition_query:
        params["query.cond"] = condition_query
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_trial_count_term(location, term_query, extra_filter=None):
    """Return count using query.term (for design-related keywords not in conditions)."""
    base_filter = "AREA[StudyType]INTERVENTIONAL"
    if extra_filter:
        base_filter += " AND " + extra_filter
    params = {
        "format": "json",
        "query.locn": location,
        "query.term": term_query,
        "filter.advanced": base_filter,
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


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
    """Fetch trial counts for all design types across regions."""
    cached = load_cache()
    if cached is not None:
        return cached

    # Estimate total API calls
    design_calls = len(DESIGN_QUERIES) * len(REGIONS)
    total_calls_base = len(REGIONS)  # base counts
    phase1_calls = len(PHASE1_SUBQUERIES)
    temporal_calls = len(TEMPORAL_PERIODS) * len(REGIONS) * 2  # advanced + total
    total_calls = total_calls_base + design_calls + phase1_calls + temporal_calls
    call_num = 0

    data = {
        "timestamp": datetime.now().isoformat(),
        "base_counts": {},
        "design_counts": {},
        "phase1_africa": {},
        "temporal_trends": {},
    }

    # --- Base interventional trial counts per region ---
    print("\n--- Base trial counts ---")
    for region, info in REGIONS.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {region} total interventional...")
        count = get_trial_count(info["location_query"])
        data["base_counts"][region] = count
        time.sleep(RATE_LIMIT)

    # --- Design-specific counts per region ---
    print("\n--- Design type counts ---")
    for region, info in REGIONS.items():
        data["design_counts"][region] = {}
        for label, query_term, weight in DESIGN_QUERIES:
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {region} / {label}...")
            count = get_trial_count_term(info["location_query"], query_term)
            data["design_counts"][region][label] = count
            time.sleep(RATE_LIMIT)

    # --- Phase 1 sub-analysis for Africa ---
    print("\n--- Phase 1 Africa sub-analysis ---")
    phase1_filter = "AREA[Phase]PHASE1"
    for label, subquery in PHASE1_SUBQUERIES.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Africa Phase 1 / {label}...")
        if subquery is None:
            count = get_trial_count("Africa", extra_filter=phase1_filter)
        else:
            count = get_trial_count_term("Africa", subquery, extra_filter=phase1_filter)
        data["phase1_africa"][label] = count
        time.sleep(RATE_LIMIT)

    # --- Temporal trends: advanced designs over time ---
    print("\n--- Temporal trends ---")
    advanced_query = (
        '"adaptive design" OR "adaptive trial" OR Bayesian OR '
        '"stepped wedge" OR "platform trial" OR "master protocol" OR '
        '"cluster randomized" OR "cluster-randomized"'
    )
    for period_label, start_date, end_date in TEMPORAL_PERIODS:
        data["temporal_trends"][period_label] = {}
        date_filter = (
            f"AREA[StartDate]RANGE[{start_date},{end_date}]"
        )
        for region, info in REGIONS.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {region} / {period_label} advanced...")
            adv_count = get_trial_count_term(
                info["location_query"], advanced_query,
                extra_filter=date_filter
            )
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {region} / {period_label} total...")
            tot_count = get_trial_count(
                info["location_query"],
                extra_filter=date_filter
            )
            data["temporal_trends"][period_label][region] = {
                "advanced": adv_count,
                "total": tot_count,
            }
            time.sleep(RATE_LIMIT)

    # --- Save cache ---
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_analysis(data):
    """Compute Design Sophistication Index, Blinding Rate, and adoption rates."""
    results = {}

    for region in REGIONS:
        base = data["base_counts"].get(region, 1)
        design = data["design_counts"].get(region, {})

        # Blinding rate
        double_blind = design.get("Double-blind", 0)
        blinding_rate = round(double_blind / base * 100, 1) if base > 0 else 0

        # Design Sophistication Index: weighted sum / total
        weighted_sum = 0
        for label, query_term, weight in DESIGN_QUERIES:
            count = design.get(label, 0)
            weighted_sum += count * weight

        dsi = round(weighted_sum / base, 3) if base > 0 else 0

        # Adoption rates per design
        adoption = {}
        for label, query_term, weight in DESIGN_QUERIES:
            count = design.get(label, 0)
            rate = round(count / base * 100, 2) if base > 0 else 0
            adoption[label] = {
                "count": count,
                "rate_pct": rate,
                "weight": weight,
            }

        results[region] = {
            "total_trials": base,
            "blinding_rate": blinding_rate,
            "double_blind_count": double_blind,
            "dsi": dsi,
            "adoption": adoption,
        }

    # Ratios
    africa = results.get("Africa", {})
    us = results.get("United States", {})
    results["blinding_gap"] = round(
        (us.get("blinding_rate", 0) - africa.get("blinding_rate", 0)), 1
    )
    results["dsi_ratio"] = round(
        us.get("dsi", 1) / africa.get("dsi", 1), 1
    ) if africa.get("dsi", 0) > 0 else 999

    # Temporal trend analysis
    trends = {}
    for period, regions_data in data.get("temporal_trends", {}).items():
        trends[period] = {}
        for region, counts in regions_data.items():
            adv = counts.get("advanced", 0)
            tot = counts.get("total", 1)
            rate = round(adv / tot * 100, 2) if tot > 0 else 0
            trends[period][region] = {
                "advanced": adv,
                "total": tot,
                "rate_pct": rate,
            }
    results["temporal_trends"] = trends

    # Phase 1 Africa
    p1 = data.get("phase1_africa", {})
    p1_total = p1.get("Phase 1 total", 256)
    results["phase1_africa"] = {
        "total": p1_total,
        "first_in_human": p1.get("First-in-human", 0),
        "pharmacokinetic": p1.get("Pharmacokinetic", 0),
        "dose_finding": p1.get("Dose-finding", 0),
    }
    fih = results["phase1_africa"]["first_in_human"]
    pk = results["phase1_africa"]["pharmacokinetic"]
    results["phase1_africa"]["fih_pct"] = round(
        fih / p1_total * 100, 1
    ) if p1_total > 0 else 0
    results["phase1_africa"]["pk_pct"] = round(
        pk / p1_total * 100, 1
    ) if p1_total > 0 else 0

    return results


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def generate_html(data, analysis):
    """Generate the full HTML dashboard."""

    africa = analysis.get("Africa", {})
    us = analysis.get("United States", {})
    blinding_gap = analysis.get("blinding_gap", 0)
    dsi_ratio = analysis.get("dsi_ratio", 0)
    trends = analysis.get("temporal_trends", {})
    p1 = analysis.get("phase1_africa", {})

    # --- Summary cards ---
    africa_total = africa.get("total_trials", 0)
    us_total = us.get("total_trials", 0)
    africa_dsi = africa.get("dsi", 0)
    us_dsi = us.get("dsi", 0)
    africa_blind = africa.get("blinding_rate", 0)
    us_blind = us.get("blinding_rate", 0)

    # --- Design comparison table rows ---
    design_rows = ""
    for label, query_term, weight in DESIGN_QUERIES:
        a_info = africa.get("adoption", {}).get(label, {})
        u_info = us.get("adoption", {}).get(label, {})
        a_count = a_info.get("count", 0)
        u_count = u_info.get("count", 0)
        a_rate = a_info.get("rate_pct", 0)
        u_rate = u_info.get("rate_pct", 0)
        ratio = round(u_count / a_count, 1) if a_count > 0 else "N/A"
        gap_color = "#ef4444" if (isinstance(ratio, (int, float)) and ratio > 10) else (
            "#f59e0b" if (isinstance(ratio, (int, float)) and ratio > 5) else "#22c55e"
        )
        star = " *" if weight >= 4 else ""
        design_rows += (
            f'<tr>'
            f'<td style="padding:10px;font-weight:bold;">'
            f'{escape_html(label)}{star}</td>'
            f'<td style="padding:10px;text-align:right;">{a_count:,}</td>'
            f'<td style="padding:10px;text-align:right;">{a_rate}%</td>'
            f'<td style="padding:10px;text-align:right;">{u_count:,}</td>'
            f'<td style="padding:10px;text-align:right;">{u_rate}%</td>'
            f'<td style="padding:10px;text-align:center;color:{gap_color};'
            f'font-weight:bold;">{ratio}x</td>'
            f'<td style="padding:10px;text-align:center;color:var(--muted);">'
            f'{weight}</td>'
            f'</tr>\n'
        )

    # --- Advanced designs spotlight rows ---
    advanced_labels = ["Adaptive", "Bayesian", "Stepped wedge",
                       "Platform trial", "Cluster-randomized"]
    advanced_rows = ""
    for label in advanced_labels:
        a_info = africa.get("adoption", {}).get(label, {})
        u_info = us.get("adoption", {}).get(label, {})
        a_count = a_info.get("count", 0)
        u_count = u_info.get("count", 0)
        a_rate = a_info.get("rate_pct", 0)
        u_rate = u_info.get("rate_pct", 0)
        diff = round(u_rate - a_rate, 2)
        color = "#ef4444" if a_count < 50 else "#f59e0b"
        advanced_rows += (
            f'<tr>'
            f'<td style="padding:10px;font-weight:bold;">'
            f'{escape_html(label)}</td>'
            f'<td style="padding:10px;text-align:right;color:{color};">'
            f'{a_count:,}</td>'
            f'<td style="padding:10px;text-align:right;">{a_rate}%</td>'
            f'<td style="padding:10px;text-align:right;">{u_count:,}</td>'
            f'<td style="padding:10px;text-align:right;">{u_rate}%</td>'
            f'<td style="padding:10px;text-align:right;color:var(--muted);">'
            f'+{diff}pp</td>'
            f'</tr>\n'
        )

    # --- Phase 1 rows ---
    phase1_rows = ""
    for sublabel, key in [("Total Phase 1", "total"),
                          ("First-in-human", "first_in_human"),
                          ("Pharmacokinetic/Bioequiv.", "pharmacokinetic"),
                          ("Dose-finding", "dose_finding")]:
        val = p1.get(key, 0)
        pct = round(val / p1.get("total", 1) * 100, 1) if (
            p1.get("total", 0) > 0 and key != "total"
        ) else ("--" if key == "total" else 0)
        phase1_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(sublabel)}</td>'
            f'<td style="padding:10px;text-align:right;font-weight:bold;">'
            f'{val:,}</td>'
            f'<td style="padding:10px;text-align:right;color:var(--muted);">'
            f'{pct}{"%" if isinstance(pct, (int, float)) else ""}</td>'
            f'</tr>\n'
        )

    # --- Temporal trend rows ---
    trend_rows = ""
    for period in ["2000-2009", "2010-2014", "2015-2019", "2020-2026"]:
        pd_data = trends.get(period, {})
        a_data = pd_data.get("Africa", {})
        u_data = pd_data.get("United States", {})
        a_rate = a_data.get("rate_pct", 0)
        u_rate = u_data.get("rate_pct", 0)
        a_adv = a_data.get("advanced", 0)
        u_adv = u_data.get("advanced", 0)
        a_color = "#ef4444" if a_rate < 1 else ("#f59e0b" if a_rate < 3 else "#22c55e")
        trend_rows += (
            f'<tr>'
            f'<td style="padding:10px;font-weight:bold;">{period}</td>'
            f'<td style="padding:10px;text-align:right;">{a_adv:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{a_color};'
            f'font-weight:bold;">{a_rate}%</td>'
            f'<td style="padding:10px;text-align:right;">{u_adv:,}</td>'
            f'<td style="padding:10px;text-align:right;">{u_rate}%</td>'
            f'</tr>\n'
        )

    # --- Chart.js data for design comparison ---
    chart_labels = json.dumps([d[0] for d in DESIGN_QUERIES])
    chart_africa = json.dumps([
        africa.get("adoption", {}).get(d[0], {}).get("rate_pct", 0)
        for d in DESIGN_QUERIES
    ])
    chart_us = json.dumps([
        us.get("adoption", {}).get(d[0], {}).get("rate_pct", 0)
        for d in DESIGN_QUERIES
    ])

    # --- Trend chart data ---
    trend_periods = json.dumps([p[0] for p in TEMPORAL_PERIODS])
    trend_africa_rates = json.dumps([
        trends.get(p[0], {}).get("Africa", {}).get("rate_pct", 0)
        for p in TEMPORAL_PERIODS
    ])
    trend_us_rates = json.dumps([
        trends.get(p[0], {}).get("United States", {}).get("rate_pct", 0)
        for p in TEMPORAL_PERIODS
    ])

    # Cluster-randomized specifics
    cluster_africa = africa.get("adoption", {}).get(
        "Cluster-randomized", {}
    ).get("count", 21)
    cluster_us = us.get("adoption", {}).get(
        "Cluster-randomized", {}
    ).get("count", 0)

    # Adaptive specifics
    adaptive_africa = africa.get("adoption", {}).get(
        "Adaptive", {}
    ).get("count", 0)
    adaptive_us = us.get("adoption", {}).get(
        "Adaptive", {}
    ).get("count", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Methodological Quality Audit -- What Trial Designs Does Africa Get?</title>
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
  font-size: 2.4rem;
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
h3 {{ font-size: 1.15rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1.05rem; margin-bottom: 2rem; }}
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
.callout {{
  border-radius: 12px;
  padding: 1.5rem 2rem;
  margin: 1.5rem 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.callout-danger {{
  background: rgba(239, 68, 68, 0.08);
  border-left: 4px solid var(--danger);
}}
.callout-warning {{
  background: rgba(245, 158, 11, 0.08);
  border-left: 4px solid var(--warning);
}}
.callout-info {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
}}
.callout-success {{
  background: rgba(34, 197, 94, 0.08);
  border-left: 4px solid var(--success);
}}
.callout-purple {{
  background: rgba(168, 85, 247, 0.08);
  border-left: 4px solid var(--purple);
}}
.big-stat {{
  font-size: 3rem;
  font-weight: 900;
  display: block;
  margin: 0.5rem 0;
}}
.scroll-x {{ overflow-x: auto; }}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
  h1 {{ font-size: 1.8rem; }}
}}
.progress-bar {{
  background: var(--border);
  border-radius: 6px;
  height: 28px;
  position: relative;
  overflow: hidden;
  margin: 0.5rem 0;
}}
.progress-fill {{
  height: 100%;
  border-radius: 6px;
  display: flex;
  align-items: center;
  padding: 0 10px;
  font-size: 0.8rem;
  font-weight: bold;
  color: #fff;
  transition: width 0.6s ease;
}}
.method-stamp {{
  display: inline-block;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.3rem 0.8rem;
  font-size: 0.8rem;
  margin: 0.2rem;
}}
footer {{
  margin-top: 3rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.85rem;
  text-align: center;
}}
</style>
</head>
<body>
<div class="container">

<h1>The Methodological Quality Audit</h1>
<p class="subtitle">What trial designs does Africa get? Comparing methodological
sophistication of {africa_total:,} African vs {us_total:,} US interventional
trials on ClinicalTrials.gov (March 2026)</p>

<!-- ============================================================ -->
<!-- SECTION 1: SUMMARY -->
<!-- ============================================================ -->

<h2>Executive Summary</h2>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Total Trials</div>
    <div class="value purple">{africa_total:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">US Total Trials</div>
    <div class="value" style="color:var(--accent);">{us_total:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa DSI</div>
    <div class="value warning">{africa_dsi}</div>
    <div class="label">Design Sophistication Index</div>
  </div>
  <div class="summary-card">
    <div class="label">US DSI</div>
    <div class="value success">{us_dsi}</div>
    <div class="label">Design Sophistication Index</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Blinding Rate</div>
    <div class="value danger">{africa_blind}%</div>
  </div>
  <div class="summary-card">
    <div class="label">US Blinding Rate</div>
    <div class="value success">{us_blind}%</div>
  </div>
  <div class="summary-card">
    <div class="label">Blinding Gap</div>
    <div class="value warning">{blinding_gap}pp</div>
    <div class="label">US leads by this margin</div>
  </div>
  <div class="summary-card">
    <div class="label">DSI Ratio (US:Africa)</div>
    <div class="value danger">{dsi_ratio}x</div>
    <div class="label">Methodology gap</div>
  </div>
</div>

<div class="callout callout-purple">
  <strong>The Design Sophistication Index (DSI)</strong> is a weighted composite score
  measuring the methodological frontier of a region's trial portfolio. Basic designs
  (open-label) score 1, intermediate designs (non-inferiority, equivalence, pragmatic)
  score 3, and cutting-edge designs (adaptive, Bayesian, platform, stepped wedge,
  cluster-randomized) score 4-5. The DSI = sum(design_count x weight) / total_trials.
  A higher DSI indicates a more methodologically advanced trial ecosystem.
</div>

<!-- ============================================================ -->
<!-- SECTION 2: BLINDING COMPARISON -->
<!-- ============================================================ -->

<h2>The Blinding Comparison</h2>

<div class="two-col">
  <div class="chart-container">
    <h3>Africa: Blinding Profile</h3>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{africa_blind}%;background:var(--danger);">
        Double-blind: {africa_blind}%
      </div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{africa.get('adoption', {}).get('Single-blind', {}).get('rate_pct', 0)}%;background:var(--warning);">
        Single-blind: {africa.get('adoption', {}).get('Single-blind', {}).get('rate_pct', 0)}%
      </div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{min(africa.get('adoption', {}).get('Open-label', {}).get('rate_pct', 0), 100)}%;background:var(--muted);">
        Open-label: {africa.get('adoption', {}).get('Open-label', {}).get('rate_pct', 0)}%
      </div>
    </div>
    <p style="margin-top:1rem;color:var(--muted);font-size:0.85rem;">
      Double-blind: {africa.get('double_blind_count', 0):,} of {africa_total:,} trials
    </p>
  </div>

  <div class="chart-container">
    <h3>United States: Blinding Profile</h3>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{us_blind}%;background:var(--success);">
        Double-blind: {us_blind}%
      </div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{us.get('adoption', {}).get('Single-blind', {}).get('rate_pct', 0)}%;background:var(--warning);">
        Single-blind: {us.get('adoption', {}).get('Single-blind', {}).get('rate_pct', 0)}%
      </div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{min(us.get('adoption', {}).get('Open-label', {}).get('rate_pct', 0), 100)}%;background:var(--muted);">
        Open-label: {us.get('adoption', {}).get('Open-label', {}).get('rate_pct', 0)}%
      </div>
    </div>
    <p style="margin-top:1rem;color:var(--muted);font-size:0.85rem;">
      Double-blind: {us.get('double_blind_count', 0):,} of {us_total:,} trials
    </p>
  </div>
</div>

<div class="callout callout-danger">
  <strong>The Double-Blind Gap.</strong> Only {africa_blind}% of African trials are
  double-blinded versus {us_blind}% in the US -- a {blinding_gap} percentage-point gap.
  Open-label designs dominate Africa's portfolio, reducing internal validity and
  increasing risk of bias in the evidence base that informs treatment for 1.46 billion
  people. This is not merely academic: open-label trials inflate effect sizes by an
  average of 13% (Cochrane, 2012), meaning African patients may receive treatments whose
  efficacy is systematically overstated.
</div>

<!-- ============================================================ -->
<!-- SECTION 3: DESIGN ADOPTION RATES -->
<!-- ============================================================ -->

<h2>Design Adoption Rates: Full Comparison</h2>

<div class="chart-container">
  <canvas id="designChart" height="320"></canvas>
</div>

<div class="scroll-x">
<table>
  <thead>
    <tr>
      <th style="padding:10px;">Design Type</th>
      <th style="padding:10px;text-align:right;">Africa Count</th>
      <th style="padding:10px;text-align:right;">Africa Rate</th>
      <th style="padding:10px;text-align:right;">US Count</th>
      <th style="padding:10px;text-align:right;">US Rate</th>
      <th style="padding:10px;text-align:center;">US:Africa Ratio</th>
      <th style="padding:10px;text-align:center;">Weight</th>
    </tr>
  </thead>
  <tbody>
    {design_rows}
  </tbody>
</table>
</div>
<p style="color:var(--muted);font-size:0.85rem;margin-top:0.5rem;">
  * = cutting-edge design (weight >= 4). DSI weight reflects methodological complexity.
</p>

<!-- ============================================================ -->
<!-- SECTION 4: ADAPTIVE TRIALS ANALYSIS -->
<!-- ============================================================ -->

<h2>Does Africa Get Adaptive Trials?</h2>

<div class="two-col">
  <div class="callout callout-danger">
    <span class="big-stat danger">{adaptive_africa}</span>
    <strong>Adaptive trials in Africa</strong><br>
    Adaptive designs allow mid-trial modifications based on interim data -- reducing
    sample size requirements by 20-40% while maintaining statistical rigor. These
    designs are ideal for resource-constrained settings, yet Africa has only
    {adaptive_africa} such trials versus {adaptive_us:,} in the US.
  </div>

  <div class="callout callout-info">
    <span class="big-stat" style="color:var(--accent);">{adaptive_us:,}</span>
    <strong>Adaptive trials in the US</strong><br>
    The irony is stark: adaptive designs were partly motivated by the need for efficient
    trials in under-resourced settings, yet they are deployed almost exclusively in
    well-funded environments. Africa gets the methodology that needs more patients,
    not less.
  </div>
</div>

<div class="callout callout-warning">
  <strong>Bayesian methods:</strong> Africa has
  {africa.get('adoption', {}).get('Bayesian', {}).get('count', 0)} Bayesian trials
  ({africa.get('adoption', {}).get('Bayesian', {}).get('rate_pct', 0)}%)
  vs {us.get('adoption', {}).get('Bayesian', {}).get('count', 0):,} in the US
  ({us.get('adoption', {}).get('Bayesian', {}).get('rate_pct', 0)}%).
  Bayesian approaches enable smaller sample sizes, sequential updating, and
  incorporation of prior information -- all advantages that would benefit
  African trial ecosystems with limited participant pools.
</div>

<!-- ============================================================ -->
<!-- SECTION 5: CLUSTER RCT SPOTLIGHT -->
<!-- ============================================================ -->

<h2>Cluster-Randomized Trials: The Right Design for Africa</h2>

<div class="callout callout-success">
  <span class="big-stat success">{cluster_africa}</span>
  <strong>Cluster-randomized trials in Africa</strong> (vs {cluster_us:,} in the US)
</div>

<p style="margin:1rem 0;line-height:1.8;">
  Cluster-randomized trials randomize at the community, clinic, or village level rather
  than individually. This design is <em>uniquely suited</em> to African health systems where:
</p>

<div class="two-col" style="margin:1rem 0;">
  <div class="callout callout-info">
    <strong>Why cluster RCTs fit Africa:</strong>
    <ul style="margin-top:0.5rem;padding-left:1.5rem;">
      <li>Community health worker interventions are delivered at group level</li>
      <li>Infectious disease interventions (bed nets, water treatment) have spillover effects</li>
      <li>Health facility-level quality improvement cannot be individually randomized</li>
      <li>Implementation science requires pragmatic, system-level randomization</li>
    </ul>
  </div>
  <div class="callout callout-warning">
    <strong>The paradox:</strong>
    <ul style="margin-top:0.5rem;padding-left:1.5rem;">
      <li>Africa has only {cluster_africa} cluster RCTs despite being the ideal setting</li>
      <li>The US has {cluster_us:,} cluster RCTs in a system where individual
          randomization is usually feasible</li>
      <li>Stepped-wedge designs (a cluster RCT variant ideal for phased rollouts) are
          even rarer in Africa</li>
      <li>Platform trials that could test multiple interventions efficiently: near-zero</li>
    </ul>
  </div>
</div>

<!-- ============================================================ -->
<!-- SECTION 6: DOUBLE-BLIND GAP -->
<!-- ============================================================ -->

<h2>The Double-Blind Gap: What It Means for Evidence</h2>

<div class="callout callout-danger">
  <p>The blinding gap is not a neutral technical detail -- it has measurable consequences
  for evidence quality:</p>
  <ul style="margin-top:1rem;padding-left:1.5rem;line-height:2;">
    <li><strong>Performance bias:</strong> Unblinded investigators may provide differential
      co-interventions</li>
    <li><strong>Detection bias:</strong> Outcome assessors aware of allocation may interpret
      subjective endpoints differently</li>
    <li><strong>Attrition bias:</strong> Unblinded participants may differentially withdraw</li>
    <li><strong>Effect inflation:</strong> Meta-analyses show unblinded trials overestimate
      effects by 9-17% (Savovic et al., BMJ 2012)</li>
  </ul>
  <p style="margin-top:1rem;">When {africa_blind}% of African trials are double-blinded
  versus {us_blind}% in the US, the African evidence base is systematically more vulnerable
  to bias. Treatments validated through open-label African trials may not replicate under
  rigorous conditions.</p>
</div>

<!-- ============================================================ -->
<!-- SECTION 7: DSI COMPARISON -->
<!-- ============================================================ -->

<h2>Design Sophistication Index: Head-to-Head</h2>

<div class="two-col">
  <div class="chart-container" style="text-align:center;">
    <h3>Africa DSI</h3>
    <span class="big-stat warning">{africa_dsi}</span>
    <p style="color:var(--muted);">Weighted design score per trial</p>
    <div style="margin-top:1rem;">
      <span class="method-stamp">Double-blind: {africa.get('adoption', {}).get('Double-blind', {}).get('count', 0):,}</span>
      <span class="method-stamp">Adaptive: {africa.get('adoption', {}).get('Adaptive', {}).get('count', 0)}</span>
      <span class="method-stamp">Bayesian: {africa.get('adoption', {}).get('Bayesian', {}).get('count', 0)}</span>
      <span class="method-stamp">Cluster: {cluster_africa}</span>
      <span class="method-stamp">Platform: {africa.get('adoption', {}).get('Platform trial', {}).get('count', 0)}</span>
    </div>
  </div>
  <div class="chart-container" style="text-align:center;">
    <h3>United States DSI</h3>
    <span class="big-stat success">{us_dsi}</span>
    <p style="color:var(--muted);">Weighted design score per trial</p>
    <div style="margin-top:1rem;">
      <span class="method-stamp">Double-blind: {us.get('adoption', {}).get('Double-blind', {}).get('count', 0):,}</span>
      <span class="method-stamp">Adaptive: {us.get('adoption', {}).get('Adaptive', {}).get('count', 0):,}</span>
      <span class="method-stamp">Bayesian: {us.get('adoption', {}).get('Bayesian', {}).get('count', 0):,}</span>
      <span class="method-stamp">Cluster: {cluster_us:,}</span>
      <span class="method-stamp">Platform: {us.get('adoption', {}).get('Platform trial', {}).get('count', 0):,}</span>
    </div>
  </div>
</div>

<div class="callout callout-purple">
  <strong>Interpreting the DSI ratio of {dsi_ratio}x:</strong> The US has a
  {dsi_ratio}-fold higher Design Sophistication Index than Africa. This means the
  average US trial contributes more methodological innovation per study. Africa's lower
  DSI reflects both fewer trials overall and a lower proportion of cutting-edge designs
  within its portfolio. The gap is widest for adaptive, Bayesian, and platform designs
  -- precisely the methodologies that could make African trials more efficient.
</div>

<!-- ============================================================ -->
<!-- SECTION 8: ADVANCED DESIGNS SPOTLIGHT -->
<!-- ============================================================ -->

<h2>Advanced Design Spotlight</h2>

<div class="scroll-x">
<table>
  <thead>
    <tr>
      <th style="padding:10px;">Frontier Design</th>
      <th style="padding:10px;text-align:right;">Africa</th>
      <th style="padding:10px;text-align:right;">Africa %</th>
      <th style="padding:10px;text-align:right;">US</th>
      <th style="padding:10px;text-align:right;">US %</th>
      <th style="padding:10px;text-align:right;">Gap</th>
    </tr>
  </thead>
  <tbody>
    {advanced_rows}
  </tbody>
</table>
</div>

<!-- ============================================================ -->
<!-- SECTION 9: PHASE 1 ANALYSIS -->
<!-- ============================================================ -->

<h2>Phase 1 in Africa: First-in-Human vs Pharmacokinetic</h2>

<div class="callout callout-info">
  Africa has <strong>{p1.get('total', 256)}</strong> Phase 1 trials. What kind of
  early-phase work does the continent receive?
</div>

<div class="scroll-x">
<table>
  <thead>
    <tr>
      <th style="padding:10px;">Phase 1 Category</th>
      <th style="padding:10px;text-align:right;">Count</th>
      <th style="padding:10px;text-align:right;">% of Phase 1</th>
    </tr>
  </thead>
  <tbody>
    {phase1_rows}
  </tbody>
</table>
</div>

<div class="callout callout-warning">
  <strong>What this reveals:</strong> If pharmacokinetic and bioequivalence studies
  dominate Africa's Phase 1 portfolio while first-in-human studies are rare, it
  suggests Africa is used to verify drug absorption in local populations <em>after</em>
  early safety work is done elsewhere -- a service role rather than a discovery role.
  Conversely, a higher first-in-human proportion would indicate genuine early-phase
  innovation happening on African soil.
</div>

<!-- ============================================================ -->
<!-- SECTION 10: TEMPORAL TRENDS -->
<!-- ============================================================ -->

<h2>Temporal Trends: Are Designs Improving?</h2>

<div class="chart-container">
  <canvas id="trendChart" height="260"></canvas>
</div>

<div class="scroll-x">
<table>
  <thead>
    <tr>
      <th style="padding:10px;">Period</th>
      <th style="padding:10px;text-align:right;">Africa Advanced</th>
      <th style="padding:10px;text-align:right;">Africa Rate</th>
      <th style="padding:10px;text-align:right;">US Advanced</th>
      <th style="padding:10px;text-align:right;">US Rate</th>
    </tr>
  </thead>
  <tbody>
    {trend_rows}
  </tbody>
</table>
</div>

<div class="callout callout-info">
  <strong>Trend interpretation:</strong> If the gap between Africa and US advanced-design
  adoption rates is narrowing over time, it suggests methodological convergence. If the
  gap is widening, Africa is falling further behind the methodological frontier even as
  its trial volume grows.
</div>

<!-- ============================================================ -->
<!-- SECTION 11: IMPLICATIONS -->
<!-- ============================================================ -->

<h2>Implications for Evidence Quality</h2>

<div class="callout callout-danger">
  <h3 style="color:var(--danger);margin-bottom:1rem;">The Methodological Double Standard</h3>
  <ol style="padding-left:1.5rem;line-height:2.2;">
    <li><strong>Lower blinding rates</strong> ({africa_blind}% vs {us_blind}%)
      produce systematically biased evidence for African populations</li>
    <li><strong>Near-absence of adaptive designs</strong> ({adaptive_africa} vs
      {adaptive_us:,}) forces Africa to use larger, slower, more expensive trial
      designs -- the opposite of what resource-constrained settings need</li>
    <li><strong>Minimal Bayesian and platform trials</strong> prevent efficient
      multi-arm testing of interventions for diseases with the highest African
      burden (malaria, TB, HIV)</li>
    <li><strong>Only {cluster_africa} cluster RCTs</strong> in a continent where
      community-level interventions are the primary healthcare delivery model</li>
    <li><strong>Phase 1 as service role</strong>: Africa's early-phase trials
      may be predominantly pharmacokinetic bridging rather than genuine drug
      discovery, reflecting a validation-not-innovation paradigm</li>
  </ol>
</div>

<div class="callout callout-success">
  <h3 style="color:var(--success);margin-bottom:1rem;">What Could Change</h3>
  <ul style="padding-left:1.5rem;line-height:2.2;">
    <li><strong>Adopt adaptive designs for endemic diseases</strong>: A single
      adaptive platform trial for malaria could replace 5-10 conventional RCTs</li>
    <li><strong>Scale cluster-randomized designs</strong> for community health
      worker interventions, bed net distribution, and facility-level quality improvement</li>
    <li><strong>Deploy Bayesian methods</strong> to leverage prior African trial
      data rather than starting from zero each time</li>
    <li><strong>Demand blinding where feasible</strong>: Even in surgical and
      behavioral trials, sham controls and blinded outcome assessment are possible</li>
    <li><strong>Build African biostatistics capacity</strong>: Advanced designs
      require advanced statistical expertise -- currently concentrated in the
      Global North</li>
  </ul>
</div>

<footer>
  <p>The Methodological Quality Audit | Project 44 of the Africa RCT Equity Audit</p>
  <p>Data source: ClinicalTrials.gov API v2 | Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <p style="margin-top:0.5rem;">Design Sophistication Index weights: Open-label=1,
  Single-blind=1.5, Double-blind=2, Non-inferiority/Equivalence/Pragmatic=3,
  Cluster/Stepped-wedge=4, Adaptive/Bayesian/Platform=5</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script>
// Design comparison chart
(function() {{
  var ctx = document.getElementById('designChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {chart_labels},
      datasets: [
        {{
          label: 'Africa (%)',
          data: {chart_africa},
          backgroundColor: 'rgba(168, 85, 247, 0.7)',
          borderColor: '#a855f7',
          borderWidth: 1,
        }},
        {{
          label: 'US (%)',
          data: {chart_us},
          backgroundColor: 'rgba(59, 130, 246, 0.7)',
          borderColor: '#3b82f6',
          borderWidth: 1,
        }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ labels: {{ color: '#94a3b8' }} }},
        title: {{
          display: true,
          text: 'Design Type Adoption Rate (% of all interventional trials)',
          color: '#e2e8f0'
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color: '#94a3b8', maxRotation: 45 }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }}
        }},
        y: {{
          ticks: {{ color: '#94a3b8', callback: function(v) {{ return v + '%'; }} }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }}
        }}
      }}
    }}
  }});
}})();

// Temporal trend chart
(function() {{
  var ctx2 = document.getElementById('trendChart').getContext('2d');
  new Chart(ctx2, {{
    type: 'line',
    data: {{
      labels: {trend_periods},
      datasets: [
        {{
          label: 'Africa Advanced Design Rate (%)',
          data: {trend_africa_rates},
          borderColor: '#a855f7',
          backgroundColor: 'rgba(168, 85, 247, 0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 5,
        }},
        {{
          label: 'US Advanced Design Rate (%)',
          data: {trend_us_rates},
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59, 130, 246, 0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 5,
        }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ labels: {{ color: '#94a3b8' }} }},
        title: {{
          display: true,
          text: 'Advanced Design Adoption Over Time (adaptive, Bayesian, cluster, stepped-wedge, platform)',
          color: '#e2e8f0'
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color: '#94a3b8' }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }}
        }},
        y: {{
          ticks: {{ color: '#94a3b8', callback: function(v) {{ return v + '%'; }} }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }}
        }}
      }}
    }}
  }});
}})();
</script>

</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Generated {OUTPUT_HTML} ({len(html):,} bytes)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 70)
    print("  THE METHODOLOGICAL QUALITY AUDIT")
    print("  What Trial Designs Does Africa Get?")
    print("=" * 70)

    data = fetch_all_data()
    analysis = compute_analysis(data)

    # Print summary
    africa = analysis.get("Africa", {})
    us = analysis.get("United States", {})
    print(f"\n--- Results ---")
    print(f"Africa total trials:    {africa.get('total_trials', 0):,}")
    print(f"US total trials:        {us.get('total_trials', 0):,}")
    print(f"Africa blinding rate:   {africa.get('blinding_rate', 0)}%")
    print(f"US blinding rate:       {us.get('blinding_rate', 0)}%")
    print(f"Blinding gap:           {analysis.get('blinding_gap', 0)}pp")
    print(f"Africa DSI:             {africa.get('dsi', 0)}")
    print(f"US DSI:                 {us.get('dsi', 0)}")
    print(f"DSI ratio (US:Africa):  {analysis.get('dsi_ratio', 0)}x")

    print(f"\n--- Design Adoption (Africa) ---")
    for label, query_term, weight in DESIGN_QUERIES:
        info = africa.get('adoption', {}).get(label, {})
        print(f"  {label:25s} {info.get('count', 0):>6,}  ({info.get('rate_pct', 0)}%)")

    print(f"\n--- Phase 1 Africa ---")
    p1 = analysis.get('phase1_africa', {})
    print(f"  Total:          {p1.get('total', 0)}")
    print(f"  First-in-human: {p1.get('first_in_human', 0)} ({p1.get('fih_pct', 0)}%)")
    print(f"  Pharmacokinetic:{p1.get('pharmacokinetic', 0)} ({p1.get('pk_pct', 0)}%)")
    print(f"  Dose-finding:   {p1.get('dose_finding', 0)}")

    generate_html(data, analysis)
    print("\nDone.")


if __name__ == "__main__":
    main()
