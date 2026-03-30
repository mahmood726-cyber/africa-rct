#!/usr/bin/env python
"""
fetch_precision_audit.py -- The Ar-Rahman Precision Audit
==========================================================
Inspired by Surah Ar-Rahman (55:5-9): "The sun and the moon follow courses
exactly computed. And the stars and the trees both prostrate. And the sky He
raised and He set up the Balance -- that you may not transgress the balance.
Establish weight in justice and do not make deficient the balance."

If divine creation follows exact computation, so should research measurement.
This project audits the PRECISION of every key number in the programme --
are our CCIs, per-capitas, and scores as exact as they can be?

For each of 10 CCI conditions:
  - Point estimate of CCI (Africa burden share vs trial share)
  - Sensitivity: CCI with +/-20% variation in WHO burden estimates
  - CCI via location="Africa" keyword vs sum-of-top-10-countries (100 queries)
  - Robustness classification: robust / fragile / uncertain

Also audits per-capita calculations: multiple population sources compared.

Usage:
    python fetch_precision_audit.py

Outputs:
    data/precision_audit_data.json  -- cached API data (24h TTL)
    precision-audit.html            -- sensitivity tornado plots, robustness

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import math
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config -------------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_FILE = DATA_DIR / "precision_audit_data.json"
OUTPUT_HTML = SCRIPT_DIR.parent / "precision-audit.html"
RATE_LIMIT_DELAY = 0.4
CACHE_TTL_HOURS = 24
MAX_RETRIES = 3

# -- 10 CCI Conditions -------------------------------------------------------
CONDITIONS = [
    "HIV", "tuberculosis", "malaria", "cancer", "diabetes",
    "cardiovascular", "hypertension", "mental health", "stroke", "sickle cell",
]

# WHO Global Burden of Disease -- Africa's approximate share of global
# mortality/DALY burden (WHO GBD 2024 estimates, %)
WHO_BURDEN_PCT = {
    "HIV":              60.0,
    "tuberculosis":     25.0,
    "malaria":          96.0,
    "cancer":           10.0,
    "diabetes":         25.0,
    "cardiovascular":   24.0,
    "hypertension":     38.0,
    "mental health":    20.0,
    "stroke":           32.0,
    "sickle cell":      75.0,
}

# Top 10 African countries by trial volume (for sum-of-countries approach)
TOP10_COUNTRIES = [
    "South Africa", "Egypt", "Kenya", "Uganda", "Nigeria",
    "Tanzania", "Ethiopia", "Ghana", "Morocco", "Tunisia",
]

# Population estimates from multiple sources (millions)
# Source 1: UN World Population Prospects 2024
# Source 2: World Bank WDI 2024
# Source 3: CIA World Factbook 2024
POP_SOURCES = {
    "South Africa": {"UN 2024": 62.0,  "World Bank 2024": 61.5, "CIA Factbook 2024": 60.4},
    "Egypt":        {"UN 2024": 110.0, "World Bank 2024": 109.3,"CIA Factbook 2024": 107.8},
    "Kenya":        {"UN 2024": 56.0,  "World Bank 2024": 55.1, "CIA Factbook 2024": 55.9},
    "Uganda":       {"UN 2024": 48.0,  "World Bank 2024": 47.2, "CIA Factbook 2024": 47.7},
    "Nigeria":      {"UN 2024": 230.0, "World Bank 2024": 223.8,"CIA Factbook 2024": 225.1},
    "Tanzania":     {"UN 2024": 67.0,  "World Bank 2024": 65.5, "CIA Factbook 2024": 64.7},
    "Ethiopia":     {"UN 2024": 130.0, "World Bank 2024": 126.5,"CIA Factbook 2024": 127.0},
    "Ghana":        {"UN 2024": 34.0,  "World Bank 2024": 33.5, "CIA Factbook 2024": 33.1},
    "Morocco":      {"UN 2024": 38.0,  "World Bank 2024": 37.5, "CIA Factbook 2024": 37.1},
    "Tunisia":      {"UN 2024": 12.0,  "World Bank 2024": 12.0, "CIA Factbook 2024": 11.9},
}

# Africa total population (millions)
AFRICA_POP_SOURCES = {
    "UN 2024":           1460.0,
    "World Bank 2024":   1430.0,
    "CIA Factbook 2024": 1440.0,
}


# -- API helper ---------------------------------------------------------------
def api_query(location=None, condition=None, study_type="INTERVENTIONAL",
              page_size=0, count_total=True):
    """Query CT.gov API v2 and return response JSON."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": str(count_total).lower(),
    }
    filters = []
    if study_type:
        filters.append(f"AREA[StudyType]{study_type}")
    if filters:
        params["filter.advanced"] = " AND ".join(filters)
    if condition:
        params["query.cond"] = condition
    if location:
        params["query.locn"] = location

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  WARNING: API error for {location}/{condition}: {e}")
                return {"totalCount": 0, "studies": []}


