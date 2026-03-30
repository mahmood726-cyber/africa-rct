#!/usr/bin/env python
"""
fetch_trial_lifecycle.py -- Project 62: The Trial Lifecycle
============================================================
From Registration to Patient Impact -- inspired by Surah Al-Isra (17:1),
the Night Journey. Like the Prophet's miraculous journey from Makkah to
Jerusalem, trace the complete journey of every Ugandan trial from
registration to patient access. Most trials in Africa never complete
this journey.

Loads Uganda's 783 trial records from data/uganda_collected_data.json
and computes lifecycle stage, drop-off analysis, and funnel metrics.

Usage:
    python fetch_trial_lifecycle.py

Outputs:
    data/trial_lifecycle_data.json  -- lifecycle analysis cache
    trial-lifecycle.html            -- dark-theme interactive dashboard

Requirements:
    Python 3.8+, no external packages needed (reads cached data)
"""

import json
import io
import math
import os
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

# Fix Windows cp1252 encoding issues
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
UGANDA_DATA = DATA_DIR / "uganda_collected_data.json"
CACHE_FILE = DATA_DIR / "trial_lifecycle_data.json"
OUTPUT_HTML = PROJECT_DIR / "trial-lifecycle.html"

# WHO Essential Medicines List (2023) + known ARVs available in Uganda
# These represent interventions actually accessible to Ugandan patients
WHO_EML_INTERVENTIONS = {
    # Antiretrovirals (widely available via PEPFAR/Global Fund)
    "dolutegravir", "tenofovir", "lamivudine", "efavirenz", "emtricitabine",
    "abacavir", "zidovudine", "nevirapine", "lopinavir", "ritonavir",
    "atazanavir", "darunavir", "raltegravir", "prep", "art",
    # Antimalarials
    "artemether", "lumefantrine", "artesunate", "chloroquine", "quinine",
    "sulfadoxine", "pyrimethamine", "amodiaquine", "mefloquine",
    "dihydroartemisinin", "piperaquine",
    # Anti-TB
    "isoniazid", "rifampicin", "rifampin", "pyrazinamide", "ethambutol",
    "bedaquiline", "delamanid", "linezolid", "moxifloxacin",
    # Vaccines (available in Uganda EPI)
    "vaccine", "vaccination", "immunization", "bcg", "opv", "ipv",
    "pentavalent", "measles", "rubella", "hpv vaccine", "rotavirus",
    "pneumococcal", "yellow fever",
    # Essential antibiotics
    "amoxicillin", "ampicillin", "gentamicin", "metronidazole",
    "ciprofloxacin", "azithromycin", "ceftriaxone", "cotrimoxazole",
    # Maternal/neonatal
    "oxytocin", "misoprostol", "magnesium sulfate", "dexamethasone",
    "chlorhexidine", "iron", "folic acid", "folate",
    # NCD basics (limited but present)
    "metformin", "insulin", "hydrochlorothiazide", "amlodipine",
    "enalapril", "atenolol", "aspirin", "simvastatin",
    "salbutamol", "beclomethasone", "prednisolone",
    # Analgesics/anaesthetics
    "morphine", "paracetamol", "ibuprofen", "ketamine", "lidocaine",
    # Antiparasitics
    "albendazole", "mebendazole", "praziquantel", "ivermectin",
    # Nutritional
    "zinc", "oral rehydration", "ors", "rutf",
    "vitamin a", "vitamin d",
}

# Condition keywords mapping to broad categories
CONDITION_CATEGORIES = {
    "HIV": ["hiv", "aids", "antiretroviral", "prep", "human immunodeficiency"],
    "Malaria": ["malaria", "plasmodium"],
    "Tuberculosis": ["tuberculosis", "tb ", "pulmonary tb"],
    "Cancer": ["cancer", "neoplasm", "tumor", "tumour", "oncol", "carcinoma",
               "lymphoma", "leukemia", "sarcoma"],
    "Cardiovascular": ["cardiovascular", "heart", "cardiac", "hypertension",
                       "stroke", "rheumatic heart"],
    "Maternal/Neonatal": ["maternal", "pregnancy", "pregnant", "neonatal",
                          "newborn", "obstetric", "postpartum", "antenatal"],
    "Mental Health": ["mental", "depression", "anxiety", "psychiatric",
                      "psycholog", "ptsd", "schizophrenia"],
    "Nutrition": ["nutrition", "malnutrition", "anemia", "anaemia",
                  "stunting", "wasting", "underweight"],
    "Infectious Other": ["pneumonia", "meningitis", "sepsis", "diarrhea",
                         "diarrhoea", "hepatitis", "covid"],
    "NCD Other": ["diabetes", "epilepsy", "sickle cell", "kidney", "renal",
                  "liver", "copd", "asthma"],
    "Surgery/Trauma": ["surgical", "surgery", "trauma", "fracture", "wound",
                       "burn"],
    "Vaccine": ["vaccine", "vaccination", "immunization"],
}


# ---------------------------------------------------------------------------
# Data loading and lifecycle computation
# ---------------------------------------------------------------------------

def load_uganda_data():
    """Load Uganda trial data from cached JSON."""
    if not UGANDA_DATA.exists():
        print(f"ERROR: Uganda data file not found at {UGANDA_DATA}")
        print("Run fetch_uganda_rcts.py first to generate the data.")
        sys.exit(1)

    with open(UGANDA_DATA, "r", encoding="utf-8") as f:
        data = json.load(f)

    trials = data.get("sample_trials", [])
    print(f"Loaded {len(trials)} Uganda trials from cache.")
    return data, trials


