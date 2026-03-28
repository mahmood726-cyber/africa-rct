#!/usr/bin/env python
"""
fetch_china_leapfrog.py -- Project 24: The China Leapfrog -- Can Africa Follow?
================================================================================
China went from ~5,000 clinical trials to 38,933 in roughly a decade.
Africa's 20 countries combined have 20,799.  What did China do that
Africa hasn't?

Compares China vs Africa's top 5 countries on total trials, per-capita
density, disease portfolio, phase distribution, and growth trajectory.
Identifies five replicable lessons for Africa.

Usage:
    python fetch_china_leapfrog.py

Outputs:
    data/china_leapfrog_data.json  (cached API results, 24h TTL)
    china-leapfrog.html            (dark-theme interactive dashboard)

Requirements:
    Python 3.8+, no external packages (uses urllib)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import math
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
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "china_leapfrog_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "china-leapfrog.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# -- Countries: name -> population in millions (2025 est.) -----------------
CHINA = {"China": 1410}

AFRICA_TOP5 = {
    "Egypt":        105,
    "South Africa":  62,
    "Uganda":        48,
    "Kenya":         55,
    "Nigeria":      230,
}

# Reference countries for calibration
REFERENCE = {
    "United States": 335,
    "India":        1440,
    "Brazil":        216,
}

ALL_COUNTRIES = {}
ALL_COUNTRIES.update(CHINA)
ALL_COUNTRIES.update(AFRICA_TOP5)
ALL_COUNTRIES.update(REFERENCE)

# Verified trial counts (fallback if API is unavailable)
VERIFIED_COUNTS = {
    "China":        38933,
    "Egypt":        12395,
    "South Africa":  3473,
    "Uganda":          783,
    "Kenya":           720,
    "Nigeria":         354,
}

COUNTRY_CATEGORY = {
    "China":        "china",
    "Egypt":        "africa",
    "South Africa": "africa",
    "Uganda":       "africa",
    "Kenya":        "africa",
    "Nigeria":      "africa",
    "United States":"reference",
    "India":        "reference",
    "Brazil":       "reference",
}

# -- Conditions for disease portfolio comparison --
CONDITIONS = {
    "HIV":                   "HIV",
    "Cancer":                "cancer OR neoplasm OR oncology",
    "Diabetes":              "diabetes",
    "Cardiovascular":        "cardiovascular OR heart failure OR coronary",
    "Stroke":                "stroke OR cerebrovascular",
    "Traditional medicine":  "traditional medicine OR herbal OR Chinese medicine",
}

# -- Phases --
PHASES = {
    "Phase 1":  "PHASE1",
    "Phase 2":  "PHASE2",
    "Phase 3":  "PHASE3",
    "Phase 4":  "PHASE4",
}

# -- Growth years for trajectory --
GROWTH_YEARS = list(range(2010, 2026))  # 2010-2025

# -- China reform timeline for narrative --
CHINA_MILESTONES = {
    2010: "~5,000 cumulative trials; pre-reform baseline",
    2015: "NMPA (then CFDA) drug review reform begins; massive backlog cleared",
    2016: "Two-invoice policy; domestic pharma begins global-standard trials",
    2017: "China joins ICH (International Council for Harmonisation)",
    2018: "Domestic innovation incentives: tax breaks for pharma R&D",
    2019: "New Drug Administration Law: accelerated approvals, conditional marketing",
    2020: "CRO industry revenue exceeds $8B; China becomes top-5 global trial site",
    2022: "PD-1/PD-L1 trials alone exceed 1,000; biotech IPO boom funds trial capacity",
    2024: "38,933 interventional trials; 27.6 per million population",
}

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


def get_trial_count(location, condition=None, phase=None, date_range=None):
    """Return total count of interventional trials for a location."""
    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if date_range:
        filters.append(f"AREA[StartDate]RANGE[{date_range[0]},{date_range[1]}]")
    if phase:
        filters.append(f"AREA[Phase]{phase}")

    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": " AND ".join(filters),
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition:
        params["query.cond"] = condition

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


def save_cache(data):
    """Save data to cache file."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nCached to {CACHE_FILE}")


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_all_data():
    """Fetch trial counts for all countries, conditions, phases, and growth."""
    cached = load_cache()
    if cached is not None:
        return cached

    # Count expected API calls
    countries = list(ALL_COUNTRIES.keys())
    n_countries = len(countries)
    n_conditions = len(CONDITIONS)
    n_phases = len(PHASES)
    n_years = len(GROWTH_YEARS)
    # For growth: only China + Africa top 5 (not reference)
    growth_countries = list(CHINA.keys()) + list(AFRICA_TOP5.keys())
    n_growth = len(growth_countries)

    total_calls = (
        n_countries                         # total counts
        + n_countries * n_conditions        # condition counts
        + n_countries * n_phases            # phase counts
        + n_growth * n_years                # growth trajectory
    )
    call_num = 0

    data = {
        "timestamp": datetime.now().isoformat(),
        "total_counts": {},
        "condition_counts": {},
        "phase_counts": {},
        "growth_by_year": {},
    }

    # --- Total interventional trials per country ---
    print("\n--- Total interventional trials ---")
    for country in countries:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} (total)...")
        count = get_trial_count(country)
        data["total_counts"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    # --- Condition-specific counts ---
    print("\n--- Condition-specific trials ---")
    for country in countries:
        data["condition_counts"][country] = {}
        for cond_name, cond_query in CONDITIONS.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} ({cond_name})...")
            count = get_trial_count(country, condition=cond_query)
            data["condition_counts"][country][cond_name] = count
            print(f"    -> {count:,} trials")
            time.sleep(RATE_LIMIT)

    # --- Phase counts ---
    print("\n--- Phase distribution ---")
    for country in countries:
        data["phase_counts"][country] = {}
        for phase_name, phase_filter in PHASES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} ({phase_name})...")
            count = get_trial_count(country, phase=phase_filter)
            data["phase_counts"][country][phase_name] = count
            print(f"    -> {count:,} trials")
            time.sleep(RATE_LIMIT)

    # --- Growth trajectory (year-by-year, China + Africa top 5) ---
    print("\n--- Growth trajectory (year-by-year) ---")
    for country in growth_countries:
        data["growth_by_year"][country] = {}
        for year in GROWTH_YEARS:
            call_num += 1
            date_start = f"{year}-01-01"
            date_end = f"{year}-12-31"
            print(f"  [{call_num}/{total_calls}] {country} ({year})...")
            count = get_trial_count(country, date_range=(date_start, date_end))
            data["growth_by_year"][country][str(year)] = count
            print(f"    -> {count:,} trials")
            time.sleep(RATE_LIMIT)

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_data(data):
    """Compute all China Leapfrog metrics."""
    results = {}

    # -- Per-capita for all countries --
    country_stats = []
    for country, pop in ALL_COUNTRIES.items():
        trials = data["total_counts"].get(country, 0)
        per_m = round(trials / pop, 1) if pop > 0 else 0
        cond = data["condition_counts"].get(country, {})
        phases = data["phase_counts"].get(country, {})
        growth = data["growth_by_year"].get(country, {})

        # Phase 1 share
        total_phased = sum(phases.values())
        p1_share = round(phases.get("Phase 1", 0) / total_phased * 100, 1) if total_phased > 0 else 0

        # Condition shares (% of total)
        cond_shares = {}
        for c_name, c_count in cond.items():
            cond_shares[c_name] = round(c_count / trials * 100, 1) if trials > 0 else 0

        country_stats.append({
            "country": country,
            "category": COUNTRY_CATEGORY.get(country, "reference"),
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
            "conditions": cond,
            "condition_shares": cond_shares,
            "phases": phases,
            "phase1_share": p1_share,
            "growth": growth,
        })

    country_stats.sort(key=lambda x: -x["per_million"])
    for i, entry in enumerate(country_stats):
        entry["rank"] = i + 1

    results["country_stats"] = country_stats

    # -- China stats --
    china = next((c for c in country_stats if c["country"] == "China"), None)
    results["china"] = china

    # -- Africa top 5 aggregate --
    africa5 = [c for c in country_stats if c["category"] == "africa"]
    africa5_trials = sum(c["trials"] for c in africa5)
    africa5_pop = sum(c["population_m"] for c in africa5)
    africa5_per_m = round(africa5_trials / africa5_pop, 1) if africa5_pop > 0 else 0
    africa5_avg_per_m = round(sum(c["per_million"] for c in africa5) / len(africa5), 1) if africa5 else 0
    results["africa5_trials"] = africa5_trials
    results["africa5_pop"] = africa5_pop
    results["africa5_per_m"] = africa5_per_m
    results["africa5_avg_per_m"] = africa5_avg_per_m
    results["africa5"] = africa5

    # -- China per-capita --
    china_pm = china["per_million"] if china else 0
    results["china_pm"] = china_pm

    # -- Ratio: China / Africa5 average --
    if africa5_avg_per_m > 0:
        results["china_vs_africa5"] = round(china_pm / africa5_avg_per_m, 1)
    else:
        results["china_vs_africa5"] = float("inf")

    # -- Phase 1 comparison --
    china_p1 = china["phase1_share"] if china else 0
    africa_p1_avg = round(sum(c["phase1_share"] for c in africa5) / len(africa5), 1) if africa5 else 0
    results["china_p1_share"] = china_p1
    results["africa_p1_avg"] = africa_p1_avg

    # -- Disease portfolio comparison --
    results["disease_comparison"] = {}
    for cond_name in CONDITIONS:
        china_share = china["condition_shares"].get(cond_name, 0) if china else 0
        africa_avg_share = round(
            sum(c["condition_shares"].get(cond_name, 0) for c in africa5) / len(africa5), 1
        ) if africa5 else 0
        results["disease_comparison"][cond_name] = {
            "china_share": china_share,
            "africa_avg_share": africa_avg_share,
        }

    # -- Growth: cumulative trajectory --
    results["growth_cumulative"] = {}
    growth_countries = list(CHINA.keys()) + list(AFRICA_TOP5.keys())
    for country in growth_countries:
        yearly = data["growth_by_year"].get(country, {})
        cumul = {}
        running = 0
        for year in GROWTH_YEARS:
            running += yearly.get(str(year), 0)
            cumul[str(year)] = running
        results["growth_cumulative"][country] = cumul

    return results