def get_total(result):
    return result.get("totalCount", 0)


# -- Cache management ---------------------------------------------------------
def is_cache_valid():
    if not CACHE_FILE.exists():
        return False
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_date = datetime.fromisoformat(data["meta"]["date"].split("+")[0])
        return (datetime.now() - cached_date) < timedelta(hours=CACHE_TTL_HOURS)
    except (json.JSONDecodeError, KeyError, ValueError):
        return False


def load_cache():
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Cache saved to {CACHE_FILE}")


# -- CCI computation ----------------------------------------------------------
def compute_cci(africa_trials, global_trials, burden_pct):
    """
    CCI = Clinical Complexity Index.
    Ratio of Africa's trial share to Africa's burden share.
    CCI = (africa_trials / global_trials) / (burden_pct / 100)
    CCI < 1 = under-researched relative to burden
    CCI > 1 = over-researched relative to burden
    """
    if global_trials == 0 or burden_pct == 0:
        return 0.0
    trial_share = africa_trials / global_trials
    burden_share = burden_pct / 100
    return round(trial_share / burden_share, 4)


# -- Data collection -----------------------------------------------------------
def collect_all_data():
    """Collect trial counts for all 10 conditions via multiple methods."""
    print("=" * 60)
    print("THE AR-RAHMAN PRECISION AUDIT -- Data Collection")
    print("=" * 60)

    all_data = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
            "conditions": len(CONDITIONS),
            "script": "fetch_precision_audit.py",
        },
        "africa_keyword": {},
        "country_sums": {},
        "global_counts": {},
        "per_country_condition": {},
    }

    # 1. Global counts for each condition
    print("\n[1/4] Fetching GLOBAL trial counts per condition...")
    for cond in CONDITIONS:
        r = api_query(condition=cond)
        count = get_total(r)
        all_data["global_counts"][cond] = count
        print(f"  {cond}: {count:,} global trials")
        time.sleep(RATE_LIMIT_DELAY)

    # 2. Africa-keyword counts for each condition
    print("\n[2/4] Fetching AFRICA-keyword trial counts per condition...")
    for cond in CONDITIONS:
        r = api_query(location="Africa", condition=cond)
        count = get_total(r)
        all_data["africa_keyword"][cond] = count
        print(f"  {cond}: {count:,} Africa-keyword trials")
        time.sleep(RATE_LIMIT_DELAY)

    # 3. Sum-of-top-10-countries counts (100 queries)
    print("\n[3/4] Fetching per-country-per-condition counts (100 queries)...")
    for country in TOP10_COUNTRIES:
        all_data["per_country_condition"][country] = {}
        for cond in CONDITIONS:
            r = api_query(location=country, condition=cond)
            count = get_total(r)
            all_data["per_country_condition"][country][cond] = count
            time.sleep(RATE_LIMIT_DELAY)
        country_total = sum(all_data["per_country_condition"][country].values())
        print(f"  {country}: {country_total:,} total across 10 conditions")

    # Compute sum-of-countries totals
    for cond in CONDITIONS:
        country_sum = sum(
            all_data["per_country_condition"][c].get(cond, 0) for c in TOP10_COUNTRIES
        )
        all_data["country_sums"][cond] = country_sum

    # 4. Per-capita data: total trials per country
    print("\n[4/4] Fetching total trials per country for per-capita audit...")
    all_data["country_totals"] = {}
    for country in TOP10_COUNTRIES:
        r = api_query(location=country)
        count = get_total(r)
        all_data["country_totals"][country] = count
        print(f"  {country}: {count:,} total trials")
        time.sleep(RATE_LIMIT_DELAY)

    # Also get Africa-keyword total
    r = api_query(location="Africa")
    all_data["africa_keyword_total"] = get_total(r)
    print(f"  Africa keyword total: {all_data['africa_keyword_total']:,}")
    time.sleep(RATE_LIMIT_DELAY)

    # Global total
    r = api_query()
    all_data["global_total"] = get_total(r)
    print(f"  Global total: {all_data['global_total']:,}")

    return all_data


