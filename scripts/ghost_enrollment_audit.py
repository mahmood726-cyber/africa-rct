#!/usr/bin/env python
"""Ghost Enrollment Audit — Project B: AfricaRCT

Identifies mega-trials (21-100 sites) and ghost trials (>100 sites) where
Uganda is virtually invisible, computes estimated enrollment contribution,
and generates an interactive HTML dashboard.

Data source: ClinicalTrials.gov API v2 (cached in data/uganda_collected_data.json)
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import math
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CACHE_PATH = Path(__file__).parent / "data" / "uganda_collected_data.json"
OUTPUT_HTML = Path(__file__).parent / "ghost-enrollment-audit.html"

CT_API_BASE = "https://clinicaltrials.gov/api/v2/studies"
CT_PARAMS = {
    "query.locn": "Uganda",
    "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    "pageSize": "200",
    "fields": "NCTId,BriefTitle,LeadSponsorName,LeadSponsorClass,"
              "OverallStatus,Phase,EnrollmentCount,StartDate,"
              "Condition,LocationCountry",
}

# WHO Essential Medicines List 2023 — drugs commonly tested in ghost trials
WHO_EML_DRUGS = {
    # Oncology
    "atezolizumab", "bevacizumab", "trastuzumab", "pertuzumab",
    "tamoxifen", "letrozole", "anastrozole", "cyclophosphamide",
    "doxorubicin", "paclitaxel", "carboplatin", "cisplatin",
    "methotrexate", "fluorouracil", "imatinib",
    # HIV/Antiretrovirals
    "dolutegravir", "cabotegravir", "tenofovir", "emtricitabine",
    "lamivudine", "efavirenz", "lopinavir", "ritonavir",
    "atazanavir", "darunavir", "zidovudine", "nevirapine",
    "abacavir", "raltegravir",
    # Cardiovascular
    "rivaroxaban", "warfarin", "aspirin", "ticagrelor",
    "enalapril", "amlodipine", "losartan", "atenolol",
    "pitavastatin", "atorvastatin", "simvastatin",
    # TB
    "rifapentine", "rifampicin", "isoniazid", "pyrazinamide",
    "ethambutol", "moxifloxacin", "bedaquiline", "pretomanid",
    "linezolid", "delamanid",
    # COVID-19
    "remdesivir", "baricitinib", "tocilizumab", "dexamethasone",
    # Sickle cell
    "hydroxyurea", "voxelotor",
    # Malaria
    "artemether", "lumefantrine", "artesunate", "chloroquine",
    "mefloquine",
    # Other
    "misoprostol", "oxytocin", "morphine", "ibuprofen",
}

# Drug keywords extracted from ghost trial titles (manual mapping)
GHOST_DRUG_MAP = {
    "NCT04961996": ("giredestrant", False),
    "NCT04873362": ("atezolizumab", True),
    "NCT00867048": ("antiretroviral therapy", True),
    "NCT05296798": ("giredestrant + palbociclib", False),
    "NCT05305547": ("ensitrelvir (S-217622)", False),
    "NCT05894239": ("inavolisib + pertuzumab/trastuzumab", False),
    "NCT05605093": ("multiple antivirals", True),
    "NCT06612268": ("etavopivat", False),
    "NCT05904886": ("atezolizumab + bevacizumab + tiragolumab", False),
    "NCT02832544": ("rivaroxaban", True),
    "NCT02344290": ("pitavastatin", True),
    "NCT04501978": ("multiple COVID therapeutics", True),
    "NCT05780437": ("tixagevimab/cilgavimab (AZD7442)", False),
    "NCT05780424": ("BRII-196/BRII-198", False),
    "NCT05780281": ("sotrovimab (VIR-7831)", False),
    "NCT05780463": ("ensovibep (MP0420)", False),
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def fetch_from_api():
    """Fetch all Uganda interventional trials from ClinicalTrials.gov API v2."""
    print("Cache not found. Fetching from ClinicalTrials.gov API v2...")
    all_trials = []
    page_token = None

    while True:
        params = dict(CT_PARAMS)
        if page_token:
            params["pageToken"] = page_token

        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{CT_API_BASE}?{query}"

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        studies = data.get("studies", [])
        for s in studies:
            proto = s.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            design_mod = proto.get("designModule", {})
            cond_mod = proto.get("conditionsModule", {})
            loc_mod = proto.get("contactsLocationsModule", {})

            lead = sponsor_mod.get("leadSponsor", {})
            locations = loc_mod.get("locations", [])
            phases_raw = (design_mod.get("phases") or ["NA"])
            enrollment_info = design_mod.get("enrollmentInfo", {})

            all_trials.append({
                "nct_id": ident.get("nctId", ""),
                "title": ident.get("briefTitle", ""),
                "sponsor": lead.get("name", ""),
                "sponsor_class": lead.get("class", "OTHER"),
                "status": status_mod.get("overallStatus", "UNKNOWN"),
                "phases": phases_raw,
                "enrollment": enrollment_info.get("count", 0) or 0,
                "start_date": status_mod.get("startDateStruct", {}).get("date", ""),
                "conditions": cond_mod.get("conditions", []),
                "locations_count": len(locations),
            })

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    # Cache the result
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache_data = {
        "meta": {
            "date": datetime.now().isoformat(),
            "api": "ClinicalTrials.gov API v2",
        },
        "uganda_total": len(all_trials),
        "sample_trials": all_trials,
    }
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)
    print(f"Cached {len(all_trials)} trials to {CACHE_PATH}")
    return all_trials


def load_trials():
    """Load trials from cache or fetch from API."""
    if CACHE_PATH.exists():
        print(f"Loading cached data from {CACHE_PATH}")
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("sample_trials", [])
    else:
        return fetch_from_api()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify_tier(locations_count):
    """Classify trial by site tier."""
    if locations_count is None:
        locations_count = 0
    if locations_count <= 1:
        return "Single-site"
    elif locations_count <= 5:
        return "Small multi"
    elif locations_count <= 20:
        return "Regional"
    elif locations_count <= 100:
        return "Mega-trial"
    else:
        return "Ghost"


def estimate_uganda_pct(enrollment, locations_count):
    """Estimate Uganda's enrollment contribution (rough proxy: 1/sites)."""
    if not locations_count or locations_count <= 0:
        return 0.0, 0
    est = enrollment / locations_count
    pct = (1.0 / locations_count) * 100
    return pct, round(est)