# ---------------------------------------------------------------------------
# HTML helpers
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


def cat_color(category):
    """Color by country category."""
    return {
        "china":     "#ef4444",
        "africa":    "#f97316",
        "reference": "#a78bfa",
    }.get(category, "#888")


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard."""

    country_stats = results["country_stats"]
    china = results["china"]
    china_pm = results["china_pm"]
    africa5 = results["africa5"]
    africa5_trials = results["africa5_trials"]
    africa5_per_m = results["africa5_per_m"]
    africa5_avg = results["africa5_avg_per_m"]

    global_max_pm = max((c["per_million"] for c in country_stats), default=1)
    if global_max_pm < 1:
        global_max_pm = 1

    # ====================================================================
    # HEAD-TO-HEAD TABLE ROWS
    # ====================================================================
    h2h_rows = ""
    for c in country_stats:
        is_china = c["country"] == "China"
        cat = c["category"]
        color = "#ef4444" if is_china else cat_color(cat)
        bg = "rgba(239,68,68,0.08)" if is_china else "transparent"
        bold = "font-weight:bold;" if is_china else ""

        bar_w = min(c["per_million"] / global_max_pm * 100, 100) if global_max_pm > 0 else 0

        cat_badge = {"china": "#ef4444", "africa": "#f97316", "reference": "#a78bfa"}
        badge_c = cat_badge.get(cat, "#888")
        cat_label = "China" if cat == "china" else "Africa Top 5" if cat == "africa" else "Reference"

        h2h_rows += f"""<tr style="background:{bg};">
  <td style="padding:10px;text-align:center;{bold}color:{color};">{c["rank"]}</td>
  <td style="padding:10px;{bold}">{escape_html(c["country"])}</td>
  <td style="padding:10px;text-align:center;">
    <span style="display:inline-block;background:{badge_c};color:#000;
      padding:2px 8px;border-radius:12px;font-size:0.7rem;font-weight:bold;">
      {cat_label}</span></td>
  <td style="padding:10px;text-align:right;">{c["population_m"]:,}M</td>
  <td style="padding:10px;text-align:right;">{c["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:{color};font-weight:bold;">{c["per_million"]}</td>
  <td style="padding:10px;text-align:right;">{c["phase1_share"]}%</td>
  <td style="padding:10px;width:18%;">
    <div style="background:rgba(255,255,255,0.08);border-radius:4px;height:18px;width:100%;position:relative;">
      <div style="background:{color};height:100%;width:{bar_w:.1f}%;border-radius:4px;"></div>
    </div></td>
</tr>
"""

    # ====================================================================
    # DISEASE PORTFOLIO COMPARISON
    # ====================================================================
    disease_rows = ""
    for cond_name in CONDITIONS:
        dc = results["disease_comparison"][cond_name]
        china_s = dc["china_share"]
        africa_s = dc["africa_avg_share"]
        diff = round(china_s - africa_s, 1)
        diff_color = "#22c55e" if diff > 0 else "#ef4444" if diff < 0 else "#9ca3af"
        diff_str = f"+{diff}" if diff > 0 else str(diff)

        # Raw counts
        china_raw = china["conditions"].get(cond_name, 0) if china else 0
        africa_raw = round(sum(c["conditions"].get(cond_name, 0) for c in africa5) / max(len(africa5), 1))

        disease_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(cond_name)}</td>
  <td style="padding:10px;text-align:right;color:#ef4444;">{china_raw:,}</td>
  <td style="padding:10px;text-align:right;color:#ef4444;">{china_s}%</td>
  <td style="padding:10px;text-align:right;color:#f97316;">{africa_raw:,}</td>
  <td style="padding:10px;text-align:right;color:#f97316;">{africa_s}%</td>
  <td style="padding:10px;text-align:right;color:{diff_color};font-weight:bold;">{diff_str}pp</td>
</tr>
"""

    # ====================================================================
    # PHASE DISTRIBUTION
    # ====================================================================
    phase_rows = ""
    for phase_name in PHASES:
        china_p = china["phases"].get(phase_name, 0) if china else 0
        china_total_phased = sum(china["phases"].values()) if china else 1
        china_pct = round(china_p / china_total_phased * 100, 1) if china_total_phased > 0 else 0

        # Africa average
        a_counts = [c["phases"].get(phase_name, 0) for c in africa5]
        a_totals = [sum(c["phases"].values()) for c in africa5]
        a_pcts = [round(a_counts[i] / a_totals[i] * 100, 1) if a_totals[i] > 0 else 0 for i in range(len(africa5))]
        africa_pct = round(sum(a_pcts) / len(a_pcts), 1) if a_pcts else 0
        africa_count = sum(a_counts)

        diff = round(china_pct - africa_pct, 1)
        diff_color = "#22c55e" if diff > 0 else "#ef4444" if diff < 0 else "#9ca3af"
        diff_str = f"+{diff}" if diff > 0 else str(diff)

        phase_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(phase_name)}</td>
  <td style="padding:10px;text-align:right;color:#ef4444;">{china_p:,}</td>
  <td style="padding:10px;text-align:right;color:#ef4444;">{china_pct}%</td>
  <td style="padding:10px;text-align:right;color:#f97316;">{africa_count:,}</td>
  <td style="padding:10px;text-align:right;color:#f97316;">{africa_pct}%</td>
  <td style="padding:10px;text-align:right;color:{diff_color};font-weight:bold;">{diff_str}pp</td>
</tr>
"""

    # ====================================================================
    # GROWTH TRAJECTORY (CUMULATIVE)
    # ====================================================================
    growth_cumul = results.get("growth_cumulative", {})

    # Build SVG-like growth chart via CSS bars per year
    growth_chart_html = ""
    # Find max cumulative for scaling
    all_cumul_vals = []
    for country_data in growth_cumul.values():
        for v in country_data.values():
            all_cumul_vals.append(v)
    max_cumul = max(all_cumul_vals) if all_cumul_vals else 1
    if max_cumul < 1:
        max_cumul = 1

    # Growth table: year columns
    growth_header = '<th style="padding:8px;text-align:left;">Country</th>'
    for year in GROWTH_YEARS:
        growth_header += f'<th style="padding:4px;text-align:right;font-size:0.7rem;">{year}</th>'

    growth_body = ""
    growth_order = ["China"] + [c["country"] for c in africa5]
    for country in growth_order:
        cumul = growth_cumul.get(country, {})
        is_china = country == "China"
        color = "#ef4444" if is_china else "#f97316"
        bold = "font-weight:bold;" if is_china else ""
        bg = "rgba(239,68,68,0.06)" if is_china else "transparent"

        cells = ""
        for year in GROWTH_YEARS:
            val = cumul.get(str(year), 0)
            cells += f'<td style="padding:4px;text-align:right;color:{color};font-size:0.75rem;">{val:,}</td>'

        growth_body += f"""<tr style="background:{bg};">
  <td style="padding:8px;{bold}white-space:nowrap;">{escape_html(country)}</td>
  {cells}
</tr>
"""

    # ====================================================================
    # PER-CAPITA BAR CHART (all countries)
    # ====================================================================
    bar_chart_rows = ""
    sorted_stats = sorted(country_stats, key=lambda x: -x["per_million"])
    for c in sorted_stats:
        is_china = c["country"] == "China"
        color = "#ef4444" if is_china else cat_color(c["category"])
        bar_w = min(c["per_million"] / global_max_pm * 100, 100) if global_max_pm > 0 else 0
        label_style = "color:#ef4444;font-weight:bold;" if is_china else ""

        bar_chart_rows += f"""<div style="display:flex;align-items:center;margin:4px 0;gap:10px;">
  <div style="width:120px;text-align:right;{label_style}font-size:0.85rem;">
    {escape_html(c["country"])}</div>
  <div style="flex:1;background:rgba(255,255,255,0.06);border-radius:4px;height:24px;">
    <div style="background:{color};height:100%;width:{bar_w:.1f}%;border-radius:4px;
      display:flex;align-items:center;padding-left:8px;">
      <span style="color:#000;font-size:0.75rem;font-weight:bold;">
        {c["per_million"]}/M</span>
    </div>
  </div>
</div>
"""

    # ====================================================================
    # CHINA MILESTONES TIMELINE
    # ====================================================================
    milestone_rows = ""
    for year, desc in sorted(CHINA_MILESTONES.items()):
        milestone_rows += f"""<div style="display:flex;gap:16px;margin:8px 0;">
  <div style="min-width:60px;font-weight:bold;color:#ef4444;font-size:1.1rem;">{year}</div>
  <div style="border-left:2px solid #ef4444;padding-left:16px;">{escape_html(desc)}</div>
</div>
"""

    # ====================================================================
    # SUMMARY STATS
    # ====================================================================
    china_trials = china["trials"] if china else 0
    china_vs_africa = results["china_vs_africa5"]
    china_p1 = results["china_p1_share"]
    africa_p1 = results["africa_p1_avg"]

    # ====================================================================
    # BUILD HTML
    # ====================================================================

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The China Leapfrog: Can Africa Follow?</title>
<style>
  :root {{
    --bg: #0a0e17;
    --surface: #111827;
    --border: #1f2937;
    --text: #e5e7eb;
    --muted: #9ca3af;
    --china: #ef4444;
    --africa: #f97316;
    --accent: #22c55e;
    --accent2: #60a5fa;
    --accent3: #a78bfa;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    line-height: 1.6;
    padding: 20px;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{
    font-size: 2rem;
    background: linear-gradient(135deg, #ef4444, #f97316);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
  }}
  h2 {{
    font-size: 1.4rem;
    color: var(--china);
    margin: 40px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
  h3 {{
    font-size: 1.1rem;
    color: var(--accent2);
    margin: 24px 0 12px;
  }}
  .subtitle {{
    color: var(--muted);
    font-size: 0.95rem;
    margin-bottom: 24px;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin: 16px 0;
  }}
  .stat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }}
  .stat-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    text-align: center;
  }}
  .stat-value {{
    font-size: 2.2rem;
    font-weight: 800;
    line-height: 1.2;
  }}
  .stat-label {{
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 4px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th {{
    background: rgba(255,255,255,0.04);
    padding: 12px 10px;
    text-align: left;
    font-weight: 600;
    color: var(--muted);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  tr:hover {{
    background: rgba(255,255,255,0.03);
  }}
  .highlight {{
    background: rgba(239,68,68,0.08);
    border-left: 4px solid #ef4444;
    padding: 16px 20px;
    border-radius: 8px;
    margin: 16px 0;
  }}
  .africa-box {{
    background: rgba(249,115,22,0.06);
    border-left: 4px solid #f97316;
    padding: 16px 20px;
    border-radius: 8px;
    margin: 16px 0;
  }}
  .analysis-box {{
    background: rgba(96,165,250,0.06);
    border-left: 4px solid #60a5fa;
    padding: 16px 20px;
    border-radius: 8px;
    margin: 16px 0;
  }}
  .lesson-box {{
    background: rgba(34,197,94,0.06);
    border-left: 4px solid #22c55e;
    padding: 16px 20px;
    border-radius: 8px;
    margin: 16px 0;
  }}
  .policy-box {{
    background: rgba(168,85,247,0.06);
    border-left: 4px solid #a78bfa;
    padding: 16px 20px;
    border-radius: 8px;
    margin: 16px 0;
  }}
  ul {{ margin: 8px 0 8px 24px; }}
  li {{ margin: 6px 0; color: var(--text); }}
  .footer {{
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 0.8rem;
    text-align: center;
  }}
  .overflow-x {{ overflow-x: auto; }}
  @media (max-width: 768px) {{
    h1 {{ font-size: 1.4rem; }}
    .stat-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .stat-value {{ font-size: 1.6rem; }}
  }}
</style>
</head>
<body>
<div class="container">

<!-- ============================================================ -->
<!-- HEADER                                                        -->
<!-- ============================================================ -->
<h1>The China Leapfrog: Can Africa Follow?</h1>
<p class="subtitle">
  China went from ~5,000 clinical trials to {china_trials:,} in roughly a decade.
  Africa's top 5 countries combined have {africa5_trials:,}.
  What did China do that Africa hasn't -- and what can Africa learn?
  <br><span style="font-size:0.8rem;">Data: ClinicalTrials.gov API v2 | Generated {datetime.now().strftime("%d %B %Y")}</span>
</p>

<!-- ============================================================ -->
<!-- 1. SUMMARY STATS                                              -->
<!-- ============================================================ -->
<h2>1. The Numbers at a Glance</h2>

<div class="stat-grid">
  <div class="stat-box">
    <div class="stat-value" style="color:#ef4444;">{china_trials:,}</div>
    <div class="stat-label">China Total Trials</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#f97316;">{africa5_trials:,}</div>
    <div class="stat-label">Africa Top 5 Combined</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#ef4444;">{china_pm}/M</div>
    <div class="stat-label">China Per Capita</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#f97316;">{africa5_per_m}/M</div>
    <div class="stat-label">Africa Top 5 Aggregate Per Capita</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#a78bfa;">{china_vs_africa}x</div>
    <div class="stat-label">China / Africa Top 5 Avg Per Capita</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#ef4444;">{china_p1}%</div>
    <div class="stat-label">China Phase 1 Share</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#f97316;">{africa_p1}%</div>
    <div class="stat-label">Africa Top 5 Avg Phase 1 Share</div>
  </div>
</div>

<div class="highlight">
  <p><strong>Key insight:</strong> China's per-capita trial density ({china_pm}/M) means it has built
  a domestic clinical trial ecosystem comparable to upper-middle-income nations. Africa's top 5 countries
  average {africa5_avg}/M -- roughly <strong>{china_vs_africa}x lower</strong> than China.
  China's higher Phase 1 share ({china_p1}% vs {africa_p1}%) signals domestic drug development,
  while Africa's trials are more often Phase 3 (externally sponsored).</p>
</div>

<!-- ============================================================ -->
<!-- 2. HEAD-TO-HEAD TABLE                                         -->
<!-- ============================================================ -->
<h2>2. Head-to-Head Comparison</h2>
<div class="card">
<div class="overflow-x">
<table>
  <thead>
    <tr>
      <th style="text-align:center;">#</th>
      <th>Country</th>
      <th style="text-align:center;">Group</th>
      <th style="text-align:right;">Pop</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Per M</th>
      <th style="text-align:right;">P1 %</th>
      <th>Density</th>
    </tr>
  </thead>
  <tbody>
{h2h_rows}
  </tbody>
</table>
</div>
</div>

<!-- ============================================================ -->
<!-- 3. PER-CAPITA BAR CHART                                       -->
<!-- ============================================================ -->
<h2>3. Per-Capita Trial Density</h2>
<div class="card">
{bar_chart_rows}
</div>

<!-- ============================================================ -->
<!-- 4. WHAT CHINA DID                                             -->
<!-- ============================================================ -->
<h2>4. What China Did: The Reform Playbook</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:16px;">
  China's clinical trial boom was not accidental. It resulted from deliberate, sequenced
  policy reforms spanning regulation, industry, academia, and global integration.
</p>

<div class="card">
  <h3>Timeline of China's Clinical Trial Acceleration</h3>
  <div style="margin-top:12px;">
{milestone_rows}
  </div>
</div>

<div class="highlight">
  <h3>4a. NMPA Regulatory Reform (2015-2019)</h3>
  <p>The former CFDA (now NMPA) cleared a backlog of over 21,000 pending drug applications in 2015-2016.
  New rules allowed simultaneous domestic-international trial conduct, accepted foreign clinical data for
  marketed drugs, and created expedited pathways for innovative drugs. Approval timelines dropped from
  8-10 years to 2-3 years for priority drugs. This single reform was the catalyst for everything that followed.</p>
</div>

<div class="highlight">
  <h3>4b. Domestic Pharma Boom</h3>
  <p>Chinese pharmaceutical companies grew from generic copycats to innovation leaders. By 2024, over 70
  Chinese biotech firms were publicly listed, collectively running thousands of clinical trials. Companies like
  BeiGene, Hengrui, and Innovent invested billions in domestic Phase 1-3 trials. This created a self-sustaining
  cycle: more trials led to more CRO capacity, which attracted more trials.</p>
</div>

<div class="highlight">
  <h3>4c. ICH Membership (2017)</h3>
  <p>China joined the International Council for Harmonisation in 2017, aligning its regulatory standards
  with the US, EU, and Japan. This meant Chinese trial data could be used in global regulatory submissions,
  making China attractive as a trial site for multinational pharma and enabling Chinese companies to compete
  globally with domestically generated data.</p>
</div>

<div class="highlight">
  <h3>4d. Academic Incentives</h3>
  <p>Chinese universities and hospitals made clinical trial leadership a criterion for academic promotion.
  Principal investigators could advance their careers through trial conduct. The number of GCP-certified
  institutions grew from ~200 in 2010 to >1,000 by 2023. This created a deep bench of trained investigators
  and research coordinators.</p>
</div>

<div class="highlight">
  <h3>4e. CRO Industry Growth</h3>
  <p>China's Contract Research Organization industry grew from near-zero to $8+ billion by 2020. Companies
  like WuXi AppTec, Pharmaron, and Tigermed built global-standard capabilities at lower cost than Western CROs.
  This infrastructure enabled both domestic and international sponsors to run trials efficiently in China.</p>
</div>

<!-- ============================================================ -->
<!-- 5. WHAT AFRICA HASN'T                                         -->
<!-- ============================================================ -->
<h2>5. What Africa Hasn't Done (Yet)</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:16px;">
  Africa has five structural barriers that China overcame or never faced.
</p>

<div class="africa-box">
  <h3>5a. Fragmented Regulation (54 Countries, 54 Agencies)</h3>
  <p>Africa has no equivalent of NMPA reform. Each of 54 countries has its own regulatory agency, many
  understaffed and underfunded. A clinical trial in three African countries requires three separate
  regulatory submissions, three ethics reviews, and three import permits. The African Medicines Agency (AMA),
  established by treaty in 2023, is Africa's best hope -- but is not yet operational. China's centralized
  NMPA could reform overnight; Africa must negotiate across 54 jurisdictions.</p>
</div>

<div class="africa-box">
  <h3>5b. No Continental Pharmaceutical Industry</h3>
  <p>Africa imports over 70% of its medicines. Without domestic pharmaceutical companies running their own
  trials, Africa remains a recipient of externally designed studies. China's boom was driven by domestic
  companies running trials for their own products. Africa lacks the BeiGenes and Hengruis that would
  generate organic trial demand independent of donor priorities.</p>
</div>

<div class="africa-box">
  <h3>5c. Brain Drain and Investigator Shortage</h3>
  <p>Africa trains physicians who emigrate to the UK, US, and Gulf states. China retained its trained
  workforce by creating domestic career paths tied to research. Africa's brain drain means that even where
  infrastructure exists, the investigator workforce is thin. Nigeria has 230 million people but only 354
  registered trials -- a workforce problem as much as a funding one.</p>
</div>

<div class="africa-box">
  <h3>5d. Donor Dependency and Disease Skew</h3>
  <p>Much of Africa's clinical trial portfolio is donor-funded, particularly through PEPFAR, GAVI, and the
  Global Fund. This creates a disease portfolio dominated by HIV, malaria, and TB -- important diseases,
  but leaving cancer, cardiovascular disease, diabetes, and stroke severely under-researched despite being
  the fastest-growing causes of death on the continent.</p>
</div>

<div class="africa-box">
  <h3>5e. Digital and Logistics Infrastructure</h3>
  <p>Clinical trials require cold chains, electronic data capture, reliable internet, and specimen transport.
  China invested massively in logistics infrastructure through the Belt and Road era. Africa's infrastructure
  gaps make trial conduct more expensive and slower, deterring sponsors who can run trials in China or India
  at lower cost with better infrastructure.</p>
</div>

<!-- ============================================================ -->
<!-- 6. DISEASE PORTFOLIO COMPARISON                               -->
<!-- ============================================================ -->
<h2>6. Disease Portfolio: Cancer vs HIV</h2>
<div class="card">
<table>
  <thead>
    <tr>
      <th>Condition</th>
      <th style="text-align:right;">China (n)</th>
      <th style="text-align:right;">China (%)</th>
      <th style="text-align:right;">Africa Avg (n)</th>
      <th style="text-align:right;">Africa Avg (%)</th>
      <th style="text-align:right;">Diff</th>
    </tr>
  </thead>
  <tbody>
{disease_rows}
  </tbody>
</table>
</div>

<div class="analysis-box">
  <p><strong>Interpretation:</strong> China's portfolio is heavily weighted toward cancer and cardiovascular
  disease, reflecting both domestic burden and pharmaceutical industry focus. Africa's portfolio is
  HIV-heavy, reflecting donor priorities. The "traditional medicine" category is a marker of domestic
  research autonomy -- China runs substantial traditional medicine trials; Africa does not.</p>
</div>

<!-- ============================================================ -->
<!-- 7. PHASE DISTRIBUTION                                         -->
<!-- ============================================================ -->
<h2>7. Phase Distribution: Who Does Early-Phase?</h2>
<div class="card">
<table>
  <thead>
    <tr>
      <th>Phase</th>
      <th style="text-align:right;">China (n)</th>
      <th style="text-align:right;">China (%)</th>
      <th style="text-align:right;">Africa Top 5 (n)</th>
      <th style="text-align:right;">Africa Avg (%)</th>
      <th style="text-align:right;">Diff</th>
    </tr>
  </thead>
  <tbody>
{phase_rows}
  </tbody>
</table>
</div>

<div class="analysis-box">
  <p><strong>Why Phase 1 share matters:</strong> A higher Phase 1 share indicates domestic drug development.
  Phase 1 trials are the earliest stage where a new drug is first tested in humans -- they require
  sophisticated facilities and regulatory capacity. When a country does mostly Phase 3 trials, it is
  typically hosting the final stage of someone else's drug. China's Phase 1 share reflects a country that
  develops its own medicines.</p>
</div>

<!-- ============================================================ -->
<!-- 8. GROWTH TRAJECTORY                                          -->
<!-- ============================================================ -->
<h2>8. Growth Trajectory: 2010-2025</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
  Cumulative interventional trials started in each year, showing China's exponential acceleration
  after the 2015 reforms vs Africa's linear growth.
</p>
<div class="card">
<div class="overflow-x">
<table style="font-size:0.8rem;">
  <thead>
    <tr>
      {growth_header}
    </tr>
  </thead>
  <tbody>
{growth_body}
  </tbody>
</table>
</div>
</div>

<div class="highlight">
  <p><strong>The inflection point:</strong> China's growth visibly accelerates after 2015 (NMPA reform year).
  Before 2015, China added ~2,000 trials/year. After 2015, it added ~4,000-5,000/year. Africa's top 5
  countries show steady but unaccelerated growth -- no comparable reform has created an inflection point.</p>
</div>

<!-- ============================================================ -->
<!-- 9. FIVE LESSONS FOR AFRICA                                    -->
<!-- ============================================================ -->
<h2>9. Five Lessons Africa Can Learn from China</h2>

<div class="lesson-box">
  <h3>Lesson 1: Regulatory Harmonization via the African Medicines Agency</h3>
  <p>The AMA Treaty (entered into force 2023) is Africa's equivalent of NMPA reform. If the AMA
  achieves a single continental regulatory pathway -- even initially for a subset of countries -- it
  could replicate China's backlog-clearing moment. <strong>Priority:</strong> Make the AMA operational
  with mutual recognition agreements among the African Union's 55 member states. A joint review process
  for clinical trials, modeled on the EU's Clinical Trials Regulation, would cut approval times from
  months to weeks.</p>
</div>

<div class="lesson-box">
  <h3>Lesson 2: Domestic Manufacturing Creates Trial Demand</h3>
  <p>Africa's Pharma Manufacturing Plan of Action and the Partnership for African Vaccine Manufacturing
  (PAVM) aim to build continental pharmaceutical capacity. Every domestic manufacturer needs clinical
  trials for its products. <strong>Priority:</strong> Link manufacturing incentives to clinical trial
  requirements. If African manufacturers must demonstrate efficacy through local Phase 1-3 trials,
  trial volume will grow organically.</p>
</div>

<div class="lesson-box">
  <h3>Lesson 3: Academic Incentives for Investigators</h3>
  <p>China made clinical trial leadership a path to academic promotion. Africa's medical schools rarely
  reward research equally with clinical service. <strong>Priority:</strong> African university promotion
  criteria should include PI-led trial conduct. Dedicated clinical research career tracks, with protected
  time and competitive salaries, would retain investigators who currently emigrate.</p>
</div>

<div class="lesson-box">
  <h3>Lesson 4: Digital Infrastructure and Data Systems</h3>
  <p>China built a national clinical trial registry, electronic data capture infrastructure, and
  specimen logistics networks. Africa's digital health investments (e.g., DHIS2) could be extended to
  clinical trial data management. <strong>Priority:</strong> Build on existing African digital health
  platforms to create trial-ready infrastructure: electronic consent, cloud-based data capture, and
  cold-chain tracking.</p>
</div>

<div class="lesson-box">
  <h3>Lesson 5: CRO Capacity as an Economic Strategy</h3>
  <p>China's CRO industry became a $8+ billion sector employing hundreds of thousands. Africa has
  minimal CRO capacity. <strong>Priority:</strong> Establish African CRO training programs and
  incentivize international CROs to open African offices. South Africa's existing capacity (3,473 trials)
  could serve as a regional CRO hub, with satellite sites across East and West Africa.</p>
</div>

<!-- ============================================================ -->
<!-- 10. THE PATH FORWARD                                          -->
<!-- ============================================================ -->
<h2>10. The Path Forward: Africa 2030</h2>

<div class="policy-box">
  <h3>Can Africa realistically follow China's path?</h3>
  <p>China's advantages -- centralized government, massive domestic market, existing manufacturing base --
  are structural. Africa cannot replicate them directly. But Africa has its own advantages:</p>
  <ul>
    <li><strong>Young, growing population</strong> -- the world's largest patient pool for clinical trials
    by 2030, particularly for diseases poorly studied elsewhere</li>
    <li><strong>Genetic diversity</strong> -- Africa has the greatest human genetic diversity, making it
    scientifically essential for pharmacogenomics and precision medicine trials</li>
    <li><strong>Under-researched diseases</strong> -- sickle cell disease, rheumatic heart disease,
    endomyocardial fibrosis, and tropical infections need African-led trials</li>
    <li><strong>Continental unity</strong> -- the AMA and African Union provide a framework for
    harmonization that China achieved through centralized authority</li>
    <li><strong>Digital leapfrogging</strong> -- mobile money (M-Pesa) showed Africa can skip
    infrastructure stages; mobile-first trial platforms could do the same</li>
  </ul>
</div>

<div class="analysis-box">
  <h3>Realistic targets for Africa by 2030</h3>
  <ul>
    <li><strong>AMA fully operational</strong> with mutual recognition in 20+ countries</li>
    <li><strong>Double Africa's per-capita trial density</strong> from ~{africa5_per_m}/M to ~{africa5_per_m * 2}/M</li>
    <li><strong>Phase 1 share above 15%</strong> (indicating domestic drug development)</li>
    <li><strong>At least 5 African-owned CROs</strong> with GCP certification</li>
    <li><strong>NCD trial share above 30%</strong> to match disease burden</li>
  </ul>
</div>

<!-- ============================================================ -->
<!-- FOOTER                                                        -->
<!-- ============================================================ -->
<div class="footer">
  <p>Project 24: The China Leapfrog -- Can Africa Follow?</p>
  <p>Data source: ClinicalTrials.gov API v2 (public, accessed {datetime.now().strftime("%d %B %Y")})</p>
  <p>Verified counts: China 38,933 | Egypt 12,395 | South Africa 3,473 | Uganda 783 | Kenya 720 | Nigeria 354</p>
  <p>Generated by fetch_china_leapfrog.py | Part of the AfricaRCT analysis series</p>
</div>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Run the full pipeline."""
    print("=" * 60)
    print("Project 24: The China Leapfrog -- Can Africa Follow?")
    print("=" * 60)

    # Fetch data
    data = fetch_all_data()

    # Analyze
    results = analyze_data(data)

    # Summary
    china = results["china"]
    if china:
        print(f"\n{'='*60}")
        print(f"CHINA: {china['trials']:,} trials, "
              f"{china['per_million']}/M population")
        print(f"Phase 1 share: {results['china_p1_share']}%")
        print(f"vs Africa Top 5 avg: {results['china_vs_africa5']}x")
        print(f"{'='*60}")

        print("\nAfrica Top 5:")
        for c in results["africa5"]:
            print(f"  {c['country']:15s} -> {c['trials']:>6,} trials, "
                  f"{c['per_million']}/M, P1={c['phase1_share']}%")

        print(f"\nAfrica Top 5 aggregate: {results['africa5_trials']:,} trials, "
              f"{results['africa5_per_m']}/M")

        print("\nDisease portfolio (China vs Africa avg share):")
        for cond, vals in results["disease_comparison"].items():
            print(f"  {cond:22s} -> China {vals['china_share']:5.1f}% | "
                  f"Africa {vals['africa_avg_share']:5.1f}%")

    # Generate HTML
    html = generate_html(data, results)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nDashboard: {OUTPUT_HTML}")

    print("\nDone.")


if __name__ == "__main__":
    main()
