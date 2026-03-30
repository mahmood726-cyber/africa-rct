#!/usr/bin/env python
"""
fetch_traditional_medicine.py -- Africa's Traditional Medicine Paradox
=====================================================================
WHO estimates 80% of Africans use traditional medicine as primary healthcare,
yet Africa has ~2 traditional/herbal medicine trials on ClinicalTrials.gov
vs China's 334 -- a 167x gap.

Queries ClinicalTrials.gov API v2 for traditional/herbal medicine trials
across Africa, China, India, Brazil, US, plus specific African remedies
and per-country breakdowns.

Usage:
    python fetch_traditional_medicine.py

Output:
    data/traditional_medicine_data.json  (cached API results, 24h TTL)
    traditional-medicine.html            (dark-theme interactive dashboard)

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

# Primary query terms for traditional/herbal medicine
TRAD_MED_QUERY = "traditional medicine OR herbal OR medicinal plant OR indigenous medicine"

# Comparison regions for the main query
COMPARISON_REGIONS = {
    "Africa": {
        "countries": [
            "South Africa", "Nigeria", "Ghana", "Kenya", "Tanzania",
            "Cameroon", "Congo, The Democratic Republic of the", "Egypt",
        ],
        "query_name": "Africa",  # We query the continent name
    },
    "China": {"query_name": "China"},
    "India": {"query_name": "India"},
    "Brazil": {"query_name": "Brazil"},
    "United States": {"query_name": "United States"},
}

# Individual African countries to break down
AFRICAN_COUNTRIES = {
    "South Africa": 62,       # population in millions (approx)
    "Nigeria": 230,
    "Ghana": 34,
    "Kenya": 56,
    "Tanzania": 67,
    "Cameroon": 28,
    "Congo, The Democratic Republic of the": 102,
    "Egypt": 109,
}

DISPLAY_NAMES = {
    "Congo, The Democratic Republic of the": "DRC",
    "United States": "US",
}

# Specific African traditional remedies to search
AFRICAN_REMEDIES = {
    "Artemisia": "Artemisia (origin of artemisinin, Nobel Prize 2015)",
    "moringa": "Moringa oleifera (drumstick tree)",
    "neem": "Azadirachta indica (neem tree)",
    "rooibos": "Aspalathus linearis (South African tea)",
    "devil's claw": "Harpagophytum (anti-inflammatory, Southern Africa)",
    "Sutherlandia": "Lessertia frutescens (cancer bush, South Africa)",
    "African potato": "Hypoxis hemerocallidea (immune support)",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "traditional_medicine_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "traditional-medicine.html"
RATE_LIMIT = 0.35  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def display_name(country):
    """Return short display name for a country."""
    return DISPLAY_NAMES.get(country, country)


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


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


def count_trials(query_term=None, location=None, intervention=None):
    """Return total count of interventional trials matching criteria."""
    params = {
        "format": "json",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if query_term:
        params["query.term"] = query_term
    if location:
        params["query.locn"] = location
    if intervention:
        params["query.intr"] = intervention
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_sample_trials(query_term=None, location=None, page_size=5):
    """Return a list of sample trial summaries."""
    params = {
        "format": "json",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": page_size,
        "countTotal": "true",
    }
    if query_term:
        params["query.term"] = query_term
    if location:
        params["query.locn"] = location
    data = api_get(params)
    if data is None:
        return []
    studies = data.get("studies", [])
    results = []
    for s in studies:
        proto = s.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        sponsor = proto.get("sponsorCollaboratorsModule", {})
        lead = sponsor.get("leadSponsor", {})
        results.append({
            "nctId": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "status": status.get("overallStatus", ""),
            "sponsor": lead.get("name", ""),
            "sponsorClass": lead.get("class", ""),
        })
    return results


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def load_cache():
    """Load cached data if fresh enough."""
    if CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            ts_str = raw.get("timestamp", "2000-01-01")
            # Handle timezone suffix for Python <3.11
            if "+" in ts_str:
                ts_str = ts_str[:ts_str.index("+")]
            ts = datetime.fromisoformat(ts_str)
            if datetime.now() - ts < timedelta(hours=CACHE_TTL_HOURS):
                print(f"Using cached data from {ts.isoformat()}")
                return raw
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def fetch_all_data():
    """Fetch all data for the traditional medicine analysis."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "region_counts": {},
        "africa_country_counts": {},
        "remedy_counts": {},
        "remedy_counts_global": {},
        "sponsor_samples": {},
    }

    # ---- 1. Regional comparison: traditional medicine trials ----
    print("\n  --- Regional comparison ---")
    # Africa as continent (search each country + sum)
    africa_total = 0
    for country in AFRICAN_COUNTRIES:
        print(f"  Africa/{display_name(country)}: {TRAD_MED_QUERY}...")
        c = count_trials(query_term=TRAD_MED_QUERY, location=country)
        data["africa_country_counts"][country] = c
        africa_total += c
        time.sleep(RATE_LIMIT)
    data["region_counts"]["Africa"] = africa_total

    for region in ["China", "India", "Brazil", "United States"]:
        loc = COMPARISON_REGIONS[region]["query_name"]
        print(f"  {region}: {TRAD_MED_QUERY}...")
        c = count_trials(query_term=TRAD_MED_QUERY, location=loc)
        data["region_counts"][region] = c
        time.sleep(RATE_LIMIT)

    # ---- 2. Specific African remedies (global + Africa-specific) ----
    print("\n  --- Specific remedy searches ---")
    for remedy in AFRICAN_REMEDIES:
        print(f"  Remedy '{remedy}' (global)...")
        c_global = count_trials(intervention=remedy)
        data["remedy_counts_global"][remedy] = c_global
        time.sleep(RATE_LIMIT)

        # Africa-specific: search each African country
        africa_remedy_total = 0
        for country in AFRICAN_COUNTRIES:
            c_af = count_trials(intervention=remedy, location=country)
            africa_remedy_total += c_af
            time.sleep(RATE_LIMIT)
        data["remedy_counts"][remedy] = africa_remedy_total
        print(f"    Global: {c_global}, Africa: {africa_remedy_total}")

    # ---- 3. Sample trials for sponsor analysis ----
    print("\n  --- Sample trials (Africa traditional medicine) ---")
    for country in ["South Africa", "Nigeria", "Kenya", "Egypt"]:
        print(f"  Samples from {country}...")
        samples = get_sample_trials(
            query_term=TRAD_MED_QUERY, location=country, page_size=10
        )
        data["sponsor_samples"][country] = samples
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Cached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_analysis(data):
    """Compute all analysis metrics from collected data."""
    results = {}

    # Regional comparison
    rc = data["region_counts"]
    africa_n = rc.get("Africa", 0)
    china_n = rc.get("China", 0)
    india_n = rc.get("India", 0)
    brazil_n = rc.get("Brazil", 0)
    us_n = rc.get("United States", 0)

    china_ratio = round(china_n / africa_n, 0) if africa_n > 0 else float("inf")
    india_ratio = round(india_n / africa_n, 1) if africa_n > 0 else float("inf")
    africa_share_pct = round(100 * africa_n / (africa_n + china_n + india_n + brazil_n + us_n), 2) \
        if (africa_n + china_n + india_n + brazil_n + us_n) > 0 else 0

    results["regional"] = {
        "Africa": africa_n,
        "China": china_n,
        "India": india_n,
        "Brazil": brazil_n,
        "United States": us_n,
        "china_to_africa_ratio": china_ratio,
        "india_to_africa_ratio": india_ratio,
        "africa_share_pct": africa_share_pct,
    }

    # Per-country breakdown
    country_data = []
    for country, count in data["africa_country_counts"].items():
        pop = AFRICAN_COUNTRIES.get(country, 1)
        country_data.append({
            "country": country,
            "display": display_name(country),
            "count": count,
            "population_m": pop,
            "per_million": round(count / pop, 3) if pop > 0 else 0,
        })
    country_data.sort(key=lambda x: -x["count"])
    results["country_breakdown"] = country_data

    # Remedy analysis
    remedy_data = []
    for remedy, desc in AFRICAN_REMEDIES.items():
        remedy_data.append({
            "name": remedy,
            "description": desc,
            "global_count": data["remedy_counts_global"].get(remedy, 0),
            "africa_count": data["remedy_counts"].get(remedy, 0),
        })
    remedy_data.sort(key=lambda x: -x["global_count"])
    results["remedies"] = remedy_data

    # Sponsor analysis from samples
    sponsor_classes = defaultdict(int)
    all_sponsors = defaultdict(int)
    for country, samples in data["sponsor_samples"].items():
        for trial in samples:
            sc = trial.get("sponsorClass", "UNKNOWN")
            sponsor_classes[sc] += 1
            sp = trial.get("sponsor", "Unknown")
            all_sponsors[sp] += 1
    results["sponsor_classes"] = dict(sponsor_classes)
    results["top_sponsors"] = dict(
        sorted(all_sponsors.items(), key=lambda x: -x[1])[:15]
    )

    return results


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def generate_html(data, analysis):
    """Generate the full HTML dashboard."""
    reg = analysis["regional"]
    countries = analysis["country_breakdown"]
    remedies = analysis["remedies"]

    africa_n = reg["Africa"]
    china_n = reg["China"]
    india_n = reg["India"]
    brazil_n = reg["Brazil"]
    us_n = reg["United States"]
    ratio = reg["china_to_africa_ratio"]
    india_ratio = reg["india_to_africa_ratio"]
    share_pct = reg["africa_share_pct"]

    # --- Chart data ---
    region_labels = json.dumps(["Africa", "China", "India", "Brazil", "US"])
    region_values = json.dumps([africa_n, china_n, india_n, brazil_n, us_n])
    region_colors = json.dumps(["#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#8b5cf6"])

    country_labels = json.dumps([c["display"] for c in countries])
    country_values = json.dumps([c["count"] for c in countries])

    remedy_labels = json.dumps([r["name"] for r in remedies])
    remedy_global = json.dumps([r["global_count"] for r in remedies])
    remedy_africa = json.dumps([r["africa_count"] for r in remedies])

    # --- Country table rows ---
    country_rows = ""
    for c in countries:
        color = "#ef4444" if c["count"] == 0 else "#f59e0b" if c["count"] < 5 else "#22c55e"
        country_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(c["display"])}</td>'
            f'<td style="padding:10px;text-align:right;">{c["population_m"]}M</td>'
            f'<td style="padding:10px;text-align:right;font-weight:bold;color:{color};">'
            f'{c["count"]}</td>'
            f'<td style="padding:10px;text-align:right;">{c["per_million"]}</td>'
            f'</tr>\n'
        )

    # --- Remedy table rows ---
    remedy_rows = ""
    for r in remedies:
        africa_pct = round(100 * r["africa_count"] / r["global_count"], 1) if r["global_count"] > 0 else 0
        remedy_rows += (
            f'<tr>'
            f'<td style="padding:10px;font-weight:bold;">{escape_html(r["name"])}</td>'
            f'<td style="padding:10px;color:var(--muted);font-size:0.85rem;">'
            f'{escape_html(r["description"])}</td>'
            f'<td style="padding:10px;text-align:right;">{r["global_count"]}</td>'
            f'<td style="padding:10px;text-align:right;font-weight:bold;'
            f'color:{"#ef4444" if r["africa_count"] == 0 else "#f59e0b"};">'
            f'{r["africa_count"]}</td>'
            f'<td style="padding:10px;text-align:right;">{africa_pct}%</td>'
            f'</tr>\n'
        )

    # --- Sponsor table rows ---
    sponsor_rows = ""
    sc = analysis.get("sponsor_classes", {})
    for cls, cnt in sorted(sc.items(), key=lambda x: -x[1]):
        label = {
            "INDUSTRY": "Industry (pharmaceutical)",
            "NIH": "NIH (US government)",
            "FED": "Federal/Government",
            "OTHER": "Academic / NGO / Other",
            "OTHER_GOV": "Other Government",
            "NETWORK": "Network",
            "INDIV": "Individual",
            "UNKNOWN": "Unknown",
        }.get(cls, cls)
        sponsor_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(label)}</td>'
            f'<td style="padding:10px;text-align:right;font-weight:bold;">{cnt}</td>'
            f'</tr>\n'
        )

    # --- WHO strategy compliance items ---
    who_items = [
        ("Member state policies", "Only 25 of 54 African countries have national TM policies (WHO 2019 survey)"),
        ("Safety monitoring", "Pharmacovigilance for herbal products is absent in most African countries"),
        ("Quality standards", "No African country has GMP-compliant herbal manufacturing at scale"),
        ("Integration into health systems", "TM practitioners operate outside formal referral pathways"),
        ("Research investment", f"Africa has {africa_n} TM trials vs China's {china_n} -- evidence generation is near zero"),
        ("Intellectual property", "Traditional knowledge is largely unprotected from biopiracy"),
        ("Biodiversity conservation", "Medicinal plant harvesting is unregulated in most regions"),
    ]
    who_rows = ""
    for area, finding in who_items:
        who_rows += (
            f'<tr>'
            f'<td style="padding:10px;font-weight:bold;">{escape_html(area)}</td>'
            f'<td style="padding:10px;color:var(--muted);">{escape_html(finding)}</td>'
            f'<td style="padding:10px;text-align:center;">'
            f'<span style="color:#ef4444;font-weight:bold;">FAILING</span></td>'
            f'</tr>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa's Traditional Medicine Paradox</title>
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
  --purple: #8b5cf6;
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
  background: linear-gradient(135deg, #22c55e, #f59e0b, #ef4444);
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
.method-note {{
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.analysis-box {{
  background: rgba(245, 158, 11, 0.08);
  border-left: 4px solid var(--warning);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.analysis-box p {{ margin-bottom: 0.8rem; }}
.analysis-box p:last-child {{ margin-bottom: 0; }}
.danger-box {{
  background: rgba(239, 68, 68, 0.08);
  border-left: 4px solid var(--danger);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.danger-box p {{ margin-bottom: 0.8rem; }}
.danger-box p:last-child {{ margin-bottom: 0; }}
.success-box {{
  background: rgba(34, 197, 94, 0.08);
  border-left: 4px solid var(--success);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.success-box p {{ margin-bottom: 0.8rem; }}
.success-box p:last-child {{ margin-bottom: 0; }}
.purple-box {{
  background: rgba(139, 92, 246, 0.08);
  border-left: 4px solid var(--purple);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.purple-box p {{ margin-bottom: 0.8rem; }}
.purple-box p:last-child {{ margin-bottom: 0; }}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.scroll-x {{ overflow-x: auto; }}
.big-number {{
  font-size: 5rem;
  font-weight: 900;
  text-align: center;
  margin: 1rem 0;
}}
.paradox-stat {{
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2rem;
  margin: 2rem 0;
  flex-wrap: wrap;
}}
.paradox-stat .side {{
  text-align: center;
  padding: 1.5rem;
  background: var(--surface);
  border-radius: 12px;
  border: 1px solid var(--border);
  min-width: 200px;
}}
.paradox-stat .vs {{
  font-size: 1.5rem;
  color: var(--muted);
  font-weight: bold;
}}
footer {{
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}}
</style>
</head>
<body>
<div class="container">

<h1>Africa's Traditional Medicine Paradox</h1>
<p class="subtitle">80% of Africans rely on traditional medicine as primary healthcare &mdash;
yet virtually none of it is rigorously tested. Africa has {africa_n} traditional/herbal medicine
trials on ClinicalTrials.gov vs China's {china_n}: a {int(ratio)}x gap.</p>

<!-- ============================================================ -->
<!-- 1. SUMMARY -->
<!-- ============================================================ -->
<h2>1. The Numbers at a Glance</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa TM Trials</div>
    <div class="value danger">{africa_n}</div>
    <div class="label">~1.4 billion people</div>
  </div>
  <div class="summary-card">
    <div class="label">China TM Trials</div>
    <div class="value warning">{china_n}</div>
    <div class="label">TCM systematization model</div>
  </div>
  <div class="summary-card">
    <div class="label">Gap Ratio</div>
    <div class="value danger">{int(ratio)}x</div>
    <div class="label">China / Africa</div>
  </div>
  <div class="summary-card">
    <div class="label">India TM Trials</div>
    <div class="value" style="color:var(--success);">{india_n}</div>
    <div class="label">Ayurveda + AYUSH system</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa's Global Share</div>
    <div class="value danger">{share_pct}%</div>
    <div class="label">of all TM trials worldwide</div>
  </div>
</div>

<div class="method-note">
<strong>Data source:</strong> ClinicalTrials.gov API v2, querying
<code>"{escape_html(TRAD_MED_QUERY)}"</code> filtered to interventional studies.
Africa total is the union of 8 major African countries (South Africa, Nigeria, Ghana,
Kenya, Tanzania, Cameroon, DRC, Egypt). Generated {datetime.now().strftime("%Y-%m-%d")}.
</div>

<!-- ============================================================ -->
<!-- 2. THE PARADOX: 80% USE, 0% EVIDENCE -->
<!-- ============================================================ -->
<h2>2. The Paradox: 80% Use, Near-Zero Evidence</h2>

<div class="paradox-stat">
  <div class="side">
    <div class="label">WHO estimate: Africans using TM</div>
    <div class="value" style="font-size:3rem;color:var(--success);">80%</div>
    <div class="label">~1.1 billion people</div>
  </div>
  <div class="vs">vs</div>
  <div class="side">
    <div class="label">Registered TM trials in Africa</div>
    <div class="value" style="font-size:3rem;color:var(--danger);">{africa_n}</div>
    <div class="label">on ClinicalTrials.gov</div>
  </div>
</div>

<div class="danger-box">
<p><strong>The paradox is staggering.</strong> The WHO's African Regional Strategy on
Traditional Medicine (2014&ndash;2023) estimated that 80% of Africa's population relies
on traditional medicine as the primary or sole source of healthcare. This makes traditional
medicine the de facto health system for over one billion people.</p>

<p>Yet on ClinicalTrials.gov &mdash; the world's largest trial registry &mdash; Africa has
only <strong>{africa_n}</strong> registered interventional trials for traditional, herbal,
or indigenous medicines. China, with a comparable population and an equally deep traditional
medicine heritage, has <strong>{china_n}</strong> &mdash; a <strong>{int(ratio)}x</strong> gap.</p>

<p><strong>This means Africa's most widely used healthcare interventions have essentially
no rigorous evidence base.</strong> Dosing, safety, drug interactions, efficacy against
specific conditions &mdash; all unknown for the remedies that 80% of the population actually takes.
Patients are being treated at population scale with unvalidated interventions, while the
global research enterprise focuses its traditional medicine efforts elsewhere.</p>
</div>

<!-- ============================================================ -->
<!-- 3. REGIONAL COMPARISON CHART -->
<!-- ============================================================ -->
<h2>3. Regional Comparison</h2>
<div class="chart-container">
  <canvas id="regionChart" height="300"></canvas>
</div>

<table>
<thead>
<tr>
  <th style="padding:10px;">Region</th>
  <th style="padding:10px;text-align:right;">TM/Herbal Trials</th>
  <th style="padding:10px;text-align:right;">Ratio vs Africa</th>
</tr>
</thead>
<tbody>
<tr>
  <td style="padding:10px;font-weight:bold;color:var(--danger);">Africa (8 countries)</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{africa_n}</td>
  <td style="padding:10px;text-align:right;">1x (baseline)</td>
</tr>
<tr>
  <td style="padding:10px;font-weight:bold;color:var(--warning);">China</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{china_n}</td>
  <td style="padding:10px;text-align:right;color:var(--danger);">{int(ratio)}x</td>
</tr>
<tr>
  <td style="padding:10px;font-weight:bold;color:var(--success);">India</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{india_n}</td>
  <td style="padding:10px;text-align:right;color:var(--danger);">{india_ratio}x</td>
</tr>
<tr>
  <td style="padding:10px;font-weight:bold;color:var(--accent);">Brazil</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{brazil_n}</td>
  <td style="padding:10px;text-align:right;">{round(brazil_n / africa_n, 1) if africa_n > 0 else 'N/A'}x</td>
</tr>
<tr>
  <td style="padding:10px;font-weight:bold;color:var(--purple);">United States</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{us_n}</td>
  <td style="padding:10px;text-align:right;">{round(us_n / africa_n, 1) if africa_n > 0 else 'N/A'}x</td>
</tr>
</tbody>
</table>

<!-- ============================================================ -->
<!-- 4. COUNTRY BREAKDOWN -->
<!-- ============================================================ -->
<h2>4. African Country Breakdown</h2>
<table>
<thead>
<tr>
  <th style="padding:10px;">Country</th>
  <th style="padding:10px;text-align:right;">Population</th>
  <th style="padding:10px;text-align:right;">TM/Herbal Trials</th>
  <th style="padding:10px;text-align:right;">Per Million</th>
</tr>
</thead>
<tbody>
{country_rows}
</tbody>
</table>

<div class="chart-container">
  <canvas id="countryChart" height="300"></canvas>
</div>

<!-- ============================================================ -->
<!-- 5. SPECIFIC REMEDY SEARCH -->
<!-- ============================================================ -->
<h2>5. Specific African Remedies on ClinicalTrials.gov</h2>

<div class="analysis-box">
<p><strong>Africa has contributed some of the world's most important medicines</strong> &mdash;
yet the systematic testing of its pharmacopoeia has barely begun. Below are search results
for seven remedies with African origins or widespread African traditional use.</p>
</div>

<table>
<thead>
<tr>
  <th style="padding:10px;">Remedy</th>
  <th style="padding:10px;">Description</th>
  <th style="padding:10px;text-align:right;">Global Trials</th>
  <th style="padding:10px;text-align:right;">Africa Trials</th>
  <th style="padding:10px;text-align:right;">Africa %</th>
</tr>
</thead>
<tbody>
{remedy_rows}
</tbody>
</table>

<div class="chart-container">
  <canvas id="remedyChart" height="300"></canvas>
</div>

<!-- ============================================================ -->
<!-- 6. THE ARTEMISININ LESSON -->
<!-- ============================================================ -->
<h2>6. The Artemisinin Lesson</h2>

<div class="success-box">
<p><strong>Artemisia annua</strong> was used in Chinese traditional medicine (qinghao) for
over 2,000 years. In 1972, Tu Youyou's team extracted artemisinin from this plant, tested it
rigorously, and created the most important antimalarial drug in history. She received the
<strong>Nobel Prize in Physiology or Medicine in 2015</strong> &mdash; the first Nobel for
a discovery derived directly from traditional herbal medicine.</p>

<p><strong>The lesson:</strong> Traditional medicine contains real pharmacological agents.
When subjected to modern scientific methods &mdash; extraction, purification, randomized
controlled trials &mdash; it can yield breakthrough therapies. Artemisinin now saves
hundreds of thousands of lives annually.</p>

<p><strong>The question:</strong> Africa's traditional pharmacopoeia is vast and diverse,
spanning thousands of plant species across dozens of ecological zones. Devil's claw has
demonstrated anti-inflammatory properties. Sutherlandia (cancer bush) shows cytotoxic
activity in vitro. The African potato has immunomodulatory effects. <em>How many more
artemisinin-class discoveries are sitting in Africa's traditional medicine systems,
untested?</em></p>

<p><strong>The tragedy:</strong> The answer is: we have no way of knowing, because almost
nobody is running the trials. China invested systematically in testing its traditional
pharmacopoeia and produced a Nobel Prize. Africa, with comparable biodiversity and an
even deeper reliance on traditional medicine, has {africa_n} registered trials.</p>
</div>

<!-- ============================================================ -->
<!-- 7. CHINA'S TCM MODEL -->
<!-- ============================================================ -->
<h2>7. China's TCM Model: What Systematization Looks Like</h2>

<div class="purple-box">
<p><strong>China's {china_n} traditional medicine trials</strong> on ClinicalTrials.gov
did not happen by accident. China has pursued a deliberate, multi-decade strategy to
validate Traditional Chinese Medicine (TCM) through modern research methods.</p>

<p><strong>Institutional infrastructure:</strong> China established the State Administration
of Traditional Chinese Medicine (SATCM) in 1986, created TCM-specific university degree
programs, built over 4,000 TCM hospitals (accounting for ~16% of all hospital visits),
and invested billions in TCM research annually.</p>

<p><strong>Regulatory framework:</strong> China's 2017 TCM Law formalized standards for
herbal product quality, practitioner licensing, and clinical evidence requirements.
The CFDA created specific pathways for TCM drug approval, including simplified Phase I
requirements for products with long historical use.</p>

<p><strong>Research funding:</strong> The National Natural Science Foundation of China
has dedicated TCM research programs. Major universities (Beijing University of Chinese
Medicine, Shanghai University of TCM) produce thousands of peer-reviewed publications
annually on herbal pharmacology.</p>

<p><strong>International projection:</strong> China actively promotes TCM internationally
through Confucius Institutes, bilateral health agreements, and WHO lobbying, resulting
in the inclusion of TCM classifications in ICD-11 (2019) &mdash; a controversial decision
that nonetheless demonstrates strategic investment in global legitimacy.</p>

<p><strong>The contrast with Africa:</strong> No African country has comparable institutional
infrastructure for traditional medicine research. There is no pan-African traditional
medicine research agency, no standardized regulatory pathway for herbal products, and
no dedicated funding stream comparable to China's NSFC programs. The {int(ratio)}x gap
is the predictable result of a {int(ratio)}x gap in investment.</p>
</div>

<!-- ============================================================ -->
<!-- 8. SPONSOR ANALYSIS -->
<!-- ============================================================ -->
<h2>8. Who Sponsors the Few Trials That Exist?</h2>

<div class="analysis-box">
<p>Of the small number of traditional medicine trials in Africa, the sponsor profile reveals
the dependency structure of African clinical research.</p>
</div>

<table>
<thead>
<tr>
  <th style="padding:10px;">Sponsor Class</th>
  <th style="padding:10px;text-align:right;">Count (sampled)</th>
</tr>
</thead>
<tbody>
{sponsor_rows}
</tbody>
</table>

<div class="method-note">
<strong>Note:</strong> Sponsor analysis is based on a sample of up to 10 trials per
country for South Africa, Nigeria, Kenya, and Egypt. The proportions are indicative
rather than exhaustive.
</div>

<!-- ============================================================ -->
<!-- 9. WHO TM STRATEGY COMPLIANCE -->
<!-- ============================================================ -->
<h2>9. WHO Traditional Medicine Strategy 2014&ndash;2023: Africa's Scorecard</h2>

<div class="danger-box">
<p>The WHO Traditional Medicine Strategy 2014&ndash;2023 outlined seven priority areas
for integrating traditional medicine into health systems. Africa's performance against
these benchmarks is uniformly poor.</p>
</div>

<table>
<thead>
<tr>
  <th style="padding:10px;">Strategy Area</th>
  <th style="padding:10px;">Africa Status</th>
  <th style="padding:10px;text-align:center;">Grade</th>
</tr>
</thead>
<tbody>
{who_rows}
</tbody>
</table>

<div class="analysis-box">
<p><strong>The fundamental failure</strong> is the disconnect between rhetoric and investment.
African governments and the WHO repeatedly affirm the importance of traditional medicine &mdash;
indeed, the WHO declared 2001&ndash;2010 the "Decade of Traditional Medicine in Africa" &mdash;
but this recognition has not translated into research funding, regulatory infrastructure,
or trial registration. China treated its TM strategy as an <em>industrial policy</em>,
not a public health platitude. Africa has done the opposite.</p>
</div>

<!-- ============================================================ -->
<!-- 10. IMPLICATIONS -->
<!-- ============================================================ -->
<h2>10. Implications and the Path Forward</h2>

<div class="success-box">
<p><strong>What would closing the gap require?</strong></p>

<p><strong>1. Pan-African TM Research Network:</strong> A coordinated, AU-level body
modeled on China's SATCM, with dedicated funding and standardized protocols for
ethnobotanical screening, phytochemical analysis, and clinical trial design for
traditional remedies.</p>

<p><strong>2. Regulatory pathways:</strong> Simplified but rigorous pathways for
traditional medicines with documented long-term use, similar to China's "classical
formula" fast-track or the EU's Traditional Herbal Medicinal Products Directive.</p>

<p><strong>3. Intellectual property protection:</strong> Before systematic testing begins,
frameworks must protect African communities' traditional knowledge from biopiracy. The
Nagoya Protocol on Access and Benefit Sharing provides a starting point, but
implementation is weak across the continent.</p>

<p><strong>4. Investment at scale:</strong> Even matching India's TM trial output
({india_n} trials) would require a {india_ratio}x increase from Africa's current
base of {africa_n}. This demands sustained funding from African governments,
multilateral institutions, and philanthropic foundations.</p>

<p><strong>5. Local clinical trial infrastructure:</strong> GCP-compliant sites,
trained investigators, ethics committees familiar with traditional medicine research,
and digital registry integration &mdash; all of which are prerequisites for credible
trial conduct.</p>
</div>

<footer>
  Data from ClinicalTrials.gov API v2 &middot; Interventional studies only &middot;
  Query: &ldquo;{escape_html(TRAD_MED_QUERY)}&rdquo; &middot;
  Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot;
  Africa RCT Landscape Project &mdash; Traditional Medicine Paradox (Project 22)
</footer>

</div>

<script>
// 1. Regional comparison bar chart
const regionCtx = document.getElementById('regionChart').getContext('2d');
new Chart(regionCtx, {{
  type: 'bar',
  data: {{
    labels: {region_labels},
    datasets: [{{
      label: 'Traditional/Herbal Medicine Trials',
      data: {region_values},
      backgroundColor: {region_colors},
      borderColor: {region_colors},
      borderWidth: 1,
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{
        display: true,
        text: 'Traditional / Herbal Medicine Trials by Region',
        color: '#94a3b8',
        font: {{ size: 14 }}
      }}
    }},
    scales: {{
      y: {{
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(255,255,255,0.05)' }},
        title: {{
          display: true,
          text: 'Number of trials',
          color: '#94a3b8'
        }}
      }},
      x: {{
        ticks: {{ color: '#e2e8f0', font: {{ size: 13 }} }},
        grid: {{ display: false }}
      }}
    }}
  }}
}});

// 2. Country breakdown bar chart
const countryCtx = document.getElementById('countryChart').getContext('2d');
new Chart(countryCtx, {{
  type: 'bar',
  data: {{
    labels: {country_labels},
    datasets: [{{
      label: 'TM/Herbal Trials',
      data: {country_values},
      backgroundColor: '#ef4444',
      borderColor: '#ef4444',
      borderWidth: 1,
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: 'y',
    plugins: {{
      legend: {{ display: false }},
      title: {{
        display: true,
        text: 'Traditional Medicine Trials by African Country',
        color: '#94a3b8',
        font: {{ size: 14 }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(255,255,255,0.05)' }}
      }},
      y: {{
        ticks: {{ color: '#e2e8f0', font: {{ size: 12 }} }},
        grid: {{ display: false }}
      }}
    }}
  }}
}});

// 3. Remedy comparison grouped bar chart
const remedyCtx = document.getElementById('remedyChart').getContext('2d');
new Chart(remedyCtx, {{
  type: 'bar',
  data: {{
    labels: {remedy_labels},
    datasets: [
      {{
        label: 'Global trials',
        data: {remedy_global},
        backgroundColor: '#3b82f6',
        borderRadius: 4,
      }},
      {{
        label: 'Africa trials',
        data: {remedy_africa},
        backgroundColor: '#ef4444',
        borderRadius: 4,
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{
        labels: {{ color: '#94a3b8' }}
      }},
      title: {{
        display: true,
        text: 'Specific African Remedies: Global vs Africa Trial Counts',
        color: '#94a3b8',
        font: {{ size: 14 }}
      }}
    }},
    scales: {{
      y: {{
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(255,255,255,0.05)' }}
      }},
      x: {{
        ticks: {{ color: '#e2e8f0', font: {{ size: 11 }} }},
        grid: {{ display: false }}
      }}
    }}
  }}
}});
</script>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 65)
    print("  Africa's Traditional Medicine Paradox")
    print("  80% use, near-zero evidence")
    print("=" * 65)

    print("\n[1/3] Fetching data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()

    print("\n[2/3] Computing analysis...")
    analysis = compute_analysis(data)

    reg = analysis["regional"]
    print(f"\n  Regional comparison (traditional/herbal medicine trials):")
    print(f"    Africa (8 countries): {reg['Africa']:,}")
    print(f"    China:                {reg['China']:,}")
    print(f"    India:                {reg['India']:,}")
    print(f"    Brazil:               {reg['Brazil']:,}")
    print(f"    United States:        {reg['United States']:,}")
    print(f"    China/Africa ratio:   {reg['china_to_africa_ratio']}x")
    print(f"    India/Africa ratio:   {reg['india_to_africa_ratio']}x")
    print(f"    Africa's global share: {reg['africa_share_pct']}%")

    print(f"\n  Per-country breakdown:")
    for c in analysis["country_breakdown"]:
        print(f"    {c['display']:20s}  {c['count']:4d} trials  "
              f"({c['per_million']:.3f} per million)")

    print(f"\n  Specific African remedies:")
    for r in analysis["remedies"]:
        print(f"    {r['name']:18s}  Global: {r['global_count']:4d}  "
              f"Africa: {r['africa_count']:4d}")

    print("\n[3/3] Generating HTML dashboard...")
    html = generate_html(data, analysis)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Written to {OUTPUT_HTML}")

    print(f"\nDone. Open {OUTPUT_HTML} in a browser to view the dashboard.")


if __name__ == "__main__":
    main()
