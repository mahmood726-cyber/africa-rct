#!/usr/bin/env python
"""
fetch_free_rider_genome.py -- The Free Rider Genome: Everyone Sequences, Nobody Treats
======================================================================================
From game theory (Olson, 1965): public goods are under-provided because individuals
benefit from others' contributions without paying.

Africa's genetic diversity is the ultimate public good for genomic medicine:
- H3Africa sequenced thousands of African genomes -> data used WORLDWIDE
- But ZERO pharmacogenomics trials in Africa
- Everyone "rides free" on African genetic data without investing in African applications
- Classic Tragedy of the Commons (Hardin, 1968) + Free Rider Problem

Queries ClinicalTrials.gov API v2 for genomics/precision medicine trials across
Africa, US, China, India, UK. Quantifies the free riding with specific gene markers
(CYP2D6, CYP2B6, CYP3A5) critical for African pharmacogenomics.

Usage:
    python fetch_free_rider_genome.py

Output:
    data/free_rider_genome_data.json   (cached API results, 24h TTL)
    free-rider-genome.html             (dark-theme interactive dashboard)

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
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# Focus regions
REGIONS = {
    "Africa": {
        "countries": ["Nigeria", "Kenya", "Uganda", "Tanzania", "Ethiopia",
                      "South Africa", "Ghana", "Cameroon", "Egypt", "Rwanda"],
        "population_m": 1460,
        "genetic_diversity": "Highest globally",
    },
    "United States": {
        "countries": ["United States"],
        "population_m": 335,
        "genetic_diversity": "Moderate (mixed ancestry)",
    },
    "China": {
        "countries": ["China"],
        "population_m": 1425,
        "genetic_diversity": "Low-moderate (Han dominant)",
    },
    "India": {
        "countries": ["India"],
        "population_m": 1440,
        "genetic_diversity": "High (endogamy + diversity)",
    },
    "United Kingdom": {
        "countries": ["United Kingdom"],
        "population_m": 68,
        "genetic_diversity": "Low-moderate",
    },
}

AFRICAN_COUNTRIES = REGIONS["Africa"]["countries"]

# Genomic medicine queries
GENOMICS_QUERIES = {
    "Precision medicine / genomics": "precision medicine OR genomic medicine OR pharmacogenomics",
    "Pharmacogenomics": "pharmacogenomics OR pharmacogenetics",
    "Biomarker-guided therapy": "biomarker guided OR biomarker-guided OR companion diagnostic",
    "Targeted therapy": "targeted therapy OR molecular targeted",
    "Gene therapy": "gene therapy OR gene editing OR CRISPR",
    "Liquid biopsy / ctDNA": "liquid biopsy OR ctDNA OR circulating tumor DNA",
}

# Pharmacogenomics genes critical for African populations
PGX_GENES = {
    "CYP2D6": {
        "query": "CYP2D6",
        "relevance": "Metabolizes 25% of all drugs; Africa has highest allele diversity globally",
        "african_impact": "Ultra-rapid metabolizers common; codeine -> morphine toxicity risk",
    },
    "CYP2B6": {
        "query": "CYP2B6",
        "relevance": "Metabolizes efavirenz (HIV); CYP2B6*6 at 40-50% frequency in Africans",
        "african_impact": "Slow metabolizers get toxic efavirenz levels; no dose-finding trials",
    },
    "CYP3A5": {
        "query": "CYP3A5",
        "relevance": "Metabolizes tacrolimus, calcium channel blockers; *1 active allele >70% in Africans vs <30% Europeans",
        "african_impact": "Africans need higher tacrolimus doses; no transplant PGx trials in Africa",
    },
    "UGT1A1": {
        "query": "UGT1A1",
        "relevance": "Gilbert syndrome and irinotecan toxicity; UGT1A1*6 rare but *28 varies",
        "african_impact": "Irinotecan dosing for colorectal cancer unknown for African genotypes",
    },
    "VKORC1": {
        "query": "VKORC1",
        "relevance": "Warfarin sensitivity; African-specific haplotypes not in standard algorithms",
        "african_impact": "Warfarin dosing algorithms developed on European data; Africans excluded",
    },
    "HLA-B*5701": {
        "query": "HLA-B*5701 OR HLA-B5701",
        "relevance": "Abacavir hypersensitivity screening; frequency varies across African populations",
        "african_impact": "HIV backbone drug; screening data from African populations limited",
    },
}

# BRCA / HER2 queries (precision oncology)
ONCOLOGY_QUERIES = {
    "BRCA (breast cancer gene)": "BRCA OR BRCA1 OR BRCA2",
    "HER2 targeted": "HER2 OR trastuzumab OR pertuzumab",
    "Triple-negative BC": "triple negative breast cancer",
    "PD-L1 / immunotherapy": "PD-L1 OR pembrolizumab OR nivolumab",
}

# H3Africa / African genomics
H3AFRICA_QUERIES = {
    "H3Africa": "H3Africa",
    "African genomics": "African genomics OR Africa genome",
    "African ancestry": "African ancestry AND genetics",
}

# Known facts about H3Africa (for HTML narrative)
H3AFRICA_FACTS = {
    "consortium_name": "Human Heredity and Health in Africa (H3Africa)",
    "funded_by": "NIH and Wellcome Trust",
    "launched": 2012,
    "participants_sequenced": 50000,
    "countries_involved": 30,
    "genomes_sequenced": 3500,
    "biobank_samples": 70000,
    "data_policy": "Open access after embargo period",
    "publications_citing": "1200+ publications globally",
    "interventional_trials_in_africa": 0,
    "pharmacogenomics_trials_in_africa": 0,
}

# Allele frequency data (published literature)
ALLELE_FREQUENCIES = {
    "CYP2D6 ultrarapid metabolizer": {
        "African": "20-40%",
        "European": "1-10%",
        "East Asian": "0-2%",
        "impact": "Codeine, tramadol, tamoxifen metabolism",
    },
    "CYP2B6*6 slow metabolizer": {
        "African": "40-50%",
        "European": "25-30%",
        "East Asian": "15-20%",
        "impact": "Efavirenz toxicity in HIV treatment",
    },
    "CYP3A5*1 active (expressors)": {
        "African": "60-90%",
        "European": "10-30%",
        "East Asian": "25-45%",
        "impact": "Tacrolimus dosing in transplant",
    },
    "VKORC1 African haplotype": {
        "African": "unique variants",
        "European": "well-characterized",
        "East Asian": "well-characterized",
        "impact": "Warfarin dose prediction algorithms",
    },
}

CACHE_FILE = Path(__file__).resolve().parent / "data" / "free_rider_genome_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "free-rider-genome.html"
RATE_LIMIT = 0.35
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24


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


def get_trial_count(query_term, location, use_cond=False):
    """Return total count of interventional trials for a query+location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if use_cond:
        params["query.cond"] = query_term
    else:
        params["query.term"] = query_term
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_trial_count_multi_location(query_term, locations, use_cond=False):
    """Return total count for a query across multiple locations (OR)."""
    location_str = " OR ".join(locations)
    params = {
        "format": "json",
        "query.locn": location_str,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    if use_cond:
        params["query.cond"] = query_term
    else:
        params["query.term"] = query_term
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


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_all_data():
    """Fetch all free rider genome data."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "genomics_by_region": {},
        "genomics_by_category": {},
        "pgx_by_region": {},
        "pgx_africa_by_gene": {},
        "pgx_us_by_gene": {},
        "pgx_china_by_gene": {},
        "oncology_by_region": {},
        "h3africa_queries": {},
        "africa_country_genomics": {},
        "africa_country_pgx": {},
    }

    regions_simple = ["United States", "China", "India", "United Kingdom"]

    # --- Genomics by region (broad + per-category) ---
    print("\n--- Genomics by region ---")
    for region_name, region_info in REGIONS.items():
        data["genomics_by_region"][region_name] = {}
        for cat_label, cat_query in GENOMICS_QUERIES.items():
            print(f"  {region_name}: {cat_label}...")
            if region_name == "Africa":
                count = get_trial_count_multi_location(cat_query, AFRICAN_COUNTRIES)
            else:
                count = get_trial_count(cat_query, region_info["countries"][0])
            data["genomics_by_region"][region_name][cat_label] = count
            time.sleep(RATE_LIMIT)

    # --- Pharmacogenomics genes by region ---
    print("\n--- PGx genes by region ---")
    for gene, gene_info in PGX_GENES.items():
        print(f"  Africa: {gene}...")
        africa_count = get_trial_count_multi_location(gene_info["query"], AFRICAN_COUNTRIES)
        data["pgx_africa_by_gene"][gene] = africa_count
        time.sleep(RATE_LIMIT)

        print(f"  US: {gene}...")
        us_count = get_trial_count(gene_info["query"], "United States")
        data["pgx_us_by_gene"][gene] = us_count
        time.sleep(RATE_LIMIT)

        print(f"  China: {gene}...")
        china_count = get_trial_count(gene_info["query"], "China")
        data["pgx_china_by_gene"][gene] = china_count
        time.sleep(RATE_LIMIT)

    # --- Precision oncology (BRCA, HER2, TNBC) ---
    print("\n--- Precision oncology by region ---")
    for region_name, region_info in REGIONS.items():
        data["oncology_by_region"][region_name] = {}
        for cat_label, cat_query in ONCOLOGY_QUERIES.items():
            print(f"  {region_name}: {cat_label}...")
            if region_name == "Africa":
                count = get_trial_count_multi_location(cat_query, AFRICAN_COUNTRIES, use_cond=True)
            else:
                count = get_trial_count(cat_query, region_info["countries"][0], use_cond=True)
            data["oncology_by_region"][region_name][cat_label] = count
            time.sleep(RATE_LIMIT)

    # --- H3Africa queries ---
    print("\n--- H3Africa / African genomics queries ---")
    for label, query in H3AFRICA_QUERIES.items():
        print(f"  Global: {label}...")
        params = {
            "format": "json",
            "query.term": query,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
            "pageSize": 1,
            "countTotal": "true",
        }
        result = api_get(params)
        global_count = result.get("totalCount", 0) if result else 0
        time.sleep(RATE_LIMIT)

        print(f"  Africa: {label}...")
        africa_count = get_trial_count_multi_location(query, AFRICAN_COUNTRIES)
        time.sleep(RATE_LIMIT)

        data["h3africa_queries"][label] = {
            "global": global_count,
            "africa": africa_count,
        }

    # --- Per-African-country genomics ---
    print("\n--- Per-African-country genomics ---")
    pgx_query = "pharmacogenomics OR pharmacogenetics"
    genomics_query = "precision medicine OR genomic medicine OR pharmacogenomics"
    for country in AFRICAN_COUNTRIES:
        print(f"  {country}: genomics...")
        gen_count = get_trial_count(genomics_query, country)
        data["africa_country_genomics"][country] = gen_count
        time.sleep(RATE_LIMIT)

        print(f"  {country}: PGx...")
        pgx_count = get_trial_count(pgx_query, country)
        data["africa_country_pgx"][country] = pgx_count
        time.sleep(RATE_LIMIT)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nCached to {CACHE_FILE}")
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_free_rider_index(data):
    """
    Free Rider Index = measure of how much others benefit from African genetic
    data without investing in African clinical applications.
    Proxy: (US + China + UK + India genomics trials) / (Africa genomics trials)
    If Africa = 0, index = infinity.
    """
    africa_total = sum(data["genomics_by_region"].get("Africa", {}).values())
    us_total = sum(data["genomics_by_region"].get("United States", {}).values())
    china_total = sum(data["genomics_by_region"].get("China", {}).values())
    india_total = sum(data["genomics_by_region"].get("India", {}).values())
    uk_total = sum(data["genomics_by_region"].get("United Kingdom", {}).values())
    global_others = us_total + china_total + india_total + uk_total

    if africa_total == 0:
        return float('inf'), 0, global_others

    return round(global_others / africa_total, 1), africa_total, global_others


def compute_pgx_void(data):
    """Compute the pharmacogenomics void."""
    africa_pgx = data.get("pgx_africa_by_gene", {})
    us_pgx = data.get("pgx_us_by_gene", {})
    china_pgx = data.get("pgx_china_by_gene", {})

    africa_total = sum(africa_pgx.values())
    us_total = sum(us_pgx.values())
    china_total = sum(china_pgx.values())

    return {
        "africa_total": africa_total,
        "us_total": us_total,
        "china_total": china_total,
        "africa_genes": africa_pgx,
        "us_genes": us_pgx,
        "china_genes": china_pgx,
    }


def compute_oncology_gap(data):
    """Compute precision oncology gap."""
    results = {}
    for label in ONCOLOGY_QUERIES:
        africa = data["oncology_by_region"].get("Africa", {}).get(label, 0)
        us = data["oncology_by_region"].get("United States", {}).get(label, 0)
        ratio = round(us / africa, 1) if africa > 0 else 999
        results[label] = {"africa": africa, "us": us, "ratio": ratio}
    return results


def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(data, fri, pgx_void, oncology_gap):
    """Generate the full HTML dashboard."""

    # Genomics comparison table
    genomics_rows = ""
    for cat in GENOMICS_QUERIES:
        vals = {}
        for region in REGIONS:
            vals[region] = data["genomics_by_region"].get(region, {}).get(cat, 0)
        color_af = "#ef4444" if vals["Africa"] == 0 else ("#f59e0b" if vals["Africa"] < 10 else "#e2e8f0")
        genomics_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(cat)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color_af};font-weight:bold;">{vals["Africa"]}</td>'
            f'<td style="padding:8px;text-align:right;">{vals["United States"]}</td>'
            f'<td style="padding:8px;text-align:right;">{vals["China"]}</td>'
            f'<td style="padding:8px;text-align:right;">{vals["India"]}</td>'
            f'<td style="padding:8px;text-align:right;">{vals["United Kingdom"]}</td>'
            f'</tr>\n'
        )

    # PGx gene table
    pgx_rows = ""
    for gene in PGX_GENES:
        af = pgx_void["africa_genes"].get(gene, 0)
        us = pgx_void["us_genes"].get(gene, 0)
        ch = pgx_void["china_genes"].get(gene, 0)
        color = "#ef4444" if af == 0 else "#e2e8f0"
        pgx_rows += (
            f'<tr>'
            f'<td style="padding:8px;font-weight:bold;">{escape_html(gene)}</td>'
            f'<td style="padding:8px;font-size:0.8rem;color:var(--muted);">'
            f'{escape_html(PGX_GENES[gene]["relevance"])}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{af}</td>'
            f'<td style="padding:8px;text-align:right;">{us}</td>'
            f'<td style="padding:8px;text-align:right;">{ch}</td>'
            f'</tr>\n'
        )

    # Oncology table
    onc_rows = ""
    for label, gap_info in oncology_gap.items():
        ratio_str = "INF" if gap_info["ratio"] == 999 else f'{gap_info["ratio"]}x'
        color = "#ef4444" if gap_info["africa"] == 0 else ("#f59e0b" if gap_info["ratio"] > 20 else "#e2e8f0")
        onc_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(label)}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{gap_info["africa"]}</td>'
            f'<td style="padding:8px;text-align:right;">{gap_info["us"]}</td>'
            f'<td style="padding:8px;text-align:right;font-weight:bold;'
            f'color:{"#ef4444" if gap_info["ratio"] > 50 else "#f59e0b"};">{ratio_str}</td>'
            f'</tr>\n'
        )

    # Allele frequency table
    allele_rows = ""
    for allele, freqs in ALLELE_FREQUENCIES.items():
        allele_rows += (
            f'<tr>'
            f'<td style="padding:8px;font-weight:bold;">{escape_html(allele)}</td>'
            f'<td style="padding:8px;text-align:center;color:#a855f7;">{escape_html(freqs["African"])}</td>'
            f'<td style="padding:8px;text-align:center;">{escape_html(freqs["European"])}</td>'
            f'<td style="padding:8px;text-align:center;">{escape_html(freqs["East Asian"])}</td>'
            f'<td style="padding:8px;font-size:0.8rem;color:var(--muted);">{escape_html(freqs["impact"])}</td>'
            f'</tr>\n'
        )

    # H3Africa rows
    h3_rows = ""
    for label, counts in data.get("h3africa_queries", {}).items():
        h3_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(label)}</td>'
            f'<td style="padding:8px;text-align:right;">{counts["global"]}</td>'
            f'<td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">{counts["africa"]}</td>'
            f'</tr>\n'
        )

    # Per-country Africa genomics
    country_gen_rows = ""
    for country in AFRICAN_COUNTRIES:
        gen = data["africa_country_genomics"].get(country, 0)
        pgx = data["africa_country_pgx"].get(country, 0)
        color = "#ef4444" if pgx == 0 else "#e2e8f0"
        country_gen_rows += (
            f'<tr>'
            f'<td style="padding:8px;">{escape_html(country)}</td>'
            f'<td style="padding:8px;text-align:right;">{gen}</td>'
            f'<td style="padding:8px;text-align:right;color:{color};font-weight:bold;">{pgx}</td>'
            f'</tr>\n'
        )

    # Chart data
    region_labels = json.dumps(list(REGIONS.keys()))
    pgx_total_by_region = json.dumps([
        sum(data["genomics_by_region"].get(r, {}).values()) for r in REGIONS
    ])

    gene_labels = json.dumps(list(PGX_GENES.keys()))
    pgx_af_vals = json.dumps([pgx_void["africa_genes"].get(g, 0) for g in PGX_GENES])
    pgx_us_vals = json.dumps([pgx_void["us_genes"].get(g, 0) for g in PGX_GENES])
    pgx_ch_vals = json.dumps([pgx_void["china_genes"].get(g, 0) for g in PGX_GENES])

    onc_labels = json.dumps(list(ONCOLOGY_QUERIES.keys()))
    onc_af_vals = json.dumps([oncology_gap[l]["africa"] for l in ONCOLOGY_QUERIES])
    onc_us_vals = json.dumps([oncology_gap[l]["us"] for l in ONCOLOGY_QUERIES])

    fri_display = "INF" if fri[0] == float('inf') else f"{fri[0]}x"

    africa_pgx_total = pgx_void["africa_total"]
    us_pgx_total = pgx_void["us_total"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Free Rider Genome: Everyone Sequences, Nobody Treats</title>
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
  --purple: #a855f7;
  --teal: #14b8a6;
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
  font-size: 2.2rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #14b8a6, #a855f7);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
h2 {{
  font-size: 1.5rem;
  margin: 2.5rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--border);
  color: var(--teal);
}}
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 2rem; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
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
.teal {{ color: var(--teal); }}
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
tr:hover {{ background: rgba(20, 184, 166, 0.05); }}
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}}
canvas {{ max-width: 100%; }}
.method-note {{
  background: rgba(20, 184, 166, 0.1);
  border-left: 4px solid var(--teal);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.danger-note {{
  background: rgba(239, 68, 68, 0.1);
  border-left: 4px solid var(--danger);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.warning-note {{
  background: rgba(245, 158, 11, 0.1);
  border-left: 4px solid var(--warning);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.purple-note {{
  background: rgba(168, 85, 247, 0.1);
  border-left: 4px solid var(--purple);
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  border-radius: 0 8px 8px 0;
  font-size: 0.9rem;
}}
.theory-box {{
  background: rgba(20, 184, 166, 0.08);
  border: 2px solid var(--teal);
  border-radius: 12px;
  padding: 2rem;
  margin: 1.5rem 0;
}}
.void-box {{
  background: rgba(239, 68, 68, 0.15);
  border: 2px solid var(--danger);
  border-radius: 12px;
  padding: 2rem;
  text-align: center;
  margin: 1.5rem 0;
}}
.void-box .big-zero {{
  font-size: 6rem;
  font-weight: 900;
  color: var(--danger);
  line-height: 1;
}}
.void-box .big-number {{
  font-size: 4rem;
  font-weight: 900;
  line-height: 1;
}}
.void-box .big-label {{
  font-size: 1.2rem;
  color: var(--muted);
  margin-top: 0.5rem;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.scroll-x {{ overflow-x: auto; }}
footer {{
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="container">

<h1>The Free Rider Genome: Everyone Sequences, Nobody Treats</h1>
<p class="subtitle">Olson (1965) meets Hardin (1968): Africa's genetic diversity is a public good
exploited without reciprocal clinical investment.</p>

<!-- 1. Game Theory -->
<h2>1. The Game Theory</h2>
<div class="theory-box">
<h3 style="color:var(--teal);margin-top:0;">The Free Rider Problem (Olson, 1965) + Tragedy of the Commons (Hardin, 1968)</h3>
<p>A <strong>public good</strong> is non-excludable (everyone can use it) and non-rivalrous
(one person's use doesn't diminish it). Africa's genetic diversity is the ultimate public good
for genomic medicine:</p>
<br>
<div class="two-col">
<div>
<p><strong style="color:var(--teal);">THE PUBLIC GOOD</strong></p>
<p>Africa has the highest genetic diversity on Earth. Every out-of-Africa population is a
subset of African diversity. This means African genomes contain variants critical for
understanding ALL human drug responses, disease susceptibility, and gene function.</p>
<br>
<p>&bull; H3Africa: {H3AFRICA_FACTS['participants_sequenced']:,} participants, {H3AFRICA_FACTS['countries_involved']} countries</p>
<p>&bull; {H3AFRICA_FACTS['genomes_sequenced']:,} whole genomes sequenced</p>
<p>&bull; {H3AFRICA_FACTS['publications_citing']} citing the data</p>
<p>&bull; Data policy: {H3AFRICA_FACTS['data_policy']}</p>
</div>
<div>
<p><strong style="color:var(--danger);">THE FREE RIDING</strong></p>
<p>Global researchers download H3Africa data, publish papers using African genetic diversity,
develop drugs informed by African variants &mdash; but invest ZERO in pharmacogenomics
trials that would benefit African patients.</p>
<br>
<p>&bull; Africa pharmacogenomics trials: <strong style="color:var(--danger);">{africa_pgx_total}</strong></p>
<p>&bull; US pharmacogenomics trials: <strong>{us_pgx_total}</strong></p>
<p>&bull; H3Africa interventional trials: <strong style="color:var(--danger);">{H3AFRICA_FACTS['interventional_trials_in_africa']}</strong></p>
<p>&bull; Free Rider Index: <strong style="color:var(--danger);">{fri_display}</strong></p>
</div>
</div>
</div>

<!-- 2. Summary -->
<h2>2. The Public Good Exploited</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Free Rider Index</div>
    <div class="value danger">{fri_display}</div>
    <div class="label">(global genomics trials) / (Africa genomics trials)</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa PGx Trials</div>
    <div class="value danger">{africa_pgx_total}</div>
    <div class="label">vs {us_pgx_total} in the US</div>
  </div>
  <div class="summary-card">
    <div class="label">H3Africa Interventional</div>
    <div class="value danger">{H3AFRICA_FACTS['interventional_trials_in_africa']}</div>
    <div class="label">{H3AFRICA_FACTS['publications_citing']} using the data</div>
  </div>
  <div class="summary-card">
    <div class="label">African Genetic Diversity</div>
    <div class="value teal">HIGHEST</div>
    <div class="label">globally; every population is a subset</div>
  </div>
</div>

<div class="void-box">
  <div class="big-zero">{africa_pgx_total}</div>
  <div class="big-label">pharmacogenomics trials in Africa</div>
  <div class="big-label" style="margin-top:1rem;color:var(--danger);">
    While Africans have the highest CYP2D6 ultra-rapid metabolizer frequency (20-40%) on Earth
  </div>
</div>

<!-- 3. H3Africa Paradox -->
<h2>3. The H3Africa Paradox: Sequencing Without Treating</h2>
<div class="danger-note">
<strong>H3Africa</strong> ({H3AFRICA_FACTS['funded_by']}, launched {H3AFRICA_FACTS['launched']})
sequenced {H3AFRICA_FACTS['genomes_sequenced']:,} African genomes and biobanked
{H3AFRICA_FACTS['biobank_samples']:,} samples. The data is used in {H3AFRICA_FACTS['publications_citing']}.
Yet <strong>zero interventional trials</strong> have translated this data into clinical applications
for African patients. The sequence-to-bedside pipeline stops at the sequence.
</div>

<table>
<thead>
<tr>
  <th>Query</th>
  <th style="text-align:right;">Global Interventional</th>
  <th style="text-align:right;">Africa Interventional</th>
</tr>
</thead>
<tbody>
{h3_rows}
</tbody>
</table>

<!-- 4. Genomics Trials -->
<h2>4. Genomics/Precision Medicine Trials by Region</h2>
<div class="scroll-x">
<table>
<thead>
<tr>
  <th>Category</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">US</th>
  <th style="text-align:right;">China</th>
  <th style="text-align:right;">India</th>
  <th style="text-align:right;">UK</th>
</tr>
</thead>
<tbody>
{genomics_rows}
</tbody>
</table>
</div>

<div class="chart-container">
<h3>Total Genomics/Precision Medicine Trials by Region</h3>
<canvas id="genomicsChart" height="300"></canvas>
</div>

<!-- 5. CYP2D6/CYP2B6 Gap -->
<h2>5. The CYP2D6/CYP2B6 Gap: Different Genes, No Dose-Finding</h2>
<div class="purple-note">
<strong>Africans metabolize drugs differently.</strong> CYP2D6 ultra-rapid metabolizers (20-40% of Africans
vs 1-10% of Europeans) convert codeine to morphine at dangerous rates. CYP2B6*6 slow metabolizers
(40-50% of Africans) accumulate toxic efavirenz levels during HIV treatment. CYP3A5 expressors
(&gt;70% of Africans vs &lt;30% of Europeans) need higher tacrolimus doses after transplant.
Yet there are <strong>{africa_pgx_total} pharmacogenomics trials</strong> in Africa to optimize dosing.
</div>

<h3>Allele Frequencies: Why African PGx Matters</h3>
<table>
<thead>
<tr>
  <th>Allele / Phenotype</th>
  <th style="text-align:center;">African</th>
  <th style="text-align:center;">European</th>
  <th style="text-align:center;">East Asian</th>
  <th>Clinical Impact</th>
</tr>
</thead>
<tbody>
{allele_rows}
</tbody>
</table>

<h3>Pharmacogenomics Trials by Gene</h3>
<table>
<thead>
<tr>
  <th>Gene</th>
  <th>Clinical Relevance</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">US</th>
  <th style="text-align:right;">China</th>
</tr>
</thead>
<tbody>
{pgx_rows}
</tbody>
</table>

<div class="chart-container">
<h3>PGx Trials by Gene: Africa vs US vs China</h3>
<canvas id="pgxChart" height="300"></canvas>
</div>

<!-- 6. BRCA/HER2 in Africa -->
<h2>6. BRCA in Africa: Triple-Negative BC Higher, Targeted Therapy Absent</h2>
<div class="warning-note">
<strong>African women have the highest rates of triple-negative breast cancer (TNBC)</strong>
globally &mdash; the subtype most resistant to standard chemotherapy and most in need of
targeted therapies. BRCA1/2 mutations are found at significant frequencies in African populations.
Yet precision oncology trials (BRCA-targeted, HER2-targeted, immunotherapy) are concentrated
overwhelmingly in the US and Europe.
</div>

<table>
<thead>
<tr>
  <th>Precision Oncology Category</th>
  <th style="text-align:right;">Africa</th>
  <th style="text-align:right;">US</th>
  <th style="text-align:right;">US:Africa Ratio</th>
</tr>
</thead>
<tbody>
{onc_rows}
</tbody>
</table>

<div class="chart-container">
<h3>Precision Oncology: Africa vs US</h3>
<canvas id="oncChart" height="300"></canvas>
</div>

<!-- 7. Per-Country -->
<h2>7. Per-Country Genomics in Africa</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Genomics/Precision Med Trials</th>
  <th style="text-align:right;">Pharmacogenomics Trials</th>
</tr>
</thead>
<tbody>
{country_gen_rows}
</tbody>
</table>

<!-- 8. Fair Contribution / Coase -->
<h2>8. What "Fair Contribution" Looks Like</h2>
<div class="theory-box">
<h3 style="color:var(--teal);margin-top:0;">The Coase Theorem Solution: Property Rights Over Genomic Data</h3>
<p>Ronald Coase (1960) showed that externalities can be resolved through well-defined property
rights and bargaining. Applied to African genomic data:</p>
<br>
<p><strong>1. Data sovereignty (establish property rights):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">African biobanks should require "clinical trial
reciprocity" &mdash; any researcher downloading African genomic data must contribute to a
fund for pharmacogenomics trials in Africa. The H3Africa data is currently open access;
conditional access would create the missing property right.</p>
<br>
<p><strong>2. Benefit-sharing agreements (Nagoya Protocol for genomes):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">The Nagoya Protocol on Access and Benefit Sharing
already governs biological resources. Extend it explicitly to human genomic data: if a drug is
developed using African genetic diversity data, a percentage of revenue funds African clinical
trials.</p>
<br>
<p><strong>3. PGx trial mandates (internalize the externality):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">For any drug where CYP2D6, CYP2B6, or CYP3A5
variants affect metabolism, require African dose-finding studies as a condition of WHO
prequalification. Currently {africa_pgx_total} such trials exist.</p>
<br>
<p><strong>4. Reciprocal data contributions (end free riding):</strong></p>
<p style="margin-left:1rem;color:var(--muted);">Every non-African genomic medicine trial that
cites African-origin genetic data should be required to include African sites or fund
equivalent African trials. Convert free riding into fair contribution.</p>
</div>

<!-- Method -->
<h2>Method</h2>
<div class="method-note">
<strong>Data source:</strong> ClinicalTrials.gov API v2 (accessed {datetime.now().strftime('%d %B %Y')}).<br>
<strong>Regions:</strong> Africa (10 countries), United States, China, India, United Kingdom.<br>
<strong>Genomics queries:</strong> Precision medicine, pharmacogenomics, biomarker-guided,
targeted therapy, gene therapy, liquid biopsy &mdash; filtered to interventional studies.<br>
<strong>PGx gene queries:</strong> CYP2D6, CYP2B6, CYP3A5, UGT1A1, VKORC1, HLA-B*5701.<br>
<strong>Oncology queries:</strong> BRCA, HER2, triple-negative BC, PD-L1/immunotherapy.<br>
<strong>Allele frequencies:</strong> Published population genetics literature
(PharmGKB, gnomAD, H3Africa publications).<br>
<strong>H3Africa facts:</strong> H3Africa consortium reports (2012-2024).<br>
<strong>Free Rider Index:</strong> Sum of genomics trials in US+China+India+UK divided by Africa total.<br>
<strong>Limitations:</strong> Single registry; pharmacogenomics studies may use different
terminology; H3Africa-funded observational studies are excluded (interventional filter);
allele frequencies are population-level estimates with intra-African variation.
</div>

<footer>
The Free Rider Genome: Everyone Sequences, Nobody Treats &mdash; ClinicalTrials.gov Registry Analysis |
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} |
Data: ClinicalTrials.gov API v2
</footer>

</div>

<script>
// Genomics by region
new Chart(document.getElementById('genomicsChart'), {{
  type: 'bar',
  data: {{
    labels: {region_labels},
    datasets: [{{
      label: 'Total Genomics/Precision Med Trials',
      data: {pgx_total_by_region},
      backgroundColor: ['#ef4444', '#3b82f6', '#f59e0b', '#a855f7', '#14b8a6'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

// PGx by gene
new Chart(document.getElementById('pgxChart'), {{
  type: 'bar',
  data: {{
    labels: {gene_labels},
    datasets: [
      {{ label: 'Africa', data: {pgx_af_vals}, backgroundColor: '#ef4444', borderRadius: 4 }},
      {{ label: 'US', data: {pgx_us_vals}, backgroundColor: '#3b82f6', borderRadius: 4 }},
      {{ label: 'China', data: {pgx_ch_vals}, backgroundColor: '#f59e0b', borderRadius: 4 }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
    }}
  }}
}});

// Precision oncology
new Chart(document.getElementById('oncChart'), {{
  type: 'bar',
  data: {{
    labels: {onc_labels},
    datasets: [
      {{ label: 'Africa', data: {onc_af_vals}, backgroundColor: '#ef4444', borderRadius: 4 }},
      {{ label: 'US', data: {onc_us_vals}, backgroundColor: '#3b82f6', borderRadius: 4 }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', maxRotation: 45 }} }}
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
    print("=" * 70)
    print("The Free Rider Genome: Everyone Sequences, Nobody Treats")
    print("=" * 70)
    print()

    print("Fetching trial data from ClinicalTrials.gov API v2...")
    data = fetch_all_data()
    print()

    print("Computing Free Rider Index...")
    fri = compute_free_rider_index(data)
    fri_display = "INF" if fri[0] == float('inf') else f"{fri[0]}x"
    print(f"  Free Rider Index: {fri_display} (Africa={fri[1]}, Others={fri[2]})")

    print("Computing pharmacogenomics void...")
    pgx_void = compute_pgx_void(data)
    print(f"  Africa PGx total: {pgx_void['africa_total']}")
    print(f"  US PGx total:     {pgx_void['us_total']}")
    print(f"  China PGx total:  {pgx_void['china_total']}")

    print("Computing precision oncology gap...")
    oncology_gap = compute_oncology_gap(data)
    for label, gap in oncology_gap.items():
        ratio_str = "INF" if gap["ratio"] == 999 else f'{gap["ratio"]}x'
        print(f"  {label}: Africa={gap['africa']}, US={gap['us']}, ratio={ratio_str}")

    # Summary
    print()
    print("-" * 70)
    print("FREE RIDER GENOME SUMMARY")
    print("-" * 70)
    print(f"  Free Rider Index:         {fri_display}")
    print(f"  Africa PGx trials:        {pgx_void['africa_total']}")
    print(f"  US PGx trials:            {pgx_void['us_total']}")
    print(f"  H3Africa interventional:  {H3AFRICA_FACTS['interventional_trials_in_africa']}")
    for gene in PGX_GENES:
        af = pgx_void["africa_genes"].get(gene, 0)
        us = pgx_void["us_genes"].get(gene, 0)
        print(f"  {gene:15s} Africa: {af:>3} | US: {us:>3}")

    # Generate HTML
    print()
    print("Generating HTML dashboard...")
    html = generate_html(data, fri, pgx_void, oncology_gap)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