def parse_date(date_str):
    """Parse date string in various formats, return datetime or None."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def classify_sponsor(sponsor, sponsor_class):
    """Classify sponsor as local (Ugandan) or foreign."""
    if not sponsor:
        return "foreign"
    s_lower = sponsor.lower()
    local_keywords = [
        "makerere", "uganda", "mulago", "kampala", "mbarara",
        "gulu", "kabale", "lira", "jinja", "soroti",
        "infectious diseases institute", "idi ",
        "joint clinical research", "jcrc",
    ]
    for kw in local_keywords:
        if kw in s_lower:
            return "local"
    # sponsor_class INDIV could be local PI
    if sponsor_class == "INDIV":
        return "ambiguous"
    return "foreign"


def categorize_condition(conditions):
    """Map condition list to broad category."""
    if not conditions:
        return "Other"
    cond_str = " ".join(conditions).lower()
    for category, keywords in CONDITION_CATEGORIES.items():
        for kw in keywords:
            if kw in cond_str:
                return category
    return "Other"


def check_intervention_access(conditions, title):
    """Check if tested intervention is likely available in Uganda."""
    text = " ".join(conditions or []).lower() + " " + (title or "").lower()
    for drug in WHO_EML_INTERVENTIONS:
        if drug in text:
            return True
    # HIV/malaria/TB trials likely test accessible interventions
    for marker in ["hiv", "malaria", "tuberculosis", "vaccine", "nutrition"]:
        if marker in text:
            return True
    return False


def compute_lifecycle(trial):
    """Compute lifecycle stages for a single trial.

    Stage 1: Registration -- all trials have this (they are in the registry)
    Stage 2: Enrollment   -- enrollment > 0
    Stage 3: Completion   -- status is COMPLETED
    Stage 4: Results      -- proxy: status is not UNKNOWN (trial has update)
    Stage 5: Publication  -- proxy: COMPLETED and not UNKNOWN
    Stage 6: Access       -- intervention likely available in Uganda (WHO EML)
    """
    nct_id = trial.get("nct_id", "")
    title = trial.get("title", "")
    status = trial.get("status", "UNKNOWN")
    enrollment = trial.get("enrollment", 0) or 0
    start_date_str = trial.get("start_date", "")
    conditions = trial.get("conditions", [])
    sponsor = trial.get("sponsor", "")
    sponsor_class = trial.get("sponsor_class", "")
    phases = trial.get("phases", [])

    start_dt = parse_date(start_date_str)

    stages = {
        "registered": True,  # Stage 1: all trials are registered
        "enrolled": enrollment > 0,  # Stage 2
        "completed": status == "COMPLETED",  # Stage 3
        "results_reported": status not in ("UNKNOWN", ""),  # Stage 4
        "published": status == "COMPLETED" and status != "UNKNOWN",  # Stage 5
        "accessible": False,  # Stage 6
    }

    # Stage 6: check if the intervention is available in Uganda
    if stages["completed"]:
        stages["accessible"] = check_intervention_access(conditions, title)

    # Lifecycle score = count of stages completed
    stage_list = ["registered", "enrolled", "completed",
                  "results_reported", "published", "accessible"]
    lifecycle_score = sum(1 for s in stage_list if stages[s])

    # Drop-off stage = first stage that failed
    dropoff_stage = None
    for i, s in enumerate(stage_list):
        if not stages[s]:
            dropoff_stage = i + 1  # 1-indexed
            break
    if dropoff_stage is None:
        dropoff_stage = 0  # completed full journey

    # Journey length (years from start to now)
    journey_years = None
    if start_dt:
        delta = datetime(2026, 3, 27) - start_dt
        journey_years = round(delta.days / 365.25, 1)

    # Classify sponsor
    sponsor_type = classify_sponsor(sponsor, sponsor_class)

    # Categorize condition
    condition_category = categorize_condition(conditions)

    # Phase (simplify)
    phase_label = "NA"
    for p in phases:
        if p in ("PHASE1", "EARLY_PHASE1"):
            phase_label = "Phase 1"
        elif p == "PHASE2":
            phase_label = "Phase 2"
        elif p == "PHASE3":
            phase_label = "Phase 3"
        elif p == "PHASE4":
            phase_label = "Phase 4"

    return {
        "nct_id": nct_id,
        "title": title,
        "sponsor": sponsor,
        "sponsor_type": sponsor_type,
        "status": status,
        "enrollment": enrollment,
        "start_date": start_date_str,
        "start_year": start_dt.year if start_dt else None,
        "conditions": conditions,
        "condition_category": condition_category,
        "phase": phase_label,
        "stages": stages,
        "lifecycle_score": lifecycle_score,
        "dropoff_stage": dropoff_stage,
        "journey_years": journey_years,
    }


def compute_aggregates(lifecycle_trials):
    """Compute aggregate statistics across all trials."""
    n = len(lifecycle_trials)
    stage_names = ["Registration", "Enrollment", "Completion",
                   "Results", "Publication", "Access"]
    stage_keys = ["registered", "enrolled", "completed",
                  "results_reported", "published", "accessible"]

    # Stage-by-stage survival
    stage_counts = []
    for key in stage_keys:
        count = sum(1 for t in lifecycle_trials if t["stages"][key])
        stage_counts.append(count)

    funnel = []
    for i, (name, count) in enumerate(zip(stage_names, stage_counts)):
        pct = round(count / n * 100, 1) if n > 0 else 0
        funnel.append({
            "stage": name,
            "stage_num": i + 1,
            "count": count,
            "pct_of_total": pct,
            "pct_of_previous": round(count / stage_counts[i - 1] * 100, 1) if i > 0 and stage_counts[i - 1] > 0 else 100.0,
        })

    # Drop-off distribution
    dropoff_dist = Counter()
    for t in lifecycle_trials:
        dropoff_dist[t["dropoff_stage"]] += 1

    dropoff_labels = {
        0: "Full Journey",
        1: "Never Registered",  # shouldn't happen
        2: "No Enrollment",
        3: "Not Completed",
        4: "No Results Update",
        5: "Not Published",
        6: "Not Accessible",
    }
    dropoff_analysis = []
    for stage_num in range(7):
        count = dropoff_dist.get(stage_num, 0)
        dropoff_analysis.append({
            "stage": stage_num,
            "label": dropoff_labels[stage_num],
            "count": count,
            "pct": round(count / n * 100, 1) if n > 0 else 0,
        })

    # Lifecycle score distribution
    score_dist = Counter(t["lifecycle_score"] for t in lifecycle_trials)

    # Stratify by sponsor type
    sponsor_strat = defaultdict(lambda: {"total": 0, "scores": [], "completed": 0, "full_journey": 0})
    for t in lifecycle_trials:
        st = t["sponsor_type"]
        sponsor_strat[st]["total"] += 1
        sponsor_strat[st]["scores"].append(t["lifecycle_score"])
        if t["stages"]["completed"]:
            sponsor_strat[st]["completed"] += 1
        if t["dropoff_stage"] == 0:
            sponsor_strat[st]["full_journey"] += 1

    sponsor_results = {}
    for st, data in sponsor_strat.items():
        avg_score = round(sum(data["scores"]) / len(data["scores"]), 2) if data["scores"] else 0
        sponsor_results[st] = {
            "total": data["total"],
            "avg_lifecycle_score": avg_score,
            "completion_rate": round(data["completed"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
            "full_journey_rate": round(data["full_journey"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
            "funnel": [],
        }
        # Per-sponsor funnel
        for i, key in enumerate(stage_keys):
            count = sum(1 for t in lifecycle_trials if t["sponsor_type"] == st and t["stages"][key])
            pct = round(count / data["total"] * 100, 1) if data["total"] > 0 else 0
            sponsor_results[st]["funnel"].append({
                "stage": stage_names[i],
                "count": count,
                "pct": pct,
            })

    # Stratify by phase
    phase_strat = defaultdict(lambda: {"total": 0, "scores": [], "completed": 0})
    for t in lifecycle_trials:
        ph = t["phase"]
        phase_strat[ph]["total"] += 1
        phase_strat[ph]["scores"].append(t["lifecycle_score"])
        if t["stages"]["completed"]:
            phase_strat[ph]["completed"] += 1

    phase_results = {}
    for ph, data in phase_strat.items():
        avg_score = round(sum(data["scores"]) / len(data["scores"]), 2) if data["scores"] else 0
        phase_results[ph] = {
            "total": data["total"],
            "avg_lifecycle_score": avg_score,
            "completion_rate": round(data["completed"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
        }

    # Stratify by condition category
    condition_strat = defaultdict(lambda: {"total": 0, "scores": [], "completed": 0, "accessible": 0})
    for t in lifecycle_trials:
        cat = t["condition_category"]
        condition_strat[cat]["total"] += 1
        condition_strat[cat]["scores"].append(t["lifecycle_score"])
        if t["stages"]["completed"]:
            condition_strat[cat]["completed"] += 1
        if t["stages"]["accessible"]:
            condition_strat[cat]["accessible"] += 1

    condition_results = {}
    for cat, data in condition_strat.items():
        avg_score = round(sum(data["scores"]) / len(data["scores"]), 2) if data["scores"] else 0
        condition_results[cat] = {
            "total": data["total"],
            "avg_lifecycle_score": avg_score,
            "completion_rate": round(data["completed"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
            "access_rate": round(data["accessible"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
        }

    # The Results Graveyard: completed but no meaningful result update
    graveyard = [t for t in lifecycle_trials
                 if t["stages"]["completed"] and not t["stages"]["accessible"]]

    # The Access Chasm: completed + results but drug not available
    completed_trials = [t for t in lifecycle_trials if t["stages"]["completed"]]
    access_chasm = [t for t in completed_trials if not t["stages"]["accessible"]]

    # Time analysis: average journey length by stage reached
    time_by_max_stage = defaultdict(list)
    for t in lifecycle_trials:
        if t["journey_years"] is not None and t["journey_years"] > 0:
            time_by_max_stage[t["lifecycle_score"]].append(t["journey_years"])

    time_analysis = {}
    for score, years_list in sorted(time_by_max_stage.items()):
        time_analysis[str(score)] = {
            "n": len(years_list),
            "mean_years": round(sum(years_list) / len(years_list), 1),
            "min_years": round(min(years_list), 1),
            "max_years": round(max(years_list), 1),
        }

    # Best journeys: trials that completed the full lifecycle (score = 6)
    best_journeys = sorted(
        [t for t in lifecycle_trials if t["lifecycle_score"] == 6],
        key=lambda t: t.get("enrollment", 0),
        reverse=True
    )[:20]

    # Temporal trends: lifecycle score by start year
    year_trends = defaultdict(lambda: {"total": 0, "scores": []})
    for t in lifecycle_trials:
        yr = t.get("start_year")
        if yr and yr >= 2000:
            year_trends[yr]["total"] += 1
            year_trends[yr]["scores"].append(t["lifecycle_score"])

    temporal = {}
    for yr in sorted(year_trends.keys()):
        data = year_trends[yr]
        avg_score = round(sum(data["scores"]) / len(data["scores"]), 2) if data["scores"] else 0
        temporal[str(yr)] = {
            "total": data["total"],
            "avg_score": avg_score,
        }

    return {
        "n_trials": n,
        "funnel": funnel,
        "dropoff_analysis": dropoff_analysis,
        "score_distribution": {str(k): v for k, v in sorted(score_dist.items())},
        "sponsor_stratification": sponsor_results,
        "phase_stratification": phase_results,
        "condition_stratification": condition_results,
        "results_graveyard_count": len(graveyard),
        "access_chasm_count": len(access_chasm),
        "completed_count": len(completed_trials),
        "time_analysis": time_analysis,
        "best_journeys": [{
            "nct_id": t["nct_id"],
            "title": t["title"],
            "sponsor": t["sponsor"],
            "sponsor_type": t["sponsor_type"],
            "enrollment": t["enrollment"],
            "condition_category": t["condition_category"],
            "phase": t["phase"],
            "journey_years": t["journey_years"],
        } for t in best_journeys],
        "temporal_trends": temporal,
    }


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


def stage_color(stage_num):
    """Color for lifecycle stage (1-6)."""
    colors = {
        1: "#60a5fa",   # blue - registration
        2: "#a78bfa",   # purple - enrollment
        3: "#34d399",   # green - completion
        4: "#fbbf24",   # amber - results
        5: "#f472b6",   # pink - publication
        6: "#22d3ee",   # cyan - access
    }
    return colors.get(stage_num, "#888")


def score_color(score):
    """Color based on lifecycle score (0-6)."""
    if score >= 6:
        return "#22c55e"
    elif score >= 4:
        return "#eab308"
    elif score >= 2:
        return "#f97316"
    else:
        return "#ef4444"


def pct_bar(pct, color, width_px=200):
    """Return inline HTML for a percentage bar."""
    bar_w = min(pct, 100)
    return (
        f'<div style="display:inline-block;background:rgba(255,255,255,0.06);'
        f'border-radius:4px;height:16px;width:{width_px}px;vertical-align:middle;">'
        f'<div style="background:{color};height:100%;width:{bar_w}%;'
        f'border-radius:4px;transition:width 0.5s;"></div></div>'
    )


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(aggregates, lifecycle_trials):
    """Generate the full HTML dashboard."""

    n = aggregates["n_trials"]
    funnel = aggregates["funnel"]
    dropoff = aggregates["dropoff_analysis"]
    score_dist = aggregates["score_distribution"]
    sponsor_strat = aggregates["sponsor_stratification"]
    phase_strat = aggregates["phase_stratification"]
    condition_strat = aggregates["condition_stratification"]
    time_data = aggregates["time_analysis"]
    best = aggregates["best_journeys"]
    temporal = aggregates["temporal_trends"]
    graveyard_count = aggregates["results_graveyard_count"]
    access_chasm_count = aggregates["access_chasm_count"]
    completed_count = aggregates["completed_count"]

    # ====================================================================
    # FUNNEL ROWS
    # ====================================================================
    funnel_rows = ""
    for f in funnel:
        sc = stage_color(f["stage_num"])
        bar = pct_bar(f["pct_of_total"], sc, 250)
        funnel_rows += f"""<tr>
  <td style="padding:12px 16px;font-weight:bold;color:{sc};font-size:1.1rem;">
    Stage {f["stage_num"]}</td>
  <td style="padding:12px 16px;font-weight:bold;">{escape_html(f["stage"])}</td>
  <td style="padding:12px 16px;text-align:right;font-size:1.3rem;font-weight:bold;
    color:{sc};">{f["count"]:,}</td>
  <td style="padding:12px 16px;text-align:right;">{f["pct_of_total"]}%</td>
  <td style="padding:12px 16px;text-align:right;color:#94a3b8;font-size:0.85rem;">
    {f["pct_of_previous"]}%</td>
  <td style="padding:12px 16px;">{bar}</td>