# -- Analysis ------------------------------------------------------------------
def analyze_precision(data):
    """Compute CCI sensitivity ranges and robustness classifications."""
    analysis = {
        "conditions": {},
        "per_capita": {},
    }

    for cond in CONDITIONS:
        global_count = data["global_counts"].get(cond, 0)
        africa_keyword = data["africa_keyword"].get(cond, 0)
        country_sum = data["country_sums"].get(cond, 0)
        burden_base = WHO_BURDEN_PCT.get(cond, 0)

        # Point estimate using Africa keyword
        cci_keyword = compute_cci(africa_keyword, global_count, burden_base)

        # Point estimate using sum of top 10 countries
        cci_countries = compute_cci(country_sum, global_count, burden_base)

        # Sensitivity: +/-20% variation in burden
        burden_low = burden_base * 0.8
        burden_high = burden_base * 1.2
        cci_burden_low = compute_cci(africa_keyword, global_count, burden_low)
        cci_burden_high = compute_cci(africa_keyword, global_count, burden_high)

        # Sensitivity: countries approach with +/-20% burden
        cci_countries_burden_low = compute_cci(country_sum, global_count, burden_low)
        cci_countries_burden_high = compute_cci(country_sum, global_count, burden_high)

        # All CCI values
        all_ccis = [
            cci_keyword, cci_countries,
            cci_burden_low, cci_burden_high,
            cci_countries_burden_low, cci_countries_burden_high,
        ]
        all_ccis = [c for c in all_ccis if c > 0]

        cci_min = min(all_ccis) if all_ccis else 0
        cci_max = max(all_ccis) if all_ccis else 0
        cci_range = round(cci_max - cci_min, 4)

        # Direction: all < 1 (under-researched), all > 1 (over-researched), or mixed
        all_under = all(c < 1 for c in all_ccis) if all_ccis else False
        all_over = all(c >= 1 for c in all_ccis) if all_ccis else False
        direction_stable = all_under or all_over

        # Robustness classification
        if not all_ccis:
            robustness = "no data"
        elif direction_stable and cci_range < 0.2:
            robustness = "robust"
        elif direction_stable:
            robustness = "uncertain"
        else:
            robustness = "fragile"

        # Keyword vs countries discrepancy
        if africa_keyword > 0 and country_sum > 0:
            method_discrepancy = round(
                abs(africa_keyword - country_sum) / max(africa_keyword, country_sum) * 100, 1
            )
        else:
            method_discrepancy = 0.0

        analysis["conditions"][cond] = {
            "global_count": global_count,
            "africa_keyword_count": africa_keyword,
            "country_sum_count": country_sum,
            "burden_pct": burden_base,
            "cci_keyword": cci_keyword,
            "cci_countries": cci_countries,
            "cci_burden_low": cci_burden_low,
            "cci_burden_high": cci_burden_high,
            "cci_countries_burden_low": cci_countries_burden_low,
            "cci_countries_burden_high": cci_countries_burden_high,
            "cci_min": cci_min,
            "cci_max": cci_max,
            "cci_range": cci_range,
            "robustness": robustness,
            "direction_stable": direction_stable,
            "method_discrepancy_pct": method_discrepancy,
        }

    # Per-capita audit
    for country in TOP10_COUNTRIES:
        total_trials = data["country_totals"].get(country, 0)
        pops = POP_SOURCES.get(country, {})
        percapitas = {}
        for source, pop_m in pops.items():
            if pop_m > 0:
                percapitas[source] = round(total_trials / pop_m, 2)

        vals = list(percapitas.values())
        if vals:
            pc_min = min(vals)
            pc_max = max(vals)
            pc_range = round(pc_max - pc_min, 2)
            pc_pct_range = round((pc_max - pc_min) / max(pc_max, 0.01) * 100, 1)
        else:
            pc_min = pc_max = pc_range = pc_pct_range = 0

        analysis["per_capita"][country] = {
            "total_trials": total_trials,
            "percapita_by_source": percapitas,
            "min": pc_min,
            "max": pc_max,
            "range": pc_range,
            "pct_range": pc_pct_range,
        }

    return analysis


