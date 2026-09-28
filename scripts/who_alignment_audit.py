# sentinel:skip-file — hardcoded paths are fixture/registry/audit-narrative data for this repo's research workflow, not portable application configuration. Same pattern as push_all_repos.py and E156 workbook files.
import json
import requests
import time

from repo_paths import data_file

# WHO GHO API Base
GHO_URL = "https://ghoapi.azureedge.net/api"

# Top Hubs
AFRICA_ISO = ["EGY", "ZAF", "NGA", "KEN", "UGA"]
EUROPE_ISO = ["FRA", "GBR", "DEU", "ESP", "ITA"]

# Indicators
# GBD_DALYRTAGE: Age-standardized DALYs (per 100,000) - Measure of Need
# GHED_PHC_pc_US_SHA2011: Primary health care expenditure per capita in US$ - Measure of Investment

def fetch_who_data(indicator, country_codes):
    print(f"  Fetching WHO Indicator {indicator}...")
    url = f"{GHO_URL}/{indicator}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200: return {}
        data = r.json().get('value', [])
        
        # Filter for latest year and our countries
        results = {}
        for entry in data:
            code = entry.get('SpatialTime') # In some indicators it's SpatialTime, in others Spatial
            if not code: code = entry.get('Spatial')
            
            if code in country_codes:
                year = entry.get('TimeDim')
                val = entry.get('NumericValue')
                if val is not None:
                    # Keep the latest year
                    if code not in results or year > results[code]['year']:
                        results[code] = {'val': val, 'year': year}
        return results
    except Exception as e:
        print(f"Error: {e}")
        return {}

def run_alignment_audit():
    print("Initiating WHO-ClinicalTrials Cross-Referencing Audit...")
    
    # 1. Fetch WHO Data
    countries = AFRICA_ISO + EUROPE_ISO
    daly_data = fetch_who_data("GBD_DALYRTAGE", countries)
    expend_data = fetch_who_data("GHED_PHC_pc_US_SHA2011", countries)
    
    # 2. Load existing RCT data (from our comparison_data_v2.json)
    with data_file("comparison_data_v2.json").open("r", encoding="utf-8") as f:
        rct_data = json.load(f)
    
    # Map ISO to names used in RCT data
    iso_map = {
        "EGY": "Egypt", "ZAF": "South Africa", "NGA": "Nigeria", "KEN": "Kenya", "UGA": "Uganda",
        "FRA": "France", "GBR": "United Kingdom", "DEU": "Germany", "ESP": "Spain", "ITA": "Italy"
    }
    
    alignment = {"Africa": [], "Europe": []}
    
    for iso, name in iso_map.items():
        region = "Africa" if iso in AFRICA_ISO else "Europe"
        rct_count = rct_data[region.lower()]['country_counts'].get(name, 0)
        daly = daly_data.get(iso, {}).get('val', 0)
        expend = expend_data.get(iso, {}).get('val', 1)
        
        alignment[region].append({
            "country": name,
            "rcts": rct_count,
            "daly_need": daly,
            "expenditure": expend,
            "rct_per_need": round(rct_count / (daly/1000) if daly else 0, 4), # Trials per 1k DALYs
            "altruism_efficiency": round(rct_count / expend if expend else 0, 4) # Trials per $ of PHC expend
        })

    # Summary Stats
    summary = {}
    for reg in ["Africa", "Europe"]:
        summary[reg] = {
            "avg_rct_per_need": round(sum(c['rct_per_need'] for c in alignment[reg]) / len(alignment[reg]), 4),
            "avg_altruism_efficiency": round(sum(c['altruism_efficiency'] for c in alignment[reg]) / len(alignment[reg]), 4)
        }

    results = {"details": alignment, "summary": summary}
    print(json.dumps(summary, indent=2))
    
    with data_file("who_alignment_data.json").open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_alignment_audit()