</tr>
"""

    # ====================================================================
    # VISUAL FUNNEL (CONSORT-style narrowing)
    # ====================================================================
    funnel_visual = ""
    max_width = 700
    for f in funnel:
        sc = stage_color(f["stage_num"])
        w = max(int(max_width * f["pct_of_total"] / 100), 80)
        funnel_visual += f"""
<div style="text-align:center;margin:4px 0;">
  <div style="display:inline-block;background:linear-gradient(135deg, {sc}33, {sc}11);
    border:2px solid {sc};border-radius:8px;width:{w}px;padding:12px 20px;
    position:relative;">
    <div style="font-size:0.75rem;color:#94a3b8;text-transform:uppercase;
      letter-spacing:1px;">Stage {f["stage_num"]}: {escape_html(f["stage"])}</div>
    <div style="font-size:1.8rem;font-weight:bold;color:{sc};">{f["count"]:,}</div>
    <div style="font-size:0.85rem;color:#cbd5e1;">{f["pct_of_total"]}% of total</div>
  </div>
</div>
<div style="text-align:center;color:#475569;font-size:1.2rem;">&#x25BC;</div>
"""

    # ====================================================================
    # DROPOFF ANALYSIS ROWS
    # ====================================================================
    dropoff_rows = ""
    for d in dropoff:
        if d["stage"] == 0:
            label_color = "#22c55e"
            icon = "&#x2714;"
        elif d["stage"] == 1:
            continue  # skip "never registered"
        else:
            label_color = "#ef4444"
            icon = "&#x2718;"
        bar = pct_bar(d["pct"], label_color, 200)
        dropoff_rows += f"""<tr>
  <td style="padding:10px 14px;color:{label_color};font-size:1.1rem;">{icon}</td>
  <td style="padding:10px 14px;font-weight:bold;">{escape_html(d["label"])}</td>
  <td style="padding:10px 14px;text-align:right;font-weight:bold;
    color:{label_color};">{d["count"]:,}</td>
  <td style="padding:10px 14px;text-align:right;">{d["pct"]}%</td>
  <td style="padding:10px 14px;">{bar}</td>