# -- HTML generation -----------------------------------------------------------
def generate_html(analysis, raw_data):
    """Generate the Precision Audit HTML dashboard."""
    date_str = datetime.now().strftime("%d %B %Y")

    conditions = analysis["conditions"]
    per_capita = analysis["per_capita"]

    # Summary stats
    robust_count = sum(1 for c in conditions.values() if c["robustness"] == "robust")
    fragile_count = sum(1 for c in conditions.values() if c["robustness"] == "fragile")
    uncertain_count = sum(1 for c in conditions.values() if c["robustness"] == "uncertain")

    # Build tornado chart data (sorted by CCI range)
    sorted_conds = sorted(conditions.items(), key=lambda x: x[1]["cci_range"], reverse=True)

    # Build tornado bars
    tornado_rows = ""
    for cond, info in sorted_conds:
        rob_class = info["robustness"]
        rob_color = {"robust": "#27ae60", "fragile": "#e74c3c", "uncertain": "#f39c12",
                     "no data": "#888"}[rob_class]
        rob_icon = {"robust": "&#10004;", "fragile": "&#10008;", "uncertain": "&#9888;",
                    "no data": "&#8212;"}[rob_class]

        # Tornado bar: low to high
        cci_low = info["cci_min"]
        cci_high = info["cci_max"]
        cci_point = info["cci_keyword"]

        # Scale: 0 to 2.0 (CCI range)
        scale_max = 2.0
        bar_left_pct = min(cci_low / scale_max * 100, 100)
        bar_width_pct = min((cci_high - cci_low) / scale_max * 100, 100)
        point_pct = min(cci_point / scale_max * 100, 100)
        # CCI=1 reference line
        ref_pct = (1.0 / scale_max) * 100

        tornado_rows += f"""
        <div class="tornado-row">
          <div class="tornado-label">{cond}
            <span class="rob-badge" style="background:{rob_color};">{rob_icon} {rob_class}</span>
          </div>
          <div class="tornado-bar-container">
            <div class="tornado-ref" style="left:{ref_pct}%;"></div>
            <div class="tornado-bar" style="left:{bar_left_pct}%;width:{max(bar_width_pct, 0.5)}%;
                 background:linear-gradient(90deg, rgba(231,76,60,0.4), rgba(39,174,96,0.4));
                 border:1px solid {rob_color};"></div>
            <div class="tornado-point" style="left:{point_pct}%;"></div>
            <div class="tornado-val-low" style="left:{bar_left_pct}%;">{cci_low:.3f}</div>
            <div class="tornado-val-high" style="left:{bar_left_pct + bar_width_pct}%;">{cci_high:.3f}</div>
          </div>
        </div>"""

    # Build condition detail cards
    detail_cards = ""
    for cond, info in sorted_conds:
        rob_class = info["robustness"]
        rob_color = {"robust": "#27ae60", "fragile": "#e74c3c", "uncertain": "#f39c12",
                     "no data": "#888"}[rob_class]

        per_country_detail = ""
        country_cond_data = raw_data.get("per_country_condition", {})
        for country in TOP10_COUNTRIES:
            count = country_cond_data.get(country, {}).get(cond, 0)
            per_country_detail += f"<span class='country-chip'>{country}: {count:,}</span> "

        direction_text = "Under-researched (CCI &lt; 1)" if info["direction_stable"] and info["cci_keyword"] < 1 \
            else "Over-researched (CCI &ge; 1)" if info["direction_stable"] \
            else "Direction unstable across methods"

        detail_cards += f"""
        <div class="detail-card">
          <h3 style="color:{rob_color};border-bottom:2px solid {rob_color};padding-bottom:8px;">
            {cond} <span class="rob-badge" style="background:{rob_color};font-size:0.8em;">
            {rob_class.upper()}</span>
          </h3>
          <div class="detail-grid">
            <div class="detail-item">
              <div class="detail-label">Global trials</div>
              <div class="detail-value">{info['global_count']:,}</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">Africa (keyword)</div>
              <div class="detail-value">{info['africa_keyword_count']:,}</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">Africa (top-10 sum)</div>
              <div class="detail-value">{info['country_sum_count']:,}</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">Method discrepancy</div>
              <div class="detail-value">{info['method_discrepancy_pct']:.1f}%</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">WHO burden share</div>
              <div class="detail-value">{info['burden_pct']:.0f}%</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">CCI (keyword)</div>
              <div class="detail-value">{info['cci_keyword']:.4f}</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">CCI (countries)</div>
              <div class="detail-value">{info['cci_countries']:.4f}</div>
            </div>
            <div class="detail-item">
              <div class="detail-label">CCI range</div>
              <div class="detail-value">{info['cci_min']:.4f} &ndash; {info['cci_max']:.4f}</div>
            </div>
          </div>
          <p style="margin-top:10px;color:var(--muted);font-size:0.9em;">
            <strong>Direction:</strong> {direction_text}<br>
            <strong>Sensitivity width:</strong> {info['cci_range']:.4f}
          </p>
          <div style="margin-top:8px;font-size:0.85em;">
            <strong>Per-country breakdown:</strong><br>{per_country_detail}
          </div>
        </div>"""

    # Build per-capita audit table
    percapita_rows = ""
    for country in TOP10_COUNTRIES:
        pc = per_capita.get(country, {})
        percapitas = pc.get("percapita_by_source", {})
        cells = ""
        for source in ["UN 2024", "World Bank 2024", "CIA Factbook 2024"]:
            val = percapitas.get(source, 0)
            cells += f"<td>{val:.2f}</td>"
        pct_range = pc.get("pct_range", 0)
        range_color = "#27ae60" if pct_range < 5 else "#f39c12" if pct_range < 10 else "#e74c3c"
        percapita_rows += f"""
        <tr>
          <td style="font-weight:600;">{country}</td>
          <td>{pc.get('total_trials', 0):,}</td>
          {cells}
          <td style="color:{range_color};font-weight:600;">{pct_range:.1f}%</td>
        </tr>"""

    # Publishable findings
    publishable = [c for c, info in conditions.items() if info["robustness"] == "robust"]
    caveated = [c for c, info in conditions.items() if info["robustness"] != "robust"]
    publishable_html = ""
    if publishable:
        publishable_html = "<ul>" + "".join(f"<li><strong>{c}</strong> (CCI {conditions[c]['cci_keyword']:.3f})</li>"
                                            for c in publishable) + "</ul>"
    else:
        publishable_html = "<p>No conditions achieved full robustness across all sensitivity tests.</p>"

    caveated_html = ""
    if caveated:
        caveated_html = "<ul>" + "".join(
            f"<li><strong>{c}</strong> ({conditions[c]['robustness']}; "
            f"range {conditions[c]['cci_min']:.3f}&ndash;{conditions[c]['cci_max']:.3f})</li>"
            for c in caveated) + "</ul>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Ar-Rahman Precision Audit | AfricaRCT</title>