def short_title(title, max_len=65):
    """Truncate title to max length."""
    if len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def drug_on_eml(drug_str):
    """Check if any word in the drug string matches WHO EML."""
    words = drug_str.lower().replace("/", " ").replace("+", " ").split()
    for w in words:
        w_clean = w.strip("(),")
        if w_clean in WHO_EML_DRUGS:
            return True
    return False


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def run_analysis(trials):
    """Run the full ghost enrollment analysis."""
    results = {}

    # Tier counts
    tier_counts = {"Single-site": 0, "Small multi": 0, "Regional": 0,
                   "Mega-trial": 0, "Ghost": 0}
    for t in trials:
        tier = classify_tier(t.get("locations_count", 0))
        tier_counts[tier] += 1

    results["tier_counts"] = tier_counts
    results["total"] = len(trials)

    # Mega + Ghost trials
    mega_ghost = []
    for t in trials:
        lc = t.get("locations_count", 0) or 0
        if lc >= 21:
            tier = classify_tier(lc)
            pct, est_ug = estimate_uganda_pct(t.get("enrollment", 0) or 0, lc)
            drug_info = GHOST_DRUG_MAP.get(t["nct_id"], ("Unknown", None))
            drug_name = drug_info[0]
            on_eml = drug_info[1]
            if on_eml is None:
                on_eml = drug_on_eml(drug_name)

            mega_ghost.append({
                "nct_id": t["nct_id"],
                "title": t["title"],
                "short_title": short_title(t["title"]),
                "sponsor": t.get("sponsor", "Unknown"),
                "sponsor_class": t.get("sponsor_class", "OTHER"),
                "status": t.get("status", "UNKNOWN"),
                "phases": t.get("phases", ["NA"]),
                "enrollment": t.get("enrollment", 0) or 0,
                "locations_count": lc,
                "conditions": t.get("conditions", []),
                "tier": tier,
                "uganda_pct": round(pct, 2),
                "est_uganda_enrollment": est_ug,
                "drug_name": drug_name,
                "drug_on_eml": on_eml,
            })

    mega_ghost.sort(key=lambda x: -x["locations_count"])
    results["mega_ghost_trials"] = mega_ghost

    # Ghost-specific stats
    ghost_trials = [t for t in mega_ghost if t["tier"] == "Ghost"]
    mega_trials = [t for t in mega_ghost if t["tier"] == "Mega-trial"]

    results["ghost_count"] = len(ghost_trials)
    results["mega_count"] = len(mega_trials)

    # Enrollment sums
    ghost_total_enroll = sum(t["enrollment"] for t in ghost_trials)
    ghost_est_ug = sum(t["est_uganda_enrollment"] for t in ghost_trials)
    mega_total_enroll = sum(t["enrollment"] for t in mega_trials)
    mega_est_ug = sum(t["est_uganda_enrollment"] for t in mega_trials)

    results["ghost_total_enrollment"] = ghost_total_enroll
    results["ghost_est_uganda"] = ghost_est_ug
    results["mega_total_enrollment"] = mega_total_enroll
    results["mega_est_uganda"] = mega_est_ug

    if ghost_total_enroll > 0:
        results["ghost_uganda_pct"] = round(ghost_est_ug / ghost_total_enroll * 100, 2)
    else:
        results["ghost_uganda_pct"] = 0

    # Sponsor breakdown for ghost
    ghost_sponsors = {}
    for t in ghost_trials:
        cls = t["sponsor_class"]
        ghost_sponsors[cls] = ghost_sponsors.get(cls, 0) + 1
    results["ghost_sponsor_breakdown"] = ghost_sponsors

    mega_sponsors = {}
    for t in mega_trials:
        cls = t["sponsor_class"]
        mega_sponsors[cls] = mega_sponsors.get(cls, 0) + 1
    results["mega_sponsor_breakdown"] = mega_sponsors

    # Drug accessibility
    eml_yes = [t for t in ghost_trials if t["drug_on_eml"]]
    eml_no = [t for t in ghost_trials if not t["drug_on_eml"]]
    results["ghost_eml_count"] = len(eml_yes)
    results["ghost_non_eml_count"] = len(eml_no)
    results["ghost_eml_trials"] = eml_yes
    results["ghost_non_eml_trials"] = eml_no

    # Terminated ghost+mega
    terminated = [t for t in mega_ghost if t["status"] in
                  ("TERMINATED", "WITHDRAWN", "SUSPENDED")]
    results["terminated_trials"] = terminated

    # Phase distribution for mega+ghost
    phase_dist = {}
    for t in mega_ghost:
        for p in t["phases"]:
            phase_dist[p] = phase_dist.get(p, 0) + 1
    results["mega_ghost_phases"] = phase_dist

    # Condition analysis for ghost
    cond_counts = {}
    for t in ghost_trials:
        for c in t["conditions"]:
            cond_counts[c] = cond_counts.get(c, 0) + 1
    results["ghost_conditions"] = dict(
        sorted(cond_counts.items(), key=lambda x: -x[1]))

    return results


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------
def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def status_color(status):
    """Return CSS color for trial status."""
    return {
        "COMPLETED": "#4ade80",
        "ACTIVE_NOT_RECRUITING": "#60a5fa",
        "RECRUITING": "#22d3ee",
        "NOT_YET_RECRUITING": "#a78bfa",
        "TERMINATED": "#f87171",
        "WITHDRAWN": "#fb923c",
        "SUSPENDED": "#fbbf24",
        "UNKNOWN": "#94a3b8",
    }.get(status, "#94a3b8")


