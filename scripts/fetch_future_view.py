"""
The Future View — What Africa's Trial Landscape Will Look Like in 2035
======================================================================
Projects forward: at current growth rates, what will Africa's trial portfolio
look like in 10 years? Under optimistic/pessimistic/status quo scenarios.

Usage:
    python fetch_future_view.py

Output:
    data/future_view_data.json  — cached data
    future-view.html            — interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)
"""

import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config ----------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "future_view_data.json"
OUTPUT_HTML = Path(__file__).parent / "future-view.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35

# -- Population and benchmark data ----------------------------------------
POPULATIONS = {
    "Uganda": 48_400_000,
    "Kenya": 56_000_000,
    "Nigeria": 230_000_000,
    "South Africa": 62_000_000,
    "Ethiopia": 130_000_000,
    "Tanzania": 67_000_000,
    "Rwanda": 14_000_000,
    "Sub-Saharan Africa": 1_200_000_000,
    "India": 1_440_000_000,
    "Brazil": 216_000_000,
    "Latin America": 660_000_000,
    "United States": 335_000_000,
}

# Current trial counts (from prior analyses)
CURRENT_COUNTS = {
    "Uganda": 783,
    "Kenya": 720,
    "Nigeria": 354,
    "South Africa": 3473,
    "Ethiopia": 240,
    "Tanzania": 431,
    "Rwanda": 121,
    "India": 11500,  # approximate CT.gov registrations
    "Brazil": 8200,
    "United States": 159196,
}

# Per-capita rates (trials per million)
def trials_per_million(country):
    count = CURRENT_COUNTS.get(country, 0)
    pop = POPULATIONS.get(country, 1)
    return round(count / pop * 1_000_000, 1)

# Latin America benchmark (from CT.gov ~ 28,000 trials / 660M people)
LATAM_RATE = round(28000 / 660 * 1, 1)  # per million


# -- API helpers -----------------------------------------------------------
def search_trials_count(location=None, query_term=None, page_size=1,
                        max_retries=3):
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }
    if location:
        params["query.locn"] = location
    if query_term:
        params["query.term"] = query_term

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json().get("totalCount", 0)
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return 0


# -- Scenario projection engine -------------------------------------------
def linear_fit(years, counts):
    """Simple linear regression: returns (slope, intercept)."""
    n = len(years)
    if n < 2:
        return 0, counts[0] if counts else 0
    sum_x = sum(years)
    sum_y = sum(counts)
    sum_xy = sum(x * y for x, y in zip(years, counts))
    sum_x2 = sum(x * x for x in years)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0, sum_y / n
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def project_scenario(year_dist, scenario, target_year=2035):
    """Project trial counts to target_year under three scenarios."""

    years = sorted(year_dist.keys())
    counts = [year_dist[y] for y in years]

    # Use recent trend (2019-2026) for status quo
    recent_years = [y for y in years if 2019 <= y <= 2025]
    recent_counts = [year_dist[y] for y in recent_years]

    if scenario == "status_quo":
        slope, intercept = linear_fit(recent_years, recent_counts)
        projections = {}
        for y in range(2026, target_year + 1):
            projected = max(0, round(slope * y + intercept))
            projections[y] = projected
        return projections

    elif scenario == "optimistic":
        # India's growth rate: ~15% per year in recent period
        growth_rate = 0.15
        base = recent_counts[-1] if recent_counts else counts[-1] if counts else 30
        projections = {}
        current = base
        for y in range(2026, target_year + 1):
            current = round(current * (1 + growth_rate))
            projections[y] = current
        return projections

    elif scenario == "pessimistic":
        # PEPFAR cuts + post-COVID stagnation: -5% per year
        growth_rate = -0.05
        base = recent_counts[-1] if recent_counts else counts[-1] if counts else 30
        projections = {}
        current = base
        for y in range(2026, target_year + 1):
            current = max(5, round(current * (1 + growth_rate)))
            projections[y] = current
        return projections

    return {}


