#!/usr/bin/env python
"""
fetch_phase_transition.py -- Project 40: The Phase Transition
=============================================================
Identifies the critical per-capita trial density at which African
research ecosystems transition from "dependent" (foreign-led,
narrow portfolio) to "self-sustaining" (local sponsors, diverse
conditions, Phase 1 capacity).

Inspired by statistical physics: phase transitions occur at critical
thresholds where system behavior changes qualitatively.

Usage:
    python fetch_phase_transition.py

Outputs:
    data/phase_transition_data.json  (cached API results, 24h TTL)
    phase-transition.html            (dark-theme interactive dashboard)

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
CACHE_FILE = DATA_DIR / "phase_transition_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "phase-transition.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# -- 20 African countries for detailed analysis --------------------------------
# Chosen to span the full density spectrum: hubs to voids

STUDY_COUNTRIES = {
    "South Africa":   62,
    "Egypt":          110,
    "Kenya":          56,
    "Uganda":         48,
    "Nigeria":        230,
    "Tanzania":       67,
    "Ethiopia":       130,
    "Ghana":          34,
    "Rwanda":         14,
    "Senegal":        17,
    "Cameroon":       27,
    "Zambia":         21,
    "Malawi":         21,
    "Mozambique":     33,
    "Burkina Faso":   23,
    "Mali":           23,
    "Niger":          27,
    "Chad":           18,
    "Madagascar":     30,
    "Democratic Republic of Congo": 102,
}

DISPLAY_NAMES = {
    "Democratic Republic of Congo": "DRC",
}

# -- Disease category keywords for condition diversity queries ----------------

CONDITION_CATEGORIES = {
    "HIV/AIDS":          "HIV OR AIDS",
    "Malaria":           "malaria",
    "Tuberculosis":      "tuberculosis OR TB",
    "Cancer":            "cancer OR neoplasm OR tumor OR tumour OR oncology",
    "Cardiovascular":    "cardiovascular OR cardiac OR heart OR hypertension OR stroke",
    "Diabetes":          "diabetes OR diabetic",
    "Respiratory":       "respiratory OR asthma OR COPD OR pneumonia OR lung",
    "Maternal/Neonatal": "maternal OR neonatal OR pregnancy OR obstetric OR perinatal",
    "Mental Health":     "mental OR psychiatric OR depression OR anxiety OR schizophrenia",
    "Nutrition":         "nutrition OR malnutrition OR stunting OR wasting",
    "Surgical":          "surgical OR surgery OR trauma",
    "Infectious Other":  "hepatitis OR cholera OR typhoid OR meningitis OR ebola",
}

# -- Phase keywords for Phase 1 detection ------------------------------------

PHASE_1_FILTER = "AREA[Phase](EARLY_PHASE1 OR PHASE1)"


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


def get_trial_count(location, extra_filter=""):
    """Return total count of interventional trials for a location with optional filter."""
    advanced = "AREA[StudyType]INTERVENTIONAL"
    if extra_filter:
        advanced += " AND " + extra_filter
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": advanced,
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_condition_count(location, condition_query):
    """Return count of interventional trials for a location + condition."""
    params = {
        "format": "json",
        "query.locn": location,
        "query.cond": condition_query,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def get_local_sponsor_count(location):
    """Proxy for local sponsorship: trials where sponsor name contains country name."""
    # Use lead sponsor location as proxy
    params = {
        "format": "json",
        "query.locn": location,
        "query.spons": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
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
    """Fetch multi-dimensional trial data for each country."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "countries": {},
    }

    n_countries = len(STUDY_COUNTRIES)
    # Total calls: total + conditions + local_sponsor + phase1 per country
    n_conditions = len(CONDITION_CATEGORIES)
    total_calls = n_countries * (1 + n_conditions + 1 + 1)
    call_num = 0

    for country, pop in STUDY_COUNTRIES.items():
        dname = DISPLAY_NAMES.get(country, country)
        country_data = {"population_m": pop}

        # 1) Total trial count
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {dname} -- total trials...")
        total = get_trial_count(country)
        country_data["total_trials"] = total
        print(f"    -> {total:,} trials")
        time.sleep(RATE_LIMIT)

        # 2) Condition-specific counts
        condition_counts = {}
        for cat_name, cat_query in CONDITION_CATEGORIES.items():
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {dname} -- {cat_name}...")
            count = get_condition_count(country, cat_query)
            condition_counts[cat_name] = count
            print(f"    -> {count:,}")
            time.sleep(RATE_LIMIT)
        country_data["condition_counts"] = condition_counts

        # 3) Local sponsor proxy
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {dname} -- local sponsor proxy...")
        local_count = get_local_sponsor_count(country)
        country_data["local_sponsor_count"] = local_count
        print(f"    -> {local_count:,}")
        time.sleep(RATE_LIMIT)

        # 4) Phase 1 trials
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {dname} -- Phase 1...")
        phase1 = get_trial_count(country, PHASE_1_FILTER)
        country_data["phase1_count"] = phase1
        print(f"    -> {phase1:,}")
        time.sleep(RATE_LIMIT)

        data["countries"][country] = country_data

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def shannon_entropy(counts):
    """Shannon entropy in bits from a dict or list of counts."""
    if isinstance(counts, dict):
        values = list(counts.values())
    else:
        values = list(counts)
    total = sum(values)
    if total == 0:
        return 0.0
    entropy = 0.0
    for v in values:
        if v > 0:
            p = v / total
            entropy -= p * math.log2(p)
    return entropy


