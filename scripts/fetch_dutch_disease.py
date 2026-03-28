#!/usr/bin/env python
"""
fetch_dutch_disease.py - The Dutch Disease of Global Health

In economics, "Dutch Disease" occurs when a boom in one export sector
(natural gas in the Netherlands, oil in Nigeria) causes the rest of
the economy to decline through currency appreciation and resource
reallocation.  In Africa's research ecosystem, PEPFAR/Global Fund HIV
funding IS the "oil" - it crowds out NCD, surgical, mental-health,
and other research.

For 15 African countries we compute:
  - Dutch Disease Coefficient (DDC) = HIV trials / non-HIV trials
      > 1 means "diseased" portfolio (HIV dominates)
      < 1 means "healthy" portfolio
  - HIV Dependency Ratio = HIV trials / total trials
  - PEPFAR-heavy vs non-PEPFAR comparison

Outputs:
  - data/dutch_disease_data.json  (cached API results, 24h TTL)
  - dutch-disease.html            (dark-theme interactive dashboard)
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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# PEPFAR-heavy countries (large HIV infrastructure investment)
PEPFAR_COUNTRIES = {
    "Uganda":        48,
    "Kenya":         56,
    "Mozambique":    33,
    "Zimbabwe":      15,
    "Zambia":        21,
    "Tanzania":      67,
    "South Africa":  62,
    "Malawi":        21,
    "Ethiopia":      130,
    "Nigeria":       230,
}

# Non-PEPFAR comparators (similar income, less HIV funding)
NON_PEPFAR_COUNTRIES = {
    "Ghana":         34,
    "Senegal":       17,
    "Burkina Faso":  23,
    "Madagascar":    30,
    "Cameroon":      27,
}

ALL_COUNTRIES = {**PEPFAR_COUNTRIES, **NON_PEPFAR_COUNTRIES}

# Condition queries for the "crowded out" sectors
CONDITION_QUERIES = {
    "hiv":           "HIV",
    "cancer":        "cancer OR neoplasm OR oncology",
    "cvd":           "cardiovascular OR heart failure OR coronary",
    "diabetes":      "diabetes OR diabetic",
    "surgery":       "surgery OR surgical OR trauma",
    "mental_health": "depression OR anxiety OR psychosis OR mental health OR schizophrenia",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "dutch_disease_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "dutch-disease.html"
RATE_LIMIT = 0.35
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

REFERENCES = [
    {"pmid": "39972388", "desc": "PEPFAR infrastructure sustainability analysis"},
    {"pmid": "37643290", "desc": "PopART trial long-term outcomes"},
    {"pmid": "36332653", "desc": "Resource allocation and health research priorities in Africa"},
    {"pmid": "34843674", "desc": "Disease-specific funding and research capacity"},
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


def get_trial_count(condition_query, location):
    """Return total count of interventional trials for a condition+location."""
    params = {
        "format": "json",
        "query.cond": condition_query,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_total_interventional(location):
    """Return total count of all interventional trials for a location."""
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
    """Fetch condition-specific trial counts for all countries."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "countries": {},
    }

    all_names = list(ALL_COUNTRIES.keys())
    n_conditions = len(CONDITION_QUERIES)
    # per country: n_conditions + 1 (total) queries
    total_calls = len(all_names) * (n_conditions + 1)
    call_num = 0

    for country in all_names:
        pop = ALL_COUNTRIES[country]
        group = "pepfar" if country in PEPFAR_COUNTRIES else "non_pepfar"
        locn = country

        condition_counts = {}
        for cond_key, cond_query in CONDITION_QUERIES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {cond_key}...")
            condition_counts[cond_key] = get_trial_count(cond_query, locn)
            time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / Total...")
        total_count = get_total_interventional(locn)
        time.sleep(RATE_LIMIT)

        data["countries"][country] = {
            "group": group,
            "population_m": pop,
            "conditions": condition_counts,
            "total_trials": total_count,
        }

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cached to {CACHE_FILE}")

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_metrics(data):
    """Compute Dutch Disease metrics for each country and group summaries."""
    country_metrics = {}

    for country, info in data["countries"].items():
        conds = info["conditions"]
        hiv = conds.get("hiv", 0)
        total = info["total_trials"]
        pop = info["population_m"]

        # Non-HIV sectors
        non_hiv_sectors = {k: v for k, v in conds.items() if k != "hiv"}
        non_hiv_total = sum(non_hiv_sectors.values())

        # Dutch Disease Coefficient: HIV / non-HIV (>1 = diseased)
        if non_hiv_total > 0:
            ddc = round(hiv / non_hiv_total, 3)
        else:
            ddc = float("inf") if hiv > 0 else 0.0

        # HIV Dependency Ratio: HIV / total
        if total > 0:
            dependency_ratio = round(hiv / total, 3)
        else:
            dependency_ratio = 0.0

        # Per-capita rates
        hiv_per_m = round(hiv / pop, 2) if pop > 0 else 0
        non_hiv_per_m = round(non_hiv_total / pop, 2) if pop > 0 else 0

        # Classification
        if ddc > 2.0:
            classification = "Severe Dutch Disease"
        elif ddc > 1.0:
            classification = "Moderate Dutch Disease"
        elif ddc > 0.5:
            classification = "Mild symptoms"
        else:
            classification = "Healthy portfolio"

        country_metrics[country] = {
            "group": info["group"],
            "population_m": pop,
            "hiv_trials": hiv,
            "non_hiv_total": non_hiv_total,
            "sector_counts": non_hiv_sectors,
            "total_trials": total,
            "ddc": ddc if ddc != float("inf") else 999.0,
            "dependency_ratio": dependency_ratio,
            "dependency_pct": round(dependency_ratio * 100, 1),
            "hiv_per_million": hiv_per_m,
            "non_hiv_per_million": non_hiv_per_m,
            "classification": classification,
        }

    # Group summaries
    groups = {"pepfar": [], "non_pepfar": []}
    for country, m in country_metrics.items():
        groups[m["group"]].append(m)

    group_summaries = {}
    for gname, members in groups.items():
        total_hiv = sum(m["hiv_trials"] for m in members)
        total_non_hiv = sum(m["non_hiv_total"] for m in members)
        total_all = sum(m["total_trials"] for m in members)
        total_pop = sum(m["population_m"] for m in members)
        valid_ddc = [m["ddc"] for m in members if m["ddc"] < 999]
        avg_ddc = round(sum(valid_ddc) / max(1, len(valid_ddc)), 3)
        avg_dep = round(
            sum(m["dependency_ratio"] for m in members) / max(1, len(members)), 3
        )
        group_ddc = round(total_hiv / total_non_hiv, 3) if total_non_hiv > 0 else 0
        group_summaries[gname] = {
            "n_countries": len(members),
            "total_hiv": total_hiv,
            "total_non_hiv": total_non_hiv,
            "total_all": total_all,
            "total_pop": total_pop,
            "hiv_per_million": round(total_hiv / total_pop, 2) if total_pop > 0 else 0,
            "non_hiv_per_million": round(total_non_hiv / total_pop, 2) if total_pop > 0 else 0,
            "group_ddc": group_ddc,
            "avg_ddc": avg_ddc,
            "avg_dependency": avg_dep,
        }

    # Sector-level analysis across all countries
    sector_totals = {}
    for cond_key in CONDITION_QUERIES:
        if cond_key == "hiv":
            continue
        sector_totals[cond_key] = {
            "pepfar": sum(
                cm["sector_counts"].get(cond_key, 0)
                for cm in country_metrics.values()
                if cm["group"] == "pepfar"
            ),
            "non_pepfar": sum(
                cm["sector_counts"].get(cond_key, 0)
                for cm in country_metrics.values()
                if cm["group"] == "non_pepfar"
            ),
        }

    return {
        "country_metrics": country_metrics,
        "group_summaries": group_summaries,
        "sector_totals": sector_totals,
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
    gs = metrics["group_summaries"]
    st = metrics["sector_totals"]

    pepfar_gs = gs.get("pepfar", {})
    non_pepfar_gs = gs.get("non_pepfar", {})

    # --- Country table rows sorted by DDC descending (worst first) ---
    sorted_countries = sorted(
        cm.items(),
        key=lambda x: -x[1]["ddc"] if x[1]["ddc"] < 999 else -9999
    )
    country_rows = ""
    for country, m in sorted_countries:
        group_badge = (
            '<span style="background:#7c3aed;padding:2px 8px;border-radius:4px;'
            'font-size:0.75rem;">PEPFAR</span>'
            if m["group"] == "pepfar"
            else '<span style="background:#374151;padding:2px 8px;border-radius:4px;'
            'font-size:0.75rem;">Non-PEPFAR</span>'
        )
        ddc = m["ddc"]
        ddc_display = f"{ddc:.2f}" if ddc < 999 else "N/A"
        ddc_color = (
            "#ef4444" if ddc > 2.0
            else "#f59e0b" if ddc > 1.0
            else "#22c55e"
        )
        cls_color = (
            "#ef4444" if "Severe" in m["classification"]
            else "#f59e0b" if "Moderate" in m["classification"]
            else "#fb923c" if "Mild" in m["classification"]
            else "#22c55e"
        )
        country_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(country)} {group_badge}</td>'
            f'<td style="padding:10px;text-align:right;">{m["hiv_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["non_hiv_total"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["total_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{ddc_color};font-weight:bold;">'
            f'{ddc_display}</td>'
            f'<td style="padding:10px;text-align:right;">{m["dependency_pct"]}%</td>'
            f'<td style="padding:10px;color:{cls_color};font-weight:bold;">'
            f'{escape_html(m["classification"])}</td>'
            f'</tr>\n'
        )

    # --- Chart data ---
    chart_countries = [c for c, _ in sorted_countries]
    chart_hiv = [cm[c]["hiv_trials"] for c in chart_countries]
    chart_non_hiv = [cm[c]["non_hiv_total"] for c in chart_countries]
    chart_labels_json = json.dumps(chart_countries)
    chart_hiv_json = json.dumps(chart_hiv)
    chart_non_hiv_json = json.dumps(chart_non_hiv)

    # --- DDC bar chart data (PEPFAR countries only) ---
    pepfar_sorted = sorted(
        [(c, m) for c, m in cm.items() if m["group"] == "pepfar" and m["ddc"] < 999],
        key=lambda x: -x[1]["ddc"]
    )
    ddc_labels = json.dumps([c for c, _ in pepfar_sorted])
    ddc_values = json.dumps([m["ddc"] for _, m in pepfar_sorted])

    # --- Sector breakdown data ---
    sector_names = list(st.keys())
    sector_pepfar = [st[s]["pepfar"] for s in sector_names]
    sector_nonpepfar = [st[s]["non_pepfar"] for s in sector_names]
    sector_labels_json = json.dumps([s.replace("_", " ").title() for s in sector_names])
    sector_pepfar_json = json.dumps(sector_pepfar)
    sector_nonpepfar_json = json.dumps(sector_nonpepfar)

    # --- Sector detail rows ---
    sector_rows = ""
    for sname in sector_names:
        p_count = st[sname]["pepfar"]
        np_count = st[sname]["non_pepfar"]
        total = p_count + np_count
        label = sname.replace("_", " ").title()
        sector_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(label)}</td>'
            f'<td style="padding:10px;text-align:right;">{p_count:,}</td>'
            f'<td style="padding:10px;text-align:right;">{np_count:,}</td>'
            f'<td style="padding:10px;text-align:right;font-weight:bold;">{total:,}</td>'
            f'</tr>\n'
        )

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
<title>The Dutch Disease of Global Health</title>
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
  --oil: #d97706;
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
  background: linear-gradient(135deg, #d97706, #ef4444);
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
.oil {{ color: var(--oil); }}
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
.insight-box {{
  background: var(--surface);
  border-left: 4px solid var(--danger);
  border-radius: 0 8px 8px 0;
  padding: 1.25rem 1.5rem;
  margin: 1.5rem 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.insight-box.success-box {{ border-left-color: var(--success); }}
.insight-box.warning-box {{ border-left-color: var(--warning); }}
.insight-box.oil-box {{ border-left-color: var(--oil); }}
.metric-def {{
  background: rgba(217, 119, 6, 0.08);
  border: 1px solid rgba(217, 119, 6, 0.2);
  border-radius: 8px;
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  font-size: 0.9rem;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 768px) {{
  .two-col {{ grid-template-columns: 1fr; }}
  h1 {{ font-size: 1.6rem; }}
}}
footer {{
  margin-top: 3rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}}
</style>
</head>
<body>
<div class="container">

<h1>The Dutch Disease of Global Health</h1>
<p class="subtitle">
  HIV Funding as Resource Curse &mdash; How PEPFAR/Global Fund spending
  crowds out cancer, cardiovascular, diabetes, surgical, and mental-health research
  across Africa
</p>

<div class="insight-box oil-box">
  <strong>The Dutch Disease Analogy</strong><br>
  In 1959, the Netherlands discovered a vast natural gas field.
  The resulting export boom caused the Dutch guilder to appreciate,
  making other exports uncompetitive. Manufacturing declined.
  Economists coined the term <em>Dutch Disease</em>: when a boom in
  one sector causes the rest of the economy to wither.<br><br>
  In Africa&rsquo;s research ecosystem, <strong>PEPFAR/Global Fund HIV
  funding is the natural gas</strong>. Billions of dollars flow into
  HIV infrastructure, attracting researchers, building trial sites,
  and training coordinators &mdash; all focused on a single disease.
  Meanwhile, cancer, heart disease, diabetes, surgery, and mental
  health research struggle to compete for the remaining scraps of
  attention and capacity.
</div>

<div class="metric-def">
  <strong>Dutch Disease Coefficient (DDC)</strong> = HIV trials / non-HIV trials<br>
  DDC &gt; 2.0 = <span class="danger">Severe Dutch Disease</span> |
  DDC 1.0&ndash;2.0 = <span class="warning">Moderate Dutch Disease</span> |
  DDC &lt; 1.0 = <span class="success">Healthy portfolio</span><br><br>
  <strong>HIV Dependency Ratio</strong> = HIV trials / total trials<br>
  How much of a country&rsquo;s research portfolio vanishes when
  &ldquo;the oil runs out&rdquo;
</div>

<h2>Summary: PEPFAR vs Non-PEPFAR Countries</h2>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">PEPFAR Group DDC</div>
    <div class="value oil">{pepfar_gs.get("group_ddc", 0):.2f}</div>
    <div class="label">{pepfar_gs.get("n_countries", 0)} countries</div>
  </div>
  <div class="summary-card">
    <div class="label">Non-PEPFAR Group DDC</div>
    <div class="value success">{non_pepfar_gs.get("group_ddc", 0):.2f}</div>
    <div class="label">{non_pepfar_gs.get("n_countries", 0)} countries</div>
  </div>
  <div class="summary-card">
    <div class="label">PEPFAR HIV Trials</div>
    <div class="value danger">{pepfar_gs.get("total_hiv", 0):,}</div>
    <div class="label">&ldquo;the oil&rdquo;</div>
  </div>
  <div class="summary-card">
    <div class="label">PEPFAR Non-HIV Trials</div>
    <div class="value warning">{pepfar_gs.get("total_non_hiv", 0):,}</div>
    <div class="label">&ldquo;the crowded-out sectors&rdquo;</div>
  </div>
  <div class="summary-card">
    <div class="label">PEPFAR Avg Dependency</div>
    <div class="value danger">{round(pepfar_gs.get("avg_dependency", 0) * 100, 1)}%</div>
    <div class="label">HIV share of total trials</div>
  </div>
  <div class="summary-card">
    <div class="label">Non-PEPFAR Avg Dependency</div>
    <div class="value success">{round(non_pepfar_gs.get("avg_dependency", 0) * 100, 1)}%</div>
    <div class="label">HIV share of total trials</div>
  </div>
</div>

<h2>Dutch Disease Coefficient Ranking</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Sorted by DDC descending &mdash; most &ldquo;diseased&rdquo; portfolio first
</p>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">HIV Trials</th>
  <th style="text-align:right;">Non-HIV Trials</th>
  <th style="text-align:right;">Total Trials</th>
  <th style="text-align:right;">DDC</th>
  <th style="text-align:right;">HIV Dep %</th>
  <th>Classification</th>
</tr>
</thead>
<tbody>
{country_rows}
</tbody>
</table>

<h2>Visual: HIV vs Non-HIV Trials per Country</h2>
<div class="chart-container">
  <canvas id="hivNonHivChart" height="100"></canvas>
</div>

<h2>Dutch Disease Coefficient &mdash; PEPFAR Countries</h2>
<div class="chart-container">
  <canvas id="ddcChart" height="80"></canvas>
</div>

<h2>The Crowded-Out Sectors</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Trial counts by disease sector &mdash; the &ldquo;manufacturing&rdquo; that
  withers while the oil boom rages
</p>
<div class="two-col">
  <div>
    <table>
    <thead>
    <tr>
      <th>Sector</th>
      <th style="text-align:right;">PEPFAR Countries</th>
      <th style="text-align:right;">Non-PEPFAR</th>
      <th style="text-align:right;">Total</th>
    </tr>
    </thead>
    <tbody>
    {sector_rows}
    </tbody>
    </table>
  </div>
  <div class="chart-container">
    <canvas id="sectorChart" height="200"></canvas>
  </div>
</div>

<h2>What Happens When the Oil Runs Out?</h2>
<div class="insight-box">
  <strong>PEPFAR Budget Plateau (2020&ndash;present)</strong><br>
  PEPFAR annual funding peaked at ~$7B and has plateaued.
  Political uncertainty means funding could decline further.
  For countries where {round(pepfar_gs.get("avg_dependency", 0) * 100, 1)}% of
  research activity is HIV-focused, a funding cut would not just reduce
  HIV research &mdash; it would collapse the entire clinical trial
  infrastructure built around it.
  <br><br>
  In economics, when the oil runs out, countries that failed to
  diversify face catastrophic recession. In African health research,
  the same pattern threatens: countries that never built NCD, surgical,
  or mental-health trial capacity will have <em>nothing left</em>.
</div>

<div class="insight-box warning-box">
  <strong>The Paradox of Plenty</strong><br>
  Like oil-rich nations that remain poor (Nigeria, Angola, Venezuela),
  PEPFAR-heavy countries may have more <em>total</em> trials but
  less diversified research portfolios. Non-PEPFAR countries
  (DDC: {non_pepfar_gs.get("group_ddc", 0):.2f}) often have healthier
  research ecosystems despite receiving less funding overall.
  The abundance of HIV funding creates a monoculture that is
  brittle and unsustainable.
</div>

<h2>Methods</h2>
<div class="insight-box success-box">
  ClinicalTrials.gov API v2 was queried for interventional trials
  across 15 African countries.  For each country, we retrieved trial
  counts for HIV, cancer, cardiovascular disease, diabetes, surgery,
  and mental health.  The Dutch Disease Coefficient (DDC) was computed
  as HIV trials divided by the sum of all non-HIV sector trials.
  Countries were classified as PEPFAR-heavy (10 countries receiving
  &gt;$100M/yr) or non-PEPFAR comparators (5 countries).  All queries
  and calculations are reproducible from the cached data file.
</div>

<h2>References</h2>
<ul style="list-style:none;padding:0;">
{ref_rows}
</ul>

<footer>
  Data: ClinicalTrials.gov API v2 |
  Generated: {data.get("timestamp", "N/A")[:10]} |
  The Dutch Disease of Global Health v1.0 |
  Open-access research tool
</footer>

</div>

<script>
// HIV vs Non-HIV stacked bar
new Chart(document.getElementById('hivNonHivChart'), {{
  type: 'bar',
  data: {{
    labels: {chart_labels_json},
    datasets: [
      {{
        label: 'HIV Trials ("the oil")',
        data: {chart_hiv_json},
        backgroundColor: 'rgba(239,68,68,0.8)',
      }},
      {{
        label: 'Non-HIV Trials ("crowded-out sectors")',
        data: {chart_non_hiv_json},
        backgroundColor: 'rgba(34,197,94,0.8)',
      }},
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'HIV vs Non-HIV Trials by Country', color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ labels: {{ color: '#94a3b8' }} }},
    }},
    scales: {{
      x: {{ stacked: true, ticks: {{ color: '#94a3b8', maxRotation: 45, minRotation: 45 }}, grid: {{ color: '#1e293b' }} }},
      y: {{ stacked: true, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }}, title: {{ display: true, text: 'Trial count', color: '#94a3b8' }} }},
    }},
  }},
}});

