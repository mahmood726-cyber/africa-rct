import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

def audit_solutions(location):
    print(f"  Auditing Structural Solutions in {location}...")
    base_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    
    # 1. Tech Transfer & Capacity Building
    tech_params = base_params.copy()
    tech_params["query.term"] = "technology transfer OR capacity building OR investigator training OR infrastructure development"
    tech_count = requests.get(BASE_URL, params=tech_params).json().get("totalCount", 0)
    time.sleep(0.3)
    
    # 2. Pan-Continental Harmonization (Multi-country Africa)
    # Using API sample to identify trials with multiple African countries
    samp_params = base_params.copy()
    samp_params["pageSize"] = 1000
    try:
        studies = requests.get(BASE_URL, params=samp_params, timeout=60).json().get("studies", [])
    except:
        studies = []
        
    pan_african = 0
    domestic_grid = 0
    
    AFRICAN_COUNTRIES = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda", "Ghana", "Ethiopia", "Tanzania", "Malawi", "Zambia", "Zimbabwe", "Senegal", "Rwanda", "Cameroon", "Mozambique"]
    
    for s in studies:
        locs = s.get("protocolSection", {}).get("contactsLocationsModule", {}).get("locations", [])
        countries = set([l.get("country", "") for l in locs])
        
        # Count how many of these countries are in Africa
        af_count = sum(1 for c in countries if c in AFRICAN_COUNTRIES)
        
        if af_count > 1:
            pan_african += 1
            
        # 3. Domestic Grid (1 country, but > 3 sites)
        if af_count == 1 and len(locs) > 3:
            domestic_grid += 1

    return {
        "tech_transfer_count": tech_count,
        "pan_continental_count": pan_african,
        "domestic_grid_count": domestic_grid
    }

results = {"Africa": audit_solutions("Africa")}
print(json.dumps(results, indent=2))

with open(DATA_DIR / "structural_solutions_data.json", "w") as f:
    json.dump(results, f, indent=2)