def tier_color(tier):
    """Return CSS color for trial tier."""
    return {
        "Ghost": "#f87171",
        "Mega-trial": "#fb923c",
        "Regional": "#fbbf24",
        "Small multi": "#4ade80",
        "Single-site": "#60a5fa",
    }.get(tier, "#94a3b8")


def sponsor_class_label(cls):
    """Human-readable sponsor class."""
    return {
        "INDUSTRY": "Pharma/Industry",
        "NIH": "NIH/US Gov",
        "OTHER": "Academic/NGO",
        "OTHER_GOV": "Government (non-US)",
        "FED": "US Federal",
        "NETWORK": "Research Network",
    }.get(cls, cls)


def generate_html(results):
    """Generate the full HTML dashboard."""
    tc = results["tier_counts"]
    mg = results["mega_ghost_trials"]
    ghost_trials = [t for t in mg if t["tier"] == "Ghost"]
    mega_trials = [t for t in mg if t["tier"] == "Mega-trial"]

    # Build table rows for ALL mega+ghost trials
    table_rows = []
    for t in mg:
        sc = status_color(t["status"])
        tc_color = tier_color(t["tier"])
        eml_badge = ('<span style="color:#4ade80">EML</span>'
                     if t["drug_on_eml"]
                     else '<span style="color:#f87171">Not EML</span>')
        phase_str = "/".join(p.replace("PHASE", "Ph") for p in t["phases"])
        status_label = t["status"].replace("_", " ").title()

        table_rows.append(f"""<tr style="border-bottom:1px solid #1e293b">
<td style="color:#22d3ee;font-family:monospace;white-space:nowrap">
  <a href="https://clinicaltrials.gov/study/{escape_html(t['nct_id'])}"
     target="_blank" style="color:#22d3ee;text-decoration:none">{escape_html(t['nct_id'])}</a>
</td>
<td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
    title="{escape_html(t['title'])}">{escape_html(t['short_title'])}</td>
<td style="font-size:0.85em">{escape_html(t['sponsor'][:35])}</td>
<td style="text-align:center;color:{tc_color};font-weight:700">{t['locations_count']}</td>
<td style="text-align:center;color:{tc_color}">{t['uganda_pct']:.1f}%</td>
<td style="font-size:0.85em">{escape_html(t['drug_name'])} {eml_badge}</td>
<td style="text-align:center">{phase_str}</td>
<td style="color:{sc};text-align:center;font-size:0.85em">{status_label}</td>
<td style="text-align:center;font-weight:700;color:{tc_color}">{t['tier']}</td>
</tr>""")

    table_html = "\n".join(table_rows)

    # Bar chart data for site tier
    tier_order = ["Single-site", "Small multi", "Regional", "Mega-trial", "Ghost"]
    tier_vals = [tc.get(t, 0) for t in tier_order]
    tier_colors = [tier_color(t) for t in tier_order]
    max_val = max(tier_vals) if tier_vals else 1

    bar_items = []
    for i, (label, val) in enumerate(zip(tier_order, tier_vals)):
        pct_bar = val / max_val * 100
        bar_items.append(f"""
<div style="display:flex;align-items:center;gap:12px;margin:8px 0">
  <div style="width:100px;text-align:right;color:{tier_colors[i]};font-weight:600;font-size:0.9em">{label}</div>
  <div style="flex:1;background:#1e293b;border-radius:6px;height:32px;position:relative;overflow:hidden">
    <div style="width:{pct_bar}%;height:100%;background:{tier_colors[i]};border-radius:6px;
                transition:width 0.6s"></div>
    <span style="position:absolute;left:12px;top:6px;font-weight:700;font-size:0.9em;color:#fff">
      {val} ({val/results['total']*100:.1f}%)</span>
  </div>
</div>""")
    bar_chart_html = "\n".join(bar_items)

    # Ghost sponsor pie data
    gs = results["ghost_sponsor_breakdown"]
    sponsor_items = []
    sponsor_colors = {"INDUSTRY": "#f87171", "NIH": "#60a5fa", "OTHER": "#4ade80",
                      "OTHER_GOV": "#fbbf24", "FED": "#a78bfa", "NETWORK": "#22d3ee"}
    for cls, count in sorted(gs.items(), key=lambda x: -x[1]):
        color = sponsor_colors.get(cls, "#94a3b8")
        sponsor_items.append(f"""
<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
  <div style="width:16px;height:16px;border-radius:50%;background:{color}"></div>
  <span style="color:{color};font-weight:600">{sponsor_class_label(cls)}</span>
  <span style="color:#94a3b8">: {count} trial{'s' if count != 1 else ''}</span>
</div>""")
    sponsor_html = "\n".join(sponsor_items)

    # Drug accessibility section
    eml_items = []
    for t in results["ghost_eml_trials"]:
        eml_items.append(f'<li style="color:#4ade80;margin:4px 0">'
                         f'{escape_html(t["drug_name"])} '
                         f'<span style="color:#94a3b8">({escape_html(t["nct_id"])} '
                         f'- {escape_html(t["conditions"][0] if t["conditions"] else "N/A")})</span></li>')
    non_eml_items = []
    for t in results["ghost_non_eml_trials"]:
        non_eml_items.append(f'<li style="color:#f87171;margin:4px 0">'
                             f'{escape_html(t["drug_name"])} '
                             f'<span style="color:#94a3b8">({escape_html(t["nct_id"])} '
                             f'- {escape_html(t["conditions"][0] if t["conditions"] else "N/A")})</span></li>')

    # Terminated section
    term_rows = []
    for t in results["terminated_trials"]:
        tc_c = tier_color(t["tier"])
        term_rows.append(f"""<tr style="border-bottom:1px solid #1e293b">
<td style="color:#22d3ee;font-family:monospace">
  <a href="https://clinicaltrials.gov/study/{escape_html(t['nct_id'])}" target="_blank"
     style="color:#22d3ee;text-decoration:none">{escape_html(t['nct_id'])}</a></td>
<td>{escape_html(t['short_title'])}</td>
<td style="text-align:center;color:{tc_c}">{t['locations_count']}</td>
<td>{escape_html(t['sponsor'][:35])}</td>
<td style="color:#f87171">{t['status'].replace('_',' ').title()}</td>
<td style="color:{tc_c}">{t['tier']}</td>
</tr>""")
    term_html = "\n".join(term_rows) if term_rows else '<tr><td colspan="6" style="color:#94a3b8;text-align:center">None found</td></tr>'

    # Condition breakdown for ghost trials
    cond_items = []
    for cond, cnt in results["ghost_conditions"].items():
        cond_items.append(f'<span style="display:inline-block;background:#1e293b;'
                          f'padding:4px 12px;border-radius:20px;margin:4px;'
                          f'font-size:0.9em;color:#e2e8f0">{escape_html(cond)} '
                          f'<strong style="color:#22d3ee">({cnt})</strong></span>')
    cond_html = "\n".join(cond_items)

    # Full HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ghost Enrollment Audit: Uganda Clinical Trials</title>
