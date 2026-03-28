"""
COVID's Lasting Impact on Africa's Clinical Trial Capacity
===========================================================
Queries ClinicalTrials.gov API v2 to analyze temporal trends in African
clinical trials before, during, and after COVID-19.

Usage:
    python fetch_covid_impact.py

Output:
    data/covid_impact_data.json     — cached API responses (24h TTL)
    covid-impact-africa.html        — interactive dark-theme dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import sys
import time
import math
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "covid_impact_data.json"
OUTPUT_HTML = Path(__file__).parent / "covid-impact-africa.html"
CACHE_TTL_HOURS = 24

YEARS = list(range(2015, 2027))  # 2015-2026
CONDITION_YEARS = list(range(2018, 2026))  # 2018-2025 for condition drill-down

CONDITIONS = {
    "HIV": "HIV",
    "Malaria": "malaria",
    "Tuberculosis": "tuberculosis",
    "Cancer": "cancer",
    "Cardiovascular": "cardiovascular",
}

RATE_LIMIT_DELAY = 0.5  # seconds between API calls
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds between retries


# ── API helpers ──────────────────────────────────────────────────────
def query_ct_gov(location=None, condition=None, year=None,
                 study_type="INTERVENTIONAL", page_size=1,
                 count_total=True):
    """Query CT.gov API v2 and return parsed JSON."""
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": str(count_total).lower(),
    }

    # Build filter.advanced
    filters = []
    if study_type:
        filters.append(f"AREA[StudyType]{study_type}")
    if year is not None:
        filters.append(
            f"AREA[StartDate]RANGE[{year}-01-01,{year}-12-31]"
        )
    if filters:
        params["filter.advanced"] = " AND ".join(filters)

    if location:
        params["query.locn"] = location
    if condition:
        params["query.cond"] = condition

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                print(f"    Retry {attempt + 1}/{MAX_RETRIES} after error: {e}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  WARNING: API error (location={location}, "
                      f"cond={condition}, year={year}): {e}")
                return {"totalCount": 0, "studies": []}


def get_total(result):
    """Extract total count from API response."""
    return result.get("totalCount", 0)


def rate_wait():
    """Polite rate-limiting delay."""
    time.sleep(RATE_LIMIT_DELAY)


# ── Data collection ──────────────────────────────────────────────────
def collect_all_data():
    """Run all queries and return structured results dict."""
    results = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
            "description": "COVID impact on Africa's clinical trial capacity",
        },
        "africa_by_year": {},
        "us_by_year": {},
        "africa_covid_by_year": {},
        "condition_by_year": {},  # {condition: {year: count}}
    }

    total_queries = (
        len(YEARS) * 2  # Africa + US by year
        + 3             # COVID-specific years (2020-2022)
        + len(CONDITIONS) * len(CONDITION_YEARS)  # condition drill-down
    )
    query_num = 0

    # ── 1. Africa trials by year (2015-2026) ─────────────────────
    print("\n[1/4] Querying Africa trials by year (2015-2026)...")
    for yr in YEARS:
        query_num += 1
        print(f"  ({query_num}/{total_queries}) Africa {yr}...")
        r = query_ct_gov(location="Africa", year=yr)
        results["africa_by_year"][str(yr)] = get_total(r)
        rate_wait()

    # ── 2. US trials by year (2015-2026) ─────────────────────────
    print("\n[2/4] Querying US trials by year (2015-2026)...")
    for yr in YEARS:
        query_num += 1
        print(f"  ({query_num}/{total_queries}) US {yr}...")
        r = query_ct_gov(location="United States", year=yr)
        results["us_by_year"][str(yr)] = get_total(r)
        rate_wait()

    # ── 3. COVID-specific trials in Africa (2020-2022) ───────────
    print("\n[3/4] Querying COVID-specific trials in Africa...")
    for yr in [2020, 2021, 2022]:
        query_num += 1
        print(f"  ({query_num}/{total_queries}) COVID Africa {yr}...")
        r = query_ct_gov(location="Africa", condition="COVID-19", year=yr)
        results["africa_covid_by_year"][str(yr)] = get_total(r)
        rate_wait()

    # ── 4. Condition-specific trials in Africa (2018-2025) ───────
    print("\n[4/4] Querying condition-specific trials in Africa...")
    for cond_name, cond_query in CONDITIONS.items():
        results["condition_by_year"][cond_name] = {}
        for yr in CONDITION_YEARS:
            query_num += 1
            print(f"  ({query_num}/{total_queries}) {cond_name} Africa {yr}...")
            r = query_ct_gov(
                location="Africa", condition=cond_query, year=yr
            )
            results["condition_by_year"][cond_name][str(yr)] = get_total(r)
            rate_wait()

    return results


# ── Analysis ─────────────────────────────────────────────────────────
def linear_regression(xs, ys):
    """Simple OLS: returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return (0, ys[0] if ys else 0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return (0, mean_y)
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    return (slope, intercept)


def compute_analytics(data):
    """Compute all derived analytics from raw data."""
    analytics = {}

    africa = data["africa_by_year"]
    us = data["us_by_year"]

    # 1. Pre-COVID trend line (2015-2019)
    pre_years = [2015, 2016, 2017, 2018, 2019]
    pre_counts = [africa.get(str(y), 0) for y in pre_years]
    slope, intercept = linear_regression(pre_years, pre_counts)
    analytics["pre_covid_trend"] = {
        "slope": round(slope, 2),
        "intercept": round(intercept, 2),
        "avg_2015_2019": round(sum(pre_counts) / len(pre_counts), 1),
    }

    # Projected values (what would have happened without COVID)
    projected = {}
    for yr in YEARS:
        projected[str(yr)] = round(slope * yr + intercept, 1)
    analytics["projected"] = projected

    # 2. COVID disruption (2020-2021 deviation from trend)
    disruption = {}
    for yr in [2020, 2021]:
        actual = africa.get(str(yr), 0)
        expected = slope * yr + intercept
        deviation = actual - expected
        deviation_pct = (deviation / expected * 100) if expected > 0 else 0
        disruption[str(yr)] = {
            "actual": actual,
            "expected": round(expected, 1),
            "deviation": round(deviation, 1),
            "deviation_pct": round(deviation_pct, 1),
        }
    analytics["covid_disruption"] = disruption

    # 3. Post-COVID recovery (2022-2025 vs pre-COVID trajectory)
    recovery = {}
    for yr in [2022, 2023, 2024, 2025]:
        actual = africa.get(str(yr), 0)
        expected = slope * yr + intercept
        recovery_pct = (actual / expected * 100) if expected > 0 else 0
        recovery[str(yr)] = {
            "actual": actual,
            "expected": round(expected, 1),
            "recovery_pct": round(recovery_pct, 1),
        }
    analytics["post_covid_recovery"] = recovery

    # Overall recovery metric: average of 2023-2025 actual vs expected
    recent_actual = sum(africa.get(str(y), 0) for y in [2023, 2024, 2025])
    recent_expected = sum(slope * y + intercept for y in [2023, 2024, 2025])
    analytics["overall_recovery_pct"] = (
        round(recent_actual / recent_expected * 100, 1)
        if recent_expected > 0 else 0
    )

    # 4. Displacement Index: did COVID trials crowd out non-COVID research?
    covid_counts = data.get("africa_covid_by_year", {})
    total_covid = sum(covid_counts.get(str(y), 0) for y in [2020, 2021, 2022])
    analytics["covid_trial_surge"] = {
        "total_2020_2022": total_covid,
        "by_year": {str(y): covid_counts.get(str(y), 0) for y in [2020, 2021, 2022]},
    }

    # Displacement: compare non-COVID trials in 2020-2021 to baseline
    cond_data = data.get("condition_by_year", {})
    displacement = {}
    for cond_name in CONDITIONS:
        cond_years = cond_data.get(cond_name, {})
        baseline = sum(cond_years.get(str(y), 0) for y in [2018, 2019]) / 2
        covid_period = sum(cond_years.get(str(y), 0) for y in [2020, 2021]) / 2
        post_covid = sum(cond_years.get(str(y), 0) for y in [2022, 2023]) / 2
        displacement[cond_name] = {
            "baseline_avg": round(baseline, 1),
            "covid_avg": round(covid_period, 1),
            "post_covid_avg": round(post_covid, 1),
            "displacement_pct": (
                round((covid_period - baseline) / baseline * 100, 1)
                if baseline > 0 else 0
            ),
            "recovery_pct": (
                round(post_covid / baseline * 100, 1)
                if baseline > 0 else 0
            ),
        }
    analytics["displacement"] = displacement

    # 5. Africa vs US comparison (normalized to 2019=100)
    africa_2019 = africa.get("2019", 1) or 1
    us_2019 = us.get("2019", 1) or 1
    normalized = {"africa": {}, "us": {}}
    for yr in YEARS:
        a = africa.get(str(yr), 0)
        u = us.get(str(yr), 0)
        normalized["africa"][str(yr)] = round(a / africa_2019 * 100, 1)
        normalized["us"][str(yr)] = round(u / us_2019 * 100, 1)
    analytics["normalized_index"] = normalized

    # 6. Projection: when will Africa return to pre-COVID trajectory?
    # Find current growth from most recent years
    recent_years = [2022, 2023, 2024, 2025]
    recent_counts = [africa.get(str(y), 0) for y in recent_years]
    if len(recent_counts) >= 2 and all(c > 0 for c in recent_counts):
        recent_slope, recent_intercept = linear_regression(
            recent_years, recent_counts
        )
        # Find intersection with pre-COVID trend
        # slope*yr + intercept = recent_slope*yr + recent_intercept
        if abs(recent_slope - slope) > 0.001:
            intersect_year = (
                (intercept - recent_intercept) / (recent_slope - slope)
            )
            analytics["projection"] = {
                "recovery_year": round(intersect_year, 1),
                "current_growth_rate": round(recent_slope, 1),
                "pre_covid_growth_rate": round(slope, 1),
                "converging": recent_slope > slope,
            }
        else:
            analytics["projection"] = {
                "recovery_year": None,
                "current_growth_rate": round(recent_slope, 1),
                "pre_covid_growth_rate": round(slope, 1),
                "converging": False,
                "note": "Parallel trajectories; recovery unlikely on current trend",
            }
    else:
        analytics["projection"] = {
            "recovery_year": None,
            "note": "Insufficient data for projection",
        }

    # 7. Summary statistics
    pre_avg = analytics["pre_covid_trend"]["avg_2015_2019"]
    peak_year = max(
        [str(y) for y in YEARS],
        key=lambda y: africa.get(y, 0)
    )
    peak_val = africa.get(peak_year, 0)
    latest_val = africa.get("2025", 0)
    analytics["summary"] = {
        "pre_covid_avg": pre_avg,
        "peak_year": peak_year,
        "peak_value": peak_val,
        "latest_value": latest_val,
        "recovery_from_peak_pct": (
            round(latest_val / peak_val * 100, 1) if peak_val > 0 else 0
        ),
    }

    return analytics


# ── HTML generation ──────────────────────────────────────────────────
def generate_html(data, analytics):
    """Generate the interactive dark-theme dashboard."""
    africa = data["africa_by_year"]
    us = data["us_by_year"]
    projected = analytics["projected"]
    normalized = analytics["normalized_index"]
    covid_surge = analytics["covid_trial_surge"]
    displacement = analytics["displacement"]
    recovery = analytics["post_covid_recovery"]
    summary = analytics["summary"]
    projection = analytics["projection"]
    cond_data = data.get("condition_by_year", {})

    # Prepare chart data
    year_labels = json.dumps(YEARS)
    africa_counts = json.dumps([africa.get(str(y), 0) for y in YEARS])
    projected_counts = json.dumps(
        [projected.get(str(y), 0) for y in YEARS]
    )
    us_counts = json.dumps([us.get(str(y), 0) for y in YEARS])
    norm_africa = json.dumps(
        [normalized["africa"].get(str(y), 0) for y in YEARS]
    )
    norm_us = json.dumps(
        [normalized["us"].get(str(y), 0) for y in YEARS]
    )

    # Condition data for chart
    cond_labels = json.dumps(CONDITION_YEARS)
    cond_datasets = {}
    cond_colors = {
        "HIV": "#e74c3c",
        "Malaria": "#f39c12",
        "Tuberculosis": "#3498db",
        "Cancer": "#9b59b6",
        "Cardiovascular": "#1abc9c",
    }
    for cond_name in CONDITIONS:
        cond_datasets[cond_name] = json.dumps(
            [cond_data.get(cond_name, {}).get(str(y), 0) for y in CONDITION_YEARS]
        )

    # COVID surge data
    covid_years_json = json.dumps([2020, 2021, 2022])
    covid_counts_json = json.dumps(
        [covid_surge["by_year"].get(str(y), 0) for y in [2020, 2021, 2022]]
    )

    # Recovery projection text
    if projection.get("recovery_year") is not None:
        proj_year = projection["recovery_year"]
        if proj_year > 2050:
            proj_text = "At current growth rates, Africa is not projected to return to its pre-COVID trajectory before 2050."
        elif proj_year < 2026:
            proj_text = f"Africa's trial output has already converged with the pre-COVID trajectory (projected intersection: {proj_year:.0f})."
        else:
            proj_text = f"At current growth rates, Africa is projected to return to its pre-COVID trajectory by {proj_year:.0f}."
        converging = projection.get("converging", False)
    else:
        proj_text = projection.get(
            "note", "Insufficient data for projection."
        )
        converging = False

    # Displacement table rows
    disp_rows = ""
    for cond_name, d in displacement.items():
        color_class = "positive" if d["displacement_pct"] > 0 else "negative"
        rec_class = "positive" if d["recovery_pct"] >= 100 else "negative"
        disp_rows += f"""
            <tr>
                <td>{cond_name}</td>
                <td>{d['baseline_avg']:.0f}</td>
                <td>{d['covid_avg']:.0f}</td>
                <td class="{color_class}">{d['displacement_pct']:+.1f}%</td>
                <td>{d['post_covid_avg']:.0f}</td>
                <td class="{rec_class}">{d['recovery_pct']:.1f}%</td>
            </tr>"""

    # Recovery table rows
    rec_rows = ""
    for yr_str, r in recovery.items():
        rec_class = "positive" if r["recovery_pct"] >= 100 else "negative"
        rec_rows += f"""
            <tr>
                <td>{yr_str}</td>
                <td>{r['actual']}</td>
                <td>{r['expected']}</td>
                <td class="{rec_class}">{r['recovery_pct']:.1f}%</td>
            </tr>"""

    # Disruption rows
    disrupt_rows = ""
    for yr_str, d in analytics["covid_disruption"].items():
        sign = "+" if d["deviation"] >= 0 else ""
        color_class = "positive" if d["deviation"] >= 0 else "negative"
        disrupt_rows += f"""
            <tr>
                <td>{yr_str}</td>
                <td>{d['actual']}</td>
                <td>{d['expected']}</td>
                <td class="{color_class}">{sign}{d['deviation']:.0f} ({sign}{d['deviation_pct']:.1f}%)</td>
            </tr>"""

    # Pre-COVID trend info
    pre_trend = analytics["pre_covid_trend"]
    overall_rec = analytics["overall_recovery_pct"]

    # Build condition dataset JS for Chart.js
    cond_dataset_js = ""
    for cond_name in CONDITIONS:
        color = cond_colors.get(cond_name, "#ffffff")
        cond_dataset_js += f"""
                    {{
                        label: '{cond_name}',
                        data: {cond_datasets[cond_name]},
                        borderColor: '{color}',
                        backgroundColor: '{color}33',
                        borderWidth: 2,
                        tension: 0.3,
                        pointRadius: 4,
                    }},"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>COVID's Lasting Impact on Africa's Clinical Trial Capacity</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></{'"<"'[0]}script>
<style>
:root {{
    --bg: #0a0e17;
    --card: #131a2b;
    --border: #1e2d4a;
    --text: #c8d6e5;
    --heading: #f5f6fa;
    --accent: #00b4d8;
    --accent2: #e74c3c;
    --accent3: #f39c12;
    --positive: #2ecc71;
    --negative: #e74c3c;
    --muted: #576574;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    line-height: 1.6;
    padding: 2rem;
}}
h1 {{
    color: var(--heading);
    font-size: 2rem;
    margin-bottom: 0.5rem;
    text-align: center;
}}
h2 {{
    color: var(--heading);
    font-size: 1.3rem;
    margin: 2rem 0 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
}}
h3 {{
    color: var(--accent);
    font-size: 1.1rem;
    margin: 1rem 0 0.5rem;
}}
.subtitle {{
    text-align: center;
    color: var(--muted);
    margin-bottom: 2rem;
    font-size: 0.9rem;
}}
.container {{
    max-width: 1200px;
    margin: 0 auto;
}}
.card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.5rem;
}}
.summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
}}
.stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.2rem;
    text-align: center;
}}
.stat-value {{
    font-size: 2rem;
    font-weight: 700;
    color: var(--accent);
}}
.stat-value.warn {{ color: var(--accent3); }}
.stat-value.danger {{ color: var(--accent2); }}
.stat-value.good {{ color: var(--positive); }}
.stat-label {{
    font-size: 0.85rem;
    color: var(--muted);
    margin-top: 0.3rem;
}}
.chart-container {{
    position: relative;
    width: 100%;
    max-height: 400px;
    margin: 1rem 0;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1rem 0;
    font-size: 0.9rem;
}}
th, td {{
    padding: 0.6rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
}}
th {{
    color: var(--heading);
    font-weight: 600;
    background: rgba(0,180,216,0.08);
}}
td.positive {{ color: var(--positive); font-weight: 600; }}
td.negative {{ color: var(--negative); font-weight: 600; }}
.insight {{
    background: rgba(0,180,216,0.08);
    border-left: 3px solid var(--accent);
    padding: 1rem 1.2rem;
    margin: 1rem 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.95rem;
}}
.warning {{
    background: rgba(231,76,60,0.08);
    border-left: 3px solid var(--accent2);
    padding: 1rem 1.2rem;
    margin: 1rem 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.95rem;
}}
.pepfar-box {{
    background: rgba(243,156,18,0.08);
    border-left: 3px solid var(--accent3);
    padding: 1rem 1.2rem;
    margin: 1rem 0;
    border-radius: 0 8px 8px 0;
}}
.footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
}}
</style>
</head>
<body>
<div class="container">
    <h1>COVID's Lasting Impact on Africa's Clinical Trial Capacity</h1>
    <p class="subtitle">
        ClinicalTrials.gov API v2 | Interventional studies | Data retrieved {data['meta']['date'][:10]}
    </p>

    <!-- ── Summary Cards ──────────────────────────────────────── -->
    <div class="summary-grid">
        <div class="stat-card">
            <div class="stat-value">{summary['pre_covid_avg']:.0f}</div>
            <div class="stat-label">Pre-COVID annual average<br>(2015-2019)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value warn">{summary['peak_value']}</div>
            <div class="stat-label">Peak year: {summary['peak_year']}</div>
        </div>
        <div class="stat-card">
            <div class="stat-value {'good' if overall_rec >= 100 else 'danger'}">{summary['latest_value']}</div>
            <div class="stat-label">2025 trial starts</div>
        </div>
        <div class="stat-card">
            <div class="stat-value {'good' if overall_rec >= 100 else 'danger'}">{overall_rec}%</div>
            <div class="stat-label">Recovery vs trajectory<br>(2023-2025 avg)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value warn">{covid_surge['total_2020_2022']}</div>
            <div class="stat-label">COVID trials in Africa<br>(2020-2022)</div>
        </div>
    </div>

    <!-- ── Chart 1: Africa Timeline ──────────────────────────── -->
    <div class="card">
        <h2>1. Africa Trial Starts: Actual vs Pre-COVID Trajectory</h2>
        <p>The dashed line shows where Africa would have been without COVID, based on the 2015-2019 linear trend
           (slope: {pre_trend['slope']:+.1f} trials/year).</p>
        <div class="chart-container">
            <canvas id="timelineChart"></canvas>
        </div>
    </div>

    <!-- ── Chart 2: Normalized Comparison ────────────────────── -->
    <div class="card">
        <h2>2. Africa vs United States: Normalized to 2019 = 100</h2>
        <p>Both regions indexed to their 2019 trial start counts, showing relative trajectory divergence.</p>
        <div class="chart-container">
            <canvas id="normalizedChart"></canvas>
        </div>
    </div>

    <!-- ── COVID Disruption Table ────────────────────────────── -->
    <div class="card">
        <h2>3. COVID-Period Disruption (2020-2021)</h2>
        <table>
            <thead>
                <tr><th>Year</th><th>Actual</th><th>Expected (trend)</th><th>Deviation</th></tr>
            </thead>
            <tbody>
                {disrupt_rows}
            </tbody>
        </table>
    </div>

    <!-- ── COVID Surge ───────────────────────────────────────── -->
    <div class="card">
        <h2>4. COVID-19 Trial Surge in Africa</h2>
        <p>How many COVID-specific interventional trials appeared in Africa during the pandemic.</p>
        <div class="chart-container" style="max-height: 300px;">
            <canvas id="covidSurgeChart"></canvas>
        </div>
        <div class="insight">
            <strong>COVID trial concentration:</strong> {covid_surge['total_2020_2022']} COVID-specific
            interventional trials were registered in Africa between 2020-2022. This represents a
            significant redirection of research infrastructure toward a single disease.
        </div>
    </div>

    <!-- ── Displacement Analysis ─────────────────────────────── -->
    <div class="card">
        <h2>5. Displacement Analysis: Did COVID Crowd Out Other Research?</h2>
        <p>Comparing average annual trial starts for key African disease conditions before, during,
           and after COVID. Displacement % shows the change during COVID versus the 2018-2019 baseline.</p>
        <table>
            <thead>
                <tr>
                    <th>Condition</th>
                    <th>Baseline avg<br>(2018-19)</th>
                    <th>COVID avg<br>(2020-21)</th>
                    <th>Displacement</th>
                    <th>Post-COVID avg<br>(2022-23)</th>
                    <th>Recovery %</th>
                </tr>
            </thead>
            <tbody>
                {disp_rows}
            </tbody>
        </table>
    </div>

    <!-- ── Per-Condition Recovery Chart ──────────────────────── -->
    <div class="card">
        <h2>6. Per-Condition Recovery Trajectories</h2>
        <p>Year-by-year trial starts for key disease conditions in Africa (2018-2025).</p>
        <div class="chart-container">
            <canvas id="conditionChart"></canvas>
        </div>
    </div>

    <!-- ── PEPFAR Plateau ────────────────────────────────────── -->
    <div class="card">
        <h2>7. The PEPFAR Plateau</h2>
        <div class="pepfar-box">
            <h3>Background</h3>
            <p>PEPFAR (the US President's Emergency Plan for AIDS Relief) has been the single
               largest funder of HIV/AIDS research infrastructure in Africa since 2003. Congressional
               debate over PEPFAR reauthorization since 2023, followed by budget cuts and
               program restructuring in 2024-2025, may compound COVID's disruption to
               Africa's clinical trial ecosystem.</p>
            <h3>Observed Pattern</h3>
            <p>HIV trials in Africa:
               baseline average (2018-19) = {displacement.get('HIV', {}).get('baseline_avg', 'N/A')},
               COVID average (2020-21) = {displacement.get('HIV', {}).get('covid_avg', 'N/A')},
               post-COVID average (2022-23) = {displacement.get('HIV', {}).get('post_covid_avg', 'N/A')}.</p>
            <p>If post-COVID HIV trial recovery stalls below baseline, the PEPFAR funding trajectory
               is a plausible contributing factor alongside COVID disruption. This represents a
               compounding risk to Africa's research capacity in its highest-burden disease area.</p>
        </div>
        <div class="warning">
            <strong>Caution:</strong> Correlation between PEPFAR budget changes and trial counts
            does not establish causation. Multiple confounders exist including domestic funding changes,
            global health priority shifts, and site-level capacity constraints.
        </div>
    </div>

    <!-- ── Post-COVID Recovery Table ─────────────────────────── -->
    <div class="card">
        <h2>8. Post-COVID Recovery vs Pre-COVID Trajectory</h2>
        <table>
            <thead>
                <tr><th>Year</th><th>Actual</th><th>Expected (trend)</th><th>Recovery %</th></tr>
            </thead>
            <tbody>
                {rec_rows}
            </tbody>
        </table>
    </div>

    <!-- ── Projection ────────────────────────────────────────── -->
    <div class="card">
        <h2>9. Projection: When Will Africa Recover?</h2>
        <div class="insight">
            <p><strong>Pre-COVID growth rate:</strong> {pre_trend['slope']:+.1f} trials/year</p>
            <p><strong>Current growth rate (2022-2025):</strong> {projection.get('current_growth_rate', 'N/A')} trials/year</p>
            <p><strong>Trajectories {'converging' if converging else 'diverging'}:</strong> {proj_text}</p>
        </div>
    </div>

    <div class="footer">
        <p>Data source: ClinicalTrials.gov API v2 (public, no authentication) |
           Analysis: Interventional studies only | Generated {data['meta']['date'][:10]}</p>
        <p>E156 micro-publication: e156-covid-impact-paper.json</p>
    </div>
</div>

<script>
// ── Chart defaults ──────────────────────────────────────────────
Chart.defaults.color = '#c8d6e5';
Chart.defaults.borderColor = '#1e2d4a';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";

const years = {year_labels};
const africaCounts = {africa_counts};
const projectedCounts = {projected_counts};
const usCounts = {us_counts};
const normAfrica = {norm_africa};
const normUS = {norm_us};

// ── Chart 1: Africa Timeline ────────────────────────────────────
new Chart(document.getElementById('timelineChart'), {{
    type: 'line',
    data: {{
        labels: years,
        datasets: [
            {{
                label: 'Africa (actual)',
                data: africaCounts,
                borderColor: '#00b4d8',
                backgroundColor: '#00b4d833',
                borderWidth: 3,
                tension: 0.3,
                pointRadius: 5,
                pointBackgroundColor: '#00b4d8',
                fill: false,
            }},
            {{
                label: 'Pre-COVID trajectory',
                data: projectedCounts,
                borderColor: '#576574',
                borderDash: [8, 4],
                borderWidth: 2,
                tension: 0,
                pointRadius: 0,
                fill: false,
            }},
        ],
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{ position: 'top' }},
            tooltip: {{
                callbacks: {{
                    afterLabel: function(ctx) {{
                        if (ctx.datasetIndex === 0) {{
                            const expected = projectedCounts[ctx.dataIndex];
                            const diff = ctx.raw - expected;
                            const pct = expected > 0 ? ((diff / expected) * 100).toFixed(1) : 0;
                            return 'vs trend: ' + (diff >= 0 ? '+' : '') + diff.toFixed(0) + ' (' + (diff >= 0 ? '+' : '') + pct + '%)';
                        }}
                    }}
                }}
            }}
        }},
        scales: {{
            y: {{
                beginAtZero: false,
                title: {{ display: true, text: 'Trial starts' }},
                grid: {{ color: '#1e2d4a' }},
            }},
            x: {{
                grid: {{ color: '#1e2d4a' }},
            }},
        }},
    }},
}});

// ── Chart 2: Normalized Comparison ──────────────────────────────
new Chart(document.getElementById('normalizedChart'), {{
    type: 'line',
    data: {{
        labels: years,
        datasets: [
            {{
                label: 'Africa (indexed)',
                data: normAfrica,
                borderColor: '#00b4d8',
                borderWidth: 3,
                tension: 0.3,
                pointRadius: 4,
                fill: false,
            }},
            {{
                label: 'United States (indexed)',
                data: normUS,
                borderColor: '#e74c3c',
                borderWidth: 3,
                tension: 0.3,
                pointRadius: 4,
                fill: false,
            }},
            {{
                label: 'Baseline (100)',
                data: years.map(() => 100),
                borderColor: '#576574',
                borderDash: [4, 4],
                borderWidth: 1,
                pointRadius: 0,
                fill: false,
            }},
        ],
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{ position: 'top' }},
        }},
        scales: {{
            y: {{
                title: {{ display: true, text: 'Index (2019 = 100)' }},
                grid: {{ color: '#1e2d4a' }},
            }},
            x: {{
                grid: {{ color: '#1e2d4a' }},
            }},
        }},
    }},
}});

// ── Chart 3: COVID Surge ────────────────────────────────────────
new Chart(document.getElementById('covidSurgeChart'), {{
    type: 'bar',
    data: {{
        labels: {covid_years_json},
        datasets: [{{
            label: 'COVID-19 trials in Africa',
            data: {covid_counts_json},
            backgroundColor: ['#e74c3c99', '#e74c3c77', '#e74c3c55'],
            borderColor: '#e74c3c',
            borderWidth: 2,
        }}],
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{ display: false }},
        }},
        scales: {{
            y: {{
                beginAtZero: true,
                title: {{ display: true, text: 'COVID-19 trials' }},
                grid: {{ color: '#1e2d4a' }},
            }},
            x: {{
                grid: {{ display: false }},
            }},
        }},
    }},
}});

// ── Chart 4: Per-Condition Recovery ─────────────────────────────
new Chart(document.getElementById('conditionChart'), {{
    type: 'line',
    data: {{
        labels: {cond_labels},
        datasets: [{cond_dataset_js}
        ],
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{ position: 'top' }},
        }},
        scales: {{
            y: {{
                beginAtZero: true,
                title: {{ display: true, text: 'Trial starts' }},
                grid: {{ color: '#1e2d4a' }},
            }},
            x: {{
                grid: {{ color: '#1e2d4a' }},
            }},
        }},
    }},
}});
</script>
</body>
</html>"""
    return html


# ── Main ─────────────────────────────────────────────────────────────
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(
            CACHE_FILE.stat().st_mtime
        )
        if cache_age < timedelta(hours=CACHE_TTL_HOURS):
            print(f"Using cached data ({cache_age.seconds // 3600}h "
                  f"{(cache_age.seconds % 3600) // 60}m old)")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            print("Cache expired. Fetching fresh data...")
            data = collect_all_data()
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    else:
        print("No cache found. Fetching data...")
        data = collect_all_data()
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nData saved to {CACHE_FILE}")

    # Compute analytics
    print("\nComputing analytics...")
    analytics = compute_analytics(data)

    # Generate HTML
    print("Generating HTML dashboard...")
    html = generate_html(data, analytics)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard written to {OUTPUT_HTML}")

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    s = analytics["summary"]
    print(f"  Pre-COVID average (2015-2019): {s['pre_covid_avg']:.0f} trials/year")
    print(f"  Peak year: {s['peak_year']} ({s['peak_value']} trials)")
    print(f"  Latest (2025): {s['latest_value']} trials")
    print(f"  Recovery vs trajectory: {analytics['overall_recovery_pct']}%")
    print(f"  COVID trials in Africa (2020-2022): "
          f"{analytics['covid_trial_surge']['total_2020_2022']}")

    proj = analytics["projection"]
    if proj.get("recovery_year") is not None:
        print(f"  Projected recovery year: {proj['recovery_year']:.0f}")
    else:
        print(f"  Projection: {proj.get('note', 'N/A')}")

    print(f"\nDisplacement analysis:")
    for cond, d in analytics["displacement"].items():
        print(f"  {cond}: baseline={d['baseline_avg']:.0f}, "
              f"COVID={d['covid_avg']:.0f} ({d['displacement_pct']:+.1f}%), "
              f"recovery={d['recovery_pct']:.1f}%")

    print(f"\nDone. Open {OUTPUT_HTML} in a browser.")


if __name__ == "__main__":
    main()
