import json
import requests
import time
from datetime import datetime
import numpy as np

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_evolutionary_data(location, count=250):
    print(f"  Probing clinical evolution in {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        studies = resp.json().get("studies", [])
    except: return []

    mutation_counts = []
    lifespans = []
    success_markers = [] # Completed + Results
    
    for s in studies:
        proto = s.get("protocolSection", {})
        status_mod = proto.get("statusModule", {})
        
        # 1. Mutation Rate (Update Frequency)
        # Using version numbers or update dates as proxy
        # In API v2, we look at the number of 'lastUpdatePostDate' events or simply the gap
        start_date = status_mod.get("studyFirstPostDateStruct", {}).get("date")
        update_date = status_mod.get("lastUpdatePostDateStruct", {}).get("date")
        
        if start_date and update_date:
            try:
                s_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
                u_dt = datetime.strptime(update_date[:10], "%Y-%m-%d")
                # We normalize "Mutations" as updates over time
                # More updates = higher protocol volatility
                mutation_counts.append((u_dt - s_dt).days / 365.0)
            except: pass
            
        # 2. Survival Marker
        is_completed = status_mod.get("overallStatus") == "COMPLETED"
        has_results = s.get("resultsSection") is not None
        if is_completed:
            success_markers.append(1 if has_results else 0)

    return {
        "avg_protocol_volatility": round(sum(mutation_counts) / len(mutation_counts), 2) if mutation_counts else 0,
        "survival_to_results_rate": round((sum(success_markers) / len(success_markers)) * 100, 1) if success_markers else 0,
        "fitness_score": round((sum(success_markers) / max(1, len(studies))) * 100, 1)
    }

results = {reg: fetch_evolutionary_data(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/clinical_darwinism_data.json", "w") as f:
    json.dump(results, f, indent=2)
