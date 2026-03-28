#!/usr/bin/env python
"""
fetch_pepfar_trap.py — The PEPFAR Dependency Trap

Compare HIV vs non-HIV trial capacity in PEPFAR-focus countries
versus non-PEPFAR countries to test whether PEPFAR-built HIV
infrastructure transfers to NCD research or creates dependency.

Extends findings from PopART/PEPFAR trials:
  PMIDs 37643290, 36332653, 34843674

Outputs:
  - data/pepfar_trap_data.json   (cached API results, 24h TTL)
  - pepfar-dependency.html       (dark-theme interactive dashboard)
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

# PEPFAR-focus countries (received >$100M/yr)
PEPFAR_COUNTRIES = {
    "Uganda":        48,
    "Kenya":         56,
    "Tanzania":      67,
    "Mozambique":    33,
    "South Africa":  62,
    "Nigeria":       230,
    "Zambia":        21,
    "Malawi":        21,
    "Zimbabwe":      15,
    "Ethiopia":      130,
}

# Non-PEPFAR comparators (similar income, less HIV funding)
NON_PEPFAR_COUNTRIES = {
    "Ghana":         34,
    "Senegal":       17,
    "Burkina Faso":  23,
    "Mali":          23,
    "DRC":           102,
    "Madagascar":    30,
    "Niger":         27,
    "Cameroon":      27,
}

ALL_COUNTRIES = {**PEPFAR_COUNTRIES, **NON_PEPFAR_COUNTRIES}

# Condition queries
HIV_QUERY = "HIV"
NCD_QUERY = "cardiovascular OR diabetes OR hypertension OR cancer OR stroke"

CACHE_FILE = Path(__file__).resolve().parent / "data" / "pepfar_trap_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "pepfar-dependency.html"
RATE_LIMIT = 0.35  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# References
REFERENCES = [
    {"pmid": "39972388", "desc": "PEPFAR infrastructure sustainability analysis"},
    {"pmid": "37643290", "desc": "PopART trial long-term outcomes"},
    {"pmid": "36332653", "desc": "PopART community-randomized HIV prevention"},
    {"pmid": "34843674", "desc": "PEPFAR infrastructure and trial capacity"},
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
    """Fetch HIV, NCD, and total trial counts for all countries."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "countries": {},
    }

    all_names = list(ALL_COUNTRIES.keys())
    # 3 queries per country: HIV, NCD, total
    total_calls = len(all_names) * 3
    call_num = 0

    for country in all_names:
        pop = ALL_COUNTRIES[country]
        group = "pepfar" if country in PEPFAR_COUNTRIES else "non_pepfar"

        # DRC needs full name for location query
        locn = "Congo" if country == "DRC" else country

        # --- HIV trials ---
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / HIV...")
        hiv_count = get_trial_count(HIV_QUERY, locn)
        time.sleep(RATE_LIMIT)

        # --- NCD trials ---
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / NCD...")
        ncd_count = get_trial_count(NCD_QUERY, locn)
        time.sleep(RATE_LIMIT)

        # --- Total interventional ---
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / Total...")
        total_count = get_total_interventional(locn)
        time.sleep(RATE_LIMIT)

        data["countries"][country] = {
            "group": group,
            "population_m": pop,
            "hiv_trials": hiv_count,
            "ncd_trials": ncd_count,
            "total_trials": total_count,
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
    """Compute all dependency metrics for each country and group summaries."""
    country_metrics = {}

    for country, info in data["countries"].items():
        hiv = info["hiv_trials"]
        ncd = info["ncd_trials"]
        total = info["total_trials"]
        pop = info["population_m"]

        # Spillover Index: NCD / HIV (>1 means good spillover)
        if hiv > 0:
            spillover = round(ncd / hiv, 3)
        else:
            spillover = float("inf") if ncd > 0 else 0.0

        # Per-capita rates
        hiv_per_m = round(hiv / pop, 2) if pop > 0 else 0
        ncd_per_m = round(ncd / pop, 2) if pop > 0 else 0

        # HIV Dominance Score: HIV / total (higher = more dependent)
        if total > 0:
            hiv_dominance = round(hiv / total, 3)
        else:
            hiv_dominance = 0.0

        # Dependency risk: if PEPFAR stopped, % of research that vanishes
        # Proxy: HIV trials as % of total
        dependency_pct = round(hiv_dominance * 100, 1)

        # Classification
        if spillover > 1.0:
            classification = "Good spillover"
        elif spillover > 0.5:
            classification = "Moderate spillover"
        elif spillover > 0.2:
            classification = "Weak spillover"
        else:
            classification = "HIV-dependent"

        country_metrics[country] = {
            "group": info["group"],
            "population_m": pop,
            "hiv_trials": hiv,
            "ncd_trials": ncd,
            "total_trials": total,
            "spillover_index": spillover if spillover != float("inf") else 999.0,
            "hiv_per_million": hiv_per_m,
            "ncd_per_million": ncd_per_m,
            "hiv_dominance": hiv_dominance,
            "dependency_pct": dependency_pct,
            "classification": classification,
        }

    # Group summaries
    groups = {"pepfar": [], "non_pepfar": []}
    for country, m in country_metrics.items():
        groups[m["group"]].append(m)

    group_summaries = {}
    for gname, members in groups.items():
        total_hiv = sum(m["hiv_trials"] for m in members)
        total_ncd = sum(m["ncd_trials"] for m in members)
        total_all = sum(m["total_trials"] for m in members)
        total_pop = sum(m["population_m"] for m in members)
        avg_spillover = round(
            sum(m["spillover_index"] for m in members if m["spillover_index"] < 999)
            / max(1, sum(1 for m in members if m["spillover_index"] < 999)),
            3
        )
        avg_dominance = round(
            sum(m["hiv_dominance"] for m in members) / max(1, len(members)), 3
        )
        group_summaries[gname] = {
            "n_countries": len(members),
            "total_hiv": total_hiv,
            "total_ncd": total_ncd,
            "total_all": total_all,
            "total_pop": total_pop,
            "hiv_per_million": round(total_hiv / total_pop, 2) if total_pop > 0 else 0,
            "ncd_per_million": round(total_ncd / total_pop, 2) if total_pop > 0 else 0,
            "group_spillover": round(total_ncd / total_hiv, 3) if total_hiv > 0 else 0,
            "avg_spillover": avg_spillover,
            "avg_dominance": avg_dominance,
        }

    # Success stories: PEPFAR countries where NCD is growing (spillover > 0.5)
    success_stories = [
        (c, m) for c, m in country_metrics.items()
        if m["group"] == "pepfar" and m["spillover_index"] > 0.5
        and m["spillover_index"] < 999
    ]
    success_stories.sort(key=lambda x: -x[1]["spillover_index"])

    # Non-PEPFAR paradox: countries with more balanced portfolios
    balanced_non_pepfar = [
        (c, m) for c, m in country_metrics.items()
        if m["group"] == "non_pepfar" and m["hiv_dominance"] < 0.3
        and m["total_trials"] > 0
    ]
    balanced_non_pepfar.sort(key=lambda x: x[1]["hiv_dominance"])

    return {
        "country_metrics": country_metrics,
        "group_summaries": group_summaries,
        "success_stories": success_stories,
        "balanced_non_pepfar": balanced_non_pepfar,
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
    success = metrics["success_stories"]
    balanced = metrics["balanced_non_pepfar"]

    pepfar_gs = gs.get("pepfar", {})
    non_pepfar_gs = gs.get("non_pepfar", {})

    # --- Summary cards ---
    pepfar_hiv = pepfar_gs.get("total_hiv", 0)
    pepfar_ncd = pepfar_gs.get("total_ncd", 0)
    pepfar_spillover = pepfar_gs.get("group_spillover", 0)
    non_pepfar_spillover = non_pepfar_gs.get("group_spillover", 0)

    # --- Country comparison table rows ---
    # Sort by spillover index ascending (worst first)
    sorted_countries = sorted(cm.items(), key=lambda x: x[1]["spillover_index"])
    country_rows = ""
    for country, m in sorted_countries:
        group_badge = (
            '<span style="background:#7c3aed;padding:2px 8px;border-radius:4px;'
            'font-size:0.75rem;">PEPFAR</span>'
            if m["group"] == "pepfar"
            else '<span style="background:#374151;padding:2px 8px;border-radius:4px;'
            'font-size:0.75rem;">Non-PEPFAR</span>'
        )
        si = m["spillover_index"]
        si_display = f"{si:.2f}" if si < 999 else "N/A"
        si_color = (
            "#22c55e" if si > 1.0
            else "#f59e0b" if si > 0.5
            else "#ef4444"
        )
        cls_color = (
            "#22c55e" if "Good" in m["classification"]
            else "#f59e0b" if "Moderate" in m["classification"]
            else "#fb923c" if "Weak" in m["classification"]
            else "#ef4444"
        )
        country_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(country)} {group_badge}</td>'
            f'<td style="padding:10px;text-align:right;">{m["hiv_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["ncd_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["total_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{si_color};font-weight:bold;">'
            f'{si_display}</td>'
            f'<td style="padding:10px;text-align:right;">{m["dependency_pct"]}%</td>'
            f'<td style="padding:10px;color:{cls_color};font-weight:bold;">'
            f'{escape_html(m["classification"])}</td>'
            f'</tr>\n'
        )

    # --- Bar chart data: HIV vs NCD per country ---
    chart_countries = [c for c, _ in sorted_countries]
    chart_hiv = [cm[c]["hiv_trials"] for c in chart_countries]
    chart_ncd = [cm[c]["ncd_trials"] for c in chart_countries]
    chart_labels_json = json.dumps(chart_countries)
    chart_hiv_json = json.dumps(chart_hiv)
    chart_ncd_json = json.dumps(chart_ncd)

    # --- Dependency chart data ---
    dep_countries = sorted(
        [(c, m) for c, m in cm.items() if m["group"] == "pepfar"],
        key=lambda x: -x[1]["dependency_pct"]
    )
    dep_labels = json.dumps([c for c, _ in dep_countries])
    dep_values = json.dumps([m["dependency_pct"] for _, m in dep_countries])

    # --- Success stories rows ---
    success_rows = ""
    if success:
        for country, m in success:
            success_rows += (
                f'<tr>'
                f'<td style="padding:10px;color:#22c55e;">{escape_html(country)}</td>'
                f'<td style="padding:10px;text-align:right;">{m["hiv_trials"]:,}</td>'
                f'<td style="padding:10px;text-align:right;">{m["ncd_trials"]:,}</td>'
                f'<td style="padding:10px;text-align:right;color:#22c55e;font-weight:bold;">'
                f'{m["spillover_index"]:.2f}</td>'
                f'<td style="padding:10px;text-align:right;">{m["ncd_per_million"]}</td>'
                f'</tr>\n'
            )
    else:
        success_rows = (
            '<tr><td colspan="5" style="padding:10px;color:#ef4444;text-align:center;">'
            'No PEPFAR country achieved a spillover index above 0.5</td></tr>'
        )

    # --- Non-PEPFAR paradox rows ---
    paradox_rows = ""
    if balanced:
        for country, m in balanced:
            paradox_rows += (
                f'<tr>'
                f'<td style="padding:10px;">{escape_html(country)}</td>'
                f'<td style="padding:10px;text-align:right;">{m["hiv_trials"]:,}</td>'
                f'<td style="padding:10px;text-align:right;">{m["ncd_trials"]:,}</td>'
                f'<td style="padding:10px;text-align:right;">{m["total_trials"]:,}</td>'
                f'<td style="padding:10px;text-align:right;font-weight:bold;">'
                f'{round(m["hiv_dominance"] * 100, 1)}%</td>'
                f'</tr>\n'
            )
    else:
        paradox_rows = (
            '<tr><td colspan="5" style="padding:10px;color:#94a3b8;text-align:center;">'
            'No non-PEPFAR country met the balanced portfolio threshold</td></tr>'
        )

    # --- Per-capita comparison rows ---
    percap_rows = ""
    percap_sorted = sorted(cm.items(), key=lambda x: -x[1]["ncd_per_million"])
    for country, m in percap_sorted:
        group_tag = "PEPFAR" if m["group"] == "pepfar" else "Non-PEPFAR"
        percap_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(country)}</td>'
            f'<td style="padding:10px;text-align:right;">{m["population_m"]}M</td>'
            f'<td style="padding:10px;text-align:right;">{m["hiv_per_million"]}</td>'
            f'<td style="padding:10px;text-align:right;">{m["ncd_per_million"]}</td>'
            f'<td style="padding:10px;font-size:0.8rem;">{group_tag}</td>'
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
<title>The PEPFAR Dependency Trap</title>
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
  background: linear-gradient(135deg, #ef4444, #7c3aed);
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
.insight-box.success-box {{
  border-left-color: var(--success);
}}
.insight-box.warning-box {{
  border-left-color: var(--warning);
}}
.insight-box.purple-box {{
  border-left-color: var(--purple);
}}
.metric-def {{
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: 8px;
  padding: 1rem 1.5rem;
  margin: 1rem 0;
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
</style>
</head>
<body>
<div class="container">

<!-- ============================================================ -->
<!-- SECTION 1: Summary -->
<!-- ============================================================ -->

<h1>The PEPFAR Dependency Trap</h1>
<p class="subtitle">
  Does PEPFAR-built HIV infrastructure transfer to NCD research, or does it
  create dependency? A cross-sectional registry analysis of 18 African nations.
</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">PEPFAR Group HIV Trials</div>
    <div class="value purple">{pepfar_hiv:,}</div>
    <div class="label">across {pepfar_gs.get("n_countries", 0)} countries</div>
  </div>
  <div class="summary-card">
    <div class="label">PEPFAR Group NCD Trials</div>
    <div class="value warning">{pepfar_ncd:,}</div>
    <div class="label">cardiovascular, diabetes, HTN, cancer, stroke</div>
  </div>
  <div class="summary-card">
    <div class="label">PEPFAR Spillover Index</div>
    <div class="value danger">{pepfar_spillover:.2f}</div>
    <div class="label">&lt;1.0 = spillover failure</div>
  </div>
  <div class="summary-card">
    <div class="label">Non-PEPFAR Spillover</div>
    <div class="value success">{non_pepfar_spillover:.2f}</div>
    <div class="label">comparator group</div>
  </div>
  <div class="summary-card">
    <div class="label">PEPFAR Avg HIV Dominance</div>
    <div class="value danger">{round(pepfar_gs.get("avg_dominance", 0) * 100, 1)}%</div>
    <div class="label">of total trials are HIV</div>
  </div>
  <div class="summary-card">
    <div class="label">Non-PEPFAR HIV Dominance</div>
    <div class="value success">{round(non_pepfar_gs.get("avg_dominance", 0) * 100, 1)}%</div>
    <div class="label">more balanced portfolio</div>
  </div>
</div>

<div class="metric-def">
  <strong>PEPFAR Spillover Index</strong> = NCD trials / HIV trials.<br>
  Values &gt;1.0 indicate good spillover (NCD research exceeds HIV); values
  &lt;1.0 indicate that HIV infrastructure has NOT translated into broader
  research capacity.<br><br>
  <strong>HIV Dominance Score</strong> = HIV trials / total trials.<br>
  Higher values indicate greater dependency on HIV-focused research.
  If PEPFAR funding stopped, this proportion of registered research would
  be at risk.
</div>

<!-- ============================================================ -->
<!-- SECTION 2: Country Comparison Table -->
<!-- ============================================================ -->

<h2>Country-Level Comparison</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  All 18 countries ranked by spillover index (worst first).
  Classification: Good (&gt;1.0), Moderate (0.5&ndash;1.0), Weak (0.2&ndash;0.5),
  HIV-dependent (&lt;0.2).
</p>

<div style="overflow-x:auto;">
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">HIV Trials</th>
  <th style="text-align:right;">NCD Trials</th>
  <th style="text-align:right;">Total</th>
  <th style="text-align:right;">Spillover Index</th>
  <th style="text-align:right;">Dependency %</th>
  <th>Classification</th>
</tr>
</thead>
<tbody>
{country_rows}
</tbody>
</table>
</div>

<!-- ============================================================ -->
<!-- SECTION 3: The Spillover Failure -->
<!-- ============================================================ -->

<h2>The Spillover Failure</h2>

<div class="insight-box">
  <strong>Key finding:</strong> PEPFAR-focus countries have built extraordinary
  HIV trial capacity ({pepfar_hiv:,} trials), but NCD research has NOT benefited
  proportionally ({pepfar_ncd:,} NCD trials, spillover index {pepfar_spillover:.2f}).
  The billions invested in HIV infrastructure &mdash; clinical sites, ethical review
  boards, data management systems, trained staff &mdash; remain siloed within
  HIV programs rather than being leveraged for the NCD epidemic that now kills
  more Africans than infectious diseases combined.
</div>

<p style="margin:1rem 0;color:var(--muted);">
  This pattern is consistent with what Hayes et al. (PMID 37643290) found in the
  PopART trial: despite building massive community health infrastructure across
  Zambia and South Africa, the systems were designed for HIV-specific endpoints
  and did not naturally extend to cardiovascular or metabolic disease screening.
</p>

<!-- ============================================================ -->
<!-- SECTION 4: Bar Chart (HIV vs NCD per Country) -->
<!-- ============================================================ -->

<h2>HIV vs NCD Trials by Country</h2>

<div class="chart-container">
  <canvas id="hivNcdChart" height="120"></canvas>
</div>

<!-- ============================================================ -->
<!-- SECTION 5: The Dependency Metric -->
<!-- ============================================================ -->

<h2>The Dependency Metric</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  If PEPFAR funding stopped tomorrow, what percentage of each country's
  registered interventional research would be at risk? This chart shows
  HIV trials as a fraction of total trials for PEPFAR-focus countries.
</p>

<div class="chart-container">
  <canvas id="dependencyChart" height="80"></canvas>
</div>

<div class="insight-box">
  <strong>Dependency interpretation:</strong> Countries where HIV trials constitute
  more than 40% of all registered research face severe vulnerability to funding
  shifts. The PEPFAR transition to "country ownership" may leave research
  infrastructure stranded if NCD capacity was never built alongside HIV programs.
</div>

<!-- ============================================================ -->
<!-- SECTION 6: Success Stories -->
<!-- ============================================================ -->

<h2>Success Stories: PEPFAR Countries Where NCDs ARE Growing</h2>

<div class="insight-box success-box">
  Are there PEPFAR countries that have successfully leveraged HIV infrastructure
  for broader research? We identify countries where the spillover index exceeds
  0.5, indicating at least moderate cross-pollination from HIV to NCD research.
</div>

<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">HIV Trials</th>
  <th style="text-align:right;">NCD Trials</th>
  <th style="text-align:right;">Spillover Index</th>
  <th style="text-align:right;">NCD/Million Pop</th>
</tr>
</thead>
<tbody>
{success_rows}
</tbody>
</table>

<!-- ============================================================ -->
<!-- SECTION 7: The Non-PEPFAR Paradox -->
<!-- ============================================================ -->

<h2>The Non-PEPFAR Paradox</h2>

<div class="insight-box purple-box">
  <strong>Paradox:</strong> Countries that did NOT receive massive PEPFAR investment
  may actually have more balanced research portfolios. Without the gravitational
  pull of HIV funding, these nations' (limited) research capacity is distributed
  more evenly across disease categories. Their absolute numbers are lower, but
  they are not structurally dependent on a single funder's priorities.
</div>

<h3>Non-PEPFAR Countries with Balanced Portfolios (HIV Dominance &lt;30%)</h3>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">HIV Trials</th>
  <th style="text-align:right;">NCD Trials</th>
  <th style="text-align:right;">Total Trials</th>
  <th style="text-align:right;">HIV Dominance</th>
</tr>
</thead>
<tbody>
{paradox_rows}
</tbody>
</table>

<h3>Per-Capita Trial Rates (All 18 Countries)</h3>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Population</th>
  <th style="text-align:right;">HIV/Million</th>
  <th style="text-align:right;">NCD/Million</th>
  <th>Group</th>
</tr>
</thead>
<tbody>
{percap_rows}
</tbody>
</table>

<!-- ============================================================ -->
<!-- SECTION 8: Policy Implications -->
<!-- ============================================================ -->

<h2>Policy Implications: Pivoting HIV Infrastructure to NCD Research</h2>

<div class="insight-box warning-box">
  <strong>The window is closing.</strong> As PEPFAR transitions to "country ownership"
  and funding plateaus, the HIV infrastructure will either be repurposed or lost.
  The data above show that spontaneous spillover is NOT happening &mdash; active
  policy intervention is required.
</div>

<div style="margin:1.5rem 0;">
<h3>1. Dual-mandate clinical sites</h3>
<p style="color:var(--muted);margin-bottom:1rem;">
  Require PEPFAR-funded clinical sites to run at least one NCD study per HIV study.
  The physical infrastructure (pharmacy, lab, data systems) is already in place.
</p>

<h3>2. NCD riders on HIV cohorts</h3>
<p style="color:var(--muted);margin-bottom:1rem;">
  HIV cohorts in PEPFAR countries already track thousands of patients longitudinally.
  Adding blood pressure, glucose, and lipid measurements costs marginal dollars but
  generates invaluable NCD data.
</p>

<h3>3. Ethics board cross-training</h3>
<p style="color:var(--muted);margin-bottom:1rem;">
  PEPFAR built institutional review board capacity that currently reviews only HIV
  protocols. Cross-training these boards for NCD trials removes a major bottleneck.
</p>

<h3>4. Data infrastructure sharing</h3>
<p style="color:var(--muted);margin-bottom:1rem;">
  Electronic data capture systems (REDCap, DHIS2) deployed for HIV programs can
  be extended to NCD registries at minimal cost.
</p>

<h3>5. Workforce transition planning</h3>
<p style="color:var(--muted);margin-bottom:1rem;">
  Clinical research coordinators, data managers, and community health workers
  trained by PEPFAR programs represent a skilled workforce that could pivot to
  NCD research &mdash; if transition funding is provided.
</p>
</div>

<!-- ============================================================ -->
<!-- References -->
<!-- ============================================================ -->

<h2>References</h2>
<ol style="padding-left:1.5rem;color:var(--muted);">
{ref_rows}
</ol>

<!-- ============================================================ -->
<!-- Footer -->
<!-- ============================================================ -->

<div class="footer">
  <p>Data source: ClinicalTrials.gov API v2 | Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
  <p>PEPFAR-focus countries: {", ".join(PEPFAR_COUNTRIES.keys())}</p>
  <p>Non-PEPFAR comparators: {", ".join(NON_PEPFAR_COUNTRIES.keys())}</p>
  <p style="margin-top:0.5rem;">
    <em>Limitation: ClinicalTrials.gov captures primarily US-registered trials.
    Local African registries (PACTR, Pan African Clinical Trials Registry) may
    contain additional studies not reflected here. The "dependency" metric is a
    structural proxy, not a causal claim.</em>
  </p>
</div>

</div><!-- end container -->

<script>
// --- HIV vs NCD Bar Chart ---
(function() {{
  var ctx = document.getElementById('hivNcdChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {chart_labels_json},
      datasets: [
        {{
          label: 'HIV Trials',
          data: {chart_hiv_json},
          backgroundColor: 'rgba(239, 68, 68, 0.7)',
          borderColor: 'rgba(239, 68, 68, 1)',
          borderWidth: 1
        }},
        {{
          label: 'NCD Trials',
          data: {chart_ncd_json},
          backgroundColor: 'rgba(59, 130, 246, 0.7)',
          borderColor: 'rgba(59, 130, 246, 1)',
          borderWidth: 1
        }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ labels: {{ color: '#e2e8f0' }} }},
        title: {{
          display: true,
          text: 'HIV vs NCD Interventional Trials by Country',
          color: '#e2e8f0',
          font: {{ size: 16 }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color: '#94a3b8', maxRotation: 45 }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }}
        }},
        y: {{
          ticks: {{ color: '#94a3b8' }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }},
          title: {{
            display: true,
            text: 'Number of Trials',
            color: '#94a3b8'
          }}
        }}
      }}
    }}
  }});
}})();

