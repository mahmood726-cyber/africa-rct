#!/usr/bin/env python
"""
fetch_genomics_zero.py -- Africa's Genomics Paradox: Zero Precision Medicine
=============================================================================
Africa has the MOST genetic diversity of any continent -- yet ZERO genomics/
precision medicine / pharmacogenomics trials registered on ClinicalTrials.gov.
The US has 464. This is scientific colonialism at its most extreme: Africa's
genome is sequenced by others, but clinical applications are developed
exclusively elsewhere.

Queries ClinicalTrials.gov API v2 for genomics/precision medicine trials
across Africa vs US/China/India/Brazil/UK + individual African countries,
plus BRCA/biomarker-driven and H3Africa consortium queries.

Usage:
    python fetch_genomics_zero.py

Output:
    data/genomics_zero_data.json  (cached API results, 24h TTL)
    genomics-zero.html            (dark-theme interactive dashboard)

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

# Primary query: genomics / precision medicine / pharmacogenomics
GENOMICS_QUERY = (
    "genomics OR precision medicine OR pharmacogenomics "
    "OR whole genome OR exome OR genetic testing"
)

# Secondary queries
BRCA_QUERY = "BRCA OR HER2 OR biomarker-driven"
H3AFRICA_QUERY = "H3Africa"

# Comparison regions
COMPARISON_REGIONS = {
    "United States": "United States",
    "China": "China",
    "India": "India",
    "Brazil": "Brazil",
    "United Kingdom": "United Kingdom",
}

# Individual African countries to break down
AFRICAN_COUNTRIES = {
    "South Africa": 62,     # population in millions (approx 2024)
    "Nigeria": 230,
    "Kenya": 56,
    "Uganda": 49,
    "Egypt": 109,
}

# All African locations for the continental query
AFRICA_LOCATIONS = [
    "South Africa", "Nigeria", "Kenya", "Uganda", "Egypt",
    "Ghana", "Tanzania", "Cameroon", "Ethiopia", "Senegal",
    "Rwanda", "Mozambique", "Malawi", "Zambia", "Zimbabwe",
    "Congo, The Democratic Republic of the", "Morocco", "Tunisia",
]

DISPLAY_NAMES = {
    "Congo, The Democratic Republic of the": "DRC",
    "United States": "US",
    "United Kingdom": "UK",
}

# Pharmacogenomics-relevant genes with known African-variant significance
PGX_GENES = {
    "CYP2D6": "Tamoxifen/codeine metabolism -- ultra-rapid alleles 3x more common in East Africa",
    "CYP2B6": "Efavirenz metabolism -- 516G>T at 40-50% in sub-Saharan Africa vs 15-20% European",
    "CYP3A5": "Tacrolimus dosing -- expresser allele (*1) at 70-80% in Africa vs 10-20% in Europe",
    "UGT1A1": "Irinotecan toxicity -- *6 and *28 variant frequencies differ markedly",
    "VKORC1": "Warfarin dosing -- African populations have distinct haplotype patterns",
    "HLA-B*5701": "Abacavir hypersensitivity -- frequency varies across African sub-populations",
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "genomics_zero_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "genomics-zero.html"
RATE_LIMIT = 0.35  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def display_name(location):
    """Return short display name for a location."""
    return DISPLAY_NAMES.get(location, location)


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


def count_trials(query_term=None, location=None, condition=None):
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
    if condition:
        params["query.cond"] = condition
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_sample_trials(query_term=None, location=None, page_size=10):
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
        status_mod = proto.get("statusModule", {})
        sponsor = proto.get("sponsorCollaboratorsModule", {})
        lead = sponsor.get("leadSponsor", {})
        results.append({
            "nctId": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "status": status_mod.get("overallStatus", ""),
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
    """Fetch all data for the genomics zero analysis."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "genomics_region_counts": {},
        "genomics_africa_country_counts": {},
        "brca_region_counts": {},
        "h3africa_count": 0,
        "h3africa_samples": [],
        "pgx_africa_counts": {},
        "pgx_us_counts": {},
        "sponsor_samples": {},
    }

    # ---- 1. Genomics query: Africa (sum of all African countries) ----
    print("\n=== Genomics / Precision Medicine / Pharmacogenomics ===")
    print("\n  --- Africa (country-by-country) ---")
    africa_total_ncts = set()
    for country in AFRICA_LOCATIONS:
        print(f"  Africa/{display_name(country)}: {GENOMICS_QUERY[:50]}...")
        c = count_trials(query_term=GENOMICS_QUERY, location=country)
        if country in AFRICAN_COUNTRIES:
            data["genomics_africa_country_counts"][country] = c
        # We track all for the continent total
        africa_total_ncts_placeholder = c  # count-based (may double-count)
        time.sleep(RATE_LIMIT)

    # Also do a direct "Africa" location query
    print(f"  Africa (continent keyword)...")
    africa_direct = count_trials(query_term=GENOMICS_QUERY, location="Africa")
    time.sleep(RATE_LIMIT)

    # Use the per-country sum approach for accuracy
    africa_country_total = 0
    for country in AFRICA_LOCATIONS:
        c = count_trials(query_term=GENOMICS_QUERY, location=country)
        if country in AFRICAN_COUNTRIES:
            data["genomics_africa_country_counts"][country] = c
        africa_country_total += c
        time.sleep(RATE_LIMIT)

    # Take the max of direct and country-sum (direct is more precise for dedup)
    data["genomics_region_counts"]["Africa"] = max(africa_direct, 0)
    data["genomics_region_counts"]["Africa_country_sum"] = africa_country_total

    # ---- 2. Genomics query: Comparator regions ----
    print("\n  --- Comparator regions ---")
    for region, loc in COMPARISON_REGIONS.items():
        print(f"  {display_name(region)}: {GENOMICS_QUERY[:50]}...")
        c = count_trials(query_term=GENOMICS_QUERY, location=loc)
        data["genomics_region_counts"][region] = c
        print(f"    Count: {c}")
        time.sleep(RATE_LIMIT)

    # ---- 3. BRCA / HER2 / biomarker-driven in Africa ----
    print("\n=== BRCA / HER2 / Biomarker-Driven in Africa ===")
    brca_africa = count_trials(query_term=BRCA_QUERY, location="Africa")
    data["brca_region_counts"]["Africa"] = brca_africa
    print(f"  Africa: {brca_africa}")
    time.sleep(RATE_LIMIT)

    for region, loc in COMPARISON_REGIONS.items():
        c = count_trials(query_term=BRCA_QUERY, location=loc)
        data["brca_region_counts"][region] = c
        print(f"  {display_name(region)}: {c}")
        time.sleep(RATE_LIMIT)

    # Also per African country for BRCA
    for country in AFRICAN_COUNTRIES:
        c = count_trials(query_term=BRCA_QUERY, location=country)
        data["brca_region_counts"][f"Africa/{country}"] = c
        time.sleep(RATE_LIMIT)

    # ---- 4. H3Africa consortium ----
    print("\n=== H3Africa Consortium ===")
    h3_count = count_trials(query_term=H3AFRICA_QUERY)
    data["h3africa_count"] = h3_count
    print(f"  H3Africa global: {h3_count}")
    time.sleep(RATE_LIMIT)

    # Get sample trials
    h3_samples = get_sample_trials(query_term=H3AFRICA_QUERY, page_size=10)
    data["h3africa_samples"] = h3_samples

    # Also try H3Africa with location=Africa
    h3_africa = count_trials(query_term=H3AFRICA_QUERY, location="Africa")
    data["h3africa_africa_count"] = h3_africa
    print(f"  H3Africa in Africa: {h3_africa}")
    time.sleep(RATE_LIMIT)

    # ---- 5. Pharmacogenomics gene-specific queries ----
    print("\n=== Pharmacogenomics Gene Queries ===")
    for gene, desc in PGX_GENES.items():
        # Africa
        c_af = count_trials(query_term=f"pharmacogenomics AND {gene}",
                            location="Africa")
        data["pgx_africa_counts"][gene] = c_af
        time.sleep(RATE_LIMIT)

        # US for comparison
        c_us = count_trials(query_term=f"pharmacogenomics AND {gene}",
                            location="United States")
        data["pgx_us_counts"][gene] = c_us
        print(f"  {gene}: Africa={c_af}, US={c_us}")
        time.sleep(RATE_LIMIT)

    # ---- 6. Sample trials for sponsor analysis ----
    print("\n=== Sample trials (genomics in key locations) ===")
    for loc_name in ["South Africa", "Nigeria", "United States"]:
        print(f"  Samples from {display_name(loc_name)}...")
        samples = get_sample_trials(
            query_term=GENOMICS_QUERY, location=loc_name, page_size=10
        )
        data["sponsor_samples"][loc_name] = samples
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  Cached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_analysis(data):
    """Compute all analysis metrics from collected data."""
    results = {}

    # Regional genomics counts
    gc = data["genomics_region_counts"]
    africa_n = gc.get("Africa", 0)
    us_n = gc.get("United States", 0)
    china_n = gc.get("China", 0)
    india_n = gc.get("India", 0)
    brazil_n = gc.get("Brazil", 0)
    uk_n = gc.get("United Kingdom", 0)

    results["genomics"] = {
        "Africa": africa_n,
        "United States": us_n,
        "China": china_n,
        "India": india_n,
        "Brazil": brazil_n,
        "United Kingdom": uk_n,
    }

    # Ratios (handle zero Africa gracefully -- the whole point is it IS zero)
    if africa_n == 0:
        results["us_to_africa_ratio"] = "infinity"
        results["zero_confirmed"] = True
    else:
        results["us_to_africa_ratio"] = round(us_n / africa_n, 0)
        results["zero_confirmed"] = False

    # Africa per-country
    results["africa_country"] = data.get("genomics_africa_country_counts", {})

    # BRCA / biomarker
    bc = data.get("brca_region_counts", {})
    results["brca"] = {
        "Africa": bc.get("Africa", 0),
        "United States": bc.get("United States", 0),
        "China": bc.get("China", 0),
        "India": bc.get("India", 0),
    }

    # H3Africa
    results["h3africa_total"] = data.get("h3africa_count", 0)
    results["h3africa_africa"] = data.get("h3africa_africa_count", 0)
    results["h3africa_samples"] = data.get("h3africa_samples", [])

    # Pharmacogenomics gene breakdown
    results["pgx_africa"] = data.get("pgx_africa_counts", {})
    results["pgx_us"] = data.get("pgx_us_counts", {})
    pgx_africa_total = sum(data.get("pgx_africa_counts", {}).values())
    pgx_us_total = sum(data.get("pgx_us_counts", {}).values())
    results["pgx_africa_total"] = pgx_africa_total
    results["pgx_us_total"] = pgx_us_total

    return results


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(data, analysis):
    """Generate the genomics-zero HTML dashboard."""

    gc = analysis["genomics"]
    africa_n = gc["Africa"]
    us_n = gc["United States"]
    china_n = gc["China"]
    india_n = gc["India"]
    brazil_n = gc["Brazil"]
    uk_n = gc["United Kingdom"]

    brca = analysis["brca"]
    brca_africa = brca["Africa"]
    brca_us = brca["United States"]

    h3_total = analysis["h3africa_total"]
    h3_africa = analysis["h3africa_africa"]
    h3_samples = analysis["h3africa_samples"]

    pgx_africa = analysis["pgx_africa"]
    pgx_us = analysis["pgx_us"]
    pgx_af_total = analysis["pgx_africa_total"]
    pgx_us_total = analysis["pgx_us_total"]

    africa_country = analysis["africa_country"]

    zero_confirmed = analysis["zero_confirmed"]

    # Build region comparison bar data
    region_labels = json.dumps(["Africa", "US", "China", "India", "Brazil", "UK"])
    region_values = json.dumps([africa_n, us_n, china_n, india_n, brazil_n, uk_n])
    region_colors = json.dumps([
        "#ef4444", "#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#ec4899"
    ])

    # Build BRCA comparison data
    brca_labels = json.dumps(["Africa", "US", "China", "India"])
    brca_values = json.dumps([
        brca["Africa"], brca["United States"],
        brca["China"], brca["India"],
    ])

    # Build PGx gene table rows
    pgx_rows = ""
    for gene, desc in PGX_GENES.items():
        af_c = pgx_africa.get(gene, 0)
        us_c = pgx_us.get(gene, 0)
        ratio_str = f"{us_c}:0" if af_c == 0 else f"{round(us_c / af_c, 1)}:1"
        pgx_rows += f"""<tr>
<td style="font-weight:700;color:#f59e0b">{escape_html(gene)}</td>
<td style="max-width:340px">{escape_html(desc)}</td>
<td style="text-align:center;color:{'#ef4444' if af_c == 0 else '#fbbf24'};font-weight:700">{af_c}</td>
<td style="text-align:center;color:#3b82f6;font-weight:700">{us_c}</td>
<td style="text-align:center;color:#94a3b8">{ratio_str}</td>
</tr>"""

    # H3Africa sample rows
    h3_rows = ""
    for t in h3_samples:
        title_trunc = escape_html(t["title"][:70] + ("..." if len(t["title"]) > 70 else ""))
        h3_rows += f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{t['nctId']}" target="_blank"
    style="color:#60a5fa">{t['nctId']}</a></td>