<style>
:root {{
  --bg: #0a0e17;
  --card: #111827;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #22d3ee;
  --danger: #f87171;
  --warn: #fb923c;
  --success: #4ade80;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  line-height: 1.6;
  min-height: 100vh;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
h1 {{
  font-size: 2.2em;
  background: linear-gradient(135deg, #f87171, #fb923c, #fbbf24);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 8px;
}}
h2 {{
  font-size: 1.5em;
  color: var(--accent);
  margin: 32px 0 16px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--border);
}}
h3 {{
  font-size: 1.15em;
  color: var(--warn);
  margin: 20px 0 10px;
}}
.subtitle {{
  color: var(--muted);
  font-size: 1.1em;
  margin-bottom: 24px;
}}
.stats-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin: 20px 0;
}}
.stat-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  text-align: center;
}}
.stat-number {{
  font-size: 2.5em;
  font-weight: 800;
  line-height: 1.1;
}}
.stat-label {{
  font-size: 0.85em;
  color: var(--muted);
  margin-top: 4px;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  margin: 16px 0;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88em;
}}
th {{
  background: #1e293b;
  color: var(--accent);
  padding: 10px 8px;
  text-align: left;
  font-weight: 600;
  position: sticky;
  top: 0;
  z-index: 2;
}}
td {{
  padding: 8px;
  vertical-align: middle;
}}
.table-wrap {{
  max-height: 800px;
  overflow-y: auto;
  border-radius: 8px;
  border: 1px solid var(--border);
}}
.table-wrap::-webkit-scrollbar {{ width: 8px; }}
.table-wrap::-webkit-scrollbar-track {{ background: var(--card); }}
.table-wrap::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 4px; }}
.invisible-callout {{
  background: linear-gradient(135deg, #1a0a0a, #1a1a0a);
  border: 2px solid var(--danger);
  border-radius: 12px;
  padding: 28px;
  margin: 20px 0;
}}
.invisible-number {{
  font-size: 4em;
  font-weight: 900;
  color: var(--danger);
  line-height: 1;
}}
.invisible-subtext {{
  font-size: 1.3em;
  color: var(--warn);
  margin-top: 8px;
}}
.eml-split {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}}
@media (max-width: 800px) {{
  .eml-split {{ grid-template-columns: 1fr; }}
  .stats-grid {{ grid-template-columns: 1fr 1fr; }}
}}
a {{ color: var(--accent); }}
a:hover {{ text-decoration: underline; }}
.footer {{
  text-align: center;
  color: var(--muted);
  font-size: 0.85em;
  margin-top: 48px;
  padding: 20px;
  border-top: 1px solid var(--border);
}}
.filter-bar {{
  display: flex;
  gap: 8px;
  margin: 12px 0;
  flex-wrap: wrap;
}}
.filter-btn {{
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 6px 14px;
  border-radius: 20px;
  cursor: pointer;
  font-size: 0.85em;
  transition: all 0.2s;
}}
.filter-btn:hover, .filter-btn.active {{
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
}}
</style>
</head>
<body>
<div class="container">