</tr>
"""

    # ====================================================================
    # SCORE DISTRIBUTION
    # ====================================================================
    score_rows = ""
    for score_str in sorted(score_dist.keys(), key=int):
        score = int(score_str)
        count = score_dist[score_str]
        pct = round(count / n * 100, 1)
        sc = score_color(score)
        bar = pct_bar(pct, sc, 200)
        label = f"Score {score}/6"
        score_rows += f"""<tr>
  <td style="padding:8px 14px;font-weight:bold;color:{sc};">{label}</td>
  <td style="padding:8px 14px;text-align:right;font-weight:bold;">{count:,}</td>
  <td style="padding:8px 14px;text-align:right;">{pct}%</td>
  <td style="padding:8px 14px;">{bar}</td>
</tr>
"""

    # ====================================================================
    # SPONSOR STRATIFICATION
    # ====================================================================
    sponsor_rows = ""
    for st in ["local", "foreign", "ambiguous"]:
        if st not in sponsor_strat:
            continue
        data = sponsor_strat[st]
        sc = "#22c55e" if st == "local" else ("#f97316" if st == "foreign" else "#94a3b8")
        sponsor_rows += f"""<tr style="background:rgba(255,255,255,0.02);">
  <td style="padding:10px 14px;font-weight:bold;color:{sc};text-transform:capitalize;">
    {escape_html(st)}</td>
  <td style="padding:10px 14px;text-align:right;">{data["total"]:,}</td>
  <td style="padding:10px 14px;text-align:right;font-weight:bold;">
    {data["avg_lifecycle_score"]}</td>
  <td style="padding:10px 14px;text-align:right;">{data["completion_rate"]}%</td>
  <td style="padding:10px 14px;text-align:right;color:{sc};font-weight:bold;">
    {data["full_journey_rate"]}%</td>
