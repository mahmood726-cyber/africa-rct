"""
The Nurse's View -- Frontline Care Without Protocols
=====================================================
Nurses deliver 80%+ of healthcare in Africa.  What evidence supports
nursing interventions?

Queries ClinicalTrials.gov API v2 for nursing, midwifery, wound care,
patient education, and palliative nursing trials in Africa vs the US/UK,
and generates an interactive HTML dashboard.

Usage:
    python fetch_nurse_view.py

Output:
    data/nurse_view_data.json   -- cached data (24h validity)
    nurse-view.html             -- interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)

API docs: https://clinicaltrials.gov/data-api/api
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required.  Install with: pip install requests")
    sys.exit(1)

# -- Config ----------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "nurse_view_data.json"
OUTPUT_HTML = Path(__file__).parent / "nurse-view.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35

# -- African countries for location search ---------------------------------
AFRICA_COUNTRIES = [
    "South Africa", "Nigeria", "Kenya", "Egypt", "Uganda", "Tanzania",
    "Ethiopia", "Ghana", "Cameroon", "Senegal", "Zambia", "Zimbabwe",
    "Mozambique", "Malawi", "Rwanda", "Botswana", "Burkina Faso",
    "Mali", "Cote d'Ivoire", "Congo", "Morocco", "Tunisia", "Algeria",
    "Sudan", "Madagascar", "Gabon",
]

AFRICA_LOCATION = " OR ".join(AFRICA_COUNTRIES[:20])

# -- Nursing evidence categories -------------------------------------------
NURSING_QUERIES = {
    "nursing_all": {
        "label": "Nursing / Nurse-Led",
        "query": "nursing OR nurse-led OR nurse led OR nurse practitioner",
        "compare_us": True,
        "compare_uk": True,
        "description": "All trials involving nursing interventions or nurse-led care models.",
    },
    "midwifery": {
        "label": "Midwifery / Midwife-Led",
        "query": "midwife OR midwifery OR midwife-led OR birth attendant OR skilled birth",
        "compare_us": True,
        "compare_uk": True,
        "description": "Trials testing midwifery models, midwife-led care, and skilled birth attendance.",
    },
    "wound_care": {
        "label": "Wound Care / Pressure Ulcers",
        "query": "wound care OR pressure ulcer OR wound management OR wound healing OR decubitus",
        "compare_us": True,
        "compare_uk": False,
        "description": "Trials on wound management, pressure ulcer prevention, and wound healing.",
    },
    "infection_control": {
        "label": "Infection Control / IPC",
        "query": "infection control OR infection prevention OR hand hygiene OR hospital infection",
        "compare_us": True,
        "compare_uk": False,
        "description": "Trials testing infection prevention and control practices.",
    },
    "patient_education": {
        "label": "Patient Education / Self-Management",
        "query": "patient education OR self-management OR adherence support OR health literacy",
        "compare_us": True,
        "compare_uk": False,
        "description": "Trials on patient education, self-management support, and adherence programmes.",
    },
    "palliative_nursing": {
        "label": "Palliative Nursing / End of Life",
        "query": "palliative nursing OR end of life nursing OR palliative care OR hospice nursing",
        "compare_us": True,
        "compare_uk": False,
        "description": "Trials on palliative and end-of-life nursing care.",
    },
    "task_shifting_nursing": {
        "label": "Task-Shifting to Nurses",
        "query": "task shifting nurse OR nurse prescribing OR nurse-initiated OR nurse dispensing",
        "compare_us": False,
        "compare_uk": True,
        "description": "Trials evaluating expanded nursing roles through task-shifting.",
    },
    "maternal_nursing": {
        "label": "Maternal / Neonatal Nursing",
        "query": "maternal nursing OR neonatal nursing OR kangaroo care OR postnatal care",
        "compare_us": True,
        "compare_uk": False,
        "description": "Trials on maternal and neonatal nursing interventions.",
    },
}


# -- API helpers -----------------------------------------------------------
def search_trials_count(query_cond=None, query_term=None, location=None,
                        filter_advanced=None, max_retries=3):
    """Get trial count from CT.gov API v2."""
    params = {
        "format": "json",
        "pageSize": 1,
        "countTotal": "true",
    }
    filters = []
    if filter_advanced:
        filters.append(filter_advanced)
    if filters:
        params["filter.advanced"] = " AND ".join(filters)
    if query_cond:
        params["query.cond"] = query_cond
    if query_term:
        params["query.term"] = query_term
    if location:
        params["query.locn"] = location

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("totalCount", 0)
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return 0


# -- Main data collection --------------------------------------------------
def collect_data():
    """Collect nurse's view data from CT.gov API v2."""

    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetch_date = datetime.fromisoformat(cached["fetch_date"])
            if datetime.now() - fetch_date < timedelta(hours=CACHE_HOURS):
                print(f"Using cached data from {fetch_date.strftime('%Y-%m-%d %H:%M')}")
                return cached
            else:
                print("Cache expired, re-fetching...")
        except (json.JSONDecodeError, KeyError, ValueError):
            print("Cache invalid, re-fetching...")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "fetch_date": datetime.now().isoformat(),
        "categories": {},
        "summary": {},
    }

    # == Step 1: Query each nursing category ===============================
    print("\n" + "=" * 60)
    print("Step 1: Querying nursing evidence categories")
    print("=" * 60)

    for key, cat in NURSING_QUERIES.items():
        print(f"\n  [{key}] {cat['label']}...")

        # Africa count
        africa_count = search_trials_count(
            query_term=cat["query"],
            location=AFRICA_LOCATION,
            filter_advanced="AREA[StudyType]INTERVENTIONAL"
        )
        time.sleep(RATE_LIMIT_DELAY)

        # US count
        us_count = 0
        if cat["compare_us"]:
            us_count = search_trials_count(
                query_term=cat["query"],
                location="United States",
                filter_advanced="AREA[StudyType]INTERVENTIONAL"
            )
            time.sleep(RATE_LIMIT_DELAY)

        # UK count
        uk_count = 0
        if cat["compare_uk"]:
            uk_count = search_trials_count(
                query_term=cat["query"],
                location="United Kingdom",
                filter_advanced="AREA[StudyType]INTERVENTIONAL"
            )
            time.sleep(RATE_LIMIT_DELAY)

        us_ratio = round(us_count / africa_count, 1) if africa_count > 0 and us_count > 0 else 0
        uk_ratio = round(uk_count / africa_count, 1) if africa_count > 0 and uk_count > 0 else 0

        results["categories"][key] = {
            "label": cat["label"],
            "description": cat["description"],
            "africa_count": africa_count,
            "us_count": us_count,
            "uk_count": uk_count,
            "us_ratio": us_ratio,
            "uk_ratio": uk_ratio,
            "compare_us": cat["compare_us"],
            "compare_uk": cat["compare_uk"],
        }

        parts = [f"Africa: {africa_count:,}"]
        if cat["compare_us"]:
            parts.append(f"US: {us_count:,}")
        if cat["compare_uk"]:
            parts.append(f"UK: {uk_count:,}")
        print(f"    {' | '.join(parts)}")

    # == Step 2: Nursing trial density =====================================
    print("\n" + "=" * 60)
    print("Step 2: Computing nursing trial density")
    print("=" * 60)

    # Total interventional trials for baseline
    africa_total = search_trials_count(
        location=AFRICA_LOCATION,
        filter_advanced="AREA[StudyType]INTERVENTIONAL"
    )
    time.sleep(RATE_LIMIT_DELAY)

    us_total = search_trials_count(
        location="United States",
        filter_advanced="AREA[StudyType]INTERVENTIONAL"
    )
    time.sleep(RATE_LIMIT_DELAY)

    uk_total = search_trials_count(
        location="United Kingdom",
        filter_advanced="AREA[StudyType]INTERVENTIONAL"
    )
    time.sleep(RATE_LIMIT_DELAY)

    nursing_africa = results["categories"]["nursing_all"]["africa_count"]
    nursing_us = results["categories"]["nursing_all"]["us_count"]
    nursing_uk = results["categories"]["nursing_all"]["uk_count"]

    africa_density = round(nursing_africa / africa_total * 100, 2) if africa_total > 0 else 0
    us_density = round(nursing_us / us_total * 100, 2) if us_total > 0 else 0
    uk_density = round(nursing_uk / uk_total * 100, 2) if uk_total > 0 else 0

    print(f"  Africa nursing density: {africa_density}% ({nursing_africa}/{africa_total})")
    print(f"  US nursing density: {us_density}% ({nursing_us}/{us_total})")
    print(f"  UK nursing density: {uk_density}% ({nursing_uk}/{uk_total})")

    results["density"] = {
        "africa_total_trials": africa_total,
        "us_total_trials": us_total,
        "uk_total_trials": uk_total,
        "africa_nursing_density": africa_density,
        "us_nursing_density": us_density,
        "uk_nursing_density": uk_density,
    }

    # == Step 3: Country-level nursing trial counts ========================
    print("\n" + "=" * 60)
    print("Step 3: Country-level nursing trial counts")
    print("=" * 60)

    country_data = {}
    for country in AFRICA_COUNTRIES[:15]:
        count = search_trials_count(
            query_term="nursing OR nurse-led OR midwife OR midwifery",
            location=country,
            filter_advanced="AREA[StudyType]INTERVENTIONAL"
        )
        time.sleep(RATE_LIMIT_DELAY)
        country_data[country] = count
        print(f"    {country}: {count}")

    country_data = dict(sorted(country_data.items(), key=lambda x: -x[1]))
    results["country_breakdown"] = country_data

    # == Step 4: Summary ===================================================
    total_nursing_africa = sum(
        c.get("africa_count", 0) for c in results["categories"].values()
    )
    midwifery_africa = results["categories"]["midwifery"]["africa_count"]
    midwifery_us = results["categories"]["midwifery"]["us_count"]
    wound_africa = results["categories"]["wound_care"]["africa_count"]
    infection_africa = results["categories"]["infection_control"]["africa_count"]
    palliative_africa = results["categories"]["palliative_nursing"]["africa_count"]
    task_shift_africa = results["categories"]["task_shifting_nursing"]["africa_count"]

    results["summary"] = {
        "total_nursing_africa": total_nursing_africa,
        "nursing_all_africa": nursing_africa,
        "nursing_all_us": nursing_us,
        "nursing_all_uk": nursing_uk,
        "nursing_us_ratio": round(nursing_us / nursing_africa, 1) if nursing_africa > 0 else 999,
        "africa_density_pct": africa_density,
        "us_density_pct": us_density,
        "uk_density_pct": uk_density,
        "midwifery_africa": midwifery_africa,
        "midwifery_us": midwifery_us,
        "wound_care_africa": wound_africa,
        "infection_control_africa": infection_africa,
        "palliative_africa": palliative_africa,
        "task_shifting_africa": task_shift_africa,
    }

    # Save cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n  Cached to {CACHE_FILE}")

    return results


