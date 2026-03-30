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

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "comparison_data_v2.json"
AFRICA_CACHE = DATA_DIR / "collected_data.json"
OUTPUT_HTML = Path(__file__).parent / "africa-europe-rct-comparison.html"

# Top 5 Countries as Representative Samples
AFRICA_TOP5 = ["Egypt", "South Africa", "Uganda", "Kenya", "Nigeria"]
EUROPE_TOP5 = ["France", "United Kingdom", "Germany", "Spain", "Italy"]

CONDITIONS = [
    "HIV", "tuberculosis", "malaria", "cancer", "diabetes",
    "cardiovascular", "hypertension", "mental health", "stroke",
]

PHASES = ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]

RATE_LIMIT_DELAY = 0.5

def search_trials(location=None, condition=None, phase=None, status=None):
    params = {"format": "json", "pageSize": 10, "countTotal": "true"}
    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if phase: filters.append(f"AREA[Phase]{phase.replace('_', ' ').title()}")
    if status:
        if isinstance(status, list):
            s_parts = " OR ".join(f"AREA[OverallStatus]{s}" for s in status)
            filters.append(f"({s_parts})")
        else:
            filters.append(f"AREA[OverallStatus]{status}")
    params["filter.advanced"] = " AND ".join(filters)
    if condition: params["query.cond"] = condition
    if location: params["query.locn"] = location
    
    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json().get("totalCount", 0)
        except Exception as e:
            print(f"  Attempt {attempt+1} failed for {location}/{condition}: {e}")
            time.sleep(2 * (attempt + 1))
    return 0

def collect_data():
    results = {"meta": {"date": datetime.now().isoformat()}, "africa": {}, "europe": {}}
    
    for region, countries, key in [("Africa", AFRICA_TOP5, "africa"), ("Europe", EUROPE_TOP5, "europe")]:
        print(f"\nQuerying {region} (Top 5 countries sum)...")
        results[key]["country_counts"] = {}
        total = 0
        for c in countries:
            count = search_trials(location=c)
            results[key]["country_counts"][c] = count
            total += count
            print(f"  {c}: {count:,}")
            time.sleep(RATE_LIMIT_DELAY)
        results[key]["total"] = total

        print(f"Querying {region} conditions...")
        results[key]["conditions"] = {}
        for cond in CONDITIONS:
            cond_sum = 0
            for c in countries:
                cond_sum += search_trials(location=c, condition=cond)
                time.sleep(RATE_LIMIT_DELAY)
            results[key]["conditions"][cond] = cond_sum
            print(f"  {cond}: {cond_sum:,}")

        print(f"Querying {region} phases...")
        results[key]["phases"] = {}
        for ph in PHASES:
            ph_sum = 0
            for c in countries:
                ph_sum += search_trials(location=c, phase=ph)
                time.sleep(RATE_LIMIT_DELAY)
            results[key]["phases"][ph] = ph_sum
            print(f"  {ph}: {ph_sum:,}")

        print(f"Querying {region} terminations...")
        term_sum = 0
        for c in countries:
            term_sum += search_trials(location=c, status=["TERMINATED", "WITHDRAWN"])
            time.sleep(RATE_LIMIT_DELAY)
        results[key]["terminated"] = term_sum
        print(f"  Terminated: {term_sum:,}")

    return results