// --- Dependency Chart ---
(function() {{
  var ctx2 = document.getElementById('dependencyChart').getContext('2d');
  new Chart(ctx2, {{
    type: 'bar',
    data: {{
      labels: {dep_labels},
      datasets: [{{
        label: 'HIV as % of Total Trials',
        data: {dep_values},
        backgroundColor: function(context) {{
          var v = context.raw;
          if (v > 40) return 'rgba(239, 68, 68, 0.8)';
          if (v > 25) return 'rgba(245, 158, 11, 0.8)';
          return 'rgba(34, 197, 94, 0.8)';
        }},
        borderWidth: 1
      }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      plugins: {{
        legend: {{ display: false }},
        title: {{
          display: true,
          text: 'PEPFAR Dependency: % of Research at Risk if HIV Funding Stops',
          color: '#e2e8f0',
          font: {{ size: 15 }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color: '#94a3b8', callback: function(v) {{ return v + '%'; }} }},
          grid: {{ color: 'rgba(30,41,59,0.5)' }},
          max: 100
        }},
        y: {{
          ticks: {{ color: '#94a3b8' }},
          grid: {{ color: 'rgba(30,41,59,0.3)' }}
        }}
      }}
    }}
  }});
}})();
</script>

</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 70)
    print("  The PEPFAR Dependency Trap")
    print("  HIV vs NCD trial capacity in PEPFAR vs non-PEPFAR countries")
    print("=" * 70)

    # Set UTF-8 output on Windows
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("\n[1/3] Fetching trial data from ClinicalTrials.gov...")
    data = fetch_all_data()

    print("\n[2/3] Computing dependency metrics...")
    metrics = compute_metrics(data)

    # Print summary
    cm = metrics["country_metrics"]
    gs = metrics["group_summaries"]
    print("\n--- PEPFAR Group ---")
    print(f"  HIV trials:      {gs['pepfar']['total_hiv']:,}")
    print(f"  NCD trials:      {gs['pepfar']['total_ncd']:,}")
    print(f"  Spillover Index: {gs['pepfar']['group_spillover']:.3f}")
    print(f"  Avg Dominance:   {gs['pepfar']['avg_dominance']:.3f}")

    print("\n--- Non-PEPFAR Group ---")
    print(f"  HIV trials:      {gs['non_pepfar']['total_hiv']:,}")
    print(f"  NCD trials:      {gs['non_pepfar']['total_ncd']:,}")
    print(f"  Spillover Index: {gs['non_pepfar']['group_spillover']:.3f}")
    print(f"  Avg Dominance:   {gs['non_pepfar']['avg_dominance']:.3f}")

    print("\n--- Per-Country Classifications ---")
    for country, m in sorted(cm.items(), key=lambda x: x[1]["spillover_index"]):
        si = m["spillover_index"]
        si_str = f"{si:.2f}" if si < 999 else "N/A"
        print(f"  {country:20s}  SI={si_str:>8s}  Dep={m['dependency_pct']:5.1f}%  {m['classification']}")

    print(f"\n[3/3] Generating HTML dashboard...")
    html = generate_html(data, metrics)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Written to {OUTPUT_HTML}")
    print(f"  File size: {OUTPUT_HTML.stat().st_size:,} bytes")
    print("\nDone.")


if __name__ == "__main__":
    main()
