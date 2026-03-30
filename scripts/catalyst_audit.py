import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_catalyst_metrics(location):
    print(f"  Measuring the Catalyst Effect in {location}...")
    
    # 1. Total Interventional
    t_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    total = requests.get(BASE_URL, params=t_params).json().get("totalCount", 1)
    time.sleep(0.3)
    
    # 2. Intelligent/Adaptive Design (Platform, Basket, Umbrella, Adaptive, AI)
    intel_params = t_params.copy()
    intel_params["query.term"] = "adaptive design OR platform trial OR basket trial OR umbrella trial OR artificial intelligence OR machine learning"
    intelligent = requests.get(BASE_URL, params=intel_params).json().get("totalCount", 0)
    time.sleep(0.3)
    
    # 3. The Multiplier (Collaborator density via API sample)
    # We will sample 100 trials to get the average facilities per sponsor
    s_params = t_params.copy()
    s_params["pageSize"] = 100
    try:
        studies = requests.get(BASE_URL, params=s_params, timeout=30).json().get("studies", [])
    except:
        studies = []
        
    enrollment_per_site = []
    facilities_list = []
    has_pubs = 0
    
    for s in studies:
        proto = s.get("protocolSection", {})
        design = proto.get("designModule", {})
        locs = proto.get("contactsLocationsModule", {}).get("locations", [])
        refs = proto.get("referencesModule", {}).get("references", [])
        
        sites = len(locs)
        enrollment = design.get("enrollmentInfo", {}).get("count", 0)
        
        if sites > 0:
            enrollment_per_site.append(enrollment / sites)
            facilities_list.append(sites)
            
        if refs:
            has_pubs += 1

    avg_enroll_per_site = sum(enrollment_per_site) / len(enrollment_per_site) if enrollment_per_site else 0
    avg_facilities = sum(facilities_list) / len(facilities_list) if facilities_list else 0
    pub_ratio = (has_pubs / max(1, len(studies))) * 100 if studies else 0

    return {
        "total": total,
        "intelligent_trials": intelligent,
        "intelligent_ratio": round((intelligent / max(1, total)) * 100, 2),
        "avg_enrollment_per_site": round(avg_enroll_per_site, 1),
        "avg_facilities": round(avg_facilities, 1),
        "publication_ratio": round(pub_ratio, 1)
    }

results = {reg: fetch_catalyst_metrics(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/catalyst_audit_data.json", "w") as f:
    json.dump(results, f, indent=2)
