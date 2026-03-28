import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
YEARS = range(2000, 2026)
REGIONS = ["Africa", "Europe"]

def fetch_yearly_metrics(location, year):
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    
    # Total Interventional
    total_params = {
        "format": "json", "pageSize": 1, "countTotal": "true",
        "query.locn": location,
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[StudyFirstPostDate]RANGE[{start}, {end}]"
    }
    
    # COVID-19 specific
    covid_params = {
        "format": "json", "pageSize": 1, "countTotal": "true",
        "query.locn": location, "query.cond": "COVID-19",
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[StudyFirstPostDate]RANGE[{start}, {end}]"
    }
    
    # Tech/Digital proxy
    tech_params = {
        "format": "json", "pageSize": 1, "countTotal": "true",
        "query.locn": location, "query.term": "digital OR virtual OR decentralized OR mobile OR wearable",
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[StudyFirstPostDate]RANGE[{start}, {end}]"
    }

    try:
        total = requests.get(BASE_URL, params=total_params, timeout=30).json().get("totalCount", 0)
        time.sleep(0.2)
        covid = requests.get(BASE_URL, params=covid_params, timeout=30).json().get("totalCount", 0)
        time.sleep(0.2)
        tech = requests.get(BASE_URL, params=tech_params, timeout=30).json().get("totalCount", 0)
        time.sleep(0.2)
        return {"total": total, "covid": covid, "tech": tech}
    except:
        return {"total": 0, "covid": 0, "tech": 0}

results = {reg: {} for reg in REGIONS}
for year in YEARS:
    print(f"Auditing Shock Year: {year}...")
    for reg in REGIONS:
        results[reg][year] = fetch_yearly_metrics(reg, year)

with open("C:/AfricaRCT/data/shock_resilience_data.json", "w") as f:
    json.dump(results, f, indent=2)

print("\nShock Audit Data Saved.")
