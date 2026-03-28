import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config --
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
OUTPUT_HTML = Path(__file__).parent / "structural-inequity-analysis.html"

# Deep Sampling
AFRICA_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda", "Tanzania", "Ethiopia", "Ghana"]
EUROPE_HUBS = ["France", "United Kingdom", "Germany", "Spain", "Italy", "Netherlands", "Belgium", "Sweden"]

AFRICAN_INSTITUTION_KEYWORDS = [
    "university", "hospital", "ministry", "medical", "college", "institute", "center", "centre",
    "cairo", "nairobi", "ibadan", "lagos", "makerere", "witwatersrand", "stellenbosch", "cape town",
    "kwazulu", "pretoria", "mansoura", "alexandria", "assiut", "tanta", "zagazig", "menoufia",
    "muhimbili", "ifakara", "kEMRI", "mRC/UVRI", "rWANDA", "eTHIOPIA", "gHANA", "nIGERIA"
]

INFECTIOUS_DISEASES = ["HIV", "tuberculosis", "malaria", "neglected", "infectious", "ebola", "cholera", "covid"]
NCDS = ["cancer", "diabetes", "cardiovascular", "hypertension", "stroke", "mental health", "alzheimer", "asthma"]

def fetch_data(location, count=250):
    params = {
        "format": "json",
        "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        resp.raise_for_status()
        return resp.json().get("studies", [])
    except Exception as e:
        print(f"  Warning: {location} fetch failed: {e}")
        return []

def analyze_inequity(studies, is_africa=True):
    metrics = {
        "total": len(studies),
        "infectious": 0,
        "ncd": 0,
        "foreign_led": 0,
        "local_led": 0,
        "industry_led": 0,
        "phase_1_2": 0,
        "phase_3_4": 0,
        "sponsors": []
    }
    
    for s in studies:
        proto = s.get("protocolSection", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        design_mod = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        
        # 1. Disease Burden
        conds = " ".join(cond_mod.get("conditions", [])).lower()
        if any(d in conds for d in INFECTIOUS_DISEASES): metrics["infectious"] += 1
        if any(d in conds for d in NCDS): metrics["ncd"] += 1
        
        # 2. Leadership (Parachute Research Proxy)
        lead = sponsor_mod.get("leadSponsor", {})
        name = lead.get("name", "").lower()
        s_class = lead.get("class", "OTHER")
        
        if s_class == "INDUSTRY":
            metrics["industry_led"] += 1
        
        if is_africa:
            # Heuristic to detect if African institution
            if any(kw in name for kw in AFRICAN_INSTITUTION_KEYWORDS):
                metrics["local_led"] += 1
            else:
                metrics["foreign_led"] += 1
        else:
            metrics["local_led"] += 1 # Assume local for European hubs in this context
            
        # 3. Innovation vs Testing (Phases)
        phases = design_mod.get("phases", [])
        if any(p in ["PHASE1", "EARLY_PHASE1", "PHASE2"] for p in phases):
            metrics["phase_1_2"] += 1
        elif any(p in ["PHASE3", "PHASE4"] for p in phases):
            metrics["phase_3_4"] += 1
            
    return metrics

def generate_html(af, eu):
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Structural Inequity Analysis: Africa vs Europe</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #0c0e14; color: #e1e1e1; padding: 40px; }}
            .container {{ max-width: 1100px; margin: 0 auto; }}
            .card {{ background: #161a23; padding: 30px; border-radius: 12px; border: 1px solid #2d3436; margin-bottom: 30px; }}
            h1 {{ color: #fff; font-size: 2.5em; border-bottom: 2px solid #e17055; padding-bottom: 10px; }}
            h2 {{ color: #fbc531; font-size: 1.4em; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 20px; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
            .metric-val {{ font-size: 3em; font-weight: bold; color: #ff7675; }}
            .eu-val {{ color: #74b9ff; }}
            .label {{ font-size: 0.9em; color: #888; }}
            .bar-container {{ background: #222; height: 20px; border-radius: 10px; margin-top: 10px; overflow: hidden; }}
            .bar {{ height: 100%; }}
            .insight-box {{ background: rgba(251, 197, 49, 0.1); border-left: 5px solid #fbc531; padding: 20px; margin-top: 20px; font-style: italic; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Structural Inequity Analysis</h1>
            <p>Guided by literature on the "10/90 Gap" and "Parachute Research" models.</p>

            <!-- Pillar 1: The 10/90 Gap -->
            <div class="card">
                <h2>The 10/90 Gap: Disease Mismatch</h2>
                <div class="grid">
                    <div>
                        <div class="label">AFRICA: Infectious vs NCD trials</div>
                        <div class="metric-val">{af['infectious']} : {af['ncd']}</div>
                        <p>Ratio: {af['infectious']/max(1,af['ncd']):.1f}x more Infectious research</p>
                    </div>
                    <div>
                        <div class="label">EUROPE: Infectious vs NCD trials</div>
                        <div class="metric-val eu">{eu['infectious']} : {eu['ncd']}</div>
                        <p>Ratio: {eu['ncd']/max(1,eu['infectious']):.1f}x more NCD research</p>
                    </div>
                </div>
                <div class="insight-box">
                    Literature Insight: While NCDs now cause more deaths in Africa than infectious diseases, the trial landscape is still "frozen" in the 20th-century infectious disease paradigm.
                </div>
            </div>

            <!-- Pillar 2: Leadership & Sovereignty -->
            <div class="card">
                <h2>Research Sovereignty vs Parachute Models</h2>
                <div class="grid">
                    <div>
                        <div class="label">AFRICA: Foreign vs Local Lead Sponsor</div>
                        <div class="metric-val">{af['foreign_led']} : {af['local_led']}</div>
                        <p>{af['foreign_led']/(af['total'] or 1)*100:.1f}% of trials are foreign-led</p>
                    </div>
                    <div>
                        <div class="label">EUROPE: Industry Dominance</div>
                        <div class="metric-val eu">{eu['industry_led']}</div>
                        <p>{eu['industry_led']/(eu['total'] or 1)*100:.1f}% of trials are Industry-sponsored</p>
                    </div>
                </div>
                <div class="insight-box">
                    Literature Insight: High "Foreign-Led" percentages in Africa are a hallmark of parachute research, where external agendas often supersede local priorities.
                </div>
            </div>

            <!-- Pillar 3: The Innovation Horizon -->
            <div class="card">
                <h2>The Innovation Horizon (Phase I/II vs III/IV)</h2>
                <div class="grid">
                    <div>
                        <div class="label">AFRICA: Phase I/II vs III/IV</div>
                        <div class="metric-val">{af['phase_1_2']} : {af['phase_3_4']}</div>
                        <p>Focus: Testing & Validation</p>
                    </div>
                    <div>
                        <div class="label">EUROPE: Phase I/II vs III/IV</div>
                        <div class="metric-val eu">{eu['phase_1_2']} : {eu['phase_3_4']}</div>
                        <p>Focus: Early Discovery & Innovation</p>
                    </div>
                </div>
            </div>

        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Initiating Structural Inequity Analysis...")
    af_studies = []
    for c in AFRICA_HUBS:
        print(f"  Sampling {c}...")
        af_studies.extend(fetch_data(c))
        time.sleep(0.5)
        
    eu_studies = []
    for c in EUROPE_HUBS:
        print(f"  Sampling {c}...")
        eu_studies.extend(fetch_data(c))
        time.sleep(0.5)
        
    print("\nQuantifying Inequity Dimensions...")
    af_stats = analyze_inequity(af_studies, is_africa=True)
    eu_stats = analyze_inequity(eu_studies, is_africa=False)
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_html(af_stats, eu_stats))
    
    print(f"\nAnalysis Complete. Structural report: {OUTPUT_HTML}")