<style>
:root {{
  --bg: #0a0e14;
  --surface: #131820;
  --surface2: #1a2030;
  --gold: #d4af37;
  --gold-dim: rgba(212,175,55,0.15);
  --text: #e8e6e3;
  --muted: #8899aa;
  --green: #27ae60;
  --red: #e74c3c;
  --orange: #f39c12;
  --blue: #3498db;
  --radius: 10px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
       line-height:1.6; }}

.header {{
  text-align:center; padding:48px 20px 32px;
  background:linear-gradient(180deg, rgba(212,175,55,0.08) 0%, transparent 100%);
  border-bottom:1px solid var(--gold-dim);
}}
.header h1 {{ font-size:2.2em; color:var(--gold); margin-bottom:8px; font-weight:700; }}
.header .verse {{
  font-style:italic; color:var(--muted); max-width:800px; margin:12px auto;
  font-size:1.05em; line-height:1.7; border-left:3px solid var(--gold); padding-left:16px;
  text-align:left;
}}
.header .arabic {{
  font-family:'Traditional Arabic','Scheherazade New',serif;
  font-size:1.5em; direction:rtl; color:var(--gold); margin:12px 0;
}}
.header .subtitle {{ color:var(--muted); font-size:1.05em; margin-top:8px; }}

.container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}

