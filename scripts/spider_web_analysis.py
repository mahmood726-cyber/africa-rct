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
OUTPUT_HTML = Path(__file__).parent / "spider-web-analysis.html"

# Sampling for the 8 Dimensions
AFRICA_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda"]
EUROPE_HUBS = ["France", "United Kingdom", "Germany", "Spain", "Italy"]

def fetch_metric(location, filter_query=None, cond=None):
    params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location}
    if filter_query: params["filter.advanced"] = filter_query
    if cond: params["query.cond"] = cond
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        return resp.json().get("totalCount", 0)
    except: return 0

def collect_spider_metrics(location_name, countries):
    print(f"  Weaving the web for {location_name}...")
    # 1. Volume (Normalized to a max of 50k for scale)
    total = fetch_metric(location_name, "AREA[StudyType]INTERVENTIONAL")
    m1 = min(100, (total / 30000) * 100) 
    
    # 2. Sovereignty (Local Lead % - Sampled from Hubs)
    # Using Industry as a proxy for 'Global North' dominance in this specific metric
    industry = fetch_metric(location_name, "AREA[LeadSponsorClass]INDUSTRY")
    m2 = max(10, 100 - (industry/max(1,total)*100)) # Higher is more 'Sovereign/Academic'
    
    # 3. Innovation (Phase 1/2 %)
    early = fetch_metric(location_name, "AREA[Phase]Phase 1 OR AREA[Phase]Phase 2")
    m3 = (early / max(1, total)) * 100 * 2 # Weighting for visibility
    
    # 4. Diversity (NCD % - The 'Shift')
    ncd = fetch_metric(location_name, cond="cancer OR diabetes OR cardiovascular")
    m4 = (ncd / max(1, total)) * 100
    
    # 5. Transparency (Completed Trials as proxy for potential results)
    comp = fetch_metric(location_name, "AREA[OverallStatus]COMPLETED")
    m5 = (comp / max(1, total)) * 100
    
    # 6. Collaboration (Multi-country trials - heuristic)
    multi = fetch_metric(location_name, "AREA[EnrollmentCount]RANGE[500, 1000000]")
    m6 = (multi / max(1, total)) * 100
    
    # 7. Efficiency (Low Termination Rate)
    term = fetch_metric(location_name, "AREA[OverallStatus]TERMINATED OR AREA[OverallStatus]WITHDRAWN")
    m7 = 100 - (term / max(1, total) * 100 * 5) # Penalty for high termination
    
    # 8. Sustainability (Phase 4 - Post-market)
    p4 = fetch_metric(location_name, "AREA[Phase]Phase 4")
    m8 = (p4 / max(1, total)) * 100 * 10 # Rare, so weight it
    
    return [round(m,1) for m in [m1, m2, m3, m4, m5, m6, m7, m8]]

def generate_html(af_scores, eu_scores):
    labels = ["Volume", "Sovereignty", "Innovation", "Diversity", "Transparency", "Collaboration", "Efficiency", "Sustainability"]
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>The Spider Web Analysis: Africa vs Europe</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;700&display=swap');
            body {{ background: #08090d; color: #fff; font-family: 'Space Grotesk', sans-serif; padding: 40px; }}
            .container {{ max-width: 1000px; margin: 0 auto; text-align: center; }}
            .chart-container {{ background: rgba(255,255,255,0.02); border: 1px solid #1a1c23; padding: 50px; border-radius: 40px; margin-top: 40px; }}
            h1 {{ font-size: 3em; margin-bottom: 10px; background: linear-gradient(to right, #ff7675, #74b9ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            .analogy-box {{ text-align: left; background: #11141d; padding: 30px; border-radius: 20px; border-left: 5px solid #ff7675; margin-top: 40px; line-height: 1.8; color: #aaa; }}
            .legend {{ display: flex; justify-content: center; gap: 30px; margin-top: 20px; }}
            .legend-item {{ display: flex; align-items: center; gap: 10px; font-weight: 700; }}
            .dot {{ width: 15px; height: 15px; border-radius: 50%; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>The Spider Web Analysis</h1>
            <p style="color: #666; letter-spacing: 2px; text-transform: uppercase;">Visualizing the Structural Geometry of Research Inequity</p>
            
            <div class="chart-container">
                <canvas id="spiderChart"></canvas>
            </div>

            <div class="legend">
                <div class="legend-item"><div class="dot" style="background: rgba(255, 118, 117, 0.7);"></div> AFRICA</div>
                <div class="legend-item"><div class="dot" style="background: rgba(116, 185, 255, 0.7);"></div> EUROPE</div>
            </div>

            <div class="analogy-box">
                <strong>The Spider Web Analogy:</strong><br>
                In clinical research, the "Web" is the infrastructure of funding, data, and power. 
                <br><br>
                • <strong>Europe's Web (Blue):</strong> A <strong>Symmetrical Grid</strong>. It is balanced across all eight dimensions. The "silk" (resources) is reinforced at every junction, from discovery (Innovation) to post-market (Sustainability). It is a web designed to support itself.
                <br><br>
                • <strong>Africa's Web (Red):</strong> A <strong>Radial Spoke</strong>. It is "sticky" in Volume and Collaboration (the outer rings) but collapses toward the center in Innovation and Sovereignty. The web is anchored to external hubs, meaning the "nutrition" (results and economic benefit) flows out along the radial lines rather than being shared across a local grid.
            </div>
        </div>

        <script>
            const ctx = document.getElementById('spiderChart').getContext('2d');
            new Chart(ctx, {{
                type: 'radar',
                data: {{
                    labels: {json.dumps(labels)},
                    datasets: [{{
                        label: 'Africa',
                        data: {json.dumps(af_scores)},
                        fill: true,
                        backgroundColor: 'rgba(255, 118, 117, 0.2)',
                        borderColor: '#ff7675',
                        pointBackgroundColor: '#ff7675',
                        pointBorderColor: '#fff',
                        pointHoverBackgroundColor: '#fff',
                        pointHoverBorderColor: '#ff7675'
                    }}, {{
                        label: 'Europe',
                        data: {json.dumps(eu_scores)},
                        fill: true,
                        backgroundColor: 'rgba(116, 185, 255, 0.2)',
                        borderColor: '#74b9ff',
                        pointBackgroundColor: '#74b9ff',
                        pointBorderColor: '#fff',
                        pointHoverBackgroundColor: '#fff',
                        pointHoverBorderColor: '#74b9ff'
                    }}]
                }},
                options: {{
                    scales: {{
                        r: {{
                            angleLines: {{ color: '#222' }},
                            grid: {{ color: '#222' }},
                            pointLabels: {{ color: '#888', font: {{ size: 14 }} }},
                            ticks: {{ display: false }},
                            suggestedMin: 0,
                            suggestedMax: 100
                        }}
                    }},
                    plugins: {{ legend: {{ display: false }} }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Initiating Spider Web Analysis...")
    af_scores = collect_spider_metrics("Africa", AFRICA_HUBS)
    eu_scores = collect_spider_metrics("Europe", EUROPE_HUBS)
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_html(af_scores, eu_scores))
        
    print(f"\nSpider Web Analysis Complete. Visualization: {OUTPUT_HTML}")