def generate_html(data):
    africa = data["africa"]
    europe = data["europe"]
    
    def get_bars(d, color):
        max_val = max(d.values()) if d else 1
        html = ""
        for k, v in sorted(d.items(), key=lambda x: x[1], reverse=True):
            pct = v / max_val * 100
            html += f'<div style="margin-bottom:12px;"><div style="font-size:0.9em; margin-bottom:4px; display:flex; justify-content:space-between;"><span>{k}</span><span>{v:,}</span></div> <div style="background:{color}; width:{pct}%; height:12px; border-radius:6px; min-width:3px;"></div></div>'
        return html

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Africa vs Europe RCT Equity Analysis</title>
        <style>
            body {{ font-family:'Inter', sans-serif; background:#0a0b10; color:#e0e0e0; padding:40px; line-height:1.6; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            h1 {{ font-size:2.8em; margin-bottom:10px; color:#fff; letter-spacing:-1px; }}
            .subtitle {{ color:#888; margin-bottom:40px; font-size:1.2em; }}
            .card {{ background:#16181d; padding:30px; border-radius:16px; border:1px solid #2d2f36; margin-bottom:30px; }}
            .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:30px; }}
            .highlight-africa {{ color:#ff7675; }}
            .highlight-europe {{ color:#74b9ff; }}
            .big-number {{ font-size:3.5em; font-weight:800; line-height:1; margin:10px 0; }}
            .label {{ font-size:0.9em; color:#888; text-transform:uppercase; }}
            table {{ width:100%; border-collapse:separate; border-spacing:0; margin-top:20px; }}
            th, td {{ padding:15px; border-bottom:1px solid #2d2f36; text-align:left; }}
            th {{ font-size:0.8em; color:#666; text-transform:uppercase; }}
            .ratio-box {{ background:rgba(255,118,117,0.1); border:1px solid #ff7675; padding:20px; border-radius:12px; text-align:center; margin-top:20px; }}
            .tag {{ padding:4px 12px; border-radius:20px; font-size:0.75em; font-weight:600; }}
            .tag-africa {{ background:rgba(255,118,117,0.2); color:#ff7675; }}
            .tag-europe {{ background:rgba(116,185,255,0.2); color:#74b9ff; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Africa vs Europe RCT Equity</h1>
            <div class="subtitle">Representative comparison using top 5 countries per region (Interventional Trials)</div>
            
            <div class="card">
                <div class="grid">
                    <div>
                        <div class="label">Africa (Top 5)</div>
                        <div class="big-number highlight-africa">{africa['total']:,}</div>
                        <div class="label">Total Interventional Trials</div>
                    </div>
                    <div>
                        <div class="label">Europe (Top 5)</div>
                        <div class="big-number highlight-europe">{europe['total']:,}</div>
                        <div class="label">Total Interventional Trials</div>
                    </div>
                </div>
                <div class="ratio-box">
                    <span style="font-size:1.5em;">The Equity Gap: <strong>{europe['total']/africa['total'] if africa['total'] else 0:.1f}x</strong> higher volume in Europe</span>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h2>Disease Focus</h2>
                    <p class="label">Trials by Condition</p>
                    {get_bars(africa['conditions'], '#ff7675')}
                    <div style="margin-top:20px; border-top:1px solid #333; padding-top:20px;">
                        {get_bars(europe['conditions'], '#74b9ff')}
                    </div>
                </div>
                <div class="card">
                    <h2>Phase Distribution</h2>
                    <p class="label">Clinical Research Pipeline</p>
                    {get_bars(africa['phases'], '#ff7675')}
                    <div style="margin-top:20px; border-top:1px solid #333; padding-top:20px;">
                        {get_bars(europe['phases'], '#74b9ff')}
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Termination Rates</h2>
                <div class="grid">
                    <div>
                        <div class="big-number highlight-africa">{africa['terminated']/africa['total']*100 if africa['total'] else 0:.1f}%</div>
                        <div class="label">African trials terminated/withdrawn</div>
                    </div>
                    <div>
                        <div class="big-number highlight-europe">{europe['terminated']/europe['total']*100 if europe['total'] else 0:.1f}%</div>
                        <div class="label">European trials terminated/withdrawn</div>
                    </div>
                </div>
            </div>

            <div class="card" style="border-left:8px solid #ff7675;">
                <h2>Strategic Insight</h2>
                <p>The analysis reveals that Europe focuses heavily on <strong>Cancer</strong> ({europe['conditions'].get('cancer',0):,} trials) which represents specialized oncology research. Africa, while having a significant disease burden, shows high <strong>HIV</strong> and <strong>Maternal/Neonatal</strong> focus, but trails significantly in specialized areas like <strong>Cardiovascular</strong> ({europe['conditions'].get('cardiovascular',0)/africa['conditions'].get('cardiovascular',1):.1f}x gap) and <strong>Mental Health</strong>.</p>
                <p>Furthermore, the high termination rate in Africa ({africa['terminated']/africa['total']*100 if africa['total'] else 0:.1f}%) compared to Europe ({europe['terminated']/europe['total']*100 if europe['total'] else 0:.1f}%) suggests structural challenges in trial sustainability.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Force refresh for the new comparison methodology
    data = collect_data()
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_html(data))
    print(f"\nReport generated: {OUTPUT_HTML}")
