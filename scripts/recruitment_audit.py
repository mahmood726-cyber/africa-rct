import json
import requests
import time
from datetime import datetime

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_recruitment_data(location, count=200):
    print(f"  Measuring recruitment velocity for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
    except: return []

    velocities = []
    site_counts = []
    enrollments = []
    
    for s in studies:
        proto = s.get("protocolSection", {})
        design = proto.get("designModule", {})
        status = proto.get("statusModule", {})
        loc_mod = proto.get("contactsLocationsModule", {})
        
        enrollment = design.get("enrollmentInfo", {}).get("count", 0)
        start = status.get("startDateStruct", {}).get("date")
        end = status.get("completionDateStruct", {}).get("date")
        
        sites = len(loc_mod.get("locations", []))
        
        if start and end and enrollment > 0:
            try:
                s_dt = datetime.strptime(start[:7], "%Y-%m")
                e_dt = datetime.strptime(end[:7], "%Y-%m")
                months = (e_dt - s_dt).days / 30.44
                if months > 0:
                    velocities.append(enrollment / months)
                    site_counts.append(sites)
                    enrollments.append(enrollment)
            except: pass
            
    return {
        "avg_velocity": round(sum(velocities) / len(velocities), 2) if velocities else 0,
        "avg_sites": round(sum(site_counts) / len(site_counts), 2) if site_counts else 0,
        "fragmentation_index": round((sum(site_counts) / max(1, sum(enrollments))) * 1000, 2) if enrollments else 0
    }

results = {reg: fetch_recruitment_data(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/recruitment_efficiency_data.json", "w") as f:
    json.dump(results, f, indent=2)