<h1>Ghost Enrollment Audit</h1>
<p class="subtitle">Where Uganda becomes invisible: mega-trials with 21-622 sites
where Ugandan patients contribute &lt;1% of enrollment</p>

<!-- Section 1: Summary Stats -->
<h2>1. Summary Statistics</h2>
<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-number" style="color:var(--accent)">{results['total']}</div>
    <div class="stat-label">Total Uganda Trials</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--danger)">{results['ghost_count']}</div>
    <div class="stat-label">Ghost Trials (&gt;100 sites)</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--warn)">{results['mega_count']}</div>
    <div class="stat-label">Mega-Trials (21-100 sites)</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--success)">{len(mg)}</div>
    <div class="stat-label">Total Mega + Ghost</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--danger)">{results['ghost_total_enrollment']:,}</div>
    <div class="stat-label">Ghost Total Enrollment</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--warn)">{results['ghost_est_uganda']:,}</div>
    <div class="stat-label">Est. Uganda Ghost Enrollment</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--danger)">{results['ghost_uganda_pct']}%</div>
    <div class="stat-label">Uganda % of Ghost Enrollment</div>
  </div>
  <div class="stat-card">
    <div class="stat-number" style="color:var(--warn)">{results['mega_est_uganda']:,}</div>
    <div class="stat-label">Est. Uganda Mega Enrollment</div>
  </div>