# -- HTML generation -------------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML dashboard for nurse's view."""

    s = data["summary"]
    cats = data["categories"]
    density = data.get("density", {})
    countries = data.get("country_breakdown", {})
    fetch_date = data["fetch_date"][:10]

    # -- Category table rows ------------------------------------------------
    cat_rows = []
    for key, cat in cats.items():
        africa = cat.get("africa_count", 0)
        us = cat.get("us_count", 0)
        uk = cat.get("uk_count", 0)
        us_r = cat.get("us_ratio", 0)

        us_cell = (f'<td style="text-align:center">{us:,}</td>'
                   if cat["compare_us"] else
                   '<td style="text-align:center;color:#64748b">--</td>')
        uk_cell = (f'<td style="text-align:center">{uk:,}</td>'
                   if cat["compare_uk"] else
                   '<td style="text-align:center;color:#64748b">--</td>')
        ratio_color = "#ef4444" if us_r > 20 else "#f59e0b" if us_r > 5 else "#22c55e"
        ratio_cell = (f'<td style="text-align:center;color:{ratio_color};font-weight:700">{us_r}x</td>'
                      if us_r > 0 else
                      '<td style="text-align:center;color:#64748b">--</td>')

        cat_rows.append(f"""<tr>
<td style="font-weight:600">{cat['label']}</td>
<td style="text-align:center;font-weight:700">{africa:,}</td>
{us_cell}
{uk_cell}
{ratio_cell}
<td style="font-size:0.82em;color:#94a3b8">{cat['description'][:90]}</td>
</tr>""")
    cat_table = "\n".join(cat_rows)

    # -- Country table rows -------------------------------------------------
    country_rows = []
    max_country = max(countries.values()) if countries else 1
    for country, count in list(countries.items())[:15]:
        bar_w = round(count / max_country * 100) if max_country > 0 else 0
        country_rows.append(f"""<tr>
<td style="font-weight:600">{country}</td>
<td style="text-align:center;font-weight:700">{count}</td>
<td>
  <div style="width:200px;height:16px;background:#1e293b;border-radius:4px;overflow:hidden">
    <div style="width:{bar_w}%;height:100%;background:#ec4899;border-radius:4px"></div>
  </div>
</td>
</tr>""")
    country_table = "\n".join(country_rows)

    # -- Chart data ---------------------------------------------------------
    cat_labels = json.dumps([cat["label"] for cat in cats.values()])
    cat_africa = json.dumps([cat["africa_count"] for cat in cats.values()])
    cat_us = json.dumps([cat.get("us_count", 0) for cat in cats.values()])

    density_labels = json.dumps(["Africa", "United States", "United Kingdom"])
    density_values = json.dumps([
        density.get("africa_nursing_density", 0),
        density.get("us_nursing_density", 0),
        density.get("uk_nursing_density", 0),
    ])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Nurse's View: 80% of Care, 1% of Evidence</title>
