#!/usr/bin/env python
"""
Africa RCT Research Programme — Living Dashboard Runner
========================================================
Runs all 60 project scripts sequentially, regenerating every dashboard
from live ClinicalTrials.gov data. Updates index.html with a fresh timestamp.

Usage:
    python run_all.py              # Run all 60 projects
    python run_all.py --quick      # Run only the 10 fastest projects
    python run_all.py --layer 1    # Run only Layer 1 (projects 1-14)
    python run_all.py --project 46 # Run a single project by number

Estimated runtime:
    Full run:  ~45-60 minutes (depending on API response times)
    Quick run: ~5-10 minutes
    Single:    ~1-3 minutes
"""

import subprocess
import sys
import os
import time
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

PROJECTS = [
    # (number, script, html_output, layer, title)
    (1,  "fetch_africa_rcts.py",         "africa-rct-analysis.html",         1, "Africa Continent"),
    (2,  "fetch_uganda_rcts.py",         "uganda-rct-analysis.html",         1, "Uganda Deep-Dive"),
    (3,  "fetch_extraction_index.py",    "novel-analysis-extraction-index.html", 1, "Extraction Index"),
    (4,  "fetch_scd_africa.py",          "scd-africa-analysis.html",         1, "SCD Africa"),
    (5,  "ghost_enrollment_audit.py",    "ghost-enrollment-audit.html",      1, "Ghost Enrollment"),
    (6,  "fetch_ncd_gap.py",             "ncd-gap-analysis.html",            1, "NCD Trial Gap"),
    (7,  "fetch_pharma_extraction.py",   "pharma-extraction-map.html",       1, "Pharma Extraction"),
    (8,  "termination_cascade.py",       "termination-cascade.html",         1, "Termination Cascade"),
    (9,  "fetch_covid_impact.py",        "covid-impact-africa.html",         1, "COVID Impact"),
    (10, "fetch_francophone_gap.py",     "francophone-gap.html",             1, "Francophone Gap"),
    (11, "fetch_conflict_zones.py",      "conflict-zone-exclusion.html",     1, "Conflict Zones"),
    (12, "fetch_vaccine_colony.py",      "vaccine-colony.html",              1, "Vaccine Colony"),
    (13, "fetch_desert_map.py",          "research-desert-map.html",         1, "Desert Map"),
    (14, "fetch_percapita_league.py",    "percapita-league.html",            1, "Per-Capita League"),
    (15, "fetch_pepfar_trap.py",         "pepfar-dependency.html",           2, "PEPFAR Trap"),
    (16, "fetch_decolonization_score.py","decolonization-scorecard.html",    2, "Decolonization"),
    (17, "fetch_rwanda_model.py",        "rwanda-model.html",                2, "Rwanda Model"),
    (18, "fetch_nigeria_paradox.py",     "nigeria-paradox.html",             2, "Nigeria Paradox"),
    (19, "fetch_global_south.py",        "global-south-comparison.html",     2, "Global South"),
    (20, "fetch_latam_mirror.py",        "latam-mirror.html",                2, "LatAm Mirror"),
    (21, "fetch_surgical_desert.py",     "surgical-desert.html",             2, "Surgical Desert"),
    (22, "fetch_traditional_medicine.py","traditional-medicine.html",        2, "Traditional Medicine"),
    (23, "fetch_trauma_gap.py",          "trauma-gap.html",                  2, "Trauma Gap"),
    (24, "fetch_china_leapfrog.py",      "china-leapfrog.html",              2, "China Leapfrog"),
    (25, "fetch_forgotten_diseases.py",  "forgotten-diseases.html",          3, "Forgotten Diseases"),
    (26, "fetch_cervical_cancer.py",     "cervical-cancer.html",             3, "Cervical Cancer"),
    (27, "fetch_palliative_desert.py",   "palliative-desert.html",           3, "Palliative Desert"),
    (28, "fetch_amr_crisis.py",          "amr-crisis.html",                  3, "AMR Crisis"),
    (29, "fetch_maternal_mortality.py",  "maternal-mortality.html",          3, "Maternal Mortality"),
    (30, "fetch_childhood_cancer.py",    "childhood-cancer.html",            3, "Childhood Cancer"),
    (31, "fetch_genomics_zero.py",       "genomics-zero.html",               3, "Genomics Zero"),
    (32, "fetch_heart_failure_africa.py","heart-failure-africa.html",        3, "Heart Failure"),
    (33, "fetch_digital_health.py",      "digital-health.html",              3, "Digital Health"),
    (34, "fetch_rhd_tragedy.py",         "rhd-tragedy.html",                 3, "RHD Tragedy"),
    (35, "fetch_air_pollution.py",       "air-pollution.html",               3, "Air Pollution"),
    (36, "fetch_mizan_index.py",         "mizan-index.html",                 4, "Mizan Index"),
    (37, "fetch_dutch_disease.py",       "dutch-disease.html",               4, "Dutch Disease"),
    (38, "fetch_terms_of_trade.py",      "terms-of-trade.html",              4, "Terms of Trade"),
    (39, "fetch_power_law.py",           "power-law.html",                   4, "Power Law"),
    (40, "fetch_phase_transition.py",    "phase-transition.html",            4, "Phase Transition"),
    (41, "fetch_principal_agent.py",     "principal-agent.html",             4, "Principal-Agent"),
    (42, "fetch_free_rider_genome.py",   "free-rider-genome.html",           4, "Free Rider Genome"),
    (43, "fetch_placebo_ethics.py",      "placebo-ethics.html",              5, "Placebo Ethics"),
    (44, "fetch_design_quality.py",      "design-quality.html",              5, "Design Quality"),
    (45, "fetch_advanced_stats.py",      "advanced-stats.html",              5, "Advanced Stats"),
    (46, "fetch_regression_model.py",    "regression-model.html",            5, "Regression Model"),
    (47, "fetch_network_analysis.py",    "network-analysis.html",            5, "Network Analysis"),
    (48, "fetch_pepfar_causal.py",       "pepfar-causal.html",               5, "PEPFAR Causal"),
    (49, "fetch_patient_access.py",      "patient-access.html",              6, "Patient Access"),
    (50, "fetch_child_view.py",          "child-view.html",                  6, "Child View"),
    (51, "fetch_mother_view.py",         "mother-view.html",                 6, "Mother View"),
    (52, "fetch_elderly_view.py",        "elderly-view.html",                6, "Elderly View"),
    (53, "fetch_doctor_view.py",         "doctor-view.html",                 6, "Doctor View"),
    (54, "fetch_admin_view.py",          "admin-view.html",                  6, "Admin View"),
    (55, "fetch_government_view.py",     "government-view.html",             6, "Government View"),
    (56, "fetch_nurse_view.py",          "nurse-view.html",                  6, "Nurse View"),
    (57, "fetch_community_view.py",      "community-view.html",              6, "Community View"),
    (58, "fetch_funder_view.py",         "funder-view.html",                 6, "Funder View"),
    (59, "fetch_researcher_view.py",     "researcher-view.html",             6, "Researcher View"),
    (60, "fetch_future_view.py",         "future-view.html",                 6, "Future View"),
]