</div>

<!-- Section 2: Full Trial Table -->
<h2>2. Complete Mega + Ghost Trial Registry ({len(mg)} trials)</h2>
<p style="color:var(--muted);margin-bottom:8px">
  All trials with &ge;21 sites that include a Ugandan location.
  Uganda Pct = estimated enrollment contribution (1 / total sites).
</p>

<div class="filter-bar">
  <button class="filter-btn active" onclick="filterTable('all')">All ({len(mg)})</button>
  <button class="filter-btn" onclick="filterTable('Ghost')" style="border-color:#f87171">
    Ghost ({results['ghost_count']})</button>
  <button class="filter-btn" onclick="filterTable('Mega-trial')" style="border-color:#fb923c">
    Mega ({results['mega_count']})</button>
  <button class="filter-btn" onclick="filterTable('INDUSTRY')">Industry</button>
  <button class="filter-btn" onclick="filterTable('NIH')">NIH</button>
  <button class="filter-btn" onclick="filterTable('TERMINATED')">Terminated</button>
</div>

<div class="table-wrap">
<table id="trialTable">
<thead>
<tr>
  <th>NCT ID</th>
  <th>Title</th>
  <th>Sponsor</th>
  <th>Sites</th>
  <th>Uganda %</th>
  <th>Drug / EML</th>
  <th>Phase</th>
  <th>Status</th>
  <th>Tier</th>
</tr>
</thead>
<tbody>
{table_html}
</tbody>
</table>
</div>

<!-- Section 3: Site Tier Distribution -->
<h2>3. Site-Tier Distribution (All 783 Trials)</h2>
<div class="card">
{bar_chart_html}
</div>