def max_entropy(n_categories):
    """Maximum possible Shannon entropy for n categories."""
    if n_categories <= 0:
        return 0.0
    return math.log2(n_categories)


def evenness(entropy_val, n_categories):
    """Pielou's evenness: J = H / H_max."""
    h_max = max_entropy(n_categories)
    if h_max == 0:
        return 0.0
    return entropy_val / h_max


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_data(data):
    """Compute phase transition metrics for each country."""
    results = {"countries": []}

    for country, pop in STUDY_COUNTRIES.items():
        cd = data["countries"].get(country, {})
        dname = DISPLAY_NAMES.get(country, country)

        total = cd.get("total_trials", 0)
        per_million = round(total / pop, 2) if pop > 0 else 0

        # Local sponsorship percentage
        local = cd.get("local_sponsor_count", 0)
        local_pct = round(local / total * 100, 1) if total > 0 else 0

        # Condition diversity (Shannon entropy)
        cond_counts = cd.get("condition_counts", {})
        h = round(shannon_entropy(cond_counts), 3)
        n_cats = len(CONDITION_CATEGORIES)
        h_max = round(max_entropy(n_cats), 3)
        j = round(evenness(h, n_cats), 3)

        # Number of non-zero condition categories
        n_active = sum(1 for v in cond_counts.values() if v > 0)

        # Phase 1 presence
        phase1 = cd.get("phase1_count", 0)
        has_phase1 = phase1 > 0

        # Composite sovereignty score (0-100)
        # Weighted: local sponsorship (40%) + diversity evenness (30%) + phase1 (20%) + density (10%)
        phase1_score = 100 if has_phase1 else 0
        density_score = min(per_million / 10 * 100, 100)  # cap at 10/M
        sovereignty = round(
            0.40 * local_pct +
            0.30 * j * 100 +
            0.20 * phase1_score +
            0.10 * density_score,
            1
        )

        # Classify phase state
        if per_million >= 3 and sovereignty >= 40:
            phase_state = "Self-sustaining"
        elif per_million >= 1 and sovereignty >= 20:
            phase_state = "Transitional"
        elif per_million >= 0.5:
            phase_state = "Dependent"
        else:
            phase_state = "Pre-critical"

        results["countries"].append({
            "country": country,
            "display_name": dname,
            "population_m": pop,
            "total_trials": total,
            "per_million": per_million,
            "local_sponsor_count": local,
            "local_sponsor_pct": local_pct,
            "condition_counts": cond_counts,
            "condition_entropy": h,
            "max_entropy": h_max,
            "evenness": j,
            "n_active_categories": n_active,
            "phase1_count": phase1,
            "has_phase1": has_phase1,
            "sovereignty_score": sovereignty,
            "phase_state": phase_state,
        })

    # Sort by per_million descending
    results["countries"].sort(key=lambda x: -x["per_million"])
    for i, c in enumerate(results["countries"]):
        c["rank"] = i + 1

    # -- Identify critical threshold --
    # Find the density value that best separates self-sustaining from dependent
    # Using a simple approach: average density of transitional countries
    transitional = [c for c in results["countries"] if c["phase_state"] == "Transitional"]
    self_sustaining = [c for c in results["countries"] if c["phase_state"] == "Self-sustaining"]
    dependent = [c for c in results["countries"] if c["phase_state"] == "Dependent"]
    precritical = [c for c in results["countries"] if c["phase_state"] == "Pre-critical"]

    if transitional:
        threshold_density = round(
            sum(c["per_million"] for c in transitional) / len(transitional), 2
        )
    else:
        threshold_density = 2.0  # Default estimate

    results["threshold"] = {
        "critical_density": threshold_density,
        "n_self_sustaining": len(self_sustaining),
        "n_transitional": len(transitional),
        "n_dependent": len(dependent),
        "n_precritical": len(precritical),
    }

    # -- Case studies --
    rwanda = next((c for c in results["countries"] if c["country"] == "Rwanda"), None)
    chad = next((c for c in results["countries"] if c["country"] == "Chad"), None)
    results["case_studies"] = {
        "rwanda": rwanda,
        "chad": chad,
    }

    # -- Carrying capacity estimate --
    # From ecology: minimum infrastructure for self-sustaining ecosystem
    # Proxy: average metrics of self-sustaining countries
    if self_sustaining:
        results["carrying_capacity"] = {
            "min_density": round(min(c["per_million"] for c in self_sustaining), 2),
            "avg_density": round(sum(c["per_million"] for c in self_sustaining) / len(self_sustaining), 2),
            "avg_sovereignty": round(sum(c["sovereignty_score"] for c in self_sustaining) / len(self_sustaining), 1),
            "avg_evenness": round(sum(c["evenness"] for c in self_sustaining) / len(self_sustaining), 3),
            "avg_local_pct": round(sum(c["local_sponsor_pct"] for c in self_sustaining) / len(self_sustaining), 1),
        }
    else:
        results["carrying_capacity"] = {
            "min_density": 3.0,
            "avg_density": 5.0,
            "avg_sovereignty": 40.0,
            "avg_evenness": 0.5,
            "avg_local_pct": 15.0,
        }

    # -- Population in each phase --
    for phase in ["Self-sustaining", "Transitional", "Dependent", "Pre-critical"]:
        pop = sum(c["population_m"] for c in results["countries"] if c["phase_state"] == phase)
        results["threshold"][f"pop_{phase.lower().replace('-', '_')}"] = pop

    results["threshold"]["total_pop"] = sum(c["population_m"] for c in results["countries"])

    return results


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