.summary-grid {{
  display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));
  gap:16px; margin:24px 0;
}}
.summary-card {{
  background:var(--surface); border-radius:var(--radius); padding:20px; text-align:center;
  border:1px solid rgba(255,255,255,0.06);
}}
.summary-card .big {{ font-size:2.5em; font-weight:700; }}
.summary-card .label {{ color:var(--muted); font-size:0.9em; margin-top:4px; }}

.section {{ margin:36px 0; }}
.section h2 {{
  font-size:1.5em; color:var(--gold); margin-bottom:16px;
  padding-bottom:8px; border-bottom:1px solid var(--gold-dim);
}}
.section h2 .sub {{ font-size:0.65em; color:var(--muted); font-weight:400; }}

/* Tornado chart */
.tornado-row {{ display:flex; align-items:center; margin:10px 0; gap:12px; }}
.tornado-label {{
  width:180px; text-align:right; font-size:0.95em; font-weight:600;
  display:flex; align-items:center; justify-content:flex-end; gap:6px; flex-shrink:0;
}}
.rob-badge {{
  display:inline-block; padding:1px 7px; border-radius:4px; font-size:0.75em;
  color:#fff; font-weight:600;
}}
.tornado-bar-container {{
  flex:1; height:32px; position:relative; background:var(--surface);
  border-radius:4px; overflow:visible;
}}
.tornado-bar {{
  position:absolute; top:4px; height:24px; border-radius:3px; z-index:2;
}}
.tornado-point {{
  position:absolute; top:2px; width:3px; height:28px; background:var(--gold);
  z-index:3; border-radius:2px;
}}
.tornado-ref {{
  position:absolute; top:0; width:2px; height:32px; background:rgba(255,255,255,0.3);
  z-index:1;
}}
.tornado-val-low, .tornado-val-high {{
  position:absolute; top:34px; font-size:0.7em; color:var(--muted); z-index:4;
  transform:translateX(-50%);
}}

/* Detail cards */
.detail-grid-container {{
  display:grid; grid-template-columns:repeat(auto-fit, minmax(520px, 1fr));
  gap:16px; margin:16px 0;
}}
.detail-card {{
  background:var(--surface); border-radius:var(--radius); padding:20px;
  border:1px solid rgba(255,255,255,0.06);
}}
.detail-grid {{
  display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-top:12px;
}}
.detail-item {{ text-align:center; }}
.detail-label {{ font-size:0.75em; color:var(--muted); }}
.detail-value {{ font-size:1.1em; font-weight:600; }}
.country-chip {{
  display:inline-block; background:var(--surface2); padding:2px 8px;
  border-radius:4px; margin:2px; font-size:0.85em;
}}

/* Per-capita table */
table {{ width:100%; border-collapse:collapse; font-size:0.92em; }}
th {{ background:var(--surface2); color:var(--gold); padding:10px 8px; text-align:left;
     font-weight:600; border-bottom:2px solid var(--gold-dim); }}
td {{ padding:8px; border-bottom:1px solid rgba(255,255,255,0.05); }}
tr:hover {{ background:rgba(212,175,55,0.04); }}

.findings-box {{
  background:var(--surface); border-radius:var(--radius); padding:20px;
  border-left:4px solid var(--gold); margin:16px 0;
}}
.findings-box h3 {{ color:var(--gold); margin-bottom:8px; }}
.findings-box ul {{ padding-left:20px; }}
.findings-box li {{ margin:4px 0; }}