<!-- Section 4: The Invisible Patients -->
<h2>4. The Invisible Patients</h2>
<div class="invisible-callout">
  <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
    <div>
      <div class="invisible-number">{results['ghost_uganda_pct']}%</div>
      <div class="invisible-subtext">Uganda's share of ghost-trial enrollment</div>
    </div>
    <div style="flex:1;min-width:300px">
      <p style="font-size:1.1em;margin-bottom:12px">
        Across <strong style="color:var(--danger)">{results['ghost_count']} ghost trials</strong>
        enrolling <strong>{results['ghost_total_enrollment']:,}</strong> participants worldwide,
        Uganda contributes an estimated <strong style="color:var(--danger)">{results['ghost_est_uganda']:,}</strong> patients.
      </p>
      <p style="color:var(--muted)">
        These patients bear the risks of experimental treatment but their country has
        virtually no influence on trial design, endpoint selection, or post-trial drug pricing.
        The median ghost trial has <strong>{int(sorted([t['locations_count'] for t in ghost_trials])[len(ghost_trials)//2]) if ghost_trials else 0}</strong> sites,
        making Uganda one data point among hundreds.
      </p>
      <p style="margin-top:12px">
        Combined with <strong style="color:var(--warn)">{results['mega_count']} mega-trials</strong>
        (est. <strong>{results['mega_est_uganda']:,}</strong> Ugandan participants),
        a total of <strong>{results['ghost_est_uganda'] + results['mega_est_uganda']:,}</strong>
        Ugandans are enrolled in trials where they constitute &lt;5% of the study population.
      </p>
    </div>
  </div>
</div>

<!-- Section 5: Drug Accessibility -->
<h2>5. Drug Accessibility Analysis (Ghost Trials)</h2>
<p style="color:var(--muted);margin-bottom:12px">
  Of {results['ghost_count']} ghost-trial drugs tested with Ugandan participants,
  how many are actually available in Uganda?
</p>
<div class="eml-split">
  <div class="card" style="border-color:#4ade80">
    <h3 style="color:#4ade80">On WHO Essential Medicines List ({results['ghost_eml_count']})</h3>
    <p style="color:var(--muted);font-size:0.9em;margin-bottom:8px">
      Drugs with a pathway to availability in LMICs</p>
    <ul style="list-style:none;padding:0">
      {"".join(eml_items)}
    </ul>
  </div>
  <div class="card" style="border-color:#f87171">
    <h3 style="color:#f87171">NOT on WHO EML ({results['ghost_non_eml_count']})</h3>
    <p style="color:var(--muted);font-size:0.9em;margin-bottom:8px">
      Likely unavailable or unaffordable in Uganda post-trial</p>
    <ul style="list-style:none;padding:0">
      {"".join(non_eml_items)}
    </ul>
  </div>
</div>

<!-- Section 6: Sponsor Patterns -->
<h2>6. Sponsor Patterns for Ghost Trials</h2>
<div class="card">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
    <div>
      <h3>Ghost Trials ({results['ghost_count']})</h3>
      {sponsor_html}
      <p style="color:var(--muted);margin-top:12px;font-size:0.9em">
        Hoffmann-La Roche alone sponsors {sum(1 for t in ghost_trials if 'Roche' in t['sponsor'])}
        of {results['ghost_count']} ghost trials (oncology-focused).</p>
    </div>
    <div>
      <h3>Mega-Trials ({results['mega_count']})</h3>
      {"".join(f'''
<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
  <div style="width:16px;height:16px;border-radius:50%;background:{sponsor_colors.get(cls, '#94a3b8')}"></div>
  <span style="color:{sponsor_colors.get(cls, '#94a3b8')};font-weight:600">{sponsor_class_label(cls)}</span>
  <span style="color:#94a3b8">: {cnt} trial{"s" if cnt != 1 else ""}</span>
</div>''' for cls, cnt in sorted(results['mega_sponsor_breakdown'].items(), key=lambda x: -x[1]))}
    </div>
  </div>
</div>

<!-- Section 7: Terminated Ghost Trials -->
<h2>7. Terminated Mega/Ghost Trials</h2>
<p style="color:var(--muted);margin-bottom:12px">
  Trials killed by global decisions where Ugandan sites had no say
  ({len(results['terminated_trials'])} found)
</p>
<div class="card">
<table>
<thead>
<tr>
  <th>NCT ID</th>
  <th>Title</th>
  <th>Sites</th>
  <th>Sponsor</th>
  <th>Status</th>
  <th>Tier</th>
</tr>
</thead>
<tbody>
{term_html}
</tbody>
</table>
</div>

<!-- Section 8: Ghost Conditions -->
<h2>8. Disease Areas in Ghost Trials</h2>
<div class="card">
  <p style="color:var(--muted);margin-bottom:12px">
    Conditions studied in the {results['ghost_count']} ghost trials
    (not necessarily Uganda's priorities):</p>
  <div style="display:flex;flex-wrap:wrap;gap:4px">
    {cond_html}
  </div>
</div>

<div class="footer">
  <p>Ghost Enrollment Audit | AfricaRCT Project B | Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <p>Data: ClinicalTrials.gov API v2 | {results['total']} Uganda interventional trials</p>
  <p style="margin-top:8px">Enrollment estimates use 1/locations_count as a proxy for
  per-site enrollment. Actual enrollment may vary by site.</p>
</div>

</div>

<script>
function filterTable(filter) {{
  const rows = document.querySelectorAll('#trialTable tbody tr');
  const btns = document.querySelectorAll('.filter-btn');
  btns.forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  rows.forEach(row => {{
    const cells = row.querySelectorAll('td');
    if (cells.length < 9) return;
    const tier = cells[8].textContent.trim();
    const sponsor = cells[2].textContent.trim();
    const status = cells[7].textContent.trim().toUpperCase().replace(/ /g, '_');

    if (filter === 'all') {{
      row.style.display = '';
    }} else if (filter === 'Ghost' || filter === 'Mega-trial') {{
      row.style.display = tier === filter ? '' : 'none';
    }} else if (filter === 'INDUSTRY') {{
      const industrySponsors = ['Hoffmann-La Roche', 'Gilead', 'Sanofi', 'Novartis',
        'AstraZeneca', 'Merck', 'ViiV', 'Shionogi', 'Novo Nordisk', 'Cardurion',
        'Tibotec', 'Daiichi'];
      const isIndustry = industrySponsors.some(s => sponsor.includes(s));
      row.style.display = isIndustry ? '' : 'none';
    }} else if (filter === 'NIH') {{
      row.style.display = sponsor.includes('National Institute') ? '' : 'none';
    }} else if (filter === 'TERMINATED') {{
      row.style.display = status === 'TERMINATED' || status === 'WITHDRAWN' ||
                          status === 'SUSPENDED' ? '' : 'none';
    }}
  }});
}}
</script>

</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Ghost Enrollment Audit — AfricaRCT Project B")
    print("=" * 60)

    trials = load_trials()
    print(f"Loaded {len(trials)} trials")

    results = run_analysis(trials)

    print(f"\nTier distribution:")
    for tier, count in results["tier_counts"].items():
        pct = count / results["total"] * 100
        print(f"  {tier:15s}: {count:4d} ({pct:.1f}%)")

    print(f"\nGhost trials (>100 sites): {results['ghost_count']}")
    print(f"Mega trials (21-100 sites): {results['mega_count']}")
    print(f"Ghost total enrollment: {results['ghost_total_enrollment']:,}")
    print(f"Estimated Uganda ghost enrollment: {results['ghost_est_uganda']:,}")
    print(f"Uganda % of ghost enrollment: {results['ghost_uganda_pct']}%")

    print(f"\nGhost sponsor breakdown: {results['ghost_sponsor_breakdown']}")
    print(f"Ghost EML drugs: {results['ghost_eml_count']}")
    print(f"Ghost non-EML drugs: {results['ghost_non_eml_count']}")
    print(f"Terminated mega/ghost: {len(results['terminated_trials'])}")

    html = generate_html(results)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML dashboard written to {OUTPUT_HTML}")
    print(f"File size: {os.path.getsize(OUTPUT_HTML):,} bytes")


if __name__ == "__main__":
    main()
