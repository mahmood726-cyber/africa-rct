#!/usr/bin/env python
"""
fetch_global_south.py - Africa vs the Developing World: A Global South Comparison
=================================================================================
The first systematic comparison of Africa's clinical trial landscape against
other developing regions (Latin America, South/Southeast Asia) with high-income
comparators.

Usage:
    python fetch_global_south.py

Outputs:
    data/global_south_data.json       (cached API results, 24h TTL)
    global-south-comparison.html      (dark-theme interactive dashboard)

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
CACHE_FILE = DATA_DIR / "global_south_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "global-south-comparison.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Country data: name -> (population_M, region, income_group, gdp_per_capita)
# ---------------------------------------------------------------------------

# Latin America
LATIN_AMERICA = {
    "Brazil":     (216, "Latin America", "Upper-middle", 10200),
    "Mexico":     (130, "Latin America", "Upper-middle", 11500),
    "Argentina":  (46,  "Latin America", "Upper-middle", 13500),
    "Colombia":   (52,  "Latin America", "Upper-middle", 6600),
    "Peru":       (34,  "Latin America", "Upper-middle", 7000),
    "Chile":      (20,  "Latin America", "High", 16800),
}

# South/Southeast Asia
SOUTH_SE_ASIA = {
    "India":       (1430, "South/SE Asia", "Lower-middle", 2500),
    "Thailand":    (72,   "South/SE Asia", "Upper-middle", 7200),
    "Indonesia":   (280,  "South/SE Asia", "Upper-middle", 4800),
    "Philippines": (117,  "South/SE Asia", "Lower-middle", 3600),
    "Vietnam":     (100,  "South/SE Asia", "Lower-middle", 4300),
    "Bangladesh":  (173,  "South/SE Asia", "Lower-middle", 2800),
    "Pakistan":    (240,  "South/SE Asia", "Lower-middle", 1600),
}

# Africa (top 6 + bottom 3 + a few more for breadth)
AFRICA = {
    "Egypt":         (110, "Africa", "Lower-middle", 4300),
    "South Africa":  (62,  "Africa", "Upper-middle", 6500),
    "Uganda":        (48,  "Africa", "Low", 960),
    "Kenya":         (56,  "Africa", "Lower-middle", 2100),
    "Tanzania":      (67,  "Africa", "Lower-middle", 1200),
    "Nigeria":       (230, "Africa", "Lower-middle", 1600),
    "Ethiopia":      (130, "Africa", "Low", 1020),
    "Ghana":         (34,  "Africa", "Lower-middle", 2300),
    "Somalia":       (18,  "Africa", "Low", 450),
    "South Sudan":   (11,  "Africa", "Low", 420),
    "Chad":          (18,  "Africa", "Low", 700),
    "Rwanda":        (14,  "Africa", "Low", 960),
}

# High-income comparators
HIGH_INCOME = {
    "United States":  (335, "High-income", "High", 80000),
    "United Kingdom": (68,  "High-income", "High", 48000),
}

# Verified trial counts (from CT.gov, to use as fallback / validation)
VERIFIED_COUNTS = {
    "Brazil": 9890, "Mexico": 6847, "Argentina": 4019,
    "Colombia": 1872, "Peru": 1726,
    "India": 5388, "Thailand": 3483, "Indonesia": 1299,
    "Philippines": 1192, "Vietnam": 932, "Bangladesh": 649, "Pakistan": 4641,
    "Egypt": 12395, "South Africa": 3473, "Uganda": 783,
    "Kenya": 720, "Tanzania": 431, "Nigeria": 354,
    "Somalia": 8, "South Sudan": 6, "Chad": 14,
    "United States": 159196, "United Kingdom": 22049,
}

# Merge all countries into one dict for iteration
ALL_COUNTRIES = {}
ALL_COUNTRIES.update(LATIN_AMERICA)
ALL_COUNTRIES.update(SOUTH_SE_ASIA)
ALL_COUNTRIES.update(AFRICA)
ALL_COUNTRIES.update(HIGH_INCOME)

# Conditions to query by region
CONDITIONS = ["HIV", "cancer", "diabetes", "cardiovascular"]

# Phases for distribution analysis
PHASE_MAP = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
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


def get_trial_count(location, condition=None, phase=None):
    """Return total count of interventional trials for a location."""
    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if phase:
        phase_label = PHASE_MAP.get(phase, phase)
        filters.append(f"AREA[Phase]{phase_label}")

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
    """Fetch trial counts for all countries, conditions, and phases."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "country_counts": {},
        "condition_counts": {},
        "phase_counts": {},
    }

    # ---------- Total trial counts per country ----------
    total_calls = len(ALL_COUNTRIES)
    call_num = 0
    print("\n=== Querying total trial counts ===")
    for country in ALL_COUNTRIES:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country}...")
        count = get_trial_count(country)
        data["country_counts"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    # ---------- Condition-specific counts by region ----------
    # Query conditions for representative countries per region
    region_reps = {
        "Africa": ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda", "Ethiopia"],
        "Latin America": ["Brazil", "Mexico", "Argentina", "Colombia", "Peru"],
        "South/SE Asia": ["India", "Thailand", "Indonesia", "Philippines", "Pakistan"],
    }

    print("\n=== Querying condition-specific counts ===")
    for region, countries in region_reps.items():
        data["condition_counts"][region] = {}
        for cond in CONDITIONS:
            total_cond = 0
            for country in countries:
                print(f"  {region} / {cond} / {country}...")
                count = get_trial_count(country, condition=cond)
                total_cond += count
                time.sleep(RATE_LIMIT)
            data["condition_counts"][region][cond] = total_cond
            print(f"    -> {region} {cond}: {total_cond:,}")

    # ---------- Phase distribution by region ----------
    print("\n=== Querying phase distribution ===")
    for region, countries in region_reps.items():
        data["phase_counts"][region] = {}
        for phase_key in PHASE_MAP:
            total_phase = 0
            for country in countries:
                print(f"  {region} / {phase_key} / {country}...")
                count = get_trial_count(country, phase=phase_key)
                total_phase += count
                time.sleep(RATE_LIMIT)
            data["phase_counts"][region][phase_key] = total_phase
            print(f"    -> {region} {phase_key}: {total_phase:,}")

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

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


def analyze_data(data):
    """Compute all rankings and derived metrics."""
    results = {}

    # -- Build unified league table --
    league = []
    for country, (pop, region, income, gdp) in ALL_COUNTRIES.items():
        trials = data["country_counts"].get(country, VERIFIED_COUNTS.get(country, 0))
        per_m = round(trials / pop, 1) if pop > 0 else 0
        league.append({
            "country": country,
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
            "region": region,
            "income_group": income,
            "gdp_per_capita": gdp,
        })

    league.sort(key=lambda x: -x["per_million"])
    for i, entry in enumerate(league):
        entry["rank"] = i + 1

    results["league"] = league

    # -- Regional averages --
    region_stats = defaultdict(lambda: {"trials": 0, "pop": 0, "countries": 0,
                                         "per_million_vals": []})
    for e in league:
        r = e["region"]
        region_stats[r]["trials"] += e["trials"]
        region_stats[r]["pop"] += e["population_m"]
        region_stats[r]["countries"] += 1
        region_stats[r]["per_million_vals"].append(e["per_million"])

    for r in region_stats:
        s = region_stats[r]
        s["weighted_per_million"] = round(s["trials"] / s["pop"], 1) if s["pop"] > 0 else 0
        s["mean_per_million"] = round(
            sum(s["per_million_vals"]) / len(s["per_million_vals"]), 1
        ) if s["per_million_vals"] else 0
        s["median_per_million"] = round(_median(s["per_million_vals"]), 1)

    results["region_stats"] = dict(region_stats)

    # -- Africa Penalty --
    dev_regions = ["Latin America", "South/SE Asia"]
    dev_vals = []
    for r in dev_regions:
        if r in region_stats:
            dev_vals.append(region_stats[r]["weighted_per_million"])

    dev_avg = sum(dev_vals) / len(dev_vals) if dev_vals else 0
    africa_avg = region_stats.get("Africa", {}).get("weighted_per_million", 0)
    results["dev_world_avg"] = round(dev_avg, 1)
    results["africa_avg"] = africa_avg
    if africa_avg > 0:
        results["africa_penalty"] = round(dev_avg / africa_avg, 1)
    else:
        results["africa_penalty"] = None

    # -- Income group analysis --
    income_stats = defaultdict(lambda: {"trials": 0, "pop": 0, "countries": 0})
    for e in league:
        ig = e["income_group"]
        income_stats[ig]["trials"] += e["trials"]
        income_stats[ig]["pop"] += e["population_m"]
        income_stats[ig]["countries"] += 1

    for ig in income_stats:
        s = income_stats[ig]
        s["per_million"] = round(s["trials"] / s["pop"], 1) if s["pop"] > 0 else 0

    results["income_stats"] = dict(income_stats)

    # -- Nigeria vs India comparison --
    nigeria = next((e for e in league if e["country"] == "Nigeria"), None)
    india = next((e for e in league if e["country"] == "India"), None)
    results["nigeria_india"] = {
        "nigeria": nigeria,
        "india": india,
        "ratio": round(india["per_million"] / nigeria["per_million"], 1) if (
            nigeria and india and nigeria["per_million"] > 0
        ) else None,
    }

    # -- South Africa as Africa's Brazil --
    sa = next((e for e in league if e["country"] == "South Africa"), None)
    brazil = next((e for e in league if e["country"] == "Brazil"), None)
    results["sa_brazil"] = {"south_africa": sa, "brazil": brazil}

    # -- GDP vs trials correlation --
    gdps = [e["gdp_per_capita"] for e in league if e["gdp_per_capita"] > 0
            and e["region"] not in ("High-income",)]
    pms = [e["per_million"] for e in league if e["gdp_per_capita"] > 0
           and e["region"] not in ("High-income",)]
    if len(gdps) >= 3:
        results["gdp_correlation"] = _spearman_rank(gdps, pms)
    else:
        results["gdp_correlation"] = None

    # -- Condition portfolio --
    results["condition_counts"] = data.get("condition_counts", {})

    # -- Phase distribution --
    results["phase_counts"] = data.get("phase_counts", {})

    # -- Latin America success metrics --
    latam_entries = [e for e in league if e["region"] == "Latin America"]
    results["latam_entries"] = sorted(latam_entries, key=lambda x: -x["per_million"])

    # -- Africa entries sorted --
    africa_entries = [e for e in league if e["region"] == "Africa"]
    results["africa_entries"] = sorted(africa_entries, key=lambda x: -x["per_million"])

    # -- Asia entries sorted --
    asia_entries = [e for e in league if e["region"] == "South/SE Asia"]
    results["asia_entries"] = sorted(asia_entries, key=lambda x: -x["per_million"])

    return results


def _median(vals):
    """Compute median of a list."""
    if not vals:
        return 0
    s = sorted(vals)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


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


def region_color(region):
    """Return display color for each region."""
    return {
        "Africa": "#ef4444",
        "Latin America": "#22c55e",
        "South/SE Asia": "#3b82f6",
        "High-income": "#9ca3af",
    }.get(region, "#888")


def region_bg(region):
    """Return background color for each region."""
    return {
        "Africa": "rgba(239,68,68,0.10)",
        "Latin America": "rgba(34,197,94,0.10)",
        "South/SE Asia": "rgba(59,130,246,0.10)",
        "High-income": "rgba(156,163,175,0.10)",
    }.get(region, "transparent")


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard."""

    league = results["league"]
    rs = results["region_stats"]

    # ====================================================================
    # 1. SUMMARY STATS
    # ====================================================================
    africa_avg = results["africa_avg"]
    dev_avg = results["dev_world_avg"]
    penalty = results["africa_penalty"]
    us_pm = next((e["per_million"] for e in league if e["country"] == "United States"), 0)
    latam_avg = rs.get("Latin America", {}).get("weighted_per_million", 0)
    asia_avg = rs.get("South/SE Asia", {}).get("weighted_per_million", 0)

    # ====================================================================
    # 2. GLOBAL LEAGUE TABLE
    # ====================================================================
    max_pm = max(e["per_million"] for e in league) if league else 1
    if max_pm < 1:
        max_pm = 1

    league_rows = ""
    for e in league:
        rc = region_color(e["region"])
        rb = region_bg(e["region"])
        bar_width = min(e["per_million"] / max_pm * 100, 100)
        league_rows += f"""<tr style="background:{rb};">
  <td style="padding:10px;text-align:center;font-weight:bold;color:{rc};">
    {e["rank"]}</td>
  <td style="padding:10px;font-weight:bold;">{escape_html(e["country"])}</td>
  <td style="padding:10px;text-align:center;">
    <span style="color:{rc};font-size:0.75rem;font-weight:bold;">
      {escape_html(e["region"])}</span></td>
  <td style="padding:10px;text-align:right;">{e["population_m"]:,}M</td>
  <td style="padding:10px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:{rc};font-weight:bold;">
    {e["per_million"]}</td>
  <td style="padding:10px;text-align:right;">{escape_html(e["income_group"])}</td>
  <td style="padding:10px;min-width:120px;">
    <div style="background:rgba(255,255,255,0.08);border-radius:4px;height:18px;
      width:100%;position:relative;">
      <div style="background:{rc};height:100%;width:{bar_width:.1f}%;
        border-radius:4px;"></div>
    </div></td>
</tr>
"""

    # ====================================================================
    # 3. REGIONAL AVERAGES BAR CHART (CSS)
    # ====================================================================
    region_order = ["Africa", "Latin America", "South/SE Asia", "High-income"]
    region_bars = ""
    bar_max = max(
        (rs.get(r, {}).get("weighted_per_million", 0) for r in region_order), default=1
    )
    if bar_max < 1:
        bar_max = 1

    for r in region_order:
        s = rs.get(r, {})
        wpm = s.get("weighted_per_million", 0)
        mpm = s.get("mean_per_million", 0)
        n = s.get("countries", 0)
        bw = min(wpm / bar_max * 100, 100)
        rc = region_color(r)
        region_bars += f"""
<div style="margin-bottom:16px;">
  <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
    <span style="font-weight:bold;color:{rc};">{escape_html(r)} ({n} countries)</span>
    <span style="color:var(--muted);">weighted {wpm}/M | mean {mpm}/M</span>
  </div>
  <div style="background:rgba(255,255,255,0.08);border-radius:6px;height:28px;width:100%;">
    <div style="background:{rc};height:100%;width:{bw:.1f}%;border-radius:6px;
      display:flex;align-items:center;padding-left:8px;">
      <span style="color:#fff;font-weight:bold;font-size:0.8rem;">{wpm}/M</span>
    </div>
  </div>
</div>"""

    # ====================================================================
    # 4. NIGERIA VS INDIA
    # ====================================================================
    ni = results["nigeria_india"]
    nigeria_data = ni["nigeria"] or {}
    india_data = ni["india"] or {}
    ni_ratio = ni.get("ratio", "N/A")

    # ====================================================================
    # 5. LATIN AMERICA SUCCESS
    # ====================================================================
    latam_rows = ""
    for e in results.get("latam_entries", []):
        latam_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(e["country"])}</td>
  <td style="padding:10px;text-align:right;">{e["population_m"]:,}M</td>
  <td style="padding:10px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:#22c55e;font-weight:bold;">{e["per_million"]}</td>
  <td style="padding:10px;text-align:right;">{escape_html(e["income_group"])}</td>
  <td style="padding:10px;text-align:right;">${e["gdp_per_capita"]:,}</td>
</tr>
"""

    # ====================================================================
    # 6. SOUTH AFRICA AS AFRICA'S BRAZIL
    # ====================================================================
    sb = results["sa_brazil"]
    sa_data = sb.get("south_africa", {}) or {}
    br_data = sb.get("brazil", {}) or {}

    # ====================================================================
    # 7. DISEASE PORTFOLIO
    # ====================================================================
    cond_data = results.get("condition_counts", {})
    cond_regions = ["Africa", "Latin America", "South/SE Asia"]
    cond_rows = ""
    for cond in CONDITIONS:
        cond_rows += f"<tr><td style='padding:10px;font-weight:bold;'>{escape_html(cond.title())}</td>"
        for cr in cond_regions:
            val = cond_data.get(cr, {}).get(cond, 0)
            cond_rows += f"<td style='padding:10px;text-align:right;'>{val:,}</td>"
        cond_rows += "</tr>\n"

    # Compute proportions for narrative
    cond_totals = {}
    for cr in cond_regions:
        t = sum(cond_data.get(cr, {}).get(c, 0) for c in CONDITIONS)
        cond_totals[cr] = t if t > 0 else 1

    cond_pct_rows = ""
    for cond in CONDITIONS:
        cond_pct_rows += f"<tr><td style='padding:10px;font-weight:bold;'>{escape_html(cond.title())}</td>"
        for cr in cond_regions:
            val = cond_data.get(cr, {}).get(cond, 0)
            pct = round(val / cond_totals[cr] * 100, 1)
            cond_pct_rows += f"<td style='padding:10px;text-align:right;'>{pct}%</td>"
        cond_pct_rows += "</tr>\n"

    # ====================================================================
    # 8. INCOME VS TRIALS
    # ====================================================================
    income_order = ["Low", "Lower-middle", "Upper-middle", "High"]
    income_rows = ""
    for ig in income_order:
        s = results["income_stats"].get(ig, {"trials": 0, "pop": 0, "countries": 0, "per_million": 0})
        income_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(ig)}</td>
  <td style="padding:10px;text-align:right;">{s["countries"]}</td>
  <td style="padding:10px;text-align:right;">{s["pop"]:.0f}M</td>
  <td style="padding:10px;text-align:right;">{s["trials"]:,}</td>
  <td style="padding:10px;text-align:right;color:#60a5fa;font-weight:bold;">{s["per_million"]}</td>