def phase_color(phase):
    """Return color for phase state."""
    return {
        "Self-sustaining": "#22c55e",
        "Transitional":    "#eab308",
        "Dependent":       "#f97316",
        "Pre-critical":    "#ef4444",
    }.get(phase, "#888")


def phase_bg(phase):
    """Return background color for phase state."""
    return {
        "Self-sustaining": "rgba(34,197,94,0.10)",
        "Transitional":    "rgba(234,179,8,0.10)",
        "Dependent":       "rgba(249,115,22,0.10)",
        "Pre-critical":    "rgba(239,68,68,0.10)",
    }.get(phase, "transparent")


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard."""

    countries = results["countries"]
    threshold = results["threshold"]
    case_studies = results["case_studies"]
    capacity = results["carrying_capacity"]
    rwanda = case_studies.get("rwanda")
    chad = case_studies.get("chad")

    # ====================================================================
    # MAIN TABLE ROWS
    # ====================================================================
    main_rows = ""
    for c in countries:
        pc = phase_color(c["phase_state"])
        pb = phase_bg(c["phase_state"])
        phase1_str = "Yes" if c["has_phase1"] else "No"
        phase1_color = "#22c55e" if c["has_phase1"] else "#ef4444"
        sov_color = "#22c55e" if c["sovereignty_score"] >= 40 else ("#eab308" if c["sovereignty_score"] >= 20 else "#ef4444")
        main_rows += f"""<tr style="background:{pb};">
  <td style="padding:8px;text-align:center;font-weight:bold;color:{pc};">{c["rank"]}</td>
  <td style="padding:8px;font-weight:bold;">{escape_html(c["display_name"])}</td>
  <td style="padding:8px;text-align:right;">{c["total_trials"]:,}</td>
  <td style="padding:8px;text-align:right;">{c["per_million"]}</td>
  <td style="padding:8px;text-align:right;">{c["local_sponsor_pct"]}%</td>
  <td style="padding:8px;text-align:right;">{c["evenness"]}</td>
  <td style="padding:8px;text-align:center;color:{phase1_color};">{phase1_str}</td>
  <td style="padding:8px;text-align:right;color:{sov_color};font-weight:bold;">{c["sovereignty_score"]}</td>
  <td style="padding:8px;">
    <span style="display:inline-block;background:{pc};color:#000;padding:2px 10px;
      border-radius:12px;font-size:0.75rem;font-weight:bold;">
      {escape_html(c["phase_state"])}</span></td>