# Quick mode: fastest scripts (no heavy API)
QUICK_SET = {2, 3, 5, 45, 47, 57, 58, 59, 60}


def delete_caches():
    """Delete all cached data to force fresh API pulls."""
    data_dir = ROOT / "data"
    if data_dir.exists():
        count = 0
        for f in data_dir.glob("*.json"):
            f.unlink()
            count += 1
        print(f"  Deleted {count} cache files")


def run_project(num, script, html, title, timeout_sec=300):
    """Run a single project script and check for HTML output."""
    script_path = ROOT / script
    if not script_path.exists():
        return False, f"Script missing: {script}"

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - start
        html_exists = (ROOT / html).exists()
        status = "OK" if result.returncode == 0 and html_exists else "FAIL"
        return status == "OK", f"{status} ({elapsed:.0f}s)"
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT ({timeout_sec}s)"
    except Exception as e:
        return False, f"ERROR: {str(e)[:40]}"


def update_index_timestamp():
    """Update the index.html with a live timestamp."""
    idx = ROOT / "index.html"
    if not idx.exists():
        return
    with open(idx, "r", encoding="utf-8") as f:
        html = f.read()
    now = datetime.now().strftime("%d %B %Y %H:%M")
    html = re.sub(
        r"March 2026",
        f"{now}",
        html,
        count=1,
    )
    with open(idx, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Updated index.html timestamp: {now}")


def main():
    args = sys.argv[1:]

    # Parse arguments
    selected = None
    if "--quick" in args:
        selected = QUICK_SET
        print("QUICK MODE: running 10 fastest projects")
    elif "--layer" in args:
        idx = args.index("--layer")
        layer = int(args[idx + 1])
        selected = {p[0] for p in PROJECTS if p[3] == layer}
        print(f"LAYER {layer}: running {len(selected)} projects")
    elif "--project" in args:
        idx = args.index("--project")
        selected = {int(args[idx + 1])}
        print(f"SINGLE PROJECT: #{list(selected)[0]}")
    elif "--fresh" in args:
        print("FRESH MODE: deleting all caches first")
        delete_caches()

    to_run = [p for p in PROJECTS if selected is None or p[0] in selected]

    print(f"\n{'=' * 60}")
    print(f"  Africa RCT Programme — Running {len(to_run)} projects")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    ok = 0
    fail = 0
    start_all = time.time()

    for num, script, html, layer, title in to_run:
        print(f"  [{num:2d}/60] {title:25s} ", end="", flush=True)
        success, msg = run_project(num, script, html, title)
        print(msg)
        if success:
            ok += 1
        else:
            fail += 1

    elapsed_all = time.time() - start_all
    mins = int(elapsed_all // 60)
    secs = int(elapsed_all % 60)

    print(f"\n{'=' * 60}")
    print(f"  COMPLETE: {ok}/{ok + fail} succeeded, {fail} failed")
    print(f"  Runtime:  {mins}m {secs}s")
    print(f"{'=' * 60}")

    # Update index timestamp
    update_index_timestamp()

    print(f"\n  Open C:\\AfricaRCT\\index.html in a browser.")


if __name__ == "__main__":
    main()