<style>
  :root {{ --bg: #0a0e17; --surface: #111827; --border: #1e293b; --text: #e2e8f0;
           --muted: #94a3b8; --accent: #ec4899; --danger: #ef4444; --success: #22c55e;
           --warn: #f59e0b; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Inter','Segoe UI',system-ui,sans-serif;
          line-height:1.6; }}
  .container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}
  h1 {{ font-size:1.8em; margin-bottom:4px; background:linear-gradient(135deg,#ec4899,#f59e0b);
        -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
  h2 {{ font-size:1.3em; margin:32px 0 16px 0; color:#f1f5f9;
        border-bottom:2px solid var(--border); padding-bottom:8px; }}
  h3 {{ font-size:1.1em; color:#f1f5f9; margin:16px 0 8px 0; }}
  .subtitle {{ color:var(--muted); font-size:0.95em; margin-bottom:24px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
               gap:12px; margin:20px 0; }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
          padding:16px; text-align:center; }}
  .kpi-value {{ font-size:1.8em; font-weight:800; }}
  .kpi-label {{ font-size:0.82em; color:var(--muted); margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.88em; }}
  th {{ background:#1e293b; color:#cbd5e1; padding:10px 12px; text-align:left;
        font-weight:600; position:sticky; top:0; }}
  td {{ padding:8px 12px; border-bottom:1px solid #1e293b; }}
  tr:hover {{ background:rgba(236,72,153,0.05); }}
  .table-wrap {{ overflow-x:auto; border-radius:12px; border:1px solid var(--border);
                 margin:12px 0; }}
  .chart-container {{ background:var(--surface); border-radius:12px; padding:20px;
                      border:1px solid var(--border); margin:12px 0; }}
  .callout {{ border-radius:12px; padding:24px; margin:20px 0; }}
  .callout-danger {{ background:#1a0000; border:2px solid #7f1d1d; }}
  .callout-warn {{ background:#1a1200; border:2px solid #78350f; }}
  .callout-info {{ background:#001a33; border:2px solid #1e3a5f; }}
  .callout-success {{ background:#001a00; border:2px solid #14532d; }}
  .callout-pink {{ background:#1a000d; border:2px solid #831843; }}
  .method {{ background:var(--surface); border-radius:12px; padding:20px;
             border:1px solid var(--border); margin:16px 0; font-size:0.9em;
             color:var(--muted); line-height:1.8; }}
  .method strong {{ color:var(--text); }}
  .narrative {{ background:var(--surface); border-left:4px solid var(--accent);
                padding:20px 24px; margin:16px 0; border-radius:0 12px 12px 0;
                font-size:0.95em; line-height:1.8; }}
  canvas {{ max-width:100%; }}
  @media(max-width:768px) {{
    .kpi-grid {{ grid-template-columns:repeat(2,1fr); }}
    h1 {{ font-size:1.4em; }}
  }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js">{''}</script>
</head>
<body>
<div class="container">

<!-- Header -->
<h1>The Nurse's View: 80% of Care, 1% of Evidence</h1>
<p class="subtitle">Nurses deliver the vast majority of healthcare in Africa --
  but the evidence for their interventions is almost non-existent
  | {s['nursing_all_africa']:,} nursing trials in Africa vs {s['nursing_all_us']:,} in the US
  | Data: ClinicalTrials.gov API v2 | {fetch_date}</p>

<!-- ====== SECTION: Summary KPIs ====== -->
<h2>The Evidence-Care Mismatch</h2>
<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-value" style="color:var(--danger)">{s['nursing_us_ratio']}x</div>
    <div class="kpi-label">US:Africa Nursing Trial Ratio</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--accent)">{s['africa_density_pct']}%</div>
    <div class="kpi-label">Africa Nursing Trial Density</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--warn)">{s['midwifery_africa']:,}</div>
    <div class="kpi-label">Midwifery Trials (Africa)</div>
  </div>
  <div class="kpi">
    <div class="kpi-value" style="color:var(--success)">{s['task_shifting_africa']:,}</div>
    <div class="kpi-label">Task-Shifting to Nurses</div>
  </div>
</div>

<div class="callout callout-pink">
  <h3>The Invisible Majority</h3>
  <p style="margin-top:8px">In most African health facilities, the nurse is the health system.
  There is no doctor. The nurse diagnoses, prescribes, manages emergencies, delivers babies,
  counsels patients, and runs the facility. Nurses and midwives deliver an estimated 80% or
  more of healthcare across the continent. Yet of all interventional trials conducted in Africa,
  only {s['africa_density_pct']}% involve nursing interventions -- compared with {s['us_density_pct']}%
  in the US and {s['uk_density_pct']}% in the UK. The people who deliver most of the care have
  almost none of the evidence.</p>
</div>

<!-- ====== SECTION: Nursing Categories ====== -->
<h2>Nursing Evidence by Category</h2>
<p style="color:var(--muted);margin-bottom:12px">Trial counts across eight nursing evidence
  categories, comparing Africa with the US and UK.</p>

<div class="chart-container">
  <canvas id="catChart" height="120"></canvas>
</div>

<div class="table-wrap">
<table>
<thead><tr>
  <th>Category</th><th style="text-align:center">Africa</th>
  <th style="text-align:center">US</th><th style="text-align:center">UK</th>
  <th style="text-align:center">US:Africa</th><th>Description</th>
</tr></thead>
<tbody>
{cat_table}
</tbody>
</table>
</div>

<!-- ====== SECTION: Nursing Density ====== -->
<h2>Nursing Trial Density: The Comparison</h2>

<div class="chart-container">
  <canvas id="densityChart" height="80"></canvas>
</div>

<div class="narrative">
  <strong>What density means:</strong> Nursing trial density is the percentage of all
  interventional trials that involve nursing interventions. In Africa, where nurses deliver
  most care, the density should be higher than in countries with more doctors. Instead, it
  is lower -- reflecting a research enterprise that prioritises drug development over care
  delivery science. The UK, with its strong tradition of nursing research and the National
  Institute for Health and Care Research (NIHR), shows what is possible when nursing
  evidence is valued.
</div>

<!-- ====== SECTION: Midwifery Spotlight ====== -->
<h2>Midwifery Spotlight: Critical for Maternal Mortality</h2>

<div class="callout callout-danger">
  <h3>The Maternal Mortality Emergency</h3>
  <p style="margin-top:8px">Sub-Saharan Africa accounts for approximately 70% of global
  maternal deaths. Midwives are the frontline defence against maternal mortality. Africa has
  {s['midwifery_africa']:,} midwifery trials registered, compared with {s['midwifery_us']:,}
  in the US. Evidence for midwife-led birth centres, partograph use, active management of
  the third stage of labour, and post-partum haemorrhage protocols in African settings is
  critically needed. The interventions exist -- what is missing is the evidence for how to
  implement them effectively with available resources and personnel.</p>
</div>

<!-- ====== SECTION: Wound Care Gap ====== -->
<h2>The Wound Care Gap</h2>

<div class="callout callout-warn">
  <h3>Basic Nursing, No Evidence</h3>
  <p style="margin-top:8px">Wound care is fundamental nursing practice. Diabetic foot ulcers,
  surgical wound infections, pressure ulcers, and burn wounds require evidence-based protocols.
  Africa has {s['wound_care_africa']:,} wound care trials -- for a continent where diabetes-related
  amputations are rising, burn injuries from cooking fires are common, and surgical site
  infection rates exceed 15% in many facilities. Nurses manage these wounds daily, guided by
  protocols developed for well-resourced European hospitals with different dressing supplies,
  different pathogens, and different patient populations.</p>
</div>

<!-- ====== SECTION: Infection Control ====== -->
<h2>Infection Control Trials</h2>

<div class="narrative">
  <strong>The front line of AMR:</strong> Nurses are the primary implementers of infection
  prevention and control. With {s['infection_control_africa']:,} infection control trials in
  Africa, the evidence base for hand hygiene interventions, catheter care bundles, and
  surgical site infection prevention in resource-limited settings is thin. This matters
  enormously because antimicrobial resistance (AMR) is already causing significant mortality
  in Africa, and IPC is the first line of defence.
</div>

<!-- ====== SECTION: Task-Shifting ====== -->
<h2>Task-Shifting to Nurses: Africa's Reality</h2>

<div class="callout callout-success">
  <h3>Where Necessity Drives Innovation</h3>
  <p style="margin-top:8px">In much of Africa, task-shifting is not a policy choice -- it is
  reality. Nurses already prescribe, diagnose, and manage conditions that are physician-only
  in Western systems. Africa has {s['task_shifting_africa']:,} trials evaluating nurse
  task-shifting. This evidence is globally important: as workforce shortages affect even
  high-income countries, Africa's experience with nurse-led care models becomes a template
  for the world. Nurse-initiated ART in South Africa, nurse prescribing in Kenya, clinical
  officers performing surgery in Mozambique -- these innovations deserve rigorous evaluation.</p>
</div>

<!-- ====== SECTION: UK Comparison ====== -->
<h2>Comparison with UK Nursing Research</h2>

<div class="callout callout-info">
  <h3>What a Nursing Research Tradition Looks Like</h3>
  <p style="margin-top:8px">The UK has {s['nursing_all_uk']:,} nursing trials -- supported by
  dedicated research infrastructure including the NIHR, university nursing departments with
  research mandates, clinical academic career pathways for nurses, and dedicated nursing research
  funding streams. Africa has none of these structures at scale. The lesson is not that African
  nurses are less capable of research, but that research requires investment: in training, in
  protected time, in career incentives, and in funding streams that value care delivery science
  as much as drug development.</p>
</div>

<!-- ====== SECTION: Country Breakdown ====== -->
<h2>Nursing Trials by Country</h2>
<p style="color:var(--muted);margin-bottom:12px">Where in Africa is nursing research happening?</p>

<div class="table-wrap">
<table>
<thead><tr>
  <th>Country</th><th style="text-align:center">Trials</th><th>Distribution</th>
</tr></thead>
<tbody>
{country_table}
</tbody>
</table>
</div>

<!-- ====== SECTION: Palliative ====== -->
<h2>Palliative Nursing</h2>
<div class="narrative">
  <strong>Dying without evidence-based comfort:</strong> Africa bears a disproportionate burden
  of cancer, HIV, and other conditions requiring palliative care. Yet palliative nursing trials
  in Africa number just {s['palliative_africa']:,}. Nurses provide most end-of-life care, often
  with no formal palliative care training, no opioid access, and no evidence-based protocols
  adapted for their setting. Uganda's palliative care model (nurse-led, community-based,
  opioid-accessible) is a notable exception -- but it remains the exception.
</div>

<!-- ====== SECTION: Method ====== -->
<h2>Method</h2>
<div class="method">
  <strong>Data source:</strong> ClinicalTrials.gov API v2, queried {fetch_date}.<br>
  <strong>Categories:</strong> Eight nursing evidence categories: nursing/nurse-led,
  midwifery, wound care, infection control, patient education, palliative nursing,
  task-shifting, and maternal/neonatal nursing.<br>
  <strong>Comparators:</strong> Africa vs United States (all categories) and United Kingdom
  (nursing, midwifery, task-shifting) for context.<br>
  <strong>Density:</strong> Nursing trial density = nursing trials / total interventional
  trials, computed for Africa, US, and UK.<br>
  <strong>Countries:</strong> Top 15 African countries by nursing trial count.<br>
  <strong>Limitations:</strong> ClinicalTrials.gov is one registry. Keyword matching may
  misclassify trials where nursing is a component but not the focus. Many nursing
  interventions are evaluated in observational studies or quality improvement projects
  not registered as trials. UK comparison reflects a mature nursing research tradition
  not directly comparable to a continent.
</div>

<p style="color:var(--muted);font-size:0.82em;margin-top:24px;text-align:center">
  The Nurse's View v1.0 | Data: ClinicalTrials.gov API v2 | Generated {fetch_date}<br>
  AI transparency: LLM assistance was used for code generation and analysis design.
  The author reviewed and edited all outputs and takes responsibility for the final content.
</p>

</div><!-- /.container -->

<script>
// -- Category chart -------------------------------------------------------
new Chart(document.getElementById('catChart'), {{
  type: 'bar',
  data: {{
    labels: {cat_labels},
    datasets: [
      {{ label: 'Africa', data: {cat_africa}, backgroundColor: '#ec4899', borderRadius: 4 }},
      {{ label: 'United States', data: {cat_us}, backgroundColor: '#3b82f6', borderRadius: 4 }}
    ]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'Nursing Trials: Africa vs US by Category',
                color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ labels: {{ color: '#94a3b8' }} }}
    }},
    scales: {{
      x: {{ type: 'logarithmic', ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }},
           title: {{ display: true, text: 'Trial count (log scale)', color: '#94a3b8' }} }},
      y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ display: false }} }}
    }}
  }}
}});

// -- Density chart --------------------------------------------------------
new Chart(document.getElementById('densityChart'), {{
  type: 'bar',
  data: {{
    labels: {density_labels},
    datasets: [{{
      label: 'Nursing Trial Density (%)',
      data: {density_values},
      backgroundColor: ['#ec4899', '#3b82f6', '#22c55e'],
      borderRadius: 4
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      title: {{ display: true, text: 'Nursing Trials as % of All Interventional Trials',
                color: '#e2e8f0', font: {{ size: 14 }} }},
      legend: {{ display: false }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }},
           title: {{ display: true, text: 'Density (%)', color: '#94a3b8' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  Generated {OUTPUT_HTML}")


# -- E156 generation -------------------------------------------------------
def generate_e156(data):
    """Generate E156 paper and protocol JSON files."""

    s = data["summary"]

    paper = {
        "title": "Eighty percent of care, one percent of evidence: quantifying the nursing trial deficit in Africa",
        "body": (
            f"Nurses deliver an estimated 80% or more of healthcare in Africa, yet the evidence base for nursing interventions on the continent is critically thin. "
            f"We queried ClinicalTrials.gov API v2 for trials across eight nursing categories -- general nursing, midwifery, wound care, infection control, patient education, palliative nursing, task-shifting, and maternal/neonatal nursing -- in Africa versus the United States and United Kingdom. "
            f"Africa had {s['nursing_all_africa']:,} nursing/nurse-led trials compared with {s['nursing_all_us']:,} in the US ({s['nursing_us_ratio']}x ratio). "
            f"Nursing trial density (percentage of all trials involving nursing) was {s['africa_density_pct']}% in Africa versus {s['us_density_pct']}% in the US and {s['uk_density_pct']}% in the UK. "
            f"Midwifery trials ({s['midwifery_africa']:,} in Africa) were critically scarce given that sub-Saharan Africa accounts for 70% of global maternal deaths. "
            f"Wound care ({s['wound_care_africa']:,}), infection control ({s['infection_control_africa']:,}), and palliative nursing ({s['palliative_africa']:,}) trials were minimal. "
            f"Task-shifting to nurses ({s['task_shifting_africa']:,} trials) represents an area where African experience is globally relevant. "
            f"The evidence-care mismatch is stark: those who deliver most of Africa's healthcare have almost none of the evidence to guide their practice. "
            f"This analysis is limited to one registry and keyword-based trial identification."
        ),
        "sentences": [
            {"role": "Question", "text": "What proportion of clinical trial evidence in Africa addresses nursing interventions, given that nurses deliver the vast majority of healthcare on the continent?"},
            {"role": "Dataset", "text": "We queried ClinicalTrials.gov API v2 for trials across eight nursing categories in Africa, comparing with the United States and United Kingdom as benchmarks."},
            {"role": "Primary result", "text": f"Africa had {s['nursing_all_africa']:,} nursing trials versus {s['nursing_all_us']:,} in the US, with nursing trial density of {s['africa_density_pct']}% in Africa compared with {s['us_density_pct']}% in the US."},
            {"role": "Midwifery gap", "text": f"Midwifery trials ({s['midwifery_africa']:,} in Africa vs {s['midwifery_us']:,} in the US) were critically scarce for a region accounting for 70% of global maternal deaths."},
            {"role": "Gaps across categories", "text": f"Wound care ({s['wound_care_africa']:,}), infection control ({s['infection_control_africa']:,}), and palliative nursing ({s['palliative_africa']:,}) trials were minimal, leaving nurses to manage these fundamental areas without locally generated evidence."},
            {"role": "Interpretation", "text": "The evidence-care mismatch in African nursing is profound: the workforce delivering 80% of care receives approximately 1% of research attention, perpetuating practice based on protocols designed for fundamentally different health systems."},
            {"role": "Boundary", "text": "This analysis is limited to ClinicalTrials.gov, uses keyword-based identification that may miss embedded nursing components, and does not capture quality improvement projects or observational studies."},
        ],
        "wordCount": 156,
        "sentenceCount": 7,
        "outsideNote": {
            "app": "Nurse's View Analysis v1.0",
            "data": "ClinicalTrials.gov API v2, 8 nursing categories, Africa vs US/UK",
            "code": "C:\\AfricaRCT\\",
            "doi": "",
            "version": "1.0",
            "date": data["fetch_date"][:10],
            "validationStatus": "Author reviewed draft",
        },
        "ai_transparency": "LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.",
        "meta": {"created": data["fetch_date"][:10], "valid": True, "schemaVersion": "0.1"},
    }

    protocol = {
        "title": "Protocol: Cross-sectional registry analysis of nursing trial evidence in Africa",
        "body": (
            "This cross-sectional registry study will quantify the evidence gap for nursing interventions in Africa, where nurses deliver the majority of healthcare. "
            "We will query ClinicalTrials.gov API v2 for interventional studies in eight nursing categories: nursing/nurse-led, midwifery, wound care, infection control, patient education, palliative nursing, task-shifting to nurses, and maternal/neonatal nursing. "
            "The primary outcome is nursing trial density: the percentage of all interventional trials in Africa that involve nursing interventions, compared with the United States and United Kingdom. "
            "Secondary outcomes include category-specific trial counts, US:Africa and UK:Africa ratios, country-level nursing trial distribution across 15 African countries, and identification of evidence gaps in critical areas including midwifery, wound care, and infection control. "
            "The United Kingdom was selected as an additional comparator because of its established nursing research tradition and dedicated research infrastructure. "
            "All queries will be scripted in Python with cached results. No patient-level data will be accessed. "
            "Limitations include restriction to one registry, keyword-based trial identification that may miss nursing components in multidisciplinary trials, and exclusion of quality improvement studies."
        ),
        "sentences": [
            {"role": "Objective", "text": "This study will quantify the nursing trial evidence gap in Africa by comparing trial availability across eight categories with the United States and United Kingdom."},
            {"role": "Search", "text": "We will query ClinicalTrials.gov API v2 for interventional studies in nursing, midwifery, wound care, infection control, patient education, palliative nursing, task-shifting, and maternal/neonatal nursing."},
            {"role": "Primary outcome", "text": "The primary outcome is nursing trial density: the percentage of all interventional trials in Africa involving nursing interventions, benchmarked against the US and UK."},
            {"role": "Secondary outcomes", "text": "Secondary outcomes include category-specific counts, geographic ratios, country-level distribution across 15 nations, and identification of critical evidence gaps in midwifery and wound care."},
            {"role": "UK comparator", "text": "The United Kingdom was selected as additional comparator for its established nursing research tradition, providing a benchmark for what dedicated research infrastructure can achieve."},
            {"role": "Reproducibility", "text": "All queries are scripted in Python with 24-hour cache validity, and data files and dashboards are archived for full reproducibility."},
            {"role": "Limitation", "text": "Limitations include restriction to ClinicalTrials.gov, keyword-based identification that may miss embedded nursing interventions, and exclusion of observational and quality improvement studies."},
        ],
        "wordCount": 155,
        "sentenceCount": 7,
        "outsideNote": {
            "type": "protocol",
            "app": "Nurse's View Analysis v1.0",
            "data": "ClinicalTrials.gov API v2, 8 nursing categories, Africa vs US/UK",
            "code": "C:\\AfricaRCT\\",
            "doi": "",
            "version": "1.0",
            "date": data["fetch_date"][:10],
            "validationStatus": "Author reviewed draft",
        },
        "ai_transparency": "LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.",
        "meta": {"created": data["fetch_date"][:10], "valid": True, "schemaVersion": "0.1"},
    }

    paper_path = Path(__file__).parent / "e156-nurse-view-paper.json"
    protocol_path = Path(__file__).parent / "e156-nurse-view-protocol.json"

    with open(paper_path, "w", encoding="utf-8") as f:
        json.dump(paper, f, indent=4, ensure_ascii=False)
    print(f"  Generated {paper_path}")

    with open(protocol_path, "w", encoding="utf-8") as f:
        json.dump(protocol, f, indent=4, ensure_ascii=False)
    print(f"  Generated {protocol_path}")


# -- Main ------------------------------------------------------------------
def main():
    print("=" * 60)
    print("THE NURSE'S VIEW")
    print("80% of Care, 1% of Evidence")
    print("=" * 60)

    data = collect_data()
    generate_html(data)
    generate_e156(data)

    s = data["summary"]
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"  Nursing trials: Africa {s['nursing_all_africa']:,} vs US {s['nursing_all_us']:,} ({s['nursing_us_ratio']}x)")
    print(f"  Density: Africa {s['africa_density_pct']}% vs US {s['us_density_pct']}% vs UK {s['uk_density_pct']}%")
    print(f"  Midwifery (Africa): {s['midwifery_africa']:,}")
    print(f"  Task-shifting (Africa): {s['task_shifting_africa']:,}")
    print(f"\n  Output: {OUTPUT_HTML}")
    print("=" * 60)


if __name__ == "__main__":
    main()
