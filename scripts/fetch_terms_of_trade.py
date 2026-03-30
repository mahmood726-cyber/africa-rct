#!/usr/bin/env python
"""
fetch_terms_of_trade.py - The Terms of Trade: Raw Data Exported, Finished Drugs Imported

Inspired by the Prebisch-Singer hypothesis in development economics:
developing countries that export raw materials and import manufactured
goods face declining terms of trade.  Africa EXPORTS raw research inputs
(participants, blood samples, clinical data) and IMPORTS finished products
(drugs, devices, vaccines) at enormous markup.

We compute "Research Terms of Trade" for Africa:
  - EXPORT side: Phase 3 trials (enrollment = raw material exported)
  - IMPORT side: Phase 1 trials (innovation = developed locally)
  - Phase 3 / Phase 1 ratio as "terms of trade proxy"
    High ratio = exporter of raw material (participants)
    Low ratio  = also an innovator
  - Africa vs US/Europe comparison
  - WHO Essential Medicines List overlap estimate

Outputs:
  - data/terms_of_trade_data.json  (cached API results, 24h TTL)
  - terms-of-trade.html            (dark-theme interactive dashboard)
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

# African countries for detailed analysis
AFRICAN_COUNTRIES = {
    "South Africa":  62,
    "Nigeria":       230,
    "Kenya":         56,
    "Uganda":        48,
    "Tanzania":      67,
    "Ethiopia":      130,
    "Ghana":         34,
    "Zambia":        21,
    "Malawi":        21,
    "Mozambique":    33,
    "Zimbabwe":      15,
    "Senegal":       17,
    "Cameroon":      27,
    "Burkina Faso":  23,
    "Rwanda":        14,
}

# Comparator regions / countries
COMPARATORS = {
    "United States": 335,
    "United Kingdom": 68,
    "France":        68,
    "Germany":       84,
    "India":         1400,
    "China":         1425,
    "Brazil":        216,
}

# WHO Essential Medicines List (2023) - representative drugs commonly
# tested in African Phase 3 trials
WHO_EML_DRUGS = [
    "dolutegravir", "tenofovir", "efavirenz", "artemether",
    "lumefantrine", "artesunate", "amoxicillin", "metformin",
    "oxytocin", "misoprostol", "zinc", "oral rehydration",
    "rifampicin", "isoniazid", "pyrazinamide", "ethambutol",
    "sulfadoxine", "pyrimethamine", "nevirapine", "lopinavir",
    "atazanavir", "abacavir", "zidovudine", "lamivudine",
    "cyclophosphamide", "cisplatin", "morphine", "ibuprofen",
]

# Drugs commonly tested in Phase 3 in Africa that are NOT affordable
PREMIUM_DRUGS = [
    "pembrolizumab", "nivolumab", "trastuzumab", "bevacizumab",
    "sofosbuvir", "ledipasvir", "cabotegravir", "lenacapavir",
    "dapagliflozin", "empagliflozin", "semaglutide",
]

CACHE_FILE = Path(__file__).resolve().parent / "data" / "terms_of_trade_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "terms-of-trade.html"
RATE_LIMIT = 0.35
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

REFERENCES = [
    {"pmid": "39972388", "desc": "Clinical trial infrastructure in Africa"},
    {"pmid": "37643290", "desc": "Global trial participation and drug access"},
    {"pmid": "36332653", "desc": "Pharmaceutical value chain and Africa"},
    {"pmid": "34843674", "desc": "Prebisch-Singer and health research paradigm"},
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


def get_phase_count(location, phase_filter):
    """Return count of interventional trials for a given phase + location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[Phase]{phase_filter}",
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


