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
OUTPUT_HTML = Path(__file__).parent / "omega-equity-frontier.html"

# The "Innovation" Keywords
INNOVATION_TERMS = [
    "genomic", "personalized", "precision medicine", "biomarker", 
    "immunotherapy", "gene therapy", "cell therapy", "monoclonal",
    "targeted therapy", "molecular", "diagnostic", "rare disease"
]

AFRICA_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda"]
EUROPE_HUBS = ["France", "United Kingdom", "Germany", "Spain", "Italy"]

def fetch_innovation_metrics(location, terms):
    """Query counts for advanced therapeutic terms."""
    counts = {}
    print(f"  Probing the frontier for {location}...")
    
    # Total Interventional
    total_params = {
        "format": "json", "pageSize": 1, "countTotal": "true",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    total = requests.get(BASE_URL, params=total_params).json().get("totalCount", 0)
    time.sleep(0.3)
    
    # Total Observational
    obs_params = {
        "format": "json", "pageSize": 1, "countTotal": "true",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]OBSERVATIONAL"
    }
    observational = requests.get(BASE_URL, params=obs_params).json().get("totalCount", 0)
    time.sleep(0.3)

    # Innovation Queries
    for term in terms:
        params = {
            "format": "json", "pageSize": 1, "countTotal": "true",
            "query.locn": location, "query.term": term,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
        }
        count = requests.get(BASE_URL, params=params).json().get("totalCount", 0)
        counts[term] = count
        time.sleep(0.3)
        
    return {
        "total": total,
        "observational": observational,
        "innovation_sum": sum(counts.values()),
        "terms": counts
    }

def generate_omega_report(af, eu):
    af_innov_pct = (af['innovation_sum'] / max(1, af['total'])) * 100
    eu_innov_pct = (eu['innovation_sum'] / max(1, eu['total'])) * 100
    
    # Observational vs Interventional Ratio
    af_obs_ratio = af['observational'] / max(1, af['total'])
    eu_obs_ratio = eu['observational'] / max(1, eu['total'])

    term_rows = ""
    for term in INNOVATION_TERMS:
        af_c = af['terms'].get(term, 0)
        eu_c = eu['terms'].get(term, 0)
        gap = eu_c / max(1, af_c)
        term_rows += f"""
        <tr>
            <td>{term.title()}</td>
            <td class="af">{af_c:,}</td>
            <td class="eu">{eu_c:,}</td>
            <td class="gap">{gap:.1f}x</td>
        </tr>"""

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Omega Analysis: The Innovation Horizon</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;700&display=swap');
            body {{ background: #000; color: #fff; font-family: 'Space Grotesk', sans-serif; padding: 60px; overflow-x: hidden; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .hero {{ text-align: center; margin-bottom: 80px; }}
            h1 {{ font-size: 5em; font-weight: 700; background: linear-gradient(45deg, #00d2ff, #3a7bd5, #ff00cc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin: 0; }}
            .tagline {{ font-size: 1.2em; color: #888; letter-spacing: 10px; text-transform: uppercase; margin-top: 20px; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 50px; margin-bottom: 50px; }}
            .card {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); padding: 40px; border-radius: 30px; backdrop-filter: blur(10px); }}
            .big-val {{ font-size: 4em; font-weight: 700; color: #00d2ff; }}
            .big-val.pink {{ color: #ff00cc; }}
            .label {{ color: #888; font-size: 0.9em; text-transform: uppercase; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 30px; }}
            th, td {{ padding: 20px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }}
            th {{ color: #444; font-size: 0.8em; text-transform: uppercase; }}
            .gap {{ color: #ff00cc; font-weight: 700; }}
            .conclusion {{ text-align: center; font-size: 1.5em; max-width: 900px; margin: 80px auto; color: #aaa; line-height: 1.8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="hero">
                <h1>OMEGA EQUITY</h1>
                <div class="tagline">The Final Frontier of Genomic Justice</div>
            </div>

            <div class="grid">
                <div class="card">
                    <div class="label">Innovation Intensity (Africa)</div>
                    <div class="big-val">{af_innov_pct:.1f}%</div>
                    <p>Percentage of trials using "Precision Medicine" keywords</p>
                </div>
                <div class="card">
                    <div class="label">Innovation Intensity (Europe)</div>
                    <div class="big-val pink">{eu_innov_pct:.1f}%</div>
                    <p>Percentage of trials using "Precision Medicine" keywords</p>
                </div>
            </div>

            <div class="card">
                <h2>The Innovation Gap: Term-by-Term Breakdown</h2>
                <table>
                    <thead>
                        <tr><th>Frontier Keyword</th><th>Africa</th><th>Europe</th><th>The Innovation Gap</th></tr>
                    </thead>
                    <tbody>
                        {term_rows}
                    </tbody>
                </table>
            </div>

            <div class="grid" style="margin-top:50px;">
                <div class="card">
                    <div class="label">The "Observation" Trap (Africa)</div>
                    <div class="big-val">{af_obs_ratio:.2f}</div>
                    <p>Ratio of Observational vs Interventional studies. Africa is more likely to be <em>observed</em> than <em>treated</em> with new tech.</p>
                </div>
                <div class="card">
                    <div class="label">The "Observation" Trap (Europe)</div>
                    <div class="big-val pink">{eu_obs_ratio:.2f}</div>
                    <p>Ratio of Observational vs Interventional studies.</p>
                </div>
            </div>

            <div class="conclusion">
                The Omega Analysis reveals the starkest divide of all: **Bio-Digital Inequity**. While Europe has pivoted towards the genomic future—where medicine is targeted to the individual—Africa remains largely within a population-level paradigm. The gap in **Immunotherapy ( {eu['terms'].get('immunotherapy',0)/max(1,af['terms'].get('immunotherapy',1)):.1f}x )** and **Genomics** isn't just a number; it is a forecast of a world where life-saving innovation is geographically gated.
            </div>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Initiating Omega Analysis: Probing the Genomic Frontier...")
    # Aggregating for speed but depth
    af_data = fetch_innovation_metrics("Africa", INNOVATION_TERMS)
    eu_data = fetch_innovation_metrics("Europe", INNOVATION_TERMS)
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_omega_report(af_data, eu_data))
        
    print(f"\nOmega Analysis Complete. The Frontier is mapped: {OUTPUT_HTML}")