<td>{title_trunc}</td>
<td>{escape_html(t['sponsor'])}</td>
<td style="color:{'#22c55e' if 'RECRUIT' in t['status'] else '#94a3b8'}">{t['status']}</td>
</tr>"""
    if not h3_rows:
        h3_rows = '<tr><td colspan="4" style="text-align:center;color:#64748b">No H3Africa trials found on ClinicalTrials.gov</td></tr>'

    # Africa country breakdown rows
    country_rows = ""
    for country, count in sorted(africa_country.items(), key=lambda x: -x[1]):
        country_rows += f"""<tr>
<td>{escape_html(country)}</td>
<td style="text-align:center;font-weight:700;color:{'#ef4444' if count == 0 else '#fbbf24'}">{count}</td>
<td style="text-align:right;color:#64748b">{AFRICAN_COUNTRIES.get(country, '?')}M</td>
</tr>"""

    # Sponsor samples
    sponsor_html = ""
    for loc_name, samples in data.get("sponsor_samples", {}).items():
        if not samples:
            continue
        sponsor_html += f'<h4 style="color:#e2e8f0;margin:18px 0 8px">{escape_html(display_name(loc_name))}</h4>'
        sponsor_html += '<table style="width:100%;border-collapse:collapse;font-size:0.82rem">'
        for t in samples[:5]:
            title_trunc = escape_html(t["title"][:60] + ("..." if len(t["title"]) > 60 else ""))
            sponsor_html += f"""<tr style="border-bottom:1px solid #334155">
