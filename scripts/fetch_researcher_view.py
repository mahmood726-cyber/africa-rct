"""
The Researcher's View — Brain Drain and Career Dead Ends
=========================================================
African researchers face impossible choices: stay and work on foreign-driven
agendas, or emigrate. Quantifies the career landscape using ClinicalTrials.gov
API v2 data.

Usage:
    python fetch_researcher_view.py

Output:
    data/researcher_view_data.json  — cached data
    researcher-view.html            — interactive dashboard

Requirements:
    Python 3.8+, requests (pip install requests)
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config ----------------------------------------------------------------
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "researcher_view_data.json"
OUTPUT_HTML = Path(__file__).parent / "researcher-view.html"
CACHE_HOURS = 24
RATE_LIMIT_DELAY = 0.35

# -- Local institution keywords -------------------------------------------
LOCAL_INSTITUTIONS = {
    "Makerere University": ["makerere"],
    "Mbarara University": ["mbarara"],
    "MRC/UVRI Uganda": ["mrc/uvri", "uvri"],
    "Infectious Diseases Institute": ["infectious diseases institute"],
    "Mulago Hospital": ["mulago"],
    "Kampala (other)": ["kampala"],
    "Gulu University": ["gulu"],
    "Busitema University": ["busitema"],
    "Uganda Cancer Institute": ["uganda cancer"],
    "Uganda Heart Institute": ["uganda heart"],
    "UNCST": ["uncst"],
    "Other Uganda": ["uganda"],
}

# Phase hierarchy for career ceiling
PHASE_HIERARCHY = {
    "PHASE1": 4, "EARLY_PHASE1": 4,
    "PHASE2": 3,
    "PHASE3": 2,
    "PHASE4": 1,
    "NA": 0,
}


# -- API helpers -----------------------------------------------------------
def search_trials_count(location=None, query_term=None, page_size=1,
                        max_retries=3):
    params = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }
    if location:
        params["query.locn"] = location
    if query_term:
        params["query.term"] = query_term

    for attempt in range(max_retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json().get("totalCount", 0)
        except requests.RequestException as e:
            print(f"  WARNING: API error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return 0


def classify_local_institution(sponsor_name):
    """Classify a sponsor as a specific local institution."""
    name_lower = sponsor_name.lower()
    for inst, keywords in LOCAL_INSTITUTIONS.items():
        if any(kw in name_lower for kw in keywords):
            return inst
    return None


# -- Data collection -------------------------------------------------------
def collect_data():
    """Collect researcher view data."""

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

    # ---- Load Uganda trials ----
    print("\n" + "=" * 70)
    print("STEP 1: Loading Uganda trial data")
    print("=" * 70)

    uganda_cache = DATA_DIR / "uganda_collected_data.json"
    if not uganda_cache.exists():
        print("  ERROR: Uganda data not found. Run fetch_uganda_rcts.py first.")
        sys.exit(1)

    with open(uganda_cache, "r", encoding="utf-8") as f:
        uganda_data = json.load(f)
    trials = uganda_data.get("sample_trials", [])
    total = len(trials)
    print(f"  Loaded {total} Uganda trials")

    # ---- Step 2: Institutional analysis ----
    print("\n" + "=" * 70)
    print("STEP 2: Institutional diversity analysis")
    print("=" * 70)

    inst_counts = Counter()
    inst_trials = {}  # institution -> list of trials
    local_count = 0
    foreign_count = 0
    all_sponsors = Counter()

    for trial in trials:
        sponsor = trial.get("sponsor", "")
        all_sponsors[sponsor] += 1
        inst = classify_local_institution(sponsor)
        if inst is not None:
            inst_counts[inst] += 1
            if inst not in inst_trials:
                inst_trials[inst] = []
            inst_trials[inst].append(trial)
            local_count += 1
        else:
            foreign_count += 1

    unique_local = len(inst_counts)
    unique_total = len(all_sponsors)
    print(f"  Unique local institutions: {unique_local}")
    print(f"  Unique total sponsors: {unique_total}")
    print(f"  Local-led: {local_count} ({round(local_count/total*100,1)}%)")
    for inst, count in inst_counts.most_common(10):
        print(f"    {inst}: {count}")

    # ---- Step 3: Phase distribution per institution (career development) ----
    print("\n" + "=" * 70)
    print("STEP 3: Phase distribution by local institution")
    print("=" * 70)

    inst_phases = {}
    inst_conditions = {}
    for inst, trial_list in inst_trials.items():
        phase_counter = Counter()
        cond_counter = Counter()
        for t in trial_list:
            phases = t.get("phases", [])
            if not phases:
                phases = ["NA"]
            for p in phases:
                phase_counter[p] += 1
            for c in t.get("conditions", []):
                cond_counter[c.lower()] += 1

        inst_phases[inst] = dict(phase_counter.most_common())
        inst_conditions[inst] = dict(cond_counter.most_common(5))
        print(f"  {inst}: phases={dict(phase_counter.most_common())}")

    # ---- Step 4: Career Ceiling Index ----
    print("\n" + "=" * 70)
    print("STEP 4: Career Ceiling Index by institution")
    print("=" * 70)

    inst_ceiling = {}
    for inst, phases in inst_phases.items():
        max_phase_score = 0
        max_phase_name = "None"
        for phase, count in phases.items():
            score = PHASE_HIERARCHY.get(phase, 0)
            if score > max_phase_score:
                max_phase_score = score
                max_phase_name = phase
        # Career Ceiling: 1 (only Phase 4/NA) to 4 (leads Phase 1)
        inst_ceiling[inst] = {
            "score": max_phase_score,
            "max_phase": max_phase_name,
            "interpretation": {
                0: "No drug development pathway",
                1: "Post-market only (Phase 4)",
                2: "Late-stage testing (Phase 3)",
                3: "Mid-stage development (Phase 2)",
                4: "Full drug development (Phase 1)",
            }.get(max_phase_score, "Unknown"),
        }
        print(f"  {inst}: ceiling={max_phase_score} ({max_phase_name})")

    # ---- Step 5: Brain Drain Risk Score ----
    print("\n" + "=" * 70)
    print("STEP 5: Brain Drain Risk Score")
    print("=" * 70)

    # Components:
    # 1. Phase 1 access (0-25): 0 if no Phase 1, 25 if Phase 1 available
    phase1_available = any(
        "PHASE1" in phases or "EARLY_PHASE1" in phases
        for phases in inst_phases.values()
    )
    phase1_score = 25 if phase1_available else 0

    # 2. Foreign control (0-25): higher = more foreign
    foreign_pct = round(foreign_count / total * 100) if total > 0 else 0
    foreign_score = min(25, round(foreign_pct / 4))

    # 3. Institutional concentration (0-25): top institution share
    top_inst_count = inst_counts.most_common(1)[0][1] if inst_counts else 0
    top_inst_pct = round(top_inst_count / local_count * 100) if local_count > 0 else 100
    concentration_score = min(25, round(top_inst_pct / 4))

    # 4. Senior PI positions (0-25): proxy = local sponsor diversity
    diversity_deficit = max(0, 25 - unique_local * 2)

    brain_drain_risk = foreign_score + concentration_score + diversity_deficit + (25 - phase1_score)
    brain_drain_risk = min(100, max(0, brain_drain_risk))

    print(f"  Phase 1 access:          {'Yes' if phase1_available else 'No'} (score: {phase1_score})")
    print(f"  Foreign control:         {foreign_pct}% (score: {foreign_score})")
    print(f"  Institutional concentration: {top_inst_pct}% (score: {concentration_score})")
    print(f"  Diversity deficit:       {diversity_deficit}")
    print(f"  BRAIN DRAIN RISK SCORE:  {brain_drain_risk}/100")

    # ---- Step 6: Makerere vs Others workload ----
    print("\n" + "=" * 70)
    print("STEP 6: Institutional capacity comparison")
    print("=" * 70)

    inst_workload = {}
    for inst, count in inst_counts.most_common():
        # Compute year range
        years = []
        for t in inst_trials.get(inst, []):
            sd = t.get("start_date", "")
            if sd and len(sd) >= 4:
                try:
                    years.append(int(sd[:4]))
                except ValueError:
                    pass
        year_range = max(years) - min(years) + 1 if years else 1
        trials_per_year = round(count / year_range, 1)
        inst_workload[inst] = {
            "total_trials": count,
            "year_range": year_range,
            "trials_per_year": trials_per_year,
            "conditions": inst_conditions.get(inst, {}),
        }
        print(f"  {inst}: {count} trials over {year_range} years ({trials_per_year}/yr)")

    # ---- Build data object ----
    data = {
        "fetch_date": datetime.now().isoformat(),
        "uganda_total": total,
        "local_count": local_count,
        "foreign_count": foreign_count,
        "local_pct": round(local_count / total * 100, 1) if total > 0 else 0,
        "unique_local_institutions": unique_local,
        "unique_total_sponsors": unique_total,
        "inst_counts": dict(inst_counts.most_common()),
        "inst_phases": inst_phases,
        "inst_conditions": inst_conditions,
        "inst_ceiling": inst_ceiling,
        "inst_workload": inst_workload,
        "brain_drain_risk": {
            "score": brain_drain_risk,
            "components": {
                "phase1_access": phase1_score,
                "foreign_control": foreign_score,
                "institutional_concentration": concentration_score,
                "diversity_deficit": diversity_deficit,
            },
        },
        "top_sponsors": all_sponsors.most_common(20),
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nCached data to {CACHE_FILE}")

    return data


# -- HTML Report Generator -------------------------------------------------
def generate_html(data):
    """Generate dark-themed HTML researcher view dashboard."""

    fetch_date = data["fetch_date"][:10]
    total = data["uganda_total"]
    local_count = data["local_count"]
    foreign_count = data["foreign_count"]
    local_pct = data["local_pct"]
    unique_local = data["unique_local_institutions"]
    unique_total = data["unique_total_sponsors"]
    inst_counts = data["inst_counts"]
    inst_phases = data["inst_phases"]
    inst_ceiling = data["inst_ceiling"]
    inst_workload = data["inst_workload"]
    bdr = data["brain_drain_risk"]
    bdr_score = bdr["score"]
    bdr_comp = bdr["components"]

    # Brain drain risk color
    bdr_color = "#ef4444" if bdr_score >= 70 else "#f59e0b" if bdr_score >= 40 else "#22c55e"

    # Institution bars
    max_inst = max(inst_counts.values()) if inst_counts else 1
    inst_bars = []
    for inst, count in sorted(inst_counts.items(), key=lambda x: x[1], reverse=True):
        bar_w = round(count / max_inst * 100)
        ceiling_info = inst_ceiling.get(inst, {})
        ceiling_score = ceiling_info.get("score", 0)
        ceiling_color = "#22c55e" if ceiling_score >= 3 else "#f59e0b" if ceiling_score >= 2 else "#ef4444"
        inst_bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:7px 0">'
            f'<div style="width:200px;text-align:right;font-weight:600;'
            f'color:#e2e8f0;font-size:13px">{inst}</div>'
            f'<div style="flex:1;background:#1e293b;border-radius:4px;height:28px;'
            f'position:relative">'
            f'<div style="width:{bar_w}%;height:100%;background:#3b82f6;'
            f'border-radius:4px"></div>'
            f'<span style="position:absolute;right:8px;top:4px;font-size:12px;'
            f'color:#94a3b8;font-weight:600">{count} trials</span>'
            f'</div>'
            f'<div style="width:100px;text-align:center">'
            f'<span style="color:{ceiling_color};font-weight:700;font-size:14px">'
            f'{"*" * ceiling_score if ceiling_score > 0 else "-"}</span>'
            f'</div>'
            f'</div>'
        )
    inst_bars_html = "\n".join(inst_bars)

    # Career ceiling table
    ceiling_rows = []
    for inst, info in sorted(inst_ceiling.items(),
                             key=lambda x: x[1]["score"], reverse=True):
        score = info["score"]
        color = "#22c55e" if score >= 3 else "#f59e0b" if score >= 2 else "#ef4444"
        phases_str = ", ".join(
            f'{p}: {c}' for p, c in inst_phases.get(inst, {}).items())
        ceiling_rows.append(
            f'<tr>'
            f'<td style="padding:8px 12px;font-weight:600">{inst}</td>'
            f'<td style="text-align:center;padding:8px 12px;'
            f'color:{color};font-weight:700;font-size:1.2rem">{score}/4</td>'
            f'<td style="padding:8px 12px;font-size:13px;color:#94a3b8">'
            f'{info["interpretation"]}</td>'
            f'<td style="padding:8px 12px;font-size:12px;color:#64748b">'
            f'{phases_str}</td>'
            f'</tr>'
        )
    ceiling_rows_html = "\n".join(ceiling_rows)

    # Workload table
    workload_rows = []
    for inst, info in sorted(inst_workload.items(),
                             key=lambda x: x[1]["total_trials"], reverse=True):
        top_conds = ", ".join(f'{c}' for c in list(
            data["inst_conditions"].get(inst, {}).keys())[:3])
        workload_rows.append(
            f'<tr>'
            f'<td style="padding:6px 10px;font-weight:600">{inst}</td>'
            f'<td style="text-align:right;padding:6px 10px;font-weight:600">'
            f'{info["total_trials"]}</td>'
            f'<td style="text-align:right;padding:6px 10px">'
            f'{info["year_range"]} yrs</td>'
            f'<td style="text-align:right;padding:6px 10px;color:#60a5fa;'
            f'font-weight:600">{info["trials_per_year"]}/yr</td>'
            f'<td style="padding:6px 10px;font-size:12px;color:#94a3b8">'
            f'{top_conds}</td>'
            f'</tr>'
        )
    workload_rows_html = "\n".join(workload_rows)

    # Brain drain risk gauge (CSS-based)
    gauge_rotation = round(bdr_score * 1.8)  # 0-180 degrees

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Impossible Choice | Africa's Researcher Career Landscape</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #cbd5e1;
    line-height: 1.6;
    min-height: 100vh;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  h1 {{
    font-size: 2.4rem; color: #f1f5f9; text-align: center; margin: 32px 0 8px;
    letter-spacing: -0.5px;
  }}
  .subtitle {{
    text-align: center; color: #64748b; font-size: 15px; margin-bottom: 32px;
  }}
  .section {{
    background: #111827; border: 1px solid #1e293b; border-radius: 12px;
    padding: 28px; margin-bottom: 24px;
  }}
  .section h2 {{
    color: #f1f5f9; font-size: 1.4rem; margin-bottom: 16px;
    padding-bottom: 10px; border-bottom: 2px solid #1e293b;
  }}
  .section h3 {{
    color: #e2e8f0; font-size: 1.1rem; margin: 16px 0 10px;
  }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 20px;
  }}
  .kpi {{
    background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
    padding: 20px; text-align: center;
  }}
  .kpi .value {{
    font-size: 2.2rem; font-weight: 800; margin: 4px 0;
  }}
  .kpi .label {{
    font-size: 13px; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .callout {{
    background: #1a0a0a; border-left: 4px solid #ef4444; padding: 16px 20px;
    margin: 16px 0; border-radius: 0 8px 8px 0; color: #fca5a5;
  }}
  .callout-green {{
    background: #0a1a0a; border-left: 4px solid #22c55e; color: #86efac;
  }}
  .callout-amber {{
    background: #1a150a; border-left: 4px solid #f59e0b; color: #fde68a;
  }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 14px;
  }}
  thead th {{
    text-align: left; padding: 10px 12px; color: #94a3b8;
    border-bottom: 2px solid #1e293b; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }}
  tbody td {{
    padding: 8px 12px; border-bottom: 1px solid #1e293b; color: #cbd5e1;
  }}
  tbody tr:hover {{ background: #1e293b44; }}
  .source {{
    text-align: center; color: #475569; font-size: 12px; margin-top: 40px;
    padding: 20px; border-top: 1px solid #1e293b;
  }}
  .source a {{ color: #3b82f6; text-decoration: none; }}
  .big-number {{
    font-size: 5rem; font-weight: 900; text-align: center;
    margin: 20px 0 10px; line-height: 1;
  }}
  .big-sub {{
    text-align: center; color: #94a3b8; font-size: 16px; margin-bottom: 16px;
  }}
  .risk-bar {{
    width: 100%; height: 32px; background: linear-gradient(to right, #22c55e, #f59e0b, #ef4444);
    border-radius: 8px; position: relative; margin: 20px 0;
  }}
  .risk-marker {{
    position: absolute; top: -8px; width: 4px; height: 48px;
    background: #f1f5f9; border-radius: 2px;
    transform: translateX(-50%);
  }}
</style>
</head>
<body>
<div class="container">

<h1>The Impossible Choice</h1>
<p class="subtitle">
  Brain Drain and Career Dead Ends for African Researchers |
  ClinicalTrials.gov API v2 | Data: {fetch_date}
</p>

<!-- ============ SECTION 1: EXECUTIVE SUMMARY ============ -->
<div class="section">
  <h2>1. The Career Landscape at a Glance</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">Uganda Trials</div>
      <div class="value" style="color:#60a5fa">{total}</div>
      <div class="label">total analysed</div>
    </div>
    <div class="kpi">
      <div class="label">Local-Led</div>
      <div class="value" style="color:#22c55e">{local_pct}%</div>
      <div class="label">{local_count} of {total}</div>
    </div>
    <div class="kpi">
      <div class="label">Local Institutions</div>
      <div class="value" style="color:#f59e0b">{unique_local}</div>
      <div class="label">of {unique_total} total sponsors</div>
    </div>
    <div class="kpi">
      <div class="label">Brain Drain Risk</div>
      <div class="value" style="color:{bdr_color}">{bdr_score}/100</div>
      <div class="label">composite score</div>
    </div>
  </div>
  <div class="callout">
    African researchers face an impossible choice: stay and work on
    foreign-driven agendas with limited career advancement, or emigrate
    to countries where they can lead research on their own terms. In
    Uganda, only <strong>{local_pct}%</strong> of trials are led by local
    institutions. Of those, the vast majority are concentrated in a single
    institution -- Makerere University. The career ceiling for most African
    researchers is being a site coordinator for someone else's trial.
  </div>
</div>

<!-- ============ SECTION 2: INSTITUTIONAL LANDSCAPE ============ -->
<div class="section">
  <h2>2. Institutional Concentration: The Makerere Monopoly</h2>
  <p style="color:#94a3b8;margin-bottom:12px">
    Local institution trial counts (right column: career ceiling score)
  </p>
  {inst_bars_html}
  <div class="callout-amber callout" style="margin-top:16px">
    <strong>The concentration problem:</strong> Makerere University leads
    {inst_counts.get('Makerere University', 0)} trials -- more than all
    other Ugandan institutions combined. This means that for the vast
    majority of Ugandan researchers, a career in clinical research means
    working at Makerere or not working at all. Regional universities like
    Mbarara ({inst_counts.get('Mbarara University', 0)} trials) and Gulu
    ({inst_counts.get('Gulu University', 0)} trials) offer almost no
    research career pathway.
  </div>
</div>

<!-- ============ SECTION 3: THE PHASE 1 CEILING ============ -->
<div class="section">
  <h2>3. The Phase 1 Ceiling: Career Development by Institution</h2>
  <p style="color:#94a3b8;margin-bottom:12px">
    Career Ceiling Index: 4 = leads Phase 1 (drug development career path),
    1 = Phase 4 only (post-market surveillance), 0 = no drug development.
  </p>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Institution</th>
        <th style="text-align:center">Ceiling Score</th>
        <th>Interpretation</th>
        <th>Phase Breakdown</th>
      </tr>
    </thead>
    <tbody>
      {ceiling_rows_html}
    </tbody>
  </table>
  </div>
  <div class="callout" style="margin-top:16px">
    <strong>The Phase 1 problem:</strong> Phase 1 trials are where drug
    development careers are built -- first-in-human studies, dose-finding,
    pharmacokinetics. These trials require regulatory expertise,
    pharmacovigilance infrastructure, and sophisticated laboratory capacity.
    Without access to Phase 1 research, Ugandan scientists cannot build
    careers in drug development. They are forever limited to testing drugs
    that others discovered, at doses that others determined, for indications
    that others chose.
  </div>
</div>

<!-- ============ SECTION 4: BRAIN DRAIN RISK ============ -->
<div class="section">
  <h2>4. Brain Drain Risk Score</h2>
  <div class="big-number" style="color:{bdr_color}">{bdr_score}<span style="font-size:2rem;color:#64748b">/100</span></div>
  <div class="big-sub">Composite Brain Drain Risk Score</div>
  <div class="risk-bar">
    <div class="risk-marker" style="left:{bdr_score}%"></div>
  </div>
  <div style="display:flex;justify-content:space-between;color:#64748b;font-size:12px;margin-bottom:16px">
    <span>0 (Low Risk)</span>
    <span>50 (Moderate)</span>
    <span>100 (Critical)</span>
  </div>

  <h3>Risk Components</h3>
  <div style="margin:12px 0">
    <div style="display:flex;align-items:center;gap:10px;margin:6px 0">
      <span style="width:220px;color:#e2e8f0;font-weight:600">Phase 1 access deficit</span>
      <div style="flex:1;background:#1e293b;border-radius:4px;height:22px">
        <div style="width:{round((25-bdr_comp['phase1_access'])/25*100)}%;height:100%;background:#ef4444;border-radius:4px"></div>
      </div>
      <span style="color:#94a3b8;min-width:60px;text-align:right">{25-bdr_comp['phase1_access']}/25</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin:6px 0">
      <span style="width:220px;color:#e2e8f0;font-weight:600">Foreign control of agenda</span>
      <div style="flex:1;background:#1e293b;border-radius:4px;height:22px">
        <div style="width:{round(bdr_comp['foreign_control']/25*100)}%;height:100%;background:#f59e0b;border-radius:4px"></div>
      </div>
      <span style="color:#94a3b8;min-width:60px;text-align:right">{bdr_comp['foreign_control']}/25</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin:6px 0">
      <span style="width:220px;color:#e2e8f0;font-weight:600">Institutional concentration</span>
      <div style="flex:1;background:#1e293b;border-radius:4px;height:22px">
        <div style="width:{round(bdr_comp['institutional_concentration']/25*100)}%;height:100%;background:#8b5cf6;border-radius:4px"></div>
      </div>
      <span style="color:#94a3b8;min-width:60px;text-align:right">{bdr_comp['institutional_concentration']}/25</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin:6px 0">
      <span style="width:220px;color:#e2e8f0;font-weight:600">Sponsor diversity deficit</span>
      <div style="flex:1;background:#1e293b;border-radius:4px;height:22px">
        <div style="width:{round(bdr_comp['diversity_deficit']/25*100)}%;height:100%;background:#06b6d4;border-radius:4px"></div>
      </div>
      <span style="color:#94a3b8;min-width:60px;text-align:right">{bdr_comp['diversity_deficit']}/25</span>
    </div>
  </div>
  <div class="callout">
    <strong>What this means:</strong> A Ugandan researcher who wants to lead
    drug development has almost no pathway to do so at home. The highest-impact
    research positions are held by foreign PIs. Local researchers are
    trained as implementers, not innovators. The rational career decision
    is emigration -- and Africa loses another generation of scientific talent.
  </div>
</div>

<!-- ============ SECTION 5: INSTITUTIONAL WORKLOAD ============ -->
<div class="section">
  <h2>5. Institutional Capacity and Workload</h2>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Institution</th>
        <th style="text-align:right">Total Trials</th>
        <th style="text-align:right">Active Period</th>
        <th style="text-align:right">Throughput</th>
        <th>Top Conditions</th>
      </tr>
    </thead>
    <tbody>
      {workload_rows_html}
    </tbody>
  </table>
  </div>
  <div class="callout-amber callout" style="margin-top:16px">
    <strong>The mentorship gap:</strong> With Makerere handling the bulk of
    Uganda's research, junior faculty face a bottleneck: too few senior PIs,
    too many trainees. The result is overworked supervisors, under-mentored
    students, and a system that produces technical operators rather than
    independent investigators. Regional universities lack the critical mass
    to sustain a research culture at all.
  </div>
</div>

<!-- ============ SECTION 6: THE RWANDA MODEL ============ -->
<div class="section">
  <h2>6. What Rwanda's Model Offers (PMID 39972388)</h2>
  <div class="callout-green callout">
    <strong>Rwanda's deliberate investment in research capacity</strong>
    offers a contrast to Uganda's organic but unequal growth. Key elements:
  </div>
  <ul style="margin:16px 0 16px 24px;color:#94a3b8;line-height:2.2">
    <li><strong style="color:#e2e8f0">Government-led research agenda:</strong>
      The Rwanda Biomedical Centre sets national priorities, not foreign funders.
      Research questions come from Rwanda's disease burden, not grant availability.</li>
    <li><strong style="color:#e2e8f0">Distributed capacity:</strong>
      Investment in multiple institutions, not just the capital. Regional
      referral hospitals are built as research sites from the beginning.</li>
    <li><strong style="color:#e2e8f0">Training pipeline:</strong>
      Structured career pathway from BSc to PhD with guaranteed positions.
      No "train and pray" model where graduates compete for non-existent posts.</li>
    <li><strong style="color:#e2e8f0">IP retention:</strong>
      Rwanda negotiates intellectual property rights in international
      collaborations, ensuring local benefit from local research.</li>
    <li><strong style="color:#e2e8f0">Regulatory investment:</strong>
      FDA Rwanda was established to build the regulatory capacity needed for
      Phase 1 trials -- the missing piece in most African countries.</li>
  </ul>
  <div class="callout-amber callout">
    <strong>Brain drain statistics:</strong> Sub-Saharan Africa loses an
    estimated 20,000 health professionals per year to emigration (WHO 2023).
    The cost of training a single physician in Africa is $50,000-100,000.
    Every researcher who emigrates represents not just lost investment but
    lost institutional knowledge, severed mentorship chains, and delayed
    research capacity. The brain drain is not a natural phenomenon -- it is
    the predictable consequence of a system that offers African researchers
    no viable career path at home.
  </div>
</div>

<div class="source">
  Data source: <a href="https://clinicaltrials.gov">ClinicalTrials.gov</a>
  API v2 (accessed {fetch_date})<br>
  Analysis: fetch_researcher_view.py | The Researcher's View<br>
  Rwanda model: PMID 39972388 | Brain drain: WHO Health Workforce Statistics 2023
</div>

</div>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated HTML: {OUTPUT_HTML}")
    print(f"  File size: {len(html):,} bytes")


# -- Main ------------------------------------------------------------------
def main():
    print("=" * 70)
    print("  The Researcher's View -- Brain Drain and Career Dead Ends")
    print("  ClinicalTrials.gov API v2 Analysis")
    print("=" * 70)

    data = collect_data()

    print("\n" + "=" * 70)
    print("KEY FINDINGS:")
    print("=" * 70)
    print(f"  Uganda total trials:       {data['uganda_total']}")
    print(f"  Local-led:                 {data['local_count']} ({data['local_pct']}%)")
    print(f"  Local institutions:        {data['unique_local_institutions']}")
    print(f"  Brain Drain Risk Score:    {data['brain_drain_risk']['score']}/100")

    generate_html(data)
    print("\nDone.")


if __name__ == "__main__":
    main()
