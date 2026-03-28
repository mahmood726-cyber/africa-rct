import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

CAPITALS = {
    "Africa": ["Cairo", "Johannesburg", "Cape Town", "Nairobi", "Lagos", "Accra", "Kampala", "Addis Ababa"], # Major hubs
    "Europe": ["Paris", "London", "Berlin", "Madrid", "Rome", "Brussels", "Amsterdam", "Vienna"],
    "China": ["Beijing", "Shanghai", "Guangzhou", "Shenzhen"],
    "India": ["Delhi", "Mumbai", "Bangalore", "Hyderabad", "Chennai"]
}

NEGLECTED_CONDS = "malaria OR tuberculosis OR ebola OR cholera OR 'sleeping sickness' OR 'chagas disease' OR 'leishmaniasis' OR 'leprosy' OR 'dengue'"

def fetch_humanitarian_metrics(location):
    print(f"  Measuring Humanitarian Multipliers in {location}...")
    
    # 1. Total Interventional
    t_params = {"format": "json", "pageSize": 100, "countTotal": "true", "query.locn": location, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    resp = requests.get(BASE_URL, params=t_params).json()
    total = resp.get("totalCount", 1)
    studies = resp.get("studies", [])
    
    # 2. Intellectual Multiplier (References/Publications)
    has_refs = 0
    rural_sites = 0
    total_sites = 0
    
    for s in studies:
        proto = s.get("protocolSection", {})
        refs = proto.get("referencesModule", {}).get("references", [])
        locs = proto.get("contactsLocationsModule", {}).get("locations", [])
        
        if refs: has_refs += 1
        
        caps = [c.lower() for c in CAPITALS.get(location, [])]
        for l in locs:
            total_sites += 1
            city = l.get("city", "").lower()
            if city and city not in caps:
                rural_sites += 1

    # 3. Neglected Disease Focus
    n_params = t_params.copy()
    n_params["pageSize"] = 1
    n_params["query.cond"] = NEGLECTED_CONDS
    neglected = requests.get(BASE_URL, params=n_params).json().get("totalCount", 0)

    return {
        "total": total,
        "publication_rate": round((has_refs / max(1, len(studies))) * 100, 1),
        "rural_inclusion_pct": round((rural_sites / max(1, total_sites)) * 100, 1) if total_sites else 0,
        "neglected_focus_pct": round((neglected / total) * 100, 1),
        "intellectual_legacy": round(has_refs * (total/100.0), 0) # Extrapolated
    }

results = {reg: fetch_humanitarian_metrics(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/humanitarian_multiplier_data.json", "w") as f:
    json.dump(results, f, indent=2)