.footer {{
  text-align:center; padding:32px; color:var(--muted); font-size:0.85em;
  border-top:1px solid rgba(255,255,255,0.06); margin-top:40px;
}}
</style>
</head>
<body>

<div class="header">
  <div class="arabic">الشَّمْسُ وَالْقَمَرُ بِحُسْبَانٍ</div>
  <h1>The Ar-Rahman Precision Audit</h1>
  <div class="verse">
    "The sun and the moon follow courses exactly computed. And the stars and the trees both
    prostrate. And the sky He raised and He set up the Balance -- that you may not transgress
    the balance. Establish weight in justice and do not make deficient the balance."
    <br>&mdash; Quran 55:5-9 (Surah Ar-Rahman)
  </div>
  <div class="subtitle">
    Every Number Must Be Exact &mdash; Auditing the precision of CCI, per-capita,
    and burden-alignment measurements across Africa
  </div>
</div>

<div class="container">

  <!-- Summary -->
  <div class="summary-grid">
    <div class="summary-card">
      <div class="big" style="color:var(--gold);">10</div>
      <div class="label">CCI Conditions Audited</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--green);">{robust_count}</div>
      <div class="label">Robust (publishable)</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--orange);">{uncertain_count}</div>
      <div class="label">Uncertain (need caveats)</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--red);">{fragile_count}</div>
      <div class="label">Fragile (direction unstable)</div>
    </div>
    <div class="summary-card">
      <div class="big" style="color:var(--blue);">{len(TOP10_COUNTRIES)}</div>
      <div class="label">Countries &times; 3 pop sources</div>
    </div>
  </div>

  <!-- Tornado Chart -->
  <div class="section">
    <h2>CCI Sensitivity Tornado Plot <span class="sub">CCI range across all
    sensitivity tests (burden &plusmn;20%, keyword vs country-sum methods)</span></h2>
    <p style="color:var(--muted);margin-bottom:16px;font-size:0.9em;">
      The gold line marks the point estimate (Africa keyword method).
      The white vertical line marks CCI = 1.0 (parity between trial share and burden share).
      Wider bars = more measurement uncertainty.
    </p>
    {tornado_rows}
  </div>

  <!-- Condition Details -->
  <div class="section">
    <h2>Condition-Level Precision Analysis <span class="sub">
    Africa-keyword vs sum-of-10-countries, burden sensitivity</span></h2>
    <div class="detail-grid-container">
      {detail_cards}
    </div>
  </div>

  <!-- Per-Capita Audit -->
  <div class="section">
    <h2>Per-Capita Population Source Audit <span class="sub">
    Trials per million using UN, World Bank, and CIA Factbook population estimates</span></h2>
    <p style="color:var(--muted);margin-bottom:12px;font-size:0.9em;">
      If per-capita figures vary more than 10% across population sources, findings
      should report the range rather than a single number.
    </p>
    <div style="overflow-x:auto;">
      <table>
        <thead>
          <tr>
            <th>Country</th>
            <th>Total Trials</th>
            <th>Per M (UN)</th>
            <th>Per M (World Bank)</th>
            <th>Per M (CIA)</th>
            <th>Range %</th>
          </tr>
        </thead>
        <tbody>
          {percapita_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Publishability Assessment -->
  <div class="section">
    <h2>Publishability Assessment</h2>
    <div class="findings-box">
      <h3 style="color:var(--green);">Robust Findings (publishable as-is)</h3>
      <p style="color:var(--muted);font-size:0.9em;margin-bottom:8px;">
        CCI direction is stable across all sensitivity tests with narrow range (&lt;0.2).
      </p>
      {publishable_html}
    </div>
    <div class="findings-box" style="border-left-color:var(--orange);">
      <h3 style="color:var(--orange);">Findings Requiring Caveats</h3>
      <p style="color:var(--muted);font-size:0.9em;margin-bottom:8px;">
        Either direction is unstable, range is wide, or method discrepancy is large.
        Report as ranges, not point estimates.
      </p>
      {caveated_html}
    </div>
  </div>

  <!-- Methodology -->
  <div class="section">
    <h2>Methodology</h2>
    <div class="findings-box" style="border-left-color:var(--blue);">
      <p><strong>CCI (Clinical Complexity Index)</strong> = (Africa trial share) / (Africa burden share).<br>
      CCI &lt; 1 means under-researched relative to burden; CCI &gt; 1 means over-researched.</p>

      <p style="margin-top:12px;"><strong>Sensitivity tests applied:</strong></p>
      <ul>
        <li><strong>Burden variation:</strong> WHO burden estimates varied by &plusmn;20% to test CCI stability</li>
        <li><strong>Counting method:</strong> "Africa" keyword search vs sum of 10 individual country queries (100 total)</li>
        <li><strong>Cross-method:</strong> Burden variation applied to both counting methods (6 CCI values per condition)</li>
      </ul>

      <p style="margin-top:12px;"><strong>Robustness classification:</strong></p>
      <ul>
        <li><strong style="color:var(--green);">Robust:</strong> CCI direction stable AND range &lt; 0.2</li>
        <li><strong style="color:var(--orange);">Uncertain:</strong> Direction stable BUT range &ge; 0.2</li>
        <li><strong style="color:var(--red);">Fragile:</strong> CCI crosses 1.0 threshold (direction changes)</li>
      </ul>

      <p style="margin-top:12px;"><strong>Per-capita audit:</strong> Trials per million computed using 3 population
      sources (UN, World Bank, CIA Factbook). Range &gt;10% flags unreliable per-capita claims.</p>
    </div>
  </div>

</div>

<div class="footer">
  <p>The Ar-Rahman Precision Audit &mdash; AfricaRCT Programme</p>
  <p>Data: ClinicalTrials.gov API v2 | Generated {date_str}</p>
  <p style="margin-top:8px;font-style:italic;">
    "Establish weight in justice and do not make deficient the balance" (55:9)
  </p>
</div>

</body>
</html>"""

    return html


# -- Main ----------------------------------------------------------------------
def main():
    print()
    print("=" * 60)
    print("  THE AR-RAHMAN PRECISION AUDIT")
    print("  Every Number Must Be Exact")
    print("=" * 60)

    # Check cache
    if is_cache_valid():
        print("\n  Using cached data (< 24h old)")
        raw_data = load_cache()
    else:
        print("\n  Fetching fresh data from ClinicalTrials.gov API v2...")
        raw_data = collect_all_data()
        save_cache(raw_data)

    # Analyze precision
    print("\n\nAnalyzing measurement precision...")
    analysis = analyze_precision(raw_data)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"{'PRECISION AUDIT RESULTS':^60}")
    print(f"{'=' * 60}")
    print(f"\n{'Condition':<20}{'CCI (kw)':<12}{'CCI (sum)':<12}{'Range':<10}{'Robustness'}")
    print("-" * 64)
    for cond in CONDITIONS:
        info = analysis["conditions"][cond]
        rob = info["robustness"]
        print(f"  {cond:<18}{info['cci_keyword']:<12.4f}{info['cci_countries']:<12.4f}"
              f"{info['cci_range']:<10.4f}{rob}")

    robust_n = sum(1 for c in analysis["conditions"].values() if c["robustness"] == "robust")
    fragile_n = sum(1 for c in analysis["conditions"].values() if c["robustness"] == "fragile")
    print(f"\n  Robust: {robust_n}/10  |  Fragile: {fragile_n}/10")

    print(f"\n{'Per-Capita Population Source Audit':^60}")
    print("-" * 64)
    for country in TOP10_COUNTRIES:
        pc = analysis["per_capita"][country]
        print(f"  {country:<20} range: {pc['pct_range']:.1f}% "
              f"({pc['min']:.2f} - {pc['max']:.2f} trials/M)")

    # Generate HTML
    print("\n\nGenerating HTML dashboard...")
    html = generate_html(analysis, raw_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written to {OUTPUT_HTML}")
    print(f"  File size: {os.path.getsize(OUTPUT_HTML):,} bytes")

    print(f"\n{'=' * 60}")
    print("Done. Open precision-audit.html in a browser to view the dashboard.")


if __name__ == "__main__":
    main()