def get_phase3_with_enrollment(location, page_size=50):
    """Get Phase 3 trials with enrollment data for a location.

    Returns list of (nctId, enrollment, title) tuples.
    Limited to first few pages to stay within rate limits.
    """
    results = []
    next_token = None

    for page in range(5):  # max 5 pages = 250 trials
        params = {
            "format": "json",
            "query.locn": location,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[Phase]PHASE3",
            "fields": "NCTId,BriefTitle,EnrollmentCount,EnrollmentType,InterventionName",
            "pageSize": page_size,
            "countTotal": "true",
        }
        if next_token:
            params["pageToken"] = next_token

        data = api_get(params)
        if data is None:
            break

        studies = data.get("studies", [])
        for study in studies:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            design = proto.get("designModule", {})
            arms = proto.get("armsInterventionsModule", {})

            nct_id = ident.get("nctId", "")
            title = ident.get("briefTitle", "")
            enrollment = design.get("enrollmentInfo", {}).get("count", 0)
            interventions = arms.get("interventions", [])
            drug_names = [
                iv.get("name", "")
                for iv in interventions
                if iv.get("type", "") in ("DRUG", "BIOLOGICAL", "DEVICE")
            ]

            if enrollment and enrollment > 0:
                results.append({
                    "nctId": nct_id,
                    "title": title,
                    "enrollment": enrollment,
                    "drugs": drug_names,
                })

        next_token = data.get("nextPageToken")
        if not next_token:
            break
        time.sleep(RATE_LIMIT)

    return results


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
    """Fetch phase-specific trial counts for African + comparator countries."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "african_countries": {},
        "comparators": {},
        "africa_phase3_detail": [],
    }

    # Phase queries: Phase 1, Phase 2, Phase 3, Phase 4
    phases = {
        "phase1": "(EARLY_PHASE1 OR PHASE1)",
        "phase2": "PHASE2",
        "phase3": "PHASE3",
        "phase4": "PHASE4",
    }

    # --- African countries ---
    all_african = list(AFRICAN_COUNTRIES.keys())
    total_calls = len(all_african) * (len(phases) + 1)  # phases + total
    total_calls += len(COMPARATORS) * (len(phases) + 1)
    total_calls += 1  # phase3 detail for Uganda
    call_num = 0

    for country in all_african:
        pop = AFRICAN_COUNTRIES[country]
        phase_counts = {}

        for phase_key, phase_filter in phases.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {phase_key}...")
            phase_counts[phase_key] = get_phase_count(country, phase_filter)
            time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / Total...")
        total_count = get_total_interventional(country)
        time.sleep(RATE_LIMIT)

        data["african_countries"][country] = {
            "population_m": pop,
            "phases": phase_counts,
            "total_trials": total_count,
        }

    # --- Comparator countries ---
    for country in COMPARATORS:
        pop = COMPARATORS[country]
        phase_counts = {}

        for phase_key, phase_filter in phases.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {phase_key}...")
            phase_counts[phase_key] = get_phase_count(country, phase_filter)
            time.sleep(RATE_LIMIT)

        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country} / Total...")
        total_count = get_total_interventional(country)
        time.sleep(RATE_LIMIT)

        data["comparators"][country] = {
            "population_m": pop,
            "phases": phase_counts,
            "total_trials": total_count,
        }

    # --- Uganda Phase 3 detail (largest African dataset) ---
    call_num += 1
    print(f"  [{call_num}/{total_calls}] Uganda Phase 3 detail...")
    data["africa_phase3_detail"] = get_phase3_with_enrollment("Uganda")

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cached to {CACHE_FILE}")

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_metrics(data):
    """Compute Research Terms of Trade metrics."""
    country_metrics = {}

    # Process African countries
    for country, info in data["african_countries"].items():
        phases = info["phases"]
        total = info["total_trials"]
        pop = info["population_m"]
        p1 = phases.get("phase1", 0)
        p2 = phases.get("phase2", 0)
        p3 = phases.get("phase3", 0)
        p4 = phases.get("phase4", 0)

        # Terms of Trade Proxy: Phase 3 / Phase 1
        # High = raw material exporter (testing, not innovating)
        if p1 > 0:
            tot_ratio = round(p3 / p1, 2)
        else:
            tot_ratio = float("inf") if p3 > 0 else 0.0

        # Innovation Index: Phase 1 / total (higher = more early-stage R&D)
        innovation = round(p1 / total, 3) if total > 0 else 0.0

        # Export Intensity: Phase 3 / total (higher = more testing labor)
        export_intensity = round(p3 / total, 3) if total > 0 else 0.0

        country_metrics[country] = {
            "region": "africa",
            "population_m": pop,
            "phase1": p1,
            "phase2": p2,
            "phase3": p3,
            "phase4": p4,
            "total_trials": total,
            "tot_ratio": tot_ratio if tot_ratio != float("inf") else 999.0,
            "innovation_index": innovation,
            "export_intensity": export_intensity,
            "per_capita_total": round(total / pop, 2) if pop > 0 else 0,
        }

    # Process comparators
    for country, info in data["comparators"].items():
        phases = info["phases"]
        total = info["total_trials"]
        pop = info["population_m"]
        p1 = phases.get("phase1", 0)
        p2 = phases.get("phase2", 0)
        p3 = phases.get("phase3", 0)
        p4 = phases.get("phase4", 0)

        if p1 > 0:
            tot_ratio = round(p3 / p1, 2)
        else:
            tot_ratio = float("inf") if p3 > 0 else 0.0

        innovation = round(p1 / total, 3) if total > 0 else 0.0
        export_intensity = round(p3 / total, 3) if total > 0 else 0.0

        country_metrics[country] = {
            "region": "comparator",
            "population_m": pop,
            "phase1": p1,
            "phase2": p2,
            "phase3": p3,
            "phase4": p4,
            "total_trials": total,
            "tot_ratio": tot_ratio if tot_ratio != float("inf") else 999.0,
            "innovation_index": innovation,
            "export_intensity": export_intensity,
            "per_capita_total": round(total / pop, 2) if pop > 0 else 0,
        }

    # Africa aggregate
    africa_p1 = sum(m["phase1"] for m in country_metrics.values() if m["region"] == "africa")
    africa_p3 = sum(m["phase3"] for m in country_metrics.values() if m["region"] == "africa")
    africa_total = sum(m["total_trials"] for m in country_metrics.values() if m["region"] == "africa")
    africa_pop = sum(m["population_m"] for m in country_metrics.values() if m["region"] == "africa")

    us_data = country_metrics.get("United States", {})
    us_p1 = us_data.get("phase1", 0)
    us_p3 = us_data.get("phase3", 0)

    africa_tot_ratio = round(africa_p3 / africa_p1, 2) if africa_p1 > 0 else 999.0
    us_tot_ratio = round(us_p3 / us_p1, 2) if us_p1 > 0 else 999.0

    # WHO EML analysis on Uganda Phase 3 detail
    detail = data.get("africa_phase3_detail", [])
    total_enrollment = 0
    eml_trials = 0
    premium_trials = 0
    large_trials = []  # enrollment > 100

    for trial in detail:
        enrollment = trial.get("enrollment", 0)
        total_enrollment += enrollment
        drugs = " ".join(trial.get("drugs", [])).lower()

        is_eml = any(d in drugs for d in WHO_EML_DRUGS)
        is_premium = any(d in drugs for d in PREMIUM_DRUGS)

        if is_eml:
            eml_trials += 1
        if is_premium:
            premium_trials += 1
        if enrollment > 100:
            large_trials.append(trial)

    eml_pct = round(eml_trials / max(1, len(detail)) * 100, 1)
    premium_pct = round(premium_trials / max(1, len(detail)) * 100, 1)

    return {
        "country_metrics": country_metrics,
        "aggregate": {
            "africa_phase1": africa_p1,
            "africa_phase3": africa_p3,
            "africa_total": africa_total,
            "africa_pop": africa_pop,
            "africa_tot_ratio": africa_tot_ratio,
            "us_tot_ratio": us_tot_ratio,
            "total_enrollment_sampled": total_enrollment,
            "n_detail_trials": len(detail),
            "eml_trials": eml_trials,
            "eml_pct": eml_pct,
            "premium_trials": premium_trials,
            "premium_pct": premium_pct,
            "large_trials_count": len(large_trials),
        },
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
    agg = metrics["aggregate"]

    # --- Africa country rows sorted by ToT ratio descending ---
    africa_sorted = sorted(
        [(c, m) for c, m in cm.items() if m["region"] == "africa"],
        key=lambda x: -x[1]["tot_ratio"] if x[1]["tot_ratio"] < 999 else -9999
    )
    comparator_sorted = sorted(
        [(c, m) for c, m in cm.items() if m["region"] == "comparator"],
        key=lambda x: -x[1]["tot_ratio"] if x[1]["tot_ratio"] < 999 else -9999
    )

    # --- Africa table rows ---
    africa_rows = ""
    for country, m in africa_sorted:
        tot = m["tot_ratio"]
        tot_display = f"{tot:.2f}" if tot < 999 else "N/A"
        tot_color = "#ef4444" if tot > 3 else "#f59e0b" if tot > 1.5 else "#22c55e"
        inn_color = "#22c55e" if m["innovation_index"] > 0.1 else "#f59e0b" if m["innovation_index"] > 0.05 else "#ef4444"
        africa_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(country)}</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase1"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase2"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase3"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["total_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{tot_color};font-weight:bold;">'
            f'{tot_display}</td>'
            f'<td style="padding:10px;text-align:right;color:{inn_color};">'
            f'{round(m["innovation_index"] * 100, 1)}%</td>'
            f'<td style="padding:10px;text-align:right;">'
            f'{round(m["export_intensity"] * 100, 1)}%</td>'
            f'</tr>\n'
        )

    # --- Comparator table rows ---
    comparator_rows = ""
    for country, m in comparator_sorted:
        tot = m["tot_ratio"]
        tot_display = f"{tot:.2f}" if tot < 999 else "N/A"
        tot_color = "#ef4444" if tot > 3 else "#f59e0b" if tot > 1.5 else "#22c55e"
        inn_color = "#22c55e" if m["innovation_index"] > 0.1 else "#f59e0b" if m["innovation_index"] > 0.05 else "#ef4444"
        comparator_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(country)}</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase1"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase2"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["phase3"]:,}</td>'
            f'<td style="padding:10px;text-align:right;">{m["total_trials"]:,}</td>'
            f'<td style="padding:10px;text-align:right;color:{tot_color};font-weight:bold;">'
            f'{tot_display}</td>'
            f'<td style="padding:10px;text-align:right;color:{inn_color};">'
            f'{round(m["innovation_index"] * 100, 1)}%</td>'
            f'<td style="padding:10px;text-align:right;">'
            f'{round(m["export_intensity"] * 100, 1)}%</td>'
            f'</tr>\n'
        )

    # --- Chart data: Africa vs Comparators Phase 1 vs Phase 3 ---
    all_labels = [c for c, _ in africa_sorted] + [c for c, _ in comparator_sorted]
    all_p1 = [cm[c]["phase1"] for c in [x[0] for x in africa_sorted]] + \
             [cm[c]["phase1"] for c in [x[0] for x in comparator_sorted]]
    all_p3 = [cm[c]["phase3"] for c in [x[0] for x in africa_sorted]] + \
             [cm[c]["phase3"] for c in [x[0] for x in comparator_sorted]]
    all_labels_json = json.dumps(all_labels)
    all_p1_json = json.dumps(all_p1)
    all_p3_json = json.dumps(all_p3)

    # --- ToT ratio chart data ---
    tot_labels = [c for c, _ in africa_sorted + comparator_sorted]
    tot_values = [
        cm[c]["tot_ratio"] if cm[c]["tot_ratio"] < 999 else 0
        for c in tot_labels
    ]
    tot_colors = [
        "'rgba(239,68,68,0.8)'" if cm[c]["region"] == "africa"
        else "'rgba(59,130,246,0.8)'"
        for c in tot_labels
    ]
    tot_labels_json = json.dumps(tot_labels)
    tot_values_json = json.dumps(tot_values)
    tot_colors_json = "[" + ",".join(tot_colors) + "]"

    # --- Innovation scatter data ---
    scatter_africa = json.dumps([
        {"x": m["innovation_index"] * 100, "y": m["export_intensity"] * 100, "label": c}
        for c, m in africa_sorted
    ])
    scatter_comp = json.dumps([
        {"x": m["innovation_index"] * 100, "y": m["export_intensity"] * 100, "label": c}
        for c, m in comparator_sorted
    ])

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
<title>The Terms of Trade - Raw Data Exported, Finished Drugs Imported</title>
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
  --cotton: #d4a574;
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
  background: linear-gradient(135deg, #d4a574, #ef4444);
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
.cotton {{ color: var(--cotton); }}
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
.insight-box.cotton-box {{ border-left-color: var(--cotton); }}
.metric-def {{
  background: rgba(212, 165, 116, 0.08);
  border: 1px solid rgba(212, 165, 116, 0.2);
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

<h1>The Terms of Trade</h1>
<p class="subtitle">
  Raw Data Exported, Finished Drugs Imported &mdash; The Prebisch-Singer
  hypothesis applied to Africa&rsquo;s clinical trial ecosystem
</p>

<div class="insight-box cotton-box">
  <strong>The Prebisch-Singer Hypothesis</strong><br>
  In 1950, Raul Prebisch and Hans Singer independently observed that
  developing countries exporting raw materials (cotton, copper, cocoa)
  and importing manufactured goods (textiles, electronics, machinery)
  face <em>declining terms of trade</em>: the price of raw materials
  falls relative to manufactures over time, trapping exporters in
  permanent disadvantage.<br><br>
  Africa&rsquo;s clinical trial ecosystem follows the same pattern.
  <strong>African countries EXPORT raw research inputs</strong> &mdash;
  participants&rsquo; bodies, blood samples, clinical data, enrollment
  numbers &mdash; and <strong>IMPORT finished products</strong> &mdash;
  patented drugs, branded devices, expensive biologics &mdash; at
  100x markup.  The cotton-to-textiles trade of the colonial era
  has been replaced by a data-to-drugs trade that is just as extractive.
</div>

<div class="metric-def">
  <strong>Terms of Trade Ratio</strong> = Phase 3 trials / Phase 1 trials<br>
  <span class="danger">High ratio</span> = country mainly tests other
  people&rsquo;s drugs (raw material exporter)<br>
  <span class="success">Low ratio</span> = country also develops its
  own drugs (innovator)<br><br>
  <strong>Innovation Index</strong> = Phase 1 / total trials
  (higher = more early-stage R&amp;D)<br>
  <strong>Export Intensity</strong> = Phase 3 / total trials
  (higher = more testing labor for foreign sponsors)
</div>

<h2>The $500 Billion Question</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">Africa Phase 3 Trials</div>
    <div class="value danger">{agg["africa_phase3"]:,}</div>
    <div class="label">&ldquo;raw materials exported&rdquo;</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa Phase 1 Trials</div>
    <div class="value success">{agg["africa_phase1"]:,}</div>
    <div class="label">&ldquo;local innovation&rdquo;</div>
  </div>
  <div class="summary-card">
    <div class="label">Africa ToT Ratio</div>
    <div class="value danger">{agg["africa_tot_ratio"]:.1f}x</div>
    <div class="label">Phase 3 / Phase 1</div>
  </div>
  <div class="summary-card">
    <div class="label">US ToT Ratio</div>
    <div class="value success">{agg["us_tot_ratio"]:.1f}x</div>
    <div class="label">Phase 3 / Phase 1</div>
  </div>
  <div class="summary-card">
    <div class="label">WHO EML Drugs in Ph3</div>
    <div class="value warning">{agg["eml_pct"]}%</div>
    <div class="label">of Uganda Ph3 trials</div>
  </div>
  <div class="summary-card">
    <div class="label">Premium Drugs in Ph3</div>
    <div class="value purple">{agg["premium_pct"]}%</div>
    <div class="label">likely unaffordable post-trial</div>
  </div>
</div>

<h2>The Colonial Trade Analogy</h2>
<div class="insight-box">
  <strong>Cotton &rarr; Textiles, Data &rarr; Drugs</strong><br>
  In the colonial era, Africa exported raw cotton and imported finished
  textiles at enormous markup.  Today, the pattern is identical:<br><br>
  <strong>EXPORT</strong>: Africa contributes {agg["africa_phase3"]:,}
  Phase 3 trials &mdash; thousands of participants whose bodies, time,
  and biological data generate the evidence that makes drug approval
  possible.  Each participant represents an &ldquo;export&rdquo; of raw
  research material.<br><br>
  <strong>IMPORT</strong>: The drugs approved using that African data
  are sold back to African health systems at prices few can afford.
  Of the Uganda Phase 3 trials sampled, only {agg["eml_pct"]}%
  test WHO Essential Medicines List drugs, while {agg["premium_pct"]}%
  test premium-priced biologics and novel agents likely unaffordable
  post-trial.
</div>

<h2>African Countries: Phase Portfolio</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Sorted by Terms of Trade ratio (highest = most extractive)
</p>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Phase 1</th>
  <th style="text-align:right;">Phase 2</th>
  <th style="text-align:right;">Phase 3</th>
  <th style="text-align:right;">Total</th>
  <th style="text-align:right;">ToT Ratio</th>
  <th style="text-align:right;">Innovation %</th>
  <th style="text-align:right;">Export %</th>
</tr>
</thead>
<tbody>
{africa_rows}
</tbody>
</table>

<h2>Comparator Countries: The Innovators</h2>
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Phase 1</th>
  <th style="text-align:right;">Phase 2</th>
  <th style="text-align:right;">Phase 3</th>
  <th style="text-align:right;">Total</th>
  <th style="text-align:right;">ToT Ratio</th>
  <th style="text-align:right;">Innovation %</th>
  <th style="text-align:right;">Export %</th>
</tr>
</thead>
<tbody>
{comparator_rows}
</tbody>
</table>

<h2>Phase 1 vs Phase 3: Africa vs the World</h2>
<div class="chart-container">
  <canvas id="phaseChart" height="100"></canvas>
</div>

<h2>Terms of Trade Ratio by Country</h2>
<div class="chart-container">
  <canvas id="totChart" height="120"></canvas>
</div>

<h2>WHO Essential Medicines List Overlap</h2>
<div class="insight-box warning-box">
  <strong>Uganda Phase 3 Sample Analysis (n={agg["n_detail_trials"]})</strong><br>
  Of Phase 3 trials sampled from Uganda with enrollment data:<br>
  &bull; <strong>{agg["eml_trials"]}</strong> ({agg["eml_pct"]}%) test
  drugs on the WHO Essential Medicines List &mdash; affordable and
  available post-trial<br>
  &bull; <strong>{agg["premium_trials"]}</strong> ({agg["premium_pct"]}%)
  test premium-priced drugs &mdash; likely unaffordable in the
  testing country after trial completion<br>
  &bull; Total estimated participants enrolled:
  <strong>{agg["total_enrollment_sampled"]:,}</strong><br><br>
  This is the heart of the Prebisch-Singer dynamic: Africa contributes
  raw material (participant data) to generate evidence that primarily
  benefits pharmaceutical companies and high-income country health
  systems.
</div>

<h2>Methods</h2>
<div class="insight-box success-box">
  ClinicalTrials.gov API v2 was queried for interventional trials by
  phase (1&ndash;4) across 15 African countries and 7 comparator
  nations (US, UK, France, Germany, India, China, Brazil).  The Terms
  of Trade ratio was computed as Phase 3 trials divided by Phase 1
  trials for each country.  For Uganda, detailed Phase 3 trial data
  was retrieved including enrollment counts and intervention names,
  which were matched against the WHO Essential Medicines List and a
  list of premium-priced agents.  All queries are reproducible from
  the cached data file.
</div>

<h2>References</h2>
<ul style="list-style:none;padding:0;">
{ref_rows}
</ul>

<footer>
  Data: ClinicalTrials.gov API v2 |
  Generated: {data.get("timestamp", "N/A")[:10]} |
  The Terms of Trade v1.0 |
  Open-access research tool
</footer>

</div>

<script>
// Phase 1 vs Phase 3 grouped bar
new Chart(document.getElementById('phaseChart'), {{
  type: 'bar',
  data: {{
    labels: {all_labels_json},
    datasets: [
      {{
        label: 'Phase 1 (Innovation)',
        data: {all_p1_json},
        backgroundColor: 'rgba(34,197,94,0.8)',
      }},
      {{
        label: 'Phase 3 (Testing / Export)',
        data: {all_p3_json},
        backgroundColor: 'rgba(239,68,68,0.8)',
      }},
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'Phase 1 (Innovation) vs Phase 3 (Testing) by Country', color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ labels: {{ color: '#94a3b8' }} }},
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45, minRotation: 45 }}, grid: {{ color: '#1e293b' }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }}, title: {{ display: true, text: 'Trial count', color: '#94a3b8' }} }},
    }},
  }},
}});

// Terms of Trade ratio horizontal bar
new Chart(document.getElementById('totChart'), {{
  type: 'bar',
  data: {{
    labels: {tot_labels_json},
    datasets: [{{
      label: 'ToT Ratio (Phase 3 / Phase 1)',
      data: {tot_values_json},
      backgroundColor: {tot_colors_json},
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'Terms of Trade Ratio (Phase 3 / Phase 1)', color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ display: false }},
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }}, title: {{ display: true, text: 'Ratio (higher = more extractive)', color: '#94a3b8' }} }},
      y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
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
    print("  The Terms of Trade")
    print("  Raw Data Exported, Finished Drugs Imported")
    print("=" * 60)

    print("\n[1/3] Fetching trial data...")
    data = fetch_all_data()

    print("\n[2/3] Computing Terms of Trade metrics...")
    metrics = compute_metrics(data)

    cm = metrics["country_metrics"]
    agg = metrics["aggregate"]

    print(f"\n--- Africa Aggregate ---")
    print(f"  Phase 1 trials:    {agg['africa_phase1']:,}")
    print(f"  Phase 3 trials:    {agg['africa_phase3']:,}")
    print(f"  ToT Ratio:         {agg['africa_tot_ratio']:.2f}")
    print(f"  US ToT Ratio:      {agg['us_tot_ratio']:.2f}")

    print(f"\n--- WHO EML Analysis (Uganda sample) ---")
    print(f"  Trials sampled:    {agg['n_detail_trials']}")
    print(f"  EML drugs:         {agg['eml_trials']} ({agg['eml_pct']}%)")
    print(f"  Premium drugs:     {agg['premium_trials']} ({agg['premium_pct']}%)")
    print(f"  Total enrollment:  {agg['total_enrollment_sampled']:,}")

    print(f"\n--- Per-Country ToT Ratio ---")
    for country, m in sorted(cm.items(), key=lambda x: -x[1]["tot_ratio"] if x[1]["tot_ratio"] < 999 else -9999):
        tot = m["tot_ratio"]
        tot_str = f"{tot:.2f}" if tot < 999 else "N/A"
        region_tag = "AFR" if m["region"] == "africa" else "CMP"
        print(f"  [{region_tag}] {country:20s}  ToT={tot_str:>8s}  Inn={round(m['innovation_index']*100,1):5.1f}%  Exp={round(m['export_intensity']*100,1):5.1f}%")

    print(f"\n[3/3] Generating HTML dashboard...")
    html = generate_html(data, metrics)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Written to {OUTPUT_HTML}")
    print(f"  File size: {OUTPUT_HTML.stat().st_size:,} bytes")
    print("\nDone.")


if __name__ == "__main__":
    main()