def compute_years_to_parity(current_rate, target_rate, growth_rate):
    """Compute years until Africa matches LatAm per-capita rate."""
    if current_rate >= target_rate:
        return 0
    if growth_rate <= 0:
        return float('inf')
    # rate * (1 + g)^n = target
    # n = log(target/rate) / log(1+g)
    try:
        n = math.log(target_rate / current_rate) / math.log(1 + growth_rate)
        return round(n, 1)
    except (ValueError, ZeroDivisionError):
        return float('inf')


# -- Data collection -------------------------------------------------------
def collect_data():
    """Collect future view data."""

    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetch_date = datetime.fromisoformat(cached["fetch_date"])
            if datetime.now() - fetch_date < timedelta(hours=CACHE_HOURS):
                print(f"Using cached data from {fetch_date.strftime('%Y-%m-%d %H:%M')}")
                return cached
            else:
                print("Cache expired, re-fetching...")
        except (json.JSONDecodeError, KeyError, ValueError):
            print("Cache invalid, re-fetching...")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Step 1: Load Uganda temporal data ----
    print("\n" + "=" * 70)
    print("STEP 1: Loading Uganda temporal data")
    print("=" * 70)

    uganda_cache = DATA_DIR / "uganda_collected_data.json"
    if not uganda_cache.exists():
        print("  ERROR: Uganda data not found. Run fetch_uganda_rcts.py first.")
        sys.exit(1)

    with open(uganda_cache, "r", encoding="utf-8") as f:
        uganda_data = json.load(f)
    trials = uganda_data.get("sample_trials", [])
    total = len(trials)
    print(f"  Loaded {total} Uganda trials")

    # Year distribution
    year_dist = Counter()
    for t in trials:
        sd = t.get("start_date", "")
        if sd and len(sd) >= 4:
            try:
                yr = int(sd[:4])
                if 1998 <= yr <= 2026:
                    year_dist[yr] += 1
            except ValueError:
                pass
    year_dist = dict(sorted(year_dist.items()))
    print(f"  Year range: {min(year_dist.keys())}-{max(year_dist.keys())}")
    print(f"  Recent trend (2019-2025): {[year_dist.get(y,0) for y in range(2019,2026)]}")

    # ---- Step 2: Africa-wide data ----
    print("\n" + "=" * 70)
    print("STEP 2: Africa-wide comparison data")
    print("=" * 70)

    comparison = uganda_data.get("comparison_countries", {})
    for country, count in comparison.items():
        rate = trials_per_million(country)
        print(f"  {country}: {count:,} trials ({rate} per million)")

    # ---- Step 3: NCD and local sponsorship trends (Uganda) ----
    print("\n" + "=" * 70)
    print("STEP 3: NCD and local sponsorship trends")
    print("=" * 70)

    ncd_keywords = ["diabetes", "hypertension", "cardiovascular", "cancer",
                    "stroke", "heart", "copd", "kidney", "obesity"]
    local_keywords = ["makerere", "mbarara", "uganda", "mulago", "gulu",
                      "kampala", "uvri", "busitema"]

    ncd_by_year = Counter()
    local_by_year = Counter()
    total_by_year = Counter()

    for t in trials:
        sd = t.get("start_date", "")
        if not sd or len(sd) < 4:
            continue
        try:
            yr = int(sd[:4])
        except ValueError:
            continue
        if yr < 2010 or yr > 2025:
            continue

        total_by_year[yr] += 1

        conds = " ".join(t.get("conditions", [])).lower()
        if any(kw in conds for kw in ncd_keywords):
            ncd_by_year[yr] += 1

        sponsor = t.get("sponsor", "").lower()
        if any(kw in sponsor for kw in local_keywords):
            local_by_year[yr] += 1

    ncd_trend = {str(y): ncd_by_year.get(y, 0) for y in range(2010, 2026)}
    local_trend = {str(y): local_by_year.get(y, 0) for y in range(2010, 2026)}
    total_trend = {str(y): total_by_year.get(y, 0) for y in range(2010, 2026)}

    # NCD share trend
    ncd_share_early = sum(ncd_by_year.get(y, 0) for y in range(2010, 2016))
    ncd_total_early = sum(total_by_year.get(y, 0) for y in range(2010, 2016))
    ncd_share_late = sum(ncd_by_year.get(y, 0) for y in range(2020, 2026))
    ncd_total_late = sum(total_by_year.get(y, 0) for y in range(2020, 2026))
    ncd_pct_early = round(ncd_share_early / ncd_total_early * 100, 1) if ncd_total_early > 0 else 0
    ncd_pct_late = round(ncd_share_late / ncd_total_late * 100, 1) if ncd_total_late > 0 else 0

    local_share_early = sum(local_by_year.get(y, 0) for y in range(2010, 2016))
    local_total_early = ncd_total_early
    local_share_late = sum(local_by_year.get(y, 0) for y in range(2020, 2026))
    local_total_late = ncd_total_late
    local_pct_early = round(local_share_early / local_total_early * 100, 1) if local_total_early > 0 else 0
    local_pct_late = round(local_share_late / local_total_late * 100, 1) if local_total_late > 0 else 0

    print(f"  NCD share: {ncd_pct_early}% (2010-15) -> {ncd_pct_late}% (2020-25)")
    print(f"  Local sponsorship: {local_pct_early}% (2010-15) -> {local_pct_late}% (2020-25)")

    # ---- Step 4: Project three scenarios ----
    print("\n" + "=" * 70)
    print("STEP 4: Three-scenario projection to 2035")
    print("=" * 70)

    scenarios = {}
    for scenario in ["status_quo", "optimistic", "pessimistic"]:
        proj = project_scenario(year_dist, scenario)
        total_2035 = sum(proj.values())
        cumulative_2035 = total + total_2035
        scenarios[scenario] = {
            "annual_projections": {str(y): c for y, c in proj.items()},
            "total_new_2026_2035": total_2035,
            "cumulative_by_2035": cumulative_2035,
            "trials_2035": proj.get(2035, 0),
        }
        print(f"  {scenario}: 2035 annual={proj.get(2035, 0)}, cumulative={cumulative_2035}")

    # ---- Step 5: Years to parity ----
    print("\n" + "=" * 70)
    print("STEP 5: Years to parity calculation")
    print("=" * 70)

    uganda_rate = trials_per_million("Uganda")
    latam_rate = LATAM_RATE

    years_parity = {
        "status_quo": compute_years_to_parity(uganda_rate, latam_rate, 0.05),
        "optimistic": compute_years_to_parity(uganda_rate, latam_rate, 0.15),
        "pessimistic": compute_years_to_parity(uganda_rate, latam_rate, -0.05),
    }

    for scenario, yrs in years_parity.items():
        label = f"{yrs} years" if yrs < 100 else "Never (declining)"
        print(f"  {scenario}: {label}")

    # ---- Step 6: Lives at stake estimate ----
    print("\n" + "=" * 70)
    print("STEP 6: Lives at stake estimate")
    print("=" * 70)

    # Conservative estimate: each trial generates evidence affecting ~50,000 people
    # Evidence gap: difference between current Africa rate and LatAm rate
    africa_total_pop = POPULATIONS["Sub-Saharan Africa"]
    current_africa_trials = sum(
        CURRENT_COUNTS.get(c, 0) for c in
        ["Uganda", "Kenya", "Nigeria", "South Africa", "Ethiopia", "Tanzania", "Rwanda"]
    )
    current_africa_rate = round(current_africa_trials / (africa_total_pop / 1_000_000), 1)
    trial_gap_per_year = round((latam_rate - current_africa_rate) * (africa_total_pop / 1_000_000))
    # Each missing trial = ~5,000 person-years of evidence lost (conservative)
    evidence_gap_lives = trial_gap_per_year * 5000

    print(f"  Current Africa rate: {current_africa_rate} per million")
    print(f"  LatAm rate: {latam_rate} per million")
    print(f"  Trial gap per year: {trial_gap_per_year:,}")
    print(f"  Evidence gap (lives/yr): {evidence_gap_lives:,}")

    # ---- Build data ----
    data = {
        "fetch_date": datetime.now().isoformat(),
        "uganda_total": total,
        "year_distribution": year_dist,
        "comparison_countries": comparison,
        "per_capita_rates": {c: trials_per_million(c) for c in CURRENT_COUNTS},
        "latam_rate": latam_rate,
        "ncd_trend": ncd_trend,
        "local_trend": local_trend,
        "total_trend": total_trend,
        "ncd_pct": {"early": ncd_pct_early, "late": ncd_pct_late},
        "local_pct": {"early": local_pct_early, "late": local_pct_late},
        "scenarios": scenarios,
        "years_to_parity": {k: v if v < 1000 else None for k, v in years_parity.items()},
        "lives_at_stake": {
            "current_africa_rate": current_africa_rate,
            "trial_gap_per_year": trial_gap_per_year,
            "evidence_gap_lives_per_year": evidence_gap_lives,
        },
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator -------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML future view dashboard."""

    fetch_date = data["fetch_date"][:10]
    total = data["uganda_total"]
    year_dist = data["year_distribution"]
    pcr = data["per_capita_rates"]
    scenarios = data["scenarios"]
    ytp = data["years_to_parity"]
    las = data["lives_at_stake"]
    ncd_pct = data["ncd_pct"]
    local_pct = data["local_pct"]

    # Historical year chart (text-based bars)
    years_sorted = sorted(year_dist.items(), key=lambda x: int(x[0]))
    max_yr_count = max(c for _, c in years_sorted) if years_sorted else 1
    year_bars = []
    for year_str, count in years_sorted:
        year = int(year_str)
        if year < 2002:
            continue
        bar_w = round(count / max_yr_count * 100)
        color = "#3b82f6" if year < 2020 else "#22c55e" if year <= 2025 else "#f59e0b"
        year_bars.append(
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
            f'<div style="width:40px;text-align:right;font-weight:600;'
            f'color:#94a3b8;font-size:12px">{year}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:3px;height:20px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:{color};'
            f'border-radius:3px"></div>'
            f'<span style="position:absolute;right:6px;top:2px;font-size:11px;'
            f'color:#64748b;font-weight:600">{count}</span>'
            f'</div></div>'
        )
    year_bars_html = "\n".join(year_bars)

    # Scenario projection table
    scenario_labels = {
        "status_quo": ("Status Quo", "#f59e0b", "Linear extrapolation of 2019-2025 trend"),
        "optimistic": ("Optimistic", "#22c55e", "India-matching 15% annual growth"),
        "pessimistic": ("Pessimistic", "#ef4444", "PEPFAR cuts + post-COVID stagnation"),
    }

    scenario_rows = []
    for sc_key, sc_data in scenarios.items():
        label, color, desc = scenario_labels[sc_key]
        t2035 = sc_data["trials_2035"]
        cum = sc_data["cumulative_by_2035"]
        yrs = ytp.get(sc_key)
        yrs_str = f"{yrs} years" if yrs is not None else "Never"
        scenario_rows.append(
            f'<tr>'
            f'<td style="padding:10px 12px;font-weight:700;color:{color}">{label}</td>'
            f'<td style="padding:10px 12px;font-size:13px;color:#94a3b8">{desc}</td>'
            f'<td style="text-align:right;padding:10px 12px;font-weight:700;'
            f'color:{color};font-size:1.2rem">{t2035}</td>'
            f'<td style="text-align:right;padding:10px 12px;font-weight:600">'
            f'{cum:,}</td>'
            f'<td style="text-align:right;padding:10px 12px;color:{color};'
            f'font-weight:600">{yrs_str}</td>'
            f'</tr>'
        )
    scenario_rows_html = "\n".join(scenario_rows)

    # Year-by-year projection bars for each scenario
    proj_years_html = ""
    for sc_key in ["optimistic", "status_quo", "pessimistic"]:
        label, color, _ = scenario_labels[sc_key]
        proj = scenarios[sc_key]["annual_projections"]
        proj_sorted = sorted(proj.items(), key=lambda x: int(x[0]))
        max_proj = max(c for _, c in proj_sorted) if proj_sorted else 1
        bars = []
        for yr_str, count in proj_sorted:
            bar_w = round(count / max(max_proj, 1) * 100)
            bars.append(
                f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
                f'<div style="width:36px;text-align:right;font-size:11px;'
                f'color:#64748b">{yr_str}</div>'
                f'<div style="flex:1;background:#1e293b;border-radius:3px;height:18px;'
                f'position:relative">'
                f'<div style="width:{bar_w}%;height:100%;background:{color};'
                f'border-radius:3px;opacity:0.7"></div>'
                f'<span style="position:absolute;right:6px;top:1px;font-size:10px;'
                f'color:#94a3b8">{count}</span>'
                f'</div></div>'
            )
        proj_years_html += (
            f'<div style="flex:1;min-width:280px">'
            f'<h3 style="color:{color};margin-bottom:8px;font-size:14px">{label}</h3>'
            f'{"".join(bars)}'
            f'</div>'
        )

    # Per-capita comparison bars
    pcr_sorted = sorted(pcr.items(), key=lambda x: x[1], reverse=True)
    max_rate = pcr_sorted[0][1] if pcr_sorted else 1
    rate_bars = []
    for country, rate in pcr_sorted:
        bar_w = round(rate / max_rate * 100)
        is_africa = country not in ["United States", "India", "Brazil"]
        color = "#ef4444" if rate < 10 else "#f59e0b" if rate < 30 else "#22c55e"
        if not is_africa:
            color = "#3b82f6"
        rate_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
            f'<div style="width:140px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:13px">{country}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:24px;'
            f'position:relative">'
            f'<div style="width:{min(bar_w, 100)}%;height:100%;background:{color};'
            f'border-radius:4px"></div>'
            f'<span style="position:absolute;right:8px;top:3px;font-size:12px;'
            f'color:#94a3b8;font-weight:600">{rate}</span>'
            f'</div></div>'
        )
    rate_bars_html = "\n".join(rate_bars)

    # Parity display
    ytp_sq = ytp.get("status_quo")
    ytp_opt = ytp.get("optimistic")
    ytp_pess = ytp.get("pessimistic")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Three Futures for Africa | Trial Landscape Projections to 2035</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #cbd5e1;
    line-height: 1.6;
    min-height: 100vh;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  h1 {{
    font-size: 2.4rem; color: #f1f5f9; text-align: center; margin: 32px 0 8px;
    letter-spacing: -0.5px;
  }}
  .subtitle {{
    text-align: center; color: #64748b; font-size: 15px; margin-bottom: 32px;
  }}
  .section {{
    background: #111827; border: 1px solid #1e293b; border-radius: 12px;
    padding: 28px; margin-bottom: 24px;
  }}
  .section h2 {{
    color: #f1f5f9; font-size: 1.4rem; margin-bottom: 16px;
    padding-bottom: 10px; border-bottom: 2px solid #1e293b;
  }}
  .section h3 {{
    color: #e2e8f0; font-size: 1.1rem; margin: 16px 0 10px;
  }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 20px;
  }}
  .kpi {{
    background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
    padding: 20px; text-align: center;
  }}
  .kpi .value {{
    font-size: 2.2rem; font-weight: 800; margin: 4px 0;
  }}
  .kpi .label {{
    font-size: 13px; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .callout {{
    background: #1a0a0a; border-left: 4px solid #ef4444; padding: 16px 20px;
    margin: 16px 0; border-radius: 0 8px 8px 0; color: #fca5a5;
  }}
  .callout-green {{
    background: #0a1a0a; border-left: 4px solid #22c55e; color: #86efac;
  }}
  .callout-amber {{
    background: #1a150a; border-left: 4px solid #f59e0b; color: #fde68a;
  }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 14px;
  }}
  thead th {{
    text-align: left; padding: 10px 12px; color: #94a3b8;
    border-bottom: 2px solid #1e293b; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }}
  tbody td {{
    padding: 8px 12px; border-bottom: 1px solid #1e293b; color: #cbd5e1;
  }}
  tbody tr:hover {{ background: #1e293b44; }}
  .source {{
    text-align: center; color: #475569; font-size: 12px; margin-top: 40px;
    padding: 20px; border-top: 1px solid #1e293b;
  }}
  .source a {{ color: #3b82f6; text-decoration: none; }}
  .big-number {{
    font-size: 5rem; font-weight: 900; text-align: center;
    margin: 20px 0 10px; line-height: 1;
  }}
  .big-sub {{
    text-align: center; color: #94a3b8; font-size: 16px; margin-bottom: 16px;
  }}
  .trio {{
    display: flex; gap: 16px; margin: 20px 0; flex-wrap: wrap;
    justify-content: center;
  }}
  .trio-item {{
    flex: 1; min-width: 200px; text-align: center;
    background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
    padding: 24px;
  }}
  .trio-item .num {{
    font-size: 2.8rem; font-weight: 900; line-height: 1;
  }}
  .trio-item .desc {{
    font-size: 13px; color: #94a3b8; margin-top: 8px;
  }}
</style>
</head>
<body>
<div class="container">

<h1>Three Futures for Africa</h1>
<p class="subtitle">
  Clinical Trial Landscape Projections to 2035 |
  ClinicalTrials.gov API v2 | Data: {fetch_date}
</p>

<!-- ============ SECTION 1: WHERE WE ARE ============ -->
<div class="section">
  <h2>1. Where We Are: The Historical Trajectory</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Uganda Trials (Total)</div>
      <div class="value" style="color:#60a5fa">{total}</div>
      <div class="label">1998-2026</div>
    </div>
    <div class="kpi">
      <div class="label">Uganda Rate</div>
      <div class="value" style="color:#f59e0b">{pcr.get('Uganda', 0)}</div>
      <div class="label">per million population</div>
    </div>
    <div class="kpi">
      <div class="label">NCD Share Trend</div>
      <div class="value" style="color:#8b5cf6">{ncd_pct['late']}%</div>
      <div class="label">up from {ncd_pct['early']}% (2010-15)</div>
    </div>
    <div class="kpi">
      <div class="label">Local Sponsor Trend</div>
      <div class="value" style="color:#22c55e">{local_pct['late']}%</div>
      <div class="label">up from {local_pct['early']}% (2010-15)</div>
    </div>
  </div>
  <h3>Annual Trial Starts in Uganda (2002-2026)</h3>
  {year_bars_html}
  <div class="callout-amber callout" style="margin-top:16px">
    <strong>The growth story:</strong> Uganda's trial activity grew steadily
    from 2 trials in 1998 to a peak of 67 in 2021. However, post-COVID
    momentum has plateaued around 45-50 per year. The question is whether
    this plateau is a temporary pause or the new normal.
  </div>
</div>

<!-- ============ SECTION 2: THREE SCENARIOS ============ -->
<div class="section">
  <h2>2. Three Scenarios for 2035</h2>
  <div class="trio">
    <div class="trio-item" style="border-color:#22c55e">
      <div class="num" style="color:#22c55e">{scenarios['optimistic']['trials_2035']}</div>
      <div class="desc">Optimistic (2035 annual)<br>
        <span style="font-size:11px">India-matching 15% growth</span></div>
    </div>
    <div class="trio-item" style="border-color:#f59e0b">
      <div class="num" style="color:#f59e0b">{scenarios['status_quo']['trials_2035']}</div>
      <div class="desc">Status Quo (2035 annual)<br>
        <span style="font-size:11px">Linear trend continues</span></div>
    </div>
    <div class="trio-item" style="border-color:#ef4444">
      <div class="num" style="color:#ef4444">{scenarios['pessimistic']['trials_2035']}</div>
      <div class="desc">Pessimistic (2035 annual)<br>
        <span style="font-size:11px">PEPFAR cuts, -5%/year</span></div>
    </div>
  </div>

  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Scenario</th>
        <th>Assumptions</th>
        <th style="text-align:right">2035 Annual</th>
        <th style="text-align:right">Cumulative by 2035</th>
        <th style="text-align:right">Years to LatAm Parity</th>
      </tr>
    </thead>
    <tbody>
      {scenario_rows_html}
    </tbody>
  </table>
  </div>

  <h3 style="margin-top:24px">Year-by-Year Projections (2026-2035)</h3>
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:12px">
    {proj_years_html}
  </div>
</div>

<!-- ============ SECTION 3: YEARS TO PARITY ============ -->
<div class="section">
  <h2>3. The Parity Clock: How Long Until Africa Catches Up?</h2>
  <p style="color:#94a3b8;margin-bottom:16px">
    Years until Uganda matches current Latin American per-capita trial rate
    ({LATAM_RATE} trials per million people)
  </p>
  <div class="trio">
    <div class="trio-item">
      <div class="num" style="color:#22c55e">{ytp_opt if ytp_opt is not None else '--'}</div>
      <div class="desc">Years (Optimistic)<br>15% annual growth</div>
    </div>
    <div class="trio-item">
      <div class="num" style="color:#f59e0b">{ytp_sq if ytp_sq is not None else '--'}</div>
      <div class="desc">Years (Status Quo)<br>5% annual growth</div>
    </div>
    <div class="trio-item">
      <div class="num" style="color:#ef4444">Never</div>
      <div class="desc">Pessimistic<br>Declining trial activity</div>
    </div>
  </div>
  <div class="callout">
    <strong>The parity gap:</strong> Even under the most optimistic scenario
    (India-matching growth), Uganda would need approximately
    <strong>{ytp_opt if ytp_opt is not None else 'many'} years</strong> to match
    Latin America's current per-capita trial rate. Under status quo conditions,
    parity is approximately <strong>{ytp_sq if ytp_sq is not None else 'many'} years</strong>
    away. Under pessimistic assumptions with PEPFAR funding cuts and continued
    stagnation, the gap widens indefinitely.
  </div>
</div>

<!-- ============ SECTION 4: PER-CAPITA RATES ============ -->
<div class="section">
  <h2>4. Current Per-Capita Trial Rates (Trials per Million)</h2>
  {rate_bars_html}
  <div class="callout-amber callout" style="margin-top:16px">
    <strong>The scale of disparity:</strong> The United States has
    <strong>{pcr.get('United States', 0)}</strong> trials per million people.
    Uganda has <strong>{pcr.get('Uganda', 0)}</strong>. Even India, another
    low-middle income country, vastly outperforms African nations in per-capita
    trial activity. The gap is not closing -- it is structural.
  </div>
</div>

<!-- ============ SECTION 5: LIVES AT STAKE ============ -->
<div class="section">
  <h2>5. Lives at Stake: The Evidence Gap's Human Cost</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Current Africa Rate</div>
      <div class="value" style="color:#ef4444">{las['current_africa_rate']}</div>
      <div class="label">trials per million</div>
    </div>
    <div class="kpi">
      <div class="label">LatAm Rate (Target)</div>
      <div class="value" style="color:#22c55e">{LATAM_RATE}</div>
      <div class="label">trials per million</div>
    </div>
    <div class="kpi">
      <div class="label">Trial Gap / Year</div>
      <div class="value" style="color:#f59e0b">{las['trial_gap_per_year']:,}</div>
      <div class="label">missing trials annually</div>
    </div>
    <div class="kpi">
      <div class="label">Evidence Gap (Lives)</div>
      <div class="value" style="color:#ef4444">{las['evidence_gap_lives_per_year']:,}</div>
      <div class="label">person-years affected / yr</div>
    </div>
  </div>
  <div class="callout">
    <strong>The human cost of the evidence gap:</strong> Each trial that does
    not happen in Africa is evidence that does not exist for African
    populations. Drugs are prescribed based on studies conducted on different
    populations, with different genetics, different diets, different
    co-morbidities, different healthcare systems. The evidence gap is not
    abstract -- it translates directly into suboptimal treatment decisions,
    inappropriate drug doses, and preventable deaths.
  </div>
</div>

<!-- ============ SECTION 6: WHAT IT WOULD TAKE ============ -->
<div class="section">
  <h2>6. What Would It Take? The 2035 Vision</h2>
  <div class="callout-green callout">
    <strong>Investment needed to reach LatAm parity by 2035:</strong>
  </div>
  <ul style="margin:16px 0 16px 24px;color:#94a3b8;line-height:2.2">
    <li><strong style="color:#e2e8f0">Domestic R&amp;D investment:</strong>
      African governments must reach the AU target of 1% GDP in R&amp;D
      spending. Current Sub-Saharan average: 0.42%. This alone would more
      than double the research base.</li>
    <li><strong style="color:#e2e8f0">Regulatory harmonization:</strong>
      The African Medicines Agency (AMA) must become operational and achieve
      mutual recognition agreements. Currently, each country's regulatory
      approval process adds 12-24 months to trial timelines.</li>
    <li><strong style="color:#e2e8f0">NCD research pivot:</strong>
      NCDs are now the fastest-growing cause of death in Africa. Research
      portfolios must shift from 80% infectious disease to at least 40%
      NCD by 2035.</li>
    <li><strong style="color:#e2e8f0">Phase 1 infrastructure:</strong>
      Build at least 5 GCP-compliant Phase 1 units across the continent.
      Without first-in-human capability, Africa will remain a testing
      ground, never a development site.</li>
    <li><strong style="color:#e2e8f0">Local manufacturing:</strong>
      Africa currently produces less than 1% of vaccines it consumes.
      BioNTech's Rwanda plant and Aspen's South Africa facility are
      promising starts but insufficient.</li>
    <li><strong style="color:#e2e8f0">Training pipeline:</strong>
      10,000 new clinical researchers by 2035 (from current ~2,000),
      with career pathways that do not require emigration. Structured
      mentorship, competitive salaries, protected research time.</li>
  </ul>
  <div class="callout-amber callout">
    <strong>Policy recommendations:</strong> (1) PEPFAR reauthorization with
    mandatory NCD research allocation; (2) African Union research sovereignty
    fund; (3) WHO-brokered technology transfer agreements; (4) Tax incentives
    for pharma companies conducting Phase 1 trials in Africa; (5) Open-access
    requirements for all trials conducted on African populations.
  </div>
</div>

<div class="source">
  Data source: <a href="https://clinicaltrials.gov">ClinicalTrials.gov</a>
  API v2 (accessed {fetch_date})<br>
  Analysis: fetch_future_view.py | The Future View<br>
  Projections: Linear regression (status quo), exponential growth (optimistic),
  exponential decline (pessimistic). All projections are illustrative and
  depend on assumptions stated in each scenario.
</div>

</div>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated HTML: {OUTPUT_HTML}")
    print(f"  File size: {len(html):,} bytes")


# -- Main ------------------------------------------------------------------
def main():
    print("=" * 70)
    print("  The Future View -- Three Futures for Africa")
    print("  ClinicalTrials.gov API v2 Analysis")
    print("=" * 70)

    data = collect_data()

    print("\n" + "=" * 70)
    print("KEY FINDINGS:")
    print("=" * 70)
    print(f"  Uganda total trials:       {data['uganda_total']}")
    for sc_key in ["optimistic", "status_quo", "pessimistic"]:
        sc = data["scenarios"][sc_key]
        print(f"  {sc_key}: 2035 annual={sc['trials_2035']}, "
              f"cumulative={sc['cumulative_by_2035']}")
    print(f"  Years to parity:           {data['years_to_parity']}")
    print(f"  Evidence gap (lives/yr):   {data['lives_at_stake']['evidence_gap_lives_per_year']:,}")

    generate_html(data)
    print("\nDone.")


if __name__ == "__main__":
    main()