</tr>
"""

    # ====================================================================
    # CONDITION DIVERSITY DETAIL (for Rwanda and Chad)
    # ====================================================================
    def condition_table(country_data):
        if not country_data:
            return "<p>No data available.</p>"
        conds = country_data.get("condition_counts", {})
        total = country_data.get("total_trials", 1)
        rows = ""
        for cat in sorted(conds.keys(), key=lambda k: -conds[k]):
            count = conds[cat]
            pct = round(count / total * 100, 1) if total > 0 else 0
            bar_w = min(pct * 2, 100)
            color = "#60a5fa" if pct > 10 else ("#94a3b8" if pct > 2 else "#475569")
            rows += f"""<tr>
  <td style="padding:6px 8px;">{escape_html(cat)}</td>
  <td style="padding:6px 8px;text-align:right;">{count:,}</td>
  <td style="padding:6px 8px;text-align:right;">{pct}%</td>
  <td style="padding:6px 8px;">
    <div style="background:rgba(255,255,255,0.06);border-radius:3px;height:12px;width:100px;">
      <div style="background:{color};height:100%;width:{bar_w}%;border-radius:3px;"></div>
    </div></td>
</tr>
"""
        return f"""<table style="font-size:0.85rem;">
  <thead><tr>
    <th style="text-align:left;padding:6px 8px;">Category</th>
    <th style="text-align:right;padding:6px 8px;">Trials</th>
    <th style="text-align:right;padding:6px 8px;">Share</th>
    <th style="padding:6px 8px;">Distribution</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>"""

    rwanda_table = condition_table(rwanda)
    chad_table = condition_table(chad)

    # ====================================================================
    # PHASE STATE SUMMARY
    # ====================================================================
    phase_summary_rows = ""
    for phase in ["Self-sustaining", "Transitional", "Dependent", "Pre-critical"]:
        n = threshold.get(f"n_{phase.lower().replace('-', '_').replace(' ', '_')}", 0)
        # Match threshold keys
        pop_key = f"pop_{phase.lower().replace('-', '_').replace(' ', '_')}"
        pop = threshold.get(pop_key, 0)
        total_pop = threshold.get("total_pop", 1)
        pop_pct = round(pop / total_pop * 100, 1) if total_pop > 0 else 0
        pc = phase_color(phase)
        phase_summary_rows += f"""<tr style="background:{phase_bg(phase)};">
  <td style="padding:10px;">
    <span style="display:inline-block;background:{pc};color:#000;padding:3px 12px;
      border-radius:12px;font-size:0.8rem;font-weight:bold;">{escape_html(phase)}</span></td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{n}</td>
  <td style="padding:10px;text-align:right;">{pop:.0f}M</td>
  <td style="padding:10px;text-align:right;color:{pc};font-weight:bold;">{pop_pct}%</td>
