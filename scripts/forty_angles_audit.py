import json
import requests
import time
import math
import numpy as np
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

def fetch_multiscalar_data(location, count=250):
    print(f"  Performing multiscalar probe for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_40_angles_audit():
    results = {}
    REGIONS = ["Africa", "Europe"]
    
    for reg in REGIONS:
        studies = fetch_multiscalar_data(reg)
        if not studies: continue
        
        # 1. Kinetic Parameters
        lifespans = []
        updates_per_year = []
        
        # 2. Fractal Parameters (Sites across cities)
        cities_per_trial = []
        
        # 3. Informational Parameters
        thematic_word_counts = []
        eligibility_to_outcome_ratio = []
        
        for s in studies:
            proto = s.get("protocolSection", {})
            status = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            locs = proto.get("contactsLocationsModule", {}).get("locations", [])
            outcomes = proto.get("outcomesModule", {})
            elig = proto.get("eligibilityModule", {})
            
            # KINETIC
            start = status.get("studyFirstPostDateStruct", {}).get("date")
            last = status.get("lastUpdatePostDateStruct", {}).get("date")
            if start and last:
                try:
                    days = (datetime.strptime(last[:10], "%Y-%m-%d") - datetime.strptime(start[:10], "%Y-%m-%d")).days
                    lifespans.append(days)
                except: pass
            
            # FRACTAL
            cities = set([l.get("city", "") for l in locs])
            cities_per_trial.append(len(cities))
            
            # INFORMATIONAL
            endpoints = len(outcomes.get("primaryOutcomes", [])) + len(outcomes.get("secondaryOutcomes", []))
            elig_len = len(elig.get("eligibilityCriteria", ""))
            if endpoints > 0:
                eligibility_to_outcome_ratio.append(elig_len / endpoints)

        results[reg] = {
            "avg_lifespan": round(np.mean(lifespans), 1) if lifespans else 0,
            "avg_cities": round(np.mean(cities_per_trial), 1) if cities_per_trial else 0,
            "complexity_ratio": round(np.mean(eligibility_to_outcome_ratio), 1) if eligibility_to_outcome_ratio else 0,
            "sample_size": len(studies)
        }

    print(json.dumps(results, indent=2))
    with open(DATA_DIR / "forty_angles_raw_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    from datetime import datetime
    run_40_angles_audit()