</tr>
"""

    # Sponsor funnel comparison
    sponsor_funnel_rows = ""
    stage_names_list = ["Registration", "Enrollment", "Completion",
                        "Results", "Publication", "Access"]
    for i, stage_name in enumerate(stage_names_list):
        sc = stage_color(i + 1)
        cells = f'<td style="padding:8px 12px;font-weight:bold;color:{sc};">{escape_html(stage_name)}</td>'
        for st in ["local", "foreign"]:
            if st in sponsor_strat:
                sf = sponsor_strat[st]["funnel"]
                if i < len(sf):
                    cells += f'<td style="padding:8px 12px;text-align:right;">{sf[i]["count"]:,} ({sf[i]["pct"]}%)</td>'
                else:
                    cells += '<td style="padding:8px 12px;text-align:right;">-</td>'
            else:
                cells += '<td style="padding:8px 12px;text-align:right;">-</td>'
        sponsor_funnel_rows += f"<tr>{cells}</tr>\n"

    # ====================================================================
    # CONDITION STRATIFICATION
    # ====================================================================
    condition_rows = ""
    sorted_conditions = sorted(condition_strat.items(), key=lambda x: -x[1]["total"])
    for cat, data in sorted_conditions:
        sc = score_color(int(data["avg_lifecycle_score"]))
        condition_rows += f"""<tr>
  <td style="padding:8px 12px;font-weight:bold;">{escape_html(cat)}</td>
  <td style="padding:8px 12px;text-align:right;">{data["total"]:,}</td>
  <td style="padding:8px 12px;text-align:right;font-weight:bold;color:{sc};">
    {data["avg_lifecycle_score"]}</td>
  <td style="padding:8px 12px;text-align:right;">{data["completion_rate"]}%</td>
  <td style="padding:8px 12px;text-align:right;font-weight:bold;">
    {data["access_rate"]}%</td>
</tr>
"""

    # ====================================================================
    # TIME ANALYSIS
    # ====================================================================
    time_rows = ""
    for score_str in sorted(time_data.keys(), key=int):
        td = time_data[score_str]
        sc = score_color(int(score_str))
        time_rows += f"""<tr>
  <td style="padding:8px 12px;font-weight:bold;color:{sc};">Score {score_str}</td>
  <td style="padding:8px 12px;text-align:right;">{td["n"]:,}</td>
  <td style="padding:8px 12px;text-align:right;font-weight:bold;">{td["mean_years"]} yrs</td>
  <td style="padding:8px 12px;text-align:right;">{td["min_years"]} yrs</td>
  <td style="padding:8px 12px;text-align:right;">{td["max_years"]} yrs</td>
</tr>
"""

    # ====================================================================
    # BEST JOURNEYS
    # ====================================================================
    best_rows = ""
    for i, t in enumerate(best[:15], 1):
        title_short = t["title"][:80] + ("..." if len(t["title"]) > 80 else "")
        jy = f'{t["journey_years"]} yrs' if t["journey_years"] else "N/A"
        best_rows += f"""<tr>
  <td style="padding:8px 10px;text-align:center;color:#22c55e;font-weight:bold;">{i}</td>
  <td style="padding:8px 10px;font-size:0.8rem;color:#94a3b8;">{escape_html(t["nct_id"])}</td>
  <td style="padding:8px 10px;font-size:0.85rem;">{escape_html(title_short)}</td>
  <td style="padding:8px 10px;text-align:right;">{t["enrollment"]:,}</td>
  <td style="padding:8px 10px;text-align:center;">{escape_html(t["condition_category"])}</td>
  <td style="padding:8px 10px;text-align:center;">{escape_html(t["sponsor_type"])}</td>
  <td style="padding:8px 10px;text-align:right;">{jy}</td>
</tr>
"""

    # ====================================================================
    # PHASE STRATIFICATION
    # ====================================================================
    phase_rows = ""
    phase_order = ["Phase 1", "Phase 2", "Phase 3", "Phase 4", "NA"]
    for ph in phase_order:
        if ph not in phase_strat:
            continue
        data = phase_strat[ph]
        sc = score_color(int(data["avg_lifecycle_score"]))
        phase_rows += f"""<tr>
  <td style="padding:8px 12px;font-weight:bold;">{escape_html(ph)}</td>
  <td style="padding:8px 12px;text-align:right;">{data["total"]:,}</td>
  <td style="padding:8px 12px;text-align:right;font-weight:bold;color:{sc};">
    {data["avg_lifecycle_score"]}</td>
  <td style="padding:8px 12px;text-align:right;">{data["completion_rate"]}%</td>