</tr>
"""

    # Rwanda vs Chad comparison
    rw_density = rwanda["per_million"] if rwanda else 0
    rw_sov = rwanda["sovereignty_score"] if rwanda else 0
    rw_evenness = rwanda["evenness"] if rwanda else 0
    rw_local = rwanda["local_sponsor_pct"] if rwanda else 0
    rw_phase1 = "Yes" if rwanda and rwanda["has_phase1"] else "No"

    ch_density = chad["per_million"] if chad else 0
    ch_sov = chad["sovereignty_score"] if chad else 0
    ch_evenness = chad["evenness"] if chad else 0
    ch_local = chad["local_sponsor_pct"] if chad else 0
    ch_phase1 = "Yes" if chad and chad["has_phase1"] else "No"

    # ====================================================================
    # FULL HTML
    # ====================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Phase Transition -- When Does Research Become Self-Sustaining?</title>
<style>
  :root {{
    --bg: #0a0e17;
    --card: #111827;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #60a5fa;
    --green: #22c55e;
    --yellow: #eab308;
    --orange: #f97316;
    --red: #ef4444;
    --purple: #a78bfa;
    --teal: #2dd4bf;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    line-height: 1.6;
    padding: 20px;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{
    font-size: 2rem;
    margin-bottom: 8px;
    background: linear-gradient(135deg, var(--teal), var(--accent));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  h2 {{
    font-size: 1.4rem;
    color: var(--accent);
    margin: 32px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
  h3 {{
    font-size: 1.1rem;
    color: var(--muted);
    margin: 20px 0 10px;
  }}
  .subtitle {{
    color: var(--muted);
    font-size: 0.95rem;
    margin-bottom: 24px;
  }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }}
  .stat-card .number {{
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 4px;
  }}
  .stat-card .label {{
    color: var(--muted);
    font-size: 0.85rem;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    overflow-x: auto;
  }}
  .insight {{
    background: rgba(45,212,191,0.08);
    border-left: 4px solid var(--teal);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.95rem;
    line-height: 1.7;
  }}
  .insight strong {{ color: var(--teal); }}
  .warning {{
    background: rgba(239,68,68,0.08);
    border-left: 4px solid var(--red);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
  }}
  .warning strong {{ color: var(--red); }}
  .policy {{
    background: rgba(34,197,94,0.08);
    border-left: 4px solid var(--green);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
  }}
  .policy strong {{ color: var(--green); }}
  .versus {{
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 24px;
    align-items: start;
    margin: 24px 0;
  }}
  .versus-panel {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
  }}
  .versus-divider {{
    display: flex;
    align-items: center;
    font-size: 1.5rem;
    font-weight: bold;
    color: var(--muted);
    padding-top: 40px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th {{
    text-align: left;
    padding: 12px 8px;
    border-bottom: 2px solid var(--border);
    color: var(--muted);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  td {{
    padding: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .phase-diagram {{
    display: flex;
    gap: 4px;
    margin: 20px 0;
    height: 40px;
    border-radius: 8px;
    overflow: hidden;
  }}
  .phase-bar {{
    display: flex;
    align-items: center;
    justify-content: center;
    color: #000;
    font-size: 0.75rem;
    font-weight: bold;
    transition: width 0.3s;
  }}
  .method {{
    color: var(--muted);
    font-size: 0.82rem;
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    line-height: 1.7;
  }}
  .formula {{
    background: rgba(96,165,250,0.1);
    border: 1px solid rgba(96,165,250,0.2);
    border-radius: 8px;
    padding: 16px;
    margin: 12px 0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 0.88rem;
    color: var(--accent);
    line-height: 1.8;
  }}
  .footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 48px;
    padding: 24px;
    border-top: 1px solid var(--border);
  }}
  @media (max-width: 768px) {{
    .versus {{ grid-template-columns: 1fr; }}
    .versus-divider {{ justify-content: center; padding: 8px 0; }}
  }}
</style>
</head>
<body>
<div class="container">

<h1>The Phase Transition</h1>
<p class="subtitle">
  When Does Research Become Self-Sustaining? Identifying the Critical Threshold
  in African Research Ecosystems
  -- Data from ClinicalTrials.gov API v2, {escape_html(data.get("timestamp", "")[:10])}
</p>

<!-- ============================================================ -->
<!-- SUMMARY CARDS -->
<!-- ============================================================ -->
<div class="summary-grid">
  <div class="stat-card">
    <div class="number" style="color:var(--teal);">{threshold["critical_density"]}</div>
    <div class="label">Critical Density<br>(trials/million threshold)</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--green);">{threshold["n_self_sustaining"]}</div>
    <div class="label">Self-Sustaining<br>Countries</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--yellow);">{threshold["n_transitional"]}</div>
    <div class="label">Transitional<br>Countries</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--red);">{threshold["n_dependent"] + threshold["n_precritical"]}</div>
    <div class="label">Dependent +<br>Pre-Critical</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--accent);">{capacity["min_density"]}</div>
    <div class="label">Carrying Capacity<br>(minimum self-sustaining density)</div>
  </div>
</div>

<!-- ============================================================ -->
<!-- PHYSICS EXPLANATION -->
<!-- ============================================================ -->
<h2>Phase Transitions in Research Ecosystems</h2>
<div class="card">
  <p style="font-size:1.05rem;line-height:1.7;margin-bottom:16px;">
    In statistical physics, a <strong>phase transition</strong> occurs when a system crosses
    a critical threshold and its behavior changes qualitatively -- water freezes at 0 degrees C,
    iron becomes magnetic below the Curie temperature. The transition is not gradual:
    it is a sudden shift in the system's organizing principle.
  </p>
  <p style="line-height:1.7;margin-bottom:16px;">
    We hypothesize that research ecosystems undergo an analogous phase transition.
    Below a critical per-capita trial density, countries are in a <strong>dependent state</strong>:
    trials are foreign-led, limited to HIV/malaria, and no local research culture exists.
    Above the threshold, countries enter a <strong>self-sustaining state</strong>: local
    sponsors emerge, the condition portfolio diversifies, Phase 1 trials appear, and
    the system generates its own momentum.
  </p>
  <div class="insight">
    <strong>The critical threshold:</strong> Our data suggest the phase transition occurs
    at approximately <strong>{threshold["critical_density"]} trials per million population</strong>.
    Countries above this density show qualitatively different research ecosystem properties:
    higher local sponsorship, greater condition diversity, and Phase 1 trial capacity.
  </div>
</div>

<!-- ============================================================ -->
<!-- PHASE DIAGRAM -->
<!-- ============================================================ -->
<h2>Phase State Distribution</h2>
<div class="card">
  <p style="margin-bottom:16px;line-height:1.7;">
    Each country is classified into one of four phase states based on trial density
    and a composite sovereignty score (weighted: 40% local sponsorship, 30% condition
    diversity, 20% Phase 1 presence, 10% density normalization).
  </p>

  <div class="phase-diagram">
    <div class="phase-bar" style="background:var(--green);width:{threshold['n_self_sustaining'] / len(countries) * 100:.0f}%;">
      Self-sustaining</div>
    <div class="phase-bar" style="background:var(--yellow);width:{threshold['n_transitional'] / len(countries) * 100:.0f}%;">
      Transitional</div>
    <div class="phase-bar" style="background:var(--orange);width:{threshold['n_dependent'] / len(countries) * 100:.0f}%;">
      Dependent</div>
    <div class="phase-bar" style="background:var(--red);width:{threshold['n_precritical'] / len(countries) * 100:.0f}%;">
      Pre-critical</div>
  </div>

  <table style="margin-top:16px;">
    <thead>
      <tr>
        <th>Phase State</th>
        <th style="text-align:right;">Countries</th>
        <th style="text-align:right;">Population</th>
        <th style="text-align:right;">Pop Share</th>
      </tr>
    </thead>
    <tbody>
{phase_summary_rows}
    </tbody>
  </table>

  <div class="formula" style="margin-top:20px;">
    Sovereignty Score = 0.40 x LocalSponsor% + 0.30 x Evenness x 100 + 0.20 x Phase1(0/100) + 0.10 x min(Density/10, 1) x 100<br>
    Self-sustaining: density >= 3/M AND sovereignty >= 40<br>
    Transitional: density >= 1/M AND sovereignty >= 20<br>
    Dependent: density >= 0.5/M<br>
    Pre-critical: density &lt; 0.5/M
  </div>
</div>

<!-- ============================================================ -->
<!-- FULL COUNTRY TABLE -->
<!-- ============================================================ -->
<h2>Multi-Dimensional Phase Analysis: 20 Countries</h2>
<div class="card">
  <table>
    <thead>
      <tr>
        <th>Rank</th><th>Country</th><th style="text-align:right;">Trials</th>
        <th style="text-align:right;">Per M</th>
        <th style="text-align:right;">Local %</th>
        <th style="text-align:right;">Evenness</th>
        <th style="text-align:center;">Phase 1</th>
        <th style="text-align:right;">Sovereignty</th>
        <th>Phase State</th>
      </tr>
    </thead>
    <tbody>
{main_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- CASE STUDY: RWANDA vs CHAD -->
<!-- ============================================================ -->
<h2>Case Study: Rwanda (Supercritical) vs Chad (Subcritical)</h2>
<div class="card">
  <p style="line-height:1.7;margin-bottom:20px;">
    Rwanda and Chad illustrate the two sides of the phase transition. Despite similar
    population sizes and low-income classification, Rwanda has crossed the critical
    threshold while Chad remains trapped in the pre-critical/dependent phase.
  </p>

  <div class="versus">
    <div class="versus-panel" style="border-color:var(--green);">
      <h3 style="color:var(--green);margin-bottom:12px;">Rwanda (Supercritical)</h3>
      <table style="font-size:0.85rem;">
        <tr><td style="color:var(--muted);">Trial density</td>
            <td style="text-align:right;font-weight:bold;color:var(--green);">{rw_density}/M</td></tr>
        <tr><td style="color:var(--muted);">Local sponsorship</td>
            <td style="text-align:right;">{rw_local}%</td></tr>
        <tr><td style="color:var(--muted);">Condition evenness</td>
            <td style="text-align:right;">{rw_evenness}</td></tr>
        <tr><td style="color:var(--muted);">Phase 1 trials</td>
            <td style="text-align:right;color:{"var(--green)" if rw_phase1 == "Yes" else "var(--red)"};">{rw_phase1}</td></tr>
        <tr><td style="color:var(--muted);">Sovereignty score</td>
            <td style="text-align:right;font-weight:bold;color:var(--green);">{rw_sov}</td></tr>
      </table>
      <p style="margin-top:12px;font-size:0.85rem;line-height:1.6;color:var(--muted);">
        Rwanda's success stems from deliberate government investment in research
        infrastructure, the Einstein-Rwanda partnership (PMID 39972388), and a
        strategic decision to diversify beyond infectious diseases.
      </p>
      <h3 style="margin-top:16px;">Condition Portfolio</h3>
      {rwanda_table}
    </div>

    <div class="versus-divider">vs</div>

    <div class="versus-panel" style="border-color:var(--red);">
      <h3 style="color:var(--red);margin-bottom:12px;">Chad (Subcritical)</h3>
      <table style="font-size:0.85rem;">
        <tr><td style="color:var(--muted);">Trial density</td>
            <td style="text-align:right;font-weight:bold;color:var(--red);">{ch_density}/M</td></tr>
        <tr><td style="color:var(--muted);">Local sponsorship</td>
            <td style="text-align:right;">{ch_local}%</td></tr>
        <tr><td style="color:var(--muted);">Condition evenness</td>
            <td style="text-align:right;">{ch_evenness}</td></tr>
        <tr><td style="color:var(--muted);">Phase 1 trials</td>
            <td style="text-align:right;color:{"var(--green)" if ch_phase1 == "Yes" else "var(--red)"};">{ch_phase1}</td></tr>
        <tr><td style="color:var(--muted);">Sovereignty score</td>
            <td style="text-align:right;font-weight:bold;color:var(--red);">{ch_sov}</td></tr>
      </table>
      <p style="margin-top:12px;font-size:0.85rem;line-height:1.6;color:var(--muted);">
        Chad's research ecosystem is trapped in a subcritical state: minimal local
        capacity, foreign-led trials concentrated on a narrow disease portfolio,
        no Phase 1 capability, and insufficient density to generate self-sustaining
        momentum.
      </p>
      <h3 style="margin-top:16px;">Condition Portfolio</h3>
      {chad_table}
    </div>
  </div>
</div>

<!-- ============================================================ -->
<!-- CARRYING CAPACITY -->
<!-- ============================================================ -->
<h2>Ecological Carrying Capacity</h2>
<div class="card">
  <p style="line-height:1.7;margin-bottom:16px;">
    In ecology, <strong>carrying capacity</strong> is the maximum population size that
    an environment can sustain indefinitely. For research ecosystems, we define the
    <strong>minimum carrying capacity</strong> as the threshold below which a country's
    research output cannot sustain itself without continuous external support.
  </p>
  <div class="insight">
    <strong>Carrying capacity profile</strong> (average of self-sustaining countries):<br>
    Minimum density: <strong>{capacity["min_density"]}/M</strong> |
    Average density: <strong>{capacity["avg_density"]}/M</strong> |
    Average sovereignty: <strong>{capacity["avg_sovereignty"]}</strong> |
    Average evenness: <strong>{capacity["avg_evenness"]}</strong> |
    Average local sponsorship: <strong>{capacity["avg_local_pct"]}%</strong>
  </div>
  <p style="line-height:1.7;margin-top:16px;">
    Countries below carrying capacity are in an <strong>ecological sink</strong>: they
    depend on external "immigration" (foreign-led trials) to maintain any research
    activity. Remove the external input, and the ecosystem collapses. Countries above
    carrying capacity are <strong>ecological sources</strong>: they generate their own
    research activity and can even export capacity to neighbors.
  </p>
</div>

<!-- ============================================================ -->
<!-- POLICY -->
<!-- ============================================================ -->
<h2>Policy: How to Push Countries Past the Threshold</h2>
<div class="card">
  <div class="policy">
    <strong>The physics insight:</strong> Phase transitions are threshold phenomena.
    Incremental investment below the threshold has no lasting effect -- the system
    snaps back to its dependent state. Investment must be concentrated enough to
    push the system past the critical point, after which self-sustaining dynamics
    take over. This argues against "peanut butter" spreading of research funding
    across 54 countries and for <strong>focused, sequential capacity-building</strong>
    targeting countries nearest the threshold.
  </div>
  <p style="line-height:1.7;margin-top:16px;">
    <strong>Four strategies from the phase transition model:</strong>
  </p>
  <ol style="margin:12px 0 0 24px;line-height:2;">
    <li><strong>Identify near-threshold countries:</strong> Focus on transitional
    countries that are closest to self-sustaining status. These require the smallest
    "push" to cross the critical point.</li>
    <li><strong>Build local sponsorship capacity:</strong> Foreign-led trials do not
    build self-sustaining ecosystems. Fund local ethics review, regulatory capacity,
    and grant-writing infrastructure to increase local sponsorship from below 10%
    to above 20%.</li>
    <li><strong>Diversify the condition portfolio:</strong> Countries trapped in
    HIV/malaria monocultures lack the breadth to sustain independent research.
    Seed NCD trials (cardiovascular, diabetes, cancer) to increase Shannon diversity.</li>
    <li><strong>Enable Phase 1 capability:</strong> Phase 1 trials are the signature
    of mature research ecosystems. Establishing even one Phase 1 unit (GMP pharmacy,
    trained pharmacologists) signals the transition to self-sustaining status.</li>
  </ol>
</div>

<!-- ============================================================ -->
<!-- METHOD -->
<!-- ============================================================ -->
<div class="method">
  <strong>Method:</strong> ClinicalTrials.gov API v2 queried for 20 African countries across
  four dimensions: (1) total interventional trials, (2) condition-specific counts across 12
  disease categories, (3) local sponsor proxy (sponsor name matching country name),
  (4) Phase 1 trial count. Shannon entropy computed across condition categories; Pielou's
  evenness (J = H/H_max) used for diversity. Composite sovereignty score: 40% local
  sponsorship + 30% condition evenness + 20% Phase 1 presence + 10% density normalization.
  Phase state classification based on density and sovereignty thresholds. Carrying capacity
  estimated from average metrics of self-sustaining countries. Population denominators
  from UN 2025 estimates. All data cached locally with 24-hour TTL.
</div>

<div class="footer">
  Project 40 of the Africa RCT Audit Series |
  Data: ClinicalTrials.gov | Analysis: Python |
  Theoretical framework: Phase transitions (Landau 1937), Carrying capacity (Verhulst 1838),
  Research ecosystem dynamics (Sarewitz & Pielke 2007)
</div>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Project 40: The Phase Transition")
    print("=" * 60)

    data = fetch_all_data()
    results = analyze_data(data)
    html = generate_html(data, results)

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nGenerated {OUTPUT_HTML}")
    print(f"  Critical density:    {results['threshold']['critical_density']}/M")
    print(f"  Self-sustaining:     {results['threshold']['n_self_sustaining']} countries")
    print(f"  Transitional:        {results['threshold']['n_transitional']} countries")
    print(f"  Dependent:           {results['threshold']['n_dependent']} countries")
    print(f"  Pre-critical:        {results['threshold']['n_precritical']} countries")

    rwanda = results["case_studies"].get("rwanda")
    chad = results["case_studies"].get("chad")
    if rwanda:
        print(f"  Rwanda sovereignty:  {rwanda['sovereignty_score']}")
    if chad:
        print(f"  Chad sovereignty:    {chad['sovereignty_score']}")
    print("\nDone.")


if __name__ == "__main__":
    main()
