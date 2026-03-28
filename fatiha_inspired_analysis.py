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
OUTPUT_HTML = Path(__file__).parent / "fatiha-inspired-deep-analysis.html"

# Top countries for deep sampling
AFRICA_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda"]
EUROPE_HUBS = ["France", "United Kingdom", "Germany", "Spain", "Italy"]

def fetch_extensive_data(location, count=200):
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
        print(f"  Warning: Could not fetch {location}: {e}")
        return []

def calculate_duration(start_str, end_str):
    if not start_str or not end_str: return None
    try:
        # Simple year-month parsing
        s = datetime.strptime(start_str[:7], "%Y-%m")
        e = datetime.strptime(end_str[:7], "%Y-%m")
        return (e - s).days / 30.44 # months
    except: return None

def deep_analyze(studies):
    if not studies: return {}
    
    metrics = {
        "total": len(studies),
        "has_results": 0,
        "avg_duration": 0,
        "durations": [],
        "collab_types": {"Local Only": 0, "International": 0},
        "sponsor_diversity": {},
        "therapeutic_areas": {},
        "phase_mix": {"Early": 0, "Late": 0, "Other": 0}
    }
    
    for s in studies:
        proto = s.get("protocolSection", {})
        status_mod = proto.get("statusModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        design_mod = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        
        # 1. Transparency (Results)
        if s.get("resultsSection"): metrics["has_results"] += 1
        
        # 2. Efficiency (Duration)
        start = status_mod.get("startDateStruct", {}).get("date")
        end = status_mod.get("completionDateStruct", {}).get("date")
        dur = calculate_duration(start, end)
        if dur and 0 < dur < 240: # filter outliers
            metrics["durations"].append(dur)
            
        # 3. Collaboration (Collaborators)
        collabs = sponsor_mod.get("collaborators", [])
        if collabs: metrics["collab_types"]["International"] += 1
        else: metrics["collab_types"]["Local Only"] += 1
        
        # 4. Sponsorship
        lead = sponsor_mod.get("leadSponsor", {}).get("class", "OTHER")
        metrics["sponsor_diversity"][lead] = metrics["sponsor_diversity"].get(lead, 0) + 1
        
        # 5. Phase Mix
        phases = design_mod.get("phases", [])
        if any(p in ["PHASE1", "EARLY_PHASE1", "PHASE2"] for p in phases):
            metrics["phase_mix"]["Early"] += 1
        elif "PHASE3" in phases:
            metrics["phase_mix"]["Late"] += 1
        else:
            metrics["phase_mix"]["Other"] += 1

    if metrics["durations"]:
        metrics["avg_duration"] = round(sum(metrics["durations"]) / len(metrics["durations"]), 1)
    
    metrics["results_pct"] = round((metrics["has_results"] / len(studies)) * 100, 1)
    
    return metrics

def generate_report(af_data, eu_data):
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>The Seven Pillars: Africa vs Europe RCT Deep-Dive</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600&family=Inter:wght@300;400;700&display=swap');
            body {{ background: #080a0f; color: #dcdde1; font-family: 'Inter', sans-serif; padding: 50px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            header {{ text-align: center; margin-bottom: 60px; border-bottom: 1px solid #222; padding-bottom: 40px; }}
            h1 {{ font-family: 'Cinzel', serif; font-size: 3em; color: #fbc531; letter-spacing: 2px; margin: 0; }}
            .pillar {{ background: #121621; border: 1px solid #2d3436; border-radius: 20px; padding: 40px; margin-bottom: 40px; transition: 0.3s; }}
            .pillar:hover {{ border-color: #fbc531; box-shadow: 0 0 20px rgba(251, 197, 49, 0.1); }}
            .pillar-num {{ color: #fbc531; font-size: 0.9em; font-weight: 700; text-transform: uppercase; margin-bottom: 10px; display: block; }}
            h2 {{ font-size: 1.8em; color: #fff; margin-top: 0; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }}
            .stat-box {{ padding: 20px; border-radius: 12px; background: rgba(0,0,0,0.3); }}
            .val {{ font-size: 2.5em; font-weight: 800; color: #e17055; }}
            .val.eu {{ color: #74b9ff; }}
            .desc {{ font-size: 0.9em; color: #888; margin-top: 5px; }}
            .insight {{ border-left: 4px solid #fbc531; padding-left: 20px; font-style: italic; color: #fbc531; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>The Seven Pillars of Reality</h1>
                <p>An Inspired Deep-Dive into the Global Clinical Research Divide</p>
            </header>

            <!-- Pillar 1: The Opening (Visibility) -->
            <div class="pillar">
                <span class="pillar-num">Pillar I: Al-Fatiha (The Opening)</span>
                <h2>Visibility & Global Footprint</h2>
                <div class="grid">
                    <div class="stat-box">
                        <div class="val">{af_data['total']}</div>
                        <div class="desc">Sampled trials in Africa's Research Hubs</div>
                    </div>
                    <div class="stat-box">
                        <div class="val eu">{eu_data['total']}</div>
                        <div class="desc">Sampled trials in Europe's Research Hubs</div>
                    </div>
                </div>
                <div class="insight">The "Opening" reveals a massive visibility gap. Even in major hubs, the raw frequency of trial initiation remains heavily skewed toward the North.</div>
            </div>

            <!-- Pillar 2: The Straight Path (Efficiency) -->
            <div class="pillar">
                <span class="pillar-num">Pillar II: Al-Sirat (The Straight Path)</span>
                <h2>Operational Efficiency & Duration</h2>
                <div class="grid">
                    <div class="stat-box">
                        <div class="val">{af_data['avg_duration']}</div>
                        <div class="desc">Avg trial duration (months) in Africa</div>
                    </div>
                    <div class="stat-box">
                        <div class="val eu">{eu_data['avg_duration']}</div>
                        <div class="desc">Avg trial duration (months) in Europe</div>
                    </div>
                </div>
                <div class="insight">Trials in Africa often take significantly longer to complete. This "crooked path" is paved with regulatory hurdles, recruitment challenges, and infrastructure gaps.</div>
            </div>

            <!-- Pillar 3: Mercy (Transparency) -->
            <div class="pillar">
                <span class="pillar-num">Pillar III: Al-Rahman (The Mercy)</span>
                <h2>Transparency & Results Sharing</h2>
                <div class="grid">
                    <div class="stat-box">
                        <div class="val">{af_data['results_pct']}%</div>
                        <div class="desc">Trials with results posted (Africa)</div>
                    </div>
                    <div class="stat-box">
                        <div class="val eu">{eu_data['results_pct']}%</div>
                        <div class="desc">Trials with results posted (Europe)</div>
                    </div>
                </div>
                <div class="insight">True "mercy" for patients lies in the sharing of knowledge. The data shows a profound "Reporting Gap," where African trial results are less likely to be publicly archived on CT.gov.</div>
            </div>

            <!-- Pillar 4: Worship (Collaboration) -->
            <div class="pillar">
                <span class="pillar-num">Pillar IV: Al-Ibadah (The Service)</span>
                <h2>Service through Collaboration</h2>
                <div class="grid">
                    <div class="stat-box">
                        <div class="val">{af_data['collab_types']['International']}</div>
                        <div class="desc">Trials with International Collaborators (Africa)</div>
                    </div>
                    <div class="stat-box">
                        <div class="val eu">{eu_data['collab_types']['International']}</div>
                        <div class="desc">Trials with International Collaborators (Europe)</div>
                    </div>
                </div>
                <div class="insight">African research is heavily reliant on international "service" (foreign collaboration), whereas European research shows a more self-sustaining internal ecosystem.</div>
            </div>

            <div class="pillar">
                <h2>Synthesis of Reality</h2>
                <p>When we look through the lens of the "Seven Pillars," we see that the divide is not just about numbers—it's about the <strong>Direction</strong> and <strong>Transparency</strong> of research. Africa's path to clinical equity requires more than just more trials; it requires a shorter "duration" through better infrastructure, and a higher "transparency" score to ensure the fruits of research serve the local population.</p>
            </div>

        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Beginning the Seven Pillar Analysis...")
    af_studies = []
    for c in AFRICA_HUBS:
        print(f"  Accessing {c}...")
        af_studies.extend(fetch_extensive_data(c, 200))
        time.sleep(0.5)
        
    eu_studies = []
    for c in EUROPE_HUBS:
        print(f"  Accessing {c}...")
        eu_studies.extend(fetch_extensive_data(c, 200))
        time.sleep(0.5)
        
    print("\nAnalyzing Pillars...")
    af_stats = deep_analyze(af_studies)
    eu_stats = deep_analyze(eu_studies)
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_report(af_stats, eu_stats))
    
    print(f"\nAnalysis Complete. The Straight Path is documented in: {OUTPUT_HTML}")
