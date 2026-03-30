#!/usr/bin/env python
"""
The Termination Cascade -- Who Kills Africa's Trials (Project E)
================================================================
Queries ClinicalTrials.gov API v2 for all TERMINATED and WITHDRAWN
interventional trials in Africa, classifies each by termination driver
(global decision, local failure, safety, futility, unknown), computes
termination rates vs completed trials, and generates an interactive
HTML dashboard.

Usage:
    python termination_cascade.py

Output:
    data/termination_cascade_data.json  -- cached API data (24h)
    termination-cascade.html            -- interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import os
import sys
import io
import time
import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# Fix Windows cp1252 encoding issues
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config -------------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_PATH = DATA_DIR / "termination_cascade_data.json"
OUTPUT_HTML = Path(__file__).parent / "termination-cascade.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35  # seconds between API calls

# Known large pharma sponsors (partial match)
PHARMA_KEYWORDS = [
    "pfizer", "novartis", "roche", "genentech", "merck", "msd",
    "astrazeneca", "johnson", "janssen", "sanofi", "gsk",
    "glaxosmithkline", "bayer", "abbvie", "amgen", "gilead",
    "bristol-myers", "bms", "lilly", "eli lilly", "boehringer",
    "takeda", "astellas", "daiichi", "regeneron", "moderna",
    "biogen", "vertex", "alexion", "servier", "otsuka",
    "eisai", "teva", "mylan", "sandoz", "shire",
]

# Safety-related keywords in why_stopped or title
SAFETY_KEYWORDS = [
    "safety", "adverse", "toxicity", "toxic", "death", "fatal",
    "serious adverse", "sae", "dsmb", "data safety",
    "side effect", "harm", "risk", "hepatotoxicity", "cardiotoxicity",
]

# Futility-related keywords
FUTILITY_KEYWORDS = [
    "futility", "futile", "lack of efficacy", "no benefit",
    "insufficient efficacy", "efficacy endpoint",
    "did not meet", "failed to demonstrate", "no significant",
    "interim analysis", "ineffective", "no difference",
]


# -- API helpers --------------------------------------------------------------
def search_trials_paginated(status, page_size=200):
    """Fetch ALL trials matching status in Africa, paginating as needed."""
    all_trials = []
    page_token = None

    while True:
        params = {
            "format": "json",
            "pageSize": page_size,
            "countTotal": "true",
            "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]{status}",
            "query.locn": "Africa",
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  WARNING: API error for status={status}: {e}")
            break

        total = data.get("totalCount", 0)
        studies = data.get("studies", [])

        if not all_trials:
            print(f"  {status}: {total} total trials found, fetching pages...")

        for s in studies:
            trial = extract_trial(s)
            if trial:
                all_trials.append(trial)

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(RATE_LIMIT_DELAY)

    print(f"  {status}: fetched {len(all_trials)} trial records")
    return all_trials, total


def count_trials(status):
    """Get total count only (no full fetch) for a status."""
    params = {
        "format": "json",
        "pageSize": 1,
        "countTotal": "true",
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]{status}",
        "query.locn": "Africa",
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("totalCount", 0)
    except requests.RequestException as e:
        print(f"  WARNING: count API error for status={status}: {e}")
        return 0


def count_trials_global(status):
    """Get global count (no location filter) for comparison."""
    params = {
        "format": "json",
        "pageSize": 1,
        "countTotal": "true",
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]{status}",
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("totalCount", 0)
    except requests.RequestException as e:
        print(f"  WARNING: global count API error for status={status}: {e}")
        return 0


def extract_trial(study):
    """Extract structured fields from a CT.gov v2 study object."""
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status_mod = proto.get("statusModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    design_mod = proto.get("designModule", {})
    cond_mod = proto.get("conditionsModule", {})
    loc_mod = proto.get("contactsLocationsModule", {})
    arms_mod = proto.get("armsInterventionsModule", {})

    lead = sponsor_mod.get("leadSponsor", {})
    locations = loc_mod.get("locations", [])
    phases_raw = design_mod.get("phases") or ["NA"]
    enrollment_info = design_mod.get("enrollmentInfo", {})

    # Extract interventions
    interventions = arms_mod.get("interventions", [])
    intervention_names = []
    for iv in interventions:
        name = iv.get("name", "")
        if name:
            intervention_names.append(name)

    # Count unique countries in locations
    countries = set()
    for loc in locations:
        country = loc.get("country", "")
        if country:
            countries.add(country)

    # Get why stopped
    why_stopped = status_mod.get("whyStoppedDescription", "")

    # Get start date
    start_struct = status_mod.get("startDateStruct", {})
    start_date = start_struct.get("date", "")

    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "sponsor": lead.get("name", "Unknown"),
        "sponsor_class": lead.get("class", "OTHER"),
        "phases": phases_raw,
        "conditions": cond_mod.get("conditions", []),
        "enrollment": enrollment_info.get("count", 0) or 0,
        "start_date": start_date,
        "locations_count": len(locations),
        "countries_count": len(countries),
        "why_stopped": why_stopped,
        "interventions": intervention_names,
        "status": status_mod.get("overallStatus", "UNKNOWN"),
    }


# -- Classification -----------------------------------------------------------
def is_foreign_pharma(sponsor, sponsor_class):
    """Check if sponsor is a foreign pharma company."""
    if sponsor_class == "INDUSTRY":
        return True
    sponsor_lower = sponsor.lower()
    for kw in PHARMA_KEYWORDS:
        if kw in sponsor_lower:
            return True
    return False


def classify_termination(trial):
    """Classify termination reason into one of 5 categories."""
    why = (trial.get("why_stopped") or "").lower()
    title = (trial.get("title") or "").lower()
    sponsor = trial.get("sponsor", "")
    sponsor_class = trial.get("sponsor_class", "OTHER")
    locations_count = trial.get("locations_count", 0) or 0
    countries_count = trial.get("countries_count", 0) or 0
    combined_text = f"{why} {title}"

    # 1. Safety signal
    for kw in SAFETY_KEYWORDS:
        if kw in combined_text:
            return "Safety signal"

    # 2. Futility
    for kw in FUTILITY_KEYWORDS:
        if kw in combined_text:
            return "Futility"

    # 3. Global decision: foreign sponsor AND multinational (>10 sites)
    if is_foreign_pharma(sponsor, sponsor_class) and locations_count > 10:
        return "Global decision"

    # 4. Local failure: single-site or few sites, likely logistics/funding
    if locations_count <= 3:
        local_keywords = [
            "funding", "recruitment", "enrol", "enroll", "accrual",
            "slow", "logistic", "resource", "staff", "closed",
            "low enrollment", "poor enrollment", "insufficient",
        ]
        for kw in local_keywords:
            if kw in combined_text:
                return "Local failure"
        # Also classify as local failure if single-site regardless of reason
        if locations_count <= 1:
            return "Local failure"

    # 5. Global decision fallback: industry sponsor with many countries
    if is_foreign_pharma(sponsor, sponsor_class) and countries_count > 3:
        return "Global decision"

    # 6. Local failure fallback: few sites with local-sounding reasons
    if locations_count <= 5:
        return "Local failure"

    return "Unknown"


def short_title(title, max_len=60):
    """Truncate title for display."""
    if not title:
        return ""
    if len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def extract_year(start_date):
    """Extract year from CT.gov date string."""
    if not start_date:
        return None
    # Formats: "2024-01-15", "January 2024", "2024", "January 15, 2024"
    match = re.search(r"(\d{4})", start_date)
    if match:
        return int(match.group(1))
    return None


# -- Data collection ----------------------------------------------------------
def collect_data():
    """Run all queries and return structured data."""
    print("=" * 60)
    print("The Termination Cascade -- Fetching Data")
    print("=" * 60)

    # Check cache
    if CACHE_PATH.exists():
        age = datetime.now() - datetime.fromtimestamp(CACHE_PATH.stat().st_mtime)
        if age < timedelta(hours=CACHE_HOURS):
            print(f"Using cached data ({age.seconds // 3600}h old)")
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
            print("Cache loaded.")

    data = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
        },
    }

    # 1. Fetch ALL terminated trials in Africa (full data)
    print("\n[1/5] Fetching TERMINATED trials in Africa...")
    terminated_trials, terminated_total = search_trials_paginated("TERMINATED")
    data["terminated_trials"] = terminated_trials
    data["terminated_total"] = terminated_total
    time.sleep(RATE_LIMIT_DELAY)

    # 2. Fetch ALL withdrawn trials in Africa (full data)
    print("\n[2/5] Fetching WITHDRAWN trials in Africa...")
    withdrawn_trials, withdrawn_total = search_trials_paginated("WITHDRAWN")
    data["withdrawn_trials"] = withdrawn_trials
    data["withdrawn_total"] = withdrawn_total
    time.sleep(RATE_LIMIT_DELAY)

    # 3. Count completed (for rate computation)
    print("\n[3/5] Counting COMPLETED trials in Africa...")
    completed_count = count_trials("COMPLETED")
    data["completed_count"] = completed_count
    print(f"  COMPLETED: {completed_count}")
    time.sleep(RATE_LIMIT_DELAY)

    # 4. Global counts for comparison
    print("\n[4/5] Fetching global counts for comparison...")
    global_terminated = count_trials_global("TERMINATED")
    time.sleep(RATE_LIMIT_DELAY)
    global_withdrawn = count_trials_global("WITHDRAWN")
    time.sleep(RATE_LIMIT_DELAY)
    global_completed = count_trials_global("COMPLETED")
    data["global_terminated"] = global_terminated
    data["global_withdrawn"] = global_withdrawn
    data["global_completed"] = global_completed
    print(f"  Global terminated: {global_terminated}")
    print(f"  Global withdrawn: {global_withdrawn}")
    print(f"  Global completed: {global_completed}")

    # 5. Summary
    print("\n[5/5] Computing summaries...")
    combined = terminated_total + withdrawn_total
    denom = combined + completed_count
    rate = (combined / denom * 100) if denom > 0 else 0
    global_denom = global_terminated + global_withdrawn + global_completed
    global_rate = ((global_terminated + global_withdrawn) / global_denom * 100) if global_denom > 0 else 0

    data["africa_termination_rate"] = round(rate, 2)
    data["global_termination_rate"] = round(global_rate, 2)

    # Cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nCached to {CACHE_PATH}")

    return data


# -- Analysis -----------------------------------------------------------------
def run_analysis(data):
    """Classify and compute all metrics."""
    results = {}

    terminated = data.get("terminated_trials", [])
    withdrawn = data.get("withdrawn_trials", [])
    all_stopped = terminated + withdrawn

    # Classify each
    for t in all_stopped:
        t["classification"] = classify_termination(t)

    results["terminated_count"] = data.get("terminated_total", len(terminated))
    results["withdrawn_count"] = data.get("withdrawn_total", len(withdrawn))
    results["combined_count"] = results["terminated_count"] + results["withdrawn_count"]
    results["completed_count"] = data.get("completed_count", 0)

    denom = results["combined_count"] + results["completed_count"]
    results["africa_rate"] = round((results["combined_count"] / denom * 100) if denom > 0 else 0, 2)
    results["global_rate"] = data.get("global_termination_rate", 0)

    # Classification breakdown
    class_counts = Counter(t["classification"] for t in all_stopped)
    results["classification_counts"] = dict(class_counts)
    results["global_decision_pct"] = round(
        (class_counts.get("Global decision", 0) / len(all_stopped) * 100) if all_stopped else 0, 1
    )

    # Sponsor breakdown (top sponsors by termination count)
    sponsor_counts = Counter(t["sponsor"] for t in all_stopped)
    results["top_sponsors"] = sponsor_counts.most_common(25)

    # Sponsor class breakdown
    sponsor_class_counts = Counter(t["sponsor_class"] for t in all_stopped)
    results["sponsor_class_counts"] = dict(sponsor_class_counts)

    # Conditions affected
    cond_counts = Counter()
    for t in all_stopped:
        for c in t.get("conditions", []):
            cond_counts[c] += 1
    results["top_conditions"] = cond_counts.most_common(30)

    # Drugs/interventions lost
    intervention_counts = Counter()
    for t in all_stopped:
        for iv in t.get("interventions", []):
            intervention_counts[iv] += 1
    results["top_interventions"] = intervention_counts.most_common(40)

    # Global decision trials (for the "Kill Switch" section)
    global_kills = [t for t in all_stopped if t["classification"] == "Global decision"]
    global_kills.sort(key=lambda x: -(x.get("enrollment", 0) or 0))
    results["global_kill_trials"] = global_kills

    # Phase distribution
    phase_counts = Counter()
    for t in all_stopped:
        for p in t.get("phases", ["NA"]):
            phase_counts[p] += 1
    results["phase_counts"] = dict(phase_counts)

    # Timeline: terminations by year
    year_counts = Counter()
    for t in all_stopped:
        year = extract_year(t.get("start_date", ""))
        if year and 1990 <= year <= 2026:
            year_counts[year] += 1
    results["year_counts"] = dict(sorted(year_counts.items()))

    # All trials for the full table
    results["all_stopped_trials"] = all_stopped

    # Global comparison counts
    results["global_terminated"] = data.get("global_terminated", 0)
    results["global_withdrawn"] = data.get("global_withdrawn", 0)
    results["global_completed"] = data.get("global_completed", 0)

    return results


# -- HTML generation ----------------------------------------------------------
def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def classification_color(cls):
    """Return CSS color for classification."""
    return {
        "Global decision": "#ef4444",
        "Local failure": "#f59e0b",
        "Safety signal": "#a855f7",
        "Futility": "#3b82f6",
        "Unknown": "#6b7280",
    }.get(cls, "#6b7280")


def classification_icon(cls):
    """Return icon for classification."""
    return {
        "Global decision": "&#x1F310;",
        "Local failure": "&#x1F4CD;",
        "Safety signal": "&#x26A0;",
        "Futility": "&#x1F4C9;",
        "Unknown": "&#x2753;",
    }.get(cls, "&#x2753;")


def generate_html(results):
    """Generate the full HTML dashboard."""
    now = datetime.now().strftime("%d %B %Y")

    terminated_count = results["terminated_count"]
    withdrawn_count = results["withdrawn_count"]
    combined = results["combined_count"]
    completed_count = results["completed_count"]
    africa_rate = results["africa_rate"]
    global_rate = results["global_rate"]
    global_decision_pct = results["global_decision_pct"]
    class_counts = results["classification_counts"]
    top_sponsors = results["top_sponsors"]
    sponsor_class_counts = results["sponsor_class_counts"]
    top_conditions = results["top_conditions"]
    top_interventions = results["top_interventions"]
    global_kills = results["global_kill_trials"]
    phase_counts = results["phase_counts"]
    year_counts = results["year_counts"]
    all_trials = results["all_stopped_trials"]

    # Build bar chart data for classifications
    class_labels = ["Global decision", "Local failure", "Safety signal", "Futility", "Unknown"]
    class_values = [class_counts.get(c, 0) for c in class_labels]
    max_class_val = max(class_values) if class_values else 1

    # Build year timeline
    years_sorted = sorted(year_counts.keys())
    max_year_val = max(year_counts.values()) if year_counts else 1

    # Build sponsor bar data
    sponsor_labels = [s[0] for s in top_sponsors[:15]]
    sponsor_values = [s[1] for s in top_sponsors[:15]]
    max_sponsor_val = max(sponsor_values) if sponsor_values else 1

    # -----------------------------------------------------------------------
    # Trial table rows
    # -----------------------------------------------------------------------
    table_rows = []
    for t in sorted(all_trials, key=lambda x: x.get("classification", "Unknown")):
        nct = escape_html(t.get("nct_id", ""))
        title_short = escape_html(short_title(t.get("title", ""), 55))
        sponsor = escape_html(short_title(t.get("sponsor", ""), 30))
        sites = t.get("locations_count", 0) or 0
        phase = escape_html(", ".join(t.get("phases", ["NA"])))
        cls = t.get("classification", "Unknown")
        color = classification_color(cls)
        interventions = t.get("interventions", [])
        drug = escape_html(short_title(", ".join(interventions[:2]), 35)) if interventions else "-"
        status = t.get("status", "UNKNOWN")
        why = escape_html(short_title(t.get("why_stopped", ""), 40)) if t.get("why_stopped") else "-"

        table_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{nct}" target="_blank" style="color:#60a5fa">{nct}</a></td>
<td title="{escape_html(t.get('title', ''))}">{title_short}</td>
<td>{sponsor}</td>
<td style="text-align:center">{sites}</td>
<td>{phase}</td>
<td style="color:{color};font-weight:600">{escape_html(cls)}</td>
<td>{drug}</td>
<td style="font-size:0.8em;color:#94a3b8">{why}</td>
</tr>""")

    trial_table_html = "\n".join(table_rows)

    # -----------------------------------------------------------------------
    # Global kill switch rows
    # -----------------------------------------------------------------------
    kill_rows = []
    for t in global_kills[:50]:
        nct = escape_html(t.get("nct_id", ""))
        title_text = escape_html(short_title(t.get("title", ""), 55))
        sponsor = escape_html(short_title(t.get("sponsor", ""), 30))
        sites = t.get("locations_count", 0) or 0
        enrollment = t.get("enrollment", 0) or 0
        interventions = t.get("interventions", [])
        drug = escape_html(", ".join(interventions[:2])) if interventions else "-"
        why = escape_html(short_title(t.get("why_stopped", ""), 50)) if t.get("why_stopped") else "-"

        kill_rows.append(f"""<tr>
<td><a href="https://clinicaltrials.gov/study/{nct}" target="_blank" style="color:#60a5fa">{nct}</a></td>
<td title="{escape_html(t.get('title', ''))}">{title_text}</td>
<td>{sponsor}</td>
<td style="text-align:center">{sites}</td>
<td style="text-align:right">{enrollment:,}</td>
<td>{drug}</td>
<td style="font-size:0.85em;color:#fbbf24">{why}</td>
</tr>""")

    kill_table_html = "\n".join(kill_rows)

    # -----------------------------------------------------------------------
    # Classification bar chart (CSS)
    # -----------------------------------------------------------------------
    class_bars = []
    for i, label in enumerate(class_labels):
        val = class_values[i]
        pct = (val / max_class_val * 100) if max_class_val > 0 else 0
        color = classification_color(label)
        class_bars.append(f"""<div style="margin-bottom:8px">
<div style="display:flex;align-items:center;gap:12px">
<span style="width:140px;text-align:right;font-size:0.9em;color:#cbd5e1">{escape_html(label)}</span>
<div style="flex:1;background:#1e293b;border-radius:4px;overflow:hidden;height:28px">
<div style="width:{pct:.1f}%;background:{color};height:100%;border-radius:4px;display:flex;align-items:center;padding-left:8px">
<span style="font-size:0.85em;font-weight:700;color:#0a0e17">{val}</span>
</div></div></div></div>""")
    class_chart_html = "\n".join(class_bars)

    # -----------------------------------------------------------------------
    # Sponsor bar chart (CSS)
    # -----------------------------------------------------------------------
    sponsor_bars = []
    for i, label in enumerate(sponsor_labels):
        val = sponsor_values[i]
        pct = (val / max_sponsor_val * 100) if max_sponsor_val > 0 else 0
        sponsor_bars.append(f"""<div style="margin-bottom:5px">
<div style="display:flex;align-items:center;gap:8px">
<span style="width:220px;text-align:right;font-size:0.8em;color:#cbd5e1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{escape_html(label)}">{escape_html(short_title(label, 35))}</span>
<div style="flex:1;background:#1e293b;border-radius:3px;overflow:hidden;height:22px">
<div style="width:{pct:.1f}%;background:#ef4444;height:100%;border-radius:3px;display:flex;align-items:center;padding-left:6px">
<span style="font-size:0.8em;font-weight:600;color:#fff">{val}</span>
</div></div></div></div>""")
    sponsor_chart_html = "\n".join(sponsor_bars)

    # -----------------------------------------------------------------------
    # Timeline bar chart (CSS)
    # -----------------------------------------------------------------------
    timeline_bars = []
    for year in years_sorted:
        val = year_counts[year]
        pct = (val / max_year_val * 100) if max_year_val > 0 else 0
        timeline_bars.append(f"""<div style="display:flex;align-items:flex-end;gap:2px;flex-direction:column;width:100%">
<div style="display:flex;align-items:center;gap:6px;width:100%">
<span style="width:50px;text-align:right;font-size:0.8em;color:#94a3b8">{year}</span>
<div style="flex:1;background:#1e293b;border-radius:3px;overflow:hidden;height:20px">
<div style="width:{pct:.1f}%;background:#f59e0b;height:100%;border-radius:3px;display:flex;align-items:center;padding-left:5px">
<span style="font-size:0.75em;font-weight:600;color:#0a0e17">{val}</span>
</div></div></div></div>""")
    timeline_chart_html = "\n".join(timeline_bars)

    # -----------------------------------------------------------------------
    # Drugs Africa Lost
    # -----------------------------------------------------------------------
    drug_rows = []
    for iv_name, count in top_interventions[:30]:
        drug_rows.append(f"""<tr>
<td>{escape_html(iv_name)}</td>
<td style="text-align:center">{count}</td>
</tr>""")
    drug_table_html = "\n".join(drug_rows)

    # -----------------------------------------------------------------------
    # Conditions affected
    # -----------------------------------------------------------------------
    condition_rows = []
    for cond_name, count in top_conditions[:20]:
        condition_rows.append(f"""<tr>
<td>{escape_html(cond_name)}</td>
<td style="text-align:center">{count}</td>
</tr>""")
    condition_table_html = "\n".join(condition_rows)

    # -----------------------------------------------------------------------
    # Sponsor class breakdown
    # -----------------------------------------------------------------------
    sc_items = []
    for cls_name, cnt in sorted(sponsor_class_counts.items(), key=lambda x: -x[1]):
        pct = (cnt / combined * 100) if combined > 0 else 0
        sc_items.append(f'<span style="color:#60a5fa;font-weight:600">{escape_html(cls_name)}</span>: {cnt} ({pct:.1f}%)')
    sponsor_class_html = " &middot; ".join(sc_items)

    # -----------------------------------------------------------------------
    # Phase breakdown
    # -----------------------------------------------------------------------
    phase_items = []
    for p_name, cnt in sorted(phase_counts.items(), key=lambda x: -x[1]):
        pct = (cnt / combined * 100) if combined > 0 else 0
        phase_items.append(f'<span style="color:#a78bfa">{escape_html(p_name)}</span>: {cnt} ({pct:.1f}%)')
    phase_html = " &middot; ".join(phase_items)

    # -----------------------------------------------------------------------
    # Assemble HTML
    # -----------------------------------------------------------------------
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Termination Cascade -- Who Kills Africa's Trials</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0e17;color:#e2e8f0;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;line-height:1.6}}
.container{{max-width:1400px;margin:0 auto;padding:20px}}
h1{{font-size:2.2em;color:#f8fafc;margin-bottom:4px;letter-spacing:-0.5px}}
h2{{font-size:1.5em;color:#f1f5f9;margin:32px 0 16px;border-bottom:2px solid #1e293b;padding-bottom:8px}}
h3{{font-size:1.15em;color:#cbd5e1;margin:20px 0 10px}}
.subtitle{{color:#94a3b8;font-size:1.1em;margin-bottom:24px}}
.date-stamp{{color:#64748b;font-size:0.85em;margin-bottom:20px}}

/* Summary cards */
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:24px 0}}
.card{{background:#111827;border:1px solid #1e293b;border-radius:12px;padding:20px;text-align:center}}
.card-value{{font-size:2.4em;font-weight:800;color:#f8fafc}}
.card-label{{font-size:0.85em;color:#94a3b8;margin-top:4px}}
.card-red .card-value{{color:#ef4444}}
.card-amber .card-value{{color:#f59e0b}}
.card-blue .card-value{{color:#3b82f6}}
.card-purple .card-value{{color:#a855f7}}
.card-green .card-value{{color:#4ade80}}

/* Section containers */
.section{{background:#111827;border:1px solid #1e293b;border-radius:12px;padding:24px;margin:24px 0}}

/* Tables */
table{{width:100%;border-collapse:collapse;font-size:0.85em}}
th{{background:#1e293b;color:#94a3b8;padding:10px 8px;text-align:left;font-weight:600;position:sticky;top:0;z-index:2}}
td{{padding:8px;border-bottom:1px solid #1e293b;color:#cbd5e1}}
tr:hover td{{background:#1a2332}}
a{{text-decoration:none}}
a:hover{{text-decoration:underline}}

/* Scrollable table wrapper */
.table-wrap{{max-height:600px;overflow-y:auto;border:1px solid #1e293b;border-radius:8px}}
.table-wrap-xl{{max-height:900px}}

/* Kill switch banner */
.kill-banner{{background:linear-gradient(135deg,#7f1d1d 0%,#450a0a 100%);border:2px solid #ef4444;border-radius:12px;padding:24px;margin:24px 0;text-align:center}}
.kill-banner h2{{color:#fca5a5;border:none;margin:0 0 8px}}
.kill-banner p{{color:#fecaca;font-size:1.05em}}

/* Rate comparison */
.rate-compare{{display:flex;gap:24px;align-items:center;justify-content:center;flex-wrap:wrap;margin:16px 0}}
.rate-box{{text-align:center;padding:16px 32px;border-radius:8px}}
.rate-box.africa{{background:#7f1d1d;border:1px solid #ef4444}}
.rate-box.global{{background:#1e293b;border:1px solid #475569}}
.rate-val{{font-size:2em;font-weight:800}}
.rate-label{{font-size:0.85em;color:#94a3b8}}

/* Filter bar */
.filter-bar{{margin:16px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.filter-bar label{{color:#94a3b8;font-size:0.85em}}
.filter-bar select,.filter-bar input{{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;font-size:0.85em}}

/* Footer */
.footer{{text-align:center;color:#475569;font-size:0.8em;margin-top:40px;padding:20px;border-top:1px solid #1e293b}}
</style>
</head>
<body>
<div class="container">

<h1>The Termination Cascade</h1>
<p class="subtitle">Who Kills Africa's Clinical Trials?</p>
<p class="date-stamp">ClinicalTrials.gov API v2 &middot; Generated {now} &middot; Project E: AfricaRCT</p>

<!-- ============================================================ -->
<!-- SUMMARY STATS                                                 -->
<!-- ============================================================ -->
<div class="cards">
<div class="card card-red">
<div class="card-value">{terminated_count}</div>
<div class="card-label">Terminated Trials</div>
</div>
<div class="card card-amber">
<div class="card-value">{withdrawn_count}</div>
<div class="card-label">Withdrawn Trials</div>
</div>
<div class="card card-purple">
<div class="card-value">{combined}</div>
<div class="card-label">Combined Stopped</div>
</div>
<div class="card card-blue">
<div class="card-value">{africa_rate}%</div>
<div class="card-label">Africa Termination Rate</div>
</div>
<div class="card card-red">
<div class="card-value">{global_decision_pct}%</div>
<div class="card-label">Global Decisions</div>
</div>
</div>

<!-- ============================================================ -->
<!-- RATE COMPARISON                                               -->
<!-- ============================================================ -->
<h2>Termination Rate: Africa vs Global</h2>
<div class="section">
<p style="color:#94a3b8;margin-bottom:16px">Rate = (Terminated + Withdrawn) / (Terminated + Withdrawn + Completed)</p>
<div class="rate-compare">
<div class="rate-box africa">
<div class="rate-val" style="color:#ef4444">{africa_rate}%</div>
<div class="rate-label">Africa</div>
</div>
<div style="font-size:2em;color:#475569">vs</div>
<div class="rate-box global">
<div class="rate-val" style="color:#60a5fa">{global_rate}%</div>
<div class="rate-label">Global Average</div>
</div>
</div>
<p style="margin-top:12px;color:#94a3b8;font-size:0.9em;text-align:center">
Africa: {combined:,} stopped / {combined + completed_count:,} total &middot;
Global: {results['global_terminated'] + results['global_withdrawn']:,} stopped / {results['global_terminated'] + results['global_withdrawn'] + results['global_completed']:,} total
</p>
</div>

<!-- ============================================================ -->
<!-- REASONS FOR TERMINATION                                       -->
<!-- ============================================================ -->
<h2>Reasons for Termination</h2>
<div class="section">
{class_chart_html}
<p style="margin-top:12px;color:#64748b;font-size:0.85em">
Global decision = foreign pharma/academic sponsor + >10 sites (multinational, terminated by HQ).
Local failure = single-site or few sites (logistics/funding).
</p>
</div>

<!-- ============================================================ -->
<!-- THE GLOBAL KILL SWITCH                                        -->
<!-- ============================================================ -->
<div class="kill-banner">
<h2>&#x1F6A8; The Global Kill Switch</h2>
<p>These {len(global_kills)} trials were multinational programs terminated by a headquarters decision.
Africa had no say -- the trial was killed globally, and African sites were collateral.</p>
</div>

<div class="section">
<h3>Multinational Trials Terminated by HQ Decision ({len(global_kills)} trials)</h3>
<div class="table-wrap table-wrap-xl">
<table>
<thead><tr>
<th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sites</th><th>Enrollment</th><th>Drug</th><th>Why Stopped</th>
</tr></thead>
<tbody>
{kill_table_html}
</tbody>
</table>
</div>
</div>

<!-- ============================================================ -->
<!-- SPONSOR BREAKDOWN                                             -->
<!-- ============================================================ -->
<h2>Who Terminates the Most in Africa?</h2>
<div class="section">
<h3>Top 15 Sponsors by Terminated/Withdrawn Count</h3>
{sponsor_chart_html}
<h3 style="margin-top:20px">By Sponsor Class</h3>
<p style="color:#cbd5e1">{sponsor_class_html}</p>
</div>

<!-- ============================================================ -->
<!-- DRUGS AFRICA LOST                                             -->
<!-- ============================================================ -->
<h2>Drugs Africa Lost</h2>
<div class="section">
<p style="color:#94a3b8;margin-bottom:12px">Interventions from terminated/withdrawn trials -- treatments that were being tested but stopped before Africa could benefit.</p>
<div class="table-wrap">
<table>
<thead><tr><th>Intervention</th><th>Terminated Trials</th></tr></thead>
<tbody>
{drug_table_html}
</tbody>
</table>
</div>
</div>

<!-- ============================================================ -->
<!-- CONDITIONS AFFECTED                                           -->
<!-- ============================================================ -->
<h2>Conditions Affected</h2>
<div class="section">
<div class="table-wrap">
<table>
<thead><tr><th>Condition</th><th>Terminated Trials</th></tr></thead>
<tbody>
{condition_table_html}
</tbody>
</table>
</div>
</div>

<!-- ============================================================ -->
<!-- PHASE DISTRIBUTION                                            -->
<!-- ============================================================ -->
<h2>Phase Distribution of Stopped Trials</h2>
<div class="section">
<p style="color:#cbd5e1">{phase_html}</p>
</div>

<!-- ============================================================ -->
<!-- TIMELINE                                                      -->
<!-- ============================================================ -->
<h2>Timeline: Are Terminations Increasing?</h2>
<div class="section">
<p style="color:#94a3b8;margin-bottom:12px">Number of terminated/withdrawn trials by start year</p>
{timeline_chart_html}
</div>

<!-- ============================================================ -->
<!-- FULL TRIAL TABLE                                              -->
<!-- ============================================================ -->
<h2>Full Trial-Level Table ({len(all_trials)} trials)</h2>
<div class="section">
<div class="filter-bar">
<label for="classFilter">Filter by classification:</label>
<select id="classFilter" onchange="filterTable()">
<option value="all">All</option>
<option value="Global decision">Global decision</option>
<option value="Local failure">Local failure</option>
<option value="Safety signal">Safety signal</option>
<option value="Futility">Futility</option>
<option value="Unknown">Unknown</option>
</select>
<label for="searchFilter" style="margin-left:12px">Search:</label>
<input id="searchFilter" type="text" placeholder="NCT ID, sponsor, title..." oninput="filterTable()" style="width:200px">
</div>
<div class="table-wrap table-wrap-xl">
<table id="trialTable">
<thead><tr>
<th>NCT ID</th><th>Title</th><th>Sponsor</th><th>Sites</th><th>Phase</th><th>Classification</th><th>Drug</th><th>Why Stopped</th>
</tr></thead>
<tbody>
{trial_table_html}
</tbody>
</table>
</div>
</div>

<div class="footer">
<p>The Termination Cascade &middot; Project E: AfricaRCT &middot; Data: ClinicalTrials.gov API v2</p>
<p>Generated {now} &middot; {len(all_trials)} trials analysed</p>
</div>

</div><!-- /.container -->

<script>
function filterTable() {{
    var classVal = document.getElementById('classFilter').value.toLowerCase();
    var searchVal = document.getElementById('searchFilter').value.toLowerCase();
    var rows = document.querySelectorAll('#trialTable tbody tr');
    for (var i = 0; i < rows.length; i++) {{
        var row = rows[i];
        var text = row.textContent.toLowerCase();
        var classCell = row.cells[5] ? row.cells[5].textContent.toLowerCase() : '';
        var classMatch = (classVal === 'all' || classCell.indexOf(classVal) >= 0);
        var searchMatch = (!searchVal || text.indexOf(searchVal) >= 0);
        row.style.display = (classMatch && searchMatch) ? '' : 'none';
    }}
}}
</script>
</body>
</html>"""
    return html


# -- Main ---------------------------------------------------------------------
def main():
    """Entry point."""
    print("=" * 60)
    print("  The Termination Cascade -- Who Kills Africa's Trials")
    print("  Project E: AfricaRCT")
    print("=" * 60)

    # Collect data
    data = collect_data()

    # Analyse
    print("\nRunning analysis...")
    results = run_analysis(data)

    # Summary
    print(f"\n--- SUMMARY ---")
    print(f"Terminated: {results['terminated_count']}")
    print(f"Withdrawn:  {results['withdrawn_count']}")
    print(f"Combined:   {results['combined_count']}")
    print(f"Completed:  {results['completed_count']}")
    print(f"Africa termination rate: {results['africa_rate']}%")
    print(f"Global termination rate: {results['global_rate']}%")
    print(f"Global decisions: {results['global_decision_pct']}% of stopped trials")
    print(f"\nClassification breakdown:")
    for cls, cnt in sorted(results["classification_counts"].items(), key=lambda x: -x[1]):
        print(f"  {cls}: {cnt}")

    # Generate HTML
    print(f"\nGenerating HTML dashboard...")
    html = generate_html(results)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard written to {OUTPUT_HTML}")
    print(f"File size: {len(html):,} bytes")

    # Also save analysis JSON
    analysis_path = DATA_DIR / "termination_cascade_analysis.json"
    with open(analysis_path, "w", encoding="utf-8") as f:
        # Make serializable
        serializable = {}
        for k, v in results.items():
            if k == "all_stopped_trials":
                serializable[k] = f"[{len(v)} trials - see main JSON cache]"
            elif k == "global_kill_trials":
                serializable[k] = f"[{len(v)} trials]"
            else:
                serializable[k] = v
        json.dump(serializable, f, indent=2, default=str)
    print(f"Analysis summary written to {analysis_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
