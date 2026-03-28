#!/usr/bin/env python
"""
fetch_nigeria_paradox.py — The Nigeria Paradox: Deep-Dive Analysis
==================================================================
Nigeria is Africa's most populous country (230M) and largest economy
($500B GDP), yet has only ~354 interventional trials (1.5/million) —
worse than India, Indonesia, Bangladesh, and every Latin American
country. This is the single most extreme large-country trial deficit
in the world.

Usage:
    python fetch_nigeria_paradox.py

Outputs:
    data/nigeria_paradox_data.json  (cached API results, 24h TTL)
    nigeria-paradox.html            (dark-theme interactive dashboard)

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
CACHE_FILE = DATA_DIR / "nigeria_paradox_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "nigeria-paradox.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# -- Nigeria reference data ---------------------------------------------------

NIGERIA_POPULATION = 230     # millions (2025 est.)
NIGERIA_GDP_NOMINAL = 500    # billion USD
NIGERIA_GDP_PER_CAPITA = 2170  # USD
NIGERIA_SCD_BIRTHS_PER_YEAR = 150_000  # ~150K SCD births/year (highest globally)

# -- Conditions to query for Nigeria ------------------------------------------

NIGERIA_CONDITIONS = {
    "HIV":              "HIV",
    "Malaria":          "Malaria",
    "Cancer":           "Cancer",
    "Diabetes":         "Diabetes",
    "Hypertension":     "Hypertension",
    "Cardiovascular":   "Cardiovascular",
    "Stroke":           "Stroke",
    "Sickle Cell":      "Sickle Cell",
    "Maternal":         "Maternal",
    "Neonatal":         "Neonatal",
    "Mental Health":    "Mental Health",
    "Tuberculosis":     "Tuberculosis",
}

# -- Phases to query ----------------------------------------------------------

PHASES = ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]

# -- Status categories to query -----------------------------------------------

STATUS_CATEGORIES = ["COMPLETED", "TERMINATED", "WITHDRAWN", "UNKNOWN", "RECRUITING"]

# -- Comparator large developing countries ------------------------------------

COMPARATOR_COUNTRIES = {
    "India":       {"pop": 1430,  "trials": 5388},
    "Indonesia":   {"pop": 280,   "trials": 1299},
    "Bangladesh":  {"pop": 173,   "trials": 649},
    "Pakistan":    {"pop": 240,   "trials": 4641},
    "Ethiopia":    {"pop": 130,   "trials": 240},
    "Brazil":      {"pop": 216,   "trials": 9890},
}


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


def get_trial_count(location, condition=None, phase=None, status=None):
    """Return total count of interventional trials for a location + filters."""
    filter_parts = ["AREA[StudyType]INTERVENTIONAL"]
    if phase:
        filter_parts.append(f"AREA[Phase]{phase}")
    if status:
        filter_parts.append(f"AREA[OverallStatus]{status}")

    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": " AND ".join(filter_parts),
        "pageSize": 1,
        "countTotal": "true",
    }
    if condition:
        params["query.cond"] = condition

    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


def fetch_trial_page(location, page_token=None, page_size=200):
    """Fetch a page of trial-level data for sponsor analysis."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": page_size,
        "countTotal": "true",
        "fields": "NCTId,BriefTitle,LeadSponsorName,LeadSponsorClass,"
                  "Phase,OverallStatus,LocationCity,LocationCountry,"
                  "StartDate,CompletionDate,EnrollmentCount",
    }
    if page_token:
        params["pageToken"] = page_token

    return api_get(params)


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
    """Fetch all Nigeria deep-dive data + comparator counts."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "nigeria_total": 0,
        "nigeria_conditions": {},
        "nigeria_phases": {},
        "nigeria_status": {},
        "nigeria_trials": [],
        "comparator_counts": {},
    }

    call_num = 0
    total_calls = (
        1                              # total count
        + len(NIGERIA_CONDITIONS)      # condition breakdown
        + len(PHASES)                  # phase breakdown
        + len(STATUS_CATEGORIES)       # status breakdown
        + 3                            # estimated pages for trial-level data
        + len(COMPARATOR_COUNTRIES)    # comparator verification
    )

    # --- 1. Nigeria total count ---
    call_num += 1
    print(f"\n[{call_num}/{total_calls}] Nigeria total interventional trials...")
    data["nigeria_total"] = get_trial_count("Nigeria")
    print(f"  -> {data['nigeria_total']:,} trials")
    time.sleep(RATE_LIMIT)

    # --- 2. Nigeria condition breakdown ---
    print("\n--- Nigeria condition breakdown ---")
    for label, cond in NIGERIA_CONDITIONS.items():
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {label}...")
        count = get_trial_count("Nigeria", condition=cond)
        data["nigeria_conditions"][label] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    # --- 3. Nigeria phase breakdown ---
    print("\n--- Nigeria phase breakdown ---")
    for phase in PHASES:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {phase}...")
        count = get_trial_count("Nigeria", phase=phase)
        data["nigeria_phases"][phase] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    # --- 4. Nigeria status breakdown ---
    print("\n--- Nigeria status breakdown ---")
    for status in STATUS_CATEGORIES:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {status}...")
        count = get_trial_count("Nigeria", status=status)
        data["nigeria_status"][status] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    # --- 5. Fetch trial-level data (paginated) for sponsor analysis ---
    print("\n--- Fetching trial-level data ---")
    page_token = None
    page_num = 0
    while True:
        page_num += 1
        call_num += 1
        print(f"  [{call_num}/{total_calls}] Page {page_num}...")
        resp = fetch_trial_page("Nigeria", page_token=page_token)
        if resp is None:
            break

        studies = resp.get("studies", [])
        for study in studies:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            design_mod = proto.get("designModule", {})
            locs_mod = proto.get("contactsLocationsModule", {})

            lead_sponsor = sponsor_mod.get("leadSponsor", {})

            # Extract cities for geographic analysis
            cities = []
            for loc in locs_mod.get("locations", []):
                if loc.get("country", "") == "Nigeria":
                    city = loc.get("city", "Unknown")
                    cities.append(city)

            enrollment = None
            enroll_info = design_mod.get("enrollmentInfo", {})
            if isinstance(enroll_info, dict):
                enrollment = enroll_info.get("count")

            trial_info = {
                "nctId": ident.get("nctId", ""),
                "title": ident.get("briefTitle", ""),
                "sponsor": lead_sponsor.get("name", "Unknown"),
                "sponsorClass": lead_sponsor.get("class", "UNKNOWN"),
                "phase": (design_mod.get("phases") or ["N/A"])[0]
                          if design_mod.get("phases") else "N/A",
                "status": status_mod.get("overallStatus", "UNKNOWN"),
                "cities": cities,
                "enrollment": enrollment,
            }
            data["nigeria_trials"].append(trial_info)

        # Check for next page
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        time.sleep(RATE_LIMIT)

    print(f"  -> {len(data['nigeria_trials']):,} trial records fetched")

    # --- 6. Comparator country counts ---
    print("\n--- Comparator country counts ---")
    for country in COMPARATOR_COUNTRIES:
        call_num += 1
        print(f"  [{call_num}/{total_calls}] {country}...")
        count = get_trial_count(country)
        data["comparator_counts"][country] = count
        print(f"    -> {count:,} trials")
        time.sleep(RATE_LIMIT)

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_data(data):
    """Compute all metrics for the Nigeria Paradox analysis."""
    results = {}

    nigeria_total = data["nigeria_total"]
    nigeria_per_m = round(nigeria_total / NIGERIA_POPULATION, 2) if NIGERIA_POPULATION > 0 else 0

    results["nigeria_total"] = nigeria_total
    results["nigeria_per_million"] = nigeria_per_m
    results["nigeria_population"] = NIGERIA_POPULATION
    results["nigeria_gdp"] = NIGERIA_GDP_NOMINAL

    # -- 1. Comparator analysis with gap ratios --
    comparator_analysis = []
    for country, ref in COMPARATOR_COUNTRIES.items():
        pop = ref["pop"]
        # Use live data if available, else reference count
        trials = data["comparator_counts"].get(country, ref["trials"])
        per_m = round(trials / pop, 2) if pop > 0 else 0
        gap_ratio = round(per_m / nigeria_per_m, 1) if nigeria_per_m > 0 else 0
        comparator_analysis.append({
            "country": country,
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
            "gap_ratio": gap_ratio,
        })
    comparator_analysis.sort(key=lambda x: -x["gap_ratio"])
    results["comparator_analysis"] = comparator_analysis

    # -- 2. Condition breakdown --
    condition_breakdown = []
    for label, count in data["nigeria_conditions"].items():
        pct = round(count / nigeria_total * 100, 1) if nigeria_total > 0 else 0
        condition_breakdown.append({
            "condition": label,
            "trials": count,
            "pct_of_total": pct,
        })
    condition_breakdown.sort(key=lambda x: -x["trials"])
    results["condition_breakdown"] = condition_breakdown

    # -- 3. SCD paradox --
    scd_trials = data["nigeria_conditions"].get("Sickle Cell", 0)
    results["scd_paradox"] = {
        "scd_trials_nigeria": scd_trials,
        "scd_births_per_year": NIGERIA_SCD_BIRTHS_PER_YEAR,
        "trials_per_10k_births": round(scd_trials / (NIGERIA_SCD_BIRTHS_PER_YEAR / 10_000), 2)
                                 if NIGERIA_SCD_BIRTHS_PER_YEAR > 0 else 0,
        "burden_note": "Nigeria accounts for ~30% of global SCD births",
    }

    # -- 4. Sponsor analysis from trial-level data --
    sponsor_classes = defaultdict(int)
    sponsor_names = defaultdict(int)
    nigerian_keywords = [
        "nigeria", "lagos", "ibadan", "abuja", "ile-ife", "enugu",
        "university of", "national hospital", "nigerian", "unilag",
        "obafemi", "ahmadu bello", "bayero", "nnamdi azikiwe",
        "university college hospital", "federal medical",
    ]
    local_trials = 0
    foreign_trials = 0

    for trial in data.get("nigeria_trials", []):
        cls = trial.get("sponsorClass", "UNKNOWN")
        sponsor_classes[cls] += 1
        sponsor_names[trial.get("sponsor", "Unknown")] += 1

        # Classify as local vs foreign
        sponsor_lower = trial.get("sponsor", "").lower()
        is_local = any(kw in sponsor_lower for kw in nigerian_keywords)
        if is_local:
            local_trials += 1
        else:
            foreign_trials += 1

    results["sponsor_classes"] = dict(sponsor_classes)
    results["top_sponsors"] = sorted(
        sponsor_names.items(), key=lambda x: -x[1]
    )[:20]
    results["local_vs_foreign"] = {
        "local": local_trials,
        "foreign": foreign_trials,
        "local_pct": round(local_trials / max(local_trials + foreign_trials, 1) * 100, 1),
    }

    # -- 5. Phase distribution --
    results["phase_distribution"] = dict(data["nigeria_phases"])

    # -- 6. Status analysis --
    status_data = dict(data["nigeria_status"])
    total_status = sum(status_data.values())
    completed = status_data.get("COMPLETED", 0)
    terminated = status_data.get("TERMINATED", 0)
    withdrawn = status_data.get("WITHDRAWN", 0)
    unknown = status_data.get("UNKNOWN", 0)
    recruiting = status_data.get("RECRUITING", 0)

    results["status_analysis"] = {
        "raw": status_data,
        "completion_rate": round(completed / max(total_status, 1) * 100, 1),
        "abandonment_rate": round((terminated + withdrawn) / max(total_status, 1) * 100, 1),
        "unknown_rate": round(unknown / max(total_status, 1) * 100, 1),
    }

    # -- 7. Geographic concentration (Lagos vs rest) --
    city_counts = defaultdict(int)
    for trial in data.get("nigeria_trials", []):
        for city in trial.get("cities", []):
            city_counts[city] += 1

    total_city = sum(city_counts.values())
    city_sorted = sorted(city_counts.items(), key=lambda x: -x[1])
    lagos_count = city_counts.get("Lagos", 0)
    ibadan_count = city_counts.get("Ibadan", 0)

    results["geographic_concentration"] = {
        "city_breakdown": city_sorted[:15],
        "lagos_count": lagos_count,
        "lagos_pct": round(lagos_count / max(total_city, 1) * 100, 1),
        "top3_pct": round(
            sum(c for _, c in city_sorted[:3]) / max(total_city, 1) * 100, 1
        ),
        "total_sites": total_city,
    }

    # -- 8. What would it take? (target trials at comparator rates) --
    targets = []
    for comp in comparator_analysis:
        needed = round(comp["per_million"] * NIGERIA_POPULATION)
        gap = needed - nigeria_total
        targets.append({
            "benchmark": comp["country"],
            "benchmark_rate": comp["per_million"],
            "needed_trials": needed,
            "gap": max(gap, 0),
        })
    results["what_would_it_take"] = targets

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


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard for The Nigeria Paradox."""

    nigeria_total = results["nigeria_total"]
    nigeria_per_m = results["nigeria_per_million"]
    comparators = results["comparator_analysis"]
    conditions = results["condition_breakdown"]
    scd = results["scd_paradox"]
    sponsors = results["top_sponsors"]
    sponsor_classes = results["sponsor_classes"]
    local_foreign = results["local_vs_foreign"]
    phases = results["phase_distribution"]
    status = results["status_analysis"]
    geo = results["geographic_concentration"]
    targets = results["what_would_it_take"]

    ts = data.get("timestamp", "unknown")

    # -- Comparator table rows --
    comp_rows = ""
    for c in comparators:
        gap_color = "#ef4444" if c["gap_ratio"] >= 5 else (
            "#f97316" if c["gap_ratio"] >= 2 else "#eab308"
        )
        comp_rows += f"""<tr>
  <td style="padding:10px 14px;font-weight:bold;">{escape_html(c["country"])}</td>
  <td style="padding:10px 14px;text-align:right;">{c["population_m"]:,}M</td>
  <td style="padding:10px 14px;text-align:right;">{c["trials"]:,}</td>
  <td style="padding:10px 14px;text-align:right;color:#60a5fa;font-weight:bold;">
    {c["per_million"]}</td>
  <td style="padding:10px 14px;text-align:right;color:{gap_color};font-weight:bold;font-size:1.1rem;">
    {c["gap_ratio"]}x</td>
</tr>
"""

    # -- Nigeria row for the comparator table --
    nigeria_row = f"""<tr style="background:rgba(239,68,68,0.15);border:2px solid #ef4444;">
  <td style="padding:10px 14px;font-weight:bold;color:#ef4444;">Nigeria</td>
  <td style="padding:10px 14px;text-align:right;">{NIGERIA_POPULATION}M</td>
  <td style="padding:10px 14px;text-align:right;color:#ef4444;font-weight:bold;">{nigeria_total:,}</td>
  <td style="padding:10px 14px;text-align:right;color:#ef4444;font-weight:bold;">
    {nigeria_per_m}</td>
  <td style="padding:10px 14px;text-align:center;color:#ef4444;font-weight:bold;">
    Baseline</td>
</tr>
"""

    # -- Condition bars --
    max_cond = max((c["trials"] for c in conditions), default=1)
    if max_cond < 1:
        max_cond = 1
    cond_bars = ""
    cond_colors = [
        "#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#8b5cf6",
        "#ec4899", "#14b8a6", "#f59e0b", "#6366f1", "#84cc16", "#e11d48",
    ]
    for i, c in enumerate(conditions):
        bar_w = max(c["trials"] / max_cond * 100, 2)
        color = cond_colors[i % len(cond_colors)]
        is_scd = " (WORLD HIGHEST BURDEN)" if c["condition"] == "Sickle Cell" else ""
        scd_style = "border:2px solid #ef4444;border-radius:8px;padding:4px;" if c["condition"] == "Sickle Cell" else ""
        cond_bars += f"""<div style="margin-bottom:8px;{scd_style}">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
    <span style="color:#e2e8f0;font-weight:500;">{escape_html(c["condition"])}{escape_html(is_scd)}</span>
    <span style="color:{color};font-weight:bold;">{c["trials"]:,} ({c["pct_of_total"]}%)</span>
  </div>
  <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:22px;">
    <div style="background:{color};height:100%;width:{bar_w:.1f}%;border-radius:4px;
      transition:width 0.5s;"></div>
  </div>
</div>
"""

    # -- Sponsor class breakdown --
    sponsor_class_html = ""
    class_colors = {
        "INDUSTRY": "#f97316",
        "NIH": "#22c55e",
        "OTHER_GOV": "#06b6d4",
        "NETWORK": "#8b5cf6",
        "OTHER": "#94a3b8",
        "UNKNOWN": "#64748b",
        "FED": "#eab308",
        "INDIV": "#ec4899",
    }
    total_sponsors = sum(sponsor_classes.values())
    for cls, count in sorted(sponsor_classes.items(), key=lambda x: -x[1]):
        pct = round(count / max(total_sponsors, 1) * 100, 1)
        color = class_colors.get(cls, "#94a3b8")
        bar_w = max(pct, 2)
        sponsor_class_html += f"""<div style="margin-bottom:6px;">
  <div style="display:flex;justify-content:space-between;margin-bottom:2px;">
    <span style="color:#e2e8f0;">{escape_html(cls)}</span>
    <span style="color:{color};font-weight:bold;">{count} ({pct}%)</span>
  </div>
  <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:16px;">
    <div style="background:{color};height:100%;width:{bar_w:.1f}%;border-radius:4px;"></div>
  </div>
</div>
"""

    # -- Top sponsors list --
    top_sponsor_rows = ""
    for name, count in sponsors[:15]:
        top_sponsor_rows += f"""<tr>
  <td style="padding:6px 10px;max-width:350px;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;">{escape_html(name)}</td>
  <td style="padding:6px 10px;text-align:right;color:#60a5fa;font-weight:bold;">{count}</td>
</tr>
"""

    # -- Phase bars --
    max_phase = max(phases.values(), default=1)
    if max_phase < 1:
        max_phase = 1
    phase_bars = ""
    phase_colors = {"PHASE1": "#06b6d4", "PHASE2": "#22c55e", "PHASE3": "#eab308", "PHASE4": "#f97316"}
    phase_labels = {"PHASE1": "Phase 1", "PHASE2": "Phase 2", "PHASE3": "Phase 3", "PHASE4": "Phase 4"}
    for phase_key in PHASES:
        count = phases.get(phase_key, 0)
        bar_w = max(count / max_phase * 100, 2)
        color = phase_colors.get(phase_key, "#94a3b8")
        phase_bars += f"""<div style="margin-bottom:8px;">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
    <span style="color:#e2e8f0;font-weight:500;">{phase_labels.get(phase_key, phase_key)}</span>
    <span style="color:{color};font-weight:bold;">{count:,}</span>
  </div>
  <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:22px;">
    <div style="background:{color};height:100%;width:{bar_w:.1f}%;border-radius:4px;"></div>
  </div>
</div>
"""

    # -- Status breakdown --
    status_raw = status["raw"]
    status_colors = {
        "COMPLETED": "#22c55e",
        "RECRUITING": "#06b6d4",
        "TERMINATED": "#ef4444",
        "WITHDRAWN": "#f97316",
        "UNKNOWN": "#64748b",
    }
    total_st = sum(status_raw.values())
    status_bars = ""
    for st in STATUS_CATEGORIES:
        count = status_raw.get(st, 0)
        pct = round(count / max(total_st, 1) * 100, 1)
        bar_w = max(pct, 2)
        color = status_colors.get(st, "#94a3b8")
        status_bars += f"""<div style="margin-bottom:8px;">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
    <span style="color:#e2e8f0;font-weight:500;">{escape_html(st)}</span>
    <span style="color:{color};font-weight:bold;">{count:,} ({pct}%)</span>
  </div>
  <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:22px;">
    <div style="background:{color};height:100%;width:{bar_w:.1f}%;border-radius:4px;"></div>
  </div>
</div>
"""

    # -- City breakdown --
    city_rows = ""
    for city, count in geo["city_breakdown"][:15]:
        pct = round(count / max(geo["total_sites"], 1) * 100, 1)
        is_lagos = city == "Lagos"
        style = "color:#ef4444;font-weight:bold;" if is_lagos else ""
        city_rows += f"""<tr>
  <td style="padding:6px 10px;{style}">{escape_html(city)}</td>
  <td style="padding:6px 10px;text-align:right;{style}">{count}</td>
  <td style="padding:6px 10px;text-align:right;{style}">{pct}%</td>
</tr>
"""

    # -- What would it take? rows --
    target_rows = ""
    for t in targets:
        target_rows += f"""<tr>
  <td style="padding:10px 14px;font-weight:bold;">{escape_html(t["benchmark"])}</td>
  <td style="padding:10px 14px;text-align:right;">{t["benchmark_rate"]}/M</td>
  <td style="padding:10px 14px;text-align:right;color:#22c55e;font-weight:bold;">
    {t["needed_trials"]:,}</td>
  <td style="padding:10px 14px;text-align:right;color:#ef4444;font-weight:bold;">
    +{t["gap"]:,}</td>
</tr>
"""

    # -- Assemble the full HTML --
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Nigeria Paradox: Africa's Largest Country, Smallest Trial Portfolio</title>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #131a2b;
  --surface2: #1a2332;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #60a5fa;
  --danger: #ef4444;
  --warning: #f97316;
  --success: #22c55e;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  line-height: 1.6;
  min-height: 100vh;
}}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
h1 {{
  font-size: 2.2rem;
  font-weight: 800;
  background: linear-gradient(135deg, #ef4444, #f97316, #eab308);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 8px;
}}
h2 {{
  font-size: 1.5rem;
  font-weight: 700;
  color: #f8fafc;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--border);
}}
h3 {{
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}}
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 20px;
}}
.card-danger {{
  border-color: rgba(239,68,68,0.3);
  background: linear-gradient(135deg, rgba(239,68,68,0.05), var(--surface));
}}
.card-warning {{
  border-color: rgba(249,115,22,0.3);
  background: linear-gradient(135deg, rgba(249,115,22,0.05), var(--surface));
}}
.stat-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}}
.stat-box {{
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px;
  text-align: center;
}}
.stat-value {{
  font-size: 2rem;
  font-weight: 800;
  line-height: 1.2;
}}
.stat-label {{
  font-size: 0.85rem;
  color: var(--muted);
  margin-top: 4px;
}}
table {{
  width: 100%;
  border-collapse: collapse;
}}
th {{
  background: var(--surface2);
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 10px 14px;
  text-align: left;
  border-bottom: 2px solid var(--border);
}}
td {{
  border-bottom: 1px solid var(--border);
  color: var(--text);
  font-size: 0.9rem;
}}
tr:hover {{
  background: rgba(96,165,250,0.04);
}}
.badge {{
  display: inline-block;
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 0.75rem;
  font-weight: 700;
}}
.badge-danger {{ background: rgba(239,68,68,0.15); color: #ef4444; }}
.badge-warning {{ background: rgba(249,115,22,0.15); color: #f97316; }}
.badge-success {{ background: rgba(34,197,94,0.15); color: #22c55e; }}
.big-number {{
  font-size: 3.5rem;
  font-weight: 900;
  color: var(--danger);
  line-height: 1;
}}
.big-label {{
  font-size: 1rem;
  color: var(--muted);
  margin-top: 6px;
}}
.paradox-box {{
  display: flex;
  align-items: center;
  gap: 30px;
  padding: 20px;
  background: rgba(239,68,68,0.06);
  border: 2px dashed rgba(239,68,68,0.3);
  border-radius: 12px;
  margin: 16px 0;
}}
.vs {{ color: var(--muted); font-size: 1.5rem; font-weight: 800; }}
.highlight {{ color: var(--danger); font-weight: 700; }}
.highlight-orange {{ color: var(--warning); font-weight: 700; }}
.highlight-green {{ color: var(--success); font-weight: 700; }}
.highlight-blue {{ color: var(--accent); font-weight: 700; }}
.explanation-list {{
  list-style: none;
  padding: 0;
}}
.explanation-list li {{
  padding: 12px 16px;
  margin-bottom: 8px;
  background: var(--surface2);
  border-left: 4px solid var(--warning);
  border-radius: 0 8px 8px 0;
  font-size: 0.95rem;
}}
.explanation-list li strong {{
  color: var(--warning);
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}}
@media (max-width: 768px) {{
  .two-col {{ grid-template-columns: 1fr; }}
  .paradox-box {{ flex-direction: column; gap: 12px; text-align: center; }}
  h1 {{ font-size: 1.6rem; }}
  .big-number {{ font-size: 2.5rem; }}
  .stat-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
footer {{
  text-align: center;
  padding: 30px;
  color: var(--muted);
  font-size: 0.8rem;
  border-top: 1px solid var(--border);
  margin-top: 40px;
}}
</style>
</head>
<body>
<div class="container">

<!-- ============================================================ -->
<!-- HEADER -->
<!-- ============================================================ -->
<div style="text-align:center;padding:40px 0 30px;">
  <h1>The Nigeria Paradox</h1>
  <p style="font-size:1.15rem;color:var(--muted);max-width:800px;margin:0 auto;">
    Africa's most populous country (230M) and largest economy ($500B GDP),<br>
    yet has only <span class="highlight">{nigeria_total:,} interventional trials</span>
    (<span class="highlight">{nigeria_per_m}/million</span>) &mdash;
    the most extreme large-country trial deficit in the world.
  </p>
  <p style="margin-top:8px;color:var(--muted);font-size:0.8rem;">
    Data: ClinicalTrials.gov API v2 | Generated: {escape_html(ts[:19])}
  </p>
</div>

<!-- ============================================================ -->
<!-- 1. SUMMARY STATS -->
<!-- ============================================================ -->
<div class="stat-grid">
  <div class="stat-box">
    <div class="stat-value" style="color:var(--danger);">{nigeria_total:,}</div>
    <div class="stat-label">Total interventional trials</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:var(--danger);">{nigeria_per_m}</div>
    <div class="stat-label">Trials per million population</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:var(--warning);">230M</div>
    <div class="stat-label">Population (Africa's largest)</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:var(--warning);">$500B</div>
    <div class="stat-label">GDP (Africa's largest economy)</div>
  </div>
</div>

<!-- ============================================================ -->
<!-- 2. THE PARADOX VISUALIZED -->
<!-- ============================================================ -->
<div class="card card-danger">
  <h2>The Paradox</h2>
  <p style="color:var(--muted);margin-bottom:16px;">
    Nigeria has the economic weight and population to be a research powerhouse.
    Instead, it ranks below every major developing country on earth.
  </p>
  <div class="paradox-box">
    <div style="flex:1;text-align:center;">
      <div class="big-number">230M</div>
      <div class="big-label">Population</div>
    </div>
    <div class="vs">+</div>
    <div style="flex:1;text-align:center;">
      <div class="big-number" style="color:var(--warning);">$500B</div>
      <div class="big-label">GDP</div>
    </div>
    <div class="vs">=</div>
    <div style="flex:1;text-align:center;">
      <div class="big-number">{nigeria_total:,}</div>
      <div class="big-label">Trials ({nigeria_per_m}/M)</div>
    </div>
  </div>
  <p style="color:var(--muted);margin-top:12px;font-style:italic;">
    For context: Bangladesh (173M, $460B GDP) has {comparators[0]["trials"] if comparators and comparators[0]["country"] == "Bangladesh" else
    next((c["trials"] for c in comparators if c["country"] == "Bangladesh"), "N/A"):,} trials.
    Pakistan (240M) has {next((c["trials"] for c in comparators if c["country"] == "Pakistan"), "N/A"):,} trials.
    Even Ethiopia (130M, far poorer) has {next((c["trials"] for c in comparators if c["country"] == "Ethiopia"), "N/A"):,} trials.
  </p>
</div>

<!-- ============================================================ -->
<!-- 3. COMPARATOR TABLE -->
<!-- ============================================================ -->
<div class="card">
  <h2>Nigeria vs. Large Developing Countries</h2>
  <p style="color:var(--muted);margin-bottom:14px;">
    "Gap Ratio" = comparator's per-capita rate / Nigeria's per-capita rate.
    A ratio of 10x means that country runs 10 times more trials per person than Nigeria.
  </p>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>Country</th>
          <th style="text-align:right;">Population</th>
          <th style="text-align:right;">Trials</th>
          <th style="text-align:right;">Per Million</th>
          <th style="text-align:right;">Gap Ratio</th>
        </tr>
      </thead>
      <tbody>
{nigeria_row}
{comp_rows}
      </tbody>
    </table>
  </div>
</div>

<!-- ============================================================ -->
<!-- 4. CONDITION BREAKDOWN -->
<!-- ============================================================ -->
<div class="card">
  <h2>Nigeria Condition Breakdown</h2>
  <p style="color:var(--muted);margin-bottom:14px;">
    What diseases are being studied in Nigeria's tiny trial portfolio?
    Note the overlap between conditions means totals exceed {nigeria_total:,}.
  </p>
{cond_bars}
</div>

<!-- ============================================================ -->
<!-- 5. THE SCD CRISIS -->
<!-- ============================================================ -->
<div class="card card-danger">
  <h2>The Sickle Cell Disease Crisis</h2>
  <p style="color:var(--muted);margin-bottom:16px;">
    Nigeria has the world's highest sickle cell disease burden &mdash;
    approximately 150,000 babies born with SCD every year (~30% of the global total).
    How much research is happening?
  </p>
  <div class="stat-grid">
    <div class="stat-box" style="border-color:rgba(239,68,68,0.4);">
      <div class="stat-value" style="color:var(--danger);">{scd["scd_trials_nigeria"]:,}</div>
      <div class="stat-label">SCD trials in Nigeria</div>
    </div>
    <div class="stat-box" style="border-color:rgba(239,68,68,0.4);">
      <div class="stat-value" style="color:var(--warning);">150K</div>
      <div class="stat-label">SCD births/year in Nigeria</div>
    </div>
    <div class="stat-box" style="border-color:rgba(239,68,68,0.4);">
      <div class="stat-value" style="color:var(--danger);">{scd["trials_per_10k_births"]}</div>
      <div class="stat-label">Trials per 10,000 SCD births</div>
    </div>
  </div>
  <p style="color:#ef4444;font-weight:600;margin-top:10px;">
    The country with the world's highest SCD burden has barely any trials
    to develop treatments, delivery models, or screening programs for its own population.
  </p>
</div>

<!-- ============================================================ -->
<!-- 6. SPONSOR ANALYSIS -->
<!-- ============================================================ -->
<div class="card">
  <h2>Who Runs Nigeria's Trials?</h2>
  <div class="two-col">
    <div>
      <h3>Sponsor Class</h3>
{sponsor_class_html}
      <div style="margin-top:16px;padding:14px;background:var(--surface2);border-radius:8px;">
        <div style="display:flex;justify-content:space-between;">
          <span>Local Nigerian sponsors:</span>
          <span class="highlight-blue">{local_foreign["local"]} ({local_foreign["local_pct"]}%)</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:6px;">
          <span>Foreign sponsors:</span>
          <span class="highlight-orange">{local_foreign["foreign"]}
            ({round(100 - local_foreign["local_pct"], 1)}%)</span>
        </div>
      </div>
    </div>
    <div>
      <h3>Top Sponsors</h3>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Sponsor</th>
              <th style="text-align:right;">Trials</th>
            </tr>
          </thead>
          <tbody>
{top_sponsor_rows}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ============================================================ -->
<!-- 7. PHASE DISTRIBUTION -->
<!-- ============================================================ -->
<div class="card">
  <h2>Phase Distribution</h2>
  <p style="color:var(--muted);margin-bottom:14px;">
    Note: not all trials have a phase designation; some span multiple phases.
  </p>
{phase_bars}
</div>

<!-- ============================================================ -->
<!-- 8. STATUS ANALYSIS -->
<!-- ============================================================ -->
<div class="card card-warning">
  <h2>Trial Status: Completed vs. Abandoned</h2>
  <div class="stat-grid" style="margin-bottom:16px;">
    <div class="stat-box">
      <div class="stat-value" style="color:var(--success);">{status["completion_rate"]}%</div>
      <div class="stat-label">Completion rate</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" style="color:var(--danger);">{status["abandonment_rate"]}%</div>
      <div class="stat-label">Termination/Withdrawal rate</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" style="color:#64748b;">{status["unknown_rate"]}%</div>
      <div class="stat-label">Unknown status rate</div>
    </div>
  </div>
{status_bars}
</div>

<!-- ============================================================ -->
<!-- 9. GEOGRAPHIC CONCENTRATION -->
<!-- ============================================================ -->
<div class="card">
  <h2>Geographic Concentration: Lagos vs. Rest of Nigeria</h2>
  <p style="color:var(--muted);margin-bottom:14px;">
    Of {geo["total_sites"]:,} trial site records, Lagos accounts for
    <span class="highlight">{geo["lagos_count"]:,} ({geo["lagos_pct"]}%)</span>.
    The top 3 cities hold <span class="highlight">{geo["top3_pct"]}%</span> of all sites.
  </p>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>City</th>
          <th style="text-align:right;">Trial Sites</th>
          <th style="text-align:right;">% of Total</th>
        </tr>
      </thead>
      <tbody>
{city_rows}
      </tbody>
    </table>
  </div>
  <p style="color:var(--muted);margin-top:12px;font-style:italic;">
    Northern Nigeria (Kano, Sokoto, Maiduguri) &mdash; home to ~100M people &mdash;
    is almost completely absent from clinical research.
  </p>
</div>

<!-- ============================================================ -->
<!-- 10. WHY? POSSIBLE EXPLANATIONS -->
<!-- ============================================================ -->
<div class="card">
  <h2>Why? Possible Explanations</h2>
  <p style="color:var(--muted);margin-bottom:14px;">
    Nigeria's trial deficit is not a mystery &mdash; it is the product of
    structural, regulatory, and institutional failures compounding over decades.
  </p>
  <ul class="explanation-list">
    <li>
      <strong>Regulatory barriers:</strong> NAFDAC approval timelines average 6-12 months
      for trial authorization, compared to weeks in South Africa or Kenya. Import permits
      for investigational products routinely take 3-6 additional months.
    </li>
    <li>
      <strong>Ethics committee fragmentation:</strong> Nigeria has hundreds of institutional
      ethics committees with no central registration or mutual recognition. Each site
      requires separate approval, creating paralysis for multi-site studies.
    </li>
    <li>
      <strong>Brain drain:</strong> Nigeria loses an estimated 2,000+ physicians per year
      to emigration (UK, US, Canada, Gulf states). Clinical research capacity
      drains with them.
    </li>
    <li>
      <strong>Funding drought:</strong> The Nigerian government allocates &lt;1% of GDP to
      health research. NIH, Wellcome, and Gates funding flows preferentially to
      East Africa (Kenya, Uganda, Tanzania) where infrastructure is established.
    </li>
    <li>
      <strong>Infrastructure gaps:</strong> Reliable electricity, cold-chain logistics,
      electronic health records, and GCP-trained staff are concentrated in Lagos
      and a handful of teaching hospitals.
    </li>
    <li>
      <strong>Security concerns:</strong> The northeast insurgency (Boko Haram) and
      northwest banditry have effectively removed ~60M people from any research
      catchment area. Sponsors avoid regions with travel advisories.
    </li>
    <li>
      <strong>Pharma bypassing:</strong> Multinational pharmaceutical companies preferentially
      site trials in South Africa, Egypt, and Kenya, which have established
      CRO networks and regulatory fast-tracks. Nigeria is seen as too difficult.
    </li>
    <li>
      <strong>No national clinical trial registry:</strong> Unlike India (CTRI), Brazil (ReBEC),
      or South Africa (SANCTR), Nigeria has no mandatory national registry,
      reducing visibility and accountability.
    </li>
  </ul>
</div>

<!-- ============================================================ -->
<!-- 11. WHAT WOULD IT TAKE? -->
<!-- ============================================================ -->
<div class="card" style="border-color:rgba(34,197,94,0.3);">
  <h2>What Would It Take?</h2>
  <p style="color:var(--muted);margin-bottom:14px;">
    If Nigeria matched other large developing countries' per-capita trial rates,
    how many trials would it need?
  </p>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>Benchmark Country</th>
          <th style="text-align:right;">Their Rate</th>
          <th style="text-align:right;">Nigeria Would Need</th>
          <th style="text-align:right;">Gap (Additional)</th>
        </tr>
      </thead>
      <tbody>
{target_rows}
      </tbody>
    </table>
  </div>
  <p style="color:var(--muted);margin-top:16px;">
    At <span class="highlight-green">Brazil's rate</span> of
    {next((t["benchmark_rate"] for t in targets if t["benchmark"] == "Brazil"), "N/A")}/million,
    Nigeria would need
    <span class="highlight-green">{next((t["needed_trials"] for t in targets if t["benchmark"] == "Brazil"), "N/A"):,}</span>
    trials &mdash; roughly
    <span class="highlight">{next((t["gap"] for t in targets if t["benchmark"] == "Brazil"), 0):,}</span>
    more than it currently has.
    Even matching
    <span class="highlight-blue">Bangladesh's</span> more modest rate would require
    <span class="highlight">{next((t["needed_trials"] for t in targets if t["benchmark"] == "Bangladesh"), "N/A"):,}</span>
    trials.
  </p>
</div>

<!-- ============================================================ -->
<!-- METHODOLOGY -->
<!-- ============================================================ -->
<div class="card" style="background:var(--surface2);">
  <h2>Methodology</h2>
  <p style="color:var(--muted);font-size:0.9rem;">
    All data queried from ClinicalTrials.gov API v2 using
    <code>filter.advanced=AREA[StudyType]INTERVENTIONAL</code> and
    <code>query.locn=Nigeria</code> (and equivalents for condition/phase/status subsets).
    Trial-level data fetched with pagination (pageSize=200) for sponsor and geographic
    analysis. Comparator country counts queried identically.
    Population estimates from UN World Population Prospects 2024 revision.
    GDP figures from World Bank 2024 estimates. SCD burden from
    Piel et al. (Lancet 2013) and GBD 2021.
    Nigerian institution classification uses keyword matching against
    known Nigerian universities, teaching hospitals, and federal medical centers.
    Limitations: ClinicalTrials.gov only; trials registered on WHO ICTRP,
    Pan African Clinical Trials Registry, or national registries are missed.
    Condition queries may overlap (a trial studying both HIV and TB
    counts in both categories).
  </p>
</div>

<footer>
  The Nigeria Paradox &mdash; Project Q, AfricaRCT Series<br>
  ClinicalTrials.gov API v2 | Generated {escape_html(ts[:10])}<br>
  &copy; 2026. Open-access analysis. No patient data used.
</footer>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------

def print_summary(results):
    """Print analysis summary to console."""
    print("\n" + "=" * 60)
    print("THE NIGERIA PARADOX — SUMMARY")
    print("=" * 60)

    print(f"\nNigeria: {results['nigeria_total']:,} trials | "
          f"{results['nigeria_per_million']}/M | "
          f"Pop: {NIGERIA_POPULATION}M | GDP: ${NIGERIA_GDP_NOMINAL}B")

    print("\n--- Gap Ratios vs Comparators ---")
    for c in results["comparator_analysis"]:
        print(f"  {c['country']:<15} {c['trials']:>6,} trials  "
              f"{c['per_million']:>6.2f}/M  "
              f"Gap: {c['gap_ratio']}x")

    print("\n--- Condition Breakdown ---")
    for c in results["condition_breakdown"]:
        print(f"  {c['condition']:<20} {c['trials']:>4} ({c['pct_of_total']}%)")

    print(f"\n--- SCD Paradox ---")
    scd = results["scd_paradox"]
    print(f"  SCD trials in Nigeria: {scd['scd_trials_nigeria']}")
    print(f"  SCD births/year: ~{scd['scd_births_per_year']:,}")
    print(f"  Trials per 10K SCD births: {scd['trials_per_10k_births']}")

    print(f"\n--- Sponsor Analysis ---")
    lf = results["local_vs_foreign"]
    print(f"  Local Nigerian: {lf['local']} ({lf['local_pct']}%)")
    print(f"  Foreign: {lf['foreign']} ({round(100 - lf['local_pct'], 1)}%)")

    print(f"\n--- Status ---")
    sa = results["status_analysis"]
    print(f"  Completion rate: {sa['completion_rate']}%")
    print(f"  Abandonment rate: {sa['abandonment_rate']}%")
    print(f"  Unknown rate: {sa['unknown_rate']}%")

    print(f"\n--- Geographic Concentration ---")
    geo = results["geographic_concentration"]
    print(f"  Lagos: {geo['lagos_count']} sites ({geo['lagos_pct']}%)")
    print(f"  Top 3 cities: {geo['top3_pct']}%")

    print(f"\n--- What Would It Take? ---")
    for t in results["what_would_it_take"]:
        print(f"  At {t['benchmark']}'s rate: {t['needed_trials']:,} needed (+{t['gap']:,})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Entry point."""
    print("=" * 60)
    print("The Nigeria Paradox — Deep-Dive Analysis")
    print("=" * 60)

    # Fetch data
    data = fetch_all_data()

    # Analyze
    print("\nAnalyzing data...")
    results = analyze_data(data)

    # Summary to console
    print_summary(results)

    # Generate HTML
    print("\nGenerating HTML dashboard...")
    html = generate_html(data, results)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nSaved to {OUTPUT_HTML}")
    print(f"File size: {OUTPUT_HTML.stat().st_size:,} bytes")
    print("\nDone.")


if __name__ == "__main__":
    main()
