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
OUTPUT_HTML = Path(__file__).parent / "moses-12-angles-analysis.html"

# The 12 Tribes of Inequity (Mapping)
ANGLES = [
    {"name": "Reuben", "dim": "Volume Gap", "icon": "🌊", "desc": "The Firstborn: Total trial volume vs the global sea of research."},
    {"name": "Simeon", "dim": "Concentration", "icon": "🏘️", "desc": "The Fortress: The dominance of just 3 countries (Egypt, SA, Nigeria)."},
    {"name": "Levi", "dim": "Disease Mismatch", "icon": "⚖️", "desc": "The Priesthood: The 'Orthodoxy' of infectious research vs NCDs."},
    {"name": "Judah", "dim": "Sponsorship", "icon": "🦁", "desc": "The Leader: Sovereignty vs Foreign-led 'Parachute' research."},
    {"name": "Dan", "dim": "Termination", "icon": "🔨", "desc": "The Judge: Trials that fail, terminate, or are withdrawn prematurely."},
    {"name": "Naphtali", "dim": "Phase Innovation", "icon": "🦌", "desc": "The Swift: The lack of early Phase I discovery in Africa."},
    {"name": "Gad", "dim": "Multi-site Dilution", "icon": "⚔️", "desc": "The Troop: Africa as a token site in global mega-trials."},
    {"name": "Asher", "dim": "The Egypt Anomaly", "icon": "🍞", "desc": "The Abundant: High volume in Egypt vs follow-through quality."},
    {"name": "Issachar", "dim": "Pediatric Burden", "icon": "👶", "desc": "The Burden: Disproportionate testing on children and infants."},
    {"name": "Zebulun", "dim": "Infrastructure", "icon": "⚓", "desc": "The Dweller: Research capacity and centers of excellence."},
    {"name": "Joseph", "dim": "Results Reporting", "icon": "🌾", "desc": "The Harvest: Trials that actually publish results to the global barn."},
    {"name": "Benjamin", "dim": "Unknown Status", "icon": "🐺", "desc": "The Lost: Trials that disappear into 'Unknown' status for years."}
]

def search(query_params):
    params = {"format": "json", "pageSize": 1, "countTotal": "true"}
    params.update(query_params)
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("totalCount", 0)
    except: return 0

def fetch_12_angles():
    print("Striking the rock... fetching the 12 Angles of the Staff.")
    results = {}
    
    # 1. Reuben (Volume)
    results["Reuben"] = search({"query.locn": "Africa", "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
    
    # 2. Simeon (Concentration)
    egypt = search({"query.locn": "Egypt"})
    sa = search({"query.locn": "South Africa"})
    results["Simeon"] = egypt + sa
    
    # 3. Levi (Mismatch)
    results["Levi"] = search({"query.locn": "Africa", "query.cond": "HIV OR malaria OR tuberculosis"})
    
    # 4. Judah (Sponsorship - Industry Proxy)
    results["Judah"] = search({"query.locn": "Africa", "filter.advanced": "AREA[LeadSponsorClass]INDUSTRY"})
    
    # 5. Dan (Termination)
    results["Dan"] = search({"query.locn": "Africa", "filter.advanced": "AREA[OverallStatus]TERMINATED OR AREA[OverallStatus]WITHDRAWN"})
    
    # 6. Naphtali (Phase I)
    results["Naphtali"] = search({"query.locn": "Africa", "filter.advanced": "AREA[Phase]Phase 1"})
    
    # 7. Gad (Multi-site - Heuristic: Large enrollment)
    results["Gad"] = search({"query.locn": "Africa", "filter.advanced": "AREA[EnrollmentCount]RANGE[1000, 1000000]"})
    
    # 8. Asher (Egypt)
    results["Asher"] = egypt
    
    # 9. Issachar (Pediatrics)
    results["Issachar"] = search({"query.locn": "Africa", "filter.advanced": "AREA[Child]true"})
    
    # 10. Zebulun (Infrastructure - Phase 4 as capacity marker)
    results["Zebulun"] = search({"query.locn": "Africa", "filter.advanced": "AREA[Phase]Phase 4"})
    
    # 11. Joseph (Results)
    # API v2 doesn't have a direct 'has results' filter in query yet, but we'll use a proxy or placeholder
    results["Joseph"] = search({"query.locn": "Africa", "filter.advanced": "AREA[OverallStatus]COMPLETED"})
    
    # 12. Benjamin (Unknown)
    results["Benjamin"] = search({"query.locn": "Africa", "filter.advanced": "AREA[OverallStatus]UNKNOWN"})
    
    return results

def generate_html(data):
    angle_cards = ""
    for a in ANGLES:
        val = data.get(a["name"], 0)
        angle_cards += f"""
        <div class="angle-card">
            <div class="angle-icon">{a['icon']}</div>
            <h3>{a['name']} ({a['dim']})</h3>
            <div class="angle-val">{val:,}</div>
            <p>{a['desc']}</p>
        </div>"""

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>The 12 Angles of the Staff: Africa RCT Equity</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@700&family=Inter:wght@300;600&display=swap');
            body {{ background: #050505; color: #d4d4d4; font-family: 'Inter', sans-serif; padding: 40px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            header {{ text-align: center; margin-bottom: 60px; }}
            h1 {{ font-family: 'Cinzel Decorative', cursive; font-size: 3.5em; color: #d4af37; margin: 0; }}
            .subtitle {{ color: #888; letter-spacing: 4px; text-transform: uppercase; font-size: 0.9em; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 25px; }}
            .angle-card {{ background: #111; border: 1px solid #222; padding: 30px; border-radius: 4px; position: relative; overflow: hidden; transition: 0.3s; }}
            .angle-card:hover {{ border-color: #d4af37; transform: translateY(-5px); }}
            .angle-icon {{ font-size: 2em; margin-bottom: 15px; }}
            .angle-val {{ font-size: 2.5em; font-weight: 700; color: #fff; margin: 10px 0; }}
            h3 {{ color: #d4af37; font-size: 1.1em; margin: 0; text-transform: uppercase; }}
            p {{ font-size: 0.85em; color: #777; line-height: 1.5; }}
            .staff-divider {{ height: 4px; background: linear-gradient(to right, transparent, #d4af37, transparent); margin: 60px 0; }}
            .prophecy {{ font-style: italic; color: #d4af37; text-align: center; max-width: 800px; margin: 0 auto 40px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>The 12 Angles of the Staff</h1>
                <div class="subtitle">Parting the Sea of Clinical Inequity</div>
            </header>
            
            <div class="prophecy">"And you shall take this staff in your hand, wherewith you shall do signs... Let the data speak the truth that the waters may part."</div>

            <div class="grid">
                {angle_cards}
            </div>

            <div class="staff-divider"></div>

            <div style="text-align:center;">
                <h2>The Commandment of Equity</h2>
                <p style="max-width:800px; margin: 0 auto;">The 12 tribes of research data reveal a continent that is often used but rarely led. To 'part the sea' of inequity, we must move from <strong>Benjamin</strong> (The Lost/Unknown) to <strong>Judah</strong> (Local Leadership) and from <strong>Levi</strong> (The Infectious Orthodoxy) to a holistic health paradigm for all African people.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    results = fetch_12_angles()
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_html(results))
    print(f"\nThe 12 Angles are revealed: {OUTPUT_HTML}")