</tr>
"""

    # GDP scatter table
    developing = [e for e in league if e["region"] != "High-income"]
    developing.sort(key=lambda x: -x["gdp_per_capita"])
    gdp_rows = ""
    for e in developing:
        rc = region_color(e["region"])
        gdp_rows += f"""<tr>
  <td style="padding:8px;color:{rc};">{escape_html(e["country"])}</td>
  <td style="padding:8px;text-align:center;color:{rc};font-size:0.75rem;">
    {escape_html(e["region"])}</td>
  <td style="padding:8px;text-align:right;">${e["gdp_per_capita"]:,}</td>
  <td style="padding:8px;text-align:right;color:{rc};font-weight:bold;">
    {e["per_million"]}</td>
</tr>
"""

    corr = results.get("gdp_correlation")
    if corr is not None:
        strength = "strong" if abs(corr) >= 0.6 else ("moderate" if abs(corr) >= 0.3 else "weak")
        direction = "positive" if corr > 0 else "negative"
        corr_str = f"Spearman rho = {corr} ({strength} {direction} correlation)"
    else:
        corr_str = "Insufficient data for correlation"

    # ====================================================================
    # 9. PHASE DISTRIBUTION
    # ====================================================================
    phase_data = results.get("phase_counts", {})
    phase_rows = ""
    phase_labels = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]
    phase_display = {
        "EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1",
        "PHASE2": "Phase 2", "PHASE3": "Phase 3", "PHASE4": "Phase 4"
    }

    for pk in phase_labels:
        phase_rows += f"<tr><td style='padding:10px;font-weight:bold;'>{phase_display[pk]}</td>"
        for cr in cond_regions:
            val = phase_data.get(cr, {}).get(pk, 0)
            phase_rows += f"<td style='padding:10px;text-align:right;'>{val:,}</td>"
        phase_rows += "</tr>\n"

    # Phase proportions
    phase_totals = {}
    for cr in cond_regions:
        t = sum(phase_data.get(cr, {}).get(pk, 0) for pk in phase_labels)
        phase_totals[cr] = t if t > 0 else 1

    phase_pct_rows = ""
    for pk in phase_labels:
        phase_pct_rows += f"<tr><td style='padding:10px;font-weight:bold;'>{phase_display[pk]}</td>"
        for cr in cond_regions:
            val = phase_data.get(cr, {}).get(pk, 0)
            pct = round(val / phase_totals[cr] * 100, 1)
            highlight = " color:#f59e0b;font-weight:bold;" if pk == "PHASE3" else ""
            phase_pct_rows += f"<td style='padding:10px;text-align:right;{highlight}'>{pct}%</td>"
        phase_pct_rows += "</tr>\n"

    # ====================================================================
    # FULL HTML
    # ====================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa vs the Developing World: A Global South Comparison</title>
<style>
  :root {{
    --bg: #0a0e17;
    --surface: #151b2b;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #ef4444;
    --green: #22c55e;
    --blue: #3b82f6;
    --amber: #f59e0b;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    line-height: 1.6; padding: 20px; max-width: 1400px; margin: 0 auto;
  }}
  h1 {{
    font-size: 2rem; margin-bottom: 8px;
    background: linear-gradient(135deg, #ef4444, #22c55e, #3b82f6);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  h2 {{
    font-size: 1.4rem; margin: 40px 0 16px 0; color: var(--text);
    border-bottom: 2px solid var(--border); padding-bottom: 8px;
  }}
  h3 {{
    font-size: 1.1rem; margin: 24px 0 12px 0; color: var(--muted);
  }}
  .subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 24px; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 24px; margin-bottom: 24px;
  }}
  .stat-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 24px;
  }}
  .stat-box {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px; text-align: center;
  }}
  .stat-box .label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase;
    letter-spacing: 0.05em; margin-bottom: 4px; }}
  .stat-box .value {{ font-size: 2rem; font-weight: bold; }}
  table {{
    width: 100%; border-collapse: collapse; margin-bottom: 16px;
  }}
  th {{
    background: rgba(255,255,255,0.05); padding: 12px 10px;
    text-align: left; font-size: 0.8rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 2px solid var(--border);
  }}
  td {{ border-bottom: 1px solid var(--border); }}
  tr:hover {{ background: rgba(255,255,255,0.03); }}
  .narrative {{
    background: rgba(239,68,68,0.06); border-left: 4px solid var(--accent);
    padding: 16px 20px; border-radius: 0 8px 8px 0; margin: 16px 0;
    color: var(--text); font-size: 0.95rem;
  }}
  .narrative.green {{
    background: rgba(34,197,94,0.06); border-left-color: var(--green);
  }}
  .narrative.blue {{
    background: rgba(59,130,246,0.06); border-left-color: var(--blue);
  }}
  .narrative.amber {{
    background: rgba(245,158,11,0.06); border-left-color: var(--amber);
  }}
  .legend {{
    display: flex; gap: 20px; flex-wrap: wrap; margin: 12px 0;
    font-size: 0.85rem;
  }}
  .legend span {{
    display: flex; align-items: center; gap: 6px;
  }}
  .legend .dot {{
    width: 12px; height: 12px; border-radius: 50%; display: inline-block;
  }}
  .comparison-grid {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
  }}
  @media (max-width: 768px) {{
    .comparison-grid {{ grid-template-columns: 1fr; }}
    .stat-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
  .footer {{
    margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border);
    color: var(--muted); font-size: 0.8rem; text-align: center;
  }}
</style>
</head>
<body>

<h1>Africa vs the Developing World</h1>
<p class="subtitle">The first systematic comparison of clinical trial density across the Global South
| Data: ClinicalTrials.gov API v2 | Generated: {datetime.now().strftime("%d %B %Y")}</p>

<!-- ================================================================ -->
<!-- 1. SUMMARY -->
<!-- ================================================================ -->
<h2>1. The Global Picture</h2>

<div class="stat-grid">
  <div class="stat-box">
    <div class="label">Africa Average</div>
    <div class="value" style="color:#ef4444;">{africa_avg}/M</div>
  </div>
  <div class="stat-box">
    <div class="label">Latin America Average</div>
    <div class="value" style="color:#22c55e;">{latam_avg}/M</div>
  </div>
  <div class="stat-box">
    <div class="label">South/SE Asia Average</div>
    <div class="value" style="color:#3b82f6;">{asia_avg}/M</div>
  </div>
  <div class="stat-box">
    <div class="label">United States</div>
    <div class="value" style="color:#9ca3af;">{us_pm}/M</div>
  </div>
  <div class="stat-box">
    <div class="label">Developing World Avg</div>
    <div class="value" style="color:#f59e0b;">{dev_avg}/M</div>
  </div>
  <div class="stat-box">
    <div class="label">Africa Penalty</div>
    <div class="value" style="color:#ef4444;">{penalty}x</div>
    <div style="color:var(--muted);font-size:0.75rem;">Dev world avg / Africa avg</div>
  </div>
</div>

<div class="narrative">
<strong>The Africa Penalty:</strong> The average developing-world country has <strong>{penalty}x</strong>
more clinical trials per capita than the average African country. Africa's weighted average of
<strong>{africa_avg} trials/M</strong> lags behind Latin America ({latam_avg}/M) and
South/SE Asia ({asia_avg}/M). The US ({us_pm}/M) is in another universe entirely.
</div>

<!-- ================================================================ -->
<!-- 2. GLOBAL LEAGUE TABLE -->
<!-- ================================================================ -->
<h2>2. The Global League Table</h2>

<p style="color:var(--muted);margin-bottom:12px;">
All ~25 countries ranked by interventional trials per million population.
Colored by region.</p>

<div class="legend">
  <span><span class="dot" style="background:#ef4444;"></span> Africa</span>
  <span><span class="dot" style="background:#22c55e;"></span> Latin America</span>
  <span><span class="dot" style="background:#3b82f6;"></span> South/SE Asia</span>
  <span><span class="dot" style="background:#9ca3af;"></span> High-income</span>
</div>

<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th style="text-align:center;">Rank</th>
  <th>Country</th>
  <th style="text-align:center;">Region</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
  <th style="text-align:right;">Income</th>
  <th>Density</th>
</tr></thead>
<tbody>
{league_rows}
</tbody>
</table>
</div>

<!-- ================================================================ -->
<!-- 3. REGIONAL AVERAGES -->
<!-- ================================================================ -->
<h2>3. Regional Averages Comparison</h2>

<div class="card">
{region_bars}
</div>

<!-- ================================================================ -->
<!-- 4. THE NIGERIA VS INDIA PROBLEM -->
<!-- ================================================================ -->
<h2>4. The Nigeria vs India Problem</h2>

<div class="narrative amber">
<strong>Why does this matter?</strong> Nigeria (230M, lower-middle-income) and India
(1,430M, lower-middle-income) are both massive, economically similar developing nations.
Yet India manages <strong>{india_data.get("per_million", "N/A")}/M</strong> while Nigeria
achieves only <strong>{nigeria_data.get("per_million", "N/A")}/M</strong> &mdash;
a <strong>{ni_ratio}x</strong> gap.
</div>

<div class="comparison-grid">
  <div class="card" style="border-left:4px solid #ef4444;">
    <h3 style="color:#ef4444;">Nigeria</h3>
    <table>
      <tr><td style="padding:8px;color:var(--muted);">Population</td>
          <td style="padding:8px;text-align:right;">{nigeria_data.get("population_m", "N/A")}M</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Trials</td>
          <td style="padding:8px;text-align:right;">{nigeria_data.get("trials", 0):,}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Per Million</td>
          <td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">
            {nigeria_data.get("per_million", "N/A")}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Income Group</td>
          <td style="padding:8px;text-align:right;">Lower-middle</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">GDP/capita</td>
          <td style="padding:8px;text-align:right;">$1,600</td></tr>
    </table>
  </div>
  <div class="card" style="border-left:4px solid #3b82f6;">
    <h3 style="color:#3b82f6;">India</h3>
    <table>
      <tr><td style="padding:8px;color:var(--muted);">Population</td>
          <td style="padding:8px;text-align:right;">{india_data.get("population_m", "N/A")}M</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Trials</td>
          <td style="padding:8px;text-align:right;">{india_data.get("trials", 0):,}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Per Million</td>
          <td style="padding:8px;text-align:right;color:#3b82f6;font-weight:bold;">
            {india_data.get("per_million", "N/A")}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Income Group</td>
          <td style="padding:8px;text-align:right;">Lower-middle</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">GDP/capita</td>
          <td style="padding:8px;text-align:right;">$2,500</td></tr>
    </table>
  </div>
</div>

<div class="card">
<h3>Possible Explanations</h3>
<ul style="padding-left:20px;color:var(--muted);">
  <li><strong>Regulatory framework:</strong> India's CDSCO, despite criticism, provides a
    relatively clear pathway for trial registration. Nigeria's NAFDAC has historically been
    slower to process approvals.</li>
  <li><strong>Pharma infrastructure:</strong> India has a massive generic pharmaceutical
    industry that generates domestic clinical research capacity.</li>
  <li><strong>Academic research culture:</strong> India's ICMR-funded research ecosystem
    and IIT/AIIMS networks create domestic trial generation at scale.</li>
  <li><strong>CRO presence:</strong> Major CROs have deep Indian operations; African CRO
    presence is largely limited to South Africa and Egypt.</li>
</ul>
</div>

<!-- ================================================================ -->
<!-- 5. THE LATIN AMERICA SUCCESS -->
<!-- ================================================================ -->
<h2>5. The Latin America Success</h2>

<div class="narrative green">
<strong>What did they do differently?</strong> Brazil (45.8/M), Mexico (52.7/M), and
Argentina (87.4/M) all achieve trial densities that would place them in Africa's top
tier. The entire Latin America region averages <strong>{latam_avg}/M</strong> &mdash;
multiples above Africa's {africa_avg}/M.
</div>

<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th>Country</th><th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th><th style="text-align:right;">Per Million</th>
  <th style="text-align:right;">Income</th><th style="text-align:right;">GDP/capita</th>
</tr></thead>
<tbody>
{latam_rows}
</tbody>
</table>
</div>

<div class="card">
<h3>Success Factors</h3>
<ul style="padding-left:20px;color:var(--muted);">
  <li><strong>ANVISA (Brazil) and ANMAT (Argentina):</strong> Regulatory agencies that
    balance rigour with efficiency, making them attractive to pharma sponsors.</li>
  <li><strong>Strong public hospital networks:</strong> Brazil's SUS and Argentina's
    public hospitals provide large patient pools with electronic records.</li>
  <li><strong>LATAM CRO networks:</strong> Well-established regional CRO presence
    (Parexel, IQVIA, PPD) with deep local expertise.</li>
  <li><strong>Upper-middle income:</strong> Higher GDP enables better research
    infrastructure, trained workforce, and regulatory capacity.</li>
  <li><strong>Disease burden alignment:</strong> NCDs (cancer, cardiovascular, diabetes)
    match pharma's therapeutic priorities, attracting industry funding.</li>
</ul>
</div>

<!-- ================================================================ -->
<!-- 6. SOUTH AFRICA AS AFRICA'S BRAZIL -->
<!-- ================================================================ -->
<h2>6. South Africa as Africa's Brazil</h2>

<div class="narrative blue">
<strong>The sole African competitor:</strong> South Africa at
<strong>{sa_data.get("per_million", "N/A")}/M</strong> is the ONLY African country
that can compete with Latin American nations. Brazil achieves
<strong>{br_data.get("per_million", "N/A")}/M</strong>. Both are upper-middle-income
with strong regulatory agencies (SAHPRA vs ANVISA).
</div>

<div class="comparison-grid">
  <div class="card" style="border-left:4px solid #ef4444;">
    <h3 style="color:#ef4444;">South Africa</h3>
    <table>
      <tr><td style="padding:8px;color:var(--muted);">Population</td>
          <td style="padding:8px;text-align:right;">{sa_data.get("population_m", "N/A")}M</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Trials</td>
          <td style="padding:8px;text-align:right;">{sa_data.get("trials", 0):,}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Per Million</td>
          <td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">
            {sa_data.get("per_million", "N/A")}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Income</td>
          <td style="padding:8px;text-align:right;">Upper-middle ($6,500)</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Regulatory body</td>
          <td style="padding:8px;text-align:right;">SAHPRA</td></tr>
    </table>
  </div>
  <div class="card" style="border-left:4px solid #22c55e;">
    <h3 style="color:#22c55e;">Brazil</h3>
    <table>
      <tr><td style="padding:8px;color:var(--muted);">Population</td>
          <td style="padding:8px;text-align:right;">{br_data.get("population_m", "N/A")}M</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Trials</td>
          <td style="padding:8px;text-align:right;">{br_data.get("trials", 0):,}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Per Million</td>
          <td style="padding:8px;text-align:right;color:#22c55e;font-weight:bold;">
            {br_data.get("per_million", "N/A")}</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Income</td>
          <td style="padding:8px;text-align:right;">Upper-middle ($10,200)</td></tr>
      <tr><td style="padding:8px;color:var(--muted);">Regulatory body</td>
          <td style="padding:8px;text-align:right;">ANVISA</td></tr>
    </table>
  </div>
</div>

<div class="card">
<h3>The Gap Between SA and the Rest of Africa</h3>
<p style="color:var(--muted);">
South Africa's {sa_data.get("per_million", "N/A")}/M makes it competitive with Latin America,
but it is an island of capability. The next African countries (Egypt aside, which is inflated
by small university studies) are Uganda ({next((e["per_million"] for e in results["africa_entries"] if e["country"] == "Uganda"), "N/A")}/M)
and Kenya ({next((e["per_million"] for e in results["africa_entries"] if e["country"] == "Kenya"), "N/A")}/M)
&mdash; a massive drop. No other African country even approaches South Africa's density when
Egypt is excluded.
</p>
</div>

<!-- ================================================================ -->
<!-- 7. DISEASE PORTFOLIO -->
<!-- ================================================================ -->
<h2>7. Disease Portfolio Comparison</h2>

<div class="narrative amber">
<strong>Africa is HIV-heavy, Latin America is cancer-heavy, Asia is mixed.</strong>
The disease portfolio reveals how colonial and donor legacies shape what gets studied where.
</div>

<h3>Absolute Trial Counts by Condition</h3>
<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th>Condition</th>
  <th style="text-align:right;color:#ef4444;">Africa</th>
  <th style="text-align:right;color:#22c55e;">Latin America</th>
  <th style="text-align:right;color:#3b82f6;">South/SE Asia</th>
</tr></thead>
<tbody>
{cond_rows}
</tbody>
</table>
</div>

<h3>Portfolio Share (% of condition trials within region)</h3>
<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th>Condition</th>
  <th style="text-align:right;color:#ef4444;">Africa %</th>
  <th style="text-align:right;color:#22c55e;">Latin America %</th>
  <th style="text-align:right;color:#3b82f6;">South/SE Asia %</th>
</tr></thead>
<tbody>
{cond_pct_rows}
</tbody>
</table>
</div>

<div class="card">
<h3>Interpretation</h3>
<ul style="padding-left:20px;color:var(--muted);">
  <li><strong>Africa's HIV dominance:</strong> Reflects PEPFAR/Global Fund investment.
    This is both a success (HIV research saves lives) and a trap (other diseases are neglected).</li>
  <li><strong>Latin America's cancer focus:</strong> Upper-middle-income NCD burden plus
    pharma oncology pipelines create a cancer-research ecosystem.</li>
  <li><strong>Asia's balance:</strong> India and Thailand have diverse disease burdens
    and research capacity spanning infectious and non-communicable diseases.</li>
</ul>
</div>

<!-- ================================================================ -->
<!-- 8. INCOME VS TRIALS -->
<!-- ================================================================ -->
<h2>8. Income Group Analysis: GDP per Capita vs Trials per Million</h2>

<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th>Income Group</th><th style="text-align:right;">Countries</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">Trials</th>
  <th style="text-align:right;">Per Million</th>
</tr></thead>
<tbody>
{income_rows}
</tbody>
</table>
</div>

<h3>GDP per Capita vs Trial Density (Developing Countries Only)</h3>
<p style="color:var(--muted);margin-bottom:8px;">{corr_str}</p>

<div class="card" style="overflow-x:auto;max-height:500px;overflow-y:auto;">
<table>
<thead><tr>
  <th>Country</th><th style="text-align:center;">Region</th>
  <th style="text-align:right;">GDP/capita</th>
  <th style="text-align:right;">Trials/M</th>
</tr></thead>
<tbody>
{gdp_rows}
</tbody>
</table>
</div>

<div class="narrative">
<strong>Money matters, but it's not everything.</strong> Income explains some of the variance,
but countries like Argentina ($13,500 GDP/capita, 87.4/M) vastly outperform Nigeria ($1,600, 1.5/M)
and even India ($2,500, 3.8/M) despite similar-ish income levels. Regulatory capacity,
CRO infrastructure, and research culture are the multipliers.
</div>

<!-- ================================================================ -->
<!-- 9. PHASE DISTRIBUTION -->
<!-- ================================================================ -->
<h2>9. Phase Distribution: Does Africa Have More Phase 3?</h2>

<div class="narrative amber">
<strong>The outsourcing hypothesis:</strong> If Africa is primarily a testing ground for
drugs developed elsewhere, we would expect a disproportionate share of Phase 3 trials
(confirmatory testing) and fewer Phase 1/2 (early development).
</div>

<h3>Absolute Phase Counts by Region</h3>
<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th>Phase</th>
  <th style="text-align:right;color:#ef4444;">Africa</th>
  <th style="text-align:right;color:#22c55e;">Latin America</th>
  <th style="text-align:right;color:#3b82f6;">South/SE Asia</th>
</tr></thead>
<tbody>
{phase_rows}
</tbody>
</table>
</div>

<h3>Phase Proportions (%)</h3>
<div class="card" style="overflow-x:auto;">
<table>
<thead><tr>
  <th>Phase</th>
  <th style="text-align:right;color:#ef4444;">Africa %</th>
  <th style="text-align:right;color:#22c55e;">Latin America %</th>
  <th style="text-align:right;color:#3b82f6;">South/SE Asia %</th>
</tr></thead>
<tbody>
{phase_pct_rows}
</tbody>
</table>
</div>

<div class="card">
<h3>Interpretation</h3>
<ul style="padding-left:20px;color:var(--muted);">
  <li>A higher Phase 3 share in Africa would suggest the continent serves primarily
    as a testing ground for drugs developed in high-income countries.</li>
  <li>A lower Phase 1 share indicates fewer early-development studies, consistent
    with limited pharmaceutical R&amp;D infrastructure.</li>
  <li>Latin America's distribution reflects both domestic pharma innovation and
    large-scale multinational Phase 3 programs.</li>
</ul>
</div>

<!-- ================================================================ -->
<!-- FOOTER -->
<!-- ================================================================ -->
<div class="footer">
  <p>Africa vs the Developing World: A Global South Comparison</p>
  <p>Data source: ClinicalTrials.gov API v2 | Population: 2025 estimates |
    GDP: World Bank 2024 estimates</p>
  <p>Generated: {datetime.now().strftime("%d %B %Y %H:%M")} |
    Cache: data/global_south_data.json (24h TTL)</p>
  <p style="margin-top:8px;">Part of the AfricaRCT project &mdash;
    mapping the clinical trial landscape across Africa</p>
</div>

</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Main entry point."""
    print("=" * 70)
    print("Africa vs the Developing World: A Global South Comparison")
    print("=" * 70)

    # Fetch data
    data = fetch_all_data()

    # Analyze
    print("\nAnalysing data...")
    results = analyze_data(data)

    # Summary
    print(f"\n--- Summary ---")
    print(f"  Africa avg:        {results['africa_avg']}/M")
    print(f"  Dev world avg:     {results['dev_world_avg']}/M")
    print(f"  Africa Penalty:    {results['africa_penalty']}x")

    rs = results["region_stats"]
    for r in ["Africa", "Latin America", "South/SE Asia", "High-income"]:
        s = rs.get(r, {})
        print(f"  {r}: weighted={s.get('weighted_per_million', 0)}/M, "
              f"mean={s.get('mean_per_million', 0)}/M, "
              f"n={s.get('countries', 0)}")

    ni = results["nigeria_india"]
    if ni.get("ratio"):
        print(f"\n  Nigeria vs India: {ni['ratio']}x gap")

    corr = results.get("gdp_correlation")
    if corr is not None:
        print(f"  GDP-trials Spearman rho = {corr}")

    # Generate HTML
    print("\nGenerating HTML dashboard...")
    html = generate_html(data, results)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved to {OUTPUT_HTML}")
    print(f"File size: {len(html):,} bytes")

    print("\nDone.")


if __name__ == "__main__":
    main()