</tr>
"""

    # ====================================================================
    # TEMPORAL CHART DATA (for inline SVG sparkline)
    # ====================================================================
    temporal_rows = ""
    years = sorted(temporal.keys(), key=int)
    for yr in years:
        td = temporal[yr]
        sc = score_color(int(td["avg_score"]))
        bar_w = min(td["total"] * 3, 200)
        temporal_rows += f"""<tr>
  <td style="padding:6px 10px;font-weight:bold;">{yr}</td>
  <td style="padding:6px 10px;text-align:right;">{td["total"]}</td>
  <td style="padding:6px 10px;text-align:right;color:{sc};font-weight:bold;">
    {td["avg_score"]}</td>
  <td style="padding:6px 10px;">
    <div style="background:{sc};height:12px;width:{bar_w}px;border-radius:3px;
      opacity:0.7;"></div></td>
</tr>
"""

    # ====================================================================
    # GRAVEYARD + ACCESS CHASM summary
    # ====================================================================
    graveyard_pct = round(graveyard_count / n * 100, 1) if n > 0 else 0
    chasm_pct = round(access_chasm_count / completed_count * 100, 1) if completed_count > 0 else 0
    completed_pct = round(completed_count / n * 100, 1) if n > 0 else 0

    # ====================================================================
    # ASSEMBLE HTML
    # ====================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Trial Lifecycle -- From Registration to Patient Impact (Uganda)</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #0a0f1a; color: #e2e8f0;
    line-height: 1.6;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 20px 24px; }}
  h1 {{
    text-align: center; font-size: 2.2rem; margin: 40px 0 8px;
    background: linear-gradient(135deg, #60a5fa, #22d3ee, #a78bfa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  .subtitle {{
    text-align: center; color: #94a3b8; font-size: 1.05rem; margin-bottom: 10px;
    font-style: italic;
  }}
  .epigraph {{
    text-align: center; color: #64748b; font-size: 0.9rem;
    max-width: 700px; margin: 0 auto 32px;
    border-left: 3px solid #334155; padding-left: 16px;
    text-align: left;
  }}
  .section {{
    background: #111827; border: 1px solid #1e293b; border-radius: 12px;
    padding: 28px; margin: 28px 0;
  }}
  .section h2 {{
    font-size: 1.4rem; margin: 0 0 6px; color: #f1f5f9;
  }}
  .section h3 {{
    font-size: 1.1rem; margin: 20px 0 10px; color: #cbd5e1;
  }}
  .section-desc {{
    color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px;
  }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 0.9rem;
  }}
  th {{
    text-align: left; padding: 10px 14px; color: #94a3b8;
    border-bottom: 2px solid #1e293b; font-weight: 600;
    font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px;
  }}
  td {{ border-bottom: 1px solid #1e293b33; }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin: 20px 0;
  }}
  .kpi {{
    background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
    padding: 20px; text-align: center;
  }}
  .kpi-value {{
    font-size: 2.2rem; font-weight: bold; margin: 4px 0;
  }}
  .kpi-label {{
    font-size: 0.8rem; color: #94a3b8; text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .kpi-sub {{
    font-size: 0.75rem; color: #64748b; margin-top: 4px;
  }}
  .funnel-container {{
    margin: 24px 0;
  }}
  .tag {{
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.75rem; font-weight: 600;
  }}
  .alert-box {{
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.3);
    border-radius: 10px; padding: 20px; margin: 16px 0;
  }}
  .success-box {{
    background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.3);
    border-radius: 10px; padding: 20px; margin: 16px 0;
  }}
  .footer {{
    text-align: center; color: #475569; font-size: 0.8rem;
    margin-top: 40px; padding: 20px 0 40px;
    border-top: 1px solid #1e293b;
  }}
  @media (max-width: 768px) {{
    .container {{ padding: 12px; }}
    h1 {{ font-size: 1.5rem; }}
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
    table {{ font-size: 0.8rem; }}
  }}
</style>
</head>
<body>
<div class="container">

<!-- ================================================================ -->
<!-- HEADER -->
<!-- ================================================================ -->
<h1>The Trial Lifecycle</h1>
<div class="subtitle">From Registration to Patient Impact -- Uganda's 783 Clinical Trials</div>
<div class="epigraph">
  <strong>"Glory be to the One Who took His servant by night from the Sacred Mosque
  to the Farthest Mosque."</strong> -- Surah Al-Isra (17:1)<br><br>
  Like the Prophet's miraculous Night Journey, every clinical trial must travel
  a long path from registration to patient impact. In Uganda, most trials never
  complete this journey. We trace where they fall.
</div>

<!-- ================================================================ -->
<!-- 1. KEY METRICS -->
<!-- ================================================================ -->
<div class="section">
  <h2>1. The Journey at a Glance</h2>
  <p class="section-desc">
    Of {n:,} registered Ugandan trials, how many complete each stage of the
    lifecycle from registration through to patient access?
  </p>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Total Trials</div>
      <div class="kpi-value" style="color:#60a5fa;">{n:,}</div>
      <div class="kpi-sub">Registered on ClinicalTrials.gov</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Completed</div>
      <div class="kpi-value" style="color:#34d399;">{completed_count:,}</div>
      <div class="kpi-sub">{completed_pct}% of registered</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Results Graveyard</div>
      <div class="kpi-value" style="color:#ef4444;">{graveyard_count:,}</div>
      <div class="kpi-sub">{graveyard_pct}% of all trials</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Access Chasm</div>
      <div class="kpi-value" style="color:#f97316;">{access_chasm_count:,}</div>
      <div class="kpi-sub">{chasm_pct}% of completed trials</div>
    </div>
  </div>
</div>

<!-- ================================================================ -->
<!-- 2. THE FUNNEL -->
<!-- ================================================================ -->
<div class="section">
  <h2>2. The Funnel: 783 Trials Narrowing at Each Stage</h2>
  <p class="section-desc">
    A CONSORT-style funnel showing how many trials reach each lifecycle stage.
    Each row shows the absolute count, percentage of the original 783, and the
    step-wise survival from the previous stage.
  </p>

  <div class="funnel-container">
    {funnel_visual}
  </div>

  <table>
    <thead><tr>
      <th>Stage</th><th>Name</th>
      <th style="text-align:right;">Count</th>
      <th style="text-align:right;">% of Total</th>
      <th style="text-align:right;">% of Previous</th>
      <th>Visual</th>
    </tr></thead>
    <tbody>
      {funnel_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 3. STAGE-BY-STAGE SURVIVAL -->
<!-- ================================================================ -->
<div class="section">
  <h2>3. Lifecycle Score Distribution</h2>
  <p class="section-desc">
    Each trial receives a score from 0 to 6 based on how many lifecycle stages
    it has completed. A score of 6 means the trial completed the full journey
    from registration to patient access.
  </p>
  <table>
    <thead><tr>
      <th>Score</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Percentage</th>
      <th>Distribution</th>
    </tr></thead>
    <tbody>
      {score_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 4. DROP-OFF ANALYSIS -->
<!-- ================================================================ -->
<div class="section">
  <h2>4. Drop-Off Analysis: Where Do Trials Die?</h2>
  <p class="section-desc">
    For each trial, we identify the first lifecycle stage it failed to reach.
    This reveals the critical bottlenecks in the Ugandan trial ecosystem.
  </p>
  <table>
    <thead><tr>
      <th></th><th>Drop-Off Point</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Percentage</th>
      <th>Visual</th>
    </tr></thead>
    <tbody>
      {dropoff_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 5. LOCAL VS FOREIGN LIFECYCLE -->
<!-- ================================================================ -->
<div class="section">
  <h2>5. Stratification: Local vs Foreign Lifecycle Completion</h2>
  <p class="section-desc">
    Do locally sponsored trials complete the lifecycle journey at the same rate
    as foreign-sponsored ones? This comparison reveals structural dependency.
  </p>

  <h3>Summary by Sponsor Type</h3>
  <table>
    <thead><tr>
      <th>Sponsor Type</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Avg Score</th>
      <th style="text-align:right;">Completion %</th>
      <th style="text-align:right;">Full Journey %</th>
    </tr></thead>
    <tbody>
      {sponsor_rows}
    </tbody>
  </table>

  <h3>Funnel Comparison: Local vs Foreign</h3>
  <table>
    <thead><tr>
      <th>Stage</th>
      <th style="text-align:right;">Local</th>
      <th style="text-align:right;">Foreign</th>
    </tr></thead>
    <tbody>
      {sponsor_funnel_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 6. CONDITION STRATIFICATION -->
<!-- ================================================================ -->
<div class="section">
  <h2>6. Lifecycle by Disease Category</h2>
  <p class="section-desc">
    HIV trials dominate Uganda's portfolio. But do they also dominate the
    lifecycle journey? Which conditions have the best and worst completion-to-access
    pipelines?
  </p>
  <table>
    <thead><tr>
      <th>Condition</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Avg Score</th>
      <th style="text-align:right;">Completion %</th>
      <th style="text-align:right;">Access %</th>
    </tr></thead>
    <tbody>
      {condition_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 7. THE RESULTS GRAVEYARD -->
<!-- ================================================================ -->
<div class="section">
  <h2>7. The Results Graveyard</h2>
  <div class="alert-box">
    <p style="margin:0;font-size:1.1rem;">
      <strong style="color:#ef4444;">{graveyard_count:,} trials</strong> completed
      their study but the tested intervention is not accessible to patients in Uganda.
      These trials extracted data from Ugandan communities but returned no tangible
      benefit to the population that bore the research burden.
    </p>
    <p style="margin:12px 0 0;color:#94a3b8;font-size:0.9rem;">
      {graveyard_pct}% of all {n:,} registered trials sit in this graveyard --
      completed, perhaps published, but delivering no patient impact in Uganda.
    </p>
  </div>
</div>

<!-- ================================================================ -->
<!-- 8. THE ACCESS CHASM -->
<!-- ================================================================ -->
<div class="section">
  <h2>8. The Access Chasm</h2>
  <div class="alert-box">
    <p style="margin:0;font-size:1.1rem;">
      Of <strong style="color:#34d399;">{completed_count:,}</strong> completed trials,
      <strong style="color:#f97316;">{access_chasm_count:,}</strong> tested interventions
      that are <em>not available</em> on the WHO Essential Medicines List or through
      known Ugandan drug access programs.
    </p>
    <p style="margin:12px 0 0;color:#94a3b8;font-size:0.9rem;">
      {chasm_pct}% of completed trials fall into this chasm -- the intervention was tested
      on Ugandan patients, but those same patients cannot access it.
      This is the final and cruelest stage of the lifecycle failure.
    </p>
  </div>
</div>

<!-- ================================================================ -->
<!-- 9. TIME ANALYSIS -->
<!-- ================================================================ -->
<div class="section">
  <h2>9. Time Analysis: How Long Does Each Stage Take?</h2>
  <p class="section-desc">
    Average journey length (years from start date to present) grouped by the
    lifecycle score each trial achieved. Higher scores tend to correlate with
    older trials that had time to complete more stages.
  </p>
  <table>
    <thead><tr>
      <th>Lifecycle Score</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Mean Duration</th>
      <th style="text-align:right;">Min</th>
      <th style="text-align:right;">Max</th>
    </tr></thead>
    <tbody>
      {time_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 10. PHASE STRATIFICATION -->
<!-- ================================================================ -->
<div class="section">
  <h2>10. Lifecycle by Trial Phase</h2>
  <p class="section-desc">
    Early-phase trials rarely reach patient access. Later-phase trials
    theoretically should, but do they in Uganda?
  </p>
  <table>
    <thead><tr>
      <th>Phase</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Avg Score</th>
      <th style="text-align:right;">Completion %</th>
    </tr></thead>
    <tbody>
      {phase_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 11. TEMPORAL TRENDS -->
<!-- ================================================================ -->
<div class="section">
  <h2>11. Temporal Trends: Lifecycle Score Over Time</h2>
  <p class="section-desc">
    How has the average lifecycle score changed across registration years?
    Older trials have had more time to progress; newer trials may still be active.
  </p>
  <table>
    <thead><tr>
      <th>Year</th>
      <th style="text-align:right;">Trials</th>
      <th style="text-align:right;">Avg Score</th>
      <th>Volume</th>
    </tr></thead>
    <tbody>
      {temporal_rows}
    </tbody>
  </table>
</div>

<!-- ================================================================ -->
<!-- 12. THE BEST JOURNEYS -->
<!-- ================================================================ -->
<div class="section">
  <h2>12. The Best Journeys: Full Lifecycle Completion</h2>
  <p class="section-desc">
    These trials completed the full journey from registration to patient access --
    the Night Journey realized. They represent what is possible when the system works.
  </p>
  {"<div class='success-box'><p style='margin:0;color:#94a3b8;'>No trials achieved a perfect lifecycle score of 6.</p></div>" if not best else ""}
  {"<table><thead><tr><th>#</th><th>NCT ID</th><th>Title</th><th style='text-align:right;'>Enrollment</th><th style='text-align:center;'>Category</th><th style='text-align:center;'>Sponsor</th><th style='text-align:right;'>Duration</th></tr></thead><tbody>" + best_rows + "</tbody></table>" if best else ""}
</div>

<!-- ================================================================ -->
<!-- METHODOLOGY -->
<!-- ================================================================ -->
<div class="section" style="border-color:#334155;">
  <h2 style="color:#94a3b8;">Methodology</h2>
  <p style="color:#64748b;font-size:0.85rem;margin:0;">
    <strong>Data source:</strong> ClinicalTrials.gov API v2, {n:,} interventional
    trials listing a Ugandan site (accessed March 2026).<br>
    <strong>Lifecycle stages:</strong> (1) Registration = present in registry;
    (2) Enrollment = enrollment &gt; 0; (3) Completion = status COMPLETED;
    (4) Results = status is not UNKNOWN; (5) Publication = COMPLETED and not
    UNKNOWN; (6) Access = intervention matches WHO EML or known Ugandan drug
    access programs (PEPFAR ARVs, EPI vaccines, essential medicines).<br>
    <strong>Sponsor classification:</strong> keyword matching on sponsor name
    for Ugandan institutions (Makerere, Mulago, IDI, JCRC, etc.).<br>
    <strong>Limitations:</strong> Access assessment uses intervention name matching
    against WHO EML keywords, which is approximate. Actual drug availability
    varies by district. Publication status is proxied from registry metadata,
    not verified against PubMed. Some trials may be registered on other
    registries not captured here.
  </p>
</div>

<div class="footer">
  Project 62: The Trial Lifecycle -- AfricaRCT Series<br>
  Inspired by Surah Al-Isra (17:1) -- the Night Journey<br>
  Data: ClinicalTrials.gov API v2 | Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}
</div>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Project 62: The Trial Lifecycle")
    print("From Registration to Patient Impact -- Uganda")
    print("=" * 70)

    # Load data
    raw_data, trials = load_uganda_data()

    # Compute lifecycle for each trial
    print(f"\nComputing lifecycle stages for {len(trials)} trials...")
    lifecycle_trials = [compute_lifecycle(t) for t in trials]

    # Compute aggregates
    print("Computing aggregate statistics...")
    aggregates = compute_aggregates(lifecycle_trials)

    # Print summary
    print(f"\n--- LIFECYCLE SUMMARY ---")
    print(f"Total trials: {aggregates['n_trials']}")
    print(f"Funnel:")
    for f in aggregates["funnel"]:
        print(f"  Stage {f['stage_num']} ({f['stage']}): "
              f"{f['count']:,} ({f['pct_of_total']}%)")
    print(f"Results graveyard: {aggregates['results_graveyard_count']}")
    print(f"Access chasm: {aggregates['access_chasm_count']}")
    print(f"Score distribution:")
    for k, v in sorted(aggregates["score_distribution"].items(), key=lambda x: int(x[0])):
        print(f"  Score {k}: {v}")

    # Cache results
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_output = {
        "meta": {
            "project": "62-trial-lifecycle",
            "generated": datetime.now().isoformat(),
            "source": "uganda_collected_data.json",
            "n_trials": len(trials),
        },
        "aggregates": aggregates,
        "trials": [{
            "nct_id": t["nct_id"],
            "title": t["title"],
            "sponsor_type": t["sponsor_type"],
            "status": t["status"],
            "condition_category": t["condition_category"],
            "phase": t["phase"],
            "lifecycle_score": t["lifecycle_score"],
            "dropoff_stage": t["dropoff_stage"],
            "journey_years": t["journey_years"],
            "stages": t["stages"],
        } for t in lifecycle_trials],
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_output, f, indent=2, ensure_ascii=False)
    print(f"\nCached lifecycle data to {CACHE_FILE}")

    # Generate HTML
    print("Generating HTML dashboard...")
    html = generate_html(aggregates, lifecycle_trials)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote HTML dashboard to {OUTPUT_HTML}")
    print(f"File size: {os.path.getsize(OUTPUT_HTML):,} bytes")

    print("\nDone.")


if __name__ == "__main__":
    main()
