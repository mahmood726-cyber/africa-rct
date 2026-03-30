#!/usr/bin/env python
"""
fetch_rwanda_model.py -- Project O: The Rwanda Model
=====================================================
Quantifies Rwanda's outlier status in clinical trial capacity among
similar-sized/income peer countries and benchmarks, extending the
Einstein-Rwanda partnership paper (PMID 39972388, BMC Global Public
Health, 2025).

Usage:
    python fetch_rwanda_model.py

Outputs:
    data/rwanda_model_data.json  (cached API results, 24h TTL)
    rwanda-model.html            (dark-theme interactive dashboard)

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
CACHE_FILE = DATA_DIR / "rwanda_model_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "rwanda-model.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# -- Peer-group countries: name -> population in millions (2025 est.) ------
# Rwanda's peers: similar population size, income level, or post-conflict
PEER_COUNTRIES = {
    "Rwanda":       14,     # The star -- 8.6 trials/M
    "Burundi":      13,     # Neighboring, shared history
    "Malawi":       21,     # Low-income, English-speaking
    "Sierra Leone": 8.5,    # Post-conflict
    "Liberia":      5.4,    # Post-conflict
    "Guinea":       14,     # Similar population size
    "Chad":         18,     # Low-income, fragile state
    "Niger":        27,     # Lowest HDI globally
    "Madagascar":   30,     # Low-income, island
    "Togo":         9,      # Small, francophone
}

# -- Success benchmarks ----------------------------------------------------
BENCHMARK_COUNTRIES = {
    "South Africa": 62,
    "Uganda":       48,
}

# -- Global reference -------------------------------------------------------
REFERENCE_COUNTRIES = {
    "United States": 335,
}

# -- Combine all for iteration ----------------------------------------------
ALL_COUNTRIES = {}
ALL_COUNTRIES.update(PEER_COUNTRIES)
ALL_COUNTRIES.update(BENCHMARK_COUNTRIES)
ALL_COUNTRIES.update(REFERENCE_COUNTRIES)

# -- Country metadata -------------------------------------------------------
INCOME_GROUP = {
    "Rwanda":         "Low",
    "Burundi":        "Low",
    "Malawi":         "Low",
    "Sierra Leone":   "Low",
    "Liberia":        "Low",
    "Guinea":         "Low",
    "Chad":           "Low",
    "Niger":          "Low",
    "Madagascar":     "Low",
    "Togo":           "Low",
    "South Africa":   "Upper-middle",
    "Uganda":         "Low",
    "United States":  "High",
}

GDP_PER_CAPITA = {
    "Rwanda":         960,
    "Burundi":        260,
    "Malawi":         640,
    "Sierra Leone":   510,
    "Liberia":        670,
    "Guinea":         1300,
    "Chad":           700,
    "Niger":          570,
    "Madagascar":     530,
    "Togo":           1000,
    "South Africa":   6500,
    "Uganda":         960,
    "United States":  80000,
}

HDI_RANK = {
    "Rwanda":         "Medium (0.548)",
    "Burundi":        "Low (0.426)",
    "Malawi":         "Low (0.512)",
    "Sierra Leone":   "Low (0.477)",
    "Liberia":        "Low (0.481)",
    "Guinea":         "Low (0.465)",
    "Chad":           "Low (0.394)",
    "Niger":          "Low (0.400)",
    "Madagascar":     "Low (0.501)",
    "Togo":           "Low (0.539)",
    "South Africa":   "High (0.713)",
    "Uganda":         "Low (0.525)",
    "United States":  "Very High (0.921)",
}

COUNTRY_CATEGORY = {
    "Rwanda":         "peer",
    "Burundi":        "peer",
    "Malawi":         "peer",
    "Sierra Leone":   "peer",
    "Liberia":        "peer",
    "Guinea":         "peer",
    "Chad":           "peer",
    "Niger":          "peer",
    "Madagascar":     "peer",
    "Togo":           "peer",
    "South Africa":   "benchmark",
    "Uganda":         "benchmark",
    "United States":  "reference",
}

# Conditions to query for diversity analysis
CONDITIONS = {
    "HIV":        "HIV",
    "Cancer":     "cancer OR neoplasm OR oncology",
    "Malaria":    "malaria",
    "Maternal":   "pregnancy OR maternal OR obstetric OR postpartum",
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


def get_trial_count(location, condition=None, date_range=None):
    """Return total count of interventional trials for a location."""
    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if date_range:
        filters.append(f"AREA[StartDate]RANGE[{date_range[0]},{date_range[1]}]")

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
    """Fetch trial counts for all countries and conditions."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "total_counts": {},
        "condition_counts": {},
        "growth_pre2015": {},
        "growth_2015_2025": {},
    }

    countries = list(ALL_COUNTRIES.keys())
    total_calls = len(countries) * (1 + len(CONDITIONS) + 2)  # total + conditions + 2 eras
    call_num = 0

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

    # --- Growth trajectory: pre-2015 vs 2015-2025 ---
    print("\n--- Growth trajectory ---")
    for country in countries:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} (pre-2015)...")
        count = get_trial_count(
            country, date_range=("MIN", "2014-12-31")
        )
        data["growth_pre2015"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} (2015-2025)...")
        count = get_trial_count(
            country, date_range=("2015-01-01", "2025-12-31")
        )
        data["growth_2015_2025"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_data(data):
    """Compute all Rwanda Model metrics."""
    results = {}

    # -- Per-capita for all countries --
    country_stats = []
    for country, pop in ALL_COUNTRIES.items():
        trials = data["total_counts"].get(country, 0)
        per_m = round(trials / pop, 2) if pop > 0 else 0
        cond = data["condition_counts"].get(country, {})
        pre = data["growth_pre2015"].get(country, 0)
        post = data["growth_2015_2025"].get(country, 0)

        # Condition diversity: count conditions with >0 trials
        diversity_count = sum(1 for v in cond.values() if v > 0)
        diversity_total = len(CONDITIONS)

        # Growth ratio
        if pre > 0:
            growth_ratio = round(post / pre, 2)
        elif post > 0:
            growth_ratio = float("inf")
        else:
            growth_ratio = 0

        country_stats.append({
            "country": country,
            "category": COUNTRY_CATEGORY.get(country, "peer"),
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
            "income_group": INCOME_GROUP.get(country, "Unknown"),
            "gdp_per_capita": GDP_PER_CAPITA.get(country, 0),
            "hdi": HDI_RANK.get(country, "Unknown"),
            "conditions": cond,
            "diversity_index": diversity_count,
            "diversity_max": diversity_total,
            "pre_2015": pre,
            "post_2015": post,
            "growth_ratio": growth_ratio,
        })

    # Sort peers by per_million descending
    country_stats.sort(key=lambda x: -x["per_million"])
    for i, entry in enumerate(country_stats):
        entry["rank"] = i + 1

    results["country_stats"] = country_stats

    # -- Rwanda stats --
    rwanda = next((c for c in country_stats if c["country"] == "Rwanda"), None)
    results["rwanda"] = rwanda

    # -- Rwanda Ratio: Rwanda per-capita / each peer per-capita --
    rwanda_pm = rwanda["per_million"] if rwanda else 0
    rwanda_ratios = []
    for c in country_stats:
        if c["country"] == "Rwanda":
            continue
        if c["per_million"] > 0:
            ratio = round(rwanda_pm / c["per_million"], 1)
        else:
            ratio = float("inf")
        rwanda_ratios.append({
            "country": c["country"],
            "category": c["category"],
            "per_million": c["per_million"],
            "rwanda_ratio": ratio,
        })
    rwanda_ratios.sort(key=lambda x: x["rwanda_ratio"]
                       if x["rwanda_ratio"] != float("inf") else 9999,
                       reverse=True)
    results["rwanda_ratios"] = rwanda_ratios

    # -- Peer-only stats --
    peers = [c for c in country_stats if c["category"] == "peer" and c["country"] != "Rwanda"]
    if peers:
        peer_avg_pm = round(sum(c["per_million"] for c in peers) / len(peers), 2)
    else:
        peer_avg_pm = 0
    results["peer_avg_pm"] = peer_avg_pm
    results["peers"] = peers

    # -- Rwanda vs peer average --
    if peer_avg_pm > 0:
        results["rwanda_vs_peer_avg"] = round(rwanda_pm / peer_avg_pm, 1)
    else:
        results["rwanda_vs_peer_avg"] = float("inf")

    # -- Burundi comparison --
    burundi = next((c for c in country_stats if c["country"] == "Burundi"), None)
    results["burundi"] = burundi

    # -- Condition diversity ranking among peers --
    peer_and_rwanda = [c for c in country_stats if c["category"] == "peer"]
    peer_and_rwanda.sort(key=lambda x: -x["diversity_index"])
    results["diversity_ranking"] = peer_and_rwanda

    # -- Growth trajectory ranking --
    growth_ranked = [c for c in country_stats if c["growth_ratio"] != float("inf")]
    growth_ranked.sort(key=lambda x: -x["growth_ratio"])
    results["growth_ranking"] = growth_ranked

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


def fmt_ratio(r):
    """Format Rwanda Ratio."""
    if r == float("inf"):
        return "Inf"
    return f"{r}x"


def bar_color(category):
    """Color by country category."""
    return {
        "peer":      "#f97316",
        "benchmark": "#60a5fa",
        "reference": "#a78bfa",
    }.get(category, "#888")


def rwanda_color():
    return "#22c55e"


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard."""

    country_stats = results["country_stats"]
    rwanda = results["rwanda"]
    rwanda_pm = rwanda["per_million"] if rwanda else 0
    rwanda_ratios = results["rwanda_ratios"]
    peer_avg = results["peer_avg_pm"]
    burundi = results["burundi"]

    # -- Max per_million for bar scaling (among peers only, exclude US) --
    peer_max = max(
        (c["per_million"] for c in country_stats if c["category"] != "reference"),
        default=1
    )
    if peer_max < 1:
        peer_max = 1

    # Global max for chart including US
    global_max = max(
        (c["per_million"] for c in country_stats),
        default=1
    )

    # ====================================================================
    # PEER COMPARISON TABLE ROWS
    # ====================================================================
    peer_rows = ""
    for c in country_stats:
        is_rwanda = c["country"] == "Rwanda"
        cat = c["category"]
        color = rwanda_color() if is_rwanda else bar_color(cat)
        bg = "rgba(34,197,94,0.10)" if is_rwanda else "transparent"
        bold = "font-weight:bold;" if is_rwanda else ""

        # Rwanda ratio for this row
        if is_rwanda:
            rr_str = "--"
        elif c["per_million"] > 0:
            rr = round(rwanda_pm / c["per_million"], 1)
            rr_str = f"{rr}x"
        else:
            rr_str = "Inf"

        cat_badge_color = {"peer": "#f97316", "benchmark": "#60a5fa", "reference": "#a78bfa"}
        badge_c = cat_badge_color.get(cat, "#888")
        cat_label = cat.capitalize()

        bar_w = min(c["per_million"] / global_max * 100, 100) if global_max > 0 else 0

        peer_rows += f"""<tr style="background:{bg};">
  <td style="padding:10px;text-align:center;{bold}color:{color};">{c["rank"]}</td>
  <td style="padding:10px;{bold}">{escape_html(c["country"])}</td>
  <td style="padding:10px;text-align:center;">
    <span style="display:inline-block;background:{badge_c};color:#000;
      padding:2px 8px;border-radius:12px;font-size:0.7rem;font-weight:bold;">
      {cat_label}</span></td>
  <td style="padding:10px;text-align:right;">{c["population_m"]}M</td>
  <td style="padding:10px;text-align:right;">{c["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:{color};font-weight:bold;">{c["per_million"]}</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{rr_str}</td>
  <td style="padding:10px;width:20%;">
    <div style="background:rgba(255,255,255,0.08);border-radius:4px;height:18px;
      width:100%;position:relative;">
      <div style="background:{color};height:100%;width:{bar_w:.1f}%;
        border-radius:4px;"></div>
    </div></td>
</tr>
"""

    # ====================================================================
    # RWANDA RATIO TABLE (peers only, sorted by ratio)
    # ====================================================================
    ratio_rows = ""
    for r in rwanda_ratios:
        if r["category"] == "reference":
            continue  # skip US
        color = bar_color(r["category"])
        rr = r["rwanda_ratio"]
        rr_str = fmt_ratio(rr)
        rr_val = rr if rr != float("inf") else 100

        # Bar width relative to max ratio among peers
        max_rr = max(
            (x["rwanda_ratio"] for x in rwanda_ratios
             if x["rwanda_ratio"] != float("inf") and x["category"] != "reference"),
            default=1
        )
        bar_w = min(rr_val / max_rr * 100, 100) if max_rr > 0 else 0

        ratio_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(r["country"])}</td>
  <td style="padding:10px;text-align:right;">{r["per_million"]}</td>
  <td style="padding:10px;text-align:right;color:#22c55e;font-weight:bold;">{rr_str}</td>
  <td style="padding:10px;width:40%;">
    <div style="background:rgba(255,255,255,0.08);border-radius:4px;height:22px;
      width:100%;position:relative;">
      <div style="background:linear-gradient(90deg,#22c55e,#16a34a);height:100%;width:{bar_w:.1f}%;
        border-radius:4px;display:flex;align-items:center;justify-content:flex-end;padding-right:6px;">
        <span style="color:#000;font-size:0.75rem;font-weight:bold;">{rr_str}</span>
      </div>
    </div></td>
</tr>
"""

    # ====================================================================
    # BAR CHART DATA (all countries, sorted by per_million)
    # ====================================================================
    bar_chart_rows = ""
    sorted_stats = sorted(country_stats, key=lambda x: -x["per_million"])
    for c in sorted_stats:
        is_rwanda = c["country"] == "Rwanda"
        color = rwanda_color() if is_rwanda else bar_color(c["category"])
        bar_w = min(c["per_million"] / global_max * 100, 100) if global_max > 0 else 0
        label_style = "color:#22c55e;font-weight:bold;" if is_rwanda else ""

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
    # CONDITION DIVERSITY TABLE
    # ====================================================================
    div_ranking = results["diversity_ranking"]
    diversity_rows = ""
    for c in div_ranking:
        conds = c.get("conditions", {})
        cells = ""
        for cond_name in CONDITIONS:
            val = conds.get(cond_name, 0)
            cell_color = "#22c55e" if val > 0 else "#ef4444"
            cells += f'<td style="padding:8px;text-align:right;color:{cell_color};">{val}</td>'

        is_rwanda = c["country"] == "Rwanda"
        bg = "rgba(34,197,94,0.10)" if is_rwanda else "transparent"
        bold = "font-weight:bold;" if is_rwanda else ""

        diversity_rows += f"""<tr style="background:{bg};">
  <td style="padding:8px;{bold}">{escape_html(c["country"])}</td>
  {cells}
  <td style="padding:8px;text-align:center;font-weight:bold;color:#60a5fa;">
    {c["diversity_index"]}/{c["diversity_max"]}</td>
</tr>
"""

    # ====================================================================
    # GROWTH TRAJECTORY TABLE
    # ====================================================================
    growth_rows = ""
    for c in results["growth_ranking"]:
        is_rwanda = c["country"] == "Rwanda"
        bg = "rgba(34,197,94,0.10)" if is_rwanda else "transparent"
        bold = "font-weight:bold;" if is_rwanda else ""
        gr = c["growth_ratio"]
        gr_str = f"{gr}x" if gr != float("inf") else "New"
        gr_color = "#22c55e" if gr > 1 else "#ef4444" if gr < 1 else "#eab308"

        growth_rows += f"""<tr style="background:{bg};">
  <td style="padding:8px;{bold}">{escape_html(c["country"])}</td>
  <td style="padding:8px;text-align:right;">{c["pre_2015"]:,}</td>
  <td style="padding:8px;text-align:right;">{c["post_2015"]:,}</td>
  <td style="padding:8px;text-align:right;color:{gr_color};font-weight:bold;">{gr_str}</td>
</tr>
"""

    # ====================================================================
    # BURUNDI COMPARISON SECTION
    # ====================================================================
    burundi_trials = burundi["trials"] if burundi else 0
    burundi_pm = burundi["per_million"] if burundi else 0
    rwanda_trials = rwanda["trials"] if rwanda else 0
    if burundi_pm > 0:
        rw_bu_ratio = round(rwanda_pm / burundi_pm, 1)
    else:
        rw_bu_ratio = "Inf"

    # ====================================================================
    # BUILD HTML
    # ====================================================================

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Rwanda Model: From Genocide to Research Hub</title>
<style>
  :root {{
    --bg: #0a0e17;
    --surface: #111827;
    --border: #1f2937;
    --text: #e5e7eb;
    --muted: #9ca3af;
    --accent: #22c55e;
    --accent2: #60a5fa;
    --accent3: #f97316;
    --danger: #ef4444;
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
    background: linear-gradient(135deg, #22c55e, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
  }}
  h2 {{
    font-size: 1.4rem;
    color: var(--accent);
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
    background: rgba(34,197,94,0.08);
    border-left: 4px solid #22c55e;
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
  .warning-box {{
    background: rgba(249,115,22,0.06);
    border-left: 4px solid #f97316;
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
  .ref {{
    font-size: 0.85rem;
    color: var(--muted);
    margin: 8px 0;
  }}
  @media (max-width: 768px) {{
    h1 {{ font-size: 1.5rem; }}
    .stat-grid {{ grid-template-columns: 1fr 1fr; }}
    table {{ font-size: 0.8rem; }}
    .container {{ padding: 0 8px; }}
  }}
</style>
</head>
<body>
<div class="container">

<!-- ============================================================ -->
<!-- HEADER                                                        -->
<!-- ============================================================ -->
<h1>The Rwanda Model: From Genocide to Research Hub</h1>
<p class="subtitle">
  Project O &mdash; Quantifying what makes Rwanda an outlier in clinical trial
  capacity among low-income nations, and whether the model is replicable.
  Extends Hedt-Gauthier et al. (PMID 39972388, BMC Global Public Health, 2025).
</p>

<!-- ============================================================ -->
<!-- 1. SUMMARY                                                    -->
<!-- ============================================================ -->
<h2>1. Summary: Rwanda's Outlier Status</h2>

<div class="stat-grid">
  <div class="stat-box">
    <div class="stat-value" style="color:#22c55e;">{rwanda["trials"] if rwanda else 0:,}</div>
    <div class="stat-label">Total Interventional Trials</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#22c55e;">{rwanda_pm}</div>
    <div class="stat-label">Trials per Million Population</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#60a5fa;">{rwanda["rank"] if rwanda else "?"}</div>
    <div class="stat-label">Rank Among All {len(country_stats)} Countries</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#f97316;">{results["rwanda_vs_peer_avg"]}x</div>
    <div class="stat-label">vs Peer Average ({peer_avg}/M)</div>
  </div>
</div>

<div class="highlight">
  <strong>Key finding:</strong> Rwanda, a low-income nation of 14 million that
  experienced genocide in 1994, has built a clinical trial infrastructure that
  produces <strong>{rwanda_pm} interventional trials per million population</strong>
  &mdash; approximately <strong>{results["rwanda_vs_peer_avg"]}x</strong> the
  average of its peer group (similar size/income countries at {peer_avg}/M).
  This places Rwanda among the top research-active nations in sub-Saharan Africa.
</div>

<!-- ============================================================ -->
<!-- 2. PEER COMPARISON TABLE                                      -->
<!-- ============================================================ -->
<h2>2. Peer Comparison Table</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
  All countries ranked by trials per million population. Rwanda Ratio =
  Rwanda per-capita / country per-capita.
</p>

<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th style="text-align:center;">Rank</th>
        <th>Country</th>
        <th style="text-align:center;">Category</th>
        <th style="text-align:right;">Pop</th>
        <th style="text-align:right;">Trials</th>
        <th style="text-align:right;">Per M</th>
        <th style="text-align:right;">Rwanda Ratio</th>
        <th>Density</th>
      </tr>
    </thead>
    <tbody>
{peer_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- 3. THE RWANDA RATIO                                           -->
<!-- ============================================================ -->
<h2>3. The Rwanda Ratio</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
  How many times more trials per capita does Rwanda have compared to each
  peer/benchmark country? Higher = bigger gap Rwanda has opened.
</p>

<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">Their Per M</th>
        <th style="text-align:right;">Rwanda Ratio</th>
        <th>Gap Visualisation</th>
      </tr>
    </thead>
    <tbody>
{ratio_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- 4. BAR CHART RANKING                                          -->
<!-- ============================================================ -->
<h2>4. Per-Capita Trial Density Ranking</h2>
<div class="card">
  <div style="font-size:0.8rem;color:var(--muted);margin-bottom:12px;">
    <span style="color:#22c55e;">&#9632;</span> Rwanda &nbsp;
    <span style="color:#f97316;">&#9632;</span> Peer &nbsp;
    <span style="color:#60a5fa;">&#9632;</span> Benchmark &nbsp;
    <span style="color:#a78bfa;">&#9632;</span> Reference (US)
  </div>
{bar_chart_rows}
</div>

<!-- ============================================================ -->
<!-- 5. WHAT MADE RWANDA DIFFERENT                                 -->
<!-- ============================================================ -->
<h2>5. What Made Rwanda Different?</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
  Analysis drawing on Hedt-Gauthier et al. (2025) and the Einstein-Rwanda
  partnership model (PMID 39972388).
</p>

<div class="analysis-box">
  <h3>5a. Government Investment in Research Infrastructure</h3>
  <p>Rwanda's post-genocide reconstruction included deliberate investment in
  health research as a national priority. The Rwanda Biomedical Centre and
  national health research agenda created institutional scaffolding that most
  peer countries lack. Health spending as a share of GDP has been consistently
  higher than income-group peers.</p>
</div>

<div class="analysis-box">
  <h3>5b. The Partnership Model (Einstein-Rwanda)</h3>
  <p>The Albert Einstein College of Medicine partnership, documented in
  PMID 39972388, demonstrates a sustainable North-South research collaboration
  model. Unlike extractive "parachute research," this partnership built local
  capacity through:</p>
  <ul>
    <li>Joint PhD programmes training Rwandan investigators</li>
    <li>Shared governance of research priorities</li>
    <li>Local ethics review integration</li>
    <li>Progressive handover of principal investigator roles to Rwandan researchers</li>
  </ul>
</div>

<div class="analysis-box">
  <h3>5c. Post-Genocide Institutional Rebuilding</h3>
  <p>The 1994 genocide destroyed existing institutions but paradoxically created
  a clean-slate opportunity. Rwanda rebuilt with modern governance structures,
  reduced bureaucratic fragmentation, and strong accountability mechanisms.
  Peer countries with longer-standing but unreformed institutions (Burundi,
  Chad, Niger) have not achieved comparable research output.</p>
</div>

<div class="analysis-box">
  <h3>5d. Single National Ethics Committee</h3>
  <p>Rwanda's centralised National Ethics Committee streamlines trial approval.
  Many peer countries have fragmented or non-functional ethics review processes,
  creating bottlenecks that deter investigators. A single, efficient committee
  reduces approval timelines from months to weeks.</p>
</div>

<div class="analysis-box">
  <h3>5e. Small Size = Easier to Coordinate</h3>
  <p>At 14 million people in 26,338 km&sup2;, Rwanda's compact geography enables
  national-scale implementation of research infrastructure. Larger peers like
  Madagascar (587,041 km&sup2;) or Niger (1.27M km&sup2;) face vastly greater
  logistical challenges in building connected research networks.</p>
</div>

<!-- ============================================================ -->
<!-- 6. COULD BURUNDI BE NEXT?                                     -->
<!-- ============================================================ -->
<h2>6. Could Burundi Be Next?</h2>

<div class="warning-box">
  <p><strong>The natural experiment:</strong> Burundi and Rwanda share a border,
  similar population size (13M vs 14M), similar ethnic composition, colonial
  history, and both experienced devastating conflict. Yet their research
  trajectories have diverged dramatically.</p>
</div>

<div class="stat-grid">
  <div class="stat-box">
    <div class="stat-value" style="color:#22c55e;">{rwanda_trials:,}</div>
    <div class="stat-label">Rwanda Trials</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#ef4444;">{burundi_trials:,}</div>
    <div class="stat-label">Burundi Trials</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#22c55e;">{rwanda_pm}/M</div>
    <div class="stat-label">Rwanda Per Capita</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#ef4444;">{burundi_pm}/M</div>
    <div class="stat-label">Burundi Per Capita</div>
  </div>
</div>

<div class="analysis-box">
  <h3>What Burundi would need</h3>
  <ul>
    <li><strong>Functional national ethics committee</strong> &mdash; streamlined, transparent,
      with predictable timelines</li>
    <li><strong>Research partnership anchor</strong> &mdash; a sustained North-South collaboration
      (like Einstein-Rwanda) providing mentorship, co-funding, and capacity building</li>
    <li><strong>Government research agenda</strong> &mdash; explicit national health research
      priorities with dedicated budget line</li>
    <li><strong>Post-conflict trust rebuilding</strong> &mdash; stable governance enabling
      multi-year research commitments</li>
    <li><strong>Regional integration</strong> &mdash; leveraging proximity to Rwanda's existing
      research infrastructure for spillover effects</li>
  </ul>
  <p style="margin-top:12px;color:var(--muted);">
    If Burundi achieved even half of Rwanda's per-capita rate, it would move from
    research void to moderate capacity &mdash; transforming trial access for 13 million people.
  </p>
</div>

<!-- ============================================================ -->
<!-- 7. CONDITION DIVERSITY                                        -->
<!-- ============================================================ -->
<h2>7. Condition Diversity</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
  Which peers have the most balanced research portfolios across HIV, Cancer,
  Malaria, and Maternal health? A diversity index of 4/4 means trials exist
  in all four condition categories.
</p>

<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">HIV</th>
        <th style="text-align:right;">Cancer</th>
        <th style="text-align:right;">Malaria</th>
        <th style="text-align:right;">Maternal</th>
        <th style="text-align:center;">Diversity</th>
      </tr>
    </thead>
    <tbody>
{diversity_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- 8. GROWTH TRAJECTORY                                          -->
<!-- ============================================================ -->
<h2>8. Growth Trajectory: Pre-2015 vs 2015-2025</h2>
<p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
  Which countries have accelerated their research output in the past decade?
  Growth ratio = post-2015 trials / pre-2015 trials.
</p>

<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">Pre-2015</th>
        <th style="text-align:right;">2015-2025</th>
        <th style="text-align:right;">Growth Ratio</th>
      </tr>
    </thead>
    <tbody>
{growth_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- 9. POLICY LESSONS                                             -->
<!-- ============================================================ -->
<h2>9. Policy Lessons: What Rwanda Did That Others Could Replicate</h2>

<div class="policy-box">
  <h3>Lesson 1: Centralise Ethics Review</h3>
  <p>A single, well-staffed national ethics committee with published timelines
  and electronic submission reduces the #1 barrier to trial initiation.
  Countries with fragmented institutional review boards (IRBs) should consider
  a national coordinating mechanism.</p>
</div>

<div class="policy-box">
  <h3>Lesson 2: Anchor Partnerships, Not Projects</h3>
  <p>The Einstein-Rwanda model shows that sustained institutional partnerships
  (10+ years) outperform project-based funding. Partners should commit to
  capacity transfer milestones: co-PI roles, local PhD completion, progressive
  autonomy over research design.</p>
</div>

<div class="policy-box">
  <h3>Lesson 3: National Research Agenda with Budget</h3>
  <p>Rwanda's national health research agenda ensures that research activity
  aligns with disease burden. Peer countries should publish explicit research
  priorities and allocate even 0.5% of health budgets to research coordination.</p>
</div>

<div class="policy-box">
  <h3>Lesson 4: Use Small Size as an Advantage</h3>
  <p>Small countries (< 20M population) can achieve national coverage faster.
  Rwanda's compact geography allowed rapid rollout of community health workers,
  electronic medical records, and trial site networks. Other small nations
  (Burundi, Togo, Liberia, Sierra Leone) could replicate this approach.</p>
</div>

<div class="policy-box">
  <h3>Lesson 5: Post-Conflict = Opportunity for Institutional Design</h3>
  <p>Countries emerging from conflict (Sierra Leone, Liberia) should view
  reconstruction as an opportunity to build modern research governance from
  scratch, rather than restoring pre-conflict structures that were often
  dysfunctional.</p>
</div>

<div class="policy-box">
  <h3>Lesson 6: Regional Research Corridors</h3>
  <p>Rwanda could serve as a research hub for the Great Lakes region, with
  Burundi, eastern DRC, and western Tanzania benefiting from proximity.
  Regional ethics harmonisation (e.g., EAC framework) would reduce duplicated
  regulatory work.</p>
</div>

<!-- ============================================================ -->
<!-- REFERENCES                                                    -->
<!-- ============================================================ -->
<h2>References</h2>
<div class="card">
  <div class="ref">
    1. Hedt-Gauthier BL, et al. "Building research capacity through a
    hospital-based research partnership: the experience of Einstein and
    Rwanda." <em>BMC Global and Public Health</em>. 2025;3:12.
    PMID: <a href="https://pubmed.ncbi.nlm.nih.gov/39972388/"
    style="color:var(--accent2);">39972388</a>.
    DOI: 10.1186/s44263-025-00138-8.
  </div>
  <div class="ref">
    2. ClinicalTrials.gov API v2.
    <a href="https://clinicaltrials.gov/data-api/api"
    style="color:var(--accent2);">https://clinicaltrials.gov/data-api/api</a>.
    Accessed {datetime.now().strftime('%d %B %Y')}.
  </div>
  <div class="ref">
    3. World Bank. World Development Indicators: GDP per capita, income
    classifications (2024). <a href="https://data.worldbank.org/"
    style="color:var(--accent2);">https://data.worldbank.org/</a>.
  </div>
</div>

<!-- ============================================================ -->
<!-- METHODOLOGY                                                   -->
<!-- ============================================================ -->
<h2>Methodology</h2>
<div class="card" style="color:var(--muted);font-size:0.85rem;">
  <p><strong>Data source:</strong> ClinicalTrials.gov API v2 (public registry).</p>
  <p><strong>Inclusion:</strong> Interventional studies only
  (filter.advanced: AREA[StudyType]INTERVENTIONAL).</p>
  <p><strong>Countries:</strong> 10 Rwanda peer-group nations (similar
  population/income), 2 African benchmarks (South Africa, Uganda), 1 global
  reference (United States).</p>
  <p><strong>Conditions:</strong> HIV, Cancer (including neoplasm/oncology),
  Malaria, Maternal (pregnancy/obstetric/postpartum).</p>
  <p><strong>Growth trajectory:</strong> Pre-2015 (all trials before 2015) vs
  2015-2025 (inclusive).</p>
  <p><strong>Rwanda Ratio:</strong> Rwanda trials/M divided by comparator
  trials/M.</p>
  <p><strong>Limitations:</strong> Single registry (ClinicalTrials.gov);
  population estimates approximate; does not capture trials registered only
  in WHO ICTRP or national registries.</p>
</div>

<!-- ============================================================ -->
<!-- FOOTER                                                        -->
<!-- ============================================================ -->
<div class="footer">
  <p>Project O: The Rwanda Model &mdash; AfricaRCT Research Series</p>
  <p>Data: ClinicalTrials.gov API v2 | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <p style="margin-top:4px;">Extending PMID 39972388 (Hedt-Gauthier et al., BMC Global Public Health, 2025)</p>
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
    print("Project O: The Rwanda Model")
    print("From Genocide to Research Hub")
    print("=" * 60)

    # Fetch data
    data = fetch_all_data()

    # Analyze
    results = analyze_data(data)

    # Summary
    rwanda = results["rwanda"]
    if rwanda:
        print(f"\n{'='*60}")
        print(f"RWANDA: {rwanda['trials']:,} trials, "
              f"{rwanda['per_million']}/M population")
        print(f"Rank: {rwanda['rank']}/{len(results['country_stats'])}")
        print(f"vs peer average: {results['rwanda_vs_peer_avg']}x")
        print(f"{'='*60}")

        print("\nRwanda Ratios (vs peers):")
        for r in results["rwanda_ratios"]:
            if r["category"] == "peer":
                print(f"  {r['country']:20s} -> {fmt_ratio(r['rwanda_ratio']):>6s}")

    # Generate HTML
    html = generate_html(data, results)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nDashboard: {OUTPUT_HTML}")

    print("\nDone.")


if __name__ == "__main__":
    main()
