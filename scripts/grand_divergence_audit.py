import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
# 5-year epochs for clarity of "Grand Divergence"
EPOCHS = [
    ("2000-01-01", "2004-12-31", "Early Adoption"),
    ("2005-01-01", "2009-12-31", "ICMJE Mandate"),
    ("2010-01-01", "2014-12-31", "Global Expansion"),
    ("2015-01-01", "2019-12-31", "Modern Surge"),
    ("2020-01-01", "2025-12-31", "Crisis & Tech")
]
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_epoch_data(location, start, end):
    print(f"  Auditing {location} for epoch {start} to {end}...")
    params = {
        "format": "json", "pageSize": 1, "countTotal": "true",
        "query.locn": location,
        "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND AREA[StudyFirstPostDate]RANGE[{start}, {end}]"
    }
    
    # Also get Industry lead count for sponsorship evolution
    ind_params = params.copy()
    ind_params["filter.advanced"] += " AND AREA[LeadSponsorClass]INDUSTRY"
    
    try:
        total = requests.get(BASE_URL, params=params, timeout=30).json().get("totalCount", 0)
        time.sleep(0.3)
        industry = requests.get(BASE_URL, params=ind_params, timeout=30).json().get("totalCount", 0)
        time.sleep(0.3)
        return {"total": total, "industry": industry}
    except:
        return {"total": 0, "industry": 0}

results = {reg: {} for reg in REGIONS}
for start, end, label in EPOCHS:
    for reg in REGIONS:
        results[reg][label] = fetch_epoch_data(reg, start, end)

with open("C:/AfricaRCT/data/grand_divergence_data.json", "w") as f:
    json.dump(results, f, indent=2)

print("\nGrand Divergence Audit Complete.")
