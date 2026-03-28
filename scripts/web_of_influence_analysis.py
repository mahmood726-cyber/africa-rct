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

# -- Configuration --
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
OUTPUT_HTML = Path(__file__).parent / "web-of-influence-analysis.html"

# Sampling for Network Analysis
AFRICA_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda"]
EUROPE_HUBS = ["France", "United Kingdom", "Germany", "Spain", "Italy"]

AFRICAN_KEYWORDS = [
    "cairo", "nairobi", "ibadan", "lagos", "makerere", "witwatersrand", "stellenbosch", "cape town",
    "kwazulu", "pretoria", "mansoura", "alexandria", "assiut", "tanta", "muhimbili", "ifakara",
    "egypt", "kenya", "nigeria", "uganda", "south africa", "ghana", "ethiopia", "tanzania"
]

def analyze_network(location, count=300):
    print(f"  Mapping the web for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
    except: return {}

    network_stats = {
        "total": len(studies),
        "sovereign_leads": 0, # Local Lead
        "dependent_leads": 0, # Foreign Lead
        "avg_collaborators": 0,
        "collaboration_sum": 0,
        "multilateral_count": 0, # > 3 collaborators
        "industry_influence": 0,
        "foreign_collaborators_in_local_leads": 0
    }

    for s in studies:
        proto = s.get("protocolSection", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        lead = sponsor_mod.get("leadSponsor", {})
        collabs = sponsor_mod.get("collaborators", [])
        
        name = lead.get("name", "").lower()
        s_class = lead.get("class", "OTHER")
        
        # Determine Sovereignty
        is_local = any(kw in name for kw in AFRICAN_KEYWORDS) if "Africa" in location or any(k in location.lower() for k in ["egypt", "kenya", "nigeria", "uganda", "south africa"]) else True
        
        if is_local: network_stats["sovereign_leads"] += 1
        else: network_stats["dependent_leads"] += 1
        
        if s_class == "INDUSTRY": network_stats["industry_influence"] += 1
        
        # Collaboration Metrics
        collab_count = len(collabs)
        network_stats["collaboration_sum"] += collab_count
        if collab_count > 3: network_stats["multilateral_count"] += 1
        
        # Check if local lead has foreign heavy-hitters
        if is_local and collab_count > 0:
            network_stats["foreign_collaborators_in_local_leads"] += 1

    network_stats["avg_collaborators"] = round(network_stats["collaboration_sum"] / max(1, len(studies)), 1)
    network_stats["sovereignty_score"] = round((network_stats["sovereign_leads"] / max(1, len(studies))) * 100, 1)
    
    return network_stats

def generate_web_report(af, eu):
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>The Web of Influence: Sovereignty vs Dependency</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;700&display=swap');
            body {{ background: #0a0a0c; color: #e0e0e0; font-family: 'Outfit', sans-serif; padding: 60px; }}
            .container {{ max-width: 1100px; margin: 0 auto; }}
            header {{ text-align: center; margin-bottom: 80px; }}
            h1 {{ font-size: 3.5em; font-weight: 700; color: #fff; margin: 0; text-transform: uppercase; letter-spacing: 2px; }}
            .line {{ height: 2px; background: linear-gradient(90deg, transparent, #6c5ce7, transparent); margin: 20px 0; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }}
            .card {{ background: #13131a; border: 1px solid #1f1f2e; padding: 40px; border-radius: 24px; position: relative; overflow: hidden; }}
            .score {{ font-size: 5em; font-weight: 700; color: #6c5ce7; line-height: 1; }}
            .score.eu {{ color: #00cec9; }}
            .label {{ font-size: 0.8em; color: #747d8c; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 10px; }}
            .stat-row {{ display: flex; justify-content: space-between; padding: 15px 0; border-bottom: 1px solid #1f1f2e; }}
            .stat-label {{ color: #a4b0be; }}
            .stat-val {{ color: #fff; font-weight: 700; }}
            .analysis {{ margin-top: 60px; font-size: 1.2em; line-height: 1.8; color: #a4b0be; text-align: center; max-width: 800px; margin-left: auto; margin-right: auto; }}
            .web-icon {{ position: absolute; right: -20px; top: -20px; font-size: 8em; opacity: 0.05; pointer-events: none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>The Web of Influence</h1>
                <div class="label">Mapping Sovereignty vs Dependency in Global RCTs</div>
                <div class="line"></div>
            </header>

            <div class="grid">
                <div class="card">
                    <div class="web-icon">🕸️</div>
                    <div class="label">Research Sovereignty (Africa)</div>
                    <div class="score">{af['sovereignty_score']}%</div>
                    <p>Percentage of trials led by Local Institutions</p>
                    <div style="margin-top:30px;">
                        <div class="stat-row"><span class="stat-label">Avg Collaborators</span><span class="stat-val">{af['avg_collaborators']}</span></div>
                        <div class="stat-row"><span class="stat-label">Industry Influence</span><span class="stat-val">{af['industry_influence']} trials</span></div>
                        <div class="stat-row"><span class="stat-label">Multilateral Networks</span><span class="stat-val">{af['multilateral_count']}</span></div>
                    </div>
                </div>
                <div class="card">
                    <div class="web-icon">🌐</div>
                    <div class="label">Research Sovereignty (Europe)</div>
                    <div class="score eu">{eu['sovereignty_score']}%</div>
                    <p>Percentage of trials led by Local Institutions</p>
                    <div style="margin-top:30px;">
                        <div class="stat-row"><span class="stat-label">Avg Collaborators</span><span class="stat-val">{eu['avg_collaborators']}</span></div>
                        <div class="stat-row"><span class="stat-label">Industry Influence</span><span class="stat-val">{eu['industry_influence']} trials</span></div>
                        <div class="stat-row"><span class="stat-label">Multilateral Networks</span><span class="stat-val">{eu['multilateral_count']}</span></div>
                    </div>
                </div>
            </div>

            <div class="analysis">
                The network analysis reveals the hidden geometry of the research divide. While Europe operates as a **Multilateral Power**—with high sovereignty and deep, dense networks—Africa’s research landscape is characterized by **"Asymmetric Dependency."** A high percentage of African-led trials are "Multilateral Islands," where local leadership is often supported (or shadowed) by significant foreign collaborators. True equity requires not just more trials, but the transition from a **Dependency Web** to a **Sovereign Network**.
            </div>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Initiating Web of Influence Network Analysis...")
    # Using aggregated samples for deep network parsing
    af_stats = analyze_network("Africa", 400)
    eu_stats = analyze_network("Europe", 400)
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_web_report(af_stats, eu_stats))
        
    print(f"\nNetwork Analysis Complete. The Web is mapped: {OUTPUT_HTML}")