// DDC bar (PEPFAR countries)
new Chart(document.getElementById('ddcChart'), {{
  type: 'bar',
  data: {{
    labels: {ddc_labels},
    datasets: [{{
      label: 'Dutch Disease Coefficient',
      data: {ddc_values},
      backgroundColor: {ddc_values}.map(v => v > 2 ? 'rgba(239,68,68,0.8)' : v > 1 ? 'rgba(245,158,11,0.8)' : 'rgba(34,197,94,0.8)'),
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'Dutch Disease Coefficient (DDC) - PEPFAR Countries', color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ display: false }},
      annotation: {{
        annotations: {{
          threshold: {{
            type: 'line',
            xMin: 1, xMax: 1,
            borderColor: '#f59e0b',
            borderWidth: 2,
            borderDash: [6, 6],
            label: {{ content: 'DDC = 1 (threshold)', display: true, color: '#f59e0b', position: 'end' }},
          }},
        }},
      }},
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }}, title: {{ display: true, text: 'DDC (HIV / non-HIV)', color: '#94a3b8' }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
    }},
  }},
}});

// Sector breakdown
new Chart(document.getElementById('sectorChart'), {{
  type: 'bar',
  data: {{
    labels: {sector_labels_json},
    datasets: [
      {{
        label: 'PEPFAR Countries',
        data: {sector_pepfar_json},
        backgroundColor: 'rgba(124,58,237,0.8)',
      }},
      {{
        label: 'Non-PEPFAR Countries',
        data: {sector_nonpepfar_json},
        backgroundColor: 'rgba(59,130,246,0.8)',
      }},
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'Non-HIV Sector Trials: PEPFAR vs Non-PEPFAR', color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ labels: {{ color: '#94a3b8' }} }},
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }}, title: {{ display: true, text: 'Trial count', color: '#94a3b8' }} }},
    }},
  }},
}});
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("  The Dutch Disease of Global Health")
    print("  HIV Funding as Resource Curse")
    print("=" * 60)

    print("\n[1/3] Fetching trial data...")
    data = fetch_all_data()

    print("\n[2/3] Computing Dutch Disease metrics...")
    metrics = compute_metrics(data)

    cm = metrics["country_metrics"]
    gs = metrics["group_summaries"]

    print("\n--- PEPFAR Group ---")
    print(f"  HIV trials:        {gs['pepfar']['total_hiv']:,}")
    print(f"  Non-HIV trials:    {gs['pepfar']['total_non_hiv']:,}")
    print(f"  Group DDC:         {gs['pepfar']['group_ddc']:.3f}")
    print(f"  Avg Dependency:    {gs['pepfar']['avg_dependency']:.3f}")

    print("\n--- Non-PEPFAR Group ---")
    print(f"  HIV trials:        {gs['non_pepfar']['total_hiv']:,}")
    print(f"  Non-HIV trials:    {gs['non_pepfar']['total_non_hiv']:,}")
    print(f"  Group DDC:         {gs['non_pepfar']['group_ddc']:.3f}")
    print(f"  Avg Dependency:    {gs['non_pepfar']['avg_dependency']:.3f}")

    print("\n--- Per-Country DDC Ranking ---")
    for country, m in sorted(cm.items(), key=lambda x: -x[1]["ddc"] if x[1]["ddc"] < 999 else -9999):
        ddc = m["ddc"]
        ddc_str = f"{ddc:.2f}" if ddc < 999 else "N/A"
        print(f"  {country:20s}  DDC={ddc_str:>8s}  Dep={m['dependency_pct']:5.1f}%  {m['classification']}")

    print(f"\n[3/3] Generating HTML dashboard...")
    html = generate_html(data, metrics)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Written to {OUTPUT_HTML}")
    print(f"  File size: {OUTPUT_HTML.stat().st_size:,} bytes")
    print("\nDone.")


if __name__ == "__main__":
    main()
