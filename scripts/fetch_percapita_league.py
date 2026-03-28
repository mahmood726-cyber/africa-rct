#!/usr/bin/env python
"""
fetch_percapita_league.py — Per-Capita Trial Density League Table
=================================================================
Ranks every African country by interventional clinical trials per
million population, compares against global benchmarks, and produces
an interactive dark-theme HTML dashboard.

Usage:
    python fetch_percapita_league.py

Outputs:
    data/percapita_league_data.json  (cached API results, 24h TTL)
    percapita-league.html            (dark-theme interactive dashboard)

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
CACHE_FILE = DATA_DIR / "percapita_league_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "percapita-league.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# -- African countries: name -> population in millions (2025 est.) ----------

AFRICAN_COUNTRIES = {
    "South Africa":             62,
    "Egypt":                    110,
    "Kenya":                    56,
    "Uganda":                   48,
    "Nigeria":                  230,
    "Tanzania":                 67,
    "Ethiopia":                 130,
    "Ghana":                    34,
    "Cameroon":                 27,
    "Mozambique":               33,
    "Malawi":                   21,
    "Zambia":                   21,
    "Zimbabwe":                 15,
    "Senegal":                  17,
    "Rwanda":                   14,
    "Democratic Republic of Congo": 102,
    "Burkina Faso":             23,
    "Mali":                     23,
    "Niger":                    27,
    "Chad":                     18,
    "Somalia":                  18,
    "South Sudan":              11,
    "Sudan":                    48,
    "Benin":                    13,
    "Togo":                     9,
    "Guinea":                   14,
    "Madagascar":               30,
    "Central African Republic": 5.5,
    "Botswana":                 2.5,
    "Namibia":                  2.7,
}

# Display names for countries whose API name differs
DISPLAY_NAMES = {
    "Democratic Republic of Congo": "DRC",
    "Central African Republic": "CAR",
}

# -- Global comparators: name -> population in millions --------------------

COMPARATOR_COUNTRIES = {
    "United States":  335,
    "United Kingdom": 68,
    "India":          1430,
    "Brazil":         216,
    "China":          1410,
}

# -- World Bank income classification (2024) --------------------------------

INCOME_GROUP = {
    "South Africa":             "Upper-middle",
    "Egypt":                    "Lower-middle",
    "Kenya":                    "Lower-middle",
    "Uganda":                   "Low",
    "Nigeria":                  "Lower-middle",
    "Tanzania":                 "Lower-middle",
    "Ethiopia":                 "Low",
    "Ghana":                    "Lower-middle",
    "Cameroon":                 "Lower-middle",
    "Mozambique":               "Low",
    "Malawi":                   "Low",
    "Zambia":                   "Low",
    "Zimbabwe":                 "Lower-middle",
    "Senegal":                  "Lower-middle",
    "Rwanda":                   "Low",
    "Democratic Republic of Congo": "Low",
    "Burkina Faso":             "Low",
    "Mali":                     "Low",
    "Niger":                    "Low",
    "Chad":                     "Low",
    "Somalia":                  "Low",
    "South Sudan":              "Low",
    "Sudan":                    "Low",
    "Benin":                    "Lower-middle",
    "Togo":                     "Low",
    "Guinea":                   "Low",
    "Madagascar":               "Low",
    "Central African Republic": "Low",
    "Botswana":                 "Upper-middle",
    "Namibia":                  "Upper-middle",
}

# GDP per capita (USD, approximate 2024 estimates)
GDP_PER_CAPITA = {
    "South Africa":             6500,
    "Egypt":                    4300,
    "Kenya":                    2100,
    "Uganda":                   960,
    "Nigeria":                  1600,
    "Tanzania":                 1200,
    "Ethiopia":                 1020,
    "Ghana":                    2300,
    "Cameroon":                 1650,
    "Mozambique":               540,
    "Malawi":                   640,
    "Zambia":                   1200,
    "Zimbabwe":                 1400,
    "Senegal":                  1700,
    "Rwanda":                   960,
    "Democratic Republic of Congo": 580,
    "Burkina Faso":             830,
    "Mali":                     880,
    "Niger":                    570,
    "Chad":                     700,
    "Somalia":                  450,
    "South Sudan":              420,
    "Sudan":                    760,
    "Benin":                    1400,
    "Togo":                     1000,
    "Guinea":                   1300,
    "Madagascar":               530,
    "Central African Republic": 480,
    "Botswana":                 7800,
    "Namibia":                  5200,
}

# -- Regional classification -----------------------------------------------

REGION_MAP = {
    "South Africa":             "Southern Africa",
    "Egypt":                    "North Africa",
    "Kenya":                    "East Africa",
    "Uganda":                   "East Africa",
    "Nigeria":                  "West Africa",
    "Tanzania":                 "East Africa",
    "Ethiopia":                 "East Africa",
    "Ghana":                    "West Africa",
    "Cameroon":                 "Central Africa",
    "Mozambique":               "Southern Africa",
    "Malawi":                   "East Africa",
    "Zambia":                   "Southern Africa",
    "Zimbabwe":                 "Southern Africa",
    "Senegal":                  "West Africa",
    "Rwanda":                   "East Africa",
    "Democratic Republic of Congo": "Central Africa",
    "Burkina Faso":             "West Africa",
    "Mali":                     "West Africa",
    "Niger":                    "West Africa",
    "Chad":                     "Central Africa",
    "Somalia":                  "East Africa",
    "South Sudan":              "East Africa",
    "Sudan":                    "North Africa",
    "Benin":                    "West Africa",
    "Togo":                     "West Africa",
    "Guinea":                   "West Africa",
    "Madagascar":               "East Africa",
    "Central African Republic": "Central Africa",
    "Botswana":                 "Southern Africa",
    "Namibia":                  "Southern Africa",
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


def get_trial_count(location):
    """Return total count of interventional trials for a location."""
    params = {
        "format": "json",
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
    """Fetch trial counts for all countries (African + comparators)."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "african_counts": {},
        "comparator_counts": {},
    }

    total_calls = len(AFRICAN_COUNTRIES) + len(COMPARATOR_COUNTRIES)
    call_num = 0

    # --- African countries ---
    print("\n--- Querying African countries ---")
    for country in AFRICAN_COUNTRIES:
        call_num += 1
        dname = DISPLAY_NAMES.get(country, country)
        print(f"  [{call_num}/{total_calls}] {dname}...")
        count = get_trial_count(country)
        data["african_counts"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    # --- Global comparators ---
    print("\n--- Querying global comparators ---")
    for country in COMPARATOR_COUNTRIES:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country}...")
        count = get_trial_count(country)
        data["comparator_counts"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def classify(per_million):
    """Classify per-capita trial density."""
    if per_million >= 10:
        return "Research hub"
    elif per_million >= 3:
        return "Moderate"
    elif per_million >= 1:
        return "Desert"
    else:
        return "Void"


def classify_color(classification):
    """Return color for classification."""
    return {
        "Research hub": "#22c55e",
        "Moderate":     "#eab308",
        "Desert":       "#f97316",
        "Void":         "#ef4444",
    }.get(classification, "#888")


def classify_bg(classification):
    """Return background color for classification."""
    return {
        "Research hub": "rgba(34,197,94,0.12)",
        "Moderate":     "rgba(234,179,8,0.12)",
        "Desert":       "rgba(249,115,22,0.12)",
        "Void":         "rgba(239,68,68,0.12)",
    }.get(classification, "transparent")


def analyze_data(data):
    """Compute all rankings and derived metrics."""
    results = {}

    # -- Per-capita for African countries --
    african_league = []
    for country, pop in AFRICAN_COUNTRIES.items():
        trials = data["african_counts"].get(country, 0)
        per_m = round(trials / pop, 2) if pop > 0 else 0
        cls = classify(per_m)
        dname = DISPLAY_NAMES.get(country, country)
        african_league.append({
            "country": country,
            "display_name": dname,
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
            "classification": cls,
            "income_group": INCOME_GROUP.get(country, "Unknown"),
            "gdp_per_capita": GDP_PER_CAPITA.get(country, 0),
            "region": REGION_MAP.get(country, "Unknown"),
        })

    # Sort by per_million descending
    african_league.sort(key=lambda x: -x["per_million"])
    for i, entry in enumerate(african_league):
        entry["rank"] = i + 1
        entry["percentile"] = round((1 - i / len(african_league)) * 100, 1)

    results["african_league"] = african_league

    # -- Comparators --
    comparators = []
    for country, pop in COMPARATOR_COUNTRIES.items():
        trials = data["comparator_counts"].get(country, 0)
        per_m = round(trials / pop, 2) if pop > 0 else 0
        comparators.append({
            "country": country,
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
        })
    comparators.sort(key=lambda x: -x["per_million"])
    results["comparators"] = comparators

    # -- Africa aggregate --
    total_trials = sum(e["trials"] for e in african_league)
    total_pop = sum(e["population_m"] for e in african_league)
    africa_avg = round(total_trials / total_pop, 2) if total_pop > 0 else 0
    results["africa_total_trials"] = total_trials
    results["africa_total_pop"] = total_pop
    results["africa_avg_per_million"] = africa_avg

    # Best and worst
    results["best"] = african_league[0] if african_league else None
    results["worst"] = african_league[-1] if african_league else None

    # -- Classification counts --
    class_counts = defaultdict(int)
    class_pop = defaultdict(float)
    for e in african_league:
        class_counts[e["classification"]] += 1
        class_pop[e["classification"]] += e["population_m"]
    results["class_counts"] = dict(class_counts)
    results["class_pop"] = dict(class_pop)
    results["total_pop"] = total_pop

    # Population in void + desert
    void_desert_pop = class_pop.get("Void", 0) + class_pop.get("Desert", 0)
    results["void_desert_pop"] = void_desert_pop
    results["void_desert_pct"] = round(void_desert_pop / total_pop * 100, 1) if total_pop > 0 else 0

    # -- Income group analysis --
    income_groups = defaultdict(lambda: {"trials": 0, "pop": 0, "countries": 0})
    for e in african_league:
        ig = e["income_group"]
        income_groups[ig]["trials"] += e["trials"]
        income_groups[ig]["pop"] += e["population_m"]
        income_groups[ig]["countries"] += 1

    for ig in income_groups:
        g = income_groups[ig]
        g["per_million"] = round(g["trials"] / g["pop"], 2) if g["pop"] > 0 else 0

    results["income_groups"] = dict(income_groups)

    # -- GDP-trials correlation (Spearman rank) --
    gdps = [e["gdp_per_capita"] for e in african_league if e["gdp_per_capita"] > 0]
    pms = [e["per_million"] for e in african_league if e["gdp_per_capita"] > 0]
    if len(gdps) >= 3:
        results["gdp_correlation"] = _spearman_rank(gdps, pms)
    else:
        results["gdp_correlation"] = None

    # -- Regional breakdown --
    regions = defaultdict(lambda: {"trials": 0, "pop": 0, "countries": 0})
    for e in african_league:
        r = e["region"]
        regions[r]["trials"] += e["trials"]
        regions[r]["pop"] += e["population_m"]
        regions[r]["countries"] += 1

    for r in regions:
        g = regions[r]
        g["per_million"] = round(g["trials"] / g["pop"], 2) if g["pop"] > 0 else 0

    results["regions"] = dict(regions)

    # -- Void countries list --
    results["void_countries"] = [e for e in african_league if e["classification"] == "Void"]

    # -- Trend potential (proxy: higher GDP + low trials = room to grow) --
    growth_potential = []
    for e in african_league:
        if e["per_million"] < 5 and e["gdp_per_capita"] > 800:
            gap = e["gdp_per_capita"] / max(e["per_million"], 0.01)
            growth_potential.append({
                "country": e["display_name"],
                "gdp_per_capita": e["gdp_per_capita"],
                "per_million": e["per_million"],
                "growth_gap_score": round(gap, 0),
            })
    growth_potential.sort(key=lambda x: -x["growth_gap_score"])
    results["growth_potential"] = growth_potential[:10]

    return results


def _spearman_rank(x, y):
    """Compute Spearman rank correlation without scipy."""
    n = len(x)
    if n < 3:
        return None

    def _rank(vals):
        indexed = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and vals[indexed[j]] == vals[indexed[j + 1]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(x)
    ry = _rank(y)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    rho = 1 - 6 * d2 / (n * (n * n - 1))
    return round(rho, 3)


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


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard."""

    african_league = results["african_league"]
    comparators = results["comparators"]
    best = results["best"]
    worst = results["worst"]
    africa_avg = results["africa_avg_per_million"]

    # Find where Africa's best ranks among comparators
    best_pm = best["per_million"] if best else 0
    global_rank_note = ""
    for c in comparators:
        if best_pm >= c["per_million"]:
            global_rank_note = (
                f'{escape_html(best["display_name"])} ({best_pm}/M) '
                f'exceeds {escape_html(c["country"])} ({c["per_million"]}/M)'
            )
            break
    if not global_rank_note and comparators:
        global_rank_note = (
            f'{escape_html(best["display_name"])} ({best_pm}/M) '
            f'is below all comparators (lowest: {escape_html(comparators[-1]["country"])} '
            f'at {comparators[-1]["per_million"]}/M)'
        )

    # -- Max per_million for bar scaling --
    max_pm = max(e["per_million"] for e in african_league) if african_league else 1
    if max_pm < 1:
        max_pm = 1

    # ====================================================================
    # LEAGUE TABLE ROWS
    # ====================================================================
    league_rows = ""
    for e in african_league:
        cls_color = classify_color(e["classification"])
        cls_bg = classify_bg(e["classification"])
        bar_width = min(e["per_million"] / max_pm * 100, 100)
        league_rows += f"""<tr style="background:{cls_bg};">
  <td style="padding:10px;text-align:center;font-weight:bold;color:{cls_color};">
    {e["rank"]}</td>
  <td style="padding:10px;font-weight:bold;">{escape_html(e["display_name"])}</td>
  <td style="padding:10px;text-align:right;">{e["population_m"]}M</td>
  <td style="padding:10px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:{cls_color};font-weight:bold;">
    {e["per_million"]}</td>
  <td style="padding:10px;">
    <span style="display:inline-block;background:{cls_color};color:#000;
      padding:2px 10px;border-radius:12px;font-size:0.75rem;font-weight:bold;">
      {escape_html(e["classification"])}</span></td>
  <td style="padding:10px;">
    <div style="background:rgba(255,255,255,0.08);border-radius:4px;height:18px;
      width:100%;position:relative;">
      <div style="background:{cls_color};height:100%;width:{bar_width:.1f}%;
        border-radius:4px;"></div>
    </div></td>
</tr>
"""

    # ====================================================================
    # COMPARATOR ROWS
    # ====================================================================
    comp_rows = ""
    for c in comparators:
        comp_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(c["country"])}</td>
  <td style="padding:10px;text-align:right;">{c["population_m"]:,}M</td>
  <td style="padding:10px;text-align:right;">{c["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:#60a5fa;font-weight:bold;">
    {c["per_million"]}</td>
</tr>
"""

    # ====================================================================
    # VOID COUNTRIES
    # ====================================================================
    void_rows = ""
    for e in results["void_countries"]:
        if e["trials"] > 0:
            one_per = e["population_m"] * 1_000_000 / e["trials"]
            if one_per >= 1_000_000:
                ratio_str = f"1 per {one_per / 1_000_000:.1f}M people"
            else:
                ratio_str = f"1 per {one_per / 1_000:,.0f}K people"
        else:
            ratio_str = "No trials"
        void_rows += f"""<tr style="background:rgba(239,68,68,0.08);">
  <td style="padding:10px;color:#ef4444;font-weight:bold;">
    {escape_html(e["display_name"])}</td>
  <td style="padding:10px;text-align:right;">{e["population_m"]}M</td>
  <td style="padding:10px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:#ef4444;font-weight:bold;">
    {e["per_million"]}</td>
  <td style="padding:10px;color:var(--muted);">{ratio_str}</td>
</tr>
"""

    # ====================================================================
    # INCOME GROUP ROWS
    # ====================================================================
    income_order = ["Low", "Lower-middle", "Upper-middle"]
    income_rows = ""
    for ig in income_order:
        g = results["income_groups"].get(ig, {"trials": 0, "pop": 0, "countries": 0, "per_million": 0})
        income_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(ig)}</td>
  <td style="padding:10px;text-align:right;">{g["countries"]}</td>
  <td style="padding:10px;text-align:right;">{g["pop"]:.0f}M</td>
  <td style="padding:10px;text-align:right;">{g["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:#60a5fa;font-weight:bold;">
    {g["per_million"]}</td>
</tr>
"""

    # ====================================================================
    # GDP SCATTER TABLE
    # ====================================================================
    gdp_rows = ""
    gdp_sorted = sorted(african_league, key=lambda x: -x["gdp_per_capita"])
    for e in gdp_sorted:
        cls_color = classify_color(e["classification"])
        gdp_rows += f"""<tr>
  <td style="padding:8px;">{escape_html(e["display_name"])}</td>
  <td style="padding:8px;text-align:right;">${e["gdp_per_capita"]:,}</td>
  <td style="padding:8px;text-align:right;color:{cls_color};font-weight:bold;">
    {e["per_million"]}</td>
  <td style="padding:8px;">{escape_html(e["income_group"])}</td>
</tr>
"""

    # ====================================================================
    # REGIONAL BREAKDOWN
    # ====================================================================
    region_order = ["North Africa", "East Africa", "West Africa", "Central Africa", "Southern Africa"]
    region_rows = ""
    for r in region_order:
        g = results["regions"].get(r, {"trials": 0, "pop": 0, "countries": 0, "per_million": 0})
        r_color = "#22c55e" if g["per_million"] >= 3 else ("#eab308" if g["per_million"] >= 1 else "#ef4444")
        region_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(r)}</td>
  <td style="padding:10px;text-align:right;">{g["countries"]}</td>
  <td style="padding:10px;text-align:right;">{g["pop"]:.0f}M</td>
  <td style="padding:10px;text-align:right;">{g["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:{r_color};font-weight:bold;">
    {g["per_million"]}</td>
</tr>
"""

    # ====================================================================
    # GROWTH POTENTIAL
    # ====================================================================
    growth_rows = ""
    for i, g in enumerate(results["growth_potential"]):
        growth_rows += f"""<tr>
  <td style="padding:8px;text-align:center;">{i + 1}</td>
  <td style="padding:8px;font-weight:bold;">{escape_html(g["country"])}</td>
  <td style="padding:8px;text-align:right;">${g["gdp_per_capita"]:,}</td>
  <td style="padding:8px;text-align:right;">{g["per_million"]}</td>
  <td style="padding:8px;text-align:right;color:#60a5fa;font-weight:bold;">
    {g["growth_gap_score"]:,.0f}</td>
</tr>
"""

    # ====================================================================
    # POPULATION-WEIGHTED INJUSTICE
    # ====================================================================
    void_desert_pop = results["void_desert_pop"]
    void_desert_pct = results["void_desert_pct"]
    total_pop = results["total_pop"]

    void_pop = results["class_pop"].get("Void", 0)
    desert_pop = results["class_pop"].get("Desert", 0)
    moderate_pop = results["class_pop"].get("Moderate", 0)
    hub_pop = results["class_pop"].get("Research hub", 0)

    void_n = results["class_counts"].get("Void", 0)
    desert_n = results["class_counts"].get("Desert", 0)
    moderate_n = results["class_counts"].get("Moderate", 0)
    hub_n = results["class_counts"].get("Research hub", 0)

    correlation_str = ""
    if results["gdp_correlation"] is not None:
        rho = results["gdp_correlation"]
        strength = "strong" if abs(rho) >= 0.6 else ("moderate" if abs(rho) >= 0.3 else "weak")
        direction = "positive" if rho > 0 else "negative"
        correlation_str = f"Spearman rho = {rho} ({strength} {direction} correlation)"
    else:
        correlation_str = "Insufficient data for correlation"

    # ====================================================================
    # FULL HTML
    # ====================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Per-Capita Trial Density League Table -- Africa</title>
<style>
  :root {{
    --bg: #0a0e17;
    --card: #111827;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #60a5fa;
    --green: #22c55e;
    --yellow: #eab308;
    --orange: #f97316;
    --red: #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
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
    margin-bottom: 8px;
    background: linear-gradient(135deg, var(--accent), var(--green));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  h2 {{
    font-size: 1.4rem;
    color: var(--accent);
    margin: 32px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
  h3 {{
    font-size: 1.1rem;
    color: var(--muted);
    margin: 20px 0 10px;
  }}
  .subtitle {{
    color: var(--muted);
    font-size: 0.95rem;
    margin-bottom: 24px;
  }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }}
  .stat-card .number {{
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 4px;
  }}
  .stat-card .label {{
    color: var(--muted);
    font-size: 0.85rem;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    overflow-x: auto;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th {{
    padding: 12px 10px;
    text-align: left;
    color: var(--muted);
    font-weight: 600;
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
  .callout {{
    background: rgba(96,165,250,0.1);
    border-left: 4px solid var(--accent);
    padding: 16px 20px;
    border-radius: 0 8px 8px 0;
    margin: 16px 0;
    font-size: 0.95rem;
  }}
  .callout-red {{
    background: rgba(239,68,68,0.1);
    border-left-color: var(--red);
  }}
  .injustice-bar {{
    height: 32px;
    border-radius: 8px;
    display: flex;
    overflow: hidden;
    margin: 12px 0;
    font-size: 0.75rem;
    font-weight: bold;
  }}
  .injustice-bar div {{
    display: flex;
    align-items: center;
    justify-content: center;
    color: #000;
    white-space: nowrap;
    padding: 0 8px;
  }}
  .legend {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    margin: 12px 0;
    font-size: 0.85rem;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .legend-dot {{
    width: 12px;
    height: 12px;
    border-radius: 50%;
    flex-shrink: 0;
  }}
  .footer {{
    color: var(--muted);
    font-size: 0.8rem;
    text-align: center;
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
  }}
  @media (max-width: 768px) {{
    body {{ padding: 12px; }}
    h1 {{ font-size: 1.4rem; }}
    .summary-grid {{ grid-template-columns: 1fr 1fr; }}
    table {{ font-size: 0.8rem; }}
  }}
</style>
</head>
<body>
<div class="container">

<!-- ===== HEADER ===== -->
<h1>Per-Capita Trial Density League Table</h1>
<p class="subtitle">
  Ranking all 30 African countries by interventional clinical trials per million
  population | ClinicalTrials.gov API v2 |
  Generated {datetime.now().strftime("%d %B %Y")}
</p>

<!-- ===== 1. SUMMARY ===== -->
<h2>1. Summary</h2>
<div class="summary-grid">
  <div class="stat-card">
    <div class="number" style="color:var(--green);">{best["per_million"] if best else 0}</div>
    <div class="label">Best: {escape_html(best["display_name"]) if best else "N/A"} (per M)</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--red);">{worst["per_million"] if worst else 0}</div>
    <div class="label">Worst: {escape_html(worst["display_name"]) if worst else "N/A"} (per M)</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--accent);">{africa_avg}</div>
    <div class="label">Africa Average (per M)</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--accent);">{results["africa_total_trials"]:,}</div>
    <div class="label">Total African Trials</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--red);">{void_desert_pct}%</div>
    <div class="label">Population in Void/Desert</div>
  </div>
</div>

<div class="callout">
  Africa's trial density ranges from <strong>{best["per_million"] if best else "?"}/M</strong>
  ({escape_html(best["display_name"]) if best else "?"}) to
  <strong>{worst["per_million"] if worst else "?"}/M</strong>
  ({escape_html(worst["display_name"]) if worst else "?"}) --
  a <strong>{round(best["per_million"] / max(worst["per_million"], 0.01), 0):.0f}x</strong> range.
  Africa's average ({africa_avg}/M) versus the UK
  ({next((c["per_million"] for c in comparators if c["country"] == "United Kingdom"), "?")}
  /M) reveals a
  {round(next((c["per_million"] for c in comparators if c["country"] == "United Kingdom"), 1) / max(africa_avg, 0.01), 0):.0f}x
  global gap.
</div>

<!-- ===== 2. THE LEAGUE TABLE ===== -->
<h2>2. The League Table: All 30 African Countries</h2>
<div class="legend">
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--green);"></div>
    Research hub (&ge;10/M)
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--yellow);"></div>
    Moderate (3-10/M)
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--orange);"></div>
    Desert (1-3/M)
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--red);"></div>
    Void (&lt;1/M)
  </div>
</div>
<div class="card">
<table>
<thead>
<tr>
  <th style="text-align:center;">Rank</th>
  <th>Country</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
  <th>Classification</th>
  <th style="width:20%;">Density</th>
</tr>
</thead>
<tbody>
{league_rows}
</tbody>
</table>
</div>

<!-- ===== 3. GLOBAL COMPARATORS ===== -->
<h2>3. Global Comparators</h2>
<div class="callout">
  {global_rank_note}
</div>
<div class="card">
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
</tr>
</thead>
<tbody>
{comp_rows}
<tr style="border-top:2px solid var(--border);font-style:italic;">
  <td style="padding:10px;">Africa (average)</td>
  <td style="padding:10px;text-align:right;">{results["africa_total_pop"]:.0f}M</td>
  <td style="padding:10px;text-align:right;">{results["africa_total_trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:var(--orange);font-weight:bold;">
    {africa_avg}</td>
</tr>
</tbody>
</table>
</div>

<!-- ===== 4. THE VOID COUNTRIES ===== -->
<h2>4. The Void Countries (&lt;1 Trial per Million)</h2>
<div class="callout callout-red">
  {len(results["void_countries"])} countries with a combined population of
  <strong>{void_pop:.0f}M</strong> people have fewer than 1 interventional
  trial per million residents.
</div>
<div class="card">
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
  <th>Trial Ratio</th>
</tr>
</thead>
<tbody>
{void_rows}
</tbody>
</table>
</div>

<!-- ===== 5. INCOME CORRELATION ===== -->
<h2>5. Income Correlation: GDP per Capita vs Trials per Million</h2>
<div class="callout">
  {correlation_str}
</div>
<div class="card">
<h3>By Income Group</h3>
<table>
<thead>
<tr>
  <th>Income Group</th>
  <th style="text-align:right;">Countries</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
</tr>
</thead>
<tbody>
{income_rows}
</tbody>
</table>
</div>

<div class="card">
<h3>Country-Level GDP vs Trial Density</h3>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">GDP/Capita</th>
  <th style="text-align:right;">Trials/M</th>
  <th>Income Group</th>
</tr>
</thead>
<tbody>
{gdp_rows}
</tbody>
</table>
</div>

<!-- ===== 6. REGIONAL BREAKDOWN ===== -->
<h2>6. Regional Breakdown</h2>
<div class="card">
<table>
<thead>
<tr>
  <th>Region</th>
  <th style="text-align:right;">Countries</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
</tr>
</thead>
<tbody>
{region_rows}
</tbody>
</table>
</div>

<!-- ===== 7. POPULATION-WEIGHTED INJUSTICE ===== -->
<h2>7. Population-Weighted Injustice</h2>
<div class="callout callout-red">
  <strong>{void_desert_pct}%</strong> of Africa's population
  ({void_desert_pop:.0f}M of {total_pop:.0f}M people) live in
  "Void" or "Desert" countries with fewer than 3 trials per million.
</div>
<div class="injustice-bar">
  <div style="width:{hub_pop / total_pop * 100:.1f}%;background:var(--green);"
    title="Research hub: {hub_pop:.0f}M">Hub {hub_pop:.0f}M</div>
  <div style="width:{moderate_pop / total_pop * 100:.1f}%;background:var(--yellow);"
    title="Moderate: {moderate_pop:.0f}M">Mod {moderate_pop:.0f}M</div>
  <div style="width:{desert_pop / total_pop * 100:.1f}%;background:var(--orange);"
    title="Desert: {desert_pop:.0f}M">Desert {desert_pop:.0f}M</div>
  <div style="width:{void_pop / total_pop * 100:.1f}%;background:var(--red);"
    title="Void: {void_pop:.0f}M">Void {void_pop:.0f}M</div>
</div>
<div class="legend">
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--green);"></div>
    Hub: {hub_n} countries, {hub_pop:.0f}M ({hub_pop / total_pop * 100:.1f}%)
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--yellow);"></div>
    Moderate: {moderate_n} countries, {moderate_pop:.0f}M ({moderate_pop / total_pop * 100:.1f}%)
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--orange);"></div>
    Desert: {desert_n} countries, {desert_pop:.0f}M ({desert_pop / total_pop * 100:.1f}%)
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:var(--red);"></div>
    Void: {void_n} countries, {void_pop:.0f}M ({void_pop / total_pop * 100:.1f}%)
  </div>
</div>

<!-- ===== 8. TREND POTENTIAL ===== -->
<h2>8. Trend Potential: Countries with Highest Growth Gap</h2>
<p style="color:var(--muted);font-size:0.9rem;margin-bottom:12px;">
  Countries with GDP per capita above $800 but trial density below 5/M.
  Growth Gap Score = GDP per capita / trials per million (higher = more
  untapped potential for trial infrastructure investment).
</p>
<div class="card">
<table>
<thead>
<tr>
  <th style="text-align:center;">#</th>
  <th>Country</th>
  <th style="text-align:right;">GDP/Capita</th>
  <th style="text-align:right;">Trials/M</th>
  <th style="text-align:right;">Growth Gap Score</th>
</tr>
</thead>
<tbody>
{growth_rows}
</tbody>
</table>
</div>

<!-- ===== FOOTER ===== -->
<div class="footer">
  <p>Per-Capita Trial Density League Table | Data: ClinicalTrials.gov API v2
  (interventional trials only) |
  Population: 2025 estimates | GDP: 2024 estimates |
  Generated {datetime.now().strftime("%d %B %Y %H:%M")}</p>
  <p style="margin-top:8px;">
    Part of the <strong>AfricaRCT</strong> project |
    E156 micro-publication pipeline
  </p>
</div>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Entry point."""
    print("=" * 60)
    print("Per-Capita Trial Density League Table")
    print("=" * 60)

    # Fetch data
    data = fetch_all_data()

    # Analyze
    print("\nAnalyzing data...")
    results = analyze_data(data)

    # Summary to console
    print("\n--- LEAGUE TABLE (Top 10) ---")
    for e in results["african_league"][:10]:
        print(f"  #{e['rank']:>2} {e['display_name']:<25} "
              f"{e['per_million']:>7.2f}/M  [{e['classification']}]")

    print(f"\n--- VOID COUNTRIES ({len(results['void_countries'])}) ---")
    for e in results["void_countries"]:
        print(f"  {e['display_name']:<25} {e['per_million']:>7.2f}/M  "
              f"({e['trials']} trials, {e['population_m']}M pop)")

    print(f"\nAfrica average: {results['africa_avg_per_million']}/M")
    print(f"Population in Void/Desert: {results['void_desert_pct']}% "
          f"({results['void_desert_pop']:.0f}M)")

    if results["gdp_correlation"] is not None:
        print(f"GDP-trials Spearman rho: {results['gdp_correlation']}")

    # Generate HTML
    print("\nGenerating HTML dashboard...")
    html = generate_html(data, results)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved to {OUTPUT_HTML}")
    print(f"File size: {OUTPUT_HTML.stat().st_size:,} bytes")
    print("\nDone.")


if __name__ == "__main__":
    main()