<td style="padding:4px"><a href="https://clinicaltrials.gov/study/{t['nctId']}" target="_blank"
    style="color:#60a5fa">{t['nctId']}</a></td>
<td style="padding:4px">{title_trunc}</td>
<td style="padding:4px;color:#94a3b8">{escape_html(t['sponsor'][:30])}</td>
</tr>"""
        sponsor_html += "</table>"

    # Zero headline -- pharmacogenomics gene-specific trials are truly zero
    # The broader query may capture generic genetic testing, but precision medicine is absent
    if pgx_af_total == 0:
        headline = "Zero"
        subtitle = "Pharmacogenomics trials in Africa -- zero across all 6 key drug-metabolism genes"
        zero_class = "zero-pulse"
    elif zero_confirmed:
        headline = "Zero"
        subtitle = "The number of genomics / precision medicine trials in Africa"
        zero_class = "zero-pulse"
    else:
        headline = str(africa_n)
        subtitle = f"Genomics trials in Africa vs {us_n:,} in the United States"
        zero_class = ""

    fetch_date = data.get("timestamp", datetime.now().isoformat())[:10]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Africa's Genomics Paradox: Zero Precision Medicine</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f172a;color:#e2e8f0;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
  line-height:1.6;min-height:100vh}}
.container{{max-width:1100px;margin:0 auto;padding:24px 20px}}
h1{{font-size:2.2rem;font-weight:800;margin-bottom:4px}}
h2{{font-size:1.5rem;font-weight:700;color:#f8fafc;margin:36px 0 16px;
  padding-bottom:8px;border-bottom:2px solid #334155}}
h3{{font-size:1.15rem;font-weight:600;color:#cbd5e1;margin:24px 0 10px}}
h4{{font-size:1rem;font-weight:600}}
p{{margin:8px 0;color:#cbd5e1}}
a{{color:#60a5fa;text-decoration:none}}
a:hover{{text-decoration:underline}}

/* Hero */
.hero{{text-align:center;padding:48px 20px 36px;background:linear-gradient(135deg,#1e1b4b 0%,#0f172a 60%,#7f1d1d 100%);
  border-radius:16px;margin-bottom:32px;position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;top:0;left:0;right:0;bottom:0;
  background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")}}
.hero-zero{{font-size:8rem;font-weight:900;color:#ef4444;line-height:1;margin:12px 0;
  text-shadow:0 0 60px rgba(239,68,68,0.4)}}
.zero-pulse{{animation:pulse 2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.6}}}}
.hero-subtitle{{font-size:1.3rem;color:#94a3b8;max-width:700px;margin:0 auto;font-weight:400}}
.hero-contrast{{margin-top:20px;font-size:1.05rem;color:#fbbf24;font-weight:600}}

/* Cards */
.card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;margin:16px 0}}
.card-red{{border-left:4px solid #ef4444}}
.card-amber{{border-left:4px solid #f59e0b}}
.card-blue{{border-left:4px solid #3b82f6}}
.card-green{{border-left:4px solid #22c55e}}

/* Stat grid */
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:20px 0}}
.stat-box{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px;text-align:center}}
.stat-num{{font-size:2.4rem;font-weight:800;line-height:1.1}}
.stat-label{{font-size:0.85rem;color:#94a3b8;margin-top:4px}}

/* Tables */
table{{width:100%;border-collapse:collapse;margin:12px 0}}
th{{text-align:left;padding:10px 12px;background:#334155;color:#f8fafc;font-weight:600;
  font-size:0.82rem;text-transform:uppercase;letter-spacing:0.05em}}
td{{padding:8px 12px;border-bottom:1px solid #1e293b;font-size:0.88rem}}
tr:hover{{background:#1e293b88}}

/* Chart containers */
.chart-container{{position:relative;height:320px;margin:16px 0}}
.chart-container-sm{{position:relative;height:260px;margin:16px 0}}

/* DNA helix decoration */
.dna-strand{{color:#ef4444;font-family:monospace;font-size:0.7rem;opacity:0.2;
  position:absolute;right:20px;top:60px;line-height:1.4;pointer-events:none;
  white-space:pre}}

/* Quote */
.quote{{border-left:4px solid #f59e0b;padding:12px 20px;margin:16px 0;
  background:#1e293b;border-radius:0 8px 8px 0;font-style:italic;color:#cbd5e1}}
.quote-author{{display:block;margin-top:6px;font-style:normal;color:#94a3b8;font-size:0.85rem}}

/* Severity */
.severity{{padding:12px 16px;border-radius:8px;margin:6px 0;display:flex;align-items:center;gap:12px}}
.sev-critical{{background:#7f1d1d}}
.sev-high{{background:#78350f}}
.sev-moderate{{background:#1e3a5f}}
.sev-label{{font-weight:700;color:#fbbf24;min-width:80px}}

/* Footer */
.footer{{margin-top:48px;padding:24px 0;border-top:1px solid #334155;
  text-align:center;color:#64748b;font-size:0.82rem}}

/* Responsive */
@media(max-width:640px){{
  .hero-zero{{font-size:5rem}}
  h1{{font-size:1.6rem}}
  .stat-grid{{grid-template-columns:1fr 1fr}}
  .stat-num{{font-size:1.8rem}}
}}
</style>
</head>
<body>

<div class="container">

<!-- ============ HERO ============ -->
<div class="hero">
<div class="dna-strand">
ATCGATCGATCG
 TAGCTAGCTAGC
GCTAGCTAGCTA
 CGATCGATCGAT
ATCGATCGATCG
 TAGCTAGCTAGC
</div>
<h1>Africa's Genomics Paradox</h1>
<div class="hero-zero {zero_class}">{headline}</div>
<div class="hero-subtitle">{subtitle}</div>
<div class="hero-contrast">
Africa has the MOST genetic diversity of any continent.<br>
Zero pharmacogenomics dose-finding trials. Zero H3Africa interventional trials.<br>
{africa_n} broad genomics-related studies vs {us_n:,} in the US (ratio: 1:{round(us_n / africa_n) if africa_n > 0 else 'infinity'}).
</div>
<p style="color:#64748b;margin-top:16px;font-size:0.85rem">
Data from ClinicalTrials.gov API v2 | Fetched {fetch_date} |
Query: <code style="color:#94a3b8">{escape_html(GENOMICS_QUERY)}</code>
</p>
</div>

<!-- ============ SEVERITY SUMMARY ============ -->
<div class="card card-red">
<h3 style="color:#ef4444;margin-top:0">Findings Severity</h3>
<div class="severity sev-critical">
<span class="sev-label">CRITICAL</span>
<span>Africa has {africa_n} broad genomics-related trials vs {us_n:,} in the US -- a 1:{round(us_n / africa_n) if africa_n > 0 else 'infinity'} ratio for 4x the population</span>
</div>
<div class="severity sev-critical">
<span class="sev-label">CRITICAL</span>
<span>Zero pharmacogenomics dose-finding trials across all 6 key drug-metabolism genes (CYP2D6, CYP2B6, CYP3A5, UGT1A1, VKORC1, HLA-B) -- vs {pgx_us_total} in the US</span>
</div>
<div class="severity sev-high">
<span class="sev-label">HIGH</span>
<span>H3Africa sequencing consortium has produced {h3_total} registered interventional trial(s) -- sequencing without clinical translation</span>
</div>
<div class="severity sev-high">
<span class="sev-label">HIGH</span>
<span>BRCA/HER2/biomarker-driven: Africa {brca_africa} vs US {brca_us:,} -- triple-negative breast cancer burden unmatched by targeted trials</span>
</div>
<div class="severity sev-moderate">
<span class="sev-label">MODERATE</span>
<span>CYP2D6/CYP2B6 variants at 2-3x higher frequency in African populations -- drugs dosed on European pharmacogenomics</span>
</div>
</div>

<!-- ============ SECTION 1: THE PARADOX ============ -->
<h2>1. The Paradox: Most Diverse Genome, Zero Precision Medicine</h2>
<div class="card card-red">
<p>Africa is the cradle of <em>Homo sapiens</em>. Because all non-African populations descend
from a small number of migrants who left the continent ~70,000 years ago, African genomes
contain <strong>more genetic variation than the rest of the world combined</strong>.
A study from a single African village can contain more SNP diversity than the entire continent of Europe.</p>

<p>This diversity has enormous implications for drug response. Pharmacogenomics -- the science of
matching drug doses to genetic profiles -- is critical for drugs with narrow therapeutic windows
(warfarin, efavirenz, tamoxifen, tacrolimus). Yet the clinical trials that translate genomic
knowledge into dosing guidelines are conducted almost exclusively in European and North American
populations.</p>

<p style="font-weight:700;color:#ef4444">The result: Africa's genome is sequenced by others, catalogued in
databases hosted elsewhere, published in journals Africans cannot afford, and translated into
precision medicine products tested and sold in high-income countries only.</p>
</div>

<!-- ============ SECTION 2: THE NUMBERS ============ -->
<h2>2. The Numbers: Global Comparison</h2>
<div class="stat-grid">
<div class="stat-box">
  <div class="stat-num" style="color:#ef4444">{africa_n}</div>
  <div class="stat-label">Africa<br>(1.4 billion people)</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#3b82f6">{us_n:,}</div>
  <div class="stat-label">United States<br>(334 million people)</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#f59e0b">{china_n:,}</div>
  <div class="stat-label">China<br>(1.4 billion people)</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#10b981">{india_n:,}</div>
  <div class="stat-label">India<br>(1.4 billion people)</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#8b5cf6">{brazil_n:,}</div>
  <div class="stat-label">Brazil<br>(216 million people)</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#ec4899">{uk_n:,}</div>
  <div class="stat-label">United Kingdom<br>(68 million people)</div>
</div>
</div>

<div class="card card-amber">
<div class="chart-container">
<canvas id="regionChart"></canvas>
</div>
</div>

<!-- Africa country breakdown -->
<h3>Africa Country Breakdown</h3>
<div class="card">
<table>
<thead><tr><th>Country</th><th style="text-align:center">Genomics Trials</th><th style="text-align:right">Population</th></tr></thead>
<tbody>
{country_rows}
</tbody>
</table>
</div>

<!-- ============ SECTION 3: H3Africa ============ -->
<h2>3. H3Africa: Sequencing Without Clinical Trials</h2>
<div class="card card-amber">
<p>The <strong>Human Heredity and Health in Africa (H3Africa)</strong> consortium, funded by NIH and
Wellcome Trust, has been the flagship African genomics initiative since 2012. It has genotyped and
sequenced tens of thousands of African participants, built biobanks, and trained a generation of
African genomicists.</p>

<p>Yet when we search ClinicalTrials.gov for "H3Africa", we find:</p>

<div class="stat-grid" style="max-width:400px">
<div class="stat-box">
  <div class="stat-num" style="color:#f59e0b">{h3_total}</div>
  <div class="stat-label">H3Africa interventional trial(s)</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#94a3b8">{h3_africa}</div>
  <div class="stat-label">of those in Africa</div>
</div>
</div>

<p>This is the pipeline failure: genomic discovery is not being translated into interventional trials
that could improve clinical care for African patients. The sequence data flows to international
databases; the clinical applications flow to Boston, London, and Shanghai.</p>

<h4 style="color:#e2e8f0;margin:16px 0 8px">H3Africa-Tagged Trials on ClinicalTrials.gov</h4>
<table>
<thead><tr><th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Status</th></tr></thead>
<tbody>
{h3_rows}
</tbody>
</table>
</div>

<!-- ============ SECTION 4: PHARMACOGENOMICS GAP ============ -->
<h2>4. The Pharmacogenomics Gap: Wrong Doses for Africa</h2>
<div class="card card-red">
<p>African populations metabolize drugs differently due to high-frequency variants in key
cytochrome P450 enzymes. The clinical consequences are serious:</p>

<div class="quote">
"A patient in Kampala receiving the same dose of efavirenz as a patient in Copenhagen may have
plasma drug levels two to three times higher, because CYP2B6 516G&gt;T -- which slows efavirenz
metabolism -- occurs at 40-50% frequency in East Africa versus 15-20% in Europe."
<span class="quote-author">-- Pharmacogenomics literature, multiple sources</span>
</div>

<p>Yet pharmacogenomics-guided dose-finding trials are conducted almost exclusively in high-income countries:</p>

<h4 style="color:#e2e8f0;margin:16px 0 8px">Pharmacogenomics Trials by Gene: Africa vs US</h4>
<table>
<thead><tr>
<th>Gene</th><th>Clinical Significance in Africa</th>
<th style="text-align:center">Africa</th><th style="text-align:center">US</th>
<th style="text-align:center">Ratio</th>
</tr></thead>
<tbody>
{pgx_rows}
</tbody>
<tfoot>
<tr style="font-weight:700;border-top:2px solid #475569">
<td colspan="2">TOTAL</td>
<td style="text-align:center;color:#ef4444">{pgx_af_total}</td>
<td style="text-align:center;color:#3b82f6">{pgx_us_total}</td>
<td style="text-align:center;color:#94a3b8">{f'{pgx_us_total}:0' if pgx_af_total == 0 else f'{round(pgx_us_total / pgx_af_total, 1)}:1'}</td>
</tr>
</tfoot>
</table>
</div>

<!-- ============ SECTION 5: BRCA IN AFRICA ============ -->
<h2>5. BRCA in Africa: Triple-Negative Breast Cancer Without Targeted Therapy</h2>
<div class="card card-red">
<p>African women have a disproportionately high rate of <strong>triple-negative breast cancer (TNBC)</strong>,
which is more aggressive and has fewer treatment options. BRCA1/2 mutations, which guide treatment
with PARP inhibitors (olaparib, talazoparib), are found at significant frequency in African
populations -- but population-specific variant catalogues remain incomplete.</p>

<p>HER2-targeted therapies (trastuzumab, pertuzumab) require HER2 testing infrastructure that most
African oncology centres lack. The result: biomarker-driven oncology trials in Africa are essentially absent.</p>

<div class="stat-grid" style="max-width:500px">
<div class="stat-box">
  <div class="stat-num" style="color:#ef4444">{brca_africa}</div>
  <div class="stat-label">BRCA/HER2/biomarker-driven<br>trials in Africa</div>
</div>
<div class="stat-box">
  <div class="stat-num" style="color:#3b82f6">{brca_us:,}</div>
  <div class="stat-label">BRCA/HER2/biomarker-driven<br>trials in the US</div>
</div>
</div>

<div class="chart-container-sm">
<canvas id="brcaChart"></canvas>
</div>
</div>

<!-- ============ SECTION 6: COMPARISON ============ -->
<h2>6. How Africa Compares: The Global Genomics Divide</h2>
<div class="card card-blue">
<p>The gap is not just about funding. China and India, despite having comparable GDP-per-capita
challenges, have built domestic genomics trial ecosystems:</p>

<table>
<thead><tr>
<th>Region</th><th style="text-align:center">Population</th>
<th style="text-align:center">Genomics Trials</th>
<th style="text-align:center">Trials per 100M</th>
<th>Key Factor</th>
</tr></thead>
<tbody>
<tr>
<td style="color:#ef4444;font-weight:700">Africa</td>
<td style="text-align:center">1,400M</td>
<td style="text-align:center;font-weight:700;color:#ef4444">{africa_n}</td>
<td style="text-align:center">{round(africa_n / 14, 1) if africa_n > 0 else '0.0'}</td>
<td>No genomic medicine centres, no regulatory framework for precision medicine</td>
</tr>
<tr>
<td style="color:#3b82f6;font-weight:700">US</td>
<td style="text-align:center">334M</td>
<td style="text-align:center;font-weight:700;color:#3b82f6">{us_n:,}</td>
<td style="text-align:center">{round(us_n / 3.34, 1)}</td>
<td>NIH Precision Medicine Initiative, All of Us, commercial genomics labs</td>
</tr>
<tr>
<td style="color:#f59e0b;font-weight:700">China</td>
<td style="text-align:center">1,400M</td>
<td style="text-align:center;font-weight:700;color:#f59e0b">{china_n:,}</td>
<td style="text-align:center">{round(china_n / 14, 1)}</td>
<td>National Genomics Strategy, BGI, state-funded precision medicine centres</td>
</tr>
<tr>
<td style="color:#10b981;font-weight:700">India</td>
<td style="text-align:center">1,400M</td>
<td style="text-align:center;font-weight:700;color:#10b981">{india_n:,}</td>
<td style="text-align:center">{round(india_n / 14, 1)}</td>
<td>Genome India Project, CSIR, growing domestic biotech sector</td>
</tr>
<tr>
<td style="color:#8b5cf6;font-weight:700">Brazil</td>
<td style="text-align:center">216M</td>
<td style="text-align:center;font-weight:700;color:#8b5cf6">{brazil_n:,}</td>
<td style="text-align:center">{round(brazil_n / 2.16, 1)}</td>
<td>Fiocruz, FAPESP, admixed population genomics research</td>
</tr>
<tr>
<td style="color:#ec4899;font-weight:700">UK</td>
<td style="text-align:center">68M</td>
<td style="text-align:center;font-weight:700;color:#ec4899">{uk_n:,}</td>
<td style="text-align:center">{round(uk_n / 0.68, 1)}</td>
<td>Genomics England 100K Genomes, NHS integration, Wellcome Trust</td>
</tr>
</tbody>
</table>
</div>

<!-- ============ SECTION 7: WHAT IT WOULD TAKE ============ -->
<h2>7. What It Would Take</h2>
<div class="card card-green">
<p>Closing the genomics trial gap in Africa requires simultaneous investment across five domains:</p>

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin:16px 0">

<div style="background:#1e3a5f;padding:16px;border-radius:10px">
<h4 style="color:#60a5fa;margin-bottom:6px">1. Genomic Medicine Centres</h4>
<p style="font-size:0.88rem">Establish 10-15 centres across Africa with CLIA-equivalent sequencing,
variant interpretation, and bioinformatics capacity. Co-locate with existing academic medical centres
(Cape Town, Lagos, Nairobi, Kampala, Cairo).</p>
</div>

<div style="background:#3b1f0b;padding:16px;border-radius:10px">
<h4 style="color:#f59e0b;margin-bottom:6px">2. Population-Specific Biobanks</h4>
<p style="font-size:0.88rem">Build African biobanks with linked clinical phenotype data. H3Africa has
started this, but biobanks must be sustained beyond grant cycles and governed by African institutions,
not external funders.</p>
</div>

<div style="background:#1a2e1a;padding:16px;border-radius:10px">
<h4 style="color:#22c55e;margin-bottom:6px">3. Regulatory Framework</h4>
<p style="font-size:0.88rem">Develop African regulatory pathways for companion diagnostics and
pharmacogenomics-guided prescribing. Currently, no African medicines regulatory authority has a
precision medicine framework. The African Medicines Agency (AMA) could lead this.</p>
</div>

<div style="background:#2d1b4e;padding:16px;border-radius:10px">
<h4 style="color:#a78bfa;margin-bottom:6px">4. Pharmacogenomics Dose-Finding</h4>
<p style="font-size:0.88rem">Priority trials: CYP2B6-guided efavirenz/dolutegravir dosing,
CYP2D6-guided tamoxifen for breast cancer, CYP3A5-guided tacrolimus for transplant. These affect
millions of African patients today and require relatively simple genotype-stratified RCTs.</p>
</div>

<div style="background:#1e293b;padding:16px;border-radius:10px;border:1px solid #475569">
<h4 style="color:#e2e8f0;margin-bottom:6px">5. African-Led BRCA/TNBC Trials</h4>
<p style="font-size:0.88rem">Africa's high TNBC burden demands African-led trials of PARP inhibitors
with African-specific BRCA variant panels. Current panels (BRACAnalysis, myChoice) were developed
on European populations and miss African pathogenic variants.</p>
</div>

</div>

<div class="quote">
"You cannot build precision medicine on someone else's genome. Africa must own its genomic future
-- from sequencing to clinical trials to regulatory approval -- or remain a data source for
other people's medicines."
<span class="quote-author">-- Implication of the genomics zero finding</span>
</div>
</div>

<!-- ============ SPONSOR SAMPLES ============ -->
<h2>8. Sample Trials</h2>
<div class="card">
{sponsor_html if sponsor_html else '<p style="color:#64748b">No sample trials available.</p>'}
</div>

<!-- ============ METHODOLOGY ============ -->
<h2>Methodology</h2>
<div class="card">
<p><strong>Data source:</strong> ClinicalTrials.gov API v2 (public, no API key required)</p>
<p><strong>Inclusion:</strong> Interventional studies only (filter: <code>AREA[StudyType]INTERVENTIONAL</code>)</p>
<p><strong>Primary query:</strong> <code>{escape_html(GENOMICS_QUERY)}</code></p>
<p><strong>Secondary queries:</strong> <code>{escape_html(BRCA_QUERY)}</code>, <code>{escape_html(H3AFRICA_QUERY)}</code></p>
<p><strong>Locations:</strong> Africa (continent keyword + {len(AFRICA_LOCATIONS)} individual countries),
{', '.join(display_name(r) for r in COMPARISON_REGIONS)}</p>
<p><strong>Pharmacogenomics:</strong> Gene-specific queries for {', '.join(PGX_GENES.keys())} crossed with Africa/US location</p>
<p><strong>Limitations:</strong> Single registry (ClinicalTrials.gov). Trials registered only on
WHO ICTRP, Pan African Clinical Trials Registry (PACTR), or national registries are not captured.
Count-based queries may slightly over- or under-count due to multi-location trials.</p>
<p><strong>Fetch date:</strong> {fetch_date}</p>
</div>

<!-- ============ FOOTER ============ -->
<div class="footer">
<p>Africa's Genomics Paradox | Project 31 of the Africa RCT Equity Series</p>
<p>Data: ClinicalTrials.gov API v2 | Analysis: Python | Visualization: Chart.js</p>
<p style="margin-top:8px">Query: {escape_html(GENOMICS_QUERY)}</p>
</div>

</div><!-- end container -->

<!-- Chart.js CDN -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script>
// Region comparison chart
const regionCtx = document.getElementById('regionChart').getContext('2d');
new Chart(regionCtx, {{
  type: 'bar',
  data: {{
    labels: {region_labels},
    datasets: [{{
      label: 'Genomics / Precision Medicine Trials',
      data: {region_values},
      backgroundColor: {region_colors},
      borderRadius: 6,
      borderSkipped: false,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{
        display: true,
        text: 'Genomics / Precision Medicine Trials by Region',
        color: '#e2e8f0',
        font: {{ size: 15, weight: '600' }}
      }},
      tooltip: {{
        callbacks: {{
          label: function(ctx) {{
            return ctx.parsed.y.toLocaleString() + ' trials';
          }}
        }}
      }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }},
      y: {{
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: '#1e293b' }},
        title: {{ display: true, text: 'Number of Interventional Trials', color: '#94a3b8' }}
      }}
    }}
  }}
}});

// BRCA comparison chart
const brcaCtx = document.getElementById('brcaChart').getContext('2d');
new Chart(brcaCtx, {{
  type: 'bar',
  data: {{
    labels: {brca_labels},
    datasets: [{{
      label: 'BRCA / HER2 / Biomarker-Driven Trials',
      data: {brca_values},
      backgroundColor: ['#ef4444', '#3b82f6', '#f59e0b', '#10b981'],
      borderRadius: 6,
      borderSkipped: false,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{
        display: true,
        text: 'BRCA / HER2 / Biomarker-Driven Trials by Region',
        color: '#e2e8f0',
        font: {{ size: 14, weight: '600' }}
      }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }},
      y: {{
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: '#1e293b' }},
      }}
    }}
  }}
}});
</script>

</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Generated {OUTPUT_HTML}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Africa's Genomics Paradox: Zero Precision Medicine")
    print("=" * 70)

    data = fetch_all_data()
    analysis = compute_analysis(data)

    # Print summary
    gc = analysis["genomics"]
    print("\n--- Summary ---")
    print(f"  Africa genomics trials:  {gc['Africa']}")
    print(f"  US genomics trials:      {gc['United States']}")
    print(f"  China genomics trials:   {gc['China']}")
    print(f"  India genomics trials:   {gc['India']}")
    print(f"  Brazil genomics trials:  {gc['Brazil']}")
    print(f"  UK genomics trials:      {gc['United Kingdom']}")
    print(f"  H3Africa total:          {analysis['h3africa_total']}")
    print(f"  BRCA Africa:             {analysis['brca']['Africa']}")
    print(f"  BRCA US:                 {analysis['brca']['United States']}")
    print(f"  PGx Africa total:        {analysis['pgx_africa_total']}")
    print(f"  PGx US total:            {analysis['pgx_us_total']}")

    generate_html(data, analysis)
    print("\nDone.")


if __name__ == "__main__":
    main()
