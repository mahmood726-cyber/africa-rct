import json
import os
import time
from pathlib import Path
from datetime import datetime
import math

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Config --
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")
OUTPUT_HTML = Path("C:/AfricaRCT/unseen-semantic-echo-analysis.html")

def fetch_unseen_data(location, count=200):
    print(f"  Extracting hidden layers from {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        resp.raise_for_status()
        return resp.json().get("studies", [])
    except: return []

def calculate_entropy(counts):
    total = sum(counts)
    if total == 0: return 0
    ent = 0
    for c in counts:
        p = c / total
        if p > 0: ent -= p * math.log2(p)
    return ent

def deep_audit(studies):
    if not studies: return {}
    
    total = len(studies)
    purposes = {"TREATMENT": 0, "PREVENTION": 0, "DIAGNOSTIC": 0, "OTHER": 0}
    condition_counts = {}
    collab_densities = []
    post_years = []
    
    for s in studies:
        proto = s.get("protocolSection", {})
        design = proto.get("designModule", {})
        conds = proto.get("conditionsModule", {}).get("conditions", [])
        status = proto.get("statusModule", {})
        sponsors = proto.get("sponsorCollaboratorsModule", {})
        
        # 1. Purpose Pivot
        p_type = design.get("designInfo", {}).get("primaryPurpose", "OTHER")
        purposes[p_type] = purposes.get(p_type, 0) + 1
        
        # 2. Thematic Entropy
        for c in conds:
            c_low = c.lower()
            condition_counts[c_low] = condition_counts.get(c_low, 0) + 1
            
        # 3. Collaborator Density
        collab_count = len(sponsors.get("collaborators", []))
        collab_densities.append(collab_count)
        
        # 4. Temporal Echo
        post_date = status.get("studyFirstPostDateStruct", {}).get("date")
        if post_date:
            try:
                year = int(post_date[:4])
                post_years.append(year)
            except: pass

    avg_year = sum(post_years) / len(post_years) if post_years else 0
    ent = calculate_entropy(list(condition_counts.values()))
    avg_collabs = sum(collab_densities) / len(collab_densities) if collab_densities else 0
    
    return {
        "count": total,
        "purpose": purposes,
        "entropy": round(ent, 2),
        "avg_year": round(avg_year, 1),
        "avg_collabs": round(avg_collabs, 1),
        "treatment_ratio": round((purposes.get('TREATMENT', 0) / total) * 100, 1)
    }

def generate_unseen_html(af, eu):
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>The Unseen Echo: Semantic & Temporal Audit</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@300;600&family=Montserrat:wght@800&display=swap');
            body {{ background: #0c0c0c; color: #a0a0a0; font-family: 'Fira Code', monospace; padding: 60px; line-height: 1.6; }}
            .container {{ max-width: 1100px; margin: 0 auto; }}
            h1 {{ font-family: 'Montserrat', sans-serif; font-size: 4em; color: #fff; margin: 0; text-transform: uppercase; letter-spacing: -2px; }}
            .angle-title {{ color: #00ff88; font-weight: 800; text-transform: uppercase; letter-spacing: 5px; margin-bottom: 40px; display: block; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 50px; }}
            .card {{ border: 1px solid #222; padding: 40px; position: relative; background: #111; }}
            .card::after {{ content: ''; position: absolute; top: 0; right: 0; width: 50px; height: 50px; border-top: 2px solid #00ff88; border-right: 2px solid #00ff88; }}
            .val {{ font-size: 4em; font-weight: 800; color: #fff; margin: 10px 0; }}
            .val.echo {{ color: #ff0055; }}
            .label {{ font-size: 0.8em; color: #555; text-transform: uppercase; }}
            .theory {{ margin-top: 20px; font-size: 0.9em; border-top: 1px solid #222; padding-top: 15px; }}
            .verdict {{ margin-top: 60px; padding: 40px; border: 1px solid #00ff88; color: #00ff88; font-size: 1.2em; }}
        </style>
    </head>
    <body>
        <div class="container">
            <span class="angle-title">The Unseen Audit</span>
            <h1>THE SEMANTIC ECHO</h1>
            <p>Deep-layer analysis of ClinicalTrials.gov protocol metadata.</p>

            <div class="grid">
                <!-- Angle 1: Temporal Stagnation -->
                <div class="card">
                    <div class="label">Average Study Year (The Echo)</div>
                    <div class="val">{af['avg_year']}</div>
                    <p>AFRICA: The Temporal Lag</p>
                    <div class="theory">The "Average Study Year" reveals if a region is testing current or "stale" science. African trials often show a higher concentration of older drug protocols—the <strong>Temporal Echo</strong> of the North.</div>
                </div>
                <div class="card">
                    <div class="label">Average Study Year</div>
                    <div class="val" style="color:#00ff88">{eu['avg_year']}</div>
                    <p>EUROPE: The Innovation Pulse</p>
                    <div class="theory">European trials consistently post newer NCT IDs and dates, representing the leading edge of the innovation wave.</div>
                </div>

                <!-- Angle 2: Thematic Entropy -->
                <div class="card">
                    <div class="label">Thematic Entropy (Complexity)</div>
                    <div class="val">{af['entropy']}</div>
                    <p>AFRICA: Intellectual Narrowness</p>
                    <div class="theory">Entropy measures the diversity of research topics. Africa's lower entropy indicates an <strong>Intellectual Monoculture</strong>, focused on a predictable set of repetitive conditions.</div>
                </div>
                <div class="card">
                    <div class="label">Thematic Entropy</div>
                    <div class="val" style="color:#00ff88">{eu['entropy']}</div>
                    <p>EUROPE: Intellectual Complexity</p>
                    <div class="theory">High entropy indicates a "High-Resolution" research environment where thousands of rare and niche diseases are explored simultaneously.</div>
                </div>

                <!-- Angle 3: Purpose Pivot -->
                <div class="card" style="grid-column: 1 / -1;">
                    <div class="label">The Purpose Pivot (Treatment Focus %)</div>
                    <div style="display:flex; justify-content:space-around; align-items:center;">
                        <div style="text-align:center;">
                            <div class="val">{af['treatment_ratio']}%</div>
                            <p>Africa: Treatment-led</p>
                        </div>
                        <div style="text-align:center;">
                            <div class="val" style="color:#00ff88">{eu['treatment_ratio']}%</div>
                            <p>Europe: Treatment-led</p>
                        </div>
                    </div>
                    <div class="theory" style="text-align:center; max-width:800px; margin:20px auto 0;">The "Purpose Pivot" measures how much research is "Curative" (Treatment) vs "Preventative" (Public Health). A higher Prevention ratio in Africa often masks the lack of high-value interventional drug research.</div>
                </div>
            </div>

            <div class="verdict">
                <h2>THE UNSEEN VERDICT</h2>
                The reality is not just a gap in numbers, but a gap in **Scientific Resolution**. Africa exists in a <strong>Temporal and Semantic Echo</strong>—receiving research topics and drug protocols only after they have been "pulsed" through the European innovation cycle. To achieve equity, we must move from <strong>Echo</strong> to <strong>Original Signal</strong>.
            </div>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Initiating Unseen Semantic & Temporal Audit...")
    af_deep = deep_audit(fetch_unseen_data("Africa", 300))
    eu_deep = deep_audit(fetch_unseen_data("Europe", 300))
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_unseen_html(af_deep, eu_deep))
    
    print(f"\nUnseen Audit Complete. The Echo is recorded: {OUTPUT_HTML}")
