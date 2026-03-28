import json
import requests
import time
from datetime import datetime
import numpy as np

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe"]

def fetch_spheres_data(location, count=300):
    print(f"  Measuring the 12 Spheres for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        studies = resp.json().get("studies", [])
    except: return {}

    spheres = {
        "churn_list": [], # Lead sponsor names
        "site_list": [], # City counts
        "eligibility_chars": [],
        "arm_counts": [],
        "country_counts": [],
        "update_counts": [], # Placeholder for protocol volatility proxy
        "condition_counts": [],
        "duration_deltas": [], # Planned vs Actual if available
        "sponsor_classes": [],
        "masking_levels": [],
        "endpoint_counts": [],
        "registration_delays": []
    }

    for s in studies:
        proto = s.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        sponsor = proto.get("sponsorCollaboratorsModule", {})
        design = proto.get("designModule", {})
        elig = proto.get("eligibilityModule", {})
        locs = proto.get("contactsLocationsModule", {})
        conds = proto.get("conditionsModule", {})
        outcomes = proto.get("outcomesModule", {})

        # 1. Churn (Lead Name)
        spheres["churn_list"].append(sponsor.get("leadSponsor", {}).get("name", "Unknown"))
        
        # 2. Spatial (Cities)
        cities = [l.get("city", "") for l in locs.get("locations", [])]
        spheres["site_list"].append(len(set(cities)))
        
        # 3. Eligibility
        spheres["eligibility_chars"].append(len(elig.get("eligibilityCriteria", "")))
        
        # 4. Arms
        spheres["arm_counts"].append(len(design.get("armsInterventionsModule", {}).get("armGroups", [])))
        
        # 5. International Reach (Countries - heuristic from locations)
        countries = [l.get("country", "") for l in locs.get("locations", [])]
        spheres["country_counts"].append(len(set(countries)))
        
        # 6. Update Frequency (Days between first post and last update)
        start = status.get("studyFirstPostDateStruct", {}).get("date")
        last = status.get("lastUpdatePostDateStruct", {}).get("date")
        if start and last:
            try:
                spheres["update_counts"].append((datetime.strptime(last[:10], "%Y-%m-%d") - datetime.strptime(start[:10], "%Y-%m-%d")).days)
            except: pass
            
        # 7. Thematic Resolution
        spheres["condition_counts"].append(len(conds.get("conditions", [])))
        
        # 8. Masking Depth
        m_info = design.get("designInfo", {}).get("maskingInfo", {})
        spheres["masking_levels"].append(1 if m_info.get("masking") != "NONE" else 0)
        
        # 9. Endpoint Density
        spheres["endpoint_counts"].append(len(outcomes.get("primaryOutcomes", [])) + len(outcomes.get("secondaryOutcomes", [])))

    results = {
        "sponsor_churn_index": round(len(set(spheres["churn_list"])) / max(1, len(studies)), 2),
        "avg_cities_per_trial": round(np.mean(spheres["site_list"]), 1) if spheres["site_list"] else 0,
        "avg_eligibility_stringency": round(np.mean(spheres["eligibility_chars"]), 0) if spheres["eligibility_chars"] else 0,
        "avg_arms": round(np.mean(spheres["arm_counts"]), 1) if spheres["arm_counts"] else 0,
        "avg_countries": round(np.mean(spheres["country_counts"]), 1) if spheres["country_counts"] else 0,
        "protocol_persistence_days": round(np.mean(spheres["update_counts"]), 0) if spheres["update_counts"] else 0,
        "avg_conditions": round(np.mean(spheres["condition_counts"]), 1) if spheres["condition_counts"] else 0,
        "masking_rate": round(np.mean(spheres["masking_levels"]) * 100, 1) if spheres["masking_levels"] else 0,
        "avg_endpoints": round(np.mean(spheres["endpoint_counts"]), 1) if spheres["endpoint_counts"] else 0,
        "total": len(studies)
    }
    return results

results = {reg: fetch_spheres_data(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/spheres_sovereignty_data.json", "w") as f:
    json.dump(results, f, indent=2)
