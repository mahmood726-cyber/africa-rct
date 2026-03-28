import json
import requests
import time
from datetime import datetime
import numpy as np

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_temporal_data(location, count=200):
    print(f"  Measuring temporal friction for {location}...")
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

    delays = []
    for s in studies:
        proto = s.get("protocolSection", {})
        status = proto.get("statusModule", {})
        
        start_date = status.get("startDateStruct", {}).get("date")
        post_date = status.get("studyFirstPostDateStruct", {}).get("date")
        
        if start_date and post_date:
            try:
                s_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
                p_dt = datetime.strptime(post_date[:10], "%Y-%m-%d")
                delay = (p_dt - s_dt).days
                if -365 < delay < 3650: # Filter outliers
                    delays.append(delay)
            except: pass
            
    return delays

results = {}
for reg in REGIONS:
    delays = fetch_temporal_data(reg)
    if delays:
        results[reg] = {
            "avg_delay": round(sum(delays) / len(delays), 1),
            "median_delay": round(np.median(delays), 1),
            "max_delay": max(delays),
            "min_delay": min(delays)
        }
    else:
        results[reg] = {"avg_delay": 0, "median_delay": 0}

print(json.dumps(results, indent=2))
with open("C:/AfricaRCT/data/temporal_friction_data.json", "w") as f:
    json.dump(results, f, indent=2)
